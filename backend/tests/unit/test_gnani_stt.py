"""Pure parts of the Prisma client: headers, frame packing, pacing, message parsing."""

import pytest

from kalpakka.gnani_stt import (
    FRAME_BYTES,
    FramePacker,
    Pacer,
    SttConfig,
    SttConnected,
    SttError,
    SttProcessing,
    SttSpeechStart,
    SttTranscript,
    SttUnknown,
    build_headers,
    config_for_persona,
    parse_message,
)
from tests.factories import make_persona


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += max(0.0, seconds)


# --- headers ---------------------------------------------------------------------------------


def _config(**kw: object) -> SttConfig:
    base: dict[str, object] = {
        "language_code": "hi-IN,en-IN",
        "keyterms": ["EMI", "NACH", "SwiftFin"],
        "request_id": "call-1",
    }
    base.update(kw)
    return SttConfig.model_validate(base)


def test_headers_carry_documented_config_for_a_call() -> None:
    headers = build_headers("k-123", _config())
    assert headers["x-api-key-id"] == "k-123"
    assert headers["model"] == "prisma-v3.0"
    assert headers["language_code"] == "hi-IN,en-IN"
    assert headers["format"] == "transcribe"
    assert headers["x-sample-rate"] == "16000"
    assert headers["x-min-silence-ms"] == "600"
    assert headers["x-idle-timeout-sec"] == "300"
    assert headers["x-api-request-id"] == "call-1"


def test_keyterms_are_joined_into_one_header() -> None:
    assert build_headers("k", _config())["keyterms"] == "EMI,NACH,SwiftFin"


def test_no_keyterms_header_when_list_is_empty() -> None:
    assert "keyterms" not in build_headers("k", _config(keyterms=[]))


def test_phone_mode_sends_8k_sample_rate() -> None:
    assert build_headers("k", _config(sample_rate=8000))["x-sample-rate"] == "8000"


def test_config_for_persona_uses_its_languages_and_keyterms() -> None:
    persona = make_persona()
    config = config_for_persona(persona, call_id="c-42")
    assert config.language_code == "hi-IN,en-IN"
    assert config.keyterms == ["Ramesh", "SwiftFin", "EMI"]
    assert config.request_id == "c-42"


def test_more_than_200_keyterms_is_rejected() -> None:
    with pytest.raises(ValueError):
        _config(keyterms=[f"t{i}" for i in range(201)])


# --- frame packing ---------------------------------------------------------------------------


def test_frame_is_512_int16_samples() -> None:
    assert FRAME_BYTES == 1024


def test_packer_emits_only_whole_1024_byte_frames() -> None:
    packer = FramePacker()
    assert packer.feed(b"\x01" * 1000) == []
    frames = packer.feed(b"\x02" * 1100)
    assert [len(f) for f in frames] == [1024, 1024]
    assert frames[0] == b"\x01" * 1000 + b"\x02" * 24


def test_flush_pads_the_remainder_with_zeros() -> None:
    packer = FramePacker()
    packer.feed(b"\x05" * 300)
    frame = packer.flush()
    assert frame == b"\x05" * 300 + b"\x00" * 724


def test_flush_with_nothing_buffered_sends_nothing() -> None:
    packer = FramePacker()
    packer.feed(b"\x05" * 1024)
    assert packer.flush() is None


# --- pacing ----------------------------------------------------------------------------------


async def test_pacer_spaces_frames_32_ms_apart_on_a_deadline() -> None:
    clock = FakeClock()
    pacer = Pacer(clock=clock, sleep=clock.sleep, frame_s=0.032)
    start = clock.now
    for _ in range(10):
        await pacer.wait()
        clock.now += 0.005  # the send itself takes 5 ms; a deadline clock absorbs it
    assert clock.now - start == pytest.approx(9 * 0.032 + 0.005, abs=1e-9)


async def test_pacer_does_not_burst_after_an_idle_gap() -> None:
    clock = FakeClock()
    pacer = Pacer(clock=clock, sleep=clock.sleep, frame_s=0.032)
    await pacer.wait()
    clock.now += 5.0  # trainee was silent for 5 s
    await pacer.wait()
    t_after_gap = clock.now
    await pacer.wait()
    assert clock.now - t_after_gap == pytest.approx(0.032)


def test_frame_duration_follows_sample_rate() -> None:
    assert Pacer.frame_seconds(16000) == pytest.approx(0.032)
    assert Pacer.frame_seconds(8000) == pytest.approx(0.064)


# --- messages --------------------------------------------------------------------------------


def test_parse_transcript_message() -> None:
    raw = (
        '{"type":"transcript","text":"main ₹4,000 abhi de sakta hoon",'
        '"detected_language_code":"hi-IN","latency":0.41,"segment_index":3}'
    )
    msg = parse_message(raw)
    assert msg == SttTranscript(
        text="main ₹4,000 abhi de sakta hoon",
        detected_language_code="hi-IN",
        latency=0.41,
        segment_index=3,
    )


def test_parse_transcript_nested_under_data() -> None:
    raw = '{"type":"transcript","data":{"text":"हाँ बोलिए","segment_index":0}}'
    msg = parse_message(raw)
    assert isinstance(msg, SttTranscript)
    assert msg.text == "हाँ बोलिए"


def test_parse_control_messages() -> None:
    assert isinstance(parse_message('{"type":"speech_start"}'), SttSpeechStart)
    assert isinstance(parse_message('{"type":"processing"}'), SttProcessing)
    connected = parse_message('{"type":"connected","model":"prisma-v3.0","format":"transcribe"}')
    assert isinstance(connected, SttConnected)
    assert connected.echo["model"] == "prisma-v3.0"


def test_parse_error_keeps_message_text() -> None:
    msg = parse_message('{"type":"error","message":"invalid language_code"}')
    assert msg == SttError(message="invalid language_code", code=None)


def test_unknown_or_malformed_messages_do_not_raise() -> None:
    assert isinstance(parse_message('{"type":"heartbeat"}'), SttUnknown)
    assert isinstance(parse_message("not json"), SttUnknown)
    assert isinstance(parse_message("[1,2]"), SttUnknown)
