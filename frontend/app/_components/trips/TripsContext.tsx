"use client"

/**
 * The trips panel's state, hoisted out of the panel.
 *
 * It moved here because the way IN moved. The panel used to own a tab welded to
 * its own left edge — an unlabelled translucent icon halfway down the viewport,
 * which is a place nobody looks and which existed on exactly the two pages that
 * mounted the drawer. The entry point is now a button in `AppHeader`, so the
 * trigger and the panel sit in different trees and need somewhere to agree.
 *
 * Mounted from `providers.tsx`, which means the panel is on every page rather
 * than on the two that remembered to render it. It is still an overlay and
 * still renders nothing at all until `mounted` — the username lives in
 * localStorage, which the server cannot see, so the first client render has to
 * match the server's "nothing yet" and correct itself a frame later.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { usePathname } from "next/navigation"
import {
  forgetUsername,
  setStoredTripCount,
  setStoredUsername,
  storedTripCount,
  storedUsername,
} from "@/lib/account"
import { getUserTrips, type TripSummary } from "@/lib/client"
import { track } from "@/lib/analytics"

const OPEN_KEY = "evtrip.drawer"

/** How long the container transform runs. Lives here because three components
 *  time themselves against it: the panel's shape, the contents crossfading
 *  inside it, and the button waiting to come back. */
export const MORPH_MS = 460

/** Where on screen the thing that opened the panel is. Plain numbers rather
 *  than the `DOMRect`, which is live and would be read long after the click. */
export interface OriginRect {
  top: number
  left: number
  width: number
  height: number
}

interface TripsValue {
  /** False until the first effect has run. Everything visible is gated on it. */
  mounted: boolean
  open: boolean
  /** `origin` is the button's rectangle at the moment it was pressed. The panel
   *  grows out of it, so it has to be measured by whatever was clicked — the
   *  header chip and the landing page's floating one are in different corners,
   *  and neither is something the panel can find on its own. */
  toggle: (next: boolean, origin?: OriginRect | null) => void
  origin: OriginRect | null
  /** Whether this open/close is the container transform rather than the slide.
   *  Published rather than decided inside the panel because the BUTTON has to
   *  act on it in the same render — it hides itself instantly for the morph
   *  (the panel is carrying a copy of its face) and not at all for the slide. */
  morphing: boolean
  username: string | null
  /** The list, or null when it has not been fetched (or there is no name). */
  trips: TripSummary[] | null
  loading: boolean
  /** What the button shows. The real length once the list is in hand, the
   *  cached one before that, and null when there is nothing to count. */
  count: number | null
  /** A name was claimed here, or adopted from another device's code. */
  adopt: (name: string) => void
  release: () => void
  dropTrip: (id: string) => void
  /** A trip was just planned by this browser. The server stamps it with the
   *  owner hash whenever the secret already holds a name, so under a name this
   *  is a certainty rather than a guess — and it is the one moment the count
   *  changes without the panel being open to notice. */
  noteTripPlanned: () => void
}

const Ctx = createContext<TripsValue | null>(null)

export function useTrips(): TripsValue {
  const value = useContext(Ctx)
  if (!value) throw new Error("useTrips must be used inside <TripsProvider>")
  return value
}

