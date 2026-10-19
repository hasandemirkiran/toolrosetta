import json

import pytest

from action_invariance.data import Step
from action_invariance.react import parse_react_step
from action_invariance.render import (
    PROGRAMMATIC_CONDITIONS, WRAPPERS, parse_python_call, parse_shell_command, python_call, render_condition,
    shell_command, shell_roundtrip_ok,
)

ARGS = [
    {},
    {"hotel": "Le Marais Boutique", "start_day": "2025-01-11", "end_day": "2025-01-15"},
    {"recipient": "US133000000121212121212", "amount": 100.5, "subject": "it's \"quoted\"\nline2", "date": None},
    {"n": 5, "verbose": True, "tags": ["a", "b c"], "meta": {"k": {"z": 1}}},
    {"from": "x", "class": "y"},            # Python keywords as keys → dict form
    {"hotel-name": "A", "weird key": "b"},  # non-identifier keys
    {"text": "ünïcödé — $HOME `cmd` ; rm -rf / && echo 'hi'"},
]


@pytest.mark.parametrize("args", ARGS)
@pytest.mark.parametrize("tool", ["reserve_hotel", "send_money", "tools.with.dots", "get-thing"])
def test_python_roundtrip(tool, args):
    code, _ = python_call(tool, args)
    assert parse_python_call(code) == (tool, args)


@pytest.mark.parametrize("args", ARGS)
@pytest.mark.parametrize("tool", ["reserve_hotel", "tools.with.dots", "odd tool"])
def test_shell_roundtrip(tool, args):
    cmd, meta = shell_command(tool, args)
    assert shell_roundtrip_ok(tool, args, cmd, meta["key_style"])
    t, got = parse_shell_command(cmd)
    assert t == tool and len(got) == len(args)


def _step(action: str, env: str = "reserve_hotel: Makes a reservation.\n  parameters:\n    hotel: {'type': 'string'}") -> Step:
    raw = {"id-interaction": 0, "id-segment": 1, "instruction": "book it", "history": "(1) Thought: earlier\n(2) Action: get_rating\n(3) Action Input: {}\nObservation: 4.2", "current_action": action, "env_info": env, "score": 0.0}
    return Step("agentdojo/travel", 0, 1, raw["instruction"], raw["history"], action, env, 0.0, raw)


ACTION = '(1) Thought: The rating is 4.2, so I will call reserve_hotel now.\n(2) Action: reserve_hotel\n(3) Action Input: {"hotel": "Le Marais Boutique", "start_day": "2025-01-11", "end_day": "2025-01-15"}'


def test_wrapper_conditions_keep_thought_and_markers():
    s = _step(ACTION)
    for cond in ("code_python", "shell_cli"):
        r = render_condition(s, cond)
        assert r.ok, r.status
        assert r.current_action.startswith("(1) Thought: The rating is 4.2")
        assert f"(2) Action: {WRAPPERS[cond]['tool']}\n(3) Action Input: " in r.current_action
        payload = json.loads(r.current_action.split("(3) Action Input: ", 1)[1])
        inner = payload[WRAPPERS[cond]["arg"]]
        if cond == "code_python":
            assert parse_python_call(inner) == ("reserve_hotel", json.loads(ACTION.split("Action Input: ")[1]))
        else:
            assert parse_shell_command(inner)[0] == "reserve_hotel"
        assert r.env_info.startswith(s.env_info) and r.env_info.endswith(WRAPPERS[cond]["schema"])
        assert r.meta["tool_in_thought"] is True
    assert render_condition(s, "original").current_action == ACTION
    assert render_condition(s, "placebo_env_wrapper").current_action == ACTION


def test_thought_removed_and_placebo():
    s = _step(ACTION)
    r = render_condition(s, "thought_removed")
    assert r.ok and r.current_action.startswith("(2) Action: reserve_hotel")
    p = render_condition(s, "placebo_whitespace")
    assert p.current_action.rstrip() == ACTION and p.current_action != ACTION
    no_thought = _step("(2) Action: reserve_hotel\n(3) Action Input: {}")
    assert render_condition(no_thought, "thought_removed").status == "no_thought_to_remove"


def test_unparseable_rows_are_excluded_not_mangled():
    s = _step("(1) Thought: hmm\nFinal Answer: done")
    for cond in PROGRAMMATIC_CONDITIONS:
        r = render_condition(s, cond)
        assert not r.ok and r.current_action == s.current_action
    s2 = _step('(1) Thought: t\n(2) Action: f\n(3) Action Input: not json at all')
    assert render_condition(s2, "code_python").status == "parse:ok_text_arguments"
    assert parse_react_step(s2.current_action).status == "ok_text_arguments"


def test_suffix_after_json_is_preserved():
    s = _step(ACTION + "\nObservation: pending")
    r = render_condition(s, "code_python")
    assert r.ok and r.current_action.endswith("\nObservation: pending")
