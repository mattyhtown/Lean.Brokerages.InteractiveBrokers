from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import ssl
import threading
import time
from types import SimpleNamespace
from typing import BinaryIO, Iterator, Mapping
import urllib.parse
import zipfile

import pytest

from research_data import cli, local_finbert, local_whisper, market_sentiment, newsletters
from research_data.adjustments import FactorRow, FactorSnapshot, parse_factor_file, read_factor_files
from research_data.alphavantage import Article, SymbolRejected, article_pages, parse_articles
from research_data.benzinga import Headline, headline_pages, parse_headlines
from research_data.cli import main as cli_main
from research_data.http import HttpResponse, HttpStatusError, LoaderError, fetch_public, require_secure_url
from research_data.local_finbert import LocalFinbert
from research_data.local_whisper import LocalWhisper
from research_data.market_sentiment import SentimentReading, fetch_news_sentiment
from research_data.news import NewsDigest, digest_items, fetch_digest
from research_data.newsletters import (
    MAX_PASSAGE_CHARS,
    Newsletter,
    clean_text,
    fetch_newsletters,
    parse_message,
    ticker_passages,
)
from research_data.options import OptionsDay, download_day, parse_cores
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
from research_data.podcasts import (
    MAX_AUDIO_BYTES,
    MAX_FEED_BYTES,
    MAX_TRANSCRIPT_BYTES,
    Episode,
    EpisodeUnavailable,
    Feed,
    FeedLine,
    Transcript,
    episode_passages,
    episode_transcript,
    parse_feed,
    read_feed,
    read_feed_list,
    transcript_text,
)
from research_data.prices import DailyBar, PriceDownload, download_daily_bars, parse_bars
from research_data.supabase import SupabaseClient, in_filter
from research_data.tickers import Watchlist, extract_tickers

WATCHLIST = Watchlist.parse("AAPL: Apple\nGOOGL: Alphabet, Google\nNVDA\n")

# Shaped like a real Substack post email: quoted-printable plain text with one
# paragraph per line and links in square brackets, plus an HTML alternative.
SUBSTACK_EMAIL = (
    b"Delivered-To: reader@example.com\r\n"
    b"Mime-Version: 1.0\r\n"
    b'Content-Type: multipart/alternative; boundary="b1"\r\n'
    b"Subject: Who Gets Rich from AI?\r\n"
    b'From: "Stephen, Behind the Sheet " <behindthesheet@substack.com>\r\n'
    b"Date: Sun, 15 Nov 2026 09:47:29 +0000\r\n"
    b"List-Id: <behindthesheet.substack.com>\r\n"
    b"\r\n"
    b"--b1\r\n"
    b'Content-Type: text/plain; charset="utf-8"\r\n'
    b"Content-Transfer-Encoding: quoted-printable\r\n"
    b"\r\n"
    b"View this post on the web at https://behindthesheet.substack.com/p/w=\r\n"
    b"ho-gets-rich\r\n"
    b"\r\n"
    b"The Paradox\r\n"
    b"That value was mainly captured by Apple and Google, and it wasn=E2=80=99t =\r\n"
    b"obvious at the time.\r\n"
    b"This month=E2=80=99s podcast  [ https://substack.com/redirect=\r\n"
    b"/d73e2afe?j=3DNVDA ]covers chips.\r\n"
    b"--b1\r\n"
    b'Content-Type: text/html; charset="utf-8"\r\n'
    b"\r\n"
    b"<html><body><p>ignored because plain text is preferred</p></body></html>\r\n"
    b"--b1--\r\n"
)


def test_tickers_come_from_cashtags_exchange_prefixes_and_the_watchlist() -> None:
    text = "Apple's margin beat. $TSLA slid, NVDA rallied and (NYSE: BRK.B) held. Alphabet too."
    assert extract_tickers(text, WATCHLIST) == ["AAPL", "BRK.B", "GOOGL", "NVDA", "TSLA"]


@pytest.mark.parametrize(
    "text",
    [
        "It cost $5 and US$AAPL is not a cashtag.",
        "Pineapple and Googled are other words; apple is lower case.",
        "MSFT is not on the watchlist and has no cashtag.",
        "NVDAX is a longer word.",
    ],
)
def test_lookalikes_are_not_tickers(text: str) -> None:
    assert extract_tickers(text, WATCHLIST) == []


def test_watchlist_rejects_bad_symbols_and_names_listed_twice() -> None:
    with pytest.raises(LoaderError, match="line 2: 'apple' is not a ticker symbol"):
        Watchlist.parse("AAPL\napple: Apple\n")
    with pytest.raises(LoaderError, match="line 2: 'Alphabet' is already listed for GOOGL"):
        Watchlist.parse("GOOGL: Alphabet\nGOOG: Alphabet\n")
    assert Watchlist.parse("# only a comment\n\n").pattern is None


def test_substack_email_becomes_clean_paragraphs() -> None:
    received = datetime(2026, 11, 15, 9, 47, 47, tzinfo=timezone.utc)
    newsletter = parse_message(0x1A1DE9B11514BB79, received, SUBSTACK_EMAIL)
    assert newsletter == Newsletter(
        id="1a1de9b11514bb79",
        sender="behindthesheet@substack.com",
        publication="behindthesheet.substack.com",
        subject="Who Gets Rich from AI?",
        received_at=received,
        body_text=(
            "View this post on the web at\n"
            "The Paradox\n"
            "That value was mainly captured by Apple and Google, and it wasn\u2019t obvious at the time.\n"
            "This month\u2019s podcast covers chips."
        ),
    )
    passages = ticker_passages(newsletter.body_text, WATCHLIST)
    assert [(p.index, p.tickers) for p in passages] == [(2, ["AAPL", "GOOGL"])]


def test_html_only_email_is_reduced_to_text() -> None:
    raw = (
        b"Subject: Hi\r\nFrom: a@substack.com\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><head><style>p{color:red}</style></head><body><p>Nvidia beat.</p><p>$AMD lagged.</p></body></html>"
    )
    newsletter = parse_message(1, datetime(2026, 1, 1, tzinfo=timezone.utc), raw)
    assert newsletter.body_text == "Nvidia beat.\n$AMD lagged."
    assert newsletter.publication is None


def test_clean_text_drops_invisible_padding() -> None:
    assert clean_text("Preview \u034f \u034f\u00ad\u200b\n\n  two   spaces\u00a0here \n") == "Preview\ntwo spaces here"


def test_long_paragraphs_split_at_sentence_ends_within_the_finbert_limit() -> None:
    sentence = "Apple grew revenue again this quarter and guided higher. "
    paragraph = (sentence * 40).strip() + " " + "x" * 2000
    passages = ticker_passages(paragraph, WATCHLIST)
    chunks = [p.text for p in passages]
    assert len(chunks) > 1
    assert all(len(chunk) <= MAX_PASSAGE_CHARS for chunk in chunks)
    assert all(chunk.endswith(".") for chunk in chunks)
    assert "".join(chunks).count("Apple") == 40


class FakeImap:
    def __init__(self, messages: dict[str, list[object]], select_status: str = "OK") -> None:
        self.messages = messages
        self.select_status = select_status
        self.calls: list[tuple[object, ...]] = []

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user, password))

    def select(self, mailbox: str, readonly: bool) -> tuple[str, list[object]]:
        self.calls.append(("select", mailbox, readonly))
        return self.select_status, [b"2"]

    def uid(self, command: str, *args: str) -> tuple[str, list[object]]:
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [" ".join(self.messages).encode("ascii")]
        return "OK", self.messages[args[0]]

    def logout(self) -> None:
        self.calls.append(("logout",))


def test_fetch_reads_the_label_read_only_and_keeps_gmail_ids_and_arrival_times() -> None:
    fake = FakeImap(
        {
            "7": [
                (
                    b'1 (X-GM-MSGID 1881917166082898809 UID 7 INTERNALDATE "15-Nov-2026 01:47:47 -0800" BODY[] {12}',
                    SUBSTACK_EMAIL,
                ),
                b")",
            ],
            # Gmail may return attributes after the message literal.
            "9": [
                (b"2 (UID 9 BODY[] {12}", SUBSTACK_EMAIL),
                b' X-GM-MSGID 255 INTERNALDATE "16-Nov-2026 00:00:00 +0000")',
            ],
        }
    )
    newsletters = list(
        fetch_newsletters("me@gmail.com", "app-pw", 'My "Stack"', date(2026, 11, 8), connect=lambda host: fake)
    )
    assert [(n.id, n.received_at) for n in newsletters] == [
        ("1a1de9b11514bb79", datetime(2026, 11, 15, 9, 47, 47, tzinfo=timezone.utc)),
        ("ff", datetime(2026, 11, 16, tzinfo=timezone.utc)),
    ]
    assert fake.calls == [
        ("login", "me@gmail.com", "app-pw"),
        ("select", '"My \\"Stack\\""', True),
        ("SEARCH", "SINCE", "08-Nov-2026"),
        ("FETCH", "7", "(X-GM-MSGID INTERNALDATE BODY.PEEK[])"),
        ("FETCH", "9", "(X-GM-MSGID INTERNALDATE BODY.PEEK[])"),
        ("logout",),
    ]


def test_fetch_refuses_a_label_that_could_carry_a_second_imap_command() -> None:
    fake = FakeImap({})
    with pytest.raises(LoaderError, match="contains a control character"):
        list(fetch_newsletters("me@gmail.com", "pw", "Substack\r\nA1 DELETE INBOX", date(2026, 1, 1), lambda host: fake))
    assert fake.calls == []


def test_fetch_reports_a_stalled_connection_and_still_logs_out() -> None:
    class Stalled(FakeImap):
        def uid(self, command: str, *args: str) -> tuple[str, list[object]]:
            raise TimeoutError("timed out")

    fake = Stalled({})
    with pytest.raises(LoaderError, match="Gmail IMAP connection failed: timed out"):
        list(fetch_newsletters("me@gmail.com", "pw", "Substack", date(2026, 1, 1), connect=lambda host: fake))
    assert fake.calls[-1] == ("logout",)


def test_gmail_is_reached_with_a_verified_certificate_and_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[tuple[str, ssl.SSLContext, float]] = []

    def imap4_ssl(host: str, *, ssl_context: ssl.SSLContext, timeout: float) -> FakeImap:
        opened.append((host, ssl_context, timeout))
        return FakeImap({})

    monkeypatch.setattr(newsletters.imaplib, "IMAP4_SSL", imap4_ssl)
    assert list(fetch_newsletters("me@gmail.com", "pw", "Substack", date(2026, 1, 1))) == []
    ((host, context, timeout),) = opened
    assert host == "imap.gmail.com"
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert timeout > 0


def test_fetch_reports_a_missing_label_and_still_logs_out() -> None:
    fake = FakeImap({}, select_status="NO")
    with pytest.raises(LoaderError, match="Could not open Gmail label 'Substack'"):
        list(fetch_newsletters("me@gmail.com", "pw", "Substack", date(2026, 1, 1), connect=lambda host: fake))
    assert fake.calls[-1] == ("logout",)


def test_fetch_rejects_a_response_without_a_gmail_id() -> None:
    fake = FakeImap({"3": [(b'1 (UID 3 INTERNALDATE "15-Nov-2026 01:47:47 -0800" BODY[] {1}', b"x"), b")"]})
    with pytest.raises(LoaderError, match="Unexpected IMAP FETCH response for message UID 3"):
        list(fetch_newsletters("me@gmail.com", "pw", "Substack", date(2026, 1, 1), connect=lambda host: fake))


# The record shape Databento documents for ohlcv-1d in JSON encoding.
BARS = (
    b'{"hd":{"ts_event":"1692576000000000000","rtype":35,"publisher_id":2,"instrument_id":523},'
    b'"open":"133550000000","high":"135200000000","low":"132710000000","close":"134360000000",'
    b'"volume":"11015261","symbol":"AMZN"}\n'
    b'{"hd":{"ts_event":"1692662400000000000","rtype":35,"publisher_id":2,"instrument_id":7290},'
    b'"open":"439090000000","high":"472900000000","low":"437260000000","close":"470990000000",'
    b'"volume":"11098972","symbol":"NVDA"}\n'
)


def test_bars_are_parsed_with_exact_prices_and_utc_trade_dates() -> None:
    bars, dropped = parse_bars(BARS)
    assert dropped == 0
    assert bars == [
        DailyBar(
            "AMZN",
            date(2023, 8, 21),
            Decimal("133.55"),
            Decimal("135.2"),
            Decimal("132.71"),
            Decimal("134.36"),
            11015261,
        ),
        DailyBar(
            "NVDA",
            date(2023, 8, 22),
            Decimal("439.09"),
            Decimal("472.9"),
            Decimal("437.26"),
            Decimal("470.99"),
            11098972,
        ),
    ]


def test_bars_without_a_usable_price_are_dropped_and_counted() -> None:
    undefined = BARS.splitlines()[0].replace(b'"close":"134360000000"', b'"close":"9223372036854775807"')
    zero = BARS.splitlines()[1].replace(b'"close":"470990000000"', b'"close":"0"')
    assert parse_bars(undefined + b"\n" + zero + b"\n") == ([], 2)


def test_bars_in_an_unexpected_shape_are_an_error() -> None:
    with pytest.raises(LoaderError, match="Unexpected Databento ohlcv-1d record"):
        parse_bars(b'{"open":"1","high":"1","low":"1","close":"1","volume":"1"}\n')


def test_a_fractional_raw_price_is_an_error_instead_of_being_cut_to_a_whole_number() -> None:
    with pytest.raises(LoaderError, match="Unexpected Databento ohlcv-1d record"):
        parse_bars(BARS.replace(b'"close":"134360000000"', b'"close":134360000000.9'))


class FakeHttp:
    def __init__(self, responses: list[HttpResponse | LoaderError]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout_seconds: int
    ) -> HttpResponse:
        self.calls.append((method, url, dict(headers), body))
        response = self.responses.pop(0)
        if isinstance(response, LoaderError):
            raise response
        return response


RANGE = json.dumps(
    {
        "start": "2018-05-01T00:00:00.000000000Z",
        "end": "2026-11-14T00:00:00.000000000Z",
        "schema": {"ohlcv-1d": {"start": "2018-05-01T00:00:00.000000000Z", "end": "2026-11-13T00:00:00.000000000Z"}},
    }
).encode("utf-8")


