import type { LiveEvent } from "../lib/useStream"
import { clock } from "../lib/format"

const TONE: Record<string, string> = {
  page_view: "#7d8ca0",
  plan_submitted: "#5b8ad4",
  trip_planned: "#43c389",
  drive_started: "#9b7ec0",
  maps_route_opened: "#5b8ad4",
  plan_failed: "#d9534f",
  trips_opened: "#7d8ca0",
  profile_claimed: "#43c389",
  profile_released: "#c99a3f",
}

const VERB: Record<string, string> = {
  page_view: "opened",
  plan_submitted: "asked for a plan",
  trip_planned: "got a plan",
  drive_started: "started driving",
  maps_route_opened: "sent the route to Maps",
  plan_failed: "planning failed",
  // Phrased without a name, because there is none in the row — the claim
  // events carry a count and the daily pseudonym, nothing else.
  trips_opened: "opened their trips",
  profile_claimed: "claimed a username",
  profile_released: "released a username",
}

/** Events as they land.
 *
 * Every field shown is a column that already exists on `app_events` — a route
 * pattern, a bucketed device, a two-letter country. Neither pseudonym is in
 * the stream payload at all, so this cannot become a live feed of individual
 * people even for the one person allowed to look at it.
 */
export function Ticker({ events }: { events: LiveEvent[] }) {
  if (!events.length) {
    return (
      <p className="text-[12.5px] text-ink-mute">
        Listening. Events appear here the moment they are recorded.
      </p>
    )
  }

  return (
    <ul className="flex max-h-[420px] flex-col gap-0 overflow-y-auto">
      {events.map((e, i) => (
        <li
          key={`${e.at}-${i}`}
          className={`flex items-baseline gap-2 border-b border-deck-line-soft py-1.5 text-[12px] last:border-0 ${
            i === 0 ? "rise" : ""
          }`}
        >
          <span className="figure w-16 shrink-0 text-ink-mute">{clock(e.at)}</span>
          <span
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ background: TONE[e.name] ?? "#7d8ca0" }}
          />
          <span className="truncate">
            <span style={{ color: TONE[e.name] ?? "var(--color-ink-soft)" }}>
              {VERB[e.name] ?? e.name}
            </span>
            {e.path && <span className="text-ink-mute"> {e.path}</span>}
            {e.reason && <span style={{ color: "#d9534f" }}> · {e.reason}</span>}
          </span>
          <span className="ml-auto shrink-0 text-[11px] text-ink-mute">
            {[e.country, e.device, e.campaign].filter(Boolean).join(" · ")}
          </span>
        </li>
      ))}
    </ul>
  )
}
