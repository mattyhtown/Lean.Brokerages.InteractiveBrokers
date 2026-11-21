# Universal instruments Cursor plugin

Agent tooling for **multi-asset** coverage alongside [Interactive Brokers + LEAN](../../../README.md):

- **Prediction markets** (Kalshi + Polymarket, read-only MCP)
- **IB lean bridge** (historical bars, quotes, positions, account summary, contract details, order preview and gated order entry via local TWS/IB Gateway)
- **SEC EDGAR XML parser** (zero-dependency Form 4 insider trades and 13F-HR institutional holdings parser)
- **Instrument router skill** for routing chat to the right MCP or keyless HTTP fallback
- **Setup docs & local scripts** for MCP health, one-command local install, and LEAN/IB live trading

## Install in Cursor

Run the idempotent local installer (builds `prediction-markets`, sets up an isolated Python `.venv` for `ib-lean-bridge`, symlinks the plugin to `~/.cursor/plugins/local/universal-instruments`, and prints the `mcpServers` block without modifying `~/.cursor/mcp.json`):

```bash
./agent-tools/universal-instruments-plugin/scripts/install-local-plugin.sh
# Preview without changing anything:
./agent-tools/universal-instruments-plugin/scripts/install-local-plugin.sh --dry-run
```

Or install manually:

1. Copy or symlink this folder to `~/.cursor/plugins/local/universal-instruments/` **or** enable MCP entries from this repo’s `mcp.json` in your user/project MCP config.
2. Build the prediction-markets server:

   ```bash
   cd agent-tools/universal-instruments-plugin/mcp/prediction-markets
   npm install && npm run build
   ```

3. Install the IB bridge (optional, for forex/bonds/futures from chat):

   ```bash
   pip install -r agent-tools/universal-instruments-plugin/mcp/ib-lean-bridge/requirements.txt
   export IB_HOST=127.0.0.1 IB_PORT=7497 IB_CLIENT_ID=77
   ```

   The bridge reads and previews by default. It only sends or cancels orders when the server is started with `IB_ALLOW_ORDERS=1`, and each call must also pass `confirm=true`. Set `IB_ACCOUNT` if the login has more than one account. See [IB_LEAN_SETUP.md](docs/IB_LEAN_SETUP.md).

4. In Cursor **Settings → Tools & MCP**, add servers from [`mcp.json`](mcp.json) (or paste the block printed by `install-local-plugin.sh`).

## Skills

| Skill | Path |
|-------|------|
| instrument-router | [`skills/instrument-router/SKILL.md`](skills/instrument-router/SKILL.md) |

## Documentation

| Doc | Purpose |
|-----|---------|
| [MCP_HEALTH_CHECKLIST.md](docs/MCP_HEALTH_CHECKLIST.md) | Reconnect Alpaca, ORATS, Kraken; OPRA and Coinbase product types |
| [IB_LEAN_SETUP.md](docs/IB_LEAN_SETUP.md) | LEAN live + IB Gateway smoke symbols |

## Scripts

```bash
# Build servers, create ib-lean-bridge .venv, symlink plugin, print mcp.json snippet
./agent-tools/universal-instruments-plugin/scripts/install-local-plugin.sh

# Check IB env vars and TCP port reachability (no IB login)
./agent-tools/universal-instruments-plugin/scripts/check-ib-env.sh

# Read-only smoke test against local TWS (7497) or IB Gateway (4002)
./agent-tools/universal-instruments-plugin/mcp/ib-lean-bridge/.venv/bin/python \
  ./agent-tools/universal-instruments-plugin/scripts/smoke-test-ib.py --port 7497

# Parse SEC EDGAR Form 4 insider trades or 13F-HR holdings (requires SEC_USER_AGENT)
SEC_USER_AGENT="Your Name you@domain.com" python3 \
  ./agent-tools/universal-instruments-plugin/scripts/edgar_parse.py form4 --ticker AAPL --limit 3
```

## Tests

```bash
python3 -m unittest discover -s agent-tools/universal-instruments-plugin/mcp/ib-lean-bridge
python3 -m unittest discover -s agent-tools/universal-instruments-plugin/scripts
```

The IB bridge tests use a stand-in for the IB client, so they need the packages in `requirements.txt` but no Gateway. The EDGAR parser tests use embedded XML fixtures and make no network calls.

## Components

| MCP | Tools |
|-----|--------|
| `prediction-markets` | `list_kalshi_markets`, `get_kalshi_market`, `get_kalshi_orderbook`, `get_kalshi_trades`, `get_kalshi_candlesticks`, `list_polymarket_markets`, `get_polymarket_market`, `get_polymarket_orderbook`, `get_polymarket_price_history` |
| `ib-lean-bridge` | `ping`, `get_historical_bars`, `get_contract_details`, `get_open_orders`, `get_positions`, `get_account_summary`, `get_quote`, `preview_order`, `place_order`, `cancel_order` |

Existing Cursor MCPs (Alpaca, Coinbase, etc.) remain the primary path for US equities, options, and crypto; see the instrument-router skill.