"use client"

/**
 * The 2.5D journey world: a side-on, orthographic, layered landscape that the
 * page scrolls through horizontally.
 *
 * Depth is faked deliberately. An orthographic camera has no perspective
 * divide, so sliding the camera would move every layer at the same rate; each
 * depth layer therefore translates itself by `worldX * factor`. The camera
 * never moves — it just looks slightly down at the road so the surface reads
 * as a plane rather than a line.
 *
 * All geometry is procedural (no assets, no external requests), which keeps
 * this compatible with the app's strict self-hosted CSP.
 */

import { useLayoutEffect, useMemo, useRef } from "react"
import * as THREE from "three"
import { Canvas, useFrame, useThree } from "@react-three/fiber"
import { Stop } from "@/lib/client"
import {
  LAYER_FACTOR,
  Scatter,
  VIEW_HALF_W,
  buildRidge,
  layerLength,
  mulberry32,
  scatterTrees,
  worldXForDistance,
} from "./geometry"

const MINT = "#17a56b"

export interface CarState {
  /** Distance along the route, metres. */
  distM: number
  color: string
  /** Lateral offset so a racing pair sits side by side. */
  lane: number
  charging: boolean
}

export interface JourneyWorldRef {
  /** Mutated outside React each frame; the scene reads it in useFrame. */
  distM: number
  cars: CarState[]
}

interface JourneySceneProps {
  totalDistM: number
  stops: Stop[]
  night: boolean
  world: JourneyWorldRef
  /** Called each frame with the on-screen x (0..1 of viewport) per stop. */
  onStopScreenX?: (positions: number[]) => void
}

export default function JourneyScene(props: JourneySceneProps) {
  return (
    <Canvas
      orthographic
      camera={{ position: [0, 11, 40], zoom: 1, near: 0.1, far: 400 }}
      dpr={[1, 1.75]}
      gl={{ antialias: true, preserveDrawingBuffer: true }}
      style={{ position: "absolute", inset: 0 }}
    >
      <color attach="background" args={[props.night ? "#0b1626" : "#dbe9e2"]} />
      <World {...props} />
    </Canvas>
  )
}

/** Featureless verge filling the frame below the road. It carries no detail,
 * so it needs no parallax — the foreground trees supply the motion cue. */
function Ground({ night }: { night: boolean }) {
  return (
    <mesh position={[0, -0.55, -20]} rotation={[-Math.PI / 2, 0, 0]}>
      <planeGeometry args={[2400, 300]} />
      <meshStandardMaterial color={night ? "#17293b" : "#b6ccbb"} roughness={1} />
    </mesh>
  )
}

function StarField() {
  const geo = useMemo(() => {
    const rand = mulberry32(2027)
    const n = 420
    const pos = new Float32Array(n * 3)
    for (let i = 0; i < n; i++) {
      pos[i * 3] = (rand() - 0.5) * 240
      pos[i * 3 + 1] = 12 + rand() * 60
      pos[i * 3 + 2] = -160
    }
    const g = new THREE.BufferGeometry()
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3))
    return g
  }, [])
  return (
    <points geometry={geo}>
      <pointsMaterial color="#dce9f5" size={1.6} sizeAttenuation={false} transparent opacity={0.75} />
    </points>
  )
}

