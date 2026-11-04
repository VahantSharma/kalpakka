"""The three launch personas as shipped in content/personas (06 "The three launch personas")."""

import datetime as dt
import json
from pathlib import Path

import pytest

from kalpakka.borrower_state import PromiseSuggestion, check_promise
from kalpakka.personas import Persona, load_personas
from kalpakka.tags import unofficial_tags

CONTENT = Path(__file__).resolve().parents[3] / "content" / "personas"
SIM_DATE = dt.date(2026, 11, 5)  # the build week; promise windows must be open on launch day


@pytest.fixture(scope="module")
def personas() -> dict[str, Persona]:
    return load_personas(CONTENT)


def test_all_three_launch_personas_ship(personas: dict[str, Persona]) -> None:
    assert set(personas) == {"ramesh", "lakshmi", "debu"}


@pytest.mark.parametrize(
    ("pid", "language", "city", "stt"),
    [
        ("lakshmi", "Tanglish", "Chennai", "ta-IN"),
        ("debu", "Benglish", "Kolkata", "bn-IN"),
    ],
)
def test_language_city_and_stt_code(
    personas: dict[str, Persona], pid: str, language: str, city: str, stt: str
) -> None:
    p = personas[pid]
    assert p.language == language
    assert p.profile.city == city
    assert p.speech.stt_language_code.split(",")[0] == stt


def test_lakshmi_carries_the_disputed_charge_trap(personas: dict[str, Persona]) -> None:
    assert "disputed_charge" in personas["lakshmi"].traps_allowed


def test_lakshmi_has_no_wife_trap(personas: dict[str, Persona]) -> None:
    # The wife trap's brief says "you are his wife"; it is written for a male borrower.
    assert "third_party_wife" not in personas["lakshmi"].traps_allowed


def test_debu_hides_a_job_loss(personas: dict[str, Persona]) -> None:
    assert "laid off" in personas["debu"].hidden.truth


@pytest.mark.parametrize("pid", ["ramesh", "lakshmi", "debu"])
def test_lender_is_the_fictional_swiftfin(personas: dict[str, Persona], pid: str) -> None:
    assert personas[pid].loan.lender == "SwiftFin"
    assert "SwiftFin" in personas[pid].speech.keyterms


@pytest.mark.parametrize("pid", ["ramesh", "lakshmi", "debu"])
def test_public_card_carries_no_hidden_value(personas: dict[str, Persona], pid: str) -> None:
    p = personas[pid]
    card = json.dumps(p.public_card().model_dump(mode="json"), ensure_ascii=False)
    for secret in (p.hidden.truth, p.hidden.can_pay_rest_by.isoformat(), *p.excuses):
        assert secret not in card
    assert f'"{p.hidden.can_pay_now}"' not in card and f": {p.hidden.can_pay_now}," not in card


@pytest.mark.parametrize("pid", ["ramesh", "lakshmi", "debu"])
def test_the_hidden_plan_is_a_valid_promise(personas: dict[str, Persona], pid: str) -> None:
    """What the borrower can really pay must pass the engine's own promise check."""
    p = personas[pid]
    now = PromiseSuggestion(amount=p.hidden.can_pay_now, date=SIM_DATE + dt.timedelta(days=1))
    rest = PromiseSuggestion(amount=p.loan.overdue_amount, date=p.hidden.can_pay_rest_by)
    assert check_promise(now, p, SIM_DATE, trust=80).valid
    assert check_promise(rest, p, SIM_DATE, trust=80).valid


@pytest.mark.parametrize("pid", ["ramesh", "lakshmi", "debu"])
def test_excuses_are_distinct_and_lines_use_official_tags(
    personas: dict[str, Persona], pid: str
) -> None:
    p = personas[pid]
    assert len(set(p.excuses)) == len(p.excuses) >= 3
    for line in (*p.excuses, p.lines.pickup, p.lines.silence, p.lines.hangup_angry):
        assert unofficial_tags(line) == []
