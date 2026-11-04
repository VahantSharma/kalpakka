"""Arena, model-free half: the run matrix, bounded concurrency, the report (07 "Arena")."""

import asyncio

import pytest

from kalpakka.arena import (
    ArenaCase,
    CallRecord,
    Rate,
    build_report,
    case_persona,
    rule_applies,
    run_matrix,
    trap_settings,
)
from tests.factories import make_persona


def _rec(**kw: object) -> CallRecord:
    base: dict[str, object] = {
        "case": ArenaCase(bot="A", persona="ramesh", trap="none", seed=1),
        "turns": 8,
        "stop": "turn_cap",
        "trap_fired": False,
        "revealed": False,
        "promised": False,
        "promise_valid": False,
        "violations": [],
    }
    base.update(kw)
    return CallRecord.model_validate(base)


# --- the matrix ---


def test_trap_settings_follow_07() -> None:
    assert trap_settings("ramesh") == ["none", "bereavement", "third_party_wife", "late_clock"]
    assert trap_settings("debu") == ["none", "bereavement", "third_party_wife", "late_clock"]
    assert trap_settings("lakshmi") == ["none", "bereavement", "disputed_charge", "late_clock"]


def test_full_matrix_is_120_calls_per_bot() -> None:
    from kalpakka.arena import build_matrix

    cases = build_matrix(["A"], ["ramesh", "lakshmi", "debu"], seeds=range(10))
    assert len(cases) == 120
    assert len(set(cases)) == 120


def test_case_persona_forces_exactly_the_trap_setting() -> None:
    persona = make_persona()
    forced = case_persona(persona, "bereavement")
    assert forced.traps_allowed == ["bereavement"]
    assert case_persona(persona, "none").traps_allowed == []
    assert persona.traps_allowed == ["bereavement", "third_party_wife", "late_clock"]


def test_a_trap_the_persona_does_not_allow_is_rejected() -> None:
    with pytest.raises(ValueError, match="disputed_charge"):
        case_persona(make_persona(), "disputed_charge")


# --- which rules could apply in a call ---


def test_context_rules_apply_only_where_their_situation_exists() -> None:
    plain = _rec()
    wife = _rec(case=ArenaCase(bot="A", persona="ramesh", trap="third_party_wife", seed=1),
                trap_fired=True)  # fmt: skip
    revealed = _rec(revealed=True)
    assert rule_applies("threat", plain.model_copy(update={"judged": True}))
    assert not rule_applies("third_party_disclosure", plain)
    assert rule_applies("third_party_disclosure", wife)
    assert not rule_applies("repeated_pressure", plain)
    assert rule_applies("repeated_pressure", revealed)


def test_a_trap_that_never_fired_does_not_count() -> None:
    early_hangup = _rec(
        case=ArenaCase(bot="A", persona="ramesh", trap="bereavement", seed=1), trap_fired=False
    )
    assert not rule_applies("bereavement_pressure", early_hangup)


# --- report ---


def test_rate_reads_as_counts_with_a_percentage() -> None:
    assert Rate(count=7, of=30).text == "7 of 30 (23%)"
    assert Rate(count=0, of=0).text == "0 of 0 (n/a)"


def test_report_counts_violations_over_calls_where_the_rule_could_apply() -> None:
    bere = ArenaCase(bot="A", persona="ramesh", trap="bereavement", seed=1)
    records = [
        _rec(case=bere, trap_fired=True, violations=["bereavement_pressure"], judged=True),
        _rec(case=bere.model_copy(update={"seed": 2}), trap_fired=True, judged=True),
        _rec(case=bere.model_copy(update={"seed": 3}), trap_fired=False, judged=True),
        _rec(violations=["threat"], judged=True),
    ]
    report = build_report(records)["A"]
    assert report.violation_rate["bereavement_pressure"].text == "1 of 2 (50%)"
    assert report.violation_rate["threat"].text == "1 of 4 (25%)"
    assert report.trap_pass_rate["bereavement"].text == "1 of 2 (50%)"
    assert report.trap_not_reached["bereavement"] == 1


def test_report_promise_turns_and_angry_hangups() -> None:
    records = [
        _rec(promised=True, promise_valid=True, turns=6, stop="closed_after_promise"),
        _rec(promised=True, promise_valid=False, turns=10, stop="angry"),
        _rec(turns=14, stop="turn_cap"),
    ]
    report = build_report(records)["A"]
    assert report.promise_rate.text == "2 of 3 (67%)"
    assert report.valid_promise_rate.text == "1 of 3 (33%)"
    assert report.angry_hangup_rate.text == "1 of 3 (33%)"
    assert report.average_turns == 10.0
    assert report.calls == 3


def test_errored_calls_are_counted_and_left_out_of_rates() -> None:
    records = [_rec(), _rec(error="LLM server returned 503", turns=0)]
    report = build_report(records)["A"]
    assert report.calls == 1
    assert report.errors == 1
    assert report.promise_rate.of == 1


def test_report_is_split_per_bot() -> None:
    a = _rec()
    b = _rec(case=ArenaCase(bot="B", persona="ramesh", trap="none", seed=1), violations=[])
    assert set(build_report([a, b])) == {"A", "B"}


# --- running the matrix ---


async def test_run_matrix_bounds_concurrency_and_keeps_order() -> None:
    cases = [ArenaCase(bot="A", persona="ramesh", trap="none", seed=s) for s in range(6)]
    running = 0
    peak = 0

    async def play(case: ArenaCase) -> CallRecord:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        running -= 1
        return _rec(case=case)

    records = await asyncio.wait_for(run_matrix(cases, play, concurrency=2), timeout=5)
    assert peak == 2
    assert [r.case.seed for r in records] == list(range(6))


async def test_a_failing_call_becomes_an_error_record_not_a_crash() -> None:
    cases = [ArenaCase(bot="A", persona="ramesh", trap="none", seed=s) for s in range(2)]

    async def play(case: ArenaCase) -> CallRecord:
        if case.seed == 1:
            raise RuntimeError("boom")
        return _rec(case=case)

    records = await asyncio.wait_for(run_matrix(cases, play, concurrency=2), timeout=5)
    assert records[0].error is None
    assert records[1].error == "RuntimeError: boom"
    assert records[1].case.seed == 1


def test_rates_round_half_up() -> None:
    assert Rate(count=1, of=8).text == "1 of 8 (13%)"
    assert Rate(count=5, of=8).text == "5 of 8 (63%)"


async def test_concurrency_below_one_is_rejected() -> None:
    async def play(case: ArenaCase) -> CallRecord:
        return _rec(case=case)

    with pytest.raises(ValueError, match="concurrency"):
        await run_matrix([], play, concurrency=0)


def test_word_rules_are_not_measured_without_a_judge() -> None:
    """Lexicon flags are only `possible`; without the judge a threat can never be confirmed."""
    unjudged = _rec(violations=[])
    judged = _rec(judged=True, violations=["threat"])
    report = build_report([unjudged, judged])["A"]
    assert report.violation_rate["threat"].text == "1 of 1 (100%)"
    assert not rule_applies("threat", unjudged)


def test_judge_failures_are_counted() -> None:
    report = build_report([_rec(judge_error="no JSON"), _rec(judged=True)])["A"]
    assert report.judge_errors == 1
