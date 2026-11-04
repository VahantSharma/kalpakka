"""Timbre v3 text-to-speech client (context doc 03 "Timbre v3.0", 05 "Failure handling").

- One `TimbreSocket` per call, opened at call start, reused for every sentence. One JSON request
  per sentence: audio tags apply from their position to the end of the request, so one sentence
  per request scopes them.
- Server messages `start`, `audio` (base64 PCM in `data.audio`), `complete`, `error`. Audio is
  raw 16-bit little-endian PCM at 24 kHz. Chunks are re-aligned to whole samples (`PcmAligner`):
  an odd-length chunk would shift every later sample by one byte and click.
- Barge-in: the caller stops reading `synthesize`; the socket is then marked dirty and reset
  (closed and reopened) before the next sentence, so half-finished audio never leaks into the
  next turn.
- `speak` adds the documented fallback: retry once, then the backup voice. It never retries
  after audio has already been yielded (that would repeat words).
- Fixed lines (pickup, silence prompt, angry hang-up, "line kharab hai") are rendered once and
  cached on disk (`PrerenderCache`), so the borrower answers within 2 s (NFR-2) without a live
  synthesis on the critical path.

Facts marked UNVERIFIED in 03 are single constants here, tagged "UNVERIFIED: re-check after
P0.2-P0.4".
"""

import asyncio
import base64
import binascii
import contextlib
import hashlib
import json
import logging
import os
import tempfile
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any, Final, Protocol

from pydantic import BaseModel, ConfigDict
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from kalpakka.personas import Persona

log = logging.getLogger(__name__)

TIMBRE_URL: Final = "wss://api.vachana.ai/api/v1/tts"
TIMBRE_MODEL: Final = "timbre-v3.0"
SAMPLE_RATE: Final = 24000
API_KEY_HEADER: Final = "X-API-Key-ID"
# UNVERIFIED: re-check after P0.2-P0.4. 03: the exact `audio_config` keys are unverified; we send
# only the two the docs show.
AUDIO_CONFIG: Final = {"container": "raw", "sample_rate": SAMPLE_RATE}
FIRST_AUDIO_TIMEOUT_S: Final = 3.0  # no `start`/first audio in 3 s -> treat as a failed request
CHUNK_TIMEOUT_S: Final = 3.0  # gap between messages inside one sentence
CONNECT_TIMEOUT_S: Final = 5.0
CLOSE_TIMEOUT_S: Final = 0.5  # never wait long for a close handshake on a failed/cut sentence
LOCK_TIMEOUT_S: Final = 5.0  # a sentence waiting longer than this for the socket is a failure
# Hinglish line for the last-resort path in 05 ("pre-rendered 'line kharab hai', end call").
# Should become a per-persona `lines` entry (content), so Tanglish/Benglish personas get their own.
LINE_TROUBLE: Final = "Hello? Line kharab hai, baad mein baat karte hain."
CACHE_VERSION: Final = 1  # bump to invalidate every cached file


class Voice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    language: str  # UNVERIFIED: re-check after P0.2-P0.4. hi-IN vs hi-en vs auto (03, P0.3)


def voice_for_persona(persona: Persona) -> Voice:
    return Voice(name=persona.speech.tts_voice, language=persona.speech.tts_language)


def build_request(text: str, voice: Voice, *, speed: float) -> str:
    return json.dumps(
        {
            "text": text,
            "voice": voice.name,
            "model": TIMBRE_MODEL,
            "language": voice.language,
            "speed": speed,
            "audio_config": AUDIO_CONFIG,
        },
        ensure_ascii=False,
    )


# --- server messages -------------------------------------------------------------------------


class _Msg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class TtsStart(_Msg):
    request_id: str | None = None


class TtsAudio(_Msg):
    pcm: bytes


class TtsComplete(_Msg):
    pass


class TtsError(_Msg):
    message: str


class TtsUnknown(_Msg):
    raw: str = ""


TtsEvent = TtsStart | TtsAudio | TtsComplete | TtsError | TtsUnknown


