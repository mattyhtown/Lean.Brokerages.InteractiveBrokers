from __future__ import annotations

import math
from dataclasses import dataclass

ANNUALIZATION = math.sqrt(252)


def rolling_sharpe_series(
    daily_returns: list[float],
    *,
    window: int,
) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(daily_returns)):
        if i + 1 < window:
            out.append(None)
            continue
        chunk = daily_returns[i + 1 - window : i + 1]
        mean = sum(chunk) / window
        var = sum((x - mean) ** 2 for x in chunk) / window
        std = math.sqrt(var)
        if std <= 1e-12:
            out.append(None)
        else:
            out.append(ANNUALIZATION * mean / std)
    return out


def max_rolling_sharpe(daily_returns: list[float], *, window: int) -> float | None:
    series = [v for v in rolling_sharpe_series(daily_returns, window=window) if v is not None]
    return max(series) if series else None


def full_sample_sharpe(daily_returns: list[float]) -> float | None:
    if not daily_returns:
        return None
    mean = sum(daily_returns) / len(daily_returns)
    var = sum((x - mean) ** 2 for x in daily_returns) / len(daily_returns)
    std = math.sqrt(var)
    if std <= 1e-12:
        return None
    return ANNUALIZATION * mean / std


def max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        dd = (peak - v) / peak if peak else 0.0
        max_dd = max(max_dd, dd)
    return max_dd


@dataclass(frozen=True)
class BacktestSummary:
    trade_count: int
    win_rate: float
    full_sharpe: float | None
    max_rolling_sharpe: float | None
    pct_days_sharpe_ge_target: float
    max_drawdown: float
    total_pnl: float

    def passes_sharpe_target(self, target: float) -> bool:
        if self.max_rolling_sharpe is None:
            return False
        return self.max_rolling_sharpe >= target


def summarize_backtest(
    daily_returns: list[float],
    daily_pnl: list[float],
    trade_outcomes: list[bool],
    *,
    window: int,
    sharpe_target: float,
) -> BacktestSummary:
    equity = 100_000.0
    curve = [equity]
    for pnl in daily_pnl:
        equity += pnl
        curve.append(equity)

    rolling = rolling_sharpe_series(daily_returns, window=window)
    defined = [r for r in rolling if r is not None]
    pct = 0.0
    if defined:
        pct = sum(1 for r in defined if r >= sharpe_target) / len(defined)

    wins = sum(1 for w in trade_outcomes if w)
    win_rate = wins / len(trade_outcomes) if trade_outcomes else 0.0

    return BacktestSummary(
        trade_count=len(trade_outcomes),
        win_rate=win_rate,
        full_sharpe=full_sample_sharpe(daily_returns),
        max_rolling_sharpe=max_rolling_sharpe(daily_returns, window=window),
        pct_days_sharpe_ge_target=pct,
        max_drawdown=max_drawdown(curve),
        total_pnl=sum(daily_pnl),
    )