"""Admission for the public: may this caller start a call, and when? (04 NFR-5/6/7, 05, 08)

Order of checks for each caller: live hours → daily caps (whole app, then per IP) → a free slot
now, or a place in a FIFO queue with its position and an ETA. The ETA is an estimate:
position * average call length / slots, never presented as a measurement. A caller counts against
the daily caps only once admitted, so leaving the queue costs nothing. Each admitted call holds
a `Lease`; releasing it (once) wakes the head of the queue. Days are IST calendar days.

Wall-clock time is injected (`wall`). Nothing here sleeps or polls.
"""

import asyncio
import datetime as dt
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Final, Literal

IST: Final = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")

RefusalReason = Literal["outside_live_hours", "daily_cap", "ip_daily_cap", "queue_full"]
Wall = Callable[[], dt.datetime]
PositionCallback = Callable[[int, int], Awaitable[None]]  # (position, eta_s)
POSITION_SEND_TIMEOUT_S: Final = 2.0  # a socket that stops reading must not stall the line


@dataclass(frozen=True)
class Refusal:
    reason: RefusalReason


class LiveHours:
    """Announced live windows in IST, e.g. ((10:45, 22:00),). `always_open` for local/staging."""

    def __init__(
        self,
        *,
        windows: tuple[tuple[dt.time, dt.time], ...],
        wall: Wall,
        always_open: bool = False,
    ) -> None:
        self._windows = windows
        self._wall = wall
        self._always_open = always_open

    def is_open(self) -> bool:
        if self._always_open:
            return True
        now = self._wall().astimezone(IST).time()
        return any(start <= now < end for start, end in self._windows)


class DailyCaps:
    """Calls per IST day: for the whole app (credits) and per IP (fairness)."""

    def __init__(self, *, per_ip: int, total: int, wall: Wall) -> None:
        self._per_ip = per_ip
        self._total = total
        self._wall = wall
        self._day: dt.date | None = None
        self._calls = 0
        self._by_ip: dict[str, int] = defaultdict(int)

    def _roll(self) -> None:
        today = self._wall().astimezone(IST).date()
        if today != self._day:
            self._day, self._calls = today, 0
            self._by_ip.clear()

    def check(self, ip: str, *, queued_from_ip: int = 0) -> RefusalReason | None:
        """`queued_from_ip`: places this IP already holds in the queue (they count too)."""
        self._roll()
        if self._calls >= self._total:
            return "daily_cap"
        if self._by_ip[ip] + queued_from_ip >= self._per_ip:
            return "ip_daily_cap"
        return None

    def count(self, ip: str) -> None:
        self._roll()
        self._calls += 1
        self._by_ip[ip] += 1

    @property
    def calls_today(self) -> int:
        self._roll()
        return self._calls


class Lease:
    def __init__(self, admission: "Admission") -> None:
        self._admission = admission
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._admission._release()

    def __enter__(self) -> "Lease":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


@dataclass
class _Waiter:
    ip: str
    on_position: PositionCallback
    granted: asyncio.Future[None]


class Admission:
    def __init__(
        self,
        *,
        max_concurrent: int,
        max_queue: int,
        avg_call_s: int,
        caps: DailyCaps,
        hours: LiveHours,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        self._max = max_concurrent
        self._max_queue = max_queue
        self._avg_call_s = avg_call_s
        self.caps = caps
        self.hours = hours
        self.active = 0
        self._queue: list[_Waiter] = []
        self._announcers: set[asyncio.Task[None]] = set()

    @property
    def queued(self) -> int:
        return len(self._queue)

    def eta_s(self, position: int) -> int:
        return round(position * self._avg_call_s / self._max)

    async def enter(self, ip: str, *, on_position: PositionCallback) -> Lease | Refusal:
        """Admit now, or wait in line (calling `on_position` whenever the place changes).
        Cancel the awaiting task to leave the queue."""
        if not self.hours.is_open():
            return Refusal(reason="outside_live_hours")
        queued_here = sum(1 for w in self._queue if w.ip == ip)
        if (reason := self.caps.check(ip, queued_from_ip=queued_here)) is not None:
            return Refusal(reason=reason)
        if self.active < self._max and not self._queue:
            return self._grant(ip)
        if len(self._queue) >= self._max_queue:
            return Refusal(reason="queue_full")
        waiter = _Waiter(ip=ip, on_position=on_position, granted=asyncio.Future())
        self._queue.append(waiter)
        try:
            await self._tell(waiter)
            await waiter.granted
        except BaseException:
            if waiter in self._queue:
                self._queue.remove(waiter)
                self._announce_soon()
            elif waiter.granted.done() and not waiter.granted.cancelled():
                self._release()  # granted just as the caller left: give the slot back
            raise
        if not self.hours.is_open():  # live hours ended while waiting
            self._release()
            return Refusal(reason="outside_live_hours")
        if (reason := self.caps.check(ip)) is not None:  # caps filled while waiting
            self._release()
            return Refusal(reason=reason)
        self.caps.count(ip)
        return Lease(self)

    def _grant(self, ip: str) -> Lease:
        self.active += 1
        self.caps.count(ip)
        return Lease(self)

    def _release(self) -> None:
        self.active -= 1
        while self._queue and self.active < self._max:
            head = self._queue.pop(0)
            if head.granted.done():  # cancelled (caller left) but not yet out of the queue
                continue
            self.active += 1
            head.granted.set_result(None)
        self._announce_soon()

    def _announce_soon(self) -> None:
        """Tell every waiter its place, concurrently and each with a timeout."""
        for waiter in list(self._queue):
            task = asyncio.get_running_loop().create_task(self._tell(waiter))
            self._announcers.add(task)  # strong reference until done
            task.add_done_callback(self._announcers.discard)

    async def _tell(self, waiter: _Waiter) -> None:
        if waiter not in self._queue:
            return
        position = self._queue.index(waiter) + 1  # computed at send time, never stale
        try:
            await asyncio.wait_for(
                waiter.on_position(position, self.eta_s(position)), POSITION_SEND_TIMEOUT_S
            )
        except Exception:  # a dead or stuck socket must not stop anyone else
            return
