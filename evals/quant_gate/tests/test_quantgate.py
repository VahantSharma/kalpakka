import csv
import io
import json

import pytest
from quantgate import (
    AB_MIN_PICK_RATE,
    AB_PAIRS,
    build_ab,
    evaluate,
    render_ab_html,
    render_gate_markdown,
    score_ab,
)


def _summary(**over: object) -> dict:
    base = {
        "state_valid_rate": 1.0,
        "judge_valid_rate": 1.0,
        "thinking_off_ok": True,
        "script_switch_replies": 0,
        "ttft_p50_s": 0.20,
        "ttft_p95_s": 0.40,
    }
    return {**base, **over}


def _run(label: str, n: int = 30, summary: dict | None = None, sha: str = "s") -> dict:
    turns = [
        {"id": f"scene#{i}", "prompt_sha": f"{sha}{i}", "agent": f"agent line {i}",
         "speech": f"{label} reply {i}"}
        for i in range(n)
    ]  # fmt: skip
    return {"meta": {"label": label}, "turns": turns, "summary": summary or _summary()}


def _gate(result: dict, name: str) -> dict:
    return next(g for g in result["gates"] if g["name"] == name)


# --- gates ---


def test_identical_good_runs_pass_everything_but_ab_is_pending() -> None:
    result = evaluate(_run("bf16"), _run("q8"), ab_pick_rate=None)
    assert all(g["status"] == "PASS" for g in result["gates"] if g["name"] != "blind_ab")
    assert _gate(result, "blind_ab")["status"] == "PENDING"
    assert result["overall"] == "PENDING"


def test_all_pass_with_ab_at_threshold() -> None:
    result = evaluate(_run("bf16"), _run("q8"), ab_pick_rate=0.40)
    assert result["overall"] == "PASS"


def test_ab_below_threshold_fails() -> None:
    result = evaluate(_run("bf16"), _run("q8"), ab_pick_rate=0.35)
    assert _gate(result, "blind_ab")["status"] == "FAIL"
    assert result["overall"] == "FAIL"


@pytest.mark.parametrize(
    ("field", "value", "gate"),
    [
        ("state_valid_rate", 0.979, "state_valid"),
        ("judge_valid_rate", 0.949, "judge_json_valid"),
        ("thinking_off_ok", False, "thinking_off"),
        ("thinking_off_ok", None, "thinking_off"),
        ("script_switch_replies", 1, "no_script_switch"),
        ("state_valid_rate", None, "state_valid"),
    ],
)
def test_each_candidate_gate_fails_on_its_own(field: str, value: object, gate: str) -> None:
    cand = _run("q8", summary=_summary(**{field: value}))
    result = evaluate(_run("bf16"), cand, ab_pick_rate=0.5)
    assert _gate(result, gate)["status"] == "FAIL"
    assert result["overall"] == "FAIL"


def test_thresholds_are_inclusive() -> None:
    cand = _run("q8", summary=_summary(state_valid_rate=0.98, judge_valid_rate=0.95))
    result = evaluate(_run("bf16"), cand, ab_pick_rate=0.5)
    assert _gate(result, "state_valid")["status"] == "PASS"
    assert _gate(result, "judge_json_valid")["status"] == "PASS"


def test_ttft_allows_ten_percent_and_no_more() -> None:
    ref = _run("bf16", summary=_summary(ttft_p50_s=0.20, ttft_p95_s=0.40))
    ok = _run("q8", summary=_summary(ttft_p50_s=0.22, ttft_p95_s=0.44))
    slow = _run("q8", summary=_summary(ttft_p50_s=0.2201, ttft_p95_s=0.40))
    assert _gate(evaluate(ref, ok, ab_pick_rate=0.5), "ttft_not_worse")["status"] == "PASS"
    assert _gate(evaluate(ref, slow, ab_pick_rate=0.5), "ttft_not_worse")["status"] == "FAIL"


def test_ttft_missing_on_either_side_fails() -> None:
    ref = _run("bf16", summary=_summary(ttft_p95_s=None))
    result = evaluate(ref, _run("q8"), ab_pick_rate=0.5)
    assert _gate(result, "ttft_not_worse")["status"] == "FAIL"


def test_different_prompts_make_the_runs_not_comparable() -> None:
    result = evaluate(_run("bf16"), _run("q8", sha="other"), ab_pick_rate=0.5)
    assert _gate(result, "same_prompts")["status"] == "FAIL"
    assert result["overall"] == "FAIL"


def test_missing_turns_make_the_runs_not_comparable() -> None:
    result = evaluate(_run("bf16"), _run("q8", n=29), ab_pick_rate=0.5)
    assert _gate(result, "same_prompts")["status"] == "FAIL"


def test_gate_markdown_has_one_row_per_gate_and_the_verdict() -> None:
    result = evaluate(_run("bf16"), _run("q8"), ab_pick_rate=None)
    md = render_gate_markdown(result)
    assert md.count("\n| ") == len(result["gates"]) + 1  # header + rows
    assert "**Overall: PENDING**" in md


# --- blind A/B ---


