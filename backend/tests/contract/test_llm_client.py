import asyncio
import socket

import pytest

from kalpakka.llm import (
    ChatTemplateThinkingAdapter,
    LLMClient,
    LLMError,
    PlainAdapter,
    ServerAdapter,
)
from tests.contract.fakes.fake_openai import FakeOpenAI, Script

MESSAGES = [
    {"role": "system", "content": "You are Ramesh."},
    {"role": "user", "content": "Namaste Ramesh ji, EMI pending hai."},
]


def client(fake: FakeOpenAI, adapter: ServerAdapter | None = None) -> LLMClient:
    return LLMClient(
        base_url=fake.base_url,
        api_key="test-llm-key-not-real",
        model="evon-test",
        adapter=adapter or PlainAdapter(),
    )


async def collect(c: LLMClient, **kwargs: object) -> list[str]:
    return [piece async for piece in c.stream_chat(MESSAGES, max_tokens=160, **kwargs)]  # type: ignore[arg-type]


async def test_streams_content_pieces_in_order(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(Script(pieces=["Arre ", "sir, ", "kal pakka."]))

    assert await collect(client(fake_llm)) == ["Arre ", "sir, ", "kal pakka."]


async def test_request_carries_model_stream_messages_and_limits(fake_llm: FakeOpenAI) -> None:
    await collect(client(fake_llm), temperature=0.7)

    body = fake_llm.requests[0]
    assert body["model"] == "evon-test"
    assert body["stream"] is True
    assert body["max_tokens"] == 160
    assert body["temperature"] == 0.7
    assert body["messages"] == MESSAGES


async def test_api_key_is_sent_as_a_bearer_token(fake_llm: FakeOpenAI) -> None:
    await collect(client(fake_llm))

    assert fake_llm.headers[0]["authorization"] == "Bearer test-llm-key-not-real"


async def test_plain_adapter_sends_no_server_specific_fields(fake_llm: FakeOpenAI) -> None:
    await collect(client(fake_llm), thinking=False)

    assert "chat_template_kwargs" not in fake_llm.requests[0]


@pytest.mark.parametrize("thinking", [False, True])
async def test_chat_template_adapter_toggles_thinking(fake_llm: FakeOpenAI, thinking: bool) -> None:
    await collect(client(fake_llm, ChatTemplateThinkingAdapter()), thinking=thinking)

    assert fake_llm.requests[0]["chat_template_kwargs"] == {"enable_thinking": thinking}


async def test_rate_limit_is_a_retryable_error_and_is_not_retried_here(
    fake_llm: FakeOpenAI,
) -> None:
    fake_llm.scripts.append(Script(status=429))

    with pytest.raises(LLMError) as excinfo:
        await collect(client(fake_llm))

    assert excinfo.value.status == 429
    assert excinfo.value.retryable
    assert len(fake_llm.requests) == 1


async def test_auth_failure_is_not_retryable(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(Script(status=401))

    with pytest.raises(LLMError) as excinfo:
        await collect(client(fake_llm))

    assert excinfo.value.status == 401
    assert not excinfo.value.retryable


async def test_unreachable_server_is_a_retryable_error() -> None:
    with socket.socket() as s:  # find a free port and leave it closed
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    c = LLMClient(
        base_url=f"http://127.0.0.1:{port}/v1",
        api_key="k",
        model="m",
        adapter=PlainAdapter(),
        timeout_s=2,
    )

    with pytest.raises(LLMError) as excinfo:
        await collect(c)

    assert excinfo.value.status is None
    assert excinfo.value.retryable


async def test_closing_the_stream_early_closes_the_upstream_request(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(
        Script(pieces=["Haan ", "ji, ", "bolo ", "sir."] * 20, piece_delay_s=0.02)
    )
    stream = client(fake_llm).stream_chat(MESSAGES, max_tokens=160)

    first = await anext(stream)
    await stream.aclose()  # barge-in: stop generating

    assert first == "Haan "
    await asyncio.wait_for(fake_llm.stream_closed_early.wait(), timeout=3)
