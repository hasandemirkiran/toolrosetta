"""Deterministic ReAct step parser.

Vendored from ToolSafe-Lab (src/toolsafe_lab/react_parser.py, `parse_react_step`) so the
field boundaries here are exactly the ones used in the representation-validity study.
Only the artifact-writing helpers were dropped.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

MARKER_PREFIX = r"^[ \t]*(?:(?:\(\d+\)|\d+[.)])[ \t]*)?(?:\*{0,2})"
THOUGHT_MARKER = re.compile(MARKER_PREFIX + r"thought(?:\*{0,2})[ \t]*:", re.IGNORECASE | re.MULTILINE)
ACTION_MARKER = re.compile(MARKER_PREFIX + r"action(?:\*{0,2})[ \t]*:", re.IGNORECASE | re.MULTILINE)
ACTION_INPUT_MARKER = re.compile(MARKER_PREFIX + r"action[ \t_]+input(?:\*{0,2})[ \t]*:", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class ParsedAction:
    status: str
    preamble: str
    thought: str
    tool_name: str
    argument_text: str
    normalized_arguments: str
    arguments_json: Any
    suffix: str
    thought_removed_text: str
    execution_only_text: str
    thought_marker_count: int
    action_marker_count: int
    action_input_marker_count: int
    thought_before_action: bool
    action_before_input: bool
    json_like_braces: bool
    argument_json_parseable: bool
    argument_json_exact: bool
    argument_json_object: bool

    @property
    def ok(self) -> bool:
        return self.status.startswith("ok_")


def _json_argument(value: str) -> tuple[bool, bool, Any, str, str]:
    candidate = value.strip()
    if not candidate:
        return False, False, None, "", ""
    if candidate.startswith("```"):
        opening_end = candidate.find("\n")
        closing_start = candidate.rfind("```")
        if opening_end >= 0 and closing_start > opening_end:
            fenced_value = candidate[opening_end + 1 : closing_start].strip()
            suffix = candidate[closing_start + 3 :].strip()
            try:
                parsed = json.loads(fenced_value)
            except (json.JSONDecodeError, TypeError):
                return False, False, None, candidate, ""
            normalized = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return True, not suffix, parsed, normalized, suffix
    leading = len(candidate) - len(candidate.lstrip())
    try:
        parsed, end = json.JSONDecoder().raw_decode(candidate.lstrip())
    except (json.JSONDecodeError, TypeError):
        return False, False, None, candidate, ""
    suffix = candidate[leading + end :].strip()
    normalized = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return True, not suffix, parsed, normalized, suffix


def parse_react_step(text: str) -> ParsedAction:
    thoughts = list(THOUGHT_MARKER.finditer(text))
    actions = list(ACTION_MARKER.finditer(text))
    action_inputs = list(ACTION_INPUT_MARKER.finditer(text))
    action_input = action_inputs[0] if action_inputs else None
    preceding_actions = [c for c in actions if c.start() < action_input.start()] if action_input is not None else []
    action = preceding_actions[-1] if preceding_actions else (actions[0] if actions else None)
    preceding_thoughts = [c for c in thoughts if c.start() < action.start()] if action is not None else []
    thought = preceding_thoughts[0] if preceding_thoughts else (thoughts[0] if thoughts else None)

    boundary = thought or action or action_input
    preamble = text[: boundary.start()].strip() if boundary is not None else text.strip()

    rationale = ""
    if thought is not None and action is not None and thought.start() < action.start():
        rationale = text[thought.end() : action.start()].strip()
    elif thought is not None:
        rationale = text[thought.end() :].strip()

    tool_name = ""
    if action is not None:
        tool_suffix = text[action.end() :]
        tool_name = tool_suffix.splitlines()[0].strip() if tool_suffix else ""

    argument_text = text[action_input.end() :].strip() if action_input is not None else ""
    json_parseable, json_exact, arguments_json, normalized, suffix = _json_argument(argument_text)
    if not json_parseable:
        normalized = argument_text

    action_before_input = action is not None and action_input is not None and action.start() < action_input.start()
    thought_before_action = thought is not None and action is not None and thought.start() < action.start()

    if action is None:
        status = "missing_action"
    elif action_input is None:
        status = "missing_action_input"
    elif not action_before_input:
        status = "out_of_order_markers"
    elif not tool_name:
        status = "empty_tool_name"
    elif not argument_text:
        status = "empty_arguments"
    elif json_parseable and isinstance(arguments_json, dict):
        status = "ok_json_object"
    elif json_parseable:
        status = "ok_json_value"
    else:
        status = "ok_text_arguments"

    thought_removed_text = text[action.start() :].strip() if action is not None else ""
    execution_only_text = "\n".join(["[TOOL_NAME]", tool_name, "[ARGUMENTS]", normalized]).strip()

    return ParsedAction(
        status=status, preamble=preamble, thought=rationale, tool_name=tool_name, argument_text=argument_text,
        normalized_arguments=normalized, arguments_json=arguments_json if json_parseable else None, suffix=suffix,
        thought_removed_text=thought_removed_text, execution_only_text=execution_only_text,
        thought_marker_count=len(thoughts), action_marker_count=len(actions), action_input_marker_count=len(action_inputs),
        thought_before_action=thought_before_action, action_before_input=action_before_input,
        json_like_braces="{" in text and "}" in text, argument_json_parseable=json_parseable,
        argument_json_exact=json_exact, argument_json_object=json_parseable and isinstance(arguments_json, dict),
    )
