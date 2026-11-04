"""Warm-up: 3 real Ramesh turns against a live Evon server, with the backend's own prompt code.

Run in the backend environment (infra/gpu/runpod.py does this for you):

    cd backend && EVON_API_KEY=... uv run --frozen python ../infra/gpu/warmup.py --base-url https://<pod>-8080.proxy.runpod.net/v1

Exit 0 only if all 3 replies arrive, start speaking, keep thinking off and carry a valid
###STATE### block. Prints the measured TTFT of each (the first one includes any cold path).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "spikes" / "evon_modal"))

from run_spike import THINKING_VARIANTS, build_turns, stream_turn  # noqa: E402
from spikekit import thinking_leaked  # noqa: E402

SCENE = "discovery_reveal"


async def main(base_url: str, model: str) -> int:
    turns, _, _ = build_turns(ROOT / "scripts" / "spikes" / "evon_modal" / "turns.yaml")
    picked = [t for t in turns if t["scene"] == SCENE][:3]
    client = AsyncOpenAI(
        base_url=base_url, api_key=os.environ["EVON_API_KEY"], max_retries=0, timeout=120
    )
    failures = 0
    for t in picked:
        r = await stream_turn(
            client, model, t["messages"], THINKING_VARIANTS["chat_template_kwargs"]
        )
        leaked = thinking_leaked(r)
        ok = r["error"] is None and r["ttft_s"] is not None and not r["state_errors"] and not leaked
        failures += not ok
        ttft = "none" if r["ttft_s"] is None else f"{r['ttft_s']:.3f} s"
        verdict = "OK" if ok else "FAIL"
        thinking = "LEAKED" if leaked else "off"
        print(f"[warmup] {t['id']:<20} ttft={ttft} state={r['state_errors'] or 'ok'} "
              f"thinking={thinking} {verdict} {r['speech'][:50]!r}")  # fmt: skip
        if r["error"]:
            print(f"[warmup]   error: {r['error']}")
    print(f"[warmup] {3 - failures}/3 OK")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", default="evon")
    args = parser.parse_args()
    if not os.environ.get("EVON_API_KEY"):
        sys.exit("set EVON_API_KEY")
    sys.exit(asyncio.run(main(args.base_url, args.model)))
