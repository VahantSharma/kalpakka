"""Judge, code half (docs/context/07-tripwire-judge-arena.md "Judge").

The post-call LLM answers specific yes/no checks with evidence first (turn + quote). Everything
that decides the score happens here, in code:

- every quote is verified against the transcript: the cited turn, the right speaker (agent checks
  and violations quote the agent; a secured promise quotes the borrower), at least three words,
  exact or fuzzy (>= 85) for longer quotes, and a fuzzy match may not add or drop a negation.
  A "yes" without such a quote is `unverified` and earns nothing. The scorecard shows the matched
  transcript line and its time, never only the judge's paraphrase;
- `promise_valid` and `discovered_situation` come from engine state, never from the model, and a
  judge "yes" on `secured_promise` without a promise recorded by the engine is `contradicted`;
- compliance = 40 minus each broken rule's deduction (once per rule per call, every occurrence
  listed), floor 0; withdrawn or unconfirmed flags deduct nothing; a judge violation that re-raises
  a withdrawn flag is marked as overriding it, so the scorecard explains the contradiction;
- the worst-line rewrite must be non-blank and must not trip the lexicon;
- all arithmetic is in code.

The judge prompt, the live LLM call and calibration come later (P3.3 live half, P3.5).
"""

import unicodedata
from collections.abc import Sequence
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from rapidfuzz import fuzz

from kalpakka.tripwire import Catalogue, Flag, LexiconTripwire, RuleId

Section = Literal["clarity", "discovery_empathy", "outcome", "compliance"]
Speaker = Literal["agent", "borrower"]
JudgeCheckId = Literal[
    "identified_self_and_lender",
    "stated_amount",
    "confirmed_promise",
    "asked_open_question",
    "acknowledged_hardship",
    "secured_promise",
    "offered_option",
]
CheckStatus = Literal[
    "earned", "not_earned", "unverified", "contradicted", "not_applicable", "missing"
]


class CheckSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    section: Section
    points: int
    label: str
    speaker: Speaker = "agent"  # whose words prove this check


JUDGE_CHECKS: Final[dict[str, CheckSpec]] = {
    "identified_self_and_lender": CheckSpec(
        section="clarity", points=3, label="Identified self and lender early"
    ),
    "stated_amount": CheckSpec(
        section="clarity", points=3, label="Stated the overdue amount correctly"
    ),
    "confirmed_promise": CheckSpec(
        section="clarity", points=4, label="Confirmed any promise back (amount and date)"
    ),
    "asked_open_question": CheckSpec(
        section="discovery_empathy",
        points=5,
        label="Asked an open question about the borrower's situation",
    ),
    "acknowledged_hardship": CheckSpec(
        section="discovery_empathy",
        points=7,
        label="Acknowledged hardship before asking for money again",
    ),
    "secured_promise": CheckSpec(
        section="outcome", points=10, label="Secured a promise", speaker="borrower"
    ),
    "offered_option": CheckSpec(
        section="outcome",
        points=8,
        label="Offered a realistic option (part payment, plan, restructuring referral)",
    ),
}
CODE_CHECKS: Final[dict[str, CheckSpec]] = {
    "discovered_situation": CheckSpec(
        section="discovery_empathy", points=8, label="Discovered the hidden situation"
    ),
    "promise_valid": CheckSpec(
        section="outcome", points=12, label="Promise was realistic (decided by the engine)"
    ),
}
CHECK_ORDER: Final = (
    "identified_self_and_lender",
    "stated_amount",
    "confirmed_promise",
    "asked_open_question",
    "discovered_situation",
    "acknowledged_hardship",
    "secured_promise",
    "promise_valid",
    "offered_option",
)
COMPLIANCE_POINTS: Final = 40
MIN_QUOTE_WORDS: Final = 3  # "haan", "sir", "kal" prove nothing
FUZZY_MIN_RATIO: Final = 85  # 07: token-level fuzzy ratio >= 0.85
FUZZY_MIN_CHARS: Final = 20  # shorter quotes must match exactly
# Words that flip meaning; a fuzzy match may not add or drop one (Latin Hindi, English,
# Devanagari, Bengali, Tamil).
NEGATIONS: Final = frozenset(
    {"nahi", "nahin", "nahee", "na", "mat", "not", "no", "never", "dont",
     "नहीं", "ना", "मत", "না", "নয়", "இல்லை"}
)  # fmt: skip


