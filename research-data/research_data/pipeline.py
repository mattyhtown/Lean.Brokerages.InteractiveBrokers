from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Iterable

from research_data.http import LoaderError
from research_data.news import NewsDigest
from research_data.newsletters import Newsletter, passage_chunks, ticker_passages
from research_data.options import OptionsDownload
from research_data.prices import DATASET, PriceDownload
from research_data.supabase import SupabaseClient, in_filter
from research_data.tickers import Watchlist

_SENTIMENT_LABELS = ("positive", "neutral", "negative")
_SCORE_BATCH_ROWS = 100
_NEWSLETTER_BATCH_ROWS = 25
_UNTAG_BATCH_ROWS = 200
GMAIL_SOURCE = "gmail"
PERPLEXITY_SOURCE = "perplexity"


@dataclass(frozen=True)
class Sentiment:
    model: str
    scores: dict[str, float]


SentimentScorer = Callable[[str], Sentiment]
PriceDownloader = Callable[[list[str], date], PriceDownload]
OptionsDownloader = Callable[[list[str], date], OptionsDownload]
NewsFetcher = Callable[[str, list[str]], NewsDigest]


def load_newsletters(
    supabase: SupabaseClient,
    newsletters: Iterable[Newsletter],
    watchlist: Watchlist,
) -> tuple[int, int]:
    """Store newsletters and their ticker passages.

    Running it again over the same messages is safe: stored newsletters are left alone, and
    stored passages only have their tickers refreshed, which is how an edited watchlist takes
    effect on older newsletters without losing sentiment scores. A stored passage that no longer
    mentions any ticker keeps its text and score but stops counting toward any ticker.
    """
    newsletter_rows: list[dict[str, object]] = []
    passage_rows: list[dict[str, object]] = []
    newsletter_count = passage_count = 0

    def flush() -> None:
        if not newsletter_rows:
            return
        supabase.upsert("newsletters", newsletter_rows, on_conflict="id", ignore_duplicates=True)
        supabase.upsert("newsletter_passages", passage_rows, on_conflict="newsletter_id,passage_index")
        current = {(row["newsletter_id"], row["passage_index"]) for row in passage_rows}
        stored = supabase.select(
            "newsletter_passages",
            columns="id,newsletter_id,passage_index",
            order="id.asc",
            filters={
                "newsletter_id": in_filter(row["id"] for row in newsletter_rows),
                "tickers": "neq.{}",
            },
        )
        stale = [row["id"] for row in stored if (row["newsletter_id"], row["passage_index"]) not in current]
        for start in range(0, len(stale), _UNTAG_BATCH_ROWS):
            supabase.update(
                "newsletter_passages",
                {"id": in_filter(stale[start : start + _UNTAG_BATCH_ROWS])},
                {"tickers": []},
            )
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
                # Leaves the row alone if another run scored it in the meantime.
                {"id": f"eq.{row['id']}", "sentiment_label": "is.null"},
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
    symbols = _source_symbols(supabase, watchlist)
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


def _source_symbols(supabase: SupabaseClient, watchlist: Watchlist) -> list[str]:
    mentioned = supabase.select("mentioned_tickers", columns="symbol", order="symbol.asc")
    return sorted({*watchlist.symbols, *(str(row["symbol"]) for row in mentioned)})


def load_options(
    supabase: SupabaseClient,
    download: OptionsDownloader,
    watchlist: Watchlist,
    start: date,
) -> tuple[int, int, list[str]]:
    """Store daily options metrics for every watchlist symbol and every ticker mentioned in a stored passage."""
    symbols = _source_symbols(supabase, watchlist)
    result = download(symbols, start)
    supabase.upsert(
        "options_daily",
        [
            {
                "symbol": day.symbol,
                "trade_date": day.trade_date.isoformat(),
                **day.metrics,
                "source_updated_at": day.source_updated_at.isoformat() if day.source_updated_at else None,
            }
            for day in result.days
        ],
        on_conflict="symbol,trade_date",
    )
    return len(symbols), len(result.days), result.warnings


def load_news(
    supabase: SupabaseClient,
    fetch: NewsFetcher,
    watchlist: Watchlist,
    day: date,
) -> tuple[int, int, int]:
    """Store one news digest per watchlist symbol for `day`, skipping symbols that already have one.

    Returns how many digests were fetched, how many symbols were skipped, and how many news
    items were stored. Each digest is saved as soon as it arrives, so a run that stops partway
    does not pay for the same digest again.
    """
    digest_ids = {symbol: f"{PERPLEXITY_SOURCE}:{symbol}:{day.isoformat()}" for symbol in watchlist.symbols}
    stored = {
        str(row["id"])
        for row in supabase.select(
            "newsletters",
            columns="id",
            order="id.asc",
            filters={"source": f"eq.{PERPLEXITY_SOURCE}", "id": f"like.*:{day.isoformat()}"},
        )
    }
    fetched = items = 0
    for symbol, digest_id in digest_ids.items():
        if digest_id in stored:
            continue
        digest = fetch(symbol, watchlist.names(symbol))
        body_text = "\n".join(digest.items)
        chunks = passage_chunks(body_text)
        supabase.upsert(
            "newsletters",
            [
                {
                    "id": digest_id,
                    "source": PERPLEXITY_SOURCE,
                    "sender": PERPLEXITY_SOURCE,
                    "publication": digest.model,
                    "subject": f"{symbol} news digest {day.isoformat()}",
                    "received_at": digest.fetched_at.isoformat(),
                    "body_text": body_text,
                }
            ],
            on_conflict="id",
            ignore_duplicates=True,
        )
        supabase.upsert(
            "newsletter_passages",
            [
                {"newsletter_id": digest_id, "passage_index": index, "passage_text": chunk, "tickers": [symbol]}
                for index, chunk in enumerate(chunks)
            ],
            on_conflict="newsletter_id,passage_index",
        )
        fetched += 1
        items += len(chunks)
    return fetched, len(digest_ids) - fetched, items
