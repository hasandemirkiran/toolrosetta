"""Deterministic, reversible re-renderings of the candidate action.

Every condition changes ONLY `current_action` (and, for wrapper conditions, appends one
fixed tool schema to `env_info`). `instruction`, `history` and `score` are never touched.

  original            reference
  placebo_whitespace  trailing whitespace only (tokenization-noise floor)
  placebo_env_wrapper action unchanged; env_info gains the execute_python schema
                      (isolates the env_info change that code_python also makes)
  thought_removed     the ReAct Thought is dropped; Action / Action Input untouched
  code_python         Thought kept; the call becomes  execute_python(code="tool(k=v, ...)")
  shell_cli           Thought kept; the call becomes  execute_bash(command="tool --k v ...")
  natural_language    Thought kept; the call is replaced by one prose sentence (LLM-written,
                      validated separately; see docs/RENDERING_DESIGN.md)

code_python and shell_cli are produced by code, not by a model, and every rendered row is
parsed back and compared with the original (tool, arguments) before it is accepted. A row
that does not round-trip is excluded from that condition and counted.
"""
from __future__ import annotations

import ast
import json
import keyword
import re
import shlex
from dataclasses import dataclass, field

from action_invariance.data import Step
from action_invariance.react import ACTION_INPUT_MARKER, ACTION_MARKER, ParsedAction, parse_react_step

ENV_SEPARATOR = "\n\n"
WRAPPERS: dict[str, dict] = {
    "code_python": {
        "tool": "execute_python",
        "arg": "code",
        "schema": (
            "execute_python: Execute a Python snippet in the agent runtime. Every available tool is "
            "exposed as a Python function with the same name and keyword arguments; the snippet's "
            "return value is returned as the observation.\n"
            "  parameters:\n"
            "    code: {'description': 'Python source to execute.', 'type': 'string'}"
        ),
    },
    "shell_cli": {
        "tool": "execute_bash",
        "arg": "command",
        "schema": (
            "execute_bash: Run a shell command in the agent runtime. Every available tool is exposed "
            "as a command-line program with the same name; parameters are passed as --options and "
            "the program's output is returned as the observation.\n"
            "  parameters:\n"
            "    command: {'description': 'Shell command to run.', 'type': 'string'}"
        ),
    },
}
CONTROL_CONDITIONS = ("original", "placebo_whitespace", "placebo_env_wrapper")
PROGRAMMATIC_CONDITIONS = ("thought_removed", "code_python", "shell_cli")
LLM_CONDITIONS = ("natural_language",)
ALL_CONDITIONS = CONTROL_CONDITIONS + PROGRAMMATIC_CONDITIONS + LLM_CONDITIONS


@dataclass
class Rendered:
    condition: str
    status: str  # "ok" or the reason the row cannot be rendered in this condition
    current_action: str
    env_info: str
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# ---------------------------------------------------------------- python rendering
def _py_ident(s: str) -> bool:
    return s.isidentifier() and not keyword.iskeyword(s)


def _py_literal(v) -> str:
    lit = repr(v)
    if ast.literal_eval(lit) != v:  # e.g. NaN from a lenient JSON decoder
        raise ValueError("value is not a Python literal")
    return lit


def python_call(tool: str, args: dict) -> tuple[str, dict]:
    func = tool if _py_ident(tool) else f"tools[{_py_literal(tool)}]"
    if all(isinstance(k, str) and _py_ident(k) for k in args):
        body = ", ".join(f"{k}={_py_literal(v)}" for k, v in args.items())
        code, key_style = f"{func}({body})", "kwargs"
    else:
        code, key_style = f"{func}(**{_py_literal(dict(args))})", "dict"
    return code, {"key_style": key_style, "func_style": "name" if _py_ident(tool) else "subscript"}


def parse_python_call(code: str) -> tuple[str, dict]:
    node = ast.parse(code, mode="eval").body
    if not isinstance(node, ast.Call) or node.args:
        raise ValueError("not a keyword-only call")
    if isinstance(node.func, ast.Name):
        tool = node.func.id
    elif isinstance(node.func, ast.Subscript) and isinstance(node.func.value, ast.Name) and node.func.value.id == "tools":
        tool = ast.literal_eval(node.func.slice)
    else:
        raise ValueError("unexpected callee")
    if len(node.keywords) == 1 and node.keywords[0].arg is None:
        return tool, ast.literal_eval(node.keywords[0].value)
    if any(k.arg is None for k in node.keywords):
        raise ValueError("mixed ** and keyword arguments")
    return tool, {k.arg: ast.literal_eval(k.value) for k in node.keywords}


# ---------------------------------------------------------------- shell rendering
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_]+$")


