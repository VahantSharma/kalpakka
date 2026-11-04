"""Persona files (`content/personas/<id>.yaml`): schema, loader, and the public card.

The full `Persona` (with `hidden`, excuses, temperament, traps) stays in the backend. The browser
and the briefing only ever see `PublicCard`, which is built from an explicit allow-list of fields
rather than by deleting secrets from a dump, so a new secret field can never leak by default.
"""

import datetime as dt
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from kalpakka.tags import unofficial_tags

Difficulty = Literal["easy", "medium", "hard"]
TrapName = Literal["bereavement", "third_party_wife", "late_clock", "disputed_charge"]
Score = Annotated[int, Field(ge=0, le=100)]

# Reveal gate per difficulty, relative to the persona's medium `reveal_trust`
# (Ramesh: 40 / 60 / 70, matching docs/context/06-borrower-engine.md).
REVEAL_OFFSETS: dict[Difficulty, int] = {"easy": -20, "medium": 0, "hard": 10}
MAX_KEYTERMS = 200  # Prisma v3 keyterms header limit


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Profile(_Strict):
    age: Annotated[int, Field(ge=18, le=100)]
    occupation: str
    city: str


class Speech(_Strict):
    stt_language_code: str
    keyterms: Annotated[list[str], Field(max_length=MAX_KEYTERMS)]
    tts_voice: str
    tts_language: str
    style: str


class Loan(_Strict):
    lender: str
    product: str
    emi: Annotated[int, Field(gt=0)]
    overdue_amount: Annotated[int, Field(gt=0)]
    dpd: Annotated[int, Field(ge=0)]
    past_promises: Annotated[int, Field(ge=0)]


class Hidden(_Strict):
    truth: str
    can_pay_now: Annotated[int, Field(ge=0)]
    can_pay_rest_by: dt.date
    reveal_trust: Score


class Temperament(_Strict):
    anger_sensitivity: Annotated[float, Field(gt=0, le=3)]
    trust_gain: Annotated[float, Field(gt=0, le=3)]
    starting_mood: Score
    starting_trust: Score


class Lines(_Strict):
    pickup: str
    silence: str
    hangup_angry: str


class PublicCard(_Strict):
    """What a trainee may see before and during the call."""

    id: str
    version: int
    display_name: str
    language: str
    difficulty_default: Difficulty
    profile: Profile
    loan: Loan


class Persona(_Strict):
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    version: Annotated[int, Field(ge=1)]
    display_name: str
    language: str
    difficulty_default: Difficulty
    profile: Profile
    speech: Speech
    loan: Loan
    hidden: Hidden
    excuses: Annotated[list[str], Field(min_length=1)]
    temperament: Temperament
    traps_allowed: list[TrapName]
    lines: Lines

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.hidden.can_pay_now > self.loan.overdue_amount:
            raise ValueError(
                f"hidden.can_pay_now ({self.hidden.can_pay_now}) exceeds "
                f"loan.overdue_amount ({self.loan.overdue_amount})"
            )
        for difficulty, offset in REVEAL_OFFSETS.items():
            gate = self.hidden.reveal_trust + offset
            if not 0 <= gate <= 100:
                raise ValueError(
                    f"hidden.reveal_trust {self.hidden.reveal_trust} gives a {difficulty} "
                    f"reveal gate of {gate}, outside 0-100"
                )
        for name, line in self.lines.model_dump().items():
            if bad := unofficial_tags(line):
                raise ValueError(f"lines.{name} uses unofficial audio tag(s) {', '.join(bad)}")
        return self

    def reveal_gate(self, difficulty: Difficulty) -> int:
        return self.hidden.reveal_trust + REVEAL_OFFSETS[difficulty]

    def public_card(self) -> PublicCard:
        return PublicCard(
            id=self.id,
            version=self.version,
            display_name=self.display_name,
            language=self.language,
            difficulty_default=self.difficulty_default,
            profile=self.profile,
            loan=self.loan,
        )


class PersonaError(Exception):
    """A persona file is missing, malformed, or fails validation. The message names the file."""


def _format_validation_error(path: Path, error: ValidationError) -> str:
    lines = [f"{path.name}: invalid persona"]
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "(file)"
        lines.append(f"  {location}: {item['msg']}")
    return "\n".join(lines)


def load_persona(path: Path) -> Persona:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PersonaError(f"{path.name}: cannot read persona: {exc}") from exc
    if not isinstance(raw, dict):
        raise PersonaError(f"{path.name}: a persona file must be a YAML mapping")
    try:
        return Persona.model_validate(raw)
    except ValidationError as exc:
        raise PersonaError(_format_validation_error(path, exc)) from exc


def load_personas(directory: Path) -> dict[str, Persona]:
    personas: dict[str, Persona] = {}
    for path in sorted(directory.glob("*.yaml")):
        persona = load_persona(path)
        if persona.id in personas:
            raise PersonaError(f"{path.name}: duplicate persona id '{persona.id}'")
        personas[persona.id] = persona
    if not personas:
        raise PersonaError(f"{directory}: no persona files (*.yaml) found")
    return personas
