import { useEffect, useRef, useState } from "react"

/** The live layer: `EventSource` over the dashboard role's SSE tail.
 *
 * `EventSource` is the right tool rather than a WebSocket because the traffic
 * is one-directional and it reconnects by itself, replaying from
 * `Last-Event-ID` — which the server honours, so a dropped connection leaves
 * no hole in the ticker.
 *
 * The ticker is capped and the counters are a single object, so a launch spike
 * cannot grow this state without bound: at a thousand events a minute the list
 * still holds the last `TICKER_MAX` and nothing else accumulates.
 */

export type LiveEvent = {
  at: string
  name: string
  path: string | null
  country: string | null
  device: string | null
  campaign: string | null
  reason: string | null
}

export type Counters = {
  events: number
  visitors: number
  clients: number
  page_views: number
  plans_submitted: number
  trips_planned: number
  drives_started: number
  plan_failures: number
}

export type StreamState = {
  status: "connecting" | "live" | "retrying"
  events: LiveEvent[]
  counters: Counters | null
  /** Bumped on every arrival, so panels can flash without diffing the list. */
  beat: number
}

const TICKER_MAX = 60

export function useStream(enabled: boolean): StreamState {
  const [state, setState] = useState<StreamState>({
    status: "connecting",
    events: [],
    counters: null,
    beat: 0,
  })
  const source = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!enabled) return
    const es = new EventSource("/api/stream", { withCredentials: true })
    source.current = es

    es.addEventListener("open", () =>
      setState((s) => ({ ...s, status: "live" }))
    )
    es.addEventListener("event", (e) => {
      const data = JSON.parse((e as MessageEvent).data) as LiveEvent
      setState((s) => ({
        ...s,
        status: "live",
        beat: s.beat + 1,
        events: [data, ...s.events].slice(0, TICKER_MAX),
      }))
    })
    es.addEventListener("counters", (e) => {
      const data = JSON.parse((e as MessageEvent).data) as Counters
      setState((s) => ({ ...s, status: "live", counters: data }))
    })
    // `EventSource` retries on its own; this only reflects that in the UI so a
    // silent dashboard is distinguishable from a quiet one.
    es.addEventListener("error", () =>
      setState((s) => ({ ...s, status: "retrying" }))
    )

    return () => {
      es.close()
      source.current = null
    }
  }, [enabled])

  return state
}
