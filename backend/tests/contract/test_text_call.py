"""One text-mode call: agent words -> tripwire -> engine -> brain on the LLM -> engine (06 loop)."""

import datetime as dt
from pathlib import Path

import pytest

from kalpakka.borrower_state import CallSetup
from kalpakka.brain import DELIMITER, load_prompt
from kalpakka.clock import SystemClock
from kalpakka.llm import LLMClient, LLMError, PlainAdapter
from kalpakka.personas import Persona
from kalpakka.state_tripwire import load_state_terms
from kalpakka.text_call import TextCall
from kalpakka.tripwire import LexiconTripwire, load_catalogue, load_lexicon
from tests.contract.fakes.fake_openai import FakeOpenAI, Script
from tests.factories import make_persona

CONTENT = Path(__file__).resolve().parents[3] / "content"


def _call(fake: FakeOpenAI, persona: Persona, seed: int = 7) -> TextCall:
    catalogue = load_catalogue(CONTENT / "rules/catalogue.yaml")
    return TextCall(
        setup=CallSetup(persona=persona, difficulty="medium", seed=seed,
                        sim_date=dt.date(2026, 11, 5)),
        llm=LLMClient(base_url=fake.base_url, api_key="k", model="evon-test",
                      adapter=PlainAdapter()),
        system=load_prompt(CONTENT / "prompts/borrower-system.md"),
        lexicon=LexiconTripwire(load_lexicon(CONTENT / "rules/lexicon.yaml", catalogue)),
        terms=load_state_terms(CONTENT / "rules/state_terms.yaml"),
        clock=SystemClock(),
    )  # fmt: skip


def _reply(speech: str, state: str = '{"mood_delta": 0, "trust_delta": 0}') -> Script:
    return Script(pieces=[speech, f"\n{DELIMITER}\n", state])


async def test_a_turn_speaks_and_applies_the_state_suggestion(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(_reply("Haan ji, kaun? ", '{"mood_delta": 10, "trust_delta": 0}'))
    call = _call(fake_llm, make_persona(traps_allowed=[]))

    result = await call.agent_turn("Namaste, main SwiftFin se Priya bol rahi hoon.")

    assert result.turn == 1
    assert result.speech == "Haan ji, kaun?"
    # Ramesh medium: start 55; +10 x anger 1.2 = +12 -> 67; drift 5 toward 55 -> 62.
    assert result.state.mood == 62
    assert result.first_token_s is not None
    assert result.split_errors == []


async def test_history_carries_the_heard_reply_into_the_next_prompt(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts += [_reply("Haan ji, bolo. "), _reply("Kal pakka. ")]
    call = _call(fake_llm, make_persona(traps_allowed=[]))

    await call.agent_turn("Namaste Ramesh ji.")
    await call.agent_turn("Aapki EMI baki hai.")

    second = fake_llm.requests[1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[2]["content"] == "Haan ji, bolo."
    assert second[3]["content"].startswith("Aapki EMI baki hai.")
    assert "TURN BRIEF (turn 2)" in second[3]["content"]
    assert [(line.turn, line.role) for line in call.transcript] == [
        (1, "agent"), (1, "borrower"), (2, "agent"), (2, "borrower"),
    ]  # fmt: skip


async def test_both_tripwire_layers_run_on_the_agent_words(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(_reply("Kya? <sigh> Itni raat ko? "))
    call = _call(fake_llm, make_persona(traps_allowed=["late_clock"]))  # sim clock 19:20

    result = await call.agent_turn("EMI overdue hai, police bhej dunga")

    got = {(f.rule, f.layer, f.status) for f in result.flags}
    assert ("threat", "lexicon", "possible") in got
    assert ("late_clock", "state", "confirmed") in got


async def test_tags_not_allowed_this_turn_are_stripped(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(_reply("<laugh> Haan ji, boliye na. "))  # irritated band: no laugh
    call = _call(fake_llm, make_persona(traps_allowed=[]))

    result = await call.agent_turn("Namaste.")

    assert "<laugh>" not in result.speech
    assert result.speech == "Haan ji, boliye na."


async def test_llm_failure_raises_and_leaves_the_call_usable(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts += [Script(status=503), _reply("Haan ji. ")]
    call = _call(fake_llm, make_persona(traps_allowed=[]))

    with pytest.raises(LLMError):
        await call.agent_turn("Namaste.")
    result = await call.agent_turn("Namaste?")

    assert result.turn == 2
    assert result.speech == "Haan ji."


async def test_open_question_at_the_gate_brings_the_reveal_brief(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(_reply("Sach bataun toh auto kharab tha. "))
    persona = make_persona(traps_allowed=[], temperament={"starting_trust": 60})
    call = _call(fake_llm, persona)

    result = await call.agent_turn("Ramesh ji, kya pareshani chal rahi hai?")

    assert result.brief.reveal_now
    assert result.state.revealed_turn == 1
    assert "engine repair" in fake_llm.requests[0]["messages"][-1]["content"]


async def test_confirmed_state_flags_reach_the_engine(fake_llm: FakeOpenAI) -> None:
    """Two bereavement pushes inside the window end the call (06 hang-up conditions)."""
    fake_llm.scripts += [_reply("Haan ji. ") for _ in range(10)]
    call = _call(fake_llm, make_persona(traps_allowed=["bereavement"]))
    [trap] = call.engine.state.traps

    for _ in range(trap.active_from + 1):
        result = await call.agent_turn("Payment kab tak karenge?")

    assert result.turn == trap.active_from + 1
    assert result.state.call_over == "bereavement_pressure"


async def test_a_failed_turn_still_records_its_flags_and_words(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(Script(status=503))
    call = _call(fake_llm, make_persona(traps_allowed=["late_clock"]))

    with pytest.raises(LLMError):
        await call.agent_turn("EMI overdue hai, aaj payment kar dijiye")

    assert [f.rule for f in call.flags] == ["late_clock"]
    assert [(line.role, line.text) for line in call.transcript] == [
        ("agent", "EMI overdue hai, aaj payment kar dijiye")
    ]


async def test_with_an_llm_layer_a_threat_is_confirmed_before_the_borrower_replies(
    fake_llm: FakeOpenAI,
) -> None:
    import json

    from kalpakka.llm_tripwire import LLMTripwire

    fake_llm.scripts += [
        Script(pieces=[json.dumps({"violation": True, "rule": "threat", "confidence": 0.9,
                                   "quote": "police bhej dunga"})]),
        _reply("Kya?! <shouting> "),
    ]  # fmt: skip
    call = _call(fake_llm, make_persona(traps_allowed=[]))
    llm = LLMClient(base_url=fake_llm.base_url, api_key="k", model="m", adapter=PlainAdapter())
    call.enable_llm_tripwire(
        LLMTripwire(llm, load_prompt(CONTENT / "prompts/tripwire-check.md"),
                    load_catalogue(CONTENT / "rules/catalogue.yaml"))
    )  # fmt: skip

    result = await call.agent_turn("Paisa do warna police bhej dunga")

    assert {(f.rule, f.status) for f in result.flags} == {("threat", "confirmed")}
    # harsh rule: mood +25 on top of the drift (55 -> 80 -> 75), trust 25 -> 5
    assert (result.state.mood, result.state.trust) == (75, 5)
    assert [f.status for f in call.flags] == ["confirmed"]