def test_download_asks_for_bars_up_to_the_latest_published_day_and_passes_on_warnings() -> None:
    http = FakeHttp(
        [
            HttpResponse(200, RANGE),
            HttpResponse(206, BARS, {"x-warning": '["Warning: 1 symbol(s) did not resolve: ZZZZ"]'}),
        ]
    )
    result = download_daily_bars("db-key", ["AMZN", "NVDA", "ZZZZ"], date(2026, 11, 1), http=http)
    assert [bar.symbol for bar in result.bars] == ["AMZN", "NVDA"]
    assert result.warnings == ["Warning: 1 symbol(s) did not resolve: ZZZZ"]
    (range_method, range_url, range_headers, _), (method, url, headers, body) = http.calls
    assert (range_method, range_url) == (
        "GET",
        "https://hist.databento.com/v0/metadata.get_dataset_range?dataset=EQUS.SUMMARY",
    )
    # HTTP basic auth with the key as the user name and an empty password.
    assert range_headers["Authorization"] == headers["Authorization"] == "Basic ZGIta2V5Og=="
    assert (method, url) == ("POST", "https://hist.databento.com/v0/timeseries.get_range")
    assert dict(urllib.parse.parse_qsl((body or b"").decode("ascii"))) == {
        "dataset": "EQUS.SUMMARY",
        "schema": "ohlcv-1d",
        "symbols": "AMZN,NVDA,ZZZZ",
        "start": "2026-11-01",
        "end": "2026-11-13T00:00:00.000000000Z",
        "encoding": "json",
        "map_symbols": "true",
    }


def test_download_makes_no_data_request_when_nothing_new_is_published() -> None:
    http = FakeHttp([HttpResponse(200, RANGE)])
    assert download_daily_bars("db-key", ["AMZN"], date(2026, 11, 13), http=http) == PriceDownload([], [])
    assert len(http.calls) == 1


def test_download_rejects_an_unreadable_range() -> None:
    with pytest.raises(LoaderError, match="Unexpected Databento dataset range response"):
        download_daily_bars("db-key", ["AMZN"], date(2026, 11, 1), http=FakeHttp([HttpResponse(200, b'{"end": 5}')]))


def supabase_with(http: FakeHttp) -> SupabaseClient:
    return SupabaseClient("https://proj.supabase.co", "secret-key", http)


def test_supabase_client_needs_a_url_and_a_secret_key() -> None:
    with pytest.raises(LoaderError, match="Missing SUPABASE_URL"):
        SupabaseClient.from_env({})
    with pytest.raises(LoaderError, match="set SUPABASE_SERVICE_ROLE_KEY or SUPABASE_SECRET_KEY"):
        SupabaseClient.from_env({"SUPABASE_URL": "https://proj.supabase.co"})
    with pytest.raises(LoaderError, match="Refusing non-HTTPS URL"):
        SupabaseClient.from_env({"SUPABASE_URL": "http://proj.supabase.co", "SUPABASE_SECRET_KEY": "k"})
    client = SupabaseClient.from_env({"SUPABASE_URL": "https://proj.supabase.co/", "SUPABASE_SECRET_KEY": " k "})
    assert (client.url, client.key) == ("https://proj.supabase.co", "k")
    assert require_secure_url("http://127.0.0.1:3000") == "http://127.0.0.1:3000"
    with pytest.raises(LoaderError, match="user name"):
        require_secure_url("https://proj.supabase.co@other.example")


def test_supabase_upsert_sends_the_key_the_conflict_target_and_the_resolution() -> None:
    http = FakeHttp([HttpResponse(201, b""), HttpResponse(201, b"")])
    client = supabase_with(http)
    client.upsert("daily_prices", [{"symbol": "A"}], on_conflict="symbol,trade_date")
    client.upsert("newsletters", [{"id": "1"}], on_conflict="id", ignore_duplicates=True)
    (method, url, headers, body), (_, second_url, second_headers, _) = http.calls
    assert (method, url) == ("POST", "https://proj.supabase.co/rest/v1/daily_prices?on_conflict=symbol%2Ctrade_date")
    assert headers["apikey"] == "secret-key"
    assert headers["Authorization"] == "Bearer secret-key"
    assert headers["Prefer"] == "resolution=merge-duplicates,return=minimal"
    assert json.loads(body or b"") == [{"symbol": "A"}]
    assert second_url == "https://proj.supabase.co/rest/v1/newsletters?on_conflict=id"
    assert second_headers["Prefer"] == "resolution=ignore-duplicates,return=minimal"


def test_supabase_writes_nothing_for_no_rows_and_refuses_an_unfiltered_update() -> None:
    http = FakeHttp([])
    client = supabase_with(http)
    client.upsert("daily_prices", [], on_conflict="symbol,trade_date")
    with pytest.raises(LoaderError, match="Refusing to update every row of newsletter_passages"):
        client.update("newsletter_passages", {}, {"sentiment_label": "positive"})
    assert http.calls == []


def test_supabase_select_pages_until_a_short_page() -> None:
    full_page = json.dumps([{"symbol": "A"}] * 1000).encode("utf-8")
    http = FakeHttp([HttpResponse(200, full_page), HttpResponse(200, b'[{"symbol": "B"}]')])
    rows = supabase_with(http).select("mentioned_tickers", columns="symbol", order="symbol.asc")
    assert len(rows) == 1001
    offsets = [urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["offset"] for _, url, _, _ in http.calls]
    assert offsets == [["0"], ["1000"]]


class FakeSupabase:
    """Records writes and serves canned reads in place of the Data API."""

    def __init__(self, reads: dict[str, list[list[dict[str, object]]]] | None = None) -> None:
        self.reads = reads or {}
        self.upserts: list[tuple[str, list[dict[str, object]], str, bool]] = []
        self.updates: list[tuple[str, dict[str, str], dict[str, object]]] = []
        self.selects: list[tuple[str, dict[str, str] | None]] = []

    def select(self, table: str, columns: str, order: str, filters=None, limit=None) -> list[dict[str, object]]:
        self.selects.append((table, filters))
        pages = self.reads.get(table, [])
        return pages.pop(0)[:limit] if pages else []

    def upsert(self, table: str, rows: list[dict[str, object]], on_conflict: str, ignore_duplicates: bool = False):
        self.upserts.append((table, list(rows), on_conflict, ignore_duplicates))

    def update(self, table: str, filters: dict[str, str], values: dict[str, object]) -> None:
        self.updates.append((table, filters, values))


def test_newsletters_are_stored_once_and_passages_refresh_their_tickers() -> None:
    received = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)
    newsletter = Newsletter("ab", "a@substack.com", "a.substack.com", "Subject", received, "No names here.\nApple won.")
    supabase = FakeSupabase()
    assert load_newsletters(supabase, [newsletter], WATCHLIST) == (1, 1)
    newsletters, passages = supabase.upserts
    assert newsletters == (
        "newsletters",
        [
            {
                "id": "ab",
                "sender": "a@substack.com",
                "publication": "a.substack.com",
                "subject": "Subject",
                "received_at": "2026-11-10T20:00:00+00:00",
                "body_text": "No names here.\nApple won.",
            }
        ],
        "id",
        True,
    )
    assert passages == (
        "newsletter_passages",
        [{"newsletter_id": "ab", "passage_index": 1, "passage_text": "Apple won.", "tickers": ["AAPL"]}],
        "newsletter_id,passage_index",
        False,
    )


def test_passages_that_no_longer_mention_a_ticker_stop_counting_but_keep_their_score() -> None:
    received = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)
    newsletter = Newsletter("ab", "a@substack.com", None, "Subject", received, "Tesla slid.\nApple won.")
    stored = [
        {"id": 7, "newsletter_id": "ab", "passage_index": 0},
        {"id": 8, "newsletter_id": "ab", "passage_index": 1},
    ]
    supabase = FakeSupabase({"newsletter_passages": [stored]})
    load_newsletters(supabase, [newsletter], WATCHLIST)
    assert supabase.selects == [("newsletter_passages", {"newsletter_id": 'in.("ab")', "tickers": "neq.{}"})]
    assert supabase.updates == [("newsletter_passages", {"id": 'in.("7")'}, {"tickers": []})]


def test_in_filter_quotes_values_so_punctuation_in_them_is_kept() -> None:
    assert in_filter(["perplexity:BRK.B:2026-11-10", 'a"b', 7]) == 'in.("perplexity:BRK.B:2026-11-10","a\\"b","7")'


def test_scoring_saves_each_passage_and_stops_at_the_limit() -> None:
    supabase = FakeSupabase(
        {"newsletter_passages": [[{"id": 4, "passage_text": "Apple won."}, {"id": 5, "passage_text": "Apple lost."}]]}
    )

    def score(text: str) -> Sentiment:
        positive = 0.9 if "won" in text else 0.1
        return Sentiment("ProsusAI/finbert", {"positive": positive, "neutral": 0.05, "negative": 0.95 - positive})

    assert score_passages(supabase, score, limit=1) == 1
    assert supabase.selects == [("newsletter_passages", {"sentiment_label": "is.null"})]
    ((table, filters, values),) = supabase.updates
    assert (table, filters) == ("newsletter_passages", {"id": "eq.4", "sentiment_label": "is.null"})
    assert values["sentiment_label"] == "positive"
    assert (values["sentiment_positive"], values["sentiment_neutral"]) == (0.9, 0.05)
    assert values["sentiment_negative"] == pytest.approx(0.05)
    assert values["sentiment_model"] == "ProsusAI/finbert"


def test_scoring_rejects_a_model_without_the_three_finbert_labels() -> None:
    supabase = FakeSupabase({"newsletter_passages": [[{"id": 4, "passage_text": "Apple won."}]]})
    with pytest.raises(LoaderError, match="other/model returned no score for: neutral, negative"):
        score_passages(supabase, lambda text: Sentiment("other/model", {"positive": 1.0}), limit=10)
    assert supabase.updates == []


def test_prices_cover_watchlist_and_mentioned_symbols() -> None:
    supabase = FakeSupabase({"mentioned_tickers": [[{"symbol": "TSLA"}, {"symbol": "AAPL"}]]})
    requested: list[tuple[list[str], date]] = []

    def download(symbols: list[str], start: date) -> PriceDownload:
        requested.append((symbols, start))
        return PriceDownload(parse_bars(BARS)[0][:1], ["Warning: something"])

    result = load_prices(supabase, download, WATCHLIST, date(2026, 11, 1))
    assert requested == [(["AAPL", "GOOGL", "NVDA", "TSLA"], date(2026, 11, 1))]
    assert result == (4, 1, ["Warning: something"])
    assert supabase.upserts == [
        (
            "daily_prices",
            [
                {
                    "symbol": "AMZN",
                    "trade_date": "2023-08-21",
                    "open": "133.55",
                    "high": "135.2",
                    "low": "132.71",
                    "close": "134.36",
                    "volume": 11015261,
                    "dataset": "EQUS.SUMMARY",
                }
            ],
            "symbol,trade_date",
            False,
        )
    ]


# Shaped like ORATS /datav2/hist/cores, cut down to the requested fields.
CORES = json.dumps(
    {
        "data": [
            {"ticker": "AAPL", "tradeDate": "2019-11-27", "iv30d": 19.5, "updatedAt": "2019-11-27T20:46:01Z"},
            {
                "ticker": "AAPL",
                "tradeDate": "2019-11-29",
                "iv30d": 18.98,
                "ivPctile1y": 18,
                "orHv20d": 19.45,
                "slope": 3.0127,
                "contango": 0.55,
                "cVolu": 164999,
                "pVolu": 94247,
                "cOi": 1831571,
                "pOi": 2030053,
                "updatedAt": "2019-11-29T17:46:01Z",
            },
        ]
    }
).encode("utf-8")
CORE_DAY = OptionsDay(
    "AAPL",
    date(2019, 11, 29),
    {
        "iv_30d": 18.98,
        "iv_percentile_1y": 18.0,
        "hv_20d": 19.45,
        "skew_slope": 3.0127,
        "contango": 0.55,
        "call_volume": 164999,
        "put_volume": 94247,
        "call_open_interest": 1831571,
        "put_open_interest": 2030053,
    },
    datetime(2019, 11, 29, 17, 46, 1, tzinfo=timezone.utc),
)


def test_cores_are_parsed_with_missing_metrics_left_empty() -> None:
    earlier, day = parse_cores(CORES)
    assert day == CORE_DAY
    assert earlier.metrics["iv_30d"] == 19.5
    assert {name for name, value in earlier.metrics.items() if value is None} == set(CORE_DAY.metrics) - {"iv_30d"}


@pytest.mark.parametrize(
    "body",
    [
        b'{"message":"ok"}',
        b'{"data":[{"tradeDate":"2019-11-29"}]}',
        b'{"data":[{"ticker":"AAPL","tradeDate":"29/11/2019"}]}',
        b'{"data":[{"ticker":"AAPL","tradeDate":"2019-11-29","iv30d":"18.98"}]}',
        b'{"data":[{"ticker":"AAPL","tradeDate":"2019-11-29","cVolu":10.5}]}',
    ],
)
def test_cores_in_an_unexpected_shape_are_an_error(body: bytes) -> None:
    with pytest.raises(LoaderError, match="Unexpected ORATS"):
        parse_cores(body)


def test_options_are_requested_for_one_date_ten_tickers_at_a_time() -> None:
    symbols = ["AAPL", *(f"T{letter}" for letter in "ABCDEFGHIJK")]
    not_found = HttpStatusError(404, "HTTP 404 from api.orats.io/datav2/hist/cores: Not Found")
    http = FakeHttp([HttpResponse(200, json.dumps({"data": json.loads(CORES)["data"][1:]}).encode("utf-8")), not_found])
    assert download_day("orats-token", symbols, date(2019, 11, 29), http) == [CORE_DAY]
    queries = [urllib.parse.parse_qs(urllib.parse.urlparse(url).query) for _, url, _, _ in http.calls]
    assert [method for method, _, _, _ in http.calls] == ["GET", "GET"]
    assert http.calls[0][1].startswith("https://api.orats.io/datav2/hist/cores?")
    assert [query["ticker"] for query in queries] == [["AAPL,TA,TB,TC,TD,TE,TF,TG,TH,TI"], ["TJ,TK"]]
    assert [query["tradeDate"] for query in queries] == [["2019-11-29"], ["2019-11-29"]]
    assert queries[0]["token"] == ["orats-token"]
    assert queries[0]["fields"] == ["ticker,tradeDate,updatedAt,iv30d,ivPctile1y,orHv20d,slope,contango,cVolu,pVolu,cOi,pOi"]


def test_an_orats_error_other_than_not_found_stops_the_download() -> None:
    http = FakeHttp([HttpStatusError(403, "HTTP 403 from api.orats.io/datav2/hist/cores: denied")])
    with pytest.raises(LoaderError, match="HTTP 403"):
        download_day("bad-token", ["AAPL"], date(2019, 11, 29), http)


