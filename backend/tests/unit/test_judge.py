from pathlib import Path

import pytest
from pydantic import ValidationError

from kalpakka.judge import (
    JUDGE_CHECKS,
    CallFacts,
    Evidence,
    JudgeCheck,
    JudgeOutput,
    JudgeViolation,
    TranscriptLine,
    WorstLine,
    find_quote,
    score_call,
    verify_quote,
)
from kalpakka.tripwire import Flag, LexiconTripwire, load_catalogue, load_lexicon

RULES = Path(__file__).resolve().parents[3] / "content" / "rules"
CATALOGUE = load_catalogue(RULES / "catalogue.yaml")
TRIPWIRE = LexiconTripwire(load_lexicon(RULES / "lexicon.yaml", CATALOGUE))

TRANSCRIPT = [
    TranscriptLine(turn=0, role="borrower", text="Haan, hello? Kaun bol raha hai?", t=0.5),
    TranscriptLine(
        turn=1,
        role="agent",
        text="Namaste Ramesh ji, main SwiftFin se Pooja bol rahi hoon.",
        t=3.0,
    ),
    TranscriptLine(turn=1, role="borrower", text="Haan bolo, kya baat hai?", t=6.2),
    TranscriptLine(
        turn=2, role="agent", text="Aapka EMI baarah hazaar paanch sau ka pending hai.", t=9.0
    ),
    TranscriptLine(turn=2, role="borrower", text="Salary saat tareekh ko aayegi sir.", t=12.4),
    TranscriptLine(turn=3, role="agent", text="Sab theek hai? Kya hua aapke saath?", t=15.0),
    TranscriptLine(
        turn=3,
        role="borrower",
        text="Auto teen hafte se kharab tha, kamaai band thi.",
        t=18.1,
    ),
    TranscriptLine(
        turn=4,
        role="agent",
        text="Samajh sakti hoon, mushkil waqt hai. Kya aap chaar hazaar abhi de sakte hain?",
        t=22.0,
    ),
    TranscriptLine(turn=4, role="borrower", text="Haan, chaar hazaar de dunga kal.", t=25.5),
    TranscriptLine(
        turn=5,
        role="agent",
        text="Theek hai, toh kal chaar hazaar, baaki bees November tak. Pakka?",
        t=28.0,
    ),
]


def yes(check_id: str, turn: int, quote: str) -> JudgeCheck:
    return JudgeCheck(id=check_id, evidence=[Evidence(turn=turn, quote=quote)], answer="yes")


GOOD_JUDGE = JudgeOutput(
    checks=[
        yes("identified_self_and_lender", 1, "main SwiftFin se Pooja bol rahi hoon"),
        yes("stated_amount", 2, "baarah hazaar paanch sau ka pending hai"),
        yes("confirmed_promise", 5, "kal chaar hazaar, baaki bees November tak"),
        yes("asked_open_question", 3, "Kya hua aapke saath?"),
        yes("acknowledged_hardship", 4, "Samajh sakti hoon, mushkil waqt hai"),
        yes("secured_promise", 4, "chaar hazaar de dunga kal"),
        yes("offered_option", 4, "Kya aap chaar hazaar abhi de sakte hain?"),
    ],
    violations=[],
    worst_line=None,
)

GOOD_FACTS = CallFacts(promise_valid=True, promise_turn=4, revealed_turn=3)


def score(
    judge: JudgeOutput = GOOD_JUDGE,
    facts: CallFacts = GOOD_FACTS,
    flags: list[Flag] | None = None,
    transcript: list[TranscriptLine] = TRANSCRIPT,
):  # type: ignore[no-untyped-def]
    return score_call(judge, transcript, facts, flags or [], CATALOGUE, TRIPWIRE)


def check(card, check_id: str):  # type: ignore[no-untyped-def]
    return next(c for c in card.checks if c.id == check_id)


# --- quote verification -----------------------------------------------------------------------


def test_exact_quote_in_the_cited_turn_verifies() -> None:
    assert verify_quote(Evidence(turn=1, quote="SwiftFin se Pooja"), TRANSCRIPT)


def test_case_punctuation_and_spacing_differences_still_verify() -> None:
    assert verify_quote(Evidence(turn=3, quote="kya HUA aapke   saath"), TRANSCRIPT)


def test_small_transcription_difference_verifies_fuzzily() -> None:
    assert verify_quote(Evidence(turn=4, quote="chaar hazar de dunga kal"), TRANSCRIPT)


