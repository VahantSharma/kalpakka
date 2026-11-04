from collections.abc import AsyncIterator

import pytest

from tests.contract.fakes.fake_openai import FakeOpenAI
from tests.contract.fakes.fake_prisma import FakePrisma


@pytest.fixture
async def fake_llm() -> AsyncIterator[FakeOpenAI]:
    fake = FakeOpenAI()
    await fake.start()
    try:
        yield fake
    finally:
        await fake.stop()


@pytest.fixture
async def fake_prisma() -> AsyncIterator[FakePrisma]:
    fake = FakePrisma(transcripts=["Ramesh ji, ₹12,500 overdue hai", "हाँ, ₹4,000 abhi chalega"])
    await fake.start()
    try:
        yield fake
    finally:
        await fake.stop()
