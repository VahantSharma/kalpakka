# ruff: noqa: E501  (the embedded HTML/CSS page keeps its natural line lengths)
"""Quant gate: may a quantised Evon (Q8_0 GGUF, or FP8) replace the BF16 reference for live turns?

Pre-registered in docs/decisions/ADR-001-evon-hosting.md before any run. Every gate must PASS;
a value that was not measured FAILS (it is never assumed). The thresholds below are the ADR's and
are not to be tuned after seeing results.

Inputs are two `run_spike.py` JSON files (reference first) and, for the last gate, Vahant's blind
A/B choices. Stdlib only.
"""

import csv
import html
import io
import random
from collections.abc import Mapping
from typing import Any, Final

STATE_MIN: Final = 0.98
JUDGE_MIN: Final = 0.95
TTFT_SLACK: Final = 1.10  # candidate p50 and p95 may be at most 10% above the reference
AB_PAIRS: Final = 20
AB_MIN_PICK_RATE: Final = 0.40

Run = Mapping[str, Any]


def _gate(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _at_least(name: str, value: float | None, minimum: float) -> dict[str, str]:
    if value is None:
        return _gate(name, "FAIL", "not measured")
    ok = value >= minimum
    return _gate(name, "PASS" if ok else "FAIL", f"{value:.3f} (need ≥ {minimum:.2f})")


def _same_prompts(ref: Run, cand: Run) -> dict[str, str]:
    r = {t["id"]: t["prompt_sha"] for t in ref["turns"]}
    c = {t["id"]: t["prompt_sha"] for t in cand["turns"]}
    if r.keys() != c.keys():
        return _gate("same_prompts", "FAIL", f"turn sets differ ({len(r)} vs {len(c)} turns)")
    diff = sorted(k for k in r if r[k] != c[k])
    if diff:
        return _gate("same_prompts", "FAIL", f"{len(diff)} prompts differ, e.g. {diff[0]}")
    return _gate("same_prompts", "PASS", f"{len(r)} identical prompt hashes")


def _ttft(ref: Mapping[str, Any], cand: Mapping[str, Any]) -> dict[str, str]:
    parts = []
    ok = True
    for key in ("ttft_p50_s", "ttft_p95_s"):
        r, c = ref.get(key), cand.get(key)
        if r is None or c is None:
            return _gate("ttft_not_worse", "FAIL", f"{key} not measured")
        limit = r * TTFT_SLACK
        ok = ok and c <= limit + 1e-12
        parts.append(f"{key[5:8]} {c:.3f} s vs ref {r:.3f} s (limit {limit:.3f})")
    r_rtt, c_rtt = ref.get("health_rtt_p50_s"), cand.get("health_rtt_p50_s")
    if r_rtt is not None and c_rtt is not None:  # context only: TTFT includes the network
        parts.append(f"RTT ref {r_rtt:.3f} s / cand {c_rtt:.3f} s")
    return _gate("ttft_not_worse", "PASS" if ok else "FAIL", "; ".join(parts))


def evaluate(
    ref: Run,
    cand: Run,
    *,
    ab_pick_rate: float | None,
    ab_labels: tuple[str | None, str | None] | None = None,
) -> dict[str, Any]:
    """`ab_labels` = (reference, candidate) labels the A/B score was built from; a score for
    other runs is refused rather than silently used."""
    if ab_labels is not None:
        expected = (ref["meta"].get("label"), cand["meta"].get("label"))
        if tuple(ab_labels) != expected:
            raise ValueError(f"A/B score is for {ab_labels}, not for {expected}")
    rs, cs = ref["summary"], cand["summary"]
    thinking = cs.get("thinking_off_ok")
    switches = cs.get("script_switch_replies")
    incomplete = [r["meta"].get("label") for r in (ref, cand) if r.get("complete") is False]
    gates = [
        _gate(
            "runs_complete",
            "FAIL" if incomplete else "PASS",
            f"incomplete: {', '.join(map(str, incomplete))}" if incomplete else "both runs finished",
        ),
        _same_prompts(ref, cand),
        _at_least("state_valid", cs.get("state_valid_rate"), STATE_MIN),
        _at_least("judge_json_valid", cs.get("judge_valid_rate"), JUDGE_MIN),
        _gate(
            "thinking_off",
            "PASS" if thinking is True else "FAIL",
            "not measured" if thinking is None else f"held={thinking}",
        ),
        _gate(
            "no_script_switch",
            "PASS" if switches == 0 else "FAIL",
            "not measured" if switches is None else f"{switches} replies switched script",
        ),
        _ttft(rs, cs),
        (
            _gate("blind_ab", "PENDING", "Vahant's A/B not scored yet")
            if ab_pick_rate is None
            else _at_least("blind_ab", ab_pick_rate, AB_MIN_PICK_RATE)
        ),
    ]
    statuses = {g["status"] for g in gates}
    overall = "FAIL" if "FAIL" in statuses else ("PENDING" if "PENDING" in statuses else "PASS")
    return {
        "reference": ref["meta"].get("label"),
        "candidate": cand["meta"].get("label"),
        "gates": gates,
        "overall": overall,
    }


def render_gate_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        f"# Quant gate: {result['candidate']} vs {result['reference']}",
        "",
        "| Gate | Status | Detail |",
        "|------|--------|--------|",
    ]
    lines += [f"| {g['name']} | {g['status']} | {g['detail']} |" for g in result["gates"]]
    lines += ["", f"**Overall: {result['overall']}**"]
    return "\n".join(lines) + "\n"


# --- blind A/B ---------------------------------------------------------------------------------


