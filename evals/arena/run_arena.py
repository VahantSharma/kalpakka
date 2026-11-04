"""Arena text mode, real run: Bot A and Bot B against the borrower engine (docs/context/07).

Run from backend/ with LLM_BASE_URL, LLM_API_KEY, LLM_MODEL set:
    uv run --frozen --offline python ../evals/arena/run_arena.py --seeds 10 --concurrency 4
Full matrix: 2 bots x 3 personas x 4 trap settings x 10 seeds = 240 calls. A smaller smoke run:
    ... run_arena.py --seeds 1 --personas ramesh
Writes report + every call (transcript, flags) to evals/runs/arena-<time>.json (git-ignored) with
prompt and persona versions; prints the report with counts and percentages. Results are only real
when run against a real model; never paste numbers from a fake run as findings.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from functools import partial
from pathlib import Path

from kalpakka.arena import build_matrix, build_report, load_arena_content, play_call, run_matrix
from kalpakka.clock import SystemClock
from kalpakka.config import Settings
from kalpakka.judge_runner import JsonSchemaAdapter
from kalpakka.llm import ChatTemplateThinkingAdapter, LLMClient, PlainAdapter

ROOT = Path(__file__).resolve().parents[2]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


async def main_async(args: argparse.Namespace) -> int:
    content = load_arena_content(ROOT / "content")
    adapter = ChatTemplateThinkingAdapter() if args.adapter == "chat-template" else PlainAdapter()
    settings = Settings()
    llm = LLMClient.from_settings(settings, adapter)
    judge_llm = None
    if args.judge:  # thinking on + json_schema; a longer timeout for the post-call judge
        judge_llm = LLMClient(
            base_url=settings.llm_base_url, api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model, adapter=JsonSchemaAdapter(adapter), timeout_s=180.0,
        )  # fmt: skip
    cases = build_matrix(args.bots, args.personas, range(args.seeds))
    sim_date = dt.datetime.now(IST).date()
    started = dt.datetime.now(IST)
    play = partial(play_call, content=content, bot_llm=llm, borrower_llm=llm,
                   clock=SystemClock(), sim_date=sim_date, judge_llm=judge_llm)  # fmt: skip
    records = await run_matrix(cases, play, concurrency=args.concurrency)
    report = build_report(records)

    for bot, r in report.items():
        print(f"Bot {bot} (prompt v{content.bots[bot].version}) · {r.calls} calls · {r.errors} errors")
        for rule, rate in r.violation_rate.items():
            print(f"  violation {rule:<24} {rate.text}")
        for trap, rate in r.trap_pass_rate.items():
            print(f"  trap pass {trap:<24} {rate.text} (not reached: {r.trap_not_reached[trap]})")
        print(f"  promise {r.promise_rate.text} · valid promise {r.valid_promise_rate.text} · "
              f"angry hang-up {r.angry_hangup_rate.text} · avg turns {r.average_turns}")
    print("limitation:", next(iter(report.values())).limitation if report else "")

    out = ROOT / "evals/runs" / f"arena-{started:%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "run_at": started.isoformat(),
        "finished_at": dt.datetime.now(IST).isoformat(),
        "sim_date": sim_date.isoformat(),
        "bot_prompt_versions": {b: content.bots[b].version for b in args.bots},
        "judge_prompt_version": content.judge_prompt.version if args.judge else None,
        "borrower_prompt_version": content.borrower_system.version,
        "persona_versions": {p: content.personas[p].version for p in args.personas},
        "report": {b: r.model_dump() for b, r in report.items()},
        "calls": [rec.model_dump(mode="json") for rec in records],
    }, ensure_ascii=False, indent=2), encoding="utf-8")  # fmt: skip
    print(f"written: {out.relative_to(ROOT)}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser()
    parser.add_argument("--bots", nargs="+", default=["A", "B"])
    parser.add_argument("--personas", nargs="+", default=["ramesh", "lakshmi", "debu"])
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--no-judge", dest="judge", action="store_false",
                        help="skip the judge: threat/abuse/misleading are then not measured")
    parser.add_argument("--adapter", choices=["chat-template", "plain"], default="chat-template")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
