"use client"

/**
 * The journey hero: a full-bleed band you scroll through horizontally, where
 * scroll position *is* the playback head.
 *
 * Scroll maps to trip TIME, not distance. That's the whole trick — a charging
 * stop occupies real scroll distance, so as you scroll through one the world
 * holds still, the clock runs on and the battery climbs. Distance (and so the
 * parallax world) is derived from time via the simulator's timeline.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import dynamic from "next/dynamic"
import { BatteryCharging, Flag, MapPin, Moon, Pause, Play, Sun, Swords, X } from "lucide-react"
import { SpeedResult, Trip } from "@/lib/client"
import { clockAt, fmtDuration, fmtKm } from "@/lib/format"
import { isCharging, sampleTimeline, timeAtDist } from "@/lib/playback"
import type { JourneyWorldRef } from "./scene/JourneyScene"
import LiveHud from "./live/LiveHud"

const JourneyScene = dynamic(() => import("./scene/JourneyScene"), {
  ssr: false,
  loading: () => (
    <div className="absolute inset-0 grid place-items-center bg-[#0b1626] text-sm text-white/50">
      <span className="animate-pulse-soft">Building your journey…</span>
    </div>
  ),
})

const MINT = "#17a56b"
const RACE = "#e8a33d"
/** Pixels of scroll per simulated minute — sets how long the road feels. */
const PX_PER_MIN = 5.2
const FACTORS = [60, 300, 900]
/** How long a deliberate look-ahead lasts before the view re-locks to the car.
 *  Long enough to read a charger label, short enough that a phone put down
 *  doesn't sit showing a place the car isn't. */
const BROWSE_TIMEOUT_MS = 20_000

interface JourneyHeroProps {
  trip: Trip
  result: SpeedResult
  race: SpeedResult | null
  raceSpeed: number | null
  feasibleSpeeds: number[]
  onSpeedChange: (s: number) => void
  onRaceSpeedChange: (s: number | null) => void
  hoverStop: string | null
  onHoverStop: (id: string | null) => void
  /**
   * Present only on a drive that is actually happening.
   *
   * Live mode is a different product wearing the same scene. The playhead is a
   * fact rather than a preview, so the transport verbs (play, scrub, race,
   * compare) all disappear and are replaced by driving ones (look ahead,
   * navigate, correct). No verb appears in both.
   */
  live?: LiveHeroState | null
}

export interface LiveHeroState {
  distM: number
  /** The measured/inferred battery — NOT what the plan predicted here. */
  soc: number
  /** How to render it: "46%" when confirmed, "44–48%" when estimated. */
  socLabel: string
  /** Clock anchor. The drive may have started on a different day than planned,
   *  in which case the planned departure is hours wrong. */
  startedAtIso: string
  /** Minutes from `startedAtIso` to arrival, on whichever plan is current. */
  etaMin: number
  aheadBehindMin: number
  socVsPlan: number
  stale: boolean
  secondsSincePing: number
  nextStop: {
    name: string
    distanceM: number
    arriveSoc: number
    lat: number
    lon: number
  } | null
}

