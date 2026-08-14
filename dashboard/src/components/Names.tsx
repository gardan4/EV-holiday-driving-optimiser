import { Bars, Empty, Panel } from "./ui"
import { group, pct } from "../lib/format"
import type { Names as NamesData } from "../lib/api"

/**
 * Usernames — whether the trips drawer was worth building.
 *
 * The lead number is adoption, not the headcount: "eleven names" says nothing
 * about a feature whose purpose is stopping people replan a journey they
 * already planned, while "what share of trips get published under a name"
 * says exactly that.
 *
 * Two clocks share this row, and each panel names its own. Claims, releases
 * and adoption are the window; live, dormant, returning and the spread are
 * all-time — a rare event scoped to seven days reads zero on a quiet week and
 * looks like a dead feature rather than a small one.
 *
 * Nothing here is clickable, which is deliberate. Every other panel on this
 * console drills down; this one bottoms out, because the next question after
 * "how many names" is "which names", and that is a list of people.
 */
export function Names({ names }: { names: NamesData }) {
  const untouched = names.live === 0 && names.claimed === 0

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Panel
        title="trips published under a name"
        hint="The adoption number — this window's trips, from the trips table."
      >
        {names.trips ? (
          <>
            <p className="figure text-[28px] leading-none" style={{ color: "var(--color-s-mint)" }}>
              {pct(names.named_share, 1)}
            </p>
            <p className="mt-2 text-[12.5px] text-ink-mute">
              <span className="figure">{group(names.named_trips)}</span> of{" "}
              <span className="figure">{group(names.trips)}</span> trips, by{" "}
              <span className="figure">{group(names.active)}</span> name
              {names.active === 1 ? "" : "s"}. Counted from the trips table, so an
              ad blocker cannot move it.
            </p>
          </>
        ) : (
          <Empty>No trips planned in this window.</Empty>
        )}
      </Panel>

      <Panel title="names" hint="Live and dormant are all-time; claimed and released are this window.">
        {untouched ? (
          <Empty>Nobody has claimed a username yet.</Empty>
        ) : (
          <>
            <p className="figure text-[28px] leading-none">{group(names.live)}</p>
            <p className="mt-2 text-[12.5px] text-ink-mute">
              live, of which <span className="figure">{group(names.dormant)}</span> hold
              nothing. <span className="figure">{group(names.claimed)}</span> claimed and{" "}
              <span className="figure">{group(names.released)}</span> released in this window
              {/* Releasing deletes the profile row, so the event pair is the
                  only surviving evidence that a name ever existed. */}
              {" "}— a released name leaves no row, so those two are the whole record of churn.
            </p>
            {names.with_trips > 0 && (
              <p className="mt-2 text-[12.5px] text-ink-mute">
                <span className="figure">{group(names.returning)}</span> of{" "}
                <span className="figure">{group(names.with_trips)}</span> names holding trips (
                {pct(names.return_rate, 0)}) came back at least a day later. Against names
                that hold trips, never against every name — one claimed yesterday has not
                had the chance.
              </p>
            )}
          </>
        )}
      </Panel>

      <Panel
        title="trips per name"
        hint={`All-time. Panel opened ${group(names.drawer_opens)}× in this window, public lists viewed ${group(names.list_views)}×.`}
      >
        <Bars rows={names.spread.filter((b) => b.value > 0)} empty="No named trips yet." />
      </Panel>
    </div>
  )
}
