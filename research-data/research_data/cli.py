from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import sys
from typing import Iterator, Mapping
from zoneinfo import ZoneInfo

from multi_llm.client import ChatRequest, LLMError, complete

from research_data.adjustments import FactorSnapshot, read_factor_files
from research_data.alphavantage import Article, article_pages
from research_data.benzinga import Headline, headline_pages
from research_data.boosted_trees import MODEL, PARAMETERS, fit
from research_data.http import LoaderError
from research_data.local_finbert import LocalFinbert
from research_data.local_whisper import LocalWhisper, whisper_installed
from research_data.market_sentiment import fetch_news_sentiment, openbb_installed
from research_data.model import describe, train_models
from research_data.news import NewsDigest, fetch_digest
from research_data.newsletters import fetch_newsletters
from research_data.options import OptionsDay, download_day
from research_data.pipeline import (
    Sentiment,
    load_adjustments,
    load_alphavantage,
    load_benzinga,
    load_market_sentiment,
    load_news,
    load_newsletters,
    load_options,
    load_podcasts,
    load_prices,
    score_passages,
)
from research_data.podcasts import Episode, FeedLine, Transcript, episode_transcript, read_feed, read_feed_list
from research_data.prices import PriceDownload, download_daily_bars
from research_data.supabase import SupabaseClient
from research_data.tickers import Watchlist

DEFAULT_WATCHLIST = Path(__file__).resolve().parent.parent / "watchlist.txt"
DEFAULT_PODCASTS = Path(__file__).resolve().parent.parent / "podcasts.txt"
_DEFAULT_LOOKBACK_DAYS = 7
_MARKET_TIME_ZONE = ZoneInfo("America/New_York")


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise LoaderError(f"Missing {name}")
    return value


