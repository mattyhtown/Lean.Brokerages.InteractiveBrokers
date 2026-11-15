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
| Short interest, borrow fee, days to cover | None | No connected source. |
| Fundamentals | `Sp-global`, `Daloopa` | Both need authentication; no tools until connected. |
| Earnings dates and results | None | No connected source. `Alpaca-trading` `get_news` gives headlines only; `get_corporate_actions` covers dividends, splits and mergers, not earnings. |
| Macro series (rates, CPI, GDP, jobs) | FRED public CSV over HTTP: `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>&cosd=<YYYY-MM-DD>` (for example `DGS10`, `DGS2`, `DFF`, `CPIAUCSL`, `UNRATE`, `PAYEMS`, `GDPC1`, `T10Y2Y`, `BAMLH0A0HYM2`) | Works without a key (checked 2026-11-15). One series per request, values only; missing observations are blank or `.`. There is no FRED MCP, and the JSON API at `api.stlouisfed.org` (search, release dates, revisions) needs a free `FRED_API_KEY`, which is not set. `You-finance` needs authentication. |
| Macro proxies | `Alpaca-trading` `get_stock_bars` on bond ETFs (`SHY,IEF,TLT,LQD,HYG,TIP`); `prediction-markets` Kalshi event markets (rate and inflation series such as `KXFEDCUT`) | Market-implied only, not the underlying economic releases. Alpaca index levels and fixed income quotes are not permitted on the current subscription. |
| Crypto prices | `Coinbase` `coinbase_products_best_bid_ask`; `Alpaca-trading` `get_crypto_snapshot` with `loc=us` | Both work. Prefer Coinbase for pricing: Alpaca quotes come from its own thin venue (BTC spread about $16 against $0.01 on Coinbase when checked). `Kraken` and `1inch-mcp` fail discovery. |
| Sector fund flows (ETF creations and redemptions) | None | No connected source. Unusual Whales is in `.cursor/mcp.json.example` but not connected. |
| Sector rotation proxy | `Alpaca-trading` `get_stock_bars` on the sector SPDRs (`XLK,XLF,XLE,XLV,XLI,XLY,XLP,XLU,XLB,XLRE,XLC`) | Works. Price change and traded dollar volume (`v` times `vw`) only; this is not money entering or leaving the funds. |
| Institutional ownership (13F holdings), insider trades, dark pool totals | None | No connected source. `Sp-global` needs authentication; Unusual Whales is not connected. |
| Large or off-exchange prints | `Alpaca-trading` `get_stock_trades` | Works, with size `s` and venue `x` per print, but there is no size filter. A sample of AAPL returned 15 prints inside 20 milliseconds, so only narrow time windows are practical. |
| Market news headlines | `Alpaca-trading` `get_news` (by symbol and date range) | Works. Articles come from Benzinga and fiscal.ai only; body text was empty in every article sampled. |
| MT Newswires | None | No connected source. IB sells it as a TWS research subscription, but the TWS API only serves Briefing.com (`BRFG`, `BRFUPDN`), Dow Jones Newsletters (`DJNL`) and Benzinga (`BZ`, paid). The LEAN IB plugin and `ib-lean-bridge` have no news calls. |
| Trending market stories | `X` `search_news` | Works. Machine-written summaries of posts on X; treat as unverified. |
| Cited web research | `You-research` | Works. A synthesized answer with sources, not a structured data feed. |

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