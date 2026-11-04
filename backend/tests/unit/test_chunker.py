import random

import pytest

from kalpakka.chunker import MIN_SENTENCE_CHARS, Chunker

ALL_TAGS = ("sigh", "pauses", "laugh", "shouting", "clears throat", "sniffles", "slow")


def chunk(text: str, allowed: tuple[str, ...] = ALL_TAGS) -> list[str]:
    c = Chunker(allowed_tags=allowed)
    return [*c.feed(text), *c.flush()]


def chunk_streamed(text: str, sizes_seed: int, allowed: tuple[str, ...] = ALL_TAGS) -> list[str]:
    rng = random.Random(sizes_seed)
    c = Chunker(allowed_tags=allowed)
    out: list[str] = []
    i = 0
    while i < len(text):
        n = rng.randint(1, 6)
        out.extend(c.feed(text[i : i + n]))
        i += n
    out.extend(c.flush())
    return out


# --- sentence cutting, three languages ------------------------------------------------------

CUT_CASES = [
    # Hinglish, Latin
    (
        "Arre sir, salary saat tareekh ko aayegi. Main tab de dunga!",
        ["Arre sir, salary saat tareekh ko aayegi.", "Main tab de dunga!"],
    ),
    # Hinglish, Devanagari with danda
    ("मैं कल पैसे दे दूंगा। पक्का बोल रहा हूं।", ["मैं कल पैसे दे दूंगा।", "पक्का बोल रहा हूं।"]),
    # Tanglish, Tamil script + Latin
    (
        "நான் next week pay panren. Festival-ku shop close aachu, sir.",
        ["நான் next week pay panren.", "Festival-ku shop close aachu, sir."],
    ),
    # Benglish, Bengali script with danda
    (
        "আমি next week-e pay kore debo। Promise korchi, dada.",
        ["আমি next week-e pay kore debo।", "Promise korchi, dada."],
    ),
    # question and exclamation
    (
        "Aap kaun bol rahe ho? Mujhe kyun pareshan kar rahe ho!",
        ["Aap kaun bol rahe ho?", "Mujhe kyun pareshan kar rahe ho!"],
    ),
    # ellipsis, three dots and the single character
    ("Haan... dekhta hoon kal tak.", ["Haan... dekhta hoon kal tak."]),
    (
        "Sir… main hospital mein hoon abhi. Baad mein baat karo.",
        ["Sir… main hospital mein hoon abhi.", "Baad mein baat karo."],
    ),
    # newline always cuts
    (
        "Theek hai sir, main dekh leta hoon\nKal phone karna aap.",
        ["Theek hai sir, main dekh leta hoon", "Kal phone karna aap."],
    ),
]


@pytest.mark.parametrize(("text", "expected"), CUT_CASES)
def test_cuts_sentences_on_danda_period_question_exclamation(
    text: str, expected: list[str]
) -> None:
    assert chunk(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Mera EMI ₹2.5 hazaar nahi, poore ₹12,500 hai, sir ji.",
        "Interest 1.5 percent laga diya aapne, ye galat hai.",
        "Mr. Sharma ne bola tha ki Rs. 4000 abhi chalega.",
        "Dr. Gupta ke paas gaya tha main, Mrs. Verma ke saath.",
    ],
)
def test_numbers_and_abbreviations_do_not_cut(text: str) -> None:
    assert chunk(text) == [text]


def test_closing_quote_stays_with_its_sentence() -> None:
    assert chunk('Usne bola "kal pakka." Phir gayab ho gaya.') == [
        'Usne bola "kal pakka."',
        "Phir gayab ho gaya.",
    ]


# --- fragment merging --------------------------------------------------------------------------


def test_short_fragment_merges_with_the_next_sentence() -> None:
    assert chunk("Arre sir. Main abhi gaadi chala raha hoon.") == [
        "Arre sir. Main abhi gaadi chala raha hoon."
    ]


def test_short_final_fragment_is_still_spoken() -> None:
    assert chunk("Main abhi gaadi chala raha hoon. Bas.") == [
        "Main abhi gaadi chala raha hoon.",
        "Bas.",
    ]


def test_merge_threshold_is_configurable_per_chunker() -> None:
    c = Chunker(allowed_tags=ALL_TAGS, min_chars=4)

    out = [*c.feed("Arre sir. Main abhi gaadi chala raha hoon. "), *c.flush()]

    assert out == ["Arre sir.", "Main abhi gaadi chala raha hoon."]
    assert MIN_SENTENCE_CHARS == 12


# --- streaming equivalence ------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize(("text", "expected"), CUT_CASES)
def test_streamed_in_random_pieces_gives_the_same_sentences(
    text: str, expected: list[str], seed: int
) -> None:
    assert chunk_streamed(text, seed) == expected


def test_decimal_split_across_feeds_does_not_cut() -> None:
    c = Chunker(allowed_tags=ALL_TAGS)

    assert c.feed("Mera EMI ₹2.") == []
    assert c.feed("5 hazaar nahi hai sir. ") == ["Mera EMI ₹2.5 hazaar nahi hai sir."]


def test_a_complete_sentence_is_released_before_the_stream_ends() -> None:
    c = Chunker(allowed_tags=ALL_TAGS)

    released = c.feed("Salary saat tareekh ko aayegi sir. Main")

    assert released == ["Salary saat tareekh ko aayegi sir."]


# --- tag policing --------------------------------------------------------------------------


def test_unofficial_tags_are_stripped() -> None:
    assert chunk("<angry> Kya bakwas hai ye, sir ji.") == ["Kya bakwas hai ye, sir ji."]


