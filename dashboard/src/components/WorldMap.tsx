import { geoGraticule, geoInterpolate, geoNaturalEarth1, geoPath } from "d3-geo"
import { useEffect, useMemo, useRef, useState } from "react"
import type { Corridor, Row } from "../lib/api"
import { WORLD } from "../lib/world"
import { Empty } from "./ui"

/** Corridors as arcs over a map of the world.
 *
 * The whole world, not just Europe, because the planner is: ORS routes between
 * any two points it has roads for, and `trip_stats` has no idea Europe is
 * special. The Europe-only version silently dropped every corridor outside its
 * frame — the exact trips most worth seeing, since they are the ones the model
 * knows least about.
 *
 * Geometry is Natural Earth, simplified and bundled (`lib/world.ts`). Bundled
 * rather than fetched because the console's CSP is `connect-src 'self'` — a
 * tile server or CDN GeoJSON would be blocked, and allowing one would break the
 * app's no-external-origins rule. So the "no in-app map" decision survives: it
 * was about the public planner and the CSP, and both hold here.
 *
 * Two things are drawn, answering different questions:
 *
 *   • **Countries**, lit where the simulator has a real motorway speed cap and
 *     hatched everywhere else. On a world map that means a small lit corner and
 *     a large hatched remainder — which is exactly true, and is the design
 *     decision "the planner is worldwide, the speed caps are not" made visible.
 *
 *   • **Corridors**, from `trip_stats`. Endpoints are the geohash-4 centres
 *     `corridors.place()` emits, so nothing resolves finer than ~20 x 25 km.
 *
 * The view frames itself on wherever the trips are, so an all-European window
 * looks like a map of Europe and an American trip widens it automatically. A
 * world map permanently zoomed out would render this app's actual traffic as a
 * thumbnail-sized smudge over the Low Countries.
 */

// ── Projection ──────────────────────────────────────────────────────────────
// `d3-geo` rather than a hand-rolled projection. The hand-rolled version was a
// Robinson lookup table, and it was wrong in a way that only shows up once the
// data leaves one hemisphere: it had no antimeridian handling, so a Tokyo → Los
// Angeles corridor was drawn 1440 units the long way across Europe and Africa
// instead of 693 across the Pacific, and its arcs were bezier curves in
// projected space rather than great circles. `geoPath` clips to the sphere and
// cuts at the antimeridian; `geoInterpolate` gives the true geodesic.
//
// Not a tile-based library (Leaflet, MapLibre): those want a tile server, which
// `connect-src 'self'` forbids and self-hosting world tiles would mean shipping
// gigabytes. `d3-geo` is ~30 kB, does no I/O, and is exactly the part of a map
// stack this needs.
const WORLD_W = 1000

const projection = geoNaturalEarth1()
projection.fitWidth(WORLD_W, { type: "Sphere" })
const path = geoPath(projection)

/** Project [lat, lon] to SVG space. Returns null behind the horizon — not
 * possible for this projection, but `d3` types it as nullable and swallowing
 * that with a `!` would be a lie for any projection that clips. */
function project(lat: number, lon: number): [number, number] | null {
  const p = projection([lon, lat])
  return p ? [p[0], p[1]] : null
}

/** A great-circle path between two points, as an SVG `d`.
 *
 * Sampled through `geoInterpolate` and handed to `geoPath` as a LineString, so
 * the antimeridian cut is `d3`'s problem rather than ours: a Pacific corridor
 * renders as two segments leaving opposite edges, which is what a flat map of a
 * round planet is supposed to do.
 */
function arcPath(a: [number, number], b: [number, number]): string | null {
  const interpolate = geoInterpolate([a[1], a[0]], [b[1], b[0]])
  const steps = 48
  const coordinates: [number, number][] = []
  for (let i = 0; i <= steps; i++) coordinates.push(interpolate(i / steps) as [number, number])
  return path({ type: "LineString", coordinates })
}

