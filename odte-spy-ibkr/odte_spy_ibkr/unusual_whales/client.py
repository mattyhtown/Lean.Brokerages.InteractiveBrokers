from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class UnusualWhalesClient:
    """Thin REST client for Unusual Whales (no MCP at runtime)."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.unusualwhales.com",
        timeout: float = 30.0,
        cache_ttl_sec: float = 90.0,
    ) -> None:
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._cache_ttl = cache_ttl_sec
        self._cache: dict[str, tuple[float, Any]] = {}

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        cache_key = f"{path}?{params}"
        now = time.monotonic()
        hit = self._cache.get(cache_key)
        if hit and now - hit[0] < self._cache_ttl:
            return hit[1]

        url = f"{self._base_url}{path}"
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(url, headers=self._headers(), params=params or {})
            resp.raise_for_status()
            data = resp.json()

        self._cache[cache_key] = (now, data)
        return data

    def flow_recent(self, ticker: str, *, limit: int = 100) -> Any:
        """GET /api/stock/{ticker}/flow-recent"""
        return self._get(f"/api/stock/{ticker}/flow-recent", {"limit": limit})

    def flow_alerts(self, ticker: str, *, limit: int = 50) -> Any:
        """GET /api/stock/{ticker}/flow-alerts"""
        return self._get(f"/api/stock/{ticker}/flow-alerts", {"limit": limit})

    def unusual_activity(self, *, ticker: str | None = None, limit: int = 50) -> Any:
        """GET /api/option-activity/unusual"""
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        return self._get("/api/option-activity/unusual", params)

    def flow_per_strike(self, ticker: str, *, date: str | None = None) -> Any:
        """GET /api/stock/{ticker}/flow-per-strike — historical when `date` (YYYY-MM-DD) is set."""
        params: dict[str, Any] = {}
        if date:
            params["date"] = date
        return self._get(f"/api/stock/{ticker}/flow-per-strike", params)

    def option_chains(
        self,
        ticker: str,
        *,
        date: str | None = None,
        greeks: bool = True,
        page: int = 0,
        limit: int = 500,
    ) -> Any:
        """GET /api/stock/{ticker}/option-chains — historical when `date` is set; paginated with greeks=true."""
        params: dict[str, Any] = {"greeks": "true" if greeks else "false", "page": page, "limit": limit}
        if date:
            params["date"] = date
        return self._get(f"/api/stock/{ticker}/option-chains", params)

    def spy_context_bundle(self, ticker: str = "SPY", *, trading_day: date | None = None) -> dict[str, Any]:
        day = trading_day or date.today()
        return {
            "date": day.isoformat(),
            "flow_recent": self.flow_recent(ticker),
            "flow_alerts": self.flow_alerts(ticker),
            "unusual": self.unusual_activity(ticker=ticker),
        }