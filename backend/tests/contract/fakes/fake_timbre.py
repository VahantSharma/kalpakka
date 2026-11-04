"""A local fake of Timbre v3 realtime TTS, following the documented protocol (context doc 03).

- Auth: `X-API-Key-ID` header; a wrong key gets an `error` message and the socket is closed.
- Each JSON request (`text`, `voice`, `model`, `language`, `speed`, `audio_config`) is answered
  with `start` (`request_id`), several `audio` messages (base64 PCM in `data.audio`) and
  `complete`. Audio is deterministic: 2 bytes per character of text, split into chunks of
  `chunk_bytes` (odd by default, to exercise sample re-alignment).
- Scripting: `fail_next` answers the next N requests with `error`; `stall_next` sends `start`
  and then nothing for the next N requests; `chunk_delay_s` slows audio down so tests can
  interrupt mid-sentence. Every request body is recorded per connection.
"""

import asyncio
import base64
import contextlib
import json
from dataclasses import dataclass, field
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

VOICES = frozenset({"Devansh", "Lakshmi", "Kalpana", "Shubham"})


def fake_pcm(text: str) -> bytes:
    """The audio the fake returns for a sentence: 2 bytes per character."""
    seed = sum(text.encode("utf-8")) % 251
    return bytes((seed + i) % 256 for i in range(2 * len(text)))


@dataclass
class FakeTimbre:
    api_key: str = "test-key"
    chunk_bytes: int = 7
    chunk_delay_s: float = 0.0
    fail_next: int = 0
    stall_next: int = 0
    reject_next_connections: int = 0  # answer the HTTP upgrade with 503
    send_binary_next: int = 0  # answer with a non-UTF-8 binary frame before the audio
    drop_mid_sentence_next: int = 0  # close the socket after the first audio chunk

    requests: list[dict[str, Any]] = field(default_factory=list)  # Any: JSON bodies
    connections: int = 0
    headers: list[dict[str, str]] = field(default_factory=list)
    _server: Server | None = None
    port: int = 0
    _ids: int = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/api/v1/tts"

    async def start(self) -> None:
        self._server = await serve(
            self._handle, "127.0.0.1", 0, process_request=self._process_request
        )
        self.port = next(iter(self._server.sockets)).getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 2)

    def _process_request(self, ws: ServerConnection, request: Request) -> Response | None:
        if self.reject_next_connections > 0:
            self.reject_next_connections -= 1
            self.connections += 1
            return Response(503, "Service Unavailable", Headers(), b"busy")
        return None

    async def _handle(self, ws: ServerConnection) -> None:
        assert ws.request is not None
        headers = {k.lower(): v for k, v in ws.request.headers.raw_items()}
        self.headers.append(headers)
        self.connections += 1
        if headers.get("x-api-key-id") != self.api_key:
            await ws.send(json.dumps({"type": "error", "message": "invalid api key"}))
            await ws.close(1008, "auth")
            return
        try:
            async for message in ws:
                body = json.loads(message)
                body["_connection"] = self.connections
                self.requests.append(body)
                await self._answer(ws, body)
        except ConnectionClosed:
            return

    async def _answer(self, ws: ServerConnection, body: dict[str, Any]) -> None:
        self._ids += 1
        if self.fail_next > 0:
            self.fail_next -= 1
            await ws.send(json.dumps({"type": "error", "message": "synthesis failed"}))
            return
        problems = [k for k in ("text", "voice", "model", "language") if not body.get(k)]
        if problems or body.get("model") != "timbre-v3.0" or body.get("voice") not in VOICES:
            await ws.send(json.dumps({"type": "error", "message": f"bad request {problems}"}))
            return
        await ws.send(json.dumps({"type": "start", "request_id": f"req-{self._ids}"}))
        if self.send_binary_next > 0:
            self.send_binary_next -= 1
            await ws.send(b"\xff\xfe\xfd")
        if self.stall_next > 0:
            self.stall_next -= 1
            return
        pcm = fake_pcm(body["text"])
        for i in range(0, len(pcm), self.chunk_bytes):
            chunk = base64.b64encode(pcm[i : i + self.chunk_bytes]).decode()
            await ws.send(json.dumps({"type": "audio", "data": {"audio": chunk}}))
            if self.drop_mid_sentence_next > 0:
                self.drop_mid_sentence_next -= 1
                await ws.close(1011, "fake drop mid-sentence")
                return
            await asyncio.sleep(self.chunk_delay_s)
        await ws.send(json.dumps({"type": "complete"}))