def test_options_cover_each_weekday_for_watchlist_and_mentioned_symbols() -> None:
    supabase = FakeSupabase({"mentioned_tickers": [[{"symbol": "TSLA"}]]})
    requested: list[tuple[list[str], date]] = []

    def download(symbols: list[str], trade_date: date) -> list[OptionsDay]:
        requested.append((symbols, trade_date))
        return [CORE_DAY] if trade_date == CORE_DAY.trade_date else []

    # Thursday 28 November 2019 through Monday 2 December.
    result = load_options(supabase, download, WATCHLIST, date(2019, 11, 28), date(2019, 12, 2))
    symbols = ["AAPL", "GOOGL", "NVDA", "TSLA"]
    assert requested == [(symbols, date(2019, 11, 28)), (symbols, date(2019, 11, 29)), (symbols, date(2019, 12, 2))]
    assert result == (
        4,
        1,
        ["ORATS returned no options data from 2019-11-28 through 2019-12-02 for GOOGL, NVDA, TSLA"],
    )
    row = {"symbol": "AAPL", "trade_date": "2019-11-29", **CORE_DAY.metrics, "source_updated_at": "2019-11-29T17:46:01+00:00"}
    assert supabase.upserts == [
        ("options_daily", [], "symbol,trade_date", False),
        ("options_daily", [row], "symbol,trade_date", False),
        ("options_daily", [], "symbol,trade_date", False),
    ]


def test_options_report_a_window_with_no_data_at_all_in_one_line() -> None:
    result = load_options(FakeSupabase(), lambda symbols, trade_date: [], WATCHLIST, date(2019, 11, 30), date(2019, 12, 2))
    assert result == (3, 0, ["ORATS returned no options data from 2019-11-30 through 2019-12-02 for any symbol"])


def test_a_digest_answer_becomes_plain_sentences() -> None:
    answer = (
        "- **Apple** beat estimates and raised guidance.[1][2]\n"
        "2. Apple faces a new EU fine, see https://example.com/story [3]\n"
        "\n"
        "\u2022 Apple `AAPL` named a new CFO.\n"
    )
    assert digest_items(answer) == [
        "Apple beat estimates and raised guidance.",
        "Apple faces a new EU fine, see",
        "Apple AAPL named a new CFO.",
    ]
    assert digest_items("NONE") == digest_items(" none.\n") == digest_items("") == []


def test_a_digest_asks_perplexity_for_the_last_day_and_is_stamped_after_the_answer() -> None:
    answer = {"model": "sonar", "choices": [{"message": {"role": "assistant", "content": "Apple won a case.[1]"}}]}
    http = FakeHttp([HttpResponse(200, json.dumps(answer).encode("utf-8"))])
    fetched_at = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)

    def clock() -> datetime:
        assert len(http.calls) == 1
        return fetched_at

    digest = fetch_digest("pplx-key", "AAPL", ["Apple"], http, clock)
    assert digest == NewsDigest("AAPL", "sonar", fetched_at, ["Apple won a case."])
    ((method, url, headers, body),) = http.calls
    assert (method, url) == ("POST", "https://api.perplexity.ai/chat/completions")
    assert headers["Authorization"] == "Bearer pplx-key"
    request = json.loads(body or b"")
    assert (request["model"], request["search_recency_filter"]) == ("sonar", "day")
    assert "Apple (ticker AAPL)" in request["messages"][1]["content"]
    assert "exactly: NONE" in request["messages"][1]["content"]


def test_a_perplexity_answer_without_text_is_an_error() -> None:
    http = FakeHttp([HttpResponse(200, b'{"choices":[]}')])
    with pytest.raises(LoaderError, match="Unexpected Perplexity response for NVDA"):
        fetch_digest("pplx-key", "NVDA", [], http)


def test_a_stored_digest_without_items_has_them_rebuilt_without_asking_perplexity_again() -> None:
    day = "2026-11-10"
    supabase = FakeSupabase(
        {
            "newsletters": [
                [
                    {"id": f"perplexity:AAPL:{day}", "body_text": "Apple beat estimates.\nApple settled."},
                    {"id": f"perplexity:GOOGL:{day}", "body_text": "Google settled."},
                    {"id": f"perplexity:NVDA:{day}", "body_text": ""},
                ]
            ],
            "newsletter_passages": [[{"newsletter_id": f"perplexity:GOOGL:{day}"}]],
        }
    )

    def fetch(symbol: str, names: list[str]) -> NewsDigest:
        raise AssertionError(f"{symbol} already has a digest")

    assert load_news(supabase, fetch, WATCHLIST, date(2026, 11, 10)) == (0, 3, 2)
    assert supabase.upserts == [
        (
            "newsletter_passages",
            [
                {
                    "newsletter_id": f"perplexity:AAPL:{day}",
                    "passage_index": 0,
                    "passage_text": "Apple beat estimates.",
                    "tickers": ["AAPL"],
                },
                {
                    "newsletter_id": f"perplexity:AAPL:{day}",
                    "passage_index": 1,
                    "passage_text": "Apple settled.",
                    "tickers": ["AAPL"],
                },
            ],
            "newsletter_id,passage_index",
            True,
        )
    ]


def test_news_is_fetched_once_per_symbol_and_day_and_tagged_with_its_symbol() -> None:
    supabase = FakeSupabase(
        {
            "newsletters": [[{"id": "perplexity:GOOGL:2026-11-10", "body_text": "Google settled."}]],
            "newsletter_passages": [[{"newsletter_id": "perplexity:GOOGL:2026-11-10"}]],
        }
    )
    fetched_at = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
    asked: list[tuple[str, list[str]]] = []

    def fetch(symbol: str, names: list[str]) -> NewsDigest:
        asked.append((symbol, names))
        items = ["Apple beat estimates.", "Apple and Google settled."] if symbol == "AAPL" else []
        return NewsDigest(symbol, "sonar", fetched_at, items)

    assert load_news(supabase, fetch, WATCHLIST, date(2026, 11, 10)) == (2, 1, 2)
    assert asked == [("AAPL", ["Apple"]), ("NVDA", [])]
    assert supabase.selects == [
        ("newsletters", {"source": "eq.perplexity", "id": "like.*:2026-11-10"}),
        ("newsletter_passages", {"newsletter_id": "like.perplexity:*:2026-11-10"}),
    ]
    apple, apple_passages, nvidia, nvidia_passages = supabase.upserts
    assert apple == (
        "newsletters",
        [
            {
                "id": "perplexity:AAPL:2026-11-10",
                "source": "perplexity",
                "sender": "perplexity",
                "publication": "sonar",
                "subject": "AAPL news digest 2026-11-10",
                "received_at": "2026-11-10T15:00:00+00:00",
                "body_text": "Apple beat estimates.\nApple and Google settled.",
            }
        ],
        "id",
        True,
    )
    assert apple_passages[1] == [
        {
            "newsletter_id": "perplexity:AAPL:2026-11-10",
            "passage_index": 0,
            "passage_text": "Apple beat estimates.",
            "tickers": ["AAPL"],
        },
        {
            "newsletter_id": "perplexity:AAPL:2026-11-10",
            "passage_index": 1,
            "passage_text": "Apple and Google settled.",
            "tickers": ["AAPL"],
        },
    ]
    # A day without news is stored too, so it is not asked for again.
    assert (nvidia[1][0]["id"], nvidia[1][0]["body_text"]) == ("perplexity:NVDA:2026-11-10", "")
    assert nvidia_passages[1] == []


# The lines of QuantConnect's AAPL factor file around the 4-for-1 split that took effect on 31 August 2020.
AAPL_FACTORS = (
    "20200507,0.9923080,0.25,303.74\n"
    "20200806,0.9949942,0.25,455.61\n"
    "20200828,0.9967882,0.25,499.23\n"
    "20201105,0.9967882,1,119.03\n"
    "20501231,1,1,0\n"
)


def test_a_factor_file_is_read_the_way_lean_reads_it() -> None:
    text = (
        "20501231,1,1,0\n"
        "20200828,0.9967882,0.25,499.23\n"
        "\n"
        "20200828,0.5,0.5,1\n"
        "19990104,0.5,1.6e+6,3\n"
        "19990105,0.5,inf,3\n"
        "19990106,0,1,3\n"
        "20201105,0.9967882,1\n"
    )
    assert parse_factor_file(text, "aapl.csv") == [
        FactorRow(date(2020, 8, 28), 0.9967882, 0.25),
        FactorRow(date(2020, 11, 5), 0.9967882, 1.0),
        FactorRow(date(2050, 12, 31), 1.0, 1.0),
    ]


@pytest.mark.parametrize("line", ["2020-08-28,1,1", "20200828,1", "20200828,one,1", "20201331,1,1", "20200828,nan,1"])
def test_a_factor_file_line_in_an_unexpected_shape_is_an_error(line: str) -> None:
    with pytest.raises(LoaderError, match="Unexpected line in QuantConnect factor file aapl.csv"):
        parse_factor_file(f"20501231,1,1,0\n{line}\n", "aapl.csv")


def write_factor_archive(lean_data: Path, name: str, files: dict[str, str]) -> None:
    folder = lean_data / "equity" / "usa" / "factor_files"
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / name, "w") as archive:
        for entry, text in files.items():
            archive.writestr(entry, text)


def test_factors_come_from_the_newest_archive_in_the_lean_data_folder(tmp_path: Path) -> None:
    write_factor_archive(tmp_path, "factor_files_20261109.zip", {"aapl.csv": "20501231,1,1,0\n"})
    write_factor_archive(
        tmp_path,
        "factor_files_20261112.zip",
        {"aapl.csv": AAPL_FACTORS, "factor_files/BRK.B.csv": "20501231,1,1,0\n", "aaplx.csv": "20501231,1,1,0\n"},
    )
    (tmp_path / "equity" / "usa" / "factor_files" / "factor_files_20269999.zip").write_bytes(b"not a date")
    (tmp_path / "equity" / "usa" / "factor_files" / "aapl.csv").write_text("20501231,2,2,0\n")

    snapshot = read_factor_files(tmp_path, ["AAPL", "BRK.B", "NVDA"])
    assert snapshot.as_of == date(2026, 11, 12)
    assert snapshot.missing == ["NVDA"]
    assert snapshot.rows_by_symbol == {
        "AAPL": parse_factor_file(AAPL_FACTORS, "aapl.csv"),
        "BRK.B": [FactorRow(date(2050, 12, 31), 1.0, 1.0)],
    }
    assert len(snapshot.rows_by_symbol["AAPL"]) == 5


def test_a_lean_data_folder_without_a_readable_archive_names_the_command_to_run(tmp_path: Path) -> None:
    with pytest.raises(LoaderError, match="Cannot read .*factor_files: No such file or directory. Run `lean data download"):
        read_factor_files(tmp_path, ["AAPL"])
    folder = tmp_path / "equity" / "usa" / "factor_files"
    folder.mkdir(parents=True)
    (folder / "aapl.csv").write_text("20501231,1,1,0\n")
    with pytest.raises(LoaderError, match="No factor_files_YYYYMMDD.zip in .*Run `lean data download"):
        read_factor_files(tmp_path, ["AAPL"])
    (folder / "factor_files_20261112.zip").write_bytes(b"not a zip")
    with pytest.raises(LoaderError, match="factor_files_20261112.zip is not a readable factor file archive"):
        read_factor_files(tmp_path, ["AAPL"])


def test_adjustments_store_each_symbol_whole_and_warn_about_gaps() -> None:
    supabase = FakeSupabase(
        {"mentioned_tickers": [[{"symbol": "TSLA"}]], "daily_prices": [[{"trade_date": "2026-11-13"}]]}
    )
    asked: list[list[str]] = []
    rows = [FactorRow(date(2020, 8, 28), 0.9967882, 0.25), FactorRow(date(2050, 12, 31), 1.0, 1.0)]

    def read(symbols: list[str]) -> FactorSnapshot:
        asked.append(symbols)
        return FactorSnapshot(date(2026, 11, 12), {"AAPL": rows, "NVDA": []}, ["GOOGL", "TSLA"])

    assert load_adjustments(supabase, read, WATCHLIST) == (
        4,
        2,
        date(2026, 11, 12),
        [
            "QuantConnect has no factor file for GOOGL, TSLA, so nothing was stored for them",
            "The factor files are dated 2026-11-12 and prices are loaded through 2026-11-13, so returns that end "
            "after 2026-11-12 are not marked as adjusted. "
            'Run `lean data download --dataset "US Equity Security Master"` to update them.',
        ],
    )
    assert asked == [["AAPL", "GOOGL", "NVDA", "TSLA"]]
    ((table, stored, on_conflict, ignore_duplicates),) = supabase.upserts
    assert (table, on_conflict, ignore_duplicates) == ("price_adjustments", "symbol", False)
    loaded_at = stored[0].pop("loaded_at")
    assert datetime.fromisoformat(str(loaded_at)).tzinfo is not None and stored[1].pop("loaded_at") == loaded_at
    assert stored == [
        {
            "symbol": "AAPL",
            "as_of": "2026-11-12",
            "factors": [
                {"through_date": "2020-08-28", "price_factor": 0.9967882, "split_factor": 0.25},
                {"through_date": "2050-12-31", "price_factor": 1.0, "split_factor": 1.0},
            ],
        },
        {"symbol": "NVDA", "as_of": "2026-11-12", "factors": []},
    ]


def test_adjustments_dated_up_to_the_latest_price_raise_no_warning() -> None:
    supabase = FakeSupabase({"daily_prices": [[{"trade_date": "2026-11-12"}]]})
    snapshot = FactorSnapshot(date(2026, 11, 12), {"AAPL": [], "GOOGL": [], "NVDA": []}, [])
    assert load_adjustments(supabase, lambda symbols: snapshot, WATCHLIST) == (3, 3, date(2026, 11, 12), [])
    assert load_adjustments(FakeSupabase(), lambda symbols: snapshot, WATCHLIST)[3] == []


# Shaped like the article in Benzinga's API reference, cut down to the fields a headline request fills.
def benzinga_article(article_id: int, created: str, title: str, author: str = "Benzinga Newsdesk") -> dict[str, object]:
    return {
        "id": article_id,
        "author": author,
        "created": created,
        "updated": created,
        "title": title,
        "teaser": "",
        "body": "",
        "url": f"https://www.benzinga.com/news/{article_id}",
        "image": [],
        "channels": [{"name": "News"}],
        "stocks": [{"name": "AAPL", "exchange": "NASDAQ"}, {"name": "NVDA", "exchange": "NASDAQ"}],
        "tags": [],
    }


def benzinga_page(*articles: dict[str, object]) -> HttpResponse:
    return HttpResponse(200, json.dumps(list(articles)).encode("utf-8"))


def test_benzinga_articles_keep_their_creation_time_and_a_one_line_headline() -> None:
    body = benzinga_page(
        benzinga_article(36444586, "Mon, 01 Jan 2024 13:35:14 -0400", "Apple  Raises\n Guidance\x00 "),
        benzinga_article(36444590, "Mon, 01 Jan 2024 18:00:00 +0000", "", author=" "),
        {**benzinga_article(1, "Tue, 02 Jan 2024 09:00:00 -0500", "Numbered as text"), "id": "36444600", "author": None},
    ).body
    first, untitled, text_id = parse_headlines(body)
    assert first == Headline(
        "36444586",
        datetime(2024, 1, 1, 13, 35, 14, tzinfo=timezone(timedelta(hours=-4))),
        "Apple Raises Guidance",
        "Benzinga Newsdesk",
    )
    assert first.created_at == datetime(2024, 1, 1, 17, 35, 14, tzinfo=timezone.utc)
    assert (untitled.title, untitled.author) == ("", None)
    assert (text_id.id, text_id.author) == ("36444600", None)


