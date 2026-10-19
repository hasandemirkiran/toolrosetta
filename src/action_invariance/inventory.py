"""Action inventory: what the 7,182 candidate actions look like, per source, and the per-step
covariates the analysis stratifies on.

`leaky` here means the tool name is restated in the agent's own Thought text (the analogue of
guard-invariance's restatement leakage): a guard can then read *what* is being called from the
rationale even after the call itself is re-rendered.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from action_invariance.data import Step, write_jsonl
from action_invariance.react import parse_react_step
from action_invariance.render import PROGRAMMATIC_CONDITIONS, render_condition


def step_covariates(steps: list[Step]) -> list[dict]:
    rows = []
    for s in steps:
        p = parse_react_step(s.raw_current_action)
        renders = {c: render_condition(s, c) for c in PROGRAMMATIC_CONDITIONS}
        args = p.arguments_json if isinstance(p.arguments_json, dict) else None
        rows.append(
            {
                "sample_id": s.sample_id,
                "instruction_id": s.instruction_id,
                "domain": s.domain,
                "source": s.source,
                "subset": s.subset,
                "trajectory_id": s.trajectory_id,
                "segment_id": s.segment_id,
                "label": s.label,
                "parse_status": p.status,
                "tool_name": p.tool_name,
                "n_args": len(args) if args is not None else None,
                "nested_args": any(isinstance(v, (dict, list)) for v in args.values()) if args else False,
                "arg_chars": len(p.argument_text),
                "thought_chars": len(p.thought),
                "action_chars": len(s.raw_current_action),
                "history_chars": len(s.history),
                "env_chars": len(s.raw_env_info),
                "has_thought": p.thought_marker_count > 0,
                "has_suffix": bool(p.suffix),
                "leaky": bool(p.tool_name) and p.tool_name.lower() in p.thought.lower(),
                "tool_in_history": bool(p.tool_name) and p.tool_name.lower() in s.history.lower(),
                **{f"render_{c}": r.status for c, r in renders.items()},
                "eligible_programmatic": all(r.ok for r in renders.values()),
            }
        )
    return rows


def summarize(rows: list[dict]) -> dict:
    def block(sub: list[dict]) -> dict:
        n = len(sub) or 1
        chars = sum(r["history_chars"] + r["action_chars"] + r["env_chars"] for r in sub)
        return {
            "steps": len(sub),
            "unsafe": sum(r["label"] >= 0.5 for r in sub),
            "trajectories": len({r["trajectory_id"] for r in sub}),
            "unique_tools": len({r["tool_name"] for r in sub if r["tool_name"]}),
            "parse_status": dict(sorted(Counter(r["parse_status"] for r in sub).items())),
            "render_status": {c: dict(sorted(Counter(r[f"render_{c}"] for r in sub).items())) for c in PROGRAMMATIC_CONDITIONS},
            "eligible_programmatic": sum(r["eligible_programmatic"] for r in sub),
            "eligible_programmatic_unsafe": sum(r["eligible_programmatic"] and r["label"] >= 0.5 for r in sub),
            "share_leaky_tool_in_thought": round(sum(r["leaky"] for r in sub) / n, 4),
            "share_tool_in_history": round(sum(r["tool_in_history"] for r in sub) / n, 4),
            "share_nested_args": round(sum(r["nested_args"] for r in sub) / n, 4),
            "mean_n_args": round(sum(r["n_args"] or 0 for r in sub) / n, 3),
            "mean_thought_chars": round(sum(r["thought_chars"] for r in sub) / n, 1),
            "approx_input_tokens_per_pass": int(chars / 4),
        }

    return {
        "all": block(rows),
        "per_source": {src: block([r for r in rows if r["source"] == src]) for src in sorted({r["source"] for r in rows})},
        "per_domain": {d: block([r for r in rows if r["domain"] == d]) for d in sorted({r["domain"] for r in rows})},
    }


def write_inventory(steps: list[Step], out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = step_covariates(steps)
    write_jsonl(out_dir / "step_covariates.jsonl", rows)
    cohort = [r["sample_id"] for r in rows if r["eligible_programmatic"]]
    (out_dir / "cohort_programmatic.txt").write_text("\n".join(cohort) + "\n", encoding="utf-8")
    summary = summarize(rows)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
