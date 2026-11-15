from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import email.utils
import json
from typing import Iterator
import urllib.parse

from research_data.http import HttpCallable, HttpStatusError, LoaderError, send

BENZINGA_URL = "https://api.benzinga.com/api/v2/news"
PAGE_SIZE = 100
MAX_PAGES = 1000
_TIMEOUT_SECONDS = 120
_NO_DATA = "no_data_found"


@dataclass(frozen=True)
class Headline:
    id: str
    # Benzinga's `created` time, which it documents as never changing.
    created_at: datetime
    title: str
    author: str | None


def parse_headlines(body: bytes) -> list[Headline]:
    """The articles of one response page, in the order sent."""
    try:
        records = json.loads(body)
        if not isinstance(records, list):
            raise TypeError(records)
    except (ValueError, TypeError):
        raise LoaderError("Unexpected Benzinga response: not a list of articles") from None
    headlines: list[Headline] = []
    for record in records:
        try:
            article_id = record["id"]
            if isinstance(article_id, bool) or not isinstance(article_id, (int, str)) or not str(article_id).isdigit():
                raise ValueError(article_id)
            created_at = email.utils.parsedate_to_datetime(record["created"])
            if created_at.tzinfo is None:
                raise ValueError(record["created"])
            title = " ".join(record["title"].replace("\x00", "").split())
            author = record.get("author")
            if author is not None and not isinstance(author, str):
                raise TypeError(author)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise LoaderError(f"Unexpected Benzinga article: {json.dumps(record)[:200]}") from None
        headlines.append(Headline(str(article_id), created_at, title, (author or "").strip() or None))
    return headlines


def headline_pages(
    token: str,
    symbol: str,
    since: date,
    http: HttpCallable = send,
) -> Iterator[list[Headline]]:
    """Download Benzinga's headlines about `symbol` published on or after `since`, a page at a time.

    Oldest first, so headlines published during the download land on the last page instead of
    shifting the pages already read.
    """
    seen: set[str] = set()
    for page in range(MAX_PAGES):
        query = {
            "token": token,
            "tickers": symbol,
            "dateFrom": since.isoformat(),
            "displayOutput": "headline",
            "sort": "created:asc",
            "pageSize": PAGE_SIZE,
            "page": page,
        }
        try:
            response = http(
                "GET",
                f"{BENZINGA_URL}?{urllib.parse.urlencode(query)}",
                # Without it Benzinga may answer in XML.
                {"accept": "application/json"},
                None,
                _TIMEOUT_SECONDS,
            )
        except HttpStatusError as exc:
            if exc.status == 404 and _NO_DATA in str(exc):
                return
            raise
        articles = parse_headlines(response.body)
        new = [article for article in articles if article.id not in seen]
        if articles and not new:
            raise LoaderError(f"Benzinga sent the same {symbol} headlines for page {page} as for an earlier page")
        seen.update(article.id for article in new)
        titled = [article for article in new if article.title]
        if titled:
            yield titled
        if len(articles) < PAGE_SIZE:
            return
    raise LoaderError(
        f"Benzinga has more than {MAX_PAGES * PAGE_SIZE} {symbol} headlines since {since}: "
        "load the rest with a later --since"
    )
