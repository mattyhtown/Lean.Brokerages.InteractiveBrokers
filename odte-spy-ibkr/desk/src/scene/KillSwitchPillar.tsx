import { useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html } from '@react-three/drei'
import type { Mesh, MeshStandardMaterial } from 'three'
import type { SafetyState, FlattenState } from '../data/types'
import { palette } from './materials'

type Props = {
  safety: SafetyState
  flatten: FlattenState
}

/** Boss / hazard prop — pulsing kill lamp at character scale */
export function KillSwitchPillar({ safety, flatten }: Props) {
  const lamp = useRef<Mesh>(null)
  const ring = useRef<Mesh>(null)

  useFrame(({ clock }) => {
    const t = clock.getElapsedTime()
    const pulse = safety.killSwitchArmed
      ? 0.55 + Math.sin(t * 5.5) * 0.45
      : 0.12
    if (lamp.current) {
      const mat = lamp.current.material as MeshStandardMaterial
      mat.emissiveIntensity = pulse
    }
    if (ring.current) {
      ring.current.rotation.y = t * 0.8
      const mat = ring.current.material as MeshStandardMaterial
      mat.emissiveIntensity = 0.2 + pulse * 0.4
    }
  })

  return (
    <group position={[0, 0, 2.35]}>
      {/* Pedestal */}
      <mesh position={[0, 0.35, 0]} castShadow>
        <cylinderGeometry args={[0.55, 0.7, 0.7, 8]} />
        <meshStandardMaterial color="#1a221e" metalness={0.45} roughness={0.4} />
      </mesh>
      <mesh position={[0, 0.85, 0]} castShadow>
        <cylinderGeometry args={[0.32, 0.38, 0.45, 12]} />
        <meshStandardMaterial color={palette.scaffoldDark} metalness={0.6} roughness={0.35} />
      </mesh>
      {/* Hazard stripes */}
      <mesh position={[0, 0.55, 0.52]} rotation={[0.2, 0, 0]}>
        <boxGeometry args={[0.9, 0.12, 0.04]} />
        <meshStandardMaterial color={palette.amber} emissive={palette.amber} emissiveIntensity={0.3} />
      </mesh>
      <mesh ref={lamp} position={[0, 1.25, 0]}>
        <sphereGeometry args={[0.22, 24, 24]} />
        <meshStandardMaterial
          color={palette.coral}
          emissive={palette.coral}
          emissiveIntensity={0.7}
        />
      </mesh>
      <mesh ref={ring} position={[0, 1.25, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.42, 0.035, 10, 32]} />
        <meshStandardMaterial color={palette.coral} emissive={palette.coral} emissiveIntensity={0.35} />
      </mesh>
      <Html position={[0, 1.95, 0]} center distanceFactor={5.5} style={{ pointerEvents: 'none' }}>
        <div className="kill-label boss">
          <strong>{safety.killSwitchArmed ? '⚠ HAZARD' : 'SAFE'}</strong>
          <span>{safety.killSwitchLabel}</span>
          <span>
            FLATTEN {flatten.flattenTimeEt} · CUT {flatten.entryCutoffEt}
          </span>
          <span className="orders-lock">UI ORDERS LOCKED</span>
        </div>
      </Html>
    </group>
  )
}
