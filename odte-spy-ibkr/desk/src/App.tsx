import { useEffect, useRef, useState } from 'react'
import { DeskScene } from './scene/DeskScene'
import { HudChrome } from './ui/HudChrome'
import { loadDeskState } from './data/loadDeskState'
import type { DeskState } from './data/types'
import { mockDeskState } from './data/mockFallback'

const POLL_MS = Number(import.meta.env.VITE_DESK_POLL_MS || 4000)

export default function App() {
  const [state, setState] = useState<DeskState>(mockDeskState)
  const [ready, setReady] = useState(false)
  const seq = useRef(0)
  const latest = useRef(state)
  latest.current = state

  useEffect(() => {
    let alive = true

    const refresh = async () => {
      const mySeq = ++seq.current
      const next = await loadDeskState(latest.current)
      if (!alive || mySeq !== seq.current) return
      setState(next)
      setReady(true)
    }

    void refresh()
    const id = window.setInterval(() => {
      void refresh()
    }, POLL_MS)

    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [])

  return (
    <div className="app-shell">
      <DeskScene state={state} />
      <HudChrome state={state} />
      {!ready ? <div className="boot-veil">assembling scaffolding…</div> : null}
    </div>
  )
}
