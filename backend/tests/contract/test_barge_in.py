"""Barge-in (context doc 05, 08): the trainee interrupts the borrower.

Server steps, in order: cancel the reply, clear the sentence queue, reset the Timbre socket,
bump the minimum valid turn, trim the borrower's turn to the sentences the browser reported
`played`, keep only the interruption mood bump.
"""

import asyncio
from pathlib import Path

import pytest

from kalpakka.db import Database
from tests.contract.test_session import FRAME, Harness
from tests.session_fakes import reply_text

S1 = "Arre sir, auto kharab pada tha teen hafte."
S2 = "Main abhi ₹4,000 de sakta hoon."
S3 = "Baaki bees tareekh tak kar dunga, pakka."
PROMISE = {"mood_delta": -5, "trust_delta": 10, "promise": {"amount": 4000, "date": "2026-10-28"}}


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    return await Database.open(tmp_path / "k.db")


async def _first_turn(h: Harness) -> None:
    h.start()
    await h.transport.wait_for("audio_end")  # pickup
    await asyncio.sleep(0.01)  # let the (very short) fake pickup finish "playing"
    h.speak()


async def _end(h: Harness) -> str:
    h.transport.send_json(type="end_call")
    return await h.finish()


async def test_barge_in_mid_reply_keeps_only_played_sentences(db: Database) -> None:
    h = Harness(db)
    h.stt.transcripts = ["Ramesh ji, ₹12,500 overdue hai.", "Ruko ruko, pehle meri baat suniye."]
    h.replies.scripts = [reply_text(f"{S1} {S2} {S3}", PROMISE)]
    h.replies.piece_delay_s = 0.02  # Evon is still writing when the trainee interrupts
    await _first_turn(h)
    await h.transport.wait_for("audio_end", count=2)  # sentence 0 of turn 1 sent
    h.transport.send_json(type="played", turn=1, sentence=0)
    h.transport.send_json(type="ptt_start")
    await h.transport.wait_for("stop_audio")
    resets_at_barge_in = h.tts.resets
    h.transport.send_audio(FRAME)
    h.transport.send_json(type="ptt_end")
    await h.transport.wait_for("transcript", count=4)  # pickup, agent, S1, agent again
    await asyncio.sleep(0.1)  # the second reply's prompt has been built
    reason = await _end(h)

    assert reason == "agent_hangup"
    turns = await db.turns("call-1")
    borrower_1 = turns[2]
    assert borrower_1.role == "borrower"
    assert borrower_1.interrupted
    assert borrower_1.text == S1
    assert borrower_1.state_after is not None
    assert borrower_1.state_after["interruptions"] == 1
    assert borrower_1.state_after["promise"] is None  # the interrupted turn's update is discarded
    # The next prompt's history holds only what the trainee heard.
    second_prompt = h.replies.requests[1]
    assert {"role": "assistant", "content": S1} in second_prompt
    assert all(S2 not in m["content"] for m in second_prompt)
    assert resets_at_barge_in == 1


async def test_barge_in_after_reply_sent_trims_to_played(db: Database) -> None:
    h = Harness(db)
    h.replies.scripts = [reply_text(f"{S1} {S2}", PROMISE)]
    await _first_turn(h)
    await h.transport.wait_for("audio_end", count=3)  # both sentences sent, still playing
    h.transport.send_json(type="played", turn=1, sentence=0)
    h.transport.send_json(type="ptt_start")
    await h.transport.wait_for("stop_audio")
    await _end(h)
    turns = await db.turns("call-1")
    assert (turns[2].text, turns[2].interrupted) == (S1, True)


async def test_full_playback_applies_the_state_update(db: Database) -> None:
    h = Harness(db)
    h.replies.scripts = [reply_text(f"{S1} {S2}", PROMISE)]
    await _first_turn(h)
    await h.transport.wait_for("audio_end", count=3)
    h.transport.send_json(type="played", turn=1, sentence=0)
    h.transport.send_json(type="played", turn=1, sentence=1)
    await h.transport.wait_for("mood")
    await _end(h)
    turns = await db.turns("call-1")
    assert (turns[2].text, turns[2].interrupted) == (f"{S1} {S2}", False)
    assert turns[2].state_after is not None
    assert turns[2].state_after["promise"]["amount"] == 4000
    assert h.transport.messages("stop_audio") == []


async def test_without_played_reports_finished_playback_counts_as_heard(db: Database) -> None:
    h = Harness(db)
    h.replies.scripts = [reply_text(f"{S1} {S2}", PROMISE)]
    await _first_turn(h)
    await h.transport.wait_for("mood")  # estimated playback end passed: turn finalised
    h.transport.send_json(type="ptt_start")
    h.transport.send_json(type="ptt_end")
    await _end(h)
    turns = await db.turns("call-1")
    assert (turns[2].text, turns[2].interrupted) == (f"{S1} {S2}", False)
    assert h.transport.messages("stop_audio") == []


async def test_no_audio_of_an_interrupted_turn_after_stop_audio(db: Database) -> None:
    h = Harness(db)
    h.replies.scripts = [reply_text(f"{S1} {S2} {S3}", PROMISE)]
    h.replies.piece_delay_s = 0.02
    h.tts.chunk_delay_s = 0.01
    await _first_turn(h)
    await h.transport.wait_for("audio_start", count=2)
    h.transport.send_json(type="ptt_start")
    await h.transport.wait_for("stop_audio")
    await asyncio.sleep(0.2)  # give any stray task time to send
    await _end(h)
    sent = h.transport.sent
    stop = next(i for i, m in enumerate(sent) if isinstance(m, dict) and m["type"] == "stop_audio")
    after = sent[stop + 1 :]
    assert not any(isinstance(m, bytes) for m in after)
    assert not any(isinstance(m, dict) and m.get("turn") == 1 for m in after)


