# ODTE SPY — Interactive Brokers + multi-provider market data

Standalone **Python 3.11+** scaffold for **0DTE SPY** short vertical credit spreads via **Interactive Brokers**, with optional **Unusual Whales** flow gating and pluggable market-data providers (Alpaca, Coinbase, stubs for ORATS / You / Context / S&P Global / Daloopa / Perspective).

This package is independent of the C# LEAN brokerage plugin in the parent repo.

## Legal / risk

This software is for education and paper testing only. Options trading involves substantial risk. You are responsible for compliance (including **PDT** rules if applicable), licensing, and capital loss. No warranty.

## Prerequisites

1. **IB TWS or IB Gateway** with API enabled.
   - Configure **Socket port** (paper default **7497**, live **7496**).
   - Add **127.0.0.1** to **Trusted IPs** if connecting locally.
   - Enable **ActiveX and Socket Clients**.
2. Optional: **Unusual Whales** API key ([dashboard](https://unusualwhales.com/dashboard/api)).
3. Optional: **Alpaca** API key/secret for read-only SPY snapshot cross-check ([Alpaca Market Data](https://docs.alpaca.markets/docs/market-data-api)).
4. Python 3.11+.

## Safety defaults

| Variable | Default | Meaning |
|----------|---------|---------|
| `LIVE_TRADING` | `false` | Must be `true` for live account semantics |
| `READ_ONLY` | `true` | IB API read-only session |
| `ENABLE_ORDERS` | `false` | Must be `true` **and** `READ_ONLY=false` with paper or live to submit IB combo orders via `place_combo_order` |

## Provider matrix

| Provider ID | Status | Env / notes |
|-------------|--------|-------------|
| `ibkr` | **Implemented** | Execution + primary spot/chain via `ib_insync` |
| `unusual_whales` | **Implemented** | `UNUSUAL_WHALES_API_KEY`, `GET /api/stock/SPY/flow-recent`, `flow-alerts`, `option-activity/unusual` |
| `alpaca` | **Implemented** | `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `ALPACA_DATA_URL` — `GET /v2/stocks/SPY/snapshot`, optional `GET /v1beta1/options/snapshots/SPY` |
| `coinbase` | **Implemented** | Public `GET https://api.exchange.coinbase.com/products/{id}/stats` for BTC/ETH 24h % → `macro_risk_off` |
| `orats` | **Stub** | `ORATS_ENABLED=true` + `ORATS_API_KEY` (raises until implemented) |
| `you_finance` | **Stub** | `YOU_FINANCE_ENABLED` + `YOU_API_KEY` |
| `context` | **Stub** | `CONTEXT_ENABLED` + `CONTEXT_API_KEY` |
| `sp_global` | **Stub** | `SP_GLOBAL_ENABLED` + `SP_GLOBAL_API_KEY` |
| `daloopa` | **Stub** | `DALOOPA_ENABLED` + `DALOOPA_API_KEY` |
| `perspective` | **Stub** | `PERSPECTIVE_ENABLED` + `PERSPECTIVE_API_KEY` |
| `macro_news` | **Implemented** | Alpaca news (`GET /v1beta1/news`, uses the Alpaca keys) + tier-1 calendar in `config/macro_events.yaml`; see "Macro news and calendar" |

Enable providers: `PROVIDERS_ENABLED=ibkr,unusual_whales,alpaca,coinbase`

Missing keys are skipped gracefully (aggregator logs and continues).

## Unusual Whales modes

- `UW_MODE=alerts_only` — log aggregated flow bias; do not block entries on flow alone.
- `UW_MODE=gate` — use aggregated `MarketContext` flow bias/strength before credit spread entry.
- `UW_REQUIRED=true` — block when flow is weak, stale, or misaligned; otherwise fail open when `false` (default).

Strategy uses **aggregated** context (UW + any future flow fields), not UW alone.

## Install & run (paper)

```bash
cd odte-spy-ibkr
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env — keep ENABLE_ORDERS=false for paper dry-run

# Single iteration (no endless loop)
python -m odte_spy_ibkr --once
```

With TWS/Gateway running on `IB_HOST`/`IB_PORT`.

## Tests

```bash
pytest
```

No live IB or UW connection required (mocked/fixture tests).

## Architecture

- `providers/base.py` — `MarketContext`, `MarketContextDelta`, `BaseProvider`
- `providers/aggregate.py` — weighted merge
- `runner.py` — builds `MarketContext` each loop before strategy
- `strategy/flow_gate.py` — gates on aggregated context (+ macro risk-off)
- `strategy/interface.py` — shared `propose_entry` / `manage_open` for live + backtest

## Option chain skew (net calls)

Each chain refresh builds `ChainSkewSnapshot` (`chain/skew.py`):

- Aggregates call vs put volume/OI and premium-at-mid
- `net_call_bias` ∈ [−1, 1] on `MarketContext`
- Credit spread selection nets intended position: `MAX_NET_CALL_RISK` cap; auto side prefers put credit when skew is strongly bullish

Live: the first IB chain of each session is saved once to `data/cache/SPY/{date}.parquet` (`.json` without pyarrow) with its capture time. The backtest reads the same layout.

## Historical backtest

```bash
pip install -e ".[dev,backtest]"
python -m odte_spy_ibkr.backtest --from 2024-03-01 --to 2025-11-14 --report-json reports/summary.json
python -m odte_spy_ibkr.backtest --from 2024-03-01 --to 2025-11-14 --require-rolling-sharpe 1.5
python -m odte_spy_ibkr.backtest --from 2024-03-01 --to 2025-11-14 --walk-forward --require-rolling-sharpe 1.5
python -m odte_spy_ibkr.backtest --from 2024-03-01 --to 2025-11-14 --verbose  # no-entry reason histogram
```

`--from` defaults to two years before `--to`, `--to` to yesterday (ET). The process exits **1** when `--require-rolling-sharpe` (or `BACKTEST_REQUIRE_SHARPE_PASS=true`, which uses `BACKTEST_SHARPE_TARGET`) is not met, **0** otherwise.

The backtest calls the same `propose_entry` / `manage_open` functions as the live runner (`strategy/interface.py`). It steps through each session every `BACKTEST_DECISION_INTERVAL_MIN` minutes and at each step sees only what was known then: a 1-minute bar is visible once it has closed, a headline from its publish time, a UW snapshot from its capture time, a calendar outcome from its release time.

### Data and cache

Sessions are NYSE trading days on which the underlying has a same-day expiry (SPY: daily from 2022-11-15, Mon/Wed/Fri before that). Market holidays are excluded and 13:00 ET early closes shorten the session.

| What | Source | Cache file |
|------|--------|-----------|
| Underlying 1-min bars | Alpaca `GET /v2/stocks/{symbol}/bars` | `data/cache/bars/{ticker}/{date}.parquet` |
| 0DTE option 1-min bars | Alpaca `GET /v1beta1/options/bars`, bounded to that session | `data/cache/{ticker}/{date}.parquet` |
| Chain snapshot (alternative to option bars) | saved by the live runner, with capture time | `data/cache/{ticker}/{date}.parquet` |
| Headlines | Alpaca `GET /v1beta1/news` | `data/cache/macro_news/{date}.json` |
| Macro calendar | `python -m odte_spy_ibkr.macro backfill` | `data/cache/macro_calendar/{year}.json` |
| UW flow | `python -m odte_spy_ibkr.unusual_whales` | `data/cache/uw/sessions/{date}.jsonl`, `data/cache/uw/{date}.json` |

Files are parquet when pandas/pyarrow are installed (`backtest` extra), JSON otherwise. The cache is read first; Alpaca is called only for a finished session that is not cached, when `ALPACA_API_KEY`/`ALPACA_API_SECRET` are set and `alpaca` is in `PROVIDERS_BACKTEST_ENABLED`. A failed or empty download is not cached, so it is retried on the next run. Alpaca's option history starts in February 2024.

A cache file for a date holds that date's own data only. The backtest never calls Alpaca's option snapshot endpoint (it has no date parameter and returns the current chain). A chain snapshot is accepted only if it carries a capture time inside that session and only contracts expiring that day; anything else is ignored and the session counts as having no chain. To check a cache written by an earlier version:

```bash
python -m odte_spy_ibkr.backtest.cache_audit            # lists suspect files and why
python -m odte_spy_ibkr.backtest.cache_audit --purge    # deletes them so they are fetched again
```

### Chain coverage and what is scored

A session with no chain of its own, or no intraday bars for the underlying, is skipped. It is never filled with another day's chain or with generated prices. The summary and `--report-json` state:

- `chain_coverage`: sessions in the scored window, how many were replayed from their own chain (`own_chain`, by source) and how many have none (`no_chain`, with dates). `scope` is `full_range` or `walk_forward_holdout`.
- `scored_window`: `from`, `to` and session count of what was scored, and how many simulated sessions fall before and after it.
- `simulated`: `from`, `to` and session count of the whole simulated range, with its own coverage counts.
- `sessions_excluded`: sessions of the scored window left out, by reason (`no_chain`, `no_spot_bars`, `session_not_completed`, `uw_cache_missing_skip`, `macro_news_missing_strict`).
- `gate`: required and achieved rolling Sharpe, `passed`, and next to it `trade_count`, `traded_days`, `scored_days`, `scored_from`, `scored_to`.

With `--walk-forward` only the holdout slice of the last complete fold is scored (`BACKTEST_WALK_FORWARD_TRAIN_DAYS` then `BACKTEST_WALK_FORWARD_TEST_DAYS` sessions, folds counted from the start of the range). Every metric, the chain coverage and the gate then describe that slice. Sessions after the last complete fold are simulated but not scored; `scored_window.simulated_sessions_after` says how many. A holdout shorter than `BACKTEST_ROLLING_WINDOW_DAYS` has no rolling Sharpe, so with the defaults (60-day window, 20-session holdout) a walk-forward run reports it as undefined and fails any Sharpe requirement.

`--allow-synthetic` (off by default) fills sessions that have no real data with generated prices so the pipeline can be exercised without keys. Those sessions are reported in a separate `synthetic` block, are in no metric and never count toward the Sharpe requirement.

### Fills, PnL and Sharpe

- Entry credit = spread mid minus `BACKTEST_SLIPPAGE_TICKS` × $0.01; exit debit = spread mark plus the same slippage, kept within [0, width].
- Commission = `BACKTEST_COMMISSION_PER_CONTRACT` per contract per leg, on entry and on exit. A spread held to the close settles at intrinsic value with no exit commission.
- Option marks come from 1-minute trade bars, not quotes: bid/ask are taken as the last trade ∓ `BACKTEST_QUOTE_HALF_SPREAD`, and a mark older than `BACKTEST_MARK_MAX_AGE_MIN` minutes is not used. Deltas are Black-Scholes from those marks. Real quoted spreads on 0DTE wings can be wider than this model assumes.
- `STOP_LOSS_CREDIT_MULTIPLE` (default 0 = off) stops a spread when its mark reaches that multiple of the entry credit; `MAX_DAILY_LOSS` ends the day.
- Daily return = day PnL / equity at the start of that day (`BACKTEST_EQUITY_BASE` plus PnL so far). Days without a trade count as zero.
- Rolling Sharpe = `sqrt(252) * mean / std` over `BACKTEST_ROLLING_WINDOW_DAYS` (60) scored days, population std; undefined when std is 0 or there are fewer days than the window. The requirement is met when the highest rolling value reaches the target. There is no minimum number of trades; read `trade_count` and `traded_days` next to the result.

### Live Sharpe gate and journal

Off by default. With `SHARPE_GATE_ENABLED=true` the live runner pauses new entries while the Sharpe of the last `SHARPE_WINDOW_DAYS` journaled days is below `SHARPE_MIN` (1.5). Open positions are still managed while entries are paused. The gate acts only on a measured shortfall: with fewer journal rows than `SHARPE_MIN_OBS` (or the window), or a window with zero variance, the Sharpe is undefined and entries are not paused.

The runner appends one row per session to `JOURNAL_PATH` (`data/journal/daily_pnl.jsonl`) at the session roll or shutdown. Its PnL is computed from the entry credit and the exit mark, not from broker fills. To load realized PnL from a broker statement instead:

```bash
python -m odte_spy_ibkr.journal ingest --csv statement.csv --date-col Date --pnl-col "Realized P/L"
```

For a date that has statement rows, they are used in place of the runner's own row. Re-running the command on the same or an extended statement does not double count.

### Unusual Whales in the backtest

Flow is replayed from disk, never from the live API. Collect it during the session:

```bash
python -m odte_spy_ibkr.unusual_whales --once
```

This appends `data/cache/uw/sessions/{date}.jsonl` and refreshes `data/cache/uw/{date}.json` (`UW_BACKTEST_CACHE_DIR`). Each snapshot is used from its capture time; a record without one is not replayed. For a session with no UW cache, `UW_BACKTEST_MODE` decides:

| Mode | Effect |
|------|--------|
| `neutral` (default) | trade with neutral flow |
| `skip` | leave the session out of the scored sample |
| `block` | keep the session as a zero-PnL day with no entries |

### Macro news and calendar

`macro_news` blocks new entries from `MACRO_BLACKOUT_BEFORE_MIN` before to `MACRO_BLACKOUT_AFTER_MIN` after a tier-1 release, and scores headlines of the last `MACRO_HEADLINE_LOOKBACK_MIN` minutes (keyword score; `MACRO_HEADLINE_INTENSITY_BLOCK` blocks, `MACRO_SIZE_SCALE` downsizes). Live and backtest use the same rules.

```bash
python -m odte_spy_ibkr.macro backfill --year 2024 --source calendar_2024.csv   # .yaml, .json or .csv
```

writes `data/cache/macro_calendar/2024.json`, which the backtest prefers over `MACRO_CALENDAR_PATH` for that year. The dates in `config/macro_events.yaml` are examples; supply the real release calendar. `MACRO_BACKTEST_MODE=strict` leaves out sessions with no headline data, `permissive` (default) trades them on the calendar alone.

### Data gaps

- No keys and an empty cache means no session has a chain: the run reports zero coverage, zero trades and fails any Sharpe requirement.
- UW has no historical backfill here; only sessions collected live can be replayed.
- The Alpaca loaders are tested against mocked responses only.

## Continuous improvement

```bash
python -m odte_spy_ibkr.improvement run
```

Nightly-style loop: random/grid challengers → short backtest window → compare to `params/active.yaml` → write `params/history/{timestamp}.json`.

Promotion rules (`IMPROVEMENT_AUTO_PROMOTE_PAPER=true` by default; live requires `IMPROVEMENT_AUTO_PROMOTE_LIVE=true`):

- Challenger rolling Sharpe must beat active by `IMPROVEMENT_SHARPE_MARGIN`
- Must meet `BACKTEST_SHARPE_TARGET` (1.5)
- Never enables live orders by itself

Each candidate is scored on the walk-forward holdout of the last 120 calendar days, on sessions that have their own chain only. The history file records `best_sample`: the scored `from`/`to`, chain coverage of those sessions, trade count and traded days, and the number of sessions simulated. A candidate with no Sharpe on that sample cannot be promoted; with the default 60-day rolling window and 20-session holdout that is every candidate (see "Chain coverage and what is scored").

## QuantConnect LEAN

You can run algorithms in LEAN separately; this package does not modify the IB brokerage plugin.