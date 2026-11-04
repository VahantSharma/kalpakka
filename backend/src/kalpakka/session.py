"""CallSession: one live call, conducted by the backend (context doc 05, 08).

The browser is a microphone, a speaker and a screen. This class owns the truth for one call:
the borrower engine, the conversation history, timings and persistence. Every dependency sits
behind a small Protocol (transport, STT, TTS, LLM replies, fixed lines, turn analysis), so
the same session runs against the real Gnani/Evon clients or the test fakes.

Flow (push-to-talk): `start_call` -> `briefing` -> pickup line (turn 0). Then per agent turn:
`ptt_start` -> mic PCM forwarded to Prisma -> `ptt_end` -> commit -> final transcript ->
analysis (tripwire flags + signals) -> `begin_turn` brief -> Evon streams -> chunker cuts
sentences -> Timbre speaks each sentence while the next one is still being written -> the
`###STATE###` update is applied at the end (`end_turn`). Silence for `silence_s` plays the
persona's silence line and counts a strike (no LLM call). Any state -> ending on hang-up,
socket drop, a borrower hang-up rule, the time cap or a fatal error.

All tasks belong to the session: one receiver task feeding an event queue, and at most one turn
task. Hidden borrower state never leaves this module except as a mood label.
"""

import asyncio
import contextlib
import datetime as dt
import logging
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol

from pydantic import ValidationError

from kalpakka.borrower_state import (
    BorrowerEngine,
    CallSetup,
    StateSuggestion,
    TurnOutcome,
    TurnSignals,
)
from kalpakka.brain import (
    SILENT_AGENT,
    FirstTokenTimeout,
    HistoryTurn,
    Prompt,
    ReplyDone,
    ReplyEvent,
    SpeechDelta,
    SpeechDone,
    build_messages,
    stream_reply,
)
from kalpakka.chunker import Chunker
from kalpakka.clock import Clock
from kalpakka.db import CallRecord, Database, TurnRecord
from kalpakka.gnani_stt import SttConfigRejected, SttTranscript, SttUnavailable
from kalpakka.gnani_tts import SAMPLE_RATE, TtsFailed, TtsUnavailable, Voice, voice_for_persona
from kalpakka.llm import LLMClient, LLMError, Message
from kalpakka.metrics import LatencyStore, TurnTimer
from kalpakka.personas import Difficulty, Persona
from kalpakka.protocol import (
    AudioEnd,
    AudioStart,
    Briefing,
    CallEnded,
    EndCall,
    ErrorMsg,
    FlagMsg,
    Mood,
    Played,
    ProtocolError,
    PttEnd,
    PttStart,
    ScorecardMsg,
    ServerMessage,
    StartCall,
    StopAudio,
    Transcript,
    encode,
    parse_client_message,
)
from kalpakka.tripwire import Flag, LexiconTripwire

log = logging.getLogger(__name__)

AUDIO_CHUNK_BYTES: Final = 9600  # 200 ms of 24 kHz PCM16 per binary frame for fixed lines
BYTES_PER_SECOND: Final = SAMPLE_RATE * 2
# After the estimated end of playback, wait this long before treating the turn as heard. Browsers
# that report `played` get longer, because their last report is the real signal (05 protocol).
PLAYBACK_GRACE_S: Final = 0.3
HARD_CAP_GRACE_S: Final = 30.0  # the engine ends at the cap on a turn end; this ends it regardless
PLAYED_REPORT_GRACE_S: Final = 5.0
EMPTY_HEARD: Final = "…"  # history text when the trainee cut the borrower off before a sentence

EndReason = Literal[
    "agent_hangup",
    "disconnected",
    "bad_start",
    "angry",
    "silence",
    "closed_after_promise",
    "bereavement_pressure",
    "time_cap",
    "llm_unavailable",
    "tts_unavailable",
    "stt_unavailable",
    "error",
]


# --- seams ---------------------------------------------------------------------------------------


class TransportClosed(Exception):
    """The browser socket is gone."""


class Transport(Protocol):
    async def receive(self) -> str | bytes: ...
    async def send_text(self, data: str) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...


class SpeechToText(Protocol):
    def send_audio(self, pcm: bytes) -> None: ...
    def commit(self) -> None: ...
    async def utterance(self, timeout_s: float) -> SttTranscript:
        """Every final since the last utterance, joined, once the commit has been answered."""
        ...


