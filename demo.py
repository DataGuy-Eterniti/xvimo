"""Xvimo live web demo: paste a message and watch the agent work, step by step.

Routes (mounted by the bot):
    GET  /demo        the demo page
    POST /demo/check  runs the pipeline and streams progress as newline-delimited JSON
"""
import base64
import json
import os
import queue
import re
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

import uuid

from events import log_check, log_feedback, log_visit
from limits import CAP_MESSAGE, take_check

router = APIRouter()
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo.html")
MAX_CHARS = 1500
RATE_LIMIT = (8, 600)  # at most 8 checks per visitor every 10 minutes, to protect model credits
_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def _allowed(ip: str) -> bool:
    limit, window = RATE_LIMIT
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown"))


VISITOR_RE = re.compile(r"^[A-Za-z0-9]{8,40}$")
_last_visit = {}
_visit_lock = threading.Lock()


def _visitor(value) -> str:
    """Anonymous random id the browser keeps for itself; anything else is ignored."""
    value = str(value or "")
    return value if VISITOR_RE.match(value) else ""


def _source(value) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "", str(value or "").lower())[:40] or "direct"


def _run(message: str, out: queue.Queue, image: tuple = None, visitor: str = "") -> None:
    """Runs the full pipeline in a worker thread, pushing progress events into the queue.

    image, if given, is (bytes, mime): the screenshot is read first, then judged like a typed message.
    """
    from investigator import investigate  # imported lazily so the page loads even without model keys
    from triage import route_reason, triage

    emit = out.put
    started = time.time()
    check_id = uuid.uuid4().hex[:16]
    try:
        if image:
            from vision import read_screenshot, screenshot_to_message
            emit({"type": "stage", "stage": "vision", "model": os.getenv("VISION_MODEL", "openbmb/MiniCPM-V-4_5")})
            reading = read_screenshot(*image)
            emit({"type": "vision", "model": reading.get("model"), "latency_s": reading.get("latency_s"),
                  "sender": reading.get("sender"), "text": reading.get("text", "")[:1500],
                  "is_message": reading.get("is_message", True)})
            if not reading.get("text"):
                emit({"type": "error", "message": "I couldn't find any message text in that image. Try a clearer screenshot."})
                return
            message = screenshot_to_message(reading)
        emit({"type": "stage", "stage": "triage", "model": os.getenv("NANO_MODEL")})
        first = triage(message)
        emit({"type": "triage", "verdict": first.get("verdict"), "confidence": first.get("confidence"),
              "scam_type": first.get("scam_type"), "language": first.get("language"),
              "red_flags": (first.get("red_flags") or [])[:5], "latency_s": first.get("latency_s"),
              "route": first.get("route"), "route_reason": route_reason(first, message)})

        if first.get("route") == "final":
            result, who = first, "nano"
        else:
            emit({"type": "stage", "stage": "agent", "model": os.getenv("ULTRA_MODEL")})
            result, who = investigate(message, first, on_event=emit), "ultra_agent"

        elapsed = time.time() - started
        from safety import harm_of, safety_lines
        harm = harm_of(result, first)
        emit({"type": "result", "id": check_id, "decided_by": who, "verdict": result.get("verdict"),
              "harmful": harm, "safety": safety_lines(harm),
              "confidence": result.get("confidence"), "scam_type": result.get("scam_type"),
              "reason": result.get("reason"), "reply": result.get("reply"),
              "evidence": (result.get("evidence") or [])[:3], "search_count": result.get("search_count", 0),
              "dropped_citations": result.get("dropped_citations", 0), "total_latency_s": round(elapsed, 2)})
        log_check("web", "web-demo", message, first, result, who, elapsed, check_id, user=visitor)
    except Exception as exc:  # never leave the page hanging
        print("Demo pipeline error:", exc)
        emit({"type": "error", "message": "The check could not be completed. Please try again in a moment."})
    finally:
        emit(None)


@router.get("/demo", response_class=HTMLResponse)
def demo_page():
    with open(HTML_PATH, encoding="utf-8") as f:
        html = f.read()
    waitlist = os.getenv("WAITLIST_URL", "").strip()
    if waitlist:  # set in .env / Render so the link never has to be edited in the page itself
        html = html.replace('const WAITLIST_URL = "";', f"const WAITLIST_URL = {json.dumps(waitlist)};")
    return html


@router.post("/demo/check")
async def demo_check(request: Request):
    body = await request.json()
    message = str(body.get("message", "")).strip()
    image = None
    data_url = str(body.get("image") or "")
    if data_url:
        match = re.match(r"^data:(image/(?:jpeg|png|webp));base64,(.+)$", data_url, re.S)
        if not match:
            raise HTTPException(status_code=400, detail="Please upload a JPG, PNG or WebP screenshot.")
        try:
            raw = base64.b64decode(match.group(2), validate=False)
        except ValueError:
            raise HTTPException(status_code=400, detail="That image could not be read.")
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Please use a screenshot under 5 MB.")
        image = (raw, match.group(1))
    if not message and not image:
        raise HTTPException(status_code=400, detail="Paste a message or add a screenshot to check.")
    if len(message) > MAX_CHARS:
        raise HTTPException(status_code=400, detail=f"Please keep it under {MAX_CHARS} characters.")
    if not _allowed(_client_ip(request)):
        raise HTTPException(status_code=429, detail="You've run a lot of checks — please wait a few minutes.")
    if not take_check():
        raise HTTPException(status_code=429, detail=CAP_MESSAGE)

    out: queue.Queue = queue.Queue()
    threading.Thread(target=_run, args=(message, out, image, _visitor(body.get("visitor"))), daemon=True).start()

    def stream():
        while True:
            event = out.get()
            if event is None:
                break
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/demo/visit")
async def demo_visit(request: Request):
    """Counts people, not page loads: one record per browser every 30 minutes, no sign-in, no cookies."""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False}
    visitor = _visitor(body.get("visitor"))
    if not visitor:
        return {"ok": False}
    now = time.time()
    with _visit_lock:
        if now - _last_visit.get(visitor, 0) < 1800:
            return {"ok": True}
        _last_visit[visitor] = now
        if len(_last_visit) > 20000:
            _last_visit.clear()
    threading.Thread(target=log_visit, args=(visitor, _source(body.get("source"))), daemon=True).start()
    return {"ok": True}


@router.post("/demo/feedback")
async def demo_feedback(request: Request):
    body = await request.json()
    check_id = str(body.get("id", ""))[:40]
    if not check_id:
        raise HTTPException(status_code=400, detail="Missing check id.")
    log_feedback(check_id, bool(body.get("helpful")), "web")
    return {"ok": True}


PRIVACY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "privacy.html")
BRAND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "brand")


@router.get("/brand/{name}", include_in_schema=False)
def brand_file(name: str):
    """Public tech-stack logo tiles used on the demo page (assets/brand/*.png)."""
    path = os.path.join(BRAND_DIR, name)
    if not re.fullmatch(r"[a-z0-9-]+\.png", name) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page():
    with open(PRIVACY_PATH, encoding="utf-8") as f:
        html = f.read()
    email = os.getenv("PRIVACY_EMAIL", "").strip()
    return html.replace("privacy@facetrust.ai", email) if email else html


@router.get("/", include_in_schema=False)
def home():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/demo")
