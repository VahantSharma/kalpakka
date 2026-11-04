import datetime as dt
import itertools

import pytest

from kalpakka.borrower_state import (
    BorrowerEngine,
    CallSetup,
    PromiseSuggestion,
    StateSuggestion,
    TurnSignals,
    check_promise,
    mood_label,
)
from kalpakka.personas import Difficulty, Persona, TrapName
from tests.factories import make_persona

SIM_DATE = dt.date(2026, 11, 10)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def engine(
    persona: Persona | None = None,
    difficulty: Difficulty = "easy",  # easy: no traps, so rule tests are not perturbed
    seed: int = 7,
    clock: FakeClock | None = None,
    time_cap_s: float | None = None,
) -> BorrowerEngine:
    setup = CallSetup(
        persona=persona or make_persona(),
        difficulty=difficulty,
        seed=seed,
        sim_date=SIM_DATE,
        time_cap_s=time_cap_s,
    )
    return BorrowerEngine(setup, clock or FakeClock())


def turn(
    e: BorrowerEngine,
    suggestion: StateSuggestion | None = None,
    signals: TurnSignals | None = None,
    *,
    interrupted: bool = False,
) -> None:
    e.begin_turn(signals or TurnSignals())
    e.end_turn(suggestion or StateSuggestion(), interrupted=interrupted)


def calm_ramesh(**temperament: float) -> Persona:
    """Mood 50, trust 50 and neutral temperament unless overridden, so deltas are exact."""
    return make_persona(
        temperament={
            "anger_sensitivity": 1.0,
            "trust_gain": 1.0,
            "starting_mood": 50,
            "starting_trust": 50,
            **temperament,
        }
    )


def medium(persona: Persona | None = None, **kwargs: object) -> BorrowerEngine:
    return engine(persona or calm_ramesh(), difficulty="medium", **kwargs)  # type: ignore[arg-type]


# --- labels ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mood", "label"),
    [
        (0, "calm"),
        (30, "calm"),
        (31, "irritated"),
        (60, "irritated"),
        (61, "angry"),
        (85, "angry"),
        (86, "furious"),
        (100, "furious"),
    ],
)
def test_mood_label_bands(mood: int, label: str) -> None:
    assert mood_label(mood) == label


# --- starting state by difficulty ------------------------------------------------------------


@pytest.mark.parametrize(
    ("difficulty", "mood", "label"),
    [("easy", 25, "calm"), ("medium", 55, "irritated"), ("hard", 70, "angry")],
)
def test_starting_mood_follows_difficulty(difficulty: Difficulty, mood: int, label: str) -> None:
    e = engine(difficulty=difficulty)

    assert e.state.mood == mood
    assert e.state.mood_label == label
    assert e.state.trust == 25


# --- update rule 1: Evon's suggestion, clamped and scaled ------------------------------------


def test_suggested_mood_delta_is_clamped_to_20() -> None:
    e = medium(calm_ramesh())

    turn(e, StateSuggestion(mood_delta=60))

    assert e.state.mood == 50 + 20 - 5  # +20 clamp, then drift 5 back toward 50


def test_negative_suggested_trust_delta_is_clamped_to_minus_20() -> None:
    e = medium(calm_ramesh())

    turn(e, StateSuggestion(trust_delta=-45))

    assert e.state.trust == 30


def test_anger_sensitivity_scales_only_positive_mood_deltas() -> None:
    e = medium(calm_ramesh(anger_sensitivity=1.5))
    turn(e, StateSuggestion(mood_delta=10))
    up = e.state.mood

    e2 = medium(calm_ramesh(anger_sensitivity=1.5))
    turn(e2, StateSuggestion(mood_delta=-10))
    down = e2.state.mood

    assert up == 50 + 15 - 5
    assert down == 50 - 10 + 5


def test_trust_gain_scales_only_positive_trust_deltas() -> None:
    e = medium(calm_ramesh(trust_gain=0.5))
    turn(e, StateSuggestion(trust_delta=10))

    e2 = medium(calm_ramesh(trust_gain=0.5))
    turn(e2, StateSuggestion(trust_delta=-10))

    assert e.state.trust == 55
    assert e2.state.trust == 40


# --- update rule 2: hard rules override the model ------------------------------------------------