class TextToSpeech(Protocol):
    def synthesize(
        self, text: str, voice: Voice, *, speed: float
    ) -> AsyncGenerator[bytes, None]: ...
    async def reset(self) -> None: ...


class ReplySource(Protocol):
    def reply(self, messages: Sequence[Message]) -> AsyncGenerator[ReplyEvent, None]: ...


class FixedLines(Protocol):
    async def line(self, persona: Persona, name: str) -> tuple[str, bytes]:
        """(text, PCM at 24 kHz) for a fixed line: pickup, silence, hangup_angry, line_trouble."""
        ...


class CallJudge(Protocol):
    """Post-call judging (session C's judge runner: transcript from the DB -> verified
    scorecard). Returns (scorecard JSON, judge version)."""

    async def judge(self, call_id: str) -> tuple[dict[str, Any], str]: ...


@dataclass(frozen=True)
class Analysis:
    signals: TurnSignals
    flags: list[Flag] = field(default_factory=list)


class TurnAnalyzer(Protocol):
    """Hook for the tripwire layers (lexicon here; state rules and the LLM layer from session C).

    Runs before `begin_turn`, so trap windows must be checked with `engine.upcoming_turn`.
    """

    def analyze(self, text: str, *, engine: BorrowerEngine, segment: int, t: float) -> Analysis: ...


class LexiconAnalyzer:
    """Default analyzer: lexicon flags only (`possible`, never confirmed), no respect markers."""

    def __init__(self, tripwire: LexiconTripwire) -> None:
        self._tripwire = tripwire

    def analyze(self, text: str, *, engine: BorrowerEngine, segment: int, t: float) -> Analysis:
        flags = self._tripwire.check(text, turn=engine.upcoming_turn, segment=segment, t=t)
        return Analysis(signals=TurnSignals(agent_silent=not text.strip()), flags=flags)


class NoAnalysis:
    def analyze(self, text: str, *, engine: BorrowerEngine, segment: int, t: float) -> Analysis:
        return Analysis(signals=TurnSignals(agent_silent=not text.strip()))


class LlmReplies:
    """Production `ReplySource`: the brain's streaming reply via the provider-agnostic client."""

    def __init__(self, llm: LLMClient, clock: Clock) -> None:
        self._llm = llm
        self._clock = clock

    def reply(self, messages: Sequence[Message]) -> AsyncGenerator[ReplyEvent, None]:
        return stream_reply(self._llm, messages, clock=self._clock)


@dataclass(frozen=True)
class SessionConfig:
    sim_date: dt.date  # the story's "today"; never the wall clock
    time_cap_s: float | None
    silence_s: float = 12.0
    transcript_timeout_s: float = 5.0
    start_timeout_s: float = 10.0
    playback_grace_s: float = PLAYBACK_GRACE_S
    judge_timeout_s: float = 90.0  # thinking-on judge; ours, tune after P3.3's real run
    mode: Literal["train", "arena", "audit"] = "train"


@dataclass
class CallDeps:
    personas: dict[str, Persona]
    system_prompt: Prompt
    open_stt: Callable[[Persona, str], AbstractAsyncContextManager[SpeechToText]]
    open_tts: Callable[[], AbstractAsyncContextManager[TextToSpeech]]
    replies: ReplySource
    lines: FixedLines
    db: Database
    clock: Clock
    wall: Callable[[], dt.datetime]  # real-world timestamps for the calls table only
    seeds: Callable[[], int]
    config: SessionConfig
    analyzer: TurnAnalyzer = field(default_factory=NoAnalysis)
    backup_voice: Voice | None = None
    latency: LatencyStore = field(default_factory=LatencyStore)
    judge: CallJudge | None = None


def story_setup(
    persona: Persona,
    difficulty: Difficulty,
    *,
    seed: int,
    sim_date: dt.date,
    time_cap_s: float | None,
) -> CallSetup:
    """The engine setup for one call. The sim date comes from config, never `date.today()`."""
    return CallSetup(
        persona=persona, difficulty=difficulty, seed=seed, sim_date=sim_date, time_cap_s=time_cap_s
    )


# --- the session ---------------------------------------------------------------------------------


