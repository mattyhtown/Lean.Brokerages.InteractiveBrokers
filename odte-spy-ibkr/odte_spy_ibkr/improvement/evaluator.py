from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from odte_spy_ibkr.backtest.runner import execute_backtest
from odte_spy_ibkr.config import Settings, load_settings
from odte_spy_ibkr.improvement.experiment import ExperimentCandidate

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 120
SAMPLE_KEYS = (
    "simulated",
    "scored_window",
    "chain_coverage",
    "score_days",
    "rolling_window_days",
    "trade_count",
    "traded_days",
)


@dataclass(frozen=True)
class EvaluationResult:
    candidate: ExperimentCandidate
    max_rolling_sharpe: float | None
    max_drawdown: float
    score: float
    # What the score rests on: the scored (holdout) window, its chain coverage and trade
    # counts, and how many sessions were simulated in total.
    sample: dict = field(default_factory=dict)


def _apply_params(settings: Settings, params: dict) -> Settings:
    for key, value in params.items():
        if hasattr(settings, key):
            object.__setattr__(settings, key, value)
    return settings


def evaluate_candidate(candidate: ExperimentCandidate, *, end: date | None = None) -> EvaluationResult:
    """Score a candidate on the walk-forward holdout slice of the last 120 days.

    Only holdout sessions replayed from their own real data are scored; generated chains
    are never requested. Without enough of them there is no Sharpe, so the score is -inf
    and the candidate cannot be promoted.
    """
    end = end or date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    settings = _apply_params(load_settings(), dict(candidate.params))
    result = execute_backtest(settings, start=start, end=end, walk_forward=True)
    summary = result.summary
    sharpe = summary.max_rolling_sharpe
    score = (
        float("-inf")
        if sharpe is None
        else sharpe - settings.improvement_max_dd_penalty * summary.max_drawdown
    )
    sample = {key: result.payload[key] for key in SAMPLE_KEYS}
    coverage, scored = sample["chain_coverage"], sample["scored_window"]
    logger.info(
        "candidate %s scored on %s to %s: %d of %d holdout sessions have their own chain, "
        "%d trade(s) over %d traded day(s); %d session(s) simulated, %d after the holdout unscored",
        candidate.name,
        scored["from"],
        scored["to"],
        coverage["own_chain"],
        coverage["sessions"],
        sample["trade_count"],
        sample["traded_days"],
        sample["simulated"]["sessions"],
        scored["simulated_sessions_after"],
    )
    return EvaluationResult(
        candidate=candidate,
        max_rolling_sharpe=sharpe,
        max_drawdown=summary.max_drawdown,
        score=score,
        sample=sample,
    )
