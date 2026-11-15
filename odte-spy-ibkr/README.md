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
| `ENABLE_ORDERS` | `false` | Must be `true` to submit orders (combo placement is still logged/dry-run in v1) |

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

Live: IB chain → JSON cache under `data/cache/SPY/{date}.json`. Backtest reads the same JSON/parquet layout.

## Historical backtest

```bash
pip install -e ".[dev,backtest]"
python -m odte_spy_ibkr.backtest --from 2024-01-01 --to 2025-11-14 --report-json reports/summary.json
python -m odte_spy_ibkr.backtest --from 2024-01-01 --to 2025-11-14 --require-rolling-sharpe 1.5
```

- Rolling Sharpe: 60d window, annualized `sqrt(252)*mean/std` on daily P&amp;L returns
- Optional live `SHARPE_GATE_ENABLED` pauses new entries when rolling Sharpe &lt; `SHARPE_MIN` (default 1.5)
- Macro news provider: Alpaca news cache + `config/macro_events.yaml` tier-1 blackout

**Gaps:** UW history is cache-first (`UW_BACKTEST_CACHE_DIR`); synthetic chain used when Alpaca keys/history missing.

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