"""Small, tested scoring helpers shared by the eval runners in `evals/`."""

from collections.abc import Sequence

from pydantic import BaseModel


class Counts(BaseModel):
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float | None:
        flagged = self.tp + self.fp
        return None if flagged == 0 else self.tp / flagged

    @property
    def recall(self) -> float | None:
        actual = self.tp + self.fn
        return None if actual == 0 else self.tp / actual


class LabelReport(BaseModel):
    overall: Counts  # utterance level: was any violation flagged where one exists?
    per_rule: dict[str, Counts]


def score_labels(expected: Sequence[set[str]], predicted: Sequence[set[str]]) -> LabelReport:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted must have the same length")
    overall = Counts()
    per_rule: dict[str, Counts] = {}
    for want, got in zip(expected, predicted, strict=True):
        if want and got:
            overall.tp += 1
        elif got:
            overall.fp += 1
        elif want:
            overall.fn += 1
        for rule in want | got:
            c = per_rule.setdefault(rule, Counts())
            if rule in want and rule in got:
                c.tp += 1
            elif rule in got:
                c.fp += 1
            else:
                c.fn += 1
    return LabelReport(overall=overall, per_rule=dict(sorted(per_rule.items())))