class _CallOver(Exception):
    def __init__(self, reason: EndReason) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class _Reply:
    """The borrower's reply in flight: finalised (`end_turn`) only once it has been heard."""

    turn: int
    t_start: float
    sentences: list[str] = field(default_factory=list)  # fully sent to the browser, in order
    ends: list[float] = field(default_factory=list)  # estimated playback end of each (monotonic)
    played: int = -1  # highest sentence index the browser reported `played`
    done: bool = False  # the LLM stream finished and every sentence was sent
    suggestion: StateSuggestion | None = None
    timer: TurnTimer | None = None
    first_duration_s: float | None = None  # audio length of sentence 0


@dataclass
class _TurnDone:
    task: "asyncio.Task[object] | None"  # which turn finished; stale ones are ignored
    outcome: TurnOutcome | None
    end: EndReason | None


_Event = str | bytes | TransportClosed | _TurnDone


class CallSession:
    def __init__(self, deps: CallDeps, transport: Transport, *, call_id: str) -> None:
        self._deps = deps
        self._transport = transport
        self.call_id = call_id
        self._events: asyncio.Queue[_Event] = asyncio.Queue()
        self._history: list[HistoryTurn] = []
        self._turn_idx = 0  # row index in the turns table
        self._segment = 0
        self._ptt = False
        self._turn_task: asyncio.Task[None] | None = None
        self._speaking_until = 0.0  # monotonic time the browser should finish playing
        self._started = 0.0
        self._persona: Persona | None = None
        self._engine: BorrowerEngine | None = None
        self._stt: SpeechToText | None = None
        self._tts: TextToSpeech | None = None
        self._voice: Voice | None = None
        self._send_lock = asyncio.Lock()
        self._reply: _Reply | None = None
        self._min_valid_turn = 0  # audio of older turns is never sent (barge-in)
        self._played_seen = False  # this browser reports `played`
        self._timer: TurnTimer | None = None  # the agent turn being timed (P2.6)
        self._silence_deadline: float | None = None  # absolute; set when the call goes idle
        self._finalize_deadline: float | None = None  # absolute; when a done reply counts as heard
        self._pending_end: EndReason | None = None  # an ending already decided inside a turn task
        self._hard_deadline: float | None = None  # time cap + grace, whatever the state

    # --- public ---

    async def run(self) -> EndReason:
        start = await self._receive_start()
        if start is None:
            return "bad_start"
        persona = self._deps.personas.get(start.persona)
        if persona is None:
            await self._send(ErrorMsg(code="unknown_persona", message=start.persona))
            return "bad_start"
        self._persona = persona
        self._voice = voice_for_persona(persona)
        cfg = self._deps.config
        seed = self._deps.seeds()
        engine = BorrowerEngine(
            story_setup(
                persona,
                start.difficulty,
                seed=seed,
                sim_date=cfg.sim_date,
                time_cap_s=cfg.time_cap_s,
            ),
            self._deps.clock,
        )
        self._engine = engine
        self._started = self._deps.clock.monotonic()
        if cfg.time_cap_s is not None:
            self._hard_deadline = self._started + cfg.time_cap_s + HARD_CAP_GRACE_S
        try:
            await self._create_call_row(start, persona, engine, seed)
        except Exception:
            log.exception("call %s: could not create the call row", self.call_id)
            await self._send(ErrorMsg(code="server_error", message="could not start the call"))
            return "error"
        reason: EndReason = "error"
        receiver = asyncio.create_task(self._receive_loop(), name=f"recv-{self.call_id}")
        try:
            reason = await self._with_voice(persona, engine)
        except TransportClosed:
            reason = "disconnected"
        except Exception:
            log.exception("call %s failed", self.call_id)
            reason = "error"
        finally:
            await self._cancel_turn()
            receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await receiver
            await self._finish(reason)
            await self._judge()
        return reason

    async def _judge(self) -> None:
        """05: ending -> judging -> done. A call whose browser left is still judged and saved."""
        judge = self._deps.judge
        if judge is None:
            return
        try:
            card, version = await asyncio.wait_for(
                judge.judge(self.call_id), timeout=self._deps.config.judge_timeout_s
            )
        except Exception:
            log.exception("call %s: judging failed", self.call_id)
            with contextlib.suppress(TransportClosed, Exception):
                await self._send(ErrorMsg(code="judge_failed", message="scorecard unavailable"))
            return
        try:
            await self._deps.db.save_scorecard(self.call_id, card, judge_version=version)
        except Exception:
            log.exception("call %s: could not store the scorecard", self.call_id)
        with contextlib.suppress(TransportClosed, Exception):
            await self._send(ScorecardMsg(scorecard=card))

    async def _create_call_row(
        self, start: StartCall, persona: Persona, engine: BorrowerEngine, seed: int
    ) -> None:
        cfg = self._deps.config
        await self._deps.db.create_call(
            CallRecord(
                id=self.call_id,
                anon_id=start.anon_id,
                mode=cfg.mode,
                persona_id=persona.id,
                persona_version=str(persona.version),
                prompt_version=str(self._deps.system_prompt.version),
                difficulty=start.difficulty,
                seed=seed,
                traps=[t.name for t in engine.state.traps],
                started_at=self._deps.wall(),
            )
        )

    # --- start and end ---

    async def _receive_start(self) -> StartCall | None:
        try:
            raw = await asyncio.wait_for(
                self._transport.receive(), timeout=self._deps.config.start_timeout_s
            )
        except (TimeoutError, TransportClosed):
            return None
        if isinstance(raw, bytes):
            await self._send(ErrorMsg(code="expected_start_call", message="audio before start"))
            return None
        try:
            msg = parse_client_message(raw)
        except ProtocolError as exc:
            await self._send(ErrorMsg(code="bad_message", message=str(exc)))
            return None
        if not isinstance(msg, StartCall):
            await self._send(ErrorMsg(code="expected_start_call", message=msg.type))
            return None
        return msg

    async def _with_voice(self, persona: Persona, engine: BorrowerEngine) -> EndReason:
        try:
            async with (
                self._deps.open_stt(persona, self.call_id) as stt,
                self._deps.open_tts() as tts,
            ):
                self._stt, self._tts = stt, tts
                await self._send(
                    Briefing(
                        call_id=self.call_id,
                        facts=persona.public_card().model_dump(mode="json"),
                        sim_clock=engine.state.sim_clock.strftime("%Y-%m-%dT%H:%M"),
                    )
                )
                await self._play_line("pickup", turn=0)
                try:
                    return await self._conduct()
                finally:
                    await self._settle_on_end()
        except (SttConfigRejected, SttUnavailable) as exc:
            log.error("call %s: speech-to-text unavailable: %s", self.call_id, exc)
            await self._send(ErrorMsg(code="stt_unavailable", message="speech service unavailable"))
            return "stt_unavailable"
        except TtsFailed as exc:
            log.error("call %s: text-to-speech unavailable: %s", self.call_id, exc)
            await self._send(ErrorMsg(code="tts_unavailable", message="voice service unavailable"))
            return "tts_unavailable"

    async def _finish(self, reason: EndReason) -> None:
        with contextlib.suppress(TransportClosed, Exception):
            await self._send(CallEnded(reason=reason))
        promise = self._engine.state.promise if self._engine else None
        try:
            await self._deps.db.finish_call(
                self.call_id,
                ended_at=self._deps.wall(),
                end_reason=reason,
                promise_amount=promise.amount if promise else None,
                promise_date=promise.date if promise else None,
                promise_valid=promise.valid if promise else None,
            )
        except Exception:
            log.exception("call %s: could not record the end of the call", self.call_id)

    # --- the conductor ---

    async def _receive_loop(self) -> None:
        while True:
            try:
                item = await self._transport.receive()
            except TransportClosed as exc:
                self._events.put_nowait(exc)
                return
            self._events.put_nowait(item)

    async def _conduct(self) -> EndReason:
        silence_s = self._deps.config.silence_s
        while True:
            now = self._now_abs()
            idle = self._turn_task is None and not self._ptt
            deadline: float | None = None
            if idle and self._reply is not None:
                if self._finalize_deadline is None:  # set once, not on every inbound event
                    grace = (
                        PLAYED_REPORT_GRACE_S
                        if self._played_seen
                        else self._deps.config.playback_grace_s
                    )
                    self._finalize_deadline = max(now, self._speaking_until) + grace
                deadline = self._finalize_deadline
            elif idle:
                if self._silence_deadline is None:  # only a real state change restarts it
                    self._silence_deadline = max(now, self._speaking_until) + silence_s
                deadline = self._silence_deadline
            else:
                self._silence_deadline = None
            if self._hard_deadline is not None:
                deadline = min(deadline or self._hard_deadline, self._hard_deadline)
            timeout = None if deadline is None else max(0.0, deadline - now)
            try:
                event = await asyncio.wait_for(self._events.get(), timeout=timeout)
            except TimeoutError:
                if self._hard_deadline is not None and self._now_abs() >= self._hard_deadline:
                    return "time_cap"
                if self._reply is not None:  # playback is over: the whole reply was heard
                    end = await self._finalize_reply(interrupted=False)
                    if end is not None:
                        return end
                    continue
                self._silence_deadline = None
                self._turn_task = asyncio.create_task(self._silent_turn(), name="silent-turn")
                continue
            if isinstance(event, TransportClosed):
                return "disconnected"
            if isinstance(event, _TurnDone):
                if event.task is not self._turn_task:
                    continue  # a turn we already cancelled (barge-in, hang-up)
                self._turn_task = None
                if event.end is not None:
                    return event.end
                reply = self._reply
                if reply is not None and reply.done and self._fully_played(reply):
                    end = await self._finalize_reply(interrupted=False)
                    if end is not None:
                        return end
                continue
            if isinstance(event, bytes):
                if self._ptt and self._stt is not None:
                    self._stt.send_audio(event)
                continue
            try:
                msg = parse_client_message(event)
            except ProtocolError as exc:
                await self._send(ErrorMsg(code="bad_message", message=str(exc)))
                continue
            end = await self._on_message(msg)
            if end is not None:
                return end

    async def _on_message(
        self, msg: StartCall | PttStart | PttEnd | Played | EndCall
    ) -> EndReason | None:
        if isinstance(msg, EndCall):
            return "agent_hangup"  # a pending reply is settled by `_settle_on_end`
        if isinstance(msg, PttStart):
            end = await self._barge_in()
            self._ptt = True
            return end
        if isinstance(msg, PttEnd):
            if not self._ptt:
                return None
            self._ptt = False
            assert self._stt is not None
            if self._timer is None:  # a merged segment keeps the first segment's timer
                self._timer = TurnTimer(self._deps.clock)
            self._timer.mark("ptt_end")
            self._stt.commit()
            self._timer.mark("commit_sent")  # queued now; paced frames ahead of it are < 1 frame
            self._turn_task = asyncio.create_task(self._agent_turn(), name="agent-turn")
        elif isinstance(msg, Played):
            return await self._on_played(msg)
        elif isinstance(msg, StartCall):
            await self._send(ErrorMsg(code="bad_message", message="call already started"))
        return None

    async def _on_played(self, msg: Played) -> EndReason | None:
        self._played_seen = True
        reply = self._reply
        if reply is None or msg.turn != reply.turn:
            return None
        reply.played = max(reply.played, msg.sentence)
        if msg.sentence == 0 and reply.timer is not None and reply.first_duration_s is not None:
            elapsed = reply.timer.since_origin()
            if elapsed is not None:  # first audible sample = end of sentence 0 minus its length
                reply.timer.mark_at("browser_first_play", elapsed - reply.first_duration_s)
        if reply.done and self._turn_task is None and self._fully_played(reply):
            return await self._finalize_reply(interrupted=False)
        return None

    @staticmethod
    def _fully_played(reply: _Reply) -> bool:
        return reply.played >= len(reply.sentences) - 1

    def _turn_running(self) -> bool:
        task = self._turn_task
        return task is not None and not task.done()

    async def _barge_in(self) -> EndReason | None:
        """`ptt_start`. While the borrower talks, this is a barge-in (05, 08): cancel the reply
        task (closes the LLM stream and the sentence queue), reset Timbre, bump the minimum valid
        turn, keep only the played sentences and the interruption mood bump.

        While Prisma has not answered yet, the pending turn is cancelled and its commit stays
        outstanding, so the next turn reads both transcripts and treats them as one agent turn.
        """
        reply = self._reply
        running = self._turn_running()
        await self._cancel_turn()
        if self._pending_end is not None:  # e.g. "line kharab hai" was playing: still end
            return self._pending_end
        if reply is None:
            if self._now_abs() < self._speaking_until:  # e.g. over the pickup line
                await self._send(StopAudio())
            return None
        interrupted = running or self._was_interrupted(reply)
        return await self._finalize_reply(interrupted=interrupted)

    def _was_interrupted(self, reply: _Reply) -> bool:
        if reply.done and reply.played >= len(reply.sentences) - 1:
            return False
        if self._played_seen:
            return True  # this browser reports playback and the last sentence is not done
        return self._now_abs() < self._speaking_until

    async def _finalize_reply(self, *, interrupted: bool, send: bool = True) -> EndReason | None:
        """Apply the borrower's turn once heard (or cut off). Only the conductor calls this."""
        reply, self._reply = self._reply, None
        self._finalize_deadline = None
        assert reply is not None
        engine = self._require_engine()
        if interrupted:
            if self._played_seen:
                heard_count = reply.played + 1
            else:
                now = self._now_abs()
                heard_count = sum(1 for end in reply.ends if end <= now)
            heard = " ".join(reply.sentences[:heard_count])
            outcome = engine.end_turn(StateSuggestion(), interrupted=True)
            self._min_valid_turn = reply.turn + 1
            self._speaking_until = self._now_abs()
            if send:
                await self._send(StopAudio())  # first: the browser must not wait on Timbre
            assert self._tts is not None
            await self._tts.reset()  # drop half-finished audio on Timbre's side
        else:
            heard = " ".join(reply.sentences)
            outcome = engine.end_turn(reply.suggestion or StateSuggestion())
        self._history.append(HistoryTurn(role="borrower", text=heard or EMPTY_HEARD))
        latency: dict[str, float] = {}
        if reply.timer is not None:
            marks = reply.timer.marks()
            stages = TurnTimer.stages_from(marks)
            self._deps.latency.add(stages)
            latency = {**marks, **{f"stage_{k}": v for k, v in stages.items()}}
        await self._store_turn(
            "borrower",
            heard,
            reply.t_start,
            self._t(),
            state=outcome,
            interrupted=interrupted,
            latency=latency,
        )
        if not send:
            return outcome.call_over
        await self._send(Mood(label=outcome.state.mood_label))
        return await self._after_turn(outcome)

    async def _settle_on_end(self) -> None:
        """The call is ending: a reply not yet finalised is stored with what was heard, so its
        words (and any valid promise) reach the transcript and `finish_call`."""
        running = self._turn_running()
        await self._cancel_turn()
        reply = self._reply
        if reply is None:
            return
        interrupted = running or self._was_interrupted(reply)
        with contextlib.suppress(TransportClosed):
            await self._finalize_reply(interrupted=interrupted, send=False)

    async def _cancel_turn(self) -> None:
        task, self._turn_task = self._turn_task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    # --- turns ---

    async def _agent_turn(self) -> None:
        end: EndReason | None = None
        outcome: TurnOutcome | None = None
        try:
            assert self._stt is not None
            try:
                heard = await self._stt.utterance(self._deps.config.transcript_timeout_s)
            except TimeoutError:
                await self._send(ErrorMsg(code="not_heard", message="no transcript; say it again"))
                return
            except (SttUnavailable, SttConfigRejected) as exc:
                log.error("call %s: speech-to-text lost: %s", self.call_id, exc)
                await self._send(ErrorMsg(code="stt_unavailable", message="speech service lost"))
                end = "stt_unavailable"
                return
            text = heard.text.strip()
            timer, self._timer = self._timer, None
            if timer is not None:
                timer.mark("transcript_final")
            if not text:
                outcome, end = await self._silence(agent_text="")
                return
            outcome, end = await self._reply_to(text, timer)
        except _CallOver as over:
            end = over.reason
        except asyncio.CancelledError:
            raise
        except TransportClosed:
            end = "disconnected"
        except Exception:
            log.exception("call %s: turn failed", self.call_id)
            end = "error"
        finally:
            self._events.put_nowait(
                _TurnDone(task=asyncio.current_task(), outcome=outcome, end=end)
            )

    async def _silent_turn(self) -> None:
        end: EndReason | None = None
        outcome: TurnOutcome | None = None
        try:
            outcome, end = await self._silence(agent_text="")
        except _CallOver as over:
            end = over.reason
        except asyncio.CancelledError:
            raise
        except TransportClosed:
            end = "disconnected"
        except Exception:
            log.exception("call %s: silent turn failed", self.call_id)
            end = "error"
        finally:
            self._events.put_nowait(
                _TurnDone(task=asyncio.current_task(), outcome=outcome, end=end)
            )

    async def _silence(self, *, agent_text: str) -> tuple[TurnOutcome | None, EndReason | None]:
        """A silent agent turn: the persona's silence line, no LLM call. Like a reply, it is
        finalised once heard, so `ptt_start` over it is an ordinary barge-in."""
        engine = self._require_engine()
        t = self._t()
        brief = engine.begin_turn(TurnSignals(agent_silent=True))
        reply = _Reply(turn=brief.turn, t_start=t, suggestion=StateSuggestion())
        self._reply = reply  # before any await: a barge-in must find the open turn
        self._history.append(HistoryTurn(role="agent", text=SILENT_AGENT))
        await self._store_turn("agent", agent_text, t, t)
        text = await self._play_line("silence", turn=brief.turn)
        reply.sentences.append(text)
        reply.ends.append(self._speaking_until)
        reply.done = True
        return None, None

    async def _reply_to(
        self, text: str, timer: TurnTimer | None = None
    ) -> tuple[TurnOutcome | None, EndReason | None]:
        """Stream the reply. The turn is finalised later, once it has been heard (or cut off)."""
        engine = self._require_engine()
        persona = self._require_persona()
        t_agent = self._t()
        await self._send(Transcript(role="agent", text=text, t=t_agent))
        analysis = self._deps.analyzer.analyze(
            text, engine=engine, segment=self._segment, t=t_agent
        )
        self._segment += 1
        for flag in analysis.flags:
            await self._send(FlagMsg(flag=flag.model_dump(mode="json")))
        await self._store_turn("agent", text, t_agent, t_agent)

        brief = engine.begin_turn(analysis.signals)
        messages = build_messages(
            self._deps.system_prompt,
            persona,
            engine.state.sim_clock,
            self._history,
            text,
            brief.text,
        )
        self._history.append(HistoryTurn(role="agent", text=text))
        mark = timer.mark if timer is not None else (lambda name: None)
        mark("brief_built")

        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        result: ReplyDone | None = None
        reply = _Reply(turn=brief.turn, t_start=self._t(), timer=timer)
        self._reply = reply

        async def produce() -> None:
            nonlocal result
            chunker = Chunker(brief.allowed_tags)

            def queue(found: list[str]) -> None:
                for sentence in found:
                    mark("first_sentence_ready")
                    sentences.put_nowait(sentence)

            try:
                mark("llm_request")
                async with contextlib.aclosing(self._deps.replies.reply(messages)) as stream:
                    async for event in stream:
                        if isinstance(event, SpeechDelta):
                            if event.text.strip():
                                mark("llm_first_token")
                            queue(chunker.feed(event.text))
                        elif isinstance(event, SpeechDone):
                            queue(chunker.flush())
                        else:
                            result = event
            finally:
                sentences.put_nowait(None)

        async def consume() -> None:
            index = 0
            while (sentence := await sentences.get()) is not None:
                if index == 0:
                    mark("tts_request")
                duration = await self._speak_sentence(
                    sentence,
                    turn=brief.turn,
                    sentence=index,
                    speed=brief.speed,
                    on_first_audio=(lambda: mark("tts_first_audio")) if index == 0 else None,
                )
                if index == 0:
                    reply.first_duration_s = duration
                reply.sentences.append(sentence)
                reply.ends.append(self._speaking_until)
                index += 1

        failure: EndReason | None = None
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(produce())
                group.create_task(consume())
        except* (FirstTokenTimeout, LLMError) as errors:
            log.error("call %s: LLM failed: %s", self.call_id, errors.exceptions[0])
            failure = "llm_unavailable"
        except* TtsUnavailable as errors:
            log.error("call %s: TTS failed: %s", self.call_id, errors.exceptions[0])
            failure = "tts_unavailable"
        except* TransportClosed:
            failure = "disconnected"  # never raise inside except*: it would wrap in a group
        if failure == "disconnected":
            self._reply = None
            raise _CallOver("disconnected")
        if failure is not None:
            # 05: one in-character "line kharab hai", then end the call politely.
            self._pending_end = failure  # survives a barge-in over the line below
            self._reply = None
            engine.end_turn(StateSuggestion())
            await self._play_line("line_trouble", turn=brief.turn, sentence=len(reply.sentences))
            raise _CallOver(failure)

        reply.suggestion = result.result.suggestion if result is not None else StateSuggestion()
        reply.done = True  # the conductor finalises it once heard
        return None, None

    async def _after_turn(self, outcome: TurnOutcome) -> EndReason | None:
        if outcome.call_over is None:
            return None
        if outcome.call_over == "angry":
            await self._play_line("hangup_angry", turn=outcome.state.turn, sentence=99)
        return outcome.call_over

    # --- audio out ---

    async def _speak_sentence(
        self,
        text: str,
        *,
        turn: int,
        sentence: int,
        speed: float,
        on_first_audio: Callable[[], None] | None = None,
    ) -> float:
        """Send one sentence's audio; returns its duration in seconds."""
        assert self._tts is not None and self._voice is not None
        if turn < self._min_valid_turn:
            return 0.0  # a cancelled turn: its audio must never reach the browser
        await self._send(AudioStart(turn=turn, sentence=sentence, rate=SAMPLE_RATE))
        sent = 0
        stream = speak_with_fallback(
            self._tts, text, self._voice, speed=speed, backup=self._deps.backup_voice
        )
        async with contextlib.aclosing(stream):
            async for pcm in stream:
                if turn < self._min_valid_turn:
                    return 0.0
                if sent == 0 and on_first_audio is not None:
                    on_first_audio()
                await self._send_audio(pcm)
                sent += len(pcm)
        await self._send(AudioEnd(turn=turn, sentence=sentence))
        await self._send(Transcript(role="borrower", text=text, t=self._t()))
        self._account_playback(sent)
        return sent / BYTES_PER_SECOND

    async def _play_line(self, name: str, *, turn: int, sentence: int = 0) -> str:
        persona = self._require_persona()
        text, pcm = await self._deps.lines.line(persona, name)
        await self._send(AudioStart(turn=turn, sentence=sentence, rate=SAMPLE_RATE))
        for i in range(0, len(pcm), AUDIO_CHUNK_BYTES):
            await self._send_audio(pcm[i : i + AUDIO_CHUNK_BYTES])
        await self._send(AudioEnd(turn=turn, sentence=sentence))
        await self._send(Transcript(role="borrower", text=text, t=self._t()))
        self._account_playback(len(pcm))
        if name == "pickup":
            await self._store_turn("borrower", text, 0.0, self._t())
        return text

    def _account_playback(self, nbytes: int) -> None:
        start = max(self._speaking_until, self._now_abs())
        self._speaking_until = start + nbytes / BYTES_PER_SECOND

    # --- plumbing ---

    async def _send(self, message: ServerMessage) -> None:
        async with self._send_lock:
            await self._transport.send_text(encode(message))

    async def _send_audio(self, pcm: bytes) -> None:
        async with self._send_lock:
            await self._transport.send_bytes(pcm)

    async def _store_turn(
        self,
        role: Literal["agent", "borrower"],
        text: str,
        t_start: float,
        t_end: float,
        *,
        state: TurnOutcome | None = None,
        interrupted: bool = False,
        latency: dict[str, float] | None = None,
    ) -> None:
        record = TurnRecord(
            call_id=self.call_id,
            idx=self._turn_idx,
            role=role,
            text=text,
            t_start=t_start,
            t_end=t_end,
            interrupted=interrupted,
            latency=latency or {},
            state_after=state.state.model_dump(mode="json") if state else None,
        )
        self._turn_idx += 1
        try:
            await self._deps.db.add_turn(record)
        except (ValueError, ValidationError):
            log.exception("call %s: could not store turn %d", self.call_id, record.idx)

    def _now_abs(self) -> float:
        return self._deps.clock.monotonic()

    def _t(self) -> float:
        return round(self._deps.clock.monotonic() - self._started, 3)

    def _require_engine(self) -> BorrowerEngine:
        assert self._engine is not None
        return self._engine

    def _require_persona(self) -> Persona:
        assert self._persona is not None
        return self._persona


async def speak_with_fallback(
    tts: TextToSpeech, text: str, voice: Voice, *, speed: float, backup: Voice | None
) -> AsyncGenerator[bytes, None]:
    """05: retry once, then the backup voice, then `TtsUnavailable`. Never retry after audio."""
    attempts = [voice, voice] + ([backup] if backup is not None else [])
    last: TtsFailed | None = None
    for attempt_voice in attempts:
        produced = False
        try:
            async with contextlib.aclosing(
                tts.synthesize(text, attempt_voice, speed=speed)
            ) as stream:
                async for chunk in stream:
                    produced = True
                    yield chunk
            return
        except TtsFailed as exc:
            if produced:
                raise TtsUnavailable(f"failed mid-sentence after audio: {exc}") from exc
            log.warning("tts attempt with %s failed: %s", attempt_voice.name, exc)
            last = exc
    raise TtsUnavailable(f"all {len(attempts)} attempts failed: {last}") from last
