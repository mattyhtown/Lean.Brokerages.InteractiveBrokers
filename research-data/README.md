# research-data

Builds the training dataset for a signal model that predicts forward equity returns: Substack newsletters from Gmail, daily news digests from Perplexity, headlines from Benzinga, FinBERT sentiment on all three, Alpha Vantage's own news sentiment scores, daily options metrics from ORATS, daily prices from Databento, split and dividend factors from QuantConnect, and the San Francisco Fed's daily news sentiment index through OpenBB, all stored in Supabase.

## What it loads

| Step | Reads | Writes |
|---|---|---|
| `newsletters` | Every message under a Gmail label, over IMAP, without marking it read | `newsletters` (one row per message) and `newsletter_passages` (the passages that mention a ticker) |
| `news` | Perplexity `sonar`, asked once per watchlist symbol for the last 24 hours of news | `newsletters` rows with `source = 'perplexity'` and one passage per news item |
| `benzinga` | Benzinga's news API, asked for each watchlist symbol and each mentioned ticker for the headlines published since `--since` | `newsletters` rows with `source = 'benzinga'` and one passage per headline |
| `alphavantage` | Alpha Vantage's `NEWS_SENTIMENT` feed, asked for each watchlist symbol and each mentioned ticker for the articles published since `--since` | `alphavantage_news` (one row per article and symbol, with Alpha Vantage's relevance and sentiment scores for that symbol) |
| `score` | Passages with no sentiment yet, from any of the three text sources | FinBERT label and positive / neutral / negative scores on each passage |
| `prices` | Databento `EQUS.SUMMARY` daily bars for the same symbols | `daily_prices` |
| `options` | ORATS end-of-day core data for the same symbols, one trade date at a time | `options_daily` |
| `adjustments` | QuantConnect's split and dividend factor files for the same symbols, from a LEAN data folder on this machine | `price_adjustments` |
| `openbb` | The San Francisco Fed Daily News Sentiment Index, fetched through OpenBB | `market_news_sentiment` (one row per calendar day) |

The `training_rows` view joins them into one row per ticker and trading day:

| Columns | From |
|---|---|
| `passage_count`, `newsletter_count`, `sentiment_positive`, `sentiment_neutral`, `sentiment_negative`, `sentiment_net` | Newsletters |
| `news_item_count`, `news_sentiment_positive`, `news_sentiment_neutral`, `news_sentiment_negative`, `news_sentiment_net` | Perplexity news digests |
| `benzinga_headline_count`, `benzinga_sentiment_positive`, `benzinga_sentiment_neutral`, `benzinga_sentiment_negative`, `benzinga_sentiment_net` | Benzinga headlines |
| `alphavantage_article_count`, `alphavantage_sentiment`, `alphavantage_relevance` | Alpha Vantage scored news |
| `market_news_sentiment` | San Francisco Fed index, the same for every symbol on a day |
| `iv_30d`, `iv_percentile_1y`, `hv_20d`, `skew_slope`, `contango`, `call_volume`, `put_volume`, `call_open_interest`, `put_open_interest` | ORATS |
| `close` | Databento |
| `forward_return_1d`, `forward_return_5d`, `returns_adjusted` | Databento closes, adjusted with QuantConnect's factors |

Every step can be run again over the same dates without creating duplicates.

## Setup

1. Create the tables by running the files in `supabase/migrations/` in order in the Supabase SQL editor. A project that already has the earlier files only needs the ones added since; `005_alphavantage_news.sql` is the newest.
2. Edit `watchlist.txt` to the symbols and company names you want to track.
3. Set these environment variables:

