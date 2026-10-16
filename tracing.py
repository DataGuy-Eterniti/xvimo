"""Optional LangSmith tracing for Xvimo.

Off by default. Turn it on by setting these (in .env for benchmark runs):
    LANGSMITH_TRACING=true
    LANGSMITH_API_KEY=lsv2_...
    LANGSMITH_PROJECT=xvimo-benchmark

When on, every step is recorded: Nano triage, the routing decision, each Ultra agent turn, every Tavily search
and the screenshot reader. Before anything leaves this machine, phone/account numbers, emails and screenshot
image data are masked. Keep it OFF on the public server unless the privacy page says traces are kept.
"""
import os
import re

from dotenv import load_dotenv

load_dotenv(override=True)

ENABLED = (os.getenv("LANGSMITH_TRACING", "").strip().lower() == "true"
           and bool(os.getenv("LANGSMITH_API_KEY", "").strip()))

_DATA_URL = re.compile(r"data:image/[a-zA-Z]+;base64,[A-Za-z0-9+/=\s]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_NUMBER = re.compile(r"\+?\d[\d\s-]{6,}\d")


def mask(value):
    """Recursively hide screenshots, emails and long digit runs (phone, account, card, BVN numbers)."""
    if isinstance(value, str):
        value = _DATA_URL.sub("[screenshot]", value)
        value = _EMAIL.sub("[EMAIL]", value)
        return _NUMBER.sub("[NUMBER]", value)
    if isinstance(value, (bytes, bytearray)):
        return f"[{len(value)} bytes of image data]"
    if isinstance(value, dict):
        return {k: mask(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask(v) for v in value]
    return value


_client = None


def _ls_client():
    global _client
    if _client is None:
        from langsmith import Client
        _client = Client(hide_inputs=mask, hide_outputs=mask)
    return _client


def wrap(openai_client):
    """Record every model call made through this OpenAI-compatible client (Nebius Token Factory)."""
    if not ENABLED:
        return openai_client
    from langsmith.wrappers import wrap_openai
    return wrap_openai(openai_client, tracing_extra={"client": _ls_client(), "tags": ["xvimo"]})


def traceable(name: str, run_type: str = "chain"):
    """Decorator that records one Xvimo step. Does nothing at all when tracing is off."""
    if not ENABLED:
        return lambda fn: fn
    from langsmith import traceable as _traceable
    return _traceable(name=name, run_type=run_type, client=_ls_client())


def flush():
    """Send any queued traces before a script exits."""
    if ENABLED and _client is not None:
        try:
            _client.flush()
        except Exception as exc:  # tracing must never break Xvimo
            print("LangSmith flush failed:", exc)
