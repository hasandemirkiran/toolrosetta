"""Ping every model in configs/models.yaml through its Vertex provider and record which
ids respond. Requires ADC (`gcloud auth application-default login`) and
GOOGLE_CLOUD_PROJECT. Writes configs/models.verified.json; does not edit models.yaml.

Usage: python scripts/verify_models.py [--only gemini,claude,maas,translation_api]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_invariance.vertex import make_provider  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="gemini,claude,maas,translation_api")
    a = ap.parse_args()
    only = set(a.only.split(","))
    cfg = yaml.safe_load((ROOT / "configs" / "models.yaml").read_text())
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        sys.exit("export GOOGLE_CLOUD_PROJECT=<your-project-id>")

    entries = []
    for name, g in cfg.get("generator", {}).items():
        if isinstance(g, dict) and g.get("provider") and g.get("model"):
            entries.append((f"generator.{name}", g["provider"], g["model"]))
    for name, m in cfg.get("mitigation", {}).items():
        if isinstance(m, dict) and m.get("provider") and m.get("model"):
            entries.append((f"mitigation.{name}", m["provider"], m["model"]))
    for guard in cfg["guards"]:
        if guard["provider"] != "local_toolsafe_lab":
            entries.append((f"guard.{guard['id']}", guard["provider"], guard["model"], guard.get("location")))

    providers, out = {}, []
    for name, prov, model, *rest in entries:
        if prov not in only:
            continue
        loc = (rest[0] if rest else None) or cfg.get("location")
        rec = {"entry": name, "provider": prov, "model": model, "location": loc, "ok": False, "error": None, "latency_s": None}
        try:
            if (prov, loc) not in providers:
                providers[(prov, loc)] = make_provider(prov, project, loc)
            p = providers[(prov, loc)]
            t0 = time.time()
            if prov == "translation_api":
                text = p.translate("Please show me my unread emails.", "de")
            else:
                text = p.complete("Reply with the single word OK.", "ping", model=model, temperature=0.0, max_tokens=8).text
            rec.update(ok=True, latency_s=round(time.time() - t0, 2), sample=text[:60])
        except Exception as exc:  # noqa: BLE001
            rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
        out.append(rec)
        print(("OK  " if rec["ok"] else "FAIL"), f"{name:28s} {prov:16s} {model:48s}", rec["error"] or "")
    (ROOT / "configs" / "models.verified.json").write_text(json.dumps(out, indent=2) + "\n")
    print(f"\n{sum(r['ok'] for r in out)}/{len(out)} reachable → configs/models.verified.json")


if __name__ == "__main__":
    main()
