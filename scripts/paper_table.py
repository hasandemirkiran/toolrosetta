"""Cross-guard summary from results/summary.json: one row per guard × condition × source with the
placebo-adjusted flip rate, ΔTPR and ΔFPR (95 % cluster-bootstrap CIs), plus baseline BACC.
Marks intervals that exclude zero with *. Usage: python scripts/paper_table.py [--source agentdojo|agentharm] [--md]"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORDER_G = ["gemini-3.8-flash", "gemini-3.8-flash-scanner", "claude-sonnet-5", "glm-5.2", "gpt-oss-120b", "gemma-4-26b", "llama-4-maverick", "qwen3.8-27b", "nemotron-3.5-lightning-30b", "ts-guard", "stepguard", "agentdog", "shieldagent", "safiron"]
ORDER_C = ["thought_removed", "code_python", "shell_cli", "natural_language"]


def fmt(x: dict | None, pct: bool = True) -> str:
    if not x:
        return ""
    p, (lo, hi) = x["point"], x["ci95"]
    star = "*" if lo > 0 or hi < 0 else ""
    k = 100 if pct else 1
    return f"{p * k:+.1f} [{lo * k:+.1f}, {hi * k:+.1f}]{star}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["agentdojo", "agentharm"], default=None)
    ap.add_argument("--md", action="store_true")
    a = ap.parse_args()
    rep = json.loads((ROOT / "results" / "summary.json").read_text())
    cells = rep["cells"]
    rows = []
    for g in ORDER_G:
        for c in ORDER_C:
            cell = cells.get(f"{g}||{c}")
            if not cell:
                continue
            for src, e in (cell.get("per_source") or {}).items():
                if a.source and src != a.source:
                    continue
                base = cells.get(f"{g}||original", {}).get("per_source", {}).get(src, {}).get("rates", {})
                fl = e.get("flips", {})
                rows.append((g, c, src, base.get("bacc"), fl.get("flip_rate"), e.get("adjusted_flip"), e.get("delta", {}).get("tpr"), e.get("delta", {}).get("fpr"), fl.get("new_misses"), fl.get("new_false_positives")))
    hdr = ("guard", "condition", "source", "base BACC", "flip %", "flip − placebo, pts", "ΔTPR, pts", "ΔFPR, pts", "new misses", "new FPs")
    if a.md:
        print("| " + " | ".join(hdr) + " |")
        print("|---|---|---|---:|---:|---|---|---|---:|---:|")
        for r in rows:
            print(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]:.3f} | {r[4] * 100:.1f} | {fmt(r[5])} | {fmt(r[6])} | {fmt(r[7])} | {r[8]} | {r[9]} |")
    else:
        print(f"{'guard':26s} {'condition':16s} {'src':9s} {'BACC':>6s} {'flip%':>6s} {'flip−placebo':>26s} {'ΔTPR':>26s} {'ΔFPR':>26s} {'miss':>5s} {'FP':>4s}")
        for r in rows:
            print(f"{r[0]:26s} {r[1]:16s} {r[2]:9s} {r[3]:6.3f} {r[4] * 100:6.1f} {fmt(r[5]):>26s} {fmt(r[6]):>26s} {fmt(r[7]):>26s} {r[8]:5d} {r[9]:4d}")
    print("\n* = 95% interval excludes zero. Wrapper conditions are adjusted against placebo_env_wrapper, thought_removed against placebo_whitespace.")


if __name__ == "__main__":
    main()
