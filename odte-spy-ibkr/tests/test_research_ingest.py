from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.research import ibkr_ingest, orats_ingest, uw_ingest
from odte_spy_ibkr.research.orats_ingest import orats_rows_to_chain_candidates


def test_orats_rows_filters_0dte_and_splits_call_put():
    rows = [
        {
            "expirDate": "2026-07-01",
            "strike": "620",
            "callBidPrice": "1.0",
            "callAskPrice": "1.1",
            "putBidPrice": "0.5",
            "putAskPrice": "0.6",
            "callVolume": "10",
            "putVolume": "20",
            "callOpenInterest": "100",
            "putOpenInterest": "200",
            "delta": "0.25",
        },
        {
            "expirDate": "2026-07-08",
            "strike": "620",
            "callBidPrice": "1.0",
            "callAskPrice": "1.1",
            "putBidPrice": "0.5",
            "putAskPrice": "0.6",
        },
    ]
    out = orats_rows_to_chain_candidates(rows, day=date(2026, 7, 1))
    assert len(out) == 2
    assert {r["right"] for r in out} == {"C", "P"}


def test_orats_ingest_requires_token():
    settings = Settings(unusual_whales_api_key="", orats_api_token="", alpaca_api_key="", alpaca_api_secret="")
    with pytest.raises(RuntimeError, match="ORATS_API_TOKEN"):
        orats_ingest.require_orats_token(settings)


def test_uw_ingest_requires_key():
    settings = Settings(unusual_whales_api_key="")
    with pytest.raises(RuntimeError, match="UNUSUAL_WHALES_API_KEY"):
        uw_ingest.require_uw_key(settings)


def test_ibkr_ingest_requires_session():
    settings = Settings()
    with patch.object(ibkr_ingest, "IBSession") as ib_cls:
        session = MagicMock()
        session.connect.side_effect = ConnectionRefusedError("no gateway")
        session.is_connected.return_value = False
        ib_cls.return_value = session
        with pytest.raises(RuntimeError, match="Interactive Brokers session not available"):
            ibkr_ingest.require_ib_connection(settings)


def test_orats_client_mock_no_network():
    from odte_spy_ibkr.orats.client import ORATSClient

    client = ORATSClient("test-token")
    with patch("httpx.Client") as client_cls:
        mock_http = MagicMock()
        client_cls.return_value.__enter__.return_value = mock_http
        mock_http.get.return_value.raise_for_status = MagicMock()
        mock_http.get.return_value.text = (
            "ticker,tradeDate,expirDate,dte,strike,stockPrice,callBidPrice,callAskPrice,putBidPrice,putAskPrice,"
            "callVolume,putVolume,callOpenInterest,putOpenInterest,delta\n"
            "SPY,2026-07-01,2026-07-01,0,620,500,1,1.1,0.5,0.6,1,2,10,20,0.3\n"
        )
        rows = client.strikes_chain_at("SPY", date(2026, 7, 1))
        assert rows and rows[0]["strike"] == "620"