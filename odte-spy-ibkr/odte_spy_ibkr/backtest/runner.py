from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from odte_spy_ibkr.backtest.calendar import ET, iter_odte_sessions
from odte_spy_ibkr.backtest.ledger import DailyLedger
from odte_spy_ibkr.backtest.metrics import BacktestSummary, rolling_stats_series, summarize_backtest
from odte_spy_ibkr.backtest.session_data import NO_CHAIN, NO_SPOT, load_session
from odte_spy_ibkr.backtest.session_log import SessionLog, VerboseSessionReport
from odte_spy_ibkr.backtest.simulator import simulate_session
from odte_spy_ibkr.backtest.walk_forward import holdout_test_days
from odte_spy_ibkr.config import Settings, load_settings
from odte_spy_ibkr.logging_utils import setup_logging

# Named explicitly: under `python -m` this module is `__main__`, outside the package logger.
logger = logging.getLogger("odte_spy_ibkr.backtest.runner")

DEFAULT_LOOKBACK_DAYS = 730
REPORT_DETAIL_KEYS = {"rolling", "trades", "sessions"}


SCOPE_FULL_RANGE = "full_range"
SCOPE_WALK_FORWARD = "walk_forward_holdout"


@dataclass(frozen=True)
class SessionOutcome:
    """What one simulated session contributed. `source` is set when the session was replayed
    from its own chain and spot; `excluded` says why it is not in the scored sample."""

    day: date
    has_own_chain: bool
    source: str | None = None
    excluded: str | None = None
    synthetic_trades: int = 0
    synthetic_pnl: float = 0.0
    synthetic: bool = False


def chain_coverage(outcomes: list[SessionOutcome]) -> dict[str, Any]:
    """Sessions replayed from their own day's chain against sessions that have none."""
    by_source: dict[str, int] = {}
    for o in outcomes:
        if o.source is not None:
            by_source[o.source] = by_source.get(o.source, 0) + 1
    return {
        "sessions": len(outcomes),
        "own_chain": sum(o.has_own_chain for o in outcomes),
        "no_chain": sum(o.excluded == NO_CHAIN for o in outcomes),
        "own_chain_by_source": by_source,
        "no_chain_dates": [o.day.isoformat() for o in outcomes if o.excluded == NO_CHAIN],
        "own_chain_but_no_spot_bars_dates": [o.day.isoformat() for o in outcomes if o.excluded == NO_SPOT],
    }


@dataclass(frozen=True)
class BacktestResult:
    payload: dict[str, Any]
    summary: BacktestSummary

    def gate(self, required: float | None) -> dict[str, Any]:
        """The rolling-Sharpe requirement and its outcome, with the sample it rests on.

        Only sessions of the scored window that were replayed from their own real data
        are in the sample. `passed` is None when nothing is required.
        """
        window = self.payload["scored_window"]
        return {
            "required_rolling_sharpe": required,
            "max_rolling_sharpe": self.summary.max_rolling_sharpe,
            "passed": None if required is None else self.summary.passes_sharpe_target(required),
            "trade_count": self.summary.trade_count,
            "traded_days": self.payload["traded_days"],
            "scored_days": self.payload["score_days"],
            "scored_from": window["from"],
            "scored_to": window["to"],
        }


