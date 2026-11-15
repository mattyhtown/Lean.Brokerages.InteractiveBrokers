from __future__ import annotations

from datetime import date

import pytest

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.quantconnect.client import qc_auth_headers
from odte_spy_ibkr.quantconnect.lean_reader import parse_lean_equity_minute_lines
from odte_spy_ibkr.research import quantconnect_ingest


def test_qc_auth_headers_shape():
    h = qc_auth_headers(user_id="123", api_token="secret")
    assert h["Authorization"].startswith("Basic ")
    assert h["Timestamp"].isdigit()


def test_parse_lean_equity_minute():
    text = "20260701 09:30,560.1,560.2,560.0,560.15,1000\n20260701 11:00,561.0,561.1,560.9,561.0,500\n"
    bars = parse_lean_equity_minute_lines(text, day=date(2026, 7, 1))
    assert len(bars) == 2
    assert bars[1]["c"] == pytest.approx(561.0)


def test_quantconnect_ingest_requires_credentials():
    settings = Settings(quantconnect_user_id="", quantconnect_api_token="")
    with pytest.raises(RuntimeError, match="QUANTCONNECT_USER_ID"):
        quantconnect_ingest.ingest_range(settings, start=date(2026, 7, 1), end=date(2026, 7, 2))