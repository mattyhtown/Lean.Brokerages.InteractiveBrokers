"""Unit tests for smoke-test-ib.py helpers (no TWS/Gateway required)."""

from __future__ import annotations

import datetime
import importlib.util
import unittest
from pathlib import Path

SMOKE_PATH = Path(__file__).resolve().parent / "smoke-test-ib.py"
_SPEC = importlib.util.spec_from_file_location("smoke_test_ib", SMOKE_PATH)
assert _SPEC and _SPEC.loader
smoke_test_ib = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(smoke_test_ib)


class SmokeTestIbHelpersTest(unittest.TestCase):
    def test_pick_front_expiry_skips_mid_month_expired_contract(self) -> None:
        contracts = [
            {"lastTradeDateOrContractMonth": "20261218"},
            {"lastTradeDateOrContractMonth": "20270319"},
            {"lastTradeDateOrContractMonth": "20270618"},
        ]
        # After December expiry on Dec 18, must skip 20261218 and pick 20270319.
        picked = smoke_test_ib._pick_front_expiry(contracts, today=datetime.date(2026, 12, 20))
        self.assertEqual(picked, "20270319")

    def test_pick_front_expiry_keeps_contract_on_expiry_day_and_strips_time(self) -> None:
        contracts = [
            {"lastTradeDateOrContractMonth": "20261218 08:30 US/Central"},
            {"lastTradeDateOrContractMonth": "20270319"},
        ]
        picked = smoke_test_ib._pick_front_expiry(contracts, today=datetime.date(2026, 12, 18))
        self.assertEqual(picked, "20261218")

    def test_pick_front_expiry_returns_empty_when_all_contracts_expired(self) -> None:
        contracts = [
            {"lastTradeDateOrContractMonth": "20260918"},
            {"lastTradeDateOrContractMonth": "20261218"},
        ]
        picked = smoke_test_ib._pick_front_expiry(contracts, today=datetime.date(2026, 12, 20))
        self.assertEqual(picked, "")

    def test_pick_front_expiry_handles_six_digit_contract_months(self) -> None:
        contracts = [
            {"lastTradeDateOrContractMonth": "202612"},
            {"lastTradeDateOrContractMonth": "202703"},
        ]
        picked = smoke_test_ib._pick_front_expiry(contracts, today=datetime.date(2026, 12, 20))
        self.assertEqual(picked, "202703")


if __name__ == "__main__":
    unittest.main()