def test_quote_from_a_different_turn_does_not_verify() -> None:
    assert not verify_quote(Evidence(turn=2, quote="SwiftFin se Pooja"), TRANSCRIPT)


def test_invented_quote_does_not_verify() -> None:
    assert not verify_quote(Evidence(turn=4, quote="main police bhej dungi"), TRANSCRIPT)


def test_quote_shorter_than_three_words_proves_nothing() -> None:
    assert not verify_quote(Evidence(turn=4, quote="kal"), TRANSCRIPT)
    assert not verify_quote(Evidence(turn=4, quote="de dunga"), TRANSCRIPT)
    assert verify_quote(Evidence(turn=4, quote="de dunga kal"), TRANSCRIPT)


def test_short_quote_must_match_exactly_not_fuzzily() -> None:
    assert not verify_quote(Evidence(turn=4, quote="de dunga kab"), TRANSCRIPT)


def test_devanagari_quote_verifies() -> None:
    line = TranscriptLine(turn=1, role="agent", text="मैं स्विफ्टफिन से पूजा बोल रही हूं।", t=1.0)

    assert verify_quote(Evidence(turn=1, quote="स्विफ्टफिन से पूजा"), [line])


# --- judge output schema ------------------------------------------------------------------------


def test_evidence_comes_before_the_answer_in_the_schema() -> None:
    props = list(JudgeCheck.model_json_schema()["properties"])

    assert props.index("evidence") < props.index("answer")


def test_unknown_check_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        JudgeCheck(id="was_nice", evidence=[], answer="yes")


def test_duplicate_check_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        JudgeOutput(checks=[yes("stated_amount", 2, "x"), yes("stated_amount", 2, "y")])


def test_judge_cannot_answer_code_decided_checks() -> None:
    assert "promise_valid" not in JUDGE_CHECKS
    assert "discovered_situation" not in JUDGE_CHECKS


# --- scoring ---------------------------------------------------------------------------------


def test_perfect_call_scores_100_with_every_mark_quoted() -> None:
    card = score()

    assert card.total == 100
    assert {s.name: s.earned for s in card.sections} == {
        "clarity": 10,
        "discovery_empathy": 20,
        "outcome": 30,
        "compliance": 40,
    }
    for c in card.checks:
        assert c.status == "earned"
        assert any(e.verified for e in c.evidence), c.id


def test_yes_with_an_unverifiable_quote_earns_nothing() -> None:
    judge = GOOD_JUDGE.model_copy(
        update={
            "checks": [
                yes("stated_amount", 2, "aapko teen lakh dene hain")
                if c.id == "stated_amount"
                else c
                for c in GOOD_JUDGE.checks
            ]
        }
    )

    card = score(judge)

    assert check(card, "stated_amount").status == "unverified"
    assert check(card, "stated_amount").earned == 0
    assert card.total == 97


def test_no_answer_earns_nothing() -> None:
    judge = GOOD_JUDGE.model_copy(
        update={
            "checks": [
                JudgeCheck(id="offered_option", evidence=[], answer="no")
                if c.id == "offered_option"
                else c
                for c in GOOD_JUDGE.checks
            ]
        }
    )

    card = score(judge)

    assert check(card, "offered_option").status == "not_earned"
    assert card.total == 92


def test_a_check_the_judge_skipped_is_reported_missing_and_earns_nothing() -> None:
    judge = GOOD_JUDGE.model_copy(
        update={"checks": [c for c in GOOD_JUDGE.checks if c.id != "asked_open_question"]}
    )

    card = score(judge)

    assert check(card, "asked_open_question").status == "missing"
    assert card.total == 95


def test_promise_validity_comes_from_code_not_the_judge() -> None:
    card = score(facts=GOOD_FACTS.model_copy(update={"promise_valid": False}))

    assert check(card, "promise_valid").earned == 0
    assert card.total == 88


def test_discovery_comes_from_state_and_quotes_the_reveal_turn() -> None:
    card = score()

    discovered = check(card, "discovered_situation")
    assert discovered.earned == 8
    assert discovered.evidence[0].turn == 3
    assert "kharab" in discovered.evidence[0].quote


def test_no_reveal_means_no_discovery_points() -> None:
    card = score(facts=CallFacts(promise_valid=True, promise_turn=4, revealed_turn=None))

    assert check(card, "discovered_situation").earned == 0
    assert check(card, "discovered_situation").status == "not_earned"


# --- compliance --------------------------------------------------------------------------------


