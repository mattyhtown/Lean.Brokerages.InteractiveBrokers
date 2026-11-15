import { Canvas } from '@react-three/fiber'
import { ContactShadows } from '@react-three/drei'
import type { DeskState } from '../data/types'
import { palette } from './materials'
import { Room } from './Room'
import { Scaffolding } from './Scaffolding'
import { DeskSurface } from './DeskSurface'
import { HeroBrand } from './HeroBrand'
import { KillSwitchPillar } from './KillSwitchPillar'
import { FloatingPanel } from './FloatingPanel'
import { DustMotes } from './DustMotes'
import { CameraRig } from './CameraRig'
import {
  SignalsContent,
  RiskContent,
  BacktestContent,
  MacroContent,
  ChainContent,
} from './stations/StationContent'

type Props = {
  state: DeskState
  selected: string | null
  onSelect: (id: string) => void
}

const FOCUS: Record<string, [number, number, number]> = {
  signals: [-3.4, 2.2, 0.2],
  risk: [3.5, 2.3, 0.1],
  backtest: [-2.6, 2.5, -3.2],
  macro: [2.8, 2.6, -3.4],
  chain: [0.2, 3.2, -1.6],
}

function SceneBody({ state, selected, onSelect }: Props) {
  return (
    <>
      <color attach="background" args={[palette.fog]} />
      <fog attach="fog" args={[palette.fog, 7, 22]} />
      <ambientLight intensity={0.28} />
      <directionalLight
        castShadow
        position={[5, 9, 3]}
        intensity={1.05}
        color="#e8d9b8"
        shadow-mapSize={[2048, 2048]}
      />
      <pointLight position={[-3.5, 3.2, 1.5]} intensity={0.7} color={palette.amber} />
      <pointLight position={[4.5, 2.6, -0.5]} intensity={0.45} color={palette.mint} />
      <spotLight
        position={[0, 5.8, 1.5]}
        angle={0.5}
        penumbra={0.55}
        intensity={0.85}
        color="#f2e6c8"
        castShadow
      />
      <pointLight position={[0, 3.4, 3.2]} intensity={0.65} color={palette.coral} distance={8} />

      <Room />
      <Scaffolding />
      <DeskSurface />
      <HeroBrand brand={state.brand} symbol={state.symbol} />
      <KillSwitchPillar safety={state.safety} flatten={state.flatten} />
      <DustMotes count={160} />

      <FloatingPanel
        id="signals"
        position={[-3.4, 2.2, 0.2]}
        rotation={[0, 0.38, 0]}
        title="Signals"
        subtitle="Live proposes · read-only"
        swayPhase={0.2}
        accent={palette.mint}
        selected={selected === 'signals'}
        onSelect={onSelect}
      >
        <SignalsContent state={state} />
      </FloatingPanel>

      <FloatingPanel
        id="risk"
        position={[3.5, 2.35, 0.1]}
        rotation={[0, -0.42, 0]}
        title="Risk"
        subtitle="Hazard · flatten · limits"
        swayPhase={1.1}
        accent={palette.coral}
        width={2.35}
        height={2.05}
        selected={selected === 'risk'}
        onSelect={onSelect}
      >
        <RiskContent state={state} />
      </FloatingPanel>

      <FloatingPanel
        id="backtest"
        position={[-2.6, 2.55, -3.2]}
        rotation={[0, 0.18, 0]}
        title="Backtest"
        subtitle="Journal Sharpe ledger"
        swayPhase={2.0}
        accent={palette.amber}
        width={2.3}
        height={1.55}
        selected={selected === 'backtest'}
        onSelect={onSelect}
      >
        <BacktestContent state={state} />
      </FloatingPanel>

      <FloatingPanel
        id="macro"
        position={[2.8, 2.7, -3.4]}
        rotation={[0, -0.22, 0]}
        title="Macro"
        subtitle="News + blackout gate"
        swayPhase={2.7}
        accent={palette.mint}
        width={2.3}
        height={1.55}
        selected={selected === 'macro'}
        onSelect={onSelect}
      >
        <MacroContent state={state} />
      </FloatingPanel>

      <FloatingPanel
        id="chain"
        position={[0.2, 3.35, -1.6]}
        title="Chain skew"
        subtitle="Net call bias surface"
        swayPhase={0.7}
        accent={palette.copper}
        width={2.6}
        height={1.45}
        selected={selected === 'chain'}
        onSelect={onSelect}
      >
        <ChainContent state={state} />
      </FloatingPanel>

      <ContactShadows position={[0, 0.01, 0]} opacity={0.55} scale={20} blur={2.2} far={9} />
      <CameraRig focus={selected ? FOCUS[selected] ?? null : null} />
    </>
  )
}

export function DeskScene({ state, selected, onSelect }: Props) {
  return (
    <Canvas
      className="desk-canvas"
      shadows
      dpr={[1, 1.75]}
      camera={{ position: [0, 1.72, 6.4], fov: 55, near: 0.1, far: 60 }}
      gl={{ antialias: true, alpha: false }}
      onPointerMissed={() => onSelect('')}
    >
      <SceneBody state={state} selected={selected} onSelect={onSelect} />
    </Canvas>
  )
}
