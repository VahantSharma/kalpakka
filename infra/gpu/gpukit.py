"""Pure helpers for the RunPod live-hours scripts (stdlib only, unit-tested).

Money rules (ADR-001): refuse to start unless the prepaid balance covers one more hour at the
planned rate on top of anything else already running. Every pod is created with a server-side
`terminateAfter` (stop time + grace), so a crashed laptop cannot leave the GPU billing overnight.
Secrets (the Evon API key, the HF token) travel only as pod environment variables, never in
`dockerArgs`, which RunPod shows in its console.
"""

import datetime as dt
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

POD_NAME: Final = "kalpakka-evon"
PORT: Final = 8080
CACHE_DIR: Final = "/workspace/llama-cache"  # on the network volume: download once, reuse
GGUF_FILE: Final[dict[str, str]] = {
    "Q8_0": "evon-v3.3-Q8_0.gguf",
    "Q4_K_M": "evon-v3.3-Q4_K_M.gguf",
}
IST: Final = dt.timezone(dt.timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class PodConfig:
    template_id: str
    network_volume_id: str
    gguf_repo: str
    gpu_type: str = "NVIDIA RTX A6000"
    planned_rate_usd_h: float = 0.59


def read_flags(path: Path) -> list[str]:
    """One flag (and value) per line; '#' starts a comment. Same format serve_q8.py reads."""
    tokens: list[str] = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            tokens += shlex.split(line)
    return tokens


def docker_args(repo: str, gguf_file: str, flags: list[str]) -> str:
    return shlex.join(["-hf", repo, "-hff", gguf_file, *flags])


def deploy_input(
    cfg: PodConfig,
    *,
    flags: list[str],
    quant: str,
    api_key: str,
    hf_token: str,
    terminate_at: str,
) -> dict[str, Any]:
    if quant not in GGUF_FILE:
        raise ValueError(f"unknown quant {quant!r}; one of {', '.join(GGUF_FILE)}")
    return {
        "name": POD_NAME,
        "cloudType": "SECURE",
        "gpuCount": 1,
        "gpuTypeId": cfg.gpu_type,
        "templateId": cfg.template_id,
        "networkVolumeId": cfg.network_volume_id,
        "dockerArgs": docker_args(cfg.gguf_repo, GGUF_FILE[quant], flags),
        "env": [
            {"key": "LLAMA_API_KEY", "value": api_key},
            {"key": "HF_TOKEN", "value": hf_token},
            {"key": "LLAMA_CACHE", "value": CACHE_DIR},
        ],
        "terminateAfter": terminate_at,
    }


def redact(text: str, secrets: list[str]) -> str:
    """Replace every non-empty secret value in an API message before it reaches a terminal."""
    for value in secrets:
        if value:
            text = text.replace(value, "***")
    return text


def proxy_url(pod_id: str, port: int = PORT) -> str:
    return f"https://{pod_id}-{port}.proxy.runpod.net"


def can_start(
    *, balance_usd: float | None, rate_usd_h: float, other_spend_usd_h: float
) -> tuple[bool, float | None]:
    """(ok, hours the balance lasts at rate + other spend). Unknown balance never starts."""
    if balance_usd is None:
        return False, None
    burn = rate_usd_h + max(0.0, other_spend_usd_h)
    hours = balance_usd / burn
    return hours >= 1.0, hours


def session_cost(*, uptime_s: float, cost_per_hr: float) -> float:
    return uptime_s / 3600 * cost_per_hr


def terminate_after(now: dt.datetime, stop_hhmm: str, *, grace_min: int) -> str:
    """Today's IST stop time + grace, as a UTC ISO string for RunPod's `terminateAfter`."""
    hh, mm = (int(x) for x in stop_hhmm.split(":"))
    stop_t = dt.time(hh, mm)  # raises ValueError on 25:00
    local = now.astimezone(IST)
    stop = dt.datetime.combine(local.date(), stop_t, tzinfo=IST) + dt.timedelta(minutes=grace_min)
    if stop <= local:
        raise ValueError(f"stop time {stop_hhmm} IST (+{grace_min} min) is already past")
    return stop.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def status_line(
    *,
    balance_usd: float | None,
    spend_usd_h: float,
    prepaid_usd: float | None,
    pod_uptime_s: float | None,
    pod_rate: float | None,
) -> str:
    parts = [
        "[cost] " + ("balance unknown" if balance_usd is None else f"balance ${balance_usd:.2f}")
    ]
    if prepaid_usd is not None and balance_usd is not None:
        parts.append(f"spent since prepaid ${prepaid_usd - balance_usd:.2f} of ${prepaid_usd:.2f}")
    if pod_uptime_s is None or pod_rate is None:
        parts.append("no pod running")
    else:
        hours = pod_uptime_s / 3600
        cost = session_cost(uptime_s=pod_uptime_s, cost_per_hr=pod_rate)
        parts.append(f"this pod {hours:.2f} h ≈ ${cost:.2f}")
    if balance_usd is not None and spend_usd_h > 0:
        parts.append(f"≈ {balance_usd / spend_usd_h:.1f} h left at ${spend_usd_h:.2f}/h")
    return " · ".join(parts)