def flag(rule: str, turn: int, quote: str, status: str = "confirmed", layer: str = "state") -> Flag:
    return Flag(
        id=f"t{turn}-s0-{rule}",
        turn=turn,
        segment=0,
        t=0.0,
        rule=rule,
        quote=quote,
        layer=layer,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
    )


THREAT_LINE = TranscriptLine(
    turn=6, role="agent", text="Kal tak nahi diya toh police bhej dunga.", t=31.0
)
MISLEAD_LINE = TranscriptLine(turn=7, role="agent", text="Aapka CIBIL zero ho jayega.", t=35.0)
ABUSE_LINE = TranscriptLine(turn=8, role="agent", text="Tum chor ho.", t=38.0)
WIFE_LINE = TranscriptLine(turn=9, role="agent", text="Aapke pati ka EMI baaki hai.", t=41.0)
LONG = [*TRANSCRIPT, THREAT_LINE, MISLEAD_LINE, ABUSE_LINE, WIFE_LINE]


def test_one_confirmed_threat_deducts_20() -> None:
    card = score(flags=[flag("threat", 6, "police bhej dunga", layer="lexicon")], transcript=LONG)

    assert card.compliance.earned == 20
    assert [d.rule for d in card.compliance.deductions] == ["threat"]


def test_two_rules_deduct_both() -> None:
    flags = [flag("threat", 6, "police bhej dunga"), flag("misleading", 7, "CIBIL zero")]

    assert score(flags=flags, transcript=LONG).compliance.earned == 40 - 20 - 15


def test_compliance_never_goes_below_zero() -> None:
    flags = [
        flag("threat", 6, "police bhej dunga"),
        flag("abuse", 8, "Tum chor ho"),
        flag("third_party_disclosure", 9, "pati ka EMI baaki hai"),
    ]

    card = score(flags=flags, transcript=LONG)

    assert card.compliance.earned == 0
    assert len(card.compliance.deductions) == 3


def test_the_same_rule_broken_twice_is_deducted_once_with_both_quotes() -> None:
    threat2 = TranscriptLine(turn=10, role="agent", text="Jail ho jayegi aapko.", t=44.0)
    flags = [flag("threat", 6, "police bhej dunga"), flag("threat", 10, "Jail ho jayegi")]

    card = score(flags=flags, transcript=[*LONG, threat2])

    assert card.compliance.earned == 20
    assert len(card.compliance.deductions[0].occurrences) == 2


def test_withdrawn_flag_is_shown_but_not_deducted() -> None:
    card = score(
        flags=[flag("threat", 6, "police bhej dunga", status="withdrawn")], transcript=LONG
    )

    assert card.compliance.earned == 40
    assert [f.status for f in card.flags] == ["withdrawn"]


def test_possible_flag_not_confirmed_by_the_judge_is_not_deducted() -> None:
    card = score(
        flags=[flag("threat", 6, "police bhej dunga", status="possible", layer="lexicon")],
        transcript=LONG,
    )

    assert card.compliance.earned == 40


def test_judge_added_violation_with_a_verified_quote_is_deducted() -> None:
    judge = GOOD_JUDGE.model_copy(
        update={
            "violations": [JudgeViolation(rule="misleading", turn=7, quote="CIBIL zero ho jayega")]
        }
    )

    card = score(judge, transcript=LONG)

    assert card.compliance.earned == 25
    assert card.compliance.deductions[0].source == "judge"


def test_judge_violation_with_an_invented_quote_is_not_deducted() -> None:
    judge = GOOD_JUDGE.model_copy(
        update={"violations": [JudgeViolation(rule="abuse", turn=2, quote="tum bewakoof ho")]}
    )

    card = score(judge, transcript=LONG)

    assert card.compliance.earned == 40
    assert card.compliance.unverified[0].rule == "abuse"


def test_judge_violation_with_an_unknown_rule_is_rejected() -> None:
    with pytest.raises(ValidationError):
        JudgeViolation(rule="rudeness", turn=2, quote="x")


# --- worst line --------------------------------------------------------------------------------


def test_worst_line_rewrite_is_kept_when_clean() -> None:
    worst = WorstLine(
        turn=6,
        quote="police bhej dunga",
        rewrite="Main samajh sakti hoon, kya hum ek chhota payment plan bana sakte hain?",
    )
    judge = GOOD_JUDGE.model_copy(update={"worst_line": worst})

    card = score(judge, transcript=LONG)

    assert card.worst_line is not None
    assert card.worst_line.rewrite_ok
    assert card.worst_line.quote_verified


