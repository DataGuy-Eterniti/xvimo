"""Records every real Xvimo check to data/events.jsonl for the admin dashboard.

Privacy: only the last 4 digits of the sender are kept, and long digit runs in the message preview
(phone, account and card numbers) are masked before anything is written.
"""
import json
import os
import re
import threading
from datetime import datetime, timezone

from paths import EVENTS_PATH, FEEDBACK_PATH
_lock = threading.Lock()


def _mask(text: str) -> str:
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[EMAIL]", text or "")
    text = re.sub(r"\+?\d[\d\s-]{6,}\d", "[NUMBER]", text)
    return text[:160]


def log_check(channel: str, sender: str, message: str, first: dict, result: dict,
              decided_by: str, total_latency: float, check_id: str = "") -> None:
    """Append one check to the event log. Never raises: logging must not break replies."""
    try:
        agent = result if decided_by == "ultra_agent" else {}
        record = {
            "id": check_id,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "channel": channel,
            "sender": (sender or "")[-4:],
            "preview": _mask(message),
            "language": first.get("language"),
            "verdict": result.get("verdict"),
            "scam_type": result.get("scam_type") or first.get("scam_type"),
            "harmful": result.get("harmful") or first.get("harmful") or "none",
            "decided_by": decided_by,
            "triage_verdict": first.get("verdict"),
            "triage_confidence": first.get("confidence"),
            "nano_latency_s": first.get("latency_s"),
            "total_latency_s": round(total_latency, 2),
            "nano_in": first.get("input_tokens") or 0,
            "nano_out": first.get("output_tokens") or 0,
            "ultra_in": agent.get("input_tokens") or 0,
            "ultra_out": agent.get("output_tokens") or 0,
            "searches": [{"tool": s.get("tool"), "results": s.get("results")} for s in agent.get("searches", [])],
            "citations": len(agent.get("evidence") or []),
            "dropped_citations": agent.get("dropped_citations", 0),
        }
        os.makedirs(os.path.dirname(EVENTS_PATH) or ".", exist_ok=True)
        with _lock, open(EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # pragma: no cover
        print("Event log failed:", exc)


def load_events() -> list:
    if not os.path.exists(EVENTS_PATH):
        return []
    out = []
    with open(EVENTS_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def log_feedback(check_id: str, helpful: bool, channel: str = "web") -> None:
    """Record a 👍/👎 rating for a check. Never raises."""
    try:
        os.makedirs(os.path.dirname(FEEDBACK_PATH) or ".", exist_ok=True)
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "id": check_id[:40],
               "helpful": bool(helpful), "channel": channel}
        with _lock, open(FEEDBACK_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as exc:  # pragma: no cover
        print("Feedback log failed:", exc)


def load_feedback() -> list:
    if not os.path.exists(FEEDBACK_PATH):
        return []
    out = []
    with open(FEEDBACK_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
