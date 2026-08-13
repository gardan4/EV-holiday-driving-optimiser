import { compact, pct } from "../lib/format"
import { Empty } from "./ui"

type Stage = { name: string; label: string; count: number; from_previous: number | null }

/** The funnel as a flowing ribbon, not four bars.
 *
 * Bars make drop-off something you compute by comparing two heights. A ribbon
 * makes it the thing you actually see: the width that peels away between two
 * stages IS the people who left, drawn to scale. Same numbers, one less step
 * between the reader and the finding.
 *
 * Hand-rolled SVG rather than Recharts' Sankey, which models a node/link graph
 * and needs a lot of persuasion to draw a single-track funnel with the losses
 * falling downward.
 */
export function Funnel({ stages, failures }: { stages: Stage[]; failures: number }) {
  const top = stages[0]?.count ?? 0
  if (!top) return <Empty>No events recorded in this window.</Empty>

  const W = 720
  const H = 190
  const pad = { top: 16, bottom: 34 }
  const band = H - pad.top - pad.bottom
  const colWidth = W / stages.length
  // Where the ribbon narrows: a gap between stages so the taper reads as a
  // transition rather than as a shape in its own right.
  const plateau = colWidth * 0.52

  const y = (count: number) => (count / top) * band
  const colour = ["#5b8ad4", "#5b8ad4", "#43c389", "#43c389"]

  const paths: string[] = []
  for (let i = 0; i < stages.length - 1; i++) {
    const x0 = i * colWidth + plateau
    const x1 = (i + 1) * colWidth
    const h0 = y(stages[i].count)
    const h1 = y(stages[i + 1].count)
    const t0 = pad.top + (band - h0)
    const t1 = pad.top + (band - h1)
    const mid = (x0 + x1) / 2
    // Cubic on both edges so the taper is a flow, not a chamfer.
    paths.push(
      `M ${x0} ${t0} C ${mid} ${t0}, ${mid} ${t1}, ${x1} ${t1} ` +
        `L ${x1} ${t1 + h1} C ${mid} ${t1 + h1}, ${mid} ${t0 + h0}, ${x0} ${t0 + h0} Z`
    )
  }

  return (
    <div className="w-full overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-[190px] w-full min-w-[520px]" role="img">
        <defs>
          <linearGradient id="ribbon" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#5b8ad4" stopOpacity={0.55} />
            <stop offset="100%" stopColor="#43c389" stopOpacity={0.5} />
          </linearGradient>
        </defs>

        {paths.map((d, i) => (
          <path key={i} d={d} fill="url(#ribbon)" opacity={0.55} />
        ))}

        {stages.map((s, i) => {
          const h = y(s.count)
          const x = i * colWidth
          const t = pad.top + (band - h)
          return (
            <g key={s.name}>
              <rect
                x={x}
                y={t}
                width={plateau}
                height={Math.max(h, 2)}
                rx={3}
                fill={colour[i] ?? "#43c389"}
                opacity={0.92}
              />
              <text x={x} y={H - 16} fill="#aab6c5" fontSize={11}>
                {s.label}
              </text>
              <text
                x={x}
                y={H - 3}
                fill="#7d8ca0"
                fontSize={11}
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {compact(s.count)}
                {s.from_previous !== null && (
                  <tspan fill="#43c389"> · {pct(s.from_previous)}</tspan>
                )}
              </text>
            </g>
          )
        })}
      </svg>

      <p className="mt-2 text-[12px] text-ink-mute">
        {failures > 0 ? (
          <>
            <span className="figure" style={{ color: "var(--color-s-rose)" }}>
              {compact(failures)}
            </span>{" "}
            plan {failures === 1 ? "attempt" : "attempts"} failed — counted beside the funnel,
            not inside it, because a failure is not a stage people pass through.
          </>
        ) : (
          "No planning failures in this window."
        )}
      </p>
      {/* The gap between "asked for a plan" and "got one back + failed" is
          requests that never reached the server at all. Worth naming, since it
          is the one number no server-side count can see. */}
      <p className="mt-1 text-[12px] text-ink-mute">
        Anything missing between stages two and three never reached the API — a blocked
        request or a closed tab.
      </p>
    </div>
  )
}
