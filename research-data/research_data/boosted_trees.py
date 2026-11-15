from __future__ import annotations

from typing import Sequence

from research_data.http import LoaderError
from research_data.model import Features, Predict

try:
    import numpy
    from sklearn.ensemble import HistGradientBoostingRegressor
except ImportError:
    # Both come from the optional `train` extra; fit reports how to install it.
    HistGradientBoostingRegressor = None

MODEL = "sklearn.ensemble.HistGradientBoostingRegressor"
# Small trees, slow learning and large leaves: daily returns are mostly noise and the history is short.
# Fixed rather than searched for, so the test on unseen days is not also what chose them.
PARAMETERS: dict[str, object] = {
    "max_depth": 3,
    "learning_rate": 0.03,
    "max_iter": 150,
    "min_samples_leaf": 40,
    "l2_regularization": 1.0,
    "early_stopping": False,
    "random_state": 0,
}
# Returns outside these percentiles of the training returns are pulled in to them before training, so that one
# crash, or a split the factor files do not cover, does not decide the fit. Scoring uses the returns as they are.
_CLIP_PERCENTILES = (1, 99)


def training_installed() -> bool:
    return HistGradientBoostingRegressor is not None


def _matrix(rows: Sequence[Features], columns: Sequence[int]) -> numpy.ndarray:
    # A missing feature stays missing: the trees learn which way to send such rows.
    return numpy.array(
        [[numpy.nan if row[column] is None else row[column] for column in columns] for row in rows], dtype=float
    ).reshape(len(rows), len(columns))


def fit(features: Sequence[Features], returns: Sequence[float]) -> Predict:
    if HistGradientBoostingRegressor is None:
        raise LoaderError("Training the model needs scikit-learn: run `poetry install --extras train`")
    # A feature with no value in any training row, such as a source not loaded yet, is left out:
    # scikit-learn cannot train on a column that is entirely missing, and it could not use one.
    columns = [
        column for column in range(len(features[0])) if any(row[column] is not None for row in features)
    ]
    if not columns:
        raise LoaderError("None of the features has a value in any of the rows to train on")
    targets = numpy.array(returns, dtype=float)
    low, high = numpy.percentile(targets, _CLIP_PERCENTILES)
    model = HistGradientBoostingRegressor(**PARAMETERS).fit(_matrix(features, columns), numpy.clip(targets, low, high))

    def predict(rows: Sequence[Features]) -> list[float]:
        if not rows:
            return []
        return [float(value) for value in model.predict(_matrix(rows, columns))]

    return predict
