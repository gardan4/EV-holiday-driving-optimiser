"use client"

/**
 * "Counting on" in the footer, linking to the switch that changes it.
 *
 * The control itself lives on the privacy page, in the section that explains
 * what is being counted, because a toggle with no explanation next to it is
 * worse than no toggle. This is here so it is reachable from every page rather
 * than only from a notice people have to think to open.
 *
 * A dot and a chip rather than an underlined word. It sat in the footer's link
 * row looking exactly like navigation, which is wrong twice: it is a status
 * before it is a destination, and the state is the part worth reading at a
 * glance.
 *
 * Renders nothing until mounted: the state comes from localStorage, so the
 * server has no way to know it, and guessing would break hydration.
 */

import Link from "next/link"
import { useEffect, useState } from "react"
import { countingState, type CountingState } from "@/lib/analytics"

export default function CountingLink() {
  const [state, setState] = useState<CountingState | null>(null)

  useEffect(() => {
    setState(countingState())
  }, [])

  if (state === null) return null

  const on = state === "on"

  return (
    <Link
      href="/privacy#counting"
      title={
        on
          ? "Usage counting is on. Change it on the privacy page."
          : "Usage counting is off."
      }
      className="inline-flex w-fit shrink-0 items-center gap-2 rounded-full border border-ink-200 px-3 py-1 text-xs text-ink-500 transition-colors hover:border-ink-300 hover:text-ink-700"
    >
      <span
        aria-hidden
        className={`h-1.5 w-1.5 rounded-full ${on ? "bg-brand-500" : "bg-ink-300"}`}
      />
      Counting {on ? "on" : "off"}
    </Link>
  )
}
