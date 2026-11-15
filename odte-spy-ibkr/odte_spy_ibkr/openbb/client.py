from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable

logger = logging.getLogger(__name__)

OPENBB_HISTORICAL_NOTES = """
OpenBB options historical 0DTE coverage (derivatives.options.chains):
- cboe, yfinance, tradier: current/live chains only — no reliable dated history for backtest.
- intrinio, tmx: support a `date` parameter (EOD snapshot) — typically require OpenBB Hub PAT
  (OPENBB_PAT) and/or provider API keys synced via obb.account.login / user_settings.json.
- For Jul–Nov 2026 SPY 0DTE backtests, prefer existing research caches (alpaca_option_bars)
  when OpenBB historical is unavailable; ingest records skips instead of synthetic chains.
""".strip()


def openbb_available() -> bool:
    try:
        import openbb  # noqa: F401

        return True
    except ImportError:
        return False


def require_openbb() -> None:
    if not openbb_available():
        raise RuntimeError(
            "OpenBB Python package is not installed. Install with: "
            "pip install 'odte-spy-ibkr[openbb]' or pip install openbb>=4.0"
        )


def configure_openbb_session(pat: str) -> None:
    """Login to OpenBB Hub when PAT is set (no-op when empty)."""
    if not pat.strip():
        return
    require_openbb()
    from openbb import obb

    try:
        obb.account.login(pat=pat.strip())
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenBB login failed (check OPENBB_PAT): %s", type(exc).__name__)


def _pick_provider(*, historical: bool, live_provider: str, historical_provider: str, pat: str) -> str:
    if historical:
        if not pat.strip() and historical_provider in {"intrinio", "tmx", "polygon"}:
            return "yfinance"  # will likely fail for date= — handled downstream
        return historical_provider
    return live_provider


def fetch_option_chains(
    *,
    symbol: str,
    day: date | None,
    provider: str,
    chains_callable: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch options chain via OpenBB and normalize to research row dicts.
    `chains_callable` supports tests (injected fake obb.derivatives.options.chains).
    """
    require_openbb()
    if chains_callable is None:
        from openbb import obb

        chains_callable = obb.derivatives.options.chains

    kwargs: dict[str, Any] = {"symbol": symbol, "provider": provider}
    if day is not None:
        kwargs["date"] = day.isoformat()

    try:
        result = chains_callable(**kwargs)
    except Exception as exc:
        logger.warning("OpenBB chains %s %s provider=%s: %s", symbol, day, provider, exc)
        return []

    return _normalize_chain_result(result, trading_day=day)


def _normalize_chain_result(result: Any, *, trading_day: date | None) -> list[dict[str, Any]]:
    df = getattr(result, "dataframe", None)
    if df is None and hasattr(result, "results"):
        df = getattr(result.results, "dataframe", None)
    if df is None:
        return []

    try:
        import pandas as pd

        if not isinstance(df, pd.DataFrame):
            return []
    except ImportError:
        return []

    rows: list[dict[str, Any]] = []
    day_s = trading_day.isoformat() if trading_day else None
    for _, record in df.iterrows():
        row = record.to_dict()
        expiry = _coerce_date(row.get("expiration") or row.get("expiry") or row.get("expiration_date"))
        if day_s and expiry and expiry != day_s:
            continue
        right = _option_right(row)
        if right is None:
            continue
        strike = _float(row.get("strike"))
        if strike is None or strike <= 0:
            continue
        bid = _float(row.get("bid") or row.get("close_bid") or row.get("open_bid"))
        ask = _float(row.get("ask") or row.get("close_ask") or row.get("open_ask"))
        mark = _float(row.get("mark"))
        if bid is None and mark is not None:
            bid = mark
        if ask is None and mark is not None:
            ask = mark
        delta = _float(row.get("delta"))
        symbol = str(row.get("contract_symbol") or row.get("symbol") or "")
        if not symbol:
            symbol = f"{row.get('underlying_symbol', 'SPY')}{expiry or day_s}{right}{int(strike)}"
        rows.append(
            {
                "symbol": symbol,
                "strike": strike,
                "right": right,
                "bid": bid,
                "ask": ask,
                "delta": delta,
                "volume": _float(row.get("volume")) or 0.0,
                "open_interest": _float(row.get("open_interest")) or 0.0,
            }
        )
    return rows


def _coerce_date(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw)
    return s[:10] if len(s) >= 10 else None


def _option_right(row: dict[str, Any]) -> str | None:
    raw = row.get("option_type") or row.get("type") or row.get("right") or ""
    s = str(raw).lower()
    if s in {"call", "c"}:
        return "C"
    if s in {"put", "p"}:
        return "P"
    sym = str(row.get("contract_symbol") or "")
    if "C" in sym[-12:] and "P" not in sym[-12:]:
        return "C"
    if "P" in sym[-12:]:
        return "P"
    return None


def _float(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
        if v != v:  # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None