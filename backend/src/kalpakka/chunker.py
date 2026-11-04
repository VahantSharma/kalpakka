"""Streamed LLM text -> speakable sentences for Timbre, one request per sentence (D9).

Cutting (docs/context/08-voice-engineering-notes.md, "Sentence chunking"):
- a run of terminators `। . ? ! …` (plus closing quotes) followed by whitespace, or a newline;
- a danda `।` cuts even with no space after it (it is never a decimal or abbreviation);
- never inside a number (`2.5` has no whitespace after the dot), after an abbreviation (`Mr.`,
  `Rs.`), or inside a `<...>` tag;
- a terminator or an unclosed `<` at the very end of the buffer waits for more text, so feeding
  the stream in any split gives the same sentences.

Merging: a sentence with fewer than `MIN_SENTENCE_CHARS` spoken characters is held and joined to
the next one (tiny TTS requests sound choppy); a held fragment is still spoken at `flush()`.

Tag policing (06 "Mood shapes the voice"): only the official 13, only those allowed this turn, at
most two per turn, rendered in canonical form. A sentence that is only tags is never sent: its tags
prefix the next sentence, or are dropped at the end (a tag scopes to the end of its request, so a
trailing tag would do nothing anyway).
"""

import unicodedata
from typing import Final

from kalpakka.tags import OFFICIAL_TAGS, TAG_PATTERN, canonical_tag, normalise_tag_name

MIN_SENTENCE_CHARS: Final = 12
MAX_TAGS_PER_TURN: Final = 2
TERMINATORS: Final = frozenset(".?!…।")
CLOSERS: Final = frozenset("\"')]”’")  # noqa: RUF001 (curly closing quotes are intended)
ABBREVIATIONS: Final = frozenset(
    {"mr", "mrs", "ms", "dr", "rs", "sr", "jr", "prof", "smt", "shri", "vs", "रु"}
)
_MAX_TAG_SPAN: Final = 42  # "<" + up to 40 characters + ">"


def _spoken(text: str) -> str:
    return " ".join(TAG_PATTERN.sub(" ", text).split())


def _word_before(text: str, end: int) -> str:
    start = end
    # Letters and combining marks: Indic vowel signs (the "ु" in "रु") are marks, not letters.
    while start > 0 and unicodedata.category(text[start - 1])[0] in "LM":
        start -= 1
    return text[start:end].lower()


class Chunker:
    """One instance per borrower turn (the tag budget is per turn)."""

    def __init__(self, allowed_tags: tuple[str, ...], min_chars: int = MIN_SENTENCE_CHARS) -> None:
        unknown = [t for t in allowed_tags if normalise_tag_name(t) not in OFFICIAL_TAGS]
        if unknown:
            raise ValueError(f"not official audio tags: {', '.join(unknown)}")
        self._allowed = frozenset(normalise_tag_name(t) for t in allowed_tags)
        self._min_chars = min_chars  # tunable in P2 latency work: short openers vs choppy audio
        self._buf = ""
        self._held = ""  # policed text waiting to reach MIN_SENTENCE_CHARS
        self._prefix = ""  # tags from a tag-only sentence, waiting for words
        self._tags_used = 0
        self._flushed = False

    def feed(self, delta: str) -> list[str]:
        if self._flushed:
            raise RuntimeError("chunker already flushed; use a new Chunker for the next turn")
        self._buf += delta
        return self._drain(final=False)

    def flush(self) -> list[str]:
        if self._flushed:
            return []
        out = self._drain(final=True)
        if self._buf.strip():
            out.extend(self._accept(self._buf))
        self._buf = ""
        if self._held:
            out.append(self._held)
            self._held = ""
        self._prefix = ""
        self._flushed = True
        return out

    # --- cutting ---

    def _drain(self, *, final: bool) -> list[str]:
        out: list[str] = []
        while (cut := self._find_cut(final=final)) is not None:
            sentence_end, resume = cut
            raw, self._buf = self._buf[:sentence_end], self._buf[resume:]
            out.extend(self._accept(raw))
        return out

    def _find_cut(self, *, final: bool) -> tuple[int, int] | None:
        """(end of sentence, start of the rest) for the first cut, or None to wait."""
        buf = self._buf
        i = 0
        while i < len(buf):
            ch = buf[i]
            if ch == "<":
                close = buf.find(">", i + 1, i + _MAX_TAG_SPAN)
                inner = buf[i + 1 : close] if close != -1 else buf[i + 1 : i + _MAX_TAG_SPAN]
                if close != -1 and "<" not in inner and "\n" not in inner:
                    i = close + 1
                    continue
                if close == -1 and not final and len(buf) - i < _MAX_TAG_SPAN and "\n" not in inner:
                    return None  # maybe a tag still streaming in
                i += 1
                continue
            if ch == "\n":
                return i, i + 1
            if ch in TERMINATORS:
                j = i
                while j < len(buf) and buf[j] in TERMINATORS:
                    j += 1
                while j < len(buf) and buf[j] in CLOSERS:
                    j += 1
                if j == len(buf):
                    return None  # need the next character to decide
                run = buf[i:j]
                if "।" in run and not buf[j].isspace():
                    return j, j  # a danda is never a decimal point or an abbreviation
                if buf[j].isspace() and not (
                    run.startswith(".")
                    and run.rstrip("".join(CLOSERS)) == "."
                    and _word_before(buf, i) in ABBREVIATIONS
                ):
                    return j, j + 1
                i = j
                continue
            i += 1
        return None

    # --- policing and merging ---

    def _police(self, raw: str) -> str:
        """Keep allowed tags (canonical, within budget); drop other tags and stray < > marks."""
        pieces: list[str] = []
        last = 0
        for match in TAG_PATTERN.finditer(raw):
            pieces.append(raw[last : match.start()].replace("<", " ").replace(">", " "))
            name = normalise_tag_name(match.group(1))
            if name in self._allowed and self._tags_used < MAX_TAGS_PER_TURN:
                self._tags_used += 1
                pieces.append(f" {canonical_tag(name)} ")
            else:
                pieces.append(" ")
            last = match.end()
        pieces.append(raw[last:].replace("<", " ").replace(">", " "))
        return " ".join("".join(pieces).split())

    def _accept(self, raw: str) -> list[str]:
        text = self._police(raw)
        if not text:
            return []
        if not _spoken(text):  # tags only: carry them to the next sentence
            self._prefix = f"{self._prefix} {text}".strip()
            return []
        # Order matters: a tag scopes from its position, so it stays after a held fragment.
        combined = " ".join(p for p in (self._held, self._prefix, text) if p)
        self._prefix = ""
        if len(_spoken(combined)) < self._min_chars:
            self._held = combined
            return []
        self._held = ""
        return [combined]
