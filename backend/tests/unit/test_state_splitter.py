import random

import pytest

from kalpakka.borrower_state import PromiseSuggestion, StateSuggestion
from kalpakka.brain import DELIMITER, SplitResult, StateSplitter

SPEECH = "Arre sir <sigh> salary saat tareekh ko aayegi. Main tab de dunga."
STATE_JSON = (
    '{"mood_delta": 5, "trust_delta": -3, "promise": {"amount": 4000, "date": "2026-11-12"}, '
    '"wants_to_hang_up": false, "agent_was_respectful": true}'
)
FULL = f"{SPEECH}\n{DELIMITER}\n{STATE_JSON}"


def run(pieces: list[str]) -> tuple[str, SplitResult]:
    splitter = StateSplitter()
    speech = "".join(splitter.feed(p) for p in pieces)
    result = splitter.finish()
    return speech + result.speech_tail, result


def test_speech_and_state_are_separated() -> None:
    speech, result = run([FULL])

    assert speech.strip() == SPEECH
    assert result.suggestion == StateSuggestion(
        mood_delta=5,
        trust_delta=-3,
        promise=PromiseSuggestion.model_validate({"amount": 4000, "date": "2026-11-12"}),
        wants_to_hang_up=False,
        agent_was_respectful=True,
    )
    assert result.errors == []


@pytest.mark.parametrize("cut", range(1, len(FULL)))
def test_delimiter_split_at_any_point_gives_the_same_result(cut: int) -> None:
    speech, result = run([FULL[:cut], FULL[cut:]])

    assert speech.strip() == SPEECH
    assert result.suggestion.mood_delta == 5
    assert result.errors == []


@pytest.mark.parametrize("seed", range(20))
def test_random_small_pieces_give_the_same_result(seed: int) -> None:
    rng = random.Random(seed)
    pieces, i = [], 0
    while i < len(FULL):
        n = rng.randint(1, 4)
        pieces.append(FULL[i : i + n])
        i += n

    speech, result = run(pieces)

    assert speech.strip() == SPEECH
    assert result.suggestion.trust_delta == -3


def test_speech_is_released_before_the_delimiter_completes() -> None:
    splitter = StateSplitter()

    released = splitter.feed("Haan ji, bolo ###ST")

    assert released == "Haan ji, bolo "
    assert not splitter.speech_done


def test_speech_done_flips_when_the_delimiter_arrives() -> None:
    splitter = StateSplitter()
    splitter.feed("Haan ji ###STA")

    released = splitter.feed('TE###\n{"mood_delta": 1}')

    assert released == ""
    assert splitter.speech_done


def test_hash_signs_in_speech_are_not_a_delimiter() -> None:
    speech, _ = run(["Mera ghar #5 mein hai, ## sir. ", DELIMITER, '{"mood_delta": 0}'])

    assert speech == "Mera ghar #5 mein hai, ## sir. "


def test_missing_delimiter_speaks_everything_and_suggests_nothing() -> None:
    speech, result = run(["Kal pakka de dunga sir."])

    assert speech == "Kal pakka de dunga sir."
    assert result.suggestion == StateSuggestion()
    assert result.errors == ["missing_delimiter"]


def test_invalid_json_gives_an_all_zero_suggestion() -> None:
    _, result = run([SPEECH, DELIMITER, '{"mood_delta": 5, trust_delta'])

    assert result.suggestion == StateSuggestion()
    assert result.errors == ["invalid_json"]


def test_json_in_a_code_fence_is_parsed() -> None:
    _, result = run([SPEECH, DELIMITER, '\n```json\n{"mood_delta": 7}\n```\n'])

    assert result.suggestion.mood_delta == 7
    assert result.errors == []


def test_bad_promise_date_drops_only_the_promise() -> None:
    _, result = run(
        [SPEECH, DELIMITER, '{"mood_delta": 4, "promise": {"amount": 4000, "date": "2026-02-30"}}']
    )

    assert result.suggestion.mood_delta == 4
    assert result.suggestion.promise is None
    assert result.errors == ["invalid_promise"]


