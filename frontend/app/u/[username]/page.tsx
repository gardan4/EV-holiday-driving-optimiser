import type { Metadata } from "next"
import { notFound } from "next/navigation"
import AppHeader from "@/app/_components/AppHeader"
import SiteFooter from "@/app/_components/SiteFooter"
import TripSummaryCard from "@/app/_components/trips/TripSummaryCard"
import type { UserTrips } from "@/lib/client"

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8100"

/**
 * Somebody's public trip list.
 *
 * Deliberately readable by anyone who knows the name and by nobody who does
 * not know it exists — there is no directory, no search, and `robots` says
 * noindex (the middleware already noindexes anything outside its allowlist;
 * this states it at the page too, because the page is the thing making the
 * promise).
 *
 * Everything shown here comes from the API's own summary, which reduces place
 * labels to their locality server-side. Nothing on this page has access to a
 * coordinate, which is the point: the trip page behind each card does, and it
 * is reachable only with the share link.
 */
async function fetchUserTrips(username: string): Promise<UserTrips | null> {
  try {
    const res = await fetch(
      `${API_URL}/api/users/${encodeURIComponent(username)}/trips`,
      { cache: "no-store" }
    )
    if (!res.ok) return null
    return (await res.json()) as UserTrips
  } catch {
    return null
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ username: string }>
}): Promise<Metadata> {
  const { username } = await params
  return {
    title: `${username}'s trips`,
    description: `Electric-car road trips planned by ${username}.`,
    robots: { index: false, follow: false },
  }
}

export default async function UserPage({
  params,
}: {
  params: Promise<{ username: string }>
}) {
  const { username } = await params
  const data = await fetchUserTrips(username)
  if (!data) notFound()

  return (
    <main className="min-h-screen">
      <AppHeader subtitle={`@${data.username}`} />
      <section className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
        <h1 className="font-display text-3xl font-bold tracking-tight text-ink-900">
          {data.username}&apos;s trips
        </h1>
        <p className="mt-2 text-sm text-ink-500">
          {data.trips.length === 0
            ? "No trips published under this name yet."
            : `${data.trips.length} trip${data.trips.length === 1 ? "" : "s"} planned with the EV Trip Optimizer.`}
        </p>

        {data.trips.length > 0 && (
          <ul className="mt-6 grid gap-3 sm:grid-cols-2">
            {data.trips.map((trip) => (
              <TripSummaryCard key={trip.id} trip={trip} />
            ))}
          </ul>
        )}

        <p className="mt-10 text-xs leading-relaxed text-ink-400">
          Start and destination are shown to the nearest town. This page exists
          because {data.username} chose a username; they can release it at any
          time, which removes this page.
        </p>
      </section>
      <SiteFooter />
    </main>
  )
}
