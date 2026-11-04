import asyncio

import pytest

from kalpakka.brain import DELIMITER, FirstTokenTimeout, ReplyDone, SpeechDelta, stream_reply
from kalpakka.clock import SystemClock
from kalpakka.llm import ChatTemplateThinkingAdapter, LLMClient, PlainAdapter
from tests.contract.fakes.fake_openai import FakeOpenAI, Script

MESSAGES = [{"role": "user", "content": "Namaste Ramesh ji."}]


def client(fake: FakeOpenAI) -> LLMClient:
    return LLMClient(base_url=fake.base_url, api_key="k", model="evon-test", adapter=PlainAdapter())


async def test_reply_streams_speech_then_ends_with_the_parsed_state(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(
        Script(pieces=["Arre sir, ", "kal pakka. ", "###STA", 'TE###\n{"mood_delta": 6}'])
    )

    events = [e async for e in stream_reply(client(fake_llm), MESSAGES, clock=SystemClock())]

    speech = "".join(e.text for e in events if isinstance(e, SpeechDelta))
    done = events[-1]
    assert speech == "Arre sir, kal pakka. "
    assert isinstance(done, ReplyDone)
    assert done.result.suggestion.mood_delta == 6
    assert done.result.errors == []


async def test_speech_done_is_signalled_before_the_json_finishes(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(
        Script(pieces=["Haan ji bolo. ", DELIMITER, '{"mood_delta":', " 1}"], piece_delay_s=0.01)
    )

    kinds = [
        type(e).__name__
        async for e in stream_reply(client(fake_llm), MESSAGES, clock=SystemClock())
    ]

    assert kinds == ["SpeechDelta", "SpeechDone", "ReplyDone"]


async def test_first_token_latency_is_measured(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(Script(pieces=["Haan. ", DELIMITER, "{}"], first_delay_s=0.2))

    events = [e async for e in stream_reply(client(fake_llm), MESSAGES, clock=SystemClock())]

    done = events[-1]
    assert isinstance(done, ReplyDone)
    assert done.first_token_s is not None
    assert done.first_token_s >= 0.15


async def test_no_first_token_in_time_raises(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(Script(pieces=["late"], first_delay_s=1.0))

    with pytest.raises(FirstTokenTimeout):
        async for _ in stream_reply(
            client(fake_llm), MESSAGES, clock=SystemClock(), first_token_timeout_s=0.2
        ):
            pass


async def test_thinking_tokens_never_reach_speech(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(
        Script(pieces=["<thi", "nk>he is polite</th", "ink>Theek hai sir. ", DELIMITER, "{}"])
    )

    events = [e async for e in stream_reply(client(fake_llm), MESSAGES, clock=SystemClock())]

    speech = "".join(e.text for e in events if isinstance(e, SpeechDelta))
    assert speech == "Theek hai sir. "
    done = events[-1]
    assert isinstance(done, ReplyDone)
    assert done.result.think_blocks == 1


async def test_reply_requests_thinking_off_and_short_output(fake_llm: FakeOpenAI) -> None:
    async for _ in stream_reply(client(fake_llm), MESSAGES, clock=SystemClock(), max_tokens=170):
        pass

    assert fake_llm.requests[0]["max_tokens"] == 170


async def test_reply_asks_the_server_to_turn_thinking_off(fake_llm: FakeOpenAI) -> None:
    c = LLMClient(
        base_url=fake_llm.base_url, api_key="k", model="m", adapter=ChatTemplateThinkingAdapter()
    )

    async for _ in stream_reply(c, MESSAGES, clock=SystemClock()):
        pass

    assert fake_llm.requests[0]["chat_template_kwargs"] == {"enable_thinking": False}


async def test_closing_the_reply_mid_stream_closes_the_upstream_request(
    fake_llm: FakeOpenAI,
) -> None:
    fake_llm.scripts.append(Script(pieces=["Haan ji bolo sir. "] * 50, piece_delay_s=0.02))
    reply = stream_reply(client(fake_llm), MESSAGES, clock=SystemClock())

    first = await anext(reply)
    await reply.aclose()  # barge-in

    assert isinstance(first, SpeechDelta)
    await asyncio.wait_for(fake_llm.stream_closed_early.wait(), timeout=3)


async def test_empty_stream_ends_cleanly_with_missing_delimiter(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(Script(pieces=[]))

    events = [e async for e in stream_reply(client(fake_llm), MESSAGES, clock=SystemClock())]

    done = events[-1]
    assert isinstance(done, ReplyDone)
    assert done.first_token_s is None
    assert done.result.errors == ["missing_delimiter"]
    assert not any(isinstance(e, SpeechDelta) for e in events)


async def test_whitespace_does_not_count_as_the_first_token(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(Script(pieces=["\n", "Haan. ", DELIMITER, "{}"], piece_delay_s=0.4))

    with pytest.raises(FirstTokenTimeout):
        async for _ in stream_reply(
            client(fake_llm), MESSAGES, clock=SystemClock(), first_token_timeout_s=0.2
        ):
            pass
