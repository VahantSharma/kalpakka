"""A whole call through the real app, real clients and fake servers (no network, no keys).

Browser (websockets client) -> FastAPI `/call` -> CallSession -> PrismaStream -> fake Prisma,
TimbreSocket -> fake Timbre, LLMClient -> fake OpenAI-compatible server. Agent audio is sent at
real-time pace in 1,024-byte frames. Content (persona, prompt, lexicon) is the real repo content.
"""

import asyncio
import contextlib
import datetime as dt
import json
import math
import struct
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from websockets.asyncio.client import ClientConnection, connect

from kalpakka.admission import Admission, DailyCaps, LiveHours
from kalpakka.brain import load_prompt
from kalpakka.clock import SystemClock
from kalpakka.config import REPO_ROOT
from kalpakka.db import Database
from kalpakka.gnani_stt import PrismaStream, config_for_persona
from kalpakka.gnani_tts import PrerenderCache, TimbreSocket
from kalpakka.llm import ChatTemplateThinkingAdapter, LLMClient
from kalpakka.personas import Persona, load_personas
from kalpakka.server import AppContext, CachedLines, create_app
from kalpakka.session import CallDeps, LexiconAnalyzer, LlmReplies, SessionConfig
from kalpakka.tripwire import LexiconTripwire, load_catalogue, load_lexicon
from tests.contract.fakes.fake_openai import FakeOpenAI, Script
from tests.contract.fakes.fake_prisma import FakePrisma
from tests.contract.fakes.fake_timbre import FakeTimbre, fake_pcm

CONTENT = REPO_ROOT / "content"


def WALL() -> dt.datetime:
    return dt.datetime(2026, 11, 9, 5, 30, tzinfo=dt.UTC)


ORIGIN = "http://localhost:3000"
AGENT_LINE = "Ramesh ji, main SwiftFin se bol raha hoon. ₹12,500 overdue hai, sab theek hai?"
THREAT_LINE = "Paise nahi diye toh police bhej dunga"
REPLY = (
    "Arre sir, kya bataun, auto teen hafte se garage mein pada tha. "
    "Main abhi ₹4,000 de sakta hoon. "
    '###STATE### {"mood_delta": -5, "trust_delta": 10, '
    '"promise": {"amount": 4000, "date": "2026-10-28"}}'
)


def tone(seconds: float, rate: int = 16000) -> bytes:
    """A 220 Hz tone as little-endian int16: stands in for recorded agent speech."""
    n = int(seconds * rate)
    return struct.pack(
        f"<{n}h", *(int(8000 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(n))
    )


class Stack:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.prisma = FakePrisma(transcripts=[AGENT_LINE, THREAT_LINE])
        self.timbre = FakeTimbre()
        self.llm = FakeOpenAI(
            scripts=[
                Script(pieces=_pieces(REPLY)),
                Script(
                    pieces=_pieces(
                        "Aap aise baat karoge toh main phone rakh deta hoon. "
                        '###STATE### {"mood_delta": 15}'
                    )
                ),
            ]
        )
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task[None] | None = None
        self.port = 0
        self.db: Database | None = None

    async def start(self) -> None:
        await self.prisma.start()
        await self.timbre.start()
        await self.llm.start()
        clock = SystemClock()
        self.db = await Database.open(self.tmp / "k.db")
        personas = load_personas(CONTENT / "personas")
        catalogue = load_catalogue(CONTENT / "rules" / "catalogue.yaml")
        lexicon = load_lexicon(CONTENT / "rules" / "lexicon.yaml", catalogue)
        llm = LLMClient(
            base_url=self.llm.base_url,
            api_key="k",
            model="evon",
            adapter=ChatTemplateThinkingAdapter(),
        )
        renderer = TimbreSocket(api_key="test-key", url=self.timbre.url)
        cache = PrerenderCache(self.tmp / "prerender", renderer)
        for persona in personas.values():
            await cache.warm(persona)

        def open_stt(persona: Persona, call_id: str) -> PrismaStream:
            return PrismaStream(
                api_key="test-key",
                config=config_for_persona(persona, call_id=call_id),
                clock=clock,
                url=self.prisma.url,
            )

        deps = CallDeps(
            personas=personas,
            system_prompt=load_prompt(CONTENT / "prompts" / "borrower-system.md"),
            open_stt=open_stt,
            open_tts=lambda: TimbreSocket(api_key="test-key", url=self.timbre.url),
            replies=LlmReplies(llm, clock),
            lines=CachedLines(cache),
            db=self.db,
            clock=clock,
            wall=lambda: dt.datetime(2026, 11, 9, 5, 30, tzinfo=dt.UTC),
            seeds=lambda: 7,
            config=SessionConfig(sim_date=dt.date(2026, 10, 28), time_cap_s=None, silence_s=30),
            analyzer=LexiconAnalyzer(LexiconTripwire(lexicon)),
        )
        context = AppContext(
            deps=deps,
            admission=Admission(
                max_concurrent=1,
                max_queue=5,
                avg_call_s=240,
                caps=DailyCaps(per_ip=50, total=100, wall=WALL),
                hours=LiveHours(windows=(), wall=WALL, always_open=True),
            ),
            allowed_origins=frozenset({ORIGIN}),
            new_call_id=iter(["call-e2e", "call-e2e-2", "call-e2e-3"]).__next__,
        )
        self.context = context
        config = uvicorn.Config(create_app(context), host="127.0.0.1", port=0, log_level="warning")
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve())
        await _until(lambda: self.server is not None and self.server.started)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.task, 5)
        for fake in (self.prisma, self.timbre, self.llm):
            await fake.stop()

    def connect(self, origin: str = ORIGIN) -> Any:  # Any: websockets connect context manager
        return connect(f"ws://127.0.0.1:{self.port}/call", origin=origin, proxy=None)  # type: ignore[arg-type]


