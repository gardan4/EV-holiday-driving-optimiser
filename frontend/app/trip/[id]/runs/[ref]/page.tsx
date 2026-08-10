import type { Metadata } from "next"
import Link from "next/link"
import { notFound } from "next/navigation"
import AppHeader from "@/app/_components/AppHeader"
import ReviewReplay from "@/app/_components/trip/live/ReviewReplay"
import { RunReview, SpeedResult, Trip } from "@/lib/client"
import { clockAt, fmtDuration, fmtKm } from "@/lib/format"

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8100"

export const metadata: Metadata = {
  title: "How the drive went",
  description: "The drive that actually happened, against the plan.",
}

async function fetchJson<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`, { cache: "no-store" })
    if (!res.ok) return null
    return (await res.json()) as T
  } catch {
    return null
  }
}

/**
 * The point of logging the whole journey: seeing where the plan was right,
 * where it wasn't, and by how much. Every row is planned next to actual — a
 * number on its own would say nothing about whether the model earned its keep.
 */
export default async function ReviewPage({
  params,
}: {
  params: Promise<{ id: string; ref: string }>
}) {
  const { id, ref } = await params
  const [trip, review] = await Promise.all([
    fetchJson<Trip>(`/api/trips/${id}`),
    fetchJson<RunReview>(`/api/trips/${id}/runs/${ref}/review`),
  ])
  if (!trip || !review) notFound()

  const late = (review.delta_min ?? 0) > 0
  const factorOff = Math.round((review.final_run_factor - 1) * 100)

  const planned =
    trip.result.speeds.find((s) => s.speed_kph === review.planned_speed_kph) ??
    trip.result.speeds.find((s) => s.feasible)

  /**
   * The drive that actually happened, shaped like a plan so the diorama can
   * race it against the one that was promised. The hero already knows how to
   * run two cars down the same road — this is the moment that pairing has
   * really been for: not 130 against 150, but the plan against the day.
   */
  const actual: SpeedResult | null =
    planned && review.trail.length > 1
      ? {
          speed_kph: 0,
          feasible: true,
          total_min: review.actual_total_min ?? review.trail[review.trail.length - 1].t_min,
          drive_min: null,
          charge_min: null,
          n_stops: null,
          rest_min: 0,
          energy_kwh: 0,
          cost_eur: 0,
          stops: [],
          timeline: review.trail,
        }
      : null

  return (
    <main className="min-h-screen">
      <AppHeader />
      {planned && actual && (
        <ReviewReplay trip={trip} planned={planned} actual={actual} />
      )}
      <div className="mx-auto max-w-3xl px-4 pb-20 pt-6">
        <h1 className="font-display text-2xl font-bold text-ink-900">
          {trip.request.origin.label.split(",")[0]} →{" "}
          {trip.request.dest.label.split(",")[0]}
        </h1>
        <p className="mt-1 text-sm text-ink-500">
          {fmtKm(trip.result.total_dist_m)} · planned at{" "}
          {review.planned_speed_kph.toFixed(0)} km/h · {trip.result.vehicle.make}{" "}
          {trip.result.vehicle.model}
        </p>

        <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Cell label="Planned" value={fmtDuration(review.planned_total_min)} />
          <Cell
            label="Actual"
            value={
              review.actual_total_min != null
                ? fmtDuration(review.actual_total_min)
                : "-"
            }
          />
          <Cell
            label="Difference"
            value={
              review.delta_min != null
                ? `${late ? "+" : "−"}${fmtDuration(Math.abs(review.delta_min))}`
                : "-"
            }
            tone={late ? "warn" : "good"}
          />
          <Cell
            label="Re-plans"
            value={String(review.n_replans)}
            sub={`${review.n_soc_readings} battery checks`}
          />
        </div>

        {factorOff !== 0 && (
          <p className="mt-4 rounded-2xl border border-ink-100 bg-white p-4 text-sm leading-relaxed text-ink-700">
            <strong>What the drive taught the model.</strong> Measured against
            your battery readings, the car used{" "}
            <strong>
              {Math.abs(factorOff)}% {factorOff > 0 ? "more" : "less"}
            </strong>{" "}
            energy than the catalog consumption curve predicted. Wind, load,
            tyres and how the car was actually driven all live in that one
            number. It is the honest gap between a fitted curve and a real
            Tuesday.
          </p>
        )}

        <h2 className="mt-8 font-display text-lg font-semibold text-ink-900">
          Stop by stop
        </h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[34rem] text-sm">
            <thead>
              <tr className="border-b border-ink-100 text-left text-xs uppercase tracking-wider text-ink-400">
                <th className="pb-2 font-semibold">Stop</th>
                <th className="pb-2 text-right font-semibold">Planned arrival</th>
                <th className="pb-2 text-right font-semibold">Actual</th>
                <th className="pb-2 text-right font-semibold">Battery in</th>
              </tr>
            </thead>
            <tbody>
              {review.stops.map((s) => (
                <tr key={s.charger_id} className="border-b border-ink-50">
                  <td className="py-2 pr-3 text-ink-800">{s.name}</td>
                  <td className="py-2 text-right font-mono text-xs text-ink-500">
                    {s.planned_arrive_min != null
                      ? clockAt(trip.request.departure_iso, s.planned_arrive_min)
                      : "-"}
                  </td>
                  <td className="py-2 text-right font-mono text-xs text-ink-800">
                    {s.actual_arrive_min != null
                      ? clockAt(trip.request.departure_iso, s.actual_arrive_min)
                      : "-"}
                  </td>
                  <td className="py-2 text-right font-mono text-xs text-ink-500">
                    {s.planned_soc != null ? `${s.planned_soc.toFixed(0)}%` : "-"}
                    {s.actual_soc != null && (
                      <span className="text-ink-800"> → {s.actual_soc.toFixed(0)}%</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-ink-400">
          Blank actuals mean the drive was never logged near that stop. Either
          it was skipped, or the phone wasn&apos;t reporting at the time.
        </p>

        <Link
          href={`/trip/${id}`}
          className="mt-8 inline-block rounded-xl border border-ink-200 px-4 py-2.5 text-sm font-semibold text-ink-700"
        >
          Back to the plan
        </Link>
      </div>
    </main>
  )
}

function Cell({
  label,
  value,
  sub,
  tone = "plain",
}: {
  label: string
  value: string
  sub?: string
  tone?: "plain" | "warn" | "good"
}) {
  const cls =
    tone === "warn" ? "text-amber-700" : tone === "good" ? "text-brand-700" : "text-ink-900"
  return (
    <div className="rounded-2xl border border-ink-100 bg-white p-3">
      <div className="text-xs font-semibold uppercase tracking-wider text-ink-400">
        {label}
      </div>
      <div className={`mt-1 font-display text-xl font-semibold ${cls}`}>{value}</div>
      {sub && <div className="text-xs text-ink-400">{sub}</div>}
    </div>
  )
}
