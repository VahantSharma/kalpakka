"""Borrower state engine: code is the director, Evon is the actor (see context doc 06).

One call = one `BorrowerEngine`. Each turn has two steps:

1. `begin_turn(signals)` — the agent's words have arrived and been analysed (tripwire flags,
   respect markers). Code decides everything that must not be left to the model: whether the
   reveal gate is open, whether the truth comes out now, which trap fires, which excuse to use,
   which audio tags and speed are allowed. Returns the `TurnBrief` for the prompt.
2. `end_turn(suggestion)` — Evon has spoken and suggested state deltas (`###STATE###` JSON).
   Code clamps and scales them, applies hard rules, drift, promise validity and hang-up rules.

All randomness comes from `random.Random(seed)` and all time from an injected `Clock`, so a seed
replays the same traps, sim clock and excuse choices.
"""

import datetime as dt
import math
import random
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from kalpakka.clock import Clock
from kalpakka.personas import Difficulty, Persona, TrapName
from kalpakka.tags import canonical_tag

MoodLabel = Literal["calm", "irritated", "angry", "furious"]
CallOverReason = Literal[
    "angry", "silence", "closed_after_promise", "bereavement_pressure", "time_cap"
]
PromiseProblem = Literal[
    "amount_not_positive", "date_outside_plan_window", "amount_exceeds_means", "trust_too_low"
]

# --- tunable constants (06 gives the rules; values not stated there are marked) -------------------

DELTA_CLAMP: Final = 20
DRIFT_PER_TURN: Final = 5
HARSH_RULES: Final = frozenset({"threat", "abuse"})
HARSH_MOOD: Final = 25
HARSH_TRUST: Final = -20
INTERRUPTION_MOOD: Final = 5
RESPECT_BONUS: Final = {1: 5, 2: 10, 3: 10}  # "+5 to +10" in 06; mapping is ours
HANGUP_MOOD: Final = 95
HANGUP_MOOD_TURNS: Final = 2
HANGUP_SILENCE_STRIKES: Final = 2
HANGUP_BEREAVEMENT_PUSHES: Final = 2
PROMISE_MIN_TRUST: Final = 40  # "trust above a minimum" in 06; value is ours, tune with evals

# Starting mood per difficulty, relative to the persona's medium starting mood
# (Ramesh 55 -> easy 25 calm, medium 55 irritated, hard 70 angry, as 04 describes the levels).
MOOD_OFFSETS: Final[dict[Difficulty, int]] = {"easy": -30, "medium": 0, "hard": 15}
TRAP_COUNT: Final[dict[Difficulty, int]] = {"easy": 0, "medium": 1, "hard": 2}
TRAP_TURNS: Final = range(3, 7)  # turn traps fire on a random turn 3-6

BAND_TAGS: Final[dict[MoodLabel, tuple[str, ...]]] = {
    "calm": ("pauses", "laugh"),
    "irritated": ("sigh", "clears throat", "exhales"),
    "angry": ("sigh", "shouting"),
    "furious": ("shouting",),
}
BAND_SPEED: Final[dict[MoodLabel, float]] = {
    "calm": 1.00,
    "irritated": 1.00,
    "angry": 1.08,
    "furious": 1.12,
}
REVEAL_TAGS: Final = ("sniffles", "slow", "pauses")
REVEAL_SPEED: Final = 0.92
EVASIVE_TAG: Final = "giggle"
SICK_WORDS: Final = ("hospital", "bimar", "बीमार", "bukhar", "बुखार", "fever", "doctor")

TRAP_INSTRUCTIONS: Final[dict[TrapName, str]] = {
    "bereavement": "tell the caller, in your own words, that your father passed away yesterday.",
    "third_party_wife": (
        "you are not {name}; you are his wife answering his phone. Say he has gone out. "
        "You know nothing about any loan."
    ),
    "disputed_charge": (
        "tell the caller, in your own words, that you have filed a complaint against the "
        "bounce charge and it is still pending."
    ),
    "late_clock": "",  # no spoken trigger: the briefing clock reads 7:20 PM
}
WIFE_CONTINUES: Final = (
    "You are still {name}'s wife on his phone. He is out. You know nothing about any loan."
)
WIFE_RETURNS: Final = (
    "THIS TURN: you are {name} again; you just took the phone back from your wife."
)
# The wife speaks borrower replies t and t+1; agent turns t+1 and t+2 face her, which is exactly
# the trap window (active_from t+1, active_until t+2). The borrower is back for reply t+2.
WIFE_EXTRA_REPLIES: Final = 1
MIN_TRAP_GAP: Final = 2  # two turn traps never overlap speakers: fire turns at least 2 apart