@pytest.mark.parametrize(
    "body",
    [
        b"<result/>",
        b'{"ok": false}',
        b'["Access denied for user 0 \\"anonymous\\""]',
        json.dumps([{**benzinga_article(1, "Mon, 01 Jan 2024 13:35:14 -0400", "x"), "id": 1.5}]).encode(),
        json.dumps([{**benzinga_article(1, "Mon, 01 Jan 2024 13:35:14 -0400", "x"), "id": True}]).encode(),
        json.dumps([{**benzinga_article(1, "Mon, 01 Jan 2024 13:35:14 -0400", "x"), "id": "12a"}]).encode(),
        json.dumps([benzinga_article(1, "2024-01-01", "x")]).encode(),
        # No offset, so the moment it names is unknown.
        json.dumps([benzinga_article(1, "Mon, 01 Jan 2024 13:35:14", "x")]).encode(),
        json.dumps([{**benzinga_article(1, "Mon, 01 Jan 2024 13:35:14 -0400", "x"), "title": None}]).encode(),
        json.dumps([{**benzinga_article(1, "Mon, 01 Jan 2024 13:35:14 -0400", "x"), "author": 7}]).encode(),
    ],
)
def test_a_benzinga_response_in_an_unexpected_shape_is_an_error(body: bytes) -> None:
    with pytest.raises(LoaderError, match="Unexpected Benzinga"):
        parse_headlines(body)


def test_benzinga_headlines_are_paged_oldest_first_until_a_short_page() -> None:
    created = "Mon, 09 Nov 2026 10:00:00 -0500"
    full = [benzinga_article(number, created, f"Headline {number}") for number in range(100)]
    full[3]["title"] = ""
    http = FakeHttp([benzinga_page(*full), benzinga_page(full[99], benzinga_article(100, created, "Headline 100"))])
    pages = list(headline_pages("bz-key", "BRK.B", date(2026, 11, 2), http))
    assert [len(page) for page in pages] == [99, 1]
    assert pages[0][3].id == "4" and pages[1][0].id == "100"
    queries = [urllib.parse.parse_qs(urllib.parse.urlparse(url).query) for _, url, _, _ in http.calls]
    assert queries[0] == {
        "token": ["bz-key"],
        "tickers": ["BRK.B"],
        "dateFrom": ["2026-11-02"],
        "displayOutput": ["headline"],
        "sort": ["created:asc"],
        "pageSize": ["100"],
        "page": ["0"],
    }
    assert queries[1] == {**queries[0], "page": ["1"]}
    assert all(
        (method, url.split("?")[0], headers) == ("GET", "https://api.benzinga.com/api/v2/news", {"accept": "application/json"})
        for method, url, headers, _ in http.calls
    )


def test_benzinga_without_headlines_is_an_empty_list_or_a_no_data_answer() -> None:
    assert list(headline_pages("bz-key", "AAPL", date(2026, 11, 2), FakeHttp([benzinga_page()]))) == []
    no_data = HttpStatusError(404, 'HTTP 404 from api.benzinga.com/api/v2/news: {"errors": [{"code": "no_data_found"}]}')
    assert list(headline_pages("bz-key", "AAPL", date(2026, 11, 2), FakeHttp([no_data]))) == []


@pytest.mark.parametrize(
    "error",
    [
        HttpStatusError(401, 'HTTP 401 from api.benzinga.com/api/v2/news: ["Access denied for user 0 \\"anonymous\\""]'),
        HttpStatusError(404, "HTTP 404 from api.benzinga.com/api/v2/news: page not found"),
        HttpStatusError(500, 'HTTP 500 from api.benzinga.com/api/v2/news: {"errors": [{"code": "no_data_found"}]}'),
    ],
)
def test_a_benzinga_error_other_than_no_data_stops_the_download(error: HttpStatusError) -> None:
    with pytest.raises(HttpStatusError) as raised:
        list(headline_pages("bz-key", "AAPL", date(2026, 11, 2), FakeHttp([error])))
    assert raised.value is error


def test_benzinga_paging_that_does_not_advance_is_an_error_instead_of_an_endless_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = "Mon, 09 Nov 2026 10:00:00 -0500"
    full = benzinga_page(*(benzinga_article(number, created, f"Headline {number}") for number in range(100)))
    with pytest.raises(LoaderError, match="same AAPL headlines for page 1"):
        list(headline_pages("bz-key", "AAPL", date(2026, 11, 2), FakeHttp([full, full])))

    monkeypatch.setattr("research_data.benzinga.MAX_PAGES", 2)
    more = benzinga_page(*(benzinga_article(number, created, f"Headline {number}") for number in range(100, 200)))
    http = FakeHttp([full, more])
    with pytest.raises(LoaderError, match="more than 2 requests for the AAPL headlines since 2026-11-02"):
        list(headline_pages("bz-key", "AAPL", date(2026, 11, 2), http))
    assert len(http.calls) == 2


def test_a_long_benzinga_download_starts_again_from_the_date_it_reached_instead_of_paging_deeper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("research_data.benzinga.PAGE_SIZE", 2)
    monkeypatch.setattr("research_data.benzinga.WINDOW_PAGES", 2)

    def article(number: int, created: str) -> dict[str, object]:
        return benzinga_article(number, created, f"Headline {number}")

    # 23:30 in New York on 9 November is already 10 November in UTC.
    first = [article(1, "Tue, 03 Nov 2026 10:00:00 -0500"), article(2, "Mon, 09 Nov 2026 09:00:00 -0500")]
    second = [article(3, "Mon, 09 Nov 2026 10:00:00 -0500"), article(4, "Mon, 09 Nov 2026 23:30:00 -0500")]
    http = FakeHttp(
        [
            benzinga_page(*first),
            benzinga_page(*second),
            # The new stretch starts with headlines already read.
            benzinga_page(first[1], second[0]),
            benzinga_page(second[1], article(5, "Thu, 12 Nov 2026 08:00:00 -0500")),
            benzinga_page(article(6, "Fri, 13 Nov 2026 08:00:00 -0500")),
        ]
    )
    pages = list(headline_pages("bz-key", "AAPL", date(2026, 11, 2), http))
    assert [[headline.id for headline in page] for page in pages] == [["1", "2"], ["3", "4"], ["5"], ["6"]]
    queries = [urllib.parse.parse_qs(urllib.parse.urlparse(url).query) for _, url, _, _ in http.calls]
    assert [(query["dateFrom"][0], query["page"][0]) for query in queries] == [
        ("2026-11-02", "0"),
        ("2026-11-02", "1"),
        ("2026-11-09", "0"),
        ("2026-11-09", "1"),
        ("2026-11-11", "0"),
    ]

    # Once the new stretch has moved past the headlines already read, a repeated page is still an error.
    moved_on = benzinga_page(second[1], article(5, "Thu, 12 Nov 2026 08:00:00 -0500"))
    repeated = FakeHttp([benzinga_page(*first), benzinga_page(*second), moved_on, benzinga_page(*second)])
    with pytest.raises(LoaderError, match="same AAPL headlines for page 1"):
        list(headline_pages("bz-key", "AAPL", date(2026, 11, 2), repeated))


def test_more_benzinga_headlines_in_two_days_than_one_stretch_holds_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("research_data.benzinga.PAGE_SIZE", 2)
    monkeypatch.setattr("research_data.benzinga.WINDOW_PAGES", 2)
    created = "Tue, 03 Nov 2026 10:00:00 -0500"
    busy = [benzinga_page(*(benzinga_article(number, created, "x") for number in pair)) for pair in ((1, 2), (3, 4))]
    with pytest.raises(LoaderError) as raised:
        list(headline_pages("bz-key", "AAPL", date(2026, 11, 3), FakeHttp(busy)))
    assert str(raised.value) == (
        "Benzinga has more than 4 AAPL headlines within two days of 2026-11-03, which cannot be paged through"
    )

    # The same when the day it restarts from holds a whole stretch of headlines already read.
    later = "Thu, 05 Nov 2026 10:00:00 -0500"
    moved = [benzinga_page(*(benzinga_article(number, later, "x") for number in pair)) for pair in ((1, 2), (3, 4))]
    with pytest.raises(LoaderError, match="more than 4 AAPL headlines within two days of 2026-11-04"):
        list(headline_pages("bz-key", "AAPL", date(2026, 11, 2), FakeHttp(moved + moved)))


def test_benzinga_headlines_are_stored_per_symbol_as_first_seen_for_watchlist_and_mentioned_symbols() -> None:
    supabase = FakeSupabase({"mentioned_tickers": [[{"symbol": "TSLA"}]]})
    created = datetime(2026, 11, 10, 9, 30, tzinfo=timezone(timedelta(hours=-5)))
    shared = Headline("77", created, "Apple and Nvidia extend chip deal", "Benzinga Newsdesk")
    later = Headline("78", created + timedelta(hours=1), "Apple hits record", None)
    asked: list[tuple[str, date]] = []

    def pages(symbol: str, since: date) -> list[list[Headline]]:
        asked.append((symbol, since))
        return {"AAPL": [[shared], [later]], "NVDA": [[shared]]}.get(symbol, [])

    result = load_benzinga(supabase, pages, WATCHLIST, date(2026, 11, 3))
    assert result == (4, 3, ["Benzinga returned no headlines since 2026-11-03 for GOOGL, TSLA"])
    assert asked == [(symbol, date(2026, 11, 3)) for symbol in ("AAPL", "GOOGL", "NVDA", "TSLA")]
    assert [(table, conflict, ignore) for table, _, conflict, ignore in supabase.upserts] == [
        ("newsletters", "id", True),
        ("newsletter_passages", "newsletter_id,passage_index", True),
    ] * 3
    assert supabase.upserts[0][1] == [
        {
            "id": "benzinga:AAPL:77",
            "source": "benzinga",
            "sender": "Benzinga Newsdesk",
            "publication": "Benzinga",
            "subject": "Apple and Nvidia extend chip deal",
            "received_at": "2026-11-10T09:30:00-05:00",
            "body_text": "Apple and Nvidia extend chip deal",
        }
    ]
    assert supabase.upserts[1][1] == [
        {
            "newsletter_id": "benzinga:AAPL:77",
            "passage_index": 0,
            "passage_text": "Apple and Nvidia extend chip deal",
            "tickers": ["AAPL"],
        }
    ]
    assert (supabase.upserts[2][1][0]["id"], supabase.upserts[2][1][0]["sender"]) == ("benzinga:AAPL:78", "benzinga")
    assert supabase.upserts[5][1][0] == {**supabase.upserts[1][1][0], "newsletter_id": "benzinga:NVDA:77", "tickers": ["NVDA"]}


def test_benzinga_with_no_headlines_at_all_is_reported_in_one_line() -> None:
    result = load_benzinga(FakeSupabase(), lambda symbol, since: [], WATCHLIST, date(2026, 11, 3))
    assert result == (3, 0, ["Benzinga returned no headlines since 2026-11-03 for any symbol"])


# Shaped like an article in a real NEWS_SENTIMENT answer: scores are strings, the time is UTC without a zone.
def alphavantage_article(
    number: int, published: str, scores: dict[str, tuple[str, str]], title: str = "Apple  hits\n record\x00 "
) -> dict[str, object]:
    return {
        "title": title,
        "url": f"https://www.benzinga.com/markets/tech/26/11/{number}/apple",
        "time_published": published,
        "authors": ["Mohd Haider"],
        "summary": "Apple shares rose.",
        "source": "Benzinga",
        "category_within_source": "General",
        "source_domain": "Benzinga",
        "topics": [{"topic": "technology", "relevance_score": "0.931289"}],
        "overall_sentiment_score": 0.249936,
        "overall_sentiment_label": "Somewhat-Bullish",
        "ticker_sentiment": [
            {
                "ticker": ticker,
                "relevance_score": relevance,
                "ticker_sentiment_score": sentiment,
                "ticker_sentiment_label": "Neutral",
            }
            for ticker, (relevance, sentiment) in scores.items()
        ],
    }


def alphavantage_page(*articles: dict[str, object]) -> HttpResponse:
    body = {
        "items": str(len(articles)),
        "sentiment_score_definition": "x <= -0.35: Bearish; x >= 0.35: Bullish",
        "relevance_score_definition": "0 < x <= 1, with a higher score indicating higher relevance.",
        "feed": list(articles),
    }
    return HttpResponse(200, json.dumps(body).encode())


def alphavantage_message(key: str, text: str) -> HttpResponse:
    return HttpResponse(200, json.dumps({key: text}).encode())


def test_alphavantage_articles_carry_the_scores_of_the_asked_symbol_and_a_utc_time() -> None:
    page = alphavantage_page(
        alphavantage_article(1, "20261115T120130", {"AAPL": ("1.000000", "0.308468"), "NVDA": ("0.900222", "0.136632")}),
        alphavantage_article(2, "20261115T1202", {"NVDA": ("0.5", "-0.2")}),
        {**alphavantage_article(3, "20261115T120300", {"AAPL": ("0.00589", "-0.386928")}, title=""), "source": " "},
    )
    assert parse_articles(page.body, "AAPL") == [
        Article(
            "https://www.benzinga.com/markets/tech/26/11/1/apple",
            datetime(2026, 11, 15, 12, 1, 30, tzinfo=timezone.utc),
            "Apple hits record",
            "Benzinga",
            1.0,
            0.308468,
        ),
        Article(
            "https://www.benzinga.com/markets/tech/26/11/3/apple",
            datetime(2026, 11, 15, 12, 3, tzinfo=timezone.utc),
            "",
            None,
            0.00589,
            -0.386928,
        ),
    ]
    (nvidia, minute_only) = parse_articles(page.body, "NVDA")
    assert (nvidia.relevance, nvidia.sentiment) == (0.900222, 0.136632)
    assert minute_only.published_at == datetime(2026, 11, 15, 12, 2, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "body",
    [
        b"<html>",
        b"[]",
        b'{"items": "0"}',
        json.dumps({"feed": [{**alphavantage_article(1, "20261115T120130", {}), "url": " "}]}).encode(),
        json.dumps({"feed": [{**alphavantage_article(1, "20261115T120130", {}), "title": None}]}).encode(),
        json.dumps({"feed": [{**alphavantage_article(1, "20261115T120130", {}), "source": 0}]}).encode(),
        json.dumps({"feed": [alphavantage_article(1, "2026-11-15 12:01:30", {})]}).encode(),
        json.dumps({"feed": [alphavantage_article(1, "20261115T120130", {"AAPL": ("high", "0.1")})]}).encode(),
        json.dumps({"feed": [alphavantage_article(1, "20261115T120130", {"AAPL": ("0.5", "NaN")})]}).encode(),
        json.dumps({"feed": [alphavantage_article(1, "20261115T120130", {"AAPL": (True, "0.1")})]}).encode(),
        json.dumps({"feed": [alphavantage_article(1, "20261115T120130", {"AAPL": ("0.5", "Infinity")})]}).encode(),
        json.dumps({"feed": ["AAPL"]}).encode(),
    ],
)
def test_an_alphavantage_response_in_an_unexpected_shape_is_an_error(body: bytes) -> None:
    with pytest.raises(LoaderError, match="Unexpected Alpha Vantage"):
        parse_articles(body, "AAPL")