def test_confirmed_threat_raises_mood_25_and_cuts_trust_20() -> None:
    e = medium(calm_ramesh())

    turn(e, signals=TurnSignals(confirmed_violations=frozenset({"threat"})))

    assert e.state.mood == 50 + 25 - 5
    assert e.state.trust == 30


def test_threat_cancels_the_models_calming_and_trust_suggestions() -> None:
    e = medium(calm_ramesh())

    turn(
        e,
        StateSuggestion(mood_delta=-15, trust_delta=15),
        TurnSignals(confirmed_violations=frozenset({"abuse"})),
    )

    assert e.state.mood == 50 + 25 - 5
    assert e.state.trust == 30


def test_interruption_discards_the_suggestion() -> None:
    e = medium(calm_ramesh())

    turn(e, StateSuggestion(mood_delta=-20, trust_delta=20), interrupted=True)

    assert e.state.trust == 50
    assert e.state.interruptions == 1


def test_interruption_adds_5_mood_compared_with_a_plain_turn() -> None:
    # Start above the drift target so the +5 is visible: plain 70 -> 65, interrupted 70 -> 70.
    def after_one_turn(interrupted: bool) -> int:
        e = medium(calm_ramesh())
        turn(e, signals=TurnSignals(confirmed_violations=frozenset({"threat"})))  # mood 70
        turn(e, interrupted=interrupted)
        return e.state.mood

    assert after_one_turn(False) == 65
    assert after_one_turn(True) == 70


@pytest.mark.parametrize(
    ("signals", "bonus"),
    [
        (TurnSignals(named_correct_amount=True), 5),
        (TurnSignals(named_correct_amount=True, asked_open_question=True), 10),
        (
            TurnSignals(
                asked_open_question=True, acknowledged_problem=True, named_correct_amount=True
            ),
            10,
        ),
    ],
)
def test_respectful_specific_turn_adds_5_to_10_trust(signals: TurnSignals, bonus: int) -> None:
    e = medium(calm_ramesh())

    turn(e, signals=signals)

    assert e.state.trust == 50 + bonus


def test_respect_bonus_is_withheld_on_a_turn_with_a_threat() -> None:
    e = medium(calm_ramesh())

    turn(
        e,
        signals=TurnSignals(named_correct_amount=True, confirmed_violations=frozenset({"threat"})),
    )

    assert e.state.trust == 30


# --- update rule 3: clamp, drift, labels -----------------------------------------------------


def test_mood_and_trust_are_clamped_to_0_100() -> None:
    e = medium(calm_ramesh(starting_mood=95, starting_trust=95))

    turn(e, StateSuggestion(mood_delta=20, trust_delta=20))

    assert e.state.trust == 100
    assert e.state.mood == 100 - 5  # clamped at 100, then drifts toward 95 (start)


def test_mood_drifts_5_toward_start_without_overshooting() -> None:
    e = medium(calm_ramesh())

    turn(e, StateSuggestion(mood_delta=3))

    assert e.state.mood == 50


def test_label_is_recomputed_after_the_update() -> None:
    e = medium(calm_ramesh(starting_mood=58))

    turn(e, signals=TurnSignals(confirmed_violations=frozenset({"threat"})))

    assert e.state.mood == 78
    assert e.state.mood_label == "angry"


# --- silence ---------------------------------------------------------------------------------


def test_silent_agent_turns_count_consecutive_strikes() -> None:
    e = medium()

    turn(e, signals=TurnSignals(agent_silent=True))
    assert e.state.silence_strikes == 1
    turn(e, signals=TurnSignals())
    assert e.state.silence_strikes == 0


def test_two_silence_strikes_end_the_call() -> None:
    e = medium()

    turn(e, signals=TurnSignals(agent_silent=True))
    turn(e, signals=TurnSignals(agent_silent=True))

    assert e.state.call_over == "silence"


# --- reveal gate ---------------------------------------------------------------------------


@pytest.mark.parametrize(("difficulty", "gate"), [("easy", 40), ("medium", 60), ("hard", 70)])
def test_reveal_gate_threshold_per_difficulty(difficulty: Difficulty, gate: int) -> None:
    assert engine(difficulty=difficulty).reveal_gate == gate


def test_reveal_gate_stays_closed_below_threshold() -> None:
    e = medium(calm_ramesh(starting_trust=59))

    brief = e.begin_turn(TurnSignals(asked_open_question=True))

    assert not brief.reveal_now
    assert "may NOT reveal" in brief.text


