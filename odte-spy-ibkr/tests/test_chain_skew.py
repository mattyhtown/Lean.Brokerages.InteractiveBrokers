from datetime import date

from odte_spy_ibkr.chain import OptionCandidate, build_chain_skew, net_call_risk_notional
from odte_spy_ibkr.chain.legs import build_vertical_spread


def _leg(strike: float, right: str, *, vol=100, oi=500, mid=0.5) -> OptionCandidate:
    return OptionCandidate(
        symbol=f"SPY {strike}{right}",
        strike=strike,
        right=right,  # type: ignore[arg-type]
        expiry=date(2026, 11, 15),
        bid=mid - 0.02,
        ask=mid + 0.02,
        volume=vol,
        open_interest=oi,
    )


def test_net_call_bias_bullish_when_calls_dominate():
    expiry = date(2026, 11, 15)
    rows = [
        _leg(501, "C", vol=300, oi=1000, mid=0.8),
        _leg(499, "P", vol=80, oi=200, mid=0.3),
    ]
    skew = build_chain_skew(rows, expiry=expiry, spot=500)
    assert skew.net_call_bias > 0.2


def test_net_call_risk_put_spread_negative_proxy():
    short = _leg(495, "P")
    long = _leg(494, "P")
    spread = build_vertical_spread(short, [short, long], width=1.0)
    assert spread is not None
    assert net_call_risk_notional(spread) < 0