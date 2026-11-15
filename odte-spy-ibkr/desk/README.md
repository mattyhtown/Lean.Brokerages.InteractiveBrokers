# ODTE Desk — live 3D ops-floor game

Vite + React + TypeScript + React Three Fiber **industrial sci-fi ops floor** for **ODTE SPY**.

Art direction: graphite bulkheads, copper scaffold, signal amber lanes, mint GO / coral STOP lamps — game HUD + player-height WASD/orbit, not a flat dashboard.

Orders stay **UI-locked**. The desk never places trades and never sets `ENABLE_ORDERS=true`.

## Live mode (preferred)

Terminal 1 — state API (polls settings, journal Sharpe, gates, providers; IBKR optional):

```bash
cd odte-spy-ibkr
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export ENABLE_ORDERS=false
python -m odte_spy_ibkr.desk_api --interval 5
# → http://127.0.0.1:8787/desk/state
# → writes data/desk/signal_state.json each tick
```

Terminal 2 — UI:

```bash
cd odte-spy-ibkr/desk
npm install
npm run dev
```

Vite proxies `/api/desk/state` → the API (no CORS). The room polls every ~4s; HUD shows `data: engine · tick N` and a live `asOf`.

### What “live” means

| Feed | Source |
|------|--------|
| Sharpe / backtest panel | `data/journal/daily_pnl.jsonl` |
| RTH / flatten / risk gate | Wall-clock + `Settings` |
| Macro / flow / spot / skew | Provider aggregator (Alpaca/UW/etc. if configured) |
| Signals | Built each tick from gates + context |
| Degraded | Providers/IBKR missing → still refreshes clocks + journal |

Without TWS/keys the loop still runs: panels update, `degraded` lists why providers failed. Static mock is **fallback only** if the API is down.

## Safety

- Desk API forces `ENABLE_ORDERS=false` in-process
- HUD shows engine `ENABLE_ORDERS` / `READ_ONLY` truthfully + **UI ORDERS LOCKED**
- No order ticket exists in the UI

## Optional env

| Var | Default | Meaning |
|-----|---------|---------|
| `VITE_DESK_POLL_MS` | `4000` | Poll interval |
| `VITE_DESK_STATE_URL` | `http://127.0.0.1:8787/desk/state` | Direct URL if proxy unused |

## Build

```bash
npm run build
npm run preview
```
