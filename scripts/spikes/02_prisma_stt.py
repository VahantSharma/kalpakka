"""Spike 02: Prisma v3 realtime STT against the real service.

Streams the M10 recordings in 1,024-byte frames at real-time pace, sends a commit after each
line, prints every server message with a timestamp, and reports commit->transcript times.
Not imported by app code. The API key is read from GNANI_API_KEY (or .env) and never printed.

Usage (from backend/):
  uv run python ../scripts/spikes/02_prisma_stt.py --runs 5
  uv run python ../scripts/spikes/02_prisma_stt.py --runs 1 --lines 1 --sep '|'
  uv run python ../scripts/spikes/02_prisma_stt.py --runs 1 --lines 1 --header speaker_names=Pooja,Ramesh
  uv run python ../scripts/spikes/02_prisma_stt.py --silence
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import wave
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from websockets.asyncio.client import ClientConnection, connect

REPO = Path(__file__).resolve().parents[2]
AUDIO = REPO / "backend/tests/fixtures/audio"
OUT = REPO / "scripts/spikes/out"
URL = "wss://api.vachana.ai/stt/v3/stream"
FRAME = 1024
FRAME_S = 512 / 16000  # 32 ms
KEYTERMS = [
    "EMI",
    "NACH",
    "PTP",
    "CIBIL",
    "foreclosure",
    "bounce charge",
    "SwiftFin",
    "Ramesh",
    "Pooja",
]
WAIT_AFTER_COMMIT_S = 6.0


class Keys(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO / ".env", extra="ignore")
    gnani_api_key: SecretStr


def headers(key: str, sep: str, extra: list[str], run: str) -> dict[str, str]:
    h = {
        "x-api-key-id": key,
        "model": "prisma-v3.0",
        "language_code": "hi-IN,en-IN",
        "format": "transcribe",
        "x-sample-rate": "16000",
        "x-min-silence-ms": "600",
        "x-idle-timeout-sec": "60",
        "x-api-request-id": run,
        "keyterms": sep.join(KEYTERMS),
    }
    for kv in extra:
        k, _, v = kv.partition("=")
        h[k] = v
    return h


def pcm(n: int) -> bytes:
    with wave.open(str(AUDIO / f"line{n}.wav")) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        return w.readframes(w.getnframes())


def short(msg: Any) -> str:
    s = msg if isinstance(msg, str) else f"<binary {len(msg)} B>"
    return s if len(s) < 600 else s[:600] + "..."


class Recv:
    def __init__(self, ws: ClientConnection, t0: float) -> None:
        self.ws, self.t0 = ws, t0
        self.msgs: list[tuple[float, dict[str, Any] | str]] = []
        self.event = asyncio.Event()

    async def loop(self) -> None:
        try:
            async for raw in self.ws:
                t = time.perf_counter()
                print(f"  [{t - self.t0:7.3f}s] <- {short(raw)}", flush=True)
                try:
                    parsed: dict[str, Any] | str = json.loads(raw)
                except (TypeError, ValueError):
                    parsed = short(raw)
                self.msgs.append((t, parsed))
                self.event.set()
        except Exception as e:
            print(f"  socket ended: {type(e).__name__}: {e}", flush=True)
        print(f"  close code={self.ws.close_code} reason={self.ws.close_reason!r}", flush=True)


def is_transcript(m: dict[str, Any] | str) -> bool:
    return isinstance(m, dict) and (
        m.get("type") == "transcript" or "text" in m or "text" in m.get("data", {})
    )


async def stream(ws: ClientConnection, audio: bytes) -> float:
    start = time.perf_counter()
    for i in range(0, len(audio), FRAME):
        frame = audio[i : i + FRAME].ljust(FRAME, b"\x00")
        await asyncio.wait_for(ws.send(frame), 2)
        delay = start + (i // FRAME + 1) * FRAME_S - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
    return time.perf_counter()


async def session(key: str, args: argparse.Namespace, run_no: int) -> list[dict[str, Any]]:
    run_id = f"spike02-{int(time.time())}-{run_no}"
    t0 = time.perf_counter()
    rows: list[dict[str, Any]] = []
    async with connect(
        URL,
        additional_headers=headers(key, args.sep, args.header, run_id),
        open_timeout=10,
        close_timeout=1,
        proxy=None,
    ) as ws:
        print(f"  [{time.perf_counter() - t0:7.3f}s] socket open", flush=True)
        r = Recv(ws, t0)
        task = asyncio.create_task(r.loop())
        await asyncio.sleep(1.0)  # let `connected` arrive
        lines = [] if args.silence else [int(x) for x in args.lines.split(",")]
        payloads = (
            [("silence", b"\x00" * 32000)]
            if args.silence
            else [(f"line{n}", pcm(n)) for n in lines]
        )
        for name, audio in payloads:
            before = len(r.msgs)
            t_last = await stream(ws, audio)
            t_commit = time.perf_counter()
            await ws.send(json.dumps({"type": "commit"}))
            print(
                f"  [{t_commit - t0:7.3f}s] -> commit ({name}, last frame {t_commit - t_last:.3f}s earlier)",
                flush=True,
            )
            deadline = t_commit + WAIT_AFTER_COMMIT_S
            post: list[tuple[float, Any]] = []
            while time.perf_counter() < deadline and not post:
                r.event.clear()
                try:
                    await asyncio.wait_for(r.event.wait(), deadline - time.perf_counter())
                except TimeoutError:
                    break
                post = [(t, m) for t, m in r.msgs[before:] if t >= t_commit and is_transcript(m)]
            await asyncio.sleep(0.8)  # settle: catch any later segment
            seg = r.msgs[before:]
            pre = [(t, m) for t, m in seg if t < t_commit and is_transcript(m)]
            after = [(t, m) for t, m in seg if t >= t_commit and is_transcript(m)]
            rows.append(
                {
                    "run": run_no,
                    "line": name,
                    "pre_commit_transcripts": len(pre),
                    "commit_to_transcript_s": round(after[0][0] - t_commit, 3) if after else None,
                    "lastframe_to_transcript_s": round(after[0][0] - t_last, 3) if after else None,
                    "transcripts": [m for _, m in pre + after],
                }
            )
        task.cancel()
    return rows


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--lines", default="1,2,3")
    p.add_argument("--sep", default=",")
    p.add_argument("--header", action="append", default=[])
    p.add_argument("--silence", action="store_true")
    args = p.parse_args()
    key = Keys().gnani_api_key.get_secret_value()  # type: ignore[call-arg]
    all_rows: list[dict[str, Any]] = []
    for n in range(1, args.runs + 1):
        print(
            f"=== run {n} sep={args.sep!r} extra={args.header} silence={args.silence}", flush=True
        )
        try:
            all_rows += await session(key, args, n)
        except Exception as e:
            print(f"  run {n} failed: {type(e).__name__}: {e}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"spike02_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=1))
    print(f"\nsaved {out.relative_to(REPO)}")
    print("\nrun line   pre  commit->tr  lastframe->tr")
    for row in all_rows:
        print(
            f"{row['run']:>3} {row['line']:<7}{row['pre_commit_transcripts']:>3}  "
            f"{row['commit_to_transcript_s']!s:>10}  {row['lastframe_to_transcript_s']!s:>12}"
        )
    c = [r["commit_to_transcript_s"] for r in all_rows if r["commit_to_transcript_s"] is not None]
    if c:
        print(
            f"\ncommit->transcript n={len(c)} p50={statistics.median(c):.3f}s max={max(c):.3f}s min={min(c):.3f}s"
        )


if __name__ == "__main__":
    asyncio.run(main())
