"""The brain: prompt building and streaming the borrower's reply (06 "Evon output contract").

- `build_messages` orders the prompt for prefix caching: one system message (fixed system prompt
  + persona block, both fixed per call) -> conversation history -> the agent's latest words plus
  the turn brief. Only the tail changes between turns; history stores agent words without briefs.
  One system message, because several chat templates reject or drop a second one.
- The persona block carries only facts a borrower would know in any case, plus the simulated date
  and time (needed to turn "bees November" into a correct promise date). The hidden truth and real
  means are given by the turn brief only once the reveal gate allows (`borrower_state`).
- `StateSplitter` separates spoken text from the state JSON while streaming. It never lets state
  reach speech: besides the exact `###STATE###` it switches on loose variants (`### STATE ###`)
  and on a bare `{` (borrower speech never contains braces). It strips `<think>` blocks, drops
  orphan `</think>` tags, and tolerates missing or partly invalid JSON.
- `stream_reply` drives one reply through an `LLMClient` and yields speech as it arrives.
"""

import asyncio
import json
import re
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kalpakka.borrower_state import PromiseSuggestion, StateSuggestion
from kalpakka.clock import Clock
from kalpakka.llm import LLMClient, Message
from kalpakka.personas import Persona

DELIMITER: Final = "###STATE###"
THINK_OPEN: Final = "<think>"
THINK_CLOSE: Final = "</think>"
SILENT_AGENT: Final = "(The caller said nothing.)"
FIRST_TOKEN_TIMEOUT_S: Final = 2.0  # 05 failure handling: no first token in 2 s -> filler path
REPLY_MAX_TOKENS: Final = 180  # 06: max_tokens 150-180

SplitError = Literal[
    "missing_delimiter",
    "loose_delimiter",
    "invalid_json",
    "invalid_state",
    "invalid_promise",
    "unclosed_think",
    "orphan_think_close",
]


# --- prompts -------------------------------------------------------------------------------------


class PromptError(Exception):
    pass


class Prompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: int
    text: str


_VERSION_HEADER = re.compile(r"\A\s*<!--\s*version:\s*(\d+)\s*-->\s*\n?")


def load_prompt(path: Path) -> Prompt:
    """A prompt file starts with `<!-- version: N -->`; the header is not sent to the model."""
    raw = path.read_text(encoding="utf-8")
    match = _VERSION_HEADER.match(raw)
    if match is None:
        raise PromptError(f"{path.name}: first line must be '<!-- version: N -->'")
    return Prompt(name=path.stem, version=int(match.group(1)), text=raw[match.end() :].strip())


def persona_block(persona: Persona, sim_clock: datetime) -> str:
    p, loan = persona.profile, persona.loan
    return "\n".join(
        [
            "PERSONA (fixed for this call)",
            f"You are {persona.display_name}, {p.age}, {p.occupation} in {p.city}.",
            f"You have a {loan.product} from {loan.lender}. EMI ₹{loan.emi:,}. Overdue "
            f"₹{loan.overdue_amount:,}, {loan.dpd} days past due. You promised to pay "
            f"{loan.past_promises} times before and did not.",
            f"How you speak: {persona.speech.style}.",
            f"Today is {sim_clock:%A}, {sim_clock:%Y-%m-%d}; the time is {sim_clock:%H:%M}. "
            "Use this date for any payment date you promise.",
        ]
    )


class HistoryTurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["agent", "borrower"]
    text: str  # borrower turns: only the text the trainee actually heard (barge-in trims it)


def build_messages(
    system: Prompt,
    persona: Persona,
    sim_clock: datetime,
    history: Sequence[HistoryTurn],
    agent_text: str,
    brief_text: str,
) -> list[Message]:
    messages: list[Message] = [
        {"role": "system", "content": f"{system.text}\n\n{persona_block(persona, sim_clock)}"}
    ]
    for turn in history:
        role = "user" if turn.role == "agent" else "assistant"
        messages.append({"role": role, "content": turn.text})
    spoken = agent_text.strip() or SILENT_AGENT
    messages.append({"role": "user", "content": f"{spoken}\n\n{brief_text}"})
    return messages


# --- splitting speech from state -----------------------------------------------------------------


class SplitResult(BaseModel):
    speech_tail: str = ""  # speech held back at the end of the stream
    suggestion: StateSuggestion = Field(default_factory=StateSuggestion)
    errors: list[SplitError] = Field(default_factory=list)
    think_blocks: int = 0
    raw_state: str = ""


