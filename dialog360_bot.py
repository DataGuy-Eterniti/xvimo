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

from limits import CAP_MESSAGE, take_check
from safety import HARM_HEADLINE, harm_of, safety_lines
from fastapi import BackgroundTasks, FastAPI, Request

from dashboard import router as dashboard_router
from demo import router as demo_router
from events import log_check

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
    "Forward, paste or screenshot any suspicious message, investment offer, job invite or link, and I'll tell you "
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
app.include_router(dashboard_router)
app.include_router(demo_router)
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


def format_reply(result: dict, decided_by: str, first: dict = None) -> str:
    harm = harm_of(result, first)
    headline = HEADLINE.get(result.get("verdict"), "⚠️ *Could not decide*")
    if harm != "none":
        headline = (HARM_HEADLINE[harm] if result.get("verdict") == "no_red_flags"
                    else f"{headline} · {HARM_HEADLINE[harm].strip('*⚠️🚩 ')}")
    lines = [headline, ""]
    lines.append(result.get("reply") or result.get("reason") or "")
    lines += ["" , *safety_lines(harm)] if harm != "none" else []
    sources = [e for e in (result.get("evidence") or []) if e.get("url")][:2]
    if sources:
        lines += ["", "*Evidence:*"]
        lines += [f"• {e.get('finding', '').strip()}\n  {e['url']}" for e in sources]
    who = "Nemotron Nano" if decided_by == "nano" else "Nemotron Ultra agent + web search"
    lines += ["", f"_Checked by Xvimo · {who}_"]
    return "\n".join(lines).strip()


def handle_text(sender: str, text: str, skip_greeting: bool = False) -> None:
    from investigator import investigate  # imported here so set-webhook works without model keys
    from triage import triage

    if not skip_greeting and text.strip().lower() in GREETINGS:
        send_text(sender, WELCOME)
        return
    if not take_check():
        send_text(sender, CAP_MESSAGE)
        return
    started = time.time()
    try:
        first = triage(text)
        if first.get("route") == "final":
            result, who = first, "nano"
        else:
            send_text(sender, "🔍 Investigating this one with web search… give me up to a minute.")
            result, who = investigate(text, first), "ultra_agent"
        send_text(sender, format_reply(result, who, first))
        elapsed = time.time() - started
        log_check("whatsapp", sender, text, first, result, who, elapsed)
        print(f"[{sender[-4:]}] {result.get('verdict')} via {who} in {round(elapsed, 1)}s")
    except Exception as exc:
        print("Pipeline error:", exc)
        send_text(sender, "Sorry, I couldn't finish checking that right now. Please try again in a moment.")


def fetch_media(media_id: str, webhook_url: str = "") -> tuple:
    """Download an image a user sent through the 360dialog sandbox.

    360dialog may answer in two styles, so both are tried:
      1. On-premise style: GET {BASE}/media/{id} returns the image bytes directly.
      2. Cloud-API style:  GET {host}/{id} returns JSON with a "url"; that url (with Meta's host swapped for
         360dialog's) returns the bytes. Every attempt is logged so failures are easy to diagnose.
    """
    auth = {"D360-API-KEY": API_KEY or ""}
    host = BASE.rsplit("/v1", 1)[0]
    # Best option: the download link 360dialog puts in the webhook itself, requested through 360dialog's host.
    if webhook_url:
        for link in (webhook_url.replace("https://lookaside.fbsbx.com", host), webhook_url):
            try:
                img = requests.get(link, headers=auth, timeout=30)
                ctype = img.headers.get("Content-Type", "")
                print(f"  media GET {link[:70]}… -> {img.status_code} {ctype}")
                if img.ok and (ctype.startswith("image/") or ctype.startswith("application/octet-stream")):
                    return img.content, (ctype.split(";")[0] if ctype.startswith("image/") else "image/jpeg")
            except requests.RequestException as exc:
                print(f"  media GET failed: {exc}")
    attempts = [f"{BASE}/media/{media_id}", f"{host}/{media_id}"]
    for url in attempts:
        try:
            resp = requests.get(url, headers=auth, timeout=30)
        except requests.RequestException as exc:
            print(f"  media GET {url} failed: {exc}")
            continue
        ctype = resp.headers.get("Content-Type", "")
        print(f"  media GET {url} -> {resp.status_code} {ctype}")
        if resp.ok and ctype.startswith("image/"):
            return resp.content, ctype.split(";")[0]
        if resp.ok and "json" in ctype:
            link = resp.json().get("url")
            if link:
                link = link.replace("https://lookaside.fbsbx.com", host)
                img = requests.get(link, headers=auth, timeout=30)
                print(f"  media GET {link[:80]}… -> {img.status_code} {img.headers.get('Content-Type', '')}")
                if img.ok and img.headers.get("Content-Type", "").startswith("image/"):
                    return img.content, img.headers["Content-Type"].split(";")[0]
        elif not resp.ok:
            print(f"  body: {resp.text[:200]}")
    raise RuntimeError("Could not download the image from 360dialog (see the media lines above).")


def handle_image(sender: str, image: dict) -> None:
    """A forwarded screenshot: read it with the vision model, then judge the text with Nemotron."""
    from vision import read_screenshot, screenshot_to_message
    try:
        send_text(sender, "📸 Reading your screenshot…")
        print(f"Screenshot received from …{sender[-4:]}: {image}")
        data, mime = fetch_media(image.get("id", ""), image.get("url", ""))
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


@app.get("/health")
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
            # The 360dialog sandbox cannot download media (documented limitation). Screenshots work on the
            # web demo and on the production WhatsApp number (whatsapp_bot.py, Meta Cloud API).
            caption = (msg.get("image") or {}).get("caption", "")
            if caption.strip():
                background.add_task(handle_text, sender, caption, True)
            else:
                background.add_task(send_text, sender,
                    "📸 Screenshot checks on WhatsApp arrive with our public launch. For now, paste the message text here, "
                    "or upload the screenshot on the Xvimo web demo.")
    return {"status": "received"}


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "set-webhook":
        set_webhook(sys.argv[2])
    else:
        print("Usage: python dialog360_bot.py set-webhook https://<tunnel>/webhook")
