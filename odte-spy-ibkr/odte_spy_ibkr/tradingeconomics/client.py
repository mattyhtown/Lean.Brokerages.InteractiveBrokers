from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
TE_BASE = "https://api.tradingeconomics.com"


class TradingEconomicsClient:
    """https://docs.tradingeconomics.com/economic_calendar/"""

    def __init__(self, api_key: str, *, base_url: str = TE_BASE) -> None:
        self._key = api_key.strip()
        self._base = base_url.rstrip("/")

    def calendar_us(self, *, start: date, end: date, values: bool = True) -> list[dict[str, Any]]:
        path = (
            f"/calendar/country/united%20states/"
            f"{start.isoformat()}/{end.isoformat()}"
        )
        params: dict[str, str] = {"c": self._key, "f": "json"}
        if values:
            params["values"] = "true"
        url = f"{self._base}{path}"
        with httpx.Client(timeout=45.0) as client:
            resp = client.get(url, params=params)
            if resp.status_code >= 400:
                logger.warning("TradingEconomics %s: %s", resp.status_code, resp.text[:200])
                resp.raise_for_status()
            data = resp.json()
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return []


def normalize_te_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Map TE calendar row to internal macro + research shape."""
    date_raw = raw.get("Date") or raw.get("date")
    release_at = _parse_release_at(date_raw)
    importance = int(raw.get("Importance") or raw.get("importance") or 0)
    tier = 1 if importance >= 3 else 2 if importance == 2 else 3
    actual_v = _pick_num(raw, "ActualValue", "actual_value")
    forecast_v = _pick_num(raw, "ForecastValue", "forecast_value", "TEForecastValue", "teforecast_value")
    surprise = None
    if actual_v is not None and forecast_v is not None:
        surprise = actual_v - forecast_v
    name = str(raw.get("Event") or raw.get("event") or raw.get("Category") or "macro")
    return {
        "name": name,
        "release_at": release_at,
        "tier": tier,
        "importance": importance,
        "actual": raw.get("Actual") or raw.get("actual"),
        "previous": raw.get("Previous") or raw.get("previous"),
        "forecast": raw.get("Forecast") or raw.get("forecast") or raw.get("TEForecast") or raw.get("teforecast"),
        "actual_value": actual_v,
        "forecast_value": forecast_v,
        "previous_value": _pick_num(raw, "PreviousValue", "previous_value"),
        "surprise": surprise,
        "calendar_id": str(raw.get("CalendarID") or raw.get("CalendarId") or raw.get("calendarId") or ""),
        "source": "tradingeconomics",
        "raw_category": raw.get("Category") or raw.get("category"),
    }


def _parse_release_at(val: Any) -> str:
    if not val:
        return ""
    if isinstance(val, str):
        # TE often returns UTC without offset, e.g. 2026-10-14T12:30:00
        try:
            if val.endswith("Z"):
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            elif "T" in val:
                dt = datetime.fromisoformat(val.replace("Z", ""))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            else:
                dt = datetime.fromisoformat(val).replace(tzinfo=ZoneInfo("UTC"))
            return dt.astimezone(ET).isoformat()
        except ValueError:
            return str(val)
    return str(val)


def _pick_num(raw: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key not in raw:
            continue
        val = raw[key]
        if val is None or val == "":
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None