def execute_backtest(
    settings: Settings,
    *,
    start: date,
    end: date,
    walk_forward: bool = False,
    verbose: bool = False,
    allow_synthetic: bool = False,
    client: httpx.Client | None = None,
) -> BacktestResult:
    """Simulate every 0DTE session in [start, end] and score the scored window.

    The scored window is the whole range, or with `walk_forward` the final holdout fold of
    the simulated sessions. Every top-level figure, `chain_coverage` and the gate describe
    the scored window only; `simulated` describes the full range.
    """
    sessions = iter_odte_sessions(start, end, settings.underlying)
    ledger = DailyLedger(equity_base=settings.backtest_equity_base)
    outcomes: list[SessionOutcome] = []
    verbose_report = VerboseSessionReport()

    for day in sessions:
        loaded = load_session(settings, day, allow_synthetic=allow_synthetic, client=client)
        if loaded.missing is not None:
            outcome = SessionOutcome(day, loaded.has_own_chain, excluded=loaded.missing)
            if loaded.session is not None:
                # Generated prices: run for a pipeline smoke test, reported apart, never scored.
                sim = simulate_session(settings, loaded.session)
                outcome = replace(outcome, synthetic=True, synthetic_trades=len(sim.trades), synthetic_pnl=sim.pnl)
            outcomes.append(outcome)
            continue
        sim = simulate_session(settings, loaded.session)
        if not sim.scored:
            outcomes.append(SessionOutcome(day, True, source=loaded.session.source, excluded=sim.session_reason))
            continue
        outcomes.append(SessionOutcome(day, True, source=loaded.session.source))
        ledger.record(day, sim.pnl, sim.trades)
        if verbose:
            verbose_report.add(SessionLog(day=day, reason=sim.session_reason, traded=sim.traded, pnl=sim.pnl))
            logger.info(
                "session %s traded=%s pnl=%.2f reason=%s",
                day.isoformat(),
                sim.traded,
                sim.pnl,
                sim.session_reason,
            )

    window_days = (
        holdout_test_days(
            sessions,
            train_days=settings.backtest_walk_forward_train_days,
            test_days=settings.backtest_walk_forward_test_days,
        )
        if walk_forward
        else sessions
    )
    in_window = set(window_days)
    window_outcomes = [o for o in outcomes if o.day in in_window]
    days = [o.day for o in window_outcomes if o.excluded is None]
    excluded: dict[str, int] = {}
    for o in window_outcomes:
        if o.excluded is not None:
            excluded[o.excluded] = excluded.get(o.excluded, 0) + 1

    in_sample = set(days)
    trades = [tr for tr in ledger.trades if tr.day in in_sample]
    returns = ledger.daily_return_series(days)
    window = settings.backtest_rolling_window_days
    summary = summarize_backtest(
        returns,
        ledger.daily_pnl_series(days),
        [tr.pnl > 0 for tr in trades],
        window=window,
        sharpe_target=settings.backtest_sharpe_target,
        equity_base=settings.backtest_equity_base,
    )
    scope = SCOPE_WALK_FORWARD if walk_forward else SCOPE_FULL_RANGE
    full_coverage = chain_coverage(outcomes)
    before = sessions.index(window_days[0]) if window_days else 0

    payload: dict[str, Any] = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "walk_forward": walk_forward,
        "total_trading_days": len(sessions),
        "simulated": {
            "from": sessions[0].isoformat() if sessions else None,
            "to": sessions[-1].isoformat() if sessions else None,
            "sessions": len(sessions),
            "own_chain": full_coverage["own_chain"],
            "no_chain": full_coverage["no_chain"],
            "sessions_scored": sum(o.excluded is None for o in outcomes),
            "total_pnl": ledger.total_pnl(),
        },
        "scored_window": {
            "scope": scope,
            "from": window_days[0].isoformat() if window_days else None,
            "to": window_days[-1].isoformat() if window_days else None,
            "sessions": len(window_days),
            "simulated_sessions_before": before,
            "simulated_sessions_after": len(sessions) - before - len(window_days),
        },
        "chain_coverage": {"scope": scope, **chain_coverage(window_outcomes)},
        "sessions_excluded": excluded,
        "score_days": len(days),
        "equity_base": settings.backtest_equity_base,
        "rolling_window_days": window,
        "sharpe_target": settings.backtest_sharpe_target,
        "trade_count": summary.trade_count,
        "traded_days": len({tr.day for tr in trades}),
        "win_rate": summary.win_rate,
        "full_sharpe": summary.full_sharpe,
        "max_rolling_sharpe": summary.max_rolling_sharpe,
        "pct_days_sharpe_ge_target": summary.pct_days_sharpe_ge_target,
        "max_drawdown": summary.max_drawdown,
        "total_pnl": summary.total_pnl,
        "total_pnl_all_days": ledger.total_pnl(),
        "rolling": [
            {"date": d.isoformat(), "mean": stat.mean, "std": stat.std, "sharpe": stat.sharpe}
            for d, stat in zip(days, rolling_stats_series(returns, window=window), strict=True)
            if stat is not None
        ],
        "trades": [tr.to_dict() for tr in trades],
    }
    if allow_synthetic:
        generated = [o for o in outcomes if o.synthetic]
        payload["synthetic"] = {
            "sessions": len(generated),
            "sessions_in_scored_window": sum(o.day in in_window for o in generated),
            "trade_count": sum(o.synthetic_trades for o in generated),
            "total_pnl": sum(o.synthetic_pnl for o in generated),
            "note": "SYNTHETIC: generated prices for sessions without real data. Not in any "
            "metric above and not counted toward the Sharpe requirement.",
        }
    if verbose_report.sessions:
        payload["session_reason_counts"] = verbose_report.reason_counts
        payload["sessions"] = [
            {"date": s.day.isoformat(), "reason": s.reason, "traded": s.traded, "pnl": s.pnl}
            for s in verbose_report.sessions
        ]
    return BacktestResult(payload=payload, summary=summary)


