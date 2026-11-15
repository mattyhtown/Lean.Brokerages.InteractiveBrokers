# Interactive Brokers + LEAN setup

This repository is the [QuantConnect IB brokerage plugin](https://github.com/QuantConnect/Lean.Brokerages.InteractiveBrokers). Use it with **LEAN live** for asset classes that MCPs do not cover fully: **forex**, **CME futures** and **index and futures options** (e.g. NDX/RUT). **Bonds** are the exception: LEAN has no bond security type, and this plugin only carries IB's `BOND` constant and contract-details callback, so a LEAN algorithm cannot subscribe to or trade a bond. Reach bonds through the **ib-lean-bridge** MCP, which talks to IB directly.

## Prerequisites

1. **IB account** with permissions for the asset classes you trade.
2. **Market data subscriptions** on IB (options, futures, forex, and bonds are separate bundles).
3. **QuantConnect organization** with the Interactive Brokers module.
4. **IB Gateway** or **TWS** — API enabled, trusted IP `127.0.0.1`, read-only off if you intend to trade.

## LEAN CLI live deployment

From your [LEAN CLI root](https://www.quantconnect.com/docs/v2/lean-cli/initialization/directory-structure#02-lean-init):

```bash
lean live "My Project"
```

Select **Interactive Brokers** as brokerage, then provide org, username, account id, and credentials per the [repo README](../../../README.md).

## Smoke symbols (after live connect)

Use these in a minimal algorithm or the **ib-lean-bridge** MCP to confirm each asset class:

| Asset class | Example LEAN / IB symbol | Notes |
|-------------|--------------------------|--------|
| Equity | `SPY` | US ETF |
| Forex | `EURUSD` | IDEALPRO |
| Future | `ES` (continuous / mapped) | Requires futures data |
| Bond | US Treasury via IB bond contract | Bridge only, not LEAN. Find the contract with `get_contract_details` (`sec_type=BOND`), then pass its `conId` as `con_id` |
| Index option | `SPX` option chain | Also available via Alpaca MCP when enabled |

### Smoke test through the bridge

With Gateway or TWS running, call the bridge tools in this order. Each step only reads until the last one.

1. `ping` — shows the host, port, client id and whether order sending is on. No network call.
2. `get_contract_details` — `symbol=EURUSD, sec_type=CASH, exchange=IDEALPRO`; then `symbol=ES, sec_type=FUT, exchange=CME`; then a Treasury with `sec_type=BOND`.
3. `get_historical_bars` — the same forex pair; the future with `expiry=<YYYYMM>`; the bond with `con_id=<conId from step 2>`. Forex has no trade bars at IB, so the bridge asks for midpoint bars.
4. `preview_order` — a small limit order on each. IB answers with the margin change and commission and sends nothing.
5. `place_order` — only if you want a real order; see the next section.

This has not been run against a live Gateway: the Cursor cloud agent VM has no IB login, and nothing listens on `127.0.0.1:7497` there (checked 2026-11-15). The bridge's logic is covered by `mcp/ib-lean-bridge/test_server.py`, which uses a stand-in for the IB client.

## Sending orders through the bridge

Order sending is off unless you turn it on.

| Setting | Effect |
|---------|--------|
| `IB_ALLOW_ORDERS=1` in the server environment | Allows `place_order` and `cancel_order`. Anything else, including unset, refuses both. |
| `confirm=true` on the call | Required on every `place_order` and `cancel_order`. The agent must show the `preview_order` result and get your approval first. |
| `IB_ACCOUNT` | Account to trade when the login has more than one. A call can override it with `account`. |
| `IB_PORT` | IB's defaults are `7497` (TWS paper), `7496` (TWS live), `4002` (Gateway paper), `4001` (Gateway live). Start on a paper port. |

Gateway's own **Read-Only API** setting must be off for previews and orders. `place_order` supports limit and market orders, `DAY` or `GTC`. `cancel_order` only reaches orders this bridge placed under the same `IB_CLIENT_ID`; `get_open_orders` lists every open order on the account. If a `place_order` call times out or errors, check `get_open_orders` before trying again, so an order that did reach IB is not sent twice.

## When to use IB vs MCP

| Need | Prefer |
|------|--------|
| US equity options chain in chat | Alpaca `get_option_chain` |
| FX, bonds, CME futures in chat | **ib-lean-bridge** MCP (this plugin) with Gateway running |
| Bonds, any workflow | **ib-lean-bridge** MCP; LEAN has no bond security type |
| Automated algo execution | LEAN live + this brokerage DLL |
| Prediction markets | **prediction-markets** MCP |

## Local check script

```bash
./agent-tools/universal-instruments-plugin/scripts/check-ib-env.sh
```

Prints the host, port and client id the bridge will use, whether order sending is on, and whether the configured `IB_PORT` accepts connections (it does not log in to IB).

## Cloud vs self-hosted

QuantConnect Cloud can host live IB with QC data feed, IB feed, or both — often simpler than self-hosting market data. See [QC IB integration](https://www.quantconnect.com/docs/v2/our-platform/live-trading/brokerages/interactive-brokers).