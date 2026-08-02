import type { Metadata } from "next"
import { notFound } from "next/navigation"
import AppHeader from "@/app/_components/AppHeader"
import LiveView from "@/app/_components/trip/live/LiveView"
import { LiveRun, Trip } from "@/lib/client"

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8100"

// Inherits `noindex` from proxy.ts, which covers all of /trip/*.
export const metadata: Metadata = {
  title: "Live drive",
  description: "Following a trip as it happens.",
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

/** The current drive, if there is one. A trip with no run yet renders the
 *  "nobody is driving this" state rather than a 404 — the link is valid, the
 *  drive just hasn't started. */
async function fetchLive(id: string): Promise<LiveRun | null> {
  try {
    const res = await fetch(`${API_URL}/api/trips/${id}/live`, { cache: "no-store" })
    if (!res.ok) return null
    return (await res.json()) as LiveRun
  } catch {
    return null
  }
}

export default async function LivePage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = await params
  const [trip, live] = await Promise.all([fetchTrip(id), fetchLive(id)])
  if (!trip) notFound()
  return (
    <main className="min-h-screen">
      <AppHeader />
      <LiveView trip={trip} initial={live} />
    </main>
  )
}
