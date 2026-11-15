from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

import httpx

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.base import BaseProvider, MarketContext, MarketContextDelta

logger = logging.getLogger(__name__)

MAX_PAGES = 500
RATE_LIMIT_RETRIES = 3


class AlpacaDataError(RuntimeError):
    """Alpaca Market Data request failed; callers must not cache anything for it."""


def alpaca_configured(settings: Settings) -> bool:
    return bool(settings.alpaca_api_key and settings.alpaca_api_secret)


def alpaca_paged_get(
    settings: Settings,
    path: str,
    params: dict[str, Any],
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[dict[str, Any]]:
    """Yield every page of a Market Data GET, following `next_page_token`."""
    if not alpaca_configured(settings):
        raise AlpacaDataError("ALPACA_API_KEY / ALPACA_API_SECRET are not set")
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
    }
    url = f"{settings.alpaca_data_url.rstrip('/')}{path}"
    owned = client is None
    http = client or httpx.Client(timeout=30.0)
    try:
        page_token: str | None = None
        for _ in range(MAX_PAGES):
            query = dict(params)
            if page_token:
                query["page_token"] = page_token
            for attempt in range(RATE_LIMIT_RETRIES + 1):
                resp = http.get(url, headers=headers, params=query)
                if resp.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                    break
                sleep(2.0**attempt)
            if resp.status_code >= 400:
                raise AlpacaDataError(f"GET {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
            payload = resp.json()
            yield payload
            page_token = payload.get("next_page_token")
            if not page_token:
                return
        raise AlpacaDataError(f"GET {path} exceeded {MAX_PAGES} pages")
    except httpx.HTTPError as exc:
        raise AlpacaDataError(f"GET {path} failed: {exc}") from exc
    finally:
        if owned:
            http.close()


class AlpacaProvider(BaseProvider):
    """Read-only Alpaca Market Data (SPY snapshot + optional options chain reference)."""

    provider_id = "alpaca"

    def __init__(self, settings: Settings) -> None:
        super().__init__(enabled="alpaca" in settings.providers_enabled_set)
        self._settings = settings
        self._data_url = settings.alpaca_data_url.rstrip("/")
        self._key = settings.alpaca_api_key
        self._secret = settings.alpaca_api_secret

    def is_configured(self) -> bool:
        return bool(self._key and self._secret)

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._key,
            "APCA-API-SECRET-KEY": self._secret,
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._data_url}{path}"
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, headers=self._headers(), params=params or {})
            resp.raise_for_status()
            return resp.json()

    def stock_snapshot(self, symbol: str) -> Any:
        """GET /v2/stocks/{symbol}/snapshot"""
        return self._get(f"/v2/stocks/{symbol}/snapshot")

    def option_snapshots(self, symbol: str, *, limit: int = 50) -> Any:
        """GET /v1beta1/options/snapshots/{underlying}"""
        return self._get(
            f"/v1beta1/options/snapshots/{symbol}",
            {"feed": "indicative", "limit": limit},
        )

    def _fetch(self, ctx: MarketContext | None) -> MarketContextDelta:
        symbol = ctx.underlying if ctx else self._settings.underlying
        snap = self.stock_snapshot(symbol)
        spot = None
        latest = snap.get("latestTrade") or snap.get("latest_trade") or {}
        if "p" in latest:
            spot = float(latest["p"])
        elif "price" in latest:
            spot = float(latest["price"])

        options_meta: dict[str, Any] = {}
        try:
            options_meta = self.option_snapshots(symbol, limit=25)
        except httpx.HTTPError as exc:
            logger.info("alpaca options snapshot unavailable: %s", exc)

        return MarketContextDelta(
            provider_id=self.provider_id,
            spot=spot,
            spot_weight=0.5,
            as_of=datetime.now(tz=timezone.utc),
            note="snapshot",
            raw={"stock_snapshot": snap, "options_snapshot": options_meta},
        )