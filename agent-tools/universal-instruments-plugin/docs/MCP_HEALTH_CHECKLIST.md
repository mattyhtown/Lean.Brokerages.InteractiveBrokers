# MCP health checklist — universal instruments

Use this after connecting or reconnecting brokers in **Cursor → Settings → Tools & MCP**.

## Quick verification

| Server | Expected state | How to verify |
|--------|----------------|---------------|
| **Alpaca-trading** | Tools visible, no `needsAuth` | Call `get_account_info` or `get_clock` |
| **Alpaca-trading-paper** | Authenticated | Same tools against paper account |
| **Alpaca-broker** | Authenticated | Required for broker/partner API features |
| **Coinbase** | Trading tools listed | `coinbase_balance` or `coinbase_products_list` |
| **ORATS** | Tools discovered (not empty namespace) | Reconnect if discovery failed |
| **Kraken** | Tools discovered | Reconnect if discovery failed |
| **Sp-global** | Authenticated | Needed for fundamentals; shows `needsAuth` until connected |
| **Daloopa** | Authenticated | Needed for fundamentals and earnings detail; shows `needsAuth` until connected |
| **You-finance** | Authenticated | Shows `needsAuth` until connected |
| **prediction-markets** (this plugin) | `list_kalshi_markets`, `list_polymarket_markets` | No secrets for read-only Gamma/Kalshi public APIs |
| **ib-lean-bridge** (this plugin) | `ping`, `get_historical_bars` | Requires TWS/IB Gateway + env vars |

## Alpaca — options and index options

- **Equity options chain:** `get_option_chain` with `underlying_symbol` (e.g. `SPY`).
- **Index options:** `underlying_symbol` = `SPX`, `XSP`, `VIX`, or `DJX` (account must have index options enabled — contact Alpaca).
- **Feed:** `feed: "opra"` needs OPRA subscription; use `feed: "indicative"` for delayed/modified quotes.
- **Index spot levels:** `get_index_latest_values` with `SPX`, `VIX`, etc. (separate from option chain).

## Coinbase — product types

| `product_type` | Instruments |
|----------------|-------------|
| `EQUITY` | US stocks and ETFs (e.g. `AAPL-USD`) |
| `SPOT` | Crypto spot |
| `FUTURE` | US domestic (CFM) futures — separate account approval |
| `GLOBAL_PERP` | International perpetuals |
| `GLOBAL_FUTURE` | International dated futures |

Use `coinbase_products_list` with filters before trading. Preview equities/futures with `coinbase_orders_preview` where supported.

## Fixing broken MCP connections

1. Open **Cursor → Settings → Tools & MCP**.
2. For each server showing **error** or **needsAuth**, use **Connect** / **Reconnect**.
3. ORATS / Kraken: if discovery still fails, disable and re-enable the server or update the plugin package.
4. Restart Cursor if tools remain stale after auth.

## Data sources without a connected MCP

These providers publish hosted MCP endpoints. None is connected or listed in this plugin's `mcp.json`; adding one is a change to your Cursor MCP settings. The handshake column is the reply to an MCP `initialize` request sent without credentials on 2026-11-15.

| Provider | Hosted MCP endpoint | Handshake without credentials |
|----------|---------------------|-------------------------------|
| CoinGecko | `https://mcp.api.coingecko.com/mcp` | Succeeds. Advertises `search_docs` and `execute`. |
| Crypto.com Exchange | `https://mcp.crypto.com/market-data/mcp` | Succeeds. Advertises `get_market_orderbook`, `get_market_trades`, `get_market_candles`, `get_widget`. |
| CoinMarketCap | `https://mcp.coinmarketcap.com/mcp` | 401 "Token not found". Needs a CoinMarketCap API key. |
| Alpha Vantage | `https://mcp.alphavantage.co/mcp` | 401 "Missing access token". Needs an Alpha Vantage API key. |
| FMP | `https://financialmodelingprep.com/mcp` | 401 "Authentication required". Needs an FMP API key. |

No hosted MCP endpoint was checked for Zacks, TipRanks, stockanalysis.com or Yahoo Finance. Until a server is connected, use the direct HTTP table in the instrument-router skill.

## Secrets (never commit)

| Variable | Used by |
|----------|---------|
| `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PEM` | Kalshi authenticated endpoints (optional; public market list works without) |
| `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID` | `ib-lean-bridge` MCP |
| Alpaca / Coinbase | Managed via Cursor MCP OAuth or env in server config |

## Coverage routing

For asset-class → tool mapping, load the **instrument-router** skill in this plugin (`skills/instrument-router/SKILL.md`).