def _sh_canon(v) -> str:
    """Canonical string form of an argument value (shell has no types)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def shell_command(tool: str, args: dict) -> tuple[str, dict]:
    kebab = all(isinstance(k, str) and _SAFE_KEY.match(k) for k in args)
    opt = (lambda k: "--" + k.replace("_", "-")) if kebab else (lambda k: "--" + k)
    parts = [shlex.quote(tool)]
    for k, v in args.items():
        parts.append(f"{shlex.quote(opt(k))} {shlex.quote(_sh_canon(v))}")
    return " ".join(parts), {"key_style": "kebab" if kebab else "verbatim"}


def parse_shell_command(cmd: str) -> tuple[str, dict]:
    toks = shlex.split(cmd)
    if not toks or (len(toks) - 1) % 2:
        raise ValueError("malformed option list")
    out = {}
    for i in range(1, len(toks), 2):
        if not toks[i].startswith("--"):
            raise ValueError(f"expected an option, got {toks[i]!r}")
        out[toks[i][2:]] = toks[i + 1]
    return toks[0], out


def shell_roundtrip_ok(tool: str, args: dict, cmd: str, key_style: str) -> bool:
    t, got = parse_shell_command(cmd)
    conv = (lambda k: k.replace("_", "-")) if key_style == "kebab" else (lambda k: k)
    want = {conv(k): _sh_canon(v) for k, v in args.items()}
    return t == tool and got == want


# ---------------------------------------------------------------- assembling the step text
def placebo_whitespace(text: str) -> str:
    return text.rstrip() + " "


def env_with_wrapper(env_info: str, condition: str) -> str:
    return env_info.rstrip() + ENV_SEPARATOR + WRAPPERS[condition]["schema"]


def wrap_block(text: str, parsed: ParsedAction, wrapper_tool: str, payload: str) -> str:
    """Replace the Action / Action Input block of `text` with a call to `wrapper_tool`,
    keeping the preamble, the Thought, the marker numbering and any trailing suffix."""
    block = parsed.thought_removed_text
    m_a = ACTION_MARKER.search(block)
    m_i = ACTION_INPUT_MARKER.search(block)
    if m_a is None or m_i is None or m_i.start() < m_a.end():
        raise ValueError("cannot locate Action / Action Input markers")
    new_block = block[: m_a.end()] + " " + wrapper_tool + "\n" + block[m_i.start() : m_i.end()] + " " + payload
    if parsed.suffix:
        new_block += "\n" + parsed.suffix
    idx = text.rfind(block)
    if idx < 0:
        raise ValueError("thought_removed_text is not a suffix of the step text")
    return text[:idx] + new_block


def _tool_in(text: str, tool: str) -> bool:
    return bool(tool) and tool.lower() in text.lower()


def render_condition(step: Step, condition: str) -> Rendered:
    text, env = step.raw_current_action, step.raw_env_info
    parsed = parse_react_step(text)
    meta = {
        "parse_status": parsed.status,
        "tool_name": parsed.tool_name,
        "n_args": len(parsed.arguments_json) if isinstance(parsed.arguments_json, dict) else None,
        "tool_in_thought": _tool_in(parsed.thought, parsed.tool_name),
        "has_thought": parsed.thought_marker_count > 0,
        "has_suffix": bool(parsed.suffix),
    }
    if condition == "original":
        return Rendered(condition, "ok", text, env, meta)
    if condition == "placebo_whitespace":
        return Rendered(condition, "ok", placebo_whitespace(text), env, meta)
    if condition == "placebo_env_wrapper":
        return Rendered(condition, "ok", text, env_with_wrapper(env, "code_python"), meta)
    if condition == "thought_removed":
        if not parsed.thought_removed_text:
            return Rendered(condition, f"parse:{parsed.status}", text, env, meta)
        if not meta["has_thought"] and not parsed.preamble:
            return Rendered(condition, "no_thought_to_remove", text, env, meta)
        return Rendered(condition, "ok", parsed.thought_removed_text, env, {**meta, "removed_chars": len(text) - len(parsed.thought_removed_text)})
    if condition in WRAPPERS:
        if parsed.status != "ok_json_object":
            return Rendered(condition, f"parse:{parsed.status}", text, env, meta)
        tool, args = parsed.tool_name, parsed.arguments_json
        try:
            if condition == "code_python":
                inner, m2 = python_call(tool, args)
                if parse_python_call(inner) != (tool, args):
                    return Rendered(condition, "roundtrip_failed", text, env, meta)
            else:
                inner, m2 = shell_command(tool, args)
                if not shell_roundtrip_ok(tool, args, inner, m2["key_style"]):
                    return Rendered(condition, "roundtrip_failed", text, env, meta)
            payload = json.dumps({WRAPPERS[condition]["arg"]: inner}, ensure_ascii=False)
            new_text = wrap_block(text, parsed, WRAPPERS[condition]["tool"], payload)
        except (ValueError, SyntaxError, TypeError) as exc:
            return Rendered(condition, f"render_error:{type(exc).__name__}", text, env, meta)
        nested = any(isinstance(v, (dict, list)) for v in args.values())
        return Rendered(condition, "ok", new_text, env_with_wrapper(env, condition), {**meta, **m2, "nested_args": nested, "inner_chars": len(inner)})
    if condition in LLM_CONDITIONS:
        return Rendered(condition, "needs_llm_render", text, env, meta)
    raise KeyError(f"unknown condition {condition!r}")


def eligible_for_all_programmatic(step: Step) -> bool:
    """True if the step can be rendered under every programmatic condition (paired cohort)."""
    return all(render_condition(step, c).ok for c in PROGRAMMATIC_CONDITIONS)
