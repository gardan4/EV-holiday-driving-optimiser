import { curvePowerKw, vehicleShortName } from "@/lib/vehicles"
import type { Vehicle } from "@/lib/client"

// Same hexes the Recharts components use, so the catalog pages and the trip
// pages look like one app. Three hues, already validated together for colour
// vision deficiency; past three, variants repeat the hues with a dash pattern
// rather than inventing a fourth colour nobody has checked.
const MINT = "#17a56b"
const BLUE = "#3f6dbf"
const AMBER = "#d98e1f"
const SERIES = [MINT, BLUE, AMBER]
const DASHES = ["", "7 4", "2 4"]
const INK = "#55677f"
const GRID = "#e8edf1"

const W = 640
const H = 260
const PAD = { top: 16, right: 16, bottom: 32, left: 44 }

function seriesStyle(i: number) {
  return {
    stroke: SERIES[i % SERIES.length],
    strokeDasharray: DASHES[Math.floor(i / SERIES.length) % DASHES.length],
  }
}

/**
 * A nameplate's DC charging curves as a plain server-rendered SVG.
 *
 * Deliberately not Recharts like the in-app charts: this page exists to be
 * crawled, and a crawler that does not run JavaScript should still come away
 * with the shape of the curve. The `<title>`/`<desc>` pair is what a text-only
 * reader gets, so it states the numbers rather than naming the picture — and
 * with several variants on one chart it has to state all of them, or the page
 * silently describes only its first car.
 *
 * One variant keeps the filled area; several do not, because overlapping fills
 * read as a third shape that is not in the data.
 */
export default function ChargeCurveChart({ vehicles }: { vehicles: Vehicle[] }) {
  const peak = Math.max(...vehicles.flatMap((v) => v.charge_curve.map(([, kw]) => kw)))
  const yMax = Math.ceil((peak * 1.1) / 25) * 25
  const solo = vehicles.length === 1

  const x = (soc: number) => PAD.left + (soc / 100) * (W - PAD.left - PAD.right)
  const y = (kw: number) => H - PAD.bottom - (kw / yMax) * (H - PAD.top - PAD.bottom)

  // Sample the interpolation rather than joining the raw points: it is the same
  // function the simulator integrates, so the drawn line is the modelled line.
  const paths = vehicles.map((v) => {
    const samples = Array.from({ length: 101 }, (_, soc) => [soc, curvePowerKw(v, soc)])
    const line = samples
      .map(([soc, kw], i) => `${i ? "L" : "M"}${x(soc).toFixed(1)} ${y(kw).toFixed(1)}`)
      .join(" ")
    return {
      line,
      area: `${line} L${x(100).toFixed(1)} ${y(0).toFixed(1)} L${x(0).toFixed(1)} ${y(0).toFixed(1)} Z`,
    }
  })

  const yTicks = Array.from({ length: yMax / 50 + 1 }, (_, i) => i * 50)
  const xTicks = [0, 20, 40, 60, 80, 100]

  return (
    <figure className="m-0">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full"
        role="img"
        aria-label={`DC charging power against state of charge for ${vehicles.length} ${
          solo ? "version" : "versions"
        }, peaking at ${Math.round(peak)} kilowatts`}
      >
        <title>{`DC charging curves, peak ${Math.round(peak)} kW`}</title>
        <desc>
          {vehicles
            .map(
              (v) =>
                `${vehicleShortName(v)}: ` +
                v.charge_curve.map(([soc, kw]) => `${soc}% ${Math.round(kw)} kW`).join(", ")
            )
            .join(". ")}
        </desc>

        {yTicks.map((kw) => (
          <g key={kw}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={y(kw)}
              y2={y(kw)}
              stroke={GRID}
              strokeWidth={1}
            />
            <text x={PAD.left - 8} y={y(kw) + 4} textAnchor="end" fontSize={11} fill={INK}>
              {kw}
            </text>
          </g>
        ))}

        {solo && (
          <>
            <defs>
              <linearGradient id="curveFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={MINT} stopOpacity={0.24} />
                <stop offset="100%" stopColor={MINT} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <path d={paths[0].area} fill="url(#curveFill)" />
          </>
        )}

        {paths.map((p, i) => (
          <path
            key={vehicles[i].slug}
            d={p.line}
            fill="none"
            strokeWidth={2.5}
            strokeLinejoin="round"
            {...seriesStyle(i)}
          />
        ))}

        {xTicks.map((soc) => (
          <text key={soc} x={x(soc)} y={H - 10} textAnchor="middle" fontSize={11} fill={INK}>
            {soc}%
          </text>
        ))}

        <text x={PAD.left - 8} y={PAD.top - 4} textAnchor="end" fontSize={10} fill={INK}>
          kW
        </text>
      </svg>

      {!solo && (
        <figcaption className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-ink-500">
          {vehicles.map((v, i) => (
            <span key={v.slug} className="inline-flex items-center gap-1.5">
              <svg width="18" height="8" aria-hidden="true">
                <line
                  x1="0"
                  y1="4"
                  x2="18"
                  y2="4"
                  strokeWidth={2.5}
                  {...seriesStyle(i)}
                />
              </svg>
              {v.variant ?? v.model}
            </span>
          ))}
        </figcaption>
      )}
    </figure>
  )
}
