# /// script
# requires-python = ">=3.12"
# dependencies = ["modal==1.6.2"]
# ///
"""Step 3. Build our own Evon GGUF on a Modal CPU container and upload it to a PRIVATE HF repo.

llama.cpp is pinned to tag b12030 (its `conversion/nemotron.py` registers NemotronHForCausalLM,
MoE included). Steps, all inside one container, reading the BF16 weights from the volume:

  1. convert_hf_to_gguf.py --outtype bf16 --no-mtp  -> /vol/gguf/evon-v3.3-bf16.gguf (intermediate)
  2. llama-quantize ... Q8_0                         -> evon-v3.3-Q8_0.gguf   (the live candidate)
  3. llama-quantize ... Q4_K_M                       -> evon-v3.3-Q4_K_M.gguf (data only)
  4. gguf_dump metadata check (architecture, chat template present), sha256 of each file
  5. upload Q8_0, Q4_K_M, SHA256SUMS and a provenance README to HF_GGUF_REPO (created private)

Needs: Modal secret `hf` (HF_TOKEN with write access), local env HF_GGUF_REPO (e.g.
"vahantsharma/evon-v3.3-gguf"). `--skip-upload` keeps everything in the volume only.
If the converter fails on the tokenizer, re-run with env CONVERT_TRANSFORMERS_PIN=5.8.1
(Evon's config was
saved with transformers 5.8.1; llama.cpp b12030 pins 4.57.6 for conversion).

    HF_GGUF_REPO=<user>/evon-v3.3-gguf uv run --script scripts/spikes/evon_modal/convert.py
"""

import argparse
import os

import modal

LLAMA_CPP_TAG = "b12030"
MODEL_ID = "gnani/gnani-evon-v3.3-30B-A3B"
VOLUME = "evon-weights"
MOUNT = "/vol"
MODEL_DIR = f"{MOUNT}/hf/gnani-evon-v3.3-30B-A3B"
OUT_DIR = f"{MOUNT}/gguf"
CPU_CORES, MEM_GIB = 8, 64
TIMEOUT_S = 120 * 60
QUANTS = ("Q8_0", "Q4_K_M")


def build_image(transformers_pin: str | None) -> modal.Image:
    img = (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install("git", "build-essential", "cmake", "ca-certificates")
        .run_commands(
            f"git clone --depth 1 --branch {LLAMA_CPP_TAG} "
            "https://github.com/ggml-org/llama.cpp /opt/llama.cpp",
            "cmake -S /opt/llama.cpp -B /opt/llama.cpp/build -DGGML_CUDA=OFF -DLLAMA_CURL=OFF "
            "-DLLAMA_BUILD_TESTS=OFF -DCMAKE_BUILD_TYPE=Release",
            "cmake --build /opt/llama.cpp/build --target llama-quantize -j 8",
        )
        .run_commands(  # the requirements file lives in the image, so pip runs there
            "python -m pip install --no-cache-dir "
            "-r /opt/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt"
        )
        # huggingface_hub comes from the converter requirements: transformers 4.57.6 needs <1.0
        .env({"HF_HUB_DISABLE_TELEMETRY": "1", "PYTHONPATH": "/opt/llama.cpp/gguf-py"})
    )
    if transformers_pin:
        img = img.uv_pip_install(f"transformers=={transformers_pin}")
    return img


# Read at import time: the image (built on import, locally and in the container) depends on it.
TRANSFORMERS_PIN = os.environ.get("CONVERT_TRANSFORMERS_PIN") or None
app = modal.App("kalpakka-evon-convert")
volume = modal.Volume.from_name(VOLUME)


