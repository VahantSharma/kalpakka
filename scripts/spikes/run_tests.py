# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest==9.2.0", "pyyaml==6.0.3", "httpx==0.28.1"]
# ///
"""Unit tests for the infra/spike helpers (budget, spike metrics, quant gate, GPU runbook).

These live outside backend/ on purpose: the backend pyproject never changes for infra tooling.
Run from anywhere:  uv run --script scripts/spikes/run_tests.py [-k name]
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUITES = [
    "scripts/spikes/evon_modal/tests",
    "evals/quant_gate/tests",
    "infra/gpu/tests",
    "infra/backend/tests",
]

if __name__ == "__main__":
    paths = [str(ROOT / s) for s in SUITES if (ROOT / s).is_dir()]
    args = ["-q", "-p", "no:cacheprovider", "--import-mode=importlib", "--rootdir", str(ROOT)]
    sys.exit(pytest.main([*args, *paths, *sys.argv[1:]]))
