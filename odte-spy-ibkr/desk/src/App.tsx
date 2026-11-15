import { useEffect, useState } from 'react'
import { DeskScene } from './scene/DeskScene'
import { HudChrome } from './ui/HudChrome'
import { loadDeskState } from './data/loadDeskState'
import type { DeskState } from './data/types'
import { mockDeskState } from './data/mockFallback'

export default function App() {
  const [state, setState] = useState<DeskState>(mockDeskState)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let alive = true
    loadDeskState().then((s) => {
      if (!alive) return
      setState(s)
      setReady(true)
    })
    return () => {
      alive = false
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
