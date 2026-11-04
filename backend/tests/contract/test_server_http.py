"""CORS on GET routes and the scorecard route's failure shape (raw ASGI; no HTTP client needed)."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kalpakka.db import Database
from kalpakka.server import create_app

ORIGIN = "https://kalpakka.vercel.app"


async def get(
    db: Database, path: str, *, method: str = "GET", origin: str | None = None
) -> tuple[int, dict[str, str], Any]:
    app = create_app(
        SimpleNamespace(  # type: ignore[arg-type]
            deps=SimpleNamespace(personas={}, db=db), allowed_origins=frozenset({ORIGIN})
        )
    )
    headers = [(b"origin", origin.encode())] if origin else []
    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(),
             "query_string": b"", "headers": headers, "http_version": "1.1", "scheme": "http",
             "server": ("t", 80), "client": ("c", 1), "root_path": ""}  # fmt: skip
    sent = [False]
    out: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        if sent[0]:
            return {"type": "http.disconnect"}
        sent[0] = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        out.append(message)

    await app(scope, receive, send)  # type: ignore[arg-type]
    start = next(m for m in out if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in out if m["type"] == "http.response.body")
    hdrs = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return start["status"], hdrs, json.loads(raw) if raw else None


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    return await Database.open(tmp_path / "k.db")


async def test_get_from_an_allowed_origin_is_readable(db: Database) -> None:
    _, ok, _ = await get(db, "/personas", origin=ORIGIN)
    _, other, _ = await get(db, "/personas", origin="https://evil.example")
    _, none, _ = await get(db, "/personas")
    assert ok["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-origin" not in other
    assert "access-control-allow-origin" not in none


async def test_only_get_gets_a_cors_grant(db: Database) -> None:
    _, headers, _ = await get(db, "/personas", method="OPTIONS", origin=ORIGIN)
    assert "access-control-allow-origin" not in headers


async def test_failed_scoring_is_424_with_flags_not_a_score(db: Database) -> None:
    await db.save_scorecard("c1", {"error": "scoring_failed", "flags": []}, judge_version="failed")
    status, _, body = await get(db, "/calls/c1/scorecard")
    assert status == 424 and "total" not in body
