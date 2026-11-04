"""Latency instrumentation (context doc 08 "Latency budget").

Ten timestamps per turn, all relative to `ptt_end` (the trainee released talk). Stages are
differences between marks; a stage whose marks were not both taken is left out, never zero.
Percentiles use the nearest-rank method on the most recent turns (`LatencyStore`).

`browser_first_play` has no message of its own in the 05 protocol. It is derived from the
browser's `played` report for sentence 0, minus that sentence's audio duration.
"""

import math
from collections import deque
from collections.abc import Sequence
from typing import Any, Final

from kalpakka.clock import Clock

MARKS: Final = (
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
)

# stage name -> (from mark, to mark)
STAGES: Final[dict[str, tuple[str, str]]] = {
    "stt_commit_to_final": ("commit_sent", "transcript_final"),
    "brief_build": ("transcript_final", "brief_built"),
    "llm_ttft": ("llm_request", "llm_first_token"),
    "first_sentence": ("llm_first_token", "first_sentence_ready"),
    "tts_first_audio": ("tts_request", "tts_first_audio"),
    "server_total": ("ptt_end", "tts_first_audio"),
    "total": ("ptt_end", "browser_first_play"),
}


class TurnTimer:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._origin: float | None = None
        self._marks: dict[str, float] = {}

    def mark(self, name: str) -> None:
        if name not in MARKS:
            raise ValueError(f"unknown latency mark {name!r}")
        if name in self._marks:
            return  # the first occurrence is the one that matters for latency
        now = self._clock.monotonic()
        if self._origin is None:
            self._origin = now
        self._marks[name] = now - self._origin

    def mark_at(self, name: str, seconds_after_origin: float) -> None:
        """A mark computed elsewhere (e.g. `browser_first_play` from a `played` report)."""
        if name not in MARKS:
            raise ValueError(f"unknown latency mark {name!r}")
        self._marks.setdefault(name, seconds_after_origin)

    def since_origin(self) -> float | None:
        return None if self._origin is None else self._clock.monotonic() - self._origin

    def marks(self) -> dict[str, float]:
        return {name: round(value, 4) for name, value in self._marks.items()}

    @staticmethod
    def stages_from(marks: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for stage, (start, end) in STAGES.items():
            if start in marks and end in marks:
                out[stage] = round(marks[end] - marks[start], 4)
        return out


def percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


class LatencyStore:
    """Stage durations of the most recent turns, for `GET /metrics/latency`."""

    def __init__(self, capacity: int = 500) -> None:
        self._turns: deque[dict[str, float]] = deque(maxlen=capacity)

    def add(self, stages: dict[str, float]) -> None:
        if stages:
            self._turns.append(dict(stages))

    def report(self) -> dict[str, Any]:  # Any: JSON-safe nested numbers
        stages: dict[str, dict[str, float | int | None]] = {}
        for stage in STAGES:
            values = [turn[stage] for turn in self._turns if stage in turn]
            if values:
                stages[stage] = {
                    "n": len(values),
                    "p50": percentile(values, 50),
                    "p95": percentile(values, 95),
                }
        return {"turns": len(self._turns), "stages": stages}
