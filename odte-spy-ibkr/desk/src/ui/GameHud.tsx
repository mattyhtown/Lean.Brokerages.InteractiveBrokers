import type { DeskState, GateState } from '../data/types'
import { statusColor, statusVerb } from '../scene/materials'

type Props = {
  state: DeskState
  selected: string | null
  onSelect: (id: string) => void
}

function GateLamp({ gate }: { gate: GateState | undefined }) {
  const status = gate?.status ?? 'watch'
  const color = statusColor(status)
  return (
    <button type="button" className={`gate-lamp status-${status}`} style={{ ['--lamp' as string]: color }}>
      <span className="lamp-bulb" />
      <span className="lamp-meta">
        <strong>{statusVerb(status)}</strong>
        <em>{gate?.label ?? 'Gate'}</em>
      </span>
    </button>
  )
}

const STATIONS = [
  { id: 'signals', label: 'SIG' },
  { id: 'risk', label: 'RSK' },
  { id: 'backtest', label: 'BT' },
  { id: 'macro', label: 'MAC' },
  { id: 'chain', label: 'SKW' },
] as const

/** Corner game HUD — minimap + gate lamps + lock bar (not dashboard cards) */
export function GameHud({ state, selected, onSelect }: Props) {
  const live = state.source === 'engine' || state.source === 'file'
  const gates = state.gates ?? {}

  return (
    <div className="game-hud" aria-label="ODTE Desk game HUD">
      {/* Top-left: title strip + live feed */}
      <div className="hud-tl">
        <div className="title-chip">
          <span className="title-chip-mark">◉</span>
          <div>
            <strong>ODTE DESK</strong>
            <em>STRICT OPS · UI ORDERS LOCKED</em>
          </div>
        </div>
        <div className={`feed-chip ${live ? 'on' : ''}`}>
          <span className="feed-dot" />
          {state.source.toUpperCase()}
          {state.tick != null ? ` · T${state.tick}` : ''}
        </div>
      </div>

      {/* Top-right: hazard / lock */}
      <div className="hud-tr">
        <div className="hazard-chip">
          <strong>{state.safety?.killSwitchArmed ? 'HAZARD ARMED' : 'HAZARD SAFE'}</strong>
          <em>KILL · FLATTEN {state.flatten?.flattenTimeEt ?? '—'}</em>
        </div>
        <div className="lock-chip">
          UI LOCK
          <span>{state.safety?.enableOrders ? 'ENGINE ORDERS ON' : 'ENGINE ORDERS OFF'}</span>
        </div>
      </div>

      {/* Bottom-left: gate lamps as GO/HOLD/STOP */}
      <div className="hud-bl">
        <div className="lamp-row">
          <GateLamp gate={gates.sharpe} />
          <GateLamp gate={gates.macro} />
          <GateLamp gate={gates.flow} />
          <GateLamp gate={gates.risk} />
        </div>
        <div className="clock-bar">
          <span>{state.symbol} {Number.isFinite(state.spot) ? state.spot.toFixed(2) : '—'}</span>
          <span>{state.flatten?.rth ? 'RTH OPEN' : 'RTH CLOSED'}</span>
          <span>{state.asOf}</span>
        </div>
      </div>

      {/* Bottom-right: minimap / station compass */}
      <div className="hud-br">
        <div className="minimap" aria-label="Station compass">
          <div className="minimap-ring">
            {STATIONS.map((s, i) => {
              const angle = (i / STATIONS.length) * Math.PI * 2 - Math.PI / 2
              const x = 50 + Math.cos(angle) * 34
              const y = 50 + Math.sin(angle) * 34
              return (
                <button
                  key={s.id}
                  type="button"
                  className={`mini-node ${selected === s.id ? 'active' : ''}`}
                  style={{ left: `${x}%`, top: `${y}%` }}
                  onClick={() => onSelect(s.id)}
                >
                  {s.label}
                </button>
              )
            })}
            <div className="mini-core">YOU</div>
          </div>
          <p className="mini-hint">WASD · drag orbit · click station</p>
        </div>
      </div>

      {state.degraded?.length ? (
        <div className="degrade-banner">DEGRADED · {state.degraded.join(' · ')}</div>
      ) : null}
    </div>
  )
}
