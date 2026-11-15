from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from odte_spy_ibkr import hosted_state
from odte_spy_ibkr.config import Settings, load_settings
from odte_spy_ibkr.improvement.evaluator import LOOKBACK_DAYS
from odte_spy_ibkr.providers.base import BaseProvider, FlowBias, MarketContext, MarketContextDelta, VolRegime

SAFETY_YAML = Path(__file__).resolve().parents[1] / "odte-spy-agent" / "bot" / "lib" / "engine-safety.yaml"
RTH_NOW = datetime(2026, 11, 13, 16, 0, tzinfo=timezone.utc)  # Friday 11:00 ET


@pytest.fixture(autouse=True)
def _no_ambient_settings(monkeypatch):
    """Settings read the process environment; a developer's shell must not decide a test."""
    monkeypatch.delenv("ODTE_NO_DOTENV", raising=False)
    for field in Settings.model_fields.values():
        if field.alias:
            monkeypatch.delenv(field.alias, raising=False)


class _FakeProvider(BaseProvider):
    def __init__(self, provider_id: str, delta: MarketContextDelta | None, *, configured: bool = True) -> None:
        super().__init__(enabled=True)
        self.provider_id = provider_id
        self._delta = delta
        self._configured = configured

    def is_configured(self) -> bool:
        return self._configured

    def _fetch(self, ctx: MarketContext | None) -> MarketContextDelta:
        assert self._delta is not None
        return self._delta


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        JOURNAL_PATH=str(tmp_path / "journal.jsonl"),
        PARAMS_ACTIVE_PATH=str(tmp_path / "active.yaml"),
        **overrides,
    )


def _state(settings: Settings, providers: list[BaseProvider]) -> dict:
    with patch.object(hosted_state, "build_providers", return_value=providers) as build:
        state = hosted_state.build_state(settings, now=RTH_NOW)
    # The IB session argument is always None: no connection is attempted.
    assert build.call_args.args[1] is None
    return state


def test_without_providers_every_market_field_is_null_not_a_default(tmp_path):
    state = _state(
        _settings(tmp_path),
        [
            _FakeProvider("ibkr", None, configured=False),
            _FakeProvider("unusual_whales", None, configured=False),
            _FakeProvider("alpaca", None, configured=False),
        ],
    )
    signal = state["signal"]
    assert signal["spot"] is None
    assert signal["flow_bias"] is None
    assert signal["flow_strength"] is None
    assert signal["net_call_bias"] is None
    assert signal["macro_risk_off"] is None
    assert signal["macro_headline_score"] is None
    assert signal["macro_event_blackout"] is None
    assert signal["vol_regime"] is None
    assert state["entry_intent"] is None
    assert state["ib_session"]["available"] is False
    assert state["gates"]["flow"]["inputs_available"] is False
    assert state["gates"]["macro"]["inputs_available"] is False
    assert [p["contributed"] for p in state["providers"]] == [False, False, False]


def test_provider_data_flows_into_signal_and_gates(tmp_path):
    state = _state(
        _settings(tmp_path, UW_MODE="gate"),
        [
            _FakeProvider("ibkr", None, configured=False),
            _FakeProvider(
                "unusual_whales",
                MarketContextDelta(
                    provider_id="unusual_whales",
                    flow_bias=FlowBias.BULLISH,
                    flow_strength=0.8,
                    flow_weight=1.0,
                    as_of=RTH_NOW,
                ),
            ),
            _FakeProvider(
                "alpaca",
                MarketContextDelta(provider_id="alpaca", spot=601.25, spot_weight=0.5),
            ),
        ],
    )
    assert state["signal"]["spot"] == pytest.approx(601.25)
    assert state["signal"]["flow_bias"] == "bullish"
    assert state["signal"]["flow_strength"] == pytest.approx(0.8)
    assert state["gates"]["flow"] == {"decision": "allow", "reason": "aligned", "inputs_available": True}
    assert state["gates"]["risk"] == {"decision": "allow", "reason": "ok", "assumes_flat_book": True}
    # Chain-derived fields stay unavailable: the live chain needs an IB session.
    assert state["signal"]["net_call_bias"] is None
    assert state["entry_intent"] is None


def test_failed_provider_is_reported_and_does_not_count_as_data(tmp_path):
    class _Broken(_FakeProvider):
        def _fetch(self, ctx):
            raise RuntimeError("402 plan limit")

    state = _state(_settings(tmp_path), [_Broken("unusual_whales", None)])
    provider = state["providers"][0]
    assert provider["contributed"] is False
    assert "402 plan limit" in provider["note"]
    assert state["signal"]["flow_bias"] is None


