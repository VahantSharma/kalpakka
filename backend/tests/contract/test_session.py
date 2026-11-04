"""CallSession: one call end to end through in-process fakes (no sockets, no network)."""

import asyncio
import datetime as dt
from pathlib import Path

import pytest

from kalpakka.borrower_state import PromiseSuggestion, check_promise
from kalpakka.brain import FirstTokenTimeout, Prompt
from kalpakka.clock import SystemClock
from kalpakka.db import Database
from kalpakka.session import CallDeps, CallSession, SessionConfig, story_setup
from tests.factories import make_persona
from tests.session_fakes import (
    FakeLines,
    FakeReplies,
    FakeStt,
    FakeTransport,
    FakeTts,
    WallClock,
    open_fake,
    reply_text,
)

RAMESH = make_persona()
SIM_DATE = dt.date(2026, 10, 28)
FRAME = b"\x10\x00" * 512
REPLY = "Arre sir, auto kharab pada tha teen hafte. Main abhi ₹4,000 de sakta hoon."


class Harness:
    def __init__(self, db: Database, *, silence_s: float = 30.0) -> None:
        self.transport = FakeTransport()
        self.stt = FakeStt(transcripts=["Ramesh ji, aapka ₹12,500 overdue hai, kya hua?"])
        self.tts = FakeTts()
        self.replies = FakeReplies(scripts=[reply_text(REPLY)])
        self.lines = FakeLines()
        self.db = db
        self.wall = WallClock(dt.datetime(2026, 12, 25, 18, 0, tzinfo=dt.UTC))  # "today" is late
        self.deps = CallDeps(
            personas={"ramesh": RAMESH},
            system_prompt=Prompt(name="borrower-system", version=1, text="You are a borrower."),
            open_stt=lambda persona, call_id: open_fake(self.stt),
            open_tts=lambda: open_fake(self.tts),
            replies=self.replies,
            lines=self.lines,
            db=db,
            clock=SystemClock(),
            wall=self.wall,
            seeds=iter(range(7, 100)).__next__,
            config=SessionConfig(
                sim_date=SIM_DATE,
                time_cap_s=None,
                silence_s=silence_s,
                transcript_timeout_s=2.0,
                playback_grace_s=0.02,
            ),
        )
        self.session = CallSession(self.deps, self.transport, call_id="call-1")
        self.task: asyncio.Task[str] | None = None

    def start(self) -> None:
        self.transport.send_json(
            type="start_call", persona="ramesh", difficulty="medium", anon_id="a1"
        )
        self.task = asyncio.create_task(self.session.run())

    def speak(self, frames: int = 3) -> None:
        self.transport.send_json(type="ptt_start")
        for _ in range(frames):
            self.transport.send_audio(FRAME)
        self.transport.send_json(type="ptt_end")

    async def finish(self) -> str:
        assert self.task is not None
        return await asyncio.wait_for(self.task, 5)


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    return await Database.open(tmp_path / "k.db")


async def test_call_starts_with_briefing_then_pickup_line(db: Database) -> None:
    h = Harness(db)
    h.start()
    await h.transport.wait_for("audio_end")
    first = h.transport.messages()
    assert first[0]["type"] == "briefing"
    assert first[0]["call_id"] == "call-1"
    assert first[0]["facts"]["display_name"] == "Ramesh bhaiya"
    assert first[1] == {"type": "audio_start", "turn": 0, "sentence": 0, "rate": 24000}
    pickup = h.transport.messages("transcript")[0]
    assert (pickup["role"], pickup["text"]) == ("borrower", "Haan, hello? Kaun bol raha hai?")
    assert h.lines.rendered == ["pickup"]
    h.transport.send_json(type="end_call")
    assert await h.finish() == "agent_hangup"


async def test_sim_clock_is_the_story_date_not_the_wall_clock(db: Database) -> None:
    h = Harness(db)
    h.start()
    await h.transport.wait_for("briefing")
    assert h.transport.messages("briefing")[0]["sim_clock"].startswith("2026-10-28T")
    h.transport.send_json(type="end_call")
    await h.finish()


