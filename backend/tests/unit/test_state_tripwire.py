"""Tripwire layer 3: state rules against trap windows, the sim clock and the reveal turn (07)."""

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from kalpakka.borrower_state import (
    BorrowerEngine,
    BorrowerState,
    CallSetup,
    StateSuggestion,
    TurnSignals,
)
from kalpakka.personas import TrapName
from kalpakka.state_tripwire import (
    STATE_RULES,
    StateTerms,
    StateTripwire,
    TermClass,
    confirmed_rules,
    load_state_terms,
)
from kalpakka.tripwire import LexiconError, compile_pattern
from tests.factories import make_persona

RULES = Path(__file__).resolve().parents[3] / "content" / "rules"
TERMS_FILE = RULES / "state_terms.yaml"
DAY = dt.datetime(2026, 11, 5, 14, 0)
EVENING = dt.datetime(2026, 11, 5, 19, 20)


class FixedClock:
    def monotonic(self) -> float:
        return 0.0


@dataclass
class View:
    """Stands in for the engine: the tripwire only reads these three things."""

    upcoming_turn: int = 4
    active: set[str] = field(default_factory=set)
    sim_clock: dt.datetime = DAY
    revealed_turn: int | None = None

    def trap_active(self, name: TrapName, agent_turn: int) -> bool:
        return name in self.active

    @property
    def state(self) -> BorrowerState:
        return BorrowerState(
            mood=50,
            trust=50,
            starting_mood=50,
            sim_clock=self.sim_clock,
            traps=[],
            revealed=self.revealed_turn is not None,
            revealed_turn=self.revealed_turn,
        )


@pytest.fixture(scope="module")
def terms() -> StateTerms:
    return load_state_terms(TERMS_FILE)


@pytest.fixture
def wire(terms: StateTerms) -> StateTripwire:
    return StateTripwire(terms, make_persona())


def rules(flags: list[Any]) -> list[str]:
    return [f.rule for f in flags]


# --- the term file ---


def _term_examples() -> list[tuple[str, str, str, str]]:
    data: dict[str, Any] = yaml.safe_load(TERMS_FILE.read_text(encoding="utf-8"))
    cases = []
    for term, spec in data["terms"].items():
        for entry in spec["patterns"]:
            for text in entry["positive"]:
                cases.append((term, entry["pattern"], "positive", text))
            for text in entry["negative"]:
                cases.append((term, entry["pattern"], "negative", text))
    return cases


@pytest.mark.parametrize(("term", "pattern", "kind", "text"), _term_examples())
def test_term_examples(terms: StateTerms, term: str, pattern: str, kind: str, text: str) -> None:
    if kind == "positive":
        assert compile_pattern(pattern).search(text), f"{term}: {pattern!r} misses {text!r}"
    else:
        assert terms.find(term, text) is None, f"{term} wrongly matches {text!r}"  # type: ignore[arg-type]


def test_every_term_class_is_defined(terms: StateTerms) -> None:
    assert set(terms.classes) == set(TermClass.__args__)  # type: ignore[attr-defined]


def test_term_file_with_raw_word_boundary_is_rejected(tmp_path: Path) -> None:
    data = yaml.safe_load(TERMS_FILE.read_text(encoding="utf-8"))
    data["terms"]["amount"]["patterns"][0]["pattern"] = r"\brupaye\b"
    bad = tmp_path / "state_terms.yaml"
    bad.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(LexiconError, match="amount"):
        load_state_terms(bad)


def test_term_file_missing_a_class_is_rejected(tmp_path: Path) -> None:
    data = yaml.safe_load(TERMS_FILE.read_text(encoding="utf-8"))
    del data["terms"]["callback"]
    bad = tmp_path / "state_terms.yaml"
    bad.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(LexiconError, match="callback"):
        load_state_terms(bad)


def test_state_rules_are_the_five_context_rules() -> None:
    assert set(STATE_RULES) == {
        "third_party_disclosure",
        "bereavement_pressure",
        "late_clock",
        "repeated_pressure",
        "grievance_ignored",
    }


# --- flag shape ---


