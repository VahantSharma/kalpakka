"""What code learns from the agent's words before the borrower replies (06 update rule 2)."""

from pathlib import Path

import pytest

from kalpakka.state_tripwire import StateTerms, agent_signals, load_state_terms
from tests.factories import make_persona

TERMS = Path(__file__).resolve().parents[3] / "content" / "rules" / "state_terms.yaml"


@pytest.fixture(scope="module")
def terms() -> StateTerms:
    return load_state_terms(TERMS)


@pytest.mark.parametrize(
    "text",
    [
        "Ramesh ji, kya pareshani chal rahi hai?",
        "Aapki taraf kya hua, bataiye?",
        "क्या दिक्कत है, बताइए",
        "What happened, can you tell me?",
        "Madam, enna prachanai?",
        "Debu babu, ki hoyeche?",
        "আপনার কী হয়েছে?",
    ],
)
def test_open_question_about_the_situation(terms: StateTerms, text: str) -> None:
    assert agent_signals(text, make_persona(), terms).asked_open_question


@pytest.mark.parametrize(
    "text",
    [
        "Kya aap Ramesh ji bol rahe hain?",
        "Kya payment kal tak ho jayega?",
        "Is this Ramesh?",
        "Apni ki Debu babu?",
    ],
)
def test_closed_questions_are_not_open(terms: StateTerms, text: str) -> None:
    assert not agent_signals(text, make_persona(), terms).asked_open_question


@pytest.mark.parametrize(
    "text",
    ["Aapka overdue 12,500 rupaye hai", "₹12500 baki hai", "total 12,500 hai sir"],
)
def test_naming_the_correct_overdue_amount(terms: StateTerms, text: str) -> None:
    assert agent_signals(text, make_persona(), terms).named_correct_amount


def test_a_wrong_amount_is_not_the_correct_amount(terms: StateTerms) -> None:
    signals = agent_signals("Aapka overdue 15,000 hai, aur EMI 2500", make_persona(), terms)
    assert not signals.named_correct_amount


def test_acknowledging_the_problem(terms: StateTerms) -> None:
    signals = agent_signals("Main samajh sakti hoon, mushkil waqt hai", make_persona(), terms)
    assert signals.acknowledged_problem


def test_silence_is_a_silent_turn(terms: StateTerms) -> None:
    assert agent_signals("   ", make_persona(), terms).agent_silent
    assert not agent_signals("Hello?", make_persona(), terms).agent_silent


def test_confirmed_violations_and_closing_pass_through(terms: StateTerms) -> None:
    signals = agent_signals(
        "Theek hai, dhanyavaad",
        make_persona(),
        terms,
        confirmed_violations=frozenset({"late_clock"}),
        closing=True,
    )
    assert signals.confirmed_violations == frozenset({"late_clock"})
    assert signals.closing


@pytest.mark.parametrize(
    "text",
    [
        "aise kaise chalega, paise do abhi",
        "kya hua payment ka? kal tak bhejo",
        "kya problem hai tumhe? paise do",
        "kyun nahi hua payment abhi tak",
        "enna problem unakku, pay pannu",
        "kya baat hai, aap phone nahi uthate",
        "eppadi irukkeenga? EMI kattunga",
        "kemon achen? taka kobe diben",
    ],
)
def test_pressure_dressed_as_a_question_is_not_open(terms: StateTerms, text: str) -> None:
    """Review P1.5: these would open the reveal gate and earn a respect bonus."""
    assert not agent_signals(text, make_persona(), terms).asked_open_question
