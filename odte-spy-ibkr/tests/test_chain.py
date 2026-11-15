from datetime import date

from odte_spy_ibkr.chain import (
    OptionCandidate,
    build_vertical_spread,
    filter_expiry,
    select_by_delta_band,
    select_by_otm_distance,
)


def _c(strike: float, right: str, delta: float | None = None, bid=0.5, ask=0.6) -> OptionCandidate:
    return OptionCandidate(
        symbol=f"SPY {strike}{right}",
        strike=strike,
        right=right,  # type: ignore[arg-type]
        expiry=date(2026, 11, 15),
        delta=delta,
        bid=bid,
        ask=ask,
    )


def test_filter_expiry():
    expiry = date(2026, 11, 15)
    rows = [_c(500, "P"), OptionCandidate("x", 501, "P", date(2026, 11, 16))]
    assert len(filter_expiry(rows, expiry)) == 1


def test_select_by_delta_band():
    rows = [_c(495, "P", 0.15), _c(490, "P", 0.08)]
    hit = select_by_delta_band(rows, right="P", delta_min=0.1, delta_max=0.2)
    assert hit is not None
    assert hit.strike == 495


def test_select_by_otm_distance_puts():
    spot = 500.0
    rows = [_c(496, "P"), _c(492, "P"), _c(498, "P")]
    hit = select_by_otm_distance(rows, right="P", spot=spot, distance=5)
    assert hit is not None
    assert hit.strike == 492


def test_build_vertical_put_spread():
    short = _c(495, "P", bid=0.4, ask=0.45)
    long = _c(494, "P", bid=0.1, ask=0.15)
    spread = build_vertical_spread(short, [short, long], width=1.0)
    assert spread is not None
    assert spread.estimated_credit == 0.25