def parse_message(raw: str) -> TtsEvent:
    try:
        data: Any = json.loads(raw)  # Any: untrusted JSON, checked field by field below
    except json.JSONDecodeError:
        return TtsUnknown(raw=raw[:200])
    if not isinstance(data, dict):
        return TtsUnknown(raw=raw[:200])
    kind = data.get("type")
    nested = data.get("data")
    inner: dict[str, Any] = nested if isinstance(nested, dict) else {}
    if kind == "audio":
        encoded = inner.get("audio")
        if not isinstance(encoded, str):
            return TtsUnknown(raw=raw[:200])
        try:
            return TtsAudio(pcm=base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError):
            return TtsUnknown(raw=raw[:200])
    if kind == "start":
        request_id = data.get("request_id", inner.get("request_id"))
        return TtsStart(request_id=str(request_id) if request_id is not None else None)
    if kind == "complete":
        return TtsComplete()
    if kind == "error":
        # UNVERIFIED: re-check after P0.2-P0.4. The error text field name is not documented.
        message = data.get("message") or inner.get("message") or data.get("error") or "error"
        return TtsError(message=str(message))
    return TtsUnknown(raw=raw[:200])


class PcmAligner:
    """Holds back a dangling byte so every emitted chunk is whole int16 samples."""

    def __init__(self) -> None:
        self._carry = b""

    def feed(self, data: bytes) -> bytes:
        data = self._carry + data
        cut = len(data) - (len(data) % 2)
        self._carry = data[cut:]
        return data[:cut]

    def finish(self) -> int:
        """End of a sentence: returns how many bytes were dropped (0 or 1)."""
        dropped, self._carry = len(self._carry), b""
        return dropped


# --- the socket ------------------------------------------------------------------------------


class TtsFailed(Exception):
    """One sentence could not be synthesised (error message, timeout, or socket drop)."""


class TtsUnavailable(Exception):
    """Retry and backup voice both failed: use the pre-rendered line and end the call."""


class _Socket(Protocol):
    async def send(self, message: str | bytes) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


Connector = Callable[[str, dict[str, str]], Awaitable[_Socket]]


async def websocket_connector(url: str, headers: dict[str, str]) -> _Socket:
    conn: ClientConnection = await connect(
        url,
        additional_headers=headers,
        proxy=None,
        open_timeout=CONNECT_TIMEOUT_S,
        close_timeout=CLOSE_TIMEOUT_S,
    )
    return conn


class TimbreSocket:
    """Use as `async with TimbreSocket(...) as tts:`. Requests are serialised: one at a time."""

    def __init__(
        self,
        *,
        api_key: str,
        url: str = TIMBRE_URL,
        connector: Connector = websocket_connector,
        first_audio_timeout_s: float = FIRST_AUDIO_TIMEOUT_S,
        chunk_timeout_s: float = CHUNK_TIMEOUT_S,
    ) -> None:
        self._headers = {API_KEY_HEADER: api_key}
        self._url = url
        self._connector = connector
        self._first_timeout = first_audio_timeout_s
        self._chunk_timeout = chunk_timeout_s
        self._socket: _Socket | None = None
        self._dirty = False
        self._lock = asyncio.Lock()
        self.resets = 0
        self.bytes_dropped = 0

    async def __aenter__(self) -> "TimbreSocket":
        await self._ensure_open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._socket is not None:
            socket, self._socket = self._socket, None
            with contextlib.suppress(Exception):
                await socket.close()

    async def reset(self) -> None:
        """Drop any half-finished audio: close now, reopen lazily before the next sentence."""
        self.resets += 1
        self._dirty = False
        await self.close()

    async def _ensure_open(self) -> _Socket:
        if self._dirty:  # the last sentence was cut short or failed
            await self.reset()
        if self._socket is None:
            try:
                self._socket = await self._connector(self._url, self._headers)
            except (OSError, ConnectionClosed, InvalidHandshake, TimeoutError) as exc:
                # InvalidHandshake: HTTP 401/429/5xx on the upgrade (not an OSError).
                raise TtsFailed(f"timbre connect failed: {exc}") from exc
        return self._socket

    async def synthesize(
        self, text: str, voice: Voice, *, speed: float
    ) -> AsyncGenerator[bytes, None]:
        """Yield whole-sample PCM chunks for one sentence. Stop iterating to interrupt it."""
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=LOCK_TIMEOUT_S)
        except TimeoutError as exc:
            raise TtsFailed(f"timbre socket busy for {LOCK_TIMEOUT_S} s") from exc
        try:
            inner = self._synthesize_locked(text, voice, speed=speed)
            async with contextlib.aclosing(inner):
                async for chunk in inner:
                    yield chunk
        finally:
            self._lock.release()

    async def _synthesize_locked(
        self, text: str, voice: Voice, *, speed: float
    ) -> AsyncGenerator[bytes, None]:
        socket = await self._ensure_open()
        aligner = PcmAligner()
        finished = False
        try:
            try:
                await socket.send(build_request(text, voice, speed=speed))
            except ConnectionClosed as exc:
                raise TtsFailed(f"timbre socket closed: {exc}") from exc
            loop = asyncio.get_running_loop()
            # Deadline for the next audio: control or unknown messages do not extend it.
            deadline = loop.time() + self._first_timeout
            while True:
                try:
                    raw = await asyncio.wait_for(
                        socket.recv(), timeout=max(0.0, deadline - loop.time())
                    )
                except TimeoutError as exc:
                    raise TtsFailed("no timbre audio before the deadline") from exc
                except ConnectionClosed as exc:
                    raise TtsFailed(f"timbre socket closed: {exc}") from exc
                event = parse_message(raw) if isinstance(raw, str) else TtsUnknown(raw="<bytes>")
                if isinstance(event, TtsAudio):
                    deadline = loop.time() + self._chunk_timeout
                    pcm = aligner.feed(event.pcm)
                    if pcm:
                        yield pcm
                elif isinstance(event, TtsComplete):
                    self.bytes_dropped += aligner.finish()
                    finished = True
                    return
                elif isinstance(event, TtsError):
                    raise TtsFailed(f"timbre error: {event.message}")
                elif isinstance(event, TtsStart):
                    continue
                else:
                    log.warning("timbre: ignoring unexpected message %s", event)
        finally:
            if not finished:
                # Interrupted or failed mid-sentence: the server may still be sending this
                # sentence's audio. Never read it as the next sentence's.
                self._dirty = True
                if self._socket is socket:
                    self._socket = None
                with contextlib.suppress(Exception):
                    await socket.close()

    async def render(self, text: str, voice: Voice, *, speed: float) -> bytes:
        return b"".join([chunk async for chunk in self.synthesize(text, voice, speed=speed)])


