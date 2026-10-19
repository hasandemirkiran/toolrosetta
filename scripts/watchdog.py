"""Endpoint watchdog: the technical backstop for the runaway-cost failure mode.

Runs in a loop (or once with --once) and UNDEPLOYS any self-deployed endpoint when either
  * its uptime exceeds --max-hours (default 8), or
  * recorded spend + live endpoint uptime exceeds the budget cap in configs/models.yaml.
Undeployed hours are appended to results/endpoint_hours.jsonl automatically.

Start it in the background before any endpoint run and leave it:
  nohup .venv/bin/python scripts/watchdog.py --every 300 > results/watchdog.log 2>&1 &

It only touches endpoints whose display name starts with "ai-" (this repo's naming).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_invariance.cost import add_endpoint_hours, ledger, live_endpoint_usd  # noqa: E402


def _token():
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _api(method, region, path, **kw):
    r = requests.request(method, f"https://{region}-aiplatform.googleapis.com/v1/{path}", headers={"Authorization": f"Bearer {_token()}"}, timeout=120, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} → {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


def sweep(cfg: dict, *, max_hours: float, dry_run: bool) -> list[str]:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or cfg.get("project")
    guards = {f"ai-{g['id']}": g for g in cfg["guards"] if g.get("provider") == "vertex_endpoint"}
    regions = sorted({g["location"] for g in guards.values() if g.get("location")})
    cap = float(cfg.get("budget", {}).get("budget_usd", 0) or 0)
    spent = ledger(ROOT, cfg.get("pricing", {}))["total_usd"] + live_endpoint_usd(cfg)["usd"]
    over_budget = cap and spent > cap
    now = dt.datetime.now(dt.timezone.utc)
    actions = []
    for region in regions:
        for ep in _api("GET", region, f"projects/{project}/locations/{region}/endpoints").get("endpoints", []):
            name = ep.get("displayName", "")
            if not name.startswith("ai-"):
                continue
            for dm in ep.get("deployedModels", []):
                hours = (now - dt.datetime.fromisoformat(dm["createTime"].replace("Z", "+00:00"))).total_seconds() / 3600
                reason = "over_budget" if over_budget else ("max_hours" if hours > max_hours else None)
                if not reason:
                    continue
                rate = float(guards.get(name, {}).get("usd_per_hour", 0))
                msg = f"{name} ({region}) up {hours:.1f} h → UNDEPLOY [{reason}] spent≈${spent:,.2f} cap=${cap:,.0f}"
                actions.append(msg)
                print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg, flush=True)
                if dry_run:
                    continue
                _api("POST", region, f"{ep['name']}:undeployModel", json={"deployedModelId": dm["id"]})
                _api("DELETE", region, ep["name"])
                add_endpoint_hours(ROOT, guard=name[3:], hours=hours, usd_per_hour=rate, note=f"watchdog:{reason}")
    return actions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-hours", type=float, default=8.0)
    ap.add_argument("--every", type=int, default=300, help="seconds between sweeps")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "configs" / "models.yaml").read_text())
    while True:
        try:
            acts = sweep(cfg, max_hours=a.max_hours, dry_run=a.dry_run)
            if not acts:
                print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ok", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "error:", exc, flush=True)
        if a.once:
            break
        time.sleep(a.every)


if __name__ == "__main__":
    main()
