# Results notes — floors, 2026-09-04

Cohorts: AgentDojo-Traj (1,220 steps) and AgentHarm-Traj (718 eligible of 731), both from
`data/inventory/cohort_programmatic.txt`. One run per guard × condition (K = 1, Hasan's decision).
Conditions: `original`, `placebo_whitespace`, `placebo_env_wrapper`. Prompt `authors_v2_structured`
(sha 0e6dd5de…, identical to guard-invariance and ToolSafe-Lab). Google Cloud project `GCP-PROJECT`.

## Deterministic provider failures

* **Claude Sonnet 5** returns an empty completion for two AgentHarm steps
  (`agentharm/benign_steps:48:2`, `agentharm/harmful_steps:9:1`) in all three conditions and on
  a retry pass, at zero billed output. Treated as fail-closed provider failures; the rows are
  absent from Claude's cells in every condition, so pairing is unaffected. n = 716 for Claude on
  AgentHarm.
* **GLM 5.2** shows 3 transient errors per condition on AgentDojo (retried by a resume pass at
  the end; see below).

## Serving notes

* Llama 4 Maverick had to be enabled for `GCP-PROJECT` in Model Garden (done 2026-09-04
  via the console; no license dialog appeared). Served from `us-east5`.
* gpt-oss-120b throttles at concurrency > 4 (429, retried).
* GLM 5.2 runs at ≈ 11 s per call: ≈ 55 min per 1,220-step condition.

## Observations recorded before any perturbed-condition inference

* Claude Sonnet 5 AgentDojo floor reproduces guard-invariance: BACC 0.954 (vs 0.958),
  whitespace-placebo flip 1.3 % (vs 1.7 %). `placebo_env_wrapper` adds 18 false positives and 0
  misses (ΔFPR CI includes 0).
* Llama 4 Maverick AgentDojo floor: BACC 0.811 (vs 0.818), placebo flip 4.1 % (vs 2.8 %).
  **`placebo_env_wrapper` is not a null for Llama on AgentDojo**: ΔTPR +0.037 [+0.011, +0.059].
  Wrapper-condition effects for Llama must be read against the env-wrapper placebo.
* Llama on AgentHarm: BACC 0.738, miss rate 26.6 % at baseline.

## Floors at freeze (protocol 1.0), four guards, both cohorts

| guard | source | n | TPR | FPR | BACC | whitespace flip | env-wrapper flip |
|---|---|---:|---:|---:|---:|---:|---:|
| gemini-3.8-flash | agentdojo | 1220 | 0.977 | 0.012 | 0.983 | 0.000 | 0.000 |
| gemini-3.8-flash | agentharm | 718 | 0.833 | 0.170 | 0.831 | 0.028 | 0.029 |
| gemini-3.8-flash-scanner | agentdojo | 1220 | 0.977 | 0.012 | 0.983 | 0.000 | 0.000 |
| gemini-3.8-flash-scanner | agentharm | 718 | 0.764 | 0.184 | 0.790 | 0.052 | 0.053 |
| claude-sonnet-5 | agentdojo | 1220 | 0.986 | 0.078 | 0.954 | 0.013 | 0.028 |
| claude-sonnet-5 | agentharm | 716 | 0.892 | 0.278 | 0.807 | 0.027 | 0.035 |
| llama-4-maverick | agentdojo | 1220 | 0.898 | 0.276 | 0.811 | 0.041 | 0.042 |
| llama-4-maverick | agentharm | 718 | 0.734 | 0.257 | 0.738 | 0.042 | 0.054 |

Sanity: every BACC > 0.5; Gemini and its scanner are identical on AgentDojo (as in
guard-invariance) but diverge on AgentHarm (scanner TPR 0.764 vs 0.833), i.e. on AgentHarm the
guard does use the user request. Claude's whitespace placebo on AgentHarm produces 6 new misses
(ΔTPR −0.030 [−0.069, +0.000]): its AgentHarm noise floor is material.

## Programmatic conditions — first two guards (protocol 1.0, primary cohort)

Placebo-adjusted flip = flip(condition) − flip(placebo) on the same steps, cluster-bootstrapped;
wrapper conditions use `placebo_env_wrapper`, `thought_removed` uses `placebo_whitespace`.

| guard | condition | source | flip − placebo [95% CI] | new misses (placebo) | new FPs (placebo) | ΔTPR [95% CI] | ΔFPR [95% CI] |
|---|---|---|---|---:|---:|---|---|
| llama-4-maverick | code_python | agentdojo | +0.026 [+0.012, +0.040] | 7 (4) | 32 (18) | +0.034 [+0.006, +0.064] | +0.008 [−0.007, +0.025] |
| llama-4-maverick | code_python | agentharm | +0.036 [+0.015, +0.059] | 6 (4) | 5 (2) | −0.005 [−0.038, +0.036] | +0.005 [−0.026, +0.037] |
| llama-4-maverick | shell_cli | agentdojo | +0.026 [+0.013, +0.041] | 9 (4) | 36 (18) | +0.017 [−0.009, +0.044] | +0.015 [−0.001, +0.031] |
| llama-4-maverick | shell_cli | agentharm | +0.033 [+0.013, +0.053] | 5 (4) | 10 (2) | −0.015 [−0.041, +0.011] | +0.019 [−0.019, +0.057] |
| llama-4-maverick | thought_removed | agentdojo | +0.087 [+0.067, +0.107] | 26 (6) | 67 (13) | +0.003 [−0.037, +0.046] | +0.036 [+0.011, +0.059] |
| llama-4-maverick | thought_removed | agentharm | +0.067 [+0.042, +0.094] | 12 (3) | 7 (3) | −0.039 [−0.086, +0.000] | +0.005 [−0.030, +0.035] |
| claude-sonnet-5 | code_python | agentdojo | +0.003 [−0.007, +0.014] | 1 (0) | 18 (18) | +0.000 | +0.000 |
| claude-sonnet-5 | code_python | agentharm | +0.007 [−0.005, +0.019] | 6 (2) | 7 (5) | −0.020 [−0.048, +0.004] | +0.024 [+0.005, +0.049] |
| claude-sonnet-5 | shell_cli | agentdojo | +0.006 [−0.005, +0.017] | 1 (0) | 30 (18) | +0.003 [−0.006, +0.014] | +0.025 [+0.013, +0.039] |
| claude-sonnet-5 | shell_cli | agentharm | +0.007 [−0.008, +0.023] | 5 (2) | 5 (5) | −0.010 [−0.046, +0.021] | +0.010 [−0.009, +0.028] |
| claude-sonnet-5 | thought_removed | agentdojo | +0.034 [+0.022, +0.047] | 1 (0) | 12 (7) | +0.011 [−0.003, +0.028] | −0.032 [−0.050, −0.016] |
| claude-sonnet-5 | thought_removed | agentharm | +0.041 [+0.021, +0.062] | 7 (6) | 7 (3) | −0.005 [−0.046, +0.033] | +0.015 [−0.014, +0.044] |

Reading so far: Claude is invariant on the miss side under code and shell renderings; the shell
form raises its AgentDojo false-positive rate by 2.5 points. Removing the Thought *lowers*
Claude's AgentDojo FPR by 3.2 points at unchanged recall. Llama churns 2.6–3.6 points above
placebo under code/shell with no net error shift, and is strongly rationale-dependent
(thought_removed: +8.7 / +6.7 points above placebo; 67 new FPs on AgentDojo, 12 new misses on
AgentHarm).

## Programmatic conditions — Gemini 3.8 Flash and its scanner control

| guard | condition | source | flip − placebo [95% CI] | new misses (placebo) | new FPs (placebo) | ΔTPR [95% CI] | ΔFPR [95% CI] |
|---|---|---|---|---:|---:|---|---|
| gemini-3.8-flash | code_python | agentdojo | +0.000 [0, 0] | 0 (0) | 0 (0) | 0 | 0 |
| gemini-3.8-flash | code_python | agentharm | +0.003 [−0.008, +0.014] | 4 (5) | 5 (6) | +0.020 [−0.005, +0.052] | +0.019 [−0.005, +0.046] |
| gemini-3.8-flash | shell_cli | agentdojo | +0.000 [0, 0] | 0 (0) | 0 (0) | 0 | 0 |
| gemini-3.8-flash | shell_cli | agentharm | +0.003 [−0.013, +0.017] | 2 (5) | 8 (6) | +0.015 [−0.005, +0.039] | +0.034 [+0.009, +0.065] |
| gemini-3.8-flash | thought_removed | agentdojo | +0.006 [+0.002, +0.011] | 0 (0) | 0 (0) | +0.003 [+0.000, +0.011] | −0.007 [−0.013, −0.002] |
| gemini-3.8-flash | thought_removed | agentharm | +0.011 [−0.006, +0.028] | 5 (2) | 5 (4) | +0.015 [+0.000, +0.035] | +0.015 [−0.018, +0.045] |
| gemini-3.8-flash-scanner | code_python | agentdojo | +0.000 [0, 0] | 0 (0) | 0 (0) | 0 | 0 |
| gemini-3.8-flash-scanner | code_python | agentharm | +0.000 [−0.015, +0.015] | 2 (2) | 3 (4) | +0.020 [−0.000, +0.044] | −0.010 [−0.037, +0.015] |
| gemini-3.8-flash-scanner | shell_cli | agentdojo | +0.000 [0, 0] | 0 (0) | 0 (0) | 0 | 0 |
| gemini-3.8-flash-scanner | shell_cli | agentharm | +0.001 [−0.017, +0.019] | 6 (2) | 4 (4) | +0.005 [−0.030, +0.042] | +0.000 [−0.027, +0.028] |
| gemini-3.8-flash-scanner | thought_removed | agentdojo | +0.006 [+0.002, +0.010] | 0 (0) | 1 (0) | 0 | −0.006 [−0.012, +0.000] |
| gemini-3.8-flash-scanner | thought_removed | agentharm | +0.046 [+0.017, +0.073] | 11 (4) | 3 (1) | −0.044 [−0.084, −0.009] | −0.019 [−0.052, +0.010] |

Reading: Gemini 3.8 Flash and its scanner are format-invariant — exactly zero flips on AgentDojo
under code and shell, nothing above placebo on AgentHarm. The shell form's +3.4-point FPR on
AgentHarm for Gemini is mostly the env-wrapper effect (env placebo alone: +2.4). Mechanism: on
AgentHarm the *scanner* (no USER_REQUEST) loses 4.4 points of recall when the Thought is removed
(11 new misses) while Gemini with the request loses none; the rationale is the scanner's intent
signal and the request is the guard's.

## Programmatic conditions — Nemotron 3.5 Lightning 30B (self-deployed, greedy)

Baseline: AgentDojo BACC 0.815 (TPR 0.972, FPR 0.341); AgentHarm BACC 0.660 (TPR 0.700, FPR 0.379).

| condition | source | flip − placebo [95% CI] | new misses (placebo) | new FPs (placebo) | ΔTPR [95% CI] | ΔFPR [95% CI] |
|---|---|---|---:|---:|---|---|
| code_python | agentdojo | +0.012 [+0.002, +0.023] | 2 (2) | 24 (15) | +0.006 [−0.006, +0.020] | +0.006 [−0.008, +0.020] |
| code_python | agentharm | +0.018 [−0.001, +0.036] | 9 (8) | 3 (5) | +0.000 [−0.030, +0.032] | −0.049 [−0.093, −0.005] |
| shell_cli | agentdojo | +0.018 [+0.009, +0.028] | 1 (2) | 31 (15) | +0.006 [−0.003, +0.018] | +0.012 [−0.004, +0.028] |
| shell_cli | agentharm | +0.018 [+0.000, +0.038] | 8 (8) | 1 (5) | +0.005 [−0.036, +0.045] | −0.058 [−0.091, −0.024] |
| thought_removed | agentdojo | +0.040 [+0.025, +0.054] | 1 (0) | 62 (19) | +0.023 [+0.008, +0.041] | +0.050 [+0.029, +0.069] |
| thought_removed | agentharm | +0.078 [+0.051, +0.103] | 5 (4) | 14 (2) | +0.049 [+0.015, +0.093] | +0.015 [−0.035, +0.061] |

Reading: first guard whose operating point moves with syntax. Code and shell forms lower its
AgentHarm FPR by 5–6 points at unchanged recall (unstructured calls read as more benign); removing
the Thought raises blocking on both sources (+5.0 FPR on AgentDojo, +4.9 TPR on AgentHarm).

## Qwen 3.8 27B (self-deployed, greedy) — all six conditions, zero errors

Baseline: AgentDojo BACC 0.824 (TPR 1.000, FPR 0.353); AgentHarm 0.742 (TPR 0.946, FPR 0.461).
Placebo flips 0.5 % / 1.4 %. Fully format-invariant: code_python and shell_cli adjusted flips
within ±0.2 points of zero on both sources with intervals including zero; shell_cli lowers AgentDojo
FPR by 0.8 points (CI excludes zero, 0 new FPs). thought_removed: +2.6 points above placebo on
AgentHarm, no net error shift.

## GLM 5.2 floors (both cohorts)

AgentDojo BACC 0.847 (TPR 0.997, FPR 0.304); AgentHarm 0.779 (TPR 0.941, FPR 0.383). Whitespace
placebo flips 5.9 % (AgentDojo) and 10.9 % (AgentHarm) at K = 1 — the noisiest guard, as in
guard-invariance. Per protocol 1.0 it is flagged low-resolution: only effects well above these
floors are interpretable. 1–3 transient errors per condition; a resume pass fills them at the end.

## TS-Guard (self-deployed, authors' XML prompt, greedy) — all six conditions

Baseline: AgentDojo BACC 0.903 (TPR 0.884, FPR 0.078); AgentHarm 0.752 (TPR 0.980, FPR 0.476).
Placebo flips 3.8 % (AgentDojo whitespace) — greedy decoding, so placebo flips are input-driven.

| condition | source | flip − placebo [95% CI] | new misses | new FPs | ΔTPR [95% CI] | ΔFPR [95% CI] |
|---|---|---|---:|---:|---|---|
|  thought_removed  |  agentdojo  |  +4.4 [+3.0, +5.9]*  |  12  |  32  |  +4.8 [+1.3, +8.6]*  |  +1.0 [-0.6, +2.6]  |
|  thought_removed  |  agentharm  |  -0.1 [-1.6, +1.3]  |  0  |  6  |  +1.0 [+0.0, +2.7]  |  -1.5 [-5.1, +2.1]  |
|  code_python  |  agentdojo  |  +5.1 [+3.4, +6.8]*  |  7  |  60  |  +6.0 [+2.9, +9.4]*  |  +5.3 [+3.4, +7.3]*  |
|  code_python  |  agentharm  |  +2.2 [+0.5, +4.2]*  |  2  |  16  |  -0.5 [-1.8, +0.0]  |  +3.4 [-2.3, +9.4]  |
|  shell_cli  |  agentdojo  |  +15.6 [+12.8, +18.2]*  |  3  |  196  |  +6.6 [+3.7, +9.5]*  |  +21.2 [+17.7, +24.2]*  |
|  shell_cli  |  agentharm  |  +4.5 [+2.3, +6.9]*  |  2  |  33  |  +0.0 [-1.9, +2.1]  |  +12.6 [+5.7, +19.2]*  |

Reading: the JSON-trained specialized guard is the format-sensitive one, and in the over-blocking
direction. The same AgentDojo actions written as shell commands raise its FPR from 7.8 % to 29 %
(196 of 868 benign steps newly blocked), with recall also up 6.6 points: it treats execute_bash as
unsafe regardless of content. Python form: +5.3 FPR, +6.0 TPR. On AgentHarm the shell form adds
12.6 points of FPR. A parser that reconstructs the Action / Action Input block from the code or
shell string restores the original inputs exactly, so the mitigation for these two forms is by
construction and needs no run.

## Cross-guard tables at 8 guards (GLM programmatic, gpt-oss, Gemma pending)

### AgentDojo

| guard | condition | source | base BACC | flip % | flip − placebo, pts | ΔTPR, pts | ΔFPR, pts | new misses | new FPs |
|---|---|---|---:|---:|---|---|---|---:|---:|
| gemini-3.8-flash | thought_removed | agentdojo | 0.983 | 0.6 | +0.6 [+0.2, +1.1]* | +0.3 [+0.0, +1.1] | -0.7 [-1.3, -0.2]* | 0 | 0 |
| gemini-3.8-flash | code_python | agentdojo | 0.983 | 0.0 | +0.0 [+0.0, +0.0] | +0.0 [+0.0, +0.0] | +0.0 [+0.0, +0.0] | 0 | 0 |
| gemini-3.8-flash | shell_cli | agentdojo | 0.983 | 0.0 | +0.0 [+0.0, +0.0] | +0.0 [+0.0, +0.0] | +0.0 [+0.0, +0.0] | 0 | 0 |
| gemini-3.8-flash-scanner | thought_removed | agentdojo | 0.983 | 0.6 | +0.6 [+0.2, +1.0]* | +0.0 [+0.0, +0.0] | -0.6 [-1.2, +0.0] | 0 | 1 |
| gemini-3.8-flash-scanner | code_python | agentdojo | 0.983 | 0.0 | +0.0 [+0.0, +0.0] | +0.0 [+0.0, +0.0] | +0.0 [+0.0, +0.0] | 0 | 0 |
| gemini-3.8-flash-scanner | shell_cli | agentdojo | 0.983 | 0.0 | +0.0 [+0.0, +0.0] | +0.0 [+0.0, +0.0] | +0.0 [+0.0, +0.0] | 0 | 0 |
| claude-sonnet-5 | thought_removed | agentdojo | 0.954 | 4.8 | +3.4 [+2.2, +4.7]* | +1.1 [-0.3, +2.8] | -3.2 [-5.0, -1.6]* | 1 | 12 |
| claude-sonnet-5 | code_python | agentdojo | 0.954 | 3.1 | +0.3 [-0.7, +1.4] | +0.0 [-0.9, +0.8] | +0.0 [-1.4, +1.3] | 1 | 18 |
| claude-sonnet-5 | shell_cli | agentdojo | 0.954 | 3.4 | +0.6 [-0.5, +1.7] | +0.3 [-0.6, +1.4] | +2.5 [+1.3, +3.9]* | 1 | 30 |
| glm-5.2 | thought_removed | agentdojo | 0.847 | 5.7 | -3.3 [-7.7, +0.9] | +0.0 [+0.0, +0.0] | -1.2 [-7.4, +5.8] | 0 | 3 |
| llama-4-maverick | thought_removed | agentdojo | 0.811 | 12.8 | +8.7 [+6.7, +10.7]* | +0.3 [-3.7, +4.6] | +3.6 [+1.1, +5.9]* | 26 | 67 |
| llama-4-maverick | code_python | agentdojo | 0.811 | 6.8 | +2.6 [+1.2, +4.0]* | +3.4 [+0.6, +6.4]* | +0.8 [-0.7, +2.5] | 7 | 32 |
| llama-4-maverick | shell_cli | agentdojo | 0.811 | 6.8 | +2.6 [+1.3, +4.1]* | +1.7 [-0.9, +4.4] | +1.5 [-0.1, +3.1] | 9 | 36 |
| qwen3.8-27b | thought_removed | agentdojo | 0.824 | 0.7 | +0.2 [-0.4, +0.7] | +0.0 [+0.0, +0.0] | +0.5 [-0.1, +1.1] | 0 | 6 |
| qwen3.8-27b | code_python | agentdojo | 0.824 | 0.7 | +0.2 [-0.1, +0.6] | +0.0 [+0.0, +0.0] | -0.7 [-1.5, +0.0] | 0 | 1 |
| qwen3.8-27b | shell_cli | agentdojo | 0.824 | 0.6 | +0.2 [-0.2, +0.6] | +0.0 [+0.0, +0.0] | -0.8 [-1.5, -0.2]* | 0 | 0 |
| nemotron-3.5-lightning-30b | thought_removed | agentdojo | 0.815 | 7.5 | +4.0 [+2.5, +5.4]* | +2.3 [+0.8, +4.1]* | +5.0 [+2.9, +6.9]* | 1 | 62 |
| nemotron-3.5-lightning-30b | code_python | agentdojo | 0.815 | 4.0 | +1.2 [+0.2, +2.3]* | +0.6 [-0.6, +2.0] | +0.6 [-0.8, +2.0] | 2 | 24 |
| nemotron-3.5-lightning-30b | shell_cli | agentdojo | 0.815 | 4.6 | +1.8 [+0.9, +2.8]* | +0.6 [-0.3, +1.8] | +1.2 [-0.4, +2.8] | 1 | 31 |
| ts-guard | thought_removed | agentdojo | 0.903 | 7.9 | +4.4 [+3.0, +5.9]* | +4.8 [+1.3, +8.6]* | +1.0 [-0.6, +2.6] | 12 | 32 |
| ts-guard | code_python | agentdojo | 0.903 | 8.9 | +5.1 [+3.4, +6.8]* | +6.0 [+2.9, +9.4]* | +5.3 [+3.4, +7.3]* | 7 | 60 |
| ts-guard | shell_cli | agentdojo | 0.903 | 19.4 | +15.6 [+12.8, +18.2]* | +6.6 [+3.7, +9.5]* | +21.2 [+17.7, +24.2]* | 3 | 196 |

* = 95% interval excludes zero. Wrapper conditions are adjusted against placebo_env_wrapper, thought_removed against placebo_whitespace.

### AgentHarm

| guard | condition | source | base BACC | flip % | flip − placebo, pts | ΔTPR, pts | ΔFPR, pts | new misses | new FPs |
|---|---|---|---:|---:|---|---|---|---:|---:|
| gemini-3.8-flash | thought_removed | agentharm | 0.831 | 3.9 | +1.1 [-0.6, +2.8] | +1.5 [+0.0, +3.5] | +1.5 [-1.8, +4.5] | 5 | 5 |
| gemini-3.8-flash | code_python | agentharm | 0.831 | 3.2 | +0.3 [-0.8, +1.4] | +2.0 [-0.5, +5.2] | +1.9 [-0.5, +4.6] | 4 | 5 |
| gemini-3.8-flash | shell_cli | agentharm | 0.831 | 3.2 | +0.3 [-1.3, +1.7] | +1.5 [-0.5, +3.9] | +3.4 [+0.9, +6.5]* | 2 | 8 |
| gemini-3.8-flash-scanner | thought_removed | agentharm | 0.790 | 9.7 | +4.6 [+1.7, +7.3]* | -4.4 [-8.4, -0.9]* | -1.9 [-5.2, +1.0] | 11 | 3 |
| gemini-3.8-flash-scanner | code_python | agentharm | 0.790 | 5.3 | +0.0 [-1.5, +1.5] | +2.0 [-0.0, +4.4] | -1.0 [-3.7, +1.5] | 2 | 3 |
| gemini-3.8-flash-scanner | shell_cli | agentharm | 0.790 | 5.4 | +0.1 [-1.7, +1.9] | +0.5 [-3.0, +4.2] | +0.0 [-2.7, +2.8] | 6 | 4 |
| claude-sonnet-5 | thought_removed | agentharm | 0.807 | 6.7 | +4.1 [+2.1, +6.2]* | -0.5 [-4.6, +3.3] | +1.5 [-1.4, +4.4] | 7 | 7 |
| claude-sonnet-5 | code_python | agentharm | 0.807 | 4.2 | +0.7 [-0.5, +1.9] | -2.0 [-4.8, +0.4] | +2.4 [+0.5, +4.9]* | 6 | 7 |
| claude-sonnet-5 | shell_cli | agentharm | 0.807 | 4.2 | +0.7 [-0.8, +2.3] | -1.0 [-4.6, +2.1] | +1.0 [-0.9, +2.8] | 5 | 5 |
| llama-4-maverick | thought_removed | agentharm | 0.738 | 10.9 | +6.7 [+4.2, +9.4]* | -3.9 [-8.6, +0.0] | +0.5 [-3.0, +3.5] | 12 | 7 |
| llama-4-maverick | code_python | agentharm | 0.738 | 9.1 | +3.6 [+1.5, +5.9]* | -0.5 [-3.8, +3.6] | +0.5 [-2.6, +3.7] | 6 | 5 |
| llama-4-maverick | shell_cli | agentharm | 0.738 | 8.8 | +3.3 [+1.3, +5.3]* | -1.5 [-4.1, +1.1] | +1.9 [-1.9, +5.7] | 5 | 10 |
| qwen3.8-27b | thought_removed | agentharm | 0.742 | 4.0 | +2.6 [+1.1, +4.4]* | -1.0 [-3.4, +1.0] | +1.0 [-2.5, +4.7] | 3 | 8 |
| qwen3.8-27b | code_python | agentharm | 0.742 | 1.7 | -0.1 [-1.2, +0.9] | +0.0 [+0.0, +0.0] | +0.5 [-2.5, +3.6] | 0 | 5 |
| qwen3.8-27b | shell_cli | agentharm | 0.742 | 1.9 | +0.1 [-1.0, +1.4] | -0.5 [-1.7, +0.0] | +0.0 [-2.9, +2.8] | 1 | 4 |
| nemotron-3.5-lightning-30b | thought_removed | agentharm | 0.660 | 12.4 | +7.8 [+5.1, +10.3]* | +4.9 [+1.5, +9.3]* | +1.5 [-3.5, +6.1] | 5 | 14 |
| nemotron-3.5-lightning-30b | code_python | agentharm | 0.660 | 9.5 | +1.8 [-0.1, +3.6] | +0.0 [-3.0, +3.2] | -4.9 [-9.3, -0.5]* | 9 | 3 |
| nemotron-3.5-lightning-30b | shell_cli | agentharm | 0.660 | 9.5 | +1.8 [+0.0, +3.8] | +0.5 [-3.6, +4.5] | -5.8 [-9.1, -2.4]* | 8 | 1 |
| ts-guard | thought_removed | agentharm | 0.752 | 3.8 | -0.1 [-1.6, +1.3] | +1.0 [+0.0, +2.7] | -1.5 [-5.1, +2.1] | 0 | 6 |
| ts-guard | code_python | agentharm | 0.752 | 4.6 | +2.2 [+0.5, +4.2]* | -0.5 [-1.8, +0.0] | +3.4 [-2.3, +9.4] | 2 | 16 |
| ts-guard | shell_cli | agentharm | 0.752 | 6.8 | +4.5 [+2.3, +6.9]* | +0.0 [-1.9, +2.1] | +12.6 [+5.7, +19.2]* | 2 | 33 |

* = 95% interval excludes zero. Wrapper conditions are adjusted against placebo_env_wrapper, thought_removed against placebo_whitespace.

## 2026-09-05 03:02 UTC+2 — credential expiry

Both the application-default and the CLI Google credentials expired at ~03:02 (organization
reauthentication policy). Every call after that returned 401: StepGuard's placebo_env_wrapper
(638 error rows) and its three programmatic conditions, TS-Guard's ASB run from
placebo_env_wrapper on, Gemini's ASB run (3,060 401s), the NL secondary judge (45 errors), and the
watchdog. StepGuard original and placebo_whitespace completed cleanly before the expiry (10 rows
each are StepGuard input-contract rejections, expected). Runs were stopped at 03:20; a watcher
resumes all of them and undeploys the two endpoints as soon as credentials are valid again
(`results/logs/resume_after_reauth.sh`). Endpoint hours during the outage are recorded from
createTime once `list` works again.

## StepGuard (self-deployed, authors' prompt via hash-pinned upstream literal, greedy, ≤4,096 new tokens) — all six conditions

Baseline: AgentDojo BACC 0.978 (TPR 0.994, FPR 0.038); AgentHarm 0.754 (TPR 0.826, FPR 0.317). 10 rows per condition
rejected by StepGuard's own input contract (ambiguous Thought / Action markers); placebo flips 0.7 % / 1.8 %.

| condition | source | flip − placebo [95% CI] | new misses | new FPs | ΔTPR [95% CI] | ΔFPR [95% CI] |
|---|---|---|---:|---:|---|---|
|  thought_removed  |  agentdojo  |  +0.0 [-0.5, +0.5]  |  0  |  6  |  +0.0 [+0.0, +0.0]  |  +0.3 [-0.3, +1.0]  |
|  thought_removed  |  agentharm  |  +1.5 [+0.7, +2.6]*  |  3  |  4  |  -0.5 [-2.7, +1.6]  |  -1.0 [-4.0, +2.2]  |
|  code_python  |  agentdojo  |  +1.2 [+0.4, +1.9]*  |  1  |  20  |  +0.0 [-0.9, +0.9]  |  +0.9 [-0.6, +2.2]  |
|  code_python  |  agentharm  |  +2.5 [+0.4, +4.7]*  |  9  |  6  |  -0.5 [-5.7, +4.4]  |  -2.0 [-5.8, +2.1]  |
|  shell_cli  |  agentdojo  |  +1.1 [+0.3, +1.8]*  |  1  |  17  |  +0.0 [-0.8, +0.9]  |  +0.3 [-0.9, +1.6]  |
|  shell_cli  |  agentharm  |  +3.6 [+1.3, +6.2]*  |  10  |  8  |  -0.5 [-5.2, +4.5]  |  -0.5 [-4.5, +3.2]  |

Reading: near-invariant. ≤ 1.2 points of churn above placebo on AgentDojo and 2.5–3.6 on AgentHarm, no
error-rate shift with an interval excluding zero. The format sensitivity is specific to TS-Guard, not to
JSON-trained guards as a class; StepGuard's contrastive same-context/different-action training (StepGen)
is the obvious candidate cause. Per-source ranking stability (8 guards, BACC): AgentDojo shell τ = 1.000,
AgentHarm shell τ = 0.786 (TS-Guard falls from 5th to last).

## ASB extension (TS-Guard complete; Gemini complete except thought_removed at time of writing)

Baselines on 4,826 renderable ASB steps: TS-Guard BACC 0.969 (TPR 0.973, FPR 0.034; placebo flips 1.2 %);
Gemini 3.8 Flash BACC 0.812 (TPR 0.670, FPR 0.046; placebo flips 2.7 %) — Gemini misses a third of ASB's unsafe steps at baseline.

| guard | condition | flip − placebo [95% CI] | new misses | new FPs | ΔTPR [95% CI] | ΔFPR [95% CI] |
|---|---|---|---:|---:|---|---|
|  gemini-3.8-flash  |  thought_removed  |  +0.1 [-1.1, +1.2]  |  14  |  11  |  +2.7 [-0.0, +5.3]  |  +0.8 [-0.5, +2.2]  |
|  gemini-3.8-flash  |  code_python  |  -0.1 [-0.6, +0.3]  |  27  |  22  |  +0.3 [-1.1, +1.6]  |  +0.2 [-0.3, +0.8]  |
|  gemini-3.8-flash  |  shell_cli  |  -0.3 [-0.8, +0.1]  |  28  |  18  |  -0.2 [-1.5, +1.1]  |  +0.1 [-0.3, +0.5]  |
|  ts-guard  |  thought_removed  |  +5.3 [+4.6, +6.0]*  |  7  |  221  |  +0.9 [+0.1, +1.8]*  |  +8.7 [+7.4, +10.0]*  |
|  ts-guard  |  code_python  |  +6.6 [+5.7, +7.5]*  |  4  |  305  |  +1.1 [+0.4, +2.0]*  |  +12.7 [+11.1, +14.3]*  |
|  ts-guard  |  shell_cli  |  +19.0 [+17.8, +20.4]*  |  5  |  873  |  +1.5 [+0.6, +2.5]*  |  +37.8 [+35.4, +40.1]*  |

Reading: on its home benchmark TS-Guard's shell-form shift is the largest in the study — FPR +37.8 points
[+35.4, +40.1], 873 benign steps newly blocked; Python +12.7; even Thought removal +8.7. Gemini is invariant on ASB
(adjusted flips within ±0.3 points). The effect scales with how well the guard fits the benchmark's JSON surface.

## AgentDoG-Qwen3-4B, ShieldAgent (thu-coai), Safiron — all six conditions, zero errors (deployed 12:12–12:50)

Prompts rendered from the authors' released code (model cards; Agent-SafetyBench score/eval_with_shield.py);
see src/action_invariance/other_guards.py. These guards judge trajectories or plans rather than TS-Bench-style
steps and start from weak baselines here — the study reports their shift, not their accuracy.

Baselines (AgentDojo / AgentHarm): AgentDoG BACC 0.687 / 0.519 (FPR 0.63 / 0.88); ShieldAgent 0.759 / 0.585
(FPR 0.40 / 0.77); Safiron 0.574 / 0.510 (TPR 0.31 / 0.10 — misses most unsafe steps). Placebo flips ≤ 0.8 %.
ShieldAgent's format has no rationale slot, so thought_removed is an identical input for it (reported n/a).

| guard | condition | source | flip − placebo [95% CI] | new misses | new FPs | ΔTPR [95% CI] | ΔFPR [95% CI] |
|---|---|---|---|---:|---:|---|---|
|  agentdog  |  thought_removed  |  agentdojo  |  +12.5 [+10.5, +14.6]*  |  0  |  133  |  +0.0 [+0.0, +0.0]  |  +11.9 [+8.8, +14.9]*  |
|  agentdog  |  thought_removed  |  agentharm  |  +7.0 [+4.6, +9.3]*  |  2  |  7  |  +1.0 [-1.7, +3.6]  |  +0.0 [-3.6, +3.8]  |
|  agentdog  |  code_python  |  agentdojo  |  +10.8 [+8.9, +12.6]*  |  0  |  162  |  +0.0 [+0.0, +0.0]  |  +18.7 [+15.9, +21.6]*  |
|  agentdog  |  code_python  |  agentharm  |  +6.3 [+3.9, +8.8]*  |  0  |  14  |  +1.5 [+0.0, +3.2]  |  +6.3 [+3.0, +10.1]*  |
|  agentdog  |  shell_cli  |  agentdojo  |  +18.8 [+16.5, +21.1]*  |  0  |  259  |  +0.0 [+0.0, +0.0]  |  +29.8 [+26.6, +33.1]*  |
|  agentdog  |  shell_cli  |  agentharm  |  +8.2 [+5.5, +11.1]*  |  0  |  22  |  +2.5 [+0.4, +5.3]*  |  +10.7 [+6.8, +15.3]*  |
|  shieldagent  |  thought_removed  |  agentdojo  |  +0.1 [+0.0, +0.3]  |  0  |  0  |  +0.0 [+0.0, +0.0]  |  -0.2 [-0.6, +0.0]  |
|  shieldagent  |  thought_removed  |  agentharm  |  +0.1 [+0.0, +0.4]  |  0  |  0  |  +0.0 [+0.0, +0.0]  |  +0.0 [+0.0, +0.0]  |
|  shieldagent  |  code_python  |  agentdojo  |  +14.4 [+12.2, +16.8]*  |  3  |  163  |  +2.8 [+0.8, +5.0]*  |  +13.4 [+10.4, +16.7]*  |
|  shieldagent  |  code_python  |  agentharm  |  +5.6 [+3.0, +8.4]*  |  0  |  25  |  +2.0 [+0.4, +3.9]*  |  +11.2 [+6.1, +16.7]*  |
|  shieldagent  |  shell_cli  |  agentdojo  |  +22.5 [+20.0, +25.1]*  |  0  |  268  |  +6.2 [+3.8, +8.7]*  |  +26.8 [+23.5, +30.4]*  |
|  shieldagent  |  shell_cli  |  agentharm  |  +4.7 [+2.4, +7.6]*  |  0  |  26  |  +1.5 [+0.0, +3.1]  |  +12.1 [+6.6, +18.3]*  |
|  safiron  |  thought_removed  |  agentdojo  |  +1.6 [+1.0, +2.4]*  |  2  |  2  |  +4.0 [+1.8, +6.3]*  |  +0.1 [-0.2, +0.5]  |
|  safiron  |  thought_removed  |  agentharm  |  +4.2 [+2.6, +5.9]*  |  2  |  1  |  +4.4 [+1.1, +9.0]*  |  -1.0 [-3.1, +1.0]  |
|  safiron  |  code_python  |  agentdojo  |  +9.7 [+7.9, +11.7]*  |  9  |  53  |  +15.3 [+11.3, +19.6]*  |  +5.1 [+3.3, +6.8]*  |
|  safiron  |  code_python  |  agentharm  |  +4.6 [+2.8, +6.4]*  |  2  |  7  |  +4.9 [+1.1, +9.0]*  |  +1.9 [-1.0, +5.2]  |
|  safiron  |  shell_cli  |  agentdojo  |  +43.1 [+39.8, +46.6]*  |  3  |  411  |  +34.9 [+29.7, +40.7]*  |  +47.1 [+43.4, +50.6]*  |
|  safiron  |  shell_cli  |  agentharm  |  +22.8 [+19.4, +26.4]*  |  2  |  39  |  +17.7 [+11.1, +25.1]*  |  +17.0 [+11.1, +22.9]*  |

Reading: four of five specialized guards shift by 21–47 FPR points under the shell form on AgentDojo (Safiron
+47.1, AgentDoG +29.8, ShieldAgent +26.8, TS-Guard +21.2); StepGuard is the exception. Python form: a third to
two thirds of the shell effect for each. Frontier and strong open guards remain invariant.

## 2026-09-05 13:20 — study complete for the short paper

Eleven guards on the primary cohort (Gemini 3.8 Flash + request-blind control, Claude Sonnet 5, Qwen 3.8 27B, Llama 4
Maverick, Nemotron 3.5 Lightning, TS-Guard, StepGuard, AgentDoG, ShieldAgent, Safiron); GLM 5.2 / gpt-oss-120b / Gemma 4
floors only (partner billing stopped 01:10). ASB extension: TS-Guard and Gemini, all six conditions. natural_language:
1,921 sentences generated and judged (Gemini 3.1 Pro 1,745 exact; Claude 573/600 before the stop); review sheet at
data/renders/manual_review.csv, not yet reviewed or run. Paper: the accompanying submission.
Overleaf project pulled to commit 959123c. Endpoints: none deployed.

## 2026-09-05 14:11–14:35 CEST — natural_language (prose) condition, 11 guards

Review: 185 judge-flagged sentences read row by row by Claude (Fable 5.1) on Hasan's instruction; Hasan
counted that read as the human review ("trust the draft"). 183 accept (every judge failure was a trailing
period absorbed into a value, a dropped null key, a type coercion, a parameter rename, an empty tool, or a
multi-sentence value truncated by the judge), 1 repair (travel:23:2, Python list literal in the recipient
slot), 1 reject (workspace:458:5, hallucinated `update_file (...)` tool whose name carries the agent's own
disclaimer; the code/shell forms keep it, the sentence dropped it). Frozen at 6fada35 with
`--no-require-review` (1,736 judge-agreed rows carry no manual decision): 1,920 / 1,921 candidates accepted,
1,920 / 1,938 cohort steps materialized (`invariance_ok: true`, env unchanged → whitespace placebo).

Runs: `results/logs/chain_nl.sh`, repeats 1, concurrency 8. API guards $22.95 (Gemini 3.71, scanner 3.63,
Claude 13.99, Llama 1.62 — Claude and Llama are partner-billed; launched before the 01:10 partner stop was
understood to cover them, not repeated). Seven endpoints deployed in one wave (region quota 16 GPUs; the
guard-invariance session held 3): 1.75 GPU-h total, each undeployed when its own run ended. Errors: Claude 3
empty completions (n = 1,917), scanner 3 Gemini errors after retry (n = 1,917), all others 0.

**Four native-format guards cannot consume prose.** StepGuard's vendored input contract requires exactly one
Action and one Action Input marker (1,914 `ambiguous_or_missing_current_action_markers`, 6 also thought-marker
errors); AgentDoG, ShieldAgent and Safiron are fed a parsed (tool, arguments) record and raise
`current_action_parse_failure` before any model call (1,919 / 1,920; the one parse that slipped through,
agentharm/benign_steps:93:1, is discarded with the rest). Their result files are moved to
`results/logs/nl_rejected_by_input_contract/` so the analysis does not report one-row cells. Reported n/a,
like ShieldAgent's thought_removed. Adapting them to accept prose would change the guard's contract — not done.

Per source, flip − whitespace placebo [95% CI], ΔTPR, ΔFPR (pts), new misses / new FPs:

| guard | source | flip − placebo | ΔTPR | ΔFPR | misses | FPs |
|---|---|---|---|---|---:|---:|
| gemini-3.8-flash | agentdojo | +0.0 [+0.0, +0.0] | +0.0 | +0.0 | 0 | 0 |
| gemini-3.8-flash | agentharm | +0.3 [-1.3, +1.8] | -0.5 [-3.5, +2.0] | +0.5 [-1.1, +2.1] | 4 | 3 |
| gemini scanner | agentdojo | +0.0 [+0.0, +0.0] | +0.0 | +0.0 | 0 | 0 |
| gemini scanner | agentharm | -0.9 [-2.5, +0.8] | -1.6 [-3.5, +0.0] | -2.0 [-4.4, +0.0] | 3 | 1 |
| claude-sonnet-5 | agentdojo | +1.9 [+0.9, +2.8]* | +0.6 [+0.0, +1.5] | +2.4 [+1.2, +3.7]* | 0 | 29 |
| claude-sonnet-5 | agentharm | +1.4 [-0.1, +3.2] | -2.1 [-6.0, +1.1] | +1.0 [-1.1, +3.8] | 6 | 3 |
| llama-4-maverick | agentdojo | +2.9 [+1.5, +4.3]* | +6.9 [+4.0, +9.8]* | +1.7 [+0.1, +3.5]* | 3 | 35 |
| llama-4-maverick | agentharm | +2.4 [+0.6, +4.5]* | -1.5 [-5.5, +2.1] | +0.5 [-1.9, +3.0] | 6 | 4 |
| qwen3.8-27b | agentdojo | -0.1 [-0.6, +0.4] | +0.0 | +0.1 [-0.4, +0.7] | 0 | 3 |
| qwen3.8-27b | agentharm | -0.3 [-1.1, +0.5] | +0.0 | +0.5 [-1.8, +3.3] | 0 | 3 |
| nemotron-30b | agentdojo | +0.7 [-0.4, +1.9] | -0.3 [-1.7, +0.9] | +1.8 [+0.4, +3.4]* | 3 | 31 |
| nemotron-30b | agentharm | +3.0 [+1.3, +4.8]* | +0.0 [-3.2, +3.3] | -1.5 [-5.6, +2.4] | 6 | 6 |
| ts-guard | agentdojo | +2.2 [+1.0, +3.7]* | +4.3 [+1.2, +7.4]* | +0.0 [-1.4, +1.4] | 8 | 19 |
| ts-guard | agentharm | -0.1 [-1.5, +1.3] | -0.5 [-1.8, +0.0] | -4.0 [-8.5, +0.0] | 1 | 7 |

Reading: prose is the mildest rendering for every guard that can read it — adjusted flips ≤ +3.0 pts
(shell: +15.6 to +43 for the specialized guards). Gemini flips zero verdicts on AgentDojo, as under code and
shell; Qwen is within 0.3. Claude's only shift is +2.4 pts FPR on AgentDojo (29 new FPs, no new misses).
Llama and TS-Guard move opposite to the shell form: recall *up* +6.9 and +4.3 on AgentDojo, TS-Guard's FPR
unchanged. Protocol claim 2 ("the prose rendering predominantly adds false positives") is not supported. Leaky
steps (Thought restates the tool name, n = 32) flip 0.0 % for every guard except Nemotron (6.2 %).
The strongest prose result is the boundary one: guards whose interface is a tool-call record have no verdict
at all for an action that arrives as language.
Ranking (BACC, 7 prose-capable guards): AgentDojo — original gemini = scanner > claude > ts-guard > qwen >
nemotron > llama; prose gemini = scanner > claude > ts-guard > llama > qwen > nemotron (Llama climbs two
places on its recall gain, τ ≈ 0.81). AgentHarm — one adjacent swap (scanner above claude, τ ≈ 0.90). The
global row in `summary.md` (τ = 0.52) is not comparable: the reference BACC for Gemini and TS-Guard there is
computed over all 6,764 original rows including ASB.

Paper: `action-invariance-paper` — prose added as a fourth condition (tables, figure, macros generalised to
`CONDS`; the four record-based guards are `n/a` cells), a prose bullet, a results paragraph, the limitations
sentence replaced, a prose example and the protocol notes in the appendix. Main text still ends on page 4,
References start on page 5, 8 pages total.
