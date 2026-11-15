from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from odte_spy_ibkr.config import Settings, load_settings
from odte_spy_ibkr.research.alpaca_ingest import RESEARCH_ROOT, load_daily_bars, load_minute_bars
from odte_spy_ibkr.research.features import FeatureRow, compute_features_for_day
from odte_spy_ibkr.research.labels import LabelRow, spy_return_to_close
from odte_spy_ibkr.research.stats import FeatureTestResult, bonferroni_adjust, evaluate_feature


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


def _require_real_minute_coverage(days: list[date], cache_dir: Path) -> None:
    missing = [d for d in days if not (cache_dir / "minute" / f"{d.isoformat()}.json").is_file()]
    if len(missing) > len(days) * 0.25:
        raise RuntimeError(
            f"Insufficient real minute-bar cache: missing {len(missing)}/{len(days)} sessions under {cache_dir / 'minute'}. "
            "Run `python -m odte_spy_ibkr.research ingest` with Alpaca keys or bootstrap from MCP exports."
        )


def build_dataset(settings: Settings, *, days: list[date]) -> list[ResearchRow]:
    cache_dir = Path(settings.backtest_cache_dir).parent / "research" / "alpaca"
    if not (cache_dir / "spy_daily.json").is_file():
        cache_dir = RESEARCH_ROOT
    _require_real_minute_coverage(days, cache_dir)
    daily = {date.fromisoformat(b["t"][:10]): float(b["c"]) for b in load_daily_bars(cache_dir)}
    rows: list[ResearchRow] = []
    prev_close: float | None = None
    ordered = sorted(days)
    for day in ordered:
        minute = load_minute_bars(day, cache_dir)
        if not minute:
            prev_close = daily.get(day, prev_close)
            continue
        feats = compute_features_for_day(
            settings,
            day=day,
            minute_bars=minute,
            prev_close=prev_close,
            chain=None,
        )
        lbl = LabelRow(
            day=day,
            spy_return_to_close=spy_return_to_close(minute),
            spread_pnl_usd=None,
        )
        rows.append(ResearchRow(feats, lbl))
        prev_close = daily.get(day, prev_close)
    if not rows:
        raise RuntimeError("No research rows built from real caches.")
    return rows


def run_research(
    settings: Settings | None = None,
    *,
    holdout_days: int = 20,
) -> ResearchReport:
    settings = settings or load_settings()
    cache_dir = RESEARCH_ROOT
    daily = load_daily_bars(cache_dir)
    days = sorted(date.fromisoformat(b["t"][:10]) for b in daily)
    if len(days) <= holdout_days + 30:
        notes = [f"Short calendar: {len(days)} trading days in daily cache."]
    else:
        notes = []
    notes.append("UW flow: untested (no historical UW cache; feature left null by design).")
    notes.append("net_call_bias: null unless per-day real option chain parquet present in cache.")

    holdout = days[-holdout_days:]
    train = days[: -holdout_days]
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
    adj = bonferroni_adjust(pvals)
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
        hypotheses_tested=len(FEATURE_COLS),
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
        f"**Hypotheses tested:** {rep.hypotheses_tested} (Bonferroni α={rep.bonferroni_alpha})",
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