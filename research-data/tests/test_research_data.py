from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
import ssl
from typing import Mapping
import urllib.parse

import pytest

from research_data import cli, local_finbert, newsletters
from research_data.cli import main as cli_main
from research_data.http import HttpResponse, HttpStatusError, LoaderError, require_secure_url
from research_data.local_finbert import LocalFinbert
from research_data.news import NewsDigest, digest_items, fetch_digest
from research_data.newsletters import (
    MAX_PASSAGE_CHARS,
    Newsletter,
    clean_text,
    fetch_newsletters,
    parse_message,
    ticker_passages,
)
from research_data.options import OptionsDay, OptionsDownload, download_options, parse_cores
from research_data.pipeline import (
    Sentiment,
    load_news,
    load_newsletters,
    load_options,
    load_prices,
    score_passages,
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


def test_options_are_requested_ten_tickers_at_a_time_and_kept_from_the_start_date() -> None:
    symbols = ["AAPL", *(f"T{letter}" for letter in "ABCDEFGHIJK")]
    not_found = HttpStatusError(404, "HTTP 404 from api.orats.io/datav2/hist/cores: Not Found")
    http = FakeHttp([HttpResponse(200, CORES), not_found])
    result = download_options("orats-token", symbols, date(2019, 11, 28), http)
    assert result.days == [CORE_DAY]
    assert result.warnings == [
        "ORATS returned no options data since 2019-11-28 for: TA, TB, TC, TD, TE, TF, TG, TH, TI, TJ, TK"
    ]
    queries = [urllib.parse.parse_qs(urllib.parse.urlparse(url).query) for _, url, _, _ in http.calls]
    assert [method for method, _, _, _ in http.calls] == ["GET", "GET"]
    assert http.calls[0][1].startswith("https://api.orats.io/datav2/hist/cores?")
    assert [query["ticker"] for query in queries] == [["AAPL,TA,TB,TC,TD,TE,TF,TG,TH,TI"], ["TJ,TK"]]
    assert queries[0]["token"] == ["orats-token"]
    assert queries[0]["fields"] == ["ticker,tradeDate,updatedAt,iv30d,ivPctile1y,orHv20d,slope,contango,cVolu,pVolu,cOi,pOi"]


def test_an_orats_error_other_than_not_found_stops_the_download() -> None:
    http = FakeHttp([HttpStatusError(403, "HTTP 403 from api.orats.io/datav2/hist/cores: denied")])
    with pytest.raises(LoaderError, match="HTTP 403"):
        download_options("bad-token", ["AAPL"], date(2019, 11, 28), http)


def test_options_cover_watchlist_and_mentioned_symbols() -> None:
    supabase = FakeSupabase({"mentioned_tickers": [[{"symbol": "TSLA"}]]})
    requested: list[tuple[list[str], date]] = []

    def download(symbols: list[str], start: date) -> OptionsDownload:
        requested.append((symbols, start))
        return OptionsDownload([CORE_DAY], ["ORATS returned no options data"])

    result = load_options(supabase, download, WATCHLIST, date(2019, 11, 1))
    assert requested == [(["AAPL", "GOOGL", "NVDA", "TSLA"], date(2019, 11, 1))]
    assert result == (4, 1, ["ORATS returned no options data"])
    assert supabase.upserts == [
        (
            "options_daily",
            [
                {
                    "symbol": "AAPL",
                    "trade_date": "2019-11-29",
                    **CORE_DAY.metrics,
                    "source_updated_at": "2019-11-29T17:46:01+00:00",
                }
            ],
            "symbol,trade_date",
            False,
        )
    ]


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


def test_news_is_fetched_once_per_symbol_and_day_and_tagged_with_its_symbol() -> None:
    supabase = FakeSupabase({"newsletters": [[{"id": "perplexity:GOOGL:2026-11-10"}]]})
    fetched_at = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
    asked: list[tuple[str, list[str]]] = []

    def fetch(symbol: str, names: list[str]) -> NewsDigest:
        asked.append((symbol, names))
        items = ["Apple beat estimates.", "Apple and Google settled."] if symbol == "AAPL" else []
        return NewsDigest(symbol, "sonar", fetched_at, items)

    assert load_news(supabase, fetch, WATCHLIST, date(2026, 11, 10)) == (2, 1, 2)
    assert asked == [("AAPL", ["Apple"]), ("NVDA", [])]
    assert supabase.selects == [("newsletters", {"source": "eq.perplexity", "id": "like.*:2026-11-10"})]
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


def test_all_leaves_out_news_and_options_when_their_keys_are_not_set(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[str] = []
    for name in ("PERPLEXITY_API_KEY", "ORATS_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli.SupabaseClient, "from_env", classmethod(lambda cls, env: FakeSupabase()))
    monkeypatch.setattr(cli, "_STEPS", {name: lambda args, env, supabase, name=name: ran.append(name) for name in cli._STEPS})
    assert cli_main(["all"]) == 0
    assert ran == ["newsletters", "score", "prices"]
    assert capsys.readouterr().err == (
        "news: skipped because PERPLEXITY_API_KEY is not set\n"
        "options: skipped because ORATS_API_TOKEN is not set\n"
    )
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-key")
    monkeypatch.setenv("ORATS_API_TOKEN", "orats-token")
    ran.clear()
    assert cli_main(["all"]) == 0
    assert ran == ["newsletters", "news", "score", "prices", "options"]


def test_a_step_run_by_name_reports_its_missing_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("PERPLEXITY_API_KEY", "ORATS_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli.SupabaseClient, "from_env", classmethod(lambda cls, env: FakeSupabase()))
    assert cli_main(["news"]) == 1
    assert "research-data: Missing PERPLEXITY_API_KEY" in capsys.readouterr().err
    assert cli_main(["options"]) == 1
    assert "research-data: Missing ORATS_API_TOKEN" in capsys.readouterr().err


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