/** "NL 52.1,5.1 → AT 47.2,11.4", or "?? 41.9,-87.6" outside modelled Europe.
 *
 * The `??` case is not an edge case: `geo.country_at` only knows coarse boxes
 * for eight European countries, so every corridor anywhere else is labelled
 * that way. Failing to parse it is how the old map came to omit them entirely.
 */
function parseEnds(
  label: string
): { a: [number, number]; b: [number, number]; ccA: string; ccB: string } | null {
  const parts = label.split("→").map((s) => s.trim())
  if (parts.length !== 2) return null
  const parsed = parts.map((p) => {
    const m = p.match(/^(\S+)?\s*(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/)
    if (!m) return null
    const cc = (m[1] ?? "").toUpperCase()
    return { cc: /^[A-Z]{2}$/.test(cc) ? cc : "", lat: Number(m[2]), lon: Number(m[3]) }
  })
  if (!parsed[0] || !parsed[1]) return null
  return {
    a: [parsed[0].lat, parsed[0].lon],
    b: [parsed[1].lat, parsed[1].lon],
    ccA: parsed[0].cc,
    ccB: parsed[1].cc,
  }
}

// Built through `geoPath` rather than by joining projected points: that is what
// clips each ring to the sphere and splits the ones that cross the antimeridian
// (Russia, Fiji) instead of smearing them across the whole frame.
const COUNTRY_PATHS = WORLD.map((c) => ({
  iso: c.iso,
  name: c.name,
  d:
    path({
      type: "Polygon",
      coordinates: c.rings.map((ring) => [...ring, ring[0]]),
    }) ?? "",
})).filter((c) => c.d)

/** The sphere outline and a 30°/20° graticule, so the map has an edge and a
 * grid. Coarse on purpose — a fine grid would imply a precision the geohash-4
 * endpoints do not have. */
const SPHERE_PATH = path({ type: "Sphere" }) ?? ""
const GRATICULE_PATH =
  path(geoGraticule().stepMajor([90, 360]).stepMinor([30, 20])()) ?? ""

// The full projected extent, for the "whole world" view.
const WORLD_BOX = (() => {
  const [[x0, y0], [x1, y1]] = path.bounds({ type: "Sphere" })
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
})()

type Box = { x: number; y: number; w: number; h: number }

// The frame's shape and its rendered width. Both are needed: the first to
// aspect-correct the fitted box, the second to size strokes in real pixels.
const ASPECT = 16 / 10
const RENDER_W = 940

// How far the view may be zoomed. Derived from the projected world rather than
// written as raw units, so changing the projection or `WORLD_W` cannot silently
// turn the zoom limits into nonsense — which is exactly what happened when this
// moved off the hand-rolled projection and its coordinate scale changed.
const EARTH_KM = 40_075

/** A span in kilometres, as projection units at the equator. */
const kmSpan = (km: number) => (WORLD_BOX.w * km) / EARTH_KM

// The floor is set by the data, not by taste. The outlines are simplified to
// ~0.18° (~20 km) and the corridor endpoints are geohash-4 centres (~20 x
// 25 km), so past roughly 2 km per pixel each simplified coastline segment is
// ~10 px long: the map turns visibly polygonal and starts implying a precision
// neither the geometry nor the endpoints have. ~1900 km across the frame is
// where that lands.
const MIN_SPAN = kmSpan(1900)
const MAX_SPAN = WORLD_BOX.w * 1.6

/** Grow a box to the frame's aspect ratio, centred.
 *
 * Without this, `preserveAspectRatio="meet"` letterboxes instead: a tall
 * fitted box (corridors spanning Chicago to Cape Town) gets scaled down to fit
 * the frame's height, leaving most of the width empty and every arc
 * hair-thin. Growing rather than cropping means nothing is ever cut off.
 */
function toAspect(b: Box): Box {
  const current = b.w / b.h
  if (current < ASPECT) {
    const w = b.h * ASPECT
    return { ...b, x: b.x - (w - b.w) / 2, w }
  }
  const h = b.w / ASPECT
  return { ...b, y: b.y - (h - b.h) / 2, h }
}

export function WorldMap({
  corridors,
  transitCountries,
  modelledCountries,
  defaultCapKph,
  pulses,
}: {
  corridors: Corridor[]
  transitCountries: Row[]
  modelledCountries: string[]
  defaultCapKph: number
  pulses: number[]
}) {
  const [hover, setHover] = useState<string | null>(null)
  const [whole, setWhole] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [focused, setFocused] = useState(false)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const drag = useRef<{ x: number; y: number; box: Box } | null>(null)

  // The frame's real rendered width. `max-w-[940px]` means it is narrower than
  // that on a small screen, and pan/zoom maths that assumed the constant would
  // drift away from the cursor.
  const [width, setWidth] = useState(RENDER_W)
  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) =>
      setWidth(entry.contentRect.width || RENDER_W)
    )
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const modelled = useMemo(
    () => new Set(modelledCountries.map((c) => c.toUpperCase())),
    [modelledCountries]
  )

  const traffic = useMemo(() => {
    const m = new Map(transitCountries.map((r) => [r.label.toUpperCase(), r.value]))
    return { m, top: Math.max(1, ...m.values()) }
  }, [transitCountries])

  const arcs = useMemo(() => {
    const max = Math.max(...corridors.map((c) => c.trips), 1)
    return corridors
      .map((c) => {
        const ends = parseEnds(c.label)
        if (!ends) return null
        const p1 = project(ends.a[0], ends.a[1])
        const p2 = project(ends.b[0], ends.b[1])
        const d = arcPath(ends.a, ends.b)
        if (!p1 || !p2 || !d) return null
        const [x1, y1] = p1
        const [x2, y2] = p2
        const gap = c.stops_per_100km ?? 0
        const stroke = gap >= 0.5 ? "#d9534f" : gap >= 0.3 ? "#b8842a" : "#43c389"
        // An unknown country code counts as unmodelled, not as modelled. It
        // means `country_at` did not recognise the point at all — which is a
        // stronger statement about the model's coverage, not a weaker one.
        const guessed = !modelled.has(ends.ccA) || !modelled.has(ends.ccB)
        return {
          d,
          width: 1 + (c.trips / max) * 3.5,
          stroke,
          guessed,
          label: c.label,
          trips: c.trips,
          stops: c.stops_per_100km,
          x1,
          y1,
          x2,
          y2,
        }
      })
      .filter(Boolean) as {
      d: string
      width: number
      stroke: string
      guessed: boolean
      label: string
      trips: number
      stops: number | null
      x1: number
      y1: number
      x2: number
      y2: number
    }[]
  }, [corridors, modelled])

  // Frame the view on the data. Padded generously so arcs bow inside the frame
  // rather than clipping, and floored so two nearby cities do not zoom to
  // street level on a country-outline map.
  const fitted: Box = useMemo(() => {
    if (!arcs.length) return WORLD_BOX
    const xs = arcs.flatMap((a) => [a.x1, a.x2])
    const ys = arcs.flatMap((a) => [a.y1, a.y2])
    const minX = Math.min(...xs)
    const maxX = Math.max(...xs)
    const minY = Math.min(...ys)
    const maxY = Math.max(...ys)
    // Relative to the world, not absolute units, for the same reason as the
    // zoom limits. The floor keeps a single short corridor from filling the
    // frame edge to edge with no context around it.
    const padX = Math.max((maxX - minX) * 0.35, kmSpan(1400))
    const padY = Math.max((maxY - minY) * 0.35, kmSpan(1000))
    return {
      x: minX - padX,
      y: minY - padY,
      w: maxX - minX + padX * 2,
      h: maxY - minY + padY * 2,
    }
  }, [arcs])

  const base = toAspect(whole ? WORLD_BOX : fitted)
  // `view` is the pan/zoom override. null means "follow the computed frame",
  // which is what the two preset buttons restore. Deliberately NOT reset when
  // the data refreshes: the panels reload every ten seconds while the stream
  // is live, and yanking the map back to centre mid-drag would make it unusable
  // exactly when it is most interesting.
  const [view, setView] = useState<Box | null>(null)
  const box = view ?? base

  // The wheel listener is registered once (it must be non-passive), so it
  // cannot close over `base` without going stale the moment the data changes.
  const baseRef = useRef(base)
  baseRef.current = base

  // Everything is drawn in projection units, so a stroke width means nothing
  // until it is divided by how many units a pixel covers. `px(n)` is "n
  // rendered pixels", which keeps borders and arcs the same visual weight at
  // any zoom — the earlier version scaled against the world extent instead and
  // drew sub-pixel country outlines whenever the view was letterboxed.
  const px = (n: number) => (n * box.w) / width
  const hovered = hover ? COUNTRY_PATHS.find((c) => c.iso === hover) : null
  const through = (n: number) => `${n} trip${n === 1 ? "" : "s"} through`

  // ── pan and zoom ──────────────────────────────────────────────────────────
  // Projection units per rendered pixel. Measured rather than assumed, because
  // the frame is `max-w-[940px]` and is narrower than that on a laptop — using
  // the constant would make the map drift away from the cursor as you drag.
  const unitsPerPx = box.w / width

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    // Ignore secondary buttons so a right-click context menu still works.
    if (e.button !== 0) return
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { x: e.clientX, y: e.clientY, box }
    setDragging(true)
  }

  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current
    if (!d) return
    // The map follows the cursor, so the view moves the opposite way.
    setView({
      ...d.box,
      x: d.box.x - (e.clientX - d.x) * unitsPerPx,
      y: d.box.y - (e.clientY - d.y) * unitsPerPx,
    })
  }

  const endDrag = (e: React.PointerEvent<SVGSVGElement>) => {
    if (drag.current && e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
    drag.current = null
    setDragging(false)
  }

  /** Arrow keys pan, +/- zoom, 0 resets.
   *
   * A drag-only map is unusable without a pointer, and this is one `svg` with
   * no other keyboard affordance — so the alternative is not a worse
   * experience, it is no experience.
   */
  const onKeyDown = (e: React.KeyboardEvent<SVGSVGElement>) => {
    const step = box.w * 0.12
    const nudge = (dx: number, dy: number) =>
      setView((c) => {
        const b = c ?? baseRef.current
        return { ...b, x: b.x + dx, y: b.y + dy }
      })
    const scale = (factor: number) =>
      setView((c) => {
        const b = c ?? baseRef.current
        const w = Math.min(Math.max(b.w * factor, MIN_SPAN), MAX_SPAN)
        const h = w / ASPECT
        return { x: b.x + (b.w - w) / 2, y: b.y + (b.h - h) / 2, w, h }
      })

    const actions: Record<string, () => void> = {
      ArrowLeft: () => nudge(-step, 0),
      ArrowRight: () => nudge(step, 0),
      ArrowUp: () => nudge(0, -step),
      ArrowDown: () => nudge(0, step),
      "+": () => scale(1 / 1.3),
      "=": () => scale(1 / 1.3),
      "-": () => scale(1.3),
      _: () => scale(1.3),
      "0": () => setView(null),
    }
    const action = actions[e.key]
    if (!action) return
    e.preventDefault()
    action()
  }

  // Wheel zoom, anchored on the cursor so the point under it stays put.
  //
  // Registered by hand rather than with `onWheel`, because React adds wheel
  // listeners passively — `preventDefault` there is a no-op, so zooming would
  // scroll the dashboard underneath at the same time.
  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      if (!rect.width || !rect.height) return
      // Where the cursor is, as a fraction of the frame.
      const fx = (e.clientX - rect.left) / rect.width
      const fy = (e.clientY - rect.top) / rect.height
      setView((current) => {
        const b = current ?? baseRef.current
        const factor = Math.exp(e.deltaY * 0.0015)
        const w = Math.min(Math.max(b.w * factor, MIN_SPAN), MAX_SPAN)
        const h = w / ASPECT
        // Keep the projected point under the cursor fixed.
        return {
          x: b.x + (b.w - w) * fx,
          y: b.y + (b.h - h) * fy,
          w,
          h,
        }
      })
    }
    el.addEventListener("wheel", onWheel, { passive: false })
    return () => el.removeEventListener("wheel", onWheel)
  }, [])

  return (
    <div className="w-full">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-[12px] text-ink-mute">
          {arcs.length === 0
            ? "No corridors in this window."
            : view
              ? "Drag to pan, scroll to zoom, double-click to reset."
              : whole
                ? "Whole world. Drag to pan, scroll to zoom."
                : `Framed on the ${arcs.length} busiest corridor${
                    arcs.length === 1 ? "" : "s"
                  }. Drag to pan, scroll to zoom.`}
        </p>
        <div className="flex items-center gap-2">
          {view && (
            <button type="button" className="chip" onClick={() => setView(null)}>
              reset view
            </button>
          )}
          <div className="flex overflow-hidden rounded-full border border-deck-line">
            {[
              { on: false, label: "fit to trips" },
              { on: true, label: "whole world" },
            ].map((opt) => (
              <button
                key={opt.label}
                type="button"
                // Picking a preset also drops any manual pan — otherwise the
                // button appears to do nothing, because the override wins.
                onClick={() => {
                  setWhole(opt.on)
                  setView(null)
                }}
                className="px-3 py-1 text-[11px] transition-colors"
                style={{
                  background:
                    !view && whole === opt.on
                      ? "color-mix(in oklab, var(--color-s-mint) 16%, transparent)"
                      : "transparent",
                  color:
                    !view && whole === opt.on
                      ? "var(--color-ink)"
                      : "var(--color-ink-mute)",
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <svg
        ref={svgRef}
        viewBox={`${box.x} ${box.y} ${box.w} ${box.h}`}
        preserveAspectRatio="xMidYMid meet"
        className="mx-auto block h-auto w-full max-w-[940px] rounded-lg focus:outline-none"
        style={{
          aspectRatio: "16 / 10",
          background: "#0d1523",
          cursor: dragging ? "grabbing" : "grab",
          // Without this a touch drag scrolls the page instead of panning, and
          // the map only moves once the browser decides the gesture was not a
          // scroll — which reads as the map being broken.
          touchAction: "none",
          outline: focused ? "1px solid #43c389" : undefined,
        }}
        role="img"
        aria-label="Planned corridors on a world map. Drag to pan, scroll to zoom. When focused, arrow keys pan and + / - zoom."
        tabIndex={0}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onKeyDown={onKeyDown}
        onDoubleClick={() => setView(null)}
      >
        <defs>
          {/* Countries the planner does not model. patternUnits stay in user
              space so the hatch keeps its pitch as the view zooms. */}
          <pattern
            id="guessHatch"
            width={px(8)}
            height={px(8)}
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <rect width={px(8)} height={px(8)} fill="#151f2e" />
            <line
              x1="0"
              y1="0"
              x2="0"
              y2={px(8)}
              stroke="#26344a"
              strokeWidth={px(2.5)}
            />
          </pattern>
        </defs>

        {/* The sphere's edge, and a coarse graticule. Both go through geoPath,
            so the meridians curve with the projection instead of being drawn as
            straight lines that drift away from the land they belong to. */}
        <path d={SPHERE_PATH} fill="none" stroke="#1b2637" strokeWidth={px(1.5)} />
        <path d={GRATICULE_PATH} fill="none" stroke="#141d2b" strokeWidth={px(1)} />

        <g>
          {COUNTRY_PATHS.map((c) => {
            const isModelled = modelled.has(c.iso)
            const trips = traffic.m.get(c.iso) ?? 0
            return (
              <path
                key={c.iso + c.name}
                d={c.d}
                fill={isModelled ? "#22314799" : "url(#guessHatch)"}
                stroke={hover === c.iso ? "#43c389" : "#3a4d68"}
                strokeWidth={px(hover === c.iso ? 1.8 : 0.9)}
                strokeLinejoin="round"
                onMouseEnter={() => setHover(c.iso)}
                onMouseLeave={() => setHover(null)}
              >
                <title>
                  {`${c.name}${trips ? ` · ${through(trips)}` : ""}${
                    isModelled ? "" : ` · not modelled (assumes ${defaultCapKph} km/h)`
                  }`}
                </title>
              </path>
            )
          })}
          {COUNTRY_PATHS.map((c) => {
            const trips = traffic.m.get(c.iso) ?? 0
            if (!trips || !modelled.has(c.iso)) return null
            return (
              <path
                key={`${c.iso}-wash`}
                d={c.d}
                fill="#43c389"
                opacity={0.08 + 0.34 * (trips / traffic.top)}
                pointerEvents="none"
              />
            )
          })}
        </g>

        <g fill="none" strokeLinecap="round">
          {arcs.map((a, i) => (
            <g key={a.label}>
              <title>
                {`${a.label} · ${a.trips} trips${
                  a.stops !== null ? ` · ${a.stops.toFixed(2)} stops/100km` : ""
                }${a.guessed ? " · crosses roads we don't model" : ""}`}
              </title>
              <path
                d={a.d}
                stroke={a.stroke}
                strokeWidth={px(a.width)}
                opacity={0.8}
                strokeDasharray={a.guessed ? `${px(7)} ${px(5)}` : undefined}
              />
              {pulses.includes(i) && (
                <circle r={px(3.5)} fill="#43c389">
                  <animateMotion dur="1.4s" repeatCount="1" path={a.d} fill="freeze" />
                  <animate
                    attributeName="opacity"
                    values="0;1;1;0"
                    dur="1.4s"
                    repeatCount="1"
                    fill="freeze"
                  />
                </circle>
              )}
            </g>
          ))}
          {arcs.map((a) => (
            <g key={`${a.label}-ends`} fill="#e8edf1" stroke="none">
              <circle cx={a.x1} cy={a.y1} r={px(2.2)} opacity={0.85} />
              <circle cx={a.x2} cy={a.y2} r={px(2.2)} opacity={0.85} />
            </g>
          ))}
        </g>
      </svg>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px] text-ink-mute">
        <span className="inline-flex items-center gap-1.5">
          <i className="h-0.5 w-5 rounded-full" style={{ background: "#43c389" }} /> easy charging
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i className="h-0.5 w-5 rounded-full" style={{ background: "#b8842a" }} /> 0.3+ stops / 100 km
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i className="h-0.5 w-5 rounded-full" style={{ background: "#d9534f" }} /> 0.5+ stops / 100 km
        </span>
        <span>line width = trips planned</span>
        <span className="inline-flex items-center gap-1.5">
          <i
            className="inline-block h-3 w-4 rounded-[2px]"
            style={{ background: "#1d2a3c", border: "1px solid #2a3a51" }}
          />
          real speed caps
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i
            className="inline-block h-3 w-4 rounded-[2px]"
            style={{
              backgroundImage: "repeating-linear-gradient(45deg,#141d2b 0 2px,#1d2938 2px 4px)",
              border: "1px solid #2a3a51",
            }}
          />
          not modelled — assumes {defaultCapKph} km/h (dashed arcs cross it)
        </span>
        {hovered && (
          <span style={{ color: "var(--color-ink-soft)" }}>
            {hovered.name}
            {traffic.m.get(hovered.iso) ? ` · ${through(traffic.m.get(hovered.iso)!)}` : ""}
          </span>
        )}
        <span className="text-ink-mute/70">
          endpoints are geohash-4 centres (~20 × 25 km), not addresses
        </span>
      </div>

      {!arcs.length && <Empty>No corridors planned in this window yet.</Empty>}
    </div>
  )
}
