from pathlib import Path
from typing import Any

import pytest
import yaml

from kalpakka.tripwire import (
    Catalogue,
    Flag,
    LexiconError,
    LexiconTripwire,
    compile_pattern,
    load_catalogue,
    load_lexicon,
    normalise,
)

RULES = Path(__file__).resolve().parents[3] / "content" / "rules"


@pytest.fixture(scope="module")
def catalogue() -> Catalogue:
    return load_catalogue(RULES / "catalogue.yaml")


@pytest.fixture(scope="module")
def tripwire(catalogue: Catalogue) -> LexiconTripwire:
    return LexiconTripwire(load_lexicon(RULES / "lexicon.yaml", catalogue))


def rules_in(t: LexiconTripwire, text: str) -> set[str]:
    return {f.rule for f in t.check(text, turn=1, segment=0, t=0.0)}


# --- catalogue ---------------------------------------------------------------------------------


def test_catalogue_matches_the_deductions_in_07(catalogue: Catalogue) -> None:
    assert {r: rule.deduction for r, rule in catalogue.rules.items()} == {
        "threat": 20,
        "abuse": 20,
        "third_party_disclosure": 20,
        "bereavement_pressure": 20,
        "late_clock": 15,
        "misleading": 15,
        "repeated_pressure": 10,
        "grievance_ignored": 15,
    }


def test_every_lexicon_layer_rule_has_lexicon_patterns(
    catalogue: Catalogue, tripwire: LexiconTripwire
) -> None:
    lexicon_rules = {r for r, rule in catalogue.rules.items() if "lexicon" in rule.layers}

    assert lexicon_rules == set(tripwire.rule_ids)


# --- shipped examples (each pattern's positive and tricky negative examples) -------------------


def _lexicon_data() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((RULES / "lexicon.yaml").read_text(encoding="utf-8"))
    return data


def _pattern_examples(kind: str) -> list[tuple[str, str, str]]:
    return [
        (rule, entry["pattern"], ex)
        for rule, spec in _lexicon_data()["rules"].items()
        for entry in spec["patterns"]
        for ex in entry[kind]
    ]


@pytest.mark.parametrize(("rule", "pattern", "text"), _pattern_examples("positive"))
def test_each_pattern_fires_on_its_own_positive_example(rule: str, pattern: str, text: str) -> None:
    assert compile_pattern(pattern).search(normalise(text)), (rule, pattern)


@pytest.mark.parametrize(("rule", "pattern", "text"), _pattern_examples("positive"))
def test_positive_example_raises_its_rule(
    tripwire: LexiconTripwire, rule: str, pattern: str, text: str
) -> None:
    assert rule in rules_in(tripwire, text)


@pytest.mark.parametrize(("rule", "pattern", "text"), _pattern_examples("negative"))
def test_tricky_negative_example_does_not_raise_its_rule(
    tripwire: LexiconTripwire, rule: str, pattern: str, text: str
) -> None:
    assert rule not in rules_in(tripwire, text)


def test_every_pattern_has_positive_and_negative_examples() -> None:
    for rule, spec in _lexicon_data()["rules"].items():
        for entry in spec["patterns"]:
            assert entry["positive"], (rule, entry["pattern"])
            assert entry["negative"], (rule, entry["pattern"])


# --- flags --------------------------------------------------------------------------------------


def test_flag_carries_turn_segment_time_quote_layer_and_status(tripwire: LexiconTripwire) -> None:
    flags = tripwire.check("Sir, police bhej dunga kal subah.", turn=4, segment=2, t=41.8)

    assert flags == [
        Flag(
            id="t4-s2-threat",
            turn=4,
            segment=2,
            t=41.8,
            rule="threat",
            quote="police bhej",
            layer="lexicon",
            status="possible",
        )
    ]


def test_matching_ignores_case(tripwire: LexiconTripwire) -> None:
    assert rules_in(tripwire, "POLICE BHEJ DUNGA") == {"threat"}


def test_one_flag_per_rule_per_segment(tripwire: LexiconTripwire) -> None:
    flags = tripwire.check("police bhej dunga, jail ho jayegi", turn=1, segment=0, t=0.0)

    assert [f.rule for f in flags] == ["threat"]


def test_several_rules_in_one_segment_are_all_flagged_in_order(tripwire: LexiconTripwire) -> None:
    flags = tripwire.check(
        "Tum chor ho, police bhej dunga aur CIBIL zero ho jayega", turn=1, segment=0, t=0.0
    )

    assert [f.rule for f in flags] == ["abuse", "misleading", "threat"]


