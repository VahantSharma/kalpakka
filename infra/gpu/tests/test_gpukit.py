import datetime as dt
from pathlib import Path

import pytest
from gpukit import (
    GGUF_FILE,
    PodConfig,
    can_start,
    deploy_input,
    docker_args,
    proxy_url,
    read_flags,
    redact,
    session_cost,
    status_line,
    terminate_after,
)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FLAGS = Path(__file__).resolve().parents[1] / "llama-server.flags"


def _cfg(**over: object) -> PodConfig:
    base = dict(
        template_id="tmpl123",
        network_volume_id="vol456",
        gguf_repo="vahant/evon-v3.3-gguf",
        gpu_type="NVIDIA RTX A6000",
        planned_rate_usd_h=0.59,
    )
    return PodConfig(**{**base, **over})  # type: ignore[arg-type]


# --- flags ---


def test_shared_flags_file_parses_without_comments_or_secrets() -> None:
    tokens = read_flags(FLAGS)
    assert tokens[:2] == ["--host", "0.0.0.0"]
    assert "--port" in tokens and tokens[tokens.index("--port") + 1] == "8080"
    assert "--jinja" in tokens
    assert not any(t.startswith("#") for t in tokens)
    assert not any("key" in t.lower() for t in tokens)
    assert "--reasoning-budget" not in tokens  # commented-out fallback stays off


def test_read_flags_handles_inline_comments(tmp_path: Path) -> None:
    f = tmp_path / "x.flags"
    f.write_text("# header\n--parallel 8   # slots\n\n--metrics\n")
    assert read_flags(f) == ["--parallel", "8", "--metrics"]


def test_docker_args_point_llama_server_at_the_private_repo_file() -> None:
    args = docker_args("vahant/evon-v3.3-gguf", GGUF_FILE["Q8_0"], ["--port", "8080"])
    assert args == "-hf vahant/evon-v3.3-gguf -hff evon-v3.3-Q8_0.gguf --port 8080"


# --- deploy input ---


def test_deploy_input_keeps_secrets_in_env_not_docker_args() -> None:
    inp = deploy_input(
        _cfg(), flags=["--port", "8080"], quant="Q8_0", api_key="sk-live-abc",
        hf_token="hf_secret", terminate_at="2026-11-09T17:45:00Z",
    )  # fmt: skip
    assert "sk-live-abc" not in inp["dockerArgs"] and "hf_secret" not in inp["dockerArgs"]
    env = {e["key"]: e["value"] for e in inp["env"]}
    assert env["LLAMA_API_KEY"] == "sk-live-abc"
    assert env["HF_TOKEN"] == "hf_secret"
    assert env["LLAMA_CACHE"].startswith("/workspace/")
    assert inp["templateId"] == "tmpl123"
    assert inp["networkVolumeId"] == "vol456"
    assert inp["gpuTypeId"] == "NVIDIA RTX A6000"
    assert inp["gpuCount"] == 1
    assert inp["cloudType"] == "SECURE"  # network volumes need Secure Cloud
    assert inp["terminateAfter"] == "2026-11-09T17:45:00Z"
    assert inp["name"] == "kalpakka-evon"


def test_deploy_input_rejects_unknown_quant() -> None:
    with pytest.raises(ValueError, match="Q2_K"):
        deploy_input(_cfg(), flags=[], quant="Q2_K", api_key="k", hf_token="t",
                     terminate_at="2026-11-09T17:45:00Z")  # fmt: skip


def test_proxy_url() -> None:
    assert proxy_url("abc123", 8080) == "https://abc123-8080.proxy.runpod.net"


# --- money ---


def test_refuses_when_balance_does_not_cover_one_hour() -> None:
    ok, hours = can_start(balance_usd=0.50, rate_usd_h=0.59, other_spend_usd_h=0.0)
    assert ok is False
    assert hours == pytest.approx(0.50 / 0.59)


def test_other_running_spend_counts_against_the_hour() -> None:
    ok, _ = can_start(balance_usd=1.00, rate_usd_h=0.59, other_spend_usd_h=0.50)
    assert ok is False


def test_allows_when_balance_covers_one_hour() -> None:
    ok, hours = can_start(balance_usd=17.0, rate_usd_h=0.59, other_spend_usd_h=0.0)
    assert ok is True
    assert hours == pytest.approx(28.81, abs=0.01)


def test_unknown_balance_refuses() -> None:
    ok, hours = can_start(balance_usd=None, rate_usd_h=0.59, other_spend_usd_h=0.0)
    assert ok is False and hours is None


def test_session_cost() -> None:
    assert session_cost(uptime_s=3600 * 12.25, cost_per_hr=0.59) == pytest.approx(7.2275)


# --- dead-man switch ---


def test_terminate_after_is_stop_time_plus_grace_in_utc() -> None:
    now = dt.datetime(2026, 11, 9, 10, 45, tzinfo=IST)
    assert terminate_after(now, "23:00", grace_min=15) == "2026-11-09T17:45:00Z"


def test_terminate_after_in_the_past_is_an_error() -> None:
    now = dt.datetime(2026, 11, 9, 23, 30, tzinfo=IST)
    with pytest.raises(ValueError, match="already past"):
        terminate_after(now, "23:00", grace_min=15)


def test_terminate_after_rejects_bad_time() -> None:
    with pytest.raises(ValueError):
        terminate_after(dt.datetime(2026, 11, 9, 10, 0, tzinfo=IST), "25:00", grace_min=0)


# --- status ---


def test_status_line_reports_spend_and_hours_left() -> None:
    line = status_line(
        balance_usd=10.0, spend_usd_h=0.59, prepaid_usd=17.0, pod_uptime_s=7200, pod_rate=0.59
    )
    assert "balance $10.00" in line
    assert "spent since prepaid $7.00 of $17.00" in line
    assert "this pod 2.00 h ≈ $1.18" in line
    assert "≈ 16.9 h left at $0.59/h" in line


def test_status_line_without_pod_or_prepaid() -> None:
    line = status_line(
        balance_usd=5.0, spend_usd_h=0.0, prepaid_usd=None, pod_uptime_s=None, pod_rate=None
    )
    assert "no pod running" in line
    assert "spent since prepaid" not in line


def test_redact_hides_every_secret_value_in_an_api_message() -> None:
    msg = (
        'Variable "$input" got invalid value { env: [{ key: "LLAMA_API_KEY", '
        'value: "sk-live-abc" }, { key: "HF_TOKEN", value: "hf_secret" }] }'
    )
    out = redact(msg, ["sk-live-abc", "hf_secret", ""])
    assert "sk-live-abc" not in out and "hf_secret" not in out
    assert out.count("***") == 2