export function TripsProvider({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false)
  const [open, setOpen] = useState(false)
  const [origin, setOrigin] = useState<OriginRect | null>(null)
  const [username, setUsername] = useState<string | null>(null)
  const [trips, setTrips] = useState<TripSummary[] | null>(null)
  const [cachedCount, setCachedCount] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setMounted(true)
    setUsername(storedUsername())
    setCachedCount(storedTripCount())
    try {
      setOpen(window.localStorage.getItem(OPEN_KEY) === "1")
    } catch {
      /* a browser that will not remember the panel state just starts closed */
    }
  }, [])

  const toggle = useCallback((next: boolean, from?: OriginRect | null) => {
    // Kept when closing, not cleared: the panel shrinks back into the same
    // rectangle it came out of, and a null here mid-close drops it to the
    // fallback slide halfway through the animation.
    if (from) setOrigin(from)
    setOpen(next)
    // Counted on the deliberate open only, never on the restore from
    // localStorage above and never on close. The question is how often somebody
    // goes looking for a trip they already planned, and a panel that was left
    // open answers it once per page load rather than once per intention.
    // `track` honours the counting opt-out and never throws.
    if (next) track("trips_opened")
    try {
      window.localStorage.setItem(OPEN_KEY, next ? "1" : "0")
    } catch {
      /* not worth a thought */
    }
  }, [])

  // Whether the container transform is available, re-read on every open and
  // close rather than from a `change` listener on the media query: this decides
  // whether an animation happens at all, and an event that does not arrive is
  // an effect that silently stops existing until the next reload. Corrected in
  // a LAYOUT effect, so React re-renders before the browser paints and the
  // panel's own morph still starts from the right frame.
  //
  // Motion is the only condition. It was gated on `min-width: 1024px` first,
  // on the theory that a phone wants a sheet — but the panel opens from the
  // same chip in the same corner on a phone, and a small control growing into
  // a full screen is the one transition phones do best. What the small screen
  // changes is the panel's SURFACE, not this: see the background classes.
  const [canMorph, setCanMorph] = useState(false)
  useLayoutEffect(() => {
    const fresh = !window.matchMedia("(prefers-reduced-motion: reduce)").matches
    setCanMorph((cur) => (cur === fresh ? cur : fresh))
  }, [open])

  // Going somewhere closes it. The panel is a list of links, so the ordinary
  // way out of it is to follow one — and it used to stay open on top of the
  // trip it had just sent you to. It survives a refresh, which is what the
  // stored flag was for; it does not follow you around the site.
  const pathname = usePathname()
  const lastPath = useRef(pathname)
  useEffect(() => {
    if (pathname === lastPath.current) return
    lastPath.current = pathname
    setOpen(false)
    try {
      window.localStorage.setItem(OPEN_KEY, "0")
    } catch {
      /* nothing to remember, nothing to do */
    }
  }, [pathname])

  const refresh = useCallback(async (name: string) => {
    setLoading(true)
    try {
      const data = await getUserTrips(name)
      setTrips(data.trips)
      setCachedCount(data.trips.length)
      setStoredTripCount(data.trips.length)
    } catch {
      // A released name, or a browser that remembers one the server does not.
      // Falling back to the claim state is better than an error nobody can act
      // on — and `forgetUsername` keeps the secret, so claiming again is one
      // step rather than a new identity.
      setTrips(null)
      setUsername(null)
      setCachedCount(null)
      forgetUsername()
    } finally {
      setLoading(false)
    }
  }, [])

  // Refetch whenever the panel is opened: the common path into this drawer is
  // "I just planned a trip", and a list cached from before that would be
  // missing the one trip the person came looking for. It is also what corrects
  // the cached badge, which is why the badge is allowed to be approximate.
  useEffect(() => {
    if (open && username) void refresh(username)
  }, [open, username, refresh])

  const adopt = useCallback((name: string) => {
    setUsername(name)
    setStoredUsername(name)
    // Not the previous name's list, and not a count carried over from it.
    setTrips(null)
    setCachedCount(null)
    setStoredTripCount(null)
  }, [])

  const release = useCallback(() => {
    setUsername(null)
    setTrips(null)
    setCachedCount(null)
    forgetUsername()
  }, [])

  const dropTrip = useCallback((id: string) => {
    setTrips((cur) => {
      const next = (cur ?? []).filter((t) => t.id !== id)
      setCachedCount(next.length)
      setStoredTripCount(next.length)
      return next
    })
  }, [])

  const noteTripPlanned = useCallback(() => {
    if (!storedUsername()) return
    setCachedCount((cur) => {
      const next = (cur ?? 0) + 1
      setStoredTripCount(next)
      return next
    })
    // The list in hand is now one short. Dropping it means the next open
    // fetches rather than rendering a list missing the trip just planned.
    setTrips(null)
  }, [])

  const value = useMemo<TripsValue>(
    () => ({
      mounted,
      open,
      toggle,
      origin,
      morphing: canMorph && origin != null,
      username,
      trips,
      loading,
      count: username === null ? null : (trips?.length ?? cachedCount),
      adopt,
      release,
      dropTrip,
      noteTripPlanned,
    }),
    [
      mounted,
      open,
      toggle,
      origin,
      canMorph,
      username,
      trips,
      loading,
      cachedCount,
      adopt,
      release,
      dropTrip,
      noteTripPlanned,
    ]
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
