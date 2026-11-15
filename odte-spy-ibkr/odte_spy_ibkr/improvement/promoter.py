from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.improvement.evaluator import EvaluationResult


@dataclass(frozen=True)
class PromotionDecision:
    promoted: bool
    reason: str


def should_promote(
    settings: Settings,
    active_metrics: dict[str, Any],
    challenger: EvaluationResult,
) -> PromotionDecision:
    active_sharpe = float(active_metrics.get("metrics", {}).get("sharpe_rolling") or 0.0)
    challenger_sharpe = challenger.max_rolling_sharpe or 0.0
    margin = settings.improvement_sharpe_margin

    if challenger_sharpe < settings.backtest_sharpe_target:
        return PromotionDecision(False, "below_sharpe_target")

    if challenger_sharpe <= active_sharpe + margin:
        return PromotionDecision(False, "insufficient_improvement")

    if not settings.paper and not settings.improvement_auto_promote_live:
        return PromotionDecision(False, "live_auto_promote_disabled")

    if settings.paper and not settings.improvement_auto_promote_paper:
        return PromotionDecision(False, "paper_auto_promote_disabled")

    return PromotionDecision(True, "promoted")