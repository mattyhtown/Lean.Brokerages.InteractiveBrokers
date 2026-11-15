# Interactive Brokers + LEAN setup

This repository is the [QuantConnect IB brokerage plugin](https://github.com/QuantConnect/Lean.Brokerages.InteractiveBrokers). Use it with **LEAN live** for asset classes that MCPs do not cover fully: **forex**, **bonds**, **CME futures**, **global options** (e.g. NDX/RUT), and **mutual funds**.

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
| Bond | US Treasury via IB bond contract | Use `BOND` / ISIN or CUSIP in IB symbology |
| Index option | `SPX` option chain | Also available via Alpaca MCP when enabled |

## When to use IB vs MCP

| Need | Prefer |
|------|--------|
| US equity options chain in chat | Alpaca `get_option_chain` |
| FX, bonds, CME futures in chat | **ib-lean-bridge** MCP (this plugin) with Gateway running |
| Automated algo execution | LEAN live + this brokerage DLL |
| Prediction markets | **prediction-markets** MCP |

## Local check script

```bash
./agent-tools/universal-instruments-plugin/scripts/check-ib-env.sh
```

Reports whether Gateway env vars are set and whether port `7497`/`4002` accepts connections (does not log in to IB).

## Cloud vs self-hosted

QuantConnect Cloud can host live IB with QC data feed, IB feed, or both — often simpler than self-hosting market data. See [QC IB integration](https://www.quantconnect.com/docs/v2/our-platform/live-trading/brokerages/interactive-brokers).