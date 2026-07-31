"use client"

import { useMemo } from "react"
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { SpeedResult } from "@/lib/client"
import { fmtDuration, fmtHm } from "@/lib/format"

const DRIVE = "#3f6dbf" // --color-chart-drive (validated)
const CHARGE = "#d98e1f" // --color-chart-charge
const OPTIMUM = "#17a56b" // brand-500
const INK = "#55677f"
const GRID = "#e8edf1"

interface SpeedChartProps {
  speeds: SpeedResult[]
  optimumSpeed: number | null
  selectedSpeed: number
  onSelect: (speed: number) => void
}

/**
 * Total door-to-door time vs cruise speed. Single series → no legend; the
 * optimum point is direct-labeled; infeasible speeds render as a gap. Click
 * anywhere on the plot to select the nearest simulated speed.
 */
export default function SpeedChart({
  speeds,
  optimumSpeed,
  selectedSpeed,
  onSelect,
}: SpeedChartProps) {
  const data = useMemo(
    () =>
      speeds.map((s) => ({
        speed: s.speed_kph,
        total: s.feasible ? s.total_min : null,
        drive: s.drive_min,
        charge: s.charge_min,
        stops: s.n_stops,
      })),
    [speeds]
  )

  const feasible = speeds.filter((s) => s.feasible)
  const best = feasible.find((s) => s.speed_kph === optimumSpeed)
  const selected = feasible.find((s) => s.speed_kph === selectedSpeed)
  const yValues = feasible.map((s) => s.total_min as number)
  const yMin = Math.min(...yValues)
  const yMax = Math.max(...yValues)
  const pad = Math.max((yMax - yMin) * 0.15, 8)

  return (
    <div className="rounded-2xl border border-ink-100 bg-white p-4 shadow-sm sm:p-5">
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <h2 className="font-display text-base font-semibold text-ink-900">
          Total trip time by cruise speed
        </h2>
        <span className="text-xs text-ink-400">click the curve to explore</span>
      </div>
      <div className="h-64 sm:h-72" role="img" aria-label="Line chart of total trip time by cruise speed">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={data}
            margin={{ top: 24, right: 16, bottom: 4, left: 4 }}
            onClick={(state) => {
              const label = state?.activeLabel
              if (label != null) {
                const sp = Number(label)
                if (speeds.find((s) => s.speed_kph === sp)?.feasible) onSelect(sp)
              }
            }}
            style={{ cursor: "pointer" }}
          >
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis
              dataKey="speed"
              tickFormatter={(v: number) => `${v}`}
              tick={{ fill: INK, fontSize: 12 }}
              axisLine={{ stroke: GRID }}
              tickLine={false}
              label={{ value: "cruise speed (km/h)", position: "insideBottom", offset: -2, fill: INK, fontSize: 11 }}
            />
            <YAxis
              domain={[Math.max(0, yMin - pad), yMax + pad]}
              tickFormatter={(v: number) => fmtHm(v)}
              tick={{ fill: INK, fontSize: 12 }}
              axisLine={false}
              tickLine={false}
              width={48}
            />
            <Tooltip
              cursor={{ stroke: INK, strokeDasharray: "4 4", strokeWidth: 1 }}
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null
                const p = payload[0].payload as (typeof data)[number]
                if (p.total == null)
                  return (
                    <TooltipCard title={`${label} km/h`}>
                      <span className="text-ink-500">not feasible — charger gaps too large</span>
                    </TooltipCard>
                  )
                return (
                  <TooltipCard title={`${label} km/h — ${fmtHm(p.total)}`}>
                    <div className="flex items-center gap-1.5">
                      <Swatch color={DRIVE} /> driving {fmtDuration(p.drive ?? 0)}
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Swatch color={CHARGE} /> charging {fmtDuration(p.charge ?? 0)}
                      {p.stops != null && <span className="text-ink-400">· {p.stops} stops</span>}
                    </div>
                  </TooltipCard>
                )
              }}
            />
            <Line
              type="monotone"
              dataKey="total"
              stroke={DRIVE}
              strokeWidth={2}
              connectNulls={false}
              dot={{ r: 2.5, fill: DRIVE, strokeWidth: 0 }}
              activeDot={{ r: 5, fill: DRIVE, stroke: "#fff", strokeWidth: 2 }}
              isAnimationActive={false}
            />
            {selected && selected.speed_kph !== optimumSpeed && (
              <ReferenceDot
                x={selected.speed_kph}
                y={selected.total_min as number}
                r={6}
                fill="#fff"
                stroke={INK}
                strokeWidth={2}
              />
            )}
            {best && (
              <ReferenceDot
                x={best.speed_kph}
                y={best.total_min as number}
                r={6}
                fill={OPTIMUM}
                stroke="#fff"
                strokeWidth={2}
                label={{
                  value: `fastest · ${best.speed_kph} km/h`,
                  position: "top",
                  fill: OPTIMUM,
                  fontSize: 12,
                  fontWeight: 700,
                  offset: 10,
                }}
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function TooltipCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-ink-100 bg-white px-3 py-2 text-xs shadow-lg shadow-ink-900/10">
      <div className="mb-1 font-semibold text-ink-900">{title}</div>
      <div className="space-y-0.5 text-ink-700">{children}</div>
    </div>
  )
}

function Swatch({ color }: { color: string }) {
  return <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />
}
