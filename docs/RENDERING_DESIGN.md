# Rendering design: what "the same action" means

This is the part of the study a reviewer will attack, so every rendering is deterministic where
it can be, and machine-verified where it cannot be deterministic.

## The unit

A TS-Bench step's `current_action` is ReAct text:

```
(1) Thought: <rationale>
(2) Action: <tool_name>
(3) Action Input: <JSON arguments>[ <suffix>]
```

`parse_react_step` (vendored from ToolSafe-Lab) splits it into preamble, Thought, tool name,
argument text, parsed JSON, and suffix. Only rows with status `ok_json_object` (94 % of the
evaluation set in ToolSafe-Lab's audit) are eligible for the code and shell conditions; the
others are excluded from those conditions and counted, never guessed at.

## Programmatic conditions

**thought_removed.** `current_action := text from the Action marker onward`. Preamble and Thought
are dropped; marker numbering is kept as released ("(2) Action: …"). Rows with no Thought and no
preamble are excluded (the condition would be identical to `original`).

**code_python.** Thought and preamble kept verbatim. The Action / Action Input block becomes

```
(2) Action: execute_python
(3) Action Input: {"code": "reserve_hotel(hotel='Le Marais Boutique', start_day='2025-01-11')"}
```

* keyword-only call; values are `repr()` of the JSON-decoded object (valid Python literals);
* if any key is not a Python identifier or is a keyword, the call uses `tool(**{...})`;
* if the tool name is not an identifier, the callee is `tools["<name>"]`;
* the code is parsed with `ast` and the recovered (tool, arguments) must equal the original
  object exactly, or the row is excluded (`roundtrip_failed`).

**shell_cli.** Same, with

```
(2) Action: execute_bash
(3) Action Input: {"command": "reserve_hotel --hotel 'Le Marais Boutique' --start-day 2025-01-11"}
```

* `--kebab-case` options when every key matches `[A-Za-z0-9_]+`, otherwise verbatim keys;
* strings are `shlex.quote`d; booleans → `true`/`false`; `null`; numbers as `repr`; nested
  values as compact JSON, quoted;
* parsed back with `shlex` and compared on canonical string values. Shell has no types, so this
  rendering preserves values exactly but not their types; stated as a limitation.

Both wrapper conditions append the wrapper tool's schema to `env_info` (one fixed string, see
`render.WRAPPERS`) so the action is a *declared* tool, not an unknown one. `placebo_env_wrapper`
makes the same `env_info` change with the action untouched, so the two effects can be separated.

Any suffix after the JSON (rare) is kept after the new block. Everything before the Action
marker is byte-identical to the original.

## Model-written condition: natural_language (not yet generated)

The Action / Action Input block is replaced by one first-person sentence, e.g.
`I will now make a reservation at Le Marais Boutique from 2025-01-11 to 2025-01-15.`

Generation: Gemini 3.8 Flash, T = 0.3, three candidates, first valid selected (never the most
different). Mechanical checks per candidate: every string-valued argument occurs verbatim in the
sentence (numbers and dates as written); the tool's own name does *not* occur (otherwise it is
not prose); no added authorization, urgency or permission phrase; length ≤ 3× the original
block. Validation: Claude Sonnet 5 must recover the exact (tool, arguments) from the sentence
plus `env_info` alone; disagreements go to a second judge (Gemini 3.1 Pro) and then to manual
review; every accepted row is read by a human before freeze. Rows rejected are excluded for all
guards (paired design).

## Secondary: rendering the history too

`history` contains earlier Action / Action Input blocks in the same format. A full-transcript
variant renders those blocks the same way as the candidate action. It answers a different
question (a consistently code-acting agent) than the primary conditions (an agent that switches
form on this step, which is what an injection or a model quirk produces), so it is kept out of
the primary analysis.

## What we deliberately do not do

* No LLM in the loop for code or shell renderings.
* No re-execution of trajectories, no re-labelling.
* No "adversarial" renderings (obfuscation, encoding, splitting). The claim is about the default
  forms coding agents use, not about evasion; evasion is a different study.
