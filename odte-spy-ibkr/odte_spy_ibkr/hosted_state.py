"""Read-only views for environments with no TWS/Gateway session (the hosted agent).

``python -m odte_spy_ibkr.hosted_state`` prints the current signal and gate
state. With ``--promotion-check <history.json>`` it prints the promotion
decision the engine reaches for that improvement record. With
``--chain-coverage FROM TO [--walk-forward]`` or
``--improvement-chain-coverage`` it prints where the sessions a backtest or
an improvement run scores would get their option chains.

None of them connects to IB, places orders, writes the journal or changes
the active params. Anything that needs an IB session is reported as
unavailable instead of estimated.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from odte_spy_ibkr.backtest.calendar import iter_trading_days
from odte_spy_ibkr.backtest.data_loader import _cache_path
from odte_spy_ibkr.backtest.walk_forward import holdout_test_days
from odte_spy_ibkr.config import Settings, load_settings
from odte_spy_ibkr.improvement.evaluator import LOOKBACK_DAYS
from odte_spy_ibkr.improvement.promoter import should_promote
from odte_spy_ibkr.improvement.registry import load_active_params
from odte_spy_ibkr.providers.aggregate import MarketContextAggregator, merge_deltas
from odte_spy_ibkr.providers.base import BaseProvider, MarketContext
from odte_spy_ibkr.providers.registry import build_providers
from odte_spy_ibkr.research.chain_io import load_chain_rows
from odte_spy_ibkr.research.ingest import log_credential_status
from odte_spy_ibkr.research.paths import CHAIN_ROOT
from odte_spy_ibkr.risk import ET, RiskLimits, RiskState, check_new_entry
from odte_spy_ibkr.risk.sharpe_gate import allow_new_entries, sharpe_gate_state
from odte_spy_ibkr.strategy.flow_gate import evaluate_flow_gate
from odte_spy_ibkr.strategy.macro_gate import evaluate_macro_gate

IB_UNAVAILABLE_REASON = "no TWS/Gateway session in this environment; connection not attempted"


def _provider_status(provider: BaseProvider, ctx: MarketContext) -> dict[str, Any]:
    note = ctx.provider_notes.get(provider.provider_id)
    configured = provider.enabled and provider.is_configured()
    return {
        "id": provider.provider_id,
        "enabled": provider.enabled,
        "configured": configured,
        "contributed": configured and not (note or "").startswith("error"),
        "note": note,
    }


def build_state(settings: Settings, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(tz=timezone.utc)
    providers = build_providers(settings, None)
    aggregator = MarketContextAggregator(providers, underlying=settings.underlying)
    seed = MarketContext(underlying=settings.underlying)
    deltas = aggregator.fetch_deltas(seed)
    ctx = merge_deltas(seed, deltas, aggregator.default_weights)

    # A merged field no provider set still holds its MarketContext default.
    def supplied(*fields: str) -> bool:
        return any(all(getattr(d, f) is not None for f in fields) for d in deltas)

    flow_available = supplied("flow_bias", "flow_strength")
    macro = evaluate_macro_gate(settings, ctx)
    flow = evaluate_flow_gate(settings, ctx, now=now)
    limits = RiskLimits(
        max_contracts=settings.max_contracts,
        max_daily_loss=settings.max_daily_loss,
        entry_cutoff=settings.entry_cutoff,
        flatten_time=settings.flatten_time,
        min_minutes_after_open=settings.min_minutes_after_open,
    )
    risk_decision, risk_reason = check_new_entry(limits, RiskState(), now=now.astimezone(ET))
    sharpe = sharpe_gate_state(
        settings.journal_path,
        window=settings.sharpe_window_days,
        min_obs=settings.sharpe_min_obs,
    )
    sharpe_ok, sharpe_reason = allow_new_entries(
        settings.journal_path,
        window=settings.sharpe_window_days,
        min_obs=settings.sharpe_min_obs,
        sharpe_min=settings.sharpe_min,
    )

    return {
        "as_of": now.isoformat(),
        "underlying": settings.underlying,
        "safety": {
            "live_trading": settings.live_trading,
            "read_only": settings.read_only,
            "enable_orders": settings.enable_orders,
            "paper": settings.paper,
            "orders_allowed": settings.orders_allowed,
            "improvement_auto_promote_live": settings.improvement_auto_promote_live,
            "improvement_auto_promote_paper": settings.improvement_auto_promote_paper,
            "alpha_scored_entry_enabled": settings.alpha_scored_entry_enabled,
        },
        "ib_session": {"available": False, "reason": IB_UNAVAILABLE_REASON},
        "credentials_present": log_credential_status(settings),
        "providers": [_provider_status(p, ctx) for p in providers],
        "signal": {
            "spot": ctx.spot,
            "flow_bias": ctx.flow_bias.value if flow_available else None,
            "flow_strength": ctx.flow_strength if flow_available else None,
            "net_call_bias": ctx.net_call_bias if ctx.chain_skew is not None else None,
            "net_call_bias_unavailable_reason": (
                None if ctx.chain_skew is not None else "live 0DTE chain comes from the IB session"
            ),
            "macro_risk_off": ctx.macro_risk_off if supplied("macro_risk_off") else None,
            "macro_headline_score": (
                ctx.macro_headline_score if supplied("macro_headline_score") else None
            ),
            "macro_event_blackout": (
                ctx.macro_event_blackout if supplied("macro_event_blackout") else None
            ),
            "vol_regime": ctx.vol_regime.value if supplied("vol_regime") else None,
        },
        "gates": {
            "risk": {
                "decision": risk_decision.value,
                "reason": risk_reason,
                "assumes_flat_book": True,
            },
            "macro": {
                "decision": macro.decision.value,
                "reason": macro.reason,
                "size_scale": macro.size_scale,
                "inputs_available": (
                    supplied("macro_risk_off")
                    or supplied("macro_headline_score")
                    or supplied("macro_event_blackout")
                ),
            },
            "flow": {
                "decision": flow.decision.value,
                "reason": flow.reason,
                "inputs_available": flow_available,
            },
            "sharpe": {
                "enabled": settings.sharpe_gate_enabled,
                "rolling_sharpe": sharpe.rolling_sharpe,
                "observations": sharpe.observations,
                "min": settings.sharpe_min,
                # The live runner consults the journal only when the gate is on.
                "allows_new_entries": sharpe_ok or not settings.sharpe_gate_enabled,
                "reason": sharpe_reason if settings.sharpe_gate_enabled else "gate_disabled",
            },
        },
        "entry_intent": None,
        "entry_intent_unavailable_reason": "no option chain without an IB session",
        "active_params": load_active_params(settings.params_active_path),
    }


def promotion_check(settings: Settings, history_path: str | Path) -> dict[str, Any]:
    """The engine's promotion decision for one improvement history record.

    The improvement CLI does not print its decision, so this asks
    ``should_promote`` the same question. It decides only; it writes nothing.
    """
    record = json.loads(Path(history_path).read_text(encoding="utf-8"))
    # A null best_sharpe is what the improvement run writes when the holdout is
    # too short to score. A missing or non-numeric one is a broken record, and
    # must not be reported as a challenger that fell below the target.
    if not isinstance(record, dict) or "best_sharpe" not in record:
        raise ValueError(f"{history_path}: not an improvement history record (no best_sharpe)")
    challenger_sharpe = record["best_sharpe"]
    if challenger_sharpe is not None and (
        isinstance(challenger_sharpe, bool) or not isinstance(challenger_sharpe, (int, float))
    ):
        raise ValueError(f"{history_path}: best_sharpe is not a number: {challenger_sharpe!r}")
    active = load_active_params(settings.params_active_path)
    decision = should_promote(
        settings,
        active,
        SimpleNamespace(max_rolling_sharpe=challenger_sharpe),  # type: ignore[arg-type]
    )
    return {
        "reason": decision.reason,
        "would_promote": decision.promoted,
        "challenger_sharpe": challenger_sharpe,
        "active_sharpe": active.get("metrics", {}).get("sharpe_rolling"),
        "sharpe_target": settings.backtest_sharpe_target,
        "sharpe_margin": settings.improvement_sharpe_margin,
    }


def chain_coverage(
    settings: Settings, start: date, end: date, *, walk_forward: bool = False
) -> dict[str, Any]:
    """Where the sessions a backtest of ``start``..``end`` scores would get their option chains.

    A walk-forward backtest, and every improvement run, simulates the whole
    range but scores only the final holdout slice, so only that slice is
    counted; ``from`` and ``to`` are then its first and last session.

    Follows the lookup order of ``backtest.data_loader.load_chain_for_day`` and
    only reads, so it has to run before the backtest: the backtest caches what
    it fetches. Only a research chain is that session's own chain. A cached one
    is whatever an earlier backtest stored under that date, and a session with
    neither gets the engine's substitute.
    """
    research = cached = missing = 0
    simulated = iter_trading_days(start, end)
    days = simulated
    if walk_forward:
        days = holdout_test_days(
            simulated,
            train_days=settings.backtest_walk_forward_train_days,
            test_days=settings.backtest_walk_forward_test_days,
        )
    for day in days:
        cache = _cache_path(settings, settings.underlying, day)
        if load_chain_rows(day, snapshot="signal", root=CHAIN_ROOT):
            research += 1
        elif cache.is_file() or cache.with_suffix(".json").is_file():
            cached += 1
        else:
            missing += 1
    return {
        "from": (days[0] if walk_forward and days else start).isoformat(),
        "to": (days[-1] if walk_forward and days else end).isoformat(),
        "sessions": len(days),
        "simulated_sessions": len(simulated),
        "research_chain_sessions": research,
        "cached_chain_sessions": cached,
        "no_chain_sessions": missing,
        "no_chain_substitute": (
            "alpaca_current_snapshot"
            if settings.alpaca_api_key and settings.alpaca_api_secret
            else "synthetic"
        ),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Read-only engine state for hosts with no IB session")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--promotion-check",
        metavar="HISTORY_JSON",
        help="Print the promotion decision for this improvement history record instead of the state.",
    )
    mode.add_argument(
        "--chain-coverage",
        nargs=2,
        type=date.fromisoformat,
        metavar=("FROM", "TO"),
        help="Print where the sessions a backtest of this range scores would get their option chains.",
    )
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help="With --chain-coverage: the backtest scores only its holdout slice.",
    )
    mode.add_argument(
        "--improvement-chain-coverage",
        action="store_true",
        help="The same, for the sessions an improvement run started today scores.",
    )
    args = parser.parse_args(argv)
    settings = load_settings()
    logging.basicConfig(stream=sys.stderr, level=getattr(logging, settings.log_level.upper(), logging.INFO))
    if args.promotion_check:
        result = promotion_check(settings, args.promotion_check)
    elif args.chain_coverage:
        result = chain_coverage(settings, *args.chain_coverage, walk_forward=args.walk_forward)
    elif args.improvement_chain_coverage:
        today = date.today()
        result = chain_coverage(
            settings, today - timedelta(days=LOOKBACK_DAYS), today, walk_forward=True
        )
    else:
        result = build_state(settings)
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
