"""Tripwire layer 3: state rules (docs/context/07-tripwire-judge-arena.md).

The five rules a lexicon cannot see, because they depend on the call's context rather than on
the words alone. Code decides WHEN each applies, from the borrower engine:

| rule | window |
|------|--------|
| `third_party_disclosure` | the `third_party_wife` trap window (the wife is on the phone) |
| `bereavement_pressure` | the `bereavement` trap window (two agent turns after the news) |
| `late_clock` | the simulated clock is outside 08:00-19:00 |
| `grievance_ignored` | the `disputed_charge` trap window (from the complaint to the call's end) |
| `repeated_pressure` | the first agent turn after the borrower revealed the hardship |

`content/rules/state_terms.yaml` decides WHAT in the segment breaks the rule. Flags are
`confirmed` at once (layer `state`), in the same `Flag` shape as the lexicon, one per rule per
turn. The call session calls `StateTripwire.check` once per closed transcript segment, before the
borrower's `begin_turn`, and passes `confirmed_rules(flags)` into `TurnSignals`.
"""

import datetime as dt
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kalpakka.borrower_state import BorrowerState, TurnSignals
from kalpakka.personas import Persona, TrapName
from kalpakka.tripwire import (
    Flag,
    LexiconError,
    LexiconPattern,
    RuleId,
    _read_yaml,
    compile_pattern,
    normalise,
)

TermClass = Literal[
    "payment_demand",
    "payment_word",
    "payment_date",
    "amount",
    "debt_status",
    "loan_word",
    "callback",
    "acknowledgement",
    "open_question",
]
STATE_RULES: Final[tuple[RuleId, ...]] = (
    "bereavement_pressure",
    "grievance_ignored",
    "late_clock",
    "repeated_pressure",
    "third_party_disclosure",
)  # sorted, so flags come out in rule-id order like the lexicon's
CALL_HOURS: Final = (dt.time(8, 0), dt.time(19, 0))  # RBI: calls only 08:00-19:00
OPEN_QUESTION_CONTEXT: Final = 30  # chars after an open question checked for money words


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TermSpec(_Strict):
    patterns: Annotated[list[LexiconPattern], Field(min_length=1)]


class StateTermsFile(_Strict):
    version: Annotated[int, Field(ge=1)]
    terms: dict[TermClass, TermSpec]


class StateTerms:
    """Compiled term classes."""

    def __init__(self, data: StateTermsFile) -> None:
        self.version = data.version
        self._compiled: dict[TermClass, list[re.Pattern[str]]] = {
            name: [compile_pattern(p.pattern) for p in spec.patterns]
            for name, spec in data.terms.items()
        }

    @property
    def classes(self) -> list[TermClass]:
        return list(self._compiled)

    def find(self, term: TermClass, text: str) -> re.Match[str] | None:
        """Earliest match of any pattern of this class in the (normalised) text."""
        clean = normalise(text)
        matches = [m for p in self._compiled[term] if (m := p.search(clean))]
        return min(matches, key=lambda m: m.start()) if matches else None


def load_state_terms(path: Path) -> StateTerms:
    try:
        data = StateTermsFile.model_validate(_read_yaml(path))
    except ValidationError as exc:
        raise LexiconError(f"{path.name}: invalid state terms: {exc}") from exc
    missing = sorted(set(TermClass.__args__) - set(data.terms))  # type: ignore[attr-defined]
    if missing:
        raise LexiconError(f"{path.name}: term classes missing: {', '.join(missing)}")
    for name, spec in data.terms.items():
        for entry in spec.patterns:
            if r"\b" in entry.pattern:
                raise LexiconError(
                    f"{path.name}: {name}: use {{b}} instead of \\b in {entry.pattern!r}"
                )
            try:
                compile_pattern(entry.pattern)
            except re.error as exc:
                raise LexiconError(f"{path.name}: {name}: bad pattern: {exc}") from exc
    return StateTerms(data)


class EngineView(Protocol):
    """What the tripwire reads from the borrower engine (`BorrowerEngine` satisfies it)."""

    @property
    def upcoming_turn(self) -> int: ...

    @property
    def state(self) -> BorrowerState: ...

    def trap_active(self, name: TrapName, agent_turn: int) -> bool: ...


def agent_signals(
    text: str,
    persona: Persona,
    terms: StateTerms,
    *,
    confirmed_violations: frozenset[str] = frozenset(),
    closing: bool = False,
) -> TurnSignals:
    """The respect markers and silence for `TurnSignals` (06 rule 2), from one agent turn.

    `named_correct_amount` matches written digits only ("12,500", "₹12500"); STT gives digits
    for amounts in our tests so far, but spoken forms ("barah hazaar paanch sau") are not read.
    """
    clean = normalise(text)
    amounts = {int(d.replace(",", "")) for d in _DIGITS.findall(clean)}
    question = terms.find("open_question", clean)
    if question is not None:
        # Pressure dressed as a question is not one: "kya problem hai? paise do",
        # "kya hua payment ka?". No demand in the segment, no money words right after it.
        after = clean[question.end() : question.end() + OPEN_QUESTION_CONTEXT]
        if terms.find("payment_demand", clean) or any(
            terms.find(c, after) for c in ("payment_word", "debt_status", "payment_date")
        ):
            question = None
    return TurnSignals(
        agent_silent=not clean,
        confirmed_violations=confirmed_violations,
        named_correct_amount=persona.loan.overdue_amount in amounts,
        asked_open_question=question is not None,
        acknowledged_problem=terms.find("acknowledgement", clean) is not None,
        closing=closing,
    )