def test_worst_line_rewrite_that_itself_violates_is_rejected() -> None:
    worst = WorstLine(
        turn=6, quote="police bhej dunga", rewrite="Pay karo warna police bhej dungi."
    )
    judge = GOOD_JUDGE.model_copy(update={"worst_line": worst})

    card = score(judge, transcript=LONG)

    assert card.worst_line is not None
    assert not card.worst_line.rewrite_ok


def test_promise_validity_cites_the_borrowers_promise_line() -> None:
    evidence = check(score(), "promise_valid").evidence

    assert [e.turn for e in evidence] == [4]
    assert "chaar hazaar de dunga" in evidence[0].quote


# --- review fixes: speaker, promise cross-check, timestamps, negation, robustness ----------------


def test_agent_check_cannot_be_proved_with_a_borrower_quote() -> None:
    assert not verify_quote(
        Evidence(turn=4, quote="chaar hazaar de dunga kal"), TRANSCRIPT, speaker="agent"
    )
    assert verify_quote(
        Evidence(turn=4, quote="chaar hazaar de dunga kal"), TRANSCRIPT, speaker="borrower"
    )


def test_generic_filler_quotes_earn_no_judge_points() -> None:
    judge = JudgeOutput(checks=[yes(cid, 4, "haan") for cid in JUDGE_CHECKS])

    card = score(judge)

    assert sum(c.earned for c in card.checks if c.id in JUDGE_CHECKS) == 0


def test_borrower_line_cannot_be_a_violation() -> None:
    judge = GOOD_JUDGE.model_copy(
        update={
            "violations": [JudgeViolation(rule="abuse", turn=4, quote="chaar hazaar de dunga kal")]
        }
    )

    card = score(judge)

    assert card.compliance.earned == 40
    assert card.compliance.unverified[0].rule == "abuse"


def test_secured_promise_needs_a_promise_recorded_by_the_engine() -> None:
    card = score(facts=CallFacts(promise_valid=False, promise_turn=None, revealed_turn=3))

    assert check(card, "secured_promise").status == "contradicted"
    assert check(card, "secured_promise").earned == 0


def test_promise_valid_without_a_recorded_promise_turn_earns_nothing() -> None:
    card = score(facts=CallFacts(promise_valid=True, promise_turn=None, revealed_turn=3))

    assert check(card, "promise_valid").earned == 0


def test_evidence_time_comes_from_the_matched_line() -> None:
    evidence = check(score(), "secured_promise").evidence[0]

    assert evidence.t == 25.5


def test_evidence_shows_the_transcript_line_not_the_judges_wording() -> None:
    evidence = check(score(), "stated_amount").evidence[0]

    assert evidence.line == "Aapka EMI baarah hazaar paanch sau ka pending hai."


def test_fuzzy_match_that_drops_a_negation_is_not_verified() -> None:
    # partial_ratio is 91.6 here (measured): fuzzy alone would "verify" the opposite meaning.
    line = TranscriptLine(
        turn=6, role="borrower", text="Main bees tareekh tak poora payment nahi kar dunga.", t=1.0
    )
    quote = Evidence(turn=6, quote="main bees tareekh tak poora payment kar dunga")

    assert not verify_quote(quote, [line])


def test_fuzzy_match_with_the_same_negation_still_verifies() -> None:
    line = TranscriptLine(
        turn=6, role="borrower", text="Main bees tareekh tak poora payment nahi kar dunga.", t=1.0
    )
    quote = Evidence(turn=6, quote="main bees tarikh tak pura payment nahi kar dunga")

    assert verify_quote(quote, [line])


def test_flag_with_an_unknown_rule_is_rejected() -> None:
    with pytest.raises(ValidationError):
        flag("rudeness", 6, "x")


def test_blank_rewrite_is_rejected() -> None:
    judge = GOOD_JUDGE.model_copy(
        update={"worst_line": WorstLine(turn=6, quote="police bhej dunga", rewrite="   ")}
    )

    card = score(judge, transcript=LONG)

    assert card.worst_line is not None
    assert not card.worst_line.rewrite_ok


def test_judge_violation_over_a_withdrawn_flag_is_marked_as_overriding_it() -> None:
    judge = GOOD_JUDGE.model_copy(
        update={"violations": [JudgeViolation(rule="threat", turn=6, quote="police bhej dunga")]}
    )
    flags = [flag("threat", 6, "police bhej dunga", status="withdrawn", layer="llm")]

    card = score(judge, flags=flags, transcript=LONG)

    occurrence = card.compliance.deductions[0].occurrences[0]
    assert occurrence.source == "judge"
    assert occurrence.overrides_withdrawn_flag