async def _until(condition: Any, within_s: float = 5.0) -> None:
    async def _poll() -> None:
        while not condition():  # noqa: ASYNC110 (uvicorn exposes no started event)
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), within_s)


async def _get_json(port: int, path: str) -> Any:  # Any: parsed JSON body
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode())
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    return json.loads(raw.split(b"\r\n\r\n", 1)[1])


def _pieces(text: str, size: int = 11) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


@pytest.fixture
async def stack(tmp_path: Path) -> AsyncIterator[Stack]:
    s = Stack(tmp_path)
    await s.start()
    try:
        yield s
    finally:
        await s.stop()


class Browser:
    def __init__(self, ws: ClientConnection) -> None:
        self.ws = ws
        self.messages: list[dict[str, Any]] = []
        self.audio = bytearray()

    async def until(self, kind: str, count: int = 1, within_s: float = 10.0) -> dict[str, Any]:
        async def _read() -> dict[str, Any]:
            while True:
                seen = [m for m in self.messages if m["type"] == kind]
                if len(seen) >= count:
                    return seen[count - 1]
                frame = await self.ws.recv()
                if isinstance(frame, bytes):
                    self.audio.extend(frame)
                else:
                    self.messages.append(json.loads(frame))

        return await asyncio.wait_for(_read(), within_s)

    async def talk(self, pcm: bytes) -> None:
        await self.ws.send(json.dumps({"type": "ptt_start"}))
        for i in range(0, len(pcm), 640):  # 20 ms browser chunks, not frame-aligned on purpose
            await self.ws.send(pcm[i : i + 640])
            await asyncio.sleep(0.02)
        await self.ws.send(json.dumps({"type": "ptt_end"}))


