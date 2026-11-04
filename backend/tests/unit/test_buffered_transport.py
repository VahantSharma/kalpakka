"""The transport used while a caller is queued (review P4.4 #2: bounded buffering)."""

import asyncio

from kalpakka.server import BUFFER_LIMIT, BufferedTransport
from kalpakka.session import TransportClosed


class Inner:
    def __init__(self, frames: list[str | bytes]) -> None:
        self.frames = frames

    async def receive(self) -> str | bytes:
        if not self.frames:
            await asyncio.sleep(3600)
        return self.frames.pop(0)

    async def send_text(self, data: str) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...


async def test_early_start_call_is_kept_for_the_session() -> None:
    transport = BufferedTransport(Inner(['{"type":"start_call"}']))  # type: ignore[arg-type]
    watch = asyncio.create_task(transport.watch())
    await asyncio.sleep(0)
    watch.cancel()
    assert await transport.receive() == '{"type":"start_call"}'


async def test_a_flood_of_text_frames_while_queued_closes_the_caller() -> None:
    flood: list[str | bytes] = ["x" * 1000] * (BUFFER_LIMIT + 10)
    transport = BufferedTransport(Inner(flood))  # type: ignore[arg-type]
    await asyncio.wait_for(transport.watch(), 1)  # returns: the caller is treated as gone
    received = 0
    try:
        while True:
            await transport.receive()
            received += 1
    except TransportClosed:
        pass
    assert received <= BUFFER_LIMIT