export default function JourneyHero({
  trip,
  result,
  race,
  raceSpeed,
  feasibleSpeeds,
  onSpeedChange,
  onRaceSpeedChange,
  hoverStop,
  onHoverStop,
  live = null,
}: JourneyHeroProps) {
  const scroller = useRef<HTMLDivElement>(null)
  const [night, setNight] = useState(true)
  const [playing, setPlaying] = useState(false)
  const [factor, setFactor] = useState(300)
  const [webgl] = useState(() => {
    if (typeof window === "undefined") return true
    try {
      const c = document.createElement("canvas")
      return !!(c.getContext("webgl2") || c.getContext("webgl"))
    } catch {
      return false
    }
  })

  const totalMin = result.total_min ?? 0
  const totalDistM = result.timeline.length
    ? result.timeline[result.timeline.length - 1].dist_m
    : 1
  const trackWidth = Math.max(Math.round(totalMin * PX_PER_MIN), 1200)

  // Mutated every frame without re-rendering React; the scene reads it.
  const world = useRef<JourneyWorldRef>({
    distM: 0,
    cars: [{ distM: 0, color: MINT, lane: 0, charging: false }],
  })
  // HUD values re-render at a human rate, not 60 fps.
  const [hud, setHud] = useState({ min: 0, soc: 100, dist: 0, charging: false, gap: 0 })
  const hudTick = useRef(0)
  const stopMarks = useRef<HTMLDivElement[]>([])

  useEffect(() => {
    world.current.cars = [
      { distM: 0, color: MINT, lane: race ? -1.9 : 0, charging: false },
      ...(race ? [{ distM: 0, color: RACE, lane: 1.9, charging: false }] : []),
    ]
  }, [race])

  /** Scroll position → sim minute → everything else. */
  const syncFromScroll = useCallback(() => {
    const el = scroller.current
    if (!el) return
    const max = el.scrollWidth - el.clientWidth
    const p = max > 0 ? el.scrollLeft / max : 0
    const min = p * totalMin

    const me = sampleTimeline(result.timeline, min)
    world.current.distM = me.dist
    const cars = world.current.cars
    if (cars[0]) {
      cars[0].distM = me.dist
      cars[0].charging = isCharging(result.timeline, min)
    }
    let gap = 0
    if (race && cars[1]) {
      const them = sampleTimeline(race.timeline, min)
      cars[1].distM = them.dist
      cars[1].charging = isCharging(race.timeline, min)
      const lead = Math.max(me.dist, them.dist)
      gap = timeAtDist(race.timeline, lead) - timeAtDist(result.timeline, lead)
    }

    const now = performance.now()
    if (now - hudTick.current > 120) {
      hudTick.current = now
      setHud({
        min,
        soc: me.soc,
        dist: me.dist,
        charging: isCharging(result.timeline, min),
        gap,
      })
    }
  }, [result, race, totalMin])

  useEffect(() => {
    syncFromScroll()
  }, [syncFromScroll])

  // Live mode: the real position drives the scrollbar, through the very same
  // funnel autoplay and scrubbing use. Nothing downstream — the scene, the
  // HUD, the stop markers — needs to know the difference.
  const liveDistM = live?.distM ?? null

  /**
   * Live mode has two states, and conflating them is what made the old
   * behaviour feel broken in both directions.
   *
   * `following` — the scene tracks the car. Horizontal scrolling is OFF, so a
   * thumb can't silently desynchronise the picture from reality at 130 km/h.
   * `browsing` — entered deliberately, for the passenger who wants to see the
   * next charger. The car keeps moving underneath as a ghost tick on the rail;
   * a persistent pill says how far ahead you're looking and brings you back.
   *
   * Browsing expires on its own, because a phone put down in a cupholder must
   * re-lock itself rather than sit lying about where the car is.
   */
  const [browsing, setBrowsing] = useState(false)
  const browseUntil = useRef(0)

  useEffect(() => {
    if (!browsing) return
    const tick = () => {
      if (performance.now() > browseUntil.current) setBrowsing(false)
    }
    const h = setInterval(tick, 1000)
    return () => clearInterval(h)
  }, [browsing])

  const nudgeBrowse = useCallback(() => {
    browseUntil.current = performance.now() + BROWSE_TIMEOUT_MS
  }, [])

  const startBrowsing = useCallback(() => {
    nudgeBrowse()
    setBrowsing(true)
  }, [nudgeBrowse])

  // Anything that changed the plan or the car's situation wins over curiosity.
  const escalate = live ? live.stale : false
  useEffect(() => {
    if (browsing && escalate) setBrowsing(false)
  }, [browsing, escalate])

  const followLocked = liveDistM != null && !browsing

  // On a real drive the battery shown here is the measured/inferred one, not
  // what the plan predicted for this point. Showing the plan's figure while
  // the panel below shows reality would put two different batteries on one
  // screen and make the reader mistrust both. While browsing ahead, though,
  // the world IS the plan's, so the plan's number is the honest one.
  const shownSoc = live && !browsing ? live.soc : hud.soc

  useEffect(() => {
    if (liveDistM == null || browsing) return
    const el = scroller.current
    if (!el) return

    // Placement needs the element's real width, and that arrives late: the 3D
    // scene is dynamically imported, so the first frames have clientWidth 0.
    // Checking `max > 0` alone isn't enough and fails plausibly — with
    // clientWidth 0, `max` is the FULL track width, every position is scaled
    // by ~1.5, and the car is quietly parked at the wrong charger. A fixed
    // number of retry frames is just a race with the bundle, so watch the
    // element instead; this also re-places on rotate and resize.
    const place = () => {
      const max = el.scrollWidth - el.clientWidth
      if (max <= 0 || el.clientWidth <= 0) return
      const minute = timeAtDist(result.timeline, liveDistM)
      el.scrollLeft = totalMin > 0 ? (minute / totalMin) * max : 0
      // Clear the HUD throttle first. It exists to keep 60 fps of scrubbing
      // from re-rendering React 60 times, but a live placement is rare and
      // must not be the frame that gets dropped — on mount the mount-time sync
      // has just claimed the window, and the HUD would keep showing 0 km while
      // the car sat correctly at 140.
      hudTick.current = 0
      syncFromScroll()
    }
    place()
    const ro = new ResizeObserver(place)
    ro.observe(el)
    return () => ro.disconnect()
  }, [liveDistM, browsing, result.timeline, totalMin, syncFromScroll])

  /** How far ahead of the car the reader is currently looking, in metres. */
  const browseAheadM = browsing && liveDistM != null ? hud.dist - liveDistM : 0

  // Autoplay drives the scrollbar, so play and scrub are the same mechanism.
  useEffect(() => {
    if (!playing || liveDistM != null) return
    let raf = 0
    let last = performance.now()
    const step = (now: number) => {
      const el = scroller.current
      if (!el) return
      const dtMin = ((now - last) / 1000 / 60) * factor
      last = now
      const max = el.scrollWidth - el.clientWidth
      const next = el.scrollLeft + (dtMin / (totalMin || 1)) * max
      el.scrollLeft = next
      if (next >= max - 1) {
        setPlaying(false)
        return
      }
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [playing, factor, totalMin])

  // Dragging the progress rail is the same mechanism as scrolling and as
  // autoplay: all three only ever move `scrollLeft`, and everything else in the
  // hero is derived from it.
  const railRef = useRef<HTMLDivElement>(null)
  // The ref is what the handlers read — a pointermove can land before React has
  // committed the state, and dropping the first frame of a drag is visible.
  // The state exists only to restyle the handle.
  const dragging = useRef(false)
  const [scrubbing, setScrubbing] = useState(false)

  const scrubTo = useCallback(
    (clientX: number) => {
      const rail = railRef.current
      const el = scroller.current
      if (!rail || !el) return
      const r = rail.getBoundingClientRect()
      const f = Math.min(1, Math.max(0, (clientX - r.left) / Math.max(1, r.width)))
      el.scrollLeft = f * (el.scrollWidth - el.clientWidth)
      // Don't wait for the scroll event: a fast drag emits pointermove faster
      // than scroll, and the world would trail the handle.
      syncFromScroll()
    },
    [syncFromScroll]
  )

  const onRailDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault()
      setPlaying(false) // you took the wheel
      dragging.current = true
      setScrubbing(true)
      e.currentTarget.setPointerCapture(e.pointerId)
      scrubTo(e.clientX)
    },
    [scrubTo]
  )

  const onRailMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (dragging.current) scrubTo(e.clientX)
    },
    [scrubTo]
  )

  const onRailUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    dragging.current = false
    setScrubbing(false)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
  }, [])

  // Vertical wheel scrolls the road — but hand the page back at either end so
  // the reader can carry on down to the charts.
  /**
   * The wheel does not drive the journey.
   *
   * A trackpad made this hero hostile: a two-finger swipe scrubbed hours of
   * the drive by accident, and the old handler also turned ordinary vertical
   * scrolling into scrubbing, so you couldn't get past the hero to read the
   * page. Both are gone. Moving through the journey is now a deliberate act —
   * drag the rail on a desktop, swipe the scene on a phone.
   *
   * Only horizontal wheel deltas are swallowed. Vertical ones are left alone
   * so the page scrolls normally with the cursor over the scene, which is most
   * of the screen. Registered natively because React's wheel listener is
   * passive and `preventDefault` there does nothing.
   */
  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const block = (e: WheelEvent) => {
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) e.preventDefault()
    }
    el.addEventListener("wheel", block, { passive: false })
    return () => el.removeEventListener("wheel", block)
  }, [])

  const positionStops = useCallback(
    (fracs: number[]) => {
      fracs.forEach((f, i) => {
        const node = stopMarks.current[i]
        if (!node) return
        const visible = f > -0.12 && f < 1.12
        node.style.opacity = visible ? "1" : "0"
        node.style.transform = `translate(-50%, 0) translateX(${(f * 100).toFixed(3)}vw)`
      })
    },
    []
  )

  const stops = result.stops
  const departIso = trip.request.departure_iso
  // Clocks on a real drive count from when it actually started. A trip planned
  // for Friday 22:00 and driven on Saturday morning would otherwise show every
  // time on the screen — including the arrival everyone is watching — many
  // hours out.
  const clockIso = live?.startedAtIso ?? departIso
  const arriveSoc = result.timeline.length
    ? result.timeline[result.timeline.length - 1].soc
    : 0
  const finished = hud.min >= totalMin - 0.5
  const meLeads = hud.gap >= 0

  const progressPct = useMemo(
    () => (totalMin > 0 ? Math.min(100, (hud.min / totalMin) * 100) : 0),
    [hud.min, totalMin]
  )

  return (
    <section
      className="relative h-[74vh] min-h-[460px] w-full overflow-hidden bg-[#0b1626]"
      aria-label="Journey"
    >
      {webgl ? (
        <JourneyScene
          totalDistM={totalDistM}
          stops={stops}
          night={night}
          world={world.current}
          onStopScreenX={positionStops}
        />
      ) : (
        <div className="absolute inset-0 grid place-items-center px-6 text-center text-sm text-white/60">
          3D view unavailable (WebGL is off) — the charts and itinerary below have everything.
        </div>
      )}

      {/* Charger labels, positioned each frame to sit above their station */}
      <div className="pointer-events-none absolute inset-x-0 top-[26%] z-20 h-0">
        {stops.map((s, i) => (
          <div
            key={s.charger_id}
            ref={(el) => {
              if (el) stopMarks.current[i] = el
            }}
            onMouseEnter={() => onHoverStop(s.charger_id)}
            onMouseLeave={() => onHoverStop(null)}
            // NB: no `-translate-x-1/2` class here. Tailwind v4 compiles that
            // to the standalone `translate` property, which composes with the
            // inline `transform` instead of being overridden by it — the label
            // would be shifted by its own width on top of the centring.
            className={`pointer-events-auto absolute left-0 top-0 flex w-max items-center gap-1.5 whitespace-nowrap rounded-full border py-1 pl-1.5 pr-2.5 font-mono text-[11px] shadow-lg transition-[opacity,background-color] duration-200 ${
              hoverStop === s.charger_id
                ? "border-amber-400 bg-amber-100 text-amber-950"
                : "border-ink-200 bg-white text-ink-900"
            }`}
            style={{ opacity: 0 }}
          >
            <span
              className="grid h-4 w-4 place-items-center rounded-full text-[9px] text-white"
              style={{ background: hoverStop === s.charger_id ? "#d98e1f" : MINT }}
            >
              ✓
            </span>
            <b className="font-semibold">{s.name.length > 24 ? s.name.slice(0, 22) + "…" : s.name}</b>
            <span className="text-ink-400">
              {Math.round(s.arrive_soc)}%→{Math.round(s.depart_soc)}%
            </span>
            <span className="font-bold text-brand-700">+{Math.round(s.charge_min)}m</span>
          </div>
        ))}
      </div>

      {/* Endpoint plates */}
      <div className="pointer-events-none absolute left-5 top-5 z-20 flex items-center gap-2 rounded-xl border border-white/12 bg-black/35 px-3 py-2 backdrop-blur">
        <MapPin className="h-3.5 w-3.5 text-white/50" />
        <span className="text-sm font-semibold text-white">
          {trip.request.origin.label.split(",")[0]}
        </span>
        <span className="text-white/35">→</span>
        <span className="text-sm font-semibold text-white">
          {trip.request.dest.label.split(",")[0]}
        </span>
        <span className="ml-1 font-mono text-[11px] text-white/45">
          {fmtKm(totalDistM)} · {result.speed_kph} km/h
        </span>
      </div>

      <button
        onClick={() => setNight((n) => !n)}
        className="absolute right-5 top-5 z-20 grid h-9 w-9 place-items-center rounded-xl border border-white/15 bg-black/35 text-white/80 backdrop-blur transition-colors hover:bg-black/55"
        aria-label={night ? "Switch to day view" : "Switch to night view"}
      >
        {night ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </button>

      {/* Finish banner */}
      {finished && (
        <div className="pointer-events-none absolute left-1/2 top-1/3 z-20 -translate-x-1/2 rounded-2xl border border-white/15 bg-black/60 px-5 py-3 text-center backdrop-blur">
          <div className="flex items-center justify-center gap-2 text-sm font-semibold text-white">
            <Flag className="h-4 w-4 text-brand-300" />
            Arrived {clockAt(departIso, totalMin)} · {Math.round(arriveSoc)}%
          </div>
          {race && race.total_min != null && result.total_min != null && (
            <div className="mt-0.5 font-mono text-xs text-brand-300">
              {fmtDuration(Math.abs(race.total_min - result.total_min))}{" "}
              {race.total_min > result.total_min ? "ahead of" : "behind"} {race.speed_kph} km/h
            </div>
          )}
        </div>
      )}

      {/* The scroll surface itself */}
      <div
        ref={scroller}
        onScroll={syncFromScroll}
        onPointerDown={browsing ? nudgeBrowse : undefined}
        onTouchStart={browsing ? nudgeBrowse : undefined}
        tabIndex={followLocked ? -1 : 0}
        // A slider you cannot move is a lie to a screen reader. While the view
        // is locked to the car it is a picture, not a control.
        role={followLocked ? "img" : "slider"}
        aria-label={followLocked ? "The drive, following the car" : "Scrub the journey"}
        aria-valuemin={followLocked ? undefined : 0}
        aria-valuemax={followLocked ? undefined : Math.round(totalMin)}
        aria-valuenow={followLocked ? undefined : Math.round(hud.min)}
        aria-valuetext={
          followLocked
            ? undefined
            : `${clockAt(clockIso, hud.min)}, ${Math.round(shownSoc)} percent battery`
        }
        className={`absolute inset-0 z-10 overflow-y-hidden focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-4 focus-visible:outline-brand-400 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden ${
          // Locked: horizontal scroll off entirely, and `pan-y` so a vertical
          // flick still scrolls the page underneath rather than being eaten.
          followLocked ? "overflow-x-hidden touch-pan-y" : "overflow-x-auto"
        }`}
      >
        <div style={{ width: trackWidth, height: "100%" }} />
      </div>

      {/* HUD. Live and preview share the scene and nothing else: the numbers a
          driver needs are not the numbers a reader exploring a plan needs, and
          every transport control is meaningless once the playhead is a fact. */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-20 p-4">
        {live && (
          <LiveHud
            live={live}
            browsing={browsing}
            browseAheadM={browseAheadM}
            onLookAhead={startBrowsing}
            onBackToNow={() => setBrowsing(false)}
            clockIso={clockIso}
          />
        )}

        {!live && (
        <div className="pointer-events-auto mx-auto flex max-w-5xl flex-wrap items-center gap-3 rounded-2xl border border-white/12 bg-black/45 px-3 py-2.5 backdrop-blur-md">
          <button
            onClick={() => setPlaying((p) => !p)}
            className="flex h-9 shrink-0 items-center gap-1.5 rounded-xl bg-white px-3 text-ink-900 transition-transform hover:scale-105"
            aria-label={playing ? "Pause the preview" : "Preview the drive"}
          >
            {playing ? <Pause className="h-4 w-4" /> : <Play className="ml-0.5 h-4 w-4" />}
            <span className="text-xs font-semibold">
              {playing ? "Pause" : "Preview"}
            </span>
          </button>

          <div className="w-16 shrink-0">
            <div className="font-mono text-sm font-bold tabular-nums text-white">
              {clockAt(departIso, hud.min)}
            </div>
            <div className="text-[9px] uppercase tracking-wider text-white/40">clock</div>
          </div>

          <div className="w-16 shrink-0">
            <div className="font-mono text-sm font-bold tabular-nums text-white">
              {Math.round(hud.dist / 1000)}
              <span className="text-[10px] font-medium text-white/50"> km</span>
            </div>
            <div className="text-[9px] uppercase tracking-wider text-white/40">driven</div>
          </div>

          {/* Your cruise speed, as a control rather than a readout. The chart
              and the pills far below drive this same value, and nothing on the
              page said so — putting the twin of the race picker here makes the
              two halves visibly one thing, whichever end you reach for. */}
          <div className="shrink-0">
            <div className="flex items-center gap-1.5 rounded-lg border border-brand-400/50 bg-brand-400/10 py-0.5 pl-1.5 pr-1">
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: MINT }} />
              <select
                value={result.speed_kph}
                onChange={(e) => onSpeedChange(Number(e.target.value))}
                className="bg-transparent font-mono text-sm font-bold tabular-nums text-white outline-none [&>option]:text-ink-900"
                aria-label="Your cruise speed"
              >
                {feasibleSpeeds.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <span className="text-[10px] font-medium text-white/50">km/h</span>
            </div>
            <div className="mt-0.5 text-[9px] uppercase tracking-wider text-white/40">
              your speed
            </div>
          </div>

          <div className="flex min-w-[140px] flex-1 items-center gap-2">
            <BatteryCharging
              className={`h-4 w-4 shrink-0 ${hud.charging ? "animate-pulse-soft text-brand-400" : "text-white/40"}`}
            />
            <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-white/15">
              <div
                className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-150"
                style={{
                  width: `${shownSoc}%`,
                  background: shownSoc < 15 ? "#e8a33d" : MINT,
                }}
              />
            </div>
            <span className="w-9 shrink-0 text-right font-mono text-xs font-bold tabular-nums text-white">
              {Math.round(shownSoc)}%
            </span>
          </div>

          <button
            onClick={() => setFactor(FACTORS[(FACTORS.indexOf(factor) + 1) % FACTORS.length])}
            className="shrink-0 rounded-lg border border-white/20 px-2 py-1 font-mono text-[11px] font-bold text-white/80 transition-colors hover:bg-white/10"
            title="Playback speed"
          >
            {factor}×
          </button>

          {race == null ? (
            <button
              onClick={() => onRaceSpeedChange(raceSpeed ?? 100)}
              className="flex shrink-0 items-center gap-1.5 rounded-xl border border-amber-300/60 bg-amber-400/15 px-3 py-1.5 text-xs font-semibold text-amber-200 transition-colors hover:bg-amber-400/25"
            >
              <Swords className="h-3.5 w-3.5" />
              Race a friend
            </button>
          ) : (
            <div className="flex shrink-0 items-center gap-1.5 rounded-xl border border-amber-300/60 bg-amber-400/15 py-1 pl-2 pr-1">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: RACE }} />
              <select
                value={raceSpeed ?? 100}
                onChange={(e) => onRaceSpeedChange(Number(e.target.value))}
                className="bg-transparent font-mono text-xs font-bold text-amber-100 outline-none [&>option]:text-ink-900"
                aria-label="Friend's speed"
              >
                {feasibleSpeeds.map((s) => (
                  <option key={s} value={s}>
                    {s} km/h
                  </option>
                ))}
              </select>
              <button
                onClick={() => onRaceSpeedChange(null)}
                className="rounded-lg p-1 text-amber-200/80 hover:bg-white/10"
                aria-label="Stop racing"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          )}

          {race && !finished && hud.min > 0 && (
            <span className="shrink-0 font-mono text-[11px] font-semibold text-white/80">
              {meLeads ? `${result.speed_kph}` : `${race.speed_kph}`} leads {fmtDuration(Math.abs(hud.gap))}
            </span>
          )}
        </div>
        )}

        {/* Progress rail — draggable. The generous vertical padding is the hit
            area; the visible rail stays hairline-thin. */}
        <div className="mx-auto mt-1 flex max-w-5xl items-center gap-3">
          <div
            ref={railRef}
            // Live: the rail still SHOWS progress, but dragging it would move
            // the playhead somewhere the car isn't.
            onPointerDown={live ? undefined : onRailDown}
            onPointerMove={live ? undefined : onRailMove}
            onPointerUp={live ? undefined : onRailUp}
            onPointerCancel={live ? undefined : onRailUp}
            // pointer-events-auto is load-bearing: the HUD wrapper above turns
            // them off so the scene stays scrubbable around the controls, and
            // this rail is a sibling of the card that turns them back on.
            className={`group pointer-events-auto relative flex-1 touch-none py-2.5 ${
              live ? "cursor-default" : scrubbing ? "cursor-grabbing" : "cursor-grab"
            }`}
            // The scroll surface above already exposes this value as a slider,
            // arrow keys and all. A second control for the same number would
            // only give a screen reader two ways to say the same thing.
            aria-hidden="true"
          >
            <div className="relative h-1 overflow-hidden rounded-full bg-white/15">
              <div
                className="absolute inset-y-0 left-0 bg-white/70"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            {stops.map((s) => (
              <span
                key={s.charger_id}
                className="pointer-events-none absolute top-1/2 h-2 w-0.5 -translate-y-1/2 rounded-full bg-brand-400"
                style={{ left: `${(s.arrive_min / (totalMin || 1)) * 100}%` }}
              />
            ))}
            {/* The handle. Positioned with `left` alone — Tailwind v4 compiles
                -translate-y-1/2 to the standalone `translate` property, so an
                inline `transform` here would compose instead of override. */}
            <span
              // Scale rather than width for the grab affordance: it keeps the
              // handle centred on the playhead at every size. In Tailwind v4
              // `scale-*` and `-translate-y-1/2` are separate properties, so
              // they compose instead of fighting.
              className={`pointer-events-none absolute top-1/2 grid h-4 w-4 -translate-y-1/2 place-items-center rounded-full bg-white shadow-lg shadow-black/40 ring-2 ring-[#0b1626] transition-transform duration-100 ${
                scrubbing ? "scale-125" : "group-hover:scale-125"
              }`}
              style={{ left: `calc(${progressPct}% - 8px)` }}
            >
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ background: hud.charging ? "#d98e1f" : MINT }}
              />
            </span>
          </div>
          <span className="shrink-0 font-mono text-[10px] uppercase tracking-wider text-white/40">
            {live
              ? browsing
                ? "looking ahead"
                : "following the car"
              : // Not "scroll sideways" any more — a trackpad swipe used to
                // scrub hours of the drive by accident, so the wheel no longer
                // moves the journey at all. Drag on a desktop, swipe on a phone.
                "drag, or swipe the scene"}
          </span>
        </div>
      </div>
    </section>
  )
}
