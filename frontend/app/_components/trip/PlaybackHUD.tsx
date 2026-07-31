"use client"

import { useEffect, useReducer } from "react"
import { BatteryCharging, Flag, Pause, Play, Swords, X } from "lucide-react"
import { SpeedResult } from "@/lib/client"
import { clockAt, fmtDuration } from "@/lib/format"
import { isCharging, PlaybackClock, sampleTimeline, timeAtDist } from "@/lib/playback"

const FACTORS = [60, 300, 900] // 1s real = 1 / 5 / 15 sim minutes

interface PlaybackHUDProps {
  clock: PlaybackClock
  departureIso: string
  main: SpeedResult
  race: SpeedResult | null
  raceSpeed: number | null
  feasibleSpeeds: number[]
  onRaceSpeedChange: (speed: number | null) => void
}

/** Floating control bar over the 3D scene: play/scrub the night, watch the
 * battery drain, and race the friend's speed with a live gap timer. */
export default function PlaybackHUD({
  clock,
  departureIso,
  main,
  race,
  raceSpeed,
  feasibleSpeeds,
  onRaceSpeedChange,
}: PlaybackHUDProps) {
  const [, force] = useReducer((n: number) => n + 1, 0)
  useEffect(() => clock.subscribe(force), [clock])

  const t = clock.simRef.min
  const mainNow = sampleTimeline(main.timeline, t)
  const mainDone = t >= (main.total_min ?? 0)
  const raceNow = race ? sampleTimeline(race.timeline, t) : null

  // Gap: how far ahead (in minutes) is the leader at the leader's position?
  let gapMin: number | null = null
  let mainLeads = true
  if (race && raceNow) {
    const leadDist = Math.max(mainNow.dist, raceNow.dist)
    mainLeads = mainNow.dist >= raceNow.dist
    const tMain = timeAtDist(main.timeline, leadDist)
    const tRace = timeAtDist(race.timeline, leadDist)
    gapMin = Math.abs(tRace - tMain)
  }
  const bothDone = race ? t >= Math.max(main.total_min ?? 0, race.total_min ?? 0) : mainDone
  const finishDelta =
    race && race.total_min != null && main.total_min != null
      ? race.total_min - main.total_min
      : null

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col gap-2 p-3">
      {/* Finish banner */}
      {bothDone && race && finishDelta != null && (
        <div className="pointer-events-auto mx-auto flex items-center gap-2 rounded-xl bg-ink-900/90 px-4 py-2 text-sm font-semibold text-white shadow-lg backdrop-blur">
          <Flag className="h-4 w-4 text-brand-300" />
          {main.speed_kph} km/h arrives {fmtDuration(Math.abs(finishDelta))}{" "}
          {finishDelta > 0 ? "before" : "after"} {race.speed_kph} km/h
        </div>
      )}

      {/* Race gap chip */}
      {race && !bothDone && gapMin != null && t > 0 && (
        <div className="pointer-events-auto mx-auto flex items-center gap-2 rounded-full bg-white/90 px-3 py-1 text-xs font-semibold text-ink-900 shadow-md backdrop-blur">
          <span className="h-2 w-2 rounded-full" style={{ background: mainLeads ? "#17a56b" : "#e8a33d" }} />
          {mainLeads ? `${main.speed_kph} km/h` : `${race.speed_kph} km/h`} leads by{" "}
          {fmtDuration(gapMin)}
          {isCharging(main.timeline, t) && (
            <span className="flex items-center gap-1 text-brand-700">
              <BatteryCharging className="h-3 w-3 animate-pulse-soft" /> charging
            </span>
          )}
        </div>
      )}

      {/* Control bar */}
      <div className="pointer-events-auto flex items-center gap-3 rounded-2xl border border-white/20 bg-white/90 px-3 py-2 shadow-lg backdrop-blur">
        <button
          onClick={() => (clock.playing ? clock.pause() : clock.play())}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-ink-900 text-white transition-colors hover:bg-ink-700"
          aria-label={clock.playing ? "Pause" : "Play"}
        >
          {clock.playing ? <Pause className="h-4 w-4" /> : <Play className="ml-0.5 h-4 w-4" />}
        </button>

        <div className="w-14 shrink-0 text-center">
          <div className="font-mono text-sm font-bold tabular-nums text-ink-900">
            {clockAt(departureIso, t)}
          </div>
          <div className="text-[9px] uppercase tracking-wider text-ink-400">sim time</div>
        </div>

        <input
          type="range"
          min={0}
          max={Math.ceil(clock.maxMin)}
          step={1}
          value={Math.min(t, clock.maxMin)}
          onChange={(e) => clock.seek(Number(e.target.value))}
          className="min-w-0 flex-1 accent-brand-500"
          aria-label="Trip progress"
        />

        {/* Battery of the selected plan */}
        <div className="hidden w-24 shrink-0 items-center gap-1.5 sm:flex">
          <BatteryCharging
            className={`h-4 w-4 ${isCharging(main.timeline, t) ? "animate-pulse-soft text-brand-600" : "text-ink-400"}`}
          />
          <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-ink-100">
            <div
              className={`absolute inset-y-0 left-0 rounded-full ${mainNow.soc < 15 ? "bg-amber-500" : "bg-brand-500"}`}
              style={{ width: `${mainNow.soc}%` }}
            />
          </div>
          <span className="w-8 text-right font-mono text-[11px] font-bold tabular-nums text-ink-700">
            {Math.round(mainNow.soc)}%
          </span>
        </div>

        <button
          onClick={() => {
            const i = FACTORS.indexOf(clock.factor)
            clock.setFactor(FACTORS[(i + 1) % FACTORS.length])
          }}
          className="shrink-0 rounded-lg border border-ink-200 px-2 py-1 font-mono text-[11px] font-bold text-ink-700 transition-colors hover:border-ink-300"
          title="Playback speed"
        >
          {clock.factor}×
        </button>

        {/* Race controls */}
        {race == null ? (
          <button
            onClick={() => onRaceSpeedChange(raceSpeed ?? 100)}
            className="flex shrink-0 items-center gap-1.5 rounded-xl border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-800 transition-colors hover:bg-amber-100"
          >
            <Swords className="h-3.5 w-3.5" />
            Race a friend
          </button>
        ) : (
          <div className="flex shrink-0 items-center gap-1.5 rounded-xl border border-amber-300 bg-amber-50 py-1 pl-2 pr-1">
            <span className="h-2.5 w-2.5 rounded-full bg-[#e8a33d]" />
            <select
              value={raceSpeed ?? 100}
              onChange={(e) => onRaceSpeedChange(Number(e.target.value))}
              className="bg-transparent font-mono text-xs font-bold text-amber-900 outline-none"
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
              className="rounded-lg p-1 text-amber-700 hover:bg-amber-100"
              aria-label="Stop racing"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
