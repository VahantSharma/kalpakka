"""PrismaStream against the fake Prisma server (documented protocol, no network)."""

import asyncio

import pytest

from kalpakka.gnani_stt import (
    COMMIT_MESSAGE,
    FRAME_BYTES,
    PrismaStream,
    SttConfig,
    SttConfigRejected,
    SttTranscript,
)
from tests.contract.fakes.fake_prisma import FakePrisma

TIMEOUT = 5.0


class FastClock:
    """Deadline pacing runs on this clock; sleeping advances it instantly."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept = 0.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)
        self.slept += max(0.0, seconds)
        await asyncio.sleep(0)


CONFIG = SttConfig(language_code="hi-IN,en-IN", keyterms=["EMI", "SwiftFin"], request_id="call-7")


def _stream(fake: FakePrisma, clock: FastClock, key: str = "test-key") -> PrismaStream:
    return PrismaStream(api_key=key, config=CONFIG, clock=clock, sleep=clock.sleep, url=fake.url)


async def test_connects_with_config_in_headers(fake_prisma: FakePrisma) -> None:
    clock = FastClock()
    async with _stream(fake_prisma, clock):
        pass
    headers = fake_prisma.connections[0]
    assert headers["x-api-key-id"] == "test-key"
    assert headers["model"] == "prisma-v3.0"
    assert headers["language_code"] == "hi-IN,en-IN"
    assert headers["keyterms"] == "EMI,SwiftFin"
    assert headers["x-api-request-id"] == "call-7"


async def test_audio_goes_up_in_exact_frames_then_commit_returns_transcript(
    fake_prisma: FakePrisma,
) -> None:
    clock = FastClock()
    async with _stream(fake_prisma, clock) as stt:
        stt.send_audio(b"\x11\x22" * 1280)  # 2,560 bytes = 2.5 frames, in odd-sized chunks
        stt.commit()
        result = await stt.transcript(TIMEOUT)
    assert result == SttTranscript(
        text="Ramesh ji, ₹12,500 overdue hai",
        detected_language_code="hi-IN",
        latency=0.3,
        segment_index=0,
    )
    assert [len(f) for f in fake_prisma.frames] == [FRAME_BYTES] * 3
    assert fake_prisma.frames[-1].endswith(b"\x00" * 512)  # remainder padded with silence
    assert fake_prisma.bad_frames == []
    assert fake_prisma.received[-1] == COMMIT_MESSAGE  # commit after the last frame


async def test_one_stream_serves_every_turn_of_the_call(fake_prisma: FakePrisma) -> None:
    clock = FastClock()
    async with _stream(fake_prisma, clock) as stt:
        texts = []
        for _ in range(2):
            stt.send_audio(b"\x01\x00" * 1024)
            stt.commit()
            texts.append((await stt.transcript(TIMEOUT)).text)
    assert texts == ["Ramesh ji, ₹12,500 overdue hai", "हाँ, ₹4,000 abhi chalega"]
    assert len(fake_prisma.connections) == 1


async def test_frames_are_paced_at_real_time(fake_prisma: FakePrisma) -> None:
    clock = FastClock()
    async with _stream(fake_prisma, clock) as stt:
        stt.send_audio(b"\x00" * FRAME_BYTES * 10)
        stt.commit()
        await stt.transcript(TIMEOUT)
    assert clock.slept == pytest.approx(9 * 0.032)  # first frame now, then one per 32 ms


async def test_wrong_key_fails_call_start_without_retrying(fake_prisma: FakePrisma) -> None:
    clock = FastClock()
    with pytest.raises(SttConfigRejected, match="invalid api key"):
        async with _stream(fake_prisma, clock, key="wrong"):
            pass
    assert len(fake_prisma.connections) == 1


async def test_connected_echo_mismatch_is_a_config_error(fake_prisma: FakePrisma) -> None:
    fake_prisma.echo_override = {"language_code": "en-IN"}
    with pytest.raises(SttConfigRejected, match="language_code"):
        async with _stream(fake_prisma, FastClock()):
            pass


async def test_dropped_stream_reconnects_and_resends_recent_audio(
    fake_prisma: FakePrisma,
) -> None:
    fake_prisma.drop_after_frames = 3
    clock = FastClock()
    async with _stream(fake_prisma, clock) as stt:
        stt.send_audio(b"\x07\x00" * 512 * 5)  # 5 frames; the socket drops after the 3rd
        stt.commit()
        result = await stt.transcript(TIMEOUT)
        reconnects = stt.reconnects
    assert reconnects == 1
    assert len(fake_prisma.connections) == 2
    assert result.text == "Ramesh ji, ₹12,500 overdue hai"
    # 3 frames reached the first socket; all 5 unanswered frames are replayed on the second.
    assert len(fake_prisma.frames) == 8
    assert fake_prisma.bad_frames == []


async def test_reconnect_replays_only_the_unanswered_segment(fake_prisma: FakePrisma) -> None:
    fake_prisma.drop_after_frames = 4  # 2 frames of turn 1, then drops on turn 2's 2nd frame
    clock = FastClock()
    async with _stream(fake_prisma, clock) as stt:
        stt.send_audio(b"\x01\x00" * 512 * 2)
        stt.commit()
        first = await stt.transcript(TIMEOUT)
        stt.send_audio(b"\x02\x00" * 512 * 3)
        stt.commit()
        second = await stt.transcript(TIMEOUT)
    assert (first.text, second.text) == (
        "Ramesh ji, ₹12,500 overdue hai",
        "हाँ, ₹4,000 abhi chalega",
    )
    replayed = fake_prisma.frames[4:]
    assert len(replayed) == 3
    assert all(f == b"\x02\x00" * 512 for f in replayed)  # turn 1 audio is never re-sent
    assert fake_prisma.commits == 2


async def test_utterance_joins_every_segment_vad_closed_before_the_commit(
    fake_prisma: FakePrisma,
) -> None:
    fake_prisma.vad_split_after_frames = 2  # the agent paused after 2 frames
    async with _stream(fake_prisma, FastClock()) as stt:
        stt.send_audio(b"\x01\x00" * 512 * 4)
        stt.commit()
        utterance = await stt.utterance(TIMEOUT)
    assert utterance.text == "Ramesh ji, ₹12,500 overdue hai हाँ, ₹4,000 abhi chalega"


async def test_utterance_waits_for_a_slow_final(fake_prisma: FakePrisma) -> None:
    fake_prisma.transcript_delay_s = 0.3
    async with _stream(fake_prisma, FastClock()) as stt:
        stt.send_audio(b"\x01\x00" * 512 * 2)
        stt.commit()
        utterance = await stt.utterance(TIMEOUT)
    assert utterance.text == "Ramesh ji, ₹12,500 overdue hai"


async def test_utterance_of_pure_silence_is_empty(fake_prisma: FakePrisma) -> None:
    async with _stream(fake_prisma, FastClock()) as stt:
        stt.commit()  # talk pressed and released without a word
        utterance = await stt.utterance(TIMEOUT)
    assert utterance.text == ""


async def test_a_late_final_joins_the_next_utterance_instead_of_shifting_turns(
    fake_prisma: FakePrisma,
) -> None:
    fake_prisma.transcript_delay_s = 0.4
    async with _stream(fake_prisma, FastClock()) as stt:
        stt.send_audio(b"\x01\x00" * 512 * 2)
        stt.commit()
        with pytest.raises(TimeoutError):
            await stt.utterance(0.1)  # gave up waiting: the final is still on its way
        fake_prisma.transcript_delay_s = 0.0
        stt.send_audio(b"\x01\x00" * 512 * 2)
        stt.commit()
        second = await stt.utterance(TIMEOUT)
    assert second.text == "Ramesh ji, ₹12,500 overdue hai हाँ, ₹4,000 abhi chalega"


async def test_utterance_still_works_after_a_drop_mid_segment(fake_prisma: FakePrisma) -> None:
    """Review P2.5 #1: the open-segment count must restart with the new connection."""
    fake_prisma.drop_after_frames = 2
    async with _stream(fake_prisma, FastClock()) as stt:
        stt.send_audio(b"\x03\x00" * 512 * 3)
        stt.commit()
        first = await stt.utterance(TIMEOUT)
        stt.send_audio(b"\x04\x00" * 512 * 2)
        stt.commit()
        second = await stt.utterance(TIMEOUT)
    assert first.text == "Ramesh ji, ₹12,500 overdue hai"
    assert second.text == "हाँ, ₹4,000 abhi chalega"


