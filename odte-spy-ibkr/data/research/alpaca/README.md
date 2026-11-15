Real Alpaca market data cache for alpha research (no synthetic bars).

Populate via:

- `python -m odte_spy_ibkr.research ingest` (requires `ALPACA_API_KEY` / `ALPACA_API_SECRET`)
- Or `scripts/bootstrap_alpaca_from_mcp_json.py` on Alpaca MCP `get_stock_bars` JSON exports

Current agent snapshot: **IEX** 1-minute SPY, ~38 sessions (2026-07-01 → 2026-11-05).