def run_backtest(
    settings: Settings,
    *,
    start: date,
    end: date,
    report_path: str | None = None,
    require_sharpe: float | None = None,
    walk_forward: bool = False,
    verbose: bool = False,
    allow_synthetic: bool = False,
    client: httpx.Client | None = None,
) -> int:
    """Run, optionally write the JSON report, and return the process exit code.

    Exit code 1 when a required rolling Sharpe (`require_sharpe`, or BACKTEST_SHARPE_TARGET
    with BACKTEST_REQUIRE_SHARPE_PASS=true) is not reached.
    """
    result = execute_backtest(
        settings,
        start=start,
        end=end,
        walk_forward=walk_forward,
        verbose=verbose,
        allow_synthetic=allow_synthetic,
        client=client,
    )
    if require_sharpe is None and settings.backtest_require_sharpe_pass:
        require_sharpe = settings.backtest_sharpe_target
    payload = result.payload
    gate = payload["gate"] = result.gate(require_sharpe)

    coverage = payload["chain_coverage"]
    headline = {k: v for k, v in payload.items() if k not in REPORT_DETAIL_KEYS}
    headline["chain_coverage"] = {k: v for k, v in coverage.items() if not k.endswith("_dates")}
    logger.info("Backtest summary: %s", json.dumps(headline))
    scored = payload["scored_window"]
    logger.info(
        "Chain coverage (%s, %s to %s): %d of %d sessions have their own chain, %d have none; "
        "%d session(s) simulated in total",
        coverage["scope"],
        scored["from"],
        scored["to"],
        coverage["own_chain"],
        coverage["sessions"],
        coverage["no_chain"],
        payload["simulated"]["sessions"],
    )
    if allow_synthetic:
        logger.warning(
            "SYNTHETIC data filled %d session(s); they are excluded from every metric and the Sharpe gate",
            payload["synthetic"]["sessions"],
        )
    if not payload["score_days"]:
        logger.warning(
            "No session had usable data. Set ALPACA_API_KEY/ALPACA_API_SECRET or populate %s.",
            settings.backtest_cache_dir,
        )
    elif payload["score_days"] < payload["rolling_window_days"]:
        logger.warning(
            "%d scored day(s) is fewer than the %d-day rolling window: the rolling Sharpe is undefined",
            payload["score_days"],
            payload["rolling_window_days"],
        )
    if report_path:
        out = Path(report_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if gate["passed"] is not None:
        logger.log(
            logging.INFO if gate["passed"] else logging.ERROR,
            "Rolling Sharpe gate %s: required %.2f, max rolling Sharpe %s, on %d trade(s) over "
            "%d traded day(s) of %d scored day(s), %s to %s",
            "PASSED" if gate["passed"] else "FAILED",
            require_sharpe,
            "undefined" if gate["max_rolling_sharpe"] is None else f"{gate['max_rolling_sharpe']:.2f}",
            gate["trade_count"],
            gate["traded_days"],
            gate["scored_days"],
            gate["scored_from"],
            gate["scored_to"],
        )
    return 1 if gate["passed"] is False else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ODTE SPY historical backtest")
    parser.add_argument("--config", default=None, help="YAML config (default: CONFIG_YAML or ./config.yaml)")
    parser.add_argument("--from", dest="start", default=None, help="YYYY-MM-DD (default: two years before --to)")
    parser.add_argument("--to", dest="end", default=None, help="YYYY-MM-DD (default: yesterday, ET)")
    parser.add_argument("--report-json", default="")
    parser.add_argument("--require-rolling-sharpe", type=float, default=None)
    parser.add_argument("--walk-forward", action="store_true", help="Score metrics on holdout walk-forward test slice only")
    parser.add_argument("--verbose", action="store_true", help="Log per-session reasons and add them to the report")
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Also simulate sessions without real data on generated prices. Smoke test only: "
        "reported under 'synthetic', excluded from every metric and from the Sharpe requirement",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    setup_logging(settings.log_level)  # type: ignore[arg-type]
    end = date.fromisoformat(args.end) if args.end else datetime.now(tz=ET).date() - timedelta(days=1)
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    code = run_backtest(
        settings,
        start=start,
        end=end,
        report_path=args.report_json or None,
        require_sharpe=args.require_rolling_sharpe,
        walk_forward=args.walk_forward,
        verbose=args.verbose,
        allow_synthetic=args.allow_synthetic,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
