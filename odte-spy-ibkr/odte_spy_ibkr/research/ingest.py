from __future__ import annotations

import logging
from datetime import date
from typing import Literal

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.research import alpaca_ingest, ibkr_ingest, orats_ingest, uw_ingest

logger = logging.getLogger(__name__)

ResearchSource = Literal["alpaca", "uw", "ibkr", "orats", "all"]


def log_credential_status(settings: Settings) -> dict[str, bool]:
    status = {
        "ALPACA_API_KEY": bool(settings.alpaca_api_key and settings.alpaca_api_secret),
        "UNUSUAL_WHALES_API_KEY": bool(settings.unusual_whales_api_key.strip()),
        "ORATS_API_TOKEN": bool(settings.orats_api_token_resolved),
        "IB_SESSION": False,
    }
    logger.info(
        "Research credential scan: alpaca=%s uw=%s orats=%s (IB requires live TWS/Gateway)",
        status["ALPACA_API_KEY"],
        status["UNUSUAL_WHALES_API_KEY"],
        status["ORATS_API_TOKEN"],
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
        sources = ["alpaca", "uw", "ibkr", "orats"]
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
    return out