# Vendored from ../guard-invariance/src/guard_invariance/cost.py (commit edabb03, 2026-09-04); only the package import path changed.
"""Spend ledger and budget brake.

Every hosted call made by this repo records `usage: {input, output}` tokens and `model` on
its JSONL row (guard results, perturbations, validations). `ledger()` sums those against
configs/models.yaml `pricing`; `estimate()` predicts a run from character counts before
it starts. `BudgetBrake` is consulted by the runner before and during a run.

This only sees calls made through this repo. Set a Google Cloud billing budget alert as
the backstop for everything else (Cloud budgets alert; they do not stop spend).
"""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path

from action_invariance.data import read_jsonl


def _price(pricing: dict, model: str | None) -> tuple[float, float]:
    p = pricing.get(model or "", pricing.get("default", [1.0, 5.0]))
    if p == "endpoint":
        return 0.0, 0.0
    return float(p[0]) / 1e6, float(p[1]) / 1e6


def row_cost(pricing: dict, row: dict) -> float:
    u = row.get("usage") or {}
    i, o = u.get("input") or 0, u.get("output") or 0
    pi, po = _price(pricing, row.get("model") or row.get("generator") or row.get("judge_model"))
    return i * pi + o * po


ENDPOINT_HOURS_FILE = "results/endpoint_hours.jsonl"


def add_endpoint_hours(root: Path, *, guard: str, hours: float, usd_per_hour: float, note: str = "") -> dict:
    """Self-deployed endpoints bill per GPU-hour, not per token. Record deploy→undeploy spans
    here (`action-invariance cost --add-hours GUARD HOURS RATE`) so the brake sees them."""
    import time

    row = {"guard": guard, "hours": float(hours), "usd_per_hour": float(usd_per_hour), "usd": round(float(hours) * float(usd_per_hour), 2), "note": note, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    from action_invariance.data import append_jsonl

    append_jsonl(Path(root) / ENDPOINT_HOURS_FILE, row)
    return row


def live_endpoint_usd(cfg: dict) -> dict:
    """Cost of endpoints that are deployed RIGHT NOW (uptime × usd_per_hour), via the REST API
    with ADC. Returns {"usd": 0.0, "endpoints": []} when there are no credentials or no
    endpoints, so callers can always add it. This closes the gap between `--add-hours`
    (recorded after undeploy) and reality (billing while deployed)."""
    import datetime as dt
    import os

    out = {"usd": 0.0, "endpoints": []}
    guards = {g["id"]: g for g in cfg.get("guards", []) if g.get("provider") == "vertex_endpoint"}
    regions = sorted({g.get("location") for g in guards.values() if g.get("location")})
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or (None if str(cfg.get("project", "")).startswith("${") else cfg.get("project"))
    if not guards or not project:
        return out
    try:
        import google.auth
        import google.auth.transport.requests
        import requests

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
    except Exception:  # noqa: BLE001 – no ADC in this environment
        return out
    rate = {f"ai-{gid}": float(g.get("usd_per_hour", 0)) for gid, g in guards.items()}
    now = dt.datetime.now(dt.timezone.utc)
    for region in regions:
        try:
            r = requests.get(f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}/endpoints", headers={"Authorization": f"Bearer {creds.token}"}, timeout=30)
            r.raise_for_status()
        except Exception:  # noqa: BLE001
            continue
        for ep in r.json().get("endpoints", []):
            for dm in ep.get("deployedModels", []):
                hours = (now - dt.datetime.fromisoformat(dm["createTime"].replace("Z", "+00:00"))).total_seconds() / 3600
                usd = hours * rate.get(ep.get("displayName", ""), 0.0)
                out["usd"] += usd
                out["endpoints"].append({"display_name": ep.get("displayName"), "region": region, "uptime_h": round(hours, 2), "usd": round(usd, 2)})
    out["usd"] = round(out["usd"], 2)
    return out


def ledger(root: Path, pricing: dict) -> dict:
    """Sum recorded spend: per-token rows in results/raw + data/perturbations, plus endpoint hours."""
    root = Path(root)
    by_model: dict[str, dict] = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0, "usd": 0.0})
    by_guard: dict[str, float] = defaultdict(float)
    files = list((root / "results" / "raw").rglob("r*.jsonl")) + list((root / "data" / "perturbations").glob("*.jsonl"))
    for f in files:
        for r in read_jsonl(f):
            if not r.get("usage") or r.get("mock"):
                continue
            model = r.get("model") or r.get("generator") or r.get("judge_model") or "unknown"
            if pricing.get(model) == "endpoint":  # self-deployed: tokens are free, hours are billed below
                continue
            c = row_cost(pricing, r)
            m = by_model[model]
            m["calls"] += 1
            m["input"] += r["usage"].get("input") or 0
            m["output"] += r["usage"].get("output") or 0
            m["usd"] += c
            by_guard[r.get("guard") or r.get("condition") or "generation"] += c
    endpoint_usd = 0.0
    hours_rows = read_jsonl(root / ENDPOINT_HOURS_FILE)
    for h in hours_rows:
        endpoint_usd += h["usd"]
        by_guard[h["guard"]] += h["usd"]
    total = sum(m["usd"] for m in by_model.values()) + endpoint_usd
    return {
        "total_usd": round(total, 2),
        "token_usd": round(total - endpoint_usd, 2),
        "endpoint_usd": round(endpoint_usd, 2),
        "endpoint_hours": round(sum(h["hours"] for h in hours_rows), 2),
        "by_model": {k: {**v, "usd": round(v["usd"], 2)} for k, v in sorted(by_model.items())},
        "by_guard": {k: round(v, 2) for k, v in sorted(by_guard.items())},
    }


