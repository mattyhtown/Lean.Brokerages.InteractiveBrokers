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
| Index spot level | `Alpaca-trading` `get_index_latest_values` (returned 403 "insufficient grants" on 2026-11-15; needs an Alpaca data entitlement) | IB bridge |
| Bond quotes | `Alpaca-trading` `get_fixed_income_latest_quotes` (returned 403 "Subscription does not permit querying fixed income" on 2026-11-15) | IB bridge sec_type BOND |
| Bond market proxy | `Alpaca-trading` `get_stock_bars` on bond ETFs (`SHY,IEF,TLT,LQD,HYG,TIP`) | — |
| Bond / FX / CME futures trade | `ib-lean-bridge` + LEAN live IB | — |
| FX historical bars | `ib-lean-bridge` `get_historical_bars` sec_type CASH | — |
| Crypto spot/perp | `Coinbase` / `Alpaca-trading` crypto tools | — |
| US crypto futures | `Coinbase` `FUTURE`, `GLOBAL_PERP` | — |
| Kalshi events / book | `prediction-markets` `list_kalshi_markets`, `get_kalshi_orderbook` | — |
| Polymarket events / book | `prediction-markets` `list_polymarket_markets`, `get_polymarket_orderbook` | — |

## Research data routing

Status as checked on 2026-11-15. Re-check a namespace before relying on it.

| User need | Route | Status and limits |
|-----------|-------|-------------------|
| LEAPS | `Alpaca-trading` `get_option_contracts` or `get_option_chain` with `expiration_date_gte` set about a year out | Works. `get_option_contracts` defaults `expiration_date_lte` to the next weekend, so a date filter is required to see long-dated contracts. |
| Short availability | `Alpaca-trading` `get_asset` | Works. Returns `shortable`, `easy_to_borrow`, `borrow_status`, `margin_requirement_short`. |
| Short interest, days to cover | HTTP: stockanalysis.com statistics payload | Works without a key. Returns short interest, prior month, short % of float and days to cover. No MCP source. |
| Borrow fee | None | No source found. |
| Fundamentals | HTTP: stockanalysis.com statistics and financials payloads | Works without a key. Alpha Vantage and FMP need an API key and none is configured. `Sp-global` and `Daloopa` still need authentication. |
| Next earnings date | HTTP: Zacks quote feed (`expected_reporting_date`) for one symbol; Alpha Vantage `EARNINGS_CALENDAR` with the `demo` key for the market-wide 3-month calendar | Both work without a private key. The calendar had 846 rows (2026-11-16 to 2027-02-12) with EPS estimate and report time, but AAPL and MSFT were absent, so a missing symbol does not mean no report. |
| Reported earnings (EPS history, surprises) | HTTP: Alpha Vantage `EARNINGS` | Needs an API key and none is configured. `Alpaca-trading` `get_news` gives headlines only; `get_corporate_actions` covers dividends, splits and mergers, not earnings. |
| Analyst ratings and price targets | HTTP: TipRanks stock data; stockanalysis.com forecast payload | Both work without a key. |
| Zacks Rank | HTTP: Zacks quote feed (`zacks_rank`, `zacks_rank_text`) | Works without a key. This is Zacks' own model rank (AAPL returned 3, "Hold"), not an analyst recommendation; report it separately from analyst consensus. |
| Macro series (rates, CPI, GDP, jobs) | HTTP: Alpha Vantage `REAL_GDP`, `CPI`, `FEDERAL_FUNDS_RATE`, `TREASURY_YIELD`, `UNEMPLOYMENT` | The `demo` key returned five series: annual real GDP, monthly CPI, monthly fed funds rate, monthly 10-year Treasury yield and monthly unemployment rate. The other intervals and maturities tried were refused; they need an API key and none is configured. `You-finance` needs authentication. |
| Macro proxies | `Alpaca-trading` `get_stock_bars` on bond ETFs (`SHY,IEF,TLT,LQD,HYG,TIP`); `prediction-markets` Kalshi event markets (rate and inflation series such as `KXFEDCUT`) | Market-implied only, not the underlying economic releases. Alpaca index levels and fixed income quotes are not permitted on the current subscription. |
| Crypto prices | `Coinbase` `coinbase_products_best_bid_ask`; `Alpaca-trading` `get_crypto_snapshot` with `loc=us` | Both work. Prefer Coinbase for pricing: Alpaca quotes come from its own thin venue (BTC spread about $16 against $0.01 on Coinbase when checked). `Kraken` and `1inch-mcp` fail discovery. |
| Crypto market cap, rank, global totals | HTTP: CoinGecko | Works without a key. Prices are aggregated across venues, not a tradable quote. CoinMarketCap needs an API key and none is configured. |
| Second crypto venue (ticker, book, candles) | HTTP: Crypto.com Exchange public API | Works without a key. |
| Sector fund flows (ETF creations and redemptions) | None | No connected source. Unusual Whales is in `.cursor/mcp.json.example` but not connected. |
| Sector rotation proxy | `Alpaca-trading` `get_stock_bars` on the sector SPDRs (`XLK,XLF,XLE,XLV,XLI,XLY,XLP,XLU,XLB,XLRE,XLC`) | Works. Price change and traded dollar volume (`v` times `vw`) only; this is not money entering or leaving the funds. |
| Hedge fund holdings by quarter, insider trades | HTTP: TipRanks stock data (`hedgeFundData`, `insiders`) | Works without a key. Alpha Vantage `INSTITUTIONAL_HOLDINGS` (holder counts, ownership percentage, holdings list) and `INSIDER_TRANSACTIONS` need an API key. `Sp-global` needs authentication; Unusual Whales is not connected. |
| Dark pool totals | None | No source found. |
| Large or off-exchange prints | `Alpaca-trading` `get_stock_trades` | Works, with size `s` and venue `x` per print, but there is no size filter. A sample of AAPL returned 15 prints inside 20 milliseconds, so only narrow time windows are practical. |
| Cited web research | `You-research` | Works. A synthesized answer with sources, not a structured data feed. |
| Market news and social sentiment | `X` `search_news` | Works. Stories are Grok-written summaries of X posts and carry a "Grok can make mistakes" disclaimer. TipRanks `bloggerSentiment` gives a bullish/bearish split. The xAI Grok API needs a key and none is configured. |