def test_unknown_fields_and_trailing_text_are_ignored() -> None:
    _, result = run([SPEECH, DELIMITER, '{"mood_delta": 2, "confidence": 0.9} thanks!'])

    assert result.suggestion.mood_delta == 2
    assert result.errors == []


def test_wrong_field_type_falls_back_to_zero_for_that_field() -> None:
    _, result = run([SPEECH, DELIMITER, '{"mood_delta": "very angry"}'])

    assert result.suggestion == StateSuggestion()
    assert result.errors == ["invalid_state"]


def test_raw_state_text_is_kept_for_logging() -> None:
    _, result = run([FULL])

    assert result.raw_state.strip() == STATE_JSON


def test_think_block_is_stripped_and_counted() -> None:
    speech, result = run(
        ["<think>user is polite, lower mood</think>Haan sir, bolo. ", DELIMITER, "{}"]
    )

    assert speech == "Haan sir, bolo. "
    assert result.think_blocks == 1


@pytest.mark.parametrize("cut", range(1, 30))
def test_think_block_split_across_feeds_is_still_stripped(cut: int) -> None:
    text = "<think>hmm, angry</think>Kya chahiye aapko? "
    speech, result = run([text[:cut], text[cut:], DELIMITER, "{}"])

    assert speech == "Kya chahiye aapko? "
    assert result.think_blocks == 1


def test_unclosed_think_block_speaks_nothing() -> None:
    speech, result = run(["<think>the user wants money and I"])

    assert speech == ""
    assert "unclosed_think" in result.errors


def test_feed_after_finish_is_refused() -> None:
    splitter = StateSplitter()
    splitter.finish()

    with pytest.raises(RuntimeError, match="finished"):
        splitter.feed("more")


# --- review fixes -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "delimiter", ["### STATE ###", "##STATE##", "###state###", "#### STATE ####", "## STATE ##"]
)
def test_loose_delimiter_variants_still_switch_to_state(delimiter: str) -> None:
    speech, result = run([f"Haan sir, kal pakka.\n{delimiter}\n" + '{"mood_delta": 4}'])

    assert speech.strip() == "Haan sir, kal pakka."
    assert result.suggestion.mood_delta == 4
    assert result.errors == ["loose_delimiter"]


@pytest.mark.parametrize("cut", range(1, 30))
def test_loose_delimiter_split_across_feeds_never_leaks_json(cut: int) -> None:
    text = 'Theek hai.\n### STATE ###\n{"mood_delta": 4}'
    speech, result = run([text[:cut], text[cut:]])

    assert "{" not in speech
    assert "STATE" not in speech
    assert result.suggestion.mood_delta == 4


def test_bare_json_without_any_delimiter_is_never_spoken() -> None:
    speech, result = run(['Kal pakka sir. {"mood_delta": 6, "trust_delta": -2}'])

    assert speech == "Kal pakka sir. "
    assert result.suggestion.mood_delta == 6
    assert result.errors == ["missing_delimiter"]


def test_orphan_think_close_is_dropped_and_recorded() -> None:
    speech, result = run(["</think>Haan bolo sir. ", DELIMITER, "{}"])

    assert speech == "Haan bolo sir. "
    assert "orphan_think_close" in result.errors


def test_one_bad_field_does_not_discard_the_rest() -> None:
    _, result = run(
        [
            SPEECH,
            DELIMITER,
            '{"mood_delta": 2.5, "trust_delta": 3, '
            '"promise": {"amount": 4000, "date": "2026-11-12"}}',
        ]
    )

    assert result.suggestion.mood_delta == 0
    assert result.suggestion.trust_delta == 3
    assert result.suggestion.promise is not None
    assert result.errors == ["invalid_state"]


def test_delimiter_held_at_the_edge_is_resolved_at_finish() -> None:
    splitter = StateSplitter()

    speech = splitter.feed("Theek hai sir. ###STATE##")
    result = splitter.finish()

    assert speech + result.speech_tail == "Theek hai sir. "
    assert result.errors == ["loose_delimiter", "invalid_json"]
