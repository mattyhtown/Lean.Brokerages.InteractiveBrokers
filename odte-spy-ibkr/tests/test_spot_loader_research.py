from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from odte_spy_ibkr.backtest.spot_loader import load_spot_for_day
from odte_spy_ibkr.config import Settings


def test_spot_from_research_minute_no_silent_default(tmp_path, monkeypatch):
    settings = Settings()
    day = date(2026, 7, 2)
    minute_dir = Path("data/research/alpaca/minute")
    if not (minute_dir / f"{day.isoformat()}.json").is_file():
        return
    spot = load_spot_for_day(settings, ticker="SPY", day=day)
    assert spot is not None
    assert spot > 500


def test_spot_missing_returns_none():
    settings = Settings()
    spot = load_spot_for_day(settings, ticker="SPY", day=date(1999, 1, 4))
    assert spot is None