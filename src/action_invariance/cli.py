"""Command-line entry point: action-invariance <subcommand> [...]"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("ACTION_INVARIANCE_ROOT", Path(__file__).resolve().parents[2]))
DATA, CONFIGS, RESULTS = ROOT / "data", ROOT / "configs", ROOT / "results"


def _models_cfg() -> dict:
    return yaml.safe_load((CONFIGS / "models.yaml").read_text(encoding="utf-8"))


def _provider(name: str, cfg: dict, guard: dict | None = None):
    from action_invariance.vertex import make_provider

    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or (None if str(cfg.get("project", "")).startswith("${") else cfg.get("project"))
    if name == "vertex_endpoint":
        g = guard or {}
        return make_provider(name, project, g.get("location") or cfg.get("location"), endpoint_id=str(g.get("endpoint_id") or ""), dedicated_dns=g.get("dedicated_dns") or None, hf_repo=g.get("hf_repo"))
    return make_provider(name, project, (guard or {}).get("location") or cfg.get("location"))


def _print_spend(cfg: dict) -> None:
    from action_invariance.cost import write_ledger

    led = write_ledger(ROOT, cfg.get("pricing", {}), cfg.get("budget", {}), cfg)
    live = led["live_endpoints"]
    extra = f" + ${live['usd']:,.2f} on {len(live['endpoints'])} DEPLOYED endpoint(s) still billing" if live["endpoints"] else ""
    print(f"[budget] recorded spend ${led['total_usd']:,.2f}{extra} / ${cfg.get('budget', {}).get('budget_usd', 0):,.0f} → results/cost_ledger.json")


def cmd_fetch(a):
    from action_invariance.fetch import fetch

    rep = fetch(DATA / "manifest.json", DATA / "raw", from_local=Path(a.from_local) if a.from_local else None, force=a.force)
    print(json.dumps(rep, indent=2))


def cmd_inventory(a):
    from action_invariance.data import load_all
    from action_invariance.inventory import write_inventory

    summary = write_inventory(load_all(DATA / "raw"), DATA / "inventory")
    print(json.dumps({"all": summary["all"], "per_source": summary["per_source"]}, indent=2))


def cmd_materialize(a):
    from action_invariance.materialize import materialize, verify_invariance
    from action_invariance.render import LLM_CONDITIONS

    for cid in a.conditions:
        rf = DATA / "renders" / f"{cid}.jsonl" if cid in LLM_CONDITIONS else None
        rep = materialize(cid, DATA / "raw", DATA / "conditions", rf)
        chk = verify_invariance(DATA / "raw", DATA / "conditions", cid)
        print(json.dumps({**rep, "invariance_ok": chk["ok"], "action_changed": chk["n_action_changed"], "env_changed": chk["n_env_changed"]}))
        if not chk["ok"]:
            sys.exit(f"invariance violated for {cid}: {chk['problems'][:3]}")


def cmd_show(a):
    """Print one step under every requested condition (for eyeballing renderings)."""
    from action_invariance.data import load_all
    from action_invariance.render import render_condition

    steps = {s.sample_id: s for s in load_all(DATA / "raw")}
    s = steps[a.sample_id]
    for cid in a.conditions:
        r = render_condition(s, cid)
        print(f"===== {cid}  [{r.status}]  {json.dumps(r.meta)}\n{r.current_action}\n")
        if r.env_info != s.raw_env_info:
            print("----- env_info tail:\n" + r.env_info[-400:] + "\n")


def cmd_cost(a):
    from action_invariance.cost import add_endpoint_hours, estimate, write_ledger
    from action_invariance.data import load_all

    cfg = _models_cfg()
    if a.add_hours:
        gid, hours, rate = a.add_hours
        print(json.dumps(add_endpoint_hours(ROOT, guard=gid, hours=float(hours), usd_per_hour=float(rate), note=a.note or "")))
    led = write_ledger(ROOT, cfg.get("pricing", {}), cfg.get("budget", {}), cfg)
    print(json.dumps(led, indent=2))
    if a.plan:
        guards = {g["id"]: g for g in cfg["guards"]}
        raw = DATA / "raw"
        if not (raw / "agentdojo").exists():
            return
        steps = load_all(raw)
        if a.subset:
            keep = {line.strip() for line in Path(a.subset).read_text().splitlines() if line.strip()}
            steps = [s for s in steps if s.sample_id in keep]
        total = 0.0
        print(f"\nPlanned design over {len(steps):,} steps, {a.n_conditions} conditions, repeats={a.repeats}:")
        for gid in a.plan:
            g = guards[gid]
            if g["provider"] == "vertex_endpoint":
                hrs = float(g.get("est_hours_per_condition", 1.0)) * a.n_conditions * len(steps) / 1220.0
                usd = hrs * float(g.get("usd_per_hour", 0.0))
                print(f"  {gid:26s} {g.get('model',''):48s} {hrs:6.1f} GPU-h × ${float(g.get('usd_per_hour', 0)):.2f}/h            = ${usd:9,.2f}")
                total += usd
                continue
            est = estimate(steps, g.get("model", ""), cfg.get("pricing", {}), repeats=a.repeats)
            print(f"  {gid:26s} {g.get('model',''):48s} ${est['usd']:8,.2f} per condition × {a.n_conditions} = ${est['usd'] * a.n_conditions:9,.2f}")
            total += est["usd"] * a.n_conditions
        print(f"  {'TOTAL':26s} {'':48s} {'':>10s}   ${total:9,.2f}")


def cmd_run(a):
    from action_invariance.cost import BudgetBrake, BudgetExceeded, ledger
    from action_invariance.run_guard import run_guard

    cfg = _models_cfg()
    guards = {g["id"]: g for g in cfg["guards"]}
    subset = None
    if a.subset:
        subset = {line.strip() for line in Path(a.subset).read_text().splitlines() if line.strip()}
    budget = cfg.get("budget", {})
    brake = None
    if not a.mock and budget:
        from action_invariance.cost import live_endpoint_usd

        live = live_endpoint_usd(cfg)
        spent = ledger(ROOT, cfg.get("pricing", {}))["total_usd"] + live["usd"]
        brake = BudgetBrake(budget_usd=budget["budget_usd"], spent_usd=spent, pricing=cfg.get("pricing", {}), warn_at_usd=budget.get("warn_at_usd"), live_fn=lambda: live_endpoint_usd(cfg), live_usd=live["usd"])
        print(f"[budget] spent so far ${spent:,.2f} / ${budget['budget_usd']:,.0f} (incl. ${live['usd']:,.2f} of live endpoint uptime)")
    for gid in a.guards:
        g = guards[gid]
        if g["provider"] == "vertex_endpoint" and not a.mock and not g.get("endpoint_id"):
            sys.exit(f"{gid}: set endpoint_id (and location) in configs/models.yaml after deploying it from Model Garden")
        provider = _provider("mock" if a.mock else g["provider"], cfg, g)
        for cid in a.conditions:
            try:
                rep = run_guard(g, cid, cond_root=DATA / "conditions", results_root=RESULTS, provider=provider, repeats=a.repeats, limit=a.limit, subset=subset, concurrency=a.concurrency, temperature=a.temperature, brake=brake, force=a.force)
            except BudgetExceeded as exc:
                sys.exit(f"[budget] {exc}")
            print(json.dumps(rep))
            if rep.get("stopped"):
                sys.exit(1)
    if brake is not None:
        _print_spend(cfg)


def cmd_import(a):
    from action_invariance.run_guard import import_external

    print(json.dumps(import_external(a.guard, a.condition, Path(a.source), RESULTS, repeat=a.repeat, cond_root=DATA / "conditions")))


def cmd_analyze(a):
    from action_invariance.analyze import analyze, write_report

    rep = analyze(RESULTS, covariates_file=DATA / "inventory" / "step_covariates.jsonl", perturbations_dir=None, n_boot=a.n_boot)
    write_report(RESULTS, rep)
    print((RESULTS / "summary.md").read_text(encoding="utf-8"))


def _nl_paths():
    return DATA / "renders", DATA / "renders" / "natural_language.jsonl"


def cmd_nl_generate(a):
    from action_invariance.data import load_all
    from action_invariance.render_nl import generate

    cfg = _models_cfg()
    g = cfg["generator"]["natural_language"]
    rdir, rfile = _nl_paths()
    rdir.mkdir(parents=True, exist_ok=True)
    steps = load_all(DATA / "raw")
    if a.subset:
        keep = {line.strip() for line in Path(a.subset).read_text().splitlines() if line.strip()}
        steps = [s for s in steps if s.sample_id in keep]
    provider = _provider("mock" if a.mock else g["provider"], cfg)
    print(json.dumps(generate(steps, rfile, provider=provider, model=g["model"], temperature=g.get("temperature", 0.3), limit=a.limit)))
    if not a.mock:
        _print_spend(cfg)


def cmd_nl_validate(a):
    from action_invariance.data import load_all
    from action_invariance.render_nl import validate

    cfg = _models_cfg()
    v = cfg["generator"]["validator" if a.judge == "primary" else "validator_secondary"]
    _, rfile = _nl_paths()
    provider = _provider("mock" if a.mock else v["provider"], cfg)
    print(json.dumps(validate(load_all(DATA / "raw"), rfile, provider=provider, model=v["model"], judge=a.judge, limit=a.limit)))
    if not a.mock:
        _print_spend(cfg)


def cmd_nl_review(a):
    from action_invariance.data import load_all
    from action_invariance.render_nl import apply_review, export_review

    rdir, rfile = _nl_paths()
    if a.apply:
        print(json.dumps(apply_review(Path(a.apply), rfile, load_all(DATA / "raw"))))
    else:
        print(json.dumps(export_review(rfile, rdir / "manual_review.csv")))


def cmd_nl_freeze(a):
    from action_invariance.render_nl import freeze

    rdir, _ = _nl_paths()
    print(json.dumps(freeze(rdir, ROOT, require_review=a.require_review), indent=2))
    print("\nstaged; commit with: git commit -m 'Freeze natural_language renders'")


def main(argv=None):
    p = argparse.ArgumentParser(prog="action-invariance")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("fetch", help="download the pinned TS-Bench eval files (or copy from ToolSafe-Lab)")
    s.add_argument("--from-local", help="dir with <source>/<file>.json, e.g. ../ToolSafe-Lab/data/raw/eval")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_fetch)

    s = sp.add_parser("inventory", help="parse every candidate action; per-step covariates, renderability, paired cohort")
    s.set_defaults(fn=cmd_inventory)

    s = sp.add_parser("materialize", help="write TS-Bench-schema condition files and verify invariance")
    s.add_argument("conditions", nargs="+")
    s.set_defaults(fn=cmd_materialize)

    s = sp.add_parser("show", help="print one step under the given conditions")
    s.add_argument("sample_id")
    s.add_argument("conditions", nargs="*", default=["original", "thought_removed", "code_python", "shell_cli"])
    s.set_defaults(fn=cmd_show)

    s = sp.add_parser("run", help="run guards over conditions")
    s.add_argument("--guards", nargs="+", required=True)
    s.add_argument("--conditions", nargs="+", required=True)
    s.add_argument("--repeats", type=int, default=1)
    s.add_argument("--limit", type=int)
    s.add_argument("--subset", help="file with one sample_id per line (default cohort: data/inventory/cohort_programmatic.txt)")
    s.add_argument("--concurrency", type=int, default=4)
    s.add_argument("--temperature", type=float, default=0.0)
    s.add_argument("--mock", action="store_true")
    s.add_argument("--force", action="store_true", help="start even if the pre-run estimate exceeds the budget")
    s.set_defaults(fn=cmd_run)

    s = sp.add_parser("cost", help="spend ledger from recorded token usage; optional design plan")
    s.add_argument("--plan", nargs="*", help="guard ids to estimate for the design")
    s.add_argument("--repeats", type=int, default=3)
    s.add_argument("--n-conditions", type=int, default=7)
    s.add_argument("--subset", help="cohort file to size the plan on")
    s.add_argument("--add-hours", nargs=3, metavar=("GUARD", "HOURS", "USD_PER_HOUR"))
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_cost)

    s = sp.add_parser("import-external", help="import predictions made outside this runner")
    s.add_argument("--guard", required=True)
    s.add_argument("--condition", required=True)
    s.add_argument("--source", required=True)
    s.add_argument("--repeat", type=int, default=0)
    s.set_defaults(fn=cmd_import)

    s = sp.add_parser("analyze", help="invariance / error-shift / ranking report")
    s.add_argument("--n-boot", type=int, default=1000)
    s.set_defaults(fn=cmd_analyze)

    s = sp.add_parser("nl-generate", help="natural_language: write one validated-by-checks sentence per eligible step")
    s.add_argument("--subset", help="cohort file (default: all raw steps)")
    s.add_argument("--limit", type=int)
    s.add_argument("--mock", action="store_true")
    s.set_defaults(fn=cmd_nl_generate)

    s = sp.add_parser("nl-validate", help="natural_language: judge must recover the exact (tool, arguments)")
    s.add_argument("--judge", choices=["primary", "secondary"], default="primary")
    s.add_argument("--limit", type=int)
    s.add_argument("--mock", action="store_true")
    s.set_defaults(fn=cmd_nl_validate)

    s = sp.add_parser("nl-review", help="export data/renders/manual_review.csv, or --apply a reviewed CSV")
    s.add_argument("--apply", help="path to the reviewed CSV")
    s.set_defaults(fn=cmd_nl_review)

    s = sp.add_parser("nl-freeze", help="pin reviewed natural_language renders (sha256 → FREEZE.json, git add -f)")
    s.add_argument("--no-require-review", dest="require_review", action="store_false")
    s.set_defaults(fn=cmd_nl_freeze, require_review=True)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
