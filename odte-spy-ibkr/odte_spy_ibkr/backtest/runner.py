from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path

from odte_spy_ibkr.backtest.calendar import iter_trading_days
from odte_spy_ibkr.backtest.data_loader import load_chain_for_day
from odte_spy_ibkr.backtest.diagnostics import explain_no_entry
from odte_spy_ibkr.backtest.spot_loader import load_spot_for_day
from odte_spy_ibkr.backtest.ledger import DailyLedger
from odte_spy_ibkr.backtest.metrics import summarize_backtest
from odte_spy_ibkr.backtest.simulator import simulate_day
from odte_spy_ibkr.backtest.walk_forward import holdout_test_days
from odte_spy_ibkr.chain import build_chain_skew, today_expiry
from odte_spy_ibkr.backtest.replay import build_backtest_context
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
    walk_forward: bool = False,
    verbose: bool = False,
) -> int:
    all_days = iter_trading_days(start, end)
    score_days = (
        holdout_test_days(
            all_days,
            train_days=settings.backtest_walk_forward_train_days,
            test_days=settings.backtest_walk_forward_test_days,
        )
        if walk_forward
        else all_days
    )
    ledger = DailyLedger(equity_base=settings.backtest_equity_base)
    full_ledger = DailyLedger(equity_base=settings.backtest_equity_base)
    open_pos: OpenPosition | None = None
    trade_wins: list[bool] = []
    no_entry_reasons: dict[str, int] = {}

    for day in all_days:
        spot = load_spot_for_day(settings, ticker=settings.underlying, day=day)
        chain = load_chain_for_day(settings, ticker=settings.underlying, day=day, spot=spot)
        pnl, open_pos, traded = simulate_day(settings, day=day, candidates=chain, spot=spot, open_position=open_pos)
        full_ledger.record(day, pnl)
        if day in score_days:
            ledger.record(day, pnl)
            if traded:
                trade_wins.append(pnl > 0)
        if verbose and not traded and open_pos is None and day in score_days:
            from zoneinfo import ZoneInfo

            et = ZoneInfo("America/New_York")
            from datetime import datetime as dt

            signal_at = dt.combine(day, dt.min.time(), tzinfo=et).replace(hour=11, minute=0)
            skew = build_chain_skew(chain, expiry=today_expiry(day), spot=spot, as_of=signal_at)
            ctx = build_backtest_context(settings, day=day, spot=spot, chain_skew=skew, now=signal_at)
            reason = explain_no_entry(settings, ctx, chain) if ctx else "uw_skip_session"
            no_entry_reasons[reason] = no_entry_reasons.get(reason, 0) + 1

    days = score_days if walk_forward else all_days
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
        "walk_forward": walk_forward,
        "score_days": len(score_days),
        "total_trading_days": len(all_days),
        "trade_count": summary.trade_count,
        "win_rate": summary.win_rate,
        "full_sharpe": summary.full_sharpe,
        "max_rolling_sharpe": summary.max_rolling_sharpe,
        "pct_days_sharpe_ge_target": summary.pct_days_sharpe_ge_target,
        "max_drawdown": summary.max_drawdown,
        "total_pnl": summary.total_pnl,
        "total_pnl_all_days": full_ledger.total_pnl(),
    }
    if no_entry_reasons:
        payload["no_entry_reasons"] = no_entry_reasons
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
    parser.add_argument("--walk-forward", action="store_true", help="Score metrics on holdout walk-forward test slice only")
    parser.add_argument("--verbose", action="store_true", help="Log aggregated no-entry reasons per holdout day")
    args = parser.parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)  # type: ignore[arg-type]
    code = run_backtest(
        settings,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        report_path=args.report_json or None,
        require_sharpe=args.require_rolling_sharpe,
        walk_forward=args.walk_forward,
        verbose=args.verbose,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()