## Direct HTTP data sources

Apart from Coinbase, none of these has a connected MCP. Call them over HTTP (shell `curl` or a web fetch). Status as checked on 2026-11-15 from a Cursor cloud agent VM.

Only put a symbol into a URL or command if it matches `^[A-Za-z0-9._-]+$`; never paste user text into a shell command. Keep API keys in environment variables and do not echo a URL that carries one.

The Zacks, TipRanks, stockanalysis.com and Yahoo endpoints are the sites' own undocumented endpoints, not published APIs. They can change or be blocked without notice, and each site's terms of use apply.

| Source | Endpoint | Key | Status and limits |
|--------|----------|-----|-------------------|
| stockanalysis.com | `https://stockanalysis.com/api/quotes/s/{symbol}` (quote); `https://stockanalysis.com/stocks/{symbol}/statistics/__data.json`; same pattern for `financials`, `financials/ratios`, `forecast`; `https://stockanalysis.com/etf/{symbol}/holdings/__data.json` | No | Works. The `__data.json` payloads are SvelteKit data: each node is a flat array in which object values are indexes into that array, so they must be resolved before reading. `ownership` returned 404. |
| Zacks | `https://quote-feed.zacks.com/index?t={symbol}` | No | Works. Use `zacks_rank`, `zacks_rank_text`, `last`, `pe_f1`, `dividend_yield`, `expected_reporting_date` (epoch seconds). The `SUNGARD_*`, `BATS_*` and `FEED_*` fields hold stale, mislabeled values; ignore them. |
| TipRanks | `https://www.tipranks.com/api/stocks/getData/?name={symbol}` | No | Works. Analyst consensus (`portfolioHoldingData.analystConsensus`), price targets (`ptConsensus`), `hedgeFundData`, `insiders`, `bloggerSentiment`, `tipranksStockScore`. The AAPL response was about 800 KB, so extract fields rather than reading it whole. |
| Alpha Vantage | `https://www.alphavantage.co/query?function={FUNCTION}&apikey={key}` | Yes | No key configured. The public `demo` key answered the requests listed here and refused every variation tried; it is limited to 2 requests per second. Answered: `GLOBAL_QUOTE`, `OVERVIEW`, `INCOME_STATEMENT`, `EARNINGS`, `INSTITUTIONAL_HOLDINGS` and `INSIDER_TRANSACTIONS` for IBM, `EARNINGS_CALENDAR` with `horizon=3month`, `REAL_GDP` annual, `CPI` monthly, `FEDERAL_FUNDS_RATE` monthly, `TREASURY_YIELD` monthly 10-year, `UNEMPLOYMENT`, `FX_DAILY` EUR/USD and `CURRENCY_EXCHANGE_RATE` BTC/EUR. Refused: `GLOBAL_QUOTE` for AAPL, `CPI` semiannual, `REAL_GDP` quarterly, `TREASURY_YIELD` daily 2-year, `EARNINGS_CALENDAR` with `horizon=12month`, `FX_DAILY` GBP/USD. Alpha Vantage states a free key allows 25 requests per day. |
| FMP (Financial Modeling Prep) | `https://financialmodelingprep.com/stable/...` | Yes | No key configured. `stable/quote` returned 401 "Invalid API KEY" with no key and with `demo`, so nothing beyond authentication was checked. |
| Yahoo Finance | `https://query1.finance.yahoo.com/v8/finance/chart/{symbol}` | No | Not usable from the cloud agent VM: chart, `quoteSummary`, search and `getcrumb` all returned 429. Not tested from any other network. |
| CoinGecko | `https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd`; `/coins/markets?vs_currency=usd&ids=bitcoin` (market cap, rank, volume); `/global` | No | Works. Coins are addressed by CoinGecko id (`bitcoin`), not ticker. |
| CoinMarketCap | `https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest` | Yes | No key configured. Returned 401 "API key missing". |
| Coinbase Exchange | `https://api.exchange.coinbase.com/products/{product}/ticker`, `/candles` | No | Works. Use only when the `Coinbase` MCP is not connected. |
| Crypto.com Exchange | GET `https://api.crypto.com/exchange/v1/public/get-tickers?instrument_name=BTC_USD`; `get-book?instrument_name=BTC_USD&depth=2`; `get-candlestick?instrument_name=BTC_USD&timeframe=1D&count=2` | No | Works as plain GET requests with query parameters. |
| xAI Grok | `https://api.x.ai/v1/...` | Yes | No key configured. `v1/models` returned 401 "No credentials presented". |

