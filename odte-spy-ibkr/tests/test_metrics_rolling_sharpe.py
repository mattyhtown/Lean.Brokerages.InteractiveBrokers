"""Rolling Sharpe on synthetic return series, and the pass/fail contract of the backtest CLI."""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pytest

from odte_spy_ibkr.backtest import runner as backtest_runner
from odte_spy_ibkr.backtest.calendar import iter_odte_sessions, session_close, session_open
from odte_spy_ibkr.backtest.ledger import TradeRecord
from odte_spy_ibkr.backtest.metrics import (
    full_sample_sharpe,
    max_drawdown,
    max_rolling_sharpe,
    rolling_sharpe_series,
    rolling_stats_series,
    summarize_backtest,
)
from odte_spy_ibkr.backtest.session_data import NO_CHAIN, SOURCE_OPTION_BARS, SOURCE_SYNTHETIC, SessionLoad
from odte_spy_ibkr.backtest.simulator import DaySimulation
from odte_spy_ibkr.improvement import evaluator
from odte_spy_ibkr.improvement.experiment import ExperimentCandidate

from conftest import build_session, constant


def test_rolling_sharpe_matches_hand_computation():
    # Window of 4: mean 0.001, population std sqrt(((.001)^2 * 2 + (.001)^2 * 2) / 4) = 0.001.
    returns = [0.002, 0.0, 0.002, 0.0]
    (stat,) = [s for s in rolling_stats_series(returns, window=4) if s is not None]
    assert stat.mean == pytest.approx(0.001)
    assert stat.std == pytest.approx(0.001)
    assert stat.sharpe == pytest.approx(math.sqrt(252))


def test_series_is_undefined_until_the_window_fills():
    returns = [0.001, -0.002, 0.003, 0.001, 0.002]
    series = rolling_sharpe_series(returns, window=3)
    assert series[:2] == [None, None]
    assert all(v is not None for v in series[2:])
    # Each value uses exactly the trailing window.
    chunk = returns[2:5]
    mean = sum(chunk) / 3
    std = math.sqrt(sum((x - mean) ** 2 for x in chunk) / 3)
    assert series[4] == pytest.approx(math.sqrt(252) * mean / std)


def test_zero_variance_window_is_undefined_not_infinite():
    assert rolling_sharpe_series([0.001] * 10, window=5)[-1] is None
    assert rolling_sharpe_series([0.0] * 10, window=5)[-1] is None
    assert max_rolling_sharpe([0.0] * 10, window=5) is None
    assert full_sample_sharpe([0.001] * 10) is None


def test_fewer_observations_than_window_gives_no_sharpe():
    assert max_rolling_sharpe([0.01, -0.01] * 20, window=60) is None


def test_sharpe_sign_follows_mean_return():
    gains = [0.001 + (i % 5) * 0.00005 for i in range(80)]
    losses = [-r for r in gains]
    assert rolling_sharpe_series(gains, window=60)[-1] > 0
    assert rolling_sharpe_series(losses, window=60)[-1] == pytest.approx(-rolling_sharpe_series(gains, window=60)[-1])


def test_max_rolling_sharpe_picks_the_best_window():
    returns = [0.002, 0.001] * 30 + [-0.002, 0.001] * 30
    series = [v for v in rolling_sharpe_series(returns, window=60) if v is not None]
    assert max_rolling_sharpe(returns, window=60) == pytest.approx(max(series))
    assert series[0] == max(series)  # the first full window is the all-gains stretch


def test_summary_pass_fail_against_target():
    alternating = [0.002, 0.0] * 40  # per-window Sharpe sqrt(252) ~ 15.9
    summary = summarize_backtest(alternating, [r * 100_000 for r in alternating], [True], window=60, sharpe_target=1.5)
    assert summary.passes_sharpe_target(1.5)
    assert summary.pct_days_sharpe_ge_target == 1.0

    weak = [0.0001, -0.0001] * 40
    summary = summarize_backtest(weak, [r * 100_000 for r in weak], [True, False], window=60, sharpe_target=1.5)
    assert summary.max_rolling_sharpe == pytest.approx(0.0, abs=1e-9)
    assert not summary.passes_sharpe_target(1.5)
    assert summary.win_rate == 0.5

    empty = summarize_backtest([], [], [], window=60, sharpe_target=1.5)
    assert empty.max_rolling_sharpe is None and not empty.passes_sharpe_target(1.5)


def test_max_drawdown_from_equity_curve():
    assert max_drawdown([100.0, 110.0, 99.0, 120.0]) == pytest.approx(0.1)
    summary = summarize_backtest([], [1_000.0, -2_200.0], [], window=60, sharpe_target=1.5, equity_base=10_000.0)
    assert summary.max_drawdown == pytest.approx(0.2)


