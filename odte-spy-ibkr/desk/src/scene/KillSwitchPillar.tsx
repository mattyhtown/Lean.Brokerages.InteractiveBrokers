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

export function KillSwitchPillar({ safety, flatten }: Props) {
  const lamp = useRef<Mesh>(null)

  useFrame(({ clock }) => {
    if (!lamp.current) return
    const pulse = 0.45 + Math.sin(clock.getElapsedTime() * 3.2) * 0.35
    const mat = lamp.current.material as MeshStandardMaterial
    mat.emissiveIntensity = safety.killSwitchArmed ? pulse : 0.15
  })

  return (
    <group position={[0, 0, 2.6]}>
      <mesh position={[0, 0.55, 0]} castShadow>
        <cylinderGeometry args={[0.35, 0.42, 1.1, 16]} />
        <meshStandardMaterial color="#2c3330" metalness={0.4} roughness={0.5} />
      </mesh>
      <mesh position={[0, 1.2, 0]} castShadow>
        <cylinderGeometry args={[0.28, 0.32, 0.25, 16]} />
        <meshStandardMaterial color={palette.scaffoldDark} metalness={0.55} roughness={0.4} />
      </mesh>
      <mesh ref={lamp} position={[0, 1.45, 0]}>
        <sphereGeometry args={[0.16, 20, 20]} />
        <meshStandardMaterial
          color={palette.coral}
          emissive={palette.coral}
          emissiveIntensity={0.6}
        />
      </mesh>
      <mesh position={[0, 0.9, 0]}>
        <torusGeometry args={[0.48, 0.03, 8, 24]} />
        <meshStandardMaterial color={palette.scaffold} metalness={0.5} roughness={0.4} />
      </mesh>
      <Html position={[0, 2.05, 0]} center distanceFactor={6} style={{ pointerEvents: 'none' }}>
        <div className="kill-label">
          <strong>{safety.killSwitchArmed ? 'KILL ARMED' : 'KILL SAFE'}</strong>
          <span>{safety.killSwitchLabel}</span>
          <span>
            Flatten {flatten.flattenTimeEt} ET · cutoff {flatten.entryCutoffEt}
          </span>
          <span className="orders-lock">ORDERS OFF · ENABLE_ORDERS locked</span>
        </div>
      </Html>
    </group>
  )
}