| Variable | Used by | Value |
|---|---|---|
| `SUPABASE_URL` | all steps | `https://<project-ref>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | all steps | The project's secret (service role) key. `SUPABASE_SECRET_KEY` also works. |
| `GMAIL_ADDRESS` | `newsletters` | The Gmail address that receives the newsletters |
| `GMAIL_APP_PASSWORD` | `newsletters` | A [Google app password](https://myaccount.google.com/apppasswords); needs 2-Step Verification and IMAP enabled |
| `HF_TOKEN` | `score`, unless run with `--finbert local` | A Hugging Face token with Inference Providers permission |
| `DATABENTO_API_KEY` | `prices` | A Databento API key. Downloads are billed to it by data volume. |
| `PERPLEXITY_API_KEY` | `news` | A Perplexity API key. Each symbol costs one `sonar` request per day. |
| `BENZINGA_API_KEY` | `benzinga` | A Benzinga API key with access to the news API |
| `ALPHAVANTAGE_API_KEY` | `alphavantage` | An Alpha Vantage API key on a premium plan. `NEWS_SENTIMENT` is a premium endpoint: a free key is refused. Each symbol costs at least one request per run. |
| `ORATS_API_TOKEN` | `options` | An ORATS Data API token. Each weekday loaded costs one request per 10 symbols. |
| `QUANTCONNECT_LEAN_DATA_DIR` | `adjustments` | The LEAN data folder that holds `equity/usa/factor_files/`. See below. |

## Run

```bash
poetry install
poetry run research-data all
```

`all` runs every step in the order of the table above over the last 7 days, which suits a daily schedule. It leaves out `news` when `PERPLEXITY_API_KEY` is not set, `benzinga` when `BENZINGA_API_KEY` is not set, `alphavantage` when `ALPHAVANTAGE_API_KEY` is not set, `options` when `ORATS_API_TOKEN` is not set, `adjustments` when `QUANTCONNECT_LEAN_DATA_DIR` is not set, and `openbb` when OpenBB is not installed, and says so. To backfill:

```bash
poetry run research-data newsletters --since 2026-01-01
poetry run research-data benzinga --since 2026-01-01
poetry run research-data alphavantage --since 2026-01-01
poetry run research-data score --limit 20000
poetry run research-data prices --start 2025-10-01
poetry run research-data options --start 2025-10-01
```

News cannot be backfilled: a digest is what Perplexity found at the moment it was asked, so `news` only ever collects today's. Run it every day you want covered, at about the same time. A second run on the same New York calendar day skips the symbols that already have a digest.

`score` saves each result as it arrives, so a run that stops partway resumes from the next unscored passage. A Benzinga backfill adds one passage per headline and symbol, which can be far more than the default `--limit` of 1000.

## Market news sentiment through OpenBB

`openbb` loads the San Francisco Fed's [Daily News Sentiment Index](https://www.frbsf.org/research-and-insights/data-and-indicators/daily-news-sentiment-index/), one value per calendar day for US news as a whole, using OpenBB's Federal Reserve extension. It needs no API key:

```bash
poetry install --extras openbb
poetry run research-data openbb
```

The extra installs about 350 MB. To keep local FinBERT as well, install both: `poetry install --extras "local openbb"`. The first run stores the whole index back to 1980; later runs add the days published since.

## Split and dividend factors from QuantConnect

`adjustments` reads files that are already on this machine. It does not call QuantConnect and cannot spend anything on your account. The files come from QuantConnect's US Equity Security Master dataset, which needs a paid subscription to download. Fetch them with the [LEAN CLI](https://www.quantconnect.com/docs/v2/lean-cli):

```bash
lean data download --dataset "US Equity Security Master"
poetry run research-data adjustments
```

The download adds one `factor_files_YYYYMMDD.zip` per day to `equity/usa/factor_files/` in the LEAN data folder. `adjustments` uses the newest one and replaces each symbol's stored factors with it. Download again before each run: a row's returns are only marked as adjusted when the factor files are at least as new as the last close those returns use.

## Scoring on your own machine

`--finbert local` runs the same `ProsusAI/finbert` model on this machine instead of calling Hugging Face Inference, so `HF_TOKEN` is not needed:

```bash
poetry install --extras local
poetry run research-data score --finbert local
```

It also works with `all`. The extra installs PyTorch and Transformers, about 5 GB on Linux, and the first run downloads the model, about 440 MB. Scoring uses a GPU when PyTorch finds one and the CPU otherwise. The model is pinned to one revision, so stored scores do not change if the model repository is updated.

## How mentions become rows

- **Rows.** Every ticker and trading day with a loaded close gets a row. On a day without text the counts are 0 and the sentiment columns are null; on a day without an ORATS row the options columns are null.
- **Tickers.** A passage counts as mentioning a ticker when it contains `$SYMBOL`, `NASDAQ: SYMBOL` (also `NYSE`, `NYSEARCA`, `AMEX`), or a symbol or company name from `watchlist.txt`. Most newsletters name companies rather than tickers, so the watchlist decides most of what is captured. After editing it, run `newsletters` again over the older dates: passages gain the added tickers, and a passage whose only names were removed stops counting toward any ticker while keeping its score.
- **Passages.** Each paragraph is one passage, split at sentence ends when longer than 800 characters so it fits FinBERT's input limit. A passage that names several tickers gives all of them the same score.
- **News.** Each line of a Perplexity digest is one news item and counts only toward the symbol the digest was asked about. A digest is timestamped when its answer arrives. Symbols that are only mentioned in newsletters and are not in `watchlist.txt` get no digest.
- **Benzinga.** Each headline is one passage. Only the headline is scored, not the article. It counts toward the symbol it was requested for, so a headline Benzinga files under several loaded symbols is stored and scored once for each. It is timestamped with Benzinga's `created` time.
- **Alpha Vantage.** Each article is stored once for each loaded symbol Alpha Vantage scored it for, with two numbers Alpha Vantage computes itself: how relevant the article is to that symbol (above 0, up to 1) and its sentiment toward that symbol (Alpha Vantage calls -0.35 and below bearish and 0.35 and above bullish). FinBERT is not run on these articles. On a row, `alphavantage_sentiment` is the average of the day's sentiment scores weighted by relevance, and `alphavantage_relevance` is the plain average relevance; both are null on a day without articles. An article is timestamped with Alpha Vantage's `time_published`.
- **Dates.** A newsletter, news digest, Benzinga headline or Alpha Vantage article counts toward the first trading day whose 16:00 New York close comes after it arrived, so a row never uses text from after the close it is paired with. Text with no trading day within the next 5 days is left out.
- **Market sentiment.** `market_news_sentiment` is the index value for the calendar day 21 days before the row's trading day, and null when that day is not loaded. The Fed updates the index about once a week. In the four weeks checked, its file was modified on a Thursday or Friday and ended on the Sunday before, which makes the newest published day up to 12 days old; 21 days leaves room for one missed release.
- **Options.** A row carries the ORATS metrics for its own trading day. ORATS takes its end-of-day snapshot about 14 minutes before the close, so those values exist before the close the row is paired with. `options_daily.source_updated_at` holds ORATS's own timestamp for each row.
- **Trading days.** The calendar is every date on which any symbol has a loaded close.
- **Returns.** `forward_return_1d` and `forward_return_5d` run from that close to the close 1 and 5 trading days later. They are null until those days are loaded, and stay null if the symbol has no bar on that day.
- **Adjustments.** Both closes are scaled by QuantConnect's price and split factors before the return is taken, so a split or dividend between them does not show up as a price move. `returns_adjusted` is true when the symbol has factors dated on or after the last close the row's returns use. `close` is the raw Databento close.

## Limits to know before training

- A row with `returns_adjusted` false may have a split or dividend inside its returns that was not removed. That covers every row until `adjustments` has run, symbols QuantConnect has no factor file for, and the newest days until newer factor files are loaded. Train on the rows where it is true.
- Factors are matched by today's ticker. A company that changed its ticker has no factors under the old one.
- Text counts only on a row of its own symbol. When the symbol has no bar on the trading day its text falls on, that text is not counted on any row.
- On early-close days (13:00 New York), text that arrives between 13:00 and 16:00 is paired with that day's close even though the close has already happened.
- News sentiment is FinBERT's reading of sentences Perplexity wrote, not of the original articles, and the same question can be answered differently on another run.
- News history starts on the first day `news` ran. Earlier rows have `news_item_count` 0 because nothing was collected, not because there was no news.
- A Benzinga headline is stored as it read when it was first loaded. In a backfill that is today's wording, which Benzinga may have edited after publication.
- Alpha Vantage's feed collects articles from many publishers, Benzinga among them, so the Alpha Vantage and Benzinga columns partly describe the same articles.
- An Alpha Vantage article keeps the scores it had when it was first loaded. In a backfill those are the scores Alpha Vantage serves today; whether it has recalculated them since publication is not known.
- The `alphavantage` step was checked against Alpha Vantage's published sample responses, not against a paid key. How its feed behaves over a long backfill is untested: the step stops with an error rather than skip articles if the feed does not come oldest first or stops advancing.
- The San Francisco Fed recalculates the whole index with every release: across the four releases checked, past values moved by up to about 0.015, against a standard deviation of about 0.12. A stored day keeps the value from the release it was first loaded from, so the days stored by the first run carry today's revised values, not the ones published at the time.
- `market_news_sentiment` assumes the Fed never misses more than one weekly release in a row. After a longer gap, rows from the gap are later filled with values that were not yet published on their date.
- The options columns are daily totals and levels from ORATS, not individual trades or sweeps.

## Tests

```bash
poetry run pytest
```

With the `local` extra installed, one test also loads the real model and scores three sentences with it. With the `openbb` extra installed, one test also downloads the real index.