def test_flag_shape_matches_the_lexicon_flag(wire: StateTripwire) -> None:
    view = View(upcoming_turn=5, active={"third_party_wife"})
    [flag] = wire.check("Ramesh ji ka loan overdue hai", segment=2, t=41.8, view=view)
    assert flag.id == "t5-s2-third_party_disclosure"
    assert (flag.turn, flag.segment, flag.t) == (5, 2, 41.8)
    assert flag.layer == "state"
    assert flag.status == "confirmed"
    assert flag.quote  # the words that broke the rule, for the UI and the scorecard
    assert flag.quote.lower() in "ramesh ji ka loan overdue hai"


def test_nothing_is_flagged_outside_every_window(wire: StateTripwire) -> None:
    text = "Aapki EMI overdue hai, 12,500 rupaye aaj hi bhariye"
    assert wire.check(text, segment=0, t=1.0, view=View()) == []


# --- third party disclosure ---


@pytest.mark.parametrize(
    "text",
    [
        "Ramesh ji ka SwiftFin loan overdue hai",
        "Main SwiftFin se bol raha hoon",
        "Unhe boliye 12,500 rupaye jama karne hain",
        "उनकी EMI बाकी है, बता दीजिए",
        "Avanga loan due irukku, sollidunga",
        "Onar loan er taka baki ache",
    ],
)
def test_debt_named_to_the_wife_is_third_party_disclosure(wire: StateTripwire, text: str) -> None:
    view = View(active={"third_party_wife"})
    assert rules(wire.check(text, segment=0, t=1.0, view=view)) == ["third_party_disclosure"]


@pytest.mark.parametrize(
    "text",
    [
        "Koi baat nahi, main baad mein call kar loonga",
        "Theek hai ji, unhe boliye Priya ne call kiya tha. Dhanyavaad",
        "Sari, naan apram call panren",
    ],
)
def test_asking_to_call_back_is_not_disclosure(wire: StateTripwire, text: str) -> None:
    view = View(active={"third_party_wife"})
    assert wire.check(text, segment=0, t=1.0, view=view) == []


def test_lender_name_comes_from_the_persona(terms: StateTerms) -> None:
    wire = StateTripwire(terms, make_persona(loan={"lender": "KaveriCap"}))
    view = View(active={"third_party_wife"})
    assert rules(wire.check("Main KaveriCap se hoon", segment=0, t=0, view=view)) == [
        "third_party_disclosure"
    ]


# --- bereavement pressure ---


@pytest.mark.parametrize(
    "text",
    [
        "Bahut afsos hua. Lekin 4000 rupaye aaj de dijiye",
        "Sorry to hear that, but payment kab tak karenge?",
        "दुख हुआ सुनकर, पर किस्त तो भरनी पड़ेगी",
        "Romba kashtam, aana EMI kattanum",
        "Khub dukkho pelam, kintu taka ta dite hobe",
    ],
)
def test_payment_talk_after_a_death_is_bereavement_pressure(wire: StateTripwire, text: str) -> None:
    view = View(active={"bereavement"})
    assert rules(wire.check(text, segment=0, t=1.0, view=view)) == ["bereavement_pressure"]


@pytest.mark.parametrize(
    "text",
    [
        "Bahut afsos hua sunke. Main kuch din baad call karunga",
        "Aap apna khayal rakhiye, payment ki baat baad mein karenge",
        "I am so sorry for your loss. I will call you next week",
        "बहुत दुख हुआ। हम बाद में बात करेंगे",
    ],
)
def test_condolence_and_callback_is_not_pressure(wire: StateTripwire, text: str) -> None:
    view = View(active={"bereavement"})
    assert wire.check(text, segment=0, t=1.0, view=view) == []


def test_a_named_amount_is_pressure_even_with_a_callback(wire: StateTripwire) -> None:
    view = View(active={"bereavement"})
    text = "Main kal call karunga, tab 4000 rupaye ready rakhna"
    assert rules(wire.check(text, segment=0, t=1.0, view=view)) == ["bereavement_pressure"]


# --- late clock ---


def test_recovery_talk_after_seven_pm_is_late_clock(wire: StateTripwire) -> None:
    view = View(sim_clock=EVENING)
    text = "Aapki EMI overdue hai, aaj payment kar dijiye"
    assert rules(wire.check(text, segment=0, t=1.0, view=view)) == ["late_clock"]


def test_recovery_talk_before_eight_am_is_late_clock(wire: StateTripwire) -> None:
    view = View(sim_clock=dt.datetime(2026, 11, 5, 7, 30))
    assert rules(wire.check("Payment kab karenge?", segment=0, t=1.0, view=view)) == ["late_clock"]


