"""Latency instrumentation (context doc 08): ten timestamps per turn, stages, percentiles."""

import pytest

from kalpakka.metrics import MARKS, STAGES, LatencyStore, TurnTimer, percentile


class StepClock:
    def __init__(self) -> None:
        self.now = 50.0

    def monotonic(self) -> float:
        return self.now


def test_the_ten_documented_marks_in_order() -> None:
    assert MARKS == (
        "ptt_end",
        "commit_sent",
        "transcript_final",
        "brief_built",
        "llm_request",
        "llm_first_token",
        "first_sentence_ready",
        "tts_request",
        "tts_first_audio",
        "browser_first_play",
    )


def test_marks_are_seconds_since_ptt_end_and_first_mark_wins() -> None:
    clock = StepClock()
    timer = TurnTimer(clock)
    timer.mark("ptt_end")
    clock.now += 0.012
    timer.mark("commit_sent")
    clock.now += 0.4
    timer.mark("transcript_final")
    clock.now += 1.0
    timer.mark("transcript_final")  # a second final for the same turn does not move the mark
    assert timer.marks() == pytest.approx(
        {"ptt_end": 0.0, "commit_sent": 0.012, "transcript_final": 0.412}
    )


def test_unknown_mark_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown latency mark"):
        TurnTimer(StepClock()).mark("coffee_break")


def test_stages_are_differences_between_marks() -> None:
    marks = {
        "ptt_end": 0.0,
        "commit_sent": 0.01,
        "transcript_final": 0.41,
        "brief_built": 0.412,
        "llm_request": 0.413,
        "llm_first_token": 0.713,
        "first_sentence_ready": 0.913,
        "tts_request": 0.914,
        "tts_first_audio": 1.114,
        "browser_first_play": 1.2,
    }
    stages = TurnTimer.stages_from(marks)
    assert stages == pytest.approx(
        {
            "stt_commit_to_final": 0.40,
            "brief_build": 0.002,
            "llm_ttft": 0.30,
            "first_sentence": 0.20,
            "tts_first_audio": 0.20,
            "server_total": 1.114,
            "total": 1.2,
        }
    )
    assert set(stages) == set(STAGES)


def test_stages_skip_what_was_not_measured() -> None:
    stages = TurnTimer.stages_from({"ptt_end": 0.0, "tts_first_audio": 1.1})
    assert stages == pytest.approx({"server_total": 1.1})


def test_percentile_uses_nearest_rank() -> None:
    values = [0.9, 1.1, 1.2, 1.3, 2.4]
    assert percentile(values, 50) == 1.2
    assert percentile(values, 95) == 2.4
    assert percentile([1.0], 95) == 1.0


def test_percentile_of_nothing_is_none() -> None:
    assert percentile([], 50) is None


def test_store_reports_p50_p95_per_stage() -> None:
    store = LatencyStore(capacity=100)
    for total in (1.0, 1.2, 1.4, 3.0):
        store.add({"server_total": total, "llm_ttft": total / 4})
    report = store.report()
    assert report["turns"] == 4
    assert report["stages"]["server_total"] == {"n": 4, "p50": 1.2, "p95": 3.0}
    assert report["stages"]["llm_ttft"]["n"] == 4
    assert "total" not in report["stages"]  # never measured -> not reported, not zero


def test_store_keeps_only_the_most_recent_turns() -> None:
    store = LatencyStore(capacity=2)
    for total in (9.0, 1.0, 2.0):
        store.add({"server_total": total})
    assert store.report()["stages"]["server_total"]["p95"] == 2.0
