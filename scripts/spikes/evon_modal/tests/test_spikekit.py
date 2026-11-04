import pytest
from spikekit import (
    foreign_scripts,
    percentile,
    prompt_sha,
    rate,
    render_markdown,
    summarise,
)

# --- percentile (nearest rank) ---


def test_percentile_of_empty_is_none() -> None:
    assert percentile([], 50) is None


def test_percentile_nearest_rank() -> None:
    values = [0.5, 0.1, 0.4, 0.2, 0.3]
    assert percentile(values, 50) == 0.3
    assert percentile(values, 95) == 0.5
    assert percentile(values, 0) == 0.1
    assert percentile(values, 100) == 0.5


def test_percentile_p95_of_twenty_is_the_nineteenth() -> None:
    values = [float(i) for i in range(1, 21)]
    assert percentile(values, 95) == 19.0


@pytest.mark.parametrize("p", [-1, 101])
def test_percentile_rejects_out_of_range(p: float) -> None:
    with pytest.raises(ValueError):
        percentile([1.0], p)


# --- script switches ---


def test_hinglish_in_devanagari_and_latin_has_no_foreign_script() -> None:
    text = "अरे भाई, salary सात तारीख को आएगी। <sigh> ₹ 4,000 abhi नहीं है!"
    assert foreign_scripts(text) == []


def test_tamil_bengali_and_cjk_are_flagged_sorted() -> None:
    text = "Haan bhai, நான் pay করব 明天"
    assert foreign_scripts(text) == ["BENGALI", "CJK", "TAMIL"]


def test_allowed_scripts_can_be_changed_per_persona() -> None:
    assert foreign_scripts("நான் pay panren", allowed=("LATIN", "TAMIL")) == []


# --- rates and hashes ---


def test_rate() -> None:
    assert rate(0, 0) is None
    assert rate(49, 50) == 0.98


def test_prompt_sha_is_stable_and_order_sensitive() -> None:
    a = [{"role": "system", "content": "x"}, {"role": "user", "content": "y"}]
    b = list(reversed(a))
    assert prompt_sha(a) == prompt_sha([dict(m) for m in a])
    assert prompt_sha(a) != prompt_sha(b)
    assert len(prompt_sha(a)) == 64


# --- summary ---


def _turn(
    ttft: float,
    *,
    errors: list[str] | None = None,
    foreign: list[str] | None = None,
    reasoning: int = 0,
    think_blocks: int = 0,
) -> dict:
    return {
        "ttft_s": ttft,
        "state_errors": errors or [],
        "foreign_scripts": foreign or [],
        "reasoning_chars": reasoning,
        "think_blocks": think_blocks,
        "completion_tokens": 40,
    }


def _run(turns: list[dict], judge: list[dict] | None = None) -> dict:
    return {
        "meta": {"label": "bf16"},
        "thinking_probe": [],
        "turns": turns,
        "judge": judge or [],
        "concurrency": {"n": 8, "wall_s": 2.0, "completion_tokens": 320, "ttft_s": [0.2] * 8},
        "cold_start_s": 41.5,
    }


def test_summary_counts_state_validity_and_errors() -> None:
    run = _run([_turn(0.2), _turn(0.3, errors=["invalid_json"]), _turn(0.4)])
    s = summarise(run)
    assert s["turns"] == 3
    assert s["state_valid"] == 2
    assert s["state_valid_rate"] == pytest.approx(2 / 3)
    assert s["state_errors"] == {"invalid_json": 1}


def test_summary_ttft_percentiles_skip_missing() -> None:
    run = _run([_turn(0.2), _turn(0.4), {**_turn(0.0), "ttft_s": None}])
    s = summarise(run)
    assert s["ttft_p50_s"] == 0.2
    assert s["ttft_p95_s"] == 0.4
    assert s["no_first_token"] == 1


def test_summary_thinking_off_holds_only_with_no_reasoning_anywhere() -> None:
    assert summarise(_run([_turn(0.2), _turn(0.2)]))["thinking_off_ok"] is True
    assert summarise(_run([_turn(0.2), _turn(0.2, reasoning=12)]))["thinking_off_ok"] is False
    assert summarise(_run([_turn(0.2, think_blocks=1)]))["thinking_off_ok"] is False
    assert summarise(_run([]))["thinking_off_ok"] is None


