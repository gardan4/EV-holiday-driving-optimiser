"use client"

/**
 * One line, once, on the results page: there is a list, and this is how you get
 * one.
 *
 * The header button is where the feature lives; this is where it explains
 * itself. A results page is the only moment "where does this go?" is a live
 * question — the trip exists, its share link is the only handle it has, and the
 * person is about to close the tab.
 *
 * Three things it must not do, and each one is a constraint on the copy:
 *
 *  - **It must not claim this trip is saved.** Claiming a name is the consent
 *    moment, and `_plan` stamps a trip only when the secret ALREADY holds one —
 *    a claim never publishes history retroactively. So the offer is "from the
 *    next one", which is what the claim form says too.
 *  - **It must not appear to people who already have a name.** For them the
 *    trip IS on the list and the line would be noise.
 *  - **It must appear once.** Dismissing it, or opening the panel from it,
 *    ends it for good; it is a signpost, not a campaign. The flag is per
 *    browser, alongside the rest of this feature's state.
 */

import { useEffect, useState } from "react"
import { Bookmark, X } from "lucide-react"
import { useTrips } from "./TripsContext"

const SEEN_KEY = "evtrip.trips.nudged"

export default function SaveTripNudge() {
  const { mounted, username, toggle } = useTrips()
  const [seen, setSeen] = useState(true)

  useEffect(() => {
    try {
      setSeen(window.localStorage.getItem(SEEN_KEY) === "1")
    } catch {
      // A browser that cannot remember would show this on every trip page.
      // Once is a signpost; every time is a nag, so silence is the safe side.
      setSeen(true)
    }
  }, [])

  function dismiss() {
    setSeen(true)
    try {
      window.localStorage.setItem(SEEN_KEY, "1")
    } catch {
      /* it is hidden for this page either way */
    }
  }

  if (!mounted || seen || username !== null) return null

  return (
    <div className="mt-4 flex items-center gap-3 rounded-xl border border-brand-200 bg-brand-50 px-3 py-2.5 text-sm text-brand-800">
      <Bookmark className="h-4 w-4 shrink-0 text-brand-600" />
      <p className="min-w-0 flex-1">
        This trip lives at its link.{" "}
        <button
          type="button"
          onClick={() => {
            dismiss()
            toggle(true)
          }}
          className="font-semibold underline underline-offset-2 hover:text-brand-900"
        >
          Pick a username
        </button>{" "}
        and the ones you plan from now on are kept in one place.
      </p>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss"
        className="shrink-0 rounded-lg p-1 text-brand-600 transition hover:bg-brand-100 hover:text-brand-900"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