def test_official_but_not_allowed_tags_are_stripped() -> None:
    out = chunk("<laugh> Kya bakwas hai ye, sir ji.", allowed=("sigh", "shouting"))

    assert out == ["Kya bakwas hai ye, sir ji."]


def test_at_most_two_tags_per_turn_across_sentences() -> None:
    out = chunk(
        "<sigh> Main kya karun sir ji. <pauses> Paisa nahi hai abhi. <sigh> Kal dekhte hain sir."
    )

    assert out == [
        "<sigh> Main kya karun sir ji.",
        "<pauses> Paisa nahi hai abhi.",
        "Kal dekhte hain sir.",
    ]


def test_tags_are_rendered_in_canonical_form() -> None:
    assert chunk("<SIGH>  Theek hai, dekhta hoon sir.") == ["<sigh> Theek hai, dekhta hoon sir."]


def test_a_tag_is_never_the_whole_sentence() -> None:
    assert chunk("Mujhe aur baat nahi karni. <sigh>") == ["Mujhe aur baat nahi karni."]


def test_a_tag_alone_between_sentences_joins_the_next_sentence() -> None:
    assert chunk("Paisa nahi hai abhi mere paas.\n<sigh>\nKal dekhte hain sir.") == [
        "Paisa nahi hai abhi mere paas.",
        "<sigh> Kal dekhte hain sir.",
    ]


def test_tag_split_across_feeds_is_recognised() -> None:
    c = Chunker(allowed_tags=("clears throat",))

    out = [*c.feed("<clears thr"), *c.feed("oat> Haan bolo, kaun hai."), *c.flush()]

    assert out == ["<clears throat> Haan bolo, kaun hai."]


def test_period_inside_a_tag_does_not_cut() -> None:
    assert chunk("<emotion.sad> Paisa nahi hai abhi mere paas.") == [
        "Paisa nahi hai abhi mere paas."
    ]


def test_dropped_tags_leave_no_double_spaces() -> None:
    assert chunk("Paisa <angry> nahi hai abhi mere paas.") == ["Paisa nahi hai abhi mere paas."]


def test_feed_after_flush_is_refused() -> None:
    c = Chunker(allowed_tags=ALL_TAGS)
    c.flush()

    with pytest.raises(RuntimeError, match="flushed"):
        c.feed("aur kuch")


def test_unallowed_tag_name_in_allowed_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="angry"):
        Chunker(allowed_tags=("sigh", "angry"))


def test_a_fragment_of_exactly_min_chars_is_not_merged() -> None:
    assert chunk("Haan haan... dekhta hoon kal tak.") == ["Haan haan...", "dekhta hoon kal tak."]


def test_curly_closing_quote_stays_with_its_sentence() -> None:
    assert chunk("Usne bola “kal pakka.” Phir gayab ho gaya.") == [
        "Usne bola “kal pakka.”",
        "Phir gayab ho gaya.",
    ]


def test_digits_after_a_period_never_count_as_closers() -> None:
    assert chunk("Total 12500 hai sir ji.2 din ruk jao aap.") == [
        "Total 12500 hai sir ji.2 din ruk jao aap."
    ]


# --- review fixes -------------------------------------------------------------------------------


def test_tag_between_a_held_fragment_and_the_next_sentence_keeps_its_position() -> None:
    # A tag scopes from where it sits; it must not move in front of "Arre sir."
    assert chunk("Arre sir.\n<sigh>\nMain abhi gaadi chala raha hoon.") == [
        "Arre sir. <sigh> Main abhi gaadi chala raha hoon."
    ]


def test_devanagari_rupee_abbreviation_does_not_cut() -> None:
    assert chunk("Haan main 500 रु. de dunga sir, pakka.") == [
        "Haan main 500 रु. de dunga sir, pakka."
    ]


def test_danda_cuts_even_without_a_following_space() -> None:
    assert chunk("मैं कल पैसे दे दूंगा।अब फ़ोन रखो आप।") == ["मैं कल पैसे दे दूंगा।", "अब फ़ोन रखो आप।"]


def test_stray_angle_brackets_never_reach_timbre() -> None:
    assert chunk("Paisa nahi hai sir <sigh. Kal dekhte hain sir.") == [
        "Paisa nahi hai sir sigh.",
        "Kal dekhte hain sir.",
    ]


def test_ordinal_st_is_not_an_abbreviation() -> None:
    assert chunk("Main de dunga sir, 21st. Pakka promise hai ji.") == [
        "Main de dunga sir, 21st.",
        "Pakka promise hai ji.",
    ]


STREAM_EDGE_CASES = [
    "Arre sir.\n<sigh>\nMain abhi gaadi chala raha hoon.",
    "<SIGH> Mr. Sharma ne bola tha Rs. 4000 chalega. <pauses> Kal dekhte hain sir.",
    'Usne bola "kal pakka." Phir gayab ho gaya. Bas.',
    "Mera EMI ₹2.5 hazaar nahi, poore ₹12,500 hai?! Aap galat ho sir ji.",
    "मैं कल पैसे दे दूंगा।अब फ़ोन रखो आप। <clears throat> हाँ बोलो।",
    "x<5 nahi hota sir. <angry> Kya bol rahe ho aap mujhe.",
]


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("text", STREAM_EDGE_CASES)
def test_edge_cases_stream_identically_in_random_pieces(text: str, seed: int) -> None:
    assert chunk_streamed(text, seed) == chunk(text)
