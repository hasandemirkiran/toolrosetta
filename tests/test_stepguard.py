import json
from pathlib import Path

import pytest

from action_invariance.data import Step
from action_invariance.stepguard_prompt import PromptContract, StepGuardInputError, normalize_history, parse_judgment, parse_stepguard, render_native_tools, render_prompt

TEMPLATE = "RISK:{risk_sources}\nREQ:{user_request}\nHIST:\n{history_text}\nCUR:\n{current_action_text}\nTOOLS:\n{tool_list_text}"
CONTRACT = PromptContract(TEMPLATE, "sources", "x", "y")
ENV = "get_rating: Get the rating.\n  parameters:\n    hotel: {'type': 'string'}\n\nreserve_hotel: Makes a reservation.\n  parameters:\n    hotel: {'type': 'string'}"
HIST = "(1) Thought: check first\n(2) Action: get_rating\n(3) Action Input: {\"hotel\": \"X\"}\nObservation: 4.2"
ACTION = '(1) Thought: fine\n(2) Action: reserve_hotel\n(3) Action Input: {"hotel": "X", "start_day": "2025-01-11"}'


def test_render_prompt_contract_shape():
    out = render_prompt(CONTRACT, instruction="book", history=HIST, current_action=ACTION, env_info=ENV)
    assert "REQ:book" in out
    assert '[Step 1] [AGENT]:\n[ACTION]: {"name": "get_rating", "arguments": {"hotel": "X"}}' in out
    assert "[Observation after Step 1] [ENVIRONMENT]: 4.2" in out
    assert '[Step 2] [AGENT]:\n[ACTION]: {"name": "reserve_hotel", "arguments": {"hotel": "X", "start_day": "2025-01-11"}}' in out
    assert "- get_rating: Get the rating." in out and "- reserve_hotel: Makes a reservation." in out
    assert "Thought" not in out.split("HIST:")[1]  # Thought-free port


def test_code_and_shell_forms_are_within_contract():
    code = '(1) Thought: fine\n(2) Action: execute_python\n(3) Action Input: {"code": "reserve_hotel(hotel=\'X\')"}'
    out = render_prompt(CONTRACT, instruction="book", history="", current_action=code, env_info=ENV + "\n\nexecute_python: Execute a Python snippet.\n  parameters:\n    code: {'type': 'string'}")
    assert '"name": "execute_python"' in out and "(no prior history)" in out and "- execute_python: Execute a Python snippet." in out


def test_prose_action_is_rejected_fail_closed():
    with pytest.raises(StepGuardInputError) as exc:
        render_prompt(CONTRACT, instruction="book", history="", current_action="(1) Thought: fine\nI will now reserve X.", env_info=ENV)
    assert "current_action_parse_failure" in str(exc.value)


def test_history_normalizer_and_parser():
    enc, diag = normalize_history(HIST)
    assert json.loads(enc) == [{"arguments": {"hotel": "X"}, "observation": "4.2", "tool": "get_rating"}]
    assert diag["discarded_thought_segments"] == 1
    assert render_native_tools(ENV) == ("- get_rating: Get the rating.\n- reserve_hotel: Makes a reservation.", 2)
    assert parse_judgment("reasoning...\n<Judgment>unsafe</Judgment>") == 1
    assert parse_judgment("<Judgment>safe</Judgment>") == 0
    assert parse_judgment("<Judgment>safe</Judgment> then <Judgment>unsafe</Judgment>") is None
    assert parse_stepguard("x <Judgment> Unsafe </Judgment>") == {"judgment": "unsafe", "composite": 1.0}
    with pytest.raises(ValueError):
        parse_stepguard("no tag")


@pytest.mark.skipif(not (Path(__file__).resolve().parents[1] / "tmp" / "StepGuard").exists(), reason="pinned upstream checkout not present")
def test_real_contract_hashes():
    from action_invariance.stepguard_prompt import PROMPT_TEMPLATE_SHA256, load_prompt_contract

    c = load_prompt_contract()
    assert c.template_sha256 == PROMPT_TEMPLATE_SHA256
    for field in ("{risk_sources}", "{user_request}", "{history_text}", "{current_action_text}", "{tool_list_text}"):
        assert field in c.template
