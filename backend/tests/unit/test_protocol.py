"""Browser <-> backend WebSocket messages: exactly the table in context doc 05."""

import json

import pytest

from kalpakka.protocol import (
    AudioEnd,
    AudioStart,
    Briefing,
    Busy,
    CallEnded,
    EndCall,
    ErrorMsg,
    FlagMsg,
    Mood,
    Played,
    ProtocolError,
    PttEnd,
    PttStart,
    StartCall,
    StopAudio,
    Transcript,
    encode,
    parse_client_message,
)


def test_start_call_is_parsed_with_its_three_fields() -> None:
    msg = parse_client_message(
        '{"type":"start_call","persona":"ramesh","difficulty":"medium","anon_id":"a-1"}'
    )
    assert msg == StartCall(persona="ramesh", difficulty="medium", anon_id="a-1")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"type":"ptt_start"}', PttStart()),
        ('{"type":"ptt_end"}', PttEnd()),
        ('{"type":"end_call"}', EndCall()),
        ('{"type":"played","turn":3,"sentence":1}', Played(turn=3, sentence=1)),
    ],
)
def test_control_messages_are_parsed(raw: str, expected: object) -> None:
    assert parse_client_message(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"type":"start_call","persona":"ramesh"}',
        '{"type":"start_call","persona":"ramesh","difficulty":"insane","anon_id":"a"}',
        '{"type":"played","turn":"three","sentence":0}',
        '{"type":"launch_missiles"}',
        "[]",
    ],
)
def test_bad_client_messages_raise_protocol_error(raw: str) -> None:
    with pytest.raises(ProtocolError):
        parse_client_message(raw)


def test_server_messages_encode_with_their_type() -> None:
    assert json.loads(encode(Busy(position=2, eta_s=90))) == {
        "type": "busy",
        "position": 2,
        "eta_s": 90,
    }
    assert json.loads(encode(AudioStart(turn=1, sentence=0, rate=24000))) == {
        "type": "audio_start",
        "turn": 1,
        "sentence": 0,
        "rate": 24000,
    }
    assert json.loads(encode(AudioEnd(turn=1, sentence=0)))["type"] == "audio_end"
    assert json.loads(encode(StopAudio())) == {"type": "stop_audio"}
    assert json.loads(encode(CallEnded(reason="agent_hangup")))["reason"] == "agent_hangup"
    assert json.loads(encode(ErrorMsg(code="bad_message", message="x")))["code"] == "bad_message"
    assert json.loads(encode(Mood(label="irritated"))) == {"type": "mood", "label": "irritated"}


def test_transcript_keeps_hinglish_unescaped() -> None:
    raw = encode(Transcript(role="borrower", text="हाँ, ₹4,000 abhi de sakta hoon", t=12.5))
    assert "हाँ, ₹4,000" in raw


def test_flag_message_carries_the_flag_object() -> None:
    flag = {
        "id": "t3-s0-threat",
        "turn": 3,
        "segment": 0,
        "t": 41.2,
        "rule": "threat",
        "quote": "police bhej dunga",
        "layer": "lexicon",
        "status": "possible",
    }
    out = json.loads(encode(FlagMsg.model_validate({"flag": flag})))
    assert out == {"type": "flag", "flag": flag}


def test_briefing_has_visible_facts_and_sim_clock_only() -> None:
    msg = Briefing(
        call_id="c1", facts={"display_name": "Ramesh bhaiya"}, sim_clock="2026-10-28T11:40"
    )
    out = json.loads(encode(msg))
    assert set(out) == {"type", "call_id", "facts", "sim_clock"}