function World({ totalDistM, stops, night, world, onStopScreenX }: JourneySceneProps) {
  const { camera, size } = useThree()

  // Frame the world: a fixed number of world units across, whatever the pixel
  // width, so the scene reads the same on a laptop and an ultrawide.
  //
  // Drive this with `zoom`, not the frustum bounds: R3F derives an orthographic
  // camera's left/right/top/bottom from the canvas size in PIXELS and rewrites
  // them on every resize, so anything set there gets clobbered and the world
  // ends up a speck. Zoom is left alone, and w/zoom is the visible world width.
  useLayoutEffect(() => {
    const cam = camera as THREE.OrthographicCamera
    cam.zoom = size.width / (VIEW_HALF_W * 2)
    cam.position.set(0, 12, 60)
    cam.lookAt(0, 5.5, 0)
    cam.updateProjectionMatrix()
  }, [camera, size])

  const far = useRef<THREE.Group>(null)
  const mid = useRef<THREE.Group>(null)
  const trees = useRef<THREE.Group>(null)
  const road = useRef<THREE.Group>(null)
  const fore = useRef<THREE.Group>(null)

  useFrame(() => {
    const wx = worldXForDistance(world.distM, totalDistM)
    if (far.current) far.current.position.x = -wx * LAYER_FACTOR.farRidge
    if (mid.current) mid.current.position.x = -wx * LAYER_FACTOR.midRidge
    if (trees.current) trees.current.position.x = -wx * LAYER_FACTOR.treeline
    if (road.current) road.current.position.x = -wx * LAYER_FACTOR.road
    if (fore.current) fore.current.position.x = -wx * LAYER_FACTOR.foreground

    if (onStopScreenX) {
      // Stops are on the road layer, so their screen position follows it.
      onStopScreenX(
        stops.map((s) => {
          const sx = worldXForDistance(s.offset_m, totalDistM) - wx
          return 0.5 + sx / (VIEW_HALF_W * 2)
        })
      )
    }
  })

  return (
    <group>
      <Lights night={night} />
      {night && <StarField />}
      <Ground night={night} />
      <group ref={far} position={[0, 0, -70]}>
        <Ridge
          seed={11}
          factor={LAYER_FACTOR.farRidge}
          baseHeight={9}
          amplitude={12}
          minStep={16}
          maxStep={30}
          depth={5}
          snowFrom={14}
          color={night ? "#3d5878" : "#a9bfc9"}
          capColor={night ? "#93aac9" : "#f2f8fa"}
        />
      </group>
      <group ref={mid} position={[0, 0, -44]}>
        <Ridge
          seed={29}
          factor={LAYER_FACTOR.midRidge}
          baseHeight={4.5}
          amplitude={8}
          minStep={11}
          maxStep={21}
          depth={5}
          snowFrom={9}
          color={night ? "#2c4763" : "#8fae9d"}
          capColor={night ? "#7f97b6" : "#eef6f7"}
        />
      </group>
      <group ref={trees} position={[0, 0, -20]}>
        <Treeline
          seed={41}
          factor={LAYER_FACTOR.treeline}
          count={520}
          zFrom={-10}
          zTo={4}
          night={night}
        />
      </group>
      <group ref={road}>
        <Road night={night} />
        {stops.map((s) => (
          <Station
            key={s.charger_id}
            x={worldXForDistance(s.offset_m, totalDistM)}
            night={night}
          />
        ))}
      </group>
      <group ref={fore} position={[0, -3.4, 20]}>
        <Treeline
          seed={83}
          factor={LAYER_FACTOR.foreground}
          count={70}
          zFrom={0}
          zTo={7}
          scale={1.9}
          night={night}
          dark
        />
      </group>
      <Cars world={world} totalDistM={totalDistM} night={night} />
    </group>
  )
}

function Lights({ night }: { night: boolean }) {
  // Light colours stay near-white and the materials carry the palette. Tinting
  // BOTH dark (a dark blue lamp on dark blue rock) multiplies out to black.
  if (night) {
    return (
      <>
        <ambientLight color="#c8d6f0" intensity={1.15} />
        <directionalLight position={[-40, 55, 70]} color="#e8f0ff" intensity={2.1} />
        <hemisphereLight args={["#b9cdf2", "#26303f", 0.7]} />
      </>
    )
  }
  return (
    <>
      <ambientLight color="#ffffff" intensity={1.2} />
      <directionalLight position={[-30, 60, 70]} color="#fff8ec" intensity={2.4} />
      <hemisphereLight args={["#dceaf6", "#cfd8cf", 0.8]} />
    </>
  )
}

// ---------------------------------------------------------------------------

