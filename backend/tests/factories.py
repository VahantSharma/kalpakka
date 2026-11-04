"""Shared test data: Ramesh built in code, so engine tests do not depend on tuned content."""

import copy
from typing import Any

from kalpakka.personas import Persona

RAMESH: dict[str, Any] = {
    "id": "ramesh",
    "version": 1,
    "display_name": "Ramesh bhaiya",
    "language": "Hinglish",
    "difficulty_default": "medium",
    "profile": {"age": 41, "occupation": "auto-rickshaw driver", "city": "Delhi"},
    "speech": {
        "stt_language_code": "hi-IN,en-IN",
        "keyterms": ["Ramesh", "SwiftFin", "EMI"],
        "tts_voice": "Devansh",
        "tts_language": "hi-IN",
        "style": "Delhi Hinglish",
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
    "excuses": [
        "salary saat tareekh ko aayegi",
        "UPI fail ho gaya",
        "main abhi hospital mein hoon",
        "kal pakka",
    ],
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


def make_persona(**overrides: Any) -> Persona:
    """RAMESH with top-level or dotted-section overrides, e.g. temperament={"starting_mood": 20}."""
    data = copy.deepcopy(RAMESH)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    return Persona.model_validate(data)
