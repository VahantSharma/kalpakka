"""Persona realism eval, scoring half (docs/context/09 "Persona realism").

Each borrower reply in a scripted call is checked in code for the six failures 09 names: leaked
the secret, caved before the gate, mentioned being an AI, broke the language style, used a
non-official audio tag, replied too long (plus an empty reply). Targets: 0 leaks, 0 character
breaks, caving < 1 in 10 runs. A run "has" a problem if any of its replies has it.

The per-persona data (`evals/persona_realism/<id>.yaml`) holds the 20 scripted agent turns and
the words that betray the hidden truth (`leak_terms`), the real means (`means_terms`) and the
language mix (`style_markers`). The checks are word lists, so they miss paraphrase: a leak with
none of the listed words passes. That is a limitation to state with every result.
"""

import datetime as dt
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from kalpakka.arena import ArenaContent, Rate, case_persona
from kalpakka.borrower_state import CallSetup
from kalpakka.clock import Clock
from kalpakka.llm import LLMClient
from kalpakka.personas import Persona
from kalpakka.tags import TAG_PATTERN, unofficial_tags
from kalpakka.text_call import TextCall, TextCallConfig
from kalpakka.tripwire import compile_pattern, normalise

Problem = Literal[
    "leak", "caved", "ai_mention", "style_break", "unofficial_tag", "too_long", "empty"
]
PROBLEMS: Final[tuple[Problem, ...]] = (
    "leak", "caved", "ai_mention", "style_break", "unofficial_tag", "too_long", "empty"
)  # fmt: skip
MAX_SENTENCES: Final = 3  # 06: 1-3 short sentences
STYLE_MIN_WORDS: Final = 5  # "Okay sir." says nothing about the language mix
AI_MENTION: Final = compile_pattern(
    r"{b}(ai|a\.i\.|एआई|artificial\s*intelligence|language\s*model|chat\s*bot|chatbot"
    r"|virtual\s*assistant|simulation|simulated|i\s*am\s*a\s*bot|main\s*(ek\s*)?bot)"
)
_SENTENCE_END = re.compile(r"[.?!।…]+(?=\s|$)")


class RealismSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    persona: str
    version: Annotated[int, Field(ge=1)]
    leak_terms: Annotated[list[str], Field(min_length=1)]
    means_terms: Annotated[list[str], Field(min_length=1)]
    style_markers: Annotated[list[str], Field(min_length=1)]
    turns: Annotated[list[str], Field(min_length=1)]


def load_realism_spec(path: Path) -> RealismSpec:
    return RealismSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class TurnScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    turn: int
    problems: list[Problem]
    speech: str = ""


def _any_word(words: Sequence[str], text: str) -> bool:
    return any(compile_pattern(r"{b}" + re.escape(w) + r"{b}").search(text) for w in words)


def score_turn(
    spec: RealismSpec,
    persona: Persona,
    raw_speech: str,
    *,
    turn: int,
    gate_open: bool,
    revealed: bool,
    reveal_now: bool,
) -> TurnScore:
    """`gate_open`: the reveal gate was open this turn; `revealed`: the truth was already out
    before this reply; `reveal_now`: this reply was briefed to tell the truth."""
    text = normalise(raw_speech)
    spoken = normalise(TAG_PATTERN.sub(" ", text))
    words = spoken.split()
    problems: list[Problem] = []
    truth_allowed = revealed or reveal_now
    if not words:
        problems.append("empty")
    # Once the gate is open the brief lets the truth out if asked; a question code failed to
    # recognise is not the persona's leak.
    if not truth_allowed and not gate_open and _any_word(spec.leak_terms, spoken):
        problems.append("leak")
    if not truth_allowed and not gate_open and _any_word(spec.means_terms, spoken):
        problems.append("caved")
    if AI_MENTION.search(spoken):
        problems.append("ai_mention")
    if len(words) >= STYLE_MIN_WORDS and not _any_word(spec.style_markers, spoken):
        problems.append("style_break")
    if unofficial_tags(text):
        problems.append("unofficial_tag")
    sentences = [p for p in _SENTENCE_END.split(spoken) if p.strip()]
    if len(sentences) > MAX_SENTENCES:
        problems.append("too_long")
    return TurnScore(turn=turn, problems=problems, speech=raw_speech)


def summarise(runs: Sequence[Sequence[TurnScore]]) -> dict[Problem, Rate]:
    """Per problem: how many runs had it at least once."""
    return {
        p: Rate(count=sum(any(p in t.problems for t in run) for run in runs), of=len(runs))
        for p in PROBLEMS
    }


async def play_spec(
    spec: RealismSpec,
    *,
    content: ArenaContent,
    llm: LLMClient,
    seed: int,
    clock: Clock,
    sim_date: dt.date,
    difficulty: Literal["easy", "medium", "hard"] = "medium",
    config: TextCallConfig | None = None,
) -> list[TurnScore]:
    """One scripted run: the spec's agent turns in order, each borrower reply scored. No traps
    (realism, not trap handling); stops early if the borrower hangs up."""
    persona = case_persona(content.personas[spec.persona], "none")
    call = TextCall(
        setup=CallSetup(persona=persona, difficulty=difficulty, seed=seed, sim_date=sim_date),
        llm=llm,
        system=content.borrower_system,
        lexicon=content.lexicon,
        terms=content.terms,
        clock=clock,
        config=config,
    )
    scores: list[TurnScore] = []
    for line in spec.turns:
        revealed_before = call.engine.state.revealed
        result = await call.agent_turn(line)
        # With no traps, the engine hands out an excuse exactly when the gate is closed and the
        # truth is not out yet (borrower_state.begin_turn).
        gate_open = result.brief.excuse is None
        scores.append(
            score_turn(
                spec,
                persona,
                result.raw_speech,
                turn=result.turn,
                gate_open=gate_open,
                revealed=revealed_before,
                reveal_now=result.brief.reveal_now,
            )
        )
        if result.state.call_over is not None:
            break
    return scores
