"""Run Nano triage on a balanced sample of the Xvimo dataset and report accuracy."""
import os
import sys
import time

import pandas as pd

from triage import triage

DATA_PATH = os.path.join("data", "xvimo_dataset.xlsx")
RESULTS_DIR = "results"
SAMPLE_PER_LABEL = int(sys.argv[1]) if len(sys.argv) > 1 else 10  # e.g. python run_triage_sample.py 25
PAUSE_SECONDS = 2  # stay under free-tier rate limits

df = pd.read_excel(DATA_PATH, sheet_name="Examples", header=1)
df = df[df["message_text"].notna() & df["label"].isin(["scam", "legit"])]
df = df[~df["id"].astype(str).str.startswith("EX")]

sample = pd.concat(
    [df[df["label"] == lab].sample(min(SAMPLE_PER_LABEL, (df["label"] == lab).sum()), random_state=42)
     for lab in ["scam", "legit"]]
).sample(frac=1, random_state=1)

records = []
for n, (_, row) in enumerate(sample.iterrows(), 1):
    try:
        out = triage(str(row["message_text"]))
    except Exception as exc:  # network or API error: record it and keep going
        out = {"verdict": "error", "reason": str(exc)[:200], "parse_ok": False}
    flagged = out["verdict"] in ("likely_scam", "suspicious")
    correct = (row["label"] == "scam") == flagged
    records.append({
        "id": row["id"], "label": row["label"], "true_type": row["scam_type"],
        "source_type": row["source_type"], "verdict": out.get("verdict"),
        "pred_type": out.get("scam_type"), "confidence": out.get("confidence"),
        "needs_web_check": out.get("needs_web_check"), "needs_escalation": out.get("needs_escalation"),
        "correct": correct, "latency_s": out.get("latency_s"),
        "input_tokens": out.get("input_tokens"), "output_tokens": out.get("output_tokens"),
        "reason": out.get("reason"), "parse_ok": out.get("parse_ok"),
    })
    mark = "OK " if correct else "MISS"
    print(f"[{n:>3}/{len(sample)}] {mark} {row['id']} true={row['label']:<5} -> {out.get('verdict')}: {out.get('reason', '')[:90]}")
    time.sleep(PAUSE_SECONDS)

res = pd.DataFrame(records)
os.makedirs(RESULTS_DIR, exist_ok=True)
out_path = os.path.join(RESULTS_DIR, "triage_sample.csv")
res.to_csv(out_path, index=False)

valid = res[res["verdict"] != "error"]
scam, legit = valid[valid["label"] == "scam"], valid[valid["label"] == "legit"]
print("\n========== NANO TRIAGE SUMMARY ==========")
print(f"Messages checked        : {len(valid)} (errors: {len(res) - len(valid)})")
print(f"Overall accuracy        : {valid['correct'].mean():.0%}")
print(f"Scams caught (recall)   : {scam['correct'].mean():.0%}  ({scam['correct'].sum()}/{len(scam)})")
print(f"False alarms on legit   : {1 - legit['correct'].mean():.0%}  ({(~legit['correct']).sum()}/{len(legit)})")
print(f"JSON parsed correctly   : {valid['parse_ok'].mean():.0%}")
print(f"Average latency         : {valid['latency_s'].mean():.1f} s")
print(f"Would escalate to Ultra : {valid['needs_escalation'].fillna(False).astype(bool).mean():.0%}")
for src, grp in valid.groupby("source_type"):
    print(f"Accuracy on {src:<20}: {grp['correct'].mean():.0%} ({len(grp)} rows)")
print(f"\nFull results saved to {out_path}")
