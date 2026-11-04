"""Prisma v3 realtime speech-to-text client (context doc 03 "Prisma v3.0", 08 "Pacing frames").

One `PrismaStream` per call, opened at call start and reused every turn:

- Config travels in **connection headers**, fixed for the session (`build_headers`).
- Audio goes up as binary frames of exactly 1,024 bytes (512 little-endian int16 samples),
  paced at real time on a deadline clock (`Pacer`), never `sleep(0.032)` per frame.
- On push-to-talk release the caller calls `commit()`: the remainder is padded with zeros to
  1,024 bytes, sent, then `{"type":"commit"}` forces the open segment closed.
- Server messages (`connected`, `speech_start`, `processing`, `transcript`, `error`) are parsed
  into typed events and handed out through `events()`. There are no partials.
- If the socket drops (anything but a 1008 config rejection), the stream reconnects and replays
  what Prisma has not yet answered: up to ~1 s of the open segment's audio, and the commit if it
  was sent (05 "Failure handling"). A send can succeed into the socket buffer just before the
  close arrives, so "sent" never means "heard"; only a transcript acknowledges a segment.

Facts marked UNVERIFIED in 03 are single constants here, tagged "UNVERIFIED: re-check after
P0.2-P0.4".
"""

import asyncio
import contextlib
import json
import logging
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from kalpakka.clock import Clock
from kalpakka.personas import MAX_KEYTERMS, Persona

log = logging.getLogger(__name__)

PRISMA_URL: Final = "wss://api.vachana.ai/stt/v3/stream"
PRISMA_MODEL: Final = "prisma-v3.0"
FRAME_BYTES: Final = 1024  # 512 int16 samples
SAMPLE_BYTES: Final = 2
COMMIT_MESSAGE: Final = '{"type":"commit"}'
CONFIG_REJECTED_CLOSE: Final = 1008
# UNVERIFIED: re-check after P0.2-P0.4. 03 marks the keyterms separator as unverified.
KEYTERMS_SEPARATOR: Final = ","
# UNVERIFIED: re-check after P0.2-P0.4. Which `connected` fields echo the config is not documented;
# we compare only the fields that are present.
ECHO_FIELDS: Final = ("model", "language_code", "format")
RESEND_FRAMES: Final = 32  # ~1 s of 16 kHz audio replayed after a reconnect
CONNECTED_TIMEOUT_S: Final = 5.0
MAX_RECONNECTS: Final = 3
RECONNECT_BACKOFF_S: Final = (0.2, 0.5, 1.0)
# UNVERIFIED: re-check after P0.2-P0.4. Whether Prisma answers a commit when no segment is open
# (no speech, or VAD already closed the segment) is not documented. We then wait this long
# after the commit before taking what we have (possibly nothing).
NO_SPEECH_GRACE_S: Final = 0.5
# Ours, tunable from P0.2: after the last segment event, wait this long for a further
# `speech_start` (VAD can close segment 1 before segment 2's `speech_start` arrives).
SEGMENT_SETTLE_S: Final = 0.05
# UNVERIFIED: re-check after P0.2-P0.4. 03 does not say every `speech_start` gets a `transcript`
# (a cough may open a segment that never yields text). After a commit, if nothing arrives for
# this long, open segments are treated as closed so the call cannot wedge on `not_heard`.
COMMIT_ANSWER_TIMEOUT_S: Final = 1.5
# On an acknowledged segment, keep this many recent frames for replay: VAD closes a segment only
# after `x-min-silence-ms` (600 ms = ~19 frames) of silence, so older frames are answered speech
# and the kept tail is silence (replaying it repeats no words).
ACK_LOOKBACK_FRAMES: Final = 19
SEND_TIMEOUT_S: Final = 2.0
EVENT_QUEUE_MAX: Final = 256  # recent events for `events()`/`transcript()`; oldest dropped


# --- config and headers ----------------------------------------------------------------------


class SttConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    language_code: str  # "hi-IN,en-IN" or "indic-auto"
    keyterms: Annotated[list[str], Field(max_length=MAX_KEYTERMS)] = Field(default_factory=list)
    request_id: str
    output_format: Literal["transcribe", "verbatim", "codemix", "transliterate"] = "transcribe"
    sample_rate: Literal[16000, 8000] = 16000
    min_silence_ms: int = 600
    idle_timeout_sec: int = 300


def config_for_persona(persona: Persona, *, call_id: str, sample_rate: int = 16000) -> SttConfig:
    return SttConfig.model_validate(
        {
            "language_code": persona.speech.stt_language_code,
            "keyterms": list(persona.speech.keyterms),
            "request_id": call_id,
            "sample_rate": sample_rate,
        }
    )


