import type { Metadata } from "next"
import { notFound } from "next/navigation"
import StartDrivePanel from "@/app/_components/trip/live/StartDrivePanel"
import { Trip } from "@/lib/client"

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8100"

// Inherits noindex from proxy.ts, which covers all of /trip/*.
export const metadata: Metadata = {
  title: "Start the drive",
  description: "Begin following this trip.",
}

async function fetchTrip(id: string): Promise<Trip | null> {
  try {
    const res = await fetch(`${API_URL}/api/trips/${id}`, { cache: "no-store" })
    if (!res.ok) return null
    return (await res.json()) as Trip
  } catch {
    return null
  }
}

/** Is somebody already driving this? Checked server-side so the phone doesn't
 *  flash the start button before finding out. */
async function isBeingDriven(id: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/api/trips/${id}/live`, { cache: "no-store" })
    if (!res.ok) return false
    const live = (await res.json()) as { status: string }
    return live.status === "active"
  } catch {
    return false
  }
}

/**
 * Which plan this phone is about to drive.
 *
 * `?speed=` carries the reader's choice across the hand-off from the results
 * page, because it is the one thing a QR code cannot ask about. It is resolved
 * against the plan's OWN feasible speeds rather than parsed as a number: this
 * is a public URL that gets scanned, forwarded and edited, and a value the
 * trip never simulated has no itinerary behind it to follow. Anything that
 * doesn't match falls back to the optimum, which is what the button did before
 * the parameter existed.
 */
function chosenSpeed(trip: Trip, raw: string | string[] | undefined): number | null {
  const want = Number(Array.isArray(raw) ? raw[0] : raw)
  if (!Number.isFinite(want)) return null
  const match = trip.result.speeds.find((s) => s.feasible && s.speed_kph === want)
  return match ? match.speed_kph : null
}

/**
 * The landing page for a scanned QR. Deliberately its own route rather than a
 * dialog on the results page: this is the phone's screen, it has one job, and
 * it is the only place a run is created.
 */
export default async function DrivePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>
  searchParams: Promise<{ [k: string]: string | string[] | undefined }>
}) {
  const [{ id }, sp] = await Promise.all([params, searchParams])
  const [trip, driving] = await Promise.all([fetchTrip(id), isBeingDriven(id)])
  if (!trip) notFound()
  return (
    <main className="min-h-screen bg-white">
      <StartDrivePanel
        trip={trip}
        alreadyDriving={driving}
        speedKph={chosenSpeed(trip, sp.speed)}
      />
    </main>
  )
}
