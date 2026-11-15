# Alpha research cache layout

All research ingest writers target these paths so `python -m odte_spy_ibkr.research evaluate` can load them.

| Path | Contents | Sources |
|------|----------|---------|
| `alpaca/minute/{YYYY-MM-DD}.json` | SPY 1-min OHLCV bars | Alpaca Data API |
| `alpaca/spy_daily.json` | Daily closes (metadata) | Alpaca |
| `ibkr/minute/{date}.json` | SPY 1-min bars | IB `reqHistoricalData` (TWS/Gateway) |
| `chains/SPY/{date}.json` | Option chain @ 11:00 ET (`rows[]`) | Alpaca option bars, Theta Data, ORATS, UW, IBKR |
| `chains/SPY/{date}_close.json` | Option chain @ 16:00 ET (spread exit marks) | Same sources |
| `thetadata/ingest_summary.json` | Last Theta ingest stats | Theta Terminal |
| `tradingeconomics/daily/{date}.json` | US calendar rows + surprise for that session | Trading Economics |
| `tradingeconomics/macro_calendar/{year}.json` | Full-year merged TE events | Trading Economics |
| `quantconnect/minute/{date}.json` | SPY 1-min OHLCV from LEAN equity files | QuantConnect |
| `quantconnect/ingest_notes.json` | QC auth + download limits from last ingest | QuantConnect |
| `uw/daily/{date}.json` | `aggregate_bias` from historical flow-per-strike | Unusual Whales |
| `uw/sessions/{date}.jsonl` | Raw UW poll bundles | Unusual Whales |
| `orats/ingest_notes.json` | Endpoint + plan notes from last ORATS ingest | ORATS |

**Not run in cloud agent VM:** UW, IBKR, ORATS, Theta Data, Trading Economics, and QuantConnect ingests are unit-tested with mocks only. QC additionally needs LEAN CLI + Dataset Market (paid tier / QCC) for downloads; REST auth alone does not stream history.

## Commands (Matthew’s machine)

```bash
cd odte-spy-ibkr
source .venv/bin/activate
export UNUSUAL_WHALES_API_KEY=...   # optional
export ORATS_API_TOKEN=...          # optional
export ALPACA_API_KEY=... ALPACA_API_SECRET=...  # optional

# Alpaca only (default)
python -m odte_spy_ibkr.research ingest --source alpaca --start 2026-07-01 --end 2026-11-05

# Unusual Whales historical flow + chain (tier limits per UW subscription)
python -m odte_spy_ibkr.research ingest --source uw --start 2026-07-01 --end 2026-11-05

# ORATS historical one-minute strikes chain (CSV; Intraday/Historical Data API plan)
python -m odte_spy_ibkr.research ingest --source orats --start 2026-07-01 --end 2026-11-05

# IB: start TWS or IB Gateway, API enabled, READ_ONLY=true, then:
python -m odte_spy_ibkr.research ingest --source ibkr --start 2026-10-01 --end 2026-11-05

# Theta Data: start Theta Terminal v3 locally, then:
export THETADATA_USERNAME=... THETADATA_PASSWORD=...
python -m odte_spy_ibkr.research ingest --source thetadata --start 2026-07-01 --end 2026-11-05

export TRADING_ECONOMICS_API_KEY=...
python -m odte_spy_ibkr.research ingest --source tradingeconomics --start 2026-07-01 --end 2026-11-05

export QUANTCONNECT_USER_ID=... QUANTCONNECT_API_TOKEN=...
# Optional: lean login / lean init workspace; QCC downloads only when QUANTCONNECT_ALLOW_PAID_DOWNLOAD=true
python -m odte_spy_ibkr.research ingest --source quantconnect --start 2026-07-01 --end 2026-11-05

python -m odte_spy_ibkr.research evaluate --run 2
```