def build_headers(api_key: str, config: SttConfig) -> dict[str, str]:
    headers = {
        "x-api-key-id": api_key,
        "model": PRISMA_MODEL,
        "language_code": config.language_code,
        "format": config.output_format,
        "x-sample-rate": str(config.sample_rate),
        "x-min-silence-ms": str(config.min_silence_ms),
        "x-idle-timeout-sec": str(config.idle_timeout_sec),
        "x-api-request-id": config.request_id,
    }
    if config.keyterms:
        headers["keyterms"] = KEYTERMS_SEPARATOR.join(config.keyterms)
    return headers


# --- frames and pacing -----------------------------------------------------------------------


class FramePacker:
    """Turns arbitrary byte chunks from the browser into exact 1,024-byte frames."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf.extend(data)
        frames: list[bytes] = []
        while len(self._buf) >= FRAME_BYTES:
            frames.append(bytes(self._buf[:FRAME_BYTES]))
            del self._buf[:FRAME_BYTES]
        return frames

    def flush(self) -> bytes | None:
        """The remainder padded with zeros (silence) to a full frame; None if nothing is left."""
        if not self._buf:
            return None
        frame = bytes(self._buf) + b"\x00" * (FRAME_BYTES - len(self._buf))
        self._buf.clear()
        return frame


Sleep = Callable[[float], Awaitable[None]]


class Pacer:
    """Deadline pacing: frame n is due at start + n * frame_s, so send time never accumulates.

    After an idle gap (deadline already in the past) the schedule restarts from now instead of
    bursting the backlog: Prisma expects real-time pace.
    """

    def __init__(self, *, clock: Clock, sleep: Sleep, frame_s: float) -> None:
        self._clock = clock
        self._sleep = sleep
        self._frame_s = frame_s
        self._deadline: float | None = None

    @staticmethod
    def frame_seconds(sample_rate: int) -> float:
        return FRAME_BYTES / SAMPLE_BYTES / sample_rate

    async def wait(self) -> None:
        now = self._clock.monotonic()
        if self._deadline is None or self._deadline < now:
            self._deadline = now  # first frame, or we were idle: send now
        else:
            await self._sleep(self._deadline - now)
        self._deadline += self._frame_s


# --- server messages -------------------------------------------------------------------------


class _Msg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class SttConnected(_Msg):
    echo: dict[str, str] = Field(default_factory=dict)


class SttSpeechStart(_Msg):
    pass


class SttProcessing(_Msg):
    pass


class SttTranscript(_Msg):
    text: str
    detected_language_code: str | None = None
    latency: float | None = None
    segment_index: int | None = None


class SttError(_Msg):
    message: str
    code: str | int | None = None


class SttUnknown(_Msg):
    raw: str = ""


SttEvent = SttConnected | SttSpeechStart | SttProcessing | SttTranscript | SttError | SttUnknown


def parse_message(raw: str) -> SttEvent:
    try:
        data: Any = json.loads(raw)  # Any: untrusted JSON, validated field by field below
    except json.JSONDecodeError:
        return SttUnknown(raw=raw[:200])
    if not isinstance(data, dict):
        return SttUnknown(raw=raw[:200])
    # UNVERIFIED: re-check after P0.2-P0.4. Fields may sit at the top level or under "data".
    fields: dict[str, Any] = dict(data)
    if isinstance(data.get("data"), dict):
        fields = {**data["data"], **{k: v for k, v in data.items() if k != "data"}}
    kind = fields.get("type")
    try:
        if kind == "transcript":
            return SttTranscript.model_validate(fields)
        if kind == "speech_start":
            return SttSpeechStart()
        if kind == "processing":
            return SttProcessing()
        if kind == "connected":
            echo = {k: str(v) for k, v in fields.items() if k in ECHO_FIELDS}
            return SttConnected(echo=echo)
        if kind == "error":
            # UNVERIFIED: re-check after P0.2-P0.4. The error text field name is not documented.
            message = fields.get("message") or fields.get("error") or "unknown error"
            return SttError(message=str(message), code=fields.get("code"))
    except ValidationError:
        return SttUnknown(raw=raw[:200])
    return SttUnknown(raw=raw[:200])


# --- the stream ------------------------------------------------------------------------------


class SttConfigRejected(Exception):
    """Prisma refused the connection config (1008 or a first `error`). A bug: never retried."""


class SttUnavailable(Exception):
    """The stream dropped and could not be re-established."""


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
        open_timeout=CONNECTED_TIMEOUT_S,
        close_timeout=0.5,
        ping_interval=10,
        ping_timeout=10,
    )
    return conn


_COMMIT: Final = object()
_Item = bytes | object


class PrismaStream:
    """Use as `async with PrismaStream(...) as stt:`; feed audio, commit, read `events()`."""

    def __init__(
        self,
        *,
        api_key: str,
        config: SttConfig,
        clock: Clock,
        sleep: Sleep = asyncio.sleep,
        url: str = PRISMA_URL,
        connector: Connector = websocket_connector,
    ) -> None:
        self._headers = build_headers(api_key, config)
        self._config = config
        self._clock = clock
        self._sleep = sleep
        self._url = url
        self._connector = connector
        self._packer = FramePacker()
        self._outbox: asyncio.Queue[_Item] = asyncio.Queue()
        self._events: asyncio.Queue[SttEvent | BaseException] = asyncio.Queue(EVENT_QUEUE_MAX)
        # Replay buffer: (sequence number, frame or commit) not yet answered by Prisma.
        self._unacked: deque[tuple[int, _Item]] = deque()
        self._seq = 0
        self._segment_marks: deque[int] = deque()  # seq at each open segment's speech_start
        self._socket: _Socket | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._reconnect_lock = asyncio.Lock()
        self._generation = 0
        self.reconnects = 0
        self.frames_sent = 0
        self._closed = False
        # Utterance tracking: VAD may split one push-to-talk utterance into several segments,
        # each opened by `speech_start` and closed by one `transcript`.
        self._open_segments = 0
        self._finals: list[SttTranscript] = []
        self._finals_total = 0
        self._commits_queued = 0  # commit() called, not yet on the wire
        self._last_segment_event = 0.0  # loop time of the last speech_start / transcript
        self._commit_sent_at: float | None = None  # loop time the latest commit went out
        self._commit_mark = 0  # finals received before that commit
        self._changed = asyncio.Event()
        self._failure: BaseException | None = None

    async def __aenter__(self) -> "PrismaStream":
        await self._open()
        self._tasks = [
            asyncio.create_task(self._send_loop(), name="prisma-send"),
            asyncio.create_task(self._recv_loop(), name="prisma-recv"),
        ]
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        self._closed = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self._socket is not None:
            with contextlib.suppress(Exception):
                await self._socket.close()

    # --- public API ---

    def send_audio(self, pcm: bytes) -> None:
        if self._failure is not None:
            return  # the stream is gone; `utterance()` reports it
        for frame in self._packer.feed(pcm):
            self._outbox.put_nowait(frame)

    def commit(self) -> None:
        """Push-to-talk released: flush the padded remainder, then close the segment."""
        tail = self._packer.flush()
        if tail is not None:
            self._outbox.put_nowait(tail)
        self._commits_queued += 1
        self._outbox.put_nowait(_COMMIT)

    async def next_event(self, timeout_s: float) -> SttEvent:
        item = await asyncio.wait_for(self._events.get(), timeout=timeout_s)
        if isinstance(item, BaseException):
            raise item
        return item

    async def events(self) -> AsyncIterator[SttEvent]:
        while True:
            item = await self._events.get()
            if isinstance(item, BaseException):
                raise item
            yield item

    async def utterance(self, timeout_s: float) -> SttTranscript:
        """After `commit()`: every final since the last utterance, joined, once no segment is
        still open. A final that arrives after a timeout joins the next utterance instead of
        shifting every later turn by one."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            if self._failure is not None:
                raise self._failure
            wake = deadline
            sent_at = self._commit_sent_at
            if self._commits_queued == 0 and sent_at is not None and self._open_segments > 0:
                quiet_since = max(sent_at, self._last_segment_event)
                if loop.time() - quiet_since > COMMIT_ANSWER_TIMEOUT_S:
                    log.warning("prisma left %d segment(s) unanswered", self._open_segments)
                    self._open_segments = 0
                    self._segment_marks.clear()
                else:
                    wake = min(deadline, quiet_since + COMMIT_ANSWER_TIMEOUT_S + 0.01)
            if self._commits_queued == 0 and sent_at is not None and self._open_segments == 0:
                # Prisma answers in order, so a final after the commit closes the utterance,
                # unless another segment's speech_start is still on its way (settle window).
                if self._finals_total > self._commit_mark:
                    settled_at = self._last_segment_event + SEGMENT_SETTLE_S
                    if loop.time() >= settled_at:
                        return self._take_finals()
                    wake = min(deadline, settled_at)
                    if loop.time() >= deadline:
                        raise TimeoutError(f"no final transcript in {timeout_s} s")
                    self._changed.clear()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            self._changed.wait(), timeout=max(0.0, wake - loop.time())
                        )
                    continue
                # No final after the commit. If VAD already closed the segment (finals waiting),
                # only settle; otherwise wait the no-speech grace.
                pause = SEGMENT_SETTLE_S if self._finals else NO_SPEECH_GRACE_S
                quiet_until = max(sent_at, self._last_segment_event) + pause
                if loop.time() >= quiet_until:
                    return self._take_finals()
                wake = min(deadline, quiet_until)
            if loop.time() >= deadline:
                raise TimeoutError(f"no final transcript in {timeout_s} s")
            self._changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), timeout=max(0.0, wake - loop.time()))

    def _take_finals(self) -> SttTranscript:
        finals, self._finals = self._finals, []
        self._commit_sent_at = None
        if not finals:
            return SttTranscript(text="")
        text = " ".join(f.text.strip() for f in finals if f.text.strip())
        return SttTranscript(
            text=text,
            detected_language_code=finals[-1].detected_language_code,
            latency=finals[-1].latency,
            segment_index=finals[-1].segment_index,
        )

    async def transcript(self, timeout_s: float) -> SttTranscript:
        """Wait for the next final transcript, skipping control messages."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            event = await self.next_event(max(0.0, deadline - loop.time()))
            if isinstance(event, SttTranscript):
                return event

    # --- connection ---

    async def _open(self) -> None:
        socket = await self._connector(self._url, self._headers)
        try:
            await self._handshake(socket)
        except BaseException:  # timeout, cancel, rejection: never leak a Prisma session
            with contextlib.suppress(Exception):
                await socket.close()
            raise
        self._socket = socket
        self._generation += 1
        self._open_segments = 0  # a new connection has no open segment; replays reopen it
        self._segment_marks.clear()

    async def _handshake(self, socket: _Socket) -> None:
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=CONNECTED_TIMEOUT_S)
        except ConnectionClosed as exc:
            await socket.close()
            if exc.rcvd is not None and exc.rcvd.code == CONFIG_REJECTED_CLOSE:
                raise SttConfigRejected(exc.rcvd.reason or "closed 1008") from exc
            raise SttUnavailable("closed before connected") from exc
        first = parse_message(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
        if isinstance(first, SttError):
            await socket.close()
            raise SttConfigRejected(first.message)
        if not isinstance(first, SttConnected):
            await socket.close()
            raise SttUnavailable(f"expected 'connected', got {type(first).__name__}")
        wanted = {
            "model": PRISMA_MODEL,
            "language_code": self._config.language_code,
            "format": self._config.output_format,
        }
        mismatched = {k: v for k, v in first.echo.items() if wanted.get(k) not in (None, v)}
        if mismatched:
            await socket.close()
            raise SttConfigRejected(f"connected echoed a different config: {mismatched}")

    async def _reconnect(self, generation: int) -> None:
        async with self._reconnect_lock:
            if generation != self._generation:
                return  # the other loop already reconnected
            for attempt in range(MAX_RECONNECTS):
                await self._sleep(RECONNECT_BACKOFF_S[min(attempt, len(RECONNECT_BACKOFF_S) - 1)])
                try:
                    await self._open()
                except SttConfigRejected:
                    raise
                except (OSError, ConnectionClosed, SttUnavailable, TimeoutError) as exc:
                    log.warning("prisma reconnect %d failed: %s", attempt + 1, exc)
                    continue
                replay = [item for _, item in self._unacked]
                log.info("prisma reconnected; replaying %d items", len(replay))
                assert self._socket is not None
                try:
                    await self._replay(self._socket, replay)
                except (ConnectionClosed, TimeoutError) as exc:
                    log.warning("prisma dropped again during replay: %s", exc)
                    continue
                self.reconnects += 1
                return
            raise SttUnavailable(f"prisma stream lost after {MAX_RECONNECTS} reconnect attempts")

    async def _replay(self, socket: _Socket, replay: list[_Item]) -> None:
        pacer = self._new_pacer()
        for item in replay:
            if isinstance(item, bytes):
                await pacer.wait()
                await asyncio.wait_for(socket.send(item), SEND_TIMEOUT_S)
            else:
                await asyncio.wait_for(socket.send(COMMIT_MESSAGE), SEND_TIMEOUT_S)

    def _new_pacer(self) -> Pacer:
        return Pacer(
            clock=self._clock,
            sleep=self._sleep,
            frame_s=Pacer.frame_seconds(self._config.sample_rate),
        )

    # --- loops ---

    async def _send_loop(self) -> None:
        pacer = self._new_pacer()
        try:
            while True:
                item = await self._outbox.get()
                while True:
                    if isinstance(item, bytes):
                        await pacer.wait()
                    dropped: ConnectionClosed | None = None
                    # Under the reconnect lock: a replay in progress finishes first, so new
                    # audio never interleaves with replayed audio.
                    async with self._reconnect_lock:
                        generation = self._generation
                        socket = self._socket
                        assert socket is not None
                        try:
                            await self._send_item(socket, item)
                        except ConnectionClosed as exc:
                            dropped = exc
                        except TimeoutError:
                            with contextlib.suppress(Exception):
                                await socket.close()  # stuck: force the reconnect path
                            dropped = ConnectionClosed(None, None)
                    if dropped is None:
                        break
                    await self._on_drop(dropped, generation)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._fail(exc)

    async def _send_item(self, socket: _Socket, item: _Item) -> None:
        if item is _COMMIT:
            await asyncio.wait_for(socket.send(COMMIT_MESSAGE), SEND_TIMEOUT_S)
            self._commits_queued = max(0, self._commits_queued - 1)
            self._commit_mark = self._finals_total
            self._commit_sent_at = asyncio.get_running_loop().time()
            self._changed.set()
        else:
            assert isinstance(item, bytes)
            await asyncio.wait_for(socket.send(item), SEND_TIMEOUT_S)
            self.frames_sent += 1
        self._remember(item)

    def _emit(self, item: SttEvent | BaseException) -> None:
        if self._events.full():
            self._events.get_nowait()  # nobody is reading `events()`: keep only recent ones
        self._events.put_nowait(item)

    def _fail(self, exc: BaseException) -> None:
        """One loop gave up: record it and stop the other loop too."""
        self._failure = exc
        self._changed.set()
        self._emit(exc)
        current = asyncio.current_task()
        for task in self._tasks:
            if task is not current:
                task.cancel()

    async def _recv_loop(self) -> None:
        try:
            while True:
                generation = self._generation
                assert self._socket is not None
                try:
                    raw = await self._socket.recv()
                except ConnectionClosed as exc:
                    await self._on_drop(exc, generation)
                    continue
                text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
                event = parse_message(text)
                if isinstance(event, SttSpeechStart):
                    self._open_segments += 1
                    self._segment_marks.append(self._seq)
                    self._last_segment_event = asyncio.get_running_loop().time()
                    self._changed.set()
                elif isinstance(event, SttTranscript):
                    self._acknowledge_segment()
                    self._open_segments = max(0, self._open_segments - 1)
                    self._finals.append(event)
                    self._finals_total += 1
                    self._last_segment_event = asyncio.get_running_loop().time()
                    self._changed.set()
                self._emit(event)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._fail(exc)

    def _remember(self, item: _Item) -> None:
        self._seq += 1
        self._unacked.append((self._seq, item))
        # Keep at most RESEND_FRAMES frames (~1 s): a drop deeper into a long utterance loses
        # the older audio (documented limit). Commits always stay.
        frames = sum(isinstance(i, bytes) for _, i in self._unacked)
        while frames > RESEND_FRAMES:
            for index, (_, old) in enumerate(self._unacked):
                if isinstance(old, bytes):
                    del self._unacked[index]
                    frames -= 1
                    break

    def _acknowledge_segment(self) -> None:
        """A transcript answers the oldest open segment. Everything sent before the next open
        segment's start (or, with none open, before the last ~600 ms) is answered: drop it,
        frames and commits alike."""
        if self._segment_marks:
            self._segment_marks.popleft()
        commits = [seq for seq, item in self._unacked if item is _COMMIT]
        if self._segment_marks:
            cut = self._segment_marks[0] - ACK_LOOKBACK_FRAMES
        elif commits:
            cut = commits[-1]  # a commit closed it: everything up to that commit is answered
        else:
            cut = self._seq - ACK_LOOKBACK_FRAMES  # VAD closed it: keep the silent tail
        while self._unacked and self._unacked[0][0] <= cut:
            self._unacked.popleft()

    async def _on_drop(self, exc: ConnectionClosed, generation: int) -> None:
        if self._closed:
            raise asyncio.CancelledError
        if exc.rcvd is not None and exc.rcvd.code == CONFIG_REJECTED_CLOSE:
            raise SttConfigRejected(exc.rcvd.reason or "closed 1008") from exc
        log.warning("prisma stream dropped (%s); reconnecting", exc)
        await self._reconnect(generation)
