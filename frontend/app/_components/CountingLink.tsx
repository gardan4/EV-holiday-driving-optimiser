"use client"

/**
 * "Counting: on" in the footer, linking to the switch that changes it.
 *
 * The control itself lives on the privacy page, in the section that explains
 * what is being counted, because a toggle with no explanation next to it is
 * worse than no toggle. This is here so it is reachable from every page rather
 * than only from a notice people have to think to open.
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

  const label = state === "on" ? "Counting: on" : "Counting: off"

  return (
    <Link
      href="/privacy#counting"
      className="underline underline-offset-2 hover:text-ink-800"
    >
      {label}
    </Link>
  )
}
