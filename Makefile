PY ?= .venv/bin/python
SYSPY ?= $(shell command -v python3.12 || command -v python3.13 || command -v python3.11 || command -v python3.10 || command -v python3)
AI ?= .venv/bin/action-invariance
CONTROLS = original placebo_whitespace placebo_env_wrapper
PROGRAMMATIC = thought_removed code_python shell_cli
LLM = natural_language
ALL = $(CONTROLS) $(PROGRAMMATIC)
GUARDS ?= gemini-3.8-flash claude-sonnet-5 llama-4-maverick glm-5.2 gpt-oss-120b gemma-4-26b qwen3.8-27b nemotron-3.5-lightning-30b ts-guard gemini-3.8-flash-scanner
REPEATS ?= 1
COHORT ?= data/inventory/cohort_primary.txt

.PHONY: setup fetch inventory materialize run analyze cost test verify-models endpoints clean-mock

setup:
	$(SYSPY) -m venv --clear .venv && $(PY) -m pip install -U pip && $(PY) -m pip install -e '.[dev,vertex]'
	@echo "next: export GOOGLE_CLOUD_PROJECT=GCP-PROJECT && gcloud auth application-default login && make verify-models"

fetch:
	$(AI) fetch --from-local ../ToolSafe-Lab/data/raw/eval || $(AI) fetch

inventory:
	$(AI) inventory

materialize:
	$(AI) materialize $(ALL)

verify-models:
	$(PY) scripts/verify_models.py

run:
	$(AI) run --guards $(GUARDS) --conditions $(ALL) --repeats $(REPEATS) --subset $(COHORT) --concurrency 6

analyze:
	$(AI) analyze

cost:
	$(AI) cost --plan $(GUARDS) --repeats $(REPEATS) --n-conditions 7 --subset $(COHORT)

test:
	$(PY) -m pytest -q

clean-mock:
	rm -rf data/conditions results/raw results/summary.* results/cost_ledger.json

# self-deployed endpoints: make endpoints CMD="deploy qwen3.8-27b --execute" | CMD=list | CMD=sync | CMD="undeploy --all --yes"
endpoints:
	$(PY) scripts/endpoints.py $(CMD)