def _read_watchlist(path: Path) -> Watchlist:
    try:
        return Watchlist.parse(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LoaderError(f"Cannot read watchlist {path}: {exc.strerror}") from None


def _read_podcast_feeds(path: Path) -> list[FeedLine]:
    try:
        return read_feed_list(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LoaderError(f"Cannot read podcast list {path}: {exc.strerror}") from None


def _hosted_finbert(text: str) -> Sentiment:
    try:
        response = complete(ChatRequest(provider="finbert", prompt=text))
    except LLMError as exc:
        raise LoaderError(str(exc)) from None
    scores = {str(item["label"]).lower(): float(item["score"]) for item in response.raw["labels"]}
    return Sentiment(response.model, scores)


def _run_newsletters(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    messages = fetch_newsletters(
        _required(env, "GMAIL_ADDRESS"),
        _required(env, "GMAIL_APP_PASSWORD"),
        args.label,
        args.since,
    )
    newsletters, passages = load_newsletters(supabase, messages, _read_watchlist(args.watchlist))
    print(f"newsletters: read {newsletters} messages since {args.since}, {passages} passages mention a ticker")


def _run_score(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    score = LocalFinbert() if args.finbert == "local" else _hosted_finbert
    scored = score_passages(supabase, score, args.limit)
    print(f"score: scored {scored} passages with {args.finbert} FinBERT")


def _run_prices(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    api_key = _required(env, "DATABENTO_API_KEY")

    def download(symbols: list[str], start: date) -> PriceDownload:
        return download_daily_bars(api_key, symbols, start)

    symbols, bars, warnings = load_prices(supabase, download, _read_watchlist(args.watchlist), args.start)
    print(f"prices: stored {bars} daily bars for {symbols} symbols since {args.start}")
    for warning in warnings:
        print(f"prices: warning: {warning}", file=sys.stderr)


def _run_news(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    api_key = _required(env, "PERPLEXITY_API_KEY")

    def fetch(symbol: str, names: list[str]) -> NewsDigest:
        return fetch_digest(api_key, symbol, names)

    day = datetime.now(_MARKET_TIME_ZONE).date()
    fetched, skipped, items = load_news(supabase, fetch, _read_watchlist(args.watchlist), day)
    print(f"news: fetched {fetched} digests for {day} and stored {items} news items, {skipped} symbols already had one")


def _run_options(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    token = _required(env, "ORATS_API_TOKEN")

    def download(symbols: list[str], trade_date: date) -> list[OptionsDay]:
        return download_day(token, symbols, trade_date)

    today = datetime.now(_MARKET_TIME_ZONE).date()
    symbols, days, warnings = load_options(supabase, download, _read_watchlist(args.watchlist), args.start, today)
    print(f"options: stored {days} daily rows for {symbols} symbols since {args.start}")
    for warning in warnings:
        print(f"options: warning: {warning}", file=sys.stderr)


def _run_adjustments(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    lean_data = Path(_required(env, "QUANTCONNECT_LEAN_DATA_DIR")).expanduser()

    def read(symbols: list[str]) -> FactorSnapshot:
        return read_factor_files(lean_data, symbols)

    symbols, stored, as_of, warnings = load_adjustments(supabase, read, _read_watchlist(args.watchlist))
    print(
        f"adjustments: stored split and dividend factors for {stored} of {symbols} symbols "
        f"from QuantConnect factor files dated {as_of}"
    )
    for warning in warnings:
        print(f"adjustments: warning: {warning}", file=sys.stderr)


def _run_benzinga(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    api_key = _required(env, "BENZINGA_API_KEY")

    def pages(symbol: str, since: date) -> Iterator[list[Headline]]:
        return headline_pages(api_key, symbol, since)

    symbols, headlines, warnings = load_benzinga(supabase, pages, _read_watchlist(args.watchlist), args.since)
    print(f"benzinga: read {headlines} headlines for {symbols} symbols since {args.since}")
    for warning in warnings:
        print(f"benzinga: warning: {warning}", file=sys.stderr)


def _run_alphavantage(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    api_key = _required(env, "ALPHAVANTAGE_API_KEY")

    def pages(symbol: str, since: date) -> Iterator[list[Article]]:
        return article_pages(api_key, symbol, since)

    symbols, articles, warnings = load_alphavantage(supabase, pages, _read_watchlist(args.watchlist), args.since)
    print(f"alphavantage: read {articles} scored articles for {symbols} symbols since {args.since}")
    for warning in warnings:
        print(f"alphavantage: warning: {warning}", file=sys.stderr)


def _run_podcasts(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    feeds = _read_podcast_feeds(args.podcasts)
    if not feeds:
        raise LoaderError(f"{args.podcasts} lists no podcast feeds")
    whisper: list[LocalWhisper] = []

    def transcribe(audio: Path) -> Transcript:
        if not whisper:
            whisper.append(LocalWhisper())
        return whisper[0](audio)

    def transcript_of(episode: Episode) -> Transcript | None:
        def announced(audio: Path) -> Transcript:
            print(f"podcasts: transcribing {episode.show}: {episode.title}", file=sys.stderr)
            return transcribe(audio)

        return episode_transcript(episode, announced if whisper_installed() else None)

    loaded = load_podcasts(supabase, feeds, read_feed, transcript_of, _read_watchlist(args.watchlist), args.since)
    print(
        f"podcasts: read {len(feeds) - len(loaded.failed_feeds)} of {len(feeds)} feeds, stored {loaded.added} "
        f"new episodes; {loaded.passages} passages mention a ticker"
    )
    for warning in loaded.warnings:
        print(f"podcasts: warning: {warning}", file=sys.stderr)
    if loaded.failed_feeds:
        raise LoaderError("Podcast feeds that could not be read: " + "; ".join(loaded.failed_feeds))


def _run_openbb(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    added, newest = load_market_sentiment(supabase, fetch_news_sentiment)
    through = f", stored through {newest}" if newest else ""
    print(f"openbb: added {added} days of the San Francisco Fed news sentiment index{through}")


def _run_train(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> None:
    for training in train_models(supabase, fit, MODEL, PARAMETERS):
        for line in describe(training):
            print(f"train: {line}")


# In the order `all` runs them: text sources before scoring, and the sources that
# follow mentioned tickers after the newsletters that mention them.
_STEPS = {
    "newsletters": _run_newsletters,
    "news": _run_news,
    "benzinga": _run_benzinga,
    "alphavantage": _run_alphavantage,
    "podcasts": _run_podcasts,
    "score": _run_score,
    "prices": _run_prices,
    "options": _run_options,
    "adjustments": _run_adjustments,
    "openbb": _run_openbb,
}
# `all` leaves these out when their setting is missing, so the original three steps keep running without them.
_OPTIONAL_STEP_SETTINGS = {
    "news": "PERPLEXITY_API_KEY",
    "benzinga": "BENZINGA_API_KEY",
    "alphavantage": "ALPHAVANTAGE_API_KEY",
    "options": "ORATS_API_TOKEN",
    "adjustments": "QUANTCONNECT_LEAN_DATA_DIR",
}


# Not part of `all`, which only loads.
_TRAIN = "train"
# Every step of `all` and then `train`, going on past a step that fails.
_DAILY = "daily"


def _skip_reason(name: str, env: Mapping[str, str], args: argparse.Namespace) -> str | None:
    """Why `all` leaves the step out, or None when it runs."""
    setting = _OPTIONAL_STEP_SETTINGS.get(name)
    if setting and not env.get(setting, "").strip():
        return f"{setting} is not set"
    if name == "podcasts" and not (args.podcasts.is_file() and _read_podcast_feeds(args.podcasts)):
        return f"{args.podcasts} lists no podcast feeds"
    if name == "openbb" and not openbb_installed():
        return "OpenBB is not installed"
    return None


def build_parser() -> argparse.ArgumentParser:
    lookback = date.today() - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
    parser = argparse.ArgumentParser(
        prog="research-data",
        description="Load newsletters, news digests, Benzinga headlines, Alpha Vantage news scores, podcast "
        "transcripts, FinBERT sentiment, daily prices, options metrics, split and dividend factors and a market "
        "news sentiment index into Supabase, and retrain the return model on them.",
    )
    parser.add_argument(
        "step",
        choices=[*_STEPS, "all", _TRAIN, _DAILY],
        help="newsletters: Gmail label to passages; news: today's Perplexity news digest per watchlist symbol; "
        "benzinga: Benzinga headlines per symbol; alphavantage: Alpha Vantage's scored news per symbol; "
        "podcasts: transcripts of the episodes in the listed podcast feeds; score: FinBERT on unscored "
        "passages; prices: Databento daily bars; options: ORATS daily options metrics; adjustments: split and dividend factors from QuantConnect factor files on this machine; "
        "openbb: the San Francisco Fed news sentiment index through OpenBB; all: every step in that order, "
        "leaving out news, benzinga, alphavantage, options and adjustments when their setting is missing, "
        "podcasts when no feeds are listed and openbb when OpenBB is not installed; train: retrain the return "
        "model on training_rows, store how it scored on days it was not trained on and its predictions for the "
        "newest session, which needs `poetry install --extras train`; daily: all and then train, going on to "
        "the next step when one fails",
    )
    parser.add_argument("--label", default="Substack", help="Gmail label to read (default: Substack)")
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        default=lookback,
        help="Read newsletters received and Benzinga news, Alpha Vantage news and podcast episodes published on "
        "or after this date "
        f"(default: {_DEFAULT_LOOKBACK_DAYS} days ago)",
    )
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=lookback,
        help=f"Load prices and options metrics from this date (default: {_DEFAULT_LOOKBACK_DAYS} days ago)",
    )
    parser.add_argument("--limit", type=int, default=1000, help="Most passages to score in one run (default: 1000)")
    parser.add_argument(
        "--finbert",
        choices=["hosted", "local"],
        default="hosted",
        help="Where FinBERT runs: hosted on Hugging Face Inference, which needs HF_TOKEN, or local on this "
        "machine, which needs `poetry install --extras local` and no token (default: hosted)",
    )
    parser.add_argument(
        "--watchlist",
        type=Path,
        default=DEFAULT_WATCHLIST,
        help="Symbols and company names to look for (default: watchlist.txt next to the package)",
    )
    parser.add_argument(
        "--podcasts",
        type=Path,
        default=DEFAULT_PODCASTS,
        help="Podcast feed addresses, one per line (default: podcasts.txt next to the package)",
    )
    return parser


def _run_daily(args: argparse.Namespace, env: Mapping[str, str], supabase: SupabaseClient) -> int:
    """One failed source should not cost the day's other sources, or the retrain on what did load."""
    failed: list[str] = []
    for name, run in {**_STEPS, _TRAIN: _run_train}.items():
        try:
            reason = _skip_reason(name, env, args)
            if reason:
                print(f"{name}: skipped because {reason}", file=sys.stderr)
                continue
            run(args, env, supabase)
        except LoaderError as exc:
            print(f"{name}: failed: {exc}", file=sys.stderr)
            failed.append(name)
    if failed:
        print(f"research-data: failed steps: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = os.environ
    try:
        supabase = SupabaseClient.from_env(env)
        if args.step == _DAILY:
            return _run_daily(args, env, supabase)
        if args.step == _TRAIN:
            _run_train(args, env, supabase)
            return 0
        for name in _STEPS if args.step == "all" else [args.step]:
            reason = _skip_reason(name, env, args) if args.step == "all" else None
            if reason:
                print(f"{name}: skipped because {reason}", file=sys.stderr)
                continue
            _STEPS[name](args, env, supabase)
    except LoaderError as exc:
        print(f"research-data: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
