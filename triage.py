"""Xvimo triage step: Nemotron Nano reads a message and returns structured analysis."""
import json
import os
import re
import time

from dotenv import load_dotenv
from openai import (APIConnectionError, BadRequestError, InternalServerError, OpenAI,
                    RateLimitError)

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
  "reason": one plain sentence a non-technical person can understand,
  "reply": a short WhatsApp-style message to the user (max 50 words), written in the SAME language as the message
           (Pidgin for Pidgin, Yoruba for Yoruba, etc.), stating the verdict, the key reason and one safety tip
}

Scam type definitions (pick the closest):
- ponzi: invest or deposit money to receive guaranteed, high or fast returns, often with recruiting or "limited slots".
- advance_fee: pay a fee to RECEIVE money you were promised (inheritance, grant, package, visa, scholarship).
- fake_loan: pay a fee to get a loan, or threats about an unpaid loan.
- fake_job: job, interview or "career chat" invitation from an unknown firm, or a fee to get a job.
- impersonation: pretends to be a bank, CBN, EFCC, a relative with a "new number", or someone who "sent money by mistake".
- phishing_link: a link that asks for login, card, BVN, NIN or OTP details.
- fake_giveaway: you "won" a prize or promo and must pay or share details to claim it.
- romance: online relationship that leads to requests for money or gift fees.

CORE RULE: A message is only "suspicious" or "likely_scam" if it ASKS the reader to do something risky
(send money or a fee, share a PIN/OTP/BVN/password/card details, click a link to log in or pay, call an unknown
number about their account, attend an unverified paid "interview") OR promises unrealistic gains.
Missing details, short or vague text, informal tone, typos, or an unfamiliar sender are NOT red flags by themselves.
If nothing risky is requested and nothing unrealistic is promised, the verdict is "no_red_flags".

Known LEGITIMATE patterns (verdict "no_red_flags" unless they also break the core rule):
- OTP or verification codes ("[CODE] is your code, do not share it"): legitimate. Only suspicious if someone asks you to SEND or FORWARD the code to them.
- Bank credit, debit, reversal or balance alerts that request no action and contain no link or phone number to call.
- Telco notices: data or airtime purchases, renewals, expiry reminders, airtime borrowing deducted on next recharge, USSD codes like *323#.
- Delivery, ride, food order, utility token, subscription and tax payment confirmations.
- A person you dealt with asking to be paid for goods or work already done (tailor, mechanic, repairer, shop).
- Bank or company messages that WARN about fraud ("we will never ask for your PIN").
- Normal chats with family, friends, church, mosque, school or community groups.

Guidance:
- Red flags include impossible or guaranteed returns, upfront fees to receive money or a loan or a job,
  urgency and limited slots, requests for PIN/OTP/BVN or card details, look-alike links,
  someone claiming a new number who asks for money, and threats.
- Use "suspicious" when there are some warning signs but you cannot be sure.
- Placeholders like [NAME], [PHONE], [ACCOUNT], [EMAIL] and [AMOUNT] hide private details. The ACTION around them
  still counts: "call [PHONE] to reactivate your card" is a request to call an unknown number about your account.
- Unsolicited job, interview, "aptitude test", "career chat" or "work brief" invitations from firms the reader never
  applied to are a well-known Nigerian scam pattern (they lead to fees or pyramid schemes). Mark these at least
  "suspicious", even when no money is requested yet.
- Any message saying an account, ATM card or BVN is blocked, deactivated or needs updating, and asking the reader
  to call, click or reply, is at least "suspicious"."""

ALLOWED_VERDICTS = {"likely_scam", "suspicious", "no_red_flags"}
CONFIDENT = 0.75  # Nano decides alone only when it is this confident


# Safety net: if the message touches a high-risk topic, Nano may not clear it alone.
RISK_PATTERN = re.compile(
    r"interview|recruit|aptitude|shortlist|vacanc|bvn|atm|block|deactivat|suspend|reactivat|invest|"
    r"profit|return|roi|loan|grant|won|win |prize|promo|bonus|giveaway|urgent|click|http|www\.|"
    r"verify|password|pin\b|otp|code|send .*money|fee|pay ",
    re.I,
)


def route_for(result: dict, message: str = "") -> str:
    """Decide in code (not by the model) whether Nano's answer is final or needs Tavily + Ultra.

    A missed scam is worse than a false alarm, so Nano can only clear a message as safe on its own
    when it is confident AND the message avoids every high-risk topic. Otherwise Ultra double-checks.
    """
    if result.get("verdict") == "no_red_flags" and RISK_PATTERN.search(message or ""):
        return "escalate"
    try:
        conf = float(result.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    if result.get("verdict") in ("likely_scam", "no_red_flags") and conf >= CONFIDENT:
        return "final"
    return "escalate"


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of the model's reply, ignoring any reasoning text."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model reply")
    return json.loads(text[start : end + 1])


RETRY_WAITS = [5, 15, 30]  # seconds to wait before each retry when the server is busy


def _create(params: dict):
    """Call the API, retrying when the server is overloaded (429/503) or the connection drops."""
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            try:
                # Ask Nano to skip long reasoning for speed; fall back if the option is not supported.
                return client.chat.completions.create(
                    **params, extra_body={"chat_template_kwargs": {"enable_thinking": False}}
                )
            except BadRequestError:
                return client.chat.completions.create(**params)
        except (RateLimitError, InternalServerError, APIConnectionError):
            if attempt == len(RETRY_WAITS):
                raise
            wait = RETRY_WAITS[attempt]
            print(f"      server busy, retrying in {wait}s...")
            time.sleep(wait)


def _call_model(message: str):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Message to check:\n\"\"\"\n{message}\n\"\"\""},
    ]
    return _create(dict(model=NANO_MODEL, messages=messages, temperature=0.0, max_tokens=1500))


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

    result["route"] = route_for(result, message)
    usage = getattr(response, "usage", None)
    result["latency_s"] = latency
    result["input_tokens"] = getattr(usage, "prompt_tokens", None)
    result["output_tokens"] = getattr(usage, "completion_tokens", None)
    result["model"] = NANO_MODEL
    return result


if __name__ == "__main__":
    demo = "Invest N50,000 today and earn N200,000 weekly. Limited slots, pay now!"
    print(json.dumps(triage(demo), indent=2, ensure_ascii=False))