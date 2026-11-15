import { useCallback, useEffect, useRef, useState } from 'react'
import { DeskScene } from './scene/DeskScene'
import { GameHud } from './ui/GameHud'
import { loadDeskState } from './data/loadDeskState'
import type { DeskState } from './data/types'
import { mockDeskState } from './data/mockFallback'

const POLL_MS = Number(import.meta.env.VITE_DESK_POLL_MS || 4000)

export default function App() {
  const [state, setState] = useState<DeskState>(mockDeskState)
  const [ready, setReady] = useState(false)
  const [selected, setSelected] = useState<string | null>('signals')
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

  const onSelect = useCallback((id: string) => {
    setSelected(id ? id : null)
  }, [])

  return (
    <div className="app-shell game-skin">
      <DeskScene state={state} selected={selected} onSelect={onSelect} />
      <GameHud state={state} selected={selected} onSelect={onSelect} />
      {!ready ? <div className="boot-veil">booting ops floor…</div> : null}
    </div>
  )
}
