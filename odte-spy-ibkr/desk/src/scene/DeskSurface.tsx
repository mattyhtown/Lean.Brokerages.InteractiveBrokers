import { palette } from './materials'

export function DeskSurface() {
  return (
    <group position={[0, 0.78, 0]}>
      {/* Main desk slab */}
      <mesh castShadow receiveShadow>
        <boxGeometry args={[7.2, 0.12, 2.8]} />
        <meshStandardMaterial color="#3d342c" roughness={0.55} metalness={0.12} />
      </mesh>
      {/* Scaffold edge trim */}
      <mesh position={[0, 0.08, 1.42]}>
        <boxGeometry args={[7.4, 0.06, 0.08]} />
        <meshStandardMaterial color={palette.scaffold} metalness={0.5} roughness={0.4} />
      </mesh>
      <mesh position={[0, 0.08, -1.42]}>
        <boxGeometry args={[7.4, 0.06, 0.08]} />
        <meshStandardMaterial color={palette.scaffold} metalness={0.5} roughness={0.4} />
      </mesh>
      {/* Legs / unfinished stilts */}
      {[
        [-3.2, -0.45, 1.1],
        [3.2, -0.45, 1.1],
        [-3.2, -0.45, -1.1],
        [3.2, -0.45, -1.1],
      ].map((p, i) => (
        <mesh key={i} position={p as [number, number, number]} castShadow>
          <cylinderGeometry args={[0.06, 0.08, 0.9, 8]} />
          <meshStandardMaterial color={palette.steel} metalness={0.65} roughness={0.35} />
        </mesh>
      ))}
      {/* Monitor risers — empty frames for depth */}
      <mesh position={[-2.1, 0.55, -0.7]}>
        <boxGeometry args={[1.6, 0.9, 0.05]} />
        <meshStandardMaterial color="#151a17" metalness={0.2} roughness={0.6} />
      </mesh>
      <mesh position={[2.1, 0.55, -0.7]}>
        <boxGeometry args={[1.6, 0.9, 0.05]} />
        <meshStandardMaterial color="#151a17" metalness={0.2} roughness={0.6} />
      </mesh>
    </group>
  )
}
