from __future__ import annotations

from datetime import date, timedelta
import random
from statistics import fmean
from typing import Callable, Sequence

import pytest

from research_data import boosted_trees, cli
from research_data.boosted_trees import MODEL, PARAMETERS, training_installed
from research_data.cli import main as cli_main
from research_data.http import LoaderError
from research_data.model import (
    MIN_TRAIN_SESSIONS,
    TEST_BLOCK_SESSIONS,
    Features,
    Outcome,
    Predict,
    Score,
    build_dataset,
    describe,
    rank_correlation,
    score,
    train_models,
    walk_forward,
)

SYMBOLS = ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA")
FIRST_DAY = date(2026, 1, 1)


def day(session: int) -> str:
    return (FIRST_DAY + timedelta(days=session)).isoformat()


def history(sessions: int, returns: Callable[[int, int], float] = lambda session, symbol: 0.001) -> list[dict]:
    """Rows shaped like training_rows: a return is known once the close it ends at is loaded."""
    return [
        {
            "symbol": name,
            "signal_date": day(session),
            "session_number": session,
            "symbol_number": symbol,
            "sentiment_net": None,
            "close": 100 + session,
            "forward_return_1d": returns(session, symbol) if session + 1 < sessions else None,
            "forward_return_5d": returns(session, symbol) * 5 if session + 5 < sessions else None,
            "returns_adjusted": True,
        }
        for session in range(sessions)
        for symbol, name in enumerate(SYMBOLS)
    ]


class RecordingFit:
    """Stands in for the model: remembers what it was trained on and predicts each row's symbol number."""

    def __init__(self) -> None:
        self.trained_on: list[list[tuple[int, int]]] = []
        self.returns: list[list[float]] = []
        self.predicted: list[list[tuple[int, int]]] = []

    def __call__(self, features: Sequence[Features], returns: Sequence[float]) -> Predict:
        self.trained_on.append([(int(row[0]), int(row[1])) for row in features])
        self.returns.append(list(returns))

        def predict(rows: Sequence[Features]) -> list[float]:
            self.predicted.append([(int(row[0]), int(row[1])) for row in rows])
            return [row[1] / 100 for row in rows]

        return predict


class FakeSupabase:
    def __init__(self, reads: dict[str, list[dict[str, object]]]) -> None:
        self.reads = reads
        self.selects: list[tuple[str, str, str, dict[str, str] | None]] = []
        self.upserts: list[tuple[str, list[dict[str, object]], str, bool]] = []

    def select(self, table: str, columns: str, order: str, filters=None, limit=None) -> list[dict[str, object]]:
        self.selects.append((table, columns, order, filters))
        rows = self.reads.get(table, [])
        if filters and "horizon_days" in filters:
            rows = [row for row in rows if filters["horizon_days"] == f"eq.{row['horizon_days']}"]
        return rows

    def upsert(self, table: str, rows: list[dict[str, object]], on_conflict: str, ignore_duplicates: bool = False):
        self.upserts.append((table, list(rows), on_conflict, ignore_duplicates))


def test_every_column_is_a_feature_except_the_key_the_raw_close_and_the_returns() -> None:
    dataset = build_dataset(history(3))
    assert dataset.features == ("session_number", "symbol_number", "sentiment_net")
    assert dataset.sessions == ("2026-01-01", "2026-01-02", "2026-01-03")
    assert len(dataset.rows) == 21
    last = dataset.rows[-1]
    assert (last.symbol, last.signal_date, last.session) == ("TSLA", "2026-01-03", 2)
    assert last.features == (2.0, 6.0, None)
    assert last.returns == {1: None, 5: None}
    assert dataset.rows[0].returns == {1: 0.001, 5: None}
    assert dataset.rows[0].returns_adjusted is True
    assert build_dataset([]).rows == ()


def test_a_row_without_the_flag_counts_as_unadjusted_and_a_feature_that_is_not_a_number_is_an_error() -> None:
    record = history(1)[0]
    assert build_dataset([{**record, "returns_adjusted": None}]).rows[0].returns_adjusted is False
    assert build_dataset([{**record, "returns_adjusted": False}]).rows[0].returns_adjusted is False
    with pytest.raises(LoaderError, match=r"training_rows\.sentiment_net is not a number: 'high'"):
        build_dataset([{**record, "sentiment_net": "high"}])
    with pytest.raises(LoaderError, match=r"training_rows\.sentiment_net is not a number: True"):
        build_dataset([{**record, "sentiment_net": True}])
    del record["forward_return_5d"]
    with pytest.raises(LoaderError, match="training_rows has no forward_return_5d column"):
        build_dataset([record])


