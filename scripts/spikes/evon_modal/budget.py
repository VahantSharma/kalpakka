"""Cost meter and budget guard for the Modal spike (stdlib only; runs on the laptop, never in a
container).

Rates are Modal's published per-second prices, read from modal.com/pricing on 2026-11-05.
Every script estimates its worst case (rate x its own timeout) **before** starting a container
and refuses if the spike's running total would pass the budget (env SPIKE_BUDGET_USD, default
$10). After a run it records wall-clock seconds, which is an upper bound on billed time: Modal
bills only while a container is up. The Modal dashboard is the source of truth for the real cost.
"""

import datetime as dt
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

RATES_USD_PER_S: Final[dict[str, float]] = {
    "A100-80GB": 0.000694,
    "L40S": 0.000542,
    "cpu_core": 0.0000131,  # one physical core (2 vCPU)
    "mem_gib": 0.00000222,
}
DEFAULT_BUDGET_USD: Final = 10.0
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class BudgetExceeded(Exception):
    pass


@dataclass(frozen=True)
class Resources:
    gpu: str | None
    cpu_cores: float
    mem_gib: float


def cost_usd(res: Resources, seconds: float) -> float:
    if seconds < 0:
        raise ValueError("seconds must be >= 0")
    per_s = res.cpu_cores * RATES_USD_PER_S["cpu_core"] + res.mem_gib * RATES_USD_PER_S["mem_gib"]
    if res.gpu is not None:
        if res.gpu not in RATES_USD_PER_S or res.gpu in ("cpu_core", "mem_gib"):
            raise ValueError(f"no rate for GPU {res.gpu!r}")
        per_s += RATES_USD_PER_S[res.gpu]
    return per_s * seconds


def budget_from_env(env: Mapping[str, str]) -> float:
    raw = env.get("SPIKE_BUDGET_USD")
    if raw is None:
        return DEFAULT_BUDGET_USD
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"SPIKE_BUDGET_USD={raw!r} is not a number") from exc
    if value <= 0:
        raise ValueError("SPIKE_BUDGET_USD must be > 0")
    return value


class Ledger:
    """A JSON file of past runs (git-ignored, under scripts/spikes/out/)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = json.loads(self.path.read_text())["entries"]
        return entries

    def spent_usd(self) -> float:
        return float(sum(e["usd"] for e in self._entries()))

    def check(self, res: Resources, *, max_seconds: float, budget_usd: float) -> float:
        """Return the planned worst-case cost, or raise if it would pass the budget."""
        planned = cost_usd(res, max_seconds)
        spent = self.spent_usd()
        if spent + planned > budget_usd:
            raise BudgetExceeded(
                f"refusing to start: spent ${spent:.2f} + worst case ${planned:.2f} "
                f"> budget ${budget_usd:.2f} (raise SPIKE_BUDGET_USD only on purpose)"
            )
        return planned

    def _write(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"entries": entries}, indent=2) + "\n")

    def reserve(self, script: str, res: Resources, *, max_seconds: float) -> str:
        """Book the worst case before a run starts, so a parallel run (or a killed terminal
        that never reaches `finish`) still counts against the budget."""
        rid = f"{script}-{dt.datetime.now(IST):%Y%m%d%H%M%S%f}"
        entry = {
            "id": rid,
            "script": script,
            "status": "in_flight",
            "started_ist": dt.datetime.now(IST).isoformat(timespec="seconds"),
            "gpu": res.gpu,
            "usd": cost_usd(res, max_seconds),
        }
        self._write([*self._entries(), entry])
        return rid

    def finish(self, rid: str, res: Resources, *, seconds: float) -> dict[str, Any]:
        """Replace a reservation with the measured wall-clock cost."""
        entries = [e for e in self._entries() if e.get("id") != rid]
        entry = self._entry(rid.rsplit("-", 1)[0], res, seconds) | {"id": rid}
        self._write([*entries, entry])
        return entry

    @staticmethod
    def _entry(script: str, res: Resources, seconds: float) -> dict[str, Any]:
        return {
            "script": script,
            "status": "done",
            "finished_ist": dt.datetime.now(IST).isoformat(timespec="seconds"),
            "gpu": res.gpu,
            "seconds": round(seconds, 1),
            "gpu_seconds": round(seconds, 1) if res.gpu else 0,
            "usd": cost_usd(res, seconds),
        }

    def record(self, script: str, res: Resources, seconds: float) -> dict[str, Any]:
        entry = self._entry(script, res, seconds)
        self._write([*self._entries(), entry])
        return entry


def meter_line(
    script: str, res: Resources, *, seconds: float, spent_before: float, budget_usd: float
) -> str:
    run = cost_usd(res, seconds)
    gpu = f"GPU-seconds {seconds:.0f} ({res.gpu})" if res.gpu else "GPU-seconds 0 (CPU only)"
    return (
        f"[cost] {script}: {gpu} · this run ${run:.2f} · spike total "
        f"${spent_before + run:.2f} of ${budget_usd:.2f} (wall-clock upper bound; "
        "the Modal dashboard has the billed figure)"
    )
