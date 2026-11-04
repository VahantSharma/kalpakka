"""Persona realism scoring (09 "Persona realism"): each reply checked in code, no model."""

from pathlib import Path

import pytest

from kalpakka.realism import (
    RealismSpec,
    TurnScore,
    load_realism_spec,
    score_turn,
    summarise,
)
from tests.factories import make_persona

EVALS = Path(__file__).resolve().parents[3] / "evals" / "persona_realism"


@pytest.fixture
def spec() -> RealismSpec:
    return RealismSpec(
        persona="ramesh",
        version=1,
        leak_terms=["engine", "repair", "मरम्मत", "garage"],
        means_terms=["4000", "4,000", "chaar hazaar"],
        style_markers=[
            "hai",
            "nahi",
            "haan",
            "ji",
            "main",
            "aap",
            "ko",
            "tab",
            "arre",
            "ka",
            "mein",
            "tha",
            "theek",
            "ab",
            "hoon",
            "है",
            "नहीं",
        ],
        turns=["Namaste"],
    )


def _score(spec: RealismSpec, speech: str, *, gate_open: bool = False, revealed: bool = False,
           reveal_now: bool = False) -> TurnScore:  # fmt: skip
    return score_turn(
        spec, make_persona(), speech, turn=3, gate_open=gate_open, revealed=revealed,
        reveal_now=reveal_now,
    )  # fmt: skip


def test_a_plain_in_character_reply_scores_clean(spec: RealismSpec) -> None:
    s = _score(spec, "Arre sir, salary saat tareekh ko aayegi, tab de dunga. <sigh>")
    assert s.problems == []


def test_naming_the_hidden_truth_before_the_gate_is_a_leak(spec: RealismSpec) -> None:
    assert "leak" in _score(spec, "Sir auto ka engine repair mein tha").problems


def test_the_truth_after_the_reveal_is_not_a_leak(spec: RealismSpec) -> None:
    assert _score(spec, "Auto ka engine repair mein tha sir", reveal_now=True).problems == []
    assert _score(spec, "Engine theek ho gaya ab", revealed=True).problems == []


def test_offering_the_real_means_before_the_gate_is_caving(spec: RealismSpec) -> None:
    assert "caved" in _score(spec, "Theek hai, abhi 4,000 de sakta hoon").problems
    assert "caved" in _score(spec, "Chaar hazaar abhi ho jayega").problems


def test_means_once_the_gate_is_open_is_not_caving(spec: RealismSpec) -> None:
    assert _score(spec, "Haan, 4000 abhi de dunga", gate_open=True).problems == []


@pytest.mark.parametrize(
    "speech",
    [
        "As an AI, I cannot pay.",
        "Main ek AI hoon, sir.",
        "I am a language model and this is a simulation.",
        "मैं एक एआई हूं",
    ],
)
def test_mentioning_being_an_ai_is_flagged(spec: RealismSpec, speech: str) -> None:
    assert "ai_mention" in _score(spec, speech).problems


def test_the_word_ai_inside_other_words_is_not_flagged(spec: RealismSpec) -> None:
    assert _score(spec, "Haan ji, main aaj nahi de paunga").problems == []


def test_a_reply_in_plain_english_breaks_the_language_style(spec: RealismSpec) -> None:
    assert (
        "style_break" in _score(spec, "I will definitely pay the full amount next week.").problems
    )


def test_a_very_short_reply_is_not_judged_for_style(spec: RealismSpec) -> None:
    assert _score(spec, "Okay sir.").problems == []


def test_an_unofficial_audio_tag_is_flagged(spec: RealismSpec) -> None:
    assert "unofficial_tag" in _score(spec, "Haan ji <cries> nahi hai paisa").problems


def test_more_than_three_sentences_is_too_long(spec: RealismSpec) -> None:
    four = "Haan ji. Main kya karun. Paisa nahi hai. Kal dekhte hain."
    three = "Haan ji. Main kya karun. Paisa nahi hai."
    assert "too_long" in _score(spec, four).problems
    assert "too_long" not in _score(spec, three).problems


def test_an_empty_reply_is_flagged(spec: RealismSpec) -> None:
    assert "empty" in _score(spec, "   ").problems


def test_summary_counts_runs_with_each_problem() -> None:
    clean = [TurnScore(turn=1, problems=[]), TurnScore(turn=2, problems=[])]
    leaky = [TurnScore(turn=1, problems=["leak"]), TurnScore(turn=2, problems=["leak", "caved"])]
    summary = summarise([clean, leaky, clean])
    assert summary["leak"].text == "1 of 3 (33%)"
    assert summary["caved"].text == "1 of 3 (33%)"
    assert summary["ai_mention"].text == "0 of 3 (0%)"


@pytest.mark.parametrize("pid", ["ramesh", "lakshmi", "debu"])
def test_shipped_specs_have_twenty_turns_and_terms(pid: str) -> None:
    spec = load_realism_spec(EVALS / f"{pid}.yaml")
    assert spec.persona == pid
    assert len(spec.turns) == 20
    assert spec.leak_terms and spec.means_terms and spec.style_markers


def test_the_truth_with_the_gate_open_is_not_a_leak(spec: RealismSpec) -> None:
    """The brief allows the truth once the gate is open; a missed open question is not a leak."""
    assert (
        _score(spec, "Sach bataun, auto ka engine repair mein tha", gate_open=True).problems == []
    )
