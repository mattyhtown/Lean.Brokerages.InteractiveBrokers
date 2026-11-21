# MCP health checklist — universal instruments

Use this after connecting or reconnecting brokers in **Cursor → Settings → Tools & MCP**.

## Quick verification

| Server | Expected state | How to verify |
|--------|----------------|---------------|
| **Alpaca-trading** | Tools visible, no `needsAuth` | Call `get_account_info` or `get_clock` |
| **Alpaca-trading-paper** | Authenticated | Same tools against paper account |
| **Alpaca-broker**, **Alpaca-broker-sandbox** | Not connected | Not needed. This is Alpaca's Broker API for firms that open accounts for their own customers; trading your own account uses `Alpaca-trading` |
| **Coinbase** | Trading tools listed | `coinbase_balance` or `coinbase_products_list` |
| **ORATS** | Tools discovered (not empty namespace) | Reconnect if discovery failed |
| **Kraken** | Tools discovered | Reconnect if discovery failed |
| **Sp-global** | Authenticated | Needed for fundamentals; shows `needsAuth` until connected |
| **Daloopa** | Authenticated | Needed for fundamentals and earnings detail; shows `needsAuth` until connected |
| **You-finance** | Authenticated | Shows `needsAuth` until connected |
| **market-data** (this plugin) | 16 live tools (`get_kraken_*`, `get_cboe_*`, `get_fred_series`, `get_treasury_auctions`, `get_finra_*`, `get_cftc_cot`, `get_crypto_fear_greed`, `get_sec_filings`) | Zero keys for 15 tools; optional `SEC_USER_AGENT` for SEC EDGAR. Supports stdio and `--http --port 3100` |
| **prediction-markets** (this plugin) | `list_kalshi_markets`, `get_kalshi_market`, `get_kalshi_orderbook`, `get_kalshi_trades`, `get_kalshi_candlesticks`, `list_polymarket_markets`, `get_polymarket_market`, `get_polymarket_orderbook`, `get_polymarket_price_history` | No secrets for read-only Gamma/Kalshi public APIs |
| **ib-lean-bridge** (this plugin) | `ping`, `get_account_summary`, `get_positions`, `get_quote`, `get_historical_bars`, `preview_order` | Requires local TWS/IB Gateway + env vars; run `scripts/smoke-test-ib.py` |

## Coverage checklist

Asset class, the tool that serves it, what the account needs, and what a live call returned on 2026-11-15 from a Cursor cloud agent. "Not checked" means no call was made.

