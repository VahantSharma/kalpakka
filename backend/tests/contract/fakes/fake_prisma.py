"""A local fake of Prisma v3 realtime STT, following the documented protocol (context doc 03).

- Config arrives in connection headers. A missing key or wrong model gets an `error` message,
  then close code 1008, as the docs describe for bad config.
- On open it sends `connected`, echoing model, language_code and format.
- Binary frames: every frame is recorded; any frame that is not exactly 1,024 bytes is recorded
  in `bad_frames` (the real server's reaction is unverified, so tests assert there are none).
- The first frame of a segment triggers `speech_start`. `{"type":"commit"}` triggers `processing`
  and then a `transcript` whose text is the next scripted line.
- `drop_after_frames` closes the first connection abruptly (code 1011) to test reconnects.
"""

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

FRAME_BYTES = 1024


@dataclass
class FakePrisma:
    api_key: str = "test-key"
    transcripts: list[str] = field(default_factory=list)
    detected_language: str = "hi-IN"
    drop_after_frames: int | None = None
    transcript_delay_s: float = 0.0
    echo_override: dict[str, str] = field(default_factory=dict)
    vad_split_after_frames: int | None = None  # VAD closes a segment mid-utterance (pause)
    drop_plan: dict[int, int] = field(default_factory=dict)  # connection no. -> drop after frames
    swallow_next_segments: int = 0  # segments that get `speech_start` but never a transcript

    connections: list[dict[str, str]] = field(default_factory=list)
    frames: list[bytes] = field(default_factory=list)
    bad_frames: list[int] = field(default_factory=list)
    commits: int = 0
    received: list[Any] = field(default_factory=list)  # Any: str control messages or bytes
    _server: Server | None = None
    port: int = 0
    _segment: int = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/stt/v3/stream"

    async def start(self) -> None:
        self._server = await serve(self._handle, "127.0.0.1", 0)
        sockets = list(self._server.sockets)
        self.port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 2)

    async def _handle(self, ws: ServerConnection) -> None:
        assert ws.request is not None
        headers = {k.lower(): v for k, v in ws.request.headers.raw_items()}
        self.connections.append(headers)
        first_connection = len(self.connections) == 1
        if headers.get("x-api-key-id") != self.api_key or headers.get("model") != "prisma-v3.0":
            await ws.send(json.dumps({"type": "error", "message": "invalid api key or model"}))
            await ws.close(1008, "bad config")
            return
        echo = {
            "model": headers.get("model", ""),
            "language_code": headers.get("language_code", ""),
            "format": headers.get("format", ""),
            **self.echo_override,
        }
        await ws.send(json.dumps({"type": "connected", **echo}))
        in_segment = False
        segment_frames = 0
        frames_here = 0
        try:
            async for message in ws:
                self.received.append(message)
                if isinstance(message, bytes):
                    self.frames.append(message)
                    frames_here += 1
                    if len(message) != FRAME_BYTES:
                        self.bad_frames.append(len(message))
                    if not in_segment:
                        in_segment = True
                        segment_frames = 0
                        await ws.send(json.dumps({"type": "speech_start"}))
                    segment_frames += 1
                    if self.vad_split_after_frames == segment_frames:
                        in_segment = False  # a pause: VAD ends the segment before the commit
                        await self._final(ws)
                    planned = self.drop_plan.get(len(self.connections))
                    if (planned is not None and frames_here >= planned) or (
                        first_connection
                        and self.drop_after_frames is not None
                        and frames_here >= self.drop_after_frames
                    ):
                        await ws.close(1011, "fake drop")
                        return
                    continue
                payload = json.loads(message)
                if payload.get("type") == "commit":
                    self.commits += 1
                    if in_segment:
                        in_segment = False
                        if self.swallow_next_segments > 0:
                            self.swallow_next_segments -= 1  # a cough: no transcript, ever
                            continue
                        await self._final(ws)
        except ConnectionClosed:
            return

    async def _final(self, ws: ServerConnection) -> None:
        await ws.send(json.dumps({"type": "processing"}))
        await asyncio.sleep(self.transcript_delay_s)
        text = self.transcripts.pop(0) if self.transcripts else ""
        await ws.send(
            json.dumps(
                {
                    "type": "transcript",
                    "text": text,
                    "detected_language_code": self.detected_language,
                    "latency": 0.3,
                    "segment_index": self._segment,
                },
                ensure_ascii=False,
            )
        )
        self._segment += 1
