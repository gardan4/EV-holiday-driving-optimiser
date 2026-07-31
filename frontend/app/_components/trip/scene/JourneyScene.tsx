"use client"

/**
 * The low-poly 3D journey diorama (Cula-inspired): the real route as a ribbon
 * road across a procedural landscape that morphs from green flatland at the
 * origin into snowy Alps at the destination. Charging stops sit at their true
 * route offsets with floating pill labels synced to the itinerary. All
 * geometry is procedural — no assets, no external requests (strict CSP safe).
 */

import { useLayoutEffect, useMemo, useRef, useState } from "react"
import * as THREE from "three"
import { Canvas, useFrame } from "@react-three/fiber"
import { Html, OrbitControls, Stars } from "@react-three/drei"
import { Moon, Sun } from "lucide-react"
import { Stop } from "@/lib/client"
import { decodePolyline } from "@/lib/polyline"
import {
  buildRoadGeometry,
  buildSceneRoute,
  mulberry32,
  SceneRoute,
  terrainHeight,
} from "./geometry"

const BRAND = "#17a56b"

interface JourneySceneProps {
  polyline: string
  totalDistM: number
  stops: Stop[]
  originLabel: string
  destLabel: string
  highlightStop?: string | null
  onHoverStop?: (chargerId: string | null) => void
  /** 0..1 fraction of route driven (M7 playback); car sits at origin when 0. */
  carProgress?: number
}

