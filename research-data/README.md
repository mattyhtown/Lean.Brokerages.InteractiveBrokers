# research-data

Builds the training dataset for a signal model that predicts forward equity returns: Substack newsletters from Gmail, FinBERT sentiment per ticker passage, and daily prices from Databento, all stored in Supabase.

## What it loads

| Step | Reads | Writes |
|---|---|---|
| `newsletters` | Every message under a Gmail label, over IMAP, without marking it read | `newsletters` (one row per message) and `newsletter_passages` (the passages that mention a ticker) |
| `score` | Passages with no sentiment yet | FinBERT label and positive / neutral / negative scores on each passage |
| `prices` | Databento `EQUS.SUMMARY` daily bars for every watchlist symbol and every mentioned ticker | `daily_prices` |

The `training_rows` view joins them into one row per ticker and trading day: passage and newsletter counts, mean sentiment, the close, and the 1-day and 5-day forward close-to-close returns.

Every step can be run again over the same dates without creating duplicates.

## Setup

1. Create the tables by running `supabase/schema.sql` in the Supabase SQL editor.
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

## Run

```bash
poetry install
poetry run research-data all
```

`all` runs the three steps in order over the last 7 days, which suits a daily schedule. To backfill:

```bash
poetry run research-data newsletters --since 2026-01-01
poetry run research-data score --limit 20000
poetry run research-data prices --start 2025-10-01
```

`score` saves each result as it arrives, so a run that stops partway resumes from the next unscored passage.

## Scoring on your own machine

`--finbert local` runs the same `ProsusAI/finbert` model on this machine instead of calling Hugging Face Inference, so `HF_TOKEN` is not needed:

```bash
poetry install --extras local
poetry run research-data score --finbert local
```

It also works with `all`. The extra installs PyTorch and Transformers, about 5 GB on Linux, and the first run downloads the model, about 440 MB. Scoring uses a GPU when PyTorch finds one and the CPU otherwise. The model is pinned to one revision, so stored scores do not change if the model repository is updated.

## How mentions become rows

- **Tickers.** A passage counts as mentioning a ticker when it contains `$SYMBOL`, `NASDAQ: SYMBOL` (also `NYSE`, `NYSEARCA`, `AMEX`), or a symbol or company name from `watchlist.txt`. Most newsletters name companies rather than tickers, so the watchlist decides most of what is captured. After extending it, run `newsletters` again over the older dates.
- **Passages.** Each paragraph is one passage, split at sentence ends when longer than 800 characters so it fits FinBERT's input limit. A passage that names several tickers gives all of them the same score.
- **Dates.** A newsletter counts toward the first trading day whose 16:00 New York close comes after Gmail received it, so a row never uses text from after the close it is paired with. A mention with no loaded close within the next 5 days is left out.
- **Returns.** `forward_return_1d` and `forward_return_5d` run from that close to the close 1 and 5 trading days later, and are null until those days are loaded.

## Limits to know before training

- Prices are unadjusted. A split or large dividend shows up as a false return across that date; exclude or adjust those rows.
- On early-close days (13:00 New York), a newsletter that arrives between 13:00 and 16:00 is paired with that day's close even though the close has already happened.
- Only ticker-days with at least one scored mention get a row. Days without newsletter coverage are absent, not zero.
- Options flow is not loaded yet.

## Tests

```bash
poetry run pytest
```

With the `local` extra installed, one test also loads the real model and scores three sentences with it.
