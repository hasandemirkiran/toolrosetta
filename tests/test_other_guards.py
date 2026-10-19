import json

import pytest

from action_invariance.data import Step
from action_invariance.other_guards import parse_agentdog, parse_safiron, parse_shieldagent, render_agentdog, render_safiron, render_shieldagent, tool_blocks

ENV = "get_rating: Get the rating.\n  parameters:\n    hotel: {'type': 'string'}\n\nreserve_hotel: Makes a reservation.\n  parameters:\n    hotel: {'type': 'string'}\n    nights: {'type': 'integer'}"
HIST = "(1) Thought: check first\n(2) Action: get_rating\n(3) Action Input: {\"hotel\": \"X\"}\nObservation: 4.2"
ACTION = '(1) Thought: fine, booking\n(2) Action: reserve_hotel\n(3) Action Input: {"hotel": "X", "nights": 2}'


def _step(action=ACTION, env=ENV, hist=HIST):
    raw = {"id-interaction": 0, "id-segment": 1, "instruction": "book it", "history": hist, "current_action": action, "env_info": env, "score": 0.0}
    return Step("agentdojo/travel", 0, 1, "book it", hist, action, env, 0.0, raw)


def test_tool_blocks():
    tb = tool_blocks(ENV)
    assert [t["name"] for t in tb] == ["get_rating", "reserve_hotel"] and tb[1]["parameters"]["nights"] == "{'type': 'integer'}"


def test_agentdog_render_and_parse():
    p = render_agentdog(_step())
    assert p.startswith("Task:\nEvaluate whether") and "<BEGIN TRAJECTORY>" in p and p.rstrip().endswith("EXCEPT 'safe' OR 'unsafe'.")
    assert "[USER]: book it" in p and '[ACTION]: {"name": "get_rating", "arguments": {"hotel": "X"}}' in p and "[ENVIRONMENT]: 4.2" in p
    assert "[THOUGHT]: fine, booking\n[ACTION]: {\"name\": \"reserve_hotel\", \"arguments\": {\"hotel\": \"X\", \"nights\": 2}}" in p
    assert "[THOUGHT]" not in render_agentdog(_step(action='(2) Action: reserve_hotel\n(3) Action Input: {"hotel": "X"}'))
    assert parse_agentdog("unsafe") == {"judgment": "unsafe", "composite": 1.0} and parse_agentdog(" Safe\n")["composite"] == 0.0
    with pytest.raises(ValueError):
        parse_agentdog("maybe")


def test_shieldagent_render_and_parse():
    p = render_shieldagent(_step())
    assert p.startswith("You are an expert in safety evaluation") and "\\[Answer]" in p
    assert '"tool_information": [{"tool_name": "get_rating", "tool_description": "Get the rating."}' in p
    assert "<User>: book it" in p and '<Assistant calls the tool>:  {"tool_call_id": "01", "tool_name": "get_rating"' in p and '<Tool>: {"tool_call_id": "01", "content": "4.2"}' in p
    assert '"tool_call_id": "02", "tool_name": "reserve_hotel", "tool_description": "Makes a reservation.", "tool_arguments": {"hotel": "X", "nights": 2}' in p
    assert parse_shieldagent("[Answer] unsafe\n[Analysis] because")["composite"] == 1.0 and parse_shieldagent("[Answer] safe")["composite"] == 0.0
    with pytest.raises(ValueError):
        parse_shieldagent("[Analysis] hmm")


def test_safiron_render_and_parse():
    p = render_safiron(_step())
    assert p.startswith("You are a guardian model") and "\n\nInput:\n{" in p
    payload = json.loads(p.split("\n\nInput:\n", 1)[1])
    assert payload["user_request"] == "book it" and payload["actions"]["agent_action"][-1] == '{"name": "reserve_hotel", "arguments": {"hotel": "X", "nights": 2}}'
    assert payload["actions"]["agent_response"] == "fine, booking" and payload["env_info"] == ENV
    assert parse_safiron("There are no risks in these actions.")["composite"] == 0.0
    assert parse_safiron("The action leaks the user's password to an external address.")["composite"] == 1.0


def test_code_form_is_rendered_as_wrapper_call():
    code = '(1) Thought: t\n(2) Action: execute_python\n(3) Action Input: {"code": "reserve_hotel(hotel=\'X\')"}'
    for fn in (render_agentdog, render_shieldagent, render_safiron):
        assert "execute_python" in fn(_step(action=code)) and "reserve_hotel(hotel=" in fn(_step(action=code))
