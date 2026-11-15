# ODTE Desk — strict 3D trading room

Vite + React + TypeScript + React Three Fiber room for the **ODTE SPY** system.

Orders stay **OFF**. The UI never sets `ENABLE_ORDERS`. Kill switch, Sharpe / macro / flow gates, flatten rules, and read-only signals / positions appear as suspended panels inside a scaffolded warehouse desk.

## Run

```bash
cd odte-spy-ibkr/desk
npm install
npm run dev
```

Open the printed local URL (default `http://localhost:5173`). Drag to orbit, scroll to zoom.

Production build:

```bash
npm run build
npm run preview
```

## Data: mock vs live

| Source | How | Requires IBKR? |
|--------|-----|----------------|
| **Static mock** (default) | `public/mock/desk-state.json` (+ embedded fallback) | No |
| **Optional local engine** | Set `VITE_DESK_TRY_ENGINE=1` and optionally `VITE_DESK_STATE_URL` (default `http://127.0.0.1:8787/desk/state`) | No — only if you expose a read-only JSON endpoint |

`loadDeskState()` always forces `enableOrders: false` and `readOnly: true` after any fetch.

There is **no order ticket** and **no control** that arms live placement.

## Stations in the room

1. **Signals** — proposed spreads, blocked reasons, empty positions  
2. **Risk** — kill / flatten / limits / ORDERS OFF lock  
3. **Backtest** — rolling Sharpe snapshot  
4. **Macro** — headlines + blackout / risk-off  
5. **Chain skew** — `net_call_bias` meter  

Scaffolding frames, suspended cables, dust motes, and a pulsing kill pillar give depth and motion without dashboard-card clutter in the hero view. Brand **ODTE Desk** is large wall type in the first composition.

## Safety contract

- Matches engine defaults: `ENABLE_ORDERS=false`, `READ_ONLY=true`, `LIVE_TRADING=false`  
- UI cannot flip those flags  
- Strict mode badge + ORDERS OFF chrome always visible  

## Stack

- Vite 8 / React 19 / TypeScript  
- `three`, `@react-three/fiber`, `@react-three/drei`  
- Fonts: **Syne** (display) + **IBM Plex Mono** (data)
