"""Xvimo admin dashboard: live usage analytics, benchmark results and build status.

Mounted by the WhatsApp bot at /admin. Protected by ADMIN_TOKEN from .env:
    https://<tunnel>/admin?token=<ADMIN_TOKEN>
"""
import csv
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from events import load_events, load_feedback, load_visits
from limits import usage
from paths import RESULTS_DIR

load_dotenv(override=True)
router = APIRouter()
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
BRAND_DIR = os.path.join("assets", "brand")
BRAND_FILES = {  # official logo files you save yourself; missing files fall back to plain text
    "nebius": ["nebius.svg", "nebius.png"],
    "nvidia": ["nvidia.svg", "nvidia.png"],
    "tavily": ["tavily.svg", "tavily.png"],
    "xvimo": ["xvimo.png", "xvimo.svg"],
}

# USD per 1M tokens. Defaults are estimates; set the real Nebius Token Factory prices in .env.
PRICES = {
    "nano_in": float(os.getenv("PRICE_NANO_IN", "0.06")),
    "nano_out": float(os.getenv("PRICE_NANO_OUT", "0.06")),
    "ultra_in": float(os.getenv("PRICE_ULTRA_IN", "1.00")),
    "ultra_out": float(os.getenv("PRICE_ULTRA_OUT", "1.00")),
}
PRICES_ARE_DEFAULTS = not any(os.getenv(k) for k in ("PRICE_NANO_IN", "PRICE_ULTRA_IN"))

PLATFORM = {
    "inference_now": "Nebius Token Factory",
    "inference_target": "Nebius Token Factory",
    "nebius_status": os.getenv("NEBIUS_STATUS", "Live on Nebius Token Factory"),
    "on_nebius": "tokenfactory.nebius" in (os.getenv("LLM_BASE_URL") or ""),
    "nano_model": os.getenv("NANO_MODEL"),
    "ultra_model": os.getenv("ULTRA_MODEL"),
}

TECH_STACK = [
    ("Triage model", "NVIDIA Nemotron 3 Nano (30B, A3B)", "Reads every message, returns structured JSON"),
    ("Investigator model", "NVIDIA Nemotron 3 Ultra (550B, A55B)", "Agent with tool calling for hard cases"),
    ("Screenshot reader", "MiniCPM-V 4.5 (Gemma 3 27B fallback)", "Reads forwarded screenshots"),
    ("Web evidence", "Tavily Search", "Open web and Nigerian regulator sites"),
    ("Inference", "Nebius Token Factory", "Every model call, OpenAI-compatible"),
    ("Messaging", "WhatsApp via 360dialog sandbox", "Meta Cloud API number after business verification"),
    ("Backend", "Python, FastAPI, Uvicorn", "Webhook server, web demo and this dashboard"),
    ("Hosting", "Render (Docker image from GitHub Actions)", "Permanent public HTTPS link"),
    ("Storage", "Neon Postgres", "Check history and feedback survive restarts"),
    ("Data", "300-message labelled benchmark", "Real + constructed, redacted"),
]

MILESTONES = [
    ("done", "Model and search connectivity", "Nano, Ultra and Tavily reachable from code"),
    ("done", "Labelled benchmark dataset", "300 messages, 8 scam types, 5 languages"),
    ("done", "Nano triage + safety-net routing", "100% recall on tuning sample"),
    ("done", "Ultra investigator agent", "Chooses its own searches, verified citations"),
    ("done", "Live on WhatsApp", "End-to-end checks on a real phone"),
    ("done", "Admin dashboard", "Usage, routing, cost and benchmark analytics"),
    ("done", "Web demo page", "Live agent trace, one-click for judges"),
    ("done", "Screenshot checks", "Vision model reads forwarded images"),
    ("done", "Running on Nebius Token Factory", "All Nemotron calls via Nebius"),
    ("done", "Public deployment", "Always-on link with lasting history"),
    ("next", "Public beta launch", "Web first, 11 Oct 2026"),
    ("blocked", "Own WhatsApp number", "Waiting for Meta business verification"),
    ("next", "Full 300-message benchmark", "Run once on the final pipeline"),
    ("next", "Demo video and submission", "Due Oct 30, 2026"),
]


