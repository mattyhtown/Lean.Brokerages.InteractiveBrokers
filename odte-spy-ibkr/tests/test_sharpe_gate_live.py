"""Live Sharpe gate: journal ingestion, the gate decision, and its wiring in the live loop.

The loop runs against fakes for IB, the provider aggregator and the chain; nothing here
connects to a broker.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from odte_spy_ibkr import runner as live_runner
from odte_spy_ibkr.backtest.alpaca_history import occ_symbol
from odte_spy_ibkr.backtest.calendar import iter_trading_days
from odte_spy_ibkr.chain import OptionCandidate
from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.journal import (
    DailyPnlRow,
    append_daily_pnl,
    ingest_broker_csv,
    main as journal_main,
    read_daily_pnl,
)
from odte_spy_ibkr.providers.base import MarketContext
from odte_spy_ibkr.risk import RiskLimits
from odte_spy_ibkr.risk.sharpe_gate import allow_new_entries, load_daily_returns

from conftest import FIXTURE_DAY

ET = ZoneInfo("America/New_York")
DAY = FIXTURE_DAY


def at(hour: int, minute: int, day: date = DAY) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ET)


def write_journal(path: str, pnls: list[float]) -> None:
    days = iter_trading_days(date(2024, 1, 2), date(2024, 12, 31))[: len(pnls)]
    for day, pnl in zip(days, pnls, strict=True):
        append_daily_pnl(path, DailyPnlRow(day=day, pnl=pnl, source="paper"))


LOSING = [-300.0, 100.0] * 30  # 60 sessions, negative mean
WINNING = [300.0, 100.0] * 30


# --- gate decision -------------------------------------------------------------------------


def test_gate_is_off_by_default_and_then_never_reads_the_journal(fixture_settings, monkeypatch):
    assert Settings().sharpe_gate_enabled is False

    def fail(*_args, **_kwargs):
        raise AssertionError("journal must not be read while the gate is off")

    monkeypatch.setattr(live_runner, "allow_new_entries", fail)
    assert live_runner.entries_allowed_by_sharpe(fixture_settings) == (True, "sharpe_gate_disabled")


def test_gate_blocks_on_a_measured_shortfall_only(fixture_settings):
    journal = fixture_settings.journal_path
    kwargs = {"window": 60, "min_obs": 60, "sharpe_min": 1.5}
    assert allow_new_entries(journal, **kwargs) == (True, "insufficient_journal")

    write_journal(journal, LOSING[:59])
    assert allow_new_entries(journal, **kwargs) == (True, "insufficient_journal")

    append_daily_pnl(journal, DailyPnlRow(day=date(2024, 12, 31), pnl=100.0))
    allowed, reason = allow_new_entries(journal, **kwargs)
    assert allowed is False and reason.startswith("rolling_sharpe_-") and reason.endswith("_below_1.5")


def test_gate_allows_when_the_journal_clears_the_minimum(fixture_settings):
    write_journal(fixture_settings.journal_path, WINNING)
    assert allow_new_entries(fixture_settings.journal_path, window=60, min_obs=60, sharpe_min=1.5) == (True, "ok")


def test_zero_variance_journal_is_undefined_and_does_not_block(fixture_settings):
    write_journal(fixture_settings.journal_path, [0.0] * 60)
    assert allow_new_entries(fixture_settings.journal_path, window=60, min_obs=60, sharpe_min=1.5) == (
        True,
        "sharpe_undefined",
    )


def test_returns_use_start_of_day_equity(fixture_settings):
    write_journal(fixture_settings.journal_path, [1_000.0, -1_010.0])
    assert load_daily_returns(fixture_settings.journal_path, equity_base=100_000.0) == pytest.approx([0.01, -0.01])


# --- journal ingestion ---------------------------------------------------------------------


def test_ingest_ibkr_flex_csv_sums_per_day_and_is_idempotent(tmp_path):
    statement = tmp_path / "flex.csv"
    statement.write_text(
        "Symbol,TradeDate,FifoPnlRealized\n"
        "SPY 240603P00445000,20240603,\"1,250.50\"\n"
        "SPY 240603P00444000,20240603,-1000.50\n"
        "SPY 240604P00450000,20240604;093000,-40\n"
        "SPY 240604P00449000,20240604,\n",
        encoding="utf-8",
    )
    journal = tmp_path / "journal" / "daily_pnl.jsonl"
    assert ingest_broker_csv(statement, journal) == 2
    assert read_daily_pnl(journal) == {date(2024, 6, 3): 250.0, date(2024, 6, 4): -40.0}

    assert ingest_broker_csv(statement, journal) == 2
    assert read_daily_pnl(journal) == {date(2024, 6, 3): 250.0, date(2024, 6, 4): -40.0}
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 2


def test_broker_rows_replace_the_runners_own_rows_for_that_date(tmp_path):
    journal = tmp_path / "daily_pnl.jsonl"
    append_daily_pnl(journal, DailyPnlRow(day=date(2024, 6, 3), pnl=18.0, source="paper"))
    append_daily_pnl(journal, DailyPnlRow(day=date(2024, 6, 4), pnl=-7.0, source="paper"))
    statement = tmp_path / "statement.csv"
    statement.write_text("Date,PnL\n2024-06-03,12.5\n", encoding="utf-8")
    ingest_broker_csv(statement, journal)
    assert read_daily_pnl(journal) == {date(2024, 6, 3): 12.5, date(2024, 6, 4): -7.0}


def test_ingest_cli_with_explicit_columns(tmp_path, capsys):
    statement = tmp_path / "s.csv"
    statement.write_text("day,net\n2024-06-03,5\n", encoding="utf-8")
    journal = tmp_path / "j.jsonl"
    journal_main(["ingest", "--csv", str(statement), "--journal", str(journal), "--date-col", "day", "--pnl-col", "net"])
    assert "1 day(s) written" in capsys.readouterr().out
    assert json.loads(journal.read_text(encoding="utf-8")) == {"date": "2024-06-03", "pnl": 5.0, "source": "broker"}

    with pytest.raises(ValueError, match="no date column"):
        ingest_broker_csv(statement, journal)


def test_malformed_journal_lines_are_ignored(tmp_path):
    journal = tmp_path / "j.jsonl"
    journal.write_text('{"date": "2024-06-03", "pnl": 5}\nnot json\n{"pnl": 3}\n\n', encoding="utf-8")
    assert read_daily_pnl(journal) == {date(2024, 6, 3): 5.0}
    assert read_daily_pnl(tmp_path / "missing.jsonl") == {}


# --- live loop wiring ----------------------------------------------------------------------


class FakeIB:
    """Chain quotes as (short mid, long mid), plus a record of any order sent."""

    def __init__(self, short: float = 0.40, long: float = 0.15):
        self.marks = (short, long)
        self.orders: list[dict] = []

    def fetch_chain_candidates(self, _spot):
        short, long = self.marks
        return [
            OptionCandidate(occ_symbol("SPY", DAY, "P", 445.0), 445.0, "P", DAY, bid=short - 0.01, ask=short + 0.01, con_id=1),
            OptionCandidate(occ_symbol("SPY", DAY, "P", 444.0), 444.0, "P", DAY, bid=long - 0.01, ask=long + 0.01, con_id=2),
        ]

    def place_combo_order(self, order):
        self.orders.append(order)


class FakeAggregator:
    def __init__(self):
        self.now = at(10, 0)

    def build(self) -> MarketContext:
        return MarketContext(spot=450.0, as_of=self.now)


class FakeSkew:
    def set_candidates(self, _candidates):
        pass


class Harness:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.loop = live_runner.LoopState()
        self.ib = FakeIB()
        self.aggregator = FakeAggregator()
        self.limits = RiskLimits(
            max_contracts=settings.max_contracts,
            max_daily_loss=settings.max_daily_loss,
            entry_cutoff=settings.entry_cutoff,
            flatten_time=settings.flatten_time,
            min_minutes_after_open=settings.min_minutes_after_open,
        )

    def step(self, now: datetime) -> None:
        self.aggregator.now = now
        live_runner.step(
            self.settings,
            self.loop,
            ib=self.ib,
            aggregator=self.aggregator,
            chain_skew_provider=FakeSkew(),
            limits=self.limits,
            now=now,
        )


@pytest.fixture
def harness(fixture_settings) -> Harness:
    return Harness(fixture_settings)


def test_default_settings_open_a_tracked_position_and_send_no_order(harness):
    harness.step(at(10, 0))
    assert harness.loop.position is not None
    # Natural credit: short bid 0.39 - long ask 0.16.
    assert harness.loop.position.entry_credit == pytest.approx(0.23)
    assert harness.ib.orders == []
    # The credit is not PnL: nothing is realized at entry.
    assert harness.loop.session_pnl == 0.0 and harness.loop.risk.realized_pnl == 0.0


def test_losing_journal_does_not_block_while_the_gate_is_off(harness):
    write_journal(harness.settings.journal_path, LOSING)
    harness.step(at(10, 0))
    assert harness.loop.position is not None


def test_enabled_gate_blocks_new_entries_on_a_losing_journal(harness, caplog):
    write_journal(harness.settings.journal_path, LOSING)
    harness.settings.sharpe_gate_enabled = True
    with caplog.at_level("INFO"):
        harness.step(at(10, 0))
    assert harness.loop.position is None
    assert "Sharpe gate blocked entries: rolling_sharpe_" in caplog.text


def test_enabled_gate_allows_entries_on_a_winning_or_short_journal(harness):
    harness.settings.sharpe_gate_enabled = True
    harness.step(at(10, 0))  # empty journal: nothing measured yet
    assert harness.loop.position is not None

    other = Harness(harness.settings)
    write_journal(harness.settings.journal_path, WINNING)
    other.step(at(10, 0))
    assert other.loop.position is not None


def test_blocked_gate_still_manages_the_open_position(harness):
    harness.step(at(10, 0))
    write_journal(harness.settings.journal_path, LOSING)
    harness.settings.sharpe_gate_enabled = True
    harness.ib.marks = (0.10, 0.04)

    harness.step(at(12, 0))
    assert harness.loop.position is not None  # held, not force-closed by the gate

    harness.step(datetime.combine(DAY, harness.settings.flatten_time, tzinfo=ET))
    assert harness.loop.position is None
    # (0.23 credit - 0.06 mid mark) * 100 * 1 contract
    assert harness.loop.session_pnl == pytest.approx(17.0)


def test_session_pnl_is_journaled_once_at_the_next_session(harness):
    harness.step(at(10, 0))
    harness.ib.marks = (0.10, 0.04)
    harness.step(datetime.combine(DAY, harness.settings.flatten_time, tzinfo=ET))
    assert read_daily_pnl(harness.settings.journal_path) == {}

    next_day = DAY + timedelta(days=1)
    harness.step(at(9, 0, next_day))
    harness.step(at(9, 5, next_day))
    assert read_daily_pnl(harness.settings.journal_path) == {DAY: pytest.approx(17.0)}
    rows = Path(harness.settings.journal_path).read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["source"] == "paper"


def test_position_never_marked_closed_is_not_journaled_as_a_gain(harness, caplog):
    harness.step(at(10, 0))
    with caplog.at_level("ERROR"):
        harness.step(at(9, 0, DAY + timedelta(days=1)))
    assert read_daily_pnl(harness.settings.journal_path) == {}
    assert "its PnL is unknown" in caplog.text


def test_flatten_without_quotes_values_the_spread_at_intrinsic(harness):
    harness.step(at(10, 0))
    harness.ib.fetch_chain_candidates = lambda _spot: []
    harness.step(datetime.combine(DAY, harness.settings.flatten_time, tzinfo=ET))
    assert harness.loop.position is None
    # Spot 450 is above the 445 short put, so intrinsic is 0 and the 0.23 credit is kept.
    assert harness.loop.session_pnl == pytest.approx(23.0)


def test_live_chain_snapshot_is_saved_once_with_its_capture_time(harness):
    harness.step(at(10, 0))
    cached = sorted((Path(harness.settings.backtest_cache_dir) / "SPY").iterdir())
    assert [p.stem for p in cached] == [DAY.isoformat()]


def test_orders_are_sent_only_when_explicitly_allowed(fixture_settings):
    assert Settings().orders_allowed is False and Settings().live_trading is False
    fixture_settings.enable_orders = True
    fixture_settings.read_only = False
    armed = Harness(fixture_settings)
    armed.step(at(10, 0))
    (order,) = armed.ib.orders
    assert (order["short_con_id"], order["long_con_id"], order["contracts"]) == (1, 2, 1)
