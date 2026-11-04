import pytest

from kalpakka.evalkit import score_labels


def test_utterance_level_counts_any_flag_as_a_detection() -> None:
    expected = [{"threat"}, {"abuse"}, set(), set()]
    predicted = [{"threat"}, set(), {"misleading"}, set()]

    report = score_labels(expected, predicted)

    assert (report.overall.tp, report.overall.fp, report.overall.fn) == (1, 1, 1)
    assert report.overall.precision == pytest.approx(0.5)
    assert report.overall.recall == pytest.approx(0.5)


def test_per_rule_counts_match_rule_ids_not_just_any_flag() -> None:
    expected = [{"threat"}, {"abuse"}]
    predicted = [{"abuse"}, {"abuse"}]

    report = score_labels(expected, predicted)

    assert (report.per_rule["threat"].tp, report.per_rule["threat"].fn) == (0, 1)
    assert (report.per_rule["abuse"].tp, report.per_rule["abuse"].fp) == (1, 1)


def test_no_predictions_gives_undefined_precision_as_none() -> None:
    report = score_labels([{"threat"}], [set()])

    assert report.overall.precision is None
    assert report.overall.recall == 0.0


def test_lengths_must_match() -> None:
    with pytest.raises(ValueError, match="same length"):
        score_labels([set()], [])
