from pathlib import Path

import pytest
from pydantic import ValidationError

from kalpakka.config import Settings

LLM_ENV = {
    "LLM_BASE_URL": "http://127.0.0.1:8001/v1",
    "LLM_API_KEY": "test-llm-key-not-real",
    "LLM_MODEL": "evon-v3.3",
}


@pytest.fixture
def llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "GNANI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in LLM_ENV.items():
        monkeypatch.setenv(name, value)


def test_settings_reads_llm_settings_from_env(llm_env: None) -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_base_url == "http://127.0.0.1:8001/v1"
    assert settings.llm_api_key.get_secret_value() == "test-llm-key-not-real"
    assert settings.llm_model == "evon-v3.3"


@pytest.mark.parametrize("name", ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"])
def test_settings_rejects_missing_required_llm_setting(
    llm_env: None, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.delenv(name)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    assert excinfo.value.errors()[0]["loc"] == (name.lower(),)
    assert excinfo.value.errors()[0]["type"] == "missing"


@pytest.mark.parametrize("empty", ["", "   "])
def test_settings_rejects_empty_llm_model(
    llm_env: None, monkeypatch: pytest.MonkeyPatch, empty: str
) -> None:
    monkeypatch.setenv("LLM_MODEL", empty)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    assert excinfo.value.errors()[0]["loc"] == ("llm_model",)
    assert "llm_model" in str(excinfo.value)


def test_settings_allows_missing_gnani_key(llm_env: None) -> None:
    settings = Settings(_env_file=None)

    assert settings.gnani_api_key is None


def test_settings_reads_gnani_key_as_secret(llm_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GNANI_API_KEY", "test-gnani-key-not-real")

    settings = Settings(_env_file=None)

    assert settings.gnani_api_key is not None
    assert settings.gnani_api_key.get_secret_value() == "test-gnani-key-not-real"


def test_settings_repr_never_shows_api_keys(llm_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GNANI_API_KEY", "test-gnani-key-not-real")

    settings = Settings(_env_file=None)

    for rendered in (repr(settings), str(settings), settings.model_dump_json()):
        assert "test-llm-key-not-real" not in rendered
        assert "test-gnani-key-not-real" not in rendered


def test_settings_reads_dotenv_file_when_given_one(
    llm_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("LLM_MODEL")
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_MODEL=from-dotenv-file\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.llm_model == "from-dotenv-file"


@pytest.mark.parametrize("empty", ["", "   "])
def test_settings_rejects_empty_llm_api_key(
    llm_env: None, monkeypatch: pytest.MonkeyPatch, empty: str
) -> None:
    monkeypatch.setenv("LLM_API_KEY", empty)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    assert excinfo.value.errors()[0]["loc"] == ("llm_api_key",)


@pytest.mark.parametrize("empty", ["", "   "])
def test_settings_treats_empty_gnani_key_as_missing(
    llm_env: None, monkeypatch: pytest.MonkeyPatch, empty: str
) -> None:
    monkeypatch.setenv("GNANI_API_KEY", empty)

    settings = Settings(_env_file=None)

    assert settings.gnani_api_key is None


def test_default_env_file_is_the_repo_root_dotenv() -> None:
    from kalpakka.config import REPO_ROOT

    assert Settings.model_config.get("env_file") == REPO_ROOT / ".env"
    assert (REPO_ROOT / "Makefile").is_file()


# --- call settings (P2.3) ----------------------------------------------------------------------


def test_sim_date_is_a_fixed_story_date_not_the_wall_clock(llm_env: None) -> None:
    import datetime as dt

    settings = Settings(_env_file=None)
    # 02: the EMI bounced on the 5th; we meet Ramesh at 23 DPD -> 2026-10-28, before his
    # can_pay_rest_by of 2026-11-20.
    assert settings.sim_date == dt.date(2026, 10, 28)


def test_sim_date_can_be_overridden_from_env(
    llm_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import datetime as dt

    monkeypatch.setenv("SIM_DATE", "2026-11-02")
    assert Settings(_env_file=None).sim_date == dt.date(2026, 11, 2)


def test_call_defaults_are_single_call_with_caps(llm_env: None) -> None:
    settings = Settings(_env_file=None)
    assert settings.max_concurrent_calls == 1
    assert settings.call_time_cap_s == 480
    assert settings.allowed_origins == ["http://localhost:3000"]


def test_allowed_origins_read_as_a_comma_separated_list(
    llm_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://kalpakka.example, http://localhost:3000")
    assert Settings(_env_file=None).allowed_origins == [
        "https://kalpakka.example",
        "http://localhost:3000",
    ]


def test_live_hours_parse_into_ist_windows() -> None:
    import datetime as dt

    from kalpakka.config import parse_live_hours

    assert parse_live_hours("10:45-22:00, 23:00-23:30") == (
        (dt.time(10, 45), dt.time(22, 0)),
        (dt.time(23, 0), dt.time(23, 30)),
    )
    assert parse_live_hours("") == ()


@pytest.mark.parametrize("bad", ["10:45", "22:00-10:45", "ten-eleven"])
def test_bad_live_hours_are_rejected_at_startup(
    llm_env: None, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("LIVE_HOURS", bad)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
