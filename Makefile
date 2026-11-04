# KalPakka task runner. Recipes run in bash (Git Bash on Windows, bash on Linux).
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := check

# --frozen: never re-resolve the lock; --offline: never touch the network.
# check-fast runs in the Stop hook, so it must be quick and network-free.
UV := uv run --frozen --offline
BACKEND := cd backend &&

# pytest exits 5 when a folder has no tests yet; that is not a failure for an empty suite.
define pytest_allow_empty
	$(BACKEND) $(UV) pytest $(1) || { rc=$$?; [ $$rc -eq 5 ] && echo "($(1): no tests yet)"; [ $$rc -eq 5 ]; }
endef

.PHONY: check check-fast test test-unit test-contract test-integration lint fmt type eval-tripwire

check: lint type test-unit test-contract   ## everything that must pass before a sub-phase closes

check-fast: lint test-unit                 ## ruff + unit tests; no network, no type check

test: test-unit test-contract test-integration

test-unit:                                 ## unit tests exist from P0.1 on: an empty suite is a failure
	$(BACKEND) $(UV) pytest tests/unit

test-contract:
	$(call pytest_allow_empty,tests/contract)

test-integration:
	$(call pytest_allow_empty,tests/integration)

lint:
	$(BACKEND) $(UV) ruff check .
	$(BACKEND) $(UV) ruff format --check .

fmt:
	$(BACKEND) $(UV) ruff format .
	$(BACKEND) $(UV) ruff check --fix .

type:
	$(BACKEND) $(UV) mypy

# --- evals (model/pattern behaviour; not part of check) ---

eval-tripwire:                             ## lexicon precision/recall on evals/tripwire/utterances.yaml
	$(BACKEND) $(UV) python ../evals/tripwire/run_lexicon.py
