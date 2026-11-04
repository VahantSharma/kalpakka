import json

import pytest
from budget import (
    RATES_USD_PER_S,
    BudgetExceeded,
    Ledger,
    Resources,
    budget_from_env,
    cost_usd,
    meter_line,
)


def test_gpu_cost_is_rate_times_seconds_plus_cpu_and_memory() -> None:
    res = Resources(gpu="A100-80GB", cpu_cores=2, mem_gib=10)
    expected = 3600 * (
        RATES_USD_PER_S["A100-80GB"]
        + 2 * RATES_USD_PER_S["cpu_core"]
        + 10 * RATES_USD_PER_S["mem_gib"]
    )
    assert cost_usd(res, 3600) == pytest.approx(expected)


def test_cpu_only_cost_has_no_gpu_part() -> None:
    res = Resources(gpu=None, cpu_cores=8, mem_gib=64)
    assert cost_usd(res, 100) == pytest.approx(100 * (8 * 0.0000131 + 64 * 0.00000222))


def test_modal_published_rates_are_the_ones_read_on_2026_11_05() -> None:
    assert RATES_USD_PER_S["A100-80GB"] == 0.000694
    assert RATES_USD_PER_S["L40S"] == 0.000542


def test_unknown_gpu_is_rejected() -> None:
    with pytest.raises(ValueError, match="H900"):
        cost_usd(Resources(gpu="H900", cpu_cores=1, mem_gib=1), 1)


def test_negative_seconds_rejected() -> None:
    with pytest.raises(ValueError):
        cost_usd(Resources(gpu=None, cpu_cores=1, mem_gib=1), -1)


def test_empty_ledger_has_spent_nothing(tmp_path) -> None:
    assert Ledger(tmp_path / "ledger.json").spent_usd() == 0.0


def test_record_persists_and_sums(tmp_path) -> None:
    path = tmp_path / "out" / "ledger.json"
    ledger = Ledger(path)
    res = Resources(gpu="L40S", cpu_cores=1, mem_gib=1)
    entry = ledger.record("serve_q8", res, 600)
    assert entry["gpu_seconds"] == 600
    again = Ledger(path)
    assert again.spent_usd() == pytest.approx(cost_usd(res, 600))
    assert json.loads(path.read_text())["entries"][0]["script"] == "serve_q8"


def test_cpu_entry_has_zero_gpu_seconds(tmp_path) -> None:
    entry = Ledger(tmp_path / "l.json").record(
        "download", Resources(gpu=None, cpu_cores=4, mem_gib=16), 60
    )
    assert entry["gpu_seconds"] == 0


def test_guard_refuses_when_worst_case_exceeds_budget(tmp_path) -> None:
    ledger = Ledger(tmp_path / "l.json")
    ledger.record("earlier", Resources(gpu="A100-80GB", cpu_cores=0, mem_gib=0), 10_000)  # $6.94
    res = Resources(gpu="A100-80GB", cpu_cores=0, mem_gib=0)
    with pytest.raises(BudgetExceeded, match=r"\$10\.00"):
        ledger.check(res, max_seconds=5400, budget_usd=10.0)  # +$3.75 -> $10.69


def test_guard_allows_when_worst_case_fits(tmp_path) -> None:
    ledger = Ledger(tmp_path / "l.json")
    res = Resources(gpu="A100-80GB", cpu_cores=0, mem_gib=0)
    planned = ledger.check(res, max_seconds=5400, budget_usd=10.0)
    assert planned == pytest.approx(3.7476)


def test_budget_from_env_default_and_override() -> None:
    assert budget_from_env({}) == 10.0
    assert budget_from_env({"SPIKE_BUDGET_USD": "4.5"}) == 4.5


@pytest.mark.parametrize("bad", ["", "abc", "-1", "0"])
def test_budget_from_env_rejects_bad_values(bad: str) -> None:
    with pytest.raises(ValueError):
        budget_from_env({"SPIKE_BUDGET_USD": bad})


def test_meter_line_shows_gpu_seconds_run_cost_and_total() -> None:
    res = Resources(gpu="A100-80GB", cpu_cores=0, mem_gib=0)
    line = meter_line("serve_ref", res, seconds=1000, spent_before=1.0, budget_usd=10.0)
    assert "GPU-seconds 1000 (A100-80GB)" in line
    assert "this run $0.69" in line
    assert "spike total $1.69 of $10.00" in line
    assert "upper bound" in line


def test_a_started_run_reserves_its_worst_case_until_it_finishes(tmp_path) -> None:
    ledger = Ledger(tmp_path / "l.json")
    a100 = Resources(gpu="A100-80GB", cpu_cores=0, mem_gib=0)
    rid = ledger.reserve("convert", a100, max_seconds=7200)  # $5.00 in flight
    with pytest.raises(BudgetExceeded):
        ledger.check(a100, max_seconds=7200, budget_usd=9.0)  # $5.00 reserved + $5.00 > $9
    ledger.finish(rid, a100, seconds=600)
    assert ledger.spent_usd() == pytest.approx(cost_usd(a100, 600))
    assert ledger.check(a100, max_seconds=7200, budget_usd=9.0) == pytest.approx(4.9968)


def test_a_reservation_never_finished_keeps_counting(tmp_path) -> None:
    path = tmp_path / "l.json"
    Ledger(path).reserve(
        "serve_ref", Resources(gpu="L40S", cpu_cores=0, mem_gib=0), max_seconds=3600
    )
    assert Ledger(path).spent_usd() == pytest.approx(0.000542 * 3600)
