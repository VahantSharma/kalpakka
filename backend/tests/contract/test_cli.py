"""`kalpakka talk`: type as the agent, see the borrower, state and TTFT per turn (P1.5)."""

import io
import json
from pathlib import Path
from typing import Any

import pytest

from kalpakka.brain import DELIMITER
from kalpakka.cli import parse_args, run_talk
from kalpakka.llm import LLMClient, PlainAdapter
from tests.contract.fakes.fake_openai import FakeOpenAI, Script


def _llm(fake: FakeOpenAI) -> LLMClient:
    return LLMClient(base_url=fake.base_url, api_key="k", model="evon-test", adapter=PlainAdapter())


def _saved(directory: Path) -> dict[str, Any]:
    [saved] = list(directory.glob("talk-ramesh-seed7-*.json"))
    data: dict[str, Any] = json.loads(saved.read_text(encoding="utf-8"))
    return data


def _reply(speech: str) -> Script:
    return Script(pieces=[speech, f"\n{DELIMITER}\n", '{"mood_delta": 2, "trust_delta": 1}'])


def test_parse_args_reads_persona_seed_and_difficulty() -> None:
    args = parse_args(["talk", "lakshmi", "--seed", "7", "--difficulty", "hard", "--debug"])
    assert (args.persona, args.seed, args.difficulty, args.debug) == ("lakshmi", 7, "hard", True)


def test_parse_args_defaults_to_the_persona_difficulty() -> None:
    assert parse_args(["talk", "ramesh"]).difficulty is None


def test_unknown_difficulty_is_rejected() -> None:
    with pytest.raises(SystemExit):
        parse_args(["talk", "ramesh", "--difficulty", "brutal"])


async def test_a_typed_conversation_prints_replies_and_saves_the_transcript(
    fake_llm: FakeOpenAI, tmp_path: Path
) -> None:
    fake_llm.scripts += [_reply("Haan ji, kaun? "), _reply("Achha, bolo. ")]
    out = io.StringIO()
    args = parse_args(["talk", "ramesh", "--seed", "7", "--debug", "--out", str(tmp_path)])

    code = await run_talk(
        args, llm=_llm(fake_llm), lines=iter(["Namaste Ramesh ji", "Kaise ho?", "/quit"]), out=out
    )

    text = out.getvalue()
    assert code == 0
    assert "Ramesh bhaiya: Haan ji, kaun?" in text
    assert "Ramesh bhaiya: Achha, bolo." in text
    assert "mood" in text and "trust" in text  # --debug shows state after each turn
    assert "TTFT p50" in text
    data = _saved(tmp_path)
    assert [t["role"] for t in data["transcript"]] == ["agent", "borrower", "agent", "borrower"]
    assert data["seed"] == 7 and data["persona_version"] == 1 and data["prompt_version"] == 1


async def test_without_debug_no_hidden_state_is_printed(
    fake_llm: FakeOpenAI, tmp_path: Path
) -> None:
    fake_llm.scripts.append(_reply("Haan ji. "))
    out = io.StringIO()
    args = parse_args(["talk", "ramesh", "--out", str(tmp_path)])

    await run_talk(args, llm=_llm(fake_llm), lines=iter(["Namaste"]), out=out)

    text = out.getvalue()
    assert "engine repair" not in text and "trust" not in text


async def test_a_threat_shows_a_live_flag(fake_llm: FakeOpenAI, tmp_path: Path) -> None:
    fake_llm.scripts.append(_reply("Kya bol rahe ho aap? "))
    out = io.StringIO()
    args = parse_args(["talk", "ramesh", "--out", str(tmp_path)])

    await run_talk(args, llm=_llm(fake_llm), lines=iter(["Police bhej dunga"]), out=out)

    assert "[flag possible: threat] police bhej" in out.getvalue().lower()


async def test_end_command_closes_the_call(fake_llm: FakeOpenAI, tmp_path: Path) -> None:
    fake_llm.scripts.append(_reply("Theek hai. "))
    out = io.StringIO()
    args = parse_args(["talk", "ramesh", "--out", str(tmp_path)])

    code = await run_talk(
        args, llm=_llm(fake_llm), lines=iter(["/end Dhanyavaad", "never sent"]), out=out
    )

    assert code == 0
    assert len(fake_llm.requests) == 1
    assert fake_llm.requests[0]["messages"][-1]["content"].startswith("Dhanyavaad")


async def test_end_with_a_failing_borrower_still_ends_and_saves(
    fake_llm: FakeOpenAI, tmp_path: Path
) -> None:
    fake_llm.scripts.append(Script(status=503))
    out = io.StringIO()
    args = parse_args(["talk", "ramesh", "--seed", "7", "--out", str(tmp_path)])

    code = await run_talk(
        args, llm=_llm(fake_llm), lines=iter(["/end Dhanyavaad", "never sent"]), out=out
    )

    assert code == 0
    assert len(fake_llm.requests) == 1
    assert _saved(tmp_path)["transcript"][0]["text"] == "Dhanyavaad"


async def test_llm_tripwire_flag_confirms_a_threat_live(
    fake_llm: FakeOpenAI, tmp_path: Path
) -> None:
    fake_llm.scripts += [
        Script(pieces=['{"violation": true, "rule": "threat", "confidence": 0.9, '
                       '"quote": "police bhej dunga"}']),
        _reply("Kya?! "),
    ]  # fmt: skip
    out = io.StringIO()
    args = parse_args(["talk", "ramesh", "--llm-tripwire", "--out", str(tmp_path)])

    await run_talk(args, llm=_llm(fake_llm), lines=iter(["Police bhej dunga"]), out=out)

    assert "[flag confirmed: threat]" in out.getvalue()
