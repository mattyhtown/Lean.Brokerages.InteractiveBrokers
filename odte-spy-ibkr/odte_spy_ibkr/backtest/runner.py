from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path

from odte_spy_ibkr.backtest.calendar import iter_trading_days
from odte_spy_ibkr.backtest.data_loader import load_chain_for_day
from odte_spy_ibkr.backtest.spot_loader import load_spot_for_day
from odte_spy_ibkr.backtest.ledger import DailyLedger
from odte_spy_ibkr.backtest.metrics import summarize_backtest
from odte_spy_ibkr.backtest.simulator import simulate_day
from odte_spy_ibkr.config import load_settings
from odte_spy_ibkr.logging_utils import setup_logging
from odte_spy_ibkr.strategy.interface import OpenPosition

logger = logging.getLogger(__name__)


def run_backtest(
    settings,
    *,
    start: date,
    end: date,
    report_path: str | None = None,
    require_sharpe: float | None = None,
) -> int:
    days = iter_trading_days(start, end)
    ledger = DailyLedger(equity_base=settings.backtest_equity_base)
    open_pos: OpenPosition | None = None
    trade_wins: list[bool] = []

    for day in days:
        spot = load_spot_for_day(settings, ticker=settings.underlying, day=day)
        chain = load_chain_for_day(settings, ticker=settings.underlying, day=day, spot=spot)
        pnl, open_pos, traded = simulate_day(settings, day=day, candidates=chain, spot=spot, open_position=open_pos)
        ledger.record(day, pnl)
        if traded:
            trade_wins.append(pnl > 0)

    returns = ledger.daily_return_series(days)
    pnls = ledger.daily_pnl_series(days)
    summary = summarize_backtest(
        returns,
        pnls,
        trade_wins,
        window=settings.backtest_rolling_window_days,
        sharpe_target=settings.backtest_sharpe_target,
    )

    payload = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "trade_count": summary.trade_count,
        "win_rate": summary.win_rate,
        "full_sharpe": summary.full_sharpe,
        "max_rolling_sharpe": summary.max_rolling_sharpe,
        "pct_days_sharpe_ge_target": summary.pct_days_sharpe_ge_target,
        "max_drawdown": summary.max_drawdown,
        "total_pnl": summary.total_pnl,
    }
    logger.info("Backtest summary: %s", json.dumps(payload))
    if report_path:
        out = Path(report_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if require_sharpe is not None and not summary.passes_sharpe_target(require_sharpe):
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="ODTE SPY historical backtest")
    parser.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--report-json", default="")
    parser.add_argument("--require-rolling-sharpe", type=float, default=None)
    args = parser.parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)  # type: ignore[arg-type]
    code = run_backtest(
        settings,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        report_path=args.report_json or None,
        require_sharpe=args.require_rolling_sharpe,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()