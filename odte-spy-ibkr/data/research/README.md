# Alpha research cache layout

All research ingest writers target these paths so `python -m odte_spy_ibkr.research evaluate` can load them.

| Path | Contents | Sources |
|------|----------|---------|
| `alpaca/minute/{YYYY-MM-DD}.json` | SPY 1-min OHLCV bars | Alpaca Data API |
| `alpaca/spy_daily.json` | Daily closes (metadata) | Alpaca |
| `ibkr/minute/{date}.json` | SPY 1-min bars | IB `reqHistoricalData` (TWS/Gateway) |
| `chains/SPY/{date}.json` | Option chain @ 11:00 ET (`rows[]`) | ORATS, UW, IBKR |
| `chains/SPY/{date}_close.json` | Option chain @ 16:00 ET (spread exit marks) | ORATS (when available) |
| `uw/daily/{date}.json` | `aggregate_bias` from historical flow-per-strike | Unusual Whales |
| `uw/sessions/{date}.jsonl` | Raw UW poll bundles | Unusual Whales |
| `orats/ingest_notes.json` | Endpoint + plan notes from last ORATS ingest | ORATS |

**Not run in cloud agent VM:** UW, IBKR, and ORATS ingest were implemented and unit-tested with mocks only (no `UNUSUAL_WHALES_API_KEY`, no TWS session, no `ORATS_API_TOKEN` on the agent). Alpaca remains the only live-ingested source there when keys exist.

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

python -m odte_spy_ibkr.research evaluate --holdout-days 12
```