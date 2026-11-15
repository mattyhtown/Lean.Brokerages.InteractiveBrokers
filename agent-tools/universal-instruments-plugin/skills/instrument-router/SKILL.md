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
| Moving averages for a few symbols (20, 50, 200 day) | `Alpaca-trading` `get_stock_bars` with `timeframe=1Day`, `adjustment=all` and about 300 calendar days of lookback; average the last N closes | Works. There is no moving-average or screener tool, so the averages are computed from the bars. A call returns at most 10,000 bars across all symbols, about 45 symbols at 200 bars each. |
| Stocks below their moving averages across an index | CBOE delayed data, no key: `https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/<SYMBOL>.json` for daily bars and `https://cdn.cboe.com/api/global/delayed_quotes/quotes/<SYMBOL>.json` for the latest close; both redirect to `cdn-api.cboe.com`. S&P 500 members: `https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv` | Works (checked 2026-11-15, 498 names screened). The history file stops one session short, so append `close` from the quote file. History is split-adjusted but not spin-off adjusted: Honeywell showed a false 51% drop on 2026-06-29, so check for one-day gaps before trusting a 200-day average. Rate limited: about 480 requests in 8 seconds returned 429 with `Retry-After: 50`; two requests a second ran clean. Dotted tickers keep the dot (`BRK.B`). The member list is a third-party file, and names listed under 200 sessions have no 200-day average. Stooq requires a browser check and is not routed to. |
| Put/call ratio, whole market | CBOE daily statistics, no key: `https://cdn.cboe.com/data/us/options/market_statistics/daily/<YYYY-MM-DD>_daily_options` | Works (checked 2026-11-15). Total, equity, index, ETP, SPX and VIX ratios plus call and put volume and open interest. Cboe exchanges only, not all US options volume. Trading days only (a Saturday returned 403); 2020 dates still returned data. |
| Put/call ratio by symbol, implied move, 30-day implied volatility | CBOE delayed chain, no key: `https://cdn.cboe.com/api/global/delayed_quotes/options/<SYMBOL>.json` (index symbols take an underscore, `_SPX`). Sum `volume` or `open_interest` by put and call; implied move is the at-the-money call plus put mid price divided by the underlying `close`, per expiry. `iv30` is on the quote file | Works (checked 2026-11-15). One file per symbol holds every contract (NVDA 4,368 contracts, 2 MB), delayed 15 minutes, with bid, ask, volume, open interest, implied volatility and greeks. Skip adjusted roots (a root with a digit, such as `B1VDA`). The NVDA 2026-11-20 straddle matched `Alpaca-trading` `get_option_chain` quotes within 0.6%. Alpaca snapshots carry contract volume in `dailyBar.v` but no open interest. Same rate limit as the row above. |
| Short availability | `Alpaca-trading` `get_asset` | Works. Returns `shortable`, `easy_to_borrow`, `borrow_status`, `margin_requirement_short`. |
| Short interest and days to cover (shares short, twice a month) | FINRA API, no key: POST `https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest` with `compareFilters` on `symbolCode`, plus `dateRangeFilters` on `settlementDate` | Works (checked 2026-11-15). Covers exchange-listed and OTC equities, about 22,500 rows per settlement date. Fields include `currentShortPositionQuantity`, `previousShortPositionQuantity`, `changePercent`, `averageDailyVolumeQuantity` and `daysToCoverQuantity` (shares short divided by average daily volume). Without a date range the rows returned start in 2020, so always set one. `sortFields` is rejected unless `settlementDate` has an `EQUAL` filter. At most 5,000 rows per request; page with `offset`. Settlement dates are mid-month and month-end, published about a week later: the newest on 2026-11-15 was 2026-10-30. |
| Short interest as a percent of float, float shares | None | The FINRA data has share counts only, with no float or shares outstanding. No connected tool returns float. SEC EDGAR XBRL has shares outstanding (`dei:EntityCommonStockSharesOutstanding`) and the dollar public float from the 10-K cover page (`dei:EntityPublicFloat`), which would give a percent of shares outstanding rather than of float; it needs `SEC_USER_AGENT` and was not verified. The Nasdaq and Yahoo quote endpoints did not answer a plain request (connection reset and 429) and are not routed to. |
| Borrow fee | None | No connected source. |
| Fundamentals | `Sp-global`, `Daloopa` | Both need authentication; no tools until connected. |
| Earnings dates and results | None for upcoming dates. Past results: SEC EDGAR filing index (8-K with item 2.02, 10-Q, 10-K) | No calendar of future earnings dates. `Alpaca-trading` `get_news` gives headlines only; `get_corporate_actions` covers dividends, splits and mergers, not earnings. |
| Macro series (rates, CPI, GDP, jobs) | FRED public CSV over HTTP: `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>&cosd=<YYYY-MM-DD>` (for example `DGS10`, `DGS2`, `DFF`, `CPIAUCSL`, `UNRATE`, `PAYEMS`, `GDPC1`, `T10Y2Y`, `BAMLH0A0HYM2`) | Works without a key (checked 2026-11-15). One series per request, values only; missing observations are blank or `.`. There is no FRED MCP, and the JSON API at `api.stlouisfed.org` (search, release dates, revisions) needs a free `FRED_API_KEY`, which is not set. `You-finance` needs authentication. |
| Macro proxies | `Alpaca-trading` `get_stock_bars` on bond ETFs (`SHY,IEF,TLT,LQD,HYG,TIP`); `prediction-markets` Kalshi event markets (rate and inflation series such as `KXFEDCUT`) | Market-implied only, not the underlying economic releases. Alpaca index levels and fixed income quotes are not permitted on the current subscription. |
| US freight price and volume indexes (truck, rail, ocean, air, diesel) | FRED public CSV, as in the macro row: `FRGEXPUSM649NCIS` and `FRGSHPUSM649NCIS` (Cass freight expenditures and shipments), `PCU484121484121` (long-distance truckload producer prices), `PCU482111482111` (rail), `PCU483111483111` (deep sea freight), `PCU481112481112` (air freight), `GASDESW` (weekly retail diesel), `TSIFRGHT` and `TRUCKD11` (freight services and truck tonnage) | Works without a key (checked 2026-11-15). Monthly indexes, not dollar rates, except diesel, which is weekly dollars per gallon. On 2026-11-15 the monthly price series ran to October 2026 and the volume series to August 2026. FRED has no Baltic Dry Index. |
| Container spot rates | Freightos Baltic Index, no key: the page `https://www.freightos.com/enterprise/terminal/freightos-baltic-index-global-container-pricing-index/` embeds the global index and 12 lanes as JSON in `window.frProductIntroTickerData`. Drewry World Container Index: the `description` meta tag of `https://www.drewry.co.uk/supply-chain-advisors/supply-chain-expertise/world-container-index-assessed-by-drewry` | Works (checked 2026-11-15), but both are values read out of web pages, not APIs, and will break if the pages change. Freightos gives dollars per 40ft container and a percent change by lane code (`FBX01`, `FBX03`, `FBX11` and so on) with no date and no history; the old `fbx.freightos.com` API address redirects to the marketing site. Drewry gives one weekly composite figure with its date. |
| Dry bulk and tanker rates (Baltic Dry Index, Baltic tanker indexes) | None | The Baltic Exchange index page shows no values without a member login. Use the futures-based funds in the next row as a proxy. |
| Freight exposure that can be traded | `Alpaca-trading` `get_stock_bars` on `BDRY` (dry bulk freight futures fund), `BWET` (tanker freight futures fund), `BOAT` and `SEA` (shipping company funds), `IYT` (US transport stocks) | Works. These are fund prices: the two futures funds track near-dated freight futures, not the spot index, and the others are equities. Freight futures themselves (container, dry bulk and tanker forward agreements) have no route here and were not checked through IB. |
| Grain barge, rail and truck rates | USDA AgTransport open data, no key: `https://agtransport.usda.gov/resource/<id>.json` with `$limit` and `$order` (for example `7spn-fbua` weekly Mississippi barge dollars per ton, `fxkn-2w9c` quarterly grain truck dollars per mile, `3az4-jkr6` rail tariff rates); list datasets at `https://agtransport.usda.gov/api/views?limit=200` | Works (checked 2026-11-15). Agricultural freight only. Barge rates ran to 2026-11-10; quarterly truck rates to 2026 Q1. |
| Truckload spot and contract rates by lane (DAT, Truckstop, SONAR) | None | No connected source; these are paid subscriptions. The FRED truckload producer price index above is the nearest free measure. |
| US weather forecast, observations and alerts | National Weather Service API, no key: `https://api.weather.gov/points/<lat>,<lon>` returns the forecast URLs for that grid cell (`forecast`, `forecastHourly`); `/stations/<ID>/observations/latest`; `/alerts/active?severity=Extreme,Severe` | Works (checked 2026-11-15) with the default curl agent. US only, about seven days ahead. Always take the grid cell from the `points` call: a guessed cell returned a different forecast. Observations are metric. |
| Global forecast, forecast ensembles and weather history | Open-Meteo, no key: `https://api.open-meteo.com/v1/forecast` (up to 16 days), `https://ensemble-api.open-meteo.com/v1/ensemble` (30 members for `gfs_seamless`), `https://archive-api.open-meteo.com/v1/archive` (daily history back to 1940), each with `latitude`, `longitude` and `daily=` variables | Works (checked 2026-11-15). Model output for a grid cell, not a station reading, so it can differ from the official figure: for Central Park on 2026-11-15 it gave a high of 46.8F against 51F from the National Weather Service. The free tier is for non-commercial use. |
| Official daily station records (highs, lows, precipitation) | National Weather Service climate report: `https://api.weather.gov/products/types/CLI/locations/<site>` lists reports, then fetch the product for its text (for example `NYC` for Central Park). NOAA daily summaries, no token: `https://www.ncei.noaa.gov/access/services/data/v1?dataset=daily-summaries&stations=<ID>&startDate=...&endDate=...&dataTypes=TMAX,TMIN,PRCP&units=standard&format=csv` | Works (checked 2026-11-15). The climate report is fixed-width text with the day's high, low, precipitation, normals and records, issued overnight for the day before. The daily summaries CSV lagged five days. The older NOAA `cdo-web` API returned 400 without a token and is not needed. |
| Heating and cooling degree days (US, population weighted) | NOAA Climate Prediction Center, no key: `https://ftp.cpc.ncep.noaa.gov/htdocs/degree_days/weighted/daily_data/latest/Population.Heating.txt` and `Population.Cooling.txt`; earlier years under `daily_data/<YEAR>/` | Works (checked 2026-11-15). Pipe-delimited, one column per day, rows for the nine census divisions and `CONUS`; ran to 2026-11-13, with yearly folders back to 1981. No gas-weighted file was found at the path tried. |
| Drought and tropical storms | US Drought Monitor: `https://usdmdataservices.unl.edu/api/USStatistics/GetDroughtSeverityStatisticsByAreaPercent?aoi=us&startdate=<M/D/YYYY>&enddate=<M/D/YYYY>&statisticsType=1` with `Accept: application/json`. National Hurricane Center: `https://www.nhc.noaa.gov/CurrentStorms.json` | Work (checked 2026-11-15). Drought is weekly percent of area by category. The storm file listed no active storms, so its storm fields were not seen. |
| Weather prediction markets | `prediction-markets` `list_kalshi_markets` with `series_ticker` (for example `KXHIGHNY`, the Central Park daily high) and `status=open`; series list at `https://api.elections.kalshi.com/trade-api/v2/series?category=Climate%20and%20Weather` | Works over the Kalshi public endpoint the tool calls (checked 2026-11-15): 532 weather series, covering daily highs and lows by city, rain, snow and hurricanes. Read-only; prices are in the `yes_bid_dollars` and `yes_ask_dollars` fields. Each market's `rules_primary` names its settlement station and source (the New York market names `CLINYC`, the Central Park climate report in the row above); read it before comparing a forecast with a price. |
| Weather derivatives and weather-sensitive funds | `Alpaca-trading` `get_stock_bars` on `UNG` (natural gas), `WEAT`, `CORN`, `SOYB` (grain futures funds) | Works for the funds, which are only indirect weather exposure. Exchange-traded degree day futures have no route here. |
| Crypto prices | `Coinbase` `coinbase_products_best_bid_ask`; `Alpaca-trading` `get_crypto_snapshot` with `loc=us` | Both work. Prefer Coinbase for pricing: Alpaca quotes come from its own thin venue (BTC spread about $16 against $0.01 on Coinbase when checked). `Kraken` and `1inch-mcp` fail discovery. |
| Sector fund flows (ETF creations and redemptions) | None | No connected source. Unusual Whales is in `.cursor/mcp.json.example` but not connected. |
| Sector rotation proxy | `Alpaca-trading` `get_stock_bars` on the sector SPDRs (`XLK,XLF,XLE,XLV,XLI,XLY,XLP,XLU,XLB,XLRE,XLC`) | Works. Price change and traded dollar volume (`v` times `vw`) only; this is not money entering or leaving the funds. |
| SEC filings by company (10-K, 10-Q, 8-K, Form 4 insider trades, Form 144, 13G) | SEC EDGAR over HTTP: `https://www.sec.gov/files/company_tickers.json` for ticker to CIK, then `https://data.sec.gov/submissions/CIK<10-digit CIK>.json` | Unavailable until `SEC_USER_AGENT` is set to a real contact in the form `Name email@domain`, and every request sends it as the `User-Agent`. Without a declared contact the SEC returns 403 (checked 2026-11-15); with one, both URLs returned 200. The ticker map gives an unpadded `cik_str`; pad it with leading zeros to 10 digits. The response is a filing index (form, dates, accession number, primary document, 8-K item codes), not parsed contents. `filings.recent` holds only about the latest 1,000 filings; for older ones fetch each file named in `filings.files` from `https://data.sec.gov/submissions/<name>`. Forms 4 and 13G appear in the issuer's index; 13F does not (see the institutional row). No EDGAR MCP is connected. |
| Financial statement values and filing full-text search | SEC EDGAR `https://data.sec.gov/api/xbrl/companyfacts/CIK<10-digit CIK>.json` (zero-padded, as above) and `https://efts.sec.gov/LATEST/search-index?q=...` | Not verified from this environment. Same `SEC_USER_AGENT` requirement. |
| Institutional ownership (13F holdings), insider trades | SEC EDGAR filing index above, then fetch and parse the filing documents. A 13F is filed by the investment manager, so look it up under the manager's CIK, not the issuer's; `company_tickers.json` does not list managers that have no ticker | Not verified for 13F. No parser exists here for 13F information tables or Form 4 XML, so there is no ready holdings or insider-trade table, and no holdings-by-issuer view without aggregating across managers. `Sp-global` needs authentication; Unusual Whales is not connected. |
| House member trades | House Clerk, no key: yearly index `https://disclosures-clerk.house.gov/public_disc/financial-pdfs/<YEAR>FD.zip` (XML and tab-delimited text; `FilingType` `P` is a periodic transaction report), then `https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/<YEAR>/<DocID>.pdf` | Works (checked 2026-11-15). The 2026 index listed 468 transaction reports from 112 filers. The index names the filer and date only; the trades are in the PDF, as amount ranges rather than exact sizes. Two electronically filed PDFs extracted as clean text; no parser exists here. |
| Senate member trades | None | The Senate eFD search at `https://efdsearch.senate.gov/search/` requires accepting a terms-of-use agreement before any search, so it is not automated here. |
| President and executive branch trades | Office of Government Ethics, no key: `https://extapps2.oge.gov/201/Presiden.nsf/API.xsp/v3/rest?start=0&length=100&search[value]=<name>` returns filings with PDF links; type `278 Transaction` is a periodic transaction report (OGE Form 278-T) | Works (checked 2026-11-15). The PDFs are scans, so extracted text is OCR quality and needs checking against the page. Amounts are ranges. |
| Congress trades as a ready table | None connected | The Apify actor `johnvc/us-congress-financial-disclosures-and-stock-trading-data` charges per transaction and was not run. The house-stock-watcher and senate-stock-watcher data buckets returned 403. Unusual Whales is not connected. |
| Dark pool volume by symbol and venue (weekly) | FINRA OTC transparency API, no key: `POST https://api.finra.org/data/group/otcMarket/name/weeklySummary` with `compareFilters` on `weekStartDate` (a Monday, required), `issueSymbolIdentifier` and `tierIdentifier` (`T1`) | Works (checked 2026-11-15). `ATS_W_SMBL` rows are dark pool (ATS) totals and `ATS_W_SMBL_FIRM` rows are per venue; `OTC_W_SMBL` rows are non-ATS off-exchange volume, mostly wholesalers. Published about two to three weeks late: the newest week available was 2026-10-19, and 2026-10-26 returned 204. |
| Off-exchange volume and short volume by symbol (daily) | FINRA Reg SHO file, no key: `https://cdn.finra.org/equity/regsho/daily/CNMSshvol<YYYYMMDD>.txt` | Works. Pipe-delimited columns `Date`, `Symbol`, `ShortVolume`, `ShortExemptVolume`, `TotalVolume`, `Market` for trades reported to FINRA facilities. Covers all off-exchange volume and does not separate dark pools from wholesalers. Divide by `get_stock_bars` daily volume for an approximate off-exchange share. |
| Large or off-exchange prints | `Alpaca-trading` `get_stock_trades` with `feed=sip` | Works, with size `s` and venue `x` per print; venue `D` is the FINRA facility, meaning off-exchange. There is no size or venue filter, and two seconds of NVDA returned 1,064 prints, so only narrow time windows are practical. |
| Social mention counts | `X` `get_posts_counts_recent` with a cashtag query such as `$NVDA -is:retweet` | Works. Counts per minute, hour or day for the last 7 days only; counts, not sentiment. |
| Retail sentiment and trending tickers | StockTwits public API, no key: `https://api.stocktwits.com/api/2/streams/symbol/<TICKER>.json` and `/api/2/trending/symbols.json` | Works. 30 most recent messages per call; only some carry a user-set Bullish or Bearish tag (7 of 30 in the NVDA sample). |
| Reddit mention ranking | ApeWisdom, no key: `https://apewisdom.io/api/v1.0/filter/all-stocks/page/1` | Works. A third-party aggregator: mentions and upvotes for the last 24 hours against the 24 hours before. Reddit's own JSON returned 403 from this environment. |
| Sentiment gauges | Crypto Fear and Greed: `https://api.alternative.me/fng/?limit=<N>`. FRED CSV: `VIXCLS` (VIX close), `UMCSENT` (consumer sentiment) | Work. CNN's equity Fear and Greed endpoint refuses automated requests. No connected source scores news articles for sentiment. |
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