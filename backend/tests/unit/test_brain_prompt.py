import datetime as dt
from pathlib import Path

import pytest

from kalpakka.borrower_state import BorrowerEngine, CallSetup, TurnSignals
from kalpakka.brain import (
    HistoryTurn,
    Prompt,
    PromptError,
    build_messages,
    load_prompt,
    persona_block,
)
from tests.factories import make_persona

PROMPTS = Path(__file__).resolve().parents[3] / "content" / "prompts"
SYSTEM = Prompt(
    name="borrower-system", version=3, text="You are a borrower. Never say you are an AI."
)


class FakeClock:
    def monotonic(self) -> float:
        return 0.0


def brief_text(turn_signals: TurnSignals | None = None) -> str:
    setup = CallSetup(
        persona=make_persona(), difficulty="easy", seed=1, sim_date=dt.date(2026, 11, 10)
    )
    return BorrowerEngine(setup, FakeClock()).begin_turn(turn_signals or TurnSignals()).text


HISTORY = [
    HistoryTurn(role="borrower", text="Haan, hello? Kaun bol raha hai?"),
    HistoryTurn(role="agent", text="Namaste Ramesh ji, main SwiftFin se Pooja bol rahi hoon."),
    HistoryTurn(role="borrower", text="Haan bolo, kya baat hai?"),
]


SIM = dt.datetime(2026, 11, 10, 11, 30)


def msgs(history: list[HistoryTurn], agent: str, brief: str) -> list[dict[str, str]]:
    return build_messages(SYSTEM, make_persona(), SIM, history, agent, brief)


def test_message_order_is_one_system_message_then_history_then_agent_turn_with_brief() -> None:
    messages = msgs(HISTORY, "Aapka EMI pending hai.", "BRIEF")

    roles = [m["role"] for m in messages]
    assert roles == ["system", "assistant", "user", "assistant", "user"]
    assert messages[0]["content"].startswith(SYSTEM.text)
    assert persona_block(make_persona(), SIM) in messages[0]["content"]
    assert messages[-1]["content"] == "Aapka EMI pending hai.\n\nBRIEF"


def test_prefix_is_identical_across_turns_for_prefix_caching() -> None:
    turn3 = msgs(HISTORY[:1], "Namaste Ramesh ji.", "BRIEF 2")
    turn4 = msgs(
        [
            *HISTORY[:1],
            HistoryTurn(role="agent", text="Namaste Ramesh ji."),
            HistoryTurn(role="borrower", text="Haan bolo."),
        ],
        "EMI pending hai.",
        "BRIEF 3",
    )

    assert turn4[: len(turn3) - 1] == turn3[:-1]
    assert turn4[len(turn3) - 1]["content"] == "Namaste Ramesh ji."  # stored without the brief


def test_history_maps_agent_to_user_and_borrower_to_assistant() -> None:
    messages = msgs(HISTORY, "x", "BRIEF")

    assert messages[2] == {"role": "user", "content": HISTORY[1].text}
    assert messages[3] == {"role": "assistant", "content": HISTORY[2].text}


def test_silent_agent_turn_is_described_not_left_empty() -> None:
    messages = msgs([], "   ", "BRIEF")

    assert messages[-1]["content"].startswith("(The caller said nothing.)")


def test_persona_block_has_visible_facts() -> None:
    block = persona_block(make_persona(), SIM)

    for fact in (
        "Ramesh bhaiya",
        "auto-rickshaw driver",
        "Delhi",
        "SwiftFin",
        "₹12,500",
        "23 days",
        "Delhi Hinglish",
    ):
        assert fact in block


def test_persona_block_tells_the_model_the_sim_date_and_time() -> None:
    # Without today's date the model guesses years for "bees November" (review blocker).
    block = persona_block(make_persona(), SIM)

    assert "Tuesday, 2026-11-10" in block
    assert "11:30" in block


def test_persona_block_never_contains_the_hidden_truth_or_means() -> None:
    block = persona_block(make_persona(), SIM)

    for secret in ("engine repair", "4,000", "4000", "2026-11-20", "kal pakka", "1.2"):
        assert secret not in block


def test_brief_is_only_in_the_last_message() -> None:
    brief = brief_text()
    messages = msgs(HISTORY, "Aapka EMI pending hai.", brief)

    assert all(brief not in m["content"] for m in messages[:-1])
    assert messages[-1]["content"].endswith(brief)


# --- prompt files -------------------------------------------------------------------------


def test_shipped_borrower_system_prompt_loads_with_a_version() -> None:
    prompt = load_prompt(PROMPTS / "borrower-system.md")

    assert prompt.name == "borrower-system"
    assert prompt.version >= 1
    assert "###STATE###" in prompt.text
    assert "AI" in prompt.text


def test_prompt_without_version_header_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "x.md"
    path.write_text("You are a borrower.\n", encoding="utf-8")

    with pytest.raises(PromptError, match="version"):
        load_prompt(path)


def test_prompt_header_is_not_sent_to_the_model(tmp_path: Path) -> None:
    path = tmp_path / "x.md"
    path.write_text("<!-- version: 4 -->\nYou are a borrower.\n", encoding="utf-8")

    prompt = load_prompt(path)

    assert prompt.version == 4
    assert prompt.text == "You are a borrower."
