import { useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html } from '@react-three/drei'
import type { Group } from 'three'
import { palette } from './materials'

type Props = {
  brand: string
  symbol: string
}

/** In-world title card on the bulkhead — game splash, not a webpage eyebrow */
export function HeroBrand({ brand, symbol }: Props) {
  const group = useRef<Group>(null)

  useFrame(({ clock }) => {
    if (!group.current) return
    group.current.position.y = 3.55 + Math.sin(clock.getElapsedTime() * 0.5) * 0.03
  })

  return (
    <group ref={group} position={[0, 3.55, -6.55]}>
      <mesh position={[0, 0, -0.02]}>
        <boxGeometry args={[8.5, 1.8, 0.08]} />
        <meshStandardMaterial color={palette.ink} metalness={0.4} roughness={0.45} />
      </mesh>
      <mesh position={[0, 0.78, 0.02]}>
        <boxGeometry args={[8.5, 0.06, 0.04]} />
        <meshStandardMaterial color={palette.amber} emissive={palette.amber} emissiveIntensity={0.5} />
      </mesh>
      <mesh position={[0, -0.78, 0.02]}>
        <boxGeometry args={[8.5, 0.06, 0.04]} />
        <meshStandardMaterial color={palette.scaffold} metalness={0.55} roughness={0.35} />
      </mesh>
      <Html
        center
        transform
        distanceFactor={7.2}
        position={[0, 0, 0.08]}
        style={{ pointerEvents: 'none', userSelect: 'none' }}
      >
        <div className="title-card">
          <p className="title-card-eyebrow">MISSION CONTROL</p>
          <h1>{brand.toUpperCase()}</h1>
          <p className="title-card-sub">
            {symbol} · STRICT OPS FLOOR · ORDERS LOCKED
          </p>
        </div>
      </Html>
    </group>
  )
}