def test_story_promise_of_4000_now_and_rest_by_the_20th_is_valid() -> None:
    setup = story_setup(RAMESH, "medium", seed=7, sim_date=SIM_DATE, time_cap_s=None)
    now = check_promise(
        PromiseSuggestion(amount=4000, date=setup.sim_date), RAMESH, setup.sim_date, 60
    )
    rest = check_promise(
        PromiseSuggestion(amount=12500, date=dt.date(2026, 11, 20)), RAMESH, setup.sim_date, 60
    )
    assert now.valid, now.problems
    assert rest.valid, rest.problems


async def test_one_agent_turn_streams_borrower_sentences_and_stores_turns(db: Database) -> None:
    h = Harness(db)
    h.start()
    await h.transport.wait_for("audio_end")
    h.speak(frames=3)
    await h.transport.wait_for("mood")
    h.transport.send_json(type="end_call")
    assert await h.finish() == "agent_hangup"

    assert bytes(h.stt.audio) == FRAME * 3
    assert h.stt.commits == 1
    roles = [(m["role"], m["text"]) for m in h.transport.messages("transcript")]
    assert roles[1] == ("agent", "Ramesh ji, aapka ₹12,500 overdue hai, kya hua?")
    borrower_lines = [text for role, text in roles[2:] if role == "borrower"]
    assert " ".join(borrower_lines) == REPLY
    starts = h.transport.messages("audio_start")
    assert [(s["turn"], s["sentence"]) for s in starts] == [(0, 0), (1, 0), (1, 1)]
    assert h.transport.messages("mood")[0]["label"] in {"calm", "irritated", "angry", "furious"}
    assert h.transport.messages("call_ended")[-1]["reason"] == "agent_hangup"

    turns = await db.turns("call-1")
    assert [(t.idx, t.role) for t in turns] == [(0, "borrower"), (1, "agent"), (2, "borrower")]
    assert turns[2].text == REPLY
    assert turns[2].state_after is not None
    call = await db.call("call-1")
    assert call is not None
    assert call.end_reason == "agent_hangup"
    assert call.ended_at == dt.datetime(2026, 12, 25, 18, 0, tzinfo=dt.UTC)


async def test_prompt_gets_the_agent_words_and_a_turn_brief(db: Database) -> None:
    h = Harness(db)
    h.start()
    await h.transport.wait_for("audio_end")
    h.speak()
    await h.transport.wait_for("mood")
    h.transport.send_json(type="end_call")
    await h.finish()
    messages = h.replies.requests[0]
    assert messages[0]["role"] == "system"
    assert "2026-10-28" in messages[0]["content"]
    assert messages[-1]["content"].startswith("Ramesh ji, aapka ₹12,500 overdue hai")


async def test_hidden_truth_never_reaches_the_browser(db: Database) -> None:
    h = Harness(db)
    h.start()
    await h.transport.wait_for("audio_end")
    h.speak()
    await h.transport.wait_for("mood")
    h.transport.send_json(type="end_call")
    await h.finish()
    everything = repr(h.transport.messages())
    assert RAMESH.hidden.truth not in everything
    assert "reveal_trust" not in everything
    assert "can_pay" not in everything
    assert "trust" not in everything


async def test_silence_plays_the_silence_line_and_two_strikes_end_the_call(db: Database) -> None:
    h = Harness(db, silence_s=0.05)
    h.start()
    assert await h.finish() == "silence"
    assert h.lines.rendered == ["pickup", "silence", "silence"]
    assert h.replies.requests == []  # silence never costs an LLM call


async def test_browser_disconnect_ends_and_records_the_call(db: Database) -> None:
    h = Harness(db)
    h.start()
    await h.transport.wait_for("audio_end")
    h.transport.disconnect()
    assert await h.finish() == "disconnected"
    call = await db.call("call-1")
    assert call is not None
    assert call.end_reason == "disconnected"


