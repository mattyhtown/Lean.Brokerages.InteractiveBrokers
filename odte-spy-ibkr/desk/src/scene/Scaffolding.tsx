import { useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import type { Group, Mesh, MeshStandardMaterial } from 'three'
import { palette } from './materials'

function Beam({
  position,
  args,
  rotation = [0, 0, 0],
}: {
  position: [number, number, number]
  args: [number, number, number]
  rotation?: [number, number, number]
}) {
  return (
    <mesh position={position} rotation={rotation} castShadow receiveShadow>
      <boxGeometry args={args} />
      <meshStandardMaterial color={palette.scaffold} metalness={0.55} roughness={0.45} />
    </mesh>
  )
}

function Coupler({ position }: { position: [number, number, number] }) {
  return (
    <mesh position={position}>
      <boxGeometry args={[0.18, 0.18, 0.18]} />
      <meshStandardMaterial color={palette.scaffoldDark} metalness={0.7} roughness={0.35} />
    </mesh>
  )
}

/** Visible structural frames — unfinished construction aesthetic */
export function Scaffolding() {
  const pulse = useRef<Group>(null)

  useFrame(({ clock }) => {
    if (!pulse.current) return
    const t = clock.getElapsedTime()
    const glow = 0.35 + Math.sin(t * 1.4) * 0.15
    pulse.current.children.forEach((child, i) => {
      const mat = (child as Mesh).material as MeshStandardMaterial
      if (mat?.emissiveIntensity !== undefined) {
        mat.emissiveIntensity = glow + (i % 3) * 0.05
      }
    })
  })

  return (
    <group>
      <Beam position={[-6.2, 2.2, -2]} args={[0.12, 4.4, 0.12]} />
      <Beam position={[-4.4, 2.2, -2]} args={[0.12, 4.4, 0.12]} />
      <Beam position={[-6.2, 2.2, 1.5]} args={[0.12, 4.4, 0.12]} />
      <Beam position={[-4.4, 2.2, 1.5]} args={[0.12, 4.4, 0.12]} />
      <Beam position={[-5.3, 3.8, -0.25]} args={[2.0, 0.1, 0.1]} />
      <Beam position={[-5.3, 2.4, -0.25]} args={[2.0, 0.1, 0.1]} />
      <Beam position={[-5.3, 1.0, -0.25]} args={[2.0, 0.1, 0.1]} />
      <Beam position={[-6.2, 3.2, -0.25]} args={[0.1, 0.1, 3.6]} />
      <Beam position={[-4.4, 3.2, -0.25]} args={[0.1, 0.1, 3.6]} />
      <Beam
        position={[-5.3, 2.8, -2]}
        args={[2.2, 0.08, 0.08]}
        rotation={[0, 0, Math.PI / 8]}
      />
      <Coupler position={[-6.2, 3.8, -2]} />
      <Coupler position={[-4.4, 3.8, 1.5]} />

      <Beam position={[5.8, 2.0, -3]} args={[0.12, 4.0, 0.12]} />
      <Beam position={[7.4, 2.0, -3]} args={[0.12, 4.0, 0.12]} />
      <Beam position={[5.8, 2.0, 0.5]} args={[0.12, 4.0, 0.12]} />
      <Beam position={[7.4, 2.0, 0.5]} args={[0.12, 4.0, 0.12]} />
      <Beam position={[6.6, 3.5, -1.25]} args={[1.8, 0.1, 0.1]} />
      <Beam position={[6.6, 2.0, -1.25]} args={[1.8, 0.1, 0.1]} />
      <Beam position={[5.8, 3.0, -1.25]} args={[0.1, 0.1, 3.6]} />
      <Beam position={[7.4, 3.0, -1.25]} args={[0.1, 0.1, 3.6]} />

      <Beam position={[0.4, 4.55, -1.2]} args={[13.5, 0.14, 0.14]} />
      <Beam position={[-2.2, 4.55, 1.8]} args={[8.5, 0.1, 0.1]} />
      <Beam position={[2.8, 4.2, -0.2]} args={[0.1, 0.9, 0.1]} />
      <Beam position={[-1.5, 4.2, 0.6]} args={[0.1, 0.9, 0.1]} />

      <group ref={pulse}>
        <mesh position={[-5.3, 4.55, -1.2]}>
          <sphereGeometry args={[0.08, 12, 12]} />
          <meshStandardMaterial
            color={palette.amber}
            emissive={palette.amber}
            emissiveIntensity={0.4}
          />
        </mesh>
        <mesh position={[6.6, 4.55, -1.2]}>
          <sphereGeometry args={[0.08, 12, 12]} />
          <meshStandardMaterial
            color={palette.amber}
            emissive={palette.amber}
            emissiveIntensity={0.4}
          />
        </mesh>
        <mesh position={[0.4, 4.55, 1.8]}>
          <sphereGeometry args={[0.07, 12, 12]} />
          <meshStandardMaterial
            color={palette.copper}
            emissive={palette.copper}
            emissiveIntensity={0.35}
          />
        </mesh>
      </group>
    </group>
  )
}