function Ridge({
  color,
  capColor,
  ...opts
}: Parameters<typeof buildRidge>[0] & { color: string; capColor: string }) {
  const { body, caps } = useMemo(() => buildRidge(opts), [opts])
  return (
    <group>
      <mesh geometry={body}>
        <meshStandardMaterial color={color} flatShading roughness={1} />
      </mesh>
      {caps && (
        <mesh geometry={caps} position={[0, 0, 0.02]}>
          <meshStandardMaterial color={capColor} flatShading roughness={0.95} />
        </mesh>
      )}
    </group>
  )
}

function Treeline({
  seed,
  factor,
  count,
  zFrom,
  zTo,
  scale,
  night,
  dark,
}: {
  seed: number
  factor: number
  count: number
  zFrom: number
  zTo: number
  scale?: number
  night: boolean
  dark?: boolean
}) {
  const items = useMemo(
    () => scatterTrees({ seed, factor, count, zFrom, zTo, scale }),
    [seed, factor, count, zFrom, zTo, scale]
  )
  const canopy = useRef<THREE.InstancedMesh>(null)
  const trunk = useRef<THREE.InstancedMesh>(null)

  useLayoutEffect(() => {
    const c = canopy.current
    const t = trunk.current
    if (!c || !t) return
    const m = new THREE.Matrix4()
    const q = new THREE.Quaternion()
    const green = new THREE.Color(dark ? "#16293c" : night ? "#245a5c" : "#77bf95")
    const snowy = new THREE.Color(dark ? "#24435c" : night ? "#7e9aa6" : "#e8f2ee")
    const tmp = new THREE.Color()
    items.forEach((it: Scatter, i) => {
      const s = it.scale
      m.compose(new THREE.Vector3(it.x, it.y + 1.35 * s, it.z), q, new THREE.Vector3(s, s, s))
      c.setMatrixAt(i, m)
      m.compose(new THREE.Vector3(it.x, it.y + 0.4 * s, it.z), q, new THREE.Vector3(s, s, s))
      t.setMatrixAt(i, m)
      tmp.lerpColors(green, snowy, it.snow * 0.85)
      c.setColorAt(i, tmp)
    })
    c.count = t.count = items.length
    c.instanceMatrix.needsUpdate = true
    t.instanceMatrix.needsUpdate = true
    if (c.instanceColor) c.instanceColor.needsUpdate = true
  }, [items, night, dark])

  return (
    <group>
      <instancedMesh ref={trunk} args={[undefined, undefined, Math.max(count, 1)]}>
        <cylinderGeometry args={[0.09, 0.13, 0.9, 5]} />
        <meshStandardMaterial color={dark ? "#0a1a28" : "#7d6249"} flatShading roughness={1} />
      </instancedMesh>
      <instancedMesh ref={canopy} args={[undefined, undefined, Math.max(count, 1)]}>
        <coneGeometry args={[0.72, 2.5, 6]} />
        <meshStandardMaterial flatShading roughness={1} />
      </instancedMesh>
    </group>
  )
}

function Road({ night }: { night: boolean }) {
  const len = layerLength(LAYER_FACTOR.road)
  const dashes = useMemo(() => {
    const out: number[] = []
    for (let x = -VIEW_HALF_W * 2; x < len; x += 6) out.push(x)
    return out
  }, [len])
  return (
    <group>
      <mesh position={[len / 2 - VIEW_HALF_W, -0.35, 1]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[len + VIEW_HALF_W * 4, 13]} />
        <meshStandardMaterial color={night ? "#2b3f57" : "#c3cfc9"} roughness={1} />
      </mesh>
      <mesh position={[len / 2 - VIEW_HALF_W, -0.3, 1]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[len + VIEW_HALF_W * 4, 8.4]} />
        <meshStandardMaterial color={night ? "#465b76" : "#8d9aa6"} roughness={0.95} />
      </mesh>
      {dashes.map((x) => (
        <mesh key={x} position={[x, -0.26, 1]} rotation={[-Math.PI / 2, 0, 0]}>
          <planeGeometry args={[2.6, 0.28]} />
          <meshStandardMaterial color="#e4edf1" opacity={0.65} transparent roughness={1} />
        </mesh>
      ))}
    </group>
  )
}

