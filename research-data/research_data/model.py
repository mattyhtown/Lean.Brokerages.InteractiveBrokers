"""Retrains the return model, tests it on days it was not trained on, and keeps a record of what it predicts.

For each horizon the model is first scored on history walked forward in time: it is trained on the rows whose
return was already known, predicts the next block of sessions, and is trained again. Then it is trained on every
row with a known return and predicts the newest session, whose return nobody knows yet. Those predictions are
stored and never changed, so later runs can score them against what happened.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from math import sqrt
from statistics import fmean, stdev
from typing import Callable, Iterable, Sequence
import uuid

from research_data.http import LoaderError
from research_data.supabase import SupabaseClient

HORIZONS = (1, 5)
# Sessions of rows with known returns the model is trained on before its first test.
MIN_TRAIN_SESSIONS = 60
# The model is trained again after predicting this many sessions.
TEST_BLOCK_SESSIONS = 20
# A day needs this many symbols before its symbols are ranked against each other.
MIN_RANKED_SYMBOLS = 5

_NOT_FEATURES = frozenset({"symbol", "signal_date", "close", "returns_adjusted"})
_LABEL_PREFIX = "forward_return"

Features = Sequence[float | None]
Predict = Callable[[Sequence[Features]], Sequence[float]]
# Trains on rows of features and their returns, and gives back what predicts returns for other rows.
Fit = Callable[[Sequence[Features], Sequence[float]], Predict]


@dataclass(frozen=True)
class Row:
    symbol: str
    signal_date: str
    # Position of signal_date among the dates that have a close for any symbol.
    session: int
    features: tuple[float | None, ...]
    # Horizon in sessions to the return over it, None until the later close is loaded.
    returns: dict[int, float | None]
    returns_adjusted: bool


@dataclass(frozen=True)
class Dataset:
    features: tuple[str, ...]
    sessions: tuple[str, ...]
    rows: tuple[Row, ...]


@dataclass(frozen=True)
class Outcome:
    """A prediction made without its return, the average return it is measured against, and the return."""

    symbol: str
    signal_date: str
    predicted: float
    baseline: float
    actual: float


@dataclass(frozen=True)
class Score:
    predictions: int
    sessions: int
    first_signal_date: str | None
    last_signal_date: str | None
    # Mean over days of the rank correlation between the predictions and the returns across symbols.
    rank_ic: float | None
    rank_ic_days: int
    # rank_ic against its own day-to-day spread; returns over several sessions overlap, so days count for less.
    rank_ic_t: float | None
    # Share of predictions with the sign of the return, and the same for always predicting the average.
    hit_rate: float | None
    baseline_hit_rate: float | None
    # 1 minus the model's squared error over that of always predicting the average: above 0 beats it.
    r2_vs_baseline: float | None
    # Mean over days of the return of the third predicted highest minus the third predicted lowest.
    top_minus_bottom: float | None

    def columns(self, prefix: str) -> dict[str, object]:
        return {f"{prefix}_{field.name}": getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True)
class Training:
    horizon: int
    # Why nothing was trained, or None.
    not_trained: str | None = None
    rows: int = 0
    features: int = 0
    first_signal_date: str = ""
    last_signal_date: str = ""
    unadjusted_returns: int = 0
    out_of_sample: Score | None = None
    live: Score | None = None
    predicted_date: str = ""
    predictions: int = 0


def _number(column: str, value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LoaderError(f"training_rows.{column} is not a number: {value!r}")
    return float(value)


def build_dataset(records: Sequence[dict[str, object]]) -> Dataset:
    """Every column of training_rows is a feature except the row's key, its raw close and its returns."""
    if not records:
        return Dataset((), (), ())
    columns = list(records[0])
    for horizon in HORIZONS:
        if f"{_LABEL_PREFIX}_{horizon}d" not in columns:
            raise LoaderError(f"training_rows has no {_LABEL_PREFIX}_{horizon}d column")
    names = tuple(name for name in columns if name not in _NOT_FEATURES and not name.startswith(_LABEL_PREFIX))
    sessions = tuple(sorted({str(record["signal_date"]) for record in records}))
    position = {day: index for index, day in enumerate(sessions)}
    rows = tuple(
        Row(
            symbol=str(record["symbol"]),
            signal_date=str(record["signal_date"]),
            session=position[str(record["signal_date"])],
            features=tuple(_number(name, record.get(name)) for name in names),
            returns={
                horizon: _number(f"{_LABEL_PREFIX}_{horizon}d", record.get(f"{_LABEL_PREFIX}_{horizon}d"))
                for horizon in HORIZONS
            },
            returns_adjusted=record.get("returns_adjusted") is True,
        )
        for record in records
    )
    return Dataset(names, sessions, rows)


