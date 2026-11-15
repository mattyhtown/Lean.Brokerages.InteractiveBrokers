from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from odte_spy_ibkr.config import Settings, load_settings
from odte_spy_ibkr.research.alpaca_ingest import RESEARCH_ROOT, load_daily_bars
from odte_spy_ibkr.research.minute_io import best_minute_root, load_minute_bars, minute_coverage
from odte_spy_ibkr.research.chain_io import load_chain_rows, rows_to_candidates
from odte_spy_ibkr.research.features import FeatureRow, compute_features_for_day
from odte_spy_ibkr.research.labels import LabelRow, modeled_spread_pnl_from_marks, spy_return_to_close
from odte_spy_ibkr.research.paths import UW_ROOT
from odte_spy_ibkr.research.holdout import (
    FEATURE_HYPOTHESES_PER_RUN,
    RUN1_HYPOTHESES_TESTED,
    run2_holdout_days,
    run2_train_days,
)
from odte_spy_ibkr.research.stats import FeatureTestResult, bonferroni_adjust, evaluate_feature
from odte_spy_ibkr.strategy.credit_spread import choose_short_leg


@dataclass
class ResearchRow:
    feature: FeatureRow
    label: LabelRow


FEATURE_COLS = [
    "net_call_bias",
    "overnight_gap",
    "opening_range_pct",
    "intraday_mom_to_signal",
    "realized_vol_opening_hour",
    "macro_minutes_to_tier1",
    "macro_tier1_surprise",
]


@dataclass(frozen=True)
class ResearchReport:
    data_start: str | None
    data_end: str | None
    n_sessions: int
    hypotheses_tested: int
    train_n: int
    holdout_n: int
    features: list[FeatureTestResult]
    features_holdout: list[FeatureTestResult]
    bonferroni_alpha: float
    any_holdout_survivor: bool
    baseline_daily_spread_pnl_mean: float | None
    notes: list[str]


def _require_real_minute_coverage(days: list[date], cache_dir: Path | None = None) -> Path:
    n_ok, missing = minute_coverage(days)
    if len(missing) > len(days) * 0.25:
        raise RuntimeError(
            f"Insufficient real minute-bar cache: missing {len(missing)}/{len(days)} sessions "
            f"(alpaca/quantconnect/ibkr research trees). "
            "Run `python -m odte_spy_ibkr.research ingest` with Alpaca, QuantConnect LEAN, or IBKR."
        )
    return cache_dir or best_minute_root(days)


