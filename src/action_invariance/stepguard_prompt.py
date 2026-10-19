"""StepGuard (arXiv 2608.24777) native action prompt, rendered exactly as ToolSafe-Lab's
Thought-free port does (src/toolsafe_lab/stepguard_bench_prepare.py, history_serialization.py,
stepguard_bench_mlx.py — the adapter, history normalizer and judgment parser are vendored from
there with only the parser import changed).

The upstream prompt literal is NOT bundled (license unclear): it is read with `ast` from a
hash-verified local checkout of github.com/zheng977/StepGuard at revision 47c9011 —
`tmp/StepGuard` by default, or $STEPGUARD_UPSTREAM_ROOT. Its Python is never imported or run.

Call shape: one user message (no system turn), the checkpoint's chat template, greedy decoding,
up to 4,096 new tokens; the decision is the single final `<Judgment>safe|unsafe</Judgment>`.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from action_invariance.data import Step
from action_invariance.react import parse_react_step

STEPGUARD_PROMPT_VERSION = "stepguard_native_thought_free_v1"
SOURCE_REVISION = "47c9011ee73c90be403846d35476ec55d5dab63b"
MODEL_REVISION = "c322d1ec2de38f204498d45f79646c6e80a0ea7e"
PROMPT_SOURCE_SHA256 = "023bf75c68bb98d101771fab98f30a1753b9d06802857c2c51e42dc8c9cab34c"
PROMPT_TEMPLATE_SHA256 = "cc6fdd9c96f66143b77b9c82dd5ff513b5069b1438c1be9960f0858dc39ffd12"
MAX_NEW_TOKENS = 4096

_TOOL_HEADER = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*)\s*:\s*(.*)$")
_THOUGHT_MARKER = re.compile(r"^[ \t]*(?:(?:\(\d+\)|\d+[.)])[ \t]*)?(?:\*{0,2})thought(?:\*{0,2})[ \t]*:", re.IGNORECASE | re.MULTILINE)
MARKER_RE = re.compile(r"(?im)^(?:\s*\(\d+\)\s*(?P<numbered>Thought|Action Input|Action)\s*:|\s*(?P<plain>Observation|Final Answer)\s*:)\s*")


class StepGuardInputError(ValueError):
    """The step cannot be rendered in StepGuard's native contract (fail-closed upstream)."""


@dataclass(frozen=True)
class PromptContract:
    template: str
    risk_sources: str
    source_sha256: str
    template_sha256: str


def upstream_root() -> Path:
    return Path(os.environ.get("STEPGUARD_UPSTREAM_ROOT", Path(__file__).resolve().parents[2] / "tmp" / "StepGuard"))


def load_prompt_contract(source: Path | None = None) -> PromptContract:
    """Extract the two pinned literals with AST only; never execute upstream Python."""
    source = Path(source) if source else upstream_root() / "src" / "guardrail" / "prompts" / "stepguard.py"
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PROMPT_SOURCE_SHA256:
        raise ValueError("Upstream StepGuard prompt source checksum mismatch")
    values: dict[str, str] = {}
    for node in ast.parse(raw, filename=str(source)).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in {"STEPGUARD_PROMPT", "RISK_SOURCES"}:
            continue
        if target.id in values:
            raise ValueError("Duplicate upstream prompt literal")
        expression, strip = node.value, False
        if isinstance(expression, ast.Call):
            if not isinstance(expression.func, ast.Attribute) or expression.func.attr != "strip" or expression.args or expression.keywords:
                raise ValueError("Unexpected executable expression in prompt literal")
            expression, strip = expression.func.value, True
        value = ast.literal_eval(expression)
        if not isinstance(value, str):
            raise ValueError("Upstream prompt value is not a string literal")
        values[target.id] = value.strip() if strip else value
    if set(values) != {"STEPGUARD_PROMPT", "RISK_SOURCES"}:
        raise ValueError("Required upstream action-prompt literals missing")
    template_hash = hashlib.sha256(values["STEPGUARD_PROMPT"].encode("utf-8")).hexdigest()
    if template_hash != PROMPT_TEMPLATE_SHA256:
        raise ValueError("Upstream StepGuard prompt template checksum mismatch")
    return PromptContract(values["STEPGUARD_PROMPT"], values["RISK_SOURCES"], digest, template_hash)