def test_polite_recovery_talk_raises_nothing(tripwire: LexiconTripwire) -> None:
    text = "Namaste Ramesh ji, main SwiftFin se Pooja bol rahi hoon. Kya aap ₹4,000 de sakte hain?"

    assert rules_in(tripwire, text) == set()


def test_empty_segment_raises_nothing(tripwire: LexiconTripwire) -> None:
    assert tripwire.check("   ", turn=1, segment=0, t=0.0) == []


def test_devanagari_boundary_does_not_match_inside_a_longer_word(
    tripwire: LexiconTripwire,
) -> None:
    assert "abuse" not in rules_in(tripwire, "तुम चोरी की रिपोर्ट लिखवाओ")


def test_devanagari_insult_with_vowel_signs_is_matched(tripwire: LexiconTripwire) -> None:
    assert "abuse" in rules_in(tripwire, "तुम बेईमान हो")


# --- invalid lexicon files -------------------------------------------------------------------


def _write(tmp_path: Path, rules: dict[str, object]) -> Path:
    path = tmp_path / "lexicon.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "rules": rules}), encoding="utf-8")
    return path


def _rule(pattern: str) -> dict[str, object]:
    return {"patterns": [{"pattern": pattern, "positive": ["x"], "negative": ["y"]}]}


def test_invalid_regex_error_names_rule_and_pattern(tmp_path: Path, catalogue: Catalogue) -> None:
    path = _write(
        tmp_path,
        {"threat": _rule("(police"), "abuse": _rule("a"), "misleading": _rule("m")},
    )

    with pytest.raises(LexiconError, match=r"threat.*\(police"):
        load_lexicon(path, catalogue)


def test_rule_unknown_to_the_catalogue_is_rejected(tmp_path: Path, catalogue: Catalogue) -> None:
    path = _write(
        tmp_path,
        {
            "threat": _rule("a"),
            "abuse": _rule("b"),
            "misleading": _rule("c"),
            "rudeness": _rule("d"),
        },
    )

    with pytest.raises(LexiconError, match="rudeness"):
        load_lexicon(path, catalogue)


def test_lexicon_layer_rule_without_patterns_is_rejected(
    tmp_path: Path, catalogue: Catalogue
) -> None:
    path = _write(tmp_path, {"threat": _rule("a"), "abuse": _rule("b")})

    with pytest.raises(LexiconError, match="misleading"):
        load_lexicon(path, catalogue)


def test_rule_without_examples_is_rejected(tmp_path: Path, catalogue: Catalogue) -> None:
    no_negative = {"patterns": [{"pattern": "a", "positive": ["x"], "negative": []}]}
    path = _write(tmp_path, {"threat": no_negative, "abuse": _rule("b"), "misleading": _rule("c")})

    with pytest.raises(LexiconError, match="threat"):
        load_lexicon(path, catalogue)


def test_raw_word_boundary_is_rejected_in_favour_of_the_indic_aware_token(
    tmp_path: Path, catalogue: Catalogue
) -> None:
    path = _write(
        tmp_path,
        {"threat": _rule(r"\bpolice"), "abuse": _rule("b"), "misleading": _rule("c")},
    )

    with pytest.raises(LexiconError, match=r"\{b\}"):
        load_lexicon(path, catalogue)


@pytest.mark.parametrize("text", ["तू साला। पैसे दे", "साला।", "শালা। টাকা দাও"])
def test_slur_before_a_danda_is_flagged(tripwire: LexiconTripwire, text: str) -> None:
    assert "abuse" in rules_in(tripwire, text)


@pytest.mark.parametrize(
    "text",
    [
        "Tum chori ki complaint police mein karo",
        "aap fraud calls se savdhan rahein",
        "तुम चोरी की रिपोर्ट लिखवाओ",
    ],
)
def test_insult_word_inside_a_longer_word_is_not_abuse(
    tripwire: LexiconTripwire, text: str
) -> None:
    assert "abuse" not in rules_in(tripwire, text)


def test_bengali_nukta_letters_match_whatever_form_the_pattern_uses() -> None:
    # U+09DF (য়) and U+09DC (ড়) are NFC composition exclusions: NFC text holds the decomposed
    # pair, so a pattern typed with the precomposed letter must be normalised too.
    composed = "বকেয়া"  # বকেয়া as typed on most keyboards
    pattern = compile_pattern("{b}" + composed)
    assert pattern.search(normalise("আপনার " + composed + " টাকা"))