async def test_interrupting_before_the_transcript_merges_both_segments(db: Database) -> None:
    h = Harness(db)
    h.stt.transcripts = ["Ramesh ji, ₹12,500 overdue hai.", "Aaj kitna de sakte ho?"]
    h.stt.delay_s = 0.1
    h.start()
    await h.transport.wait_for("audio_end")
    h.speak()
    h.speak()  # pressed talk again before Prisma answered the first segment
    await h.transport.wait_for("mood")
    await _end(h)
    agent = [m["text"] for m in h.transport.messages("transcript") if m["role"] == "agent"]
    assert agent == ["Ramesh ji, ₹12,500 overdue hai. Aaj kitna de sakte ho?"]
    assert len(h.replies.requests) == 1


async def test_barge_in_over_the_silence_line_keeps_the_call_going(db: Database) -> None:
    """Review P2.3 #1: an interrupted turn must close the engine turn, or the next one fails."""
    h = Harness(db, silence_s=0.05)
    h.stt.transcripts = ["Haan Ramesh ji, main yahin hoon."]
    h.tts.chunk_delay_s = 0.0
    h.start()
    await h.transport.wait_for("audio_end", count=2)  # pickup, then the silence line
    h.speak()  # talks over "Hello? Sun rahe ho?"
    await h.transport.wait_for("transcript", count=4)
    await asyncio.sleep(0.05)
    assert await _end(h) == "agent_hangup"
    assert h.replies.requests  # the agent's turn reached the LLM: no "begin_turn twice"


async def test_silence_turn_store_window_is_safe_for_a_barge_in(db: Database) -> None:
    """Review P2.5 #2: ptt_start while the silent turn is still storing its agent row."""
    h = Harness(db, silence_s=0.05)
    h.stt.transcripts = ["Haan ji, sun raha hoon."]
    original = db.add_turn

    async def slow_add_turn(turn: object) -> None:
        await asyncio.sleep(0.05)
        await original(turn)  # type: ignore[arg-type]

    db.add_turn = slow_add_turn  # type: ignore[method-assign]
    h.start()
    await h.transport.wait_for("audio_end")
    await asyncio.sleep(0.08)  # the silence turn has begun and is inside the slow store
    h.speak()
    await h.transport.wait_for("transcript", count=3)
    await asyncio.sleep(0.2)
    assert await _end(h) == "agent_hangup"


async def test_barge_in_over_line_trouble_still_ends_the_call(db: Database) -> None:
    """Review P2.5 #3: an ending decided inside a turn survives a barge-in over its last line."""
    from kalpakka.llm import LLMError

    h = Harness(db)
    h.replies.fail_with = LLMError("LLM server returned 503", status=503, retryable=False)
    h.lines.delay_s["line_trouble"] = 0.2
    h.start()
    await h.transport.wait_for("audio_end")
    await asyncio.sleep(0.01)
    h.speak()
    await asyncio.sleep(0.1)  # "line kharab hai" is being prepared
    h.transport.send_json(type="ptt_start")
    assert await h.finish() == "llm_unavailable"


async def test_hanging_up_mid_stream_stores_an_interrupted_turn(db: Database) -> None:
    """Review P2.5 #4."""
    h = Harness(db)
    h.replies.scripts = [reply_text(f"{S1} {S2} {S3}", PROMISE)]
    h.replies.piece_delay_s = 0.03
    await _first_turn(h)
    await h.transport.wait_for("audio_end", count=2)
    assert await _end(h) == "agent_hangup"
    turns = await db.turns("call-1")
    assert turns[-1].role == "borrower"
    assert turns[-1].interrupted
    assert turns[-1].state_after is not None
    assert turns[-1].state_after["promise"] is None


async def test_reply_heard_before_the_time_cap_is_still_stored(db: Database) -> None:
    """Review P2.5 #5: the cap fires inside the `played` grace window."""
    import kalpakka.session as session_module
    from kalpakka.session import SessionConfig
    from tests.contract.test_session import SIM_DATE

    h = Harness(db)
    h.deps.config = SessionConfig(sim_date=SIM_DATE, time_cap_s=0.4, silence_s=30)
    h.replies.scripts = [reply_text(f"{S1} {S2}", PROMISE)]
    original = session_module.HARD_CAP_GRACE_S
    session_module.HARD_CAP_GRACE_S = 0.0
    try:
        h.start()
        await h.transport.wait_for("audio_end")
        h.transport.send_json(type="played", turn=0, sentence=0)  # this browser reports played
        await asyncio.sleep(0.01)
        h.speak()
        await h.transport.wait_for("audio_end", count=3)
        h.transport.send_json(type="played", turn=1, sentence=0)  # sentence 1 still playing
        assert await h.finish() == "time_cap"
    finally:
        session_module.HARD_CAP_GRACE_S = original
    turns = await db.turns("call-1")
    assert [t.role for t in turns][-1] == "borrower"
    assert turns[-1].text == S1
