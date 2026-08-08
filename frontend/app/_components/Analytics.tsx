"use client"

import { usePathname } from "next/navigation"
import { useEffect, useRef } from "react"
import { track } from "@/lib/analytics"

/**
 * Fires one `page_view` per navigation.
 *
 * `usePathname` rather than `useSearchParams` on purpose: reading search params
 * would opt the whole tree into client-side rendering (Next requires a Suspense
 * boundary for it), and the query string is the one part of the URL we have
 * decided not to look at anyway.
 *
 * The ref guards React 19's development double-invoke and the re-renders that
 * come from `ResultsView` changing state — without it a single visit to a trip
 * page reports several views.
 */
export default function Analytics() {
  const pathname = usePathname()
  const last = useRef<string | null>(null)

  useEffect(() => {
    if (!pathname || last.current === pathname) return
    last.current = pathname
    track("page_view", pathname)
  }, [pathname])

  return null
}
