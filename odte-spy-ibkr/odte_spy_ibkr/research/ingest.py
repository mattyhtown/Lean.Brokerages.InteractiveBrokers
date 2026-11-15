from __future__ import annotations

import logging
from datetime import date
from typing import Literal

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.research import (
    alpaca_ingest,
    ibkr_ingest,
    orats_ingest,
    quantconnect_ingest,
    thetadata_ingest,
    tradingeconomics_ingest,
    uw_ingest,
)

logger = logging.getLogger(__name__)

ResearchSource = Literal[
    "alpaca",
    "uw",
    "ibkr",
    "orats",
    "thetadata",
    "tradingeconomics",
    "quantconnect",
    "all",
]


def resolve_ingest_sources(sources: list[ResearchSource] | None) -> list[ResearchSource]:
    """Default to alpaca only when --source is omitted; do not merge default with explicit sources."""
    if sources is None:
        return ["alpaca"]
    return list(sources)


def log_credential_status(settings: Settings) -> dict[str, bool]:
    status = {
        "ALPACA_API_KEY": bool(settings.alpaca_api_key and settings.alpaca_api_secret),
        "UNUSUAL_WHALES_API_KEY": bool(settings.unusual_whales_api_key.strip()),
        "ORATS_API_TOKEN": bool(settings.orats_api_token_resolved),
        "THETADATA_USERNAME": bool(settings.thetadata_username.strip() and settings.thetadata_password.strip()),
        "TRADING_ECONOMICS_API_KEY": bool(settings.trading_economics_api_key.strip()),
        "QUANTCONNECT_USER_ID": bool(
            settings.quantconnect_user_id.strip() and settings.quantconnect_api_token.strip()
        ),
        "IB_SESSION": False,
    }
    logger.info(
        "Research credential scan: alpaca=%s uw=%s orats=%s thetadata=%s tradingeconomics=%s "
        "quantconnect=%s (IB=TWS; Theta=loopback terminal; QC=LEAN CLI + Dataset Market)",
        status["ALPACA_API_KEY"],
        status["UNUSUAL_WHALES_API_KEY"],
        status["ORATS_API_TOKEN"],
        status["THETADATA_USERNAME"],
        status["TRADING_ECONOMICS_API_KEY"],
        status["QUANTCONNECT_USER_ID"],
    )
    return status


def run_ingest(
    settings: Settings,
    *,
    sources: list[ResearchSource],
    start: date,
    end: date,
) -> dict[str, object]:
    log_credential_status(settings)
    if "all" in sources:
        sources = ["alpaca", "uw", "ibkr", "orats", "thetadata", "tradingeconomics", "quantconnect"]
    out: dict[str, object] = {}
    for src in sources:
        if src == "alpaca":
            out["alpaca"] = alpaca_ingest.ingest_range(settings, start=start, end=end)
        elif src == "uw":
            out["uw"] = uw_ingest.ingest_range(settings, start=start, end=end)
        elif src == "ibkr":
            out["ibkr"] = ibkr_ingest.ingest_range(settings, start=start, end=end)
        elif src == "orats":
            out["orats"] = orats_ingest.ingest_range(settings, start=start, end=end)
        elif src == "thetadata":
            out["thetadata"] = thetadata_ingest.ingest_range(settings, start=start, end=end)
        elif src == "tradingeconomics":
            out["tradingeconomics"] = tradingeconomics_ingest.ingest_range(settings, start=start, end=end)
        elif src == "quantconnect":
            out["quantconnect"] = quantconnect_ingest.ingest_range(settings, start=start, end=end)
    return out