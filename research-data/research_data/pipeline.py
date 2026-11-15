from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Iterable

from research_data.http import LoaderError
from research_data.newsletters import Newsletter, ticker_passages
from research_data.prices import DATASET, PriceDownload
from research_data.supabase import SupabaseClient
from research_data.tickers import Watchlist

_SENTIMENT_LABELS = ("positive", "neutral", "negative")
_SCORE_BATCH_ROWS = 100
_NEWSLETTER_BATCH_ROWS = 25


@dataclass(frozen=True)
class Sentiment:
    model: str
    scores: dict[str, float]


SentimentScorer = Callable[[str], Sentiment]
PriceDownloader = Callable[[list[str], date], PriceDownload]


def load_newsletters(
    supabase: SupabaseClient,
    newsletters: Iterable[Newsletter],
    watchlist: Watchlist,
) -> tuple[int, int]:
    """Store newsletters and their ticker passages.

    Running it again over the same messages is safe: stored newsletters are left alone, and
    stored passages only have their tickers refreshed, which is how a longer watchlist takes
    effect on older newsletters without losing sentiment scores.
    """
    newsletter_rows: list[dict[str, object]] = []
    passage_rows: list[dict[str, object]] = []
    newsletter_count = passage_count = 0

    def flush() -> None:
        supabase.upsert("newsletters", newsletter_rows, on_conflict="id", ignore_duplicates=True)
        supabase.upsert("newsletter_passages", passage_rows, on_conflict="newsletter_id,passage_index")
        newsletter_rows.clear()
        passage_rows.clear()

    for newsletter in newsletters:
        newsletter_rows.append(
            {
                "id": newsletter.id,
                "sender": newsletter.sender,
                "publication": newsletter.publication,
                "subject": newsletter.subject,
                "received_at": newsletter.received_at.isoformat(),
                "body_text": newsletter.body_text,
            }
        )
        passages = ticker_passages(newsletter.body_text, watchlist)
        passage_rows.extend(
            {
                "newsletter_id": newsletter.id,
                "passage_index": passage.index,
                "passage_text": passage.text,
                "tickers": passage.tickers,
            }
            for passage in passages
        )
        newsletter_count += 1
        passage_count += len(passages)
        if len(newsletter_rows) >= _NEWSLETTER_BATCH_ROWS:
            flush()
    flush()
    return newsletter_count, passage_count


def score_passages(supabase: SupabaseClient, score: SentimentScorer, limit: int) -> int:
    """Score unscored passages, saving each result as it arrives so a failed run resumes where it stopped."""
    scored = 0
    while scored < limit:
        batch = supabase.select(
            "newsletter_passages",
            columns="id,passage_text",
            order="id.asc",
            filters={"sentiment_label": "is.null"},
            limit=min(_SCORE_BATCH_ROWS, limit - scored),
        )
        if not batch:
            break
        for row in batch:
            sentiment = score(str(row["passage_text"]))
            missing = [label for label in _SENTIMENT_LABELS if label not in sentiment.scores]
            if missing:
                raise LoaderError(f"{sentiment.model} returned no score for: {', '.join(missing)}")
            supabase.update(
                "newsletter_passages",
                {"id": f"eq.{row['id']}"},
                {
                    "sentiment_label": max(_SENTIMENT_LABELS, key=lambda label: sentiment.scores[label]),
                    "sentiment_positive": sentiment.scores["positive"],
                    "sentiment_neutral": sentiment.scores["neutral"],
                    "sentiment_negative": sentiment.scores["negative"],
                    "sentiment_model": sentiment.model,
                    "scored_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            scored += 1
    return scored


def load_prices(
    supabase: SupabaseClient,
    download: PriceDownloader,
    watchlist: Watchlist,
    start: date,
) -> tuple[int, int, list[str]]:
    """Store daily bars for every watchlist symbol and every ticker mentioned in a stored passage."""
    mentioned = supabase.select("mentioned_tickers", columns="symbol", order="symbol.asc")
    symbols = sorted({*watchlist.symbols, *(str(row["symbol"]) for row in mentioned)})
    result = download(symbols, start)
    supabase.upsert(
        "daily_prices",
        [
            {
                "symbol": bar.symbol,
                "trade_date": bar.trade_date.isoformat(),
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": bar.volume,
                "dataset": DATASET,
            }
            for bar in result.bars
        ],
        on_conflict="symbol,trade_date",
    )
    return len(symbols), len(result.bars), result.warnings
