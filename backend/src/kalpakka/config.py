"""Application settings: the only way configuration enters KalPakka.

Values come from environment variables, then the repo-root `.env` file if it exists (local runs
only; deployed hosts set real environment variables and have no `.env`). API keys are `SecretStr`
so they never appear in `repr`, logs or JSON dumps. Empty or whitespace-only values count as
missing: a required key fails, the optional Gnani key becomes `None`.

The LLM is reached through any OpenAI-compatible server; which server hosts Evon is a deployment
choice, so only its base URL, key and model name live here.
"""

import datetime as dt
from pathlib import Path
from typing import Annotated

from pydantic import SecretStr, StringConstraints, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/src/kalpakka/config.py -> parents[3] is the repo root (editable install, as uv syncs).
REPO_ROOT = Path(__file__).resolve().parents[3]

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def parse_live_hours(spec: str) -> tuple[tuple[dt.time, dt.time], ...]:
    """ "10:45-22:00, 23:00-23:30" -> ((10:45, 22:00), (23:00, 23:30)); empty -> no windows."""
    windows: list[tuple[dt.time, dt.time]] = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        try:
            start_s, end_s = part.split("-")
            start, end = (
                dt.time.fromisoformat(start_s.strip()),
                dt.time.fromisoformat(end_s.strip()),
            )
        except ValueError as exc:
            raise ValueError(f"live hours window {part!r} is not HH:MM-HH:MM") from exc
        if not start < end:
            raise ValueError(f"live hours window {part!r} ends before it starts")
        windows.append((start, end))
    return tuple(windows)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    llm_base_url: NonEmptyStr
    llm_api_key: SecretStr
    llm_model: NonEmptyStr
    gnani_api_key: SecretStr | None = None

    # --- calls (P2.3) ---
    # The simulated "today" of every call: a fixed story date, never the wall clock. 02: the EMI
    # bounced on the 5th and we meet Ramesh at 23 DPD -> 2026-10-28, before can_pay_rest_by
    # (2026-11-20), so a "₹4,000 now, the rest by the 20th" promise stays valid on any real date.
    sim_date: dt.date = dt.date(2026, 10, 28)
    max_concurrent_calls: int = 1  # until P0.4 measures what the account allows concurrently
    call_time_cap_s: float = 480.0  # 8 minutes; 04 caps public calls (tuned in P4.4)
    allowed_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    db_path: Path = REPO_ROOT / "data" / "kalpakka.db"
    # --- admission for the public (P4.4). Values are ours; the daily total must be recomputed
    # from measured credits per call (P0.4): credits_left / (credits_per_call * days_left).
    max_queue: int = 20
    avg_call_s: int = 240  # ETA estimate shown to queued callers; not a measurement
    calls_per_ip_per_day: int = 5
    calls_per_day: int = 200
    live_hours: str = "10:45-22:00"  # IST windows, comma-separated "HH:MM-HH:MM"
    live_always_open: bool = False  # local runs and staging set this true
    trust_forwarded_for: bool = False  # true only behind our own reverse proxy
    prerender_dir: Path = REPO_ROOT / "data" / "prerender"

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("live_hours")
    @classmethod
    def _valid_live_hours(cls, value: str) -> str:
        parse_live_hours(value)
        return value

    @field_validator("llm_api_key")
    @classmethod
    def _required_key_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("gnani_api_key")
    @classmethod
    def _blank_optional_key_is_missing(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            return None
        return value
