import { useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Text, Center } from '@react-three/drei'
import type { Group } from 'three'
import { palette } from './materials'

type Props = {
  brand: string
  symbol: string
}

/** Hero-level brand in the room — dominates first viewport composition */
export function HeroBrand({ brand, symbol }: Props) {
  const group = useRef<Group>(null)

  useFrame(({ clock }) => {
    if (!group.current) return
    const t = clock.getElapsedTime()
    group.current.position.y = 3.35 + Math.sin(t * 0.4) * 0.04
  })

  return (
    <group ref={group} position={[0, 3.35, -5.7]}>
      <Center>
        <Text
          fontSize={0.72}
          letterSpacing={0.08}
          color={palette.panelGlass}
          anchorX="center"
          anchorY="middle"
          outlineWidth={0.012}
          outlineColor="#0c100e"
          maxWidth={10}
        >
          {brand.toUpperCase()}
        </Text>
      </Center>
      <Text
        position={[0, -0.55, 0.05]}
        fontSize={0.22}
        letterSpacing={0.18}
        color={palette.amber}
        anchorX="center"
        anchorY="middle"
      >
        {`${symbol} · STRICT · ORDERS OFF`}
      </Text>
      {/* Scaffold underline */}
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