def test_reveal_gate_opens_at_threshold_and_records_the_turn() -> None:
    e = medium(calm_ramesh(starting_trust=60))

    brief = e.begin_turn(TurnSignals())

    assert e.state.gate_opened_turn == 1
    assert "may tell the truth" in brief.text
    assert not brief.reveal_now  # open, but the caller has not asked about the situation


def test_truth_comes_out_when_gate_open_and_caller_asks() -> None:
    e = medium(calm_ramesh(starting_trust=60))

    brief = e.begin_turn(TurnSignals(asked_open_question=True))
    e.end_turn(StateSuggestion())

    assert brief.reveal_now
    assert "engine repair" in brief.text
    assert e.state.revealed
    assert e.state.revealed_turn == 1


def test_hard_gate_needs_an_open_question_even_above_threshold() -> None:
    e = engine(calm_ramesh(starting_trust=80), difficulty="hard")

    brief = e.begin_turn(TurnSignals())

    assert e.state.gate_opened_turn is None
    assert "may NOT reveal" in brief.text


def test_hard_gate_opens_once_an_open_question_has_been_asked() -> None:
    e = engine(calm_ramesh(starting_trust=80), difficulty="hard", seed=1)

    turn(e, signals=TurnSignals(asked_open_question=True))

    assert e.state.gate_opened_turn == 1


def test_reveal_turn_uses_reveal_tags_and_slower_speed() -> None:
    e = medium(calm_ramesh(starting_trust=60))

    brief = e.begin_turn(TurnSignals(asked_open_question=True))

    assert set(brief.allowed_tags) == {"sniffles", "slow", "pauses"}
    assert brief.speed == pytest.approx(0.92)


# --- excuses -------------------------------------------------------------------------------


def test_below_gate_the_brief_offers_an_unused_excuse() -> None:
    e = engine(calm_ramesh(starting_trust=10), difficulty="easy")
    seen = []
    for _ in range(4):
        brief = e.begin_turn(TurnSignals())
        assert brief.excuse is not None
        seen.append(brief.excuse)
        e.end_turn(StateSuggestion())

    assert sorted(seen) == sorted(make_persona().excuses)


@pytest.mark.parametrize("seed", range(40))
def test_excuses_restart_without_repeating_the_last_one(seed: int) -> None:
    e = engine(calm_ramesh(starting_trust=10), difficulty="easy", seed=seed)
    picks = []
    for _ in range(12):
        picks.append(e.begin_turn(TurnSignals()).excuse)
        e.end_turn(StateSuggestion())

    assert None not in picks
    assert all(a != b for a, b in itertools.pairwise(picks))


def test_hospital_excuse_allows_a_cough() -> None:
    persona = make_persona(excuses=["main abhi hospital mein hoon"])
    e = medium(persona)

    brief = e.begin_turn(TurnSignals())

    assert "coughs" in brief.allowed_tags


# --- tags and speed by mood band ----------------------------------------------------------


@pytest.mark.parametrize(
    ("start_mood", "tags", "speed"),
    [
        (20, {"pauses", "laugh"}, 1.00),
        (50, {"sigh", "clears throat", "exhales"}, 1.00),
        (70, {"sigh", "shouting"}, 1.08),
        (90, {"shouting"}, 1.12),
    ],
)
def test_mood_band_sets_allowed_tags_and_speed(
    start_mood: int, tags: set[str], speed: float
) -> None:
    persona = calm_ramesh(starting_mood=start_mood)
    e = engine(persona.model_copy(update={"excuses": ["kal pakka"]}), difficulty="medium")

    brief = e.begin_turn(TurnSignals())

    assert tags <= set(brief.allowed_tags)
    assert brief.speed == pytest.approx(speed)
    assert "Use at most two" in brief.text


# --- traps ---------------------------------------------------------------------------------


def test_easy_calls_have_no_traps() -> None:
    assert engine(difficulty="easy").state.traps == []


@pytest.mark.parametrize(("difficulty", "count"), [("medium", 1), ("hard", 2)])
def test_trap_count_by_difficulty(difficulty: Difficulty, count: int) -> None:
    for seed in range(30):
        assert len(engine(difficulty=difficulty, seed=seed).state.traps) == count


def test_same_seed_gives_same_trap_schedule_and_excuses() -> None:
    def run(seed: int) -> tuple[object, list[str | None]]:
        e = engine(difficulty="hard", seed=seed)
        excuses = []
        for _ in range(3):
            excuses.append(e.begin_turn(TurnSignals()).excuse)
            e.end_turn(StateSuggestion())
        return e.state.traps, excuses

    assert run(42) == run(42)


