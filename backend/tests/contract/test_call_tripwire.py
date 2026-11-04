"""One tripwire per call: instant layers per segment, background LLM review, a flag ledger."""

import asyncio
import datetime as dt
import json
from pathlib import Path

from kalpakka.borrower_state import BorrowerEngine, CallSetup
from kalpakka.brain import load_prompt
from kalpakka.call_tripwire import CallTripwire
from kalpakka.judge import TranscriptLine
from kalpakka.llm import LLMClient, PlainAdapter
from kalpakka.llm_tripwire import LLMTripwire
from kalpakka.state_tripwire import load_state_terms
from kalpakka.tripwire import LexiconTripwire, load_catalogue, load_lexicon
from tests.contract.fakes.fake_openai import FakeOpenAI, Script
from tests.factories import make_persona

CONTENT = Path(__file__).resolve().parents[3] / "content"
CATALOGUE = load_catalogue(CONTENT / "rules/catalogue.yaml")


class Clock:
    def monotonic(self) -> float:
        return 0.0


def _setup(
    fake: FakeOpenAI | None, trap: str = "late_clock"
) -> tuple[CallTripwire, BorrowerEngine]:
    persona = make_persona(traps_allowed=[trap])
    engine = BorrowerEngine(
        CallSetup(persona=persona, difficulty="medium", seed=1, sim_date=dt.date(2026, 11, 5)),
        Clock(),
    )
    llm_layer = None
    if fake is not None:
        llm = LLMClient(base_url=fake.base_url, api_key="k", model="m", adapter=PlainAdapter())
        llm_layer = LLMTripwire(llm, load_prompt(CONTENT / "prompts/tripwire-check.md"), CATALOGUE)
    wire = CallTripwire(
        lexicon=LexiconTripwire(load_lexicon(CONTENT / "rules/lexicon.yaml", CATALOGUE)),
        terms=load_state_terms(CONTENT / "rules/state_terms.yaml"),
        persona=persona,
        llm_layer=llm_layer,
    )
    return wire, engine


def test_instant_layers_run_together_and_feed_the_ledger() -> None:
    wire, engine = _setup(None)  # sim clock 19:20

    flags = wire.on_segment("EMI overdue hai, police bhej dunga", segment=0, t=3.0, engine=engine)

    assert {(f.rule, f.layer, f.status) for f in flags} == {
        ("threat", "lexicon", "possible"),
        ("late_clock", "state", "confirmed"),
    }
    assert wire.confirmed_rules(turn=1) == frozenset({"late_clock"})
    assert [f.rule for f in wire.flags] == ["threat", "late_clock"]


async def test_background_review_updates_the_ledger_in_place(fake_llm: FakeOpenAI) -> None:
    wire, engine = _setup(fake_llm)
    flags = wire.on_segment("Paisa do warna police bhej dunga", segment=0, t=3.0, engine=engine)
    fake_llm.scripts.append(Script(pieces=[json.dumps(
        {"violation": True, "rule": "threat", "confidence": 0.9, "quote": "police bhej dunga"}
    )]))  # fmt: skip

    changed = await asyncio.wait_for(
        wire.review("Paisa do warna police bhej dunga", segment=0, t=3.0, turn=1,
                    context=[], lexicon_flags=flags),
        timeout=10,
    )  # fmt: skip

    assert [(f.rule, f.status) for f in changed] == [("threat", "confirmed")]
    assert {f.id: f.status for f in wire.flags}["t1-s0-threat"] == "confirmed"
    assert len(wire.flags) == len(flags)  # updated in place, not appended
    assert wire.confirmed_rules(turn=1) == frozenset({"threat", "late_clock"})


async def test_review_never_overturns_a_state_rule(fake_llm: FakeOpenAI) -> None:
    wire, engine = _setup(fake_llm)
    flags = wire.on_segment("EMI overdue hai", segment=0, t=1.0, engine=engine)
    fake_llm.scripts.append(Script(pieces=[json.dumps(
        {"violation": False, "rule": None, "confidence": 0.95, "quote": ""}
    )]))  # fmt: skip

    await wire.review("EMI overdue hai", segment=0, t=1.0, turn=1, context=[],
                      lexicon_flags=flags)  # fmt: skip

    assert wire.confirmed_rules(turn=1) == frozenset({"late_clock"})


async def test_without_an_llm_layer_review_is_a_no_op() -> None:
    wire, engine = _setup(None)
    flags = wire.on_segment("police bhej dunga", segment=0, t=1.0, engine=engine)

    changed = await wire.review("police bhej dunga", segment=0, t=1.0, turn=1,
                                context=[TranscriptLine(turn=1, role="agent", text="x", t=0)],
                                lexicon_flags=flags)  # fmt: skip

    assert changed == []
    assert {f.status for f in wire.flags if f.rule == "threat"} == {"possible"}