# ---------------------------------------------------------------- history normalizer (ToolSafe-Lab)
def _argument_value(value: str) -> tuple[object, bool]:
    stripped = value.strip()
    if not stripped:
        return "", False
    try:
        parsed, end = json.JSONDecoder().raw_decode(stripped)
    except (json.JSONDecodeError, TypeError):
        return stripped, False
    if stripped[end:].strip():
        return stripped, False
    return parsed, True


def normalize_history(history: str) -> tuple[str, dict[str, int]]:
    """Deterministic Thought-free JSON tool history and text-free diagnostics."""
    diagnostics: Counter = Counter()
    if not history.strip():
        diagnostics["empty_history"] = 1
        return "[]", dict(diagnostics)
    markers = list(MARKER_RE.finditer(history))
    diagnostics["numbered_thought_segments"] = sum((m.group("numbered") or "").casefold() == "thought" for m in markers)
    events: list[dict[str, object]] = []
    pending: dict[str, object] = {}

    def flush() -> None:
        nonlocal pending
        if pending:
            events.append(pending)
            pending = {}

    if markers and history[: markers[0].start()].strip():
        events.append({"observation": history[: markers[0].start()].strip()})
        diagnostics["prefix_context_events"] += 1
    for index, match in enumerate(markers):
        kind = (match.group("numbered") or match.group("plain") or "").casefold()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(history)
        content = history[match.end() : end].strip()
        if kind == "thought":
            diagnostics["discarded_thought_segments"] += 1
            continue
        if kind == "action":
            if "tool" in pending or "observation" in pending:
                flush()
            pending["tool"] = content
            diagnostics["action_segments"] += 1
            continue
        if kind == "action input":
            arguments, parsed = _argument_value(content)
            pending["arguments"] = arguments
            diagnostics["argument_segments"] += 1
            diagnostics["json_argument_segments" if parsed else "text_argument_segments"] += 1
            continue
        if kind == "observation":
            pending["observation"] = content
            diagnostics["observation_segments"] += 1
            flush()
            continue
        if kind == "final answer":
            diagnostics["discarded_final_answer_segments"] += 1
            flush()
    flush()
    if not markers:
        events.append({"observation": history.strip()})
        diagnostics["unmarked_context_events"] += 1
    diagnostics["output_events"] = len(events)
    return json.dumps(events, ensure_ascii=False, sort_keys=True, separators=(",", ":")), dict(diagnostics)


# ---------------------------------------------------------------- prompt adapter (ToolSafe-Lab)
def render_native_tools(env_info: str) -> tuple[str, int]:
    tools: list[list[str]] = []
    current: list[str] | None = None
    in_parameters = False
    for line in env_info.splitlines():
        stripped = line.strip()
        if not stripped:
            in_parameters = False
            continue
        header = _TOOL_HEADER.match(stripped)
        if not line.startswith(" ") and header:
            current = [header.group(1).strip(), header.group(2).strip()]
            tools.append(current)
            in_parameters = False
        elif current is not None:
            if stripped == "parameters:":
                in_parameters = True
            elif in_parameters and ":" in stripped:
                continue
            else:
                current[1] = f"{current[1]} {stripped}".strip()
    lines = []
    for name, description in tools:
        first_line = description.split("\n", 1)[0].strip()
        lines.append(f"- {name}: {first_line}" if first_line else f"- {name}")
    return "\n".join(lines), len(tools)


def _action_text(tool: str, arguments: Any) -> str:
    return "[ACTION]: " + json.dumps({"name": tool, "arguments": arguments}, ensure_ascii=False, allow_nan=False)