def estimate(steps, model: str, pricing: dict, *, repeats: int = 1, chars_per_token: float = 4.0, system_chars: int = 1500, output_tokens: int = 80) -> dict:
    """Pre-run estimate from rendered-input character counts."""
    chars = sum(len(s.history) + len(s.current_action) + len(s.env_info) + len(s.instruction) + system_chars + 200 for s in steps)
    inp = chars / chars_per_token * repeats
    out = output_tokens * len(steps) * repeats
    pi, po = _price(pricing, model)
    return {"model": model, "calls": len(steps) * repeats, "input_tokens": int(inp), "output_tokens": int(out), "usd": inp * pi + out * po}


class BudgetBrake:
    """Thread-safe running total with a hard cap. Raise when the cap would be exceeded."""

    def __init__(self, *, budget_usd: float, spent_usd: float, pricing: dict, warn_at_usd: float | None = None, live_fn=None, live_usd: float = 0.0, refresh_every: int = 200):
        self.budget = float(budget_usd)
        self.spent = float(spent_usd)
        self.pricing = pricing
        self.warn_at = warn_at_usd
        self._lock = threading.Lock()
        self._warned = False
        # Live endpoint uptime keeps billing during a run; re-poll it every `refresh_every`
        # recorded rows and fold the delta into `spent`.
        self._live_fn = live_fn
        self._live_usd = float(live_usd)
        self._refresh_every = refresh_every
        self._n = 0

    def _refresh_live(self) -> None:
        if self._live_fn is None:
            return
        try:
            new = float(self._live_fn()["usd"])
        except Exception:  # noqa: BLE001
            return
        self.spent += new - self._live_usd
        self._live_usd = new

    def check_estimate(self, est_usd: float, *, force: bool = False) -> None:
        if not force and self.spent + est_usd > self.budget:
            raise BudgetExceeded(f"estimated ${est_usd:,.2f} + spent ${self.spent:,.2f} exceeds budget ${self.budget:,.2f}; pass --force to override")

    def record(self, row: dict) -> float:
        with self._lock:
            c = row_cost(self.pricing, row)
            self.spent += c
            self._n += 1
            if self._n % self._refresh_every == 0:
                self._refresh_live()
            if self.warn_at and not self._warned and self.spent >= self.warn_at:
                self._warned = True
                print(f"[budget] WARNING: spend ${self.spent:,.2f} passed warn threshold ${self.warn_at:,.2f}", flush=True)
            if self.spent > self.budget:
                raise BudgetExceeded(f"spend ${self.spent:,.2f} exceeds budget ${self.budget:,.2f}; stopping")
            return c

    def snapshot(self) -> dict:
        return {"spent_usd": round(self.spent, 2), "budget_usd": self.budget, "remaining_usd": round(self.budget - self.spent, 2)}


class BudgetExceeded(RuntimeError):
    pass


def write_ledger(root: Path, pricing: dict, budget: dict, cfg: dict | None = None) -> dict:
    led = ledger(root, pricing)
    live = live_endpoint_usd(cfg) if cfg else {"usd": 0.0, "endpoints": []}
    led["live_endpoints"] = live
    led["total_incl_live_usd"] = round(led["total_usd"] + live["usd"], 2)
    led["budget"] = {"budget_usd": budget.get("budget_usd"), "remaining_usd": round(float(budget.get("budget_usd", 0)) - led["total_incl_live_usd"], 2)}
    out = Path(root) / "results" / "cost_ledger.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(led, indent=2) + "\n", encoding="utf-8")
    return led
