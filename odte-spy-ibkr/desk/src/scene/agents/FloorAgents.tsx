import { useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html } from '@react-three/drei'
import * as THREE from 'three'
import type { DeskState, GateStatus } from '../../data/types'
import { palette } from '../materials'

/** Floor waypoints near each station — character-scale ops floor patrol. */
const STATION_WAYPOINTS: Record<string, [number, number, number]> = {
  signals: [-2.85, 0, 2.45],
  risk: [2.9, 0, 2.45],
  backtest: [-2.4, 0, -2.35],
  macro: [2.5, 0, -2.45],
  chain: [0.1, 0, -0.85],
}

const LOOP_HUB: [number, number, number] = [0, 0, 3.05]

type AgentSpec = {
  id: string
  name: string
  role: string
  color: string
  accent: string
  /** Ordered station ids to visit */
  route: string[]
  /** Base walk speed (units/sec) */
  speed: number
  /** Phase offset so agents don't sync */
  phase: number
}

const AGENTS: AgentSpec[] = [
  {
    id: 'scout-a',
    name: 'SCOUT-A',
    role: 'Signals runner',
    color: '#3d4a42',
    accent: palette.mint,
    route: ['signals', 'chain', 'macro', 'signals'],
    speed: 1.15,
    phase: 0,
  },
  {
    id: 'risk-b',
    name: 'RISK-B',
    role: 'Hazard watch',
    color: '#4a3a36',
    accent: palette.coral,
    route: ['risk', 'chain', 'backtest', 'risk'],
    speed: 0.95,
    phase: 1.7,
  },
  {
    id: 'ledger-c',
    name: 'LEDGER-C',
    role: 'Sharpe clerk',
    color: '#3a3a48',
    accent: palette.amber,
    route: ['backtest', 'macro', 'signals', 'backtest'],
    speed: 1.05,
    phase: 3.4,
  },
  {
    id: 'flow-d',
    name: 'FLOW-D',
    role: 'Skew runner',
    color: '#2e3c38',
    accent: palette.copper,
    route: ['chain', 'risk', 'macro', 'chain'],
    speed: 1.25,
    phase: 5.1,
  },
]

function worstGate(gates: DeskState['gates']): GateStatus {
  const vals = Object.values(gates).map((g) => g.status)
  if (vals.includes('block')) return 'block'
  if (vals.includes('watch')) return 'watch'
  return 'pass'
}

function routePoints(route: string[]): THREE.Vector3[] {
  const pts: THREE.Vector3[] = [new THREE.Vector3(...LOOP_HUB)]
  for (const id of route) {
    const wp = STATION_WAYPOINTS[id]
    if (wp) pts.push(new THREE.Vector3(...wp))
  }
  pts.push(new THREE.Vector3(...LOOP_HUB))
  return pts
}

