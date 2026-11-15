import type { DeskState, GateState } from '../../data/types'
import { statusColor, statusVerb } from '../materials'

function GateChip({ gate }: { gate: GateState | undefined }) {
  if (!gate) {
    return (
      <div className="gate-chip" style={{ borderColor: statusColor('watch') }}>
        <span className="gate-dot" style={{ background: statusColor('watch') }} />
        <div>
          <strong>HOLD</strong>
          <em>gate unavailable</em>
          <p>Retained prior mock / incomplete engine data</p>
        </div>
      </div>
    )
  }

  return (
    <div className="gate-chip" style={{ borderColor: statusColor(gate.status) }}>
      <span className="gate-dot" style={{ background: statusColor(gate.status) }} />
      <div>
        <strong>
          {statusVerb(gate.status)} · {gate.label}
        </strong>
        <em>
          {gate.value} / {gate.threshold}
        </em>
        <p>{gate.detail}</p>
      </div>
    </div>
  )
}

export function SignalsContent({ state }: { state: DeskState }) {
  const signals = state.signals ?? []
  const positions = state.positions ?? []
  const spot = Number.isFinite(state.spot) ? state.spot : 0

  return (
    <div className="station-block">
      <p className="spot">
        {state.symbol ?? 'SPY'} {spot.toFixed(2)} · read-only
      </p>
      <ul className="signal-list">
        {signals.map((s) => (
          <li key={s.id} className={s.blocked ? 'blocked' : 'open'}>
            <span>{s.side.replace('_', ' ')}</span>
            <p>{s.summary}</p>
            <small>
              {s.blocked ? 'BLOCKED' : 'VISIBLE'} · {s.reason}
            </small>
          </li>
        ))}
      </ul>
      {positions.length === 0 ? (
        <p className="muted">No open positions</p>
      ) : (
        <ul className="signal-list">
          {positions.map((p) => (
            <li key={p.id}>
              <span>
                {p.side} ×{p.contracts}
              </span>
              <p>
                credit {p.entryCredit} · manage {p.manageAction}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function RiskContent({ state }: { state: DeskState }) {
  const flatten = state.flatten
  return (
    <div className="station-block">
      <GateChip gate={state.gates?.risk} />
      <GateChip gate={state.gates?.sharpe} />
      <div className="kv">
        <span>Flatten</span>
        <strong>
          {flatten?.shouldFlatten ? 'NOW' : `${flatten?.minutesToFlatten ?? '—'}m`}
        </strong>
      </div>
      <div className="kv">
        <span>RTH</span>
        <strong>{flatten?.rth ? 'open' : 'closed'}</strong>
      </div>
      <div className="kv warn">
        <span>UI orders</span>
        <strong>LOCKED</strong>
      </div>
      <div className="kv">
        <span>Engine ENABLE_ORDERS</span>
        <strong>{state.safety?.enableOrders ? 'true' : 'false'}</strong>
      </div>
      <div className="kv">
        <span>Engine READ_ONLY</span>
        <strong>{state.safety?.readOnly === false ? 'false' : 'true'}</strong>
      </div>
      <p className="muted">{state.safety?.ordersLockedReason ?? 'Desk UI cannot place orders'}</p>
    </div>
  )
}

export function BacktestContent({ state }: { state: DeskState }) {
  const b = state.backtest
  return (
    <div className="station-block">
      <GateChip gate={state.gates?.sharpe} />
      <div className="metrics-grid">
        <div>
          <em>Trades</em>
          <strong>{b?.trades ?? '—'}</strong>
        </div>
        <div>
          <em>Win</em>
          <strong>
            {b && Number.isFinite(b.winRate) ? `${(b.winRate * 100).toFixed(0)}%` : '—'}
          </strong>
        </div>
        <div>
          <em>Sharpe</em>
          <strong>
            {b && Number.isFinite(b.rollingSharpe) ? b.rollingSharpe.toFixed(2) : '—'}
          </strong>
        </div>
        <div>
          <em>Max DD</em>
          <strong>
            {b && Number.isFinite(b.maxDrawdown)
              ? `${(b.maxDrawdown * 100).toFixed(1)}%`
              : '—'}
          </strong>
        </div>
      </div>
      <p className="muted">{b?.note ?? 'No backtest snapshot'}</p>
    </div>
  )
}

export function MacroContent({ state }: { state: DeskState }) {
  const macro = state.macro
  const headlines = macro?.headlines ?? []
  return (
    <div className="station-block">
      <GateChip gate={state.gates?.macro} />
      <div className="kv">
        <span>Risk-off</span>
        <strong>{macro?.riskOff ? 'YES' : 'no'}</strong>
      </div>
      <div className="kv">
        <span>Blackout</span>
        <strong>{macro?.blackout ? 'ACTIVE' : 'clear'}</strong>
      </div>
      <ul className="headline-list">
        {headlines.map((h, i) => (
          <li key={i}>
            <small>{h.source}</small> {h.title}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function ChainContent({ state }: { state: DeskState }) {
  const c = state.chainSkew
  const bias = c && Number.isFinite(c.netCallBias) ? c.netCallBias : 0
  return (
    <div className="station-block">
      <GateChip gate={state.gates?.flow} />
      <div className="skew-meter">
        <div
          className="skew-fill"
          style={{
            width: `${((bias + 1) / 2) * 100}%`,
          }}
        />
        <span>net_call_bias {bias.toFixed(2)}</span>
      </div>
      <div className="kv">
        <span>Call prem</span>
        <strong>
          {c && Number.isFinite(c.callPremiumMid)
            ? `${(c.callPremiumMid / 1e6).toFixed(2)}M`
            : '—'}
        </strong>
      </div>
      <div className="kv">
        <span>Put prem</span>
        <strong>
          {c && Number.isFinite(c.putPremiumMid)
            ? `${(c.putPremiumMid / 1e6).toFixed(2)}M`
            : '—'}
        </strong>
      </div>
      <p className="muted">{c?.note ?? 'No skew snapshot'}</p>
    </div>
  )
}
