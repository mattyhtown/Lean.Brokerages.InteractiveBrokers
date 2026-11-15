from __future__ import annotations

from datetime import date
from pathlib import Path

RESEARCH_DATA = Path("data/research")
ALPACA_ROOT = RESEARCH_DATA / "alpaca"
IBKR_ROOT = RESEARCH_DATA / "ibkr"
UW_ROOT = RESEARCH_DATA / "uw"
ORATS_ROOT = RESEARCH_DATA / "orats"
THETADATA_ROOT = RESEARCH_DATA / "thetadata"
TRADINGECONOMICS_ROOT = RESEARCH_DATA / "tradingeconomics"
QUANTCONNECT_ROOT = RESEARCH_DATA / "quantconnect"
CHAIN_ROOT = RESEARCH_DATA / "chains" / "SPY"


def chain_path(day: date, *, snapshot: str = "signal") -> Path:
    if snapshot == "signal":
        return CHAIN_ROOT / f"{day.isoformat()}.json"
    return CHAIN_ROOT / f"{day.isoformat()}_{snapshot}.json"


def minute_path(source_root: Path, day: date) -> Path:
    return source_root / "minute" / f"{day.isoformat()}.json"


def daily_spy_path(source_root: Path) -> Path:
    return source_root / "spy_daily.json"