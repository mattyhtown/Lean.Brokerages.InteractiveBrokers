from __future__ import annotations

from datetime import date
from unittest.mock import patch

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.research import openbb_ingest


def test_openbb_ingest_skips_when_chain_exists(tmp_path, monkeypatch):
    settings = Settings(UNDERLYING="SPY")
    chain_root = tmp_path / "chains" / "SPY"
    chain_root.mkdir(parents=True)
    day = date(2026, 7, 2)
    (chain_root / f"{day.isoformat()}.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(openbb_ingest, "CHAIN_ROOT", chain_root)
    monkeypatch.setattr(openbb_ingest, "OPENBB_ROOT", tmp_path / "openbb")
    monkeypatch.setattr(openbb_ingest, "require_openbb", lambda: None)
    monkeypatch.setattr(openbb_ingest, "configure_openbb_session", lambda pat: None)

    stats = openbb_ingest.ingest_range(settings, start=day, end=day)
    assert stats["days_skipped_existing"] == 1
    assert stats["days_chain"] == 0


@patch("odte_spy_ibkr.research.openbb_ingest.fetch_option_chains", return_value=[])
def test_openbb_ingest_counts_empty_days(mock_fetch, tmp_path, monkeypatch):
    settings = Settings(UNDERLYING="SPY")
    openbb_root = tmp_path / "openbb"
    chain_root = tmp_path / "chains" / "SPY"
    chain_root.mkdir(parents=True)

    monkeypatch.setattr(openbb_ingest, "CHAIN_ROOT", chain_root)
    monkeypatch.setattr(openbb_ingest, "OPENBB_ROOT", openbb_root)
    monkeypatch.setattr(openbb_ingest, "require_openbb", lambda: None)
    monkeypatch.setattr(openbb_ingest, "configure_openbb_session", lambda pat: None)
    monkeypatch.setattr(
        openbb_ingest,
        "load_minute_bars",
        lambda d: [{"timestamp": "2026-07-02T15:00:00Z", "close": 500.0}],
    )
    monkeypatch.setattr(openbb_ingest, "_price_at", lambda bars, t: 500.0)

    day = date(2026, 7, 2)
    stats = openbb_ingest.ingest_range(settings, start=day, end=day)
    assert stats["days_empty"] == 1
    mock_fetch.assert_called_once()