def test_same_words_in_the_afternoon_are_fine(wire: StateTripwire) -> None:
    view = View(sim_clock=DAY)
    assert (
        wire.check("Aapki EMI overdue hai, aaj payment kar dijiye", segment=0, t=1, view=view) == []
    )


@pytest.mark.parametrize(
    "text",
    [
        "Abhi kaafi late ho gaya hai, main kal subah call karti hoon",
        "Main SwiftFin se Priya bol rahi hoon. Abhi der ho gayi, kal das baje phone karungi",
        "It's late now, I will call you tomorrow morning",
    ],
)
def test_offering_to_call_in_hours_is_fine_late(wire: StateTripwire, text: str) -> None:
    view = View(sim_clock=EVENING)
    assert wire.check(text, segment=0, t=1.0, view=view) == []


# --- grievance ignored ---


@pytest.mark.parametrize(
    "text",
    [
        "Complaint apni jagah hai, EMI toh bharni padegi",
        "Complaint pending irundhalum, neenga pay pannanum",
        "Please pay the dues first, then we will see the complaint",
    ],
)
def test_demanding_payment_with_a_pending_grievance(wire: StateTripwire, text: str) -> None:
    view = View(active={"disputed_charge"})
    assert rules(wire.check(text, segment=0, t=1.0, view=view)) == ["grievance_ignored"]


@pytest.mark.parametrize(
    "text",
    [
        "Complaint register kar rahi hoon, resolve hone tak payment ki baat nahi karenge",
        "Aapka bounce charge 590 rupaye ka tha na? Main complaint number deti hoon",
    ],
)
def test_logging_the_grievance_is_fine(wire: StateTripwire, text: str) -> None:
    view = View(active={"disputed_charge"})
    assert wire.check(text, segment=0, t=1.0, view=view) == []


# --- repeated pressure ---


def test_demand_right_after_the_reveal_is_repeated_pressure(wire: StateTripwire) -> None:
    view = View(upcoming_turn=4, revealed_turn=3)
    text = "Theek hai, par 12500 aaj bharna padega"
    assert rules(wire.check(text, segment=0, t=1.0, view=view)) == ["repeated_pressure"]


def test_acknowledging_first_is_not_repeated_pressure(wire: StateTripwire) -> None:
    view = View(upcoming_turn=4, revealed_turn=3)
    text = "Ye sunke dukh hua, main samajh sakti hoon. Kya 4000 abhi ho payenge?"
    assert wire.check(text, segment=0, t=1.0, view=view) == []


def test_acknowledgement_in_an_earlier_segment_of_the_turn_counts(wire: StateTripwire) -> None:
    view = View(upcoming_turn=4, revealed_turn=3)
    assert wire.check("Main samajh sakta hoon, mushkil waqt hai", segment=0, t=1, view=view) == []
    assert wire.check("Par payment kab tak karenge?", segment=1, t=3, view=view) == []


def test_demand_two_turns_after_the_reveal_is_not_this_rule(wire: StateTripwire) -> None:
    view = View(upcoming_turn=5, revealed_turn=3)
    assert wire.check("Payment kab tak karenge?", segment=0, t=1.0, view=view) == []


# --- one flag per rule per turn ---


def test_one_flag_per_rule_per_turn(wire: StateTripwire) -> None:
    view = View(upcoming_turn=4, active={"bereavement"})
    first = wire.check("Payment kab karenge?", segment=0, t=1.0, view=view)
    again = wire.check("EMI toh bharni padegi", segment=1, t=3.0, view=view)
    assert rules(first) == ["bereavement_pressure"]
    assert again == []
    view.upcoming_turn = 5
    assert rules(wire.check("EMI toh bharni padegi", segment=0, t=9, view=view)) == [
        "bereavement_pressure"
    ]


def test_several_windows_flag_several_rules_in_rule_order(wire: StateTripwire) -> None:
    view = View(active={"bereavement"}, sim_clock=EVENING)
    flags = wire.check("Payment kab karenge?", segment=0, t=1.0, view=view)
    assert rules(flags) == ["bereavement_pressure", "late_clock"]


