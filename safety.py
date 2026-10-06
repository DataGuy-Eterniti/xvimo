"""Shared wording for harmful (abusive, sexual or threatening) messages, used by every channel."""

HARM_HEADLINE = {
    "harassment": "⚠️ *Abusive message*",
    "sexual": "⚠️ *Sexual harassment*",
    "threat": "🚩 *Threatening message*",
}
HARM_LABEL = {"harassment": "Abusive message", "sexual": "Sexual harassment", "threat": "Threatening message"}

SAFETY_TIP = ("You don't have to reply. In WhatsApp you can block and report the sender: open the chat, tap their "
              "name or number, then Block and Report.")
THREAT_TIP = ("If you feel unsafe or are being blackmailed, don't send money or images. Keep the messages as evidence, "
              "talk to someone you trust, and contact the police.")


def harm_of(result: dict, first: dict = None) -> str:
    """The harmful flag for a check, taken from the final result or, failing that, from triage."""
    for source in (result or {}, first or {}):
        value = source.get("harmful")
        if value in HARM_HEADLINE:
            return value
    return "none"


def safety_lines(harm: str) -> list:
    if harm == "none":
        return []
    return [SAFETY_TIP] + ([THREAT_TIP] if harm == "threat" else [])