def render_prompt(contract: PromptContract, *, instruction: str, history: str, current_action: str, env_info: str) -> str:
    """Only the four benchmark fields enter the prompt. Raises StepGuardInputError with the
    upstream port's stable error codes when the step is outside StepGuard's input contract."""
    errors: list[str] = []
    parsed = parse_react_step(current_action)
    if not parsed.status.startswith("ok_"):
        errors.append("current_action_parse_failure")
    if parsed.action_marker_count != 1 or parsed.action_input_marker_count != 1:
        errors.append("ambiguous_or_missing_current_action_markers")
    if parsed.thought_marker_count > 1:
        errors.append("ambiguous_current_thought_markers")
    if not parsed.action_before_input or not parsed.tool_name.strip():
        errors.append("invalid_current_action_boundaries")
    if parsed.suffix or (parsed.argument_json_parseable and not parsed.argument_json_exact):
        errors.append("nonexact_json_arguments")
    arguments = json.loads(parsed.normalized_arguments) if parsed.argument_json_parseable else parsed.normalized_arguments
    if not parsed.argument_json_parseable and _THOUGHT_MARKER.search(arguments):
        errors.append("structural_thought_in_text_arguments")
    normalized, hdiag = normalize_history(history)
    if len(_THOUGHT_MARKER.findall(history)) != hdiag.get("discarded_thought_segments", 0):
        errors.append("unsupported_history_thought_markers")
    events = json.loads(normalized)
    parts: list[str] = []
    action_count = 0
    for event in events:
        if not isinstance(event, dict) or not event or set(event) - {"tool", "arguments", "observation"}:
            errors.append("invalid_history_event")
            continue
        if "tool" in event:
            if not isinstance(event["tool"], str) or not event["tool"].strip():
                errors.append("missing_history_tool")
                continue
            if "arguments" not in event:
                errors.append("missing_history_arguments")
                continue
            action_count += 1
            parts.append(f"[Step {action_count}] [AGENT]:\n" + _action_text(event["tool"], event["arguments"]))
        elif "arguments" in event:
            errors.append("missing_history_tool")
        if "observation" in event:
            if not isinstance(event["observation"], str):
                errors.append("invalid_history_observation")
                continue
            prefix = f"[Observation after Step {action_count}] [ENVIRONMENT]:" if action_count else "[ENVIRONMENT]:"
            parts.append(f"{prefix} {event['observation']}")
    tool_text, tool_count = render_native_tools(env_info)
    if env_info.strip() and tool_count == 0:
        errors.append("unparsed_nonempty_tool_descriptions")
    if errors:
        raise StepGuardInputError("input_parse_error:" + ",".join(sorted(set(errors))))
    return contract.template.format(
        risk_sources=contract.risk_sources, user_request=instruction,
        history_text="\n\n".join(parts) if parts else "(no prior history)",
        current_action_text=f"[Step {action_count + 1}] [AGENT]:\n" + _action_text(parsed.tool_name, arguments),
        tool_list_text=tool_text,
    )


_CONTRACT: PromptContract | None = None


def render_stepguard(step: Step) -> str:
    global _CONTRACT
    if _CONTRACT is None:
        _CONTRACT = load_prompt_contract()
    return render_prompt(_CONTRACT, instruction=step.instruction, history=step.history, current_action=step.current_action, env_info=step.env_info)


def parse_judgment(text: str) -> int | None:
    """1 = unsafe, 0 = safe, None = invalid (exactly one <Judgment>…</Judgment> pair required)."""
    if not isinstance(text, str):
        return None
    if len(re.findall(r"<\s*Judgment\b", text, re.IGNORECASE)) != 1 or len(re.findall(r"<\s*/\s*Judgment\b", text, re.IGNORECASE)) != 1:
        return None
    match = re.search(r"<Judgment>\s*(safe|unsafe)\s*</Judgment>", text, re.IGNORECASE)
    return int(match.group(1).lower() == "unsafe") if match else None


def parse_stepguard(text: str) -> dict:
    j = parse_judgment(text)
    if j is None:
        raise ValueError("invalid_judgment: no single <Judgment>safe|unsafe</Judgment> in response")
    return {"judgment": "unsafe" if j else "safe", "composite": float(j)}
