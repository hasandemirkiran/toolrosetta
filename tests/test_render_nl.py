import json
from pathlib import Path

from action_invariance.data import SOURCES, load_all
from action_invariance.materialize import materialize, verify_invariance
from action_invariance.render_nl import apply_review, args_match, check_sentence, compose, export_review, generate, tool_block, validate
from action_invariance.vertex import Completion

ENV = "get_rating: Get the rating.\n  parameters:\n    hotel: {'type': 'string'}\n\nreserve_hotel: Makes a reservation for a hotel.\n  parameters:\n    hotel: {'type': 'string'}\n    start_day: {'type': 'string'}\n    nights: {'type': 'integer'}"
ACTION = '(1) Thought: rating is fine, booking.\n(2) Action: reserve_hotel\n(3) Action Input: {"hotel": "Le Marais Boutique", "start_day": "2025-01-11", "nights": 4}'


class FakeProvider:
    """Generator: builds a sentence from the arguments; judge: parses the sentence back."""

    def __init__(self, bad_first=False):
        self.bad_first = bad_first
        self.calls = 0

    def complete(self, system, user, *, model, temperature=0.0, max_tokens=1024, json_schema=None):
        self.calls += 1
        if "TOOL DEFINITION:" in user:  # generation
            args = json.loads(user.split("ARGUMENTS (JSON):\n", 1)[1])
            if self.bad_first and self.calls == 1:
                return Completion(text=json.dumps({"sentence": "I will now call reserve_hotel right away."}), model=model)
            vals = ", ".join(str(v) for v in args.values())
            return Completion(text=json.dumps({"sentence": f"I will now book a stay using these details: {vals}."}), model=model)
        sentence = user.split("STATEMENT:\n", 1)[1]  # judge
        vals = sentence.split("details: ", 1)[1].rstrip(".").split(", ")
        return Completion(text=json.dumps({"tool": "reserve_hotel", "arguments": {"hotel": vals[0], "start_day": vals[1], "nights": int(vals[2])}}), model=model)


def test_tool_block_and_checks():
    assert tool_block(ENV, "reserve_hotel").startswith("reserve_hotel: Makes a reservation") and "get_rating" not in tool_block(ENV, "reserve_hotel")
    args = {"hotel": "Le Marais Boutique", "start_day": "2025-01-11", "nights": 4}
    assert check_sentence("I will now book Le Marais Boutique from 2025-01-11 for 4 nights.", "reserve_hotel", args, ACTION) == []
    fails = check_sentence("I will now call reserve_hotel for Le Marais immediately.", "reserve_hotel", args, ACTION)
    assert "tool_name_present" in fails and "forbidden_addition" in fails and any(f.startswith("value_missing") for f in fails)
    assert args_match({"nights": 4.0, "hotel": " Le  Marais "}, {"hotel": "Le Marais", "nights": 4})


def _raw(root: Path):
    for src, files in SOURCES.items():
        for f in files:
            rows = [{"id-interaction": i, "id-segment": 1, "instruction": "book", "history": "", "current_action": ACTION, "env_info": ENV, "score": 0.0} for i in range(2)]
            (root / src).mkdir(parents=True, exist_ok=True)
            (root / src / f"{f}.json").write_text(json.dumps(rows))


def test_generate_validate_review_materialize(tmp_path: Path):
    raw, rdir = tmp_path / "raw", tmp_path / "renders"
    _raw(raw)
    steps = load_all(raw)
    rfile = rdir / "natural_language.jsonl"
    rdir.mkdir()
    gen = FakeProvider(bad_first=True)
    st = generate(steps, rfile, provider=gen, model="fake", limit=4)
    assert st["generated"] == 4 and st["no_valid_candidate"] == 0
    rows = [json.loads(line) for line in rfile.read_text().splitlines()]
    assert rows[0]["n_attempts"] == 2 and rows[0]["text"].startswith("(1) Thought: rating is fine, booking.\nI will now book")
    assert compose(steps[0], rows[0]["sentence"]) == rows[0]["text"]
    for judge in ("primary", "secondary"):
        v = validate(steps, rfile, provider=FakeProvider(), model="fake-judge", judge=judge)
        assert v["ok"] == 4 and v["mismatch"] == 0
    csv_path = rdir / "manual_review.csv"
    assert export_review(rfile, csv_path)["rows"] == 4
    text = csv_path.read_text().splitlines()
    text[1] = text[1].replace(",,,\n", "")  # keep header shape
    # decide: accept row 1, repair row 2 with a valid sentence, reject row 3, leave row 4 undecided
    import csv as _csv

    recs = list(_csv.DictReader(csv_path.open()))
    recs[0]["decision"] = "accept"
    recs[1]["decision"], recs[1]["repaired_sentence"] = "repair", "I will now reserve Le Marais Boutique starting 2025-01-11 for 4 nights."
    recs[2]["decision"] = "reject"
    with csv_path.open("w", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=recs[0].keys())
        w.writeheader()
        w.writerows(recs)
    ap = apply_review(csv_path, rfile, steps)
    assert ap == {"accept": 1, "repair": 1, "reject": 1, "undecided": 1, "repair_failed_checks": 0, "accepted_total": 3}  # undecided row accepted on judge agreement
    rep = materialize("natural_language", raw, tmp_path / "conditions", rfile)
    assert rep["n_rows"] == 3 and rep["status_counts"]["missing_render"] == len(steps) - 3
    chk = verify_invariance(raw, tmp_path / "conditions", "natural_language")
    assert chk["ok"] and chk["n_action_changed"] == 3 and chk["n_env_changed"] == 0
