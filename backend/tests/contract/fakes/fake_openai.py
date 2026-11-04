"""A local fake of an OpenAI-compatible `/v1/chat/completions` streaming server.

It speaks the documented wire format (Server-Sent Events of `chat.completion.chunk` objects ending
in `data: [DONE]`) that vLLM, SGLang and llama.cpp's server all expose, on 127.0.0.1 with a random
port. Tests script the reply (pieces, delays, error status) and inspect every request body.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse


@dataclass
class Script:
    pieces: list[str] = field(default_factory=list)
    first_delay_s: float = 0.0
    piece_delay_s: float = 0.0
    status: int = 200


@dataclass
class FakeOpenAI:
    scripts: list[Script] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    stream_closed_early: asyncio.Event = field(default_factory=asyncio.Event)
    _server: uvicorn.Server | None = None
    _task: asyncio.Task[None] | None = None
    port: int = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/v1/chat/completions")
        async def completions(request: Request) -> Response:
            body = await request.json()
            self.requests.append(body)
            self.headers.append(dict(request.headers))
            script = self.scripts.pop(0) if self.scripts else Script(pieces=["ok"])
            if script.status != 200:
                return JSONResponse(
                    {"error": {"message": "scripted error", "type": "fake", "code": script.status}},
                    status_code=script.status,
                )
            return StreamingResponse(
                self._sse(body["model"], script), media_type="text/event-stream"
            )

        return app

    async def _sse(self, model: str, script: Script) -> AsyncIterator[str]:
        def chunk(delta: dict[str, str], finish: str | None = None) -> str:
            payload = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        completed = False
        try:
            yield chunk({"role": "assistant"})
            await asyncio.sleep(script.first_delay_s)
            for piece in script.pieces:
                yield chunk({"content": piece})
                await asyncio.sleep(script.piece_delay_s)
            yield chunk({}, finish="stop")
            yield "data: [DONE]\n\n"
            completed = True
        finally:
            if not completed:
                self.stream_closed_early.set()

    async def start(self) -> None:
        config = uvicorn.Config(
            self.app(), host="127.0.0.1", port=0, log_level="warning", lifespan="off"
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        for _ in range(200):
            if self._server.started:
                break
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("fake OpenAI server did not start")
        self.port = self._server.servers[0].sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None and self._task is not None:
            self._server.should_exit = True
            await asyncio.wait_for(self._task, timeout=5)
