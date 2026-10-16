"""Xvimo benchmark + open-model leaderboard on the labelled dataset (data/xvimo_dataset.xlsx).

Two kinds of run:
  1. Single-pass triage, same prompt, different open models on Nebius Token Factory  -> the leaderboard
  2. The full Xvimo pipeline (Nano triage -> Ultra agent with Tavily)                -> Xvimo's real score

Examples:
  python bench.py --limit 20                              # quick smoke test on 20 messages
  python bench.py                                         # all messages, default models + full pipeline
  python bench.py --models nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B google/gemma-3-27b-it --no-pipeline
  python bench.py --pipeline-only

Safe to stop and restart: finished messages are saved to results/bench/*.jsonl and skipped next time.
With LANGSMITH_TRACING=true in .env, every run is also traced in LangSmith (numbers and emails masked).
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from dotenv import load_dotenv

load_dotenv(override=True)

import tracing  # noqa: E402
from triage import triage  # noqa: E402

OUT = os.path.join("results", "bench")
DEFAULT_MODELS = [m for m in [
    os.getenv("NANO_MODEL"),
    os.getenv("SUPER_MODEL", "nvidia/nemotron-3-super-120b-a12b"),
    os.getenv("ULTRA_MODEL"),
    os.getenv("VISION_FALLBACK_MODEL", "google/gemma-3-27b-it"),
] if m]
# USD per 1M tokens (input, output) for the cost column; unknown models show "n/a"
PRICES = {
    os.getenv("NANO_MODEL", ""): (float(os.getenv("PRICE_NANO_IN", "0.06")), float(os.getenv("PRICE_NANO_OUT", "0.24"))),
    os.getenv("ULTRA_MODEL", ""): (float(os.getenv("PRICE_ULTRA_IN", "1.00")), float(os.getenv("PRICE_ULTRA_OUT", "3.00"))),
}
_lock = threading.Lock()


def load_dataset(limit: int = 0, seed: int = 7) -> pd.DataFrame:
    df = pd.read_excel(os.path.join("data", "xvimo_dataset.xlsx"), sheet_name="Examples", header=1)
    df = df[df["message_text"].notna() & df["label"].isin(["scam", "legit"])]
    df = df[~df["id"].astype(str).str.startswith("EX")]
    if limit:  # balanced, reproducible subset
        per = max(1, limit // 2)
        df = pd.concat([g.sample(min(per, len(g)), random_state=seed) for _, g in df.groupby("label")])
    return df.reset_index(drop=True)


def slug(model: str) -> str:
    return model.replace("/", "__")


def done_ids(path: str) -> set:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {json.loads(line)["id"] for line in f if line.strip()}


def save(path: str, rec: dict) -> None:
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def correct(label: str, verdict: str):
    if verdict not in ("likely_scam", "suspicious", "no_red_flags"):
        return None
    return (label == "scam") == (verdict != "no_red_flags")


def run_one(kind: str, model: str, row) -> dict:
    msg = str(row["message_text"])
    started = time.time()
    try:
        if kind == "pipeline":
            from xvimo import check
            out = check(msg)
            extra = {"decided_by": out.get("decided_by"), "searches": out.get("search_count", 0),
                     "citations": len(out.get("evidence") or []), "dropped_citations": out.get("dropped_citations", 0)}
            tri = out.get("triage") or out
            tin = (tri.get("input_tokens") or 0)
            tout = (tri.get("output_tokens") or 0)
            if out.get("decided_by") == "ultra_agent":
                extra.update(ultra_in=out.get("input_tokens") or 0, ultra_out=out.get("output_tokens") or 0)
        else:
            out = triage(msg, model=model)
            extra, tin, tout = {"route": out.get("route")}, out.get("input_tokens") or 0, out.get("output_tokens") or 0
        verdict, err = out.get("verdict"), ""
    except Exception as exc:
        out, verdict, err, extra, tin, tout = {}, "error", str(exc)[:200], {}, 0, 0
    return {"id": str(row["id"]), "label": row["label"], "language": row.get("language"),
            "scam_type": row.get("scam_type"), "source_type": row.get("source_type"),
            "model": model, "kind": kind, "verdict": verdict, "correct": correct(row["label"], verdict),
            "pred_language": out.get("language"), "latency_s": round(time.time() - started, 2),
            "input_tokens": tin, "output_tokens": tout, "error": err, **extra}


def run(kind: str, model: str, df: pd.DataFrame, workers: int) -> None:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{'pipeline' if kind == 'pipeline' else slug(model)}.jsonl")
    todo = df[~df["id"].astype(str).isin(done_ids(path))]
    label = "Xvimo full pipeline" if kind == "pipeline" else model
    print(f"\n=== {label}: {len(todo)} to run ({len(df) - len(todo)} already done) ===")
    n = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_one, kind, model, r) for _, r in todo.iterrows()]
        for fut in as_completed(futures):
            rec = fut.result()
            save(path, rec)
            n += 1
            mark = "ERR " if rec["correct"] is None else ("ok  " if rec["correct"] else "MISS")
            print(f"  [{n}/{len(todo)}] {mark} {rec['id']:<8} {rec['label']:<5} -> {rec['verdict']:<13} {rec['latency_s']}s")


def summarize(df_ids: set) -> pd.DataFrame:
    rows = []
    for fn in sorted(os.listdir(OUT)):
        if not fn.endswith(".jsonl"):
            continue
        recs = [json.loads(l) for l in open(os.path.join(OUT, fn), encoding="utf-8") if l.strip()]
        recs = [r for r in recs if r["id"] in df_ids]
        if not recs:
            continue
        d = pd.DataFrame(recs)
        ok = d[d["correct"].notna()].copy()
        ok["correct"] = ok["correct"].astype(bool)
        scam, legit = ok[ok.label == "scam"], ok[ok.label == "legit"]
        model = "Xvimo pipeline (Nano → Ultra agent + Tavily)" if fn == "pipeline.jsonl" else d["model"].iloc[0]
        row = {
            "system": model,
            "messages": len(ok),
            "errors": int((d["verdict"] == "error").sum()),
            "accuracy_%": round(100 * ok["correct"].mean(), 1) if len(ok) else None,
            "scams_caught_%": round(100 * scam["correct"].mean(), 1) if len(scam) else None,
            "false_alarms_%": round(100 * (1 - legit["correct"].mean()), 1) if len(legit) else None,
            "avg_latency_s": round(ok["latency_s"].mean(), 2) if len(ok) else None,
        }
        for lang in ("english", "pidgin", "yoruba", "hausa", "igbo"):
            sub = ok[ok.language == lang]
            row[f"acc_{lang}_%"] = f"{round(100 * sub['correct'].mean())} (n={len(sub)})" if len(sub) else "-"
        if fn == "pipeline.jsonl" and "decided_by" in d:
            row["answered_by_nano_%"] = round(100 * (ok["decided_by"] == "nano").mean(), 1)
        price = PRICES.get(model)
        if fn == "pipeline.jsonl" and len(ok):
            nano_p = PRICES.get(os.getenv("NANO_MODEL", ""), (0, 0))
            ultra_p = PRICES.get(os.getenv("ULTRA_MODEL", ""), (0, 0))
            usd = (ok["input_tokens"].sum() * nano_p[0] + ok["output_tokens"].sum() * nano_p[1]
                   + ok.get("ultra_in", pd.Series(dtype=float)).fillna(0).sum() * ultra_p[0]
                   + ok.get("ultra_out", pd.Series(dtype=float)).fillna(0).sum() * ultra_p[1]) / 1e6
            row["usd_per_1k_messages"] = round(1000 * usd / len(ok), 3)
        elif price and len(ok):
            usd = (ok["input_tokens"].sum() * price[0] + ok["output_tokens"].sum() * price[1]) / 1e6
            row["usd_per_1k_messages"] = round(1000 * usd / len(ok), 3)
        rows.append(row)
    return pd.DataFrame(rows).sort_values("accuracy_%", ascending=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="*", default=DEFAULT_MODELS, help="models for the single-pass leaderboard")
    ap.add_argument("--limit", type=int, default=0, help="balanced subset size (0 = all)")
    ap.add_argument("--workers", type=int, default=3, help="parallel requests per model")
    ap.add_argument("--no-pipeline", action="store_true", help="skip the full Xvimo pipeline run")
    ap.add_argument("--pipeline-only", action="store_true", help="only run the full Xvimo pipeline")
    args = ap.parse_args()

    df = load_dataset(args.limit)
    print(f"Dataset: {len(df)} messages ({(df.label == 'scam').sum()} scam / {(df.label == 'legit').sum()} legit) · "
          f"tracing {'ON' if tracing.ENABLED else 'off'}")
    try:
        if not args.pipeline_only:
            for m in args.models:
                run("triage", m, df, args.workers)
        if not args.no_pipeline:
            run("pipeline", os.getenv("ULTRA_MODEL", ""), df, max(1, min(args.workers, 2)))
    except KeyboardInterrupt:
        print("\nStopped. Progress is saved; run the same command again to continue.")
    finally:
        tracing.flush()

    board = summarize(set(df["id"].astype(str)))
    board.to_csv(os.path.join("results", "leaderboard.csv"), index=False)
    try:
        with open(os.path.join("results", "leaderboard.md"), "w", encoding="utf-8") as f:
            f.write(board.to_markdown(index=False))
    except ImportError:
        print("(pip install tabulate to also get results/leaderboard.md)")
    print("\n========== LEADERBOARD ==========")
    print(board.to_string(index=False))
    print("\nSaved: results/leaderboard.csv and results/leaderboard.md")


if __name__ == "__main__":
    sys.exit(main())