@pytest.mark.parametrize("horizon", [1, 5])
def test_a_model_only_trains_on_returns_that_were_known_before_the_first_day_it_predicts(horizon: int) -> None:
    sessions = 110
    fit = RecordingFit()
    outcomes = walk_forward(build_dataset(history(sessions)), horizon, fit)
    last_known = sessions - 1 - horizon
    first = MIN_TRAIN_SESSIONS - 1 + horizon
    blocks = list(range(first, last_known + 1, TEST_BLOCK_SESSIONS))
    assert len(fit.trained_on) == len(fit.predicted) == len(blocks) >= 2
    for block, trained_on, predicted in zip(blocks, fit.trained_on, fit.predicted):
        # The return of session s ends at the close of session s + horizon.
        assert max(session for session, _ in trained_on) + horizon == block
        assert {session for session, _ in trained_on} == set(range(block - horizon + 1))
        assert len(trained_on) == (block - horizon + 1) * len(SYMBOLS)
        assert {session for session, _ in predicted} == set(range(block, min(block + 20, last_known + 1)))
    # Every session with a known return after the first training window is predicted exactly once.
    assert [(o.signal_date, o.symbol) for o in outcomes] == [
        (day(session), name) for session in range(first, last_known + 1) for name in SYMBOLS
    ]
    assert outcomes[0].predicted == 0.0 and outcomes[6].predicted == 0.06


def test_the_average_the_model_is_measured_against_is_that_of_the_rows_it_was_trained_on() -> None:
    dataset = build_dataset(history(101, returns=lambda session, symbol: session / 1000))
    fit = RecordingFit()
    outcomes = walk_forward(dataset, 1, fit)
    assert fit.returns[0] == [session / 1000 for session in range(60) for _ in SYMBOLS]
    by_day = {outcome.signal_date: outcome for outcome in outcomes}
    assert by_day[day(60)].baseline == pytest.approx(fmean(range(60)) / 1000)
    assert by_day[day(79)].baseline == pytest.approx(fmean(range(60)) / 1000)
    assert by_day[day(80)].baseline == pytest.approx(fmean(range(80)) / 1000)
    assert by_day[day(80)].actual == 0.08


@pytest.mark.parametrize(("horizon", "enough"), [(1, 81), (5, 89)])
def test_a_history_too_short_to_test_a_whole_block_of_sessions_is_not_tested(horizon: int, enough: int) -> None:
    fit = RecordingFit()
    assert walk_forward(build_dataset(history(enough - 1)), horizon, fit) == []
    assert walk_forward(build_dataset(history(MIN_TRAIN_SESSIONS - 1)), horizon, fit) == []
    assert fit.trained_on == []
    outcomes = walk_forward(build_dataset(history(enough)), horizon, fit)
    assert len({outcome.signal_date for outcome in outcomes}) == TEST_BLOCK_SESSIONS


def test_rows_whose_return_is_missing_are_neither_trained_on_nor_tested() -> None:
    records = history(100)
    for record in records:
        if record["symbol"] == "TSLA" and record["session_number"] in (10, 70):
            record["forward_return_1d"] = None
    fit = RecordingFit()
    outcomes = walk_forward(build_dataset(records), 1, fit)
    assert (10, 6) not in fit.trained_on[0] and (10, 5) in fit.trained_on[0]
    assert (70, 6) not in fit.predicted[0] and (70, 5) in fit.predicted[0]
    assert (day(70), "TSLA") not in {(outcome.signal_date, outcome.symbol) for outcome in outcomes}


