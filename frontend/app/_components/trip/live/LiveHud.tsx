"use client"

/**
 * The driving HUD — the whole live screen, really.
 *
 * The diorama stays full-bleed, so this overlay has to carry every number a
 * driver actually needs, because there is nothing above the fold except the
 * scene. Four things, in the order a driver asks them:
 *
 *   1. where's the next charger and what will I get there on
 *   2. when do I arrive
 *   3. how's the battery against the plan
 *   4. am I ahead or behind
 *
 * Everything else — distance covered, consumption calibration, the speed
 * sweep — is retrospection, and belongs on the review page.
 *
 * Laid out as one compact row rather than a grid of cells: same shape as the
 * preview HUD it sits in place of, and short enough not to park itself on top
 * of the car.
 */

import {
  BatteryMedium,
  ChevronsRight,
  Clock,
  Navigation,
  Pencil,
  Undo2,
} from "lucide-react"
import { LiveHeroState } from "../JourneyHero"
import { clockAt, fmtDuration, fmtKm } from "@/lib/format"

export default function LiveHud({
  live,
  browsing,
  browseAheadM,
  onLookAhead,
  onBackToNow,
  clockIso,
  onCorrectBattery,
}: {
  live: LiveHeroState
  browsing: boolean
  browseAheadM: number
  onLookAhead: () => void
  onBackToNow: () => void
  clockIso: string
  /** Driver only. The battery readout is where a driver looks when they want
   *  to fix the battery, so it is what they get to press — the check itself
   *  lives below a full-bleed scene, which on a phone means below the fold. */
  onCorrectBattery?: () => void
}) {
  const late = live.aheadBehindMin > 1
  const early = live.aheadBehindMin < -1
  // Past a few minutes of silence the numbers are history, not telemetry, and
  // saying so matters most to whoever is at home waiting on an arrival time.
  const quiet = live.secondsSincePing > 180
  const dimmed = live.stale || quiet

  return (
    <div className="pointer-events-auto mx-auto max-w-5xl">
      {browsing && (
        <button
          onClick={onBackToNow}
          className="hover-grow-sm mb-2 flex w-full items-center justify-center gap-2 rounded-2xl border border-white/20 bg-white px-4 py-2.5 text-sm font-semibold text-ink-900 shadow-lg transition-transform duration-150 ease-out"
        >
          <Undo2 className="h-4 w-4" />
          Back to the car
          {browseAheadM > 500 && (
            <span className="font-normal text-ink-500">
              · looking {fmtKm(browseAheadM)} ahead
            </span>
          )}
        </button>
      )}

      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-white/12 bg-black/55 px-3 py-2.5 backdrop-blur-md">
        <span
          className={`grid h-9 shrink-0 place-items-center rounded-xl px-2.5 text-[10px] font-bold uppercase tracking-wider ${
            dimmed ? "bg-white/15 text-white/70" : "bg-red-500 text-white"
          }`}
          title={
            live.stale
              ? "Off the planned route, so position and battery are last known"
              : quiet
                ? "No recent update from the driving phone"
                : "Following the drive as it happens"
          }
        >
          {live.stale ? "Paused" : quiet ? "Stale" : "● Live"}
        </span>

        <Readout
          icon={<Clock className="h-3 w-3" />}
          label="arriving"
          value={clockAt(clockIso, live.etaMin)}
          sub={
            Math.abs(live.aheadBehindMin) < 1
              ? "on time"
              : `${late ? "+" : "−"}${fmtDuration(Math.abs(live.aheadBehindMin))}`
          }
          tone={late ? "warn" : early ? "good" : "plain"}
          dim={dimmed}
        />

        <Readout
          icon={<BatteryMedium className="h-3 w-3" />}
          label="battery"
          value={live.socLabel}
          sub={
            Math.abs(live.socVsPlan) < 1
              ? "as planned"
              : `${live.socVsPlan > 0 ? "+" : ""}${Math.round(live.socVsPlan)}% vs plan`
          }
          tone={live.socVsPlan < -5 ? "warn" : live.socVsPlan > 5 ? "good" : "plain"}
          dim={dimmed}
          onPress={onCorrectBattery}
          pressLabel="Correct the battery reading"
        />

        {/* The next stop takes whatever room is left — its name is the one
            genuinely variable-length thing here, and truncating it beats
            letting it push the numbers around. */}
        <div className="min-w-[9rem] flex-1 border-l border-white/10 pl-3">
          {live.nextStop ? (
            <>
              <div className="truncate text-sm font-semibold leading-tight text-white">
                {live.nextStop.name}
              </div>
              <div className="font-mono text-[10px] text-white/50">
                {fmtKm(live.nextStop.distanceM)} · arrive{" "}
                {Math.round(live.nextStop.arriveSoc)}%
              </div>
            </>
          ) : (
            <>
              <div className="truncate text-sm font-semibold leading-tight text-white">
                No more stops
              </div>
              <div className="font-mono text-[10px] text-white/50">
                straight through
              </div>
            </>
          )}
        </div>

        {live.nextStop && (
          <a
            href={`https://www.google.com/maps/search/?api=1&query=${live.nextStop.lat},${live.nextStop.lon}`}
            target="_blank"
            rel="noopener noreferrer"
            className="hover-grow flex h-9 shrink-0 items-center gap-1.5 rounded-xl bg-white/95 px-3 text-xs font-semibold text-ink-900 transition-transform duration-150 ease-out"
          >
            <Navigation className="h-3.5 w-3.5" />
            Navigate
          </a>
        )}

        <button
          onClick={browsing ? onBackToNow : onLookAhead}
          className={`flex h-9 shrink-0 items-center gap-1.5 rounded-xl border px-3 text-xs font-semibold transition-colors ${
            browsing
              ? "border-white/40 bg-white/15 text-white"
              : "border-white/20 text-white/80 hover:bg-white/10"
          }`}
        >
          <ChevronsRight className="h-3.5 w-3.5" />
          {browsing ? "Following" : "Look ahead"}
        </button>
      </div>

      {dimmed && (
        <p className="mt-1 px-1 text-[11px] leading-relaxed text-amber-200/90">
          {live.stale
            ? "Off the planned route, so these are the last known figures, not live ones."
            : `No update from the driving phone for ${fmtDuration(
                live.secondsSincePing / 60
              )}. It may have lost signal.`}
        </p>
      )}
    </div>
  )
}