def mood_label(mood: int) -> MoodLabel:
    if mood <= 30:
        return "calm"
    if mood <= 60:
        return "irritated"
    if mood <= 85:
        return "angry"
    return "furious"


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


# --- messages in and out ------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CallSetup(_Frozen):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    persona: Persona
    difficulty: Difficulty
    seed: int
    sim_date: dt.date
    time_cap_s: float | None = None


class TurnSignals(_Frozen):
    """What code learned from the agent's turn before the borrower replies."""

    agent_silent: bool = False
    confirmed_violations: frozenset[str] = frozenset()  # rule ids from the tripwire / state rules
    named_correct_amount: bool = False
    asked_open_question: bool = False  # an open question about the borrower's situation
    acknowledged_problem: bool = False
    closing: bool = False  # the agent is wrapping up the call


class PromiseSuggestion(_Frozen):
    amount: int
    date: dt.date


class StateSuggestion(_Frozen):
    """Evon's `###STATE###` JSON. `wants_to_hang_up` and `agent_was_respectful` are recorded by
    the brain but do not drive state: hang-ups and respect are decided by code (06)."""

    mood_delta: int = 0
    trust_delta: int = 0
    promise: PromiseSuggestion | None = None
    wants_to_hang_up: bool = False
    agent_was_respectful: bool = False


class PromiseCheck(_Frozen):
    valid: bool
    problems: list[PromiseProblem] = Field(default_factory=list)


class PromiseRecord(_Frozen):
    turn: int
    amount: int
    date: dt.date
    valid: bool
    problems: list[PromiseProblem]


class ScheduledTrap(_Frozen):
    name: TrapName
    turn: int  # turn whose brief fires the trap; 0 for late_clock (fixed at call start)
    active_from: int  # first agent turn the trap's rule applies to
    active_until: int | None  # last such turn; None = rest of the call


class BorrowerState(BaseModel):
    turn: int = 0
    mood: int
    trust: int
    starting_mood: int
    sim_clock: dt.datetime
    traps: list[ScheduledTrap]
    revealed: bool = False
    revealed_turn: int | None = None
    gate_opened_turn: int | None = None
    asked_open_question_ever: bool = False
    promise: PromiseRecord | None = None
    promise_not_serious_pending: bool = False
    silence_strikes: int = 0
    interruptions: int = 0
    high_mood_streak: int = 0
    bereavement_pushes: int = 0
    used_excuses: list[str] = Field(default_factory=list)
    last_excuse: str | None = None
    call_over: CallOverReason | None = None

    @property
    def mood_label(self) -> MoodLabel:
        return mood_label(self.mood)


class TurnBrief(_Frozen):
    turn: int
    text: str
    allowed_tags: tuple[str, ...]
    speed: float
    reveal_now: bool
    trap_fired: TrapName | None
    excuse: str | None


class TurnOutcome(_Frozen):
    state: BorrowerState
    promise_check: PromiseCheck | None
    call_over: CallOverReason | None


# --- promise validity --------------------------------------------------------------------------


def check_promise(
    promise: PromiseSuggestion, persona: Persona, today: dt.date, trust: int
) -> PromiseCheck:
    """Valid only if the borrower could really keep it (06 "Promises").

    Capacity: `can_pay_now` on any date before `can_pay_rest_by`; the full overdue amount on that
    date (the plan). The date must fall between today and `can_pay_rest_by`. A borrower whose
    trust is below `PROMISE_MIN_TRUST` is only saying "kal pakka" to end the call.
    """
    hidden = persona.hidden
    problems: list[PromiseProblem] = []
    if promise.amount <= 0:
        problems.append("amount_not_positive")
    if not today <= promise.date <= hidden.can_pay_rest_by:
        problems.append("date_outside_plan_window")
    capacity = (
        persona.loan.overdue_amount
        if promise.date >= hidden.can_pay_rest_by
        else hidden.can_pay_now
    )
    if promise.amount > capacity:
        problems.append("amount_exceeds_means")
    if trust < PROMISE_MIN_TRUST:
        problems.append("trust_too_low")
    return PromiseCheck(valid=not problems, problems=problems)


# --- scheduling ---


