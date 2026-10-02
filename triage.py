"""Xvimo triage step: Nemotron Nano reads a message and returns structured analysis."""
import json
import os
import re
import time

from dotenv import load_dotenv
from openai import OpenAI, BadRequestError

load_dotenv(override=True)

client = OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("LLM_API_KEY"))
NANO_MODEL = os.getenv("NANO_MODEL")

SYSTEM_PROMPT = """You are Xvimo, a fraud analyst protecting ordinary people in Nigeria and across Africa.
You receive one message (SMS, WhatsApp, social post or email) that a user wants checked.
Analyse it and reply with ONLY a JSON object, no other text, using exactly these keys:

{
  "verdict": "likely_scam" | "suspicious" | "no_red_flags",
  "confidence": number between 0 and 1,
  "scam_type": "ponzi" | "impersonation" | "fake_job" | "fake_loan" | "phishing_link" | "romance" | "fake_giveaway" | "advance_fee" | "other" | "none",
  "language": "english" | "pidgin" | "yoruba" | "hausa" | "igbo" | "mixed" | "other",
  "organizations": [names of companies, banks, agencies or platforms mentioned],
  "urls": [links found in the message],
  "money_requested": true or false,
  "promised_returns": short text of any promised profit or prize, or "",
  "red_flags": [short phrases, at most 5],
  "needs_web_check": true if an organization, platform or link should be verified online,
  "needs_escalation": true if the case is ambiguous and needs deeper reasoning,
  "reason": one plain sentence a non-technical person can understand
}

Guidance:
- Genuine bank alerts, OTP codes, delivery updates, utility tokens and normal family or business chats are usually legitimate.
- A legitimate message may WARN about fraud (e.g. "we will never ask for your PIN"); that is not a scam.
- Red flags include impossible or guaranteed returns, upfront fees to receive money or a loan or a job,
  urgency and limited slots, requests for PIN/OTP/BVN or card details, look-alike links,
  someone claiming a new number who asks for money, and threats.
- Use "suspicious" when there are some warning signs but you cannot be sure.
- Placeholders like [NAME], [PHONE], [ACCOUNT] and [AMOUNT] are redactions; ignore them as evidence."""

ALLOWED_VERDICTS = {"likely_scam", "suspicious", "no_red_flags"}


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of the model's reply, ignoring any reasoning text."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model reply")
    return json.loads(text[start : end + 1])


def _call_model(message: str):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Message to check:\n\"\"\"\n{message}\n\"\"\""},
    ]
    params = dict(model=NANO_MODEL, messages=messages, temperature=0.2, max_tokens=1500)
    try:
        # Ask Nano to skip long reasoning for speed; fall back if the option is not supported.
        return client.chat.completions.create(
            **params, extra_body={"chat_template_kwargs": {"enable_thinking": False}}
        )
    except BadRequestError:
        return client.chat.completions.create(**params)


def triage(message: str) -> dict:
    """Run Nano triage on one message. Returns the analysis plus timing and token usage."""
    started = time.time()
    response = _call_model(message)
    latency = round(time.time() - started, 2)

    raw = response.choices[0].message.content or ""
    try:
        result = _extract_json(raw)
        if result.get("verdict") not in ALLOWED_VERDICTS:
            result["verdict"] = "suspicious"
        result["parse_ok"] = True
    except (ValueError, json.JSONDecodeError):
        result = {"verdict": "suspicious", "reason": "Model reply could not be parsed", "parse_ok": False}

    usage = getattr(response, "usage", None)
    result["latency_s"] = latency
    result["input_tokens"] = getattr(usage, "prompt_tokens", None)
    result["output_tokens"] = getattr(usage, "completion_tokens", None)
    result["model"] = NANO_MODEL
    return result


if __name__ == "__main__":
    demo = "Invest N50,000 today and earn N200,000 weekly. Limited slots, pay now!"
    print(json.dumps(triage(demo), indent=2, ensure_ascii=False))
