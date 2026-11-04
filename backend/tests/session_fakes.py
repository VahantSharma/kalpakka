"""In-process fakes for CallSession tests: browser transport, STT, TTS, LLM replies, fixed lines.

These sit at the session's Protocol seams. The network fakes (fake Prisma / Timbre / OpenAI
servers) are used by the integration test instead.
"""

import asyncio
import datetime as dt
import json
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from kalpakka.brain import ReplyDone, ReplyEvent, SpeechDelta, SpeechDone, StateSplitter
from kalpakka.gnani_stt import SttTranscript
from kalpakka.gnani_tts import Voice
from kalpakka.llm import Message
from kalpakka.personas import Persona
from kalpakka.session import TransportClosed


class FakeTransport:
    """The browser end of `/call`. Tests push client frames and read what the server sent."""

    def __init__(self) -> None:
        self.inbox: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        self.sent: list[dict[str, Any] | bytes] = []  # Any: decoded JSON messages
        self.new_message = asyncio.Event()
        self.closed = False

    # client side helpers
    def send_json(self, **message: Any) -> None:
        self.inbox.put_nowait(json.dumps(message, ensure_ascii=False))

    def send_audio(self, pcm: bytes) -> None:
        self.inbox.put_nowait(pcm)

    def disconnect(self) -> None:
        self.inbox.put_nowait(None)

    def messages(self, kind: str | None = None) -> list[dict[str, Any]]:
        out = [m for m in self.sent if isinstance(m, dict)]
        return [m for m in out if kind is None or m["type"] == kind]

    def audio_bytes(self) -> bytes:
        return b"".join(m for m in self.sent if isinstance(m, bytes))

    async def wait_for(self, kind: str, count: int = 1, within_s: float = 5.0) -> None:
        async def _until() -> None:
            while len(self.messages(kind)) < count:
                self.new_message.clear()
                await self.new_message.wait()

        await asyncio.wait_for(_until(), within_s)

    # server side (the Transport protocol)
    async def receive(self) -> str | bytes:
        item = await self.inbox.get()
        if item is None:
            raise TransportClosed("browser went away")
        return item

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))
        self.new_message.set()

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)
        self.new_message.set()


@dataclass
class FakeStt:
    transcripts: list[str] = field(default_factory=list)
    audio: bytearray = field(default_factory=bytearray)
    commits: int = 0
    _ready: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    closed: bool = False
    delay_s: float = 0.0  # time from commit to transcript

    def send_audio(self, pcm: bytes) -> None:
        self.audio.extend(pcm)

    _pending: int = 0  # commits whose transcript has not been produced yet

    def commit(self) -> None:
        self.commits += 1
        self._pending += 1
        text = self.transcripts.pop(0) if self.transcripts else ""

        def deliver() -> None:
            self._pending -= 1
            self._ready.put_nowait(text)

        asyncio.get_running_loop().call_later(self.delay_s, deliver)

    async def utterance(self, timeout_s: float) -> SttTranscript:
        """Like PrismaStream.utterance: every final since the last call, once none is pending."""
        texts: list[str] = []
        texts.append(await asyncio.wait_for(self._ready.get(), timeout_s))
        while self._pending > 0 or not self._ready.empty():
            texts.append(await asyncio.wait_for(self._ready.get(), timeout_s))
        joined = " ".join(t for t in texts if t.strip())
        return SttTranscript(
            text=joined, detected_language_code="hi-IN", segment_index=self.commits
        )


@dataclass
class FakeTts:
    requests: list[tuple[str, str, float]] = field(default_factory=list)
    resets: int = 0
    chunk_delay_s: float = 0.0

    @staticmethod
    def pcm_for(text: str) -> bytes:
        return b"\x01\x00" * max(1, len(text))

    async def synthesize(
        self, text: str, voice: Voice, *, speed: float
    ) -> AsyncGenerator[bytes, None]:
        self.requests.append((text, voice.name, speed))
        pcm = self.pcm_for(text)
        half = len(pcm) // 2 // 2 * 2
        for chunk in (pcm[:half], pcm[half:]):
            await asyncio.sleep(self.chunk_delay_s)
            if chunk:
                yield chunk

    async def reset(self) -> None:
        self.resets += 1


def reply_text(speech: str, state: dict[str, Any] | None = None) -> list[str]:
    """What Evon would stream: speech, the delimiter, the state JSON (split into pieces)."""
    raw = speech + " ###STATE### " + json.dumps(state or {"mood_delta": 0, "trust_delta": 0})
    return [raw[i : i + 9] for i in range(0, len(raw), 9)]


@dataclass
class FakeReplies:
    """Scripted Evon replies through the real StateSplitter, so speech/state split is real."""

    scripts: list[list[str]] = field(default_factory=list)
    requests: list[list[Message]] = field(default_factory=list)
    piece_delay_s: float = 0.0
    fail_with: BaseException | None = None

    async def reply(self, messages: Sequence[Message]) -> AsyncGenerator[ReplyEvent, None]:
        self.requests.append(list(messages))
        if self.fail_with is not None:
            raise self.fail_with
        pieces = self.scripts.pop(0) if self.scripts else reply_text("Haan ji.")
        splitter = StateSplitter()
        done = False
        for piece in pieces:
            await asyncio.sleep(self.piece_delay_s)
            speech = splitter.feed(piece)
            if speech:
                yield SpeechDelta(text=speech)
            if splitter.speech_done and not done:
                done = True
                yield SpeechDone()
        result = splitter.finish()
        if result.speech_tail:
            yield SpeechDelta(text=result.speech_tail)
        if not done:
            yield SpeechDone()
        yield ReplyDone(result=result, first_token_s=0.2)


class FakeLines:
    """Fixed lines as PCM; `rendered` records which were asked for."""

    def __init__(self) -> None:
        self.rendered: list[str] = []
        self.delay_s: dict[str, float] = {}  # per line name: render time

    async def line(self, persona: Persona, name: str) -> tuple[str, bytes]:
        self.rendered.append(name)
        await asyncio.sleep(self.delay_s.get(name, 0.0))
        text = getattr(persona.lines, name, "Hello? Line kharab hai.")
        return text, FakeTts.pcm_for(text)


@asynccontextmanager
async def open_fake(obj: Any) -> AsyncIterator[Any]:  # Any: any fake behind a factory
    yield obj


class WallClock:
    """The real-world clock the session uses only for timestamps (never for the story date)."""

    def __init__(self, now: dt.datetime) -> None:
        self.now = now

    def __call__(self) -> dt.datetime:
        return self.now
