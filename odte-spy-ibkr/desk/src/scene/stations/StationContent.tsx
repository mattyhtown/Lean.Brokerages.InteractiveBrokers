import type { DeskState, GateState } from '../../data/types'
import { statusColor } from '../materials'

function GateChip({ gate }: { gate: GateState }) {
  return (
    <div className="gate-chip" style={{ borderColor: statusColor(gate.status) }}>
      <span className="gate-dot" style={{ background: statusColor(gate.status) }} />
      <div>
        <strong>{gate.label}</strong>
        <em>
          {gate.value} / {gate.threshold} · {gate.status}
        </em>
        <p>{gate.detail}</p>
      </div>
    </div>
  )
}

export function SignalsContent({ state }: { state: DeskState }) {
  return (
    <div className="station-block">
      <p className="spot">
        {state.symbol} {state.spot.toFixed(2)} · read-only
      </p>
      <ul className="signal-list">
        {state.signals.map((s) => (
          <li key={s.id} className={s.blocked ? 'blocked' : 'open'}>
            <span>{s.side.replace('_', ' ')}</span>
            <p>{s.summary}</p>
            <small>
              {s.blocked ? 'BLOCKED' : 'VISIBLE'} · {s.reason}
            </small>
          </li>
        ))}
      </ul>
      {state.positions.length === 0 ? (
        <p className="muted">No open positions</p>
      ) : (
        <ul className="signal-list">
          {state.positions.map((p) => (
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
  return (
    <div className="station-block">
      <GateChip gate={state.gates.risk} />
      <GateChip gate={state.gates.sharpe} />
      <div className="kv">
        <span>Flatten</span>
        <strong>
          {state.flatten.shouldFlatten ? 'NOW' : `${state.flatten.minutesToFlatten}m`}
        </strong>
      </div>
      <div className="kv">
        <span>RTH</span>
        <strong>{state.flatten.rth ? 'open' : 'closed'}</strong>
      </div>
      <div className="kv warn">
        <span>Orders</span>
        <strong>OFF</strong>
      </div>
      <p className="muted">{state.safety.ordersLockedReason}</p>
    </div>
  )
}

export function BacktestContent({ state }: { state: DeskState }) {
  const b = state.backtest
  return (
    <div className="station-block">
      <GateChip gate={state.gates.sharpe} />
      <div className="metrics-grid">
        <div>
          <em>Trades</em>
          <strong>{b.trades}</strong>
        </div>
        <div>
          <em>Win</em>
          <strong>{(b.winRate * 100).toFixed(0)}%</strong>
        </div>
        <div>
          <em>Sharpe</em>
          <strong>{b.rollingSharpe.toFixed(2)}</strong>
        </div>
        <div>
          <em>Max DD</em>
          <strong>{(b.maxDrawdown * 100).toFixed(1)}%</strong>
        </div>
      </div>
      <p className="muted">{b.note}</p>
    </div>
  )
}

export function MacroContent({ state }: { state: DeskState }) {
  return (
    <div className="station-block">
      <GateChip gate={state.gates.macro} />
      <div className="kv">
        <span>Risk-off</span>
        <strong>{state.macro.riskOff ? 'YES' : 'no'}</strong>
      </div>
      <div className="kv">
        <span>Blackout</span>
        <strong>{state.macro.blackout ? 'ACTIVE' : 'clear'}</strong>
      </div>
      <ul className="headline-list">
        {state.macro.headlines.map((h, i) => (
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
  return (
    <div className="station-block">
      <GateChip gate={state.gates.flow} />
      <div className="skew-meter">
        <div
          className="skew-fill"
          style={{
            width: `${((c.netCallBias + 1) / 2) * 100}%`,
          }}
        />
        <span>net_call_bias {c.netCallBias.toFixed(2)}</span>
      </div>
      <div className="kv">
        <span>Call prem</span>
        <strong>{(c.callPremiumMid / 1e6).toFixed(2)}M</strong>
      </div>
      <div className="kv">
        <span>Put prem</span>
        <strong>{(c.putPremiumMid / 1e6).toFixed(2)}M</strong>
      </div>
      <p className="muted">{c.note}</p>
    </div>
  )
}
