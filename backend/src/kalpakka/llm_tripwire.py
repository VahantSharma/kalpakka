"""Tripwire layer 2: a short LLM check with context (docs/context/07-tripwire-judge-arena.md).

Per closed agent segment, with the last three turns as context, the LLM answers one JSON object
(`violation`, `rule`, `confidence`, `quote`). Code turns that into flag updates:

- a lexicon `possible` flag of the same rule, confidence >= threshold -> `confirmed` (same id
  and layer, so the scorecard still shows the lexicon raised it);
- "no violation", confidence >= threshold -> each lexicon flag of the segment -> `withdrawn`;
- a violation the lexicon did not raise -> a new `confirmed` flag (layer `llm`), only if its quote
  is really in the segment and the catalogue lets the llm layer raise that rule;
- low confidence, malformed JSON, a server error or a timeout -> no update (flags stay possible).

It never blocks the turn: the call session runs `check` as a background task it owns, and passes
newly confirmed rules to the engine (`end_turn(confirmed_violations=...)` if the reply has not
finished, else `apply_violations`). Thinking off, temperature 0, a small token budget, a timeout.
"""

import asyncio
import json
import re
from collections.abc import Sequence
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kalpakka.brain import Prompt
from kalpakka.judge import Evidence, TranscriptLine, find_quote
from kalpakka.llm import LLMClient, LLMError, Message
from kalpakka.tripwire import Catalogue, Flag, RuleId

CONTEXT_TURNS: Final = 3
CHECK_MAX_TOKENS: Final = 120
CHECK_TIMEOUT_S: Final = 3.0  # 07 budgets ~1 s; the timeout only stops a stuck check
CONFIDENCE_THRESHOLD: Final = 0.7  # ours; tune with the frozen set's precision/recall
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMCheck(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    violation: bool
    rule: RuleId | None = None
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    quote: str = ""


class TripwireVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    updates: list[Flag]
    check: LLMCheck | None
    error: str | None  # "timeout", a server error, or why the JSON was rejected


def _parse(raw: str) -> LLMCheck:
    text = _THINK.sub("", raw)
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object")
    data, _ = json.JSONDecoder().raw_decode(text[start:])
    return LLMCheck.model_validate(data)


class LLMTripwire:
    def __init__(
        self,
        llm: LLMClient,
        prompt: Prompt,
        catalogue: Catalogue,
        *,
        threshold: float = CONFIDENCE_THRESHOLD,
        timeout_s: float = CHECK_TIMEOUT_S,
    ) -> None:
        self._llm = llm
        self._prompt = prompt
        self._rules = {r: s for r, s in catalogue.rules.items() if "llm" in s.layers}
        self._threshold = threshold
        self._timeout_s = timeout_s

    def messages(
        self,
        text: str,
        *,
        turn: int,
        context: Sequence[TranscriptLine],
        lexicon_flags: Sequence[Flag],
    ) -> list[Message]:
        recent = [line for line in context if turn - CONTEXT_TURNS <= line.turn < turn]
        lines = [
            "RULES",
            *[f"- {rule}: {spec.meaning}" for rule, spec in self._rules.items()],
            "",
            "CONTEXT (last turns)",
            *([f"T{c.turn} {c.role}: {c.text}" for c in recent] or ["- start of the call"]),
            "",
            f"SEGMENT (agent, turn {turn}): {text}",
            "WORD FILTER: "
            + (", ".join(f'{f.rule} "{f.quote}"' for f in lexicon_flags) or "nothing flagged"),
        ]
        return [
            {"role": "system", "content": self._prompt.text},
            {"role": "user", "content": "\n".join(lines)},
        ]

    async def _ask(self, messages: list[Message]) -> str:
        pieces = [
            p
            async for p in self._llm.stream_chat(
                messages, max_tokens=CHECK_MAX_TOKENS, temperature=0.0, thinking=False
            )
        ]
        return "".join(pieces)

    async def check(
        self,
        text: str,
        *,
        turn: int,
        segment: int,
        t: float,
        context: Sequence[TranscriptLine],
        lexicon_flags: Sequence[Flag],
    ) -> TripwireVerdict:
        msgs = self.messages(text, turn=turn, context=context, lexicon_flags=lexicon_flags)
        try:
            raw = await asyncio.wait_for(self._ask(msgs), timeout=self._timeout_s)
        except TimeoutError:
            return TripwireVerdict(updates=[], check=None, error="timeout")
        except LLMError as exc:
            return TripwireVerdict(updates=[], check=None, error=str(exc))
        try:
            check = _parse(raw)
        except (ValueError, ValidationError) as exc:
            return TripwireVerdict(updates=[], check=None, error=f"rejected: {exc}")

        updates: list[Flag] = []
        if check.confidence < self._threshold:
            return TripwireVerdict(updates=updates, check=check, error=None)
        # Only the lexicon's amber flags are this layer's to decide; state flags are final.
        reviewable = [f for f in lexicon_flags if f.layer == "lexicon" and f.status == "possible"]
        flagged = {f.rule for f in lexicon_flags}
        for f in reviewable:
            if check.violation and check.rule == f.rule:
                updates.append(f.model_copy(update={"status": "confirmed"}))
            elif not check.violation:
                updates.append(f.model_copy(update={"status": "withdrawn"}))
        if (
            check.violation
            and check.rule is not None
            and check.rule in self._rules
            and check.rule not in flagged
        ):
            segment_line = TranscriptLine(turn=turn, role="agent", text=text, t=t)
            if find_quote(Evidence(turn=turn, quote=check.quote), [segment_line], "agent"):
                updates.append(
                    Flag(
                        id=f"t{turn}-s{segment}-{check.rule}-llm",
                        turn=turn,
                        segment=segment,
                        t=t,
                        rule=check.rule,
                        quote=check.quote,
                        layer="llm",
                        status="confirmed",
                    )
                )
        return TripwireVerdict(updates=updates, check=check, error=None)
