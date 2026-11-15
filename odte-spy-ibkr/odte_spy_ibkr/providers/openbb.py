from __future__ import annotations

import logging
from datetime import datetime, timezone

from odte_spy_ibkr.chain import build_chain_skew, today_expiry
from odte_spy_ibkr.research.chain_io import rows_to_candidates
from odte_spy_ibkr.chain.enrich import enrich_chain_rows
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.openbb.client import configure_openbb_session, fetch_option_chains, openbb_available
from odte_spy_ibkr.providers.base import BaseProvider, MarketContext, MarketContextDelta, ProviderNotConfigured

logger = logging.getLogger(__name__)


class OpenBBProvider(BaseProvider):
    """OpenBB Platform options chains (live context; historical via research ingest)."""

    provider_id = "openbb"

    def __init__(self, settings: Settings) -> None:
        super().__init__(enabled="openbb" in settings.providers_enabled_set)
        self._settings = settings

    def is_configured(self) -> bool:
        return openbb_available()

    def _fetch(self, ctx: MarketContext | None) -> MarketContextDelta:
        if not self.is_configured():
            raise ProviderNotConfigured(
                self.provider_id,
                "Install openbb (pip install 'odte-spy-ibkr[openbb]') to enable OpenBB provider",
            )
        configure_openbb_session(self._settings.openbb_pat)
        symbol = ctx.underlying if ctx else self._settings.underlying
        spot = ctx.spot if ctx else None
        rows = fetch_option_chains(
            symbol=symbol,
            day=None,
            provider=self._settings.openbb_live_provider,
        )
        if not rows:
            return MarketContextDelta(
                provider_id=self.provider_id,
                as_of=datetime.now(tz=timezone.utc),
                note="openbb_empty_chain",
            )
        trading_day = today_expiry()
        if spot and spot > 0 and any(r.get("delta") is None for r in rows):
            rows, _ = enrich_chain_rows(rows, day=trading_day, spot=spot)
        candidates = rows_to_candidates(rows, trading_day)
        skew = build_chain_skew(candidates, expiry=trading_day, spot=spot)
        return MarketContextDelta(
            provider_id=self.provider_id,
            spot=spot,
            spot_weight=0.35,
            net_call_bias=skew.net_call_bias,
            as_of=datetime.now(tz=timezone.utc),
            note=f"openbb:{self._settings.openbb_live_provider}",
            raw={"row_count": len(rows), "provider": self._settings.openbb_live_provider},
        )