function AgentFigure({
  spec,
  urgency,
}: {
  spec: AgentSpec
  urgency: number
}) {
  const root = useRef<THREE.Group>(null)
  const leftLeg = useRef<THREE.Mesh>(null)
  const rightLeg = useRef<THREE.Mesh>(null)
  const leftArm = useRef<THREE.Mesh>(null)
  const rightArm = useRef<THREE.Mesh>(null)
  const glow = useRef<THREE.Mesh>(null)

  const path = useMemo(() => routePoints(spec.route), [spec.route])
  const segLens = useMemo(() => {
    const lens: number[] = []
    for (let i = 0; i < path.length - 1; i++) {
      lens.push(path[i].distanceTo(path[i + 1]))
    }
    return lens
  }, [path])
  const totalLen = useMemo(() => segLens.reduce((a, b) => a + b, 0), [segLens])

  const state = useRef({
    dist: (spec.phase * 2.3) % Math.max(totalLen, 0.01),
    dwell: 0,
    atStation: false,
  })

  useFrame((_, dt) => {
    const g = root.current
    if (!g || totalLen < 0.01) return

    const speedMul = 1 + urgency * 0.85
    const walkSpeed = spec.speed * speedMul

    // Dwell at stations so they "work the panel"
    if (state.current.dwell > 0) {
      state.current.dwell -= dt
      const bob = Math.sin(performance.now() * 0.006 + spec.phase) * 0.02
      g.position.y = bob
      if (leftLeg.current) leftLeg.current.rotation.x = 0
      if (rightLeg.current) rightLeg.current.rotation.x = 0
      if (leftArm.current) leftArm.current.rotation.x = -0.35
      if (rightArm.current) rightArm.current.rotation.x = 0.2
      if (glow.current) {
        const mat = glow.current.material as THREE.MeshStandardMaterial
        mat.emissiveIntensity = 0.55 + Math.sin(performance.now() * 0.008) * 0.25
      }
      return
    }

    state.current.dist += walkSpeed * dt
    if (state.current.dist >= totalLen) state.current.dist -= totalLen

    let remaining = state.current.dist
    let seg = 0
    while (seg < segLens.length - 1 && remaining > segLens[seg]) {
      remaining -= segLens[seg]
      seg++
    }
    const a = path[seg]
    const b = path[Math.min(seg + 1, path.length - 1)]
    const len = segLens[seg] || 1
    const t = THREE.MathUtils.clamp(remaining / len, 0, 1)
    const pos = a.clone().lerp(b, t)
    g.position.set(pos.x, 0, pos.z)

    const dir = b.clone().sub(a)
    if (dir.lengthSq() > 1e-6) {
      const yaw = Math.atan2(dir.x, dir.z)
      g.rotation.y = THREE.MathUtils.lerp(g.rotation.y, yaw, 1 - Math.exp(-8 * dt))
    }

    // Arrive near a station waypoint → short dwell
    const nearStation = Object.values(STATION_WAYPOINTS).some((wp) => {
      const dx = pos.x - wp[0]
      const dz = pos.z - wp[2]
      return dx * dx + dz * dz < 0.04
    })
    if (nearStation && t > 0.92 && !state.current.atStation) {
      state.current.atStation = true
      state.current.dwell = 0.9 + (spec.phase % 1) * 0.6
    } else if (!nearStation) {
      state.current.atStation = false
    }

    const swing = Math.sin(performance.now() * 0.014 * speedMul + spec.phase) * 0.55
    if (leftLeg.current) leftLeg.current.rotation.x = swing
    if (rightLeg.current) rightLeg.current.rotation.x = -swing
    if (leftArm.current) leftArm.current.rotation.x = -swing * 0.7
    if (rightArm.current) rightArm.current.rotation.x = swing * 0.7
    g.position.y = Math.abs(Math.sin(performance.now() * 0.014 * speedMul + spec.phase)) * 0.03

    if (glow.current) {
      const mat = glow.current.material as THREE.MeshStandardMaterial
      mat.emissiveIntensity = 0.35 + urgency * 0.4
    }
  })

  return (
    <group ref={root} scale={1.35} castShadow>
      {/* Foot shadow disc */}
      <mesh position={[0, 0.02, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <circleGeometry args={[0.28, 20]} />
        <meshBasicMaterial color="#000000" transparent opacity={0.35} />
      </mesh>
      {/* Boots */}
      <mesh position={[-0.1, 0.09, 0.03]} castShadow>
        <boxGeometry args={[0.14, 0.1, 0.22]} />
        <meshStandardMaterial color="#121614" roughness={0.75} />
      </mesh>
      <mesh position={[0.1, 0.09, 0.03]} castShadow>
        <boxGeometry args={[0.14, 0.1, 0.22]} />
        <meshStandardMaterial color="#121614" roughness={0.75} />
      </mesh>
      {/* Legs */}
      <mesh ref={leftLeg} position={[-0.1, 0.32, 0]} castShadow>
        <boxGeometry args={[0.12, 0.4, 0.14]} />
        <meshStandardMaterial color={spec.color} roughness={0.55} metalness={0.2} />
      </mesh>
      <mesh ref={rightLeg} position={[0.1, 0.32, 0]} castShadow>
        <boxGeometry args={[0.12, 0.4, 0.14]} />
        <meshStandardMaterial color={spec.color} roughness={0.55} metalness={0.2} />
      </mesh>
      {/* Torso */}
      <mesh position={[0, 0.78, 0]} castShadow>
        <boxGeometry args={[0.42, 0.52, 0.24]} />
        <meshStandardMaterial color={spec.color} roughness={0.4} metalness={0.3} />
      </mesh>
      {/* Pack / silhouette */}
      <mesh position={[0, 0.82, -0.16]} castShadow>
        <boxGeometry args={[0.28, 0.36, 0.12]} />
        <meshStandardMaterial color="#1a221e" roughness={0.5} metalness={0.4} />
      </mesh>
      {/* Antenna */}
      <mesh position={[0.12, 1.28, -0.05]} castShadow>
        <cylinderGeometry args={[0.015, 0.015, 0.28, 6]} />
        <meshStandardMaterial color={spec.accent} emissive={spec.accent} emissiveIntensity={0.6} />
      </mesh>
      {/* Chest lamp */}
      <mesh ref={glow} position={[0, 0.84, 0.14]}>
        <boxGeometry args={[0.16, 0.1, 0.05]} />
        <meshStandardMaterial
          color={spec.accent}
          emissive={spec.accent}
          emissiveIntensity={0.85}
          toneMapped={false}
        />
      </mesh>
      {/* Arms */}
      <mesh ref={leftArm} position={[-0.3, 0.78, 0]} castShadow>
        <boxGeometry args={[0.1, 0.4, 0.12]} />
        <meshStandardMaterial color={spec.color} roughness={0.5} />
      </mesh>
      <mesh ref={rightArm} position={[0.3, 0.78, 0]} castShadow>
        <boxGeometry args={[0.1, 0.4, 0.12]} />
        <meshStandardMaterial color={spec.color} roughness={0.5} />
      </mesh>
      {/* Head + visor */}
      <mesh position={[0, 1.18, 0]} castShadow>
        <boxGeometry args={[0.26, 0.26, 0.26]} />
        <meshStandardMaterial color="#1e2622" roughness={0.35} metalness={0.45} />
      </mesh>
      <mesh position={[0, 1.18, 0.14]}>
        <boxGeometry args={[0.2, 0.09, 0.05]} />
        <meshStandardMaterial
          color={spec.accent}
          emissive={spec.accent}
          emissiveIntensity={1.1}
          toneMapped={false}
        />
      </mesh>
      {/* Name plate */}
      <Html position={[0, 1.58, 0]} center distanceFactor={7} style={{ pointerEvents: 'none' }}>
        <div className="agent-tag">
          <strong>{spec.name}</strong>
          <span>{spec.role}</span>
        </div>
      </Html>
    </group>
  )
}

type Props = {
  state: DeskState
}

export function FloorAgents({ state }: Props) {
  const gate = worstGate(state.gates)
  const urgency =
    (gate === 'block' ? 1 : gate === 'watch' ? 0.45 : 0) +
    (state.flatten.shouldFlatten ? 0.55 : 0) +
    (state.safety.killSwitchArmed ? 0.35 : 0)
  const clamped = Math.min(1.5, urgency)

  // Quiet data still moves — never freeze agents
  return (
    <group>
      {/* Lane markers so paths read as game floor routes */}
      {Object.entries(STATION_WAYPOINTS).map(([id, wp]) => (
        <mesh key={id} position={[wp[0], 0.02, wp[2]]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[0.22, 0.32, 24]} />
          <meshStandardMaterial
            color={palette.amber}
            emissive={palette.amber}
            emissiveIntensity={0.35}
            transparent
            opacity={0.55}
            toneMapped={false}
          />
        </mesh>
      ))}
      {AGENTS.map((spec) => (
        <AgentFigure key={spec.id} spec={spec} urgency={clamped} />
      ))}
    </group>
  )
}
