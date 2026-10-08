"""Records every real Xvimo check (and every 👍/👎 rating) for the admin dashboard.

Storage:
  * DATABASE_URL set (e.g. a free Neon Postgres) -> rows go to the database, so history survives
    restarts and redeploys on Render.
  * Otherwise -> data/events.jsonl and data/feedback.jsonl, as before (local development).
If the database is ever unreachable, the record is written to the local file instead, so nothing is lost.

Privacy: only the last 4 digits of the sender are kept, and long digit runs in the message preview
(phone, account and card numbers) are masked before anything is written.

One-time import of your local history into the database:
    python events.py import
"""
import hashlib
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone

from dotenv import load_dotenv

from paths import EVENTS_PATH, FEEDBACK_PATH, VISITS_PATH

load_dotenv(override=True)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_lock = threading.Lock()
_table_ready = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS xvimo_events (
    uid  TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    ts   TEXT NOT NULL,
    data JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS xvimo_events_kind_ts ON xvimo_events (kind, ts);
"""


# ---------------------------------------------------------------- storage helpers
def _connect():
    import psycopg  # only needed when DATABASE_URL is set
    return psycopg.connect(DATABASE_URL, connect_timeout=15, autocommit=True)


def _ensure_table(conn) -> None:
    global _table_ready
    if not _table_ready:
        conn.execute(SCHEMA)
        _table_ready = True


def _uid(kind: str, record: dict) -> str:
    """Stable id per record, so importing the same history twice never creates duplicates."""
    raw = kind + json.dumps(record, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _db_insert(kind: str, records: list) -> int:
    from psycopg.types.json import Jsonb
    with _connect() as conn:
        _ensure_table(conn)
        added = 0
        for r in records:
            cur = conn.execute(
                "INSERT INTO xvimo_events (uid, kind, ts, data) VALUES (%s, %s, %s, %s) ON CONFLICT (uid) DO NOTHING",
                (_uid(kind, r), kind, r.get("ts", ""), Jsonb(r)))
            added += cur.rowcount
        return added


def _db_load(kind: str) -> list:
    with _connect() as conn:
        _ensure_table(conn)
        rows = conn.execute("SELECT data FROM xvimo_events WHERE kind = %s ORDER BY ts", (kind,)).fetchall()
    return [r[0] for r in rows]


def _file_append(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _file_load(path: str) -> list:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _save(kind: str, path: str, record: dict) -> None:
    if DATABASE_URL:
        try:
            _db_insert(kind, [record])
            return
        except Exception as exc:
            print(f"Database write failed, keeping {kind} in local file instead:", exc)
    _file_append(path, record)


def _load(kind: str, path: str) -> list:
    if DATABASE_URL:
        try:
            return _db_load(kind)
        except Exception as exc:
            print(f"Database read failed, showing local {kind} file instead:", exc)
    return _file_load(path)


# ---------------------------------------------------------------- public API
def anon_id(channel: str, sender: str) -> str:
    """Anonymous, stable id for a WhatsApp user: a salted hash, never the number itself."""
    if not sender:
        return ""
    salt = os.getenv("HASH_SALT") or os.getenv("ADMIN_TOKEN") or "xvimo"
    return hashlib.sha256(f"{salt}:{channel}:{sender}".encode()).hexdigest()[:16]

def _mask(text: str) -> str:
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[EMAIL]", text or "")
    text = re.sub(r"\+?\d[\d\s-]{6,}\d", "[NUMBER]", text)
    return text[:160]


def log_check(channel: str, sender: str, message: str, first: dict, result: dict,
              decided_by: str, total_latency: float, check_id: str = "", user: str = "") -> None:
    """Record one check. Never raises: logging must not break replies."""
    try:
        agent = result if decided_by == "ultra_agent" else {}
        record = {
            "id": check_id,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "channel": channel,
            "user": user[:40],
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
        _save("check", EVENTS_PATH, record)
    except Exception as exc:  # pragma: no cover
        print("Event log failed:", exc)


def load_events() -> list:
    return _load("check", EVENTS_PATH)


def log_feedback(check_id: str, helpful: bool, channel: str = "web") -> None:
    """Record a 👍/👎 rating for a check. Never raises."""
    try:
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "id": check_id[:40],
               "helpful": bool(helpful), "channel": channel}
        _save("feedback", FEEDBACK_PATH, rec)
    except Exception as exc:  # pragma: no cover
        print("Feedback log failed:", exc)


def load_feedback() -> list:
    return _load("feedback", FEEDBACK_PATH)


def log_visit(visitor: str, source: str = "") -> None:
    """Record one web demo page view by an anonymous browser id. Never raises."""
    try:
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "visitor": visitor[:40],
               "source": (source or "direct")[:40]}
        _save("visit", VISITS_PATH, rec)
    except Exception as exc:  # pragma: no cover
        print("Visit log failed:", exc)


def load_visits() -> list:
    return _load("visit", VISITS_PATH)


# ---------------------------------------------------------------- one-time import
if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "import":
        if not DATABASE_URL:
            sys.exit("Add DATABASE_URL to your .env first.")
        for kind, path in (("check", EVENTS_PATH), ("feedback", FEEDBACK_PATH), ("visit", VISITS_PATH)):
            records = _file_load(path)
            added = _db_insert(kind, records) if records else 0
            print(f"{kind}: {len(records)} found in {path}, {added} new rows added to the database.")
    else:
        print("Usage: python events.py import")