def test_alphavantage_news_is_paged_oldest_first_from_the_minute_of_the_newest_article_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("research_data.alphavantage.SHORTEST_PAGE", 3)
    scores = {"BRK.B": ("0.5", "0.1")}
    first = [
        alphavantage_article(1, "20261102T093000", scores),
        alphavantage_article(2, "20261103T101500", {"AAPL": ("0.5", "0.1")}),
        alphavantage_article(3, "20261103T101559", scores),
    ]
    second = [first[2], alphavantage_article(4, "20261104T080000", scores)]
    http = FakeHttp([alphavantage_page(*first), alphavantage_page(*second)])
    pages = list(article_pages("av-key", "BRK.B", date(2026, 11, 2), http))
    assert [[article.url.split("/")[-2] for article in page] for page in pages] == [["1", "3"], ["4"]]
    queries = [urllib.parse.parse_qs(urllib.parse.urlparse(url).query) for _, url, _, _ in http.calls]
    assert queries[0] == {
        "function": ["NEWS_SENTIMENT"],
        "tickers": ["BRK.B"],
        "time_from": ["20261102T0000"],
        "sort": ["EARLIEST"],
        "limit": ["1000"],
        "apikey": ["av-key"],
    }
    assert queries[1] == {**queries[0], "time_from": ["20261103T1015"]}
    assert all(
        (method, url.split("?")[0], headers, body) == ("GET", "https://www.alphavantage.co/query", {}, None)
        for method, url, headers, body in http.calls
    )


def test_alphavantage_stops_at_a_response_too_short_to_have_been_cut_off() -> None:
    scores = {"AAPL": ("0.5", "0.1")}

    def articles(count: int) -> list[dict[str, object]]:
        return [alphavantage_article(number, f"202611{2 + number // 60:02d}T09{number % 60:02d}00", scores) for number in range(count)]

    http = FakeHttp([alphavantage_page(*articles(49))])
    assert [len(page) for page in article_pages("av-key", "AAPL", date(2026, 11, 2), http)] == [49]
    assert len(http.calls) == 1

    # 50 is what the API sends when it applies its default limit, so there may be more.
    http = FakeHttp([alphavantage_page(*articles(50)), alphavantage_page(*articles(50)[-1:])])
    assert [len(page) for page in article_pages("av-key", "AAPL", date(2026, 11, 2), http)] == [50]
    assert "time_from=20261102T0949" in http.calls[1][1] and len(http.calls) == 2


def test_alphavantage_articles_sent_newest_first_are_an_error_when_more_may_follow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scores = {"AAPL": ("0.5", "0.1")}
    newest_first = [alphavantage_article(2, "20261104T080000", scores), alphavantage_article(1, "20261103T080000", scores)]
    pages = article_pages("av-key", "AAPL", date(2026, 11, 2), FakeHttp([alphavantage_page(*newest_first)]))
    assert [[article.url.split("/")[-2] for article in page] for page in pages] == [["2", "1"]]

    monkeypatch.setattr("research_data.alphavantage.SHORTEST_PAGE", 2)
    http = FakeHttp([alphavantage_page(*newest_first)])
    with pytest.raises(LoaderError) as raised:
        list(article_pages("av-key", "AAPL", date(2026, 11, 2), http))
    assert str(raised.value) == "Alpha Vantage did not send the AAPL articles oldest first"
    same_minute = [alphavantage_article(1, "20261104T080030", scores), alphavantage_article(2, "20261104T080000", scores)]
    http = FakeHttp([alphavantage_page(*same_minute), alphavantage_page(same_minute[0])])
    with pytest.raises(LoaderError, match="oldest first"):
        list(article_pages("av-key", "AAPL", date(2026, 11, 2), http))


def test_alphavantage_without_articles_is_an_empty_feed_or_a_no_articles_message() -> None:
    since = date(2026, 11, 2)
    assert list(article_pages("av-key", "AAPL", since, FakeHttp([alphavantage_page()]))) == []
    no_articles = alphavantage_message("Information", "No articles found. Please adjust the time range.")
    assert list(article_pages("av-key", "AAPL", since, FakeHttp([no_articles]))) == []
    unscored = alphavantage_page(alphavantage_article(1, "20261102T093000", {"NVDA": ("0.5", "0.1")}))
    assert list(article_pages("av-key", "AAPL", since, FakeHttp([unscored]))) == []


@pytest.mark.parametrize(
    ("key", "text", "error", "message"),
    [
        (
            "Information",
            "Thank you for using Alpha Vantage! This is a premium endpoint.",
            LoaderError,
            "Alpha Vantage refused the AAPL request: Thank you for using Alpha Vantage! This is a premium endpoint.",
        ),
        (
            "Note",
            "We have detected your API key as av-key and our standard API rate limit is 25 requests per day.",
            LoaderError,
            "Alpha Vantage refused the AAPL request: We have detected your API key as [key] and our standard API "
            "rate limit is 25 requests per day.",
        ),
        (
            "Error Message",
            "the parameter apikey is invalid or missing.",
            LoaderError,
            "Alpha Vantage refused the AAPL request: the parameter apikey is invalid or missing.",
        ),
        (
            "Information",
            "Invalid inputs. Please refer to the API documentation and try again (apikey av-key).",
            SymbolRejected,
            "Alpha Vantage rejected the AAPL request: Invalid inputs. Please refer to the API documentation and "
            "try again (apikey [key]).",
        ),
    ],
)
def test_an_alphavantage_refusal_stops_the_download_without_showing_the_key(
    key: str, text: str, error: type[LoaderError], message: str
) -> None:
    with pytest.raises(LoaderError) as raised:
        list(article_pages("av-key", "AAPL", date(2026, 11, 2), FakeHttp([alphavantage_message(key, text)])))
    assert type(raised.value) is error
    assert str(raised.value) == message


def test_an_alphavantage_http_error_does_not_show_the_key() -> None:
    error = HttpStatusError(403, "HTTP 403 from www.alphavantage.co/query: forbidden for av-key")
    with pytest.raises(LoaderError) as raised:
        list(article_pages("av-key", "AAPL", date(2026, 11, 2), FakeHttp([error])))
    assert str(raised.value) == "HTTP 403 from www.alphavantage.co/query: forbidden for [key]"


def alphavantage_time_from(http: FakeHttp) -> list[str]:
    return [urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["time_from"][0] for _, url, _, _ in http.calls]


def test_alphavantage_ends_at_a_response_shorter_than_an_earlier_one() -> None:
    scores = {"AAPL": ("0.5", "0.1")}
    early = [alphavantage_article(number, f"20261103T09{number:02d}00", scores) for number in range(5)]
    # 55 articles in the newest minute: asked again from that minute, they come back as a response of 50 or more.
    burst = [alphavantage_article(100 + number, f"20261104T1015{number:02d}", scores) for number in range(55)]
    http = FakeHttp([alphavantage_page(*early, *burst), alphavantage_page(*burst)])
    assert [len(page) for page in article_pages("av-key", "AAPL", date(2026, 11, 2), http)] == [60]
    assert alphavantage_time_from(http) == ["20261102T0000", "20261104T1015"]


def test_alphavantage_goes_on_from_the_next_minute_when_one_minute_fills_a_whole_response() -> None:
    scores = {"AAPL": ("0.5", "0.1")}
    burst = [alphavantage_article(number, f"20261104T2359{number:02d}", scores) for number in range(55)]
    empty = alphavantage_message("Information", "No articles found. Please adjust the time range.")
    http = FakeHttp([alphavantage_page(*burst), alphavantage_page(*burst), empty])
    assert [len(page) for page in article_pages("av-key", "AAPL", date(2026, 11, 2), http)] == [55]
    assert alphavantage_time_from(http) == ["20261102T0000", "20261104T2359", "20261105T0000"]

    # An API that sent all 1,000 asked for would have sent this one along with the 55.
    later = alphavantage_page(alphavantage_article(900, "20261105T080000", scores))
    http = FakeHttp([alphavantage_page(*burst), alphavantage_page(*burst), later])
    pages = article_pages("av-key", "AAPL", date(2026, 11, 2), http)
    assert len(next(pages)) == 55
    with pytest.raises(LoaderError) as raised:
        next(pages)
    assert str(raised.value) == (
        "Alpha Vantage sends at most 55 AAPL articles a request, not the 1000 asked for, and 2026-11-04 23:59 UTC "
        "has at least that many: any more from that minute cannot be read"
    )
    assert alphavantage_time_from(http) == ["20261102T0000", "20261104T2359", "20261105T0000"]


def test_alphavantage_stops_when_a_minute_fills_a_response_no_longer_than_one_that_held_older_articles() -> None:
    scores = {"AAPL": ("0.5", "0.1")}
    early = [alphavantage_article(number, f"20261103T09{number:02d}00", scores) for number in range(5)]
    burst = [alphavantage_article(100 + number, f"20261104T1015{number:02d}", scores) for number in range(55)]
    # Sixty articles asked for from the 2nd come back as 55, and so do the 55 or more of 10:15.
    http = FakeHttp([alphavantage_page(*early, *burst[:50]), alphavantage_page(*burst), alphavantage_page(*burst)])
    pages = article_pages("av-key", "AAPL", date(2026, 11, 2), http)
    assert [len(next(pages)), len(next(pages))] == [55, 5]
    with pytest.raises(LoaderError) as raised:
        next(pages)
    assert str(raised.value) == (
        "Alpha Vantage sends at most 55 AAPL articles a request, not the 1000 asked for, and 2026-11-04 10:15 UTC "
        "has at least that many: any more from that minute cannot be read"
    )
    assert alphavantage_time_from(http) == ["20261102T0000", "20261104T1015", "20261104T1015"]


def test_alphavantage_paging_that_cannot_advance_is_an_error_instead_of_an_endless_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("research_data.alphavantage.SHORTEST_PAGE", 2)
    monkeypatch.setattr("research_data.alphavantage.PAGE_SIZE", 3)
    scores = {"AAPL": ("0.5", "0.1")}
    full = alphavantage_page(*(alphavantage_article(number, "20261103T101500", scores) for number in (1, 2, 3)))
    http = FakeHttp([full, full])
    with pytest.raises(LoaderError) as raised:
        list(article_pages("av-key", "AAPL", date(2026, 11, 2), http))
    assert str(raised.value) == (
        "Alpha Vantage has more than 3 AAPL articles at 2026-11-03 10:15 UTC, which is more than one request can return"
    )
    assert len(http.calls) == 2

    monkeypatch.setattr("research_data.alphavantage.MAX_PAGES", 2)
    more = alphavantage_page(*(alphavantage_article(number, "20261104T101500", scores) for number in (4, 5, 6)))
    http = FakeHttp([full, more])
    with pytest.raises(LoaderError, match="more than 2 pages of AAPL articles since 2026-11-02"):
        list(article_pages("av-key", "AAPL", date(2026, 11, 2), http))
    assert len(http.calls) == 2


def test_alphavantage_articles_are_stored_per_symbol_as_first_seen_for_watchlist_and_mentioned_symbols() -> None:
    supabase = FakeSupabase({"mentioned_tickers": [[{"symbol": "TSLA"}]]})
    published = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)
    apple = Article("https://example.com/a", published, "Apple and Nvidia extend chip deal", "Benzinga", 0.9, 0.31)
    nvidia = Article("https://example.com/a", published, "Apple and Nvidia extend chip deal", "Benzinga", 0.4, -0.12)
    later = Article("https://example.com/b", published + timedelta(hours=1), "Apple hits record", None, 0.2, 0.5)
    asked: list[tuple[str, date]] = []

    def pages(symbol: str, since: date) -> list[list[Article]]:
        asked.append((symbol, since))
        if symbol == "TSLA":
            raise SymbolRejected("Alpha Vantage rejected the TSLA request: Invalid inputs.")
        return {"AAPL": [[apple, later]], "NVDA": [[nvidia]]}.get(symbol, [])

    result = load_alphavantage(supabase, pages, WATCHLIST, date(2026, 11, 3))
    assert result == (
        4,
        3,
        [
            "Alpha Vantage rejected the TSLA request: Invalid inputs.",
            "Alpha Vantage returned no articles since 2026-11-03 for GOOGL",
        ],
    )
    assert asked == [(symbol, date(2026, 11, 3)) for symbol in ("AAPL", "GOOGL", "NVDA", "TSLA")]
    assert [(table, conflict, ignore) for table, _, conflict, ignore in supabase.upserts] == [
        ("alphavantage_news", "symbol,url", True)
    ] * 2
    stored = {
        "symbol": "AAPL",
        "url": "https://example.com/a",
        "published_at": "2026-11-10T14:30:00+00:00",
        "title": "Apple and Nvidia extend chip deal",
        "source": "Benzinga",
        "relevance": 0.9,
        "sentiment": 0.31,
    }
    assert supabase.upserts[0][1] == [
        stored,
        {
            "symbol": "AAPL",
            "url": "https://example.com/b",
            "published_at": "2026-11-10T15:30:00+00:00",
            "title": "Apple hits record",
            "source": None,
            "relevance": 0.2,
            "sentiment": 0.5,
        },
    ]
    assert supabase.upserts[1][1] == [{**stored, "symbol": "NVDA", "relevance": 0.4, "sentiment": -0.12}]


def test_alphavantage_with_no_articles_or_every_symbol_rejected_is_reported_once() -> None:
    result = load_alphavantage(FakeSupabase(), lambda symbol, since: [], WATCHLIST, date(2026, 11, 3))
    assert result == (3, 0, ["Alpha Vantage returned no articles since 2026-11-03 for any symbol"])

    def rejected(symbol: str, since: date) -> list[list[Article]]:
        raise SymbolRejected(f"Alpha Vantage rejected the {symbol} request: Invalid inputs.")

    with pytest.raises(SymbolRejected, match="rejected the NVDA request"):
        load_alphavantage(FakeSupabase(), rejected, WATCHLIST, date(2026, 11, 3))


class FakeObb:
    """Stands in for `openbb.obb` with the result shape of the San Francisco Fed news sentiment call."""

    def __init__(self, outcome: object) -> None:
        self.calls: list[dict[str, object]] = []
        self.federal_reserve = SimpleNamespace(sf=SimpleNamespace(news_sentiment=self._news_sentiment))
        self._outcome = outcome

    def _news_sentiment(self, **arguments: object) -> object:
        self.calls.append(arguments)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return SimpleNamespace(results=self._outcome)


class FakeOpenBBError(Exception):
    pass


