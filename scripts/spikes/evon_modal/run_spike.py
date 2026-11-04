r"""Step 5. Measure one Evon server (BF16 reference, Q8_0, or FP8) against the same 30 scripted
Ramesh turns, and write a JSON run + a markdown summary to evals/runs/ (git-ignored).

Runs in the backend environment, because prompts come from the real code (`brain.build_messages`
+ `BorrowerEngine` briefs) and replies are split by the real `StateSplitter`:

    cd backend && EVON_API_KEY=... uv run --frozen \
        python ../scripts/spikes/evon_modal/run_spike.py \
        --base-url https://<host>/v1 --label bf16 [--cold-start]
    cd backend && uv run --frozen python ../scripts/spikes/evon_modal/run_spike.py --dry-run

Measured, in order:
  1. cold start (only with --cold-start: seconds until GET /health answers 200), then a
     prefix-cache probe (same fresh prompt twice: TTFT drop = cache active)
  2. thinking-off probe: 3 turns x request variants; picks the variant that keeps reasoning out
  3. 30 sequential turns, thinking off: TTFT (first non-blank content), STATE validity, scripts
  4. 10 judge calls, thinking on, response_format JSON schema = JudgeOutput (5 scenes x 2)
  5. 8 concurrent turns: wall time, completion tokens/s, per-request TTFT
Nothing is retried and nothing is filled in: a failed call is recorded as failed.
"""

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, get_args

import yaml
from openai import AsyncOpenAI
from pydantic import ValidationError
from spikekit import (
    foreign_scripts,
    percentile,
    prompt_sha,
    render_markdown,
    summarise,
    thinking_leaked,
)

from kalpakka.borrower_state import BorrowerEngine, CallSetup, StateSuggestion, TurnSignals
from kalpakka.brain import REPLY_MAX_TOKENS, HistoryTurn, StateSplitter, build_messages, load_prompt
from kalpakka.clock import SystemClock
from kalpakka.judge import JUDGE_CHECKS, JudgeOutput
from kalpakka.llm import Message
from kalpakka.personas import load_persona
from kalpakka.tripwire import RuleId

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUNS = ROOT / "evals" / "runs"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

TURN_TEMPERATURE = 0.8  # brain.stream_reply default
JUDGE_TEMPERATURE, JUDGE_TOP_P = 0.6, 0.95  # model card, reasoning mode
JUDGE_MAX_TOKENS = 8192
JUDGE_REPEATS = 2
CONCURRENT_N = 8
THINKING_VARIANTS: dict[str, dict[str, Any]] = {
    "chat_template_kwargs": {"chat_template_kwargs": {"enable_thinking": False}},
    "none": {},
}


# --- prompts (pure, deterministic) ---------------------------------------------------------------


def build_turns(script_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict]:
    """Every turn's exact prompt, built by the real engine from the script (fixed context)."""
    script = yaml.safe_load(script_path.read_text(encoding="utf-8"))
    persona = load_persona(ROOT / "content" / "personas" / f"{script['persona']}.yaml")
    system = load_prompt(ROOT / "content" / "prompts" / "borrower-system.md")
    sim_date = dt.date.fromisoformat(script["sim_date"])
    turns: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []
    for scene in script["scenes"]:
        engine = BorrowerEngine(
            CallSetup(persona=persona, difficulty=scene["difficulty"], seed=scene["seed"],
                      sim_date=sim_date),
            SystemClock(),
        )  # fmt: skip
        traps = [(t.name, t.turn) for t in engine.state.traps]
        history: list[HistoryTurn] = []
        transcript: list[dict[str, Any]] = []
        for i, t in enumerate(scene["turns"], start=1):
            sig = dict(t.get("signals") or {})
            sig["confirmed_violations"] = frozenset(sig.get("confirmed_violations", []))
            brief = engine.begin_turn(TurnSignals(**sig))
            messages = build_messages(
                system, persona, engine.state.sim_clock, history, t["agent"], brief.text
            )
            turns.append({
                "id": f"{scene['id']}#{i}", "scene": scene["id"], "turn": i,
                "messages": messages, "prompt_sha": prompt_sha(messages),
                "brief": brief.text, "trap_fired": brief.trap_fired,
                "reveal_now": brief.reveal_now, "agent": t["agent"],
            })  # fmt: skip
            outcome = engine.end_turn(StateSuggestion())
            history += [HistoryTurn(role="agent", text=t["agent"]),
                        HistoryTurn(role="borrower", text=t["heard"])]  # fmt: skip
            transcript += [{"turn": i, "role": "agent", "text": t["agent"]},
                           {"turn": i, "role": "borrower", "text": t["heard"]}]  # fmt: skip
            if outcome.call_over is not None and i < len(scene["turns"]):
                raise SystemExit(f"{scene['id']}: call ended at turn {i} ({outcome.call_over}); "
                                 "edit the script so every scripted turn is reached")  # fmt: skip
        scenes.append({"id": scene["id"], "difficulty": scene["difficulty"], "traps": traps,
                       "sim_clock": engine.state.sim_clock.isoformat(),
                       "transcript": transcript})  # fmt: skip
    meta = {"persona": f"{persona.id}@v{persona.version}",
            "prompt": f"{system.name}@v{system.version}",
            "script": f"{script_path.name}@v{script['version']}"}  # fmt: skip
    return turns, scenes, meta