def build_ab(ref: Run, cand: Run, *, seed: int) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """20 pairs of replies to the same turn, sides and order shuffled by `seed`.

    Returns (pairs for the page, keys kept in a separate file). Pair ids carry no information.
    """
    c_by_id = {t["id"]: t for t in cand["turns"]}
    usable = [
        (r, c_by_id[r["id"]])
        for r in ref["turns"]
        if r["id"] in c_by_id and r.get("speech", "").strip() and c_by_id[r["id"]].get("speech", "").strip()
        and r["prompt_sha"] == c_by_id[r["id"]]["prompt_sha"]
    ]  # fmt: skip
    if len(usable) < AB_PAIRS:
        raise ValueError(f"need {AB_PAIRS} turns with speech from both runs, have {len(usable)}")
    rng = random.Random(seed)
    chosen = rng.sample(usable, AB_PAIRS)
    pairs: list[dict[str, str]] = []
    keys: dict[str, Any] = {
        "seed": seed,
        "reference": ref["meta"].get("label"),
        "candidate": cand["meta"].get("label"),
        "pairs": {},
    }
    for i, (r, c) in enumerate(chosen, start=1):
        pid = f"p{i:02d}"
        cand_first = rng.random() < 0.5
        a, b = (c, r) if cand_first else (r, c)
        pairs.append(
            {
                "pair_id": pid,
                "turn_id": r["id"],
                "agent": r.get("agent", ""),
                "A": a["speech"],
                "B": b["speech"],
            }
        )
        keys["pairs"][pid] = {
            "turn_id": r["id"],
            "A": "cand" if cand_first else "ref",
            "B": "ref" if cand_first else "cand",
        }
    return pairs, keys


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Evon blind A/B</title>
<style>
:root {{ --bg:#fafaf7; --fg:#1b1b18; --muted:#6b6b63; --card:#fff; --line:#deded6; --pick:#1f6feb; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#141412; --fg:#ecece6; --muted:#9a9a90; --card:#1d1d1a; --line:#34342f; --pick:#58a6ff; }}
}}
body {{ background:var(--bg); color:var(--fg); font:16px/1.5 system-ui, sans-serif;
       margin:0 auto; max-width:760px; padding:16px; }}
.pair {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px;
        margin:14px 0; }}
.agent {{ color:var(--muted); font-size:14px; }}
.opts {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:8px; }}
@media (max-width:560px) {{ .opts {{ grid-template-columns:1fr; }} }}
button.opt {{ text-align:left; font:inherit; color:inherit; background:transparent; cursor:pointer;
             border:2px solid var(--line); border-radius:8px; padding:10px; }}
button.opt.on {{ border-color:var(--pick); }}
#bar {{ position:sticky; bottom:0; background:var(--bg); padding:10px 0; border-top:1px solid var(--line); }}
</style></head><body>
<h1>Which reply sounds more like Ramesh?</h1>
<p>{n} pairs. Each pair answers the same agent line. Pick the reply that is more believable as a
stressed Delhi auto driver on a recovery call (language mix, tone, follows the situation). Every
pair needs a pick. Then download the CSV.</p>
{pairs}
<div id="bar"><span id="count">0/{n} picked</span>
<button id="dl" disabled>Download CSV</button></div>
<script>
const picks = {{}};
const total = {n};
document.querySelectorAll("button.opt").forEach(btn => btn.addEventListener("click", () => {{
  const pid = btn.dataset.pair;
  picks[pid] = btn.dataset.choice;
  document.querySelectorAll(`button.opt[data-pair="${{pid}}"]`)
    .forEach(b => b.classList.toggle("on", b === btn));
  const done = Object.keys(picks).length;
  document.getElementById("count").textContent = `${{done}}/${{total}} picked`;
  document.getElementById("dl").disabled = done !== total;
}}));
document.getElementById("dl").addEventListener("click", () => {{
  const rows = ["pair_id,choice", ...Object.keys(picks).sort().map(p => `${{p}},${{picks[p]}}`)];
  const blob = new Blob([rows.join("\\n") + "\\n"], {{type: "text/csv"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "ab-choices.csv";
  a.click();
}});
</script></body></html>
"""


def render_ab_html(pairs: list[dict[str, str]]) -> str:
    """The page shows only the agent line and the two replies; turn ids and keys never appear."""
    blocks = []
    for p in pairs:
        pid = html.escape(p["pair_id"])
        opts = "".join(
            f'<button class="opt" data-pair="{pid}" data-choice="{side}"><b>{side}</b> '
            f"{html.escape(p[side])}</button>"
            for side in ("A", "B")
        )
        blocks.append(
            f'<div class="pair"><div class="agent">{pid} · Agent: {html.escape(p["agent"])}</div>'
            f'<div class="opts">{opts}</div></div>'
        )
    return _PAGE.format(n=len(pairs), pairs="\n".join(blocks))


def score_ab(csv_text: str, keys: Mapping[str, Any]) -> dict[str, Any]:
    """Unblind Vahant's choices. Every pair must be answered exactly once with A or B."""
    known = keys["pairs"]
    seen: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        pid, choice = row["pair_id"].strip(), row["choice"].strip()
        if pid not in known:
            raise ValueError(f"unknown pair id {pid!r}")
        if choice not in ("A", "B"):
            raise ValueError(f"choice for {pid} must be A or B, got {choice!r}")
        if pid in seen:
            raise ValueError(f"pair {pid} answered twice")
        seen[pid] = choice
    if len(seen) != len(known):
        raise ValueError(f"answered {len(seen)} of {len(known)} pairs; every pair needs a pick")
    cand = sum(1 for pid, choice in seen.items() if known[pid][choice] == "cand")
    pick_rate = cand / len(known)
    return {
        "answered": len(seen),
        "cand_picks": cand,
        "pick_rate": pick_rate,
        "status": "PASS" if pick_rate >= AB_MIN_PICK_RATE else "FAIL",
    }
