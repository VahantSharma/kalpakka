"""Browser <-> backend WebSocket `/call` messages: the table in context doc 05, nothing more.

Binary frames are 16-bit little-endian PCM (up: mic at 16 kHz in 1,024-byte frames; down:
borrower audio at the rate announced in `audio_start`). Text frames are JSON with a `type`.
Client messages are validated on the way in (`parse_client_message`); server messages are
pydantic models serialised by `encode`. Hidden borrower state has no field here by design.

Deviation from 05 (recorded in the P2.3 log): `briefing` also carries `call_id`, so the browser
can fetch `GET /calls/{id}/scorecard` after the call.
"""

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from kalpakka.personas import Difficulty


class _Msg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- browser -> backend ------------------------------------------------------------------------


class StartCall(_Msg):
    type: Literal["start_call"] = "start_call"
    persona: Annotated[str, Field(min_length=1, max_length=64)]
    difficulty: Difficulty
    anon_id: Annotated[str, Field(min_length=1, max_length=128)]


class PttStart(_Msg):
    type: Literal["ptt_start"] = "ptt_start"


class PttEnd(_Msg):
    type: Literal["ptt_end"] = "ptt_end"


class Played(_Msg):
    type: Literal["played"] = "played"
    turn: Annotated[int, Field(ge=0)]
    sentence: Annotated[int, Field(ge=0)]


class EndCall(_Msg):
    type: Literal["end_call"] = "end_call"


ClientMessage = Annotated[
    StartCall | PttStart | PttEnd | Played | EndCall, Field(discriminator="type")
]
_client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


class ProtocolError(Exception):
    pass


def parse_client_message(raw: str) -> StartCall | PttStart | PttEnd | Played | EndCall:
    try:
        data: Any = json.loads(raw)  # Any: untrusted JSON, validated by the adapter
    except json.JSONDecodeError as exc:
        raise ProtocolError("not JSON") from exc
    try:
        return _client_adapter.validate_python(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise ProtocolError(f"bad message: {where}: {first['msg']}") from exc


# --- backend -> browser ------------------------------------------------------------------------


class Busy(_Msg):
    type: Literal["busy"] = "busy"
    position: int
    eta_s: int


class Briefing(_Msg):
    type: Literal["briefing"] = "briefing"
    call_id: str
    facts: dict[str, Any]  # Any: the persona's public card (PublicCard.model_dump), JSON-safe
    sim_clock: str  # ISO "YYYY-MM-DDTHH:MM", the call's simulated date and time


class AudioStart(_Msg):
    type: Literal["audio_start"] = "audio_start"
    turn: int
    sentence: int
    rate: int


class AudioEnd(_Msg):
    type: Literal["audio_end"] = "audio_end"
    turn: int
    sentence: int


class StopAudio(_Msg):
    type: Literal["stop_audio"] = "stop_audio"


class Transcript(_Msg):
    type: Literal["transcript"] = "transcript"
    role: Literal["agent", "borrower"]
    text: str
    t: float  # seconds since call start


class FlagMsg(_Msg):
    type: Literal["flag"] = "flag"
    flag: dict[str, Any]  # Any: tripwire.Flag.model_dump()


class FlagUpdate(_Msg):
    type: Literal["flag_update"] = "flag_update"
    flag: dict[str, Any]  # Any: tripwire.Flag.model_dump()


class Mood(_Msg):
    type: Literal["mood"] = "mood"
    label: str  # label only, never numbers


class CallEnded(_Msg):
    type: Literal["call_ended"] = "call_ended"
    reason: str


class ScorecardMsg(_Msg):
    type: Literal["scorecard"] = "scorecard"
    scorecard: dict[str, Any]  # Any: judge.Scorecard.model_dump()


class ErrorMsg(_Msg):
    type: Literal["error"] = "error"
    code: str
    message: str


ServerMessage = (
    Busy
    | Briefing
    | AudioStart
    | AudioEnd
    | StopAudio
    | Transcript
    | FlagMsg
    | FlagUpdate
    | Mood
    | CallEnded
    | ScorecardMsg
    | ErrorMsg
)


def encode(message: ServerMessage) -> str:
    return json.dumps(message.model_dump(mode="json"), ensure_ascii=False)
