from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from odte_spy_ibkr.backtest.metrics import rolling_sharpe_series
from odte_spy_ibkr.journal import read_daily_pnl


@dataclass(frozen=True)
class SharpeGateState:
    rolling_sharpe: float | None
    observations: int


def load_daily_returns(journal_path: str | Path, *, equity_base: float = 100_000.0) -> list[float]:
    """One return per journaled date: that day's PnL over equity at the start of the day."""
    equity = equity_base
    returns: list[float] = []
    for pnl in read_daily_pnl(journal_path).values():
        returns.append(pnl / equity if equity else 0.0)
        equity += pnl
    return returns


def sharpe_gate_state(
    journal_path: str | Path,
    *,
    window: int,
    min_obs: int,
    equity_base: float = 100_000.0,
) -> SharpeGateState:
    returns = load_daily_returns(journal_path, equity_base=equity_base)
    if len(returns) < max(min_obs, window):
        return SharpeGateState(rolling_sharpe=None, observations=len(returns))
    return SharpeGateState(
        rolling_sharpe=rolling_sharpe_series(returns, window=window)[-1],
        observations=len(returns),
    )


def allow_new_entries(
    journal_path: str | Path,
    *,
    window: int,
    min_obs: int,
    sharpe_min: float,
    equity_base: float = 100_000.0,
) -> tuple[bool, str]:
    """Whether new entries may open. Open positions are managed regardless.

    Too little history, or a window with zero variance, leaves the Sharpe undefined and
    does not block: the gate only acts on a measured shortfall.
    """
    state = sharpe_gate_state(journal_path, window=window, min_obs=min_obs, equity_base=equity_base)
    if state.rolling_sharpe is None:
        if state.observations < max(min_obs, window):
            return True, "insufficient_journal"
        return True, "sharpe_undefined"
    if state.rolling_sharpe < sharpe_min:
        return False, f"rolling_sharpe_{state.rolling_sharpe:.2f}_below_{sharpe_min}"
    return True, "ok"
