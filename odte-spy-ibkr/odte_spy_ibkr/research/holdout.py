from __future__ import annotations

from datetime import date

from odte_spy_ibkr.backtest.calendar import iter_trading_days

# Run-1 holdout (spent; do not reuse for run-2 inference): last 8 sessions of the
# initial 38-file MCP bootstrap cache ending 2026-11-05.
RUN1_SPENT_HOLDOUT_LAST_DAY = date(2026, 11, 5)
RUN1_SPENT_HOLDOUT_SESSIONS = 8

# Run-2 holdout chosen BEFORE viewing run-2 metrics: all trading days from
# 2026-10-01 through 2026-10-14 (inclusive), disjoint from run-1 tail (>= 2026-10-27).
RUN2_HOLDOUT_START = date(2026, 10, 1)
RUN2_HOLDOUT_END = date(2026, 10, 14)

RUN1_HYPOTHESES_TESTED = 6

FEATURE_HYPOTHESES_PER_RUN = 7


def run2_holdout_days(calendar_start: date, calendar_end: date) -> list[date]:
    return [d for d in iter_trading_days(calendar_start, calendar_end) if RUN2_HOLDOUT_START <= d <= RUN2_HOLDOUT_END]


def run2_train_days(all_days: list[date]) -> list[date]:
    hold = set(run2_holdout_days(all_days[0], all_days[-1]) if all_days else [])
    return [d for d in all_days if d not in hold]