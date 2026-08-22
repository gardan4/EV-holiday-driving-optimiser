"use client"

import { useSyncExternalStore } from "react"

const subscribe = () => () => {}

/**
 * False during the server render and the first client render, true afterwards.
 *
 * For anything whose correct value depends on WHERE THE READER IS, which the
 * server cannot know. The drive screen's clocks are the case this exists for:
 * they count from `run.started_at`, an instant the server recorded in UTC, and
 * rendering it as a wall clock needs the viewer's timezone. The container that
 * server-renders the page runs in UTC, so it produced 18:58 while a phone in
 * CEST produced the correct 20:58 — a hydration mismatch, a React error in the
 * console, and a visible flash of the wrong time.
 *
 * Worth being precise about why this appeared only once the clocks were FIXED.
 * The old code parsed the naive UTC timestamp as local and formatted it as
 * local, which is zone-invariant in its digits: both sides produced the same
 * wrong answer, so they matched. Correctness is what made the two sides differ.
 *
 * `useSyncExternalStore` rather than `useState` + `useEffect`, because it says
 * exactly this — one snapshot for the server, another for the client — without
 * a set-state-in-effect that lints as a cascading render.
 *
 * Same rule `DepartureField` already follows for "today": if it depends on the
 * reader's clock or calendar, resolve it after mount and render a placeholder
 * until it lands.
 */
export function useMounted(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false
  )
}
