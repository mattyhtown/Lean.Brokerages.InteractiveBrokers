from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from odte_spy_ibkr.openbb.client import _normalize_chain_result, fetch_option_chains


class _FakeResults:
    def __init__(self, df: pd.DataFrame) -> None:
        self.dataframe = df


def test_normalize_chain_result_filters_0dte_and_maps_greeks():
    df = pd.DataFrame(
        [
            {
                "contract_symbol": "SPY260702P00740000",
                "strike": 740.0,
                "option_type": "put",
                "expiration": "2026-07-02",
                "bid": 1.2,
                "ask": 1.3,
                "delta": -0.18,
                "volume": 50,
                "open_interest": 100,
            },
            {
                "contract_symbol": "SPY260703C00750000",
                "strike": 750.0,
                "option_type": "call",
                "expiration": "2026-07-03",
                "bid": 2.0,
                "ask": 2.1,
                "delta": 0.3,
                "volume": 10,
                "open_interest": 20,
            },
        ]
    )
    rows = _normalize_chain_result(_FakeResults(df), trading_day=date(2026, 7, 2))
    assert len(rows) == 1
    assert rows[0]["right"] == "P"
    assert rows[0]["delta"] == -0.18


def test_fetch_option_chains_uses_injected_callable(monkeypatch):
    df = pd.DataFrame(
        [
            {
                "contract_symbol": "SPY260702C00745000",
                "strike": 745.0,
                "option_type": "call",
                "expiration": "2026-07-02",
                "bid": 1.0,
                "ask": 1.1,
                "delta": 0.15,
                "volume": 1,
                "open_interest": 2,
            }
        ]
    )

    monkeypatch.setattr("odte_spy_ibkr.openbb.client.openbb_available", lambda: True)
    monkeypatch.setattr("odte_spy_ibkr.openbb.client.require_openbb", lambda: None)

    def chains(**kwargs):
        assert kwargs["symbol"] == "SPY"
        assert kwargs["provider"] == "cboe"
        assert kwargs["date"] == "2026-07-02"
        return _FakeResults(df)

    rows = fetch_option_chains(symbol="SPY", day=date(2026, 7, 2), provider="cboe", chains_callable=chains)
    assert len(rows) == 1
    assert rows[0]["strike"] == 745.0


def test_ingest_openbb_requires_package(monkeypatch):
    from odte_spy_ibkr.config import Settings
    from odte_spy_ibkr.openbb import client

    monkeypatch.setattr(client, "openbb_available", lambda: False)
    with pytest.raises(RuntimeError, match="not installed"):
        client.require_openbb()