/** Value over a tiny label — the same shape the preview HUD uses, so the two
 *  modes read as one design rather than two.
 *
 *  `onPress` turns one into a control without changing what it looks like:
 *  same numbers, same place, plus a pencil on the label row. A readout that
 *  becomes a button when there is something to do with it beats a button
 *  parked somewhere else, on a bar that is already full at this width. */
function Readout({
  icon,
  label,
  value,
  sub,
  tone,
  dim,
  onPress,
  pressLabel,
}: {
  icon: React.ReactNode
  label: string
  value: string
  sub: string
  tone: "plain" | "warn" | "good"
  dim?: boolean
  onPress?: () => void
  pressLabel?: string
}) {
  const toneCls =
    tone === "warn"
      ? "text-amber-300"
      : tone === "good"
        ? "text-brand-300"
        : "text-white/45"

  const body = (
    <>
      <div className="font-mono text-sm font-bold tabular-nums leading-tight text-white">
        {value}
      </div>
      <div className="flex items-center gap-1 text-[9px] uppercase tracking-wider text-white/40">
        {icon}
        {label}
        {onPress && <Pencil className="h-2.5 w-2.5" />}
      </div>
      <div className={`text-[10px] leading-tight ${toneCls}`}>{sub}</div>
    </>
  )

  if (!onPress) {
    return <div className={`shrink-0 ${dim ? "opacity-60" : ""}`}>{body}</div>
  }

  return (
    <button
      onClick={onPress}
      aria-label={pressLabel}
      title={pressLabel}
      // Negative margin so the tap target grows without moving the number:
      // this sits in a row measured to the pixel against the next stop's name.
      className={`-mx-1.5 -my-1 shrink-0 rounded-lg px-1.5 py-1 text-left transition-colors hover:bg-white/10 active:bg-white/15 ${
        dim ? "opacity-60" : ""
      }`}
    >
      {body}
    </button>
  )
}