Hosted MCP endpoints exist for some of these; see `docs/MCP_HEALTH_CHECKLIST.md`. None is connected.

## Moomoo (not connected)

Moomoo is a broker, not a keyless data source: every route needs a moomoo login. Status as checked on 2026-11-15; coverage is from moomoo's API documentation (v10.12), not from live calls.

| Route | Status |
|-------|--------|
| Hosted MCP `https://mcp.moomoo.com/mcp` | Not connected. OAuth sign-in; an unauthenticated `initialize` returned 401 "missing Bearer token". Scopes: `quote:read`, `quote:write` (watchlists), `trade:read`, `trade:write`. Tool names cannot be listed until it is authorized. |
| OpenAPI through the OpenD gateway | Not available. OpenD must be running and logged in; nothing listens on `127.0.0.1:11111` on the cloud agent VM. Python SDK is `moomoo-api` on PyPI (10.12.7208). |
| Web endpoints without a login | Not found. The one moomoo.com quote endpoint tried returned "Params Error". |

Documented coverage once connected:

- **Quotes:** US stocks and ETFs, US stock and index options, US futures, Hong Kong and A-share markets, crypto, event contracts. No US index levels, OTC securities or forex, so it does not fill the index spot or FX gaps above.
- **Live trading on a Moomoo US account:** US stocks, ETFs and options, Hong Kong stocks, China Connect A-shares, crypto, event contracts. US futures are not tradable on Moomoo US.
- **Paper trading:** US stocks, options and futures.

