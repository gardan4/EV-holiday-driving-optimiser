"use client"

import { ChevronLeft, ChevronRight } from "lucide-react"
import { SpeedResult, Trip } from "@/lib/client"
import type { Band } from "@/lib/verdict"
import { batteryShape, buildVerdict } from "@/lib/summary"
import { clockAt, fmtDuration, fmtHm } from "@/lib/format"

/**
 * The answer in one glance: the dominant number (time saved), the arrival
 * clock as its counterweight, the facts as quiet readouts, one line of
 * advice. Same white card language as the charts below it.
 */
export default function AnswerBlock({
  trip,
  selected,
  band,
  onSelectSpeed,
}: {
  trip: Trip
  selected: SpeedResult
  band: Band | null
  onSelectSpeed: (speed: number) => void
}) {
  const { result, request } = trip
  const v = buildVerdict(result, selected.speed_kph)
  if (!v) return null

  const totalMin = selected.total_min ?? 0
  const arriveClock = clockAt(request.departure_iso, totalMin)
  const batt = batteryShape(selected)

  // The neighbouring feasible speeds, for the stepper arrows.
  const feasible = result.speeds
    .filter((s) => s.feasible)
    .map((s) => s.speed_kph)
    .sort((a, b) => a - b)
  const idx = feasible.indexOf(selected.speed_kph)
  const lower = idx > 0 ? feasible[idx - 1] : null
  const higher = idx >= 0 && idx < feasible.length - 1 ? feasible[idx + 1] : null

  // The dominant number. Time saved is the headline; the arrival clock takes
  // over only when there is nothing to compare against.
  let big: string
  let tail: string
  let mint = false
  if (v.baseline == null || v.savedMin == null) {
    big = arriveClock
    tail = "arrival"
  } else if (v.isBaseline) {
    big = arriveClock
    tail = "arrival. The plan the faster ones are measured against"
  } else if (v.tie) {
    big = "±0"
    tail = `vs the ${v.baseline.speed_kph} km/h plan. Same arrival, drive whichever feels calmer`
  } else if (v.savedMin > 0) {
    big = `−${fmtDuration(v.savedMin)}`
    tail = `vs the ${v.baseline.speed_kph} km/h plan`
    mint = true
  } else {
    big = `+${fmtDuration(-v.savedMin)}`
    tail = `slower than the ${v.baseline.speed_kph} km/h plan`
  }

  // At most one line of advice; the flat band is the app's real insight.
  let advice: React.ReactNode = null
  if (band && !band.sharp) {
    advice = (
      <>
        <strong className="font-semibold text-brand-700">
          {band.lo} to {band.hi} km/h
        </strong>{" "}
        all arrive within {band.toleranceMin} min.
        {band.lo !== band.best && band.loSavesEur > 0.5 && (
          <>
            {" "}
            Driving {band.lo} saves{" "}
            <span className="font-semibold text-[#8a5a10]">
              €{band.loSavesEur.toFixed(0)}
            </span>{" "}
            in charging.
          </>
        )}
      </>
    )
  } else if (result.optimum_at_top_speed) {
    advice = (
      <>
        Faster stays quicker all the way to the car&apos;s{" "}
        {Math.round(result.vehicle.top_speed_kph)} km/h limiter.
      </>
    )
  } else if (band?.sharp) {
    advice = (
      <>
        Only {band.best} km/h gets there this fast. Everything else loses{" "}
        {band.toleranceMin} min or more.
      </>
    )
  }

  return (
    <div className="flex items-stretch gap-2 sm:gap-3">
      <SpeedStep
        dir="down"
        target={lower}
        onSelect={onSelectSpeed}
        isBest={lower != null && lower === result.optimum_speed}
      />
      <section
        aria-label="The answer"
        className="min-w-0 flex-1 rounded-2xl border border-ink-100 bg-white p-5 shadow-sm sm:p-6"
      >
      {/* Headline: cruise speed — the number — arrival, one centered row.
          Gaps inline for the same stale-stylesheet reason as the readouts. */}
      <div
        className="flex flex-wrap items-center justify-center text-center"
        style={{ columnGap: "3rem", rowGap: "0.75rem" }}
      >
        <div>
          <div className="font-mono text-2xl font-semibold text-ink-900">
            {selected.speed_kph}
          </div>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-400">
            cruise km/h
          </div>
        </div>
        <div>
          <div
            className={`font-display text-4xl font-bold leading-none sm:text-5xl ${
              mint ? "text-brand-600" : "text-ink-900"
            }`}
          >
            {big}
          </div>
          <div className="mt-1.5 text-sm text-ink-500">{tail}</div>
        </div>
        {!v.isBaseline && v.baseline != null && (
          <div>
            <div className="font-mono text-2xl font-semibold text-ink-900">
              {arriveClock}
            </div>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-400">
              arrival
            </div>
          </div>
        )}
      </div>

      {/* Readouts, one quiet centered row. Gap is inline: a dev tab holding a
          stale stylesheet drops utilities new to the codebase, and this row
          jammed into one word three times that way — its spacing is
          layout-critical, so it doesn't get to depend on the stylesheet being
          current. */}
      <div
        className="mt-5 flex flex-wrap justify-center text-center"
        style={{ columnGap: "2.25rem", rowGap: "0.75rem" }}
      >
        <Readout value={fmtHm(totalMin)} label="door to door" />
        <Readout
          value={`${selected.n_stops ?? 0}`}
          label={selected.n_stops === 1 ? "charging stop" : "charging stops"}
          tone="charge"
        />
        {(selected.n_stops ?? 0) > 0 && (
          <Readout
            value={fmtDuration(selected.charge_min ?? 0)}
            label="plugged in"
            tone="charge"
          />
        )}
        {selected.cost_eur > 0 && (
          <Readout
            value={`€${selected.cost_eur.toFixed(0)}`}
            label="charging cost"
            tone="charge"
          />
        )}
        {batt && (
          <Readout
            value={`${Math.round(batt.arriveSoc)}%`}
            label="on arrival"
            tone="battery"
          />
        )}
      </div>

      {(advice || (v.bestGainMin != null && v.bestGainMin > 1)) && (
        <p className="mt-4 border-t border-ink-100 pt-3.5 text-center text-xs leading-relaxed text-ink-600 sm:text-sm">
          {advice}
          {v.best != null && v.bestGainMin != null && v.bestGainMin > 1 && (
            <>
              {advice && " "}
              {v.best.speed_kph} km/h is {fmtDuration(v.bestGainMin)} faster.{" "}
              <button
                onClick={() => onSelectSpeed(v.best!.speed_kph)}
                className="font-semibold text-brand-700 underline decoration-brand-300 underline-offset-2 transition-colors hover:text-brand-800"
              >
                Switch to it
              </button>
              .
            </>
          )}
        </p>
      )}
      </section>
      <SpeedStep
        dir="up"
        target={higher}
        onSelect={onSelectSpeed}
        isBest={higher != null && higher === result.optimum_speed}
      />
    </div>
  )
}

