import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"

/**
 * Changes a selection without letting the page move under the reader.
 *
 * Switching cruise speed can swap a 7-stop itinerary for a 3-stop one, which
 * changes the document height by several hundred pixels. Nothing above the
 * chart moves, but a shorter page forces the browser to clamp scrollTop, so a
 * reader scrolled past the chart gets yanked upwards the moment they click.
 *
 * Records where the anchored element sits in the viewport, applies the change,
 * then scrolls by the delta so that element stays visually still.
 *
 * Restoring the scroll position is not enough on its own: near the bottom of
 * the page the position we want to restore no longer exists, because the
 * document just got shorter than it was. So we also hold the document open
 * with temporary bottom padding, and drop it again the moment the reader
 * scrolls off the reserved tail — no permanent dead space, no yank.
 */
export function useAnchoredSelection<T>(apply: (value: T) => void) {
  const anchorRef = useRef<HTMLElement | null>(null)
  const pendingTop = useRef<number | null>(null)
  const reserved = useRef(0)
  const [, bump] = useState(0)

  const select = useCallback(
    (value: T) => {
      pendingTop.current = anchorRef.current?.getBoundingClientRect().top ?? null
      apply(value)
      bump((n) => n + 1) // guarantees the layout effect runs even if `value` is unchanged
    },
    [apply]
  )

  useLayoutEffect(() => {
    const before = pendingTop.current
    pendingTop.current = null
    if (before == null || !anchorRef.current) return
    const after = anchorRef.current.getBoundingClientRect().top
    const delta = after - before
    if (Math.abs(delta) <= 0.5) return

    // The browser has already clamped scrollY to the shorter document, so
    // `delta` is exactly the yank the reader would otherwise see.
    const want = window.scrollY + delta
    const overshoot = want - (document.documentElement.scrollHeight - window.innerHeight)
    if (overshoot > 0) {
      reserved.current += overshoot
      document.body.style.paddingBottom = `${reserved.current}px`
    }
    window.scrollTo({ top: want, behavior: "instant" })
  })

  // Reclaim the reserved tail as soon as the reader no longer stands on it.
  // Removing padding below the viewport moves nothing they can see.
  useEffect(() => {
    const onScroll = () => {
      if (reserved.current === 0) return
      const withoutReserve =
        document.documentElement.scrollHeight - reserved.current - window.innerHeight
      if (window.scrollY <= withoutReserve) {
        reserved.current = 0
        document.body.style.paddingBottom = ""
      }
    }
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => {
      window.removeEventListener("scroll", onScroll)
      if (reserved.current !== 0) {
        reserved.current = 0
        document.body.style.paddingBottom = ""
      }
    }
  }, [])

  return { anchorRef, select }
}
