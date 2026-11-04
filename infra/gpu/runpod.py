# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Live-hours GPU control for Evon on one RunPod RTX A6000 (P5.3). Stdlib only.

    uv run --script infra/gpu/runpod.py start [--until 23:00] [--quant Q8_0] [--no-warmup]
    uv run --script infra/gpu/runpod.py status
    uv run --script infra/gpu/runpod.py warmup
    uv run --script infra/gpu/runpod.py stop

start: refuses unless the prepaid balance covers one hour at the planned rate (plus anything else
running) → creates pod `kalpakka-evon` from the template + network volume, with a server-side
terminateAfter (= --until + 15 min, IST) → waits for the RunPod proxy and llama-server /health
→ warm-up (3 real Ramesh turns via the backend's own prompt code) → prints the base URL.
stop: terminates the pod (the network volume, and so the downloaded GGUF, is kept) and prints
what this session cost. Pods are terminated rather than stopped: a stopped pod may not get its
GPU back.

Env (names only; values never printed): RUNPOD_API_KEY, RUNPOD_TEMPLATE_ID,
RUNPOD_NETWORK_VOLUME_ID, HF_GGUF_REPO, HF_TOKEN (read-only token is enough on the pod),
EVON_API_KEY. Optional: RUNPOD_GPU_TYPE (default "NVIDIA RTX A6000"), RUNPOD_RATE_USD_H
(planned rate, default 0.59), RUNPOD_PREPAID_USD (the prepaid cap, default 17).
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from gpukit import (
    IST,
    POD_NAME,
    PodConfig,
    can_start,
    deploy_input,
    proxy_url,
    read_flags,
    redact,
    session_cost,
    status_line,
    terminate_after,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GRAPHQL = os.environ.get("RUNPOD_GRAPHQL_URL", "https://api.runpod.io/graphql")  # tests only
LEDGER = ROOT / "scripts" / "spikes" / "out" / "gpu-ledger.json"  # git-ignored

Q_MYSELF = """query { myself { clientBalance currentSpendPerHr
  pods { id name desiredStatus costPerHr runtime { uptimeInSeconds } } } }"""
M_DEPLOY = """mutation Deploy($input: PodFindAndDeployOnDemandInput) {
  podFindAndDeployOnDemand(input: $input) { id costPerHr desiredStatus machineId } }"""
M_TERMINATE = """mutation Terminate($input: PodTerminateInput!) { podTerminate(input: $input) }"""


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        sys.exit(f"set {name}")
    return value


def _secrets() -> list[str]:
    return [os.environ.get(n, "") for n in ("EVON_API_KEY", "HF_TOKEN", "RUNPOD_API_KEY")]


def gql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        GRAPHQL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {env('RUNPOD_API_KEY')}",
            "User-Agent": "kalpakka-infra/1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data: dict[str, Any] = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        sys.exit(redact(f"RunPod API HTTP {exc.code}: {exc.read()[:300]!r}", _secrets()))
    except (urllib.error.URLError, OSError) as exc:
        sys.exit(f"RunPod API unreachable ({GRAPHQL}): {exc}")
    if data.get("errors"):
        # Validation errors can echo the input (including env values): redact before printing.
        sys.exit(redact(f"RunPod API error: {data['errors'][0].get('message')}", _secrets()))
    return data["data"]


def myself() -> dict[str, Any]:
    return gql(Q_MYSELF)["myself"]


def our_pods(me: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in me.get("pods") or [] if p["name"] == POD_NAME]


def http_ok(url: str, timeout: float = 20) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return bool(resp.status == 200)
    except (urllib.error.URLError, OSError):
        return False


def wait_healthy(url: str, timeout_s: float) -> float:
    started = time.monotonic()
    last = 0.0
    while time.monotonic() - started < timeout_s:
        if http_ok(f"{url}/health"):
            return time.monotonic() - started
        if time.monotonic() - last > 60:
            print(
                f"[gpu] waiting for llama-server /health … {time.monotonic() - started:.0f} s",
                flush=True,
            )
            last = time.monotonic()
        time.sleep(5)
    sys.exit(
        f"[gpu] llama-server not healthy after {timeout_s:.0f} s; pod still RUNNING — run `stop`"
    )


def record(entry: dict[str, Any]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(LEDGER.read_text()) if LEDGER.exists() else {"sessions": []}
    data["sessions"].append(entry)
    LEDGER.write_text(json.dumps(data, indent=2) + "\n")


def print_status(me: dict[str, Any]) -> None:
    pods = [p for p in our_pods(me) if p["desiredStatus"] == "RUNNING"]
    pod = pods[0] if pods else None
    uptime = (pod.get("runtime") or {}).get("uptimeInSeconds") if pod else None
    prepaid = float(os.environ.get("RUNPOD_PREPAID_USD", "17"))
    print(status_line(
        balance_usd=me.get("clientBalance"), spend_usd_h=me.get("currentSpendPerHr") or 0.0,
        prepaid_usd=prepaid, pod_uptime_s=uptime, pod_rate=pod["costPerHr"] if pod else None,
    ))  # fmt: skip
    if pod:
        url = proxy_url(pod["id"])
        health = "healthy" if http_ok(f"{url}/health") else "NOT healthy"
        print(f"[gpu] pod {pod['id']} RUNNING · {url}/v1 · llama-server {health}")


def warmup(url: str) -> int:
    """3 real Ramesh turns through the backend's prompt code (runs in the backend env)."""
    cmd = ["uv", "run", "--frozen", "python", str(HERE / "warmup.py"), "--base-url", f"{url}/v1"]
    return subprocess.run(cmd, cwd=ROOT / "backend", check=False).returncode


def cmd_start(args: argparse.Namespace) -> None:
    cfg = PodConfig(
        template_id=env("RUNPOD_TEMPLATE_ID"),
        network_volume_id=env("RUNPOD_NETWORK_VOLUME_ID"),
        gguf_repo=env("HF_GGUF_REPO"),
        gpu_type=os.environ.get("RUNPOD_GPU_TYPE", "NVIDIA RTX A6000"),
        planned_rate_usd_h=float(os.environ.get("RUNPOD_RATE_USD_H", "0.59")),
    )
    me = myself()
    if any(p["desiredStatus"] == "RUNNING" for p in our_pods(me)):
        print("[gpu] a kalpakka-evon pod is already running; not starting a second one")
        print_status(me)
        return
    ok, hours = can_start(
        balance_usd=me.get("clientBalance"),
        rate_usd_h=cfg.planned_rate_usd_h,
        other_spend_usd_h=me.get("currentSpendPerHr") or 0.0,
    )
    if not ok:
        sys.exit(
            f"[gpu] REFUSING: balance ${me.get('clientBalance')} lasts "
            f"{'unknown' if hours is None else f'{hours:.2f}'} h (< 1 h). Top up first."
        )
    stop_at = terminate_after(dt.datetime.now(IST), args.until, grace_min=15)
    inp = deploy_input(
        cfg, flags=read_flags(HERE / "llama-server.flags"), quant=args.quant,
        api_key=env("EVON_API_KEY"), hf_token=env("HF_TOKEN"), terminate_at=stop_at,
    )  # fmt: skip
    print(f"[gpu] balance ${me['clientBalance']:.2f} (≈ {hours:.1f} h); deploying {cfg.gpu_type}, "
          f"auto-terminate at {stop_at} UTC", flush=True)  # fmt: skip
    started = time.monotonic()
    pod = gql(M_DEPLOY, {"input": inp})["podFindAndDeployOnDemand"]
    if not pod:
        sys.exit("[gpu] no A6000 available with this network volume right now; retry in a minute")
    rate = pod["costPerHr"]
    print(f"[gpu] pod {pod['id']} created at ${rate}/h", flush=True)
    if rate > cfg.planned_rate_usd_h * 1.2:
        print(f"[gpu] WARNING: ${rate}/h is above the planned ${cfg.planned_rate_usd_h}/h")
    url = proxy_url(pod["id"])
    ready = wait_healthy(url, timeout_s=45 * 60)  # the first start downloads ~32 GB
    print(f"[gpu] llama-server healthy {ready:.0f} s after create "
          f"(total {time.monotonic() - started:.0f} s)", flush=True)  # fmt: skip
    now = dt.datetime.now(IST).isoformat(timespec="seconds")
    record({"event": "start", "pod": pod["id"], "rate": rate, "ist": now, "ready_s": round(ready),
            "balance": me["clientBalance"], "terminate_after": stop_at})  # fmt: skip
    if not args.no_warmup and warmup(url) != 0:
        print("[gpu] WARM-UP FAILED: pod is RUNNING and billing. Fix or run `stop`.")
        sys.exit(1)
    print(f"[gpu] READY · LLM_BASE_URL={url}/v1 · LLM_MODEL=evon")
    print_status(myself())


def cmd_status(_: argparse.Namespace) -> None:
    print_status(myself())


def cmd_warmup(_: argparse.Namespace) -> None:
    pods = [p for p in our_pods(myself()) if p["desiredStatus"] == "RUNNING"]
    if not pods:
        sys.exit("[gpu] no running kalpakka-evon pod")
    sys.exit(warmup(proxy_url(pods[0]["id"])))


def cmd_stop(_: argparse.Namespace) -> None:
    me = myself()
    pods = our_pods(me)
    if not pods:
        print("[gpu] no kalpakka-evon pod; nothing to stop")
        print_status(me)
        return
    for p in pods:
        uptime = (p.get("runtime") or {}).get("uptimeInSeconds") or 0
        cost = session_cost(uptime_s=uptime, cost_per_hr=p["costPerHr"])
        gql(M_TERMINATE, {"input": {"podId": p["id"]}})
        print(f"[gpu] terminated {p['id']} after {uptime / 3600:.2f} h at ${p['costPerHr']}/h "
              f"≈ ${cost:.2f} (network volume kept)")  # fmt: skip
        now = dt.datetime.now(IST).isoformat(timespec="seconds")
        record({"event": "stop", "pod": p["id"], "uptime_s": uptime, "rate": p["costPerHr"],
                "usd": round(cost, 4), "ist": now})  # fmt: skip
    time.sleep(3)
    print_status(myself())


def main() -> None:
    parser = argparse.ArgumentParser(description="Evon live-hours GPU on RunPod")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start")
    p.add_argument(
        "--until", default="23:00", help="IST stop time; pod self-terminates 15 min later"
    )
    p.add_argument("--quant", default="Q8_0", help="GGUF to serve (the gated one)")
    p.add_argument("--no-warmup", action="store_true")
    p.set_defaults(fn=cmd_start)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("warmup").set_defaults(fn=cmd_warmup)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
