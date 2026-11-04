import copy
import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import yaml

from kalpakka.personas import Difficulty, Persona, PersonaError, load_persona, load_personas

CONTENT_PERSONAS = Path(__file__).resolve().parents[3] / "content" / "personas"

RAMESH: dict[str, Any] = {
    "id": "ramesh",
    "version": 1,
    "display_name": "Ramesh bhaiya",
    "language": "Hinglish",
    "difficulty_default": "medium",
    "profile": {"age": 41, "occupation": "auto-rickshaw driver", "city": "Delhi"},
    "speech": {
        "stt_language_code": "hi-IN,en-IN",
        "keyterms": ["Ramesh", "SwiftFin", "EMI", "NACH", "CIBIL", "bounce charge"],
        "tts_voice": "Devansh",
        "tts_language": "hi-IN",
        "style": "Delhi Hinglish; Hindi in Devanagari, English words in Latin",
    },
    "loan": {
        "lender": "SwiftFin",
        "product": "two-wheeler loan",
        "emi": 2500,
        "overdue_amount": 12500,
        "dpd": 23,
        "past_promises": 2,
    },
    "hidden": {
        "truth": "auto was off the road 3 weeks for engine repair; earnings stopped",
        "can_pay_now": 4000,
        "can_pay_rest_by": "2026-11-20",
        "reveal_trust": 60,
    },
    "excuses": ["salary saat tareekh ko", "UPI fail ho gaya", "kal pakka"],
    "temperament": {
        "anger_sensitivity": 1.2,
        "trust_gain": 0.8,
        "starting_mood": 55,
        "starting_trust": 25,
    },
    "traps_allowed": ["bereavement", "third_party_wife", "late_clock"],
    "lines": {
        "pickup": "Haan, hello? Kaun bol raha hai?",
        "silence": "Hello? Sun rahe ho?",
        "hangup_angry": "Mujhe aur baat nahi karni. <sigh>",
    },
}


def write_persona(tmp_path: Path, data: dict[str, Any], name: str = "ramesh.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def ramesh(**overrides: Any) -> dict[str, Any]:
    data = copy.deepcopy(RAMESH)
    data.update(overrides)
    return data


# --- loading ---------------------------------------------------------------------------------


def test_load_persona_reads_full_persona_with_hidden_truth(tmp_path: Path) -> None:
    persona = load_persona(write_persona(tmp_path, RAMESH))

    assert isinstance(persona, Persona)
    assert persona.loan.overdue_amount == 12500
    assert persona.hidden.can_pay_now == 4000
    assert persona.hidden.can_pay_rest_by == dt.date(2026, 11, 20)


def test_load_personas_indexes_every_file_by_id(tmp_path: Path) -> None:
    write_persona(tmp_path, RAMESH)
    write_persona(tmp_path, ramesh(id="debu", display_name="Debu da"), name="debu.yaml")

    personas = load_personas(tmp_path)

    assert sorted(personas) == ["debu", "ramesh"]


def test_load_personas_rejects_duplicate_ids(tmp_path: Path) -> None:
    write_persona(tmp_path, RAMESH, name="a.yaml")
    write_persona(tmp_path, RAMESH, name="b.yaml")

    with pytest.raises(PersonaError, match="duplicate persona id 'ramesh'"):
        load_personas(tmp_path)


def test_shipped_ramesh_persona_is_valid() -> None:
    persona = load_persona(CONTENT_PERSONAS / "ramesh.yaml")

    assert persona.id == "ramesh"
    assert persona.loan.overdue_amount == 12500
    assert persona.loan.dpd == 23


# --- the public card never carries secrets ----------------------------------------------------


def test_public_card_shows_briefing_facts(tmp_path: Path) -> None:
    card = load_persona(write_persona(tmp_path, RAMESH)).public_card()

    assert card.display_name == "Ramesh bhaiya"
    assert card.language == "Hinglish"
    assert card.loan.lender == "SwiftFin"
    assert card.loan.overdue_amount == 12500
    assert card.loan.dpd == 23
    assert card.loan.past_promises == 2


def test_public_card_has_no_hidden_fields(tmp_path: Path) -> None:
    card = load_persona(write_persona(tmp_path, RAMESH)).public_card()
    dumped = card.model_dump()

    for secret_field in ("hidden", "excuses", "temperament", "traps_allowed", "lines", "speech"):
        assert secret_field not in dumped


def test_public_card_json_contains_no_secret_values(tmp_path: Path) -> None:
    card_json = load_persona(write_persona(tmp_path, RAMESH)).public_card().model_dump_json()

    for secret in ("engine repair", "4000", "2026-11-20", "kal pakka", "bereavement", "1.2"):
        assert secret not in card_json


def test_public_card_rejects_extra_fields() -> None:
    from kalpakka.personas import PublicCard

    with pytest.raises(ValueError):
        PublicCard.model_validate(
            {
                **load_persona(CONTENT_PERSONAS / "ramesh.yaml").public_card().model_dump(),
                "hidden": {"truth": "leak"},
            }
        )


# --- invalid files fail with clear errors ----------------------------------------------------


def test_missing_field_error_names_file_and_field(tmp_path: Path) -> None:
    data = ramesh()
    del data["hidden"]["can_pay_now"]

    with pytest.raises(PersonaError) as excinfo:
        load_persona(write_persona(tmp_path, data))

    message = str(excinfo.value)
    assert "ramesh.yaml" in message
    assert "hidden.can_pay_now" in message


def test_unknown_field_is_rejected_as_a_typo(tmp_path: Path) -> None:
    with pytest.raises(PersonaError, match="excusses"):
        load_persona(write_persona(tmp_path, ramesh(excusses=["kal pakka"])))


def test_unknown_trap_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PersonaError, match="traps_allowed"):
        load_persona(write_persona(tmp_path, ramesh(traps_allowed=["bereavement", "fake_trap"])))


