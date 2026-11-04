from collections.abc import AsyncIterator

import pytest

from tests.contract.fakes.fake_openai import FakeOpenAI


@pytest.fixture
async def fake_llm() -> AsyncIterator[FakeOpenAI]:
    fake = FakeOpenAI()
    await fake.start()
    try:
        yield fake
    finally:
        await fake.stop()