def test_a_blackout_from_any_provider_is_reported_with_the_gate_it_closed(tmp_path):
    state = _state(
        _settings(tmp_path),
        [
            _FakeProvider(
                "tradingeconomics",
                MarketContextDelta(
                    provider_id="tradingeconomics",
                    macro_event_blackout=True,
                    macro_risk_off=False,
                    vol_regime=VolRegime.HIGH,
                ),
            ),
        ],
    )
    assert state["signal"]["macro_event_blackout"] is True
    assert state["signal"]["macro_risk_off"] is False
    assert state["signal"]["vol_regime"] == "high"
    # This provider sent no headline score, so the merged 0.0 is a default.
    assert state["signal"]["macro_headline_score"] is None
    assert state["gates"]["macro"]["decision"] == "block"
    assert state["gates"]["macro"]["inputs_available"] is True


def test_sharpe_gate_reads_the_journal(tmp_path):
    journal = tmp_path / "journal.jsonl"
    rows = [{"date": f"2026-10-{d:02d}", "pnl": -50.0 if d % 2 else 10.0} for d in range(1, 11)]
    journal.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    state = _state(
        _settings(tmp_path, SHARPE_GATE_ENABLED="true", SHARPE_MIN_OBS="5", SHARPE_WINDOW_DAYS="5"),
        [],
    )
    sharpe = state["gates"]["sharpe"]
    assert sharpe["enabled"] is True
    assert sharpe["observations"] == 10
    assert sharpe["rolling_sharpe"] < 1.5
    assert sharpe["allows_new_entries"] is False
    assert sharpe["reason"].startswith("rolling_sharpe_")


def test_a_disabled_sharpe_gate_does_not_block_entries(tmp_path):
    journal = tmp_path / "journal.jsonl"
    rows = [{"date": f"2026-10-{d:02d}", "pnl": -50.0 if d % 2 else 10.0} for d in range(1, 11)]
    journal.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    state = _state(
        _settings(tmp_path, SHARPE_GATE_ENABLED="false", SHARPE_MIN_OBS="5", SHARPE_WINDOW_DAYS="5"),
        [],
    )
    sharpe = state["gates"]["sharpe"]
    assert sharpe["enabled"] is False
    assert sharpe["rolling_sharpe"] < sharpe["min"]
    assert sharpe["allows_new_entries"] is True
    assert sharpe["reason"] == "gate_disabled"


def test_state_is_json_and_does_not_write_the_journal(tmp_path, capsys):
    settings = _settings(tmp_path)
    with (
        patch.object(hosted_state, "load_settings", return_value=settings),
        patch.object(hosted_state, "build_providers", return_value=[]),
    ):
        hosted_state.main([])
    out = json.loads(capsys.readouterr().out)
    assert out["safety"]["orders_allowed"] is False
    assert not Path(settings.journal_path).exists()


def test_agent_safety_yaml_overrides_an_environment_that_enables_orders(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name, value in {
        "ENABLE_ORDERS": "true",
        "READ_ONLY": "false",
        "LIVE_TRADING": "true",
        "PAPER": "false",
        "CONFIG_YAML": str(SAFETY_YAML),
    }.items():
        monkeypatch.setenv(name, value)
    settings = load_settings()
    assert settings.enable_orders is False
    assert settings.read_only is True
    assert settings.live_trading is False
    assert settings.paper is True
    assert settings.orders_allowed is False


def _history(tmp_path: Path, best_sharpe: float | None) -> Path:
    path = tmp_path / "20261115T090000Z.json"
    path.write_text(json.dumps({"best_candidate": "candidate_1", "best_sharpe": best_sharpe}), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("best_sharpe", "active_sharpe", "reason"),
    [
        (None, None, "below_sharpe_target"),
        (1.2, None, "below_sharpe_target"),
        (1.9, 1.85, "insufficient_improvement"),
        (2.4, 1.6, "paper_auto_promote_disabled"),
    ],
)
def test_promotion_check_matches_the_engine_decision_and_writes_nothing(tmp_path, best_sharpe, active_sharpe, reason):
    settings = _settings(tmp_path, IMPROVEMENT_AUTO_PROMOTE_PAPER="false", IMPROVEMENT_SHARPE_MARGIN="0.1")
    active_path = Path(settings.params_active_path)
    if active_sharpe is not None:
        active_path.write_text(f"metrics:\n  sharpe_rolling: {active_sharpe}\n", encoding="utf-8")
    before = active_path.read_text(encoding="utf-8") if active_path.exists() else None

    result = hosted_state.promotion_check(settings, _history(tmp_path, best_sharpe))

    assert result["reason"] == reason
    assert result["would_promote"] is False
    assert result["challenger_sharpe"] == best_sharpe
    assert result["sharpe_target"] == settings.backtest_sharpe_target
    after = active_path.read_text(encoding="utf-8") if active_path.exists() else None
    assert after == before


def test_promotion_check_cli_prints_one_json_object(tmp_path, capsys):
    settings = _settings(tmp_path, IMPROVEMENT_AUTO_PROMOTE_PAPER="false")
    with patch.object(hosted_state, "load_settings", return_value=settings):
        hosted_state.main(["--promotion-check", str(_history(tmp_path, 0.4))])
    assert json.loads(capsys.readouterr().out)["reason"] == "below_sharpe_target"


@pytest.mark.parametrize("record", [{"best_candidate": "candidate_1"}, {"best_sharpe": "1.9"}, {"best_sharpe": True}, []])
def test_promotion_check_rejects_a_broken_record_instead_of_calling_it_below_target(tmp_path, record):
    settings = _settings(tmp_path)
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="best_sharpe"):
        hosted_state.promotion_check(settings, path)