def test_market_sentiment_is_every_published_day_of_the_whole_index(monkeypatch: pytest.MonkeyPatch) -> None:
    obb = FakeObb(
        [
            SimpleNamespace(date=date(2026, 11, 7), news_sentiment=0.0592549704015255),
            SimpleNamespace(date=date(2026, 11, 8), news_sentiment=None),
            SimpleNamespace(date=date(2026, 11, 9), news_sentiment=-0.01),
        ]
    )
    monkeypatch.setattr(market_sentiment, "obb", obb)
    assert fetch_news_sentiment() == [
        SentimentReading(date(2026, 11, 7), 0.0592549704015255),
        SentimentReading(date(2026, 11, 9), -0.01),
    ]
    assert obb.calls == [{}]


def test_market_sentiment_names_the_extra_to_install_and_reports_an_openbb_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market_sentiment, "obb", None)
    assert not market_sentiment.openbb_installed()
    with pytest.raises(LoaderError, match="run `poetry install --extras openbb`"):
        fetch_news_sentiment()
    monkeypatch.setattr(market_sentiment, "obb", FakeObb(FakeOpenBBError("\n[Unexpected Error] -> ProxyError")))
    monkeypatch.setattr(market_sentiment, "OpenBBError", FakeOpenBBError, raising=False)
    assert market_sentiment.openbb_installed()
    with pytest.raises(LoaderError) as raised:
        fetch_news_sentiment()
    assert str(raised.value) == (
        "OpenBB could not load the San Francisco Fed news sentiment index: [Unexpected Error] -> ProxyError"
    )


@pytest.mark.skipif(not market_sentiment.openbb_installed(), reason="needs the openbb extra and the network")
def test_openbb_returns_the_published_index_as_daily_readings() -> None:
    readings = fetch_news_sentiment()
    days = [reading.observation_date for reading in readings]
    assert days[0] == date(1980, 1, 1) and days == sorted(set(days))
    assert all(isinstance(reading.value, float) and -2 < reading.value < 2 for reading in readings)
    # Daily without gaps since 1985, which the 21-day lookup in training_rows relies on.
    recent = [day for day in days if day >= date(1985, 1, 1)]
    assert all((later - earlier).days == 1 for earlier, later in zip(recent, recent[1:]))


def test_market_sentiment_adds_only_days_newer_than_the_stored_ones() -> None:
    readings = [SentimentReading(date(2026, 11, day), day / 100) for day in (6, 7, 8)]
    first = FakeSupabase()
    assert load_market_sentiment(first, lambda: readings) == (3, date(2026, 11, 8))
    assert first.upserts == [
        (
            "market_news_sentiment",
            [
                {"observation_date": "2026-11-06", "value": 0.06},
                {"observation_date": "2026-11-07", "value": 0.07},
                {"observation_date": "2026-11-08", "value": 0.08},
            ],
            "observation_date",
            True,
        )
    ]
    later = FakeSupabase({"market_news_sentiment": [[{"observation_date": "2026-11-07"}]]})
    assert load_market_sentiment(later, lambda: readings) == (1, date(2026, 11, 8))
    assert [row["observation_date"] for row in later.upserts[0][1]] == ["2026-11-08"]
    current = FakeSupabase({"market_news_sentiment": [[{"observation_date": "2026-11-08"}]]})
    assert load_market_sentiment(current, lambda: readings) == (0, date(2026, 11, 8))
    assert current.upserts[0][1] == []
    assert load_market_sentiment(FakeSupabase(), lambda: []) == (0, None)


OPTIONAL_SETTINGS = (
    "PERPLEXITY_API_KEY",
    "BENZINGA_API_KEY",
    "ALPHAVANTAGE_API_KEY",
    "ORATS_API_TOKEN",
    "QUANTCONNECT_LEAN_DATA_DIR",
)


def test_all_leaves_out_the_optional_steps_whose_setting_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    ran: list[str] = []
    for name in OPTIONAL_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli.SupabaseClient, "from_env", classmethod(lambda cls, env: FakeSupabase()))
    monkeypatch.setattr(cli, "_STEPS", {name: lambda args, env, supabase, name=name: ran.append(name) for name in cli._STEPS})
    monkeypatch.setattr(market_sentiment, "obb", None)
    feeds = tmp_path / "podcasts.txt"
    feeds.write_text("# no feeds yet\n")
    assert cli_main(["all", "--podcasts", str(feeds)]) == 0
    assert cli_main(["all", "--podcasts", str(tmp_path / "missing.txt")]) == 0
    assert ran == ["newsletters", "score", "prices"] * 2
    assert capsys.readouterr().err == (
        "news: skipped because PERPLEXITY_API_KEY is not set\n"
        "benzinga: skipped because BENZINGA_API_KEY is not set\n"
        "alphavantage: skipped because ALPHAVANTAGE_API_KEY is not set\n"
        f"podcasts: skipped because {feeds} lists no podcast feeds\n"
        "options: skipped because ORATS_API_TOKEN is not set\n"
        "adjustments: skipped because QUANTCONNECT_LEAN_DATA_DIR is not set\n"
        "openbb: skipped because OpenBB is not installed\n"
        "news: skipped because PERPLEXITY_API_KEY is not set\n"
        "benzinga: skipped because BENZINGA_API_KEY is not set\n"
        "alphavantage: skipped because ALPHAVANTAGE_API_KEY is not set\n"
        f"podcasts: skipped because {tmp_path / 'missing.txt'} lists no podcast feeds\n"
        "options: skipped because ORATS_API_TOKEN is not set\n"
        "adjustments: skipped because QUANTCONNECT_LEAN_DATA_DIR is not set\n"
        "openbb: skipped because OpenBB is not installed\n"
    )
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-key")
    monkeypatch.setenv("BENZINGA_API_KEY", "bz-key")
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "av-key")
    monkeypatch.setenv("ORATS_API_TOKEN", "orats-token")
    monkeypatch.setenv("QUANTCONNECT_LEAN_DATA_DIR", "/lean/data")
    monkeypatch.setattr(market_sentiment, "obb", FakeObb([]))
    feeds.write_text("https://feeds.example.com/show\n")
    ran.clear()
    assert cli_main(["all", "--podcasts", str(feeds)]) == 0
    assert ran == [
        "newsletters",
        "news",
        "benzinga",
        "alphavantage",
        "podcasts",
        "score",
        "prices",
        "options",
        "adjustments",
        "openbb",
    ]
    assert capsys.readouterr().err == ""


def test_a_step_run_by_name_reports_its_missing_setting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in OPTIONAL_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli.SupabaseClient, "from_env", classmethod(lambda cls, env: FakeSupabase()))
    assert cli_main(["news"]) == 1
    assert "research-data: Missing PERPLEXITY_API_KEY" in capsys.readouterr().err
    assert cli_main(["options"]) == 1
    assert "research-data: Missing ORATS_API_TOKEN" in capsys.readouterr().err
    assert cli_main(["adjustments"]) == 1
    assert "research-data: Missing QUANTCONNECT_LEAN_DATA_DIR" in capsys.readouterr().err
    assert cli_main(["benzinga"]) == 1
    assert "research-data: Missing BENZINGA_API_KEY" in capsys.readouterr().err
    assert cli_main(["alphavantage"]) == 1
    assert "research-data: Missing ALPHAVANTAGE_API_KEY" in capsys.readouterr().err
    monkeypatch.setattr(market_sentiment, "obb", None)
    assert cli_main(["openbb"]) == 1
    assert "research-data: The openbb step needs OpenBB's Federal Reserve extension" in capsys.readouterr().err


def test_cli_loads_benzinga_headlines_since_the_given_date_and_the_market_index(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    watchlist = tmp_path / "watchlist.txt"
    watchlist.write_text("AAPL: Apple\nNVDA\n")
    supabase = FakeSupabase()
    asked: list[tuple[str, str, date]] = []

    def pages(token: str, symbol: str, since: date) -> list[list[Headline]]:
        asked.append((token, symbol, since))
        created = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)
        return [[Headline("77", created, "Apple hits record", None)]] if symbol == "AAPL" else []

    monkeypatch.setenv("BENZINGA_API_KEY", " bz-key ")
    monkeypatch.setattr(cli.SupabaseClient, "from_env", classmethod(lambda cls, env: supabase))
    monkeypatch.setattr(cli, "headline_pages", pages)
    assert cli_main(["benzinga", "--since", "2026-11-03", "--watchlist", str(watchlist)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "benzinga: read 1 headlines for 2 symbols since 2026-11-03\n"
    assert captured.err == "benzinga: warning: Benzinga returned no headlines since 2026-11-03 for NVDA\n"
    assert asked == [("bz-key", "AAPL", date(2026, 11, 3)), ("bz-key", "NVDA", date(2026, 11, 3))]

    def articles(api_key: str, symbol: str, since: date) -> list[list[Article]]:
        asked.append((api_key, symbol, since))
        published = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)
        return [[Article("https://example.com/a", published, "Apple hits record", "Benzinga", 0.9, 0.31)]] * (symbol == "AAPL")

    asked.clear()
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", " av-key ")
    monkeypatch.setattr(cli, "article_pages", articles)
    assert cli_main(["alphavantage", "--since", "2026-11-03", "--watchlist", str(watchlist)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "alphavantage: read 1 scored articles for 2 symbols since 2026-11-03\n"
    assert captured.err == "alphavantage: warning: Alpha Vantage returned no articles since 2026-11-03 for NVDA\n"
    assert asked == [("av-key", "AAPL", date(2026, 11, 3)), ("av-key", "NVDA", date(2026, 11, 3))]
    assert supabase.upserts[-1][0] == "alphavantage_news"

    monkeypatch.setattr(cli, "fetch_news_sentiment", lambda: [SentimentReading(date(2026, 11, 8), 0.09)])
    assert cli_main(["openbb"]) == 0
    assert capsys.readouterr().out == (
        "openbb: added 1 days of the San Francisco Fed news sentiment index, stored through 2026-11-08\n"
    )
    monkeypatch.setattr(cli, "fetch_news_sentiment", lambda: [])
    assert cli_main(["openbb"]) == 0
    assert capsys.readouterr().out == "openbb: added 0 days of the San Francisco Fed news sentiment index\n"


def test_cli_reads_factor_files_from_the_lean_data_folder(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    write_factor_archive(tmp_path / "lean", "factor_files_20261112.zip", {"aapl.csv": AAPL_FACTORS})
    watchlist = tmp_path / "watchlist.txt"
    watchlist.write_text("AAPL: Apple\nNVDA\n")
    supabase = FakeSupabase({"daily_prices": [[{"trade_date": "2026-11-12"}]]})
    monkeypatch.setenv("QUANTCONNECT_LEAN_DATA_DIR", str(tmp_path / "lean"))
    monkeypatch.setattr(cli.SupabaseClient, "from_env", classmethod(lambda cls, env: supabase))
    assert cli_main(["adjustments", "--watchlist", str(watchlist)]) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "adjustments: stored split and dividend factors for 1 of 2 symbols "
        "from QuantConnect factor files dated 2026-11-12\n"
    )
    assert captured.err == (
        "adjustments: warning: QuantConnect has no factor file for NVDA, so nothing was stored for them\n"
    )
    ((_, stored, _, _),) = supabase.upserts
    assert [(row["symbol"], len(row["factors"])) for row in stored] == [("AAPL", 5)]


def test_cli_names_the_missing_setting(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY", "DATABENTO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert cli_main(["prices"]) == 1
    assert "research-data: Missing SUPABASE_URL" in capsys.readouterr().err
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    assert cli_main(["prices"]) == 1
    assert "research-data: Missing DATABENTO_API_KEY" in capsys.readouterr().err


def test_local_finbert_names_the_extra_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_finbert, "torch", None)
    with pytest.raises(LoaderError, match="run `poetry install --extras local`"):
        LocalFinbert()


@pytest.mark.skipif(local_finbert.torch is None, reason="needs the local extra; downloads the model on first use")
def test_local_finbert_scores_the_three_labels_as_probabilities() -> None:
    score = LocalFinbert()
    raised = score("Shares surged after the company raised full-year guidance.")
    cut = score("The company cut its dividend and warned that margins will fall next year.")
    assert raised.model == "ProsusAI/finbert"
    assert sorted(raised.scores) == ["negative", "neutral", "positive"]
    assert sum(raised.scores.values()) == pytest.approx(1.0, abs=1e-4)
    assert raised.scores["positive"] > 0.8 and cut.scores["negative"] > 0.8
    assert sum(score("Nvidia beat estimates. " * 400).scores.values()) == pytest.approx(1.0, abs=1e-4)


def test_cli_scores_locally_without_a_hugging_face_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    supabase = FakeSupabase({"newsletter_passages": [[{"id": 4, "passage_text": "Apple won."}]]})
    scores = {"positive": 0.9, "neutral": 0.06, "negative": 0.04}

    def hosted_must_not_run(request: object) -> None:
        raise AssertionError("the hosted model was called")

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(cli.SupabaseClient, "from_env", classmethod(lambda cls, env: supabase))
    monkeypatch.setattr(cli, "LocalFinbert", lambda: lambda text: Sentiment("ProsusAI/finbert", scores))
    monkeypatch.setattr(cli, "complete", hosted_must_not_run)
    assert cli_main(["score", "--finbert", "local"]) == 0
    assert capsys.readouterr().out == "score: scored 1 passages with local FinBERT\n"
    ((_, filters, values),) = supabase.updates
    assert (filters["id"], values["sentiment_label"]) == ("eq.4", "positive")



def test_a_podcast_list_is_https_feed_addresses_one_per_line() -> None:
    feeds = read_feed_list(
        "# shows\n\nhttps://feeds.example.com/private/TOKEN.rss  # paid\n"
        "  https://other.example.com/show\nhttps://feeds.example.com/private/TOKEN.rss\n"
    )
    assert feeds == [
        FeedLine(3, "https://feeds.example.com/private/TOKEN.rss"),
        FeedLine(4, "https://other.example.com/show"),
    ]
    assert feeds[0].label == "line 3 (feeds.example.com)"
    assert read_feed_list("# nothing yet\n") == []
    for bad in ("http://feeds.example.com/show", "feeds.example.com/show", "https://feeds.example.com/a b"):
        with pytest.raises(LoaderError, match="^Podcast list line 2 is not an https:// feed address$"):
            read_feed_list(f"# shows\n{bad}\n")


def podcast_item(
    guid: str | None,
    published: str | None,
    title: str,
    audio: str | None = "https://audio.example.com/episode.mp3",
    transcripts: tuple[tuple[str, str], ...] = (),
) -> str:
    parts = [f"<title>{title}</title>", "<description>Notes that mention Apple.</description>"]
    if guid is not None:
        parts.append(f'<guid isPermaLink="false">{guid}</guid>')
    if published is not None:
        parts.append(f"<pubDate>{published}</pubDate>")
    if audio is not None:
        parts.append(f'<enclosure url="{audio}" length="34202926" type="audio/mpeg" />')
    parts.extend(f'<podcast:transcript url="{url}" type="{kind}" language="en" />' for kind, url in transcripts)
    return "<item>" + "".join(parts) + "</item>"


def podcast_feed(*items: str, title: str = "Odd Lots") -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        'xmlns:podcast="https://podcastindex.org/namespace/1.0" version="2.0"><channel>'
        f"<title>{title}</title><language>en-US</language>" + "".join(items) + "</channel></rss>"
    ).encode()


def episode_id(identity: str) -> str:
    return "podcast:" + hashlib.sha256(identity.encode()).hexdigest()[:32]


def test_a_feed_becomes_its_episodes_since_a_date_oldest_first() -> None:
    body = podcast_feed(
        podcast_item(
            "g-4",
            "Sat, 14 Nov 2026 09:00:00 +0000",
            "How Wall Street\n  Is Bracing",
            "https://audio.example.com/4.mp3?a=1&amp;b=2",
            (
                ("application/x-subrip", "https://t.example.com/4.srt2"),
                ("application/srt", "https://t.example.com/4.srt"),
                ("text/html", "https://t.example.com/4.html"),
                ("TEXT/VTT", "https://t.example.com/4.vtt?x=1&amp;y=2"),
                ("application/json", "https://t.example.com/4.json"),
                ("text/plain", "https://t.example.com/4.txt"),
                ("text/plain", "https://t.example.com/4-second.txt"),
            ),
        ),
        podcast_item("g-4", "Sat, 14 Nov 2026 10:00:00 +0000", "The same episode listed again"),
        podcast_item("g-3", "Thu, 12 Nov 2026 23:30:00 -0500", "Late in New York", None, (("text/html", "https://t/3"),)),
        podcast_item(None, "Thu, 12 Nov 2026 10:00:00 -0000", "No guid", "https://audio.example.com/no-guid.mp3"),
        podcast_item("g-2", "Thu, 12 Nov 2026 00:00:00 +0000", "On the first moment of the day"),
        podcast_item("g-1", "Wed, 11 Nov 2026 23:59:59 +0000", "A second too early"),
        podcast_item("g-0", "Wed, 11 Nov 2026 20:00:00 -0500", "Already the 12th in UTC"),
        podcast_item("g-x", None, "No date"),
        podcast_item("g-y", "soon", "Unreadable date"),
        podcast_item(None, "Fri, 13 Nov 2026 09:00:00 +0000", "A note with neither guid nor audio", None),
    )
    feed = parse_feed(body, date(2026, 11, 12))
    assert (feed.show, feed.undated) == ("Odd Lots", 2)
    assert [(episode.title, episode.published_at) for episode in feed.episodes] == [
        ("On the first moment of the day", datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc)),
        ("Already the 12th in UTC", datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc)),
        ("No guid", datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)),
        ("Late in New York", datetime(2026, 11, 13, 4, 30, tzinfo=timezone.utc)),
        ("How Wall Street Is Bracing", datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)),
    ]
    assert feed.episodes[2].published_at.tzinfo is not None
    assert [episode.id for episode in feed.episodes] == [
        episode_id("g-2"),
        episode_id("g-0"),
        episode_id("https://audio.example.com/no-guid.mp3"),
        episode_id("g-3"),
        episode_id("g-4"),
    ]
    late, bracing = feed.episodes[3:]
    assert (late.show, late.audio_url, late.transcript_urls) == ("Odd Lots", None, ())
    assert bracing.audio_url == "https://audio.example.com/4.mp3?a=1&b=2"
    assert bracing.transcript_urls == (
        "https://t.example.com/4.txt",
        "https://t.example.com/4.vtt?x=1&y=2",
        "https://t.example.com/4.srt",
        "https://t.example.com/4.srt2",
    )


