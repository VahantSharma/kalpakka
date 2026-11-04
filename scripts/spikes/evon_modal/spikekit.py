"""Pure helpers for the Evon spike: percentiles, script-switch detection, prompt hashing and the
run summary. Stdlib only, so the quant gate and the spike runner share one definition of each
number. No number here is ever filled in when it was not measured: missing values stay None and
print as "n/a" / "not measured".
"""

import hashlib
import json
import math
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

DEFAULT_ALLOWED_SCRIPTS = ("LATIN", "DEVANAGARI")  # Ramesh: Hindi in Devanagari, English in Latin
_IGNORED_NAME_PREFIXES = ("COMBINING", "ZERO WIDTH", "VARIATION SELECTOR", "MODIFIER")
_THINK_ERRORS = frozenset({"unclosed_think", "orphan_think_close"})


def percentile(values: Iterable[float], p: float) -> float | None:
    """Nearest-rank percentile (no interpolation): always a value that was really observed."""
    if not 0 <= p <= 100:
        raise ValueError("p must be in [0, 100]")
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def foreign_scripts(text: str, allowed: Sequence[str] = DEFAULT_ALLOWED_SCRIPTS) -> list[str]:
    """Scripts of letters/marks in `text` outside `allowed` (sorted, unique)."""
    seen: set[str] = set()
    for ch in text:
        if unicodedata.category(ch)[0] not in ("L", "M"):
            continue
        name = unicodedata.name(ch, "")
        if not name or name.startswith(_IGNORED_NAME_PREFIXES):
            continue
        script = name.split(" ", 1)[0]
        if script not in allowed:
            seen.add(script)
    return sorted(seen)


def rate(ok: int, total: int) -> float | None:
    return None if total == 0 else ok / total


def prompt_sha(messages: Sequence[Mapping[str, str]]) -> str:
    """sha256 of the exact prompt, so two runs can prove they answered identical prompts."""
    canonical = json.dumps(
        [dict(m) for m in messages], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def thinking_leaked(turn: Mapping[str, Any]) -> bool:
    """Reasoning reached us at all: a reasoning field, a think block, a cut-off or orphan tag."""
    return bool(
        turn.get("reasoning_chars", 0) > 0
        or turn.get("think_blocks", 0)
        or _THINK_ERRORS & set(turn.get("state_errors", []))
        or "<think>" in turn.get("raw", "")
    )


def summarise(run: Mapping[str, Any]) -> dict[str, Any]:
    turns: list[Mapping[str, Any]] = run.get("turns", [])
    judge: list[Mapping[str, Any]] = run.get("judge", [])
    conc: Mapping[str, Any] = run.get("concurrency") or {}

    errors: Counter[str] = Counter()
    for t in turns:
        errors.update(t["state_errors"])
    state_valid = sum(1 for t in turns if not t["state_errors"])
    ttfts = [t["ttft_s"] for t in turns if t.get("ttft_s") is not None]
    leaked = sum(1 for t in turns if thinking_leaked(t))
    switch_replies = [t for t in turns if t["foreign_scripts"]]
    seen = sorted({s for t in switch_replies for s in t["foreign_scripts"]})
    judge_ok = sum(1 for j in judge if j["ok"])

    probe: Mapping[str, Any] = run.get("prefix_probe") or {}
    fresh, again = probe.get("uncached_ttft_s"), probe.get("cached_ttft_s")
    wall = conc.get("wall_s")
    toks = conc.get("completion_tokens")
    return {
        "turns": len(turns),
        "state_valid": state_valid,
        "state_valid_rate": rate(state_valid, len(turns)),
        "state_errors": dict(sorted(errors.items())),
        "ttft_p50_s": percentile(ttfts, 50),
        "ttft_p95_s": percentile(ttfts, 95),
        "no_first_token": len(turns) - len(ttfts),
        "thinking_leaked_replies": leaked,
        "thinking_off_ok": None if not turns else leaked == 0,
        "thinking_off_variant": run.get("thinking_off_variant"),
        "script_switch_replies": len(switch_replies),
        "foreign_scripts_seen": seen,
        "judge_calls": len(judge),
        "judge_valid": judge_ok,
        "judge_valid_rate": rate(judge_ok, len(judge)),
        "concurrent_n": conc.get("n"),
        "concurrent_tok_per_s": (toks / wall) if wall and toks is not None else None,
        "concurrent_ttft_p95_s": percentile(conc.get("ttft_s") or [], 95),
        "cold_start_s": run.get("cold_start_s"),
        "health_rtt_p50_s": percentile(run.get("health_rtt_s") or [], 50),
        "prefix_ttft_uncached_s": fresh,
        "prefix_ttft_cached_s": again,
        "prefix_cache_speedup": (fresh / again) if fresh and again else None,
    }


def _frac(ok: int, total: int) -> str:
    return "n/a" if total == 0 else f"{ok}/{total} ({100 * ok / total:.1f}%)"


def _secs(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f} s"


def render_markdown(s: Mapping[str, Any], *, label: str) -> str:
    def yes_no(v: bool | None) -> str:
        return "not measured" if v is None else ("yes" if v else "NO")

    tps = s["concurrent_tok_per_s"]
    speedup = s.get("prefix_cache_speedup")
    prefix = (
        "not measured"
        if speedup is None
        else f"{_secs(s['prefix_ttft_uncached_s'])} → {_secs(s['prefix_ttft_cached_s'])} "
        f"({speedup:.2f}x)"
    )
    rows = [
        ("`###STATE###` valid", _frac(s["state_valid"], s["turns"])),
        ("STATE errors", ", ".join(f"{k} x{v}" for k, v in s["state_errors"].items()) or "none"),
        ("TTFT p50 / p95 (thinking off)", f"{_secs(s['ttft_p50_s'])} / {_secs(s['ttft_p95_s'])}"),
        ("Replies with no first token", str(s["no_first_token"])),
        ("Thinking off held", yes_no(s["thinking_off_ok"])),
        ("Thinking-off request field that worked", str(s["thinking_off_variant"] or "none")),
        ("Replies with a script switch", str(s["script_switch_replies"])),
        ("Foreign scripts seen", ", ".join(s["foreign_scripts_seen"]) or "none"),
        ("Judge JSON valid", _frac(s["judge_valid"], s["judge_calls"])),
        (
            f"{s['concurrent_n'] or '?'}-concurrent throughput",
            "not measured" if tps is None else f"{tps:.1f} completion tok/s",
        ),
        ("Concurrent TTFT p95", _secs(s["concurrent_ttft_p95_s"])),
        ("Prefix cache: TTFT fresh → repeated", prefix),
        ("Network RTT to server (GET /health p50)", _secs(s.get("health_rtt_p50_s"))),
        (
            "Cold start",
            "not measured" if s["cold_start_s"] is None else f"{s['cold_start_s']:.1f} s",
        ),
    ]
    lines = [f"# Evon spike run: {label}", "", "| Metric | Value |", "|--------|-------|"]
    lines += [f"| {k} | {v} |" for k, v in rows]
    return "\n".join(lines) + "\n"
