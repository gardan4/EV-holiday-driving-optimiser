"use client"

/**
 * The way into the trips panel.
 *
 * It replaces a tab on the left edge of the viewport that was, deliberately,
 * almost invisible: no label until hover, 55% white over a hero, and mounted on
 * two pages. The reasoning for the translucency was sound on the trip page — it
 * sat over the 3D journey scene, where an opaque pill reads as damage to the
 * picture — but the landing page was paying that cost for nothing, and a
 * left-edge tab is a developer-tool idiom in the first place. People look at the
 * top right for their own things.
 *
 * The control has two jobs and they are not the same job, so it has two states:
 *
 *  - **Before a username it has to sell the feature.** It is tinted, it says
 *    "Your trips", and it is the only mint thing in the header besides the
 *    wordmark. Somebody who has never opened it does not know a list exists.
 *  - **After, it has to be findable.** It becomes the handle — `@name` — with
 *    the number of trips beside it. A count is a reason to click; a route glyph
 *    is not. It comes from `storedTripCount`, so it is on the first paint
 *    without a fetch, and the panel corrects it on open.
 */

import { useRef } from "react"
import { Route, User } from "lucide-react"
import { MORPH_MS, useTrips } from "./TripsContext"

/** The chip's surface, minus its position and its behaviour.
 *
 *  Shared with the panel, which draws a copy of it inside the shape it opens
 *  with. That copy is the whole reason the morph reads as one object: the first
 *  frame of the panel has to BE the chip, down to the tint and the badge, or
 *  what you see is a button vanishing and a blank pill growing where it was.
 *  One definition, so the copy cannot drift from the original. */
export const chipSkin = (claimed: boolean) =>
  `inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-medium ${
    claimed
      ? "border-ink-200 bg-white text-ink-700"
      : "border-brand-200 bg-brand-50 text-brand-700"
  }`

export function ChipFace({
  claimed,
  label,
  count,
}: {
  claimed: boolean
  label: string
  count: number | null
}) {
  return (
    <>
      {claimed ? (
        <User className="h-4 w-4 shrink-0" />
      ) : (
        <Route className="h-4 w-4 shrink-0" />
      )}
      {/* A phone is where the header runs out of room first — the wordmark
          beside this is wide — so the mobile label is a shorter WORD rather
          than a truncation of the long one: "Trips", never "Your tri…". A
          username does not survive that treatment at all, so once there is a
          count it carries the chip on its own and the name waits for a screen
          it fits on. */}
      {!(claimed && count) && <span className="sm:hidden">Trips</span>}
      <span className="hidden truncate sm:inline">{label}</span>
      {count != null && count > 0 && (
        <span
          className={`shrink-0 rounded-full px-1.5 text-xs font-semibold tabular-nums ${
            claimed ? "bg-brand-50 text-brand-700" : "bg-brand-500 text-white"
          }`}
        >
          {count}
        </span>
      )}
    </>
  )
}

export default function TripsButton({
  /** The landing page has no header to sit in, so there it floats in the same
   *  corner instead. Same chip either way — two entry points that look
   *  different are two things to learn. */
  floating = false,
}: {
  floating?: boolean
}) {
  const { mounted, open, toggle, morphing, username, count } = useTrips()
  const ref = useRef<HTMLButtonElement>(null)

  // Nothing server-rendered: the label depends on localStorage. Rendering
  // "Your trips" and swapping it for a username a frame later is a hydration
  // mismatch and a flicker in the one place the eye is being drawn.
  if (!mounted) return null

  const claimed = username !== null
  const label = claimed ? `@${username}` : "Your trips"

  return (
    <button
      ref={ref}
      type="button"
      onClick={() => {
        const r = ref.current?.getBoundingClientRect()
        toggle(
          !open,
          r ? { top: r.top, left: r.left, width: r.width, height: r.height } : null
        )
      }}
      aria-expanded={open}
      aria-label={claimed ? `Your trips, ${username}` : "Your trips"}
      // For the morph it goes out INSTANTLY, not on a fade: the panel's first
      // frame is a pixel copy of this chip in this exact place, so a fade would
      // be two identical chips dissolving through each other, and any lag shows
      // as a double image. Coming back it waits for the shape to finish
      // shrinking (the copy is on screen until then) and then appears under the
      // cursor with no crossfade at all. For the slide there is nothing to hand
      // over to, so it simply stays.
      style={
        morphing ? { transitionDelay: open ? "0ms" : `${MORPH_MS - 40}ms` } : undefined
      }
      className={`${chipSkin(claimed)} max-w-[9.5rem] shrink-0 transition-[color,background-color,border-color,opacity] duration-150 sm:max-w-[14rem] ${
        claimed
          ? "hover:border-brand-300 hover:text-brand-700"
          : "hover:border-brand-300 hover:bg-brand-100"
      } ${morphing && open ? "opacity-0 duration-0" : "opacity-100"} ${
        floating
          ? "fixed right-4 top-4 z-30 shadow-sm shadow-ink-900/5 backdrop-blur sm:right-6"
          : ""
      }`}
    >
      <ChipFace claimed={claimed} label={label} count={count} />
    </button>
  )
}
