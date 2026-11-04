"""Pure parts of the Timbre client: request shape, message parsing, PCM framing, cache keys."""

import base64
import json

from kalpakka.gnani_tts import (
    PcmAligner,
    TtsAudio,
    TtsComplete,
    TtsError,
    TtsStart,
    TtsUnknown,
    Voice,
    build_request,
    cache_key,
    parse_message,
    voice_for_persona,
)
from tests.factories import make_persona

RAMESH = Voice(name="Devansh", language="hi-IN")


def test_request_is_one_sentence_with_documented_fields() -> None:
    body = json.loads(build_request("Arre sir, ₹4,000 abhi de sakta hoon.", RAMESH, speed=0.95))
    assert body == {
        "text": "Arre sir, ₹4,000 abhi de sakta hoon.",
        "voice": "Devansh",
        "model": "timbre-v3.0",
        "language": "hi-IN",
        "speed": 0.95,
        "audio_config": {"container": "raw", "sample_rate": 24000},
    }


def test_request_keeps_devanagari_and_tags_unescaped() -> None:
    raw = build_request("<sigh> हाँ बोलिए", RAMESH, speed=1.0)
    assert "<sigh> हाँ बोलिए" in raw


def test_voice_for_persona_reads_speech_section() -> None:
    assert voice_for_persona(make_persona()) == RAMESH


def test_parse_audio_decodes_base64_under_data() -> None:
    pcm = b"\x01\x00\x02\x00"
    raw = json.dumps({"type": "audio", "data": {"audio": base64.b64encode(pcm).decode()}})
    assert parse_message(raw) == TtsAudio(pcm=pcm)


def test_parse_lifecycle_messages() -> None:
    assert parse_message('{"type":"start","request_id":"r-1"}') == TtsStart(request_id="r-1")
    assert parse_message('{"type":"complete"}') == TtsComplete()
    assert parse_message('{"type":"error","message":"voice not found"}') == TtsError(
        message="voice not found"
    )


def test_bad_messages_do_not_raise() -> None:
    assert isinstance(parse_message("{"), TtsUnknown)
    assert isinstance(parse_message('{"type":"audio","data":{"audio":"%%%"}}'), TtsUnknown)
    assert isinstance(parse_message('{"type":"metrics"}'), TtsUnknown)


def test_aligner_never_emits_half_a_sample() -> None:
    aligner = PcmAligner()
    assert aligner.feed(b"\x01\x02\x03") == b"\x01\x02"
    assert aligner.feed(b"\x04") == b"\x03\x04"
    assert aligner.feed(b"") == b""


def test_aligner_drops_a_dangling_byte_at_the_end() -> None:
    aligner = PcmAligner()
    aligner.feed(b"\x01\x02\x03")
    assert aligner.finish() == 1  # one byte dropped, reported for the log


def test_cache_key_changes_with_anything_that_changes_the_audio() -> None:
    line = "Haan, hello? Kaun bol raha hai?"
    base = cache_key(line, RAMESH, speed=1.0)
    assert cache_key(line, RAMESH, speed=1.0) == base
    assert cache_key(line, Voice(name="Shubham", language="hi-IN"), speed=1.0) != base
    assert cache_key(line, Voice(name="Devansh", language="hi-en"), speed=1.0) != base
    assert cache_key(line, RAMESH, speed=0.9) != base
    assert cache_key(line + " ", RAMESH, speed=1.0) != base
