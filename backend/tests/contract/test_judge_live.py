"""Judge live half on the fake server: a finished text call -> judge request -> scorecard."""

import datetime as dt
import json
from pathlib import Path

from kalpakka.borrower_state import CallSetup
from kalpakka.brain import DELIMITER, load_prompt
from kalpakka.clock import SystemClock
from kalpakka.judge_runner import JsonSchemaAdapter, run_judge
from kalpakka.llm import ChatTemplateThinkingAdapter, LLMClient, PlainAdapter
from kalpakka.state_tripwire import load_state_terms
from kalpakka.text_call import TextCall
from kalpakka.tripwire import LexiconTripwire, load_catalogue, load_lexicon
from tests.contract.fakes.fake_openai import FakeOpenAI, Script
from tests.factories import make_persona

CONTENT = Path(__file__).resolve().parents[3] / "content"
CATALOGUE = load_catalogue(CONTENT / "rules/catalogue.yaml")
LEXICON = LexiconTripwire(load_lexicon(CONTENT / "rules/lexicon.yaml", CATALOGUE))
PROMPT = load_prompt(CONTENT / "prompts/judge-system.md")


def _reply(speech: str) -> Script:
    return Script(pieces=[speech, f"\n{DELIMITER}\n", '{"mood_delta": 0, "trust_delta": 0}'])


async def _finished_call(fake: FakeOpenAI) -> TextCall:
    fake.scripts += [_reply("Haan ji, boliye. "), _reply("Itni raat ko? ")]
    call = TextCall(
        setup=CallSetup(persona=make_persona(traps_allowed=["late_clock"]), difficulty="medium",
                        seed=1, sim_date=dt.date(2026, 11, 5)),
        llm=LLMClient(base_url=fake.base_url, api_key="k", model="m", adapter=PlainAdapter()),
        system=load_prompt(CONTENT / "prompts/borrower-system.md"),
        lexicon=LEXICON,
        terms=load_state_terms(CONTENT / "rules/state_terms.yaml"),
        clock=SystemClock(),
    )  # fmt: skip
    await call.agent_turn("Namaste, main SwiftFin se Priya bol rahi hoon.")
    await call.agent_turn("Aapka overdue 12,500 rupaye hai, aaj payment kar dijiye.")
    return call


def _judge_llm(fake: FakeOpenAI) -> LLMClient:
    adapter = JsonSchemaAdapter(ChatTemplateThinkingAdapter())
    return LLMClient(base_url=fake.base_url, api_key="k", model="m", adapter=adapter)


JUDGE_JSON = {
    "checks": [
        {"id": "identified_self_and_lender",
         "evidence": [{"turn": 1, "quote": "main SwiftFin se Priya bol rahi hoon"}],
         "answer": "yes"},
        {"id": "stated_amount",
         "evidence": [{"turn": 2, "quote": "Aapka overdue 12,500 rupaye hai"}], "answer": "yes"},
        {"id": "asked_open_question", "evidence": [], "answer": "no"},
    ],
    "violations": [{"rule": "late_clock", "turn": 2, "quote": "aaj payment kar dijiye"}],
    "worst_line": {"turn": 2, "quote": "aaj payment kar dijiye",
                   "rewrite": "Abhi der ho gayi hai, main kal subah das baje call karti hoon."},
}  # fmt: skip


async def test_a_finished_call_is_judged_and_scored(fake_llm: FakeOpenAI) -> None:
    call = await _finished_call(fake_llm)
    fake_llm.scripts.append(Script(pieces=["<think>checking</think>", json.dumps(JUDGE_JSON)]))

    run = await run_judge(_judge_llm(fake_llm), PROMPT, call.judge_input(), catalogue=CATALOGUE,
                          tripwire=LEXICON)  # fmt: skip

    assert run.error is None and run.scorecard is not None
    checks = {c.id: c.status for c in run.scorecard.checks}
    assert checks["identified_self_and_lender"] == "earned"
    assert checks["stated_amount"] == "earned"
    [deduction] = run.scorecard.compliance.deductions  # state flag + judge: one rule, once
    assert deduction.rule == "late_clock" and deduction.points == 15
    assert run.scorecard.worst_line is not None and run.scorecard.worst_line.rewrite_ok
    assert run.prompt_version == 1


async def test_the_judge_request_has_thinking_on_schema_and_zero_temperature(
    fake_llm: FakeOpenAI,
) -> None:
    call = await _finished_call(fake_llm)
    fake_llm.scripts.append(Script(pieces=[json.dumps(JUDGE_JSON)]))

    await run_judge(_judge_llm(fake_llm), PROMPT, call.judge_input(), catalogue=CATALOGUE,
                    tripwire=LEXICON)  # fmt: skip

    body = fake_llm.requests[-1]
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    assert body["response_format"]["type"] == "json_schema"
    assert body["temperature"] == 0.0
    user = body["messages"][1]["content"]
    assert "T2 agent" in user and "late_clock" in user


async def test_malformed_judge_json_is_a_clean_failure(fake_llm: FakeOpenAI) -> None:
    call = await _finished_call(fake_llm)
    fake_llm.scripts.append(Script(pieces=['{"checks": [{"id": "stated_amount", ']))

    run = await run_judge(_judge_llm(fake_llm), PROMPT, call.judge_input(), catalogue=CATALOGUE,
                          tripwire=LEXICON)  # fmt: skip

    assert run.scorecard is None
    assert run.error_reason == "invalid_json"
    assert run.raw.startswith('{"checks"')


async def test_judge_server_error_is_a_clean_failure(fake_llm: FakeOpenAI) -> None:
    call = await _finished_call(fake_llm)
    fake_llm.scripts.append(Script(status=500))

    run = await run_judge(_judge_llm(fake_llm), PROMPT, call.judge_input(), catalogue=CATALOGUE,
                          tripwire=LEXICON)  # fmt: skip

    assert run.scorecard is None and run.error_reason == "llm_error"
    assert run.error is not None and "500" in run.error


async def test_judge_input_from_the_call_has_its_history(fake_llm: FakeOpenAI) -> None:
    call = await _finished_call(fake_llm)
    data = call.judge_input()
    assert [s.turn for s in data.state_history] == [1, 2]
    assert data.sim_clock.time() == dt.time(19, 20)
    assert [f.rule for f in data.flags] == ["late_clock"]
    assert data.facts().promise_turn is None