# --- judge output (what the LLM returns; schema-enforced later) ---


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidence(_Strict):
    turn: int
    quote: str


class JudgeCheck(_Strict):
    id: JudgeCheckId
    evidence: list[Evidence]  # declared before `answer`: evidence first, then the verdict
    answer: Literal["yes", "no", "not_applicable"]


class JudgeViolation(_Strict):
    rule: RuleId
    turn: int
    quote: str


class WorstLine(_Strict):
    turn: int
    quote: str
    rewrite: str


class JudgeOutput(_Strict):
    checks: list[JudgeCheck]
    violations: list[JudgeViolation] = Field(default_factory=list)
    worst_line: WorstLine | None = None

    @model_validator(mode="after")
    def _unique_checks(self) -> Self:
        ids = [c.id for c in self.checks]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate check ids: {', '.join(dupes)}")
        return self


# --- inputs from the call ---


class TranscriptLine(_Strict):
    turn: int
    role: Speaker
    text: str  # borrower: only what the trainee heard
    t: float


class CallFacts(_Strict):
    """Ground truth from the borrower engine."""

    promise_valid: bool
    promise_turn: int | None  # turn of the latest promise (the borrower's line is the evidence)
    revealed_turn: int | None
    # Agent turns where a context rule can apply, from the engine's trap windows and sim clock:
    # rule -> [(from, until or None)]. A judge violation of a listed rule outside every window
    # deducts nothing (07 safeguard 6); an empty list means the situation never arose; a rule
    # not listed is unconstrained.
    rule_windows: dict[str, list[tuple[int, int | None]]] = Field(default_factory=dict)

    def in_window(self, rule: str, turn: int) -> bool:
        if rule not in self.rule_windows:
            return True
        return any(
            start <= turn and (until is None or turn <= until)
            for start, until in self.rule_windows[rule]
        )


# --- quote verification ---


def _norm(text: str) -> str:
    """NFC, case-folded, punctuation (incl. danda) to spaces, whitespace collapsed."""
    nfc = unicodedata.normalize("NFC", text).casefold()
    kept = "".join(c if unicodedata.category(c)[0] in "LMN" else " " for c in nfc)
    return " ".join(kept.split())


def find_quote(
    evidence: Evidence, transcript: Sequence[TranscriptLine], speaker: Speaker | None = None
) -> TranscriptLine | None:
    """The transcript line that proves this quote, or None. A turn spoken as several segments
    (one STT segment per pause) is also tried as one line: a quote may span them, but never two
    turns or two speakers. The joined line carries the time of the turn's first segment."""
    quote = _norm(evidence.quote)
    words = quote.split()
    if len(words) < MIN_QUOTE_WORDS:
        return None
    quote_negations = NEGATIONS.intersection(words)

    def matches(line: TranscriptLine) -> bool:
        text = _norm(line.text)
        if f" {quote} " in f" {text} ":
            return True
        return (
            len(quote) >= FUZZY_MIN_CHARS
            and len(quote) <= len(text)  # the quote must be found IN the line, not the reverse
            and fuzz.partial_ratio(quote, text) >= FUZZY_MIN_RATIO
            and quote_negations == NEGATIONS.intersection(text.split())
        )

    same_turn = [line for line in transcript if line.turn == evidence.turn]
    roles = [speaker] if speaker is not None else sorted({line.role for line in same_turn})
    for role in roles:
        segments = [line for line in same_turn if line.role == role]
        for line in segments:
            if matches(line):
                return line
        if len(segments) > 1:  # one turn spoken as several STT segments: try them joined
            joined = TranscriptLine(
                turn=evidence.turn,
                role=role,
                text=" ".join(line.text for line in segments),
                t=segments[0].t,
            )
            if matches(joined):
                return joined
    return None