def test_promotion_check_cli_fails_on_a_broken_record(tmp_path, capsys):
    path = tmp_path / "broken.json"
    path.write_text("{}", encoding="utf-8")
    with patch.object(hosted_state, "load_settings", return_value=_settings(tmp_path)):
        with pytest.raises(ValueError):
            hosted_state.main(["--promotion-check", str(path)])
    assert capsys.readouterr().out == ""


def _write_chain(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows), encoding="utf-8")


def test_chain_coverage_separates_dated_chains_from_cached_and_missing_ones(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path, BACKTEST_CACHE_DIR="data/cache")
    row = {"symbol": "SPY", "strike": 600.0, "right": "P"}
    _write_chain(Path("data/research/chains/SPY/2026-11-02.json"), [row])
    # An empty research file is not a chain: the backtest falls through to its cache.
    _write_chain(Path("data/research/chains/SPY/2026-11-03.json"), [])
    _write_chain(Path("data/cache/SPY/2026-11-03.json"), [row])
    _write_chain(Path("data/cache/SPY/2026-11-04.parquet"), [])
    before = sorted(p.as_posix() for p in Path("data").rglob("*") if p.is_file())

    # Monday to the next Monday: six sessions, the weekend is skipped.
    coverage = hosted_state.chain_coverage(settings, date(2026, 11, 2), date(2026, 11, 9))

    assert coverage == {
        "from": "2026-11-02",
        "to": "2026-11-09",
        "sessions": 6,
        "research_chain_sessions": 1,
        "cached_chain_sessions": 2,
        "no_chain_sessions": 3,
        "no_chain_substitute": "synthetic",
    }
    assert sorted(p.as_posix() for p in Path("data").rglob("*") if p.is_file()) == before


def test_chain_coverage_names_the_alpaca_snapshot_when_the_engine_has_keys(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path, ALPACA_API_KEY="k", ALPACA_API_SECRET="s")
    coverage = hosted_state.chain_coverage(settings, date(2026, 11, 2), date(2026, 11, 2))
    assert coverage["no_chain_sessions"] == 1
    assert coverage["no_chain_substitute"] == "alpaca_current_snapshot"


def test_chain_coverage_cli_covers_the_window_an_improvement_run_scores(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    with patch.object(hosted_state, "load_settings", return_value=_settings(tmp_path)):
        hosted_state.main(["--improvement-chain-coverage"])
        improvement = json.loads(capsys.readouterr().out)
        hosted_state.main(["--chain-coverage", "2026-11-02", "2026-11-06"])
        explicit = json.loads(capsys.readouterr().out)
    today = date.today()
    assert improvement["to"] == today.isoformat()
    assert improvement["from"] == (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    assert improvement["sessions"] == improvement["no_chain_sessions"] > 0
    assert (explicit["from"], explicit["to"], explicit["sessions"]) == ("2026-11-02", "2026-11-06", 5)


def test_a_dotenv_file_is_ignored_when_the_host_builds_the_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text("ALPACA_API_KEY=from-dotenv\nPARAMS_ACTIVE_PATH=elsewhere.yaml\n", encoding="utf-8")
    assert load_settings().alpaca_api_key == "from-dotenv"

    monkeypatch.setenv("ODTE_NO_DOTENV", "1")
    settings = load_settings()
    assert settings.alpaca_api_key == ""
    assert settings.params_active_path == "params/active.yaml"
