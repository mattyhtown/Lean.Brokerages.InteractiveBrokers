from odte_spy_ibkr.backtest.metrics import max_rolling_sharpe, rolling_sharpe_series


def test_rolling_sharpe_positive_trend():
    returns = [0.001 + (i % 5) * 0.00005 for i in range(80)]
    series = rolling_sharpe_series(returns, window=60)
    assert series[-1] is not None
    assert series[-1] > 0


def test_require_sharpe_target_passes():
    returns = [0.002 + (i % 4) * 0.0001 for i in range(120)]
    mx = max_rolling_sharpe(returns, window=60)
    assert mx is not None
    assert mx > 0