async def test_unknown_persona_is_an_error_and_no_call_is_stored(db: Database) -> None:
    h = Harness(db)
    h.transport.send_json(type="start_call", persona="sherlock", difficulty="easy", anon_id="a")
    reason = await asyncio.wait_for(h.session.run(), 5)
    assert reason == "bad_start"
    assert h.transport.messages("error")[0]["code"] == "unknown_persona"
    assert await db.call("call-1") is None


async def test_first_message_must_be_start_call(db: Database) -> None:
    h = Harness(db)
    h.transport.send_json(type="ptt_start")
    assert await asyncio.wait_for(h.session.run(), 5) == "bad_start"
    assert h.transport.messages("error")[0]["code"] == "expected_start_call"


async def test_bad_message_mid_call_is_a_recoverable_error(db: Database) -> None:
    h = Harness(db)
    h.start()
    await h.transport.wait_for("audio_end")
    h.transport.inbox.put_nowait('{"type":"dance"}')
    await h.transport.wait_for("error")
    assert h.transport.messages("error")[0]["code"] == "bad_message"
    h.transport.send_json(type="end_call")
    assert await h.finish() == "agent_hangup"


async def test_llm_without_first_token_plays_line_trouble_and_ends(db: Database) -> None:
    h = Harness(db)
    h.replies.fail_with = FirstTokenTimeout("no first token in 2.0 s")
    h.start()
    await h.transport.wait_for("audio_end")
    h.speak()
    assert await h.finish() == "llm_unavailable"
    assert h.lines.rendered == ["pickup", "line_trouble"]


async def test_audio_is_ignored_while_talk_is_not_pressed(db: Database) -> None:
    h = Harness(db)
    h.start()
    await h.transport.wait_for("audio_end")
    h.transport.send_audio(FRAME)  # no ptt_start: open mic noise
    h.transport.send_json(type="end_call")
    await h.finish()
    assert bytes(h.stt.audio) == b""


async def test_empty_transcript_counts_as_a_silent_turn(db: Database) -> None:
    h = Harness(db)
    h.stt.transcripts = [""]
    h.start()
    await h.transport.wait_for("audio_end")
    h.speak()
    await h.transport.wait_for("audio_end", count=2)
    h.transport.send_json(type="end_call")
    await h.finish()
    assert h.lines.rendered == ["pickup", "silence"]
    assert h.replies.requests == []


async def test_mic_noise_outside_push_to_talk_does_not_reset_the_silence_timer(
    db: Database,
) -> None:
    """Review P2.3 #2."""
    h = Harness(db, silence_s=0.15)
    h.start()
    await h.transport.wait_for("audio_end")
    for _ in range(10):
        h.transport.send_audio(FRAME)  # an open mic streaming noise every 30 ms
        await asyncio.sleep(0.03)
    assert h.lines.rendered[:2] == ["pickup", "silence"]
    h.transport.send_json(type="end_call")
    await h.finish()


async def test_hard_cap_ends_a_call_even_with_talk_held_down(db: Database) -> None:
    """Review P2.3 #3: one stuck tab must not hold the only call slot forever."""
    import kalpakka.session as session_module

    h = Harness(db)
    h.deps.config = SessionConfig(
        sim_date=SIM_DATE, time_cap_s=0.1, silence_s=30, playback_grace_s=0.02
    )
    original = session_module.HARD_CAP_GRACE_S
    session_module.HARD_CAP_GRACE_S = 0.1
    try:
        h.start()
        await h.transport.wait_for("audio_end")
        h.transport.send_json(type="ptt_start")  # ...and never released
        assert await h.finish() == "time_cap"
    finally:
        session_module.HARD_CAP_GRACE_S = original


