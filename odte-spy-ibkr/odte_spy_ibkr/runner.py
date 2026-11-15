from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone

from odte_spy_ibkr.chain import build_chain_skew, today_expiry
from odte_spy_ibkr.chain.cache_io import save_chain_json
from odte_spy_ibkr.config import Settings, load_settings
from odte_spy_ibkr.ib_client import IBSession
from odte_spy_ibkr.logging_utils import setup_logging
from odte_spy_ibkr.providers.aggregate import MarketContextAggregator
from odte_spy_ibkr.providers.chain_skew import ChainSkewProvider
from odte_spy_ibkr.providers.registry import build_providers
from odte_spy_ibkr.risk import RiskDecision, RiskLimits, RiskState, check_new_entry, is_rth, should_flatten
from odte_spy_ibkr.risk.sharpe_gate import allow_new_entries
from odte_spy_ibkr.strategy.interface import propose_entry

logger = logging.getLogger(__name__)


def apply_chain_skew(ctx, candidates, spot):
    skew = build_chain_skew(candidates, expiry=today_expiry(), spot=spot, as_of=datetime.now(tz=timezone.utc))
    ctx.net_call_bias = skew.net_call_bias
    ctx.chain_skew = skew
    return ctx


def run_loop(settings: Settings, *, once: bool = False) -> None:
    ib: IBSession | None = None
    if "ibkr" in settings.providers_enabled_set:
        ib = IBSession(settings)
        try:
            ib.connect()
            ib.stream_spot()
        except Exception as exc:  # noqa: BLE001
            logger.warning("IB connect failed (continuing with other providers): %s", exc)
            ib = None

    providers = build_providers(settings, ib)
    chain_skew_provider = ChainSkewProvider(settings)
    providers.append(chain_skew_provider)
    aggregator = MarketContextAggregator(providers, underlying=settings.underlying)
    limits = RiskLimits(
        max_contracts=settings.max_contracts,
        max_daily_loss=settings.max_daily_loss,
        entry_cutoff=settings.entry_cutoff,
        flatten_time=settings.flatten_time,
        min_minutes_after_open=settings.min_minutes_after_open,
    )
    state = RiskState()
    last_uw_poll = 0.0

    try:
        while True:
            if not is_rth():
                logger.info("Outside RTH; sleeping")
                if once:
                    break
                time.sleep(settings.poll_interval_sec)
                continue

            ctx = aggregator.build()
            if settings.sharpe_gate_enabled:
                ok, sharpe_reason = allow_new_entries(
                    settings.journal_path,
                    window=settings.sharpe_window_days,
                    min_obs=settings.sharpe_min_obs,
                    sharpe_min=settings.sharpe_min,
                )
                if not ok:
                    logger.info("Sharpe gate blocked entries: %s", sharpe_reason)
                    if once:
                        break
                    time.sleep(settings.poll_interval_sec)
                    continue

            now = time.monotonic()
            if now - last_uw_poll >= settings.uw_poll_interval_sec:
                last_uw_poll = now
                logger.debug("UW poll tick at %s", datetime.now(tz=timezone.utc).isoformat())

            decision, reason = check_new_entry(limits, state)
            if should_flatten(limits):
                logger.info("Flatten window reached")
            elif decision == RiskDecision.ALLOW:
                candidates = []
                if ib is not None:
                    candidates = ib.fetch_chain_candidates(ctx.spot)
                    save_chain_json(candidates, base_dir=settings.backtest_cache_dir, ticker=settings.underlying)
                if candidates:
                    chain_skew_provider.set_candidates(candidates)
                    ctx = apply_chain_skew(ctx, candidates, ctx.spot)
                intent = propose_entry(settings, ctx, candidates) if candidates else None
                if intent:
                    ctx.net_call_risk = intent.net_call_risk
                    logger.info(
                        "Entry intent %s credit~%s net_call_bias=%.2f net_call_risk=%.0f reason=%s",
                        intent.side.value,
                        intent.spread.estimated_credit,
                        ctx.net_call_bias,
                        intent.net_call_risk,
                        intent.reason,
                    )
                    if settings.orders_allowed and ib is not None:
                        ib.place_combo_order(
                            {
                                "side": intent.side.value,
                                "short": intent.spread.short_leg.symbol,
                                "long": intent.spread.long_leg.symbol,
                                "contracts": intent.contracts,
                            }
                        )
                else:
                    logger.info("No entry intent (%s)", reason)
            else:
                logger.info("Entry blocked: %s", reason)

            if once:
                break
            time.sleep(settings.poll_interval_sec)
    finally:
        if ib is not None:
            ib.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="0DTE SPY IBKR runner")
    parser.add_argument("--once", action="store_true", help="Run a single iteration")
    args = parser.parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)  # type: ignore[arg-type]
    logger.info(
        "Safety: LIVE_TRADING=%s READ_ONLY=%s ENABLE_ORDERS=%s providers=%s",
        settings.live_trading,
        settings.read_only,
        settings.enable_orders,
        settings.providers_enabled,
    )
    run_loop(settings, once=args.once)


if __name__ == "__main__":
    main()