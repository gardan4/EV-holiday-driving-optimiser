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
import { fmtDuration } from "@/lib/format"

const COST = "#d98e1f" // --color-chart-charge (validated)
const MINT = "#17a56b"
const INK = "#55677f"
const GRID = "#e8edf1"

interface CostChartProps {
  speeds: SpeedResult[]
  optimumSpeed: number | null
  selectedSpeed: number
  onSelect: (speed: number) => void
}

/**
 * What each cruise speed costs to charge. Deliberately a separate chart on the
 * same x-axis rather than a second y-axis on the time chart — two scales in one
 * frame invite false "where the lines cross" readings.
 */
export default function CostChart({
  speeds,
  optimumSpeed,
  selectedSpeed,
  onSelect,
}: CostChartProps) {
  const data = useMemo(
    () =>
      speeds.map((s) => ({
        speed: s.speed_kph,
        cost: s.feasible ? s.cost_eur : null,
        kwh: s.energy_kwh,
        total: s.total_min,
      })),
    [speeds]
  )

  const feasible = speeds.filter((s) => s.feasible)
  if (feasible.length === 0 || feasible.every((s) => s.cost_eur === 0)) return null

  const cheapest = feasible.reduce((a, b) => (b.cost_eur < a.cost_eur ? b : a))
  const fastest = feasible.find((s) => s.speed_kph === optimumSpeed) ?? null
  const selected = feasible.find((s) => s.speed_kph === selectedSpeed) ?? null

  const costs = feasible.map((s) => s.cost_eur)
  const pad = Math.max((Math.max(...costs) - Math.min(...costs)) * 0.18, 2)

  // The trade-off in one sentence: what the fast plan buys and what it costs.
  let verdict: string | null = null
  if (fastest && cheapest && fastest.speed_kph !== cheapest.speed_kph) {
    const extra = fastest.cost_eur - cheapest.cost_eur
    const saved = (cheapest.total_min ?? 0) - (fastest.total_min ?? 0)
    if (saved > 0 && extra > 0) {
      verdict =
        `The fastest plan costs €${extra.toFixed(2)} more than the cheapest and saves ` +
        `${fmtDuration(saved)}, about €${(extra / (saved / 60)).toFixed(2)} per hour bought.`
    }
  }

  return (
    <div className="rounded-2xl border border-ink-100 bg-white p-4 shadow-sm sm:p-5">
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <h2 className="font-display text-base font-semibold text-ink-900">
          What each speed costs to charge
        </h2>
        <span className="text-xs text-ink-400">at the plug, incl. losses</span>
      </div>
      <div
        className="h-48 sm:h-52"
        role="img"
        aria-label="Line chart of charging cost in euros by cruise speed"
      >
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={data}
            margin={{ top: 22, right: 16, bottom: 4, left: 4 }}
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
              tick={{ fill: INK, fontSize: 12 }}
              axisLine={{ stroke: GRID }}
              tickLine={false}
              label={{
                value: "cruise speed (km/h)",
                position: "insideBottom",
                offset: -2,
                fill: INK,
                fontSize: 11,
              }}
            />
            <YAxis
              domain={[Math.max(0, Math.min(...costs) - pad), Math.max(...costs) + pad]}
              tickFormatter={(v: number) => `€${Math.round(v)}`}
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
                if (p.cost == null) return null
                return (
                  <div className="rounded-xl border border-ink-100 bg-white px-3 py-2 text-xs shadow-lg shadow-ink-900/10">
                    <div className="font-semibold text-ink-900">
                      {label} km/h, €{p.cost.toFixed(2)}
                    </div>
                    <div className="text-ink-500">{Math.round(p.kwh)} kWh bought</div>
                  </div>
                )
              }}
            />
            <Line
              type="monotone"
              dataKey="cost"
              stroke={COST}
              strokeWidth={2}
              connectNulls={false}
              dot={{ r: 2.5, fill: COST, strokeWidth: 0 }}
              activeDot={{ r: 5, fill: COST, stroke: "#fff", strokeWidth: 2 }}
              isAnimationActive={false}
            />
            {selected && selected.speed_kph !== optimumSpeed && (
              <ReferenceDot
                x={selected.speed_kph}
                y={selected.cost_eur}
                r={6}
                fill="#fff"
                stroke={INK}
                strokeWidth={2}
              />
            )}
            <ReferenceDot
              x={cheapest.speed_kph}
              y={cheapest.cost_eur}
              r={5.5}
              fill={COST}
              stroke="#fff"
              strokeWidth={2}
              label={{
                value: `cheapest · €${cheapest.cost_eur.toFixed(0)}`,
                position: "bottom",
                fill: "#8a5a10",
                fontSize: 11,
                fontWeight: 700,
                offset: 8,
              }}
            />
            {fastest && (
              <ReferenceDot
                x={fastest.speed_kph}
                y={fastest.cost_eur}
                r={5.5}
                fill={MINT}
                stroke="#fff"
                strokeWidth={2}
                label={{
                  value: `fastest · €${fastest.cost_eur.toFixed(0)}`,
                  position: "top",
                  fill: MINT,
                  fontSize: 11,
                  fontWeight: 700,
                  offset: 8,
                }}
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {verdict && <p className="mt-1 text-xs leading-relaxed text-ink-500">{verdict}</p>}
    </div>
  )
}