@app.function(
    image=build_image(TRANSFORMERS_PIN),
    volumes={MOUNT: volume},
    secrets=[modal.Secret.from_name("hf")],
    cpu=CPU_CORES,
    memory=MEM_GIB * 1024,
    timeout=TIMEOUT_S,
)
def convert(repo: str | None, keep_bf16: bool) -> dict[str, object]:
    import hashlib
    import subprocess
    import time
    from pathlib import Path

    def run(cmd: list[str]) -> float:
        print("+", " ".join(cmd), flush=True)
        t = time.monotonic()
        subprocess.run(cmd, check=True)
        return round(time.monotonic() - t, 1)

    def sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(16 * 1024 * 1024):
                h.update(chunk)
        return h.hexdigest()

    out = Path(OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    revision = (Path(MODEL_DIR) / "REVISION").read_text().strip()
    timings: dict[str, float] = {}
    bf16 = out / "evon-v3.3-bf16.gguf"
    if not bf16.exists():
        timings["convert_bf16_s"] = run([
            "python", "/opt/llama.cpp/convert_hf_to_gguf.py", MODEL_DIR,
            "--outtype", "bf16", "--no-mtp", "--outfile", str(bf16),
        ])  # fmt: skip
    files: dict[str, Path] = {}
    for q in QUANTS:
        target = out / f"evon-v3.3-{q}.gguf"
        if not target.exists():
            timings[f"quantize_{q}_s"] = run([
                "/opt/llama.cpp/build/bin/llama-quantize", str(bf16), str(target), q,
                str(CPU_CORES),
            ])  # fmt: skip
        files[q] = target
    meta = subprocess.run(
        ["python", "/opt/llama.cpp/gguf-py/gguf/scripts/gguf_dump.py", "--no-tensors",
         str(files["Q8_0"])],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    arch = next((ln.split("=", 1)[1].strip().strip("'\"") for ln in meta.splitlines()
                 if "general.architecture" in ln), "unknown")  # fmt: skip
    has_template = "tokenizer.chat_template" in meta
    sums = {q: sha256(p) for q, p in files.items()}
    (out / "SHA256SUMS").write_text("".join(f"{sums[q]}  {files[q].name}\n" for q in QUANTS))
    if not keep_bf16:
        bf16.unlink()
    volume.commit()

    uploaded = None
    if repo:
        from huggingface_hub import HfApi

        api = HfApi()
        api.create_repo(repo, private=True, exist_ok=True, repo_type="model")
        readme = (
            f"# Evon v3.3 GGUF (private build)\n\nDerived from `{revision}` (Apache-2.0) with "
            f"llama.cpp `{LLAMA_CPP_TAG}`: convert_hf_to_gguf.py --outtype bf16 --no-mtp, then "
            "llama-quantize Q8_0 / Q4_K_M. Q8_0 is the serving candidate; Q4_K_M is kept as data."
            "\n\n```\n" + (out / "SHA256SUMS").read_text() + "```\n"
        )
        (out / "README.md").write_text(readme)
        for path in [*files.values(), out / "SHA256SUMS", out / "README.md"]:
            t = time.monotonic()
            api.upload_file(path_or_fileobj=str(path), path_in_repo=path.name, repo_id=repo)
            timings[f"upload_{path.name}_s"] = round(time.monotonic() - t, 1)
        uploaded = repo
    return {
        "source": revision,
        "llama_cpp": LLAMA_CPP_TAG,
        "architecture": arch,
        "chat_template_in_gguf": has_template,
        "sha256": sums,
        "gb": {q: round(p.stat().st_size / 1e9, 2) for q, p in files.items()},
        "timings": timings,
        "uploaded_to": uploaded,
    }


if __name__ == "__main__":
    from budget import Resources
    from runner import guarded

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--keep-bf16", action="store_true", help="keep the bf16 GGUF in the volume")
    args = parser.parse_args()
    repo = None if args.skip_upload else os.environ.get("HF_GGUF_REPO")
    if not args.skip_upload and not repo:
        raise SystemExit(
            "set HF_GGUF_REPO=<user>/<repo> (private repo is created) or --skip-upload"
        )
    res = Resources(gpu=None, cpu_cores=CPU_CORES, mem_gib=MEM_GIB)
    with guarded("convert", res, max_seconds=TIMEOUT_S), modal.enable_output(), app.run():
        result = convert.remote(repo, args.keep_bf16)
    print(f"[convert] {result}")
    if result["architecture"] != "nemotron_h_moe" or not result["chat_template_in_gguf"]:
        raise SystemExit("[convert] metadata check FAILED: see architecture / chat template above")
