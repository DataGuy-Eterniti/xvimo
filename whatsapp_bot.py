"""Xvimo WhatsApp bot: receives messages from the WhatsApp Cloud API and replies with a verdict.

Run locally:   uvicorn whatsapp_bot:app --port 8000
Then expose it with a public HTTPS tunnel and set the webhook URL in the Meta dashboard.
"""
import hashlib
import hmac
import os
import threading
from collections import OrderedDict

import requests
from dotenv import load_dotenv

from safety import HARM_HEADLINE, harm_of, safety_lines
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from xvimo import check

load_dotenv(override=True)

TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "xvimo-verify-2026")
APP_SECRET = os.getenv("WHATSAPP_APP_SECRET")  # optional: enables signature checking
GRAPH = "https://graph.facebook.com/v23.0"

app = FastAPI(title="Xvimo WhatsApp bot")

# Meta sometimes delivers the same message twice; remember recent IDs so we answer once.
_seen = OrderedDict()
_seen_lock = threading.Lock()

HEADLINE = {
    "likely_scam": "🚨 *LIKELY SCAM*",
    "suspicious": "⚠️ *SUSPICIOUS — be careful*",
    "no_red_flags": "✅ *No red flags found*",
}

WELCOME = (
    "👋 *Welcome to Xvimo* — scam check before you pay.\n\n"
    "Forward or paste any suspicious message, investment offer, job invite or link, "
    "and I'll tell you if it looks like a scam, with the reasons and evidence.\n\n"
    "_Xvimo gives guidance, not a guarantee. When money is involved, always confirm "
    "through official channels._"
)


# ---------------------------------------------------------------- WhatsApp helpers
def send_text(to: str, body: str) -> None:
    """Send a plain text WhatsApp message."""
    resp = requests.post(
        f"{GRAPH}/{PHONE_NUMBER_ID}/messages",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"messaging_product": "whatsapp", "to": to, "type": "text",
              "text": {"preview_url": False, "body": body[:4000]}},
        timeout=30,
    )
    if resp.status_code != 200:
        print("Send failed:", resp.status_code, resp.text[:300])


def format_reply(result: dict) -> str:
    """Turn a pipeline result into a WhatsApp-friendly message."""
    harm = harm_of(result, result.get("triage"))
    headline = HEADLINE.get(result.get("verdict"), "⚠️ *Could not decide*")
    if harm != "none":
        headline = (HARM_HEADLINE[harm] if result.get("verdict") == "no_red_flags"
                    else f"{headline} · {HARM_HEADLINE[harm].strip('*⚠️🚩 ')}")
    lines = [headline, ""]
    lines.append(result.get("reply") or result.get("reason") or "")
    lines += ["", *safety_lines(harm)] if harm != "none" else []
    sources = [e for e in (result.get("evidence") or []) if e.get("url")][:2]
    if sources:
        lines += ["", "*Evidence:*"]
        for e in sources:
            lines.append(f"• {e.get('finding', '').strip()}\n  {e['url']}")
    who = "Nemotron Nano" if result.get("decided_by") == "nano" else "Nemotron Ultra agent + web search"
    lines += ["", f"_Checked by Xvimo · {who}_"]
    return "\n".join(line for line in lines if line is not None).strip()


def is_duplicate(message_id: str) -> bool:
    with _seen_lock:
        if message_id in _seen:
            return True
        _seen[message_id] = True
        if len(_seen) > 500:
            _seen.popitem(last=False)
        return False


def signature_ok(raw_body: bytes, header: str) -> bool:
    """Verify Meta's X-Hub-Signature-256 header when an app secret is configured."""
    if not APP_SECRET:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


# ---------------------------------------------------------------- background work
def handle_text(sender: str, text: str, skip_greeting: bool = False) -> None:
    """Run the full Xvimo pipeline and reply. Runs after Meta has already received its 200 OK."""
    if not skip_greeting and text.strip().lower() in {"hi", "hello", "hey", "start", "help", "menu"}:
        send_text(sender, WELCOME)
        return
    send_text(sender, "🔍 Checking this message… this can take up to a minute for tricky cases.")
    try:
        result = check(text)
        send_text(sender, format_reply(result))
        print(f"[{sender[-4:]}] {result.get('verdict')} via {result.get('decided_by')} "
              f"in {result.get('total_latency_s')}s")
    except Exception as exc:
        print("Pipeline error:", exc)
        send_text(sender, "Sorry, I couldn't finish checking that right now. Please try again in a moment.")


def fetch_media(media_id: str) -> tuple:
    """Meta Cloud API: look up the media URL, then download it with the same access token."""
    auth = {"Authorization": f"Bearer {TOKEN}"}
    meta = requests.get(f"{GRAPH}/{media_id}", headers=auth, timeout=30)
    meta.raise_for_status()
    info = meta.json()
    img = requests.get(info["url"], headers=auth, timeout=30)
    img.raise_for_status()
    return img.content, (info.get("mime_type") or img.headers.get("Content-Type", "image/jpeg")).split(";")[0]


def handle_image(sender: str, image: dict) -> None:
    """A forwarded screenshot: read it with the vision model, then judge the text with Nemotron."""
    from vision import read_screenshot, screenshot_to_message
    try:
        send_text(sender, "📸 Reading your screenshot…")
        data, mime = fetch_media(image.get("id", ""))
        reading = read_screenshot(data, mime)
        if not reading.get("text"):
            send_text(sender, "I couldn't find any message text in that image. Please send a clearer screenshot, or paste the text.")
            return
        message = screenshot_to_message(reading)
        if image.get("caption"):
            message += f"\n(User's caption: {image['caption']})"
        handle_text(sender, message, skip_greeting=True)
    except Exception as exc:
        print("Screenshot error:", exc)
        send_text(sender, "Sorry, I couldn't read that screenshot. Please try again, or paste the message text.")


# ---------------------------------------------------------------- routes
@app.get("/")
def health():
    return {"status": "ok", "service": "xvimo"}


@app.get("/webhook")
def verify(request: Request):
    """Meta calls this once to confirm the webhook URL belongs to us."""
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def receive(request: Request, background: BackgroundTasks):
    """Meta sends every incoming WhatsApp message here. Reply 200 fast; do the work in the background."""
    raw = await request.body()
    if not signature_ok(raw, request.headers.get("X-Hub-Signature-256", "")):
        raise HTTPException(status_code=401, detail="Bad signature")

    data = await request.json()
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                if is_duplicate(msg.get("id", "")):
                    continue
                sender, kind = msg.get("from"), msg.get("type")
                if kind == "text":
                    background.add_task(handle_text, sender, msg["text"]["body"])
                elif kind == "image":
                    background.add_task(handle_image, sender, msg.get("image", {}))
                else:
                    background.add_task(send_text, sender, "Please send the suspicious message as text.")
    return {"status": "received"}