"""TS-Bench step loading for all three evaluation sources.

Raw files are JSON lists of step dicts with keys
  id-interaction, id-segment, instruction, history, current_action, env_info, score
`score` is the released step label: 0.0 safe, 0.5 controversial, 1.0 unsafe. Strict
labels: 0.5 and 1.0 are unsafe (same convention as ToolSafe-Lab and guard-invariance).

`domain` is "<source>/<file>" (e.g. "agentdojo/banking", "agentharm/harmful_steps",
"asb/DPI_attack_success") so sample ids are unique across sources and the vendored runner,
cost and analysis code (which only know `domain`) work unchanged.

The raw dict is kept on every Step so materialized condition files reproduce the original
structure byte-for-byte except for the fields a condition is allowed to change.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

SOURCES: dict[str, tuple[str, ...]] = {
    "agentdojo": ("banking", "slack", "travel", "workspace"),
    "agentharm": ("benign_steps", "harmful_steps"),
    "asb": ("DPI_attack_success", "OPI_attack_success", "atttack_failure"),
}
DOMAINS = tuple(f"{s}/{f}" for s, files in SOURCES.items() for f in files)
RAW_KEYS = ("id-interaction", "id-segment", "instruction", "history", "current_action", "env_info", "score")


def instruction_id(domain: str, instruction: str) -> str:
    digest = hashlib.sha1(f"{domain}\x1f{instruction}".encode("utf-8")).hexdigest()
    return f"{domain.split('/')[0][:2]}-{digest[:10]}"


@dataclass
class Step:
    domain: str
    interaction_id: int
    segment_id: int
    instruction: str
    history: str
    current_action: str
    env_info: str
    label: float
    raw: dict = field(repr=False)
    row_index: int = 0
    dup_suffix: str = ""

    @property
    def source(self) -> str:
        return self.domain.split("/")[0]

    @property
    def subset(self) -> str:
        return self.domain.split("/", 1)[1]

    @property
    def sample_id(self) -> str:
        """Stable step id. Materialized rows carry it in `_sample_id`; raw rows derive it from
        (domain, interaction, segment), disambiguated by original row index where a source file
        repeats a key (AgentHarm-Traj and ASB-Traj do)."""
        return self.raw.get("_sample_id") or f"{self.domain}:{self.interaction_id}:{self.segment_id}{self.dup_suffix}"

    @property
    def trajectory_id(self) -> str:
        return f"{self.domain}:{self.interaction_id}"

    @property
    def instruction_id(self) -> str:
        return instruction_id(self.domain, self.raw_instruction)

    @property
    def raw_instruction(self) -> str:
        return self.raw.get("_original_instruction", self.raw["instruction"])

    @property
    def raw_current_action(self) -> str:
        """The ORIGINAL candidate action, even after materialization replaced `current_action`."""
        return self.raw.get("_original_current_action", self.raw["current_action"])

    @property
    def raw_env_info(self) -> str:
        return self.raw.get("_original_env_info", self.raw["env_info"])

    @property
    def unsafe(self) -> bool:
        return self.label >= 0.5


def load_domain(path: Path, domain: str) -> list[Step]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected a JSON list")
    steps: list[Step] = []
    for i, row in enumerate(rows):
        missing = [k for k in RAW_KEYS if k not in row]
        if missing:
            raise ValueError(f"{path}: row {i} missing keys {missing}")
        steps.append(
            Step(
                domain=domain,
                interaction_id=int(row["id-interaction"]),
                segment_id=int(row["id-segment"]),
                instruction=row["instruction"],
                history=row["history"],
                current_action=row["current_action"],
                env_info=row["env_info"],
                label=float(row["score"]),
                raw=row,
                row_index=i,
            )
        )
    # (interaction, segment) is unique in AgentDojo-Traj; other sources are checked here and
    # disambiguated deterministically by row index if ever needed (files are sha256-pinned).
    counts = Counter((s.interaction_id, s.segment_id) for s in steps)
    for s in steps:
        if counts[(s.interaction_id, s.segment_id)] > 1:
            s.dup_suffix = f"#{s.row_index}"
    return steps


def load_all(root: Path, domains: Iterable[str] = DOMAINS) -> list[Step]:
    """Load every <source>/<file>.json under `root` that exists. Missing files are skipped
    (a condition may be materialized for a subset of sources); zero rows is an error."""
    steps: list[Step] = []
    for domain in domains:
        path = Path(root) / f"{domain}.json"
        if path.exists():
            steps.extend(load_domain(path, domain))
    if not steps:
        raise FileNotFoundError(f"no TS-Bench files under {root}")
    return steps


def dump_domain(steps: list[Step], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [s.raw for s in steps]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_jsonl(path: Path, row: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)
