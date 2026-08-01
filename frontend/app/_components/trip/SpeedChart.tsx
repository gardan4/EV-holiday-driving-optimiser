"use client"

import { useMemo } from "react"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { SpeedResult } from "@/lib/client"
import { fmtDuration, fmtHm } from "@/lib/format"

const DRIVE = "#3f6dbf" // --color-chart-drive (validated)
const CHARGE = "#d98e1f" // --color-chart-charge
const REST = "#aab6c5" // ink-300, for break time
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
 * Total door-to-door time by cruise speed, split into what you spend the time
 * ON. Stacked bars rather than a line so the charging block is visible as a
 * quantity — the whole argument is "faster driving buys more charging" — with
 * the charging cost direct-labelled above each bar.
 *
 * Bars start at zero (their length encodes magnitude); cost lives in a label
 * rather than a second y-axis, which would invite false crossing-point reads.
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
        drive: s.feasible ? (s.drive_min ?? 0) : 0,
        charge: s.feasible ? (s.charge_min ?? 0) : 0,
        rest: s.feasible ? (s.rest_min ?? 0) : 0,
        total: s.feasible ? s.total_min : null,
        cost: s.feasible ? s.cost_eur : 0,
        stops: s.n_stops,
        feasible: s.feasible,
      })),
    [speeds]
  )

  const feasible = speeds.filter((s) => s.feasible)
  if (feasible.length === 0) return null
  const anyRest = feasible.some((s) => (s.rest_min ?? 0) > 0)
  const anyCost = feasible.some((s) => s.cost_eur > 0)
  const maxTotal = Math.max(...feasible.map((s) => s.total_min ?? 0))

  return (
    <div className="rounded-2xl border border-ink-100 bg-white p-4 shadow-sm sm:p-5">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="font-display text-base font-semibold text-ink-900">
          Where the time goes, by cruise speed
        </h2>
        <span className="text-xs text-ink-400">click a bar to explore it</span>
      </div>

      {/* Legend: two or more series, so identity is never colour-alone. */}
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-500">
        <Key color={CHARGE} label="charging" />
        {anyRest && <Key color={REST} label="breaks" />}
        <Key color={DRIVE} label="driving" />
        {anyCost && (
          <span className="text-ink-400">
            € above each bar = what that plan costs to charge
          </span>
        )}
      </div>

      <div
        className="h-72 sm:h-80"
        role="img"
        aria-label="Stacked bar chart of driving and charging time by cruise speed, with charging cost labelled above each bar"
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            margin={{ top: 26, right: 12, bottom: 4, left: 4 }}
            barCategoryGap="18%"
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
              domain={[0, Math.ceil((maxTotal * 1.12) / 60) * 60]}
              tickFormatter={(v: number) => fmtHm(v)}
              tick={{ fill: INK, fontSize: 12 }}
              axisLine={false}
              tickLine={false}
              width={48}
            />
            <Tooltip
              cursor={{ fill: "rgba(85,103,127,0.07)" }}
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null
                const p = payload[0].payload as (typeof data)[number]
                if (!p.feasible)
                  return (
                    <TooltipCard title={`${label} km/h`}>
                      <span className="text-ink-500">not feasible — charger gaps too large</span>
                    </TooltipCard>
                  )
                return (
                  <TooltipCard title={`${label} km/h — ${fmtHm(p.total ?? 0)}`}>
                    <Row color={DRIVE} text={`driving ${fmtDuration(p.drive)}`} />
                    <Row
                      color={CHARGE}
                      text={`charging ${fmtDuration(p.charge)}${
                        p.stops != null ? ` · ${p.stops} stops` : ""
                      }`}
                    />
                    {p.rest > 0 && <Row color={REST} text={`breaks ${fmtDuration(p.rest)}`} />}
                    {p.cost > 0 && (
                      <div className="pt-0.5 font-semibold text-ink-900">
                        €{p.cost.toFixed(2)} of charging
                      </div>
                    )}
                  </TooltipCard>
                )
              }}
            />

            {/* Charging sits on the baseline: comparing it across bars is what
                this chart is for, and a shared baseline makes that easy. Driving
                goes on top so the label always has a non-zero segment to sit on
                (breaks are usually absorbed by the charging stops). */}
            <Bar dataKey="charge" stackId="t" fill={CHARGE} stroke="#fff" strokeWidth={1}>
              {data.map((d) => (
                <Cell key={d.speed} fillOpacity={dim(d.speed, selectedSpeed) ? 0.45 : 1} />
              ))}
            </Bar>
            <Bar dataKey="rest" stackId="t" fill={REST} stroke="#fff" strokeWidth={1}>
              {data.map((d) => (
                <Cell key={d.speed} fillOpacity={dim(d.speed, selectedSpeed) ? 0.45 : 1} />
              ))}
            </Bar>
            <Bar
              dataKey="drive"
              stackId="t"
              fill={DRIVE}
              stroke="#fff"
              strokeWidth={1}
              radius={[4, 4, 0, 0]}
            >
              {data.map((d) => (
                <Cell key={d.speed} fillOpacity={dim(d.speed, selectedSpeed) ? 0.45 : 1} />
              ))}
              <LabelList
                dataKey="cost"
                position="top"
                content={(props) => (
                  <CostLabel
                    {...props}
                    data={data}
                    optimumSpeed={optimumSpeed}
                    selectedSpeed={selectedSpeed}
                  />
                )}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

/** Bars other than the selected one are held back slightly. */
function dim(speed: number, selected: number): boolean {
  return speed !== selected
}

interface CostLabelProps {
  x?: number | string
  y?: number | string
  width?: number | string
  index?: number
  data: { speed: number; cost: number; feasible: boolean }[]
  optimumSpeed: number | null
  selectedSpeed: number
}

/** Cost above the bar, with the fastest plan called out by name. */
function CostLabel({ x, y, width, index, data, optimumSpeed, selectedSpeed }: CostLabelProps) {
  if (index == null) return null
  const d = data[index]
  if (!d || !d.feasible || d.cost <= 0) return null
  const cx = Number(x) + Number(width) / 2
  const isBest = d.speed === optimumSpeed
  const isSel = d.speed === selectedSpeed
  const top = Number(y)
  return (
    <g>
      {isBest && (
        <text
          x={cx}
          y={top - 18}
          textAnchor="middle"
          fill={OPTIMUM}
          fontSize={11}
          fontWeight={700}
        >
          fastest
        </text>
      )}
      <text
        x={cx}
        y={top - 6}
        textAnchor="middle"
        fill={isBest ? OPTIMUM : isSel ? "#0e1a2b" : "#7d8ca0"}
        fontSize={11}
        fontWeight={isBest || isSel ? 700 : 500}
      >
        €{Math.round(d.cost)}
      </text>
    </g>
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

function Row({ color, text }: { color: string; text: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <Key color={color} />
      {text}
    </div>
  )
}

function Key({ color, label }: { color: string; label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color }} />
      {label}
    </span>
  )
}
