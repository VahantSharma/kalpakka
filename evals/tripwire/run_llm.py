"""Tripwire eval, lexicon + LLM layer vs lexicon alone, on a frozen utterance set (09).

Run from backend/ with LLM_BASE_URL, LLM_API_KEY, LLM_MODEL set:
    uv run --frozen --offline python ../evals/tripwire/run_llm.py [utterances.yaml]
Each utterance is one agent segment with no call context. Final flags = lexicon flags not
withdrawn, plus flags the LLM layer added. Writes the result with lexicon, prompt and set versions
to evals/runs/ (git-ignored). Numbers are real only against a real model.
"""

import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

import yaml

from kalpakka.brain import load_prompt
from kalpakka.config import Settings
from kalpakka.evalkit import Counts, score_labels
from kalpakka.judge_runner import JsonSchemaAdapter
from kalpakka.llm import ChatTemplateThinkingAdapter, LLMClient
from kalpakka.llm_tripwire import LLMCheck, LLMTripwire
from kalpakka.tripwire import LexiconTripwire, load_catalogue, load_lexicon

ROOT = Path(__file__).resolve().parents[2]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def line(name: str, c: Counts) -> str:
    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.2f}"

    return (f"{name:<12} tp={c.tp:<3} fp={c.fp:<3} fn={c.fn:<3} "
            f"precision={pct(c.precision)} recall={pct(c.recall)}")  # fmt: skip


async def main_async(set_name: str) -> int:
    catalogue = load_catalogue(ROOT / "content/rules/catalogue.yaml")
    lexicon_file = load_lexicon(ROOT / "content/rules/lexicon.yaml", catalogue)
    lexicon = LexiconTripwire(lexicon_file)
    prompt = load_prompt(ROOT / "content/prompts/tripwire-check.md")
    settings = Settings()
    adapter = JsonSchemaAdapter(ChatTemplateThinkingAdapter(), name="tripwire_check", model=LLMCheck)
    llm = LLMClient(base_url=settings.llm_base_url, api_key=settings.llm_api_key.get_secret_value(),
                    model=settings.llm_model, adapter=adapter)  # fmt: skip
    wire = LLMTripwire(llm, prompt, catalogue)
    data = yaml.safe_load((ROOT / "evals/tripwire" / set_name).read_text(encoding="utf-8"))

    expected, lex_only, combined, rows = [], [], [], []
    for i, item in enumerate(data["utterances"]):
        flags = lexicon.check(item["text"], turn=1, segment=i, t=0.0)
        verdict = await wire.check(item["text"], turn=1, segment=i, t=0.0, context=[],
                                   lexicon_flags=flags)  # fmt: skip
        final = {f.id: f for f in flags}
        for u in verdict.updates:
            final[u.id] = u
        got = {f.rule for f in final.values() if f.status != "withdrawn"}
        expected.append(set(item["labels"]))
        lex_only.append({f.rule for f in flags})
        combined.append(got)
        rows.append({"text": item["text"], "expected": item["labels"],
                     "lexicon": sorted({f.rule for f in flags}), "combined": sorted(got),
                     "llm": verdict.check.model_dump() if verdict.check else None,
                     "error": verdict.error})  # fmt: skip

    now = dt.datetime.now(IST)
    print(f"tripwire eval · {now:%Y-%m-%d %H:%M} IST · lexicon v{lexicon_file.version} · "
          f"check prompt v{prompt.version} · set {set_name} v{data['version']} · n={len(rows)}")
    for name, predicted in (("lexicon", lex_only), ("lexicon+llm", combined)):
        report = score_labels(expected, predicted)
        print(line(name, report.overall))
    errors = sum(r["error"] is not None for r in rows)
    print(f"llm errors: {errors} of {len(rows)}")
    out = ROOT / "evals/runs" / f"tripwire-llm-{Path(set_name).stem}-{now:%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"run_at": now.isoformat(), "lexicon_version": lexicon_file.version,
                               "prompt_version": prompt.version, "set": set_name,
                               "set_version": data["version"], "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")  # fmt: skip
    print(f"written: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.exit(asyncio.run(main_async(sys.argv[1] if len(sys.argv) > 1 else "utterances.yaml")))