export default function JourneyScene(props: JourneySceneProps) {
  const [night, setNight] = useState(true)
  const [webgl] = useState(() => {
    if (typeof window === "undefined") return true
    try {
      const c = document.createElement("canvas")
      return !!(c.getContext("webgl2") || c.getContext("webgl"))
    } catch {
      return false
    }
  })

  if (!webgl) {
    return (
      <div className="flex h-64 items-center justify-center rounded-2xl border border-ink-100 bg-white text-sm text-ink-400">
        3D view unavailable (WebGL is disabled) — the chart and itinerary have everything.
      </div>
    )
  }

  return (
    <div className="relative h-[430px] overflow-hidden rounded-2xl border border-ink-100 shadow-sm sm:h-[480px]">
      <Canvas
        camera={{ position: [4, 58, 86], fov: 40 }}
        dpr={[1, 1.75]}
        gl={{ antialias: true, preserveDrawingBuffer: true }}
      >
        <color attach="background" args={[night ? "#0b1626" : "#dcebe3"]} />
        <fog attach="fog" args={[night ? "#0b1626" : "#dcebe3", 120, 260]} />
        <SceneContent {...props} night={night} />
      </Canvas>
      <button
        onClick={() => setNight((n) => !n)}
        className="absolute right-3 top-3 flex h-9 w-9 items-center justify-center rounded-xl border border-white/20 bg-white/85 text-ink-700 shadow-sm backdrop-blur transition-colors hover:bg-white"
        title={night ? "Switch to day" : "Switch to night"}
        aria-label={night ? "Switch to day view" : "Switch to night view"}
      >
        {night ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </button>
      <div className="pointer-events-none absolute bottom-3 left-3 rounded-lg bg-black/25 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-white/80 backdrop-blur-sm">
        drag to orbit · scroll to zoom
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------

function SceneContent({
  polyline,
  totalDistM,
  stops,
  originLabel,
  destLabel,
  highlightStop,
  onHoverStop,
  carProgress = 0,
  night,
}: JourneySceneProps & { night: boolean }) {
  const route = useMemo(() => buildSceneRoute(decodePolyline(polyline)), [polyline])

  return (
    <group>
      <Lights night={night} />
      {night && <Stars radius={220} depth={40} count={1500} factor={3} saturation={0} fade />}
      <Terrain route={route} night={night} />
      <Road route={route} night={night} />
      <Trees route={route} />
      <Mountains route={route} night={night} />
      <Houses route={route} />
      <EndpointMarker route={route} u={0} label={originLabel} kind="origin" />
      <EndpointMarker route={route} u={1} label={destLabel} kind="dest" />
      {stops.map((s) => (
        <Station
          key={s.charger_id}
          route={route}
          stop={s}
          u={Math.min(s.offset_m / totalDistM, 0.995)}
          night={night}
          highlighted={highlightStop === s.charger_id}
          onHover={onHoverStop}
        />
      ))}
      <Car route={route} u={carProgress} night={night} />
      <OrbitControls
        target={[0, 0, 0]}
        minDistance={25}
        maxDistance={170}
        minPolarAngle={0.15}
        maxPolarAngle={1.32}
        enableDamping
        dampingFactor={0.08}
      />
    </group>
  )
}

function Lights({ night }: { night: boolean }) {
  if (night) {
    return (
      <>
        <ambientLight color="#39496b" intensity={1.1} />
        <directionalLight position={[-40, 60, -30]} color="#8fa8d8" intensity={0.9} />
        <hemisphereLight args={["#1d2c4a", "#0a121f", 0.6]} />
      </>
    )
  }
  return (
    <>
      <ambientLight color="#ffffff" intensity={0.75} />
      <directionalLight position={[-30, 70, 20]} color="#fff7e8" intensity={1.4} />
      <hemisphereLight args={["#cfe4f5", "#e8f0e9", 0.5]} />
    </>
  )
}

// ---------------------------------------------------------------------------
// Landscape
// ---------------------------------------------------------------------------

const GROUND_MARGIN = 26

function groundBounds(route: SceneRoute) {
  return {
    minX: route.bounds.minX - GROUND_MARGIN,
    maxX: route.bounds.maxX + GROUND_MARGIN,
    minZ: route.bounds.minZ - GROUND_MARGIN,
    maxZ: route.bounds.maxZ + GROUND_MARGIN,
  }
}

function Terrain({ route, night }: { route: SceneRoute; night: boolean }) {
  const geometry = useMemo(() => {
    const b = groundBounds(route)
    const w = b.maxX - b.minX
    const d = b.maxZ - b.minZ
    const segX = 110
    const segZ = 70
    const geo = new THREE.PlaneGeometry(w, d, segX, segZ)
    geo.rotateX(-Math.PI / 2)
    geo.translate(b.minX + w / 2, 0, b.minZ + d / 2)

    const pos = geo.getAttribute("position") as THREE.BufferAttribute
    const colors = new Float32Array(pos.count * 3)
    const grass = new THREE.Color("#b5d9c0")
    const grassDark = new THREE.Color("#93c4a4")
    const snow = new THREE.Color("#f2f7f9")
    const tmp = new THREE.Color()
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i)
      const z = pos.getZ(i)
      const progress = route.progressAt(x, z)
      const rd = route.roadDistance(x, z)
      const h = terrainHeight(x, z, progress, rd)
      pos.setY(i, h)
      // Snowier toward the destination and with altitude.
      const snowiness = Math.min(1, Math.pow(progress, 2.2) * 1.15 + h * 0.12)
      const patch = (Math.sin(x * 0.9 + z * 1.3) + 1) / 2
      tmp.lerpColors(patch > 0.5 ? grass : grassDark, snow, snowiness)
      colors[i * 3] = tmp.r
      colors[i * 3 + 1] = tmp.g
      colors[i * 3 + 2] = tmp.b
    }
    geo.setAttribute("color", new THREE.BufferAttribute(colors, 3))
    geo.computeVertexNormals()
    return geo
  }, [route])

  return (
    <mesh geometry={geometry} receiveShadow>
      <meshStandardMaterial vertexColors flatShading roughness={1} />
    </mesh>
  )
}

function Road({ route, night }: { route: SceneRoute; night: boolean }) {
  const geometry = useMemo(() => buildRoadGeometry(route.curve), [route])
  const edges = useMemo(() => buildRoadGeometry(route.curve, 2.1, 0.045), [route])
  return (
    <group>
      <mesh geometry={edges}>
        <meshStandardMaterial color={night ? "#233046" : "#cfd9d3"} roughness={1} />
      </mesh>
      <mesh geometry={geometry}>
        <meshStandardMaterial color={night ? "#39455c" : "#8b97a3"} roughness={0.9} />
      </mesh>
    </group>
  )
}

function Trees({ route }: { route: SceneRoute }) {
  const COUNT = 340
  const trunkRef = useRef<THREE.InstancedMesh>(null)
  const canopyRef = useRef<THREE.InstancedMesh>(null)

  const trees = useMemo(() => {
    const rng = mulberry32(1337)
    const b = groundBounds(route)
    const out: { x: number; z: number; y: number; s: number; snow: number }[] = []
    let guard = 0
    while (out.length < COUNT && guard++ < COUNT * 12) {
      const x = b.minX + rng() * (b.maxX - b.minX)
      const z = b.minZ + rng() * (b.maxZ - b.minZ)
      const rd = route.roadDistance(x, z)
      if (rd < 3.2) continue
      const progress = route.progressAt(x, z)
      const y = terrainHeight(x, z, progress, rd)
      const density = 0.35 + 0.65 * (1 - Math.pow(progress, 1.8)) // sparser in the high Alps
      if (rng() > density) continue
      out.push({ x, z, y, s: 0.55 + rng() * 0.85, snow: Math.pow(progress, 2.0) })
    }
    return out
  }, [route])

  useLayoutEffect(() => {
    const trunk = trunkRef.current
    const canopy = canopyRef.current
    if (!trunk || !canopy) return
    const m = new THREE.Matrix4()
    const q = new THREE.Quaternion()
    const mint = new THREE.Color("#8fd3ac")
    const mintDark = new THREE.Color("#5cb383")
    const snowy = new THREE.Color("#e7f2ec")
    const tmp = new THREE.Color()
    trees.forEach((t, i) => {
      m.compose(new THREE.Vector3(t.x, t.y + 0.5 * t.s, t.z), q, new THREE.Vector3(t.s, t.s, t.s))
      trunk.setMatrixAt(i, m)
      m.compose(new THREE.Vector3(t.x, t.y + 1.55 * t.s, t.z), q, new THREE.Vector3(t.s, t.s, t.s))
      canopy.setMatrixAt(i, m)
      tmp.lerpColors(i % 2 ? mint : mintDark, snowy, t.snow)
      canopy.setColorAt(i, tmp)
    })
    trunk.count = canopy.count = trees.length
    trunk.instanceMatrix.needsUpdate = true
    canopy.instanceMatrix.needsUpdate = true
    if (canopy.instanceColor) canopy.instanceColor.needsUpdate = true
  }, [trees])

  return (
    <group>
      <instancedMesh ref={trunkRef} args={[undefined, undefined, COUNT]}>
        <cylinderGeometry args={[0.09, 0.13, 1, 5]} />
        <meshStandardMaterial color="#8a6f55" flatShading roughness={1} />
      </instancedMesh>
      <instancedMesh ref={canopyRef} args={[undefined, undefined, COUNT]}>
        <coneGeometry args={[0.7, 2.1, 6]} />
        <meshStandardMaterial flatShading roughness={1} />
      </instancedMesh>
    </group>
  )
}

function Mountains({ route, night }: { route: SceneRoute; night: boolean }) {
  const peaks = useMemo(() => {
    const rng = mulberry32(4242)
    const b = groundBounds(route)
    const out: { x: number; z: number; h: number; r: number }[] = []
    // A back range along the far side, growing toward the destination.
    for (let i = 0; i < 14; i++) {
      const t = 0.35 + (i / 14) * 0.75 + rng() * 0.05 // toward the right/destination
      const x = b.minX + t * (b.maxX - b.minX)
      const side = i % 2 === 0 ? 1 : -1
      const z = side > 0 ? b.maxZ - rng() * 9 : b.minZ + rng() * 9
      if (route.roadDistance(x, z) < 8) continue
      const grow = Math.pow(Math.max(0, t - 0.25) / 0.75, 1.5)
      out.push({ x, z, h: 5 + grow * (11 + rng() * 6), r: 4.5 + rng() * 3.5 })
    }
    return out
  }, [route])

  return (
    <group>
      {peaks.map((p, i) => (
        <group key={i} position={[p.x, 0, p.z]}>
          <mesh position={[0, p.h / 2, 0]}>
            <coneGeometry args={[p.r, p.h, 5]} />
            <meshStandardMaterial color={night ? "#2c3a54" : "#9fb3ad"} flatShading roughness={1} />
          </mesh>
          <mesh position={[0, p.h * 0.82, 0]}>
            <coneGeometry args={[p.r * 0.38, p.h * 0.36, 5]} />
            <meshStandardMaterial color="#f4f9fb" flatShading roughness={0.9} />
          </mesh>
        </group>
      ))}
    </group>
  )
}

function Houses({ route }: { route: SceneRoute }) {
  const houses = useMemo(() => {
    const rng = mulberry32(99)
    const start = route.curve.getPointAt(0.015)
    const out: { x: number; z: number; rot: number; s: number }[] = []
    for (let i = 0; i < 6; i++) {
      const a = rng() * Math.PI * 2
      const r = 4 + rng() * 7
      const x = start.x + Math.cos(a) * r
      const z = start.z + Math.sin(a) * r
      if (route.roadDistance(x, z) < 2.6) continue
      out.push({ x, z, rot: rng() * Math.PI, s: 0.8 + rng() * 0.5 })
    }
    return out
  }, [route])

  return (
    <group>
      {houses.map((h, i) => (
        <group key={i} position={[h.x, 0, h.z]} rotation={[0, h.rot, 0]} scale={h.s}>
          <mesh position={[0, 0.55, 0]}>
            <boxGeometry args={[1.4, 1.1, 1.1]} />
            <meshStandardMaterial color="#f7f4ee" flatShading roughness={1} />
          </mesh>
          <mesh position={[0, 1.35, 0]} rotation={[0, Math.PI / 4, 0]}>
            <coneGeometry args={[1.15, 0.75, 4]} />
            <meshStandardMaterial color="#c96f4a" flatShading roughness={1} />
          </mesh>
        </group>
      ))}
    </group>
  )
}

// ---------------------------------------------------------------------------
// Stops, endpoints, car
// ---------------------------------------------------------------------------

function sideOffset(route: SceneRoute, u: number, dist: number): THREE.Vector3 {
  const p = route.curve.getPointAt(u)
  const t = route.curve.getTangentAt(u)
  const n = new THREE.Vector3(-t.z, 0, t.x).normalize()
  return p.clone().add(n.multiplyScalar(dist))
}

function Station({
  route,
  stop,
  u,
  night,
  highlighted,
  onHover,
}: {
  route: SceneRoute
  stop: Stop
  u: number
  night: boolean
  highlighted: boolean
  onHover?: (id: string | null) => void
}) {
  const pos = useMemo(() => sideOffset(route, u, 2.6), [route, u])
  const accent = highlighted ? "#ffd166" : BRAND

  return (
    <group
      position={pos}
      onPointerOver={(e) => {
        e.stopPropagation()
        onHover?.(stop.charger_id)
      }}
      onPointerOut={() => onHover?.(null)}
    >
      {/* Canopy on two posts */}
      <mesh position={[0, 1.5, 0]}>
        <boxGeometry args={[1.7, 0.14, 1.3]} />
        <meshStandardMaterial color={accent} flatShading roughness={0.7} />
      </mesh>
      {[-0.6, 0.6].map((dx) => (
        <mesh key={dx} position={[dx, 0.75, 0]}>
          <cylinderGeometry args={[0.06, 0.06, 1.5, 6]} />
          <meshStandardMaterial color="#dfe5ea" roughness={0.8} />
        </mesh>
      ))}
      {/* Charger unit */}
      <mesh position={[0, 0.45, -0.35]}>
        <boxGeometry args={[0.35, 0.9, 0.25]} />
        <meshStandardMaterial color="#ffffff" flatShading roughness={0.7} />
      </mesh>
      {night && <pointLight position={[0, 1.9, 0]} color="#ffe1b0" intensity={highlighted ? 9 : 4} distance={9} />}

      <Html position={[0, 2.7, 0]} center zIndexRange={[10, 0]}>
        <div
          className={`pointer-events-auto flex -translate-y-1 cursor-default items-center gap-1.5 whitespace-nowrap rounded-full border bg-white/95 py-1 pl-1.5 pr-2.5 text-[11px] font-medium shadow-md backdrop-blur transition-all ${
            highlighted ? "border-amber-300 shadow-amber-200/50 scale-105" : "border-ink-100"
          }`}
          onMouseEnter={() => onHover?.(stop.charger_id)}
          onMouseLeave={() => onHover?.(null)}
        >
          <span
            className="flex h-4 w-4 items-center justify-center rounded-full text-white"
            style={{ background: highlighted ? "#e8a33d" : BRAND }}
          >
            <svg viewBox="0 0 24 24" className="h-2.5 w-2.5" fill="none" stroke="currentColor" strokeWidth="3.5">
              <path d="M20 6L9 17l-5-5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <span className="text-ink-900">{shortName(stop.name)}</span>
          <span className="text-ink-400">
            {Math.round(stop.arrive_soc)}%→{Math.round(stop.depart_soc)}%
          </span>
          <span className="font-mono font-bold text-brand-700">+{Math.round(stop.charge_min)}m</span>
        </div>
      </Html>
    </group>
  )
}

function shortName(name: string): string {
  return name.length > 22 ? name.slice(0, 20) + "…" : name
}

function EndpointMarker({
  route,
  u,
  label,
  kind,
}: {
  route: SceneRoute
  u: number
  label: string
  kind: "origin" | "dest"
}) {
  const pos = useMemo(() => route.curve.getPointAt(u), [route, u])
  return (
    <group position={pos}>
      {kind === "dest" && (
        <group>
          {/* Chalet */}
          <mesh position={[2.2, 0.6, -1.5]}>
            <boxGeometry args={[1.8, 1.2, 1.4]} />
            <meshStandardMaterial color="#8a6f55" flatShading roughness={1} />
          </mesh>
          <mesh position={[2.2, 1.55, -1.5]} rotation={[0, Math.PI / 4, 0]}>
            <coneGeometry args={[1.5, 0.9, 4]} />
            <meshStandardMaterial color="#f4f9fb" flatShading roughness={1} />
          </mesh>
        </group>
      )}
      {/* Flag pole */}
      <mesh position={[0, 1.1, 0]}>
        <cylinderGeometry args={[0.05, 0.05, 2.2, 6]} />
        <meshStandardMaterial color="#dfe5ea" />
      </mesh>
      <mesh position={[0.35, 1.9, 0]}>
        <boxGeometry args={[0.7, 0.42, 0.04]} />
        <meshStandardMaterial color={kind === "origin" ? BRAND : "#e8564b"} flatShading />
      </mesh>
      <Html position={[0, 3.1, 0]} center zIndexRange={[10, 0]}>
        <div className="pointer-events-none whitespace-nowrap rounded-full bg-ink-900/85 px-2.5 py-1 text-[11px] font-semibold text-white shadow-md backdrop-blur">
          {label.split(",")[0]}
        </div>
      </Html>
    </group>
  )
}

function Car({ route, u, night }: { route: SceneRoute; u: number; night: boolean }) {
  const group = useRef<THREE.Group>(null)

  // Position + orient along the road (springless direct placement; playback
  // in M7 updates `u` per frame via props/state).
  useFrame(() => {
    const g = group.current
    if (!g) return
    const clamped = THREE.MathUtils.clamp(u, 0, 1)
    const p = route.curve.getPointAt(clamped)
    const t = route.curve.getTangentAt(clamped)
    g.position.set(p.x, p.y + 0.12, p.z)
    g.rotation.y = Math.atan2(-t.z, t.x) + Math.PI / 2
  })

  return (
    <group ref={group}>
      {/* Body */}
      <mesh position={[0, 0.32, 0]}>
        <boxGeometry args={[0.95, 0.34, 1.9]} />
        <meshStandardMaterial color={BRAND} flatShading roughness={0.5} />
      </mesh>
      {/* Cabin */}
      <mesh position={[0, 0.6, -0.05]}>
        <boxGeometry args={[0.8, 0.3, 1.05]} />
        <meshStandardMaterial color="#0e1a2b" flatShading roughness={0.3} />
      </mesh>
      {/* Wheels */}
      {[
        [-0.5, 0.62],
        [0.5, 0.62],
        [-0.5, -0.62],
        [0.5, -0.62],
      ].map(([x, z], i) => (
        <mesh key={i} position={[x, 0.16, z]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.16, 0.16, 0.12, 10]} />
          <meshStandardMaterial color="#1a2334" roughness={0.9} />
        </mesh>
      ))}
      {/* Headlights */}
      {night && (
        <>
          <spotLight
            position={[0, 0.45, 1.0]}
            target-position={[0, 0, 8]}
            angle={0.55}
            penumbra={0.7}
            intensity={30}
            distance={22}
            color="#fff3d6"
          />
          <mesh position={[0.28, 0.35, 0.96]}>
            <sphereGeometry args={[0.07, 6, 6]} />
            <meshStandardMaterial color="#fff3d6" emissive="#fff3d6" emissiveIntensity={2} />
          </mesh>
          <mesh position={[-0.28, 0.35, 0.96]}>
            <sphereGeometry args={[0.07, 6, 6]} />
            <meshStandardMaterial color="#fff3d6" emissive="#fff3d6" emissiveIntensity={2} />
          </mesh>
        </>
      )}
    </group>
  )
}
