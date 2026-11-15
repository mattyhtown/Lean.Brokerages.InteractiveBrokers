# research-data

Builds the training dataset for a signal model that predicts forward equity returns: Substack newsletters from Gmail, daily news digests from Perplexity, headlines from Benzinga, podcast transcripts, FinBERT sentiment on all four, Alpha Vantage's own news sentiment scores, daily options metrics from ORATS, daily prices from Databento, split and dividend factors from QuantConnect, and the San Francisco Fed's daily news sentiment index through OpenBB, all stored in Supabase. It also retrains the model on that dataset, tests it on days it was not trained on, and keeps the scores and predictions in Supabase.

## What it loads

| Step | Reads | Writes |
|---|---|---|
| `newsletters` | Every message under a Gmail label, over IMAP, without marking it read | `newsletters` (one row per message) and `newsletter_passages` (the passages that mention a ticker) |
| `news` | Perplexity `sonar`, asked once per watchlist symbol for the last 24 hours of news | `newsletters` rows with `source = 'perplexity'` and one passage per news item |
| `benzinga` | Benzinga's news API, asked for each watchlist symbol and each mentioned ticker for the headlines published since `--since` | `newsletters` rows with `source = 'benzinga'` and one passage per headline |
| `alphavantage` | Alpha Vantage's `NEWS_SENTIMENT` feed, asked for each watchlist symbol and each mentioned ticker for the articles published since `--since` | `alphavantage_news` (one row per article and symbol, with Alpha Vantage's relevance and sentiment scores for that symbol) |
| `podcasts` | The RSS feeds listed in `podcasts.txt`: each episode published since `--since`, as the transcript its publisher links to or, without one, its audio transcribed on this machine | `newsletters` rows with `source = 'podcast'` holding the whole transcript, and the passages that mention a ticker |
| `score` | Passages with no sentiment yet, from any of the four text sources | FinBERT label and positive / neutral / negative scores on each passage |
| `prices` | Databento `EQUS.SUMMARY` daily bars for the same symbols | `daily_prices` |
| `options` | ORATS end-of-day core data for the same symbols, one trade date at a time | `options_daily` |
| `adjustments` | QuantConnect's split and dividend factor files for the same symbols, from a LEAN data folder on this machine | `price_adjustments` |
| `openbb` | The San Francisco Fed Daily News Sentiment Index, fetched through OpenBB | `market_news_sentiment` (one row per calendar day) |
| `train` | `training_rows` and the predictions stored by earlier runs | `model_runs` (one row per retrain and horizon, with its scores) and `model_predictions` (one row per symbol, day and horizon) |

The `training_rows` view joins them into one row per ticker and trading day:

| Columns | From |
|---|---|
| `passage_count`, `newsletter_count`, `sentiment_positive`, `sentiment_neutral`, `sentiment_negative`, `sentiment_net` | Newsletters |
| `news_item_count`, `news_sentiment_positive`, `news_sentiment_neutral`, `news_sentiment_negative`, `news_sentiment_net` | Perplexity news digests |
| `benzinga_headline_count`, `benzinga_sentiment_positive`, `benzinga_sentiment_neutral`, `benzinga_sentiment_negative`, `benzinga_sentiment_net` | Benzinga headlines |
| `podcast_passage_count`, `podcast_episode_count`, `podcast_sentiment_positive`, `podcast_sentiment_neutral`, `podcast_sentiment_negative`, `podcast_sentiment_net` | Podcast episodes |
| `alphavantage_article_count`, `alphavantage_sentiment`, `alphavantage_relevance` | Alpha Vantage scored news |
| `market_news_sentiment` | San Francisco Fed index, the same for every symbol on a day |
| `iv_30d`, `iv_percentile_1y`, `hv_20d`, `skew_slope`, `contango`, `call_volume`, `put_volume`, `call_open_interest`, `put_open_interest` | ORATS |
| `close` | Databento |
| `forward_return_1d`, `forward_return_5d`, `returns_adjusted` | Databento closes, adjusted with QuantConnect's factors |

Every loading step can be run again over the same dates without creating duplicates. `train` adds a `model_runs` row each time it runs and never changes a stored prediction.

## Setup