# Earliest of: a think block, an orphan close tag, a (possibly loose) delimiter, or a bare brace.
_SPEECH_MARKERS = re.compile(
    r"(?P<think><think>)|(?P<close></think>)|(?P<delim>#{2,}\s*state\s*#{2,})|(?P<brace>\{)",
    re.IGNORECASE,
)
# A buffer tail that could still grow into a delimiter ("#", "## ST", "###STATE#").
_DELIM_TAIL = re.compile(r"#+\s*(?:s(?:t(?:a(?:t(?:e\s*#*)?)?)?)?)?\Z", re.IGNORECASE)


def _held_prefix_len(text: str, markers: Sequence[str]) -> int:
    """Length of the longest suffix of `text` that could still grow into one of `markers`."""
    best = 0
    for marker in markers:
        for n in range(min(len(marker) - 1, len(text)), 0, -1):
            if text.endswith(marker[:n]):
                best = max(best, n)
                break
    return best


class StateSplitter:
    def __init__(self) -> None:
        self._buf = ""
        self._mode: Literal["speech", "think", "state"] = "speech"
        self._state_text = ""
        self._think_blocks = 0
        self._errors: list[SplitError] = []
        self._finished = False

    @property
    def speech_done(self) -> bool:
        return self._mode == "state"

    def _current_mode(self) -> Literal["speech", "think", "state"]:
        return self._mode

    def feed(self, delta: str) -> str:
        """Return speech that is safe to speak now."""
        if self._finished:
            raise RuntimeError("splitter finished; use a new one per reply")
        self._buf += delta
        out: list[str] = []
        while self._buf:
            if self._mode == "state":
                self._state_text += self._buf
                self._buf = ""
            elif self._mode == "think":
                end = self._buf.find(THINK_CLOSE)
                if end == -1:
                    keep = _held_prefix_len(self._buf, [THINK_CLOSE])
                    self._buf = self._buf[len(self._buf) - keep :] if keep else ""
                    break
                self._buf = self._buf[end + len(THINK_CLOSE) :]
                self._think_blocks += 1
                self._mode = "speech"
            elif not self._speech_step(out):
                break
        return "".join(out)

    def _speech_step(self, out: list[str], *, final: bool = False) -> bool:
        """Consume speech up to the next marker. False when more input is needed."""
        match = _SPEECH_MARKERS.search(self._buf)
        if match is None:
            tail = _DELIM_TAIL.search(self._buf)
            keep = max(
                _held_prefix_len(self._buf, [THINK_OPEN, THINK_CLOSE]),
                len(self._buf) - tail.start() if tail else 0,
            )
            out.append(self._buf[: len(self._buf) - keep])
            self._buf = self._buf[len(self._buf) - keep :]
            return False
        kind = match.lastgroup
        if kind == "delim" and match.end() == len(self._buf) and not final:
            # "###STATE##" may still grow a '#': wait, so the exact form is not called loose.
            out.append(self._buf[: match.start()])
            self._buf = self._buf[match.start() :]
            return False
        out.append(self._buf[: match.start()])
        if kind == "think":
            self._buf = self._buf[match.end() :]
            self._mode = "think"
        elif kind == "close":
            self._buf = self._buf[match.end() :]
            self._errors.append("orphan_think_close")
        elif kind == "delim":
            if match.group(0) != DELIMITER:
                self._errors.append("loose_delimiter")
            self._buf = self._buf[match.end() :]
            self._mode = "state"
        else:  # bare brace: JSON with no delimiter; keep the brace for the parser
            self._errors.append("missing_delimiter")
            self._buf = self._buf[match.start() :]
            self._mode = "state"
        return True

    def finish(self) -> SplitResult:
        if self._finished:
            raise RuntimeError("splitter already finished")
        self._finished = True
        tail = ""
        if self._mode == "speech" and self._buf:  # resolve anything held at the edge
            out: list[str] = []
            while self._buf and self._mode == "speech" and self._speech_step(out, final=True):
                pass
            tail = "".join(out)
            mode = self._current_mode()  # _speech_step may have switched it
            if mode == "state":
                self._state_text += self._buf
            elif mode == "speech":
                tail += self._buf
            self._buf = ""
        errors = list(self._errors)
        if self._mode == "think":
            return SplitResult(
                speech_tail=tail,
                errors=[*errors, "unclosed_think"],
                think_blocks=self._think_blocks,
            )
        if self._mode == "speech":
            return SplitResult(
                speech_tail=tail,
                errors=[*errors, "missing_delimiter"],
                think_blocks=self._think_blocks,
            )
        suggestion, parse_errors = _parse_state(self._state_text)
        return SplitResult(
            speech_tail=tail,
            suggestion=suggestion,
            errors=errors + parse_errors,
            think_blocks=self._think_blocks,
            raw_state=self._state_text,
        )


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_SCALAR_FIELDS: Final = ("mood_delta", "trust_delta", "wants_to_hang_up", "agent_was_respectful")


