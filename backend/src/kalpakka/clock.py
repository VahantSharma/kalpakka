"""Injected time. Logic never calls `time.time()` directly, so tests can freeze and advance it."""

import time
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()