def test_rank_correlation_shares_ranks_between_equal_values_and_is_zero_when_nothing_is_ranked() -> None:
    assert rank_correlation([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == pytest.approx(1)
    assert rank_correlation([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == pytest.approx(-1)
    assert rank_correlation([1, 2, 3, 4, 5], [2, 1, 3, 5, 4]) == pytest.approx(0.8)
    # Ranks 0, 1.5, 1.5, 3 against 0, 1, 2, 3.
    assert rank_correlation([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(4.5 / (4.5 * 5) ** 0.5)
    assert rank_correlation([7, 7, 7], [1, 2, 3]) == 0
    assert rank_correlation([1, 2, 3], [0, 0, 0]) == 0


def outcomes_of(signal_date: str, predicted: Sequence[float], actual: Sequence[float]) -> list[Outcome]:
    return [Outcome(SYMBOLS[i], signal_date, predicted[i], 0.001, actual[i]) for i in range(len(predicted))]


SCORED = [
    *outcomes_of("2026-03-02", [0.01, 0.02, 0.03, 0.04, 0.05], [-0.02, -0.01, 0.0, 0.01, 0.03]),
    *outcomes_of("2026-03-03", [0.01, 0.02, 0.03, 0.04, 0.05], [0.02, 0.01, 0.03, 0.05, 0.04]),
    # Too few symbols to rank, but still predictions.
    *outcomes_of("2026-03-04", [-0.01, 0.02], [0.01, -0.03]),
]


def test_the_score_of_predictions_against_what_happened() -> None:
    found = score(SCORED, 1)
    assert (found.predictions, found.sessions) == (12, 3)
    assert (found.first_signal_date, found.last_signal_date) == ("2026-03-02", "2026-03-04")
    assert found.rank_ic == pytest.approx(0.9)
    assert found.rank_ic_days == 2
    assert found.rank_ic_t == pytest.approx(9.0)
    # A return of exactly zero has no direction to get right.
    assert found.hit_rate == pytest.approx(7 / 11)
    assert found.baseline_hit_rate == pytest.approx(8 / 11)
    assert found.r2_vs_baseline == pytest.approx(1 - 0.0073 / 0.007732)
    assert found.top_minus_bottom == pytest.approx((0.05 + 0.02) / 2)


def test_overlapping_returns_count_for_fewer_days_and_a_day_of_equal_predictions_ranks_nothing() -> None:
    assert score(SCORED, 5).rank_ic_t == pytest.approx(9.0 / 5**0.5)
    flat = outcomes_of("2026-03-05", [0.01] * 5, [0.05, 0.01, 0.02, 0.03, -0.04])
    found = score([*SCORED, *flat], 1)
    assert found.rank_ic == pytest.approx(0.6)
    assert found.top_minus_bottom == pytest.approx(0.07 / 3)
    # Seven symbols: the top and bottom thirds are two symbols each.
    seven = outcomes_of("2026-03-06", [1, 2, 3, 4, 5, 6, 7], [0.01, 0.03, 0.0, 0.0, 0.0, 0.05, 0.09])
    assert score(seven, 1).top_minus_bottom == pytest.approx(0.07 - 0.02)
    assert score(seven, 1).rank_ic_t is None


def test_nothing_to_score_is_a_score_of_nothing() -> None:
    assert score([], 1) == Score(0, 0, None, None, None, 0, None, None, None, None, None)
    still = [Outcome("AAPL", "2026-03-02", 0.01, 0.0, 0.0)]
    found = score(still, 1)
    assert (found.hit_rate, found.baseline_hit_rate, found.r2_vs_baseline, found.rank_ic) == (None, None, None, None)


def test_a_retrain_stores_its_score_and_predicts_only_the_newest_session() -> None:
    records = history(100, returns=lambda session, symbol: (symbol - 2) / 1000)
    stored = [
        {**row, "horizon_days": 1}
        for o in SCORED
        for row in [
            {
                "symbol": o.symbol,
                "signal_date": o.signal_date,
                "predicted_return": o.predicted,
                "baseline_return": o.baseline,
                "realized_return": o.actual,
            }
        ]
    ]
    supabase = FakeSupabase({"training_rows": records, "model_prediction_outcomes": stored})
    fit = RecordingFit()
    one_day, five_day = train_models(supabase, fit, "a model", {"depth": 3})

    assert supabase.selects == [
        ("training_rows", "*", "signal_date.asc,symbol.asc", None),
        (
            "model_prediction_outcomes",
            "symbol,signal_date,predicted_return,baseline_return,realized_return",
            "signal_date.asc,symbol.asc",
            {"horizon_days": "eq.1", "realized_return": "not.is.null"},
        ),
        (
            "model_prediction_outcomes",
            "symbol,signal_date,predicted_return,baseline_return,realized_return",
            "signal_date.asc,symbol.asc",
            {"horizon_days": "eq.5", "realized_return": "not.is.null"},
        ),
    ]
    assert [(table, on_conflict, ignore) for table, _, on_conflict, ignore in supabase.upserts] == [
        ("model_runs", "id", True),
        ("model_predictions", "symbol,signal_date,horizon_days", True),
        ("model_runs", "id", True),
        ("model_predictions", "symbol,signal_date,horizon_days", True),
    ]
    (run,) = supabase.upserts[0][1]
    tested = score(walk_forward(build_dataset(records), 1, RecordingFit()), 1)
    assert run == {
        "id": run["id"],
        "horizon_days": 1,
        "model": "a model",
        "parameters": {"depth": 3},
        "features": ["session_number", "symbol_number", "sentiment_net"],
        "training_rows": 99 * 7,
        "training_sessions": 99,
        "first_signal_date": day(0),
        "last_signal_date": day(98),
        "unadjusted_returns": 0,
        **tested.columns("oos"),
        **score(SCORED, 1).columns("live"),
    }
    assert run["oos_predictions"] == 39 * 7 and run["oos_first_signal_date"] == day(60)
    assert run["live_predictions"] == 12 and run["live_rank_ic"] == pytest.approx(0.9)

    # The last model is trained on every known return and predicts the one session that has none.
    walk_forward_fits = 2
    assert sorted({session for session, _ in fit.trained_on[walk_forward_fits]}) == list(range(99))
    assert fit.predicted[walk_forward_fits] == [(99, symbol) for symbol in range(7)]
    assert supabase.upserts[1][1] == [
        {
            "symbol": name,
            "signal_date": day(99),
            "horizon_days": 1,
            "run_id": run["id"],
            "predicted_return": symbol / 100,
            "baseline_return": pytest.approx(0.001),
        }
        for symbol, name in enumerate(SYMBOLS)
    ]

    (five_day_run,) = supabase.upserts[2][1]
    assert five_day_run["id"] != run["id"]
    assert (five_day_run["horizon_days"], five_day_run["training_sessions"]) == (5, 95)
    assert five_day_run["live_predictions"] == 0 and five_day_run["live_rank_ic"] is None
    assert {row["signal_date"] for row in supabase.upserts[3][1]} == {day(99)}
    assert {row["run_id"] for row in supabase.upserts[3][1]} == {five_day_run["id"]}
    assert supabase.upserts[3][1][0]["baseline_return"] == pytest.approx(0.005)

    assert describe(one_day) == [
        f"1-day model trained on 693 rows from {day(0)} to {day(98)} with 3 features",
        f"1-day model on days it was not trained on, 273 predictions from {day(60)} to {day(98)}: rank IC 1.000 "
        "over 39 days (t n/a), right direction 83.3% against 66.7% for always predicting the average, R2 against "
        "the average -290.250, top third minus bottom third 0.50%",
        "1-day model stored predictions so far, 12 predictions from 2026-03-02 to 2026-03-04: rank IC 0.900 over "
        "2 days (t 9.000), right direction 63.6% against 72.7% for always predicting the average, R2 against the "
        "average 0.056, top third minus bottom third 3.50%",
        f"1-day model: 7 predictions for {day(99)}",
    ]
    assert describe(five_day)[-2:] == [
        "5-day model: no stored prediction has a known return yet",
        f"5-day model: 7 predictions for {day(99)}",
    ]


def test_returns_that_may_miss_a_split_are_counted_and_reported() -> None:
    records = history(100)
    # The newest session has no return yet, so its rows are not among those counted.
    for record in records[:14] + records[-7:]:
        record["returns_adjusted"] = False
    supabase = FakeSupabase({"training_rows": records})
    one_day, _ = train_models(supabase, RecordingFit(), "a model", {})
    assert supabase.upserts[0][1][0]["unadjusted_returns"] == 14
    assert "1-day model: 14 of the returns it was trained on may miss a split or dividend" in describe(one_day)


def test_a_return_that_is_already_known_is_never_stored_as_a_prediction() -> None:
    records = history(100)
    records[-1]["forward_return_1d"] = 0.02
    supabase = FakeSupabase({"training_rows": records})
    train_models(supabase, RecordingFit(), "a model", {})
    assert [row["symbol"] for row in supabase.upserts[1][1]] == list(SYMBOLS[:-1])
    assert [row["symbol"] for row in supabase.upserts[3][1]] == list(SYMBOLS)


def test_too_little_history_stores_nothing_and_says_what_is_missing() -> None:
    supabase = FakeSupabase({"training_rows": history(85)})
    fit = RecordingFit()
    one_day, five_day = train_models(supabase, fit, "a model", {})
    # 85 sessions are enough for the 1-day model and four short for the 5-day one.
    assert [table for table, *_ in supabase.upserts] == ["model_runs", "model_predictions"]
    assert one_day.not_trained is None
    assert describe(five_day) == [
        "5-day model not trained: 80 sessions have a known 5-day return; the test needs 60 to train on, "
        "5 more for their returns to be known and 20 to test on"
    ]
    empty = FakeSupabase({})
    assert [training.not_trained is not None for training in train_models(empty, fit, "a model", {})] == [True, True]
    assert empty.upserts == []


needs_scikit_learn = pytest.mark.skipif(not training_installed(), reason="needs `poetry install --extras train`")


def planted(sessions: int, seed: int = 0) -> list[dict]:
    """Returns that follow one feature, with as much noise again, a feature of noise and one mostly missing."""
    source = random.Random(seed)
    records = history(sessions)
    for record in records:
        signal = source.gauss(0, 1)
        record["sentiment_net"] = signal if source.random() < 0.9 else None
        record["iv_30d"] = source.gauss(0.3, 0.1)
        record["podcast_sentiment_net"] = source.gauss(0, 1) if source.random() < 0.1 else None
        for horizon in (1, 5):
            if record[f"forward_return_{horizon}d"] is not None:
                record[f"forward_return_{horizon}d"] = 0.01 * signal + source.gauss(0, 0.01)
    return records


@needs_scikit_learn
def test_the_model_finds_a_signal_that_is_there_and_gives_the_same_answer_each_time() -> None:
    dataset = build_dataset(planted(140))
    outcomes = walk_forward(dataset, 1, boosted_trees.fit)
    found = score(outcomes, 1)
    assert found.predictions == 79 * 7
    assert found.rank_ic > 0.4 and found.rank_ic_t > 5
    assert found.r2_vs_baseline > 0.2
    assert found.hit_rate > 0.6 > found.baseline_hit_rate
    assert found.top_minus_bottom > 0.01
    assert walk_forward(dataset, 1, boosted_trees.fit) == outcomes


@needs_scikit_learn
def test_one_extreme_return_does_not_decide_the_fit() -> None:
    features = [(float(i % 10),) for i in range(400)]
    returns = [0.001 * (i % 10) for i in range(400)]
    returns[7] = 50.0
    predict = boosted_trees.fit(features, returns)
    predicted = predict([(float(i),) for i in range(10)])
    assert max(predicted) < 0.02
    assert predicted[9] > predicted[0]
    assert predict([]) == []
    assert PARAMETERS["early_stopping"] is False and PARAMETERS["random_state"] == 0


@needs_scikit_learn
def test_a_feature_with_no_value_in_any_training_row_is_left_out() -> None:
    # A source that is not loaded yet leaves its columns empty, which scikit-learn refuses to train on.
    features = [(None, float(i % 10), None) for i in range(400)]
    returns = [0.001 * (i % 10) for i in range(400)]
    predict = boosted_trees.fit(features, returns)
    low, high = predict([(None, 0.0, None), (5.0, 9.0, 1.0)])
    assert high > low
    # A value where there was none to learn from changes nothing.
    assert predict([(5.0, 9.0, 1.0)]) == predict([(None, 9.0, None)])
    with pytest.raises(LoaderError, match="None of the features has a value in any of the rows to train on"):
        boosted_trees.fit([(None, None)] * 100, [0.01] * 100)


def test_training_without_scikit_learn_names_the_extra_to_install(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(boosted_trees, "HistGradientBoostingRegressor", None)
    assert not training_installed()
    with pytest.raises(LoaderError, match="needs scikit-learn: run `poetry install --extras train`"):
        boosted_trees.fit([(1.0,)], [0.01])
    supabase = FakeSupabase({"training_rows": history(100)})
    monkeypatch.setattr(cli.SupabaseClient, "from_env", classmethod(lambda cls, env: supabase))
    assert cli_main(["train"]) == 1
    assert capsys.readouterr().err == (
        "research-data: Training the model needs scikit-learn: run `poetry install --extras train`\n"
    )
    assert supabase.upserts == []


def test_cli_train_retrains_and_prints_the_score(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    supabase = FakeSupabase({"training_rows": history(85)})
    monkeypatch.setattr(cli.SupabaseClient, "from_env", classmethod(lambda cls, env: supabase))
    monkeypatch.setattr(cli, "fit", RecordingFit())
    assert cli_main(["train"]) == 0
    printed = capsys.readouterr()
    assert printed.err == ""
    lines = printed.out.splitlines()
    assert lines[0] == f"train: 1-day model trained on 588 rows from {day(0)} to {day(83)} with 3 features"
    assert lines[-2] == f"train: 1-day model: 7 predictions for {day(84)}"
    assert lines[-1].startswith("train: 5-day model not trained: 80 sessions have a known 5-day return")
    (run,) = supabase.upserts[0][1]
    assert (run["model"], run["parameters"]) == (MODEL, PARAMETERS)
