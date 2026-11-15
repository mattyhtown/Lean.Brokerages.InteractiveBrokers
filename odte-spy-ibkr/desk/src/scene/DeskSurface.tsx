import { palette } from './materials'

export function DeskSurface() {
  return (
    <group position={[0, 0.72, -0.2]}>
      <mesh castShadow receiveShadow>
        <boxGeometry args={[6.8, 0.14, 2.6]} />
        <meshStandardMaterial color="#2e2820" roughness={0.5} metalness={0.18} />
      </mesh>
      {/* Glowing edge */}
      <mesh position={[0, 0.09, 1.32]}>
        <boxGeometry args={[6.9, 0.04, 0.06]} />
        <meshStandardMaterial color={palette.amber} emissive={palette.amber} emissiveIntensity={0.45} />
      </mesh>
      {/* Console props */}
      <mesh position={[-1.8, 0.35, -0.55]} castShadow>
        <boxGeometry args={[1.4, 0.7, 0.08]} />
        <meshStandardMaterial color="#0e1411" emissive="#1a2e24" emissiveIntensity={0.25} />
      </mesh>
      <mesh position={[1.8, 0.35, -0.55]} castShadow>
        <boxGeometry args={[1.4, 0.7, 0.08]} />
        <meshStandardMaterial color="#0e1411" emissive="#2a2218" emissiveIntensity={0.2} />
      </mesh>
      {/* Keyboard plate */}
      <mesh position={[0, 0.1, 0.55]}>
        <boxGeometry args={[2.2, 0.04, 0.7]} />
        <meshStandardMaterial color="#1a1f1c" metalness={0.3} roughness={0.5} />
      </mesh>
      {[
        [-2.9, -0.4, 1.0],
        [2.9, -0.4, 1.0],
        [-2.9, -0.4, -1.0],
        [2.9, -0.4, -1.0],
      ].map((p, i) => (
        <mesh key={i} position={p as [number, number, number]} castShadow>
          <cylinderGeometry args={[0.07, 0.09, 0.85, 8]} />
          <meshStandardMaterial color={palette.steel} metalness={0.7} roughness={0.3} />
        </mesh>
      ))}
    </group>
  )
}
