# OpenBB research cache

Ingest:

```bash
pip install 'odte-spy-ibkr[openbb]'
export OPENBB_PAT=...   # optional for Hub; required for most historical providers
python -m odte_spy_ibkr.research ingest --source openbb --start 2026-07-01 --end 2026-11-05
```

## What OpenBB supplies

| Mode | Providers (default) | Historical `date=` | Notes |
|------|---------------------|-------------------|--------|
| Live runner | `OPENBB_LIVE_PROVIDER=cboe` | N/A | Full chain + greeks when package installed |
| Research ingest | `OPENBB_HISTORICAL_PROVIDER=intrinio` | Yes (EOD) | Needs `OPENBB_PAT` + provider credentials in OpenBB Hub |
| Free without PAT | `cboe`, `yfinance` | **No** | Current chain only; ingest skips days (no synthetic) |

Chains land in `data/research/openbb/chains/SPY/{date}.json` and, when the unified cache is empty, `data/research/chains/SPY/{date}.json` with `source=openbb`.

Backtests read unified chains first, then OpenBB cache. Prefer existing `alpaca_option_bars` when both exist.