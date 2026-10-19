"""Materialize a condition: rewrite `current_action` (and, for wrapper conditions, append one
fixed schema to `env_info`) for every step and write the result in the exact TS-Bench schema
under data/conditions/<condition>/<source>/<file>.json.

Each row carries `_original_current_action`, `_original_env_info`, `_condition` and
`_render_status` so downstream code can recover the pairing. Rows that cannot be rendered in
a condition are dropped from that condition (paired analysis then excludes them for every
guard) and counted in MATERIALIZE_REPORT.json.
"""
from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from pathlib import Path

from action_invariance.data import Step, dump_domain, load_all, read_jsonl
from action_invariance.render import ENV_SEPARATOR, LLM_CONDITIONS, WRAPPERS, render_condition


def load_renders(path: Path) -> dict[str, str]:
    """data/renders/<condition>.jsonl rows: {sample_id, text, accepted: bool, ...}"""
    return {r["sample_id"]: r["text"] for r in read_jsonl(path) if r.get("accepted", True)}


def materialize(condition: str, raw_root: Path, out_root: Path, render_file: Path | None = None) -> dict:
    steps = load_all(Path(raw_root))
    llm_text = load_renders(render_file) if condition in LLM_CONDITIONS and render_file else {}
    out_dir = Path(out_root) / condition
    per_domain: dict[str, list[Step]] = defaultdict(list)
    statuses: Counter = Counter()
    for s in steps:
        if condition in LLM_CONDITIONS:
            text = llm_text.get(s.sample_id)
            status = "ok" if text is not None else "missing_render"
            env = s.raw_env_info
        else:
            r = render_condition(s, condition)
            text, status, env = r.current_action, r.status, r.env_info
        statuses[status] += 1
        if status != "ok":
            continue
        raw = copy.deepcopy(s.raw)
        raw["_sample_id"] = s.sample_id
        raw["_original_current_action"] = s.raw_current_action
        raw["_original_env_info"] = s.raw_env_info
        raw["_condition"] = condition
        raw["_render_status"] = status
        raw["current_action"] = text
        raw["env_info"] = env
        per_domain[s.domain].append(Step(s.domain, s.interaction_id, s.segment_id, s.instruction, s.history, text, env, s.label, raw, s.row_index, s.dup_suffix))
    for domain, rows in per_domain.items():
        dump_domain(rows, out_dir / f"{domain}.json")
    report = {"condition": condition, "n_rows": sum(len(v) for v in per_domain.values()), "n_input": len(steps), "status_counts": dict(sorted(statuses.items()))}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "MATERIALIZE_REPORT.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def verify_invariance(raw_root: Path, cond_root: Path, condition: str) -> dict:
    """Assert that instruction, history and label are unchanged, and that env_info is either
    unchanged or the original plus exactly the condition's fixed wrapper schema."""
    original = {s.sample_id: s for s in load_all(Path(raw_root))}
    cond = load_all(Path(cond_root) / condition)
    wrapper = WRAPPERS.get("code_python" if condition == "placebo_env_wrapper" else condition)
    problems = []
    changed_action = changed_env = 0
    for s in cond:
        o = original[s.sample_id]
        for key in ("instruction", "history"):
            if o.raw[key] != s.raw[key]:
                problems.append((s.sample_id, key))
        if o.label != s.label:
            problems.append((s.sample_id, "score"))
        if s.raw["_original_current_action"] != o.raw_current_action:
            problems.append((s.sample_id, "_original_current_action"))
        if s.raw["env_info"] != o.raw_env_info:
            changed_env += 1
            expected = o.raw_env_info.rstrip() + ENV_SEPARATOR + wrapper["schema"] if wrapper else o.raw_env_info
            if s.raw["env_info"] != expected:
                problems.append((s.sample_id, "env_info"))
        elif wrapper is not None:
            problems.append((s.sample_id, "env_info_missing_wrapper"))
        changed_action += s.current_action != o.raw_current_action
    return {"condition": condition, "n_rows": len(cond), "n_action_changed": changed_action, "n_env_changed": changed_env, "problems": problems[:20], "ok": not problems}
