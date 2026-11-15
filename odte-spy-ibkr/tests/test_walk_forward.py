from __future__ import annotations

from datetime import date, timedelta

from odte_spy_ibkr.backtest.walk_forward import holdout_test_days, walk_forward_windows


def test_holdout_excludes_initial_train_window():
    start = date(2024, 1, 1)
    days = [start + timedelta(days=i) for i in range(100)]
    test = holdout_test_days(days, train_days=60, test_days=20)
    assert len(test) == 20
    assert test[0] == days[80]