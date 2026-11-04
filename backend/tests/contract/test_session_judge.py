"""CallSession scoring a finished call: scorecard message + stored card, or "scoring failed"
with the flags (never an invented score)."""

import json
from pathlib import Path

import pytest

from kalpakka.brain import load_prompt
from kalpakka.call_tripwire import CallTripwire
from kalpakka.db import Database
from kalpakka.llm import LLMClient, PlainAdapter
from kalpakka.scorer import JudgeScorer
from kalpakka.state_tripwire import load_state_terms
from kalpakka.tripwire import LexiconTripwire, load_catalogue, load_lexicon
from tests.contract.fakes.fake_openai import FakeOpenAI, Script
from tests.contract.test_session import Harness

CONTENT = Path(__file__).resolve().parents[3] / "content"
CATALOGUE = load_catalogue(CONTENT / "rules/catalogue.yaml")
LEXICON = LexiconTripwire(load_lexicon(CONTENT / "rules/lexicon.yaml", CATALOGUE))
TERMS = load_state_terms(CONTENT / "rules/state_terms.yaml")
JUDGE_JSON = {
    "checks": [{"id": "stated_amount", "evidence": [{"turn": 1, "quote": "12,500"}],
                "answer": "yes"}],
    "violations": [],
    "worst_line": None,
}  # fmt: skip


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    return await Database.open(tmp_path / "k.db")


async def judged_call(db: Database, fake: FakeOpenAI, pieces: list[str]) -> Harness:
    h = Harness(db)
    llm = LLMClient(base_url=fake.base_url, api_key="k", model="m", adapter=PlainAdapter())
    h.deps.scorer = JudgeScorer(
        llm, load_prompt(CONTENT / "prompts/judge-system.md"), catalogue=CATALOGUE, tripwire=LEXICON
    )
    h.stt.transcripts = ["Paisa do warna police bhej dunga, ₹12,500 abhi"]
    h.deps.state_terms = TERMS
    h.deps.tripwire_factory = lambda p: CallTripwire(lexicon=LEXICON, terms=TERMS, persona=p)
    fake.scripts.append(Script(pieces=pieces))
    h.start()
    await h.transport.wait_for("audio_end")
    h.speak()
    await h.transport.wait_for("mood")
    h.transport.send_json(type="end_call")
    await h.finish()
    return h


async def test_a_finished_call_is_scored_sent_and_stored(
    db: Database, fake_llm: FakeOpenAI
) -> None:
    h = await judged_call(db, fake_llm, [json.dumps(JUDGE_JSON)])

    [msg] = h.transport.messages("scorecard")
    stored = await db.scorecard("call-1")
    assert stored == msg["scorecard"]
    assert {"total", "sections", "checks", "compliance", "flags"} <= set(stored)
    user = fake_llm.requests[-1]["messages"][1]["content"]
    assert "12,500" in user  # the judge saw the transcript of this call


async def test_a_failed_judge_sends_scoring_failed_with_flags_and_no_score(
    db: Database, fake_llm: FakeOpenAI
) -> None:
    h = await judged_call(db, fake_llm, ["this is not json"])

    assert h.transport.messages("scorecard") == []
    [err] = [m for m in h.transport.messages("error") if m["code"] == "scoring_failed"]
    assert err["message"] == "scoring failed"
    assert any(f["rule"] == "threat" for f in err["flags"])
    stored = await db.scorecard("call-1")
    assert stored is not None and stored["error"] == "scoring_failed" and "total" not in stored
