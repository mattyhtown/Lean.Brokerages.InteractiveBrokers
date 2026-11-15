import { useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html } from '@react-three/drei'
import type { Group } from 'three'
import { palette } from './materials'

type Props = {
  brand: string
  symbol: string
}

/**
 * Hero-level brand in the room — local HTML only (no drei Text CDN font).
 * Avoids Suspense hangs that blank the whole canvas when remote fonts fail.
 */
export function HeroBrand({ brand, symbol }: Props) {
  const group = useRef<Group>(null)

  useFrame(({ clock }) => {
    if (!group.current) return
    const t = clock.getElapsedTime()
    group.current.position.y = 3.35 + Math.sin(t * 0.4) * 0.04
  })

  return (
    <group ref={group} position={[0, 3.35, -5.7]}>
      <Html
        center
        transform
        distanceFactor={6.5}
        position={[0, 0, 0.05]}
        style={{ pointerEvents: 'none', userSelect: 'none' }}
      >
        <div className="hero-brand">
          <h1>{brand.toUpperCase()}</h1>
          <p>
            {symbol} · STRICT · ORDERS OFF
          </p>
        </div>
      </Html>
      <mesh position={[0, -0.82, 0]}>
        <boxGeometry args={[5.2, 0.04, 0.04]} />
        <meshStandardMaterial color={palette.scaffold} metalness={0.6} roughness={0.35} />
      </mesh>
      <mesh position={[-2.55, -0.7, 0]}>
        <boxGeometry args={[0.06, 0.28, 0.06]} />
        <meshStandardMaterial color={palette.scaffoldDark} />
      </mesh>
      <mesh position={[2.55, -0.7, 0]}>
        <boxGeometry args={[0.06, 0.28, 0.06]} />
        <meshStandardMaterial color={palette.scaffoldDark} />
      </mesh>
    </group>
  )
}
