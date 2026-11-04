# /// script
# requires-python = ">=3.12"
# dependencies = ["modal==1.6.2"]
# ///
"""Step 2. Serve the BF16 reference: vLLM (official image v0.31.0, card requires >= 0.12.0) on one
A100-80GB, OpenAI-compatible, API key required, scale to zero.

Model-card flags, unchanged: --trust-remote-code --enable-auto-tool-choice
--tool-call-parser qwen3_coder --reasoning-parser nano_v3. Added: --enable-prefix-caching,
--api-key (from the Modal secret `evon-api`, key EVON_API_KEY), --served-model-name evon,
--max-model-len 32768 (card default is 131072; our prompts are < 8K, so a smaller window leaves
more memory for batching. Revisit if the judge needs more).

The app is ephemeral: it lives while this script runs (default 60 min, --minutes to change) and is
stopped when the script exits or on Ctrl-C. Containers scale to zero after 5 idle minutes, so the
GPU bill is at most the hold time and usually less.

    uv run --script scripts/spikes/evon_modal/serve_ref.py --minutes 60
"""

import argparse
import subprocess
import time

import modal

VOLUME = "evon-weights"
MOUNT = "/vol"
MODEL_DIR = f"{MOUNT}/hf/gnani-evon-v3.3-30B-A3B"
GPU = "A100-80GB"
CPU_CORES, MEM_GIB = 4, 32
PORT = 8000
MAX_MINUTES = 90  # hard ceiling on the hold, used for the worst-case budget check

app = modal.App("kalpakka-evon-ref")
image = modal.Image.from_registry("vllm/vllm-openai:v0.31.0", add_python="3.12").entrypoint([])
weights = modal.Volume.from_name(VOLUME)
compile_cache = modal.Volume.from_name("evon-vllm-cache", create_if_missing=True)

VLLM_CMD = [
    "vllm", "serve", MODEL_DIR,
    "--served-model-name", "evon",
    "--host", "0.0.0.0", "--port", str(PORT),
    "--trust-remote-code",
    "--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder",
    "--reasoning-parser", "nano_v3",
    "--enable-prefix-caching",
    "--max-model-len", "32768",
]  # fmt: skip


@app.function(
    image=image,
    gpu=GPU,
    cpu=CPU_CORES,
    memory=MEM_GIB * 1024,
    volumes={MOUNT: weights, "/root/.cache/vllm": compile_cache},
    secrets=[modal.Secret.from_name("evon-api")],
    timeout=MAX_MINUTES * 60,
    scaledown_window=5 * 60,
    max_containers=1,
)
@modal.concurrent(max_inputs=32)
@modal.web_server(port=PORT, startup_timeout=20 * 60)
def serve() -> None:
    import os

    # The key is passed through the environment, never on a logged command line.
    env = {**os.environ, "VLLM_API_KEY": os.environ["EVON_API_KEY"]}
    subprocess.Popen(VLLM_CMD, env=env)


def hold(minutes: float, url: str) -> None:
    print(f"[serve_ref] base URL: {url}/v1   (model name: evon; key: your EVON_API_KEY)")
    print("[serve_ref] first request cold-starts the container (weights load + CUDA graphs).")
    print(f"[serve_ref] holding for {minutes:.0f} min; Ctrl-C stops the app now.", flush=True)
    deadline = time.monotonic() + minutes * 60
    try:
        while time.monotonic() < deadline:
            time.sleep(min(60.0, max(0.0, deadline - time.monotonic())))
            left = (deadline - time.monotonic()) / 60
            print(f"[serve_ref] {left:.0f} min left", flush=True)
    except KeyboardInterrupt:
        print("[serve_ref] Ctrl-C: stopping the app")


if __name__ == "__main__":
    from budget import Resources
    from runner import guarded

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, default=60)
    args = parser.parse_args()
    if not 0 < args.minutes <= MAX_MINUTES:
        raise SystemExit(f"--minutes must be in (0, {MAX_MINUTES}]")
    res = Resources(gpu=GPU, cpu_cores=CPU_CORES, mem_gib=MEM_GIB)
    with guarded("serve_ref", res, max_seconds=args.minutes * 60), modal.enable_output(), app.run():
        hold(args.minutes, serve.get_web_url())
    print("[serve_ref] app stopped (check `modal app list` shows no running kalpakka-evon-ref)")
