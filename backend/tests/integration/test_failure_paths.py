"""Failure paths of a live call, through the real app and the fake servers (05 "Failure handling").

Each test breaks one dependency mid-call and checks the trainee hears something sensible and the
call is recorded with an honest end reason.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.contract.fakes.fake_openai import Script
from tests.integration.test_call_end_to_end import Browser, Stack, _pieces, tone

START = {"type": "start_call", "persona": "ramesh", "difficulty": "medium", "anon_id": "anon-9"}
REPLY = (
    "Arre sir, kya bataun, auto teen hafte se garage mein pada tha. "
    "Main abhi ₹4,000 de sakta hoon. ###STATE### {}"
)


@pytest.fixture
async def stack(tmp_path: Path) -> AsyncIterator[Stack]:
    s = Stack(tmp_path)
    await s.start()
    try:
        yield s
    finally:
        await s.stop()


async def _call_until_first_turn(stack: Stack) -> tuple[object, Browser]:
    ws_cm = stack.connect()
    ws = await ws_cm.__aenter__()
    browser = Browser(ws)
    await ws.send(json.dumps(START))
    await browser.until("audio_end")  # pickup
    return ws_cm, browser


async def test_prisma_drop_mid_turn_is_recovered_and_the_turn_completes(stack: Stack) -> None:
    stack.prisma.drop_after_frames = 5  # the stream dies while the agent is talking
    stack.llm.scripts = [Script(pieces=_pieces(REPLY))]
    ws_cm, browser = await _call_until_first_turn(stack)
    try:
        await browser.talk(tone(0.6))
        agent = await browser.until("transcript", count=2)
        assert agent["role"] == "agent"
        assert agent["text"].startswith("Ramesh ji")
        await browser.until("audio_end", count=3)  # the borrower answered
    finally:
        await ws_cm.__aexit__(None, None, None)  # type: ignore[attr-defined]
    assert len(stack.prisma.connections) == 2
    assert stack.prisma.bad_frames == []


async def test_llm_stream_breaking_mid_reply_plays_line_trouble_and_ends(stack: Stack) -> None:
    stack.llm.scripts = [Script(pieces=_pieces(REPLY), abort_after=8)]
    ws_cm, browser = await _call_until_first_turn(stack)
    try:
        await browser.talk(tone(0.4))
        ended = await browser.until("call_ended")
        borrower = [m["text"] for m in browser.messages if m["type"] == "transcript"]
    finally:
        await ws_cm.__aexit__(None, None, None)  # type: ignore[attr-defined]
    assert ended["reason"] == "llm_unavailable"
    assert borrower[-1] == "Hello? Line kharab hai, baad mein baat karte hain."
    assert stack.db is not None
    call = await stack.db.call("call-e2e")
    assert call is not None
    assert call.end_reason == "llm_unavailable"


async def test_llm_with_no_first_token_in_two_seconds_ends_politely(stack: Stack) -> None:
    stack.llm.scripts = [Script(pieces=_pieces(REPLY), first_delay_s=3.0)]
    ws_cm, browser = await _call_until_first_turn(stack)
    try:
        await browser.talk(tone(0.4))
        ended = await browser.until("call_ended", within_s=10)
    finally:
        await ws_cm.__aexit__(None, None, None)  # type: ignore[attr-defined]
    assert ended["reason"] == "llm_unavailable"


async def test_timbre_dropping_before_any_audio_is_retried_on_a_new_socket(stack: Stack) -> None:
    stack.timbre.fail_next = 1  # first attempt for sentence 1 errors before audio
    stack.llm.scripts = [Script(pieces=_pieces(REPLY))]
    ws_cm, browser = await _call_until_first_turn(stack)
    try:
        await browser.talk(tone(0.4))
        await browser.until("audio_end", count=3)
        await ws_cm_send_end(browser)
    finally:
        await ws_cm.__aexit__(None, None, None)  # type: ignore[attr-defined]
    spoken = [m["text"] for m in browser.messages if m["type"] == "transcript"]
    assert "Main abhi ₹4,000 de sakta hoon." in spoken


async def test_timbre_dropping_mid_sentence_ends_with_line_trouble(stack: Stack) -> None:
    stack.llm.scripts = [Script(pieces=_pieces(REPLY))]
    ws_cm, browser = await _call_until_first_turn(stack)
    stack.timbre.drop_mid_sentence_next = 1
    try:
        await browser.talk(tone(0.4))
        ended = await browser.until("call_ended")
    finally:
        await ws_cm.__aexit__(None, None, None)  # type: ignore[attr-defined]
    assert ended["reason"] == "tts_unavailable"
    lines = [m["text"] for m in browser.messages if m["type"] == "transcript"]
    assert lines[-1] == "Hello? Line kharab hai, baad mein baat karte hain."


async def ws_cm_send_end(browser: Browser) -> None:
    await browser.ws.send(json.dumps({"type": "end_call"}))
    await browser.until("call_ended")
    await asyncio.sleep(0)