async def speak(
    tts: TimbreSocket, text: str, voice: Voice, *, speed: float, backup: Voice | None
) -> AsyncGenerator[bytes, None]:
    """05: retry once, then the backup voice, then `TtsUnavailable`. No retry after audio."""
    attempts = [voice, voice] + ([backup] if backup is not None else [])
    last: TtsFailed | None = None
    for attempt_voice in attempts:
        produced = False
        try:
            stream = tts.synthesize(text, attempt_voice, speed=speed)
            async with contextlib.aclosing(stream):  # barge-in closes us -> closes the sentence
                async for chunk in stream:
                    produced = True
                    yield chunk
            return
        except TtsFailed as exc:
            if produced:
                raise TtsUnavailable(f"failed mid-sentence after audio: {exc}") from exc
            log.warning("timbre attempt with %s failed: %s", attempt_voice.name, exc)
            last = exc
    raise TtsUnavailable(f"all {len(attempts)} attempts failed: {last}") from last


# --- pre-rendered fixed lines ----------------------------------------------------------------


class LineRenderer(Protocol):
    async def render(self, text: str, voice: Voice, *, speed: float) -> bytes: ...


def cache_key(text: str, voice: Voice, *, speed: float) -> str:
    parts = [str(CACHE_VERSION), TIMBRE_MODEL, json.dumps(AUDIO_CONFIG, sort_keys=True)]
    parts += [voice.name, voice.language]
    parts += [f"{speed:.3f}", text]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


class PrerenderCache:
    """On-disk PCM cache for fixed lines, keyed by everything that changes the audio."""

    def __init__(self, directory: Path, renderer: LineRenderer) -> None:
        self._dir = directory
        self._renderer = renderer
        self.renders = 0

    def path_for(self, text: str, voice: Voice, *, speed: float) -> Path:
        return self._dir / f"{cache_key(text, voice, speed=speed)}.pcm"

    async def get(self, text: str, voice: Voice, *, speed: float = 1.0) -> bytes:
        path = self.path_for(text, voice, speed=speed)
        cached = await asyncio.to_thread(_read_if_exists, path)
        if cached is not None:
            return cached
        pcm = await self._renderer.render(text, voice, speed=speed)
        if not pcm:
            raise TtsFailed(f"empty audio for fixed line {text!r}")
        self.renders += 1
        await asyncio.to_thread(_write_atomic, path, pcm)
        return pcm

    async def warm(self, persona: Persona) -> dict[str, bytes]:
        """Render (or load) every fixed line for a persona. Call at startup, not mid-call."""
        voice = voice_for_persona(persona)
        lines = persona.lines.model_dump() | {"line_trouble": LINE_TROUBLE}
        return {name: await self.get(text, voice) for name, text in lines.items()}


def _read_if_exists(path: Path) -> bytes | None:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    return data or None


def _write_atomic(path: Path, data: bytes) -> None:
    """Unique temp file + fsync + rename: concurrent writers never publish a spliced file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.stem, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
