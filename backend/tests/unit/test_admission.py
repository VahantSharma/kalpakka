"""Admission for the public (04 NFR-5/6/7): queue, positions, daily and per-IP caps, hours."""

import asyncio
import datetime as dt

import pytest

from kalpakka.admission import IST, Admission, DailyCaps, Lease, LiveHours, Refusal


class Wall:
    def __init__(self, now: dt.datetime) -> None:
        self.now = now

    def __call__(self) -> dt.datetime:
        return self.now


def _ist(day: int, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 11, day, hour, minute, tzinfo=IST)


def _admission(max_concurrent: int = 1, max_queue: int = 3, **caps: int) -> Admission:
    wall = Wall(_ist(9, 12))
    return Admission(
        max_concurrent=max_concurrent,
        max_queue=max_queue,
        avg_call_s=240,
        caps=DailyCaps(per_ip=caps.get("per_ip", 5), total=caps.get("total", 100), wall=wall),
        hours=LiveHours(windows=((dt.time(10, 45), dt.time(22, 0)),), wall=wall),
    )


async def _no_updates(position: int, eta_s: int) -> None:
    return None


# --- concurrency + queue ---------------------------------------------------------------------


async def test_a_free_slot_admits_at_once() -> None:
    admission = _admission()
    lease = await admission.enter("1.2.3.4", on_position=_no_updates)
    assert isinstance(lease, Lease)
    assert admission.active == 1


async def test_second_caller_waits_in_line_with_position_and_eta() -> None:
    admission = _admission()
    first = await admission.enter("1.1.1.1", on_position=_no_updates)
    assert isinstance(first, Lease)
    updates: list[tuple[int, int]] = []

    async def record(position: int, eta_s: int) -> None:
        updates.append((position, eta_s))

    waiting = asyncio.create_task(admission.enter("2.2.2.2", on_position=record))
    await asyncio.sleep(0)
    assert updates == [(1, 240)]
    assert not waiting.done()
    first.release()
    second = await asyncio.wait_for(waiting, 1)
    assert isinstance(second, Lease)


async def test_positions_move_up_as_the_line_moves() -> None:
    admission = _admission(max_queue=5)
    first = await admission.enter("1.1.1.1", on_position=_no_updates)
    assert isinstance(first, Lease)
    seen: dict[str, list[int]] = {"b": [], "c": []}

    def recorder(name: str):
        async def record(position: int, eta_s: int) -> None:
            seen[name].append(position)

        return record

    b = asyncio.create_task(admission.enter("2.2.2.2", on_position=recorder("b")))
    await asyncio.sleep(0)
    c = asyncio.create_task(admission.enter("3.3.3.3", on_position=recorder("c")))
    await asyncio.sleep(0)
    assert (seen["b"], seen["c"]) == ([1], [2])
    first.release()
    lease_b = await asyncio.wait_for(b, 1)
    await asyncio.sleep(0)
    assert seen["c"] == [2, 1]
    assert isinstance(lease_b, Lease)
    lease_b.release()
    assert isinstance(await asyncio.wait_for(c, 1), Lease)


async def test_a_full_queue_refuses() -> None:
    admission = _admission(max_queue=1)
    await admission.enter("1.1.1.1", on_position=_no_updates)
    waiting = asyncio.create_task(admission.enter("2.2.2.2", on_position=_no_updates))
    await asyncio.sleep(0)
    refused = await admission.enter("3.3.3.3", on_position=_no_updates)
    assert refused == Refusal(reason="queue_full")
    waiting.cancel()


async def test_leaving_the_queue_frees_the_place_for_those_behind() -> None:
    admission = _admission(max_queue=5)
    first = await admission.enter("1.1.1.1", on_position=_no_updates)
    assert isinstance(first, Lease)
    positions: list[int] = []

    async def record(position: int, eta_s: int) -> None:
        positions.append(position)

    leaver = asyncio.create_task(admission.enter("2.2.2.2", on_position=_no_updates))
    await asyncio.sleep(0)
    stayer = asyncio.create_task(admission.enter("3.3.3.3", on_position=record))
    await asyncio.sleep(0)
    leaver.cancel()  # closed the tab while waiting
    await asyncio.sleep(0.01)  # position updates go out as background sends
    assert positions == [2, 1]
    assert admission.queued == 1
    first.release()
    assert isinstance(await asyncio.wait_for(stayer, 1), Lease)


async def test_a_lease_releases_once() -> None:
    admission = _admission()
    lease = await admission.enter("1.1.1.1", on_position=_no_updates)
    assert isinstance(lease, Lease)
    lease.release()
    lease.release()
    assert admission.active == 0


# --- caps ------------------------------------------------------------------------------------


async def test_per_ip_daily_cap_refuses_the_sixth_call() -> None:
    admission = _admission(per_ip=5, max_concurrent=10)
    for _ in range(5):
        lease = await admission.enter("9.9.9.9", on_position=_no_updates)
        assert isinstance(lease, Lease)
        lease.release()
    assert await admission.enter("9.9.9.9", on_position=_no_updates) == Refusal(
        reason="ip_daily_cap"
    )
    assert isinstance(await admission.enter("8.8.8.8", on_position=_no_updates), Lease)


async def test_total_daily_cap_protects_the_credits() -> None:
    admission = _admission(total=2, max_concurrent=10)
    for ip in ("1.1.1.1", "2.2.2.2"):
        assert isinstance(await admission.enter(ip, on_position=_no_updates), Lease)
    assert await admission.enter("3.3.3.3", on_position=_no_updates) == Refusal(reason="daily_cap")


