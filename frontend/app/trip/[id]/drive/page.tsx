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
 * The landing page for a scanned QR. Deliberately its own route rather than a
 * dialog on the results page: this is the phone's screen, it has one job, and
 * it is the only place a run is created.
 */
export default async function DrivePage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = await params
  const [trip, driving] = await Promise.all([fetchTrip(id), isBeingDriven(id)])
  if (!trip) notFound()
  return (
    <main className="min-h-screen bg-white">
      <StartDrivePanel trip={trip} alreadyDriving={driving} />
    </main>
  )
}
