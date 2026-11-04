# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Quant gate CLI (stdlib only). Run from the repo root:

1. Build the blind A/B page (keys go to a SEPARATE file; do not open it before scoring):
     uv run --script evals/quant_gate/cli.py ab evals/runs/<bf16>.json evals/runs/<q8>.json
2. Open evals/runs/ab-<stamp>/ab.html in a browser, pick all 20, download ab-choices.csv.
3. Unblind:
     uv run --script evals/quant_gate/cli.py score \
         ~/Downloads/ab-choices.csv evals/runs/ab-keys-<stamp>.json
4. Gate (all seven gates; prints markdown, writes JSON next to the runs):
     uv run --script evals/quant_gate/cli.py gate <bf16>.json <q8>.json \
         --ab evals/runs/ab-score-<stamp>.json
"""

import argparse
import datetime as dt
import json
import secrets
import sys
from pathlib import Path

from quantgate import build_ab, evaluate, render_ab_html, render_gate_markdown, score_ab

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "evals" / "runs"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _load(path: Path) -> dict:
    data: dict = json.loads(path.read_text(encoding="utf-8"))
    return data


def _stamp() -> str:
    return dt.datetime.now(IST).strftime("%Y%m%d-%H%M%S")


def cmd_ab(args: argparse.Namespace) -> None:
    seed = args.seed if args.seed is not None else secrets.randbelow(2**31)
    pairs, keys = build_ab(_load(args.ref), _load(args.cand), seed=seed)
    keys["ref_file"], keys["cand_file"] = args.ref.name, args.cand.name
    stamp = _stamp()
    page_dir = RUNS / f"ab-{stamp}"
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "ab.html").write_text(render_ab_html(pairs), encoding="utf-8")
    keys_file = RUNS / f"ab-keys-{stamp}.json"
    keys_file.write_text(json.dumps(keys, ensure_ascii=False, indent=1) + "\n")
    print(f"[ab] page: {(page_dir / 'ab.html').relative_to(ROOT)}  ({len(pairs)} pairs)")
    print(f"[ab] keys: {keys_file.relative_to(ROOT)}  (do NOT open until you have scored)")


def cmd_score(args: argparse.Namespace) -> None:
    keys = _load(args.keys)
    result = score_ab(args.csv.read_text(encoding="utf-8"), keys)
    result.update(candidate=keys["candidate"], reference=keys["reference"], seed=keys["seed"])
    out = RUNS / f"ab-score-{_stamp()}.json"
    out.write_text(json.dumps(result, indent=1) + "\n")
    print(
        f"[score] {keys['candidate']} picked {result['cand_picks']}/{result['answered']} "
        f"= {result['pick_rate']:.0%} (need ≥ 40%): {result['status']}"
    )
    print(f"[score] wrote {out.relative_to(ROOT)}")


def cmd_gate(args: argparse.Namespace) -> None:
    ab = _load(args.ab) if args.ab else None
    result = evaluate(
        _load(args.ref),
        _load(args.cand),
        ab_pick_rate=ab["pick_rate"] if ab else None,
        ab_labels=(ab["reference"], ab["candidate"]) if ab else None,
    )
    md = render_gate_markdown(result)
    out = RUNS / f"quant-gate-{result['candidate']}-{_stamp()}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    out.with_suffix(".md").write_text(md)
    print(md)
    print(f"[gate] wrote {out.relative_to(ROOT)} and .md")
    sys.exit(0 if result["overall"] == "PASS" else 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evon quant gate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ab", help="build the blind A/B page and a separate keys file")
    p.add_argument("ref", type=Path)
    p.add_argument("cand", type=Path)
    p.add_argument("--seed", type=int, help="default: random, recorded in the keys file")
    p.set_defaults(fn=cmd_ab)
    p = sub.add_parser("score", help="unblind the downloaded CSV")
    p.add_argument("csv", type=Path)
    p.add_argument("keys", type=Path)
    p.set_defaults(fn=cmd_score)
    p = sub.add_parser("gate", help="evaluate all gates (exit 0 only on PASS)")
    p.add_argument("ref", type=Path)
    p.add_argument("cand", type=Path)
    p.add_argument("--ab", type=Path, help="ab-score JSON from the score step")
    p.set_defaults(fn=cmd_gate)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
