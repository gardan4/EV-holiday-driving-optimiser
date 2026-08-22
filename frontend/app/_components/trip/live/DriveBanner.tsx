/**
 * What has happened to this plan, at the top of the plan.
 *
 * `/trip/[id]` used to render identically whether the trip had never been
 * driven, was being driven at that very moment, or had been driven and
 * reviewed. That made the share link a dead end for the passenger: they
 * received it, saw a plan, and had no idea a drive was under way or where to
 * follow it.
 *
 * Three faces of one URL. Server-rendered, so a watcher opening the link
 * during a drive sees the live state immediately rather than after a poll.
 */

import Link from "next/link"
import { LiveRun } from "@/lib/client"
import { clockSince, fmtDuration, fmtKm } from "@/lib/format"

export default function DriveBanner({
  tripId,
  live,
}: {
  tripId: string
  live: LiveRun | null
}) {
  if (!live) return null

  if (live.status === "active") {
    const behind = live.state.ahead_behind_min
    return (
      <Link
        href={`/trip/${tripId}/live`}
        // The header above already ends in `py-6`, so the air on top comes for
        // free; this only has to supply the matching gap underneath, which it
        // wasn't — the stat cards were butted straight against it.
        className="mb-6 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 transition-colors hover:border-red-300"
      >
        <span className="flex shrink-0 items-center gap-2 font-display text-sm font-semibold text-red-900">
          <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
          Being driven right now
        </span>
        <span className="min-w-0 flex-1 text-sm text-red-900/80">
          {fmtKm(live.state.offset_m)} in ·{" "}
          {Math.abs(behind) < 1
            ? "on time"
            : `${fmtDuration(Math.abs(behind))} ${behind > 0 ? "behind" : "ahead"}`}{" "}
          · arriving {clockSince(live.started_at, arrivalMin(live))}
        </span>
        <span className="shrink-0 text-sm font-semibold text-red-900 underline underline-offset-2">
          Follow the drive
        </span>
      </Link>
    )
  }

  return (
    <Link
      href={`/trip/${tripId}/runs/${live.run_ref}`}
      className="mb-6 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-2xl border border-ink-200 bg-white px-4 py-3 transition-colors hover:border-brand-300"
    >
      <span className="shrink-0 font-display text-sm font-semibold text-ink-900">
        This trip has been driven
      </span>
      <span className="min-w-0 flex-1 text-sm text-ink-500">
        {fmtKm(live.state.offset_m)} covered
        {live.plan ? ` · re-planned ${live.plan.plan_version}×` : ""}
      </span>
      <span className="shrink-0 text-sm font-semibold text-brand-700 underline underline-offset-2">
        See how it went
      </span>
    </Link>
  )
}

/** Minutes from the drive's start to arrival, on whichever plan is in force. */
function arrivalMin(live: LiveRun): number {
  if (live.plan) return live.plan.benchmark.live_total_min
  return live.state.at_min + Math.max(0, live.state.ahead_behind_min)
}
