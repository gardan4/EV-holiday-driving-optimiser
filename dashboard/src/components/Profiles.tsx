import type { Journeys } from "../lib/api"
import { group, pct } from "../lib/format"
import { Bars, Empty, Panel } from "./ui"

/** The opt-in username, and whether it does anything.
 *
 * Every number here comes from `profiles` and `trips.owner_hash` except the two
 * that cannot: releases and list views are events, so they start the day
 * counting shipped, expire at 90 days, and miss anyone who opted out.
 *
 * Two things belong on the panel rather than in a comment, because they change
 * how the numbers should be read:
 *
 * * **Stock and flow are mixed on purpose.** "names right now" ignores the date
 *   range — it is the state of the table — while everything beside it is the
 *   window. Labelled, because a stock figure that does not move when the window
 *   narrows reads as a broken filter otherwise.
 * * **Published is a share, not a count.** The count goes up whenever the app
 *   gets busier, which is not the same as the feature being used.
 */
export function Profiles({ journeys }: { journeys: Journeys }) {
  const p = journeys.profiles

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Panel
        title="usernames"
        hint="Names are all-time; claims and releases are the window."
      >
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <Figure label="names right now" value={group(p.live)} tone="var(--color-s-mint)" />
          <Figure label="publish something" value={pct(p.active_rate, 0)} />
          <Figure label="claimed in window" value={group(p.claimed)} />
          <Figure
            label="released in window"
            value={group(p.released)}
            tone={p.released ? "var(--color-s-rose)" : undefined}
          />
        </div>
        <p className="mt-3 text-[11.5px] text-ink-mute">
          <span className="figure">{group(p.empty)}</span> hold no trips at all — somebody
          opted in and then planned nothing, which no page-view count shows. Claims count
          names that still exist, so one claimed and released inside the window appears only
          in the release.
        </p>
      </Panel>

      <Panel
        title="trips planned under a name"
        hint="A share, not a count: the count rises whenever the app gets busier."
      >
        <p className="figure text-[28px] leading-none" style={{ color: "var(--color-s-drive)" }}>
          {pct(p.publish_rate, 1)}
        </p>
        <p className="mt-2 text-[12.5px] text-ink-mute">
          <span className="figure">{group(p.published)}</span> of{" "}
          <span className="figure">{group(p.trips)}</span> trips in this window carried a
          username. A trip is stamped only when the browser already held a name, so this can
          never include anything planned before somebody opted in.
        </p>
        <p className="mt-3 text-[11.5px] text-ink-mute">
          Public lists opened: <span className="figure">{group(p.list_views)}</span>. Handing
          somebody a name and having it work is the whole feature — from events, so it
          undercounts by everyone who opted out of counting.
        </p>
      </Panel>

      <Panel
        title="trips per name"
        hint="Same buckets as trips per planner beside it, from the same function."
      >
        {p.per_name.length ? (
          <Bars rows={p.per_name} empty="No published trips in this window." />
        ) : (
          <Empty>No trips published under a name in this window.</Empty>
        )}
      </Panel>
    </div>
  )
}

function Figure({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <p className="panel-title !text-[10px]">{label}</p>
      <p className="figure mt-1 text-[20px] leading-none" style={{ color: tone }}>
        {value}
      </p>
    </div>
  )
}