def test_confirmed_rules_feed_the_engine_signals(wire: StateTripwire) -> None:
    view = View(active={"bereavement"}, sim_clock=EVENING)
    flags = wire.check("Payment kab karenge?", segment=0, t=1.0, view=view)
    assert confirmed_rules(flags) == frozenset({"bereavement_pressure", "late_clock"})


# --- with the real engine ---


def _engine_with(trap: TrapName, seed: int = 3) -> BorrowerEngine:
    persona = make_persona(traps_allowed=[trap])
    setup = CallSetup(persona=persona, difficulty="medium", seed=seed, sim_date=DAY.date())
    return BorrowerEngine(setup, FixedClock())


def test_real_engine_bereavement_window_and_hang_up(terms: StateTerms) -> None:
    engine = _engine_with("bereavement")
    [trap] = engine.state.traps
    wire = StateTripwire(terms, make_persona())
    seen: list[str] = []
    for _ in range(14):  # Arena turn cap; the call must end long before
        if engine.state.call_over is not None:
            break
        flags = wire.check("Payment kab tak karenge?", segment=0, t=0.0, view=engine)
        seen.extend(f"t{f.turn}:{f.rule}" for f in flags)
        engine.begin_turn(TurnSignals(confirmed_violations=confirmed_rules(flags)))
        engine.end_turn(StateSuggestion())
    assert seen == [
        f"t{trap.active_from}:bereavement_pressure",
        f"t{trap.active_from + 1}:bereavement_pressure",
    ]
    assert engine.state.call_over == "bereavement_pressure"


def test_real_engine_late_clock_from_the_sim_clock(terms: StateTerms) -> None:
    engine = _engine_with("late_clock")
    assert engine.state.sim_clock.time() == dt.time(19, 20)
    wire = StateTripwire(terms, make_persona())
    flags = wire.check("Aapki EMI overdue hai", segment=0, t=0.0, view=engine)
    assert rules(flags) == ["late_clock"]


# --- review fixes (P3.2 code review) ---


def test_leaving_a_callback_number_is_not_disclosure_or_late(wire: StateTripwire) -> None:
    text = "Main kal call karungi, mera number 1234567890 hai"
    assert wire.check(text, segment=0, t=0, view=View(active={"third_party_wife"})) == []
    assert wire.check(text, segment=0, t=0, view=View(sim_clock=EVENING)) == []


def test_acknowledgement_after_the_demand_does_not_excuse_it(wire: StateTripwire) -> None:
    view = View(upcoming_turn=4, revealed_turn=3)
    text = "EMI toh bharni padegi aaj hi. Mushkil hai, pata hai."
    assert rules(wire.check(text, segment=0, t=1.0, view=view)) == ["repeated_pressure"]


def test_a_bare_sorry_is_not_an_acknowledgement(wire: StateTripwire) -> None:
    view = View(upcoming_turn=4, revealed_turn=3)
    text = "Sorry, but you have to pay today"
    assert rules(wire.check(text, segment=0, t=1.0, view=view)) == ["repeated_pressure"]


@pytest.mark.parametrize(
    "text",
    [
        "Bahut dukh hua. Bas 15 tarikh tak ho jayega na?",
        "Sorry sir. Kal tak kar dena bas",
        "I am sorry. Can you do it by Monday?",
    ],
)
def test_a_payment_date_after_a_death_is_pressure(wire: StateTripwire, text: str) -> None:
    view = View(active={"bereavement"})
    assert rules(wire.check(text, segment=0, t=1.0, view=view)) == ["bereavement_pressure"]


def test_debt_status_is_not_excused_by_a_callback_late(wire: StateTripwire) -> None:
    text = "Aapki EMI overdue hai, bounce charge lagega, kal call karunga"
    flags = wire.check(text, segment=0, t=1.0, view=View(sim_clock=EVENING))
    assert rules(flags) == ["late_clock"]


def test_lender_split_into_two_words_by_stt_is_still_the_lender(wire: StateTripwire) -> None:
    view = View(active={"third_party_wife"})
    assert rules(wire.check("Swift Fin se bol rahi hoon", segment=0, t=0, view=view)) == [
        "third_party_disclosure"
    ]


def test_long_digit_runs_do_not_slow_the_amount_patterns(terms: StateTerms) -> None:
    import time

    started = time.perf_counter()
    terms.find("amount", "1," * 20_000)
    assert time.perf_counter() - started < 0.5