def test_different_seeds_vary_the_schedule() -> None:
    schedules = {repr(engine(difficulty="hard", seed=s).state.traps) for s in range(30)}

    assert len(schedules) > 1


def test_turn_traps_fire_on_turns_3_to_6_and_only_allowed_traps() -> None:
    for seed in range(50):
        for trap in engine(difficulty="hard", seed=seed).state.traps:
            assert trap.name in {"bereavement", "third_party_wife", "late_clock"}
            if trap.name != "late_clock":
                assert 3 <= trap.turn <= 6


def test_two_turn_traps_never_share_a_turn() -> None:
    persona = make_persona(traps_allowed=["bereavement", "third_party_wife"])
    for seed in range(50):
        traps = engine(persona, difficulty="hard", seed=seed).state.traps
        assert traps[0].turn != traps[1].turn


def _engine_with_trap(name: TrapName) -> BorrowerEngine:
    persona = make_persona(traps_allowed=[name])
    return engine(persona, difficulty="medium", seed=3)


def test_late_clock_trap_sets_sim_clock_to_7_20_pm_for_the_whole_call() -> None:
    e = _engine_with_trap("late_clock")

    trap = e.state.traps[0]
    assert e.state.sim_clock == dt.datetime(2026, 11, 10, 19, 20)
    assert (trap.active_from, trap.active_until) == (1, None)


def test_without_late_clock_sim_clock_is_inside_8am_to_7pm() -> None:
    for seed in range(30):
        clock = engine(difficulty="easy", seed=seed).state.sim_clock
        assert clock.date() == SIM_DATE
        assert dt.time(8, 0) <= clock.time() < dt.time(19, 0)


def test_bereavement_trap_fires_through_the_brief_on_its_turn() -> None:
    e = _engine_with_trap("bereavement")
    trap = e.state.traps[0]

    briefs = []
    for _ in range(trap.turn):
        briefs.append(e.begin_turn(TurnSignals()))
        e.end_turn(StateSuggestion())

    assert briefs[-1].trap_fired == "bereavement"
    assert "passed away" in briefs[-1].text
    assert all(b.trap_fired is None for b in briefs[:-1])
    assert (trap.active_from, trap.active_until) == (trap.turn + 1, trap.turn + 2)


def test_third_party_wife_trap_brief_switches_speaker() -> None:
    e = _engine_with_trap("third_party_wife")
    trap = e.state.traps[0]

    for _ in range(trap.turn - 1):
        turn(e)
    brief = e.begin_turn(TurnSignals())

    assert brief.trap_fired == "third_party_wife"
    assert "wife" in brief.text


def test_trap_active_reports_the_window() -> None:
    e = _engine_with_trap("bereavement")
    trap = e.state.traps[0]

    assert not e.trap_active("bereavement", trap.turn)
    assert e.trap_active("bereavement", trap.turn + 1)
    assert e.trap_active("bereavement", trap.turn + 2)
    assert not e.trap_active("bereavement", trap.turn + 3)


def test_bereavement_pushed_twice_ends_the_call() -> None:
    e = _engine_with_trap("bereavement")
    trap = e.state.traps[0]
    for _ in range(trap.turn):
        turn(e)

    push = TurnSignals(confirmed_violations=frozenset({"bereavement_pressure"}))
    turn(e, signals=push)
    assert e.state.call_over is None
    turn(e, signals=push)

    assert e.state.call_over == "bereavement_pressure"


# --- promises ------------------------------------------------------------------------------


def _check(amount: int, date: dt.date, trust: int = 60) -> list[str]:
    return check_promise(
        PromiseSuggestion(amount=amount, date=date), make_persona(), SIM_DATE, trust
    ).problems


def test_part_payment_within_means_before_plan_date_is_valid() -> None:
    assert _check(4000, dt.date(2026, 11, 12)) == []


def test_full_amount_on_plan_date_is_valid() -> None:
    assert _check(12500, dt.date(2026, 11, 20)) == []


def test_amount_above_means_before_plan_date_is_invalid() -> None:
    assert _check(12500, dt.date(2026, 11, 11)) == ["amount_exceeds_means"]