def test_unofficial_audio_tag_in_a_line_is_rejected(tmp_path: Path) -> None:
    data = ramesh()
    data["lines"]["hangup_angry"] = "Mujhe aur baat nahi karni. <angry>"

    with pytest.raises(PersonaError, match="<angry>"):
        load_persona(write_persona(tmp_path, data))


def test_can_pay_now_above_overdue_amount_is_rejected(tmp_path: Path) -> None:
    data = ramesh()
    data["hidden"]["can_pay_now"] = 15000

    with pytest.raises(PersonaError, match="can_pay_now"):
        load_persona(write_persona(tmp_path, data))


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("temperament", "starting_mood", 101),
        ("temperament", "starting_trust", -1),
        ("hidden", "reveal_trust", 120),
        ("loan", "dpd", -3),
        ("loan", "emi", 0),
    ],
)
def test_out_of_range_numbers_are_rejected(
    tmp_path: Path, section: str, field: str, value: int
) -> None:
    data = ramesh()
    data[section][field] = value

    with pytest.raises(PersonaError, match=f"{section}.{field}"):
        load_persona(write_persona(tmp_path, data))


def test_reveal_trust_must_leave_room_for_easy_and_hard_gates(tmp_path: Path) -> None:
    data = ramesh()
    data["hidden"]["reveal_trust"] = 95  # hard gate would need trust 105

    with pytest.raises(PersonaError, match="reveal_trust"):
        load_persona(write_persona(tmp_path, data))


def test_too_many_keyterms_are_rejected(tmp_path: Path) -> None:
    data = ramesh()
    data["speech"]["keyterms"] = [f"term{i}" for i in range(201)]

    with pytest.raises(PersonaError, match="keyterms"):
        load_persona(write_persona(tmp_path, data))


def test_malformed_yaml_error_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("id: ramesh\n  version: [1\n", encoding="utf-8")

    with pytest.raises(PersonaError, match=r"broken.yaml"):
        load_persona(path)


def test_yaml_that_is_not_a_mapping_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- ramesh\n", encoding="utf-8")

    with pytest.raises(PersonaError, match=r"list.yaml"):
        load_persona(path)


@pytest.mark.parametrize(("difficulty", "gate"), [("easy", 40), ("medium", 60), ("hard", 70)])
def test_reveal_gate_per_difficulty_is_offset_from_reveal_trust(
    tmp_path: Path, difficulty: Difficulty, gate: int
) -> None:
    persona = load_persona(write_persona(tmp_path, RAMESH))

    assert persona.reveal_gate(difficulty) == gate


def test_load_personas_fails_when_the_folder_has_no_personas(tmp_path: Path) -> None:
    with pytest.raises(PersonaError, match="no persona files"):
        load_personas(tmp_path)
