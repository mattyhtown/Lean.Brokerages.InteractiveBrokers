import type { DeskState } from '../data/types'

type Props = {
  state: DeskState
}

/** Minimal chrome — strict badge always; ORDERS locked for UI; engine flags shown honestly */
export function HudChrome({ state }: Props) {
  const engineOrders = Boolean(state.safety?.enableOrders)
  const live = state.source === 'engine' || state.source === 'file'
  return (
    <div className="hud">
      <div className="hud-left">
        <span className="mode-pill">STRICT DESK</span>
        <span className={`src-pill ${live ? 'live' : ''}`}>
          data: {state.source}
          {state.tick != null ? ` · tick ${state.tick}` : ''}
        </span>
        {state.degraded?.length ? (
          <span className="src-pill warn">degraded: {state.degraded.join(',')}</span>
        ) : null}
      </div>
      <div className="hud-center">
        <span>drag to orbit · scroll to zoom · live poll</span>
      </div>
      <div className="hud-right">
        <span className="lock-pill">UI ORDERS LOCKED</span>
        {engineOrders ? (
          <span className="src-pill warn">engine ENABLE_ORDERS=true</span>
        ) : (
          <span className="src-pill">engine orders off</span>
        )}
        <span className="asof">{state.asOf}</span>
      </div>
    </div>
  )
}
