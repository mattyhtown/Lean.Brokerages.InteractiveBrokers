---
name: instrument-router
description: Route research and trades to the right MCP by asset class (equities, bonds, forex, futures, options, crypto, prediction markets). Use for multi-asset questions or before placing orders.
---

# Instrument router

## Instructions

1. Classify the user request into one primary asset class (and venue if prediction market).
2. Call tools on the **first** route below; use fallback only when the primary returns unsupported or missing data.
3. Use **preview** tools before any live order (Coinbase `coinbase_orders_preview`, Alpaca order tools with user confirmation).
4. Never place prediction-market orders without explicit user approval (read-only tools first).

## Symbol convention

| Prefix | Meaning | Example |
|--------|---------|---------|
| `EQUITY:` | Stock/ETF | `EQUITY:AAPL` |
| `OPT:` | Option | `OPT:SPY:2025-06-20:C:450` |
| `IDXOPT:` | Index option underlier | `IDXOPT:SPX` |
| `FX:` | Forex | `FX:EURUSD` |
| `FUT:` | Future | `FUT:ES` |
| `BOND:` | Fixed income | `BOND:US912797SX61` (ISIN) |
| `CRYPTO:` | Crypto spot | `CRYPTO:BTC-USD` |
| `PM:` | Prediction market | `PM:kalshi:TICKER` or `PM:polymarket:slug` |

## Routing table

| User need | Primary MCP / tool | Fallback |
|-----------|-------------------|----------|
| US stock/ETF bars, trade | `Alpaca-trading` `get_stock_bars`, `place_stock_order` | `Coinbase` `product_type=EQUITY`; IB `ib-lean-bridge` `get_historical_bars` sec_type STK |
| US equity option chain | `Alpaca-trading` `get_option_chain`, `get_option_contracts` | IB bridge + LEAN live |
| SPX/VIX/DJX/XSP index options | `Alpaca-trading` `get_option_chain` (underlier SPX, VIX, …) | IB only for NDX/RUT |
| Index spot level | `Alpaca-trading` `get_index_latest_values` | IB bridge |
| Bond quotes | `Alpaca-trading` `get_fixed_income_latest_quotes` (ISINs) | IB bridge sec_type BOND |
| Bond / FX / CME futures trade | `ib-lean-bridge` + LEAN live IB | — |
| FX historical bars | `ib-lean-bridge` `get_historical_bars` sec_type CASH | — |
| Crypto spot/perp | `Coinbase` / `Alpaca-trading` crypto tools | — |
| US crypto futures | `Coinbase` `FUTURE`, `GLOBAL_PERP` | — |
| Kalshi events / book | `prediction-markets` `list_kalshi_markets`, `get_kalshi_orderbook` | — |
| Polymarket events / book | `prediction-markets` `list_polymarket_markets`, `get_polymarket_orderbook` | — |
| Fundamentals | `Sp-global`, `Daloopa` | — |

## MCP health

If a namespace fails discovery, see `docs/MCP_HEALTH_CHECKLIST.md` in this plugin. Reconnect ORATS/Kraken/Alpaca-paper in Cursor settings.

## Examples

- "SPX 0DTE puts" → Alpaca `get_option_chain` with `underlying_symbol=SPX`, `type=put`, tight expiration filter.
- "EURUSD last day" → `ib-lean-bridge` `get_historical_bars` with `symbol=EURUSD`, `sec_type=CASH`, `exchange=IDEALPRO`.
- "Kalshi Fed rate market" → `list_kalshi_markets` with `status=open` and search results client-side.
- "Polymarket book" → `get_polymarket_market` then `get_polymarket_orderbook` with token id from market.

## Performance notes

- Option chains: always filter expiration, type, and strike; paginate with `page_token` / `limit`.
- Polymarket Gamma list: use `limit` and `offset`.
- IB history: keep `duration` small (e.g. `1 D`) to avoid timeouts.

## Troubleshooting

- **Alpaca indicative feed:** set `feed=indicative` if OPRA is not subscribed.
- **IB connection refused:** run `scripts/check-ib-env.sh`; start Gateway on `IB_PORT`.
- **Prediction markets HTTP errors:** check public API status; retry with smaller `limit`.