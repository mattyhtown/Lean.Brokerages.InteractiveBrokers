from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from odte_spy_ibkr.config import Settings, load_settings
from odte_spy_ibkr.improvement.experiment import ExperimentCandidate


@dataclass(frozen=True)
class EvaluationResult:
    candidate: ExperimentCandidate
    max_rolling_sharpe: float | None
    max_drawdown: float
    score: float


def _apply_params(settings: Settings, params: dict) -> Settings:
    for key, value in params.items():
        if hasattr(settings, key):
            object.__setattr__(settings, key, value)
    return settings


def evaluate_candidate(candidate: ExperimentCandidate, *, end: date | None = None) -> EvaluationResult:
    end = end or date.today()
    start = end - timedelta(days=120)
    settings = _apply_params(load_settings(), dict(candidate.params))
    # run_backtest logs summary but we need metrics — run simplified internal path
    from odte_spy_ibkr.backtest.calendar import iter_trading_days
    from odte_spy_ibkr.backtest.data_loader import load_chain_for_day
    from odte_spy_ibkr.backtest.ledger import DailyLedger
    from odte_spy_ibkr.backtest.metrics import max_drawdown, max_rolling_sharpe, summarize_backtest
    from odte_spy_ibkr.backtest.simulator import simulate_day
    from odte_spy_ibkr.strategy.interface import OpenPosition

    days = iter_trading_days(start, end)
    ledger = DailyLedger(equity_base=settings.backtest_equity_base)
    open_pos: OpenPosition | None = None
    wins: list[bool] = []
    for day in days:
        from odte_spy_ibkr.backtest.spot_loader import load_spot_for_day

        spot = load_spot_for_day(settings, ticker=settings.underlying, day=day)
        chain = load_chain_for_day(settings, ticker=settings.underlying, day=day, spot=spot)
        pnl, open_pos, traded = simulate_day(settings, day=day, candidates=chain, spot=spot, open_position=open_pos)
        ledger.record(day, pnl)
        if traded:
            wins.append(pnl > 0)
    returns = ledger.daily_return_series(days)
    pnls = ledger.daily_pnl_series(days)
    summary = summarize_backtest(
        returns,
        pnls,
        wins,
        window=settings.backtest_rolling_window_days,
        sharpe_target=settings.backtest_sharpe_target,
    )
    sharpe = summary.max_rolling_sharpe or 0.0
    score = sharpe - settings.improvement_max_dd_penalty * summary.max_drawdown
    return EvaluationResult(
        candidate=candidate,
        max_rolling_sharpe=summary.max_rolling_sharpe,
        max_drawdown=summary.max_drawdown,
        score=score,
    )