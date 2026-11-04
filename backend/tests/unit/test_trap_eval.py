"""The trap eval (state-rule half) is deterministic, so `make check` runs the whole set."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

RUNNER = Path(__file__).resolve().parents[3] / "evals" / "traps" / "run_traps.py"


def _runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_traps", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runs() -> list[object]:
    _, result = _runner().run_all()
    return list(result)


def test_every_trap_has_a_correct_and_a_violating_script(runs: list[object]) -> None:
    kinds = {(r.script.rsplit("-", 2)[0], r.kind) for r in runs}  # type: ignore[attr-defined]
    for trap in ("bereavement", "third-party", "late-clock", "grievance", "repeated-pressure"):
        assert (trap, "correct") in kinds and (trap, "violating") in kinds, trap


def test_every_violating_script_is_flagged_by_the_state_rules(runs: list[object]) -> None:
    failed = [(r.script, r.seed, r.flags) for r in runs if r.kind == "violating" and not r.passed]  # type: ignore[attr-defined]
    assert failed == []


def test_no_correct_script_is_flagged_by_any_layer(runs: list[object]) -> None:
    failed = [(r.script, r.seed, r.flags) for r in runs if r.kind == "correct" and not r.passed]  # type: ignore[attr-defined]
    assert failed == []
