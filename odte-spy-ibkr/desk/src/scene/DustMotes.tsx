import { useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import type { Points } from 'three'
import * as THREE from 'three'

/** Slow drifting dust — atmospheric motion in the room volume */
export function DustMotes({ count = 120 }: { count?: number }) {
  const points = useRef<Points>(null)
  const positions = useMemo(() => {
    const arr = new Float32Array(count * 3)
    for (let i = 0; i < count; i++) {
      arr[i * 3] = (Math.random() - 0.5) * 16
      arr[i * 3 + 1] = 0.4 + Math.random() * 4.5
      arr[i * 3 + 2] = (Math.random() - 0.5) * 12
    }
    return arr
  }, [count])

  useFrame(({ clock }) => {
    if (!points.current) return
    const t = clock.getElapsedTime() * 0.08
    points.current.rotation.y = t * 0.15
    const geo = points.current.geometry
    const pos = geo.attributes.position as THREE.BufferAttribute
    for (let i = 0; i < count; i++) {
      const y = pos.getY(i) + Math.sin(t + i) * 0.0008
      pos.setY(i, y > 5 ? 0.5 : y)
    }
    pos.needsUpdate = true
  })

  return (
    <points ref={points}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial
        size={0.035}
        color="#cbb892"
        transparent
        opacity={0.35}
        depthWrite={false}
        sizeAttenuation
      />
    </points>
  )
}