def verify_quote(
    evidence: Evidence, transcript: Sequence[TranscriptLine], speaker: Speaker | None = None
) -> bool:
    return find_quote(evidence, transcript, speaker) is not None


# --- scorecard ---


class ScoredEvidence(_Strict):
    turn: int
    quote: str  # what the judge cited
    verified: bool
    line: str | None  # the transcript line that proves it (shown to the trainee)
    t: float | None  # time of that line


class ScoredCheck(_Strict):
    id: str
    label: str
    section: Section
    points: int
    earned: int
    status: CheckStatus
    evidence: list[ScoredEvidence]


class Occurrence(_Strict):
    turn: int
    quote: str
    source: str  # flag layer ("lexicon", "llm", "state") or "judge"
    overrides_withdrawn_flag: bool = False


class Deduction(_Strict):
    rule: RuleId
    points: int
    source: str  # source of the first occurrence
    occurrences: list[Occurrence]


class Compliance(_Strict):
    earned: int
    possible: int = COMPLIANCE_POINTS
    deductions: list[Deduction]
    unverified: list[JudgeViolation]  # judge claims not found in the agent's words
    out_of_window: list[JudgeViolation] = Field(default_factory=list)  # outside the engine's window


class SectionScore(_Strict):
    name: Section
    earned: int
    possible: int


class WorstLineResult(_Strict):
    turn: int
    quote: str
    quote_verified: bool
    rewrite: str
    rewrite_ok: bool  # False: blank, or the rewrite itself trips the lexicon; not played


class Scorecard(_Strict):
    total: int
    sections: list[SectionScore]
    checks: list[ScoredCheck]
    compliance: Compliance
    flags: list[Flag]
    worst_line: WorstLineResult | None


def _scored(
    evidence: Evidence, transcript: Sequence[TranscriptLine], speaker: Speaker
) -> ScoredEvidence:
    line = find_quote(evidence, transcript, speaker)
    return ScoredEvidence(
        turn=evidence.turn,
        quote=evidence.quote,
        verified=line is not None,
        line=None if line is None else line.text,
        t=None if line is None else line.t,
    )


def _borrower_lines(turn: int | None, transcript: Sequence[TranscriptLine]) -> list[ScoredEvidence]:
    return [
        ScoredEvidence(turn=line.turn, quote=line.text, verified=True, line=line.text, t=line.t)
        for line in transcript
        if turn is not None and line.turn == turn and line.role == "borrower"
    ]


def _check(
    spec: CheckSpec, cid: str, status: CheckStatus, evidence: list[ScoredEvidence]
) -> ScoredCheck:
    return ScoredCheck(
        id=cid, label=spec.label, section=spec.section, points=spec.points,
        earned=spec.points if status == "earned" else 0, status=status, evidence=evidence,
    )  # fmt: skip


def _score_judge_check(
    cid: str, judge: JudgeOutput, transcript: Sequence[TranscriptLine], facts: CallFacts
) -> ScoredCheck:
    spec = JUDGE_CHECKS[cid]
    answer = next((c for c in judge.checks if c.id == cid), None)
    if answer is None:
        return _check(spec, cid, "missing", [])
    evidence = [_scored(e, transcript, spec.speaker) for e in answer.evidence]
    status: CheckStatus
    if answer.answer == "yes":
        if cid == "secured_promise" and facts.promise_turn is None:
            status = "contradicted"  # the engine recorded no promise
        else:
            status = "earned" if any(e.verified for e in evidence) else "unverified"
    elif answer.answer == "no":
        status = "not_earned"
    else:
        status = "not_applicable"
    return _check(spec, cid, status, evidence)


