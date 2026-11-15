from __future__ import annotations

import logging
from datetime import date, time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ThetaTerminalNotRunning(RuntimeError):
    """Raised when the v3 Theta Terminal loopback server is unreachable."""


class ThetaDataClient:
    """
    Talks to the local Theta Terminal REST server (default 127.0.0.1:25503).

  Auth is session-level when the terminal starts (email/password or API key), not per HTTP call.
    See https://http-docs.thetadata.us/Articles/Getting-Started/Getting-Started.html
    """

    def __init__(self, *, base_url: str = "http://127.0.0.1:25503", timeout_sec: float = 120.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_sec

    def ping(self) -> bool:
        """Any HTTP response from loopback counts as reachable (auth is on terminal start)."""
        try:
            with httpx.Client(timeout=3.0) as client:
                client.get(f"{self._base}/v3/option/history/quote", params={"symbol": "SPY", "format": "json"})
                return True
        except httpx.ConnectError:
            return False
        except httpx.HTTPError:
            return True

    def require_terminal(self) -> None:
        if not self.ping():
            raise ThetaTerminalNotRunning(
                "Theta Terminal v3 is not reachable at "
                f"{self._base}. Start it locally, e.g. "
                "`java -jar ThetaTerminalv3.jar <THETADATA_USERNAME> <THETADATA_PASSWORD>` "
                "(credentials are read at terminal launch, not on each HTTP call)."
            )

    def option_history_quote(
        self,
        *,
        symbol: str,
        expiration: date,
        quote_date: date,
        start_time: time,
        end_time: time,
        strike_range: int = 15,
        max_dte: int = 0,
        interval: str = "1m",
    ) -> list[dict[str, Any]]:
        params = {
            "symbol": symbol,
            "expiration": expiration.strftime("%Y%m%d"),
            "date": quote_date.strftime("%Y%m%d"),
            "start_time": start_time.strftime("%H:%M:%S"),
            "end_time": end_time.strftime("%H:%M:%S"),
            "interval": interval,
            "strike_range": strike_range,
            "max_dte": max_dte,
            "right": "both",
            "format": "json",
        }
        url = f"{self._base}/v3/option/history/quote"
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(url, params=params)
            if resp.status_code >= 400:
                logger.warning("Theta quote %s: %s %s", quote_date, resp.status_code, resp.text[:300])
                return []
            payload = resp.json()
        return _coerce_quote_rows(payload)


def _coerce_quote_rows(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("response", "data", "quotes", "rows"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
        # single-contract envelope
        if "strike" in payload or "bid" in payload:
            return [payload]
    return []