"use client"

import { useEffect, useMemo, useReducer } from "react"
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { SpeedResult } from "@/lib/client"
import { clockAt, fmtKm } from "@/lib/format"
import { PlaybackClock } from "@/lib/playback"

const MINT = "#17a56b"
const AMBER = "#d98e1f"
const INK = "#55677f"
const GRID = "#e8edf1"

interface BatteryChartProps {
  result: SpeedResult
  departureIso: string
  targetSoc: number
  clock: PlaybackClock
}

/**
 * Estimated battery % across the trip for the selected speed — the sawtooth
 * of driving drain and charging spikes. A cursor follows 3D playback.
 */
export default function BatteryChart({ result, departureIso, targetSoc, clock }: BatteryChartProps) {
  // Follow the playback clock at its ~5 Hz notify rate.
  const [, force] = useReducer((n: number) => n + 1, 0)
  useEffect(() => clock.subscribe(force), [clock])

  const data = useMemo(
    () => result.timeline.map((p) => ({ t: p.t_min, soc: p.soc, dist: p.dist_m })),
    [result.timeline]
  )
  const totalMin = result.total_min ?? 0
  const cursor = clock.simRef.min
  const showCursor = cursor > 0 && cursor <= totalMin

  if (data.length === 0) return null

  return (
    <div className="rounded-2xl border border-ink-100 bg-white p-4 shadow-sm sm:p-5">
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <h2 className="font-display text-base font-semibold text-ink-900">
          Battery through the night — {result.speed_kph} km/h
        </h2>
        <span className="text-xs text-ink-400">
          each spike is a charging stop
        </span>
      </div>
      <div
        className="h-44 sm:h-48"
        role="img"
        aria-label={`Estimated battery percentage over the trip at ${result.speed_kph} kilometers per hour`}
      >
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 4 }}>
            <defs>
              <linearGradient id="socFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={MINT} stopOpacity={0.28} />
                <stop offset="100%" stopColor={MINT} stopOpacity={0.03} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis
              dataKey="t"
              type="number"
              domain={[0, Math.ceil(totalMin)]}
              tickFormatter={(t: number) => clockAt(departureIso, t)}
              tickCount={7}
              tick={{ fill: INK, fontSize: 11 }}
              axisLine={{ stroke: GRID }}
              tickLine={false}
            />
            <YAxis
              domain={[0, 100]}
              ticks={[0, 25, 50, 75, 100]}
              tickFormatter={(v: number) => `${v}%`}
              tick={{ fill: INK, fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={40}
            />
            <Tooltip
              cursor={{ stroke: INK, strokeDasharray: "4 4", strokeWidth: 1 }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const p = payload[0].payload as (typeof data)[number]
                return (
                  <div className="rounded-xl border border-ink-100 bg-white px-3 py-2 text-xs shadow-lg shadow-ink-900/10">
                    <div className="font-semibold text-ink-900">
                      {clockAt(departureIso, p.t)} · {Math.round(p.soc)}%
                    </div>
                    <div className="text-ink-500">{fmtKm(p.dist)} driven</div>
                  </div>
                )
              }}
            />
            {/* Arrival floor the plan must respect */}
            <ReferenceLine
              y={targetSoc}
              stroke={AMBER}
              strokeDasharray="5 4"
              label={{
                value: `arrive ≥ ${Math.round(targetSoc)}%`,
                position: "insideBottomRight",
                fill: AMBER,
                fontSize: 10,
                fontWeight: 700,
              }}
            />
            {/* Playback cursor (follows the 3D scene) */}
            {showCursor && <ReferenceLine x={cursor} stroke={INK} strokeWidth={1.5} />}
            <Area
              type="linear"
              dataKey="soc"
              stroke={MINT}
              strokeWidth={2}
              fill="url(#socFill)"
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
