"""The tripwire of one call: all three layers of 07 behind one object the call session uses.

Per closed agent transcript segment, the session (voice `CallSession` or `TextCall`):

1. `flags = tw.on_segment(text, segment=, t=, engine=engine)` — lexicon (`possible`) and state
   rules (`confirmed`) in milliseconds, BEFORE `engine.begin_turn`; send `flags` to the UI.
2. Starts `tw.review(...)` as a background task it owns (cancelled on hang-up) with the last turns
   as context; when it returns, send the changed flags to the UI (same ids: amber -> red, or
   fades out) and pass newly confirmed rules to the engine.
3. `tw.confirmed_rules(turn)` gives the rules for `TurnSignals.confirmed_violations`;
   `tw.flags` is the ledger with each flag's latest status, which the judge's `score_call` needs.

State-rule flags are final: the LLM layer reviews lexicon flags only, so it can never withdraw a
trap-window or late-clock violation.
"""

from collections.abc import Sequence

from kalpakka.judge import TranscriptLine
from kalpakka.llm_tripwire import LLMTripwire
from kalpakka.personas import Persona
from kalpakka.state_tripwire import EngineView, StateTerms, StateTripwire
from kalpakka.tripwire import Flag, LexiconTripwire


class CallTripwire:
    def __init__(
        self,
        *,
        lexicon: LexiconTripwire,
        terms: StateTerms,
        persona: Persona,
        llm_layer: LLMTripwire | None = None,
    ) -> None:
        self._lexicon = lexicon
        self._state = StateTripwire(terms, persona)
        self._llm = llm_layer
        self._ledger: dict[str, Flag] = {}

    @property
    def flags(self) -> list[Flag]:
        """Every flag raised so far, with its latest status, in the order first raised."""
        return list(self._ledger.values())

    def confirmed_rules(self, turn: int) -> frozenset[str]:
        return frozenset(
            f.rule for f in self._ledger.values() if f.turn == turn and f.status == "confirmed"
        )

    def on_segment(self, text: str, *, segment: int, t: float, engine: EngineView) -> list[Flag]:
        turn = engine.upcoming_turn
        flags = self._lexicon.check(text, turn=turn, segment=segment, t=t)
        flags += self._state.check(text, segment=segment, t=t, view=engine)
        for f in flags:
            self._ledger[f.id] = f
        return flags

    async def review(
        self,
        text: str,
        *,
        segment: int,
        t: float,
        turn: int,
        context: Sequence[TranscriptLine],
        lexicon_flags: Sequence[Flag],
    ) -> list[Flag]:
        """The LLM layer's verdict applied to the ledger; returns the flags that changed.
        Errors and timeouts change nothing (the verdict carries the reason)."""
        if self._llm is None:
            return []
        reviewable = [f for f in lexicon_flags if f.layer == "lexicon"]
        verdict = await self._llm.check(
            text, turn=turn, segment=segment, t=t, context=context, lexicon_flags=reviewable
        )
        changed: list[Flag] = []
        for update in verdict.updates:
            if self._ledger.get(update.id) != update:
                self._ledger[update.id] = update
                changed.append(update)
        return changed