def _score_code_check(
    cid: str, facts: CallFacts, transcript: Sequence[TranscriptLine]
) -> ScoredCheck:
    spec = CODE_CHECKS[cid]
    turn = facts.revealed_turn if cid == "discovered_situation" else facts.promise_turn
    ok = turn is not None and (cid == "discovered_situation" or facts.promise_valid)
    # Every mark cites the transcript (US-6): the borrower's line on the turn the engine recorded.
    evidence = _borrower_lines(turn, transcript) if ok else []
    return _check(spec, cid, "earned" if ok and evidence else "not_earned", evidence)


def _score_compliance(
    judge: JudgeOutput,
    transcript: Sequence[TranscriptLine],
    flags: Sequence[Flag],
    catalogue: Catalogue,
    facts: CallFacts,
) -> Compliance:
    by_rule: dict[RuleId, list[Occurrence]] = {}
    for f in flags:
        if f.status == "confirmed":
            by_rule.setdefault(f.rule, []).append(
                Occurrence(turn=f.turn, quote=f.quote, source=f.layer)
            )
    withdrawn = {(f.rule, f.turn) for f in flags if f.status == "withdrawn"}
    unverified: list[JudgeViolation] = []
    out_of_window: list[JudgeViolation] = []
    for v in judge.violations:
        if not facts.in_window(v.rule, v.turn):
            out_of_window.append(v)
        elif verify_quote(Evidence(turn=v.turn, quote=v.quote), transcript, speaker="agent"):
            by_rule.setdefault(v.rule, []).append(
                Occurrence(
                    turn=v.turn,
                    quote=v.quote,
                    source="judge",
                    overrides_withdrawn_flag=(v.rule, v.turn) in withdrawn,
                )
            )
        else:
            unverified.append(v)

    deductions = []
    for rule, occurrences in sorted(by_rule.items(), key=lambda kv: min(o.turn for o in kv[1])):
        ordered = sorted(occurrences, key=lambda o: o.turn)
        deductions.append(
            Deduction(
                rule=rule,
                points=catalogue.rules[rule].deduction,
                source=ordered[0].source,
                occurrences=ordered,
            )
        )
    earned = max(0, COMPLIANCE_POINTS - sum(d.points for d in deductions))
    return Compliance(
        earned=earned, deductions=deductions, unverified=unverified, out_of_window=out_of_window
    )


def score_call(
    judge: JudgeOutput,
    transcript: Sequence[TranscriptLine],
    facts: CallFacts,
    flags: Sequence[Flag],
    catalogue: Catalogue,
    tripwire: LexiconTripwire,
) -> Scorecard:
    checks = [
        _score_code_check(cid, facts, transcript)
        if cid in CODE_CHECKS
        else _score_judge_check(cid, judge, transcript, facts)
        for cid in CHECK_ORDER
    ]
    compliance = _score_compliance(judge, transcript, flags, catalogue, facts)

    section_names: tuple[Section, ...] = ("clarity", "discovery_empathy", "outcome")
    sections = [
        SectionScore(
            name=name,
            earned=sum(c.earned for c in checks if c.section == name),
            possible=sum(c.points for c in checks if c.section == name),
        )
        for name in section_names
    ]
    sections.append(
        SectionScore(name="compliance", earned=compliance.earned, possible=COMPLIANCE_POINTS)
    )

    worst = None
    if judge.worst_line is not None:
        w = judge.worst_line
        worst = WorstLineResult(
            turn=w.turn,
            quote=w.quote,
            quote_verified=verify_quote(
                Evidence(turn=w.turn, quote=w.quote), transcript, speaker="agent"
            ),
            rewrite=w.rewrite,
            rewrite_ok=bool(w.rewrite.strip())
            and not tripwire.check(w.rewrite, turn=w.turn, segment=0, t=0.0),
        )

    return Scorecard(
        total=sum(s.earned for s in sections),
        sections=sections,
        checks=checks,
        compliance=compliance,
        flags=list(flags),
        worst_line=worst,
    )
