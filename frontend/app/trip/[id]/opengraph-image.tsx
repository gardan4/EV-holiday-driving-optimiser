import { ImageResponse } from "next/og"

/** Per-trip share card: the headline claim ("135 beats 100 by 54 min"). */

export const alt = "EV Trip Optimizer result"
export const size = { width: 1200, height: 630 }
export const contentType = "image/png"

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8100"
const NAVY = "#0e1a2b"
const MINT = "#17a56b"

interface TripLite {
  request: { origin: { label: string }; dest: { label: string }; departure_iso: string }
  result: {
    optimum_speed: number | null
    total_dist_m: number
    vehicle: { make: string; model: string; variant: string | null }
    speeds: { speed_kph: number; feasible: boolean; total_min: number | null }[]
  }
}

export default async function OgImage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  let trip: TripLite | null = null
  try {
    const res = await fetch(`${API_URL}/api/trips/${id}`, { cache: "no-store" })
    if (res.ok) trip = (await res.json()) as TripLite
  } catch {
    // fall through to the generic card
  }

  const origin = trip?.request.origin.label.split(",")[0] ?? "Your trip"
  const dest = trip?.request.dest.label.split(",")[0] ?? ""
  const best = trip?.result.speeds.find((s) => s.speed_kph === trip?.result.optimum_speed)
  const baseline = trip?.result.speeds.find((s) => s.feasible && s.speed_kph === 100)
  const delta =
    best?.total_min != null && baseline?.total_min != null && baseline.total_min > best.total_min
      ? Math.round(baseline.total_min - best.total_min)
      : null

  const headline = best
    ? `Cruise ${best.speed_kph} km/h.`
    : "Should you really drive 100?"
  const sub =
    delta != null && best
      ? `${delta} minutes sooner than at 100 km/h — charging stops included.`
      : "Find the cruise speed that gets your EV there first."

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          gap: 26,
          background: `linear-gradient(150deg, ${NAVY} 0%, #16283f 65%, #0d3a2c 130%)`,
          padding: "72px 84px",
          color: "white",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14, fontSize: 30, fontWeight: 600 }}>
          <div style={{ width: 16, height: 16, borderRadius: 999, background: MINT, display: "flex" }} />
          EV Trip Optimizer
        </div>
        <div style={{ fontSize: 40, color: "rgba(255,255,255,0.75)", display: "flex" }}>
          {origin}
          {dest ? ` → ${dest}` : ""}
          {trip ? ` · ${Math.round(trip.result.total_dist_m / 1000)} km` : ""}
        </div>
        <div style={{ fontSize: 84, fontWeight: 800, lineHeight: 1.02, letterSpacing: "-0.02em", display: "flex" }}>
          {headline}
        </div>
        <div style={{ fontSize: 34, lineHeight: 1.3, color: MINT, fontWeight: 700, display: "flex" }}>
          {sub}
        </div>
        {trip && (
          <div style={{ fontSize: 26, color: "rgba(255,255,255,0.6)", display: "flex" }}>
            {trip.result.vehicle.make} {trip.result.vehicle.model} {trip.result.vehicle.variant ?? ""}
          </div>
        )}
      </div>
    ),
    { ...size }
  )
}
