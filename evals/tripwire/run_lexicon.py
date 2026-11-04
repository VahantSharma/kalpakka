"""Tripwire eval, lexicon layer alone: precision and recall on evals/tripwire/utterances.yaml.

Run from backend/:  uv run --frozen --offline python ../evals/tripwire/run_lexicon.py
(or `make eval-tripwire` from the repo root). Prints a summary and writes the full result,
with lexicon and set versions, to evals/runs/ (git-ignored; paste the summary into the log).
"""

import datetime as dt
import json
import sys
from pathlib import Path

import yaml

from kalpakka.evalkit import Counts, score_labels
from kalpakka.tripwire import LexiconTripwire, load_catalogue, load_lexicon

ROOT = Path(__file__).resolve().parents[2]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def line(name: str, c: Counts) -> str:
    return (
        f"{name:<12} tp={c.tp:<3} fp={c.fp:<3} fn={c.fn:<3} "
        f"precision={pct(c.precision)} recall={pct(c.recall)}"
    )


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Windows cp1252 console
    catalogue = load_catalogue(ROOT / "content/rules/catalogue.yaml")
    lexicon = load_lexicon(ROOT / "content/rules/lexicon.yaml", catalogue)
    tripwire = LexiconTripwire(lexicon)
    data = yaml.safe_load((ROOT / "evals/tripwire/utterances.yaml").read_text(encoding="utf-8"))

    rows = []
    expected: list[set[str]] = []
    predicted: list[set[str]] = []
    for item in data["utterances"]:
        flags = tripwire.check(item["text"], turn=0, segment=0, t=0.0)
        got = {f.rule for f in flags}
        want = set(item["labels"])
        expected.append(want)
        predicted.append(got)
        rows.append(
            {
                "text": item["text"],
                "expected": sorted(want),
                "predicted": sorted(got),
                "quotes": [f.quote for f in flags],
            }
        )

    report = score_labels(expected, predicted)
    now = dt.datetime.now(IST)
    print(
        f"tripwire lexicon eval · {now:%Y-%m-%d %H:%M} IST · lexicon v{lexicon.version} · "
        f"set v{data['version']} · n={len(rows)}"
    )
    print(line("utterance", report.overall))
    for rule, counts in report.per_rule.items():
        print(line(rule, counts))
    print("misses and false alarms:")
    for r in rows:
        if r["expected"] != r["predicted"]:
            print(f"  want={r['expected']} got={r['predicted']}  {r['text']}")

    out_dir = ROOT / "evals/runs"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"tripwire-lexicon-{now:%Y%m%d-%H%M%S}.json"
    out.write_text(
        json.dumps(
            {
                "run_at": now.isoformat(),
                "layer": "lexicon",
                "lexicon_version": lexicon.version,
                "set_version": data["version"],
                "overall": report.overall.model_dump(),
                "per_rule": {k: v.model_dump() for k, v in report.per_rule.items()},
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"written: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
