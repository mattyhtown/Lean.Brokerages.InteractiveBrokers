# Alpha Vantage research cache

```bash
export ALPHAVANTAGE_API_KEY=...
python -m odte_spy_ibkr.research ingest --source alphavantage --start 2026-07-01 --end 2026-11-05
```

## Endpoints used

| Endpoint | Purpose | Free-tier notes |
|----------|---------|-----------------|
| `GLOBAL_QUOTE` | Live runner spot cross-check | 1 call per provider refresh |
| `TIME_SERIES_DAILY_ADJUSTED` | `spy_daily.json` | Single call per ingest if cache missing |
| `TIME_SERIES_INTRADAY` (1min) | Per-session `minute/{date}.json` | **One call** returns many days; split locally (respect 5/min, ~25/day limits) |
| `REALTIME_OPTIONS` | Optional `options/SPY/realtime.json` | Set `ALPHAVANTAGE_INGEST_REALTIME_OPTIONS=true`; entitlement varies |
| `HISTORICAL_OPTIONS` | Optional dated `options/SPY/{date}.json` | Set `ALPHAVANTAGE_INGEST_OPTIONS=true`; **often premium** — empty days recorded, no synthetic chains |

## Layout

- `data/research/alphavantage/minute/{YYYY-MM-DD}.json` — Alpaca-compatible bar list (`t`, `o`, `h`, `l`, `c`, `v`)
- `data/research/alphavantage/spy_daily.json`
- `data/research/alphavantage/options/SPY/` — optional option payloads (raw API JSON)

`load_minute_bars()` checks Alpaca → QuantConnect → **Alpha Vantage** → IBKR roots.