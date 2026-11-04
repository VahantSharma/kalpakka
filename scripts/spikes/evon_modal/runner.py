"""Laptop-side wrapper every Modal spike script uses: budget check before, cost meter after.

Never imported inside a Modal container (containers only see the script's own module).
"""

import contextlib
import datetime as dt
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from budget import BudgetExceeded, Ledger, Resources, budget_from_env, meter_line

LEDGER = Ledger(Path(__file__).resolve().parents[1] / "out" / "ledger.json")


@contextlib.contextmanager
def guarded(script: str, res: Resources, *, max_seconds: float) -> Iterator[None]:
    """Refuse to start if the worst case passes SPIKE_BUDGET_USD; always record and print cost."""
    budget = budget_from_env(os.environ)
    try:
        planned = LEDGER.check(res, max_seconds=max_seconds, budget_usd=budget)
    except BudgetExceeded as exc:
        sys.exit(f"[budget] {script}: {exc}")
    spent = LEDGER.spent_usd()  # includes worst cases of runs still in flight
    print(
        f"[budget] {script}: spent or reserved so far ${spent:.2f}; worst case this run "
        f"${planned:.2f} (timeout {max_seconds / 60:.0f} min); budget ${budget:.2f}",
        flush=True,
    )
    rid = LEDGER.reserve(script, res, max_seconds=max_seconds)
    started = time.monotonic()
    try:
        yield
    finally:
        seconds = time.monotonic() - started
        LEDGER.finish(rid, res, seconds=seconds)
        print(meter_line(script, res, seconds=seconds, spent_before=spent, budget_usd=budget))
        print(billing_hint())


def billing_hint() -> str:
    """Modal's own billing report for today, if this plan exposes it; never fatal."""
    try:
        import modal.billing

        start = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        items = modal.billing.workspace_billing_report(start=start)
        total = sum(float(i.cost) for i in items)
        return f"[cost] Modal billing report since {start:%Y-%m-%d} 00:00 UTC: ${total:.2f}"
    except Exception as exc:  # report only; the ledger above is the guard
        name = type(exc).__name__
        return f"[cost] Modal billing report not available here ({name}); check the dashboard"