# --- CLI contract -------------------------------------------------------------------------


def _trade(day, pnl):
    return TradeRecord(
        day=day,
        side="put_credit",
        short_strike=445.0,
        long_strike=444.0,
        contracts=1,
        opened_at=session_open(day),
        closed_at=session_close(day),
        entry_credit=0.0,
        exit_debit=0.0,
        commission=0.0,
        pnl=pnl,
        exit_reason="scripted",
    )


def _patch_sessions(monkeypatch, settings, pnl_by_index, *, real=lambda day: True):
    """Replace data loading and simulation with a scripted PnL series (no market data needed).

    A day with PnL 0 has no trade. Days where `real(day)` is false have no chain of their
    own; they get a generated session only when the run asks for synthetic data.
    """
    counter = iter(range(10_000))

    def fake_load_session(_settings, day, *, allow_synthetic=False, **_kwargs):
        if real(day):
            return SessionLoad(
                build_session(settings, day=day, spot=constant(450.0), legs={}), None, has_own_chain=True
            )
        generated = (
            build_session(settings, day=day, spot=constant(450.0), legs={}, source=SOURCE_SYNTHETIC)
            if allow_synthetic
            else None
        )
        return SessionLoad(generated, NO_CHAIN)

    def fake_simulate(_settings, session):
        pnl = pnl_by_index(next(counter))
        trades = (_trade(session.day, pnl),) if pnl else ()
        return DaySimulation(
            pnl=pnl,
            open_position=None,
            traded=bool(pnl),
            session_reason="scripted",
            trades=trades,
            source=session.source,
        )

    monkeypatch.setattr(backtest_runner, "load_session", fake_load_session)
    monkeypatch.setattr(backtest_runner, "simulate_session", fake_simulate)
    monkeypatch.setattr(backtest_runner, "load_settings", lambda _path=None: settings)


CLI_RANGE = ["--from", "2024-03-01", "--to", "2024-08-30"]


def test_cli_exits_1_when_required_rolling_sharpe_is_not_met(monkeypatch, fixture_settings, tmp_path):
    _patch_sessions(monkeypatch, fixture_settings, lambda i: 100.0 if i % 2 else -100.0)
    report = tmp_path / "reports" / "backtest.json"
    with pytest.raises(SystemExit) as exit_info:
        backtest_runner.main([*CLI_RANGE, "--report-json", str(report), "--require-rolling-sharpe", "1.5"])
    assert exit_info.value.code == 1

    payload = json.loads(report.read_text(encoding="utf-8"))
    gate = payload["gate"]
    assert gate["required_rolling_sharpe"] == 1.5
    assert gate["passed"] is False
    assert gate["max_rolling_sharpe"] == payload["max_rolling_sharpe"] < 1.5
    assert payload["rolling_window_days"] == 60
    assert payload["score_days"] == len(payload["rolling"]) + 59
    assert set(payload["rolling"][0]) == {"date", "mean", "std", "sharpe"}


def test_cli_exits_0_when_required_rolling_sharpe_is_met(monkeypatch, fixture_settings, tmp_path):
    _patch_sessions(monkeypatch, fixture_settings, lambda i: 200.0 if i % 2 else 0.0)
    report = tmp_path / "backtest.json"
    with pytest.raises(SystemExit) as exit_info:
        backtest_runner.main([*CLI_RANGE, "--report-json", str(report), "--require-rolling-sharpe", "1.5"])
    assert exit_info.value.code == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["gate"]["passed"] is True
    assert payload["chain_coverage"]["own_chain_by_source"] == {SOURCE_OPTION_BARS: payload["score_days"]}


def test_gate_result_states_trades_and_traded_days(monkeypatch, fixture_settings, tmp_path, caplog):
    """The pass/fail line carries the sample it rests on; the gate has no minimum-trades rule."""
    _patch_sessions(monkeypatch, fixture_settings, lambda i: 200.0 if i % 2 else 0.0)
    report = tmp_path / "backtest.json"
    with caplog.at_level("INFO"):
        code = backtest_runner.run_backtest(
            fixture_settings,
            start=date(2024, 3, 1),
            end=date(2024, 8, 30),
            report_path=str(report),
            require_sharpe=1.5,
        )
    payload = json.loads(report.read_text(encoding="utf-8"))
    gate = payload["gate"]
    scored = payload["score_days"]
    assert code == 0
    assert gate["trade_count"] == gate["traded_days"] == scored // 2
    assert gate["scored_days"] == scored
    assert (payload["trade_count"], payload["traded_days"]) == (gate["trade_count"], gate["traded_days"])
    (line,) = [r.getMessage() for r in caplog.records if "Rolling Sharpe gate" in r.getMessage()]
    assert f"on {gate['trade_count']} trade(s) over {gate['traded_days']} traded day(s) of {scored} scored day(s)" in line


