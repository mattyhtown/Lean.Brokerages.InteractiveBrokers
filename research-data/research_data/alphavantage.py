from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import json
import math
from typing import Iterator
import urllib.parse

from research_data.http import HttpCallable, LoaderError, send

ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"
# The most articles one request may ask for.
PAGE_SIZE = 1000
# What a request returns when the API applies its default limit. A response shorter than this
# cannot have been cut off, whatever limit the API really applied.
SHORTEST_PAGE = 50
MAX_PAGES = 1000
_TIMEOUT_SECONDS = 120
# Alpha Vantage answers HTTP 200 with one of these keys instead of a feed when it has no data
# or refuses the request.
_MESSAGE_KEYS = ("Information", "Note", "Error Message")
_NO_ARTICLES = "no articles found"
_INVALID_INPUTS = "invalid inputs"


class SymbolRejected(LoaderError):
    """Alpha Vantage would not take the request for one symbol."""


@dataclass(frozen=True)
class Article:
    url: str
    # Alpha Vantage's `time_published`, which is in UTC.
    published_at: datetime
    title: str
    source: str | None
    # How much of the article is about the symbol, above 0 and up to 1.
    relevance: float
    # Alpha Vantage's own score for the symbol in this article: it calls -0.35 and below bearish
    # and 0.35 and above bullish.
    sentiment: float


@dataclass(frozen=True)
class _FeedEntry:
    url: str
    published_at: datetime
    title: str
    source: str | None
    scores: dict[str, tuple[float, float]]


def _published_at(value: str) -> datetime:
    form = "%Y%m%dT%H%M%S" if len(value) == 15 else "%Y%m%dT%H%M"
    return datetime.strptime(value, form).replace(tzinfo=timezone.utc)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise TypeError(value)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(value)
    return number


def _feed(body: bytes, symbol: str) -> list[_FeedEntry]:
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise TypeError(payload)
    except (ValueError, TypeError):
        raise LoaderError(f"Unexpected Alpha Vantage response for {symbol}: not a JSON object") from None
    records = payload.get("feed")
    if not isinstance(records, list):
        message = next((str(payload[key]) for key in _MESSAGE_KEYS if key in payload), None)
        if message is None:
            raise LoaderError(f"Unexpected Alpha Vantage response for {symbol}: no feed and no message")
        if _NO_ARTICLES in message.lower():
            return []
        if _INVALID_INPUTS in message.lower():
            raise SymbolRejected(f"Alpha Vantage rejected the {symbol} request: {message}")
        raise LoaderError(f"Alpha Vantage refused the {symbol} request: {message}")
    entries: list[_FeedEntry] = []
    for record in records:
        try:
            url = record["url"].replace("\x00", "").strip()
            if not url:
                raise ValueError(url)
            source = record.get("source")
            if source is not None and not isinstance(source, str):
                raise TypeError(source)
            entries.append(
                _FeedEntry(
                    url,
                    _published_at(record["time_published"]),
                    " ".join(record["title"].replace("\x00", "").split()),
                    (source or "").replace("\x00", "").strip() or None,
                    {
                        score["ticker"]: (_number(score["relevance_score"]), _number(score["ticker_sentiment_score"]))
                        for score in record["ticker_sentiment"]
                    },
                )
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            raise LoaderError(f"Unexpected Alpha Vantage article: {json.dumps(record)[:200]}") from None
    return entries


def _scored(entries: list[_FeedEntry], symbol: str) -> list[Article]:
    return [
        Article(entry.url, entry.published_at, entry.title, entry.source, *entry.scores[symbol])
        for entry in entries
        if symbol in entry.scores
    ]


def parse_articles(body: bytes, symbol: str) -> list[Article]:
    """The articles of one response that Alpha Vantage scored for `symbol`, in the order sent."""
    return _scored(_feed(body, symbol), symbol)


def article_pages(
    api_key: str,
    symbol: str,
    since: date,
    http: HttpCallable = send,
) -> Iterator[list[Article]]:
    """Download Alpha Vantage's scored news about `symbol` published on or after `since` (UTC), a page at a time.

    Oldest first. The API has no page number, so each further request starts at the minute of
    the newest article read so far, and the articles that minute shares with the page before
    are dropped. The download ends at a response too short to have been cut off.
    """
    start = datetime.combine(since, time.min, tzinfo=timezone.utc)
    seen: set[str] = set()
    for _ in range(MAX_PAGES):
        query = {
            "function": "NEWS_SENTIMENT",
            "tickers": symbol,
            "time_from": start.strftime("%Y%m%dT%H%M"),
            "sort": "EARLIEST",
            "limit": PAGE_SIZE,
            "apikey": api_key,
        }
        try:
            response = http("GET", f"{ALPHAVANTAGE_URL}?{urllib.parse.urlencode(query)}", {}, None, _TIMEOUT_SECONDS)
            entries = _feed(response.body, symbol)
        except LoaderError as exc:
            # Alpha Vantage repeats the key in some of its messages.
            kind = SymbolRejected if isinstance(exc, SymbolRejected) else LoaderError
            raise kind(str(exc).replace(api_key, "[key]")) from None
        more = len(entries) >= SHORTEST_PAGE
        if more and entries[0].published_at > entries[-1].published_at:
            # Paging forward from the newest article would skip everything older than this page.
            raise LoaderError(f"Alpha Vantage did not send the {symbol} articles oldest first")
        new = [entry for entry in entries if entry.url not in seen]
        seen.update(entry.url for entry in new)
        scored = _scored(new, symbol)
        if scored:
            yield scored
        if not more:
            return
        if not new:
            raise LoaderError(
                f"Alpha Vantage sent the same {len(entries)} {symbol} articles again "
                f"from {start:%Y-%m-%d %H:%M} UTC"
            )
        start = max(entry.published_at for entry in entries)
    raise LoaderError(
        f"Alpha Vantage has more than {MAX_PAGES} pages of {symbol} articles since {since}: "
        "load the rest with a later --since"
    )
