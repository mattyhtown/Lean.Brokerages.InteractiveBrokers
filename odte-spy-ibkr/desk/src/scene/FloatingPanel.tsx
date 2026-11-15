import { useMemo, useRef, type ReactNode } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html, RoundedBox } from '@react-three/drei'
import type { Group } from 'three'
import { palette } from './materials'

type Props = {
  position: [number, number, number]
  rotation?: [number, number, number]
  width?: number
  height?: number
  title: string
  subtitle?: string
  swayPhase?: number
  children: ReactNode
  accent?: string
}

export function FloatingPanel({
  position,
  rotation = [0, 0, 0],
  width = 2.4,
  height = 1.6,
  title,
  subtitle,
  swayPhase = 0,
  children,
  accent = palette.amber,
}: Props) {
  const group = useRef<Group>(null)
  const baseY = position[1]

  useFrame(({ clock }) => {
    if (!group.current) return
    const t = clock.getElapsedTime()
    group.current.position.y = baseY + Math.sin(t * 0.55 + swayPhase) * 0.06
    group.current.rotation.z = Math.sin(t * 0.35 + swayPhase) * 0.012
  })

  const frame = useMemo(
    () => (
      <>
        {/* Scaffold frame around panel */}
        <mesh position={[0, height / 2 + 0.08, 0]}>
          <boxGeometry args={[width + 0.2, 0.05, 0.05]} />
          <meshStandardMaterial color={palette.scaffold} metalness={0.5} roughness={0.4} />
        </mesh>
        <mesh position={[0, -height / 2 - 0.08, 0]}>
          <boxGeometry args={[width + 0.2, 0.05, 0.05]} />
          <meshStandardMaterial color={palette.scaffold} metalness={0.5} roughness={0.4} />
        </mesh>
        <mesh position={[-width / 2 - 0.08, 0, 0]}>
          <boxGeometry args={[0.05, height + 0.2, 0.05]} />
          <meshStandardMaterial color={palette.scaffold} metalness={0.5} roughness={0.4} />
        </mesh>
        <mesh position={[width / 2 + 0.08, 0, 0]}>
          <boxGeometry args={[0.05, height + 0.2, 0.05]} />
          <meshStandardMaterial color={palette.scaffold} metalness={0.5} roughness={0.4} />
        </mesh>
        {/* Suspension cables */}
        <mesh position={[-width / 2 + 0.15, height / 2 + 0.55, 0]}>
          <cylinderGeometry args={[0.012, 0.012, 1.0, 8]} />
          <meshStandardMaterial color={palette.steel} metalness={0.8} roughness={0.3} />
        </mesh>
        <mesh position={[width / 2 - 0.15, height / 2 + 0.55, 0]}>
          <cylinderGeometry args={[0.012, 0.012, 1.0, 8]} />
          <meshStandardMaterial color={palette.steel} metalness={0.8} roughness={0.3} />
        </mesh>
      </>
    ),
    [width, height],
  )

  return (
    <group ref={group} position={position} rotation={rotation}>
      {frame}
      <RoundedBox args={[width, height, 0.06]} radius={0.04} smoothness={4}>
        <meshStandardMaterial
          color={palette.board}
          metalness={0.15}
          roughness={0.55}
          transparent
          opacity={0.92}
        />
      </RoundedBox>
      <mesh position={[0, height / 2 - 0.06, 0.035]}>
        <boxGeometry args={[width * 0.92, 0.03, 0.01]} />
        <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.35} />
      </mesh>
      <Html
        transform
        occlude="blending"
        position={[0, 0, 0.05]}
        distanceFactor={2.8}
        style={{ pointerEvents: 'none', userSelect: 'none' }}
      >
        <div className="panel-surface" style={{ width: width * 110, height: height * 110 }}>
          <header className="panel-head">
            <h2>{title}</h2>
            {subtitle ? <p>{subtitle}</p> : null}
          </header>
          <div className="panel-body">{children}</div>
        </div>
      </Html>
    </group>
  )
}
