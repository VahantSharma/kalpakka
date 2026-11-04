"""Tripwire layer 2 on the fake server: confirm, withdraw or add flags with context (07)."""

import asyncio
import json
from pathlib import Path

import pytest

from kalpakka.brain import load_prompt
from kalpakka.judge import TranscriptLine
from kalpakka.llm import LLMClient, PlainAdapter
from kalpakka.llm_tripwire import LLMTripwire, TripwireVerdict
from kalpakka.tripwire import Flag, load_catalogue
from tests.contract.fakes.fake_openai import FakeOpenAI, Script

CONTENT = Path(__file__).resolve().parents[3] / "content"
CATALOGUE = load_catalogue(CONTENT / "rules/catalogue.yaml")
PROMPT = load_prompt(CONTENT / "prompts/tripwire-check.md")
CONTEXT = [
    TranscriptLine(turn=1, role="agent", text="Namaste, main SwiftFin se Priya.", t=1.0),
    TranscriptLine(turn=1, role="borrower", text="Haan ji.", t=2.0),
    TranscriptLine(turn=2, role="agent", text="Aapki EMI baki hai.", t=4.0),
    TranscriptLine(turn=2, role="borrower", text="Kal de dunga.", t=5.0),
    TranscriptLine(turn=3, role="agent", text="Theek hai.", t=7.0),
    TranscriptLine(turn=3, role="borrower", text="Haan.", t=8.0),
    TranscriptLine(turn=4, role="agent", text="Pichhli baar bhi yahi bola tha.", t=10.0),
    TranscriptLine(turn=4, role="borrower", text="Is baar pakka.", t=11.0),
]


def _wire(fake: FakeOpenAI, **kw: object) -> LLMTripwire:
    llm = LLMClient(base_url=fake.base_url, api_key="k", model="m", adapter=PlainAdapter())
    return LLMTripwire(llm, PROMPT, CATALOGUE, **kw)  # type: ignore[arg-type]


def _verdict(violation: bool, rule: str | None, confidence: float, quote: str = "") -> Script:
    payload = {"violation": violation, "rule": rule, "confidence": confidence, "quote": quote}
    return Script(pieces=[json.dumps(payload)])


def _lexicon_flag(rule: str, quote: str, turn: int = 5) -> Flag:
    return Flag(id=f"t{turn}-s0-{rule}", turn=turn, segment=0, t=12.0, rule=rule,  # type: ignore[arg-type]
                quote=quote, layer="lexicon", status="possible")  # fmt: skip


async def _check(wire: LLMTripwire, text: str, flags: list[Flag]) -> TripwireVerdict:
    return await asyncio.wait_for(
        wire.check(text, turn=5, segment=0, t=12.0, context=CONTEXT, lexicon_flags=flags),
        timeout=10,
    )


