"""Persona realism eval end to end on the fake server: scripted agent turns -> scored replies."""

import datetime as dt
from pathlib import Path

from kalpakka.arena import load_arena_content
from kalpakka.brain import DELIMITER
from kalpakka.clock import SystemClock
from kalpakka.llm import LLMClient, PlainAdapter
from kalpakka.realism import RealismSpec, play_spec, summarise
from tests.contract.fakes.fake_openai import FakeOpenAI, Script

ROOT = Path(__file__).resolve().parents[3]
SPEC = RealismSpec(
    persona="ramesh",
    version=1,
    leak_terms=["engine", "repair"],
    means_terms=["4000", "chaar hazaar"],
    style_markers=["hai", "ji", "haan", "ka", "mein", "kaun"],
    turns=["Namaste Ramesh ji", "Kya pareshani hai?", "Aap robot ho?"],
)


def _reply(speech: str) -> Script:
    return Script(pieces=[speech, f"\n{DELIMITER}\n", '{"mood_delta": 0, "trust_delta": 0}'])


async def test_a_scripted_run_scores_every_reply(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts += [
        _reply("Haan ji, kaun hai?"),
        _reply("Auto ka engine repair mein hai."),  # trust 25 < gate 60: a leak
        _reply("As an AI I cannot say."),
    ]
    llm = LLMClient(base_url=fake_llm.base_url, api_key="k", model="m", adapter=PlainAdapter())

    scores = await play_spec(
        SPEC, content=load_arena_content(ROOT / "content"), llm=llm, seed=1,
        clock=SystemClock(), sim_date=dt.date(2026, 11, 5),
    )  # fmt: skip

    assert [s.problems for s in scores] == [[], ["leak"], ["ai_mention", "style_break"]]
    assert summarise([scores])["leak"].text == "1 of 1 (100%)"
    # no traps in a realism run: the brief never fires one
    assert all("THIS TURN" not in r["messages"][-1]["content"] for r in fake_llm.requests)
