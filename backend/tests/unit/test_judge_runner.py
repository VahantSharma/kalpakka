"""Judge runner, model-free half: the judge's input and parsing its output (07 "Judge")."""

import datetime as dt
import json
from pathlib import Path

import pytest

from kalpakka.borrower_state import PromiseRecord, ScheduledTrap
from kalpakka.brain import load_prompt
from kalpakka.judge import JudgeOutput, TranscriptLine
from kalpakka.judge_runner import (
    JsonSchemaAdapter,
    JudgeError,
    JudgeInput,
    StateSnapshot,
    build_judge_messages,
    parse_judge_output,
)
from kalpakka.llm import ChatTemplateThinkingAdapter
from kalpakka.tripwire import Flag, load_catalogue
from tests.factories import make_persona

CONTENT = Path(__file__).resolve().parents[3] / "content"


def judge_input(**kw: object) -> JudgeInput:
    base: dict[str, object] = {
        "persona": make_persona(),
        "difficulty": "medium",
        "sim_clock": dt.datetime(2026, 11, 5, 19, 20),
        "transcript": [
            TranscriptLine(turn=1, role="agent", text="Namaste, main SwiftFin se Priya.", t=1.0),
            TranscriptLine(turn=1, role="borrower", text="Haan ji, boliye.", t=3.5),
        ],
        "traps": [ScheduledTrap(name="bereavement", turn=4, active_from=5, active_until=6)],
        "gate_opened_turn": 3,
        "revealed_turn": None,
        "promise": PromiseRecord(turn=7, amount=4000, date=dt.date(2026, 11, 6), valid=True,
                                 problems=[]),
        "state_history": [StateSnapshot(turn=1, mood=55, trust=25, mood_label="irritated")],
        "flags": [Flag(id="t5-s0-bereavement_pressure", turn=5, segment=0, t=20.0,
                       rule="bereavement_pressure", quote="payment kab", layer="state",
                       status="confirmed")],
    }  # fmt: skip
    base.update(kw)
    return JudgeInput.model_validate(base)


@pytest.fixture(scope="module")
def messages() -> list[dict[str, str]]:
    catalogue = load_catalogue(CONTENT / "rules/catalogue.yaml")
    prompt = load_prompt(CONTENT / "prompts/judge-system.md")
    return build_judge_messages(prompt, judge_input(), catalogue)


def test_the_judge_prompt_is_versioned() -> None:
    assert load_prompt(CONTENT / "prompts/judge-system.md").version == 1


def test_system_then_one_user_message(messages: list[dict[str, str]]) -> None:
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "evidence FIRST" in messages[0]["content"]


def test_input_carries_transcript_with_turns_speakers_and_times(
    messages: list[dict[str, str]],
) -> None:
    user = messages[1]["content"]
    assert "T1 agent (1.0 s): Namaste, main SwiftFin se Priya." in user
    assert "T1 borrower (3.5 s): Haan ji, boliye." in user


def test_input_carries_hidden_facts_traps_reveal_promise_state_and_flags(
    messages: list[dict[str, str]],
) -> None:
    user = messages[1]["content"]
    assert "engine repair" in user  # hidden truth: the judge needs it, the trainee never sees it
    assert "₹4,000" in user and "2026-11-20" in user
    assert "bereavement" in user and "turns 5-6" in user
    assert "gate opened on turn 3" in user and "never revealed" in user
    assert "valid (decided by code)" in user
    assert "T1 mood 55 (irritated) trust 25" in user
    assert 'T5 state confirmed bereavement_pressure: "payment kab"' in user
    assert "19:20" in user and "08:00-19:00" in user
    assert "₹12,500" in user


def test_input_lists_every_rule_with_its_meaning(messages: list[dict[str, str]]) -> None:
    user = messages[1]["content"]
    for rule in ("threat", "abuse", "third_party_disclosure", "grievance_ignored", "late_clock"):
        assert f"- {rule}:" in user


def test_input_embeds_the_output_schema(messages: list[dict[str, str]]) -> None:
    user = messages[1]["content"]
    schema = json.loads(user[user.index("SCHEMA\n") + len("SCHEMA\n") :])
    assert schema == JudgeOutput.model_json_schema()


# --- parsing ---

GOOD = {
    "checks": [{"id": "stated_amount", "evidence": [{"turn": 1, "quote": "x y z"}],
                "answer": "yes"}],
    "violations": [],
    "worst_line": None,
}  # fmt: skip


def test_parse_plain_json() -> None:
    out = parse_judge_output(json.dumps(GOOD))
    assert out.checks[0].id == "stated_amount"


def test_parse_skips_think_blocks_and_prose_around_the_object() -> None:
    raw = "<think>let me see {not json}</think>\nHere you go:\n" + json.dumps(GOOD) + "\nDone."
    assert parse_judge_output(raw).checks[0].answer == "yes"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("", "no_json"),
        ("I think the agent did well.", "no_json"),
        ('{"checks": [', "invalid_json"),
        (
            '{"checks": [{"id": "made_up_check", "evidence": [], "answer": "yes"}]}',
            "invalid_schema",
        ),
        ("[1, 2, 3]", "no_json"),
    ],
)
def test_malformed_output_is_a_clean_judge_error(raw: str, reason: str) -> None:
    with pytest.raises(JudgeError) as info:
        parse_judge_output(raw)
    assert info.value.reason == reason
    assert info.value.raw == raw


def test_json_schema_adapter_adds_the_schema_and_keeps_the_server_fields() -> None:
    adapter = JsonSchemaAdapter(ChatTemplateThinkingAdapter(), name="judge_output")
    body = adapter.extra_body(thinking=True)
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    rf = body["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "judge_output"
    assert rf["json_schema"]["schema"] == JudgeOutput.model_json_schema()


def test_facts_carry_the_engine_windows_for_trap_rules_and_the_clock() -> None:
    late = judge_input().facts()  # bereavement 5-6, sim clock 19:20
    assert late.rule_windows["bereavement_pressure"] == [(5, 6)]
    assert late.rule_windows["third_party_disclosure"] == []  # no wife trap in this call
    assert late.rule_windows["late_clock"] == [(1, None)]
    day = judge_input(sim_clock=dt.datetime(2026, 11, 5, 14, 0)).facts()
    assert day.rule_windows["late_clock"] == []
    assert "repeated_pressure" not in day.rule_windows