def test_date_after_plan_window_is_invalid() -> None:
    assert _check(4000, dt.date(2026, 12, 1)) == ["date_outside_plan_window"]


def test_date_in_the_past_is_invalid() -> None:
    assert _check(4000, dt.date(2026, 11, 9)) == ["date_outside_plan_window"]


def test_promise_from_an_untrusting_borrower_is_not_serious() -> None:
    assert _check(4000, dt.date(2026, 11, 12), trust=20) == ["trust_too_low"]


def test_zero_or_negative_amount_is_invalid() -> None:
    assert "amount_not_positive" in _check(0, dt.date(2026, 11, 12))


def test_invalid_promise_is_stored_and_the_next_brief_says_it_was_not_serious() -> None:
    e = medium(calm_ramesh(starting_trust=20))

    e.begin_turn(TurnSignals())
    outcome = e.end_turn(
        StateSuggestion(promise=PromiseSuggestion(amount=12500, date=dt.date(2026, 11, 11)))
    )
    brief = e.begin_turn(TurnSignals())

    assert outcome.promise_check is not None
    assert not outcome.promise_check.valid
    assert e.state.promise is not None
    assert not e.state.promise.valid
    assert "not serious" in brief.text


def test_valid_promise_then_closing_ends_the_call() -> None:
    e = medium(calm_ramesh(starting_trust=60))

    turn(e, StateSuggestion(promise=PromiseSuggestion(amount=4000, date=dt.date(2026, 11, 12))))
    assert e.state.promise is not None and e.state.promise.valid
    turn(e, signals=TurnSignals(closing=True))

    assert e.state.call_over == "closed_after_promise"


def test_closing_without_a_valid_promise_does_not_end_the_call() -> None:
    e = medium()

    turn(e, signals=TurnSignals(closing=True))

    assert e.state.call_over is None


# --- other hang-ups ------------------------------------------------------------------------


def test_mood_95_two_turns_in_a_row_ends_the_call() -> None:
    e = medium(calm_ramesh(starting_mood=100))
    threat = TurnSignals(confirmed_violations=frozenset({"threat"}))

    turn(e, signals=threat)
    assert e.state.call_over is None
    turn(e, signals=threat)

    assert e.state.call_over == "angry"


def test_time_cap_ends_the_call() -> None:
    clock = FakeClock()
    e = medium(clock=clock, time_cap_s=300)

    turn(e)
    assert e.state.call_over is None
    clock.now += 301
    turn(e)

    assert e.state.call_over == "time_cap"


def test_turns_after_the_call_is_over_are_refused() -> None:
    e = medium()
    turn(e, signals=TurnSignals(agent_silent=True))
    turn(e, signals=TurnSignals(agent_silent=True))

    with pytest.raises(RuntimeError, match="call is over"):
        e.begin_turn(TurnSignals())


def test_end_turn_without_begin_turn_is_refused() -> None:
    with pytest.raises(RuntimeError, match="begin_turn"):
        medium().end_turn(StateSuggestion())


# --- brief ------------------------------------------------------------------------------------


def test_brief_states_turn_mood_trust_and_length_rule() -> None:
    e = medium(calm_ramesh())

    text = e.begin_turn(TurnSignals()).text

    assert text.startswith("TURN BRIEF (turn 1)")
    assert "Mood: 50/100 (irritated). Trust: 50/100." in text
    assert "1-3 short sentences" in text
    assert "Numbers as spoken words" in text


def test_state_snapshot_is_a_copy() -> None:
    e = medium()
    snapshot = e.state
    turn(e, signals=TurnSignals(agent_silent=True))

    assert snapshot.silence_strikes == 0


# --- review fixes: violation timing, wife window, trap spacing, coverage gaps -----------------


def test_threat_confirmed_after_begin_turn_still_applies_at_end_turn() -> None:
    # The LLM tripwire confirms threats ~1 s later, after the brief is built (07).
    e = medium(calm_ramesh())

    e.begin_turn(TurnSignals())
    e.end_turn(StateSuggestion(), confirmed_violations=frozenset({"threat"}))

    assert e.state.mood == 70
    assert e.state.trust == 30


def test_violation_confirmed_between_turns_applies_the_hard_rule_once() -> None:
    e = medium(calm_ramesh())
    turn(e)

    e.apply_violations(frozenset({"abuse"}))

    assert e.state.mood == 75
    assert e.state.trust == 30


