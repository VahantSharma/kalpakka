# /// script
# requires-python = ">=3.12"
# dependencies = ["modal==1.6.2"]
# ///
"""Step 4. Serve our Q8_0 GGUF with llama-server (official image server-cuda12-b12030, same build
as the converter) on one L40S 48 GB, OpenAI-compatible, API key required, scale to zero.

The L40S stands in for the RunPod A6000 (both 48 GB); the A6000 is slower, so its TTFT is
re-measured in P5.3. Flags come from infra/gpu/llama-server.flags (shared with RunPod).
The GGUF is read from the Modal volume written by convert.py.

    uv run --script scripts/spikes/evon_modal/serve_q8.py --minutes 45 [--quant Q8_0]
"""

import argparse
import os
import shlex
import subprocess
import time
from pathlib import Path

import modal

LLAMA_CPP_TAG = "b12030"
VOLUME = "evon-weights"
MOUNT = "/vol"
GPU = "L40S"
CPU_CORES, MEM_GIB = 4, 16
PORT = 8080
MAX_MINUTES = 60


def read_flags(path: Path) -> list[str]:
    """One flag (and value) per line, '#' comments; returns argv tokens."""
    tokens: list[str] = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            tokens += shlex.split(line)
    return tokens


# The flags file only exists on the laptop; the image carries them into the container as env.
# The path is computed only on the laptop: in the container this file is /root/serve_q8.py.
FLAGS = (
    shlex.join(read_flags(Path(__file__).resolve().parents[3] / "infra/gpu/llama-server.flags"))
    if modal.is_local()
    else ""
)
QUANT = os.environ.get("EVON_QUANT", "Q8_0")

app = modal.App("kalpakka-evon-q8")
image = (
    modal.Image.from_registry(
        f"ghcr.io/ggml-org/llama.cpp:server-cuda12-{LLAMA_CPP_TAG}", add_python="3.12"
    )
    .entrypoint([])
    .env({"EVON_LLAMA_FLAGS": FLAGS, "EVON_QUANT": QUANT})
)
weights = modal.Volume.from_name(VOLUME)


@app.function(
    image=image,
    gpu=GPU,
    cpu=CPU_CORES,
    memory=MEM_GIB * 1024,
    volumes={MOUNT: weights},
    secrets=[modal.Secret.from_name("evon-api")],
    timeout=MAX_MINUTES * 60,
    scaledown_window=5 * 60,
    max_containers=1,
)
@modal.concurrent(max_inputs=32)
@modal.web_server(port=PORT, startup_timeout=15 * 60)
def serve() -> None:
    model = f"{MOUNT}/gguf/evon-v3.3-{os.environ['EVON_QUANT']}.gguf"
    cmd = ["/app/llama-server", "-m", model, *shlex.split(os.environ["EVON_LLAMA_FLAGS"])]
    # llama-server reads the key from LLAMA_API_KEY; it never appears on the command line.
    env = {**os.environ, "LLAMA_API_KEY": os.environ["EVON_API_KEY"]}
    subprocess.Popen(cmd, env=env)


if __name__ == "__main__":
    from budget import Resources
    from runner import guarded

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, default=45)
    args = parser.parse_args()
    if not 0 < args.minutes <= MAX_MINUTES:
        raise SystemExit(f"--minutes must be in (0, {MAX_MINUTES}]")
    print(f"[serve_q8] quant {QUANT} (set EVON_QUANT=Q4_K_M only for data); flags: {FLAGS}")
    res = Resources(gpu=GPU, cpu_cores=CPU_CORES, mem_gib=MEM_GIB)
    with guarded("serve_q8", res, max_seconds=args.minutes * 60), modal.enable_output(), app.run():
        url = serve.get_web_url()
        print(f"[serve_q8] base URL: {url}/v1   (model name: evon; key: your EVON_API_KEY)")
        print(
            f"[serve_q8] holding for {args.minutes:.0f} min; Ctrl-C stops the app now.", flush=True
        )
        deadline = time.monotonic() + args.minutes * 60
        try:
            while (left := deadline - time.monotonic()) > 0:
                time.sleep(min(60.0, left))
                print(
                    f"[serve_q8] {max(0.0, deadline - time.monotonic()) / 60:.0f} min left",
                    flush=True,
                )
        except KeyboardInterrupt:
            print("[serve_q8] Ctrl-C: stopping the app")
    print("[serve_q8] app stopped (check `modal app list` shows no running kalpakka-evon-q8)")
