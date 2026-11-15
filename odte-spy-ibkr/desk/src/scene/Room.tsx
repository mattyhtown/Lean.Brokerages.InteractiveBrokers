import { palette } from './materials'

/** Game-level ops floor — strong silhouette walls, trench lighting, catwalk rails */
export function Room() {
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, 0]} receiveShadow>
        <planeGeometry args={[28, 20]} />
        <meshStandardMaterial color={palette.concrete} roughness={0.95} metalness={0.04} />
      </mesh>

      {/* Floor trench / game lane */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0.2]} receiveShadow>
        <planeGeometry args={[4.2, 10]} />
        <meshStandardMaterial color="#141c18" roughness={0.7} metalness={0.25} emissive="#1a2a22" emissiveIntensity={0.15} />
      </mesh>
      {/* Lane edge lights */}
      {[-2.15, 2.15].map((x) => (
        <mesh key={x} position={[x, 0.05, 0.2]}>
          <boxGeometry args={[0.06, 0.04, 10]} />
          <meshStandardMaterial color={palette.amber} emissive={palette.amber} emissiveIntensity={0.55} />
        </mesh>
      ))}

      {/* Back bulkhead */}
      <mesh position={[0, 3.4, -7]} receiveShadow>
        <boxGeometry args={[28, 7, 0.5]} />
        <meshStandardMaterial color="#101612" roughness={0.9} />
      </mesh>
      {/* Title wall plate */}
      <mesh position={[0, 3.8, -6.7]}>
        <boxGeometry args={[10, 2.2, 0.12]} />
        <meshStandardMaterial color={palette.board} metalness={0.35} roughness={0.5} />
      </mesh>

      {/* Side bulkheads with window slits */}
      <mesh position={[-12, 3.4, 0]}>
        <boxGeometry args={[0.4, 7, 18]} />
        <meshStandardMaterial color="#121814" roughness={0.85} />
      </mesh>
      <mesh position={[12, 3.4, 0]}>
        <boxGeometry args={[0.4, 7, 18]} />
        <meshStandardMaterial color="#121814" roughness={0.85} />
      </mesh>

      {/* Catwalk rails */}
      {[-4.5, 4.5].map((z) => (
        <group key={z}>
          <mesh position={[0, 1.1, z]}>
            <boxGeometry args={[16, 0.05, 0.05]} />
            <meshStandardMaterial color={palette.steelBright} metalness={0.7} roughness={0.3} />
          </mesh>
          {[-6, -2, 2, 6].map((x) => (
            <mesh key={x} position={[x, 0.55, z]}>
              <boxGeometry args={[0.05, 1.1, 0.05]} />
              <meshStandardMaterial color={palette.steel} metalness={0.65} roughness={0.35} />
            </mesh>
          ))}
        </group>
      ))}

      {/* Overhead light bars */}
      {[-3, 0, 3].map((x) => (
        <mesh key={x} position={[x, 5.2, -1]}>
          <boxGeometry args={[2.2, 0.08, 0.35]} />
          <meshStandardMaterial color="#cfd8d0" emissive="#b8c9b8" emissiveIntensity={0.7} />
        </mesh>
      ))}

      {/* Atmosphere shafts */}
      <mesh position={[-2.5, 3.5, 0]} rotation={[0.15, 0.3, 0]}>
        <planeGeometry args={[2.8, 5.5]} />
        <meshBasicMaterial color="#c9b896" transparent opacity={0.035} depthWrite={false} />
      </mesh>
    </group>
  )
}