def test_an_episode_time_without_a_zone_is_read_as_utc_wherever_the_loader_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        feed = parse_feed(podcast_feed(podcast_item("g", "Thu, 12 Nov 2026 10:00:00 -0000", "No zone")), date(2026, 11, 12))
    finally:
        monkeypatch.undo()
        time.tzset()
    assert [episode.published_at for episode in feed.episodes] == [datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)]


@pytest.mark.parametrize(
    "body",
    [
        b"<html><body>Not found</body></html>",
        b'<feed xmlns="http://www.w3.org/2005/Atom"><title>An Atom feed</title></feed>',
        b"<rss><channel><title>Cut off",
        b'<rss version="2.0"></rss>',
        b"<channel><item><guid>g</guid></item></channel>",
        b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaa">]><rss><channel><title>&a;</title></channel></rss>',
        b"",
    ],
)
def test_a_page_that_is_not_an_rss_feed_is_an_error(body: bytes) -> None:
    with pytest.raises(LoaderError, match="^it is not an RSS podcast feed$"):
        parse_feed(body, date(2026, 11, 12))


def test_a_feed_is_read_from_its_address_within_the_feed_size_limit() -> None:
    fetch = FakeFetch({"https://feeds.example.com/show": podcast_feed(podcast_item("g", "Sat, 14 Nov 2026 09:00:00 +0000", "One"))})
    feed = read_feed(FeedLine(1, "https://feeds.example.com/show"), date(2026, 11, 12), fetch)
    assert [episode.title for episode in feed.episodes] == ["One"]
    assert fetch.calls == [("https://feeds.example.com/show", MAX_FEED_BYTES, False)]


# The four shapes below are cut from real published transcripts.
OMNY_PLAIN = (
    b"00:00:02\nSpeaker 1: Bloomberg Audio Studios. Podcasts.\n\n00:00:18\n"
    b"Speaker 3: The NVIDIA story is so fast moving. See https://example.com/x for more.\n\n01:02:03.5\nSpeaker 1: That's right.\n"
)
BRACKETED_PLAIN = "\ufeff[00:00:00] Cameron: Welcome to QAV.\r[00:00:25] Tony: I am, yeah.\r[1:02:03] Cameron: At [00:10] we sold.\r".encode()
WEBVTT = (
    b"WEBVTT - How Wall Street Is Bracing\n\nNOTE made by a machine\nand not checked\n\n"
    b"1\n0:00:25.090 --> 0:00:29.870\n<v Speaker 3>Tracy, the NVIDIA story\n\n"
    b"0:00:30.010 --> 0:00:31.960 align:start\n<v Speaker 3>is so <i>fast</i> moving\nthat we have to be careful.\n\n"
)
SUBRIP = (
    b"1\r\n00:00:00,040 --> 00:00:03,640\r\nWelcome to the Deep Dive. \r\nThis week we are analyzing what \r\n\r\n"
    b"2\r\n00:00:03,640 --> 00:00:09,760\r\nhappens when a $1.23 \r\ntrillion market\r\n\r\n3\r\n00:00:09,760 --> 00:00:12,080\r\n2026\r\n"
)
HEADED_PLAIN = b"Shanaysha Sauls (00:00)\nSo I always go for the simple first.\n\nMarcus Schafer (00:24)\nThis is episode 43.\n"


@pytest.mark.parametrize(
    ("body", "text"),
    [
        (
            OMNY_PLAIN,
            "Speaker 1: Bloomberg Audio Studios. Podcasts. Speaker 3: The NVIDIA story is so fast moving. "
            "See for more. Speaker 1: That's right.",
        ),
        (BRACKETED_PLAIN, "Cameron: Welcome to QAV. Tony: I am, yeah. Cameron: At [00:10] we sold."),
        (WEBVTT, "Tracy, the NVIDIA story is so fast moving that we have to be careful."),
        (SUBRIP, "Welcome to the Deep Dive. This week we are analyzing what happens when a $1.23 trillion market 2026"),
        (HEADED_PLAIN, "Shanaysha Sauls (00:00) So I always go for the simple first. Marcus Schafer (00:24) This is episode 43."),
        (b"\xff\xfe not text \x00", "\ufffd\ufffd not text"),
        (b"00:00:02\n\n[00:10]\n", ""),
    ],
)
def test_a_published_transcript_becomes_one_running_text(body: bytes, text: str) -> None:
    assert transcript_text(body) == text


class FakeFetch:
    """Serves canned files in place of the public web, and records what was asked for."""

    def __init__(self, files: dict[str, bytes | LoaderError]) -> None:
        self.files = files
        self.calls: list[tuple[str, int, bool]] = []

    def __call__(self, url: str, max_bytes: int, sink: BinaryIO | None) -> HttpResponse:
        self.calls.append((url, max_bytes, sink is not None))
        answer = self.files[url]
        if isinstance(answer, LoaderError):
            raise answer
        if sink is None:
            return HttpResponse(200, answer)
        sink.write(answer)
        return HttpResponse(200, b"")


def an_episode(transcripts: tuple[str, ...] = (), audio: str | None = "https://audio.example.com/1.mp3") -> Episode:
    published = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
    return Episode(episode_id("g-1"), "Odd Lots", "Bracing", published, transcripts, audio)


def must_not_transcribe(audio: Path) -> Transcript:
    raise AssertionError("the audio was transcribed")


def test_an_episode_uses_the_first_published_transcript_that_can_be_read() -> None:
    fetch = FakeFetch(
        {
            "https://t.example.com/1.txt": HttpStatusError(404, "HTTP 404 from t.example.com"),
            "https://t.example.com/1.vtt": b"WEBVTT\n\n",
            "https://t.example.com/1.srt": SUBRIP,
            "https://t.example.com/1.srt2": b"never read",
        }
    )
    links = tuple(f"https://t.example.com/1.{kind}" for kind in ("txt", "vtt", "srt", "srt2"))
    transcript = episode_transcript(an_episode(links), must_not_transcribe, fetch)
    assert transcript == Transcript(
        "Welcome to the Deep Dive. This week we are analyzing what happens when a $1.23 trillion market 2026", "publisher"
    )
    assert fetch.calls == [(link, MAX_TRANSCRIPT_BYTES, False) for link in links[:3]]


def test_an_episode_without_a_readable_transcript_has_its_audio_transcribed_from_a_file_that_is_then_removed() -> None:
    fetch = FakeFetch(
        {
            "https://t.example.com/1.txt": LoaderError("Request to t.example.com failed: timed out"),
            "https://audio.example.com/1.mp3": b"ID3 audio bytes",
        }
    )
    heard: list[tuple[Path, bytes]] = []

    def transcribe(audio: Path) -> Transcript:
        heard.append((audio, audio.read_bytes()))
        return Transcript(" Nvidia beat.\u200b See https://example.com/ad  now. ", "Systran/faster-whisper-small.en")

    transcript = episode_transcript(an_episode(("https://t.example.com/1.txt",)), transcribe, fetch)
    assert transcript == Transcript("Nvidia beat. See now.", "Systran/faster-whisper-small.en")
    assert fetch.calls == [
        ("https://t.example.com/1.txt", MAX_TRANSCRIPT_BYTES, False),
        ("https://audio.example.com/1.mp3", MAX_AUDIO_BYTES, True),
    ]
    ((audio, content),) = heard
    assert content == b"ID3 audio bytes" and not audio.exists()


def test_an_episode_that_cannot_become_text_says_why() -> None:
    fetch = FakeFetch(
        {
            "https://t.example.com/1.txt": HttpStatusError(404, "HTTP 404 from t.example.com"),
            "https://t.example.com/empty.txt": b"00:00:02\n",
            "https://audio.example.com/1.mp3": LoaderError("The file at audio.example.com is larger than 1000 MB"),
            "https://audio.example.com/music.mp3": b"ID3",
        }
    )
    assert episode_transcript(an_episode(), None, fetch) is None
    assert episode_transcript(an_episode(("https://t.example.com/empty.txt",)), None, fetch) is None
    assert fetch.calls == [("https://t.example.com/empty.txt", MAX_TRANSCRIPT_BYTES, False)]
    with pytest.raises(EpisodeUnavailable, match="^its transcript could not be read: HTTP 404 from t.example.com$"):
        episode_transcript(an_episode(("https://t.example.com/1.txt",)), None, fetch)
    with pytest.raises(EpisodeUnavailable, match="^it has neither a readable transcript nor audio$"):
        episode_transcript(an_episode(audio=None), must_not_transcribe, fetch)
    with pytest.raises(EpisodeUnavailable, match="^its audio could not be downloaded: The file at audio.example.com is larger"):
        episode_transcript(an_episode(), must_not_transcribe, fetch)
    with pytest.raises(EpisodeUnavailable, match="^no speech was found in its audio$"):
        episode_transcript(an_episode(audio="https://audio.example.com/music.mp3"), lambda audio: Transcript(" \u200b ", "m"), fetch)

    def model_missing(audio: Path) -> Transcript:
        raise LoaderError("Cannot load the model")

    with pytest.raises(LoaderError, match="^Cannot load the model$") as raised:
        episode_transcript(an_episode(audio="https://audio.example.com/music.mp3"), model_missing, fetch)
    assert not isinstance(raised.value, EpisodeUnavailable)


def test_a_listening_app_named_in_a_transcript_is_not_a_mention_of_its_company() -> None:
    watchlist = Watchlist.parse("AAPL: Apple\nGOOGL: Google\nAMZN: Amazon\n")
    text = (
        "Find the Bloomberg channel on Apple Podcasts and follow. "
        + "Filler sentence here. " * 40
        + "We are on Apple Podcasts, Google Podcasts and Amazon Music, and Apple reported today. "
        + "Filler sentence here. " * 40
        + "Amazon Music lost subscribers to Amazon Musical and to Google Podcast."
    )
    chunks = newsletters.passage_chunks(text)
    assert len(chunks) == 3
    assert [(passage.index, passage.tickers) for passage in ticker_passages(text, watchlist)] == [
        (0, ["AAPL"]),
        (1, ["AAPL", "AMZN", "GOOGL"]),
        (2, ["AMZN", "GOOGL"]),
    ]
    found = episode_passages(text, watchlist)
    assert [(passage.index, passage.tickers) for passage in found] == [(1, ["AAPL"]), (2, ["AMZN"])]
    assert [passage.text for passage in found] == [chunks[1], chunks[2]]


def podcast_episodes() -> list[Episode]:
    return [
        Episode(episode_id("g-1"), "Odd Lots", "Megaspeed", datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc), (), None),
        Episode(episode_id("g-2"), "Odd Lots", "Bracing", datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc), (), None),
    ]


