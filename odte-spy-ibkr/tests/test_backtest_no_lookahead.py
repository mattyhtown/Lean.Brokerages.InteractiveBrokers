from datetime import date, datetime
from zoneinfo import ZoneInfo

from odte_spy_ibkr.backtest.replay import build_backtest_context
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.providers.macro_news import score_headlines_before

ET = ZoneInfo("America/New_York")


def test_macro_blackout_not_active_before_release_window(tmp_path, monkeypatch):
    settings = Settings(
        MACRO_CALENDAR_PATH=str(tmp_path / "macro_events.yaml"),
        MACRO_BLACKOUT_BEFORE_MIN=30,
        MACRO_BLACKOUT_AFTER_MIN=45,
    )
    (tmp_path / "macro_events.yaml").write_text(
        "events:\n  - name: CPI\n    release_at: '2026-11-15T13:30:00-05:00'\n",
        encoding="utf-8",
    )
    day = date(2026, 11, 15)
    ctx = build_backtest_context(
        settings,
        day=day,
        spot=500,
        now=datetime(2026, 11, 15, 11, 0, tzinfo=ET),
    )
    assert ctx is not None
    assert ctx.macro_event_blackout is False


def test_headline_lookahead_filtered(tmp_path):
    settings = Settings(BACKTEST_CACHE_DIR=str(tmp_path))
    day = date(2026, 11, 15)
    cache = tmp_path / "macro_news" / f"{day.isoformat()}.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        '[{"published_at": "2026-11-15T18:30:00+00:00", "headline": "CPI inflation shock", "symbols": ["SPY"], "sentiment": -0.5}]',
        encoding="utf-8",
    )
    as_of = datetime(2026, 11, 15, 11, 0, tzinfo=ET)
    assert score_headlines_before(settings, day, as_of) == 0.0