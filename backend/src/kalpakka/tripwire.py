"""Tripwire layer 1: the lexicon (docs/context/07-tripwire-judge-arena.md).

One rule catalogue (`content/rules/catalogue.yaml`) is shared with the judge. The lexicon
(`content/rules/lexicon.yaml`) holds regex patterns for the rules a lexicon can catch; each hit
becomes a `possible` flag that the background LLM layer may confirm or withdraw.

Patterns use `{b}`, an Indic-aware word boundary: Python's `\\b` treats Devanagari, Bengali and
Tamil vowel signs as non-word characters, so it would "find" boundaries inside words. Raw `\\b`
is rejected at load time.
"""

import re
import unicodedata
from pathlib import Path
from typing import Annotated, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

Layer = Literal["lexicon", "llm", "state"]
FlagStatus = Literal["possible", "confirmed", "withdrawn"]
RuleId = Literal[
    "threat",
    "abuse",
    "third_party_disclosure",
    "bereavement_pressure",
    "late_clock",
    "misleading",
    "repeated_pressure",
    "grievance_ignored",
]  # the catalogue in 07; content/rules/catalogue.yaml must use exactly these ids

# Word characters for {b}: \w plus the Indic blocks Devanagari ... Malayalam (U+0900-U+0DFF) and
# ZWNJ/ZWJ, but NOT the danda / double danda (U+0964, U+0965): they end sentences, so a slur right
# before "।" must still match. Built from code points so the ranges stay visible in review.
_INDIC: Final = f"{chr(0x0900)}-{chr(0x0963)}{chr(0x0966)}-{chr(0x0DFF)}{chr(0x200C)}{chr(0x200D)}"
_WORD: Final = rf"[\w{_INDIC}]"
INDIC_BOUNDARY: Final = rf"(?:(?<!{_WORD})(?={_WORD})|(?<={_WORD})(?!{_WORD}))"


class LexiconError(Exception):
    """A rules file is malformed, inconsistent with the catalogue, or has a bad pattern."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuleSpec(_Strict):
    meaning: str
    deduction: Annotated[int, Field(ge=1, le=40)]
    layers: Annotated[list[Layer], Field(min_length=1)]


class Catalogue(_Strict):
    version: Annotated[int, Field(ge=1)]
    rules: dict[RuleId, RuleSpec]


class LexiconPattern(_Strict):
    pattern: str
    positive: Annotated[list[str], Field(min_length=1)]  # each must match this pattern
    negative: Annotated[list[str], Field(min_length=1)]  # near-misses: must not raise the rule


class LexiconRule(_Strict):
    patterns: Annotated[list[LexiconPattern], Field(min_length=1)]


class Lexicon(_Strict):
    version: Annotated[int, Field(ge=1)]
    rules: dict[RuleId, LexiconRule]


class Flag(_Strict):
    id: str
    turn: int
    segment: int
    t: float
    rule: RuleId
    quote: str
    layer: Layer
    status: FlagStatus


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LexiconError(f"{path.name}: cannot read: {exc}") from exc


def load_catalogue(path: Path) -> Catalogue:
    try:
        return Catalogue.model_validate(_read_yaml(path))
    except ValidationError as exc:
        raise LexiconError(f"{path.name}: invalid catalogue: {exc}") from exc


def load_lexicon(path: Path, catalogue: Catalogue) -> Lexicon:
    try:
        lexicon = Lexicon.model_validate(_read_yaml(path))
    except ValidationError as exc:
        raise LexiconError(f"{path.name}: invalid lexicon: {exc}") from exc

    unknown = sorted(set(lexicon.rules) - set(catalogue.rules))
    if unknown:
        raise LexiconError(f"{path.name}: rules not in the catalogue: {', '.join(unknown)}")
    wanted = {r for r, spec in catalogue.rules.items() if "lexicon" in spec.layers}
    missing = sorted(wanted - set(lexicon.rules))
    if missing:
        raise LexiconError(
            f"{path.name}: catalogue says these rules have lexicon patterns, but none are "
            f"defined: {', '.join(missing)}"
        )
    for rule_id, rule in lexicon.rules.items():
        for entry in rule.patterns:
            pattern = entry.pattern
            if r"\b" in pattern:
                raise LexiconError(
                    f"{path.name}: {rule_id}: use {{b}} instead of \\b in pattern {pattern!r}"
                )
            try:
                compile_pattern(pattern)
            except re.error as exc:
                raise LexiconError(
                    f"{path.name}: {rule_id}: bad pattern {pattern!r}: {exc}"
                ) from exc
    return lexicon


def compile_pattern(pattern: str) -> re.Pattern[str]:
    # NFC like the text it searches: Bengali য় and ড় change form under NFC.
    nfc = unicodedata.normalize("NFC", pattern)
    return re.compile(nfc.replace("{b}", INDIC_BOUNDARY), re.IGNORECASE)


def normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


class LexiconTripwire:
    def __init__(self, lexicon: Lexicon) -> None:
        self._rules: dict[RuleId, list[re.Pattern[str]]] = {
            rule_id: [compile_pattern(p.pattern) for p in rule.patterns]
            for rule_id, rule in sorted(lexicon.rules.items())
        }

    @property
    def rule_ids(self) -> list[RuleId]:
        return list(self._rules)

    def check(self, text: str, *, turn: int, segment: int, t: float) -> list[Flag]:
        """One `possible` flag per rule hit in this transcript segment, ordered by rule id."""
        clean = normalise(text)
        flags: list[Flag] = []
        for rule_id, patterns in self._rules.items():
            matches = [m for p in patterns if (m := p.search(clean))]
            if not matches:
                continue
            first = min(matches, key=lambda m: m.start())
            flags.append(
                Flag(
                    id=f"t{turn}-s{segment}-{rule_id}",
                    turn=turn,
                    segment=segment,
                    t=t,
                    rule=rule_id,
                    quote=first.group(0).strip(),
                    layer="lexicon",
                    status="possible",
                )
            )
        return flags
