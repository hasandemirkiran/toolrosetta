"""Fetch the pinned TS-Bench evaluation files for all three sources (or copy them from a
local ToolSafe-Lab checkout) and verify sha256 against data/manifest.json."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import requests


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(manifest_path: Path, raw_root: Path, *, from_local: Path | None = None, force: bool = False) -> list[dict]:
    """Populate raw_root/<source>/<file>.json for every manifest asset.
    from_local: a directory holding <source>/<file>.json (e.g. ToolSafe-Lab/data/raw/eval)."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    raw_root = Path(raw_root)
    report = []
    for asset in manifest["assets"]:
        target = raw_root / asset["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not force and sha256_of(target) == asset["sha256"]:
            report.append({"path": asset["path"], "sha256": asset["sha256"], "ok": True, "source": "cached"})
            continue
        src = Path(from_local) / asset["path"] if from_local is not None else None
        if src is not None and src.exists():
            shutil.copyfile(src, target)
            source = str(src)
        else:
            resp = requests.get(asset["source"], timeout=180)
            resp.raise_for_status()
            target.write_bytes(resp.content)
            source = asset["source"]
        digest = sha256_of(target)
        if digest != asset["sha256"]:
            raise ValueError(f"sha256 mismatch for {asset['path']}: expected {asset['sha256']}, got {digest}")
        report.append({"path": asset["path"], "sha256": digest, "ok": True, "source": source})
    (raw_root / "FETCH_REPORT.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
