from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from research_data.http import LoaderError

try:
    from openbb import obb
    from openbb_core.app.model.abstract.error import OpenBBError
except ImportError:
    # Both come from the optional `openbb` extra; fetch_news_sentiment reports how to install it.
    obb = None

INSTALL_COMMAND = "poetry install --extras openbb"


@dataclass(frozen=True)
class SentimentReading:
    observation_date: date
    value: float


def openbb_installed() -> bool:
    return obb is not None


def fetch_news_sentiment() -> list[SentimentReading]:
    """The San Francisco Fed's Daily News Sentiment Index, every day it publishes, read through OpenBB."""
    if obb is None:
        raise LoaderError(f"The openbb step needs OpenBB's Federal Reserve extension: run `{INSTALL_COMMAND}`")
    try:
        results = obb.federal_reserve.sf.news_sentiment().results
    except OpenBBError as exc:
        detail = " ".join(str(exc).split())
        raise LoaderError(f"OpenBB could not load the San Francisco Fed news sentiment index: {detail}") from None
    return [
        SentimentReading(result.date, result.news_sentiment)
        for result in results
        if result.news_sentiment is not None
    ]