def test_summary_script_switches_count_replies_not_characters() -> None:
    run = _run([_turn(0.2, foreign=["TAMIL", "CJK"]), _turn(0.2), _turn(0.2, foreign=["TAMIL"])])
    s = summarise(run)
    assert s["script_switch_replies"] == 2
    assert s["foreign_scripts_seen"] == ["CJK", "TAMIL"]


def test_summary_judge_validity_and_concurrency() -> None:
    judge = [{"ok": True}, {"ok": False}, {"ok": True}, {"ok": True}]
    s = summarise(_run([_turn(0.2)], judge))
    assert s["judge_calls"] == 4
    assert s["judge_valid_rate"] == 0.75
    assert s["concurrent_tok_per_s"] == 160.0
    assert s["concurrent_ttft_p95_s"] == 0.2
    assert s["cold_start_s"] == 41.5


def test_markdown_marks_numbers_as_measured_by_this_run() -> None:
    md = render_markdown(summarise(_run([_turn(0.2)], [{"ok": True}])), label="bf16")
    assert md.startswith("# Evon spike run: bf16")
    assert "| `###STATE###` valid | 1/1 (100.0%) |" in md
    assert "| TTFT p50 / p95 (thinking off) | 0.200 s / 0.200 s |" in md
    assert "| Judge JSON valid | 1/1 (100.0%) |" in md


def test_markdown_says_not_measured_instead_of_inventing() -> None:
    run = _run([])
    run["cold_start_s"] = None
    md = render_markdown(summarise(run), label="q8")
    assert "| Cold start | not measured |" in md
    assert "| TTFT p50 / p95 (thinking off) | n/a / n/a |" in md


def test_summary_reports_prefix_cache_drop_from_the_repeat_probe() -> None:
    run = _run([_turn(0.2)])
    run["prefix_probe"] = {"uncached_ttft_s": 0.60, "cached_ttft_s": 0.15}
    s = summarise(run)
    assert s["prefix_ttft_uncached_s"] == 0.60
    assert s["prefix_ttft_cached_s"] == 0.15
    assert s["prefix_cache_speedup"] == pytest.approx(4.0)
    assert "| Prefix cache: TTFT fresh → repeated | 0.600 s → 0.150 s (4.00x) |" in render_markdown(
        s, label="bf16"
    )


def test_summary_prefix_cache_not_measured_when_probe_failed() -> None:
    run = _run([_turn(0.2)])
    run["prefix_probe"] = {"uncached_ttft_s": None, "cached_ttft_s": 0.15}
    s = summarise(run)
    assert s["prefix_cache_speedup"] is None
    assert "| Prefix cache: TTFT fresh → repeated | not measured |" in render_markdown(s, label="x")


@pytest.mark.parametrize("err", ["unclosed_think", "orphan_think_close"])
def test_visible_think_tags_count_as_thinking_leaks(err: str) -> None:
    s = summarise(_run([_turn(0.2), _turn(0.2, errors=[err])]))
    assert s["thinking_leaked_replies"] == 1
    assert s["thinking_off_ok"] is False


def test_raw_think_tag_counts_as_a_leak() -> None:
    turn = {**_turn(0.2), "raw": "<think>hmm</think>हाँ भाई"}
    assert summarise(_run([turn]))["thinking_off_ok"] is False


def test_modifier_letters_and_symbols_are_not_a_script_switch() -> None:
    assert foreign_scripts("Ramesh\u02bcs auto, ठीक है") == []  # MODIFIER LETTER APOSTROPHE


def test_summary_reports_health_round_trip_so_ttft_can_be_read_against_it() -> None:
    run = _run([_turn(0.2)])
    run["health_rtt_s"] = [0.05, 0.04, 0.06]
    s = summarise(run)
    assert s["health_rtt_p50_s"] == 0.05
    assert "| Network RTT to server (GET /health p50) | 0.050 s |" in render_markdown(s, label="x")
