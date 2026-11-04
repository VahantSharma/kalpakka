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
