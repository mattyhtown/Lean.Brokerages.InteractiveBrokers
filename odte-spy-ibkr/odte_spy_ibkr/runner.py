from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from datetime import date

from odte_spy_ibkr.chain import (
    OptionCandidate,
    build_chain_skew,
    spread_intrinsic,
    spread_mark_from_chain,
    today_expiry,
)
from odte_spy_ibkr.chain.cache_io import save_chain_json
from odte_spy_ibkr.config import Settings, load_settings
from odte_spy_ibkr.journal import DailyPnlRow, append_daily_pnl
from odte_spy_ibkr.ib_client import IBSession
from odte_spy_ibkr.logging_utils import setup_logging
from odte_spy_ibkr.providers.aggregate import MarketContextAggregator
from odte_spy_ibkr.providers.chain_skew import ChainSkewProvider
from odte_spy_ibkr.providers.registry import build_providers
from odte_spy_ibkr.risk import RiskDecision, RiskLimits, RiskState, check_new_entry, is_rth, should_flatten
from odte_spy_ibkr.risk.sharpe_gate import allow_new_entries
from odte_spy_ibkr.strategy.interface import OpenPosition, manage_open, propose_entry
from odte_spy_ibkr.unusual_whales.collector import collect_once
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)


def apply_chain_skew(ctx, candidates, spot):
    skew = build_chain_skew(candidates, expiry=today_expiry(), spot=spot, as_of=datetime.now(tz=timezone.utc))
    ctx.net_call_bias = skew.net_call_bias
    ctx.chain_skew = skew
    return ctx


@dataclass
class LoopState:
    risk: RiskState = field(default_factory=RiskState)
    session_day: date | None = None
    session_pnl: float = 0.0
    session_active: bool = False
    session_incomplete: bool = False
    position: OpenPosition | None = None
    last_uw_poll: float = 0.0


def entries_allowed_by_sharpe(settings: Settings) -> tuple[bool, str]:
    """Sharpe gate on new entries. Off unless SHARPE_GATE_ENABLED; the journal is not read then."""
    if not settings.sharpe_gate_enabled:
        return True, "sharpe_gate_disabled"
    return allow_new_entries(
        settings.journal_path,
        window=settings.sharpe_window_days,
        min_obs=settings.sharpe_min_obs,
        sharpe_min=settings.sharpe_min,
        equity_base=settings.backtest_equity_base,
    )


def roll_session(settings: Settings, loop: LoopState, today: date | None) -> None:
    """Journal the finished session's realized PnL and start `today` with a clean day state."""
    if loop.session_day is not None and loop.session_day != today:
        if loop.position is not None:
            logger.error(
                "Position from %s was never marked closed; its PnL is unknown and the day is "
                "not journaled. Ingest the broker statement: python -m odte_spy_ibkr.journal ingest",
                loop.session_day,
            )
            loop.position = None
            loop.session_incomplete = True
        if loop.session_active and not loop.session_incomplete:
            source = "live" if settings.live_trading else "paper"
            append_daily_pnl(
                settings.journal_path,
                DailyPnlRow(day=loop.session_day, pnl=loop.session_pnl, source=source),
            )
        loop.session_pnl = 0.0
        loop.session_active = False
        loop.session_incomplete = False
        loop.risk = RiskState()
    loop.session_day = today


def _fetch_chain(ib, spot: float | None) -> list[OptionCandidate]:
    return ib.fetch_chain_candidates(spot) if ib is not None else []


def _manage_position(settings: Settings, loop: LoopState, ctx, *, ib, limits: RiskLimits, now: datetime) -> None:
    position = loop.position
    assert position is not None
    mark = spread_mark_from_chain(position.spread, _fetch_chain(ib, ctx.spot))
    action = manage_open(settings, position, ctx, now=now, spread_mark=mark)
    if action == "hold" and should_flatten(limits, now):
        action = "flatten"
    if action == "hold":
        return
    if mark is None and ctx.spot is not None and should_flatten(limits, now):
        # No quotes this late in the day: value the spread at intrinsic.
        mark = spread_intrinsic(position.spread, ctx.spot)
    if mark is None:
        logger.warning("Exit signal %s but no spread mark available; holding", action)
        return
    exit_pnl = (position.entry_credit - mark) * 100 * position.contracts
    loop.session_pnl += exit_pnl
    loop.risk = RiskState(realized_pnl=loop.risk.realized_pnl + exit_pnl, open_contracts=0)
    loop.position = None
    logger.info("Position closed action=%s mark=%.2f exit_pnl=%.2f", action, mark, exit_pnl)


