from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from odte_spy_ibkr.chain.bs_greeks import black_scholes_delta, implied_volatility
from odte_spy_ibkr.chain.enrich import enrich_chain_rows
from odte_spy_ibkr.research.features import SIGNAL_TIME

ET = ZoneInfo("America/New_York")


def test_implied_vol_recovers_known_price():
    spot, strike, t = 100.0, 100.0, 0.02
    vol = 0.25
    price = spot * 0.5  # rough ATM-ish; use BS forward
    from odte_spy_ibkr.chain.bs_greeks import black_scholes_price

    price = black_scholes_price(spot=spot, strike=strike, t=t, vol=vol, right="C")
    assert price is not None
    iv = implied_volatility(price=price, spot=spot, strike=strike, t=t, right="C")
    assert iv is not None
    assert abs(iv - vol) < 0.02


def test_enrich_chain_row_computes_put_delta():
    day = date(2026, 7, 2)
    as_of = datetime.combine(day, SIGNAL_TIME, tzinfo=ET)
    rows = [
        {
            "symbol": "SPY260702P00740000",
            "strike": 740.0,
            "right": "P",
            "bid": 2.5,
            "ask": 2.5,
            "delta": None,
            "volume": 10,
            "open_interest": 0,
        }
    ]
    enriched, stats = enrich_chain_rows(rows, day=day, spot=745.0, as_of=as_of)
    assert stats["computed"] == 1
    assert enriched[0]["delta"] is not None
    assert enriched[0]["delta"] < 0


def test_enrich_leaves_null_when_no_price():
    day = date(2026, 7, 2)
    rows = [{"strike": 740.0, "right": "P", "bid": 0, "ask": 0, "delta": None}]
    enriched, stats = enrich_chain_rows(rows, day=day, spot=745.0)
    assert stats["failed"] == 1
    assert enriched[0]["delta"] is None