def _load_uw_flow_strength(day: date) -> float | None:
    p = UW_ROOT / "daily" / f"{day.isoformat()}.json"
    if not p.is_file():
        return None
    try:
        return float(json.loads(p.read_text(encoding="utf-8")).get("aggregate_bias"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _spread_pnl(
    settings: Settings,
    day: date,
    *,
    signal_rows: list[dict],
    close_rows: list[dict],
    spot: float,
) -> float | None:
    entry = rows_to_candidates(signal_rows, day)
    exit_c = rows_to_candidates(close_rows, day) if close_rows else entry
    short = choose_short_leg(settings, entry, right="P", spot=spot)
    if short is None:
        return None
    from odte_spy_ibkr.chain import build_vertical_spread

    spread = build_vertical_spread(short, entry, width=settings.spread_width)
    if spread is None or spread.short_leg.mid is None or spread.long_leg.mid is None:
        return None
    exit_short = next((c for c in exit_c if c.strike == short.strike and c.right == short.right), None)
    if exit_short is None:
        return None
    exit_long = next(
        (c for c in exit_c if c.strike == spread.long_leg.strike and c.right == spread.long_leg.right),
        None,
    )
    if exit_long is None or exit_short.mid is None or exit_long.mid is None:
        return None
    return modeled_spread_pnl_from_marks(
        entry_short_mid=spread.short_leg.mid,
        entry_long_mid=spread.long_leg.mid,
        exit_short_mid=exit_short.mid,
        exit_long_mid=exit_long.mid,
        commission=settings.backtest_commission_per_contract,
    )


def build_dataset(settings: Settings, *, days: list[date]) -> list[ResearchRow]:
    cache_dir = _require_real_minute_coverage(days)
    if not (cache_dir / "spy_daily.json").is_file():
        for fallback in (RESEARCH_ROOT, cache_dir):
            if (fallback / "spy_daily.json").is_file():
                daily_src = fallback
                break
        else:
            daily_src = cache_dir
    else:
        daily_src = cache_dir
    daily = {date.fromisoformat(b["t"][:10]): float(b["c"]) for b in load_daily_bars(daily_src)}
    rows: list[ResearchRow] = []
    prev_close: float | None = None
    ordered = sorted(days)
    for day in ordered:
        minute = load_minute_bars(day)
        if not minute:
            prev_close = daily.get(day, prev_close)
            continue
        signal_rows = load_chain_rows(day, snapshot="signal")
        close_rows = load_chain_rows(day, snapshot="close")
        chain = rows_to_candidates(signal_rows, day) if signal_rows else None
        feats = compute_features_for_day(
            settings,
            day=day,
            minute_bars=minute,
            prev_close=prev_close,
            chain=chain,
        )
        uw_strength = _load_uw_flow_strength(day)
        feats = FeatureRow(
            day=feats.day,
            net_call_bias=feats.net_call_bias,
            overnight_gap=feats.overnight_gap,
            opening_range_pct=feats.opening_range_pct,
            intraday_mom_to_signal=feats.intraday_mom_to_signal,
            realized_vol_opening_hour=feats.realized_vol_opening_hour,
            macro_minutes_to_tier1=feats.macro_minutes_to_tier1,
            macro_tier1_surprise=feats.macro_tier1_surprise,
            uw_flow_strength=uw_strength,
        )
        from odte_spy_ibkr.research.features import SIGNAL_TIME, _price_at

        spot = _price_at(minute, SIGNAL_TIME)
        spread_pnl = None
        if signal_rows and spot and spot > 0:
            spread_pnl = _spread_pnl(settings, day, signal_rows=signal_rows, close_rows=close_rows, spot=spot)
        lbl = LabelRow(
            day=day,
            spy_return_to_close=spy_return_to_close(minute),
            spread_pnl_usd=spread_pnl,
        )
        rows.append(ResearchRow(feats, lbl))
        prev_close = daily.get(day, prev_close)
    if not rows:
        raise RuntimeError("No research rows built from real caches.")
    return rows


def run_research(
    settings: Settings | None = None,
    *,
    holdout_days: int | None = None,
    run: int = 2,
) -> ResearchReport:
    settings = settings or load_settings()
    cache_dir = RESEARCH_ROOT
    daily = load_daily_bars(cache_dir)
    days = sorted(date.fromisoformat(b["t"][:10]) for b in daily)
    if run == 2:
        holdout = run2_holdout_days(days[0], days[-1])
        train = run2_train_days(days)
        notes = [
            f"Run-2 holdout fixed a priori: {holdout[0].isoformat() if holdout else '?'} "
            f"through {holdout[-1].isoformat() if holdout else '?'} "
            f"({len(holdout)} sessions); run-1 tail holdout not reused.",
        ]
        hypotheses_tested = RUN1_HYPOTHESES_TESTED + FEATURE_HYPOTHESES_PER_RUN
    else:
        tail = holdout_days if holdout_days is not None else 8
        if len(days) <= tail + 30:
            notes = [f"Short calendar: {len(days)} trading days in daily cache."]
        else:
            notes = []
        holdout = days[-tail:]
        train = days[: -tail]
        hypotheses_tested = len(FEATURE_COLS)
    uw_days = sum(1 for p in (UW_ROOT / "daily").glob("*.json")) if (UW_ROOT / "daily").is_dir() else 0
    if uw_days:
        notes.append(f"UW flow cache: {uw_days} daily files under data/research/uw/daily/")
    else:
        notes.append("UW flow: untested (no historical UW research cache; uw_flow_strength left null).")
    chain_days = sum(1 for _ in (Path("data/research/chains/SPY").glob("*.json")) if _.name != "_ingest_summary.json")
    notes.append(f"Unified 0DTE chain cache: {chain_days} session files under data/research/chains/SPY/.")

    full = build_dataset(settings, days=days)
    train_rows = [r for r in full if r.feature.day in train]
    hold_rows = [r for r in full if r.feature.day in holdout]

    results: list[FeatureTestResult] = []
    hold_results: list[FeatureTestResult] = []
    target_train = [r.label.spy_return_to_close for r in train_rows if r.label.spy_return_to_close is not None]
    for col in FEATURE_COLS:
        train_pairs = [
            (getattr(r.feature, col), r.label.spy_return_to_close)
            for r in train_rows
            if getattr(r.feature, col) is not None and r.label.spy_return_to_close is not None
        ]
        xs_t, ys_t = zip(*train_pairs, strict=False) if train_pairs else ([], [])
        results.append(evaluate_feature(col, list(xs_t), list(ys_t)))
        hold_pairs = [
            (getattr(r.feature, col), r.label.spy_return_to_close)
            for r in hold_rows
            if getattr(r.feature, col) is not None and r.label.spy_return_to_close is not None
        ]
        xs_h, ys_h = zip(*hold_pairs, strict=False) if hold_pairs else ([], [])
        hold_results.append(evaluate_feature(f"{col}_holdout", list(xs_h), list(ys_h)))

    pvals = [r.p_value_approx for r in results]
    bonferroni_m = hypotheses_tested if run == 2 else len(FEATURE_COLS)
    adj = bonferroni_adjust(pvals, family_size=bonferroni_m)
    survivors = [
        hold
        for train, hold, p_adj in zip(results, hold_results, adj, strict=False)
        if train.n >= 10
        and p_adj is not None
        and p_adj < 0.05
        and hold.ic is not None
        and abs(hold.ic) > 0.05
        and hold.n >= 5
    ]
    any_survivor = len(survivors) > 0
    if not any_survivor:
        notes.append("No feature survived Bonferroni-adjusted holdout IC screen at alpha=0.05.")

    baseline_mean = sum(target_train) / len(target_train) if target_train else None
    return ResearchReport(
        data_start=days[0].isoformat() if days else None,
        data_end=days[-1].isoformat() if days else None,
        n_sessions=len(full),
        hypotheses_tested=hypotheses_tested,
        train_n=len(train_rows),
        holdout_n=len(hold_rows),
        features=results,
        features_holdout=hold_results,
        bonferroni_alpha=0.05,
        any_holdout_survivor=any_survivor,
        baseline_daily_spread_pnl_mean=baseline_mean,
        notes=notes,
    )


def report_markdown(rep: ResearchReport) -> str:
    lines = [
        "# ODTE SPY — alpha research",
        "",
        f"**Data range:** {rep.data_start} → {rep.data_end} ({rep.n_sessions} sessions with minute bars)",
        f"**Train / holdout:** {rep.train_n} / {rep.holdout_n} (holdout untouched until final tests)",
        f"**Hypotheses tested (cumulative):** {rep.hypotheses_tested} (Bonferroni α={rep.bonferroni_alpha})",
        "",
        "## Notes",
    ]
    for n in rep.notes:
        lines.append(f"- {n}")
    lines.extend(["", "## Train-window feature tests (IC vs SPY 11:00→close return)", ""])
    lines.append("| Feature | n | IC | Hit rate | t-stat | p (approx) |")
    lines.append("|---------|---:|---:|---:|---:|---:|")
    for r in rep.features:
        ic_s = f"{r.ic:.3f}" if r.ic is not None else "—"
        hr_s = f"{r.hit_rate:.3f}" if r.hit_rate is not None else "—"
        t_s = f"{r.t_stat:.2f}" if r.t_stat is not None else "—"
        p_s = f"{r.p_value_approx:.3f}" if r.p_value_approx is not None else "—"
        lines.append(f"| {r.feature} | {r.n} | {ic_s} | {hr_s} | {t_s} | {p_s} |")
    lines.extend(["", "## Holdout (final)", ""])
    for r in rep.features_holdout:
        lines.append(
            f"- **{r.feature}**: n={r.n}, IC={r.ic}, hit_rate={r.hit_rate}, t={r.t_stat}"
        )
    lines.extend(
        [
            "",
            f"**Any holdout survivor:** {rep.any_holdout_survivor}",
            f"**Baseline** (always in, mean SPY return): {rep.baseline_daily_spread_pnl_mean}",
        ]
    )
    return "\n".join(lines)