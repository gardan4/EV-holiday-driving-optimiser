"use client"

/**
 * "Not this one."
 *
 * The planner optimises minutes, and minutes are not the only reason a stop is
 * a bad stop. The quickest charger on the corridor can be a lay-by behind a
 * warehouse with nowhere to eat, at nine in the evening, with everyone in the
 * car hungry. The model cannot see any of that — OpenChargeMap has no reliable
 * amenities and we store none — so this does not try to judge. It hands over
 * the next few candidates with the one thing the model CAN say about each,
 * which is what choosing it costs in arrival time, and lets the human do the
 * judging they were always better at.
 *
 * Three things that matter in the copy. **We say we don't know what's there** —
 * an app that silently ranked "services" above "lay-by" would be inventing
 * data — and every option links out to Maps, which is where a person can
 * actually see whether there is a building next to it. **The cost is to the
 * destination**, not to the stop: swapping a charger re-optimises every stop
 * after it, and a per-leg number would flatter the swap.
 *
 * And **bays are capacity, never availability**. OpenChargeMap lists how many
 * DC points a site has; no operator publishes free-stall counts to us, and
 * there is no aggregator behind this app. Twelve bays is a better bet than one
 * on a Friday evening and that is the whole of what the number means — so it
 * is labelled as what it is, in the one place a driver would otherwise read it
 * as "twelve free".
 */

import { useState } from "react"
import { BatteryCharging, Check, Loader2, MapPin, X } from "lucide-react"
import { Alternative, Alternatives } from "@/lib/client"
import { fmtBays, fmtDuration, fmtKm, mapsLink } from "@/lib/format"

export default function AlternativeStops({
  data,
  busy,
  onTake,
  onClose,
}: {
  data: Alternatives
  busy: boolean
  onTake: (alt: Alternative) => void
  onClose: () => void
}) {
  const [taking, setTaking] = useState<string | null>(null)

  return (
    <div className="mt-3 rounded-2xl border border-ink-200 bg-white p-3 sm:p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-base font-semibold text-ink-900">
            Somewhere else to charge
          </h2>
          <p className="mt-0.5 text-xs leading-relaxed text-ink-500">
            Priced against arriving, at {Math.round(data.speed_kph)} km/h. Bays
            are how many the site lists, <b className="font-semibold">not</b>{" "}
            how many are free — nobody publishes live availability to us. And we
            don&apos;t know what else is there, so open one in Maps if you need
            somewhere to eat.
          </p>
        </div>
        <button
          onClick={onClose}
          aria-label="Close"
          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-ink-400 hover:bg-ink-100"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {data.current && (
        <div className="mt-3 rounded-xl border border-ink-100 bg-ink-50/60 p-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-400">
            Planned now
          </div>
          <Row alt={data.current} />
        </div>
      )}

      {data.alternatives.length === 0 ? (
        <p className="mt-3 rounded-xl border border-ink-100 px-3 py-2 text-sm text-ink-500">
          Nothing else on this stretch reaches the destination. On a thin
          corridor the plan really does depend on this one.
        </p>
      ) : (
        <ul className="mt-2 space-y-2">
          {data.alternatives.map((alt) => (
            <li
              key={alt.charger_id}
              className="rounded-xl border border-ink-100 p-2.5"
            >
              <Row alt={alt} />
              <div className="mt-2 flex items-center gap-2">
                <button
                  onClick={() => {
                    setTaking(alt.charger_id)
                    onTake(alt)
                  }}
                  disabled={busy}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-brand-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
                >
                  {busy && taking === alt.charger_id ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Check className="h-4 w-4" />
                  )}
                  Stop here instead
                </button>
                <a
                  href={mapsLink(alt.lat, alt.lon)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-xl border border-ink-200 px-3 py-2 text-xs font-semibold text-ink-700"
                >
                  <MapPin className="h-3.5 w-3.5" />
                  Look
                </a>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** One charger: what it is, and what taking it costs. */
function Row({ alt }: { alt: Alternative }) {
  const later = alt.delta_min >= 0.5
  return (
    <div className="flex items-start justify-between gap-2">
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-ink-900">
          {alt.name}
        </div>
        <div className="mt-0.5 truncate text-xs text-ink-500">
          {alt.operator && <span>{alt.operator} · </span>}
          {Math.round(alt.power_kw)} kW
          {fmtBays(alt.n_points) && <span> · {fmtBays(alt.n_points)}</span>} · in{" "}
          {fmtKm(alt.dist_from_here_m)}
        </div>
        <div className="mt-0.5 flex items-center gap-1 text-xs text-ink-500">
          <BatteryCharging className="h-3 w-3 shrink-0 text-brand-600" />
          arrive {Math.round(alt.arrive_soc)}% · charge to{" "}
          {Math.round(alt.depart_soc)}% · {Math.round(alt.charge_min)} min
        </div>
      </div>
      <span
        className={`shrink-0 rounded-md px-1.5 py-0.5 font-mono text-xs font-bold ${
          later ? "bg-amber-100 text-amber-800" : "bg-brand-50 text-brand-700"
        }`}
        title="Change to your arrival time"
      >
        {later ? `+${fmtDuration(alt.delta_min)}` : "same"}
      </span>
    </div>
  )
}
