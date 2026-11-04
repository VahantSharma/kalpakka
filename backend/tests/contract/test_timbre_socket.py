"""TimbreSocket, speak() fallback and the pre-render cache against the fake Timbre server."""

import contextlib
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from kalpakka.gnani_tts import (
    LINE_TROUBLE,
    PrerenderCache,
    TimbreSocket,
    TtsFailed,
    TtsUnavailable,
    Voice,
    speak,
)
from tests.contract.fakes.fake_timbre import FakeTimbre, fake_pcm
from tests.factories import make_persona

DEVANSH = Voice(name="Devansh", language="hi-IN")
SHUBHAM = Voice(name="Shubham", language="hi-IN")
SENTENCE = "Arre sir, ₹4,000 abhi de sakta hoon, baaki bees tareekh tak."


@pytest.fixture
async def fake_timbre() -> AsyncIterator[FakeTimbre]:
    fake = FakeTimbre()
    await fake.start()
    try:
        yield fake
    finally:
        await fake.stop()


def _socket(fake: FakeTimbre, **kw: float) -> TimbreSocket:
    return TimbreSocket(api_key="test-key", url=fake.url, **kw)


async def test_one_sentence_returns_its_whole_audio(fake_timbre: FakeTimbre) -> None:
    async with _socket(fake_timbre) as tts:
        chunks = [c async for c in tts.synthesize(SENTENCE, DEVANSH, speed=1.0)]
    assert b"".join(chunks) == fake_pcm(SENTENCE)
    assert all(len(c) % 2 == 0 for c in chunks)  # 7-byte server chunks re-aligned to samples
    assert fake_timbre.headers[0]["x-api-key-id"] == "test-key"
    assert fake_timbre.requests[0]["text"] == SENTENCE


async def test_every_sentence_reuses_the_call_socket(fake_timbre: FakeTimbre) -> None:
    async with _socket(fake_timbre) as tts:
        for text in ("Haan, hello? Kaun bol raha hai?", "<sigh> Sir, auto band pada tha."):
            await tts.render(text, DEVANSH, speed=1.0)
    assert fake_timbre.connections == 1
    assert [r["text"] for r in fake_timbre.requests][1] == "<sigh> Sir, auto band pada tha."


async def test_interrupting_a_sentence_resets_the_socket_before_the_next(
    fake_timbre: FakeTimbre,
) -> None:
    fake_timbre.chunk_delay_s = 0.01
    async with _socket(fake_timbre) as tts:
        stream = tts.synthesize(SENTENCE, DEVANSH, speed=1.0)
        async with contextlib.aclosing(stream):
            await anext(stream)  # barge-in after the first chunk
        fake_timbre.chunk_delay_s = 0.0
        after = await tts.render("Haan bolo.", DEVANSH, speed=1.0)
        resets = tts.resets
    assert after == fake_pcm("Haan bolo.")  # no leftover audio from the interrupted sentence
    assert resets == 1
    assert fake_timbre.connections == 2


async def test_server_error_raises_tts_failed(fake_timbre: FakeTimbre) -> None:
    fake_timbre.fail_next = 1
    async with _socket(fake_timbre) as tts:
        with pytest.raises(TtsFailed, match="synthesis failed"):
            await tts.render(SENTENCE, DEVANSH, speed=1.0)


async def test_stalled_request_times_out(fake_timbre: FakeTimbre) -> None:
    fake_timbre.stall_next = 1
    async with _socket(fake_timbre, first_audio_timeout_s=0.2) as tts:
        with pytest.raises(TtsFailed, match="no timbre audio"):
            await tts.render(SENTENCE, DEVANSH, speed=1.0)
        assert await tts.render("Haan bolo.", DEVANSH, speed=1.0) == fake_pcm("Haan bolo.")


async def test_speak_retries_once_with_the_same_voice(fake_timbre: FakeTimbre) -> None:
    fake_timbre.fail_next = 1
    async with _socket(fake_timbre) as tts:
        audio = b"".join([c async for c in speak(tts, SENTENCE, DEVANSH, speed=1.0, backup=None)])
    assert audio == fake_pcm(SENTENCE)
    assert [r["voice"] for r in fake_timbre.requests] == ["Devansh", "Devansh"]


