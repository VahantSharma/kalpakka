"""Text CLI (P1.5): type as the recovery agent, read the borrower's replies.

    uv run python -m kalpakka.cli talk ramesh --seed 7 [--difficulty medium] [--debug]

(`uv run kalpakka talk ...` once `[project.scripts] kalpakka = "kalpakka.cli:main"` is added.)
Commands at the prompt: `/quit` ends; `/end <words>` says the words as the closing turn and ends;
an empty line is a silent agent turn. `--debug` prints the hidden state after every turn (local
runs only). The transcript, flags and first-token times are saved as JSON (default `evals/runs/`,
git-ignored) with seed, persona version and prompt version, so a run can be replayed and compared.
"""

import argparse
import asyncio
import datetime as dt
import json
import statistics
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TextIO, get_args

from kalpakka.borrower_state import CallSetup
from kalpakka.brain import FirstTokenTimeout, load_prompt
from kalpakka.clock import SystemClock
from kalpakka.config import REPO_ROOT, Settings
from kalpakka.llm import ChatTemplateThinkingAdapter, LLMClient, LLMError, PlainAdapter
from kalpakka.llm_tripwire import LLMTripwire
from kalpakka.personas import Difficulty, load_personas
from kalpakka.state_tripwire import load_state_terms
from kalpakka.text_call import TextCall, TurnResult
from kalpakka.tripwire import LexiconTripwire, load_catalogue, load_lexicon

CONTENT = REPO_ROOT / "content"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
ADAPTERS: dict[str, type[ChatTemplateThinkingAdapter] | type[PlainAdapter]] = {
    "chat-template": ChatTemplateThinkingAdapter,
    "plain": PlainAdapter,
}


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="kalpakka")
    sub = parser.add_subparsers(dest="command", required=True)
    talk = sub.add_parser("talk", help="talk to a borrower as the agent, in text")
    talk.add_argument("persona", help="persona id, e.g. ramesh, lakshmi, debu")
    talk.add_argument("--seed", type=int, default=7)
    talk.add_argument("--difficulty", choices=get_args(Difficulty), default=None)
    talk.add_argument("--debug", action="store_true", help="print hidden state each turn")
    talk.add_argument(
        "--llm-tripwire", action="store_true", help="add tripwire layer 2 (one LLM check per turn)"
    )
    talk.add_argument("--out", type=Path, default=REPO_ROOT / "evals" / "runs")
    talk.add_argument(
        "--adapter",
        choices=sorted(ADAPTERS),
        default="chat-template",  # turns thinking off on vLLM/SGLang; pending P0.5 findings
    )
    return parser.parse_args(list(argv))


def _flag_lines(result: TurnResult) -> list[str]:
    return [f"  [flag {f.status}: {f.rule}] {f.quote}" for f in result.flags]


def _debug_line(result: TurnResult) -> str:
    s = result.state
    ttft = "n/a" if result.first_token_s is None else f"{result.first_token_s:.3f} s"
    return (
        f"  turn {result.turn} · mood {s.mood} ({s.mood_label}) · trust {s.trust} · "
        f"revealed {'yes' if s.revealed else 'no'} · TTFT {ttft}"
        + (f" · split errors {result.split_errors}" if result.split_errors else "")
    )


def _save(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_talk(
    args: argparse.Namespace, *, llm: LLMClient, lines: Iterator[str], out: TextIO
) -> int:
    personas = load_personas(CONTENT / "personas")
    if args.persona not in personas:
        out.write(f"unknown persona {args.persona!r}; have: {', '.join(sorted(personas))}\n")
        return 2
    persona = personas[args.persona]
    difficulty: Difficulty = args.difficulty or persona.difficulty_default
    catalogue = load_catalogue(CONTENT / "rules/catalogue.yaml")
    system = load_prompt(CONTENT / "prompts/borrower-system.md")
    sim_date = dt.datetime.now(IST).date()
    call = TextCall(
        setup=CallSetup(persona=persona, difficulty=difficulty, seed=args.seed, sim_date=sim_date),
        llm=llm,
        system=system,
        lexicon=LexiconTripwire(load_lexicon(CONTENT / "rules/lexicon.yaml", catalogue)),
        terms=load_state_terms(CONTENT / "rules/state_terms.yaml"),
        clock=SystemClock(),
    )
    if args.llm_tripwire:
        call.enable_llm_tripwire(
            LLMTripwire(llm, load_prompt(CONTENT / "prompts/tripwire-check.md"), catalogue)
        )
    card = persona.public_card()
    out.write(
        f"Calling {card.display_name} ({card.language}, {difficulty}) · {card.loan.lender} "
        f"{card.loan.product} · overdue ₹{card.loan.overdue_amount:,} · {card.loan.dpd} DPD · "
        f"clock {call.engine.state.sim_clock:%H:%M}\n"
        f"{card.display_name}: {persona.lines.pickup}\n"
    )

    ttfts: list[float] = []
    over = None
    while over is None:
        out.write("you> ")
        out.flush()
        line = await asyncio.to_thread(next, lines, None)
        if line is None or line.strip() == "/quit":
            break
        closing = line.startswith("/end")
        text = line[len("/end") :].strip() if closing else line.strip()
        try:
            result = await call.agent_turn(text, closing=closing)
        except (LLMError, FirstTokenTimeout) as exc:
            out.write(f"  [borrower could not reply: {exc}]\n")
            over = call.engine.state.call_over
            if closing:
                break
            continue
        out.write(f"{persona.display_name}: {result.speech}\n")
        for flag_line in _flag_lines(result):
            out.write(flag_line + "\n")
        if args.debug:
            out.write(_debug_line(result) + "\n")
        if result.first_token_s is not None:
            ttfts.append(result.first_token_s)
        over = result.state.call_over
        if closing:
            break
    if over is not None:
        out.write(f"[call over: {over}]\n")
    if ttfts:
        out.write(f"TTFT p50 {statistics.median(ttfts):.3f} s over {len(ttfts)} turns\n")

    now = dt.datetime.now(IST)
    path = args.out / f"talk-{persona.id}-seed{args.seed}-{now:%Y%m%d-%H%M%S}.json"
    record = {
        "run_at": now.isoformat(),
        "persona": persona.id,
        "persona_version": persona.version,
        "prompt_version": system.version,
        "difficulty": difficulty,
        "seed": args.seed,
        "call_over": over,
        "ttft_s": ttfts,
        "transcript": [line.model_dump() for line in call.transcript],
        "flags": [f.model_dump() for f in call.flags],
    }
    await asyncio.to_thread(_save, path, record)
    out.write(f"saved: {path}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Windows console
    settings = Settings()  # values come from the environment
    llm = LLMClient.from_settings(settings, ADAPTERS[args.adapter]())
    return asyncio.run(run_talk(args, llm=llm, lines=iter(sys.stdin.readline, ""), out=sys.stdout))


if __name__ == "__main__":
    sys.exit(main())