def test_cli_without_requirement_reports_and_exits_0(monkeypatch, fixture_settings, tmp_path):
    _patch_sessions(monkeypatch, fixture_settings, lambda i: -100.0)
    report = tmp_path / "backtest.json"
    with pytest.raises(SystemExit) as exit_info:
        backtest_runner.main([*CLI_RANGE, "--report-json", str(report)])
    assert exit_info.value.code == 0
    gate = json.loads(report.read_text(encoding="utf-8"))["gate"]
    assert gate["passed"] is None and gate["required_rolling_sharpe"] is None


def test_require_sharpe_pass_setting_applies_the_configured_target(monkeypatch, fixture_settings):
    _patch_sessions(monkeypatch, fixture_settings, lambda i: 100.0 if i % 2 else -100.0)
    fixture_settings.backtest_require_sharpe_pass = True
    code = backtest_runner.run_backtest(fixture_settings, start=date(2024, 3, 1), end=date(2024, 8, 30))
    assert code == 1


def test_no_data_fails_the_requirement_and_reports_no_sharpe(fixture_settings, tmp_path):
    """Empty cache and no API keys: nothing is scored and nothing is invented."""
    report = tmp_path / "backtest.json"
    code = backtest_runner.run_backtest(
        fixture_settings,
        start=date(2024, 6, 3),
        end=date(2024, 6, 7),
        report_path=str(report),
        require_sharpe=1.5,
    )
    assert code == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["score_days"] == 0
    assert payload["sessions_excluded"] == {NO_CHAIN: 5}
    assert payload["chain_coverage"]["own_chain"] == 0 and payload["chain_coverage"]["no_chain"] == 5
    assert payload["max_rolling_sharpe"] is None and payload["full_sharpe"] is None
    assert payload["gate"]["passed"] is False and payload["gate"]["trade_count"] == 0
    assert "synthetic" not in payload


def test_synthetic_sessions_are_labelled_and_never_count_toward_the_gate(monkeypatch, fixture_settings, tmp_path):
    """Every session lacks a chain. With the opt-in they are simulated on generated prices
    that would clear the target easily, and the requirement still fails on zero real trades."""
    _patch_sessions(monkeypatch, fixture_settings, lambda i: 200.0 if i % 2 else 0.0, real=lambda day: False)
    report = tmp_path / "backtest.json"
    code = backtest_runner.run_backtest(
        fixture_settings,
        start=date(2024, 3, 1),
        end=date(2024, 8, 30),
        report_path=str(report),
        require_sharpe=1.5,
        allow_synthetic=True,
    )
    assert code == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    sessions = payload["total_trading_days"]
    assert payload["synthetic"]["sessions"] == payload["synthetic"]["sessions_in_scored_window"] == sessions
    assert payload["synthetic"]["trade_count"] == sessions // 2
    assert payload["synthetic"]["note"].startswith("SYNTHETIC")
    assert payload["chain_coverage"]["no_chain"] == sessions and payload["chain_coverage"]["own_chain"] == 0
    assert payload["score_days"] == 0 and payload["trade_count"] == 0 and payload["total_pnl"] == 0.0
    assert payload["max_rolling_sharpe"] is None and payload["rolling"] == [] and payload["trades"] == []
    assert payload["gate"] == {
        "required_rolling_sharpe": 1.5,
        "max_rolling_sharpe": None,
        "passed": False,
        "trade_count": 0,
        "traded_days": 0,
        "scored_days": 0,
        "scored_from": "2024-03-01",
        "scored_to": "2024-08-30",
    }


def test_synthetic_fill_does_not_change_the_real_result(monkeypatch, fixture_settings):
    """Real data through May, none afterwards: the scored series is the same with and without the opt-in."""
    cutoff = date(2024, 6, 1)

    def run(allow_synthetic):
        _patch_sessions(monkeypatch, fixture_settings, lambda i: 200.0 if i % 2 else 0.0, real=lambda d: d < cutoff)
        return backtest_runner.execute_backtest(
            fixture_settings, start=date(2024, 3, 1), end=date(2024, 8, 30), allow_synthetic=allow_synthetic
        ).payload

    without, with_fill = run(False), run(True)
    synthetic = with_fill.pop("synthetic")
    assert with_fill == without
    assert synthetic["sessions"] == without["chain_coverage"]["no_chain"] > 0
    assert without["score_days"] == without["chain_coverage"]["own_chain"]
    assert without["trades"] and all(row["day"] < cutoff.isoformat() for row in without["trades"])