If it is connected, treat it as a fallback to `Alpaca-trading` for US equity and option quotes, and do not place a moomoo order without explicit user approval.

## Tradier (not connected)

Tradier is a broker; quotes, option chains and trading all need a Tradier account. Status as checked on 2026-11-15.

| Route | Status |
|-------|--------|
| Hosted MCP `https://mcp.tradier.com/mcp` | Not connected. `initialize` and `tools/list` answer without credentials and list 21 tools, but `get_market_quotes` returned "API key is required but not provided (via JWT or API_KEY header)". |
| REST `https://api.tradier.com/v1` (sandbox: `https://sandbox.tradier.com/v1`) | No token configured. Quotes and option chains returned 401 "Invalid access token" on both hosts. `markets/clock` and `markets/calendar` answer without a token. |

Tools the hosted MCP lists:

- **Market data:** `get_market_quotes`, `get_historical_data` (daily, weekly or monthly bars), `get_options_chain` (one expiration, optional greeks), `get_company_profile`, `get_market_calendar`.
- **Account:** `get_user_profile`, `get_account_balances`, `get_account_historical_balances`, `get_positions`, `get_orders`, `get_account_history`, `get_gainloss`, `get_watchlists`, `add_to_watchlist`.
- **Orders:** `place_equity_order`, `place_option_order`, `place_multileg_option_order`, `place_oco_order`, `place_oto_order`, `place_otoco_order`, `cancel_order`.

The list has no intraday bars and nothing for futures, forex or crypto. If it is connected, treat it as a fallback to `Alpaca-trading` for US equity quotes and option chains with greeks, and do not place a Tradier order without explicit user approval.

## MCP health

If a namespace fails discovery, see `docs/MCP_HEALTH_CHECKLIST.md` in this plugin. Reconnect ORATS/Kraken/Alpaca-paper in Cursor settings.

## Examples

- "SPX 0DTE puts" → Alpaca `get_option_chain` with `underlying_symbol=SPX`, `type=put`, tight expiration filter.
- "EURUSD last day" → `ib-lean-bridge` `get_historical_bars` with `symbol=EURUSD`, `sec_type=CASH`, `exchange=IDEALPRO`.
- "Kalshi Fed rate market" → `list_kalshi_markets` with `status=open` and search results client-side.
- "Polymarket book" → `get_polymarket_market` then `get_polymarket_orderbook` with token id from market.
- "AAPL short interest" → GET `https://stockanalysis.com/stocks/aapl/statistics/__data.json`, resolve the index references, read `shortSelling`.
- "Analyst view on AAPL" → GET `https://www.tipranks.com/api/stocks/getData/?name=AAPL` for consensus and price targets. Report `zacks_rank` from `https://quote-feed.zacks.com/index?t=AAPL` separately as Zacks' model rank.

## Performance notes

- Option chains: always filter expiration, type, and strike; paginate with `page_token` / `limit`.
- Polymarket Gamma list: use `limit` and `offset`.
- IB history: keep `duration` small (e.g. `1 D`) to avoid timeouts.

## Troubleshooting

- **Alpaca indicative feed:** set `feed=indicative` if OPRA is not subscribed.
- **IB connection refused:** run `scripts/check-ib-env.sh`; start Gateway on `IB_PORT`.
- **Prediction markets HTTP errors:** check public API status; retry with smaller `limit`.
- **Alpha Vantage "demo purposes only":** the `demo` key only answers a fixed set of requests (see the direct HTTP table); anything else needs a real key.
- **Yahoo Finance 429:** the cloud agent VM is refused on every endpoint; use another source from the direct HTTP table.