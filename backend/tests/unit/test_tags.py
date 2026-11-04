import pytest

from kalpakka.tags import OFFICIAL_TAGS, canonical_tag, find_tags, unofficial_tags


def test_there_are_exactly_13_official_tags() -> None:
    assert len(OFFICIAL_TAGS) == 13
    assert "clears throat" in OFFICIAL_TAGS


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Arre sir <sigh> kal pakka.", ["sigh"]),
        ("<clears throat> Haan bolo.", ["clears throat"]),
        ("< sigh > theek hai", ["sigh"]),
        ("<SIGH> theek hai", ["sigh"]),
        ("<clears   throat>", ["clears throat"]),
        ("<sniffles> पापा ... <slow> नहीं रहे", ["sniffles", "slow"]),
    ],
)
def test_find_tags_normalises_names(text: str, found: list[str]) -> None:
    assert find_tags(text) == found


@pytest.mark.parametrize(
    "text",
    [
        "<sad-voice> nahi",
        "<laughs_softly> haan",
        "<emotion:angry> kya",
        "<laugh2> haha",
        "<हंसी> अच्छा",
        "<" + "a" * 35 + ">",
        "₹<amount> dena hai",
    ],
)
def test_anything_tag_shaped_that_is_not_official_is_reported(text: str) -> None:
    assert unofficial_tags(text) != []


def test_official_tags_in_any_case_or_spacing_are_not_reported() -> None:
    assert unofficial_tags("<SIGH> < pauses > <Clears Throat>") == []


def test_canonical_tag_renders_the_documented_form() -> None:
    assert canonical_tag("  Clears   THROAT ") == "<clears throat>"


def test_text_without_angle_brackets_has_no_tags() -> None:
    assert find_tags("Main bees tareekh tak chaar hazaar de dunga.") == []
