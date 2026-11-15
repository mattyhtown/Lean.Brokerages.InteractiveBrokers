# Universal instruments Cursor plugin

Agent tooling for **multi-asset** coverage alongside [Interactive Brokers + LEAN](../../../README.md):

- **Prediction markets** (Kalshi + Polymarket, read-only MCP)
- **IB lean bridge** (historical bars, contract details, order preview and gated order entry via IB Gateway)
- **Instrument router skill** for routing chat to the right MCP
- **Setup docs** for MCP health and LEAN/IB live trading

## Install in Cursor

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

4. In Cursor **Settings → Tools & MCP**, add servers from [`mcp.json`](mcp.json) (adjust `${workspaceFolder}` if needed).

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
chmod +x agent-tools/universal-instruments-plugin/scripts/check-ib-env.sh
./agent-tools/universal-instruments-plugin/scripts/check-ib-env.sh
```

## Tests

```bash
cd agent-tools/universal-instruments-plugin/mcp/ib-lean-bridge
python -m unittest test_server.py
```

The tests use a stand-in for the IB client, so they need the packages in `requirements.txt` but no Gateway.

## Components

| MCP | Tools |
|-----|--------|
| `prediction-markets` | `list_kalshi_markets`, `get_kalshi_market`, `get_kalshi_orderbook`, `get_kalshi_trades`, `get_kalshi_candlesticks`, `list_polymarket_markets`, `get_polymarket_market`, `get_polymarket_orderbook`, `get_polymarket_price_history` |
| `ib-lean-bridge` | `ping`, `get_historical_bars`, `get_contract_details`, `get_open_orders`, `preview_order`, `place_order`, `cancel_order` |

Existing Cursor MCPs (Alpaca, Coinbase, etc.) remain the primary path for US equities, options, and crypto; see the instrument-router skill.