async def test_browser_dropping_mid_reply_is_recorded_as_disconnected(db: Database) -> None:
    """Review P2.3 #5."""
    h = Harness(db)
    h.replies.piece_delay_s = 0.05
    h.start()
    await h.transport.wait_for("audio_end")
    await asyncio.sleep(0.01)
    h.speak()
    await h.transport.wait_for("transcript", count=2)

    async def broken(data: str) -> None:
        raise session_module_transport_closed("socket gone")

    from kalpakka.session import TransportClosed as session_module_transport_closed

    h.transport.send_text = broken  # type: ignore[method-assign]
    h.transport.send_bytes = broken  # type: ignore[assignment,method-assign]
    assert await h.finish() == "disconnected"
    call = await db.call("call-1")
    assert call is not None
    assert call.end_reason == "disconnected"


async def test_losing_speech_to_text_mid_call_ends_with_stt_unavailable(db: Database) -> None:
    """Review P2.3 #7."""
    from kalpakka.gnani_stt import SttUnavailable

    h = Harness(db)

    async def lost(timeout_s: float) -> object:
        raise SttUnavailable("prisma stream lost after 3 reconnect attempts")

    h.stt.utterance = lost  # type: ignore[method-assign,assignment]
    h.start()
    await h.transport.wait_for("audio_end")
    h.speak()
    assert await h.finish() == "stt_unavailable"
    assert h.transport.messages("error")[0]["code"] == "stt_unavailable"


async def test_each_turn_records_its_latency_marks_and_stages(db: Database) -> None:
    h = Harness(db)
    h.start()
    await h.transport.wait_for("audio_end")
    await asyncio.sleep(0.01)
    h.speak()
    await h.transport.wait_for("audio_end", count=3)
    h.transport.send_json(type="played", turn=1, sentence=0)
    h.transport.send_json(type="played", turn=1, sentence=1)
    await h.transport.wait_for("mood")
    h.transport.send_json(type="end_call")
    await h.finish()

    turns = await db.turns("call-1")
    latency = turns[2].latency
    for mark in (
        "ptt_end",
        "commit_sent",
        "transcript_final",
        "brief_built",
        "llm_request",
        "llm_first_token",
        "first_sentence_ready",
        "tts_request",
        "tts_first_audio",
        "browser_first_play",
    ):
        assert mark in latency, mark
    assert latency["ptt_end"] == 0.0
    assert latency["ptt_end"] <= latency["transcript_final"] <= latency["tts_first_audio"]
    assert "stage_server_total" in latency
    report = h.deps.latency.report()
    assert report["turns"] == 1
    assert report["stages"]["server_total"]["n"] == 1


class FakeJudge:
    def __init__(self, delay_s: float = 0.0) -> None:
        self.judged: list[str] = []
        self.delay_s = delay_s

    async def judge(self, call_id: str) -> tuple[dict[str, object], str]:
        await asyncio.sleep(self.delay_s)
        self.judged.append(call_id)
        return {"total": 71, "worst_line": "Paise nahi diye toh police bhej dunga"}, "judge-v1"


async def test_after_the_call_it_is_judged_and_the_scorecard_sent_and_stored(
    db: Database,
) -> None:
    h = Harness(db)
    h.deps.judge = FakeJudge()
    h.start()
    await h.transport.wait_for("audio_end")
    h.transport.send_json(type="end_call")
    await h.finish()
    kinds = [m["type"] for m in h.transport.messages()]
    assert kinds.index("call_ended") < kinds.index("scorecard")
    assert h.transport.messages("scorecard")[0]["scorecard"]["total"] == 71
    assert await db.scorecard("call-1") == {
        "total": 71,
        "worst_line": "Paise nahi diye toh police bhej dunga",
    }


async def test_a_call_whose_browser_left_is_still_judged_and_saved(db: Database) -> None:
    h = Harness(db)
    h.deps.judge = FakeJudge()
    h.start()
    await h.transport.wait_for("audio_end")
    h.transport.disconnect()
    assert await h.finish() == "disconnected"
    assert await db.scorecard("call-1") is not None