def test_late_bereavement_pressure_counts_toward_the_hang_up() -> None:
    e = _engine_with_trap("bereavement")
    trap = e.state.traps[0]
    for _ in range(trap.turn + 1):
        turn(e)
    e.apply_violations(frozenset({"bereavement_pressure"}))
    turn(e, signals=TurnSignals(confirmed_violations=frozenset({"bereavement_pressure"})))

    assert e.state.call_over == "bereavement_pressure"


def test_upcoming_turn_is_the_turn_the_next_agent_segment_belongs_to() -> None:
    e = medium()
    turn(e)

    assert e.upcoming_turn == 2


def test_wife_speaks_on_the_trap_turn_and_one_more_then_borrower_returns() -> None:
    e = _engine_with_trap("third_party_wife")
    t = e.state.traps[0].turn
    for _ in range(t - 1):
        turn(e)

    fire = e.begin_turn(TurnSignals())
    e.end_turn(StateSuggestion())
    still = e.begin_turn(TurnSignals())
    e.end_turn(StateSuggestion())
    back = e.begin_turn(TurnSignals())

    assert "you are his wife" in fire.text
    assert "still" in still.text and "wife" in still.text
    assert "took the phone back" in back.text


def test_third_party_window_covers_every_agent_turn_that_faces_the_wife() -> None:
    e = _engine_with_trap("third_party_wife")
    t = e.state.traps[0].turn

    assert [e.trap_active("third_party_wife", n) for n in (t, t + 1, t + 2, t + 3)] == [
        False,
        True,
        True,
        False,
    ]


def test_while_the_wife_is_on_the_line_there_is_no_excuse_or_reveal_line() -> None:
    e = _engine_with_trap("third_party_wife")
    t = e.state.traps[0].turn
    for _ in range(t - 1):
        turn(e)

    for brief in (e.begin_turn(TurnSignals(asked_open_question=True)),):
        assert brief.excuse is None
        assert not brief.reveal_now
        assert "reveal" not in brief.text.lower()
        assert "truth" not in brief.text.lower()


def test_hard_turn_traps_are_at_least_two_turns_apart() -> None:
    persona = make_persona(traps_allowed=["bereavement", "third_party_wife"])
    for seed in range(100):
        a, b = engine(persona, difficulty="hard", seed=seed).state.traps
        assert abs(a.turn - b.turn) >= 2


def test_disputed_charge_window_runs_to_the_end_of_the_call() -> None:
    e = _engine_with_trap("disputed_charge")
    t = e.state.traps[0].turn

    assert (e.state.traps[0].active_from, e.state.traps[0].active_until) == (t + 1, None)
    assert e.trap_active("disputed_charge", t + 30)


def test_mood_95_streak_reached_through_drift_ends_the_call() -> None:
    e = medium(calm_ramesh(starting_mood=90))
    threat = TurnSignals(confirmed_violations=frozenset({"threat"}))

    turn(e, signals=threat)  # 90 + 25 -> 100 (clamp) -> 95 (drift)
    assert e.state.mood == 95
    assert e.state.call_over is None
    turn(e, signals=threat)

    assert e.state.call_over == "angry"


def test_reveal_brief_tells_the_borrower_what_they_can_really_pay() -> None:
    e = medium(calm_ramesh(starting_trust=60))

    brief = e.begin_turn(TurnSignals(asked_open_question=True))

    assert "₹4,000" in brief.text
    assert "2026-11-20" in brief.text


def test_after_reveal_the_brief_keeps_the_real_means_consistent() -> None:
    e = medium(calm_ramesh(starting_trust=60))
    turn(e, signals=TurnSignals(asked_open_question=True))

    brief = e.begin_turn(TurnSignals())

    assert "already told" in brief.text
    assert "₹4,000" in brief.text


def test_cancel_turn_undoes_a_reveal_turn_that_was_never_heard() -> None:
    e = medium(calm_ramesh(starting_trust=60))
    before = e.state

    brief = e.begin_turn(TurnSignals(asked_open_question=True))
    assert brief.reveal_now
    e.cancel_turn()

    assert e.state == before
    assert not e.state.revealed
    again = e.begin_turn(TurnSignals(asked_open_question=True))  # the reveal can still happen
    assert again.reveal_now
    assert again.turn == 1


def test_cancel_turn_without_begin_turn_is_an_error() -> None:
    with pytest.raises(RuntimeError):
        engine().cancel_turn()
