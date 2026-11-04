"""FastAPI app: WebSocket `/call` plus the REST endpoints a call needs (context doc 05).

`create_app(context)` takes everything already built, so tests run the real app against the
fake Gnani and LLM servers. `create_production_app()` builds that context from `Settings`
(env only) in the app's lifespan: SQLite, content, the provider-agnostic LLM client, Gnani
clients, and the fixed-line cache warmed at startup.
"""

import asyncio
import contextlib
import datetime as dt
import logging
import random
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect, WebSocketState

from kalpakka.admission import Admission, DailyCaps, Lease, LiveHours, Refusal
from kalpakka.brain import load_prompt
from kalpakka.call_tripwire import CallTripwire
from kalpakka.clock import SystemClock
from kalpakka.config import REPO_ROOT, Settings, parse_live_hours
from kalpakka.db import Database
from kalpakka.gnani_stt import PrismaStream, config_for_persona
from kalpakka.gnani_tts import LINE_TROUBLE, PrerenderCache, TimbreSocket, voice_for_persona
from kalpakka.judge_runner import JsonSchemaAdapter
from kalpakka.llm import ChatTemplateThinkingAdapter, LLMClient
from kalpakka.llm_tripwire import LLMCheck, LLMTripwire
from kalpakka.personas import Persona, load_personas
from kalpakka.protocol import Busy, ErrorMsg, encode
from kalpakka.scorer import JudgeScorer
from kalpakka.session import (
    CallDeps,
    CallSession,
    LexiconAnalyzer,
    LlmReplies,
    SessionConfig,
    SpeechToText,
    TextToSpeech,
    TransportClosed,
)
from kalpakka.state_tripwire import load_state_terms
from kalpakka.tripwire import LexiconTripwire, load_catalogue, load_lexicon

log = logging.getLogger(__name__)

CONTENT = REPO_ROOT / "content"
POLICY_VIOLATION = 1008


REFUSAL_TEXT: dict[str, str] = {
    "outside_live_hours": "Ramesh only takes calls during live hours.",
    "daily_cap": "Today's calls are used up. Come back tomorrow.",
    "ip_daily_cap": "You have used today's calls from this network.",
    "queue_full": "The line is full right now. Try again in a few minutes.",
}
BUFFER_LIMIT: Final = 64  # frames (any kind) a queued browser may send before its call starts


def client_ip(ws: WebSocket, *, trust_forwarded_for: bool) -> str:
    """Behind our own reverse proxy the client is the last X-Forwarded-For entry it added."""
    forwarded = ws.headers.get("x-forwarded-for")
    if trust_forwarded_for and forwarded:
        return forwarded.split(",")[-1].strip()
    return ws.client.host if ws.client else "unknown"


class BufferedTransport:
    """Reads the socket while the caller waits in the queue, so a hang-up there is noticed
    and an early `start_call` is kept for the session."""

    def __init__(self, inner: "WebSocketTransport") -> None:
        self._inner = inner
        self._buffer: deque[str | bytes] = deque()
        self._closed: TransportClosed | None = None

    async def watch(self) -> None:
        """Buffer incoming frames until the socket closes (then return)."""
        while True:
            try:
                item = await self._inner.receive()
            except TransportClosed as exc:
                self._closed = exc
                return
            if len(self._buffer) >= BUFFER_LIMIT:  # flooding while queued: treat as gone
                self._closed = TransportClosed("too many frames before the call started")
                return
            self._buffer.append(item)

    async def receive(self) -> str | bytes:
        if self._buffer:
            return self._buffer.popleft()
        if self._closed is not None:
            raise self._closed
        return await self._inner.receive()

    async def send_text(self, data: str) -> None:
        await self._inner.send_text(data)

    async def send_bytes(self, data: bytes) -> None:
        await self._inner.send_bytes(data)


async def admit_while_watching(
    admission: Admission,
    ip: str,
    transport: BufferedTransport,
    on_position: Callable[[int, int], Awaitable[None]],
) -> Lease | Refusal | None:
    """Wait for a slot; None if the caller hung up while waiting."""
    entering = asyncio.create_task(admission.enter(ip, on_position=on_position))
    watching = asyncio.create_task(transport.watch())
    done, _ = await asyncio.wait({entering, watching}, return_when=asyncio.FIRST_COMPLETED)
    if entering not in done:  # the socket closed first
        entering.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await entering
        return None
    watching.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watching
    try:
        return entering.result()
    except TransportClosed:  # a position update could not be sent
        return None


