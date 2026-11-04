"""Judge, live half (docs/context/07 "Judge"): build the judge's input, call the LLM, parse.

`build_judge_messages` gives the judge everything code knows and the trainee did not: the hidden
facts, the trap windows, when the reveal gate opened, the engine's promise verdict, the mood and
trust history, every live flag, the rule catalogue, the transcript with turns and times, and the
output JSON schema. `run_judge` calls the LLM with thinking on (07: post-call, Evon with thinking)
and, through `JsonSchemaAdapter`, the OpenAI-standard `response_format` json_schema; it parses the
reply into `JudgeOutput` and hands it to `judge.score_call`, which verifies quotes and does all
arithmetic. Malformed output never raises past `run_judge`: it comes back as `JudgeRun.error`.

Which structured-output method the host honours (and whether thinking can be on at the same time)
is a P0.5 finding; until then `structured` and `thinking` are parameters, both on by default, and
the schema is also written into the prompt so a host that ignores `response_format` still works.
"""

import datetime as dt
import json
import re
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from kalpakka.borrower_state import MoodLabel, PromiseRecord, ScheduledTrap
from kalpakka.brain import Prompt
from kalpakka.judge import CallFacts, JudgeOutput, Scorecard, TranscriptLine, score_call
from kalpakka.llm import LLMClient, LLMError, Message, ServerAdapter
from kalpakka.personas import Difficulty, Persona
from kalpakka.state_tripwire import CALL_HOURS
from kalpakka.tripwire import Catalogue, Flag, LexiconTripwire

JUDGE_MAX_TOKENS: Final = 4096  # thinking + a nine-check JSON; tune with P0.5's token findings
JUDGE_TEMPERATURE: Final = 0.0
JudgeErrorReason = Literal["no_json", "invalid_json", "invalid_schema", "llm_error"]
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
# The context rule each trap creates (the late clock is read from the sim clock instead).
TRAP_RULES: Final = {
    "bereavement": "bereavement_pressure",
    "third_party_wife": "third_party_disclosure",
    "disputed_charge": "grievance_ignored",
}


class JudgeError(Exception):
    def __init__(self, reason: JudgeErrorReason, message: str, raw: str) -> None:
        super().__init__(message)
        self.reason: JudgeErrorReason = reason
        self.raw = raw


class JsonSchemaAdapter:
    """Wraps a server adapter and asks for JSON matching `model` (default `JudgeOutput`) through
    OpenAI `response_format` json_schema, which vLLM and SGLang accept. Server fields are kept."""

    def __init__(
        self,
        inner: ServerAdapter,
        *,
        name: str = "judge_output",
        model: type[BaseModel] = JudgeOutput,
    ) -> None:
        self._inner = inner
        self._name = name
        self._schema = model.model_json_schema()

    def extra_body(self, *, thinking: bool) -> dict[str, Any]:
        body = dict(self._inner.extra_body(thinking=thinking))
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": self._name, "schema": self._schema},
        }
        return body


class StateSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    turn: int
    mood: int
    trust: int
    mood_label: MoodLabel


