import type { Metadata } from "next"
import { notFound } from "next/navigation"
import SiteFooter from "@/app/_components/SiteFooter"
import AppHeader from "@/app/_components/AppHeader"
import ResultsView from "@/app/_components/trip/ResultsView"
import TripsDrawer from "@/app/_components/trips/TripsDrawer"
import { LiveRun, Trip } from "@/lib/client"
import { headlineDescription } from "@/lib/summary"

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8100"

async function fetchTrip(id: string): Promise<Trip | null> {
  try {
    const res = await fetch(`${API_URL}/api/trips/${id}`, { cache: "no-store" })
    if (!res.ok) return null
    return (await res.json()) as Trip
  } catch {
    return null
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>
}): Promise<Metadata> {
  const { id } = await params
  const trip = await fetchTrip(id)
  if (!trip) return { title: "Trip not found" }
  const origin = trip.request.origin.label.split(",")[0]
  const dest = trip.request.dest.label.split(",")[0]
  // Same sentence the page states in the hero and answer block — one builder,
  // so the share card cannot drift from what the page says.
  const description = headlineDescription(trip)
  return {
    title: `${origin} → ${dest}`,
    description,
    openGraph: { title: `${origin} → ${dest} · EV Trip Optimizer`, description },
  }
}

/** The most recent drive of this trip, if there has been one. Absent is the
 *  normal case and not an error. */
async function fetchLive(id: string): Promise<LiveRun | null> {
  try {
    const res = await fetch(`${API_URL}/api/trips/${id}/live`, { cache: "no-store" })
    if (!res.ok) return null
    return (await res.json()) as LiveRun
  } catch {
    return null
  }
}

export default async function TripPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const [trip, live] = await Promise.all([fetchTrip(id), fetchLive(id)])
  if (!trip) notFound()
  return (
    <main className="min-h-screen">
      <TripsDrawer />
      <AppHeader />
      <ResultsView trip={trip} live={live} />
      <SiteFooter />
    </main>
  )
}