def _parse_state(text: str) -> tuple[StateSuggestion, list[SplitError]]:
    """Field by field: one bad field falls back to its zero value, the rest still count."""
    match = _JSON_OBJECT.search(text)
    if match is None:
        return StateSuggestion(), ["invalid_json"]
    try:
        data, _ = json.JSONDecoder().raw_decode(match.group(0))
    except json.JSONDecodeError:
        return StateSuggestion(), ["invalid_json"]
    if not isinstance(data, dict):
        return StateSuggestion(), ["invalid_json"]

    errors: list[SplitError] = []
    fields: dict[str, Any] = {}  # Any: raw JSON values, each validated on its own below
    for name in _SCALAR_FIELDS:
        if name not in data:
            continue
        try:
            StateSuggestion.model_validate({name: data[name]})
        except ValidationError:
            if "invalid_state" not in errors:
                errors.append("invalid_state")
            continue
        fields[name] = data[name]
    if data.get("promise") is not None:
        try:
            fields["promise"] = PromiseSuggestion.model_validate(data["promise"])
        except ValidationError:
            errors.append("invalid_promise")
    return StateSuggestion.model_validate(fields), errors


# --- one streamed reply --------------------------------------------------------------------------


class FirstTokenTimeout(Exception):
    pass


class SpeechDelta(BaseModel):
    text: str


class SpeechDone(BaseModel):
    """The delimiter arrived: flush the chunker now, before the JSON finishes."""


class ReplyDone(BaseModel):
    result: SplitResult
    first_token_s: float | None  # first non-whitespace content; None if the stream was empty


ReplyEvent = SpeechDelta | SpeechDone | ReplyDone


async def stream_reply(
    llm: LLMClient,
    messages: Sequence[Message],
    *,
    clock: Clock,
    first_token_timeout_s: float = FIRST_TOKEN_TIMEOUT_S,
    max_tokens: int = REPLY_MAX_TOKENS,
    temperature: float = 0.8,
) -> AsyncGenerator[ReplyEvent, None]:
    """Thinking is off for live turns. Closing this generator closes the LLM stream (barge-in)."""
    splitter = StateSplitter()
    started = clock.monotonic()
    first_token_s: float | None = None
    stream = llm.stream_chat(
        messages, max_tokens=max_tokens, temperature=temperature, thinking=False
    )
    try:
        leading: list[str] = []
        while True:  # wait for the first non-whitespace content, within the time budget
            remaining = first_token_timeout_s - (clock.monotonic() - started)
            if remaining <= 0:
                raise FirstTokenTimeout(f"no first token in {first_token_timeout_s} s")
            try:
                piece = await asyncio.wait_for(anext(stream), timeout=remaining)
            except TimeoutError as exc:
                raise FirstTokenTimeout(f"no first token in {first_token_timeout_s} s") from exc
            except StopAsyncIteration:
                break
            leading.append(piece)
            if piece.strip():
                first_token_s = clock.monotonic() - started
                break

        async def rest() -> AsyncIterator[str]:
            for piece in leading:
                yield piece
            if first_token_s is not None:
                async for piece in stream:
                    yield piece

        announced_done = False
        async for piece in rest():
            speech = splitter.feed(piece)
            if speech:
                yield SpeechDelta(text=speech)
            if splitter.speech_done and not announced_done:
                announced_done = True
                yield SpeechDone()
        result = splitter.finish()
        if result.speech_tail:
            yield SpeechDelta(text=result.speech_tail)
        if not announced_done:
            yield SpeechDone()
        yield ReplyDone(result=result, first_token_s=first_token_s)
    finally:
        await stream.aclose()
