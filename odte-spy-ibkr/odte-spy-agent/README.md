# odte-spy-agent

An agent SDK (`@grok/agent-sdk`) project that runs the research side of the
`odte_spy_ibkr` engine in the parent folder. It does not trade.

## Tools

| Tool | Runs | Effect |
| --- | --- | --- |
| `signal_state` | `python -m odte_spy_ibkr.hosted_state` | read |
| `research_ingest` | `python -m odte_spy_ibkr.research ingest` (alpaca, uw, orats, tradingeconomics, quantconnect) | write |
| `research_evaluate` | `python -m odte_spy_ibkr.research evaluate` | read |
| `backtest` | `python -m odte_spy_ibkr.backtest --require-rolling-sharpe` | read |
| `improvement_run` | `python -m odte_spy_ibkr.improvement run` | write |

There is no order tool. The model has no built-in tools (`tools: []`), so it
has no shell and cannot edit files. Every engine process is started by
`bot/lib/engine.ts`, which:

- runs only the four modules above, never the live runner (`python -m odte_spy_ibkr`);
- passes an allowlisted environment, then sets `ENABLE_ORDERS=false`,
  `LIVE_TRADING=false`, `READ_ONLY=true`, `PAPER=true`, both
  `IMPROVEMENT_AUTO_PROMOTE_*` flags and `QUANTCONNECT_ALLOW_PAID_DOWNLOAD`
  to `false`;
- points `CONFIG_YAML` at `bot/lib/engine-safety.yaml`, which the engine
  applies after the environment;
- redacts secret values from the output it returns.

## What it cannot do

Interactive Brokers needs a TWS or Gateway session on the same network. A
hosted agent has none, so there is no live option chain, net call bias, entry
intent, IBKR history ingest, account view or order placement. `signal_state`
returns those fields as `null` with the reason.

Theta Data ingest is not exposed either: the engine's client talks to a Theta
Terminal on `127.0.0.1`, and the hosted agent has no such process.

## Needs

- Node 22.13 or newer.
- Python 3.11 or newer with the engine's dependencies (`pip install -e ..`).
  Set `ODTE_PYTHON` to pick the interpreter and `ODTE_ENGINE_DIR` when the
  engine is not in the parent folder.
- Secret names are declared in `bot/lib/hosting.ts`. Values are set per
  deployment with `agent-sdk secret set`, never in this repository.

## Develop

```bash
npm install
npm run check
npm test                      # fake engine, no Python and no network
npx agent-sdk validate --dir . --json
npx agent-sdk tool call signal_state --dir . --json
```
