# ToolRosetta: Surface-Form Invariance of Tool-Call Guards

**Hasan Demirkiran and Jens Ernstberger** ([Kontext](https://kontext.security))

arXiv version to follow (submitted) · Accepted at the NeurIPS 2026 Workshop on Agents in the Wild: Safety, Security, and Beyond (AIWILD).

Code, frozen protocol, renderers and round-trip parser, guard adapters, aggregate results and all per-row verdicts for the paper. Sibling repositories: [linguaguardbench](https://github.com/hasandemirkiran/linguaguardbench) and [shortcut-sensitivity-agent-safety-evaluations](https://github.com/hasandemirkiran/shortcut-sensitivity-agent-safety-evaluations).

## Per-row verdicts

`results/raw/<guard>/<condition>/r<k>.jsonl.gz` holds one JSON row per guard call (84 files, 228,901 rows): `sample_id`, `trajectory_id`, `segment_id`, `instruction_id`, `domain`, `label`, `condition`, `guard`, `model`, `prompt_version`, `prompt_sha`, `repeat`, `temperature`, `instruction`, `assessment`, `composite`, `block`, `usage`, `latency_s`, `raw_text`, `error`. Rows with a non-null `error` are failed calls that the analysis skips. The analysis code reads uncompressed files: run `gunzip -rk results/raw` first, then follow the `analyze` step below. TS-Bench trajectories are not redistributed; they are fetched from the TS-Bench release at the pinned commit in `data/manifest.json`.

## How to cite

```bibtex
@article{demirkiran2026toolrosetta,
  title   = {ToolRosetta: Surface-Form Invariance of Tool-Call Guards},
  author  = {Demirkiran, Hasan and Ernstberger, Jens},
  journal = {arXiv preprint},
  year    = {2026},
  note    = {NeurIPS 2026 Workshop on Agents in the Wild (AIWILD)}
}
```

## License

Code, scripts, documentation and derived aggregate results in this repository are released under the MIT License (see `LICENSE`). Upstream benchmarks, model weights and provider responses remain under their own terms.

---

ToolRosetta: the same tool call written as JSON, Python, shell, and prose, with labels held fixed, to test whether a step-level guard's verdict depends on the surface form.

Does a step-level agent guard's verdict depend on how the candidate tool call is *written*?

We take the TS-Bench evaluation set (7,182 labelled tool-invocation steps from AgentDojo-Traj,
AgentHarm-Traj and ASB-Traj), rewrite **only the candidate action** into the forms real agents
emit, keep the instruction, history and label byte-identical, and re-run guards. Because the
action's meaning and its label never change, any change in a guard's decision is attributable to
the surface form of the action alone.

| condition | what the guard sees for the same action |
|---|---|
| original | `(1) Thought: … (2) Action: reserve_hotel (3) Action Input: {"hotel": "Le Marais", …}` |
| thought_removed | the same without the Thought |
| code_python | `Action: execute_python`, `Action Input: {"code": "reserve_hotel(hotel='Le Marais', …)"}` |
| shell_cli | `Action: execute_bash`, `Action Input: {"command": "reserve_hotel --hotel 'Le Marais' …"}` |
| natural_language | `I will now reserve Le Marais for 2025-01-11 to 2025-01-15.` (model-written, validated) |
| placebo_whitespace, placebo_env_wrapper | controls for tokenization noise and for the env_info change |

The two code conditions are produced by code, not by a model, and every rendered row is parsed
back and compared with the original tool and arguments before it is accepted
(`src/action_invariance/render.py`). Labels are therefore inherited, never re-annotated.

Sibling of [linguaguardbench](https://github.com/hasandemirkiran/linguaguardbench) (perturbs the *instruction*, the trusted
channel) and the shortcut audit ([shortcut-sensitivity-agent-safety-evaluations](https://github.com/hasandemirkiran/shortcut-sensitivity-agent-safety-evaluations)) (perturbs the *untrusted*
channel: injection markers and tool names). This project perturbs the *action* channel, so between
the three studies every input a guard reads has been intervened on with labels held fixed.

## Research questions

1. **Invariance.** What fraction of per-step verdicts flip when the same call is written as code,
   as a shell command, or as prose, over and above the whitespace placebo?
2. **Direction.** Do flips add misses (unsafe actions allowed) or false positives (benign actions
   blocked)? ΔTPR / ΔFPR with trajectory-clustered bootstrap intervals, per source.
3. **Ranking.** Does the guard leaderboard reorder across renderings? (Kendall τ vs. original.)
4. **Mechanism.** Is the flip rate lower when the Thought restates the tool name (`leaky` stratum),
   and does `thought_removed` move verdicts on its own?
5. **Mitigation.** Does parsing the code or shell form back into the tidy form before the guard
   sees it (resolve-then-judge) recover the original error rates?

## Pipeline

```
fetch ──► inventory ──► materialize ──► run ──► analyze
 pinned    parse all     TS-Bench      guards    flips, ΔFPR/ΔTPR,
 TS-Bench  actions,      schema, only  (Agent    bootstrap CIs,
 files     cohort,       action/env    Platform) Kendall τ, strata
           covariates    changed         └─ budget brake + cost ledger
```

```bash
make setup                      # venv + deps
make fetch inventory            # copies ToolSafe-Lab's pinned files if present, else downloads
make materialize                # writes data/conditions/<cond>/<source>/<file>.json + verifies invariance
.venv/bin/action-invariance show "agentdojo/travel:0:1"     # eyeball one step under every rendering
export GOOGLE_CLOUD_PROJECT=GCP-PROJECT
make verify-models              # pings every model id in configs/models.yaml
make cost                       # design estimate on the paired cohort
make run                        # guards × conditions, one run each, on the paired cohort
make analyze                    # results/summary.md + summary.json
```

Offline smoke test (no cloud): `make test`, or
```bash
.venv/bin/action-invariance run --guards gemini-3.8-flash --conditions original code_python --limit 50 --mock
.venv/bin/action-invariance analyze --n-boot 100 && make clean-mock
```

## Layout

```
PROTOCOL.md                 pre-registration draft; freeze before the first non-mock guard run
docs/RENDERING_DESIGN.md    what "same action" means, how each rendering is built and verified
configs/conditions.yaml     the seven conditions
configs/models.yaml         guards (managed + self-deployed), generator/validator, pricing, budget
data/manifest.json          pinned TS-Bench files (commit + sha256)
data/inventory/             step covariates, renderability, paired cohort            [generated]
data/conditions/<cond>/     TS-Bench-schema files with only the allowed fields changed [generated]
data/renders/               model-written renderings + validation (natural_language)  [generated]
results/raw/<guard>/<cond>/ one row per step per repeat, full assessment              [generated]
results/summary.{json,md}   analysis report                                           [generated]
src/action_invariance/      data · react · render · inventory · materialize · run_guard · cost · analyze · cli
                            (run_guard, vertex, guard_prompt, ts_guard_prompt, cost, analyze are vendored
                             from guard-invariance so results are directly comparable)
```