def _predict(fit: Fit, training: Sequence[Row], horizon: int, rows: Sequence[Row]) -> tuple[Sequence[float], float]:
    returns = [row.returns[horizon] for row in training]
    predict = fit([row.features for row in training], returns)
    predicted = predict([row.features for row in rows])
    if len(predicted) != len(rows):
        raise LoaderError(f"The model predicted {len(predicted)} returns for {len(rows)} rows")
    return predicted, fmean(returns)


def walk_forward(dataset: Dataset, horizon: int, fit: Fit) -> list[Outcome]:
    """Predictions for every session after the first MIN_TRAIN_SESSIONS, each from a model that could not see it.

    A row's return is known `horizon` sessions after its own, so the model that predicts the block of sessions
    starting at session b is trained only on rows of sessions up to b - horizon. Empty when the history is too
    short to test a whole block.
    """
    known = [row for row in dataset.rows if row.returns[horizon] is not None]
    sessions = sorted({row.session for row in known})
    if len(sessions) < MIN_TRAIN_SESSIONS:
        return []
    first = sessions[MIN_TRAIN_SESSIONS - 1] + horizon
    if sessions[-1] - first + 1 < TEST_BLOCK_SESSIONS:
        return []
    outcomes: list[Outcome] = []
    for block in range(first, sessions[-1] + 1, TEST_BLOCK_SESSIONS):
        training = [row for row in known if row.session + horizon <= block]
        tested = [row for row in known if block <= row.session < block + TEST_BLOCK_SESSIONS]
        if not tested:
            continue
        predicted, baseline = _predict(fit, training, horizon, tested)
        outcomes.extend(
            Outcome(row.symbol, row.signal_date, float(value), baseline, row.returns[horizon])
            for row, value in zip(tested, predicted)
        )
    return outcomes


def _ranks(values: Sequence[float]) -> list[float]:
    """Positions in ascending order, equal values sharing the mean of their positions."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for index in order[start : end + 1]:
            ranks[index] = (start + end) / 2
        start = end + 1
    return ranks


def rank_correlation(first: Sequence[float], second: Sequence[float]) -> float:
    """Spearman's. 0 when either side is the same everywhere, which ranks nothing."""
    a, b = _ranks(first), _ranks(second)
    mean_a, mean_b = fmean(a), fmean(b)
    spread_a = sum((value - mean_a) ** 2 for value in a)
    spread_b = sum((value - mean_b) ** 2 for value in b)
    if spread_a == 0 or spread_b == 0:
        return 0.0
    return sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b)) / sqrt(spread_a * spread_b)


def _top_minus_bottom(day: Sequence[Outcome]) -> float:
    if len({outcome.predicted for outcome in day}) == 1:
        return 0.0
    ordered = sorted(day, key=lambda outcome: (outcome.predicted, outcome.symbol))
    third = len(ordered) // 3
    return fmean(o.actual for o in ordered[-third:]) - fmean(o.actual for o in ordered[:third])


def _hit_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    hits = [(guess > 0) == (actual > 0) for guess, actual in pairs if actual != 0]
    return sum(hits) / len(hits) if hits else None


def score(outcomes: Sequence[Outcome], horizon: int) -> Score:
    days: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        days.setdefault(outcome.signal_date, []).append(outcome)
    ranked = [day for _, day in sorted(days.items()) if len(day) >= MIN_RANKED_SYMBOLS]
    correlations = [rank_correlation([o.predicted for o in day], [o.actual for o in day]) for day in ranked]
    rank_ic_t = None
    if len(correlations) >= 2 and stdev(correlations) > 0:
        rank_ic_t = fmean(correlations) / (stdev(correlations) / sqrt(len(correlations) / horizon))
    baseline_error = sum((o.actual - o.baseline) ** 2 for o in outcomes)
    return Score(
        predictions=len(outcomes),
        sessions=len(days),
        first_signal_date=min(days, default=None),
        last_signal_date=max(days, default=None),
        rank_ic=fmean(correlations) if correlations else None,
        rank_ic_days=len(correlations),
        rank_ic_t=rank_ic_t,
        hit_rate=_hit_rate((o.predicted, o.actual) for o in outcomes),
        baseline_hit_rate=_hit_rate((o.baseline, o.actual) for o in outcomes),
        r2_vs_baseline=(
            1 - sum((o.actual - o.predicted) ** 2 for o in outcomes) / baseline_error if baseline_error else None
        ),
        top_minus_bottom=fmean(_top_minus_bottom(day) for day in ranked) if ranked else None,
    )


