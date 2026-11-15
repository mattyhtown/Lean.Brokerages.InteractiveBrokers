from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.improvement.promoter import PromotionDecision
from odte_spy_ibkr.providers.registry import build_providers
from odte_spy_ibkr.research.ingest import resolve_ingest_sources


def test_resolve_ingest_sources_default_alpaca_only():
    assert resolve_ingest_sources(None) == ["alpaca"]


def test_resolve_ingest_sources_explicit_single():
    assert resolve_ingest_sources(["tradingeconomics"]) == ["tradingeconomics"]


def test_research_ingest_argparse_explicit_source_not_merged():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", default=None, choices=["alpaca", "tradingeconomics"])
    args = parser.parse_args(["--source", "tradingeconomics"])
    assert resolve_ingest_sources(args.source) == ["tradingeconomics"]


def test_default_providers_include_macro_inputs():
    settings = Settings()
    enabled = settings.providers_enabled_set
    assert "openbb" in enabled
    assert "alphavantage" in enabled
    assert "macro_news" in enabled
    assert "tradingeconomics" in enabled
    providers = build_providers(settings, ib_session=None)
    macro = next(p for p in providers if p.provider_id == "macro_news")
    te = next(p for p in providers if p.provider_id == "tradingeconomics")
    assert macro.enabled
    assert te.enabled


def test_improvement_run_prints_decision():
    from odte_spy_ibkr.improvement import __main__ as improvement_main

    decision = PromotionDecision(False, "insufficient_improvement")
    fake_result = type(
        "R",
        (),
        {
            "score": 0.1,
            "max_rolling_sharpe": 0.5,
            "max_drawdown": 0.1,
            "candidate": type("C", (), {"name": "c0", "params": {}})(),
        },
    )()

    buf = io.StringIO()
    with (
        patch.object(improvement_main, "load_settings", return_value=Settings()),
        patch.object(improvement_main, "load_active_params", return_value={"metrics": {}}),
        patch.object(improvement_main, "grid_candidates", return_value=[fake_result.candidate]),
        patch.object(improvement_main, "evaluate_candidate", return_value=fake_result),
        patch.object(improvement_main, "should_promote", return_value=decision),
        patch.object(improvement_main, "write_history"),
        redirect_stdout(buf),
    ):
        improvement_main.run_improvement()
    out = buf.getvalue()
    assert "No promotion" in out
    assert decision.reason in out