| Asset class | Tool | Account needs | Status on 2026-11-15 |
|-------------|------|---------------|----------------------|
| US stocks and ETFs | `Alpaca-trading` `get_stock_bars`, `place_stock_order`; `Coinbase` with `product_type=EQUITY` | A funded account at the broker used | `Alpaca-trading` connected (`get_clock` answered). `Coinbase` listed `NVDA-USD`. No order was placed. |
| Equity option chains | `Alpaca-trading` `get_option_chain` | Signed OPRA agreement for `feed=opra`; nothing extra for `feed=indicative` | `feed=opra` returned 403 "OPRA agreement is not signed". `feed=indicative` returned SPY quotes. |
| Index option chains (SPX, XSP, VIX, DJX) | `Alpaca-trading` `get_option_chain` | Same feed rule; trading them needs Alpaca to enable index options on the account | SPX chain returned on `feed=indicative`. Trading enablement not checked. |
| Index levels | `Alpaca-trading` `get_index_latest_values` | Alpaca index data entitlement | 403 "insufficient grants". |
| Bond quotes | `Alpaca-trading` `get_fixed_income_latest_quotes` | Alpaca fixed income data subscription | 403 "Subscription does not permit querying fixed income". |
| Crypto spot | `Coinbase` with `product_type=SPOT`; `Alpaca-trading` crypto tools with `loc=us` | A funded account | Both answered; see the instrument-router skill. |
| US crypto futures | `Coinbase` with `product_type=FUTURE` | Separate Coinbase futures approval to trade | Listing visible (`BIP-20DEC30-CDE`, `BIT-27NOV26-CDE`). Trading approval not checked. |
| International perpetuals | `Coinbase` with `product_type=GLOBAL_PERP` | Separate eligibility and approval to trade | Listing visible. Trading eligibility not checked. |
| Forex, CME futures, bonds, NDX and RUT options | `ib-lean-bridge` (this plugin); LEAN live for everything except bonds | IB account with trading permission and a market data subscription for each asset class; Gateway or TWS running with the API enabled | Not connected: no IB login on the cloud agent, nothing listening on `127.0.0.1:7497`. |
| Prediction markets | `prediction-markets` (this plugin) | Nothing for reading | All nine tools answered against Kalshi and Polymarket. |
| Options analytics | `orats` | ORATS subscription | Tool discovery failed (still loading on 2026-11-21); reconnect in Cursor settings. Keyless fallback for IV30, greeks, put/call ratios and straddle implied move: CBOE delayed chains (`https://cdn.cboe.com/api/global/delayed_quotes/options/<SYMBOL>.json` + quote `iv30`). |
| Kraken | `Kraken` | Kraken API connection (optional for reads) | Tool discovery failed (still loading on 2026-11-21); reconnect in Cursor settings. Keyless read-only fallback verified 2026-11-21: Kraken public REST (`https://api.kraken.com/0/public/Ticker`, `/Depth`, `/OHLC`, `/Trades`, `/AssetPairs`). |
| Paper trading | `Alpaca-trading-paper` (same tools as `Alpaca-trading`, against the paper account) | Sign-in | Connected. `get_account_info` returned status `ACTIVE`, options level 3, shorting enabled and crypto active. Data entitlements match the live connection: `feed=opra` returned 403 "OPRA agreement is not signed" and the SPX chain returned on `feed=indicative`. No order was placed. |
| Broker API | `Alpaca-broker`, `Alpaca-broker-sandbox` | Sign-in for each; these are Alpaca's Broker API for firms that open accounts for their own customers, not needed to trade your own account | Not used. The account owner trades through the Trading API and declined the Broker sign-in on 2026-11-21; leave both disconnected. |

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
2. For each server showing **error**, **loading**, or **needsAuth**, click **Connect** / **Reconnect**.
3. **ORATS / Kraken:** if discovery still fails or stays stuck in `loading`, toggle the server off and back on, confirm its API token / URL in your user MCP config (`~/.cursor/mcp.json`), or update the plugin package. Until reconnected, use CBOE delayed option chains in place of `orats` and Kraken public REST (`/0/public/Ticker`, `/Depth`, `/OHLC`, `/Trades`) in place of `Kraken` for read-only data.
4. Fully quit and reopen Cursor if tools remain stale after auth.

## User-managed keys, entitlements, and auth runbook

These items are account- or environment-level and cannot be enabled automatically by the repo. Check off only the ones you want to unlock:

