import { useCallback, useLayoutEffect, useRef, useState } from "react"

/**
 * Changes a selection without letting the page move under the reader.
 *
 * Switching cruise speed can swap a 4-stop itinerary for a 9-stop one, which
 * changes the document height by several hundred pixels. Nothing above the
 * chart moves, but a shorter page forces the browser to clamp scrollTop, so a
 * reader scrolled past the chart gets yanked upwards the moment they click.
 *
 * Records where the anchored element sits in the viewport, applies the change,
 * then scrolls by the delta so that element stays visually still.
 */
export function useAnchoredSelection<T>(apply: (value: T) => void) {
  const anchorRef = useRef<HTMLElement | null>(null)
  const pendingTop = useRef<number | null>(null)
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
    if (Math.abs(delta) > 0.5) window.scrollBy({ top: delta, behavior: "instant" })
  })

  return { anchorRef, select }
}
