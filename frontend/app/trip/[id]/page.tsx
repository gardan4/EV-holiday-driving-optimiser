import type { Metadata } from "next"
import { notFound } from "next/navigation"
import SiteFooter from "@/app/_components/SiteFooter"
import AppHeader from "@/app/_components/AppHeader"
import ResultsView from "@/app/_components/trip/ResultsView"
import { LiveRun, Trip } from "@/lib/client"
import { fmtDuration, fmtHm } from "@/lib/format"

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
  const best = trip.result.speeds.find((s) => s.speed_kph === trip.result.optimum_speed)
  const baseline = trip.result.speeds.find((s) => s.feasible && s.speed_kph === 100)
  const origin = trip.request.origin.label.split(",")[0]
  const dest = trip.request.dest.label.split(",")[0]
  let description = `${origin} → ${dest} in a ${trip.result.vehicle.make} ${trip.result.vehicle.model}.`
  if (best?.total_min != null) {
    description = `${origin} → ${dest}: cruise ${best.speed_kph} km/h and you're there in ${fmtHm(best.total_min)}`
    if (baseline?.total_min != null && baseline.total_min > best.total_min) {
      description += `, ${fmtDuration(baseline.total_min - best.total_min)} sooner than at 100 km/h`
    }
    description += ", charging stops included."
  }
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
      <AppHeader />
      <ResultsView trip={trip} live={live} />
      <SiteFooter />
    </main>
  )
}
