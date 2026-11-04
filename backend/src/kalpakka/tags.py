"""Timbre v3 audio tags: the official 13 (docs.gnani.ai, TTS audio tags) and how to find them.

Matching is deliberately broad: anything shaped like `<...>` (no nested brackets, no newline, up
to 40 characters) counts as a tag, and is official only if its normalised name (trimmed,
lower-cased, inner spaces collapsed) is one of the 13. Text sent to Timbre always uses the
canonical form from `canonical_tag`, so whether Timbre accepts `<SIGH>` or `< sigh >` never matters.
Cost of broad matching: prose like `x < y > z` counts as a tag; borrower speech has none.
"""

import re
from typing import Final

OFFICIAL_TAGS: Final[frozenset[str]] = frozenset(
    {
        "laugh",
        "giggle",
        "sigh",
        "exhales",
        "sniffles",
        "yawns",
        "coughs",
        "clears throat",
        "whispers",
        "shouting",
        "excited",
        "slow",
        "pauses",
    }
)

TAG_PATTERN: Final[re.Pattern[str]] = re.compile(r"<([^<>\n]{1,40})>")


def normalise_tag_name(raw: str) -> str:
    return " ".join(raw.lower().split())


def canonical_tag(name: str) -> str:
    return f"<{normalise_tag_name(name)}>"


def find_tags(text: str) -> list[str]:
    """Normalised names of every tag-shaped token, in order of appearance."""
    return [normalise_tag_name(m.group(1)) for m in TAG_PATTERN.finditer(text)]


def unofficial_tags(text: str) -> list[str]:
    return [canonical_tag(name) for name in find_tags(text) if name not in OFFICIAL_TAGS]
