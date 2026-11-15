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
from research_data.http import HttpResponse, LoaderError, require_secure_url
from research_data.local_finbert import LocalFinbert
from research_data.newsletters import (
    MAX_PASSAGE_CHARS,
    Newsletter,
    clean_text,
    fetch_newsletters,
    parse_message,
    ticker_passages,
)
from research_data.pipeline import Sentiment, load_newsletters, load_prices, score_passages
from research_data.prices import DailyBar, PriceDownload, download_daily_bars, parse_bars
from research_data.supabase import SupabaseClient
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
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout_seconds: int
    ) -> HttpResponse:
        self.calls.append((method, url, dict(headers), body))
        return self.responses.pop(0)


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

