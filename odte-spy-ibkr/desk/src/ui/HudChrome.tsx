import type { DeskState } from '../data/types'

type Props = {
  state: DeskState
}

/** Minimal chrome — not hero cards; status strip only */
export function HudChrome({ state }: Props) {
  return (
    <div className="hud">
      <div className="hud-left">
        <span className="mode-pill">{state.mode.toUpperCase()} DESK</span>
        <span className="src-pill">data: {state.source}</span>
      </div>
      <div className="hud-center">
        <span>drag to orbit · scroll to zoom · walk the room</span>
      </div>
      <div className="hud-right">
        <span className="lock-pill">ORDERS OFF</span>
        <span className="asof">{state.asOf}</span>
      </div>
    </div>
  )
}
