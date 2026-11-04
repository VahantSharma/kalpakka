"""SQLite persistence (context doc 05 "Data model"). No audio is ever stored.

One `sqlite3` connection per process, used from worker threads through `asyncio.to_thread` and
serialised by an `asyncio.Lock`, so the event loop never blocks on disk and writes never
interleave. WAL mode keeps readers from blocking the writer.
"""

import asyncio
import datetime as dt
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (anon_id TEXT PRIMARY KEY, nickname TEXT, created_at TEXT,
  clone_consent INTEGER DEFAULT 0, voice_embedding TEXT);
CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, anon_id TEXT, mode TEXT,
  persona_id TEXT, persona_version TEXT, prompt_version TEXT, difficulty TEXT,
  seed INTEGER, traps TEXT, started_at TEXT, ended_at TEXT, end_reason TEXT,
  score_total INTEGER, promise_amount INTEGER, promise_date TEXT, promise_valid INTEGER);
CREATE TABLE IF NOT EXISTS turns (call_id TEXT, idx INTEGER, role TEXT, text TEXT,
  t_start REAL, t_end REAL, interrupted INTEGER, latency TEXT, state_after TEXT,
  PRIMARY KEY (call_id, idx));
CREATE TABLE IF NOT EXISTS flags (id TEXT PRIMARY KEY, call_id TEXT, turn_idx INTEGER,
  segment_idx INTEGER, t REAL, rule TEXT, quote TEXT, layer TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS scorecards (call_id TEXT PRIMARY KEY, card TEXT, judge_version TEXT);
CREATE TABLE IF NOT EXISTS arena_runs (id TEXT PRIMARY KEY, bot_prompt TEXT, started_at TEXT,
  finished_at TEXT, summary TEXT);
"""

T = TypeVar("T")


class CallRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    anon_id: str
    mode: Literal["train", "arena", "audit"]
    persona_id: str
    persona_version: str
    prompt_version: str
    difficulty: str
    seed: int
    traps: list[str] = Field(default_factory=list)
    started_at: dt.datetime
    ended_at: dt.datetime | None = None
    end_reason: str | None = None
    score_total: int | None = None
    promise_amount: int | None = None
    promise_date: dt.date | None = None
    promise_valid: bool | None = None


class TurnRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    call_id: str
    idx: int
    role: Literal["agent", "borrower"]
    text: str  # borrower: only the text the trainee actually heard
    t_start: float
    t_end: float
    interrupted: bool = False
    latency: dict[str, float] = Field(default_factory=dict)
    state_after: dict[str, Any] | None = None  # Any: BorrowerState.model_dump(mode="json")


class Database:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path) -> "Database":
        def _open() -> sqlite3.Connection:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.commit()
            return conn

        return cls(await asyncio.to_thread(_open))

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)

    async def _run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        async with self._lock:
            return await asyncio.to_thread(fn, self._conn)

    # --- writes ---

    async def create_call(self, call: CallRecord) -> None:
        row = call.model_dump(mode="json")
        row["traps"] = json.dumps(call.traps)
        row["promise_valid"] = None if call.promise_valid is None else int(call.promise_valid)
        columns = ", ".join(row)
        marks = ", ".join(f":{k}" for k in row)

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute(f"INSERT INTO calls ({columns}) VALUES ({marks})", row)
            conn.commit()

        await self._run(_write)

    async def finish_call(
        self,
        call_id: str,
        *,
        ended_at: dt.datetime,
        end_reason: str,
        promise_amount: int | None = None,
        promise_date: dt.date | None = None,
        promise_valid: bool | None = None,
    ) -> None:
        values = {
            "id": call_id,
            "ended_at": ended_at.isoformat(),
            "end_reason": end_reason,
            "promise_amount": promise_amount,
            "promise_date": promise_date.isoformat() if promise_date else None,
            "promise_valid": None if promise_valid is None else int(promise_valid),
        }

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE calls SET ended_at=:ended_at, end_reason=:end_reason, "
                "promise_amount=:promise_amount, promise_date=:promise_date, "
                "promise_valid=:promise_valid WHERE id=:id",
                values,
            )
            conn.commit()

        await self._run(_write)

    async def add_turn(self, turn: TurnRecord) -> None:
        row = turn.model_dump(mode="json")
        row["interrupted"] = int(turn.interrupted)
        row["latency"] = json.dumps(turn.latency)
        row["state_after"] = None if turn.state_after is None else json.dumps(turn.state_after)

        def _write(conn: sqlite3.Connection) -> None:
            try:
                conn.execute(
                    "INSERT INTO turns (call_id, idx, role, text, t_start, t_end, interrupted, "
                    "latency, state_after) VALUES (:call_id, :idx, :role, :text, :t_start, "
                    ":t_end, :interrupted, :latency, :state_after)",
                    row,
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"duplicate turn {turn.call_id}#{turn.idx}") from exc
            conn.commit()

        await self._run(_write)

    async def save_scorecard(
        self, call_id: str, card: dict[str, Any], *, judge_version: str
    ) -> None:
        def _write(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT OR REPLACE INTO scorecards (call_id, card, judge_version) VALUES (?, ?, ?)",
                (call_id, json.dumps(card, ensure_ascii=False), judge_version),
            )
            conn.commit()

        await self._run(_write)

    # --- reads ---

    async def scorecard(self, call_id: str) -> dict[str, Any] | None:
        def _read(conn: sqlite3.Connection) -> sqlite3.Row | None:
            row: sqlite3.Row | None = conn.execute(
                "SELECT card FROM scorecards WHERE call_id = ?", (call_id,)
            ).fetchone()
            return row

        row = await self._run(_read)
        if row is None:
            return None
        card: dict[str, Any] = json.loads(row["card"])
        return card

    async def call(self, call_id: str) -> CallRecord | None:
        def _read(conn: sqlite3.Connection) -> sqlite3.Row | None:
            row: sqlite3.Row | None = conn.execute(
                "SELECT * FROM calls WHERE id = ?", (call_id,)
            ).fetchone()
            return row

        row = await self._run(_read)
        if row is None:
            return None
        data = dict(row)
        data["traps"] = json.loads(data["traps"] or "[]")
        if data["promise_valid"] is not None:
            data["promise_valid"] = bool(data["promise_valid"])
        return CallRecord.model_validate(data)

    async def turns(self, call_id: str) -> list[TurnRecord]:
        def _read(conn: sqlite3.Connection) -> list[sqlite3.Row]:
            return conn.execute(
                "SELECT * FROM turns WHERE call_id = ? ORDER BY idx", (call_id,)
            ).fetchall()

        out: list[TurnRecord] = []
        for row in await self._run(_read):
            data = dict(row)
            data["interrupted"] = bool(data["interrupted"])
            data["latency"] = json.loads(data["latency"] or "{}")
            data["state_after"] = json.loads(data["state_after"]) if data["state_after"] else None
            out.append(TurnRecord.model_validate(data))
        return out

    async def tables(self) -> set[str]:
        def _read(conn: sqlite3.Connection) -> set[str]:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            return {r["name"] for r in rows}

        return await self._run(_read)