async def test_speak_falls_back_to_the_backup_voice(fake_timbre: FakeTimbre) -> None:
    fake_timbre.fail_next = 2
    async with _socket(fake_timbre) as tts:
        audio = b"".join(
            [c async for c in speak(tts, SENTENCE, DEVANSH, speed=1.0, backup=SHUBHAM)]
        )
    assert audio == fake_pcm(SENTENCE)
    assert [r["voice"] for r in fake_timbre.requests] == ["Devansh", "Devansh", "Shubham"]


async def test_speak_gives_up_after_retry_and_backup(fake_timbre: FakeTimbre) -> None:
    fake_timbre.fail_next = 3
    async with _socket(fake_timbre) as tts:
        with pytest.raises(TtsUnavailable):
            async for _ in speak(tts, SENTENCE, DEVANSH, speed=1.0, backup=SHUBHAM):
                pass


async def test_prerender_renders_once_then_reads_from_disk(
    fake_timbre: FakeTimbre, tmp_path: Path
) -> None:
    async with _socket(fake_timbre) as tts:
        cache = PrerenderCache(tmp_path, tts)
        first = await cache.get("Haan, hello? Kaun bol raha hai?", DEVANSH)
        second = await cache.get("Haan, hello? Kaun bol raha hai?", DEVANSH)
    assert first == second == fake_pcm("Haan, hello? Kaun bol raha hai?")
    assert cache.renders == 1
    assert len(fake_timbre.requests) == 1


async def test_prerender_survives_a_restart(fake_timbre: FakeTimbre, tmp_path: Path) -> None:
    async with _socket(fake_timbre) as tts:
        await PrerenderCache(tmp_path, tts).get(LINE_TROUBLE, DEVANSH)
    async with _socket(fake_timbre) as tts:
        restarted = PrerenderCache(tmp_path, tts)
        assert await restarted.get(LINE_TROUBLE, DEVANSH) == fake_pcm(LINE_TROUBLE)
    assert restarted.renders == 0


async def test_warm_renders_every_fixed_line_of_a_persona(
    fake_timbre: FakeTimbre, tmp_path: Path
) -> None:
    async with _socket(fake_timbre) as tts:
        lines = await PrerenderCache(tmp_path, tts).warm(make_persona())
    assert set(lines) == {"pickup", "silence", "hangup_angry", "line_trouble"}
    assert lines["pickup"] == fake_pcm("Haan, hello? Kaun bol raha hai?")
    assert len(os.listdir(tmp_path)) == 4


async def test_interrupting_speak_frees_the_socket_for_the_next_sentence(
    fake_timbre: FakeTimbre,
) -> None:
    fake_timbre.chunk_delay_s = 0.01
    async with _socket(fake_timbre) as tts:
        stream = speak(tts, SENTENCE, DEVANSH, speed=1.0, backup=SHUBHAM)
        async with contextlib.aclosing(stream):
            await anext(stream)  # barge-in during the first sentence
        assert not tts._lock.locked()
        fake_timbre.chunk_delay_s = 0.0
        after = await tts.render("Haan bolo.", DEVANSH, speed=1.0)
    assert after == fake_pcm("Haan bolo.")
    assert fake_timbre.connections == 2


async def test_rejected_reconnect_still_goes_through_the_fallback_chain(
    fake_timbre: FakeTimbre,
) -> None:
    async with _socket(fake_timbre) as tts:
        await tts.reset()  # e.g. after a barge-in
        fake_timbre.reject_next_connections = 1  # HTTP 503 on the upgrade
        audio = b"".join([c async for c in speak(tts, SENTENCE, DEVANSH, speed=1.0, backup=None)])
    assert audio == fake_pcm(SENTENCE)


async def test_unexpected_binary_frame_is_ignored(fake_timbre: FakeTimbre) -> None:
    fake_timbre.send_binary_next = 1
    async with _socket(fake_timbre) as tts:
        assert await tts.render(SENTENCE, DEVANSH, speed=1.0) == fake_pcm(SENTENCE)
