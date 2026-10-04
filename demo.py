"""Xvimo live web demo: paste a message and watch the agent work, step by step.

Routes (mounted by the bot):
    GET  /demo        the demo page
    POST /demo/check  runs the pipeline and streams progress as newline-delimited JSON
"""
import json
import os
import queue
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from events import log_check

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


def _run(message: str, out: queue.Queue) -> None:
    """Runs the full pipeline in a worker thread, pushing progress events into the queue."""
    from investigator import investigate  # imported lazily so the page loads even without model keys
    from triage import route_reason, triage

    emit = out.put
    started = time.time()
    try:
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
        emit({"type": "result", "decided_by": who, "verdict": result.get("verdict"),
              "confidence": result.get("confidence"), "scam_type": result.get("scam_type"),
              "reason": result.get("reason"), "reply": result.get("reply"),
              "evidence": (result.get("evidence") or [])[:3], "search_count": result.get("search_count", 0),
              "dropped_citations": result.get("dropped_citations", 0), "total_latency_s": round(elapsed, 2)})
        log_check("web", "web-demo", message, first, result, who, elapsed)
    except Exception as exc:  # never leave the page hanging
        print("Demo pipeline error:", exc)
        emit({"type": "error", "message": "The check could not be completed. Please try again in a moment."})
    finally:
        emit(None)


@router.get("/demo", response_class=HTMLResponse)
def demo_page():
    with open(HTML_PATH, encoding="utf-8") as f:
        return f.read()


@router.post("/demo/check")
async def demo_check(request: Request):
    body = await request.json()
    message = str(body.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="Paste a message to check.")
    if len(message) > MAX_CHARS:
        raise HTTPException(status_code=400, detail=f"Please keep it under {MAX_CHARS} characters.")
    if not _allowed(_client_ip(request)):
        raise HTTPException(status_code=429, detail="You've run a lot of checks — please wait a few minutes.")

    out: queue.Queue = queue.Queue()
    threading.Thread(target=_run, args=(message, out), daemon=True).start()

    def stream():
        while True:
            event = out.get()
            if event is None:
                break
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
