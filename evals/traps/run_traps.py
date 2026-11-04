"""Trap eval, state-rule half: every violating script flagged, no correct script flagged.

Each script in evals/traps/scripts.yaml runs through the real `BorrowerEngine` for every seed in
the file (the seed moves the trap to a different turn), with both deterministic tripwire layers:
the lexicon and the state rules. The borrower's words are not needed: state rules read the engine,
so the borrower's suggestion is all-zero each turn. Deterministic; no model, no network.

Run from backend/:  uv run --frozen --offline python ../evals/traps/run_traps.py
Writes the full result to evals/runs/ (git-ignored); paste the summary into the log.
"""

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from kalpakka.borrower_state import BorrowerEngine, CallSetup, StateSuggestion, TurnSignals
from kalpakka.personas import Persona, load_persona
from kalpakka.state_tripwire import StateTerms, StateTripwire, confirmed_rules, load_state_terms
from kalpakka.tripwire import LexiconTripwire, load_catalogue, load_lexicon

ROOT = Path(__file__).resolve().parents[2]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SIM_DATE = dt.date(2026, 11, 5)
MAX_TURNS = 14  # the Arena cap (07)


class _Clock:
    def monotonic(self) -> float:
        return 0.0


class ScriptRun(BaseModel):
    script: str
    seed: int
    kind: str
    expect: str | None
    window: tuple[int, int | None]
    flags: list[str]  # "t<turn>:<layer>:<rule>"
    passed: bool


def _persona_for(script: dict[str, Any]) -> Persona:
    persona = load_persona(ROOT / "content/personas" / f"{script['persona']}.yaml")
    if script["trap"] == "reveal":  # no trap; trust starts at the gate so an open question reveals
        temperament = persona.temperament.model_copy(
            update={"starting_trust": persona.reveal_gate("medium")}
        )
        return persona.model_copy(update={"traps_allowed": [], "temperament": temperament})
    return persona.model_copy(update={"traps_allowed": [script["trap"]]})


class Content(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    lexicon: LexiconTripwire
    terms: StateTerms


def load_content() -> Content:
    root = ROOT / "content/rules"
    catalogue = load_catalogue(root / "catalogue.yaml")
    return Content(
        lexicon=LexiconTripwire(load_lexicon(root / "lexicon.yaml", catalogue)),
        terms=load_state_terms(root / "state_terms.yaml"),
    )


def run_script(script: dict[str, Any], seed: int, content: Content) -> ScriptRun:
    lexicon = content.lexicon
    persona = _persona_for(script)
    state_wire = StateTripwire(content.terms, persona)
    engine = BorrowerEngine(
        CallSetup(persona=persona, difficulty="medium", seed=seed, sim_date=SIM_DATE), _Clock()
    )

    reveal_turn = script.get("reveal_on_turn")
    if script["trap"] == "reveal":
        start, until = reveal_turn + 1, reveal_turn + 1
    else:
        [trap] = engine.state.traps
        start, until = trap.active_from, trap.active_until
    last = (until if until is not None else start + len(script["window"]) - 1) + 1

    lead, window = script["lead"], script["window"]
    flags: list[str] = []
    while engine.state.call_over is None and engine.upcoming_turn <= min(last, MAX_TURNS):
        turn = engine.upcoming_turn
        if turn < start:
            text = lead[(turn - 1) % len(lead)]
        elif turn - start < len(window) and (until is None or turn <= until):
            text = window[turn - start]
        else:
            text = script["after"]
        segment_flags = lexicon.check(text, turn=turn, segment=0, t=0.0)
        segment_flags += state_wire.check(text, segment=0, t=0.0, view=engine)
        flags += [f"t{f.turn}:{f.layer}:{f.rule}" for f in segment_flags]
        engine.begin_turn(
            TurnSignals(
                confirmed_violations=confirmed_rules(segment_flags),
                asked_open_question=turn == reveal_turn,
            )
        )
        engine.end_turn(StateSuggestion())

    if script["trap"] == "reveal" and engine.state.revealed_turn != reveal_turn:
        raise RuntimeError(
            f"{script['id']} seed {seed}: borrower did not reveal on turn {reveal_turn}"
        )

    expect = script.get("expect")
    if script["kind"] == "violating":
        in_window = range(start, (until if until is not None else last) + 1)
        passed = any(
            f.endswith(f":state:{expect}") and int(f.split(":")[0][1:]) in in_window
            for f in flags
        )
    else:
        passed = not flags
    return ScriptRun(
        script=script["id"], seed=seed, kind=script["kind"], expect=expect,
        window=(start, until), flags=flags, passed=passed,
    )  # fmt: skip


def run_all() -> tuple[dict[str, Any], list[ScriptRun]]:
    data = yaml.safe_load((ROOT / "evals/traps/scripts.yaml").read_text(encoding="utf-8"))
    content = load_content()
    runs = [run_script(s, seed, content) for s in data["scripts"] for seed in data["seeds"]]
    return data, runs


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Windows console
    data, runs = run_all()
    now = dt.datetime.now(IST)
    print(f"trap eval (state rules) · {now:%Y-%m-%d %H:%M} IST · set v{data['version']}")
    for kind in ("violating", "correct"):
        group = [r for r in runs if r.kind == kind]
        ok = sum(r.passed for r in group)
        print(f"{kind:<10} {ok} of {len(group)} passed ({ok / len(group):.0%})")
    for r in runs:
        if not r.passed:
            print(f"  FAIL {r.script} seed={r.seed} window={r.window} flags={r.flags}")
    out_dir = ROOT / "evals/runs"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"traps-state-{now:%Y%m%d-%H%M%S}.json"
    out.write_text(
        json.dumps(
            {"run_at": now.isoformat(), "set_version": data["version"],
             "runs": [r.model_dump() for r in runs]},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )  # fmt: skip
    print(f"written: {out.relative_to(ROOT)}")
    return 0 if all(r.passed for r in runs) else 1


if __name__ == "__main__":
    sys.exit(main())
