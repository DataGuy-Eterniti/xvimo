"""Daily spending protection shared by every channel.

DAILY_CHECK_CAP (in .env or the endpoint's environment) limits total checks per UTC day across the web demo
and WhatsApp, so a sudden surge cannot drain the Nebius credits. The counter resets each day and on restart.
"""
import os
import threading
from datetime import datetime, timezone

DAILY_CHECK_CAP = int(os.getenv("DAILY_CHECK_CAP", "600"))
_state = {"day": None, "count": 0}
_lock = threading.Lock()


def take_check() -> bool:
    """Reserve one check for today. Returns False once today's cap is reached."""
    today = datetime.now(timezone.utc).date().isoformat()
    with _lock:
        if _state["day"] != today:
            _state.update(day=today, count=0)
        if _state["count"] >= DAILY_CHECK_CAP:
            return False
        _state["count"] += 1
        return True


def usage() -> dict:
    with _lock:
        return {"day": _state["day"], "count": _state["count"], "cap": DAILY_CHECK_CAP}


CAP_MESSAGE = ("Xvimo has reached today's limit of free checks — thank you for the interest! "
               "Please try again tomorrow. When in doubt, don't send money and confirm through official channels.")
