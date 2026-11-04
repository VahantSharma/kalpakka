"""The configuration measured in P0.5 must be the one that serves: no silent drift."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CARD_FLAGS = {  # Evon model card, read 2026-11-05
    "--trust-remote-code",
    "--enable-auto-tool-choice",
    "--tool-call-parser qwen3_coder",
    "--reasoning-parser nano_v3",
}


def _serve_ref_flags() -> set[str]:
    tree = ast.parse((ROOT / "scripts/spikes/evon_modal/serve_ref.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "VLLM_CMD":
            items = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
            return _pairs(items)
    raise AssertionError("VLLM_CMD not found")


def _launch_sh_flags() -> set[str]:
    text = (ROOT / "infra/vllm/launch.sh").read_text()
    block = re.search(r"args=\((.*?)\n\)", text, re.DOTALL)
    assert block, "args=( ... ) block not found"
    tokens = [t for line in block.group(1).splitlines() for t in line.split() if t]
    return _pairs(tokens)


def _pairs(tokens: list[str]) -> set[str]:
    """'--flag value' pairs and bare '--flag's; values that are variables are kept as written."""
    out: set[str] = set()
    for i, tok in enumerate(tokens):
        if not tok.startswith("--"):
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        out.add(f"{tok} {nxt}" if nxt and not nxt.startswith("--") else tok)
    return out


def test_modal_reference_uses_every_model_card_flag() -> None:
    assert _serve_ref_flags() >= CARD_FLAGS


def test_rented_gpu_launcher_uses_every_model_card_flag() -> None:
    assert _launch_sh_flags() >= CARD_FLAGS


def test_both_vllm_launchers_enable_prefix_caching_and_name_the_model_evon() -> None:
    for flags in (_serve_ref_flags(), _launch_sh_flags()):
        assert "--enable-prefix-caching" in flags
        assert "--served-model-name evon" in flags