async def test_scripted_agent_audio_completes_a_call_and_stores_turns(stack: Stack) -> None:
    async with stack.connect() as ws:
        browser = Browser(ws)
        await ws.send(
            json.dumps(
                {
                    "type": "start_call",
                    "persona": "ramesh",
                    "difficulty": "medium",
                    "anon_id": "anon-1",
                }
            )
        )
        briefing = await browser.until("briefing")
        assert briefing["sim_clock"].startswith("2026-10-28")
        await browser.until("audio_end")  # pickup line, pre-rendered

        await browser.talk(tone(0.6))
        agent = await browser.until("transcript", count=2)
        assert (agent["role"], agent["text"]) == ("agent", AGENT_LINE)
        await browser.until("mood")

        await browser.talk(tone(0.4))
        flag = await browser.until("flag")
        assert flag["flag"]["rule"] == "threat"
        assert flag["flag"]["status"] == "possible"
        await browser.until("mood", count=2)

        await ws.send(json.dumps({"type": "end_call"}))
        ended = await browser.until("call_ended")
        assert ended["reason"] == "agent_hangup"

    # Audio: every agent frame reached Prisma at exactly 1,024 bytes, then a commit per turn.
    assert stack.prisma.bad_frames == []
    assert stack.prisma.commits == 2
    assert len(stack.prisma.connections) == 1  # one stream for the whole call
    # The borrower's first reply was spoken sentence by sentence on one Timbre socket.
    spoken = [r["text"] for r in stack.timbre.requests if r["_connection"] > 1]
    assert spoken[:2] == [
        "Arre sir, kya bataun, auto teen hafte se garage mein pada tha.",
        "Main abhi ₹4,000 de sakta hoon.",
    ]
    assert fake_pcm(spoken[0]) in bytes(browser.audio)

    assert stack.db is not None
    turns = await stack.db.turns("call-e2e")
    assert [(t.idx, t.role) for t in turns] == [
        (0, "borrower"),
        (1, "agent"),
        (2, "borrower"),
        (3, "agent"),
        (4, "borrower"),
    ]
    assert turns[1].text == AGENT_LINE
    assert turns[2].text.startswith("Arre sir, kya bataun")
    assert turns[2].state_after is not None
    assert turns[2].state_after["promise"]["amount"] == 4000
    call = await stack.db.call("call-e2e")
    assert call is not None
    assert call.end_reason == "agent_hangup"
    assert call.promise_amount == 4000
    # Latency: every finished borrower turn fed /metrics/latency (numbers are fake-server numbers).
    report = await _get_json(stack.port, "/metrics/latency")
    assert report["turns"] == 2
    assert report["stages"]["server_total"]["n"] == 2
    assert report["stages"]["stt_commit_to_final"]["p50"] > 0
    # No judge is wired in this stack: the stored call reports "judging"; unknown ids are 404.
    assert await _get_json(stack.port, "/calls/call-e2e/scorecard") == {"status": "judging"}
    assert await _get_json(stack.port, "/calls/nope/scorecard") == {"error": "unknown call"}
    # The hidden truth never crossed the socket.
    assert "engine repair" not in json.dumps(browser.messages, ensure_ascii=False)


async def test_a_second_caller_waits_in_line_then_gets_the_call(stack: Stack) -> None:
    start = {"type": "start_call", "persona": "ramesh", "difficulty": "easy", "anon_id": "a"}
    async with stack.connect() as first:
        await first.send(json.dumps(start))
        await Browser(first).until("briefing")
        async with stack.connect() as second:
            await second.send(json.dumps(start))  # sent while queued: kept for the call
            waiting = Browser(second)
            busy = await waiting.until("busy")
            assert busy == {"type": "busy", "position": 1, "eta_s": 240}
            await first.send(json.dumps({"type": "end_call"}))
            briefing = await waiting.until("briefing")
            assert briefing["facts"]["id"] == "ramesh"
            await second.send(json.dumps({"type": "end_call"}))
            await waiting.until("call_ended")


async def test_wrong_origin_is_refused(stack: Stack) -> None:
    with pytest.raises(Exception, match=r"1008|403|rejected"):
        async with stack.connect(origin="https://evil.example") as ws:
            await asyncio.wait_for(ws.recv(), 5)


async def test_outside_live_hours_the_caller_is_told_and_no_call_starts(stack: Stack) -> None:
    app_context = stack.context
    app_context.admission.hours = LiveHours(windows=(), wall=WALL)  # closed now
    async with stack.connect() as ws:
        refusal = json.loads(await asyncio.wait_for(ws.recv(), 5))
    assert refusal["type"] == "error"
    assert refusal["code"] == "outside_live_hours"
    assert stack.db is not None
    assert await stack.db.call("call-e2e") is None
