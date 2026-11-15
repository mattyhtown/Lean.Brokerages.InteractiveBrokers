from __future__ import annotations

import logging
from datetime import datetime, timezone

from odte_spy_ibkr.alphavantage.client import AV_API_NOTES, fetch_global_quote, is_configured
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.base import BaseProvider, MarketContext, MarketContextDelta, ProviderNotConfigured

logger = logging.getLogger(__name__)


class AlphaVantageProvider(BaseProvider):
    """Alpha Vantage equity quote cross-check (GLOBAL_QUOTE); options via research ingest."""

    provider_id = "alphavantage"

    def __init__(self, settings: Settings) -> None:
        super().__init__(enabled="alphavantage" in settings.providers_enabled_set)
        self._settings = settings

    def is_configured(self) -> bool:
        return is_configured(self._settings.alphavantage_api_key)

    def _fetch(self, ctx: MarketContext | None) -> MarketContextDelta:
        if not self.is_configured():
            raise ProviderNotConfigured(
                self.provider_id,
                "Set ALPHAVANTAGE_API_KEY for Alpha Vantage provider",
            )
        symbol = ctx.underlying if ctx else self._settings.underlying
        try:
            spot = fetch_global_quote(
                symbol=symbol,
                api_key=self._settings.alphavantage_api_key,
                base_url=self._settings.alphavantage_base_url,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("alphavantage quote failed: %s", type(exc).__name__)
            return MarketContextDelta(
                provider_id=self.provider_id,
                as_of=datetime.now(tz=timezone.utc),
                note="alphavantage_error",
                error=str(type(exc).__name__),
            )
        return MarketContextDelta(
            provider_id=self.provider_id,
            spot=spot,
            spot_weight=0.25,
            as_of=datetime.now(tz=timezone.utc),
            note="global_quote",
            raw={"api_notes": AV_API_NOTES, "symbol": symbol},
        )