def test_podcast_episodes_are_stored_once_as_text_with_their_ticker_passages() -> None:
    first, second = podcast_episodes()
    asked: list[tuple[FeedLine, date]] = []
    transcripts = {
        first.id: Transcript("Nobody is named here. Apple and Google both won.", "publisher"),
        second.id: Transcript("Follow us on Apple Podcasts.", "Systran/faster-whisper-small.en"),
    }

    def feed_of(feed: FeedLine, since: date) -> Feed:
        asked.append((feed, since))
        return Feed("Odd Lots", [first, second], 0)

    supabase = FakeSupabase()
    line = FeedLine(3, "https://feeds.example.com/private/TOKEN.rss")
    loaded = load_podcasts(supabase, [line], feed_of, lambda episode: transcripts[episode.id], WATCHLIST, date(2026, 11, 12))
    assert (loaded.added, loaded.passages, loaded.warnings, loaded.failed_feeds) == (2, 1, [], [])
    assert asked == [(line, date(2026, 11, 12))]
    assert supabase.upserts == [
        (
            "newsletters",
            [
                {
                    "id": first.id,
                    "source": "podcast",
                    "sender": "podcast",
                    "publication": "Odd Lots",
                    "subject": "Megaspeed",
                    "received_at": "2026-11-12T09:00:00+00:00",
                    "body_text": "Nobody is named here. Apple and Google both won.",
                    "transcript_source": "publisher",
                }
            ],
            "id",
            True,
        ),
        (
            "newsletter_passages",
            [
                {
                    "newsletter_id": first.id,
                    "passage_index": 0,
                    "passage_text": "Nobody is named here. Apple and Google both won.",
                    "tickers": ["AAPL", "GOOGL"],
                }
            ],
            "newsletter_id,passage_index",
            False,
        ),
        (
            "newsletters",
            [
                {
                    "id": second.id,
                    "source": "podcast",
                    "sender": "podcast",
                    "publication": "Odd Lots",
                    "subject": "Bracing",
                    "received_at": "2026-11-14T09:00:00+00:00",
                    "body_text": "Follow us on Apple Podcasts.",
                    "transcript_source": "Systran/faster-whisper-small.en",
                }
            ],
            "id",
            True,
        ),
        ("newsletter_passages", [], "newsletter_id,passage_index", False),
    ]
    assert supabase.selects[0] == ("newsletters", {"id": in_filter([first.id, second.id])})
    assert "TOKEN" not in repr(supabase.upserts)


def test_a_stored_episode_is_not_transcribed_again_and_follows_the_watchlist() -> None:
    first, second = podcast_episodes()
    stored_text = "Tesla slid. " + "Filler sentence here. " * 40 + "Apple won."
    assert len(stored_text) > MAX_PASSAGE_CHARS
    supabase = FakeSupabase(
        {
            "newsletters": [[{"id": first.id, "body_text": stored_text}]],
            "newsletter_passages": [
                [
                    {"id": 7, "newsletter_id": first.id, "passage_index": 0},
                    {"id": 8, "newsletter_id": first.id, "passage_index": 1},
                ]
            ],
        }
    )
    transcribed: list[str] = []

    def transcript_of(episode: Episode) -> Transcript:
        transcribed.append(episode.id)
        return Transcript("Nvidia beat.", "publisher")

    watchlist = Watchlist.parse("AAPL: Apple\nNVDA: Nvidia\n")
    loaded = load_podcasts(
        supabase, [FeedLine(1, "https://f.example.com/a")], lambda feed, since: Feed("Odd Lots", [first, second], 0),
        transcript_of, watchlist, date(2026, 11, 12),
    )
    assert transcribed == [second.id]
    assert (loaded.added, loaded.passages) == (1, 2)
    assert [(table, [row.get("tickers", row.get("id")) for row in rows]) for table, rows, _, _ in supabase.upserts] == [
        ("newsletter_passages", [["AAPL"]]),
        ("newsletters", [second.id]),
        ("newsletter_passages", [["NVDA"]]),
    ]
    assert supabase.upserts[0][1][0]["passage_index"] == 1
    # The stored passage about Tesla, which the watchlist no longer names, stops counting.
    assert supabase.updates == [("newsletter_passages", {"id": 'in.("7")'}, {"tickers": []})]


def test_stored_episodes_are_looked_up_fifty_at_a_time() -> None:
    episodes = [
        Episode(episode_id(f"g-{number}"), "Show", f"Episode {number}", datetime(2026, 11, 12, tzinfo=timezone.utc), (), None)
        for number in range(51)
    ]
    supabase = FakeSupabase(
        {"newsletters": [[], [{"id": episodes[50].id, "body_text": "Apple won."}]]}
    )
    loaded = load_podcasts(
        supabase, [FeedLine(1, "https://f.example.com/a")], lambda feed, since: Feed("Show", episodes, 0),
        lambda episode: None, WATCHLIST, date(2026, 11, 12),
    )
    lookups = [filters for table, filters in supabase.selects if table == "newsletters"]
    assert lookups == [{"id": in_filter(e.id for e in episodes[:50])}, {"id": in_filter([episodes[50].id])}]
    assert (loaded.added, loaded.passages) == (0, 1)
    assert loaded.warnings == [
        "50 episodes of Show were left out because they have no published transcript in plain text, WebVTT or "
        "SubRip: `poetry install --extras podcasts` lets this machine transcribe their audio"
    ]


def test_an_unreadable_episode_or_feed_does_not_stop_the_ones_after_it() -> None:
    first, second = podcast_episodes()
    other = Episode(episode_id("g-9"), "", "Untitled show's episode", datetime(2026, 11, 13, tzinfo=timezone.utc), (), None)

    def feed_of(feed: FeedLine, since: date) -> Feed:
        if feed.line == 2:
            raise HttpStatusError(404, "HTTP 404 from gone.example.com")
        return Feed("Odd Lots", [first, second], 3) if feed.line == 1 else Feed("", [other], 0)

    def transcript_of(episode: Episode) -> Transcript | None:
        if episode.id == first.id:
            raise EpisodeUnavailable("its audio could not be downloaded: HTTP 403 from audio.example.com")
        return Transcript("Apple won.", "publisher")

    supabase = FakeSupabase()
    feeds = [
        FeedLine(1, "https://f.example.com/a"),
        FeedLine(2, "https://gone.example.com/private/TOKEN"),
        FeedLine(5, "https://f.example.com/b"),
    ]
    loaded = load_podcasts(supabase, feeds, feed_of, transcript_of, WATCHLIST, date(2026, 11, 12))
    assert (loaded.added, loaded.passages) == (2, 2)
    assert loaded.failed_feeds == ["line 2 (gone.example.com): HTTP 404 from gone.example.com"]
    assert loaded.warnings == [
        "3 episodes of Odd Lots have no readable publication time and were left out",
        "Odd Lots: 'Megaspeed' was left out because its audio could not be downloaded: HTTP 403 from audio.example.com",
    ]
    stored = [rows[0] for table, rows, _, _ in supabase.upserts if table == "newsletters"]
    assert [(row["id"], row["publication"]) for row in stored] == [(second.id, "Odd Lots"), (other.id, None)]

    def broken(episode: Episode) -> Transcript:
        raise LoaderError("Cannot load the model")

    with pytest.raises(LoaderError, match="^Cannot load the model$"):
        load_podcasts(FakeSupabase(), feeds, feed_of, broken, WATCHLIST, date(2026, 11, 12))


class PublicSite(BaseHTTPRequestHandler):
    """A stand-in for the public web that `fetch_public` reads through the real HTTP layer."""

    agents: list[str | None] = []

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.agents.append(self.headers.get("User-Agent"))
        port = self.server.server_address[1]
        redirects = {
            "/moved": "/file",
            "/hop": f"http://127.0.0.1:{port}/moved",
            "/away": "http://media.example.com/private-token/audio.mp3",
            "/odd": "ftp://media.example.com/audio.mp3",
        }
        if self.path in redirects:
            self.send_response(302)
            self.send_header("Location", redirects[self.path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/file" or self.path == "/cut":
            body = b"x" * 3000
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body[:1000] if self.path == "/cut" else body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "9")
        self.end_headers()
        self.wfile.write(b"not here\n")


@pytest.fixture
def public_site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), PublicSite)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    PublicSite.agents.clear()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_a_public_file_is_read_through_redirects_into_memory_or_a_file(public_site: str) -> None:
    response = fetch_public(f"{public_site}/hop", 3000)
    assert (response.status, response.body, response.headers["content-type"]) == (200, b"x" * 3000, "audio/mpeg")
    assert PublicSite.agents == ["research-data/0.1"] * 3
    sink = io.BytesIO()
    assert fetch_public(f" {public_site}/file\n", 3000, sink).body == b""
    assert sink.getvalue() == b"x" * 3000


def test_a_public_file_that_is_too_large_or_cut_short_is_an_error(public_site: str) -> None:
    sink = io.BytesIO()
    with pytest.raises(LoaderError, match="^The file at 127.0.0.1 is larger than 0 MB$"):
        fetch_public(f"{public_site}/file", 2999, sink)
    assert len(sink.getvalue()) < 3000
    with pytest.raises(LoaderError, match="^Download from 127.0.0.1 stopped after 1000 of 3000 bytes$"):
        fetch_public(f"{public_site}/cut", 3000)


def test_public_download_errors_name_the_host_and_never_the_rest_of_the_address(public_site: str) -> None:
    with pytest.raises(HttpStatusError, match="^HTTP 404 from 127.0.0.1$") as missing:
        fetch_public(f"{public_site}/private-token/feed.rss", 3000)
    assert missing.value.status == 404
    with pytest.raises(LoaderError, match="^The address at media.example.com is not HTTPS$"):
        fetch_public(f"{public_site}/away", 3000)
    with pytest.raises(LoaderError, match="^An address is not a plain web address$"):
        fetch_public(f"{public_site}/odd", 3000)
    with pytest.raises(LoaderError, match="^The address at feeds.example.com is not HTTPS$"):
        fetch_public("http://feeds.example.com/private-token/feed.rss", 3000)
    for address in ("ftp://feeds.example.com/private-token", "https://user@feeds.example.com/private-token", "feed.rss", ""):
        with pytest.raises(LoaderError, match="^An address is not a plain web address$"):
            fetch_public(address, 3000)
    closed = ThreadingHTTPServer(("127.0.0.1", 0), PublicSite)
    port = closed.server_address[1]
    closed.server_close()
    with pytest.raises(LoaderError, match="^Request to 127.0.0.1 failed: ") as refused:
        fetch_public(f"http://127.0.0.1:{port}/private-token/feed.rss", 3000)
    assert "private-token" not in str(refused.value)


def test_cli_stores_podcast_episodes_and_fails_after_loading_when_a_feed_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    first, second = podcast_episodes()
    feeds = tmp_path / "podcasts.txt"
    feeds.write_text("https://f.example.com/a\n# https://f.example.com/off\nhttps://gone.example.com/private/TOKEN\n")
    watchlist = tmp_path / "watchlist.txt"
    watchlist.write_text("AAPL: Apple\n")
    supabase = FakeSupabase()
    asked: list[tuple[str, date]] = []
    models: list[str] = []

    def feed_of(feed: FeedLine, since: date) -> Feed:
        asked.append((feed.url, since))
        if "gone" in feed.url:
            raise HttpStatusError(404, "HTTP 404 from gone.example.com")
        return Feed("Odd Lots", [first, second], 1)

    def transcript_of(episode: Episode, transcribe: object) -> Transcript | None:
        return transcribe(tmp_path / "audio") if episode.id == first.id or transcribe else None  # type: ignore[operator]

    def loaded_model() -> object:
        models.append("loaded")
        return lambda audio: Transcript(f"Apple won, said {audio.name}.", "Systran/faster-whisper-small.en")

    monkeypatch.setattr(cli.SupabaseClient, "from_env", classmethod(lambda cls, env: supabase))
    monkeypatch.setattr(cli, "read_feed", feed_of)
    monkeypatch.setattr(cli, "episode_transcript", transcript_of)
    monkeypatch.setattr(cli, "whisper_installed", lambda: True)
    monkeypatch.setattr(cli, "LocalWhisper", loaded_model)
    argv = ["podcasts", "--since", "2026-11-12", "--podcasts", str(feeds), "--watchlist", str(watchlist)]
    assert cli_main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == "podcasts: read 1 of 2 feeds, stored 2 new episodes; 2 passages mention a ticker\n"
    assert captured.err == (
        "podcasts: transcribing Odd Lots: Megaspeed\n"
        "podcasts: transcribing Odd Lots: Bracing\n"
        "podcasts: warning: 1 episodes of Odd Lots have no readable publication time and were left out\n"
        "research-data: Podcast feeds that could not be read: line 3 (gone.example.com): HTTP 404 from gone.example.com\n"
    )
    assert "TOKEN" not in captured.out + captured.err
    assert asked == [
        ("https://f.example.com/a", date(2026, 11, 12)),
        ("https://gone.example.com/private/TOKEN", date(2026, 11, 12)),
    ]
    assert models == ["loaded"]
    stored = [rows[0] for table, rows, _, _ in supabase.upserts if table == "newsletters"]
    assert [row["body_text"] for row in stored] == ["Apple won, said audio."] * 2

    # Without faster-whisper there is nothing to transcribe with, and the model is never loaded.
    monkeypatch.setattr(cli, "whisper_installed", lambda: False)
    monkeypatch.setattr(cli, "episode_transcript", lambda episode, transcribe: None if transcribe is None else 1 / 0)
    feeds.write_text("https://f.example.com/a\n")
    assert cli_main(argv) == 0
    captured = capsys.readouterr()
    assert captured.out == "podcasts: read 1 of 1 feeds, stored 0 new episodes; 0 passages mention a ticker\n"
    assert "2 episodes of Odd Lots were left out because they have no published transcript" in captured.err
    assert models == ["loaded"]


def test_the_podcasts_step_needs_a_list_with_at_least_one_feed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(cli.SupabaseClient, "from_env", classmethod(lambda cls, env: FakeSupabase()))
    feeds = tmp_path / "podcasts.txt"
    assert cli_main(["podcasts", "--podcasts", str(feeds)]) == 1
    assert capsys.readouterr().err == f"research-data: Cannot read podcast list {feeds}: No such file or directory\n"
    feeds.write_text("# https://f.example.com/off\n")
    assert cli_main(["podcasts", "--podcasts", str(feeds)]) == 1
    assert capsys.readouterr().err == f"research-data: {feeds} lists no podcast feeds\n"
    feeds.write_text("http://f.example.com/a\n")
    assert cli_main(["podcasts", "--podcasts", str(feeds)]) == 1
    assert capsys.readouterr().err == "research-data: Podcast list line 1 is not an https:// feed address\n"


def test_the_podcast_list_shipped_with_the_package_follows_no_feed_until_one_is_chosen() -> None:
    assert read_feed_list(cli.DEFAULT_PODCASTS.read_text(encoding="utf-8")) == []


def test_local_whisper_names_the_extra_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_whisper, "WhisperModel", None)
    assert not local_whisper.whisper_installed()
    with pytest.raises(LoaderError, match="run `poetry install --extras podcasts`"):
        LocalWhisper()


@pytest.mark.skipif(not local_whisper.whisper_installed(), reason="needs the podcasts extra; downloads the model on first use")
def test_local_whisper_reports_audio_it_cannot_decode_as_one_unavailable_episode(tmp_path: Path) -> None:
    audio = tmp_path / "episode.audio"
    audio.write_bytes(b"<html>Not found</html>" * 100)
    with pytest.raises(EpisodeUnavailable, match="^its audio could not be decoded: Invalid data found when processing input$"):
        LocalWhisper()(audio)
