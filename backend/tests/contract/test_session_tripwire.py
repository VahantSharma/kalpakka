"""CallSession with the three-layer tripwire: flags to the browser, background LLM review,
and the respect markers that let the borrower tell the truth."""

import asyncio
import json
from pathlib import Path

import pytest

from kalpakka.brain import load_prompt
from kalpakka.call_tripwire import CallTripwire
from kalpakka.db import Database
from kalpakka.llm import LLMClient, PlainAdapter
from kalpakka.llm_tripwire import LLMTripwire
from kalpakka.personas import Persona
from kalpakka.state_tripwire import load_state_terms
from kalpakka.tripwire import LexiconTripwire, load_catalogue, load_lexicon
from tests.contract.fakes.fake_openai import FakeOpenAI, Script
from tests.contract.test_session import Harness
from tests.factories import make_persona

CONTENT = Path(__file__).resolve().parents[3] / "content"
CATALOGUE = load_catalogue(CONTENT / "rules/catalogue.yaml")
TERMS = load_state_terms(CONTENT / "rules/state_terms.yaml")
THREAT = "Paisa do warna police bhej dunga"


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    return await Database.open(tmp_path / "k.db")


def wire_up(h: Harness, persona: Persona, fake: FakeOpenAI, transcript: str) -> None:
    llm = LLMClient(base_url=fake.base_url, api_key="k", model="m", adapter=PlainAdapter())
    layer = LLMTripwire(llm, load_prompt(CONTENT / "prompts/tripwire-check.md"), CATALOGUE)
    lexicon = LexiconTripwire(load_lexicon(CONTENT / "rules/lexicon.yaml", CATALOGUE))
    h.deps.personas = {"ramesh": persona}
    h.deps.state_terms = TERMS
    h.deps.tripwire_factory = lambda p: CallTripwire(
        lexicon=lexicon, terms=TERMS, persona=p, llm_layer=layer
    )
    h.stt.transcripts = [transcript]


async def threat_call(db: Database, fake: FakeOpenAI, *, confirm: bool) -> tuple[Harness, int]:
    h = Harness(db)
    wire_up(h, make_persona(traps_allowed=["late_clock"]), fake, THREAT)
    verdict = {"violation": confirm, "rule": "threat", "confidence": 0.9, "quote": "police"}
    fake.scripts.append(Script(pieces=[json.dumps(verdict)]))
    h.start()
    await h.transport.wait_for("audio_end")
    h.speak()
    await h.transport.wait_for("mood")
    h.transport.send_json(type="end_call")
    await h.finish()
    assert h.session._engine is not None
    return h, h.session._engine.state.mood


async def test_threat_goes_possible_then_confirmed_and_raises_mood(
    db: Database, fake_llm: FakeOpenAI, tmp_path: Path
) -> None:
    h, mood = await threat_call(db, fake_llm, confirm=True)

    first = [m for m in h.transport.messages("flag") if m["flag"]["rule"] == "threat"]
    assert [m["flag"]["status"] for m in first] == ["possible"]
    updates = h.transport.messages("flag_update")
    assert [(m["flag"]["id"], m["flag"]["status"]) for m in updates] == [
        (first[0]["flag"]["id"], "confirmed")
    ]
    _, baseline = await threat_call(await Database.open(tmp_path / "b.db"), fake_llm, confirm=False)
    assert mood > baseline  # the confirmed threat moved the borrower, not just the UI


async def test_open_question_with_respect_lets_the_borrower_tell_the_truth(
    db: Database, fake_llm: FakeOpenAI
) -> None:
    h = Harness(db)
    persona = make_persona(temperament={"starting_trust": 60})
    wire_up(h, persona, fake_llm, "Ramesh ji, aap theek hain? Kya problem hai, bataiye.")
    h.start()
    await h.transport.wait_for("audio_end")
    h.speak()
    await h.transport.wait_for("mood")
    h.transport.send_json(type="end_call")
    await h.finish()

    brief = " ".join(str(m["content"]) for m in h.replies.requests[0])
    assert "Tell them the truth now" in brief
    assert h.session._engine is not None and h.session._engine.state.revealed
    await asyncio.sleep(0)
