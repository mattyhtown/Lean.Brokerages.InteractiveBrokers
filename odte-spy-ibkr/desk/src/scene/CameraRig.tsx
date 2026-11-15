import { useEffect, useRef } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import * as THREE from 'three'

const SPEED = 4.2
const PLAYER_EYE = 1.72
const BOUNDS = 9

type ControlsHandle = {
  target: THREE.Vector3
  update: () => void
  minPolarAngle: number
  maxPolarAngle: number
}

/** Player-height orbit + WASD strafe with light idle bob */
export function CameraRig({ focus }: { focus: [number, number, number] | null }) {
  const keys = useRef<Record<string, boolean>>({})
  const controls = useRef<ControlsHandle | null>(null)
  const { camera } = useThree()
  const bob = useRef(0)

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      keys.current[e.code] = true
    }
    const up = (e: KeyboardEvent) => {
      keys.current[e.code] = false
    }
    const clearKeys = () => {
      keys.current = {}
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', clearKeys)
    document.addEventListener('visibilitychange', clearKeys)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', clearKeys)
      document.removeEventListener('visibilitychange', clearKeys)
    }
  }, [])

  useEffect(() => {
    if (!focus || !controls.current) return
    // Aim at eye-level look-at near the station — not panel top height
    controls.current.target.set(focus[0], focus[1], focus[2])
  }, [focus])

  useFrame((_, dt) => {
    const ctrl = controls.current
    if (!ctrl) return
    const forward = new THREE.Vector3()
    camera.getWorldDirection(forward)
    forward.y = 0
    forward.normalize()
    const right = new THREE.Vector3().crossVectors(forward, new THREE.Vector3(0, 1, 0)).normalize()
    const move = new THREE.Vector3()
    if (keys.current.KeyW || keys.current.ArrowUp) move.add(forward)
    if (keys.current.KeyS || keys.current.ArrowDown) move.sub(forward)
    if (keys.current.KeyD || keys.current.ArrowRight) move.add(right)
    if (keys.current.KeyA || keys.current.ArrowLeft) move.sub(right)
    if (move.lengthSq() > 0) {
      move.normalize().multiplyScalar(SPEED * dt)
      camera.position.add(move)
      ctrl.target.add(move)
      camera.position.x = THREE.MathUtils.clamp(camera.position.x, -BOUNDS, BOUNDS)
      camera.position.z = THREE.MathUtils.clamp(camera.position.z, -BOUNDS, BOUNDS)
      ctrl.target.x = THREE.MathUtils.clamp(ctrl.target.x, -BOUNDS, BOUNDS)
      ctrl.target.z = THREE.MathUtils.clamp(ctrl.target.z, -BOUNDS, BOUNDS)
    }
    bob.current += dt
    const bobY = Math.sin(bob.current * 1.6) * 0.018
    // Keep player-height camera; focus targets are eye-level so orbit won't fight the lerp
    camera.position.y = THREE.MathUtils.lerp(camera.position.y, PLAYER_EYE + bobY, 0.08)
    ctrl.target.y = THREE.MathUtils.lerp(ctrl.target.y, focus ? focus[1] : 1.4, 0.12)
    ctrl.update()
  })

  return (
    <OrbitControls
      ref={controls as never}
      makeDefault
      enablePan
      enableZoom
      minDistance={2.5}
      maxDistance={14}
      maxPolarAngle={Math.PI * 0.49}
      minPolarAngle={0.25}
      target={[0, 1.4, 0]}
      enableDamping
      dampingFactor={0.07}
    />
  )
}