function Station({ x, night }: { x: number; night: boolean }) {
  return (
    <group position={[x, 0, -4.6]}>
      <mesh position={[0, 2.9, 0]}>
        <boxGeometry args={[6.2, 0.36, 3.4]} />
        <meshStandardMaterial color={MINT} flatShading roughness={0.7} />
      </mesh>
      {[-2.4, 2.4].map((dx) => (
        <mesh key={dx} position={[dx, 1.45, 0]}>
          <cylinderGeometry args={[0.16, 0.16, 2.9, 6]} />
          <meshStandardMaterial color={night ? "#8d9dad" : "#e6ecef"} roughness={0.8} />
        </mesh>
      ))}
      <mesh position={[0, 0.85, -0.9]}>
        <boxGeometry args={[0.9, 1.7, 0.5]} />
        <meshStandardMaterial color="#f2f6f8" flatShading roughness={0.7} />
      </mesh>
      <mesh position={[0, 1.25, -0.63]}>
        <boxGeometry args={[0.42, 0.3, 0.06]} />
        <meshStandardMaterial color={MINT} emissive={MINT} emissiveIntensity={night ? 1.6 : 0.3} />
      </mesh>
      {night && <pointLight position={[0, 3.6, 0]} color="#ffe6b8" intensity={26} distance={16} />}
    </group>
  )
}

function Cars({
  world,
  totalDistM,
  night,
}: {
  world: JourneyWorldRef
  totalDistM: number
  night: boolean
}) {
  return (
    <group>
      {world.cars.map((_, i) => (
        <Car key={i} index={i} world={world} totalDistM={totalDistM} night={night} />
      ))}
    </group>
  )
}

function Car({
  index,
  world,
  totalDistM,
  night,
}: {
  index: number
  world: JourneyWorldRef
  totalDistM: number
  night: boolean
}) {
  const g = useRef<THREE.Group>(null)
  const bob = useRef(0)
  const car = world.cars[index]

  useFrame((_, dt) => {
    const node = g.current
    if (!node) return
    const c = world.cars[index]
    if (!c) return
    // The lead car holds the centre of the screen; others sit at their real
    // offset from it, so falling behind is visible as distance on the road.
    const dx =
      worldXForDistance(c.distM, totalDistM) - worldXForDistance(world.distM, totalDistM)
    node.position.x = dx
    node.position.z = c.lane
    bob.current += dt * (c.charging ? 0 : 9)
    node.position.y = c.charging ? 0 : Math.sin(bob.current) * 0.035
  })

  const color = car?.color ?? MINT

  return (
    <group ref={g} position={[0, 0, car?.lane ?? 0]}>
      <mesh position={[0, 0.85, 0]}>
        <boxGeometry args={[5.4, 0.95, 2.5]} />
        <meshStandardMaterial color={color} flatShading roughness={0.45} />
      </mesh>
      <mesh position={[-0.15, 1.62, 0]}>
        <boxGeometry args={[3.1, 0.85, 2.15]} />
        <meshStandardMaterial color="#0e1a2b" flatShading roughness={0.3} />
      </mesh>
      {[-1.7, 1.7].map((dx) => (
        <mesh key={dx} position={[dx, 0.42, 1.15]} rotation={[Math.PI / 2, 0, 0]}>
          <cylinderGeometry args={[0.44, 0.44, 0.3, 12]} />
          <meshStandardMaterial color="#141d2b" roughness={0.9} />
        </mesh>
      ))}
      {[-1.7, 1.7].map((dx) => (
        <mesh key={"l" + dx} position={[dx, 0.42, -1.15]} rotation={[Math.PI / 2, 0, 0]}>
          <cylinderGeometry args={[0.44, 0.44, 0.3, 12]} />
          <meshStandardMaterial color="#141d2b" roughness={0.9} />
        </mesh>
      ))}
      <mesh position={[2.75, 0.95, 0]}>
        <boxGeometry args={[0.18, 0.4, 1.9]} />
        <meshStandardMaterial
          color="#fff3d6"
          emissive="#fff3d6"
          emissiveIntensity={night ? 2.4 : 0.4}
        />
      </mesh>
    </group>
  )
}

export { mulberry32 }
