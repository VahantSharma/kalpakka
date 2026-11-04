# /// script
# requires-python = ">=3.12"
# dependencies = ["modal==1.6.2"]
# ///
"""Step 1. Snapshot Evon v3.3 BF16 into the Modal Volume `evon-weights` on a CPU container.

No GPU time is spent downloading. Needs the Modal secret `hf` holding HF_TOKEN (the repo is not
gated, but a token avoids anonymous rate limits). Idempotent: files already in the volume are
skipped by snapshot_download.

    uv run --script scripts/spikes/evon_modal/download.py [--revision main]
"""

import argparse
import time

import modal

MODEL_ID = "gnani/gnani-evon-v3.3-30B-A3B"
VOLUME = "evon-weights"
MOUNT = "/vol"
MODEL_DIR = f"{MOUNT}/hf/gnani-evon-v3.3-30B-A3B"
CPU_CORES, MEM_GIB = 4, 16
TIMEOUT_S = 45 * 60

app = modal.App("kalpakka-evon-download")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install("huggingface_hub==2.2.0", "hf_xet==1.7.0")
    .env({"HF_HUB_DISABLE_TELEMETRY": "1"})
)
volume = modal.Volume.from_name(VOLUME, create_if_missing=True)


@app.function(
    image=image,
    volumes={MOUNT: volume},
    secrets=[modal.Secret.from_name("hf")],
    cpu=CPU_CORES,
    memory=MEM_GIB * 1024,
    timeout=TIMEOUT_S,
)
def download(revision: str) -> dict[str, object]:
    from pathlib import Path

    from huggingface_hub import HfApi, snapshot_download

    sha = HfApi().model_info(MODEL_ID, revision=revision).sha
    started = time.monotonic()
    snapshot_download(MODEL_ID, revision=sha, local_dir=MODEL_DIR, max_workers=16)
    seconds = time.monotonic() - started
    root = Path(MODEL_DIR)
    (root / "REVISION").write_text(f"{MODEL_ID}@{sha}\n")
    files = [p for p in root.rglob("*") if p.is_file() and ".cache" not in p.parts]
    volume.commit()
    return {
        "revision": sha,
        "files": len(files),
        "gb": round(sum(p.stat().st_size for p in files) / 1e9, 2),
        "download_s": round(seconds, 1),
        "safetensors": sorted(p.name for p in files if p.suffix == ".safetensors")[:3],
    }


if __name__ == "__main__":
    from budget import Resources
    from runner import guarded

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--revision", default="main")
    args = parser.parse_args()
    res = Resources(gpu=None, cpu_cores=CPU_CORES, mem_gib=MEM_GIB)
    with guarded("download", res, max_seconds=TIMEOUT_S), modal.enable_output(), app.run():
        result = download.remote(args.revision)
    print(f"[download] {result}")
    print(f"[download] weights at volume {VOLUME}:{MODEL_DIR.removeprefix(MOUNT)}")
