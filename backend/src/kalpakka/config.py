"""Application settings: the only way configuration enters KalPakka.

Values come from environment variables, then the repo-root `.env` file if it exists (local runs
only; deployed hosts set real environment variables and have no `.env`). API keys are `SecretStr`
so they never appear in `repr`, logs or JSON dumps. Empty or whitespace-only values count as
missing: a required key fails, the optional Gnani key becomes `None`.

The LLM is reached through any OpenAI-compatible server; which server hosts Evon is a deployment
choice, so only its base URL, key and model name live here.
"""

from pathlib import Path
from typing import Annotated

from pydantic import SecretStr, StringConstraints, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/kalpakka/config.py -> parents[3] is the repo root (editable install, as uv syncs).
REPO_ROOT = Path(__file__).resolve().parents[3]

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    llm_base_url: NonEmptyStr
    llm_api_key: SecretStr
    llm_model: NonEmptyStr
    gnani_api_key: SecretStr | None = None

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