# --- a quote across two segments of one turn (P3.3 known gap) ---

SPLIT = [
    TranscriptLine(turn=4, role="agent", text="Ramesh ji main samajh sakti hoon,", t=20.0),
    TranscriptLine(turn=4, role="agent", text="aap 4000 abhi aur baaki bees tak de dijiye", t=22.5),
    TranscriptLine(turn=4, role="borrower", text="Theek hai madam.", t=25.0),
    TranscriptLine(turn=5, role="agent", text="dhanyavaad Ramesh ji", t=30.0),
]


def test_a_quote_spanning_two_segments_of_one_turn_verifies() -> None:
    quote = Evidence(turn=4, quote="main samajh sakti hoon, aap 4000 abhi")
    line = find_quote(quote, SPLIT, speaker="agent")
    assert line is not None
    assert line.t == 20.0  # the time of the turn's first segment
    assert "samajh" in line.text and "4000" in line.text


def test_a_quote_spanning_two_turns_does_not_verify() -> None:
    quote = Evidence(turn=4, quote="bees tak de dijiye dhanyavaad Ramesh ji")
    assert find_quote(quote, SPLIT, speaker="agent") is None


def test_a_quote_spanning_agent_and_borrower_does_not_verify() -> None:
    quote = Evidence(turn=4, quote="baaki bees tak de dijiye theek hai madam")
    assert find_quote(quote, SPLIT, speaker="agent") is None
    assert find_quote(quote, SPLIT, speaker=None) is None


def test_a_long_quote_is_not_proved_by_a_short_line_inside_it() -> None:
    """Fuzzy partial matching scores a short line found inside a padded quote as 100."""
    transcript = [TranscriptLine(turn=2, role="agent", text="Aapki EMI baki hai ji", t=5.0)]
    padded = Evidence(turn=2, quote="aapki EMI baki hai ji, main aapki poori madad karungi")
    assert find_quote(padded, transcript, speaker="agent") is None


# --- trap-rule violations must fall in the engine's window (07 safeguard 6) ---

WINDOW_LINES = [
    TranscriptLine(turn=2, role="agent", text="Aapki EMI kab tak bharenge aap", t=5.0),
    TranscriptLine(turn=5, role="agent", text="Afsos hua par EMI kab tak bharenge aap", t=20.0),
]


def _window_card(rule: str, turn: int, windows: dict[str, list[tuple[int, int | None]]]) -> object:
    catalogue = load_catalogue(RULES / "catalogue.yaml")
    lexicon = LexiconTripwire(load_lexicon(RULES / "lexicon.yaml", catalogue))
    text = next(line.text for line in WINDOW_LINES if line.turn == turn)
    judge = JudgeOutput(
        checks=[],
        violations=[JudgeViolation(rule=rule, turn=turn, quote=text)],  # type: ignore[arg-type]
    )
    facts = CallFacts(promise_valid=False, promise_turn=None, revealed_turn=None,
                      rule_windows=windows)  # fmt: skip
    return score_call(judge, WINDOW_LINES, facts, [], catalogue, lexicon).compliance


def test_a_trap_violation_inside_its_window_is_deducted() -> None:
    compliance = _window_card("bereavement_pressure", 5, {"bereavement_pressure": [(5, 6)]})
    assert [d.rule for d in compliance.deductions] == ["bereavement_pressure"]  # type: ignore[attr-defined]


def test_a_trap_violation_outside_its_window_is_not_deducted() -> None:
    compliance = _window_card("bereavement_pressure", 2, {"bereavement_pressure": [(5, 6)]})
    assert compliance.deductions == []  # type: ignore[attr-defined]
    assert [v.turn for v in compliance.out_of_window] == [2]  # type: ignore[attr-defined]


def test_a_trap_rule_with_no_trap_in_the_call_is_not_deducted() -> None:
    compliance = _window_card("third_party_disclosure", 5, {"third_party_disclosure": []})
    assert compliance.deductions == []  # type: ignore[attr-defined]


def test_a_rule_without_a_window_entry_is_unconstrained() -> None:
    compliance = _window_card("repeated_pressure", 2, {})
    assert [d.rule for d in compliance.deductions] == ["repeated_pressure"]  # type: ignore[attr-defined]
