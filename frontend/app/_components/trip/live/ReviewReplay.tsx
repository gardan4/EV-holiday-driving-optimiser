"use client"

/**
 * The drive you actually made, replayed against the one you were promised.
 *
 * The hero's race mode was built to compare two cruise speeds. This reuses it
 * for the comparison the app has really been building towards: the plan as a
 * ghost, and the day as it happened alongside it. Scrub it and you can watch
 * where the two came apart.
 */

import { SpeedResult, Trip } from "@/lib/client"
import JourneyHero from "../JourneyHero"

export default function ReviewReplay({
  trip,
  planned,
  actual,
}: {
  trip: Trip
  planned: SpeedResult
  actual: SpeedResult
}) {
  return (
    <div>
      <JourneyHero
        trip={trip}
        result={planned}
        race={actual}
        raceSpeed={null}
        feasibleSpeeds={[]}
        onSpeedChange={() => {}}
        // Nothing to swap: the second car is the drive that happened, not a
        // speed you can pick.
        onRaceSpeedChange={() => {}}
        hoverStop={null}
        onHoverStop={() => {}}
      />
      <p className="mx-auto max-w-3xl px-4 pt-3 text-xs leading-relaxed text-ink-500">
        Mint is the plan; amber is where you actually were. The trail is
        recorded every few minutes, so it&apos;s the shape of the drive rather
        than every metre of it.
      </p>
    </div>
  )
}
