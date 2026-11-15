import { useMemo, useRef, type ReactNode } from 'react'
import { useFrame, type ThreeEvent } from '@react-three/fiber'
import { Html, RoundedBox } from '@react-three/drei'
import type { Group, Mesh } from 'three'
import { palette } from './materials'

type Props = {
  id: string
  position: [number, number, number]
  rotation?: [number, number, number]
  width?: number
  height?: number
  title: string
  subtitle?: string
  swayPhase?: number
  children: ReactNode
  accent?: string
  selected?: boolean
  onSelect?: (id: string) => void
}

export function FloatingPanel({
  id,
  position,
  rotation = [0, 0, 0],
  width = 2.4,
  height = 1.6,
  title,
  subtitle,
  swayPhase = 0,
  children,
  accent = palette.amber,
  selected = false,
  onSelect,
}: Props) {
  const group = useRef<Group>(null)
  const rim = useRef<Mesh>(null)
  const baseY = position[1]

  useFrame(({ clock }) => {
    if (!group.current) return
    const t = clock.getElapsedTime()
    const pulse = selected ? 0.1 : 0.05
    group.current.position.y = baseY + Math.sin(t * 0.7 + swayPhase) * pulse
    group.current.rotation.z = Math.sin(t * 0.4 + swayPhase) * 0.012
    if (rim.current) {
      const mat = rim.current.material as { emissiveIntensity?: number }
      if (mat.emissiveIntensity !== undefined) {
        mat.emissiveIntensity = selected
          ? 0.85 + Math.sin(t * 4) * 0.25
          : 0.25 + Math.sin(t * 1.5 + swayPhase) * 0.12
      }
    }
  })

  const frame = useMemo(
    () => (
      <>
        <mesh position={[0, height / 2 + 0.08, 0]}>
          <boxGeometry args={[width + 0.25, 0.06, 0.06]} />
          <meshStandardMaterial color={palette.scaffold} metalness={0.55} roughness={0.35} />
        </mesh>
        <mesh position={[0, -height / 2 - 0.08, 0]}>
          <boxGeometry args={[width + 0.25, 0.06, 0.06]} />
          <meshStandardMaterial color={palette.scaffold} metalness={0.55} roughness={0.35} />
        </mesh>
        <mesh position={[-width / 2 - 0.1, 0, 0]}>
          <boxGeometry args={[0.06, height + 0.25, 0.06]} />
          <meshStandardMaterial color={palette.scaffold} metalness={0.55} roughness={0.35} />
        </mesh>
        <mesh position={[width / 2 + 0.1, 0, 0]}>
          <boxGeometry args={[0.06, height + 0.25, 0.06]} />
          <meshStandardMaterial color={palette.scaffold} metalness={0.55} roughness={0.35} />
        </mesh>
        <mesh position={[-width / 2 + 0.15, height / 2 + 0.55, 0]}>
          <cylinderGeometry args={[0.014, 0.014, 1.0, 8]} />
          <meshStandardMaterial color={palette.steel} metalness={0.85} roughness={0.25} />
        </mesh>
        <mesh position={[width / 2 - 0.15, height / 2 + 0.55, 0]}>
          <cylinderGeometry args={[0.014, 0.014, 1.0, 8]} />
          <meshStandardMaterial color={palette.steel} metalness={0.85} roughness={0.25} />
        </mesh>
      </>
    ),
    [width, height],
  )

  const handleClick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation()
    onSelect?.(id)
  }

  return (
    <group ref={group} position={position} rotation={rotation} onClick={handleClick}>
      {frame}
      <RoundedBox args={[width, height, 0.08]} radius={0.05} smoothness={4}>
        <meshStandardMaterial
          color={palette.board}
          metalness={0.2}
          roughness={0.45}
          transparent
          opacity={0.94}
          emissive={selected ? accent : '#000000'}
          emissiveIntensity={selected ? 0.18 : 0}
        />
      </RoundedBox>
      <mesh ref={rim} position={[0, height / 2 - 0.05, 0.05]}>
        <boxGeometry args={[width * 0.94, 0.045, 0.02]} />
        <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.35} />
      </mesh>
      {selected ? (
        <mesh position={[0, 0, -0.08]}>
          <planeGeometry args={[width + 0.35, height + 0.35]} />
          <meshBasicMaterial color={accent} transparent opacity={0.08} depthWrite={false} />
        </mesh>
      ) : null}
      <Html
        transform
        position={[0, 0, 0.06]}
        distanceFactor={2.8}
        style={{ pointerEvents: 'none', userSelect: 'none' }}
      >
        <div className={`panel-surface ${selected ? 'selected' : ''}`} style={{ width: width * 110, height: height * 110 }}>
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
