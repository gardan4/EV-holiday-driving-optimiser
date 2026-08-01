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

interface JourneyHeroProps {
  trip: Trip
  result: SpeedResult
  race: SpeedResult | null
  raceSpeed: number | null
  feasibleSpeeds: number[]
  onRaceSpeedChange: (s: number | null) => void
  hoverStop: string | null
  onHoverStop: (id: string | null) => void
}

export default function JourneyHero({
  trip,
  result,
  race,
  raceSpeed,
  feasibleSpeeds,
  onRaceSpeedChange,
  hoverStop,
  onHoverStop,
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

  // Autoplay drives the scrollbar, so play and scrub are the same mechanism.
  useEffect(() => {
    if (!playing) return
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

  // Vertical wheel scrolls the road — but hand the page back at either end so
  // the reader can carry on down to the charts.
  const onWheel = useCallback((e: React.WheelEvent) => {
    const el = scroller.current
    if (!el) return
    if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return
    const max = el.scrollWidth - el.clientWidth
    const atStart = el.scrollLeft <= 0 && e.deltaY < 0
    const atEnd = el.scrollLeft >= max - 1 && e.deltaY > 0
    if (atStart || atEnd) return
    el.scrollLeft += e.deltaY
    e.preventDefault()
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
        onWheel={onWheel}
        tabIndex={0}
        role="slider"
        aria-label="Scrub the journey"
        aria-valuemin={0}
        aria-valuemax={Math.round(totalMin)}
        aria-valuenow={Math.round(hud.min)}
        aria-valuetext={`${clockAt(departIso, hud.min)}, ${Math.round(hud.soc)} percent battery`}
        className="absolute inset-0 z-10 overflow-x-auto overflow-y-hidden focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-4 focus-visible:outline-brand-400 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        <div style={{ width: trackWidth, height: "100%" }} />
      </div>

      {/* HUD + transport controls */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-20 p-4">
        <div className="pointer-events-auto mx-auto flex max-w-5xl flex-wrap items-center gap-3 rounded-2xl border border-white/12 bg-black/45 px-3 py-2.5 backdrop-blur-md">
          <button
            onClick={() => setPlaying((p) => !p)}
            className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-white text-ink-900 transition-transform hover:scale-105"
            aria-label={playing ? "Pause" : "Play the drive"}
          >
            {playing ? <Pause className="h-4 w-4" /> : <Play className="ml-0.5 h-4 w-4" />}
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

          <div className="flex min-w-[140px] flex-1 items-center gap-2">
            <BatteryCharging
              className={`h-4 w-4 shrink-0 ${hud.charging ? "animate-pulse-soft text-brand-400" : "text-white/40"}`}
            />
            <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-white/15">
              <div
                className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-150"
                style={{
                  width: `${hud.soc}%`,
                  background: hud.soc < 15 ? "#e8a33d" : MINT,
                }}
              />
            </div>
            <span className="w-9 shrink-0 text-right font-mono text-xs font-bold tabular-nums text-white">
              {Math.round(hud.soc)}%
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

        {/* Progress rail */}
        <div className="mx-auto mt-2 flex max-w-5xl items-center gap-2">
          <div className="relative h-1 flex-1 overflow-hidden rounded-full bg-white/15">
            <div className="absolute inset-y-0 left-0 bg-white/70" style={{ width: `${progressPct}%` }} />
            {stops.map((s) => (
              <span
                key={s.charger_id}
                className="absolute top-1/2 h-2 w-0.5 -translate-y-1/2 rounded-full bg-brand-400"
                style={{ left: `${(s.arrive_min / (totalMin || 1)) * 100}%` }}
              />
            ))}
          </div>
          <span className="font-mono text-[10px] uppercase tracking-wider text-white/40">
            scroll sideways to drive
          </span>
        </div>
      </div>
    </section>
  )
}