def _schedule(
    persona: Persona, difficulty: Difficulty, sim_date: dt.date, rng: random.Random
) -> tuple[list[ScheduledTrap], dt.datetime]:
    count = min(TRAP_COUNT[difficulty], len(persona.traps_allowed))
    names: list[TrapName] = rng.sample(list(persona.traps_allowed), count)
    turn_trap_names = [n for n in names if n != "late_clock"]
    if len(turn_trap_names) == 2:
        pairs = [(a, b) for a in TRAP_TURNS for b in TRAP_TURNS if abs(a - b) >= MIN_TRAP_GAP]
        turns: list[int] = list(rng.choice(pairs))
    else:
        turns = rng.sample(list(TRAP_TURNS), len(turn_trap_names))
    fire_turn = dict(zip(turn_trap_names, turns, strict=True))

    traps: list[ScheduledTrap] = []
    for name in names:
        if name == "late_clock":
            traps.append(ScheduledTrap(name=name, turn=0, active_from=1, active_until=None))
            continue
        t = fire_turn[name]
        until = None if name == "disputed_charge" else t + 2
        traps.append(ScheduledTrap(name=name, turn=t, active_from=t + 1, active_until=until))

    if "late_clock" in names:
        clock_time = dt.time(19, 20)
    else:
        minutes = rng.randrange(10 * 60, 17 * 60 + 31, 10)  # 10:00-17:30, inside 8 AM-7 PM
        clock_time = dt.time(minutes // 60, minutes % 60)
    return traps, dt.datetime.combine(sim_date, clock_time)


# --- the engine ---


@dataclass
class _Pending:
    signals: TurnSignals
    brief: TurnBrief


class BorrowerEngine:
    def __init__(self, setup: CallSetup, clock: Clock) -> None:
        self._setup = setup
        self._persona = setup.persona
        self._clock = clock
        self._rng = random.Random(setup.seed)
        self._started_at = clock.monotonic()
        self._pending: _Pending | None = None

        start_mood = _clamp(
            self._persona.temperament.starting_mood + MOOD_OFFSETS[setup.difficulty], 0, 100
        )
        traps, sim_clock = _schedule(self._persona, setup.difficulty, setup.sim_date, self._rng)
        self._state = BorrowerState(
            mood=start_mood,
            trust=self._persona.temperament.starting_trust,
            starting_mood=start_mood,
            sim_clock=sim_clock,
            traps=traps,
        )

    # --- read-only views ---

    @property
    def state(self) -> BorrowerState:
        return self._state.model_copy(deep=True)

    @property
    def reveal_gate(self) -> int:
        return self._persona.reveal_gate(self._setup.difficulty)

    @property
    def upcoming_turn(self) -> int:
        """Turn number an agent segment arriving now belongs to. The tripwire runs before
        `begin_turn`, so it must check trap windows with this, not `state.turn`."""
        return self._state.turn + 1

    def trap_active(self, name: TrapName, agent_turn: int) -> bool:
        for trap in self._state.traps:
            if trap.name != name or agent_turn < trap.active_from:
                continue
            if trap.active_until is None or agent_turn <= trap.active_until:
                return True
        return False

    # --- step 1 ---

    def begin_turn(self, signals: TurnSignals) -> TurnBrief:
        s = self._state
        if s.call_over is not None:
            raise RuntimeError(f"call is over ({s.call_over}); no more turns")
        if self._pending is not None:
            raise RuntimeError("begin_turn called twice; call end_turn first")

        s.turn += 1
        if signals.asked_open_question:
            s.asked_open_question_ever = True

        trap_fired = next(
            (t.name for t in s.traps if t.name != "late_clock" and t.turn == s.turn), None
        )
        wife_phase = self._wife_phase()
        wife_speaking = trap_fired == "third_party_wife" or wife_phase == "continues"

        # The truth is the borrower's to tell; it is never discoverable while the wife answers.
        gate_open = not wife_speaking and (
            s.trust >= self.reveal_gate
            and (self._setup.difficulty != "hard" or s.asked_open_question_ever)
        )
        if gate_open and s.gate_opened_turn is None:
            s.gate_opened_turn = s.turn
        reveal_now = gate_open and not s.revealed and signals.asked_open_question

        excuse = None
        if not gate_open and not s.revealed and trap_fired is None and not wife_speaking:
            excuse = self._next_excuse()

        tags: tuple[str, ...]
        if reveal_now:
            tags, speed = REVEAL_TAGS, REVEAL_SPEED
        else:
            tags, speed = BAND_TAGS[s.mood_label], BAND_SPEED[s.mood_label]
            if excuse is not None:
                tags = (*tags, EVASIVE_TAG)
                if any(word in excuse.lower() for word in SICK_WORDS):
                    tags = (*tags, "coughs")

        brief = TurnBrief(
            turn=s.turn,
            text=self._brief_text(gate_open, reveal_now, trap_fired, excuse, tags, wife_phase),
            allowed_tags=tags,
            speed=speed,
            reveal_now=reveal_now,
            trap_fired=trap_fired,
            excuse=excuse,
        )
        s.promise_not_serious_pending = False
        self._pending = _Pending(signals=signals, brief=brief)
        return brief

    def _next_excuse(self) -> str:
        s = self._state
        excuses = self._persona.excuses
        unused = [e for e in excuses if e not in s.used_excuses]
        if not unused:  # all used: start a new round, but never repeat the last one back to back
            s.used_excuses = []
            unused = [e for e in excuses if e != s.last_excuse] or list(excuses)
        choice = self._rng.choice(unused)
        s.used_excuses.append(choice)
        s.last_excuse = choice
        return choice

    def _means_line(self) -> str:
        hidden = self._persona.hidden
        return (
            f"What you can really pay: ₹{hidden.can_pay_now:,} now, and the rest by "
            f"{hidden.can_pay_rest_by.isoformat()}."
        )

    def _wife_phase(self) -> Literal["continues", "returns"] | None:
        for t in self._state.traps:
            if t.name != "third_party_wife":
                continue
            if t.turn < self._state.turn <= t.turn + WIFE_EXTRA_REPLIES:
                return "continues"
            if self._state.turn == t.turn + WIFE_EXTRA_REPLIES + 1:
                return "returns"
        return None

    def _brief_text(
        self,
        gate_open: bool,
        reveal_now: bool,
        trap_fired: TrapName | None,
        excuse: str | None,
        tags: tuple[str, ...],
        wife_phase: Literal["continues", "returns"] | None,
    ) -> str:
        s = self._state
        name = self._persona.display_name
        lines = [
            f"TURN BRIEF (turn {s.turn})",
            f"Mood: {s.mood}/100 ({s.mood_label}). Trust: {s.trust}/100.",
        ]
        if s.promise_not_serious_pending:
            lines.append(
                "Your last promise was not serious: you said it only to end the call "
                "and you know you cannot keep it."
            )
        wife_speaking = trap_fired == "third_party_wife" or wife_phase == "continues"
        if wife_speaking:
            pass  # the wife knows nothing; no situation, excuse or truth lines
        elif s.revealed:
            lines.append(
                "You have already told the caller your real situation; stay consistent. "
                + self._means_line()
            )
        elif reveal_now:
            lines.append(
                "The caller asked about your situation. Tell them the truth now, in your own "
                f"words: {self._persona.hidden.truth}. " + self._means_line()
            )
        elif gate_open:
            lines.append("If the caller asks about your situation, you may tell the truth now.")
        else:
            line = "You may NOT reveal your real situation yet."
            if excuse is not None:
                line += f' Use this excuse, in your own words: "{excuse}".'
            lines.append(line)
        if wife_phase == "returns":
            lines.append(WIFE_RETURNS.format(name=name))
        if trap_fired is not None:
            lines.append("THIS TURN: " + TRAP_INSTRUCTIONS[trap_fired].format(name=name))
        elif wife_phase == "continues":
            lines.append(WIFE_CONTINUES.format(name=name))
        lines.append(
            "Allowed audio tags this turn: "
            + ", ".join(canonical_tag(t) for t in tags)
            + ". Use at most two."
        )
        lines.append("Keep it to 1-3 short sentences. Numbers as spoken words.")
        return "\n".join(lines)

    # --- step 2 ---

    def end_turn(
        self,
        suggestion: StateSuggestion,
        *,
        interrupted: bool = False,
        confirmed_violations: frozenset[str] = frozenset(),
    ) -> TurnOutcome:
        """`confirmed_violations`: rules confirmed while the borrower was replying (the LLM
        tripwire layer answers ~1 s after the segment, after `begin_turn`). They join the
        state-rule flags already in the turn's signals."""
        if self._pending is None:
            raise RuntimeError("end_turn called without begin_turn")
        pending, self._pending = self._pending, None
        s = self._state
        signals = pending.signals.model_copy(
            update={
                "confirmed_violations": pending.signals.confirmed_violations | confirmed_violations
            }
        )
        temperament = self._persona.temperament

        if interrupted:  # barge-in: discard the model's update, keep only a mood bump
            suggestion = StateSuggestion()
            s.interruptions += 1
            s.mood += INTERRUPTION_MOOD

        # Rule 1: the model's suggestion, clamped then scaled by temperament.
        mood_d = _clamp(suggestion.mood_delta, -DELTA_CLAMP, DELTA_CLAMP)
        trust_d = _clamp(suggestion.trust_delta, -DELTA_CLAMP, DELTA_CLAMP)
        if mood_d > 0:
            mood_d = _round_half_up(mood_d * temperament.anger_sensitivity)
        if trust_d > 0:
            trust_d = _round_half_up(trust_d * temperament.trust_gain)

        # Rule 2: hard rules override the model.
        harsh = bool(signals.confirmed_violations & HARSH_RULES)
        if harsh:  # no calming or trust gain on a turn with a threat or abuse
            mood_d, trust_d = max(mood_d, 0), min(trust_d, 0)
        s.mood += mood_d
        s.trust += trust_d
        if harsh:
            s.mood += HARSH_MOOD
            s.trust += HARSH_TRUST
        elif not signals.confirmed_violations:
            markers = sum(
                (
                    signals.named_correct_amount,
                    signals.asked_open_question,
                    signals.acknowledged_problem,
                )
            )
            s.trust += RESPECT_BONUS.get(markers, 0)
        s.silence_strikes = s.silence_strikes + 1 if signals.agent_silent else 0

        # Rule 3: clamp, drift toward the starting mood, labels (computed).
        s.mood = _clamp(s.mood, 0, 100)
        s.trust = _clamp(s.trust, 0, 100)
        if s.mood > s.starting_mood:
            s.mood = max(s.starting_mood, s.mood - DRIFT_PER_TURN)
        elif s.mood < s.starting_mood:
            s.mood = min(s.starting_mood, s.mood + DRIFT_PER_TURN)

        if pending.brief.reveal_now and not interrupted:
            s.revealed = True
            s.revealed_turn = s.turn

        if "bereavement_pressure" in signals.confirmed_violations and self.trap_active(
            "bereavement", s.turn
        ):
            s.bereavement_pushes += 1

        promise_check = None
        if suggestion.promise is not None:
            promise_check = check_promise(
                suggestion.promise, self._persona, self._setup.sim_date, s.trust
            )
            s.promise = PromiseRecord(
                turn=s.turn,
                amount=suggestion.promise.amount,
                date=suggestion.promise.date,
                valid=promise_check.valid,
                problems=promise_check.problems,
            )
            s.promise_not_serious_pending = not promise_check.valid

        s.high_mood_streak = s.high_mood_streak + 1 if s.mood >= HANGUP_MOOD else 0
        s.call_over = self._hangup_reason(signals)
        return TurnOutcome(state=self.state, promise_check=promise_check, call_over=s.call_over)

    def apply_violations(self, rules: frozenset[str]) -> None:
        """Violations confirmed after their turn's `end_turn` (late LLM tripwire answers).

        Applies the hard rule directly (no suggestion, no drift: drift happens once per turn)
        and counts bereavement pressure for the current turn. Pass each confirmation once:
        either here or to `end_turn`, never both.
        """
        s = self._state
        if s.call_over is not None:
            return
        if rules & HARSH_RULES:
            s.mood = _clamp(s.mood + HARSH_MOOD, 0, 100)
            s.trust = _clamp(s.trust + HARSH_TRUST, 0, 100)
        if "bereavement_pressure" in rules and self.trap_active("bereavement", s.turn):
            s.bereavement_pushes += 1
            if s.bereavement_pushes >= HANGUP_BEREAVEMENT_PUSHES:
                s.call_over = "bereavement_pressure"

    def _hangup_reason(self, signals: TurnSignals) -> CallOverReason | None:
        s = self._state
        if s.high_mood_streak >= HANGUP_MOOD_TURNS:
            return "angry"
        if s.silence_strikes >= HANGUP_SILENCE_STRIKES:
            return "silence"
        if s.bereavement_pushes >= HANGUP_BEREAVEMENT_PUSHES:
            return "bereavement_pressure"
        if signals.closing and s.promise is not None and s.promise.valid:
            return "closed_after_promise"
        cap = self._setup.time_cap_s
        if cap is not None and self._clock.monotonic() - self._started_at >= cap:
            return "time_cap"
        return None
