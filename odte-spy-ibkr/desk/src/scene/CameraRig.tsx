import { useEffect, useRef } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import * as THREE from 'three'

const SPEED = 4.2
const PLAYER_EYE = 1.72

type ControlsHandle = {
  target: THREE.Vector3
  update: () => void
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
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [])

  useEffect(() => {
    if (!focus || !controls.current) return
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
    }
    bob.current += dt
    const bobY = Math.sin(bob.current * 1.6) * 0.018
    camera.position.y = THREE.MathUtils.lerp(camera.position.y, PLAYER_EYE + bobY, 0.08)
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
