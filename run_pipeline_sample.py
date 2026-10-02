"""Evaluate the full Xvimo pipeline (Nano triage + Ultra agent) on a balanced sample.

Usage: python run_pipeline_sample.py 5     (5 scam + 5 legit; keep small to save credits)
"""
import os
import sys
import time

import pandas as pd

from xvimo import check

PER_LABEL = int(sys.argv[1]) if len(sys.argv) > 1 else 5
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 7  # change the seed to test on different, unseen rows

df = pd.read_excel(os.path.join("data", "xvimo_dataset.xlsx"), sheet_name="Examples", header=1)
df = df[df["message_text"].notna() & df["label"].isin(["scam", "legit"])]
df = df[~df["id"].astype(str).str.startswith("EX")]
sample = pd.concat([df[df["label"] == lab].sample(PER_LABEL, random_state=SEED) for lab in ("scam", "legit")])
sample = sample.sample(frac=1, random_state=1)

rows = []
for n, (_, r) in enumerate(sample.iterrows(), 1):
    try:
        out = check(str(r["message_text"]))
    except Exception as exc:
        out = {"verdict": "error", "reason": str(exc)[:150], "decided_by": "error"}
    ok = None if out["verdict"] == "error" else (r["label"] == "scam") == (out["verdict"] != "no_red_flags")
    rows.append({"id": r["id"], "label": r["label"], "source_type": r["source_type"], "verdict": out["verdict"],
                 "decided_by": out.get("decided_by"), "correct": ok, "searches": out.get("search_count", 0),
                 "citations": len(out.get("evidence", []) or []), "latency_s": out.get("total_latency_s"),
                 "reason": out.get("reason"), "reply": out.get("reply")})
    mark = "ERR " if ok is None else ("OK  " if ok else "MISS")
    who = "ULTRA" if out.get("decided_by") == "ultra_agent" else "nano "
    print(f"[{n:>2}/{len(sample)}] {mark} {who} {r['id']} true={r['label']:<5} -> {out['verdict']} "
          f"(searches {out.get('search_count', 0)}): {str(out.get('reason', ''))[:70]}")
    time.sleep(2)

res = pd.DataFrame(rows)
os.makedirs("results", exist_ok=True)
res.to_csv(os.path.join("results", "pipeline_sample.csv"), index=False)
v = res[res["verdict"] != "error"].copy()
v["correct"] = v["correct"].astype(bool)
s, l, u = v[v.label == "scam"], v[v.label == "legit"], v[v.decided_by == "ultra_agent"]
print("\n========== XVIMO PIPELINE SUMMARY ==========")
print(f"Messages checked       : {len(v)} (errors: {len(res) - len(v)})")
print(f"Final accuracy         : {v['correct'].mean():.0%}")
print(f"Scams caught           : {s['correct'].mean():.0%} ({s['correct'].sum()}/{len(s)})")
print(f"False alarms on legit  : {1 - l['correct'].mean():.0%} ({(~l['correct']).sum()}/{len(l)})")
print(f"Decided by Nano        : {(v.decided_by == 'nano').mean():.0%}")
print(f"Decided by Ultra agent : {len(u) / max(len(v), 1):.0%}  -> accuracy {u['correct'].mean() if len(u) else 0:.0%}")
print(f"Avg searches per agent case : {u['searches'].mean() if len(u) else 0:.1f}")
print(f"Avg verified citations      : {u['citations'].mean() if len(u) else 0:.1f}")
print(f"Avg time: Nano {v[v.decided_by == 'nano']['latency_s'].mean():.1f}s | Agent {u['latency_s'].mean() if len(u) else 0:.1f}s")
print("Full results: results/pipeline_sample.csv")
