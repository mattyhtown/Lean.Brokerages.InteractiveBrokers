# odte-spy-agent

An agent SDK (`@grok/agent-sdk`) project that runs the research side of the
`odte_spy_ibkr` engine in the parent folder. It does not trade.

## Tools

| Tool | Runs | Effect |
| --- | --- | --- |
| `signal_state` | `python -m odte_spy_ibkr.hosted_state` | idempotent (caches the day's headlines) |
| `research_ingest` | `python -m odte_spy_ibkr.research ingest` (alpaca, uw, orats, tradingeconomics, quantconnect) | write |
| `research_evaluate` | `python -m odte_spy_ibkr.research evaluate` | read |
| `backtest` | `python -m odte_spy_ibkr.backtest --require-rolling-sharpe` | write (its own cache) |
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
- sets `ODTE_NO_DOTENV=1`, so the engine does not read a `.env` in its
  folder. Keys for a local run go in the shell that starts the agent;
- redacts secret values from the output it returns.

Ingest, evaluate, backtest and improvement jobs started by one agent process
run one at a time: the engine reads and writes its cache files without
locking. A second process on the same engine folder is not coordinated.
`signal_state` does not wait in that queue. The one file it writes is the
day's headline cache, which the engine's `macro_news` provider fills on its
first call of the day and reads for the rest of it.

`backtest` and `improvement_run` return `chainCoverage` for the sessions the
score rests on: the holdout window for an improvement run or a walk-forward
backtest, every session otherwise. Each of those sessions is in one count.

The engine skips a session it has no price for, and scores it as a flat day.
`no_local_spot_sessions` counts the sessions with no price on disk (a cached
daily bar or research minute bars); with Alpaca keys the engine asks Alpaca
for a daily bar before it skips one. The backtest summary's
`skipped_no_spot` is the number it did skip, over the whole range.

The engine simulates a session on that day's own option chain only when the
research cache has one (ORATS or Unusual Whales ingest, or an OpenBB chain
file already on disk; OpenBB ingest is not a source here). For any
other session it uses a chain an earlier backtest cached, Alpaca's current
snapshot, or a synthetic chain, and none of those is evidence about that day.

## What it cannot do

Interactive Brokers needs a TWS or Gateway session on the same network. A
hosted agent has none, so there is no live option chain, net call bias, entry
intent, IBKR history ingest, account view or order placement. `signal_state`
returns those fields as `null` with the reason.

The engine enables its `openbb`, `macro_news` and `tradingeconomics`
providers by default. `openbb` needs the optional `openbb` package and reads
`OPENBB_PAT`; this agent declares neither, so that provider reports as not
configured. `macro_news` and `tradingeconomics` answer 0 and `false` when
they have no headlines, release file or calendar to read, so `signal_state`
returns those macro fields as `null` and lists what was missing in
`macro_unavailable_reasons`.

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
