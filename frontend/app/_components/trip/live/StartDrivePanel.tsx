"use client"

/**
 * The only place a drive is ever born.
 *
 * Deliberately phone-shaped and deliberately empty: three facts and one
 * button. Whoever presses it becomes the driver, because the token is minted
 * here and stored on this device — which is why this page, and not the results
 * page on a laptop, is where starting happens.
 */

import { useState } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { AlertTriangle, Loader2, Navigation } from "lucide-react"
import { toast } from "sonner"
import { Trip, startRun } from "@/lib/client"
import { track } from "@/lib/analytics"
import { clockAt, fmtDuration, fmtKm } from "@/lib/format"
import { plannedRouteLegs } from "@/lib/maps"
import { storeToken } from "@/lib/useLiveRun"
import MapsRouteButton from "../MapsRouteButton"

export default function StartDrivePanel({
  trip,
  alreadyDriving,
  speedKph = null,
}: {
  trip: Trip
  alreadyDriving: boolean
  /** The plan the reader chose on the results page, already checked against
   *  this trip's feasible speeds. Null means they were on the optimum. */
  speedKph?: number | null
}) {
  const router = useRouter()
  // The chosen plan, then the optimum, then whatever is feasible. The drive is
  // followed and benchmarked against this one, so it has to be the plan the
  // reader was actually looking at when they handed it over.
  const best =
    (speedKph != null
      ? trip.result.speeds.find((s) => s.speed_kph === speedKph)
      : undefined) ??
    trip.result.speeds.find((s) => s.speed_kph === trip.result.optimum_speed) ??
    trip.result.speeds.find((s) => s.feasible)
  const [soc, setSoc] = useState(Math.round(trip.request.depart_soc ?? 100))
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState(alreadyDriving)

  async function go(supersede: boolean) {
    if (!best) return
    setBusy(true)
    try {
      const here = await currentPosition().catch(() => null)
      const run = await startRun(trip.id, {
        planned_speed_kph: best.speed_kph,
        depart_soc: soc,
        lat: here?.lat ?? trip.request.origin.lat,
        lon: here?.lon ?? trip.request.origin.lon,
        supersede,
      })
      storeToken(trip.id, run.run_id)
      track("drive_started")
      router.replace(`/trip/${trip.id}/live`)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Could not start the drive"
      if (msg.includes("already being driven")) setConflict(true)
      else toast.error(msg)
      setBusy(false)
    }
  }

  if (!best) {
    return (
      <p className="text-center text-sm text-ink-500">
        This plan has no feasible speed to drive.
      </p>
    )
  }

  return (
    <div className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-md flex-col justify-center px-5 py-8">
      <p className="text-xs font-semibold uppercase tracking-wider text-brand-700">
        Ready when you are
      </p>
      <h1 className="mt-1 font-display text-3xl font-bold leading-tight text-ink-900">
        {trip.request.origin.label.split(",")[0]} →{" "}
        {trip.request.dest.label.split(",")[0]}
      </h1>
      <p className="mt-2 text-sm text-ink-500">
        {fmtKm(trip.result.total_dist_m)} · hold {best.speed_kph} km/h ·{" "}
        {best.n_stops} stops · {fmtDuration(best.total_min ?? 0)}
      </p>

      {conflict ? (
        <div className="mt-6 rounded-2xl border border-amber-300 bg-amber-50 p-4">
          <p className="flex items-start gap-2 text-sm font-semibold text-amber-900">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            This trip is already being driven
          </p>
          <p className="mt-1 text-sm text-amber-900/90">
            Another device started it. Follow that drive, or take over from here
            Taking over ends the other one.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link
              href={`/trip/${trip.id}/live`}
              className="rounded-xl bg-amber-900 px-3 py-2 text-sm font-semibold text-white"
            >
              Follow that drive
            </Link>
            <button
              onClick={() => void go(true)}
              disabled={busy}
              className="rounded-xl border border-amber-400 px-3 py-2 text-sm font-semibold text-amber-900 disabled:opacity-50"
            >
              Take over
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="mt-8">
            <label className="text-xs font-semibold uppercase tracking-wider text-ink-500">
              Battery right now
            </label>
            <div className="mt-2 flex items-center gap-2">
              <Big label="−5" onClick={() => setSoc((s) => clamp(s - 5))} />
              <div className="flex-1 text-center">
                <div className="font-display text-5xl font-bold tabular-nums tracking-tight text-ink-900">
                  {soc}
                  <span className="text-2xl text-ink-400">%</span>
                </div>
              </div>
              <Big label="+5" onClick={() => setSoc((s) => clamp(s + 5))} />
            </div>
            <p className="mt-2 text-center text-xs text-ink-400">
              Read it off the dash. The whole drive is measured from here.
            </p>
          </div>

          <button
            onClick={() => void go(false)}
            disabled={busy}
            className="mt-8 flex w-full items-center justify-center gap-2 rounded-2xl bg-brand-600 py-4 text-lg font-bold text-white transition-transform active:scale-[0.99] disabled:opacity-60"
          >
            {busy ? (
              <Loader2 className="h-5 w-5 animate-spin" />
            ) : (
              <Navigation className="h-5 w-5" />
            )}
            Start driving
          </button>

          {/* The other half of setting off: the plan is only useful in the car
              if the car's navigation knows about the chargers. Below the start
              button deliberately — this phone can only follow the drive if it
              is started here first, and Maps opens in another app. */}
          <div className="mt-3">
            <MapsRouteButton
              legs={plannedRouteLegs(trip, best.stops)}
              label="Open the route in Maps"
              full
            />
            <p className="mt-2 text-center text-xs leading-relaxed text-ink-400">
              With every charging stop already in it. Start the drive first and
              this phone keeps following it while you navigate.
            </p>
          </div>

          <p className="mt-3 text-center text-xs leading-relaxed text-ink-400">
            This phone will follow the drive and share its location. Anyone with
            the trip link can watch; only this phone can steer. A coarse trail
            is stored on the server and deleted after 90 days.{" "}
            {/* The browser permission prompt is about to fire. Whatever this
                app has to say about what it records, this is the moment it is
                worth reading — not buried in a footer on another page. */}
            <Link
              href="/privacy"
              className="underline underline-offset-2 hover:text-ink-600"
            >
              What we record
            </Link>
          </p>
        </>
      )}

      <Link
        href={`/trip/${trip.id}`}
        className="mt-8 text-center text-sm text-ink-500 underline underline-offset-2"
      >
        See the full plan first
      </Link>
    </div>
  )
}

function Big({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="grid h-16 w-16 shrink-0 place-items-center rounded-2xl border border-ink-200 bg-white font-mono text-lg font-bold text-ink-700 active:bg-ink-100"
    >
      {label}
    </button>
  )
}

function currentPosition(): Promise<{ lat: number; lon: number }> {
  return new Promise((resolve, reject) => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      reject(new Error("no geolocation"))
      return
    }
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude }),
      reject,
      { enableHighAccuracy: true, timeout: 8000 }
    )
  })
}

const clamp = (n: number) => Math.max(0, Math.min(100, n))