def test_build_ab_makes_twenty_pairs_of_the_same_turn() -> None:
    pairs, keys = build_ab(_run("bf16"), _run("q8"), seed=7)
    assert len(pairs) == AB_PAIRS == 20
    for p in pairs:
        n = p["turn_id"].split("#")[1]
        assert {p["A"], p["B"]} == {f"bf16 reply {n}", f"q8 reply {n}"}
        assert keys["pairs"][p["pair_id"]][(p["A"].split(" ")[0] == "q8" and "A") or "B"] == "cand"


def test_shuffle_is_seeded_and_both_sides_are_used() -> None:
    a1, k1 = build_ab(_run("bf16"), _run("q8"), seed=7)
    a2, k2 = build_ab(_run("bf16"), _run("q8"), seed=7)
    b, _ = build_ab(_run("bf16"), _run("q8"), seed=8)
    assert a1 == a2 and k1 == k2
    assert a1 != b
    cand_on_a = sum(1 for v in k1["pairs"].values() if v["A"] == "cand")
    assert 0 < cand_on_a < AB_PAIRS


def test_pairs_skip_turns_with_empty_speech() -> None:
    ref = _run("bf16")
    ref["turns"][0]["speech"] = ""
    pairs, _ = build_ab(ref, _run("q8"), seed=1)
    assert "scene#0" not in {p["turn_id"] for p in pairs}


def test_too_few_usable_turns_is_an_error() -> None:
    with pytest.raises(ValueError, match="20"):
        build_ab(_run("bf16", n=10), _run("q8", n=10), seed=1)


def test_html_hides_labels_keys_and_turn_ids() -> None:
    pairs, keys = build_ab(_run("bf16"), _run("q8"), seed=3)
    html = render_ab_html(pairs)
    assert "bf16 reply" in html  # the replies themselves are shown
    for secret in ("cand", '"ref"', ">ref<", '"bf16"', '"q8"', "scene#", json.dumps(keys)[:40]):
        assert secret not in html
    assert html.count('class="pair"') == AB_PAIRS


def test_html_escapes_reply_text() -> None:
    ref = _run("bf16")
    for t in ref["turns"]:
        t["speech"] = "<script>alert(1)</script> ठीक है"
    pairs, _ = build_ab(ref, _run("q8"), seed=3)
    html = render_ab_html(pairs)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def _csv(rows: list[tuple[str, str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["pair_id", "choice"])
    w.writerows(rows)
    return buf.getvalue()


def test_score_unblinds_and_computes_candidate_pick_rate() -> None:
    pairs, keys = build_ab(_run("bf16"), _run("q8"), seed=5)
    cand_side = {pid: ("A" if v["A"] == "cand" else "B") for pid, v in keys["pairs"].items()}
    ids = [p["pair_id"] for p in pairs]
    picks = [(pid, cand_side[pid]) for pid in ids[:8]]
    picks += [(pid, "B" if cand_side[pid] == "A" else "A") for pid in ids[8:]]
    result = score_ab(_csv(picks), keys)
    assert result["answered"] == 20
    assert result["cand_picks"] == 8
    assert result["pick_rate"] == pytest.approx(0.40)
    assert result["status"] == "PASS"
    assert AB_MIN_PICK_RATE == 0.40


def test_score_rejects_incomplete_or_unknown_answers() -> None:
    pairs, keys = build_ab(_run("bf16"), _run("q8"), seed=5)
    ids = [p["pair_id"] for p in pairs]
    with pytest.raises(ValueError, match="19 of 20"):
        score_ab(_csv([(pid, "A") for pid in ids[:19]]), keys)
    with pytest.raises(ValueError, match="unknown pair"):
        score_ab(_csv([(pid, "A") for pid in ids[:19]] + [("zz", "A")]), keys)
    with pytest.raises(ValueError, match="choice"):
        score_ab(_csv([(pid, "C") for pid in ids]), keys)
    with pytest.raises(ValueError, match="twice"):
        score_ab(_csv([(ids[0], "A")] * 2 + [(pid, "A") for pid in ids[1:]]), keys)


def test_ab_score_for_other_runs_is_refused() -> None:
    with pytest.raises(ValueError, match="A/B score is for"):
        evaluate(_run("bf16"), _run("q8"), ab_pick_rate=0.5, ab_labels=("bf16", "q4"))


def test_ab_score_for_the_same_runs_is_used() -> None:
    result = evaluate(_run("bf16"), _run("q8"), ab_pick_rate=0.5, ab_labels=("bf16", "q8"))
    assert result["overall"] == "PASS"


def test_ttft_gate_detail_shows_network_rtt_when_measured() -> None:
    ref = _run("bf16", summary=_summary(health_rtt_p50_s=0.050))
    cand = _run("q8", summary=_summary(health_rtt_p50_s=0.080))
    detail = next(g for g in evaluate(ref, cand, ab_pick_rate=0.5)["gates"]
                  if g["name"] == "ttft_not_worse")["detail"]  # fmt: skip
    assert "RTT ref 0.050 s / cand 0.080 s" in detail


def test_an_incomplete_run_cannot_pass_the_gate() -> None:
    cand = {**_run("q8"), "complete": False}
    result = evaluate(_run("bf16"), cand, ab_pick_rate=0.5)
    assert next(g for g in result["gates"] if g["name"] == "runs_complete")["status"] == "FAIL"
    assert result["overall"] == "FAIL"
