from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from odte_spy_ibkr.backtest.metrics import rolling_sharpe_series


@dataclass(frozen=True)
class SharpeGateState:
    rolling_sharpe: float | None
    observations: int


def load_daily_returns(journal_path: str | Path) -> list[float]:
    path = Path(journal_path)
    if not path.is_file():
        return []
    rows: list[tuple[date, float]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        d = date.fromisoformat(str(payload["date"]))
        pnl = float(payload.get("pnl", 0.0))
        rows.append((d, pnl))
    rows.sort(key=lambda x: x[0])
    equity = 100_000.0
    returns: list[float] = []
    for _, pnl in rows:
        ret = pnl / equity if equity else 0.0
        returns.append(ret)
        equity += pnl
    return returns


def sharpe_gate_state(
    journal_path: str | Path,
    *,
    window: int,
    min_obs: int,
) -> SharpeGateState:
    returns = load_daily_returns(journal_path)
    if len(returns) < min_obs:
        return SharpeGateState(rolling_sharpe=None, observations=len(returns))
    series = rolling_sharpe_series(returns, window=window)
    latest = series[-1] if series else None
    return SharpeGateState(rolling_sharpe=latest, observations=len(returns))


def allow_new_entries(
    journal_path: str | Path,
    *,
    window: int,
    min_obs: int,
    sharpe_min: float,
) -> tuple[bool, str]:
    state = sharpe_gate_state(journal_path, window=window, min_obs=min_obs)
    if state.rolling_sharpe is None:
        return True, "insufficient_journal"
    if state.rolling_sharpe < sharpe_min:
        return False, f"rolling_sharpe_{state.rolling_sharpe:.2f}_below_{sharpe_min}"
    return True, "ok"