"""One text-mode call: the per-turn loop of 06 without audio (CLI, Arena, persona realism eval).

Per agent turn: both deterministic tripwire layers run on the agent's words (lexicon `possible`,
state rules `confirmed`) -> `agent_signals` -> `begin_turn` (the brief) -> the brain streams the
borrower's reply on the LLM -> the chunker polices tags -> `end_turn` with the parsed suggestion.
The voice `CallSession` (session A) runs the same steps with Prisma segments and Timbre audio;
this is the text twin, so Arena and evals exercise exactly the engine, brain and tripwire a voice
call uses.

LLM settings (`max_tokens`, temperature, first-token timeout, thinking off) come from
`TextCallConfig`; their defaults are the brain's (06: 150-180 tokens) and are pending the measured
P0.5 findings.
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from kalpakka.borrower_state import (
    BorrowerEngine,
    BorrowerState,
    CallSetup,
    StateSuggestion,
    TurnBrief,
)
from kalpakka.brain import (
    FIRST_TOKEN_TIMEOUT_S,
    REPLY_MAX_TOKENS,
    HistoryTurn,
    Prompt,
    ReplyDone,
    SpeechDelta,
    SplitError,
    build_messages,
    stream_reply,
)
from kalpakka.call_tripwire import CallTripwire
from kalpakka.chunker import Chunker
from kalpakka.clock import Clock
from kalpakka.judge import TranscriptLine
from kalpakka.judge_runner import JudgeInput, StateSnapshot
from kalpakka.llm import LLMClient
from kalpakka.llm_tripwire import LLMTripwire
from kalpakka.state_tripwire import StateTerms, agent_signals
from kalpakka.tripwire import Flag, LexiconTripwire


class TextCallConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_tokens: int = REPLY_MAX_TOKENS
    temperature: float = 0.8
    first_token_timeout_s: float = FIRST_TOKEN_TIMEOUT_S


class TurnResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    turn: int
    agent_text: str
    flags: list[Flag]
    speech: str  # what the borrower said (tags policed); the trainee "heard" all of it
    raw_speech: str  # the model's spoken text before tag policing (realism eval)
    brief: TurnBrief  # debug only: never sent to a browser
    state: BorrowerState  # debug only: never sent to a browser
    first_token_s: float | None
    split_errors: list[SplitError]


class TextCall:
    def __init__(
        self,
        *,
        setup: CallSetup,
        llm: LLMClient,
        system: Prompt,
        lexicon: LexiconTripwire,
        terms: StateTerms,
        clock: Clock,
        config: TextCallConfig | None = None,
    ) -> None:
        self.setup = setup
        self.engine = BorrowerEngine(setup, clock)
        self._llm = llm
        self._system = system
        self._lexicon = lexicon
        self._terms = terms
        self._tripwire = CallTripwire(lexicon=lexicon, terms=terms, persona=setup.persona)
        self._clock = clock
        self._config = config or TextCallConfig()
        self._started = clock.monotonic()
        self._history: list[HistoryTurn] = []
        self._transcript: list[TranscriptLine] = []
        self._state_history: list[StateSnapshot] = []

    @property
    def transcript(self) -> Sequence[TranscriptLine]:
        return tuple(self._transcript)

    @property
    def flags(self) -> Sequence[Flag]:
        """Every flag with its latest status (the tripwire's ledger)."""
        return tuple(self._tripwire.flags)

    def enable_llm_tripwire(self, layer: LLMTripwire) -> None:
        """Add tripwire layer 2. In text mode its check is awaited before the borrower replies
        (no audio to protect), so a confirmed threat already moves the engine this turn."""
        self._tripwire = CallTripwire(
            lexicon=self._lexicon, terms=self._terms, persona=self.setup.persona, llm_layer=layer
        )

    def judge_input(self) -> JudgeInput:
        """Everything the post-call judge needs, from this call's engine and records."""
        s = self.engine.state
        return JudgeInput(
            persona=self.setup.persona,
            difficulty=self.setup.difficulty,
            sim_clock=s.sim_clock,
            transcript=list(self._transcript),
            traps=s.traps,
            gate_opened_turn=s.gate_opened_turn,
            revealed_turn=s.revealed_turn,
            promise=s.promise,
            state_history=list(self._state_history),
            flags=list(self._tripwire.flags),
        )

    def _now(self) -> float:
        return round(self._clock.monotonic() - self._started, 3)

    async def agent_turn(self, text: str, *, closing: bool = False) -> TurnResult:
        """Raises `LLMError` / `FirstTokenTimeout` after closing the engine's turn with a zero
        suggestion, so the call stays usable (the caller decides whether to go on). The agent's
        words and flags are recorded; the borrower's history gets nothing for that turn.

        Known gap: if that turn was the reveal turn, the engine still marks the truth revealed
        (`end_turn` applies `reveal_now`), though nobody heard it. Fixing it needs a
        `BorrowerEngine.cancel_turn()`."""
        turn = self.engine.upcoming_turn
        t_agent = self._now()
        raised = self._tripwire.on_segment(text, segment=0, t=t_agent, engine=self.engine)
        await self._tripwire.review(
            text, segment=0, t=t_agent, turn=turn, context=self._transcript, lexicon_flags=raised
        )
        flags = [f for f in self._tripwire.flags if f.turn == turn]
        signals = agent_signals(
            text,
            self.setup.persona,
            self._terms,
            confirmed_violations=self._tripwire.confirmed_rules(turn),
            closing=closing,
        )
        brief = self.engine.begin_turn(signals)
        state_now = self.engine.state
        messages = build_messages(
            self._system,
            self.setup.persona,
            state_now.sim_clock,
            self._history,
            text,
            brief.text,
        )

        chunker = Chunker(brief.allowed_tags)
        sentences: list[str] = []
        raw: list[str] = []
        done: ReplyDone | None = None
        try:
            async for event in stream_reply(
                self._llm,
                messages,
                clock=self._clock,
                first_token_timeout_s=self._config.first_token_timeout_s,
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
            ):
                if isinstance(event, SpeechDelta):
                    raw.append(event.text)
                    sentences += chunker.feed(event.text)
                elif isinstance(event, ReplyDone):
                    done = event
        except BaseException:  # incl. cancellation: never leave the engine mid-turn
            self.engine.end_turn(StateSuggestion())
            # The agent did speak and any flag stands; the borrower said nothing.
            self._transcript.append(TranscriptLine(turn=turn, role="agent", text=text, t=t_agent))
            raise
        sentences += chunker.flush()
        assert done is not None  # stream_reply always ends with ReplyDone
        outcome = self.engine.end_turn(done.result.suggestion)

        speech = " ".join(s.strip() for s in sentences if s.strip())
        self._history += [
            HistoryTurn(role="agent", text=text),
            HistoryTurn(role="borrower", text=speech),
        ]
        self._transcript += [
            TranscriptLine(turn=turn, role="agent", text=text, t=t_agent),
            TranscriptLine(turn=turn, role="borrower", text=speech, t=self._now()),
        ]
        s = outcome.state
        self._state_history.append(
            StateSnapshot(turn=turn, mood=s.mood, trust=s.trust, mood_label=s.mood_label)
        )
        return TurnResult(
            turn=turn,
            agent_text=text,
            flags=flags,
            speech=speech,
            raw_speech="".join(raw).strip(),
            brief=brief,
            state=outcome.state,
            first_token_s=done.first_token_s,
            split_errors=done.result.errors,
        )