def judge_messages(scene: dict[str, Any]) -> list[Message]:
    template = (HERE / "judge_prompt.md").read_text(encoding="utf-8")
    template = re.sub(r"\A\s*<!--.*?-->\s*", "", template, flags=re.DOTALL)
    checks = "\n".join(f"- {cid}: {spec.label}" for cid, spec in JUDGE_CHECKS.items())
    rules = ", ".join(get_args(RuleId))
    lines = [f"[turn {x['turn']}] {x['role'].upper()}: {x['text']}" for x in scene["transcript"]]
    return [
        {"role": "system", "content": template.format(checks=checks, rules=rules)},
        {"role": "user", "content": "TRANSCRIPT\n" + "\n".join(lines)},
    ]


def judge_response_format() -> dict[str, Any]:
    return {"type": "json_schema",
            "json_schema": {"name": "judge_output", "strict": True,
                            "schema": JudgeOutput.model_json_schema()}}  # fmt: skip


# --- calls ---------------------------------------------------------------------------------------


def _reasoning_of(delta: Any) -> str:
    extra = getattr(delta, "model_extra", None) or {}
    return str(extra.get("reasoning_content") or extra.get("reasoning") or "")


async def stream_turn(client: AsyncOpenAI, model: str, messages: list[Message],
                      extra: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    started = time.monotonic()
    deltas: list[str] = []
    reasoning = 0
    ttft = first_any = None
    usage = None
    error = None
    try:
        stream = await client.chat.completions.create(
            model=model, messages=messages, stream=True, max_tokens=REPLY_MAX_TOKENS,  # type: ignore[arg-type]
            temperature=TURN_TEMPERATURE, stream_options={"include_usage": True},
            extra_body=extra or None,
        )  # fmt: skip
        async for chunk in stream:
            if chunk.usage is not None:
                usage = chunk.usage.completion_tokens
            for choice in chunk.choices:
                r = _reasoning_of(choice.delta)
                reasoning += len(r)
                piece = choice.delta.content or ""
                if (piece or r) and first_any is None:
                    first_any = time.monotonic() - started
                if piece:
                    deltas.append(piece)
                    if ttft is None and piece.strip():
                        ttft = time.monotonic() - started
    except Exception as exc:  # any failure is recorded as a failed call; nothing is retried
        error = f"{type(exc).__name__}: {exc}"[:300]
    splitter = StateSplitter()
    speech = "".join(splitter.feed(d) for d in deltas)
    result = splitter.finish()
    speech += result.speech_tail
    errors = list(result.errors) + (["request_failed"] if error else [])
    return {
        "ttft_s": ttft, "first_any_s": first_any, "total_s": time.monotonic() - started,
        "completion_tokens": usage, "raw": "".join(deltas), "speech": speech.strip(),
        "state_errors": errors, "think_blocks": result.think_blocks,
        "reasoning_chars": reasoning, "foreign_scripts": foreign_scripts(speech), "error": error,
    }  # fmt: skip


def _strip_to_json(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"\A```(?:json)?\s*|\s*```\Z", "", text)
    return text.strip()


async def judge_call(client: AsyncOpenAI, model: str, scene: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    rec: dict[str, Any] = {"scene": scene["id"], "ok": False, "ok_lenient": False, "error": None}
    try:
        resp = await client.chat.completions.create(
            model=model, messages=judge_messages(scene), max_tokens=JUDGE_MAX_TOKENS,  # type: ignore[arg-type]
            temperature=JUDGE_TEMPERATURE, top_p=JUDGE_TOP_P,
            response_format=judge_response_format(),  # type: ignore[arg-type]
            extra_body={"chat_template_kwargs": {"enable_thinking": True}},
        )  # fmt: skip
        msg = resp.choices[0].message
        content = msg.content or ""
        rec["reasoning_chars"] = len(_reasoning_of(msg))
        rec["completion_tokens"] = resp.usage.completion_tokens if resp.usage else None
        rec["finish_reason"] = resp.choices[0].finish_reason
        rec["content"] = content
        try:
            out = JudgeOutput.model_validate_json(content)
            missing = sorted(set(JUDGE_CHECKS) - {c.id for c in out.checks})
            rec["ok"] = not missing  # schema-valid but skipping checks is not a usable answer
            if missing:
                rec["error"] = f"strict: missing checks {missing}"
        except ValidationError as exc:
            rec["error"] = f"strict: {exc.errors()[0]['msg']}"[:300]
        try:
            JudgeOutput.model_validate_json(_strip_to_json(content))
            rec["ok_lenient"] = True
        except ValidationError:
            pass
    except Exception as exc:  # any failure (incl. empty choices) is a failed call
        rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
    rec["total_s"] = time.monotonic() - started
    return rec


async def health_rtts(base_url: str, *, n: int) -> list[float]:
    """n sequential GET /health round trips: the network share of every TTFT number."""
    url = base_url.rstrip("/").removesuffix("/v1") + "/health"

    def one() -> float:
        t = time.monotonic()
        with urllib.request.urlopen(url, timeout=30) as resp:  # our own server
            resp.read()
        return time.monotonic() - t

    return [await asyncio.to_thread(one) for _ in range(n)]


async def wait_healthy(base_url: str, timeout_s: float = 20 * 60) -> float:
    """Seconds until GET <root>/health answers 200 (vLLM and llama-server both serve /health)."""
    url = base_url.rstrip("/").removesuffix("/v1") + "/health"

    def ok() -> bool:
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:  # our own server
                return bool(resp.status == 200)
        except (urllib.error.URLError, OSError):
            return False

    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        if await asyncio.to_thread(ok):
            return time.monotonic() - started
        await asyncio.sleep(2)
    raise SystemExit(f"server not healthy after {timeout_s:.0f} s")


# --- the run -------------------------------------------------------------------------------------


async def run(args: argparse.Namespace, record: dict[str, Any]) -> None:
    turns, scenes, meta = build_turns(args.turns)
    key = os.environ.get("EVON_API_KEY")
    if not key:
        raise SystemExit("set EVON_API_KEY (the key the server was started with)")
    client = AsyncOpenAI(base_url=args.base_url, api_key=key, max_retries=0, timeout=300)
    record["meta"] = {**meta, "label": args.label, "model": args.model,
                              "server": args.base_url.split("//")[-1].split("/")[0],
                              "started_ist": dt.datetime.now(IST).isoformat(timespec="seconds"),
                              "git_commit": git_commit()}  # fmt: skip

    healthy_after = await wait_healthy(args.base_url)
    record["cold_start_s"] = healthy_after if args.cold_start else None
    print(
        f"[spike] healthy after {healthy_after:.1f} s (cold start: {record['cold_start_s']})",
        flush=True,
    )
    record["health_rtt_s"] = await health_rtts(args.base_url, n=10)
    print(
        f"[spike] network RTT p50 {percentile(record['health_rtt_s'], 50):.3f} s",
        flush=True,
    )
    warm = await stream_turn(client, args.model, turns[0]["messages"], {})
    print(f"[spike] warm-up done in {warm['total_s']:.1f} s (excluded)", flush=True)

    # Prefix cache: a nonce at the very start makes the first request uncacheable; the identical
    # second request can reuse the whole prompt if prefix caching works for this architecture.
    fresh = [dict(m) for m in turns[5]["messages"]]
    fresh[0]["content"] = f"[probe {secrets.token_hex(8)}]\n" + fresh[0]["content"]
    first = await stream_turn(client, args.model, fresh, THINKING_VARIANTS["chat_template_kwargs"])
    second = await stream_turn(client, args.model, fresh, THINKING_VARIANTS["chat_template_kwargs"])
    record["prefix_probe"] = {"uncached_ttft_s": first["ttft_s"], "cached_ttft_s": second["ttft_s"]}
    print(f"[spike] prefix probe: {record['prefix_probe']}", flush=True)

    probe: list[dict[str, Any]] = []
    for name, extra in THINKING_VARIANTS.items():
        for t in turns[:3]:
            r = await stream_turn(client, args.model, t["messages"], extra)
            leaked = thinking_leaked(r)
            probe.append({"variant": name, "turn": t["id"], "leaked": leaked,
                          "reasoning_chars": r["reasoning_chars"],
                          "error": r["error"]})  # fmt: skip
    ok_variants = [n for n in THINKING_VARIANTS
                   if all(not p["leaked"] and not p["error"]
                          for p in probe if p["variant"] == n)]  # fmt: skip
    record["thinking_probe"] = probe
    record["thinking_off_variant"] = ok_variants[0] if ok_variants else None
    extra = THINKING_VARIANTS[record["thinking_off_variant"] or "chat_template_kwargs"]
    print(f"[spike] thinking off via: {record['thinking_off_variant']}", flush=True)

    record["turns"] = []
    for t in turns:
        r = await stream_turn(client, args.model, t["messages"], extra)
        keep = {k: t[k] for k in ("id", "scene", "turn", "prompt_sha", "brief", "trap_fired",
                                  "reveal_now", "agent")}  # fmt: skip
        record["turns"].append({**keep, **r})
        ttft = None if r["ttft_s"] is None else round(r["ttft_s"], 3)
        print(f"[spike] {t['id']:<22} ttft={ttft} "
              f"state={'ok' if not r['state_errors'] else r['state_errors']} "
              f"{r['speech'][:70]!r}", flush=True)  # fmt: skip

    jobs = [s for s in scenes for _ in range(JUDGE_REPEATS)]
    sem = asyncio.Semaphore(5)

    async def one(scene: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            return await judge_call(client, args.model, scene)

    record["judge"] = list(await asyncio.gather(*(one(s) for s in jobs)))
    print(f"[spike] judge valid: {sum(j['ok'] for j in record['judge'])}/{len(jobs)}", flush=True)

    started = time.monotonic()
    conc = await asyncio.gather(*(stream_turn(client, args.model, t["messages"], extra)
                                  for t in turns[:CONCURRENT_N]))  # fmt: skip
    wall = time.monotonic() - started
    toks = [c["completion_tokens"] for c in conc]
    record["concurrency"] = {
        "n": CONCURRENT_N, "wall_s": wall,
        "completion_tokens": None if None in toks else sum(t for t in toks if t is not None),
        "ttft_s": [c["ttft_s"] for c in conc if c["ttft_s"] is not None],
        "errors": [c["error"] for c in conc if c["error"]],
    }  # fmt: skip


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True)  # fmt: skip
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def dry_run(script: Path) -> None:
    turns, scenes, meta = build_turns(script)
    print(f"[dry-run] {meta}")
    for s in scenes:
        print(
            f"[dry-run] scene {s['id']} ({s['difficulty']}): traps {s['traps']} "
            f"clock {s['sim_clock']}"
        )
    for t in turns:
        first = t["brief"].splitlines()[1]
        chars = sum(len(m["content"]) for m in t["messages"])
        flags = " ".join(x for x in (f"trap={t['trap_fired']}" if t["trap_fired"] else "",
                                     "REVEAL" if t["reveal_now"] else "") if x)  # fmt: skip
        print(f"[dry-run] {t['id']:<22} {t['prompt_sha'][:12]} {chars:>5} chars | {first} {flags}")
    print(f"[dry-run] {len(turns)} turns, {len(scenes)} judge transcripts")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evon spike runner (see module docstring)")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL ending in /v1")
    parser.add_argument("--label", help="run label, e.g. bf16, q8, fp8")
    parser.add_argument("--model", default="evon", help="served model name (default evon)")
    parser.add_argument("--turns", type=Path, default=HERE / "turns.yaml")
    parser.add_argument("--cold-start", action="store_true", help="measure time to /health 200")
    parser.add_argument("--dry-run", action="store_true", help="build prompts only; no network")
    args = parser.parse_args()
    if args.dry_run:
        dry_run(args.turns)
        return
    if not args.base_url or not args.label:
        parser.error("--base-url and --label are required (or use --dry-run)")
    record: dict[str, Any] = {"complete": False}
    try:
        asyncio.run(run(args, record))
        record["complete"] = True
    finally:  # a crash or Ctrl-C still writes what was measured, marked incomplete
        write_run(args.label, record)


def write_run(label: str, record: dict[str, Any]) -> None:
    record.setdefault("turns", [])
    record.setdefault("judge", [])
    record.setdefault("meta", {"label": label, "git_commit": git_commit(), "started_ist": "?"})
    record["summary"] = summarise(record)
    RUNS.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(IST).strftime("%Y%m%d-%H%M")
    tag = "" if record["complete"] else "-INCOMPLETE"
    out = RUNS / f"evon-spike-{label}-{stamp}{tag}.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str) + "\n")
    md = render_markdown(record["summary"], label=label + tag)
    md += f"\nRun file: `{out.relative_to(ROOT)}` · git {record['meta']['git_commit']} · " \
          f"{record['meta']['started_ist']}\n"  # fmt: skip
    out.with_suffix(".md").write_text(md)
    print(md)
    print(f"[spike] wrote {out.relative_to(ROOT)} and .md")


if __name__ == "__main__":
    sys.exit(main())
