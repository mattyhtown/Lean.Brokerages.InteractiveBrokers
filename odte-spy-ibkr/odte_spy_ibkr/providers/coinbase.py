from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.base import BaseProvider, MarketContext, MarketContextDelta

logger = logging.getLogger(__name__)


class CoinbaseProvider(BaseProvider):
    """Optional macro risk-off flag from BTC/ETH 24h percent moves (public Exchange stats API)."""

    provider_id = "coinbase"

    def __init__(self, settings: Settings) -> None:
        super().__init__(enabled="coinbase" in settings.providers_enabled_set)
        self._settings = settings
        self._threshold_pct = settings.coinbase_risk_off_threshold_pct
        self._products = settings.coinbase_products_list

    def is_configured(self) -> bool:
        # Public product stats do not require API keys; enabling provider is enough.
        return True

    def _product_change_pct(self, product_id: str) -> float | None:
        url = f"https://api.exchange.coinbase.com/products/{product_id}/stats"
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            stats = resp.json()
        try:
            open_px = float(stats["open"])
            last_px = float(stats["last"])
        except (KeyError, TypeError, ValueError):
            return None
        if open_px <= 0:
            return None
        return (last_px - open_px) / open_px * 100.0

    def _fetch(self, ctx: MarketContext | None) -> MarketContextDelta:
        changes: dict[str, float] = {}
        for product in self._products:
            try:
                chg = self._product_change_pct(product)
                if chg is not None:
                    changes[product] = chg
            except httpx.HTTPError as exc:
                logger.warning("coinbase stats failed for %s: %s", product, exc)

        risk_off = any(chg <= -abs(self._threshold_pct) for chg in changes.values())
        note = ", ".join(f"{k}={v:+.2f}%" for k, v in changes.items()) or "no_data"

        return MarketContextDelta(
            provider_id=self.provider_id,
            macro_risk_off=risk_off,
            as_of=datetime.now(tz=timezone.utc),
            note=note,
            raw={"changes_pct": changes, "threshold_pct": self._threshold_pct},
        )