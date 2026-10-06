"""Xvimo end-to-end pipeline: Nano triage, then the Ultra investigator agent when needed.

Usage:
    python xvimo.py "paste a suspicious message here"
    python xvimo.py            (runs four built-in demo messages)
"""
import sys
import time

from investigator import investigate
from triage import triage

VERDICT_LABEL = {"likely_scam": "LIKELY SCAM", "suspicious": "SUSPICIOUS", "no_red_flags": "NO RED FLAGS FOUND"}


def check(message: str) -> dict:
    """Run the full Xvimo pipeline on one message and return the final result."""
    started = time.time()
    first = triage(message)
    if first.get("route") == "final":
        final = {**first, "decided_by": "nano"}
    else:
        deep = investigate(message, first)
        final = {**deep, "decided_by": "ultra_agent", "triage": first}
        final.setdefault("harmful", first.get("harmful", "none"))
    final["total_latency_s"] = round(time.time() - started, 2)
    return final


def show(message: str, result: dict) -> None:
    print("\n" + "=" * 72)
    print("MESSAGE :", message[:200] + ("..." if len(message) > 200 else ""))
    print("VERDICT :", VERDICT_LABEL.get(result.get("verdict"), result.get("verdict")),
          f"(confidence {result.get('confidence')})")
    print("TYPE    :", result.get("scam_type"))
    print("DECIDED :", "Nemotron Nano (triage)" if result["decided_by"] == "nano"
          else f"Nemotron Ultra agent ({result.get('agent_mode')}, {result.get('search_count', 0)} searches)")
    for s in result.get("searches", []):
        print(f"   - {s['tool']}: \"{s['query']}\" -> {s['results']} results")
    print("REASON  :", result.get("reason"))
    for e in result.get("evidence", []):
        print(f"   * {e.get('finding')}\n     {e.get('url')}")
    if result.get("reply"):
        print("REPLY   :", result["reply"])
    print(f"TIME    : {result['total_latency_s']} s")


DEMO_MESSAGES = [
    "Good morning sir, the generator repair is done. Total cost na N15,000. Make I send my account?",
    "Dear customer your BVN has been deactivated due to CBN new policy. Call [PHONE] immediately to reactivate "
    "or your account will be blocked within 24 hours.",
    "CRYPTO SIGNAL GROUP: Our members earned 35% daily profit this week. Minimum deposit $100. Withdraw anytime. "
    "Join now before registration closes: https://profitvault-ng.site/join",
    "Mummy I don reach school safe. I go call you for night.",
]

if __name__ == "__main__":
    messages = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else DEMO_MESSAGES
    for m in messages:
        show(m, check(m))