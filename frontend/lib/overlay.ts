"use client"

/**
 * Keeping an overlay mounted long enough to leave.
 *
 * React unmounts on the same render that flips the flag, so a conditionally
 * rendered dialog has no exit: it is in the tree, and then it is not. That is
 * why both overlays in this app used to vanish instantly no matter how they
 * arrived. This holds the element in the tree for the length of its exit and
 * marks it `leaving` while it is there, so the CSS has something to animate
 * against.
 *
 * Only the EXIT needs JavaScript. The entrance is `@starting-style` in
 * `globals.css` — deliberately, because the React way of doing it (mount in the
 * closed style, then flip on the next animation frame) is the pattern
 * `TripsDrawer` documents as broken: nested `requestAnimationFrame` callbacks
 * never run in a tab that is not being painted, so the dialog opens in its
 * "before" state and stays there until you look at the tab. The browser's own
 * first-paint style has no such hole.
 *
 * `exitMs` must match the CSS. It is passed rather than read back off the
 * element because the two overlays leave at different speeds, and a
 * `transitionend` listener would need a fallback timer anyway for the case
 * where the transition is dropped entirely — which is exactly what
 * `prefers-reduced-motion` does.
 */

import { useEffect, useState } from "react"

export function useOverlayPresence(open: boolean, exitMs: number) {
  const [present, setPresent] = useState(open)

  useEffect(() => {
    if (open) {
      setPresent(true)
      return
    }
    const t = window.setTimeout(() => setPresent(false), exitMs)
    return () => window.clearTimeout(t)
  }, [open, exitMs])

  // `leaving` is derived, not stored: it is true exactly while the element is
  // still mounted and no longer wanted. A second piece of state here would be
  // one render behind on the frame that matters.
  return { present, leaving: present && !open }
}
