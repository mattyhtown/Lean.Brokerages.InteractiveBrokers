# research-data

Builds the training dataset for a signal model that predicts forward equity returns: Substack newsletters from Gmail, daily news digests from Perplexity, FinBERT sentiment on both, daily options metrics from ORATS, and daily prices from Databento, all stored in Supabase.

## What it loads

| Step | Reads | Writes |
|---|---|---|
| `newsletters` | Every message under a Gmail label, over IMAP, without marking it read | `newsletters` (one row per message) and `newsletter_passages` (the passages that mention a ticker) |
| `news` | Perplexity `sonar`, asked once per watchlist symbol for the last 24 hours of news | `newsletters` rows with `source = 'perplexity'` and one passage per news item |
| `score` | Passages with no sentiment yet, from either source | FinBERT label and positive / neutral / negative scores on each passage |
| `prices` | Databento `EQUS.SUMMARY` daily bars for every watchlist symbol and every mentioned ticker | `daily_prices` |
| `options` | ORATS end-of-day core data for the same symbols | `options_daily` |

The `training_rows` view joins them into one row per ticker and trading day:

| Columns | From |
|---|---|
| `passage_count`, `newsletter_count`, `sentiment_positive`, `sentiment_neutral`, `sentiment_negative`, `sentiment_net` | Newsletters |
| `news_item_count`, `news_sentiment_positive`, `news_sentiment_neutral`, `news_sentiment_negative`, `news_sentiment_net` | Perplexity news digests |
| `iv_30d`, `iv_percentile_1y`, `hv_20d`, `skew_slope`, `contango`, `call_volume`, `put_volume`, `call_open_interest`, `put_open_interest` | ORATS |
| `close`, `forward_return_1d`, `forward_return_5d` | Databento |

Every step can be run again over the same dates without creating duplicates.

## Setup

1. Create the tables by running the files in `supabase/migrations/` in order in the Supabase SQL editor. A project set up before options and news were added only needs `002_options_and_news.sql`.
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
| `ORATS_API_TOKEN` | `options` | An ORATS Data API token. Each 10 symbols cost one request per run. |

## Run

```bash
poetry install
poetry run research-data all
```

`all` runs every step in the order of the table above over the last 7 days, which suits a daily schedule. It leaves out `news` when `PERPLEXITY_API_KEY` is not set and `options` when `ORATS_API_TOKEN` is not set, and says so. To backfill:

```bash
poetry run research-data newsletters --since 2026-01-01
poetry run research-data score --limit 20000
poetry run research-data prices --start 2025-10-01
poetry run research-data options --start 2025-10-01
```

News cannot be backfilled: a digest is what Perplexity found at the moment it was asked, so `news` only ever collects today's. Run it every day you want covered, at about the same time. A second run on the same New York calendar day skips the symbols that already have a digest.

`score` saves each result as it arrives, so a run that stops partway resumes from the next unscored passage.

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
- **Dates.** A newsletter or news digest counts toward the first trading day whose 16:00 New York close comes after it arrived, so a row never uses text from after the close it is paired with. Text with no trading day within the next 5 days is left out.
- **Options.** A row carries the ORATS metrics for its own trading day. ORATS takes its end-of-day snapshot about 14 minutes before the close, so those values exist before the close the row is paired with. `options_daily.source_updated_at` holds ORATS's own timestamp for each row.
- **Trading days.** The calendar is every date on which any symbol has a loaded close.
- **Returns.** `forward_return_1d` and `forward_return_5d` run from that close to the close 1 and 5 trading days later. They are null until those days are loaded, and stay null if the symbol has no bar on that day.

## Limits to know before training

- Prices are unadjusted. A split or large dividend shows up as a false return across that date; exclude or adjust those rows.
- On early-close days (13:00 New York), a newsletter or news digest that arrives between 13:00 and 16:00 is paired with that day's close even though the close has already happened.
- News sentiment is FinBERT's reading of sentences Perplexity wrote, not of the original articles, and the same question can be answered differently on another run.
- News history starts on the first day `news` ran. Earlier rows have `news_item_count` 0 because nothing was collected, not because there was no news.
- The options columns are daily totals and levels from ORATS, not individual trades or sweeps.

## Tests

```bash
poetry run pytest
```

With the `local` extra installed, one test also loads the real model and scores three sentences with it.