1. Create the tables by running the files in `supabase/migrations/` in order in the Supabase SQL editor. A project that already has the earlier files only needs the ones added since; `007_model.sql` is the newest.
2. Edit `watchlist.txt` to the symbols and company names you want to track.
3. To load podcasts, add their feed addresses to `podcasts.txt`. It ships with none. See [Podcasts](#podcasts).
4. Set these environment variables:

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

`all` runs every step in the order of the table above over the last 7 days, which suits a daily schedule. It leaves out `news` when `PERPLEXITY_API_KEY` is not set, `benzinga` when `BENZINGA_API_KEY` is not set, `alphavantage` when `ALPHAVANTAGE_API_KEY` is not set, `options` when `ORATS_API_TOKEN` is not set, `adjustments` when `QUANTCONNECT_LEAN_DATA_DIR` is not set, `podcasts` when `podcasts.txt` lists no feed, and `openbb` when OpenBB is not installed, and says so. To backfill:

```bash
poetry run research-data newsletters --since 2026-01-01
poetry run research-data benzinga --since 2026-01-01
poetry run research-data alphavantage --since 2026-01-01
poetry run research-data podcasts --since 2026-01-01
poetry run research-data score --limit 20000
poetry run research-data prices --start 2025-10-01
poetry run research-data options --start 2025-10-01
```

News cannot be backfilled: a digest is what Perplexity found at the moment it was asked, so `news` only ever collects today's. Run it every day you want covered, at about the same time. A second run on the same New York calendar day skips the symbols that already have a digest.

`score` saves each result as it arrives, so a run that stops partway resumes from the next unscored passage. A Benzinga backfill adds one passage per headline and symbol, which can be far more than the default `--limit` of 1000.

## Training the model

```bash
poetry install --extras train
poetry run research-data train
```

`train` fits two models on `training_rows`, one for the return over the next trading day and one for the next 5, and takes a few seconds. Each is a set of gradient-boosted trees (scikit-learn's `HistGradientBoostingRegressor`) with fixed settings. Its inputs are every column of `training_rows` except the symbol, the date, the raw close and the returns; a column that is empty in every row it trains on, such as a source that is not loaded yet, is left out. Nothing is saved to disk: the model is fitted again from the table on every run, so new rows and new columns are picked up without any other step.

**The test on days it was not trained on.** Before anything is stored, the model is walked forward through the history. It is trained on the first 60 sessions whose return was already known, predicts the next 20 sessions, is trained again on everything known by then, predicts the next 20, and so on. A return over 5 days is only known 5 sessions later, so the model that predicts a block never trains on a return that ends after the block's first day. This needs 81 sessions of closes for the 1-day model and 89 for the 5-day one; with fewer, `train` says so and stores nothing for that model.

**What is stored.** Each run adds one row per model to `model_runs`. The `oos_` columns are the walk-forward test and the `live_` columns are the same measures over the stored predictions whose return has since become known:

| Column | Meaning |
|---|---|
| `rank_ic` | Each day, the rank correlation across symbols between the predictions and the returns that followed, averaged over days. 0 is no skill, 1 is a perfect ordering. Only days with 5 or more symbols count. |
| `rank_ic_t` | `rank_ic` against how much it varies from day to day. 5-day returns overlap, so their days count for a fifth. |
| `hit_rate`, `baseline_hit_rate` | The share of predictions with the sign of the return, and the same for always predicting the average return of the rows trained on. |
| `r2_vs_baseline` | 1 minus the model's squared error divided by that of always predicting that average. Above 0 beats it, below 0 is worse than it. |
| `top_minus_bottom` | Each day, the return of the third of symbols predicted highest minus the third predicted lowest, averaged over days. |

To see whether it improves as rows accumulate:

```sql
select trained_at, horizon_days, training_rows, oos_rank_ic, oos_r2_vs_baseline,
       live_predictions, live_rank_ic, live_hit_rate
from model_runs order by horizon_days, trained_at;
```

**Predictions.** After the test, the model is trained on every row with a known return and predicts the newest session, the one whose return nobody knows yet. Those rows go to `model_predictions` and are never changed: a second run for the same session keeps the first prediction. `model_prediction_outcomes` shows each prediction next to the return that followed, null until the later close is loaded. Only the newest session is predicted, so a day on which `train` did not run has no prediction.

## Podcasts

`podcasts` needs no API key. List the shows to follow in `podcasts.txt`, one RSS feed address per line; a show's feed address is usually linked from its website or its hosting page. Only `https://` addresses are accepted.

A private feed, such as a paid Substack or Patreon feed, has an address that works as a password. Keep such a list outside the repository and point at it with `--podcasts /path/to/list`. The loader never prints or stores a feed's address: messages name the line of the list and the host.

For each episode the step first looks for a transcript the publisher links to in the feed (`<podcast:transcript>`) as plain text, WebVTT or SubRip. When there is none, it downloads the audio and transcribes it on this machine with [Whisper](https://github.com/SYSTRAN/faster-whisper), which needs an extra:

```bash
poetry install --extras podcasts
poetry run research-data podcasts
```

The extra installs about 350 MB and the first transcription downloads the model, `Systran/faster-whisper-small.en`, about 460 MB. On 4 CPU cores it transcribed about 11 minutes of speech per minute, so a one-hour episode takes 5 to 6 minutes. Without the extra, episodes that have no published transcript are left out and the step says how many. To keep the other extras as well, name them together: `poetry install --extras "local podcasts"`.

An episode's text is fetched or transcribed once. Later runs over the same dates only redo the search for tickers in the stored text, so they take seconds and pick up an edited watchlist. `newsletters.transcript_source` records where each text came from: `publisher`, or the name of the model. The model is pinned to one revision.

An episode that cannot be read is reported and tried again on the next run. When a feed cannot be read, the other feeds are still loaded and the step then ends with an error naming it.

## Market news sentiment through OpenBB

`openbb` loads the San Francisco Fed's [Daily News Sentiment Index](https://www.frbsf.org/research-and-insights/data-and-indicators/daily-news-sentiment-index/), one value per calendar day for US news as a whole, using OpenBB's Federal Reserve extension. It needs no API key:

```bash
poetry install --extras openbb
poetry run research-data openbb
```

The extra installs about 350 MB. To keep other extras as well, name them together: `poetry install --extras "local openbb"`. The first run stores the whole index back to 1980; later runs add the days published since.

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
- **Podcasts.** A transcript is cut into passages of up to 800 characters at sentence ends, and the ones that mention a ticker are scored like newsletter passages. "Apple Podcasts", "Google Podcasts" and "Amazon Music" do not count as mentions of those companies, because shows name them to say where to listen. An episode is timestamped with the publication time in its feed.
- **Alpha Vantage.** Each article is stored once for each loaded symbol Alpha Vantage scored it for, with two numbers Alpha Vantage computes itself: how relevant the article is to that symbol (above 0, up to 1) and its sentiment toward that symbol (Alpha Vantage calls -0.35 and below bearish and 0.35 and above bullish). FinBERT is not run on these articles. On a row, `alphavantage_sentiment` is the average of the day's sentiment scores weighted by relevance, and `alphavantage_relevance` is the plain average relevance; both are null on a day without articles. An article is timestamped with Alpha Vantage's `time_published`.
- **Dates.** A newsletter, news digest, Benzinga headline, podcast episode or Alpha Vantage article counts toward the first trading day whose 16:00 New York close comes after it arrived, so a row never uses text from after the close it is paired with. Text with no trading day within the next 5 days is left out.
- **Market sentiment.** `market_news_sentiment` is the index value for the calendar day 21 days before the row's trading day, and null when that day is not loaded. The Fed updates the index about once a week. In the four weeks checked, its file was modified on a Thursday or Friday and ended on the Sunday before, which makes the newest published day up to 12 days old; 21 days leaves room for one missed release.
- **Options.** A row carries the ORATS metrics for its own trading day. ORATS takes its end-of-day snapshot about 14 minutes before the close, so those values exist before the close the row is paired with. `options_daily.source_updated_at` holds ORATS's own timestamp for each row.
- **Trading days.** The calendar is every date on which any symbol has a loaded close.
- **Returns.** `forward_return_1d` and `forward_return_5d` run from that close to the close 1 and 5 trading days later. They are null until those days are loaded, and stay null if the symbol has no bar on that day.
- **Adjustments.** Both closes are scaled by QuantConnect's price and split factors before the return is taken, so a split or dividend between them does not show up as a price move. `returns_adjusted` is true when the symbol has factors dated on or after the last close the row's returns use. `close` is the raw Databento close.

## Limits to know before training

- A row with `returns_adjusted` false may have a split or dividend inside its returns that was not removed. That covers every row until `adjustments` has run, symbols QuantConnect has no factor file for, and the newest days until newer factor files are loaded. `train` uses those rows too, because without the factor files there would be nothing else to train on, and says how many there are. For fitting, returns beyond the 1st and 99th percentile are pulled in to those percentiles, which keeps an unremoved split from deciding the fit; the scores use the returns as they are, so an unremoved split does distort `r2_vs_baseline` and `top_minus_bottom`.
- A prediction is made after the close its return starts from. The 1-day return it predicts runs from that close to the next, so the part of it that happens before the next open has already passed by the time a morning run stores the prediction. The scores take no account of trading costs.
- Seven symbols are few. A day's rank correlation across 7 symbols is noisy, and a good score over a few months of them can be luck: with returns that no feature explained, 2 of 20 simulated one-year histories still showed a 1-day `rank_ic_t` beyond 2.
- Text history starts when each source was first loaded. Prices and options can be backfilled for years, but on those older rows the newsletter, news and podcast columns are empty, so the model cannot learn from text until months of it have accumulated.
- The model's settings were chosen once and not tuned on this data. The walk-forward test retrains every 20 sessions, while a daily schedule retrains every day.
- `train` was checked on made-up histories, not on real loaded data: the hosted tables were empty when it was written.
- Factors are matched by today's ticker. A company that changed its ticker has no factors under the old one.
- Text counts only on a row of its own symbol. When the symbol has no bar on the trading day its text falls on, that text is not counted on any row.
- On early-close days (13:00 New York), text that arrives between 13:00 and 16:00 is paired with that day's close even though the close has already happened.
- News sentiment is FinBERT's reading of sentences Perplexity wrote, not of the original articles, and the same question can be answered differently on another run.
- News history starts on the first day `news` ran. Earlier rows have `news_item_count` 0 because nothing was collected, not because there was no news.
- A Benzinga headline is stored as it read when it was first loaded. In a backfill that is today's wording, which Benzinga may have edited after publication.
- A podcast episode is dated by the publication time its feed states, which the publisher can set or change. In a backfill, an episode's transcript and audio are what the publisher serves today.
- A transcript covers everything in the episode: advertisements, promotions for other shows and the hosts' small talk are cut into passages and scored like the rest. Audio advertisements are often inserted per download, so two downloads of one episode can differ.
- Transcribing the same episode twice does not give the same text. One episode downloaded twice, with a different advertisement inserted each time, had Nvidia in 15 passages both times, but "while he was in charge at Microsoft" and "Alphabet" in one transcript were "while he was soft" and "alphabet" in the other, which lost a Microsoft and a Google passage. The stored text is whichever was made first.
- Podcast speech is mostly conversational, and FinBERT was trained on written financial news. In the four episodes checked, FinBERT labelled 82 of 87 passages neutral.
- Whisper mishears names. On one episode that also has a published transcript, it found Nvidia in 23 passages against the published transcript's 22 and Google in 1 against 1, but heard "a shipment to Apple from Qualcomm" as "a shipment to Qualcomm", losing the episode's one mention of Apple. A company name it spells differently from `watchlist.txt` is missed, and the watchlist is case-sensitive.
- Only English episodes are transcribed correctly: the model is English-only. Published transcripts in HTML or JSON are not read, audio served over plain `http://` is refused, and only the episodes in the feed document itself are seen, which for some shows is not the whole back catalogue.
- Alpha Vantage's feed collects articles from many publishers, Benzinga among them, so the Alpha Vantage and Benzinga columns partly describe the same articles.
- An Alpha Vantage article keeps the scores it had when it was first loaded. In a backfill those are the scores Alpha Vantage serves today; whether it has recalculated them since publication is not known.
- The `alphavantage` step was checked against Alpha Vantage's published sample responses, not against a paid key. How its feed behaves over a long backfill is untested: the step stops with an error rather than skip articles if the feed does not come oldest first, if one minute holds 1,000 or more articles about a symbol, or if the responses show Alpha Vantage sends fewer articles than the 1,000 asked for and one minute fills a whole response.
- One case the `alphavantage` step cannot tell apart: if Alpha Vantage sends fewer articles than asked for, and every article about a symbol since `--since` falls in one minute that holds more than it sends, the rest of that minute is missed without an error. A later run with the same `--since` stops with the error above once newer articles exist.
- The San Francisco Fed recalculates the whole index with every release: across the four releases checked, past values moved by up to about 0.015, against a standard deviation of about 0.12. A stored day keeps the value from the release it was first loaded from, so the days stored by the first run carry today's revised values, not the ones published at the time.
- `market_news_sentiment` assumes the Fed never misses more than one weekly release in a row. After a longer gap, rows from the gap are later filled with values that were not yet published on their date.
- The options columns are daily totals and levels from ORATS, not individual trades or sweeps.

## Tests

```bash
poetry run pytest
```

With the `local` extra installed, one test also loads the real model and scores three sentences with it. With the `openbb` extra installed, one test also downloads the real index. With the `podcasts` extra installed, one test also loads the real Whisper model. With the `train` extra installed, three tests also train the real scikit-learn model.
