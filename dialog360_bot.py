"""Xvimo on WhatsApp via the free 360dialog Sandbox (no signup form, no card).

Get a key:     send START (uppercase) on WhatsApp to the 360dialog sandbox number; it replies with your API key.
Run the bot:   python -m uvicorn dialog360_bot:app --port 8000
Set webhook:   python dialog360_bot.py set-webhook https://<your-tunnel>.trycloudflare.com/webhook
"""
import os
import sys
import threading
import time
from collections import OrderedDict

import requests
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request

load_dotenv(override=True)

API_KEY = os.getenv("D360_API_KEY")
BASE = "https://waba-sandbox.360dialog.io/v1"
HEADERS = {"D360-API-KEY": API_KEY or "", "Content-Type": "application/json"}

HEADLINE = {
    "likely_scam": "🚨 *LIKELY SCAM*",
    "suspicious": "⚠️ *SUSPICIOUS — be careful*",
    "no_red_flags": "✅ *No red flags found*",
}
WELCOME = (
    "👋 *Welcome to Xvimo* — scam check before you pay.\n\n"
    "Forward or paste any suspicious message, investment offer, job invite or link, and I'll tell you "
    "if it looks like a scam, with the reasons and evidence.\n\n"
    "_Xvimo gives guidance, not a guarantee. When money is involved, always confirm through official channels._"
)
GREETINGS = {"hi", "hello", "hey", "help", "menu"}  # START is reserved by the sandbox for issuing keys


# ---------------------------------------------------------------- 360dialog helpers
def set_webhook(url: str) -> None:
    resp = requests.post(f"{BASE}/configs/webhook", headers=HEADERS, json={"url": url}, timeout=30)
    print("Webhook set." if resp.status_code < 300 else f"Failed: {resp.status_code} {resp.text[:300]}")


def send_text(to: str, body: str) -> None:
    resp = requests.post(
        f"{BASE}/messages", headers=HEADERS, timeout=30,
        json={"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
              "type": "text", "text": {"body": body[:4000]}},
    )
    if resp.status_code >= 300:
        print("Send failed:", resp.status_code, resp.text[:300])


def extract_messages(data: dict) -> list:
    """Support both payload styles: Cloud API (entry/changes/value) and the older flat format."""
    found = []
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            found += change.get("value", {}).get("messages", [])
    found += data.get("messages", [])
    return found


# ---------------------------------------------------------------- FastAPI app
app = FastAPI(title="Xvimo WhatsApp bot (360dialog sandbox)")
_seen = OrderedDict()
_seen_lock = threading.Lock()


def is_duplicate(mid: str) -> bool:
    with _seen_lock:
        if mid in _seen:
            return True
        _seen[mid] = True
        if len(_seen) > 500:
            _seen.popitem(last=False)
        return False


def format_reply(result: dict, decided_by: str) -> str:
    lines = [HEADLINE.get(result.get("verdict"), "⚠️ *Could not decide*"), ""]
    lines.append(result.get("reply") or result.get("reason") or "")
    sources = [e for e in (result.get("evidence") or []) if e.get("url")][:2]
    if sources:
        lines += ["", "*Evidence:*"]
        lines += [f"• {e.get('finding', '').strip()}\n  {e['url']}" for e in sources]
    who = "Nemotron Nano" if decided_by == "nano" else "Nemotron Ultra agent + web search"
    lines += ["", f"_Checked by Xvimo · {who}_"]
    return "\n".join(lines).strip()


def handle_text(sender: str, text: str) -> None:
    from investigator import investigate  # imported here so set-webhook works without model keys
    from triage import triage

    if text.strip().lower() in GREETINGS:
        send_text(sender, WELCOME)
        return
    started = time.time()
    try:
        first = triage(text)
        if first.get("route") == "final":
            result, who = first, "nano"
        else:
            send_text(sender, "🔍 Investigating this one with web search… give me up to a minute.")
            result, who = investigate(text, first), "ultra_agent"
        send_text(sender, format_reply(result, who))
        print(f"[{sender[-4:]}] {result.get('verdict')} via {who} in {round(time.time() - started, 1)}s")
    except Exception as exc:
        print("Pipeline error:", exc)
        send_text(sender, "Sorry, I couldn't finish checking that right now. Please try again in a moment.")


@app.get("/")
def health():
    return {"status": "ok", "service": "xvimo-360dialog"}


@app.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks):
    data = await request.json()
    for msg in extract_messages(data):
        if is_duplicate(msg.get("id", "")):
            continue
        sender, kind = msg.get("from", ""), msg.get("type")
        if kind == "text":
            text = msg.get("text", {}).get("body", "")
            if text.strip() == "START":
                continue  # the sandbox handles this itself
            background.add_task(handle_text, sender, text)
        elif kind == "image":
            background.add_task(send_text, sender,
                                "📸 Screenshot checks are coming soon. For now, please paste the message text.")
    return {"status": "received"}


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "set-webhook":
        set_webhook(sys.argv[2])
    else:
        print("Usage: python dialog360_bot.py set-webhook https://<tunnel>/webhook")
