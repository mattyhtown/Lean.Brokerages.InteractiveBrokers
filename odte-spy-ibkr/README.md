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
| `orats` | **Research ingest** | `ORATS_API_TOKEN` — historical 1-min strikes chain via `python -m odte_spy_ibkr.research ingest --source orats` (no live MCP) |
| `openbb` | **Live + research** | `pip install 'odte-spy-ibkr[openbb]'`; optional `OPENBB_PAT` for Hub/historical; live chains default `OPENBB_LIVE_PROVIDER=cboe`; historical ingest `OPENBB_HISTORICAL_PROVIDER=intrinio` — see `data/research/openbb/README.md` |
| `alphavantage` | **Live + research** | `ALPHAVANTAGE_API_KEY` — `GLOBAL_QUOTE` for spot; ingest `TIME_SERIES_INTRADAY` / daily; options endpoints optional/premium — see `data/research/alphavantage/README.md` |
| `you_finance` | **Stub** | `YOU_FINANCE_ENABLED` + `YOU_API_KEY` |
| `context` | **Stub** | `CONTEXT_ENABLED` + `CONTEXT_API_KEY` |
| `sp_global` | **Stub** | `SP_GLOBAL_ENABLED` + `SP_GLOBAL_API_KEY` |
| `daloopa` | **Stub** | `DALOOPA_ENABLED` + `DALOOPA_API_KEY` |
| `perspective` | **Stub** | `PERSPECTIVE_ENABLED` + `PERSPECTIVE_API_KEY` |

Enable providers: `PROVIDERS_ENABLED=ibkr,unusual_whales,alpaca,openbb,alphavantage,coinbase`

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

Live: IB chain → JSON cache under `data/cache/SPY/{date}.json`. Backtest reads the same JSON/parquet layout.

## Historical backtest

```bash
pip install -e ".[dev,backtest]"
python -m odte_spy_ibkr.backtest --from 2024-01-01 --to 2025-11-14 --report-json reports/summary.json
python -m odte_spy_ibkr.backtest --from 2024-01-01 --to 2025-11-14 --walk-forward --require-rolling-sharpe 1.5
python -m odte_spy_ibkr.backtest --from 2024-01-01 --to 2025-11-14 --verbose  # no-entry reason histogram
```

- Rolling Sharpe: 60d window, annualized `sqrt(252)*mean/std` on daily P&amp;L returns
- Optional live `SHARPE_GATE_ENABLED` pauses new entries when rolling Sharpe &lt; `SHARPE_MIN` (default 1.5)
- Macro news provider: Alpaca news cache + `config/macro_events.yaml` tier-1 blackout

- Walk-forward: `--walk-forward` scores only the holdout test window (`BACKTEST_WALK_FORWARD_TRAIN_DAYS` / `TEST_DAYS`); improvement evaluator uses the same holdout.
- Cache: chain bars prefer **parquet** when pandas/pyarrow installed (`data/cache/SPY/{date}.parquet`).
- Live journal: `data/journal/daily_pnl.jsonl` appended at session roll / shutdown for `SHARPE_GATE_ENABLED`.

**UW session collector** (live, requires API key):

```bash
python -m odte_spy_ibkr.unusual_whales --once
```

Appends `data/cache/uw/sessions/{date}.jsonl` and refreshes `data/cache/uw/{date}.json` for backtest replay.

**Gaps:** Without Alpaca/UW keys, backtest uses **synthetic** chains (not historical marks); UW history is cache-first.

## Alpha research ingest (real data)

See `data/research/README.md` for cache layout.

```bash
# Credential scan runs at the start of every research CLI invocation.
python -m odte_spy_ibkr.research ingest --source alpaca --start 2026-07-01 --end 2026-11-05
python -m odte_spy_ibkr.research ingest --source uw     # needs UNUSUAL_WHALES_API_KEY
python -m odte_spy_ibkr.research ingest --source orats  # needs ORATS_API_TOKEN
python -m odte_spy_ibkr.research ingest --source openbb  # pip install 'odte-spy-ibkr[openbb]'; OPENBB_PAT for dated history
python -m odte_spy_ibkr.research ingest --source alphavantage  # needs ALPHAVANTAGE_API_KEY
python -m odte_spy_ibkr.research ingest --source ibkr   # needs TWS/Gateway on IB_HOST:IB_PORT, READ_ONLY=true
python -m odte_spy_ibkr.research ingest --source thetadata        # needs THETADATA_* + local Theta Terminal v3
python -m odte_spy_ibkr.research ingest --source tradingeconomics # needs TRADING_ECONOMICS_API_KEY
python -m odte_spy_ibkr.research ingest --source quantconnect      # needs QC creds + local LEAN data or CLI
python -m odte_spy_ibkr.research evaluate --run 2
```

- **Alpaca:** SPY minutes + daily summary.
- **Unusual Whales:** `flow-per-strike?date=` + `option-chains?date=&greeks=true` (historical); `flow-recent` is live-only per UW docs.
- **ORATS:** `GET /datav2/historical/one-minute/strikes/chain` (CSV, EST `tradeDate`); does not provide equity OHLC — pair with Alpaca/IBKR for SPY bars.
- **IBKR:** `reqHistoricalData` read-only; ~11s pacing between option requests.
- **Theta Data:** `GET http://127.0.0.1:25503/v3/option/history/quote` for 0DTE NBBO (requires **Theta Terminal** running locally with `THETADATA_USERNAME` / `THETADATA_PASSWORD` at launch — loopback HTTP has no per-request auth). Writes unified `chains/SPY/{date}.json`; backtest loader reads that cache first.
- **Trading Economics:** `GET /calendar/country/united%20states/{start}/{end}?c=KEY&values=true` — actual/forecast/previous + numeric surprise; feeds `data/research/tradingeconomics/` and mirrors tier-1/2 rows into `data/cache/macro_calendar/`. New research feature: `macro_tier1_surprise` (actual − forecast for tier-1 releases before 11:00 ET, no lookahead).

- **QuantConnect:** `POST /api/v2/authenticate` with `QUANTCONNECT_USER_ID` + `QUANTCONNECT_API_TOKEN`; historical files come from the **LEAN CLI** `lean data download` (Dataset Market, **QCC per file**, paid organization tier — not a REST bulk history API). Imports on-disk LEAN zips into `data/research/quantconnect/minute/` and unified `chains/SPY/`. Set `QUANTCONNECT_ALLOW_PAID_DOWNLOAD=true` only when you accept QCC charges.

Neither UW, IBKR, ORATS, Theta Data, Trading Economics, nor QuantConnect ingest was executed against live services in the cloud agent environment (mocked unit tests only).

## Continuous improvement

```bash
python -m odte_spy_ibkr.improvement run
```

Nightly-style loop: random/grid challengers → short backtest window → compare to `params/active.yaml` → write `params/history/{timestamp}.json`.

Promotion rules (`IMPROVEMENT_AUTO_PROMOTE_PAPER=true` by default; live requires `IMPROVEMENT_AUTO_PROMOTE_LIVE=true`):

- Challenger rolling Sharpe must beat active by `IMPROVEMENT_SHARPE_MARGIN`
- Must meet `BACKTEST_SHARPE_TARGET` (1.5)
- Never enables live orders by itself

## QuantConnect LEAN

You can run algorithms in LEAN separately; this package does not modify the IB brokerage plugin.