| Item | What it unlocks | How to enable |
|------|-----------------|---------------|
| `SEC_USER_AGENT` (`Name email@domain`) | SEC EDGAR filing indexes, `companyfacts`, full-text search, and `scripts/edgar_parse.py` (Form 4 + 13F XML) | Export in your shell profile and Cursor Cloud Agents Secrets: `export SEC_USER_AGENT="Your Name you@domain.com"` |
| `FRED_API_KEY` (free) | FRED JSON API (`api.stlouisfed.org`: series search, release dates, revisions); public CSV already works without a key | Request a free key at [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) and store in env / Secrets |
| `ALPHAVANTAGE_API_KEY` (free tier: 25 req/day) | Per-symbol Alpha Vantage earnings history, estimate revisions, statements, transcripts, and non-demo macro/FX calls | Request a free key at [alphavantage.co](https://www.alphavantage.co/support/#api-key) and store in env / Secrets |
| `EIA_API_KEY` (free) | Structured JSON oil/gas inventory and production series from `api.eia.gov` (avoids `.xls` parsing) | Register at [eia.gov/opendata](https://www.eia.gov/opendata/register.php) and store in env / Secrets |
| Alpaca OPRA agreement | Live `feed=opra` option quotes on `Alpaca-trading` and `Alpaca-trading-paper` (currently 403; `feed=indicative` works now) | Sign the OPRA market data agreement in the Alpaca dashboard |
| Alpaca index & fixed-income entitlements | `get_index_latest_values` (SPX/VIX spot), `get_fixed_income_latest_quotes`, and index options trading | Enable index options trading and market data subscriptions in the Alpaca dashboard (or contact Alpaca support) |
| Coinbase `FUTURE` / `GLOBAL_PERP` approvals | Trading US CFM crypto futures and international perpetuals (product listings are already readable) | Complete Coinbase Financial Markets futures / international eligibility approval in Coinbase |
| `Sp-global`, `Daloopa`, `You-finance` MCPs | Authenticated fundamentals, financial models, and institutional earnings detail | Click **Connect** for each in **Cursor → Settings → Tools & MCP** (optional; stockanalysis.com / TipRanks / Zacks HTTP routes work without them) |
| Aikido security scan (`aikido_login`) | Cloud SAST + secrets scans through the Aikido MCP | Run the Aikido sign-in flow in Cursor if you want Aikido cloud scans; otherwise run local static/secret checks before committing |

## Data sources without a connected MCP

These providers publish hosted MCP endpoints. None is connected or listed in this plugin's `mcp.json`; adding one is a change to your Cursor MCP settings. The handshake column is the reply to an MCP `initialize` request sent without credentials on 2026-11-15.

| Provider | Hosted MCP endpoint | Handshake without credentials |
|----------|---------------------|-------------------------------|
| CoinGecko | `https://mcp.api.coingecko.com/mcp` | Succeeds. Advertises `search_docs` and `execute`. |
| Crypto.com Exchange | `https://mcp.crypto.com/market-data/mcp` | Succeeds. Advertises `get_market_orderbook`, `get_market_trades`, `get_market_candles`, `get_widget`. |
| CoinMarketCap | `https://mcp.coinmarketcap.com/mcp` | 401 "Token not found". Needs a CoinMarketCap API key. |
| Alpha Vantage | `https://mcp.alphavantage.co/mcp` | 401 "Missing access token". Needs an Alpha Vantage API key. |
| FMP | `https://financialmodelingprep.com/mcp` | 401 "Authentication required". Needs an FMP API key. |
| Moomoo | `https://mcp.moomoo.com/mcp` | 401 "missing Bearer token". OAuth sign-in with a moomoo account; scopes `quote:read`, `quote:write`, `trade:read`, `trade:write`. Granting `trade:write` lets the agent place and cancel orders. |
| Tradier | `https://mcp.tradier.com/mcp` | Succeeds and lists 21 tools, including six order-placement tools. A data call returned "API key is required but not provided (via JWT or API_KEY header)", so it needs a Tradier sign-in or API token. |
| Seeking Alpha | `https://seekingalpha.com/mcp` | 401 "Authentication required". OAuth sign-in with scope `mcp:pro_mcp`. Seeking Alpha's help center says it is for PRO subscribers only and provides Quant, Analyst and Factor grades. |
| Bloomberg | `https://mcp.bloomberg.com/mcp` | 401 "No definition of jwt found in header or query string". Its resource metadata names `https://bsso.blpprofessional.com` (Bloomberg sign-in) as the authorization server. Bloomberg describes Enterprise MCP as part of Data License Plus, a licensed enterprise data product; whether this host is that service was not confirmed. No tool list was returned, so its news coverage is unknown. |

No hosted MCP endpoint was checked for Zacks, TipRanks, stockanalysis.com or Yahoo Finance. Until a server is connected, use the direct HTTP table in the instrument-router skill.

## Secrets (never commit)

| Variable | Used by |
|----------|---------|
| `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PEM` | Kalshi authenticated endpoints (optional; public market list works without) |
| `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID` | `ib-lean-bridge` MCP |
| `IB_ACCOUNT`, `IB_ALLOW_ORDERS` | `ib-lean-bridge` MCP. Not secrets: the account to trade, and `1` to allow `place_order` and `cancel_order` (off by default) |
| Alpaca / Coinbase | Managed via Cursor MCP OAuth or env in server config |

## Coverage routing

For asset-class → tool mapping, load the **instrument-router** skill in this plugin (`skills/instrument-router/SKILL.md`).