def _try_entry(settings: Settings, loop: LoopState, ctx, *, ib, chain_skew_provider, now: datetime) -> None:
    candidates = _fetch_chain(ib, ctx.spot)
    if not candidates:
        logger.info("No entry intent (no chain)")
        return
    save_chain_json(
        candidates,
        base_dir=settings.backtest_cache_dir,
        ticker=settings.underlying,
        snapshot_at=now,
    )
    chain_skew_provider.set_candidates(candidates)
    ctx = apply_chain_skew(ctx, candidates, ctx.spot)
    intent = propose_entry(settings, ctx, candidates, now=now)
    if not intent:
        logger.info("No entry intent")
        return
    ctx.net_call_risk = intent.net_call_risk
    logger.info(
        "Entry intent %s credit~%s net_call_bias=%.2f net_call_risk=%.0f reason=%s",
        intent.side.value,
        intent.spread.estimated_credit,
        ctx.net_call_bias,
        intent.net_call_risk,
        intent.reason,
    )
    credit = intent.spread.estimated_credit or settings.min_credit
    # The credit is not PnL: nothing is realized until the spread is closed or expires.
    loop.position = OpenPosition(
        spread=intent.spread,
        side=intent.side,
        contracts=intent.contracts,
        entry_credit=credit,
        opened_at=now,
    )
    loop.risk = RiskState(realized_pnl=loop.risk.realized_pnl, open_contracts=intent.contracts)
    short_cid = getattr(intent.spread.short_leg, "con_id", None)
    long_cid = getattr(intent.spread.long_leg, "con_id", None)
    if settings.orders_allowed and ib is not None and short_cid and long_cid:
        ib.place_combo_order(
            {
                "side": intent.side.value,
                "short_con_id": short_cid,
                "long_con_id": long_cid,
                "contracts": intent.contracts,
                "lmt_price": credit,
            }
        )


def step(
    settings: Settings,
    loop: LoopState,
    *,
    ib,
    aggregator,
    chain_skew_provider,
    limits: RiskLimits,
    now: datetime | None = None,
) -> None:
    """One poll. An open position is always managed; the risk limits and the Sharpe gate
    only decide whether a new one may be opened."""
    now = now or datetime.now(tz=ET)
    roll_session(settings, loop, now.astimezone(ET).date())
    if not is_rth(now):
        logger.info("Outside RTH; sleeping")
        return
    loop.session_active = True
    ctx = aggregator.build()

    if loop.position is not None:
        _manage_position(settings, loop, ctx, ib=ib, limits=limits, now=now)
        return
    if should_flatten(limits, now):
        logger.info("Flatten window reached")
        return
    decision, reason = check_new_entry(limits, loop.risk, now=now)
    if decision != RiskDecision.ALLOW:
        logger.info("Entry blocked: %s", reason)
        return
    allowed, sharpe_reason = entries_allowed_by_sharpe(settings)
    if not allowed:
        logger.info("Sharpe gate blocked entries: %s", sharpe_reason)
        return
    _try_entry(settings, loop, ctx, ib=ib, chain_skew_provider=chain_skew_provider, now=now)


def _poll_unusual_whales(settings: Settings, loop: LoopState) -> None:
    mono = time.monotonic()
    if mono - loop.last_uw_poll < settings.uw_poll_interval_sec:
        return
    loop.last_uw_poll = mono
    if not settings.unusual_whales_api_key:
        logger.debug("UW poll skipped (no API key)")
        return
    try:
        collect_once(settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("UW collector failed: %s", exc)


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
    loop = LoopState()

    try:
        while True:
            now = datetime.now(tz=ET)
            if is_rth(now):
                _poll_unusual_whales(settings, loop)
            step(
                settings,
                loop,
                ib=ib,
                aggregator=aggregator,
                chain_skew_provider=chain_skew_provider,
                limits=limits,
                now=now,
            )
            if once:
                break
            time.sleep(settings.poll_interval_sec)
    finally:
        if loop.position is not None:
            logger.warning("Stopping with an open position; only closed trades are journaled")
            loop.position = None
        roll_session(settings, loop, None)
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