def _cost(nano_in, nano_out, ultra_in, ultra_out) -> float:
    return (nano_in * PRICES["nano_in"] + nano_out * PRICES["nano_out"]
            + ultra_in * PRICES["ultra_in"] + ultra_out * PRICES["ultra_out"]) / 1_000_000


def _avg(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(sum(values) / len(values), 2) if values else None


def _percentile(values, q):
    values = sorted(v for v in values if isinstance(v, (int, float)))
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


def deep_stats(events: list) -> dict:
    """Second-level analysis: how triage and the agent interact, confidence, latency spread, tokens."""
    agent = [e for e in events if e.get("decided_by") == "ultra_agent"]
    nano = [e for e in events if e.get("decided_by") == "nano"]

    # Why cases were escalated, judged from what triage said.
    reasons = Counter()
    for e in agent:
        tv, conf = e.get("triage_verdict"), e.get("triage_confidence") or 0
        if tv == "suspicious":
            reasons["Triage unsure (suspicious)"] += 1
        elif tv == "no_red_flags":
            reasons["Looked safe but touches a risky topic"] += 1
        elif conf < 0.75:
            reasons["Low triage confidence"] += 1
        else:
            reasons["Other"] += 1

    # What the agent concluded compared with triage's first view.
    outcome = Counter()
    for e in agent:
        tv, fv = e.get("triage_verdict"), e.get("verdict")
        if tv == "suspicious" and fv == "likely_scam":
            outcome["Confirmed as scam"] += 1
        elif tv in ("suspicious", "likely_scam") and fv == "no_red_flags":
            outcome["Cleared as safe"] += 1
        elif tv == "no_red_flags" and fv != "no_red_flags":
            outcome["Caught a scam triage missed"] += 1
        elif tv == fv:
            outcome["Agreed with triage"] += 1
        else:
            outcome["Changed verdict"] += 1

    buckets = Counter()
    for e in events:
        c = e.get("triage_confidence")
        if isinstance(c, (int, float)):
            buckets[min(int(c * 10), 9)] += 1
    confidence = [{"band": f"{b / 10:.1f}–{(b + 1) / 10:.1f}", "count": buckets.get(b, 0)} for b in range(5, 10)]

    hours = Counter(datetime.fromisoformat(e["ts"]).hour for e in events)
    return {
        "escalation_reasons": dict(reasons),
        "agent_outcomes": dict(outcome),
        "confidence": confidence,
        "latency": {
            "nano_p50": _percentile([e["total_latency_s"] for e in nano], .5),
            "nano_p90": _percentile([e["total_latency_s"] for e in nano], .9),
            "agent_p50": _percentile([e["total_latency_s"] for e in agent], .5),
            "agent_p90": _percentile([e["total_latency_s"] for e in agent], .9),
        },
        "tokens": {
            "nano_per_check": _avg([e["nano_in"] + e["nano_out"] for e in events]),
            "ultra_per_case": _avg([e["ultra_in"] + e["ultra_out"] for e in agent]),
        },
        "cost_per_check": {
            "nano_path": _avg([_cost(e["nano_in"], e["nano_out"], 0, 0) for e in nano]),
            "agent_path": _avg([_cost(e["nano_in"], e["nano_out"], e["ultra_in"], e["ultra_out"]) for e in agent]),
        },
        "hours": [{"hour": h, "checks": hours.get(h, 0)} for h in range(24)],
    }


def live_stats(events: list) -> dict:
    n = len(events)
    nano = [e for e in events if e.get("decided_by") == "nano"]
    agent = [e for e in events if e.get("decided_by") == "ultra_agent"]
    actual = sum(_cost(e["nano_in"], e["nano_out"], e["ultra_in"], e["ultra_out"]) for e in events)
    per_agent_case = _avg([_cost(e["nano_in"], e["nano_out"], e["ultra_in"], e["ultra_out"]) for e in agent])
    all_ultra = (per_agent_case or 0) * n
    tool_counts = Counter(s.get("tool") for e in agent for s in e.get("searches", []))
    days = Counter(e["ts"][:10] for e in events)
    flagged_days = Counter(e["ts"][:10] for e in events if e.get("verdict") != "no_red_flags")
    # Continuous run of days (zeros included) so the trend is a real line even with one active day:
    # at least the last 7 days, at most the last 30.
    end = datetime.now(timezone.utc).date()
    first = min((datetime.fromisoformat(d).date() for d in days), default=end)
    start = max(min(first, end - timedelta(days=6)), end - timedelta(days=29))
    day_range = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekdays = Counter(datetime.fromisoformat(e["ts"]).weekday() for e in events)
    today = datetime.now().astimezone().date().isoformat()
    flagged_total = sum(1 for e in events if e.get("verdict") != "no_red_flags")
    return {
        "total": n,
        "verdicts": dict(Counter(e.get("verdict") for e in events)),
        "routing": {"nano": len(nano), "agent": len(agent)},
        "agent_verdicts": dict(Counter(e.get("verdict") for e in agent)),
        "nano_verdicts": dict(Counter(e.get("verdict") for e in nano)),
        "scam_types": dict(Counter(e.get("scam_type") for e in events
                                   if e.get("verdict") != "no_red_flags" and e.get("scam_type"))),
        "languages": dict(Counter(e.get("language") or "unknown" for e in events)),
        "latency": {"nano": _avg([e["total_latency_s"] for e in nano]),
                    "agent": _avg([e["total_latency_s"] for e in agent])},
        "agent": {"avg_searches": _avg([len(e.get("searches", [])) for e in agent]),
                  "avg_citations": _avg([e.get("citations") for e in agent]),
                  "dropped_citations": sum(e.get("dropped_citations", 0) for e in agent),
                  "tools": dict(tool_counts)},
        "cost": {"actual": round(actual, 5), "all_ultra": round(all_ultra, 5),
                 "saving_pct": round(100 * (1 - actual / all_ultra), 1) if all_ultra else None,
                 "prices": PRICES, "estimated": PRICES_ARE_DEFAULTS},
        "daily": [{"day": d, "checks": days.get(d, 0), "flagged": flagged_days.get(d, 0)} for d in day_range],
        "weekdays": [{"day": weekday_names[i], "checks": weekdays.get(i, 0)} for i in range(7)],
        "latency_series": [{"t": e["ts"][5:16].replace("T", " "), "s": e.get("total_latency_s"),
                            "by": e.get("decided_by")} for e in events[-30:]],
        "today": days.get(today, 0),
        "flagged_total": flagged_total,
        "flagged_pct": round(100 * flagged_total / n, 1) if n else None,
        "recent": [{k: e.get(k) for k in ("ts", "sender", "preview", "language", "verdict", "scam_type",
                                          "decided_by", "total_latency_s", "citations")}
                   for e in events[-25:][::-1]],
    }


def _read_csv(path):
    if not os.path.exists(path):
        return None, None
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    when = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%d %b %Y, %H:%M")
    return rows, when


def _is_true(value) -> bool:
    return str(value).strip().lower() in ("true", "1")


def benchmark_stats() -> dict:
    out = {}
    rows, when = _read_csv(os.path.join(RESULTS_DIR, "triage_sample.csv"))
    if rows:
        ok = [r for r in rows if r.get("verdict") != "error"]
        scam = [r for r in ok if r["label"] == "scam"]
        legit = [r for r in ok if r["label"] == "legit"]
        final = [r for r in ok if r.get("route") == "final"]
        out["triage"] = {
            "when": when, "n": len(ok),
            "accuracy": _pct(ok), "recall": _pct(scam), "false_alarm": 100 - _pct(legit) if legit else None,
            "nano_final_share": round(100 * len(final) / len(ok), 1) if ok else None,
            "nano_final_accuracy": _pct(final),
        }
    rows, when = _read_csv(os.path.join(RESULTS_DIR, "pipeline_sample.csv"))
    if rows:
        ok = [r for r in rows if r.get("verdict") != "error"]
        scam = [r for r in ok if r["label"] == "scam"]
        legit = [r for r in ok if r["label"] == "legit"]
        out["pipeline"] = {
            "when": when, "n": len(ok), "accuracy": _pct(ok), "recall": _pct(scam),
            "false_alarm": 100 - _pct(legit) if legit else None,
        }
    return out


def _pct(rows):
    return round(100 * sum(_is_true(r.get("correct")) for r in rows) / len(rows), 1) if rows else None


def _feedback_stats() -> dict:
    fb = load_feedback()
    latest = {}
    for f in fb:  # one vote per check: keep the latest
        latest[f.get("id")] = f.get("helpful")
    votes = list(latest.values())
    up = sum(1 for v in votes if v)
    return {"votes": len(votes), "helpful": up, "not_helpful": len(votes) - up,
            "helpful_pct": round(100 * up / len(votes), 1) if votes else None}


def audience_stats(events: list, visits: list) -> dict:
    """People, not messages: anonymous browser ids (web) and hashed numbers (WhatsApp). No sign-in needed."""
    today = datetime.now(timezone.utc).date()
    week_ago = (today - timedelta(days=6)).isoformat()
    day = lambda r: (r.get("ts") or "")[:10]
    web_visitors = {v["visitor"] for v in visits if v.get("visitor")}
    checkers = {e["user"] for e in events if e.get("user")}
    web_checkers = {e["user"] for e in events if e.get("user") and e.get("channel") == "web"}
    wa_users = {e["user"] for e in events if e.get("user") and e.get("channel") == "whatsapp"}
    people = web_visitors | checkers
    active_days = {}
    for r in [*visits, *events]:
        uid = r.get("visitor") or r.get("user")
        if uid:
            active_days.setdefault(uid, set()).add(day(r))
    first_source = {}
    for v in sorted(visits, key=day):
        first_source.setdefault(v.get("visitor"), v.get("source") or "direct")
    in_range = lambda d0: {r.get("visitor") or r.get("user") for r in [*visits, *events]
                           if (r.get("visitor") or r.get("user")) and day(r) >= d0}
    per_day = {}
    for r in [*visits, *events]:
        uid = r.get("visitor") or r.get("user")
        if uid:
            per_day.setdefault(day(r), set()).add(uid)
    return {
        "people": len(people),
        "today": len(in_range(today.isoformat())),
        "last_7_days": len(in_range(week_ago)),
        "web_visitors": len(web_visitors),
        "ran_a_check": len(checkers),
        "whatsapp_users": len(wa_users),
        "returning": sum(1 for d in active_days.values() if len(d) >= 2),
        "conversion_pct": round(100 * len(web_checkers & web_visitors) / len(web_visitors), 1) if web_visitors else None,
        "checks_per_person": round(len(events) / len(checkers), 1) if checkers else None,
        "sources": dict(Counter(first_source.values())),
        "per_day": {d: len(u) for d, u in per_day.items()},
    }


def _check_token(request: Request):
    expected = os.getenv("ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=403, detail="Set ADMIN_TOKEN in .env to enable the dashboard.")
    if request.query_params.get("token") != expected:
        raise HTTPException(status_code=403, detail="Add ?token=<ADMIN_TOKEN> to the address.")


def _brand_path(name: str):
    for filename in BRAND_FILES.get(name, []):
        path = os.path.join(BRAND_DIR, filename)
        if os.path.exists(path):
            return path
    return None


@router.get("/admin/brand/{name}")
def brand_logo(name: str):
    path = _brand_path(name)
    if not path:
        raise HTTPException(status_code=404, detail="Logo not found")
    return FileResponse(path)


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    _check_token(request)
    with open(HTML_PATH, encoding="utf-8") as f:
        return f.read()


@router.get("/admin/data")
def admin_data(request: Request):
    _check_token(request)
    events = load_events()
    audience = audience_stats(events, load_visits())
    live = live_stats(events)
    for p in live["daily"]:
        p["people"] = audience["per_day"].get(p["day"], 0)
    return {
        "generated": datetime.now().strftime("%d %b %Y, %H:%M"),
        "platform": PLATFORM,
        "live": live,
        "audience": audience,
        "deep": deep_stats(events),
        "benchmarks": benchmark_stats(),
        "tech_stack": [{"layer": a, "tool": b, "role": c} for a, b, c in TECH_STACK],
        "milestones": [{"status": a, "title": b, "detail": c} for a, b, c in MILESTONES],
        "brands": {name: bool(_brand_path(name)) for name in BRAND_FILES},
        "feedback": _feedback_stats(),
        "usage_today": usage(),
        "links": {"demo": "/demo", "whatsapp": os.getenv("WHATSAPP_CHAT_URL", "").strip()},
    }