async def test_a_real_threat_is_confirmed(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(_verdict(True, "threat", 0.92, "police bhej dunga"))
    flag = _lexicon_flag("threat", "police bhej")

    result = await _check(_wire(fake_llm), "Paisa nahi diya toh police bhej dunga", [flag])

    [update] = result.updates
    assert (update.id, update.status, update.layer) == (flag.id, "confirmed", "lexicon")
    assert result.error is None


async def test_informing_rights_is_withdrawn(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(_verdict(False, None, 0.9))
    flag = _lexicon_flag("threat", "police complaint")

    result = await _check(
        _wire(fake_llm), "Aap police complaint kar sakte hain agar koi pareshan kare", [flag]
    )

    [update] = result.updates
    assert (update.id, update.status) == (flag.id, "withdrawn")


async def test_a_missed_violation_is_added_when_its_quote_is_in_the_segment(
    fake_llm: FakeOpenAI,
) -> None:
    fake_llm.scripts.append(_verdict(True, "threat", 0.85, "anjaam bura hoga"))

    result = await _check(_wire(fake_llm), "Soch lo, warna anjaam bura hoga", [])

    [added] = result.updates
    assert added.rule == "threat" and added.status == "confirmed" and added.layer == "llm"
    assert added.quote == "anjaam bura hoga"
    assert added.id == "t5-s0-threat-llm"


async def test_a_new_flag_whose_quote_is_not_in_the_segment_is_dropped(
    fake_llm: FakeOpenAI,
) -> None:
    fake_llm.scripts.append(_verdict(True, "abuse", 0.95, "tum chor ho"))

    result = await _check(_wire(fake_llm), "Aap payment kab karenge?", [])

    assert result.updates == []


async def test_low_confidence_changes_nothing(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(_verdict(False, None, 0.4))
    flag = _lexicon_flag("threat", "police bhej")

    result = await _check(_wire(fake_llm), "police bhej dunga", [flag])

    assert result.updates == []


async def test_a_rule_the_llm_layer_may_not_raise_is_ignored(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(_verdict(True, "late_clock", 0.99, "aaj payment kar dijiye"))

    result = await _check(_wire(fake_llm), "aaj payment kar dijiye", [])

    assert result.updates == []  # late_clock is decided by the sim clock alone (state layer)


async def test_a_different_rule_confirms_nothing_and_adds_the_new_one(
    fake_llm: FakeOpenAI,
) -> None:
    fake_llm.scripts.append(_verdict(True, "misleading", 0.9, "jail ho jayegi"))
    flag = _lexicon_flag("threat", "jail ho jayegi")

    result = await _check(_wire(fake_llm), "jail ho jayegi aapko", [flag])

    assert [(u.rule, u.status) for u in result.updates] == [("misleading", "confirmed")]


@pytest.mark.parametrize("pieces", [["not json"], ['{"violation": tru'], ['{"rule": 5}']])
async def test_malformed_output_leaves_lexicon_flags_possible(
    fake_llm: FakeOpenAI, pieces: list[str]
) -> None:
    fake_llm.scripts.append(Script(pieces=pieces))

    result = await _check(_wire(fake_llm), "police bhej dunga", [_lexicon_flag("threat", "x")])

    assert result.updates == []
    assert result.error is not None


async def test_server_error_is_reported_not_raised(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(Script(status=503))

    result = await _check(_wire(fake_llm), "police bhej dunga", [])

    assert result.updates == [] and result.error is not None and "503" in result.error


async def test_a_slow_answer_times_out_cleanly(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(Script(pieces=["{}"], first_delay_s=1.0))

    result = await _check(_wire(fake_llm, timeout_s=0.2), "police bhej dunga", [])

    assert result.updates == [] and result.error == "timeout"


async def test_request_is_short_cold_and_has_the_last_three_turns(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(_verdict(False, None, 0.9))

    await _check(_wire(fake_llm), "Aap kab tak pay karenge?", [])

    body = fake_llm.requests[0]
    assert body["temperature"] == 0.0
    assert body["max_tokens"] <= 150
    user = body["messages"][1]["content"]
    assert "Namaste, main SwiftFin se Priya." not in user  # turn 1 is outside the last 3 turns
    assert "T2 agent: Aapki EMI baki hai." in user and "T4 borrower: Is baar pakka." in user
    assert "SEGMENT (agent, turn 5): Aap kab tak pay karenge?" in user
    assert "- threat:" in user and "- late_clock:" not in user


async def test_only_possible_lexicon_flags_can_be_withdrawn(fake_llm: FakeOpenAI) -> None:
    fake_llm.scripts.append(_verdict(False, None, 0.95))
    state_flag = Flag(id="t5-s0-late_clock", turn=5, segment=0, t=12.0, rule="late_clock",
                      quote="payment", layer="state", status="confirmed")  # fmt: skip

    result = await _check(_wire(fake_llm), "aaj payment kar dijiye", [state_flag])

    assert result.updates == []
