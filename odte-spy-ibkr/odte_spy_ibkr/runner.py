from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone

from odte_spy_ibkr.config import Settings, load_settings
from odte_spy_ibkr.ib_client import IBSession
from odte_spy_ibkr.logging_utils import setup_logging
from odte_spy_ibkr.providers.aggregate import MarketContextAggregator
from odte_spy_ibkr.providers.registry import build_providers
from odte_spy_ibkr.risk import RiskLimits, RiskState, check_new_entry, is_rth, should_flatten
from odte_spy_ibkr.strategy.credit_spread import build_entry_plan

logger = logging.getLogger(__name__)


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

    aggregator = MarketContextAggregator(
        build_providers(settings, ib),
        underlying=settings.underlying,
    )
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
            logger.info(
                "MarketContext spot=%s bias=%s strength=%.2f macro_risk_off=%s providers=%s",
                ctx.spot,
                ctx.flow_bias.value,
                ctx.flow_strength,
                ctx.macro_risk_off,
                list(ctx.provider_notes.keys()),
            )

            now = time.monotonic()
            if now - last_uw_poll >= settings.uw_poll_interval_sec:
                last_uw_poll = now
                logger.debug("UW poll tick at %s", datetime.now(tz=timezone.utc).isoformat())

            decision, reason = check_new_entry(limits, state)
            if should_flatten(limits):
                logger.info("Flatten window reached")
            elif decision.name == "ALLOW" and ib is not None:
                candidates = ib.fetch_chain_candidates(ctx.spot)
                plan = build_entry_plan(settings, ctx, candidates)
                if plan:
                    logger.info(
                        "Entry candidate %s spread credit~%s gate=%s",
                        plan.side.value,
                        plan.spread.estimated_credit,
                        plan.gate.reason,
                    )
                    if settings.orders_allowed:
                        ib.place_combo_order(
                            {
                                "side": plan.side.value,
                                "short": plan.spread.short_leg.symbol,
                                "long": plan.spread.long_leg.symbol,
                            }
                        )
                else:
                    logger.info("No entry plan (risk/gate/chain)")
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