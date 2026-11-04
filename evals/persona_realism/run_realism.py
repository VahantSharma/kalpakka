"""Persona realism eval, real run (docs/context/09): 20 scripted turns x N runs per persona.

Run from backend/ with LLM_BASE_URL, LLM_API_KEY, LLM_MODEL set:
    uv run --frozen --offline python ../evals/persona_realism/run_realism.py --runs 10
Targets (09): 0 leaks, 0 character breaks (AI mention, style break), caving < 1 in 10 runs.
Writes every reply and its problems to evals/runs/realism-<time>.json (git-ignored) with spec,
persona and prompt versions. Word-list checks miss paraphrase; read some transcripts too.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

from kalpakka.arena import load_arena_content
from kalpakka.clock import SystemClock
from kalpakka.config import Settings
from kalpakka.llm import ChatTemplateThinkingAdapter, LLMClient, PlainAdapter
from kalpakka.realism import load_realism_spec, play_spec, summarise

ROOT = Path(__file__).resolve().parents[2]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TARGET_ZERO = ("leak", "ai_mention", "style_break")


async def main_async(args: argparse.Namespace) -> int:
    content = load_arena_content(ROOT / "content")
    adapter = ChatTemplateThinkingAdapter() if args.adapter == "chat-template" else PlainAdapter()
    llm = LLMClient.from_settings(Settings(), adapter)
    started = dt.datetime.now(IST)
    results: dict[str, object] = {}
    met_all = True
    for pid in args.personas:
        spec = load_realism_spec(ROOT / "evals/persona_realism" / f"{pid}.yaml")
        runs = [
            await play_spec(spec, content=content, llm=llm, seed=seed, clock=SystemClock(),
                            sim_date=started.date(), difficulty=args.difficulty)  # fmt: skip
            for seed in range(args.runs)
        ]
        summary = summarise(runs)
        met = all(summary[p].count == 0 for p in TARGET_ZERO) and (
            summary["caved"].count * 10 < len(runs)
        )
        met_all &= met
        print(f"{pid} (spec v{spec.version}, persona v{content.personas[pid].version}, "
              f"prompt v{content.borrower_system.version}) · {len(runs)} runs · "
              f"targets {'MET' if met else 'NOT MET'}")  # fmt: skip
        for problem, rate in summary.items():
            print(f"  {problem:<15} runs with it: {rate.text}")
        results[pid] = {
            "spec_version": spec.version,
            "persona_version": content.personas[pid].version,
            "summary": {k: v.model_dump() for k, v in summary.items()},
            "targets_met": met,
            "runs": [[t.model_dump() for t in run] for run in runs],
        }
    out = ROOT / "evals/runs" / f"realism-{started:%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"run_at": started.isoformat(), "difficulty": args.difficulty,
                               "prompt_version": content.borrower_system.version,
                               "personas": results}, ensure_ascii=False, indent=2),
                   encoding="utf-8")  # fmt: skip
    print(f"written: {out.relative_to(ROOT)}")
    return 0 if met_all else 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser()
    parser.add_argument("--personas", nargs="+", default=["ramesh", "lakshmi", "debu"])
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard"], default="medium")
    parser.add_argument("--adapter", choices=["chat-template", "plain"], default="chat-template")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