def test_caps_reset_on_the_next_ist_day() -> None:
    wall = Wall(_ist(9, 23, 50))
    caps = DailyCaps(per_ip=1, total=10, wall=wall)
    assert caps.check("1.1.1.1") is None
    caps.count("1.1.1.1")
    assert caps.check("1.1.1.1") == "ip_daily_cap"
    wall.now = _ist(10, 0, 5)  # just after midnight IST
    assert caps.check("1.1.1.1") is None


async def test_queued_caller_is_counted_only_when_admitted() -> None:
    admission = _admission(per_ip=1, max_queue=3)
    first = await admission.enter("1.1.1.1", on_position=_no_updates)
    assert isinstance(first, Lease)
    leaver = asyncio.create_task(admission.enter("2.2.2.2", on_position=_no_updates))
    await asyncio.sleep(0)
    leaver.cancel()
    await asyncio.sleep(0)
    first.release()
    assert isinstance(await admission.enter("2.2.2.2", on_position=_no_updates), Lease)


# --- live hours ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("when", "open_"),
    [
        (_ist(9, 10, 44), False),
        (_ist(9, 10, 45), True),
        (_ist(9, 21, 59), True),
        (_ist(9, 22, 0), False),
    ],
)
def test_live_hours_window_in_ist(when: dt.datetime, open_: bool) -> None:
    assert (
        LiveHours(windows=((dt.time(10, 45), dt.time(22, 0)),), wall=Wall(when)).is_open() is open_
    )


def test_live_hours_compare_in_ist_even_if_the_clock_is_utc() -> None:
    utc_noon = dt.datetime(2026, 11, 9, 6, 30, tzinfo=dt.UTC)  # 12:00 IST
    assert LiveHours(windows=((dt.time(10, 45), dt.time(22, 0)),), wall=Wall(utc_noon)).is_open()


def test_always_open_override_for_staging() -> None:
    hours = LiveHours(windows=(), wall=Wall(_ist(9, 3)), always_open=True)
    assert hours.is_open()


async def test_outside_live_hours_refuses_before_anything_else() -> None:
    wall = Wall(_ist(9, 8, 0))
    admission = Admission(
        max_concurrent=1,
        max_queue=3,
        avg_call_s=240,
        caps=DailyCaps(per_ip=5, total=100, wall=wall),
        hours=LiveHours(windows=((dt.time(10, 45), dt.time(22, 0)),), wall=wall),
    )
    assert await admission.enter("1.1.1.1", on_position=_no_updates) == Refusal(
        reason="outside_live_hours"
    )


# --- review P4.4 --------------------------------------------------------------------------------


async def test_hang_up_racing_a_release_does_not_leak_the_slot() -> None:
    """Review P4.4 #1: cancel the queued caller and release in the same loop turn."""
    admission = _admission(max_queue=3)
    first = await admission.enter("1.1.1.1", on_position=_no_updates)
    assert isinstance(first, Lease)
    queued = asyncio.create_task(admission.enter("2.2.2.2", on_position=_no_updates))
    await asyncio.sleep(0)
    queued.cancel()
    first.release()  # must not raise, must not keep a phantom slot
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert admission.active == 0
    assert isinstance(await admission.enter("3.3.3.3", on_position=_no_updates), Lease)


async def test_one_ip_cannot_fill_the_queue() -> None:
    """Review P4.4 #3: queued places count against the per-IP cap too."""
    admission = _admission(per_ip=2, max_queue=10)
    first = await admission.enter("6.6.6.6", on_position=_no_updates)
    assert isinstance(first, Lease)
    waiting = asyncio.create_task(admission.enter("6.6.6.6", on_position=_no_updates))
    await asyncio.sleep(0)
    assert await admission.enter("6.6.6.6", on_position=_no_updates) == Refusal(
        reason="ip_daily_cap"
    )
    waiting.cancel()


async def test_a_caller_still_queued_when_live_hours_end_is_refused() -> None:
    """Review P4.4 #6."""
    admission = _admission()
    first = await admission.enter("1.1.1.1", on_position=_no_updates)
    assert isinstance(first, Lease)
    waiting = asyncio.create_task(admission.enter("2.2.2.2", on_position=_no_updates))
    await asyncio.sleep(0)
    admission.hours = LiveHours(windows=(), wall=Wall(_ist(9, 22, 10)))  # 22:10, closed
    first.release()
    assert await asyncio.wait_for(waiting, 1) == Refusal(reason="outside_live_hours")
    assert admission.active == 0


async def test_a_stuck_socket_does_not_stall_position_updates() -> None:
    """Review P4.4 #5."""
    import kalpakka.admission as admission_module

    admission = _admission(max_queue=5)
    first = await admission.enter("1.1.1.1", on_position=_no_updates)
    assert isinstance(first, Lease)
    seen: list[int] = []

    async def stuck(position: int, eta_s: int) -> None:
        if position == 1:  # after the first update this client stops reading
            await asyncio.sleep(3600)

    async def record(position: int, eta_s: int) -> None:
        seen.append(position)

    original = admission_module.POSITION_SEND_TIMEOUT_S
    admission_module.POSITION_SEND_TIMEOUT_S = 0.05
    try:
        a = asyncio.create_task(admission.enter("2.2.2.2", on_position=stuck))
        await asyncio.sleep(0.1)
        b = asyncio.create_task(admission.enter("3.3.3.3", on_position=record))
        await asyncio.sleep(0.1)
        c = asyncio.create_task(admission.enter("4.4.4.4", on_position=_no_updates))
        await asyncio.sleep(0.1)
        c.cancel()  # the line behind b changes; b's update must still arrive
        await asyncio.sleep(0.2)
    finally:
        admission_module.POSITION_SEND_TIMEOUT_S = original
    assert seen[0] == 2
    a.cancel()
    b.cancel()
