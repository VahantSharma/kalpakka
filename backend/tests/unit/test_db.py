"""SQLite persistence of calls and turns (schema from context doc 05). No audio is stored."""

import datetime as dt
from pathlib import Path

import pytest

from kalpakka.db import CallRecord, Database, TurnRecord


def _call(call_id: str = "c-1") -> CallRecord:
    return CallRecord(
        id=call_id,
        anon_id="anon-7",
        mode="train",
        persona_id="ramesh",
        persona_version="1",
        prompt_version="3",
        difficulty="medium",
        seed=7,
        traps=["third_party_wife"],
        started_at=dt.datetime(2026, 11, 5, 9, 30, tzinfo=dt.UTC),
    )


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = await Database.open(tmp_path / "kalpakka.db")
    return database


async def test_a_call_and_its_turns_round_trip(db: Database) -> None:
    await db.create_call(_call())
    await db.add_turn(
        TurnRecord(
            call_id="c-1",
            idx=0,
            role="borrower",
            text="Haan, hello? Kaun bol raha hai?",
            t_start=0.0,
            t_end=1.8,
        )
    )
    await db.add_turn(
        TurnRecord(
            call_id="c-1",
            idx=1,
            role="agent",
            text="Ramesh ji, ₹12,500 overdue hai",
            t_start=3.0,
            t_end=5.1,
            latency={"transcript_final": 0.31},
        )
    )
    turns = await db.turns("c-1")
    assert [(t.idx, t.role) for t in turns] == [(0, "borrower"), (1, "agent")]
    assert turns[1].text == "Ramesh ji, ₹12,500 overdue hai"
    assert turns[1].latency == {"transcript_final": 0.31}
    call = await db.call("c-1")
    assert call is not None
    assert call.traps == ["third_party_wife"]
    assert call.ended_at is None


async def test_finishing_a_call_records_reason_and_promise(db: Database) -> None:
    await db.create_call(_call())
    await db.finish_call(
        "c-1",
        ended_at=dt.datetime(2026, 11, 5, 9, 34, tzinfo=dt.UTC),
        end_reason="closed_after_promise",
        promise_amount=4000,
        promise_date=dt.date(2026, 10, 28),
        promise_valid=True,
    )
    call = await db.call("c-1")
    assert call is not None
    assert call.end_reason == "closed_after_promise"
    assert (call.promise_amount, call.promise_date, call.promise_valid) == (
        4000,
        dt.date(2026, 10, 28),
        True,
    )


async def test_turn_index_is_unique_per_call(db: Database) -> None:
    await db.create_call(_call())
    turn = TurnRecord(call_id="c-1", idx=0, role="agent", text="hello", t_start=0, t_end=1)
    await db.add_turn(turn)
    with pytest.raises(ValueError, match="duplicate turn"):
        await db.add_turn(turn)


async def test_unknown_call_is_none(db: Database) -> None:
    assert await db.call("missing") is None
    assert await db.turns("missing") == []


async def test_schema_has_every_table_from_the_architecture_doc(db: Database) -> None:
    assert await db.tables() == {"players", "calls", "turns", "flags", "scorecards", "arena_runs"}


async def test_reopening_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "k.db"
    first = await Database.open(path)
    await first.create_call(_call())
    await first.close()
    second = await Database.open(path)
    assert await second.call("c-1") is not None


async def test_scorecard_round_trip(db: Database) -> None:
    await db.create_call(_call())
    assert await db.scorecard("c-1") is None
    card = {"total": 62, "compliance": {"points": 20}, "quotes": ["police bhej dunga"]}
    await db.save_scorecard("c-1", card, judge_version="judge-v1")
    assert await db.scorecard("c-1") == card