class WebSocketTransport:
    """`session.Transport` over a Starlette WebSocket."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def receive(self) -> str | bytes:
        message = await self._ws.receive()
        if message["type"] == "websocket.disconnect":
            raise TransportClosed(f"browser closed ({message.get('code')})")
        if message.get("bytes") is not None:
            data: bytes = message["bytes"]
            return data
        text: str = message.get("text") or ""
        return text

    async def send_text(self, data: str) -> None:
        try:
            await self._ws.send_text(data)
        except (WebSocketDisconnect, RuntimeError) as exc:
            raise TransportClosed(str(exc)) from exc

    async def send_bytes(self, data: bytes) -> None:
        try:
            await self._ws.send_bytes(data)
        except (WebSocketDisconnect, RuntimeError) as exc:
            raise TransportClosed(str(exc)) from exc


@dataclass
class AppContext:
    deps: CallDeps
    admission: Admission
    allowed_origins: frozenset[str]
    trust_forwarded_for: bool = False
    new_call_id: Callable[[], str] = lambda: uuid.uuid4().hex[:16]
    health: Callable[[], dict[str, Any]] = lambda: {}  # Any: JSON-safe status fields


def create_app(
    context: AppContext | None = None,
    *,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    app = FastAPI(title="KalPakka", lifespan=lifespan)
    if context is not None:
        app.state.context = context

    def ctx() -> AppContext:
        found: AppContext = app.state.context
        return found

    @app.middleware("http")
    async def cors(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """GET routes for the front door on another origin (ALLOWED_ORIGINS); nothing else."""
        response = await call_next(request)
        origin = request.headers.get("origin")
        context = getattr(app.state, "context", None)
        if request.method == "GET" and origin and context and origin in context.allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        c = ctx()
        return {
            "ok": True,
            "live": c.admission.hours.is_open(),
            "active_calls": c.admission.active,
            "queued": c.admission.queued,
            "calls_today": c.admission.caps.calls_today,
            **c.health(),
        }

    @app.get("/metrics/latency")
    async def latency() -> dict[str, Any]:
        """p50/p95 per stage over recent turns. Measured numbers only; empty until calls run."""
        return ctx().deps.latency.report()

    @app.get("/calls/{call_id}/scorecard", response_model=None)
    async def scorecard(call_id: str) -> dict[str, Any] | JSONResponse:
        db = ctx().deps.db
        card = await db.scorecard(call_id)
        if card is not None:
            if card.get("error") == "scoring_failed":  # the flags only; never a made-up score
                return JSONResponse(card, status_code=424)
            return card
        if await db.call(call_id) is not None:
            return JSONResponse({"status": "judging"}, status_code=202)
        return JSONResponse({"error": "unknown call"}, status_code=404)

    @app.get("/personas")
    async def personas() -> list[dict[str, Any]]:
        return [p.public_card().model_dump(mode="json") for p in ctx().deps.personas.values()]

    @app.websocket("/call")
    async def call(ws: WebSocket) -> None:
        c = ctx()
        origin = ws.headers.get("origin")
        if origin not in c.allowed_origins:
            log.warning("refused /call from origin %r", origin)
            await ws.close(code=POLICY_VIOLATION)
            return
        await ws.accept()
        transport = BufferedTransport(WebSocketTransport(ws))

        async def on_position(position: int, eta_s: int) -> None:
            await transport.send_text(encode(Busy(position=position, eta_s=eta_s)))

        ip = client_ip(ws, trust_forwarded_for=c.trust_forwarded_for)
        result = await admit_while_watching(c.admission, ip, transport, on_position)
        if isinstance(result, Refusal):
            with contextlib.suppress(TransportClosed):
                await transport.send_text(
                    encode(ErrorMsg(code=result.reason, message=REFUSAL_TEXT[result.reason]))
                )
        elif result is not None:
            with result:
                session = CallSession(c.deps, transport, call_id=c.new_call_id())
                reason = await session.run()
                log.info("call %s ended: %s", session.call_id, reason)
        if ws.client_state == WebSocketState.CONNECTED:
            with contextlib.suppress(RuntimeError):
                await ws.close()

    @app.exception_handler(Exception)
    async def _unhandled(_: object, exc: Exception) -> JSONResponse:
        log.exception("unhandled error: %s", exc)
        return JSONResponse({"error": "internal"}, status_code=500)

    return app


# --- production wiring ---------------------------------------------------------------------------


class CachedLines:
    """`session.FixedLines` backed by the on-disk pre-render cache."""

    def __init__(self, cache: PrerenderCache) -> None:
        self._cache = cache

    async def line(self, persona: Persona, name: str) -> tuple[str, bytes]:
        text = LINE_TROUBLE if name == "line_trouble" else str(getattr(persona.lines, name))
        return text, await self._cache.get(text, voice_for_persona(persona))


def create_production_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = Settings()  # values come from the environment
        clock = SystemClock()

        def wall() -> dt.datetime:
            return dt.datetime.now(dt.UTC)

        db = await Database.open(settings.db_path)
        personas = load_personas(CONTENT / "personas")
        catalogue = load_catalogue(CONTENT / "rules" / "catalogue.yaml")
        tripwire = LexiconTripwire(load_lexicon(CONTENT / "rules" / "lexicon.yaml", catalogue))
        llm = LLMClient.from_settings(settings, ChatTemplateThinkingAdapter())
        base = ChatTemplateThinkingAdapter()

        def llm_with(adapter: JsonSchemaAdapter, timeout_s: float) -> LLMClient:
            return LLMClient(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key.get_secret_value(),
                model=settings.llm_model,
                adapter=adapter,
                timeout_s=timeout_s,
            )

        terms = load_state_terms(CONTENT / "rules" / "state_terms.yaml")
        check_layer = LLMTripwire(
            llm_with(JsonSchemaAdapter(base, name="tripwire_check", model=LLMCheck), 30.0),
            load_prompt(CONTENT / "prompts" / "tripwire-check.md"),
            catalogue,
        )

        def new_tripwire(persona: Persona) -> CallTripwire:
            return CallTripwire(
                lexicon=tripwire, terms=terms, persona=persona, llm_layer=check_layer
            )

        scorer = JudgeScorer(
            llm_with(JsonSchemaAdapter(base), 85.0),
            load_prompt(CONTENT / "prompts" / "judge-system.md"),
            catalogue=catalogue,
            tripwire=tripwire,
        )
        gnani_key = settings.gnani_api_key.get_secret_value() if settings.gnani_api_key else ""

        def open_stt(persona: Persona, call_id: str) -> AbstractAsyncContextManager[SpeechToText]:
            return PrismaStream(
                api_key=gnani_key, config=config_for_persona(persona, call_id=call_id), clock=clock
            )

        def open_tts() -> AbstractAsyncContextManager[TextToSpeech]:
            return TimbreSocket(api_key=gnani_key)

        render_socket = TimbreSocket(api_key=gnani_key)
        cache = PrerenderCache(settings.prerender_dir, render_socket)
        warmed: dict[str, str] = {}
        if gnani_key:
            for persona in personas.values():
                try:
                    await cache.warm(persona)
                    warmed[persona.id] = "ok"
                except Exception as exc:  # startup must not die on a Gnani hiccup
                    log.error("fixed-line warm-up failed for %s: %s", persona.id, exc)
                    warmed[persona.id] = "failed"
        rng = random.SystemRandom()
        deps = CallDeps(
            personas=personas,
            system_prompt=load_prompt(CONTENT / "prompts" / "borrower-system.md"),
            open_stt=open_stt,
            open_tts=open_tts,
            replies=LlmReplies(llm, clock),
            lines=CachedLines(cache),
            db=db,
            clock=clock,
            wall=wall,
            seeds=lambda: rng.randrange(1, 2**31),
            config=SessionConfig(sim_date=settings.sim_date, time_cap_s=settings.call_time_cap_s),
            analyzer=LexiconAnalyzer(tripwire),
            tripwire_factory=new_tripwire,
            state_terms=terms,
            scorer=scorer,
        )
        app.state.context = AppContext(
            deps=deps,
            admission=Admission(
                max_concurrent=settings.max_concurrent_calls,
                max_queue=settings.max_queue,
                avg_call_s=settings.avg_call_s,
                caps=DailyCaps(
                    per_ip=settings.calls_per_ip_per_day,
                    total=settings.calls_per_day,
                    wall=wall,
                ),
                hours=LiveHours(
                    windows=parse_live_hours(settings.live_hours),
                    wall=wall,
                    always_open=settings.live_always_open,
                ),
            ),
            allowed_origins=frozenset(settings.allowed_origins),
            trust_forwarded_for=settings.trust_forwarded_for,
            health=lambda: {"gnani_key": bool(gnani_key), "fixed_lines": warmed},
        )
        try:
            yield
        finally:
            await asyncio.gather(render_socket.close(), db.close(), return_exceptions=True)

    return create_app(lifespan=lifespan)