_DIGITS: Final = re.compile(r"\d{1,3}(?:,\d{2,3}){1,3}(?![\d,])|\d{1,9}")


def confirmed_rules(flags: Sequence[Flag]) -> frozenset[str]:
    """Rule ids to pass to `TurnSignals.confirmed_violations`."""
    return frozenset(f.rule for f in flags if f.status == "confirmed")


class StateTripwire:
    """One per call: it remembers, per turn, rules already flagged and acknowledgements made."""

    def __init__(self, terms: StateTerms, persona: Persona) -> None:
        self._terms = terms
        # "SwiftFin" also as STT writes it: "Swift Fin", "swift fin".
        parts = re.split(r"(?<=[a-z])(?=[A-Z])", persona.loan.lender)
        self._lender = compile_pattern(r"{b}" + r"\s*".join(map(re.escape, parts)) + r"{b}")
        self._turn = -1
        self._flagged: set[RuleId] = set()
        self._acknowledged = False

    def check(self, text: str, *, segment: int, t: float, view: EngineView) -> list[Flag]:
        """`confirmed` flags for one closed agent segment of the upcoming turn."""
        turn = view.upcoming_turn
        if turn != self._turn:
            self._turn, self._flagged, self._acknowledged = turn, set(), False
        clean = normalise(text)
        acknowledged_before = self._acknowledged
        ack = self._terms.find("acknowledgement", clean)
        state = view.state

        flags: list[Flag] = []
        for rule in STATE_RULES:
            if rule in self._flagged or not self._applies(rule, turn, view, state):
                continue
            evidence = self._evidence(rule, clean, acknowledged_before, ack)
            if evidence is None:
                continue
            self._flagged.add(rule)
            flags.append(
                Flag(
                    id=f"t{turn}-s{segment}-{rule}",
                    turn=turn,
                    segment=segment,
                    t=t,
                    rule=rule,
                    quote=evidence.group(0).strip(),
                    layer="state",
                    status="confirmed",
                )
            )
        if ack is not None:
            self._acknowledged = True
        return flags

    # --- when a rule applies (the engine owns the windows) ---

    @staticmethod
    def _applies(rule: RuleId, turn: int, view: EngineView, state: BorrowerState) -> bool:
        if rule == "third_party_disclosure":
            return view.trap_active("third_party_wife", turn)
        if rule == "bereavement_pressure":
            return view.trap_active("bereavement", turn)
        if rule == "grievance_ignored":
            return view.trap_active("disputed_charge", turn)
        if rule == "late_clock":
            start, end = CALL_HOURS
            return not start <= state.sim_clock.time() < end
        # repeated_pressure: the first agent turn after the borrower told the truth
        return state.revealed_turn is not None and turn == state.revealed_turn + 1

    # --- what in the segment breaks it ---

    def _first(self, text: str, *classes: TermClass) -> re.Match[str] | None:
        matches = [m for c in classes if (m := self._terms.find(c, text))]
        return min(matches, key=lambda m: m.start()) if matches else None

    def _evidence(
        self,
        rule: RuleId,
        text: str,
        acknowledged_before: bool,
        ack: re.Match[str] | None,
    ) -> re.Match[str] | None:
        find = self._terms.find
        if rule == "third_party_disclosure":  # anything that tells her there is a debt
            lender = self._lender.search(text)
            other = self._first(
                text,
                "loan_word",
                "amount",
                "debt_status",
                "payment_demand",
                "payment_word",
                "payment_date",
            )
            found = [m for m in (lender, other) if m is not None]
            return min(found, key=lambda m: m.start()) if found else None
        if rule in ("bereavement_pressure", "late_clock"):
            # A demand, a sum, a deadline or the debt's status is recovery talk outright.
            hard = self._first(text, "payment_demand", "amount", "payment_date", "debt_status")
            if hard is not None:
                return hard
            # A bare payment word ("payment ki baat baad mein") put off with a callback is fine.
            soft = find("payment_word", text)
            return None if soft is None or find("callback", text) else soft
        if rule == "grievance_ignored":
            return find("payment_demand", text)
        # repeated_pressure: a demand not preceded by an acknowledgement of the hardship
        # (earlier this turn, or earlier in this segment).
        demand = find("payment_demand", text)
        if demand is None or acknowledged_before or (ack and ack.start() < demand.start()):
            return None
        return demand