def _stored_outcomes(supabase: SupabaseClient, horizon: int) -> list[Outcome]:
    """Predictions earlier runs stored whose return has since become known."""
    records = supabase.select(
        "model_prediction_outcomes",
        "symbol,signal_date,predicted_return,baseline_return,realized_return",
        order="signal_date.asc,symbol.asc",
        filters={"horizon_days": f"eq.{horizon}", "realized_return": "not.is.null"},
    )
    return [
        Outcome(
            str(record["symbol"]),
            str(record["signal_date"]),
            float(record["predicted_return"]),
            float(record["baseline_return"]),
            float(record["realized_return"]),
        )
        for record in records
    ]


def _train_horizon(
    supabase: SupabaseClient, dataset: Dataset, horizon: int, fit: Fit, model: str, parameters: dict[str, object]
) -> Training:
    known = [row for row in dataset.rows if row.returns[horizon] is not None]
    tested = walk_forward(dataset, horizon, fit)
    if not tested:
        sessions = len({row.session for row in known})
        return Training(
            horizon,
            not_trained=f"{sessions} sessions have a known {horizon}-day return; the test needs "
            f"{MIN_TRAIN_SESSIONS} to train on, {horizon} more for their returns to be known and "
            f"{TEST_BLOCK_SESSIONS} to test on",
        )
    out_of_sample = score(tested, horizon)
    live = score(_stored_outcomes(supabase, horizon), horizon)
    # Only the newest session: an older one without a return would be predicted from returns that came after it.
    newest = [row for row in dataset.rows if row.session == len(dataset.sessions) - 1 and row.returns[horizon] is None]
    predicted, baseline = _predict(fit, known, horizon, newest)
    training = Training(
        horizon,
        rows=len(known),
        features=len(dataset.features),
        first_signal_date=min(row.signal_date for row in known),
        last_signal_date=max(row.signal_date for row in known),
        unadjusted_returns=sum(not row.returns_adjusted for row in known),
        out_of_sample=out_of_sample,
        live=live,
        predicted_date=dataset.sessions[-1],
        predictions=len(newest),
    )
    run_id = str(uuid.uuid4())
    supabase.upsert(
        "model_runs",
        [
            {
                "id": run_id,
                "horizon_days": horizon,
                "model": model,
                "parameters": parameters,
                "features": list(dataset.features),
                "training_rows": training.rows,
                "training_sessions": len({row.session for row in known}),
                "first_signal_date": training.first_signal_date,
                "last_signal_date": training.last_signal_date,
                "unadjusted_returns": training.unadjusted_returns,
                **out_of_sample.columns("oos"),
                **live.columns("live"),
            }
        ],
        on_conflict="id",
        ignore_duplicates=True,
    )
    # The first prediction for a symbol and day is the one kept: a later run could have seen more.
    supabase.upsert(
        "model_predictions",
        [
            {
                "symbol": row.symbol,
                "signal_date": row.signal_date,
                "horizon_days": horizon,
                "run_id": run_id,
                "predicted_return": float(value),
                "baseline_return": baseline,
            }
            for row, value in zip(newest, predicted)
        ],
        on_conflict="symbol,signal_date,horizon_days",
        ignore_duplicates=True,
    )
    return training


def train_models(supabase: SupabaseClient, fit: Fit, model: str, parameters: dict[str, object]) -> list[Training]:
    dataset = build_dataset(supabase.select("training_rows", "*", order="signal_date.asc,symbol.asc"))
    return [_train_horizon(supabase, dataset, horizon, fit, model, parameters) for horizon in HORIZONS]


def _percent(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def _decimal(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _describe_score(found: Score) -> str:
    return (
        f"{found.predictions} predictions from {found.first_signal_date} to {found.last_signal_date}: "
        f"rank IC {_decimal(found.rank_ic)} over {found.rank_ic_days} day{'' if found.rank_ic_days == 1 else 's'} "
        f"(t {_decimal(found.rank_ic_t)}), "
        f"right direction {_percent(found.hit_rate)} against {_percent(found.baseline_hit_rate)} for always "
        f"predicting the average, R2 against the average {_decimal(found.r2_vs_baseline)}, "
        f"top third minus bottom third {_percent(found.top_minus_bottom, 2)}"
    )


def describe(training: Training) -> list[str]:
    name = f"{training.horizon}-day model"
    if training.not_trained:
        return [f"{name} not trained: {training.not_trained}"]
    lines = [
        f"{name} trained on {training.rows} rows from {training.first_signal_date} to "
        f"{training.last_signal_date} with {training.features} features",
        f"{name} on days it was not trained on, {_describe_score(training.out_of_sample)}",
    ]
    if training.unadjusted_returns:
        lines.append(
            f"{name}: {training.unadjusted_returns} of the returns it was trained on may miss a split or dividend"
        )
    if training.live.predictions:
        lines.append(f"{name} stored predictions so far, {_describe_score(training.live)}")
    else:
        lines.append(f"{name}: no stored prediction has a known return yet")
    lines.append(f"{name}: {training.predictions} predictions for {training.predicted_date}")
    return lines