def test_synthetic_data_is_off_by_default():
    args = backtest_runner.build_parser().parse_args([])
    assert args.allow_synthetic is False


def test_walk_forward_scores_only_the_holdout_slice(monkeypatch, fixture_settings):
    _patch_sessions(monkeypatch, fixture_settings, lambda i: 50.0)
    fixture_settings.backtest_walk_forward_train_days = 60
    fixture_settings.backtest_walk_forward_test_days = 20
    result = backtest_runner.execute_backtest(
        fixture_settings, start=date(2024, 3, 1), end=date(2024, 8, 30), walk_forward=True
    )
    assert result.payload["score_days"] == 20
    assert result.payload["total_pnl"] == pytest.approx(20 * 50.0)
    assert result.payload["total_pnl_all_days"] == pytest.approx(result.payload["simulated"]["sessions_scored"] * 50.0)


# --- walk-forward: everything reported describes the scored (holdout) window ---------------

# 86 sessions, as in a 120-day improvement lookback: one 60/20 fold, six sessions left over.
WF_RANGE = {"start": date(2024, 3, 1), "end": date(2024, 7, 3)}
WF_SESSIONS = iter_odte_sessions(WF_RANGE["start"], WF_RANGE["end"])
HOLDOUT = WF_SESSIONS[60:80]


def _walk_forward(monkeypatch, settings, *, real=lambda day: True, **kwargs):
    _patch_sessions(monkeypatch, settings, lambda i: 200.0 if i % 2 else 0.0, real=real)
    settings.backtest_walk_forward_train_days = 60
    settings.backtest_walk_forward_test_days = 20
    return backtest_runner.execute_backtest(settings, walk_forward=True, **WF_RANGE, **kwargs)


def test_walk_forward_report_states_the_scored_window_and_total_simulated(monkeypatch, fixture_settings):
    assert len(WF_SESSIONS) == 86
    payload = _walk_forward(monkeypatch, fixture_settings).payload

    assert payload["scored_window"] == {
        "scope": "walk_forward_holdout",
        "from": HOLDOUT[0].isoformat(),
        "to": HOLDOUT[-1].isoformat(),
        "sessions": 20,
        "simulated_sessions_before": 60,
        "simulated_sessions_after": 6,  # the newest sessions fall outside the only complete fold
    }
    assert payload["simulated"]["sessions"] == payload["total_trading_days"] == 86
    assert (payload["simulated"]["from"], payload["simulated"]["to"]) == ("2024-03-01", "2024-07-03")
    assert payload["simulated"]["sessions_scored"] == 86 and payload["score_days"] == 20
    assert {row["day"] for row in payload["trades"]} <= {d.isoformat() for d in HOLDOUT}


def test_walk_forward_chain_coverage_counts_only_the_scored_sessions(monkeypatch, fixture_settings):
    """Five train sessions and three holdout sessions have no chain; three more follow the holdout."""
    missing = {*WF_SESSIONS[10:15], *HOLDOUT[4:7], *WF_SESSIONS[81:84]}
    result = _walk_forward(monkeypatch, fixture_settings, real=lambda day: day not in missing)
    payload = result.payload

    coverage = payload["chain_coverage"]
    assert coverage["scope"] == "walk_forward_holdout"
    assert (coverage["sessions"], coverage["own_chain"], coverage["no_chain"]) == (20, 17, 3)
    assert coverage["no_chain_dates"] == [d.isoformat() for d in HOLDOUT[4:7]]
    assert coverage["own_chain_by_source"] == {SOURCE_OPTION_BARS: 17}
    assert payload["sessions_excluded"] == {NO_CHAIN: 3}
    assert payload["score_days"] == 17
    # The full range is reported apart.
    assert (payload["simulated"]["own_chain"], payload["simulated"]["no_chain"]) == (75, 11)

    gate = result.gate(1.5)
    assert (gate["scored_days"], gate["scored_from"], gate["scored_to"]) == (
        17,
        HOLDOUT[0].isoformat(),
        HOLDOUT[-1].isoformat(),
    )
    assert gate["trade_count"] == payload["trade_count"] == gate["traded_days"] <= 17


def test_full_range_run_reports_full_range_scope(monkeypatch, fixture_settings):
    _patch_sessions(monkeypatch, fixture_settings, lambda i: 50.0)
    payload = backtest_runner.execute_backtest(fixture_settings, **WF_RANGE).payload
    assert payload["chain_coverage"]["scope"] == payload["scored_window"]["scope"] == "full_range"
    assert payload["scored_window"]["sessions"] == payload["chain_coverage"]["sessions"] == 86
    assert payload["scored_window"]["simulated_sessions_after"] == 0


def test_walk_forward_holdout_without_chains_fails_the_gate_even_with_synthetic_fill(
    monkeypatch, fixture_settings, tmp_path
):
    """Real chains in the train slice only. The holdout is filled with generated prices that
    alternate +200/0, which would clear 1.5 if they were scored."""
    holdout = set(HOLDOUT)
    _patch_sessions(monkeypatch, fixture_settings, lambda i: 200.0 if i % 2 else 0.0, real=lambda d: d not in holdout)
    fixture_settings.backtest_walk_forward_train_days = 60
    fixture_settings.backtest_walk_forward_test_days = 20
    report = tmp_path / "wf.json"
    code = backtest_runner.run_backtest(
        fixture_settings,
        report_path=str(report),
        require_sharpe=1.5,
        walk_forward=True,
        allow_synthetic=True,
        **WF_RANGE,
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert code == 1
    assert payload["synthetic"]["sessions_in_scored_window"] == 20 and payload["synthetic"]["trade_count"] == 10
    assert (payload["chain_coverage"]["own_chain"], payload["chain_coverage"]["no_chain"]) == (0, 20)
    assert payload["gate"]["passed"] is False
    assert (payload["gate"]["trade_count"], payload["gate"]["traded_days"], payload["gate"]["scored_days"]) == (0, 0, 0)
    assert payload["trades"] == [] and payload["total_pnl"] == 0.0


def test_improvement_evaluator_reports_the_sample_behind_its_score(monkeypatch, fixture_settings, caplog):
    end = WF_RANGE["end"]
    sessions = iter_odte_sessions(end - timedelta(days=evaluator.LOOKBACK_DAYS), end)
    holdout = sessions[60:80]
    missing = set(holdout[:2])
    _patch_sessions(monkeypatch, fixture_settings, lambda i: 200.0 if i % 2 else 0.0, real=lambda d: d not in missing)
    fixture_settings.backtest_walk_forward_train_days = 60
    fixture_settings.backtest_walk_forward_test_days = 20
    monkeypatch.setattr(evaluator, "load_settings", lambda: fixture_settings)
    requested: list[bool] = []
    real_execute = evaluator.execute_backtest

    def spy(settings, **kwargs):
        requested.append(kwargs.get("allow_synthetic", False))
        return real_execute(settings, **kwargs)

    monkeypatch.setattr(evaluator, "execute_backtest", spy)
    with caplog.at_level("INFO"):
        result = evaluator.evaluate_candidate(ExperimentCandidate(name="c0", params={}), end=end)

    assert requested == [False]  # the evaluator never asks for generated chains
    sample = result.sample
    assert sample["scored_window"] == {
        "scope": "walk_forward_holdout",
        "from": holdout[0].isoformat(),
        "to": holdout[-1].isoformat(),
        "sessions": 20,
        "simulated_sessions_before": 60,
        "simulated_sessions_after": len(sessions) - 80,
    }
    assert len(sessions) - 80 > 0  # the newest sessions of the lookback are not scored
    assert sample["simulated"]["sessions"] == len(sessions)
    coverage = sample["chain_coverage"]
    assert (coverage["scope"], coverage["sessions"], coverage["own_chain"], coverage["no_chain"]) == (
        "walk_forward_holdout",
        20,
        18,
        2,
    )
    assert sample["score_days"] == 18
    assert sample["trade_count"] == sample["traded_days"] == 9
    # 18 scored days cannot fill the 60-day rolling window, so there is no Sharpe to promote on.
    assert sample["rolling_window_days"] == 60
    assert result.max_rolling_sharpe is None and result.score == float("-inf")
    assert f"scored on {sample['scored_window']['from']} to {sample['scored_window']['to']}" in caplog.text


def test_evaluator_scores_minus_infinity_without_real_holdout_data(monkeypatch, fixture_settings):
    _patch_sessions(monkeypatch, fixture_settings, lambda i: 200.0, real=lambda d: False)
    monkeypatch.setattr(evaluator, "load_settings", lambda: fixture_settings)
    result = evaluator.evaluate_candidate(ExperimentCandidate(name="c0", params={}), end=WF_RANGE["end"])
    assert result.max_rolling_sharpe is None and result.score == float("-inf")
    assert result.sample["chain_coverage"]["own_chain"] == 0 and result.sample["trade_count"] == 0