/** One flanking arrow: step to the neighbouring feasible cruise speed. */
function SpeedStep({
  dir,
  target,
  onSelect,
  isBest,
}: {
  dir: "down" | "up"
  target: number | null
  onSelect: (speed: number) => void
  isBest: boolean
}) {
  const Chevron = dir === "down" ? ChevronLeft : ChevronRight
  return (
    <button
      disabled={target == null}
      onClick={() => target != null && onSelect(target)}
      aria-label={
        target == null
          ? `No ${dir === "down" ? "slower" : "faster"} feasible plan`
          : `Cruise ${target} km/h instead`
      }
      title={target == null ? undefined : `Cruise ${target} km/h`}
      className={`flex w-10 shrink-0 flex-col items-center justify-center gap-0.5 self-stretch rounded-2xl border transition-colors sm:w-11 ${
        target == null
          ? "cursor-not-allowed border-ink-100 bg-ink-100/40 text-ink-300"
          : isBest
            ? "border-brand-300 bg-brand-50 text-brand-700 hover:bg-brand-100"
            : "border-ink-200 bg-white text-ink-500 shadow-sm hover:border-brand-300 hover:text-brand-700"
      }`}
    >
      <Chevron className="h-4 w-4" />
      {target != null && (
        <span className="font-mono text-[10px] font-semibold">{target}</span>
      )}
    </button>
  )
}

/** Same coding as the charts: amber is charging, mint is battery. */
const TONE: Record<string, string> = {
  charge: "text-[#8a5a10]",
  battery: "text-brand-600",
}

function Readout({
  value,
  label,
  tone,
}: {
  value: string
  label: string
  tone?: "charge" | "battery"
}) {
  return (
    <div>
      <div
        className={`font-mono text-sm font-semibold ${tone ? TONE[tone] : "text-ink-900"}`}
      >
        {value}
      </div>
      <div className="mt-0.5 text-[10px] font-semibold uppercase tracking-wider text-ink-400">
        {label}
      </div>
    </div>
  )
}
