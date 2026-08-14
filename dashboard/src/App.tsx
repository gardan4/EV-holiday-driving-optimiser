import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  api,
  Unauthenticated,
  type Journeys,
  type Meta,
  type Names as NamesData,
  type Overview,
  type Segments,
  type Upstream,
} from "./lib/api"
import { useFilters, type Facet } from "./lib/useFilters"
import { useStream } from "./lib/useStream"
import { compact, group, minutes, pct } from "./lib/format"
import { Bars, Empty, Panel, Tile } from "./components/ui"
import { FilterBar } from "./components/FilterBar"
import { Funnel } from "./components/Funnel"
import { Series } from "./components/Series"
import { Ticker } from "./components/Ticker"
import { Quota } from "./components/Quota"
import { WorldMap } from "./components/WorldMap"
import { Login } from "./components/Login"
import { Upstream as UpstreamPanel } from "./components/UpstreamPanel"
import { Drives } from "./components/Drives"
import { Names } from "./components/Names"

export function App() {
  const [authed, setAuthed] = useState<boolean | null>(null)
  const { filters, toggle, setDays, clear, active } = useFilters()

  const [overview, setOverview] = useState<Overview | null>(null)
  const [segments, setSegments] = useState<Segments | null>(null)
  const [journeys, setJourneys] = useState<Journeys | null>(null)
  const [upstream, setUpstream] = useState<Upstream | null>(null)
  const [names, setNames] = useState<NamesData | null>(null)
  // Fetched once: it describes the system, not the window, so it does not
  // belong in the per-filter reload.
  const [meta, setMeta] = useState<Meta | null>(null)
  const [error, setError] = useState<string | null>(null)

  const live = useStream(authed === true)

  // An hourly x-axis for a one-day window and a daily one beyond it. A 90-day
  // chart at hourly resolution is 2160 bars of noise; a 24-hour chart at daily
  // resolution is one bar.
  const granularity = filters.days <= 1 ? "hour" : "day"

  useEffect(() => {
    api
      .session()
      .then((s) => setAuthed(s.authenticated))
      // A 404 means no STATS_TOKEN is configured — there is no dashboard here
      // and a password box would be a lie.
      .catch(() => setAuthed(false))
  }, [])

  const load = useCallback(async () => {
    if (authed !== true) return

    // allSettled, not all: these are four independent panels, and with `all`
    // one failing endpoint rejects the batch, so every other panel keeps its
    // stale data or sits on "Loading…" forever. On a dashboard that is worse
    // than a visible gap — it looks like the numbers are still coming rather
    // than like something broke.
    const [o, s, j, u, n] = await Promise.allSettled([
      api.overview(filters, granularity),
      api.segments(filters),
      api.journeys(filters),
      api.upstream(Math.max(14, filters.days)),
      api.names(filters),
    ])

    if ([o, s, j, u, n].some((r) => r.status === "rejected" && r.reason instanceof Unauthenticated)) {
      setAuthed(false)
      return
    }

    if (o.status === "fulfilled") setOverview(o.value)
    if (s.status === "fulfilled") setSegments(s.value)
    if (j.status === "fulfilled") setJourneys(j.value)
    if (u.status === "fulfilled") setUpstream(u.value)
    if (n.status === "fulfilled") setNames(n.value)

    const failed = [
      ["overview", o],
      ["segments", s],
      ["journeys", j],
      ["upstream", u],
      ["names", n],
    ].filter(([, r]) => (r as PromiseSettledResult<unknown>).status === "rejected")

    setError(
      failed.length
        ? `${failed.map(([name]) => name).join(", ")} failed to load — the rest of the page is current.`
        : null
    )
  }, [authed, filters, granularity])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (authed !== true) return
    api.meta().then(setMeta).catch(() => setMeta(null))
  }, [authed])

  // Refetch when the stream says something happened, but at most every 10s:
  // the panels are aggregates, and re-running fifteen queries per arriving
  // event would turn a launch spike into a self-inflicted load test.
  const lastRefresh = useRef(0)
  useEffect(() => {
    if (!live.beat) return
    const now = Date.now()
    if (now - lastRefresh.current < 10_000) return
    lastRefresh.current = now
    void load()
  }, [live.beat, load])

  // Fire a pulse along a random arc when a trip is planned. The stream does not
  // carry coordinates — trip_stats is where corridors live and events are not
  // joined to it — so this is honest ambience rather than a claim about which
  // corridor: it says "a trip was planned just now", which is true.
  //
  // Gated on `prefers-reduced-motion` HERE, in JavaScript, and that is not a
  // stylistic choice. The pulse is an SVG SMIL `<animateMotion>` (WorldMap.tsx),
  // and SMIL is not CSS: no media query reaches it, so the `@media
  // (prefers-reduced-motion: reduce)` block in `theme.css` leaves the single
  // most vestibular thing on this console — a dot travelling the width of the
  // world map, on a loop as fast as traffic arrives — running at full strength.
  // Not rendering the element is the only off switch it has.
  const [pulses, setPulses] = useState<number[]>([])
  const arcCount = journeys?.corridors.top.length ?? 0
  const [wantsMotion, setWantsMotion] = useState(true)
  useEffect(() => {
    const q = window.matchMedia("(prefers-reduced-motion: reduce)")
    const read = () => setWantsMotion(!q.matches)
    read()
    // Listened to rather than read once: this console is left open for hours,
    // and the setting can change under it.
    q.addEventListener("change", read)
    return () => q.removeEventListener("change", read)
  }, [])
  useEffect(() => {
    if (!wantsMotion) return
    const latest = live.events[0]
    if (!latest || latest.name !== "trip_planned" || arcCount === 0) return
    const idx = Math.floor(Math.random() * arcCount)
    setPulses((p) => [...p, idx])
    const t = window.setTimeout(() => setPulses((p) => p.slice(1)), 1500)
    return () => window.clearTimeout(t)
  }, [live.beat, live.events, arcCount, wantsMotion])

  const totals = overview?.totals
  const counters = live.counters

  const headline = useMemo(
    () => [
      {
        label: "page views",
        value: totals?.page_views?.value ?? 0,
        change: totals?.page_views?.change ?? null,
      },
      {
        label: "visitor-days",
        value: totals?.visitors?.value ?? 0,
        change: totals?.visitors?.change ?? null,
      },
      {
        label: "trips planned",
        value: totals?.trips_planned?.value ?? 0,
        change: totals?.trips_planned?.change ?? null,
      },
      {
        label: "drives started",
        value: totals?.drives_started?.value ?? 0,
        change: totals?.drives_started?.change ?? null,
      },
    ],
    [totals]
  )

  if (authed === null) {
    return <main className="grid min-h-full place-items-center text-ink-mute">…</main>
  }
  if (!authed) return <Login onIn={() => setAuthed(true)} />

  return (
    <div className="relative z-10 px-4 pb-16 sm:px-6">
      <FilterBar
        filters={filters}
        active={active}
        status={live.status}
        onDays={setDays}
        onClear={clear}
        onDrop={(f: Facet, v: string) => toggle(f, v)}
        onLogout={async () => {
          await api.logout()
          setAuthed(false)
        }}
      />

      <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
        {error && (
          <div className="panel" style={{ borderColor: "var(--color-s-rose)" }}>
            <p className="text-[13px]" style={{ color: "var(--color-s-rose)" }}>
              {error}
            </p>
          </div>
        )}

        {/* Headline tiles. The "today" row underneath comes from the live
            stream rather than the window query, so it ticks between refreshes. */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {headline.map((t) => (
            <Tile key={t.label} label={t.label} value={t.value} change={t.change} />
          ))}
        </div>

        <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
          <Panel
            title="the shape of the window"
            hint={
              granularity === "hour"
                ? "Hourly. Visitors are a daily-rotating pseudonym, so at this resolution they undercount."
                : "Page views with visitors beneath them, on one shared axis."
            }
          >
            {overview ? (
              <Series
                views={overview.series.page_views}
                visitors={overview.series.visitors}
                granularity={granularity}
              />
            ) : (
              <Empty>Loading…</Empty>
            )}
          </Panel>

          <Panel
            title="live"
            hint="Straight off the events table, as rows land."
            right={
              counters ? (
                <span className="figure text-[11px] text-ink-mute">
                  {group(counters.page_views)} views today
                </span>
              ) : null
            }
          >
            <Ticker events={live.events} />
          </Panel>
        </div>

        <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
          <Panel title="what people did" hint="Each stage as a share of the one before it.">
            {overview ? (
              <Funnel stages={overview.funnel} failures={overview.plan_failures} />
            ) : (
              <Empty>Loading…</Empty>
            )}
          </Panel>

          <Panel
            title="free-tier headroom, today"
            hint="Resets at midnight UTC — the unit the ceilings are actually in."
          >
            {overview ? <Quota providers={overview.providers} /> : <Empty>Loading…</Empty>}
          </Panel>
        </div>

        {/* The map. Corridors come from trip_stats, which is coarsened and not
            joined to events — so the facet chips above do not narrow it, and
            the panel says so rather than quietly showing unfiltered data. */}
        <Panel
          title="where people drive"
          hint={
            active.length
              ? "Corridors come from trip_stats, which carries no device or campaign — the chips above do not apply here. Only the date range does."
              : "Endpoints are geohash-4 centres. Colour is charging difficulty, width is volume."
          }
        >
          {journeys ? (
            <WorldMap
              corridors={journeys.corridors.top}
              transitCountries={journeys.shape.transit_countries}
              // Empty until /api/meta lands: better a moment of "nothing is
              // modelled" than a hardcoded list that can go stale.
              modelledCountries={meta?.modelled_countries ?? []}
              defaultCapKph={meta?.default_cap_kph ?? 130}
              pulses={pulses}
            />
          ) : (
            <Empty>Loading…</Empty>
          )}
        </Panel>

        <div className="grid gap-4 lg:grid-cols-3">
          <Panel title="where charging fails them" hint="Ranked by stops per 100 km, not raw stops.">
            {journeys?.corridors.hardest.length ? (
              <ul className="flex flex-col gap-2">
                {journeys.corridors.hardest.slice(0, 8).map((c) => (
                  <li key={c.label} className="flex items-baseline justify-between gap-3 text-[12.5px]">
                    <span className="truncate text-ink-soft" title={c.label}>
                      {c.label}
                    </span>
                    <span className="figure shrink-0 text-ink-mute">
                      {c.stops_per_100km?.toFixed(2) ?? "—"}
                      <span className="text-ink-mute/60"> /100km</span>
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No corridors yet.</Empty>
            )}
          </Panel>

          <Panel
            title="roads we don't model"
            hint="Countries outside the speed-cap table — demand we serve with a 130 guess."
          >
            <p className="mb-3 text-[12.5px] text-ink-mute">
              <span className="figure text-[20px] text-ink">
                {journeys?.corridors.unplaceable_trips ?? 0}
              </span>{" "}
              trips landed outside modelled Europe.
            </p>
            <Bars rows={journeys?.corridors.unmodelled_countries ?? []} empty="All inside Europe." />
          </Panel>

          <Panel title="why planning failed" hint="Recorded server-side, so this counts every failure.">
            <Bars
              rows={segments?.failure_reasons ?? []}
              empty="Nothing failed in this window."
            />
          </Panel>
        </div>

        {/* Drive telemetry — the only evidence of what happens after the plan. */}
        {journeys && <Drives journeys={journeys} />}

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Panel title="country" hint="Needs a trusted proxy setting CF-IPCountry.">
            <Bars
              rows={segments?.countries ?? []}
              active={filters.country ?? null}
              onPick={(v) => toggle("country", v)}
              empty="No country data — TRUSTED_PROXY_HEADERS is off."
            />
          </Panel>
          <Panel title="device">
            <Bars
              rows={segments?.devices ?? []}
              active={filters.device ?? null}
              onPick={(v) => toggle("device", v)}
            />
          </Panel>
          <Panel title="browser">
            <Bars
              rows={segments?.browsers ?? []}
              active={filters.browser ?? null}
              onPick={(v) => toggle("browser", v)}
            />
          </Panel>
          <Panel title="operating system">
            <Bars
              rows={segments?.operating_systems ?? []}
              active={filters.os ?? null}
              onPick={(v) => toggle("os", v)}
            />
          </Panel>
        </div>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Panel title="pages">
            <Bars
              rows={segments?.paths ?? []}
              active={filters.path ?? null}
              onPick={(v) => toggle("path", v)}
            />
          </Panel>
          <Panel title="came from">
            <Bars
              rows={segments?.referrers ?? []}
              active={filters.referrer ?? null}
              onPick={(v) => toggle("referrer", v)}
            />
          </Panel>
          <Panel title="which thread" hint="Path only — the query string is always dropped.">
            <Bars
              rows={segments?.referrer_paths ?? []}
              active={filters.referrer_path ?? null}
              onPick={(v) => toggle("referrer_path", v)}
            />
          </Panel>
          <Panel title="which link worked" hint="?src= tags, replayed across the visit.">
            <Bars
              rows={segments?.campaigns ?? []}
              active={filters.campaign ?? null}
              onPick={(v) => toggle("campaign", v)}
              empty="No tagged links used yet."
            />
          </Panel>
        </div>

        <div className="grid gap-4 lg:grid-cols-3">
          <Panel
            title="did they come back?"
            hint="Seen before this window opened — not merely twice inside it."
          >
            {overview && (
              <>
                <p className="figure text-[28px] leading-none">
                  {pct(overview.retention.rate, 1)}
                </p>
                <p className="mt-2 text-[12.5px] text-ink-mute">
                  <span className="figure">{group(overview.retention.returning)}</span> of{" "}
                  <span className="figure">{group(overview.retention.identified)}</span> browsers
                  that sent a persistent id. Private windows, cleared storage and opt-outs sit
                  outside the denominator, so this under-counts.
                </p>
              </>
            )}
          </Panel>

          <Panel title="trips per planner" hint="By the counting pseudonym, not by username.">
            <Bars rows={journeys?.planners ?? []} empty="No trips yet." />
          </Panel>

          <Panel title="cars" hint="With the average optimum cruise speed each one lands on.">
            {journeys?.cars.length ? (
              <ul className="flex flex-col gap-2">
                {journeys.cars.slice(0, 10).map((c) => (
                  <li key={c.label} className="flex items-baseline justify-between gap-3 text-[12.5px]">
                    <span className="truncate text-ink-soft">{c.label}</span>
                    <span className="figure shrink-0 text-ink-mute">
                      {compact(c.value)}
                      {c.avg_optimum_kph ? ` · ${Math.round(c.avg_optimum_kph)} km/h` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No trips yet.</Empty>
            )}
          </Panel>
        </div>

        {/* Usernames. Reads `profiles` and `trips`, so the facet chips above do
            not narrow it — only the date range does, same as the map. */}
        {names && <Names names={names} />}

        {/* Trip shape — the charge-share number is the product's own thesis. */}
        {journeys && (
          <div className="grid gap-4 lg:grid-cols-3">
            <Panel
              title="how much of the journey is charging"
              hint="Charging minutes over total journey minutes, per trip."
            >
              <p className="figure text-[28px] leading-none" style={{ color: "var(--color-s-charge)" }}>
                {journeys.shape.median_charge_share !== null
                  ? `${journeys.shape.median_charge_share.toFixed(1)}%`
                  : "—"}
              </p>
              <p className="mt-1 mb-3 text-[12px] text-ink-mute">
                median · mean{" "}
                {journeys.shape.mean_charge_share !== null
                  ? `${journeys.shape.mean_charge_share.toFixed(1)}%`
                  : "—"}
              </p>
              <Bars rows={journeys.shape.charge_shares} empty="No trips yet." />
            </Panel>

            <Panel title="how far people go" hint={`Mean ${Math.round(journeys.shape.mean_km)} km.`}>
              <Bars rows={journeys.shape.distances} empty="No trips yet." />
            </Panel>

            <Panel
              title="when they travel"
              hint="Departure month — a holiday app's cycle is annual, so this is the seasonality."
            >
              <Bars rows={journeys.shape.months} empty="No trips yet." />
            </Panel>
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-3">
          <Panel
            title="what speed the planner picks"
            hint="The answer to the question on the front page, in aggregate."
          >
            <Bars
              rows={journeys?.shape.speeds ?? []}
              empty="No trips yet."
              format={(n) => group(n)}
            />
          </Panel>
          <Panel title="countries crossed" hint="Per trip, from the route's country list.">
            <Bars rows={journeys?.shape.transit_countries ?? []} empty="No trips yet." />
          </Panel>
          <Panel
            title="plans that were possible"
            hint="A trip is infeasible when no speed in the sweep gets there."
          >
            {journeys && (
              <>
                <p className="figure text-[28px] leading-none" style={{ color: "var(--color-s-mint)" }}>
                  {pct(journeys.shape.feasible_rate, 1)}
                </p>
                <p className="mt-2 text-[12.5px] text-ink-mute">
                  <span className="figure">{group(journeys.shape.feasible)}</span> of{" "}
                  <span className="figure">{group(journeys.shape.trips)}</span> planned trips.
                  Average distance {Math.round(journeys.shape.mean_km)} km, median drive{" "}
                  {minutes(journeys.drives.median_minutes)}.
                </p>
              </>
            )}
          </Panel>
        </div>

        {upstream && <UpstreamPanel upstream={upstream} />}

        <footer className="mt-2 text-[11.5px] leading-relaxed text-ink-mute">
          Visitors are a pseudonym salted with the date, so it cannot be followed past
          midnight — over a week the number means visitor-days, not people. Corridors are
          geohash-4 (~20 × 25 km) and reach back to the first deploy; everything counted from
          events starts the day counting shipped, and expires at 90 days.
        </footer>
      </div>
    </div>
  )
}
