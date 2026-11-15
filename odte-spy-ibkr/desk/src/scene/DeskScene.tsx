import { Suspense } from 'react'
import { Canvas } from '@react-three/fiber'
import { Environment, ContactShadows } from '@react-three/drei'
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
}

function SceneBody({ state }: Props) {
  return (
    <>
      <color attach="background" args={['#141816']} />
      <fog attach="fog" args={['#141816', 10, 28]} />
      <ambientLight intensity={0.35} />
      <directionalLight
        castShadow
        position={[6, 10, 4]}
        intensity={1.15}
        color="#f0e2c4"
        shadow-mapSize={[2048, 2048]}
      />
      <pointLight position={[-4, 3.5, 2]} intensity={0.55} color={palette.amber} />
      <pointLight position={[5, 2.8, -1]} intensity={0.4} color="#8fb9a0" />
      <spotLight
        position={[0, 6, 2]}
        angle={0.45}
        penumbra={0.6}
        intensity={0.7}
        color="#e8d7b4"
        castShadow
      />

      <Room />
      <Scaffolding />
      <DeskSurface />
      <HeroBrand brand={state.brand} symbol={state.symbol} />
      <KillSwitchPillar safety={state.safety} flatten={state.flatten} />
      <DustMotes />

      {/* Layered stations in depth */}
      <FloatingPanel
        position={[-3.4, 2.35, 0.2]}
        rotation={[0, 0.35, 0]}
        title="Signals"
        subtitle="Proposed spreads · read-only"
        swayPhase={0.2}
        accent={palette.mint}
      >
        <SignalsContent state={state} />
      </FloatingPanel>

      <FloatingPanel
        position={[3.5, 2.45, 0.1]}
        rotation={[0, -0.4, 0]}
        title="Risk"
        subtitle="Kill · flatten · limits"
        swayPhase={1.1}
        accent={palette.coral}
        width={2.2}
        height={1.7}
      >
        <RiskContent state={state} />
      </FloatingPanel>

      <FloatingPanel
        position={[-2.6, 2.7, -3.2]}
        rotation={[0, 0.15, 0]}
        title="Backtest"
        subtitle="Rolling Sharpe ledger"
        swayPhase={2.0}
        accent={palette.amber}
        width={2.3}
        height={1.55}
      >
        <BacktestContent state={state} />
      </FloatingPanel>

      <FloatingPanel
        position={[2.8, 2.85, -3.4]}
        rotation={[0, -0.2, 0]}
        title="Macro"
        subtitle="News + blackout gate"
        swayPhase={2.7}
        accent="#8fb9a0"
        width={2.3}
        height={1.55}
      >
        <MacroContent state={state} />
      </FloatingPanel>

      <FloatingPanel
        position={[0.2, 3.55, -1.6]}
        title="Chain skew"
        subtitle="Net call bias surface"
        swayPhase={0.7}
        accent={palette.copper}
        width={2.6}
        height={1.45}
      >
        <ChainContent state={state} />
      </FloatingPanel>

      <ContactShadows position={[0, 0.01, 0]} opacity={0.45} scale={18} blur={2.4} far={8} />
      <Environment preset="warehouse" environmentIntensity={0.25} />
      <CameraRig />
    </>
  )
}

export function DeskScene({ state }: Props) {
  return (
    <Canvas
      className="desk-canvas"
      shadows
      dpr={[1, 1.75]}
      camera={{ position: [0.2, 2.4, 7.2], fov: 42, near: 0.1, far: 60 }}
      gl={{ antialias: true, alpha: false }}
    >
      <Suspense fallback={null}>
        <SceneBody state={state} />
      </Suspense>
    </Canvas>
  )
}
