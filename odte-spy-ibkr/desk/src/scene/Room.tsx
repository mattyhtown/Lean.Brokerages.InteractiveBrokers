import { palette } from './materials'

export function Room() {
  return (
    <group>
      {/* Floor — worn concrete with subtle grid via roughness contrast */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, 0]} receiveShadow>
        <planeGeometry args={[24, 18]} />
        <meshStandardMaterial color={palette.concrete} roughness={0.92} metalness={0.05} />
      </mesh>

      {/* Floor inlay desk zone */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.01, 0.4]} receiveShadow>
        <planeGeometry args={[10.5, 6.2]} />
        <meshStandardMaterial color={palette.concreteLite} roughness={0.85} metalness={0.08} />
      </mesh>

      {/* Back wall */}
      <mesh position={[0, 3.2, -6.5]} receiveShadow>
        <boxGeometry args={[24, 6.5, 0.35]} />
        <meshStandardMaterial color="#1e2420" roughness={0.95} />
      </mesh>

      {/* Side walls — open framing feel */}
      <mesh position={[-11.5, 3.2, 0]}>
        <boxGeometry args={[0.25, 6.5, 16]} />
        <meshStandardMaterial color="#232926" roughness={0.9} transparent opacity={0.85} />
      </mesh>
      <mesh position={[11.5, 3.2, 0]}>
        <boxGeometry args={[0.25, 6.5, 16]} />
        <meshStandardMaterial color="#232926" roughness={0.9} transparent opacity={0.85} />
      </mesh>

      {/* Wall board rail */}
      <mesh position={[0, 2.6, -6.25]}>
        <boxGeometry args={[14, 2.4, 0.08]} />
        <meshStandardMaterial color={palette.board} roughness={0.7} metalness={0.2} />
      </mesh>

      {/* Ambient volume light shafts (geometry planes) */}
      <mesh position={[-3, 4.2, -1]} rotation={[0.2, 0.4, 0.1]}>
        <planeGeometry args={[2.5, 5]} />
        <meshBasicMaterial color="#c9b896" transparent opacity={0.04} depthWrite={false} />
      </mesh>
      <mesh position={[4, 4.0, 0.5]} rotation={[-0.15, -0.35, 0]}>
        <planeGeometry args={[2.2, 4.5]} />
        <meshBasicMaterial color="#a8c4b0" transparent opacity={0.035} depthWrite={false} />
      </mesh>
    </group>
  )
}
