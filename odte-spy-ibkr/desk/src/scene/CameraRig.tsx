import { OrbitControls } from '@react-three/drei'

/** Orbit / walk-around — constrained so the hero brand stays in first composition */
export function CameraRig() {
  return (
    <OrbitControls
      makeDefault
      enablePan
      enableZoom
      minDistance={4}
      maxDistance={16}
      maxPolarAngle={Math.PI * 0.48}
      minPolarAngle={0.2}
      target={[0, 1.6, 0]}
      enableDamping
      dampingFactor={0.06}
    />
  )
}