async def test_segment_closed_by_vad_before_release_returns_without_the_long_grace(
    fake_prisma: FakePrisma,
) -> None:
    """Review P2.5 #6: no 0.5 s wait when the final is already in hand."""
    from kalpakka.gnani_stt import NO_SPEECH_GRACE_S

    fake_prisma.vad_split_after_frames = 2
    async with _stream(fake_prisma, FastClock()) as stt:
        stt.send_audio(b"\x01\x00" * 512 * 2)  # VAD closes the segment on the 2nd frame
        await stt.next_event(TIMEOUT)  # connected/speech_start/... let the final land first
        while not stt._finals:
            await stt.next_event(TIMEOUT)
        loop = asyncio.get_running_loop()
        started = loop.time()
        stt.commit()
        utterance = await stt.utterance(TIMEOUT)
        elapsed = loop.time() - started
    assert utterance.text == "Ramesh ji, ₹12,500 overdue hai"
    assert elapsed < NO_SPEECH_GRACE_S


async def test_a_segment_that_never_gets_a_transcript_does_not_wedge_the_call(
    fake_prisma: FakePrisma,
) -> None:
    """Review STT #1: a cough opens a segment Prisma never answers."""
    fake_prisma.swallow_next_segments = 1
    async with _stream(fake_prisma, FastClock()) as stt:
        stt.send_audio(b"\x05\x00" * 512 * 2)
        stt.commit()
        first = await stt.utterance(TIMEOUT)  # treated as silence after the answer timeout
        stt.send_audio(b"\x06\x00" * 512 * 2)
        stt.commit()
        second = await stt.utterance(TIMEOUT)
    assert first.text == ""
    assert second.text == "Ramesh ji, ₹12,500 overdue hai"


async def test_a_drop_during_the_replay_uses_the_next_attempt(fake_prisma: FakePrisma) -> None:
    """Review STT #3."""
    fake_prisma.drop_plan = {1: 3, 2: 1}  # drops mid-turn, then again during the replay
    async with _stream(fake_prisma, FastClock()) as stt:
        stt.send_audio(b"\x07\x00" * 512 * 4)
        stt.commit()
        result = await stt.utterance(TIMEOUT)
        reconnects = stt.reconnects
    assert result.text == "Ramesh ji, ₹12,500 overdue hai"
    assert len(fake_prisma.connections) == 3
    # The replay's sends can succeed into the buffer before the close arrives, so the second
    # drop may be noticed on receive and counted as its own reconnect.
    assert reconnects in (1, 2)