class JudgeInput(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    persona: Persona
    difficulty: Difficulty
    sim_clock: dt.datetime
    transcript: list[TranscriptLine]
    traps: list[ScheduledTrap]
    gate_opened_turn: int | None
    revealed_turn: int | None
    promise: PromiseRecord | None
    state_history: list[StateSnapshot]
    flags: list[Flag]

    def facts(self) -> CallFacts:
        windows: dict[str, list[tuple[int, int | None]]] = {
            rule: [(t.active_from, t.active_until) for t in self.traps if t.name == trap]
            for trap, rule in TRAP_RULES.items()
        }
        start, end = CALL_HOURS
        in_hours = start <= self.sim_clock.time() < end
        windows["late_clock"] = [] if in_hours else [(1, None)]
        return CallFacts(
            promise_valid=self.promise is not None and self.promise.valid,
            promise_turn=None if self.promise is None else self.promise.turn,
            revealed_turn=self.revealed_turn,
            rule_windows=windows,
        )


def _trap_line(trap: ScheduledTrap) -> str:
    until = "the end of the call" if trap.active_until is None else str(trap.active_until)
    when = "set at call start" if trap.turn == 0 else f"fired on borrower turn {trap.turn}"
    span = (
        f"turns {trap.active_from}-{trap.active_until}"
        if trap.active_until is not None
        else f"turn {trap.active_from} to {until}"
    )
    return f"- {trap.name}: {when}; its rule applies to agent {span}"


def render_input(data: JudgeInput, catalogue: Catalogue) -> str:
    p, loan, hidden = data.persona, data.persona.loan, data.persona.hidden
    start, end = CALL_HOURS
    lines = [
        "CALL",
        f"Borrower: {p.display_name} ({p.language}), {p.profile.occupation}, {p.profile.city}. "
        f"Difficulty: {data.difficulty}.",
        f"Loan: {loan.lender} {loan.product}, EMI ₹{loan.emi:,}, overdue ₹{loan.overdue_amount:,}"
        f" (the correct amount to state), {loan.dpd} days past due.",
        f"Simulated time of the call: {data.sim_clock:%A %H:%M} (calls allowed "
        f"{start:%H:%M}-{end:%H:%M}).",
        "",
        "HIDDEN FACTS (code-owned; the agent was not told these)",
        f"Real situation: {hidden.truth}.",
        f"Real means: ₹{hidden.can_pay_now:,} now; the full overdue by "
        f"{hidden.can_pay_rest_by.isoformat()}.",
        "Reveal: "
        + (
            f"gate opened on turn {data.gate_opened_turn}"
            if data.gate_opened_turn is not None
            else "gate never opened"
        )
        + "; "
        + (
            f"revealed on turn {data.revealed_turn}"
            if data.revealed_turn is not None
            else "never revealed"
        )
        + ".",
        "",
        "TRAPS",
        *([_trap_line(t) for t in data.traps] or ["- none"]),
        "",
        "PROMISE",
        (
            f"₹{data.promise.amount:,} on {data.promise.date.isoformat()} (borrower turn "
            f"{data.promise.turn}): {'valid' if data.promise.valid else 'not valid'} (decided by "
            "code"
            + (f": {', '.join(data.promise.problems)}" if data.promise.problems else "")
            + ")"
            if data.promise is not None
            else "none recorded"
        ),
        "",
        "STATE AFTER EACH TURN",
        *[f"T{s.turn} mood {s.mood} ({s.mood_label}) trust {s.trust}" for s in data.state_history],
        "",
        "LIVE FLAGS (hints, not verdicts)",
        *(
            [f'T{f.turn} {f.layer} {f.status} {f.rule}: "{f.quote}"' for f in data.flags]
            or ["- none"]
        ),
        "",
        "RULES",
        *[f"- {rule}: {spec.meaning}" for rule, spec in catalogue.rules.items()],
        "",
        "TRANSCRIPT",
        *[f"T{t.turn} {t.role} ({t.t:.1f} s): {t.text}" for t in data.transcript],
        "",
        "SCHEMA",
        json.dumps(JudgeOutput.model_json_schema(), ensure_ascii=False),
    ]
    return "\n".join(lines)


def build_judge_messages(prompt: Prompt, data: JudgeInput, catalogue: Catalogue) -> list[Message]:
    return [
        {"role": "system", "content": prompt.text},
        {"role": "user", "content": render_input(data, catalogue)},
    ]


def parse_judge_output(raw: str) -> JudgeOutput:
    text = _THINK.sub("", raw)
    start = text.find("{")
    if start == -1:
        raise JudgeError("no_json", "the judge returned no JSON object", raw)
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise JudgeError("invalid_json", f"the judge returned invalid JSON: {exc}", raw) from exc
    try:
        return JudgeOutput.model_validate(data)
    except ValidationError as exc:
        raise JudgeError(
            "invalid_schema", f"the judge's JSON does not match the schema: {exc}", raw
        ) from exc


class JudgeRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    scorecard: Scorecard | None
    output: JudgeOutput | None
    error: str | None
    error_reason: JudgeErrorReason | None
    raw: str
    prompt_version: int


async def run_judge(
    llm: LLMClient,
    prompt: Prompt,
    data: JudgeInput,
    *,
    catalogue: Catalogue,
    tripwire: LexiconTripwire,
    thinking: bool = True,
    max_tokens: int = JUDGE_MAX_TOKENS,
) -> JudgeRun:
    """Score one finished call. `llm` should carry a `JsonSchemaAdapter` for structured output."""
    messages = build_judge_messages(prompt, data, catalogue)
    pieces: list[str] = []
    try:
        async for piece in llm.stream_chat(
            messages, max_tokens=max_tokens, temperature=JUDGE_TEMPERATURE, thinking=thinking
        ):
            pieces.append(piece)
    except LLMError as exc:
        return _failed("llm_error", str(exc), "".join(pieces), prompt)
    raw = "".join(pieces)
    try:
        output = parse_judge_output(raw)
    except JudgeError as exc:
        return _failed(exc.reason, str(exc), raw, prompt)
    card = score_call(output, data.transcript, data.facts(), data.flags, catalogue, tripwire)
    return JudgeRun(
        scorecard=card, output=output, error=None, error_reason=None, raw=raw,
        prompt_version=prompt.version,
    )  # fmt: skip


def _failed(reason: JudgeErrorReason, message: str, raw: str, prompt: Prompt) -> JudgeRun:
    return JudgeRun(
        scorecard=None, output=None, error=message, error_reason=reason, raw=raw,
        prompt_version=prompt.version,
    )  # fmt: skip
