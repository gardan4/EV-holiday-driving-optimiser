# CLAUDE.md

Guidance for Claude Code (and any AI agent) working in this repository.

> **Keep this file and `AGENTS.md` in sync** — they are twins (one for Claude, one
> for Codex). When you change one, mirror the change in the other.

## What this is

**EV Trip Optimizer** — a public web app that answers: *"what cruise speed gets
you to your holiday destination earliest in an EV, charging stops included?"*
The user enters origin/destination, picks a car, sets departure and desired
arrival charge; the backend fetches the real route, simulates every cruise speed
(90–160 km/h) against the car's consumption + DC charge curves, and returns a
total-time-vs-speed sweep, a stop-by-stop itinerary, and a low-poly 3D "journey
scene" with animated playback and a race mode. Trips persist under unguessable
UUIDs (the share link). **There is no auth** — fully public by design.

Stack: **FastAPI** (async SQLAlchemy on Azure SQL / MSSQL) + **Next.js 16**
(App Router, React 19, Tailwind 4, React Three Fiber), deployed as containers to
**Azure App Service** via **Bicep** + GitHub Actions.

## Architecture

### Backend (`src/`) — FastAPI + Python 3.12 (uv)

- **The engine** `src/app/services/simulator.py` — pure-Python, zero-I/O trip
  simulator: quadratic consumption (`Wh/km = a + b·v²`), piecewise-linear charge
  curves, per-country speed caps (DE-derestriction heuristic: free-flow ≥ 118),
  and an exact forward DP over (charger node, 2.5% SoC bucket) that discovers
  the optimal stop plan per speed. The DP plans on the grid; the chosen plan is
  re-simulated continuously for exact itinerary numbers + playback timeline.
  **Change it only with `uv run pytest tests/test_simulator.py` green.**
- **External data** `src/app/services/routing.py` (ORS geocode + directions,
  cache-first: `route_cache` keyed by geohash6 O/D, 7d TTL) and
  `src/app/services/chargers.py` (OpenChargeMap corridor search via geohash-4
  tiles, 14d TTL, projection onto the polyline). Free-tier quotas are protected
  by these caches — never call ORS/OCM per-speed or per-request.
- **Geo helpers** `src/app/services/geo.py` — haversine, geohash, polyline5
  codec, point→route projection, coarse EU country boxes.
- **Routers** `src/app/api/` — `vehicles.py`, `geocode.py`, `trips.py`
  (`POST /api/trips` = plan + persist permalink; sweep runs via
  `asyncio.to_thread`), `feedback.py`, `events.py`. Schemas in `api/schemas.py`
  mirror `frontend/lib/client.ts`.
- **Usage counting** `src/app/api/events.py` + `src/app/core/visitor.py` +
  `src/app/services/usage.py` — first-party, no third-party script, no CSP
  change. `POST /api/events` takes an allowlisted event name; the read side is
  `GET /api/events/stats` behind `X-Stats-Token` (default-deny) and
  `uv run python -m scripts.usage_report`. See the design decision below before
  touching any of it.
- **Analytics query layer** `src/app/services/analytics/` — `Filters` (a window
  plus allowlisted equality constraints) and five primitives: `timeseries`,
  `breakdown`, `funnel`, `histogram`, `totals`/`compare`, plus `retention`.
  `usage.usage_stats` is a composition over these, so the terminal report and
  the dashboard cannot answer the same question differently.
  `filters.day_bucket` holds the MSSQL/SQLite date-truncation split.
- **The metrics nothing used to read** `services/drives.py` (TripRun/TripEvent:
  completion, replans, real charge-stop durations, plan-vs-actual),
  `services/trip_shape.py` (distance and charge-share distributions, departure
  month, transit countries) and `services/upstream_health.py` (ORS/OCM as a
  trend, with latency percentiles).
- **The admin dashboard** `src/app/api/admin.py` + `dashboard/` — its own
  process role, its own origin, its own auth. See the design decision below.
- **Models** `src/app/models/__init__.py` — `Vehicle` (curated catalog with
  consumption/charge-curve JSON), `Charger`/`OcmTile`/`RouteCache` (caches),
  `Trip` (request+result JSON, id = share token), `TripRun`/`TripEvent` (live
  drives), `Feedback`, `AppEvent` (usage counts), `TripStat` (coarsened trip
  analytics), and the `GUID` type.
- **Seed data** `src/scripts/seed_vehicles.py` — the curated EV catalog;
  idempotent upsert-by-slug, re-run by `db_bootstrap` on **every** boot, so the
  file is the catalog. Curves are hand-curated from public fast-charge tests —
  cite sources in `source_note` when adding cars. Read the module docstring
  before adding one: `sort_order` is derived from list position (so the file's
  order is the catalog's order and each make must be one unbroken run), slugs
  are permanent because the upsert never deletes, and every `source_note` must
  be unique. `tests/test_vehicle_catalog.py` enforces all of it, which matters
  because `db_bootstrap` swallows sync failures — a malformed entry does not
  crash the API, it just leaves the deployed catalog on yesterday's data.
- **Config** `src/app/core/config.py` — `pydantic-settings`; needs
  `SECRET_KEY`/`ENCRYPTION_KEY` (generated by init) and `ORS_API_KEY`/
  `OCM_API_KEY` for live routing (simulator + tests run without them).
- **Migrations** `src/alembic/` — baseline `0001_initial`. `db_bootstrap.py`
  runs `create_all` + stamp on a fresh DB, else `alembic upgrade head`.

### Frontend (`frontend/`) — Next.js 16, React 19, Tailwind 4, R3F

- `app/page.tsx` — landing + `TripForm` (geocode autocomplete, vehicle cards,
  SoC sliders) → `POST /api/trips` → redirect to `/trip/[id]`.
- `app/trip/[id]/` — server-rendered results + per-trip OG image.
  `ResultsView` owns `selectedSpeed`; chart, itinerary, and 3D scene follow.
- `app/ev/` — the indexable catalog: `/ev` plus one `/ev/[slug]` page per car,
  server-rendered from `GET /api/vehicles` on an hourly revalidate.
  `lib/vehicles.ts` holds the fetch *and* a deliberate port of the simulator's
  `curve_power_kw` / `charge_minutes` / `consumption_wh_km` — the pages claim
  these are what the app models, so the two must move together.
- SEO surface: `lib/seo.ts` (schema.org JSON-LD, emitted via
  `_components/JsonLd.tsx`), `lib/faq.ts` (one source for the rendered FAQ and
  its `FAQPage` markup — they must not drift), `app/llms.txt/route.ts`,
  `lib/site.ts` (the single `SITE_URL`).
- `app/_components/trip/scene/JourneyScene.tsx` — the 3D diorama (all
  procedural geometry, no assets, strict-CSP safe). `geometry.ts` maps the real
  polyline to an arc-length-true scene curve — stop positions and playback are
  distance-truthful.
- `lib/playback.ts` — `PlaybackClock` mutates a shared ref per rAF (cars read
  it in `useFrame`); React UI subscribes at ~5 Hz. Race mode = second timeline.
- `proxy.ts` — middleware: CSP (fully self-hosted; no external origins) +
  security headers + www redirect + noindex for `/trip/*`.
- `lib/client.ts` — typed API client; keep in sync with `api/schemas.py`.
- Design system: EV-mint brand ramp + navy ink in `app/globals.css`,
  Bricolage Grotesque display font, chart palette validated for CVD.

### Dashboard (`dashboard/`) — Vite + React 19 + Tailwind 4 + Recharts

The admin console — **local only**, run with `./scripts/dashboard.sh` (⇧⌘P →
"Mission control"). Dark-only "flight deck", sharing the app's validated chart
hexes but not its light surface. Vite builds into `src/dashboard_static/`
(gitignored) so the `dashboard` process role can serve it with `StaticFiles`.
Nothing builds it in CI, so a UI change that does not compile is only caught by
running it — `npm --prefix dashboard run build` also runs `tsc --noEmit`.

- `App.tsx` composes the panels; `lib/useFilters.ts` keeps the segment in the
  **URL**, so a view is linkable and survives a refresh. Clicking any bar adds
  a facet chip that narrows every panel at once — that is the whole drilldown
  mechanic, so depth costs no extra UI.
- `lib/useStream.ts` is the `EventSource` layer (capped ticker, rolling
  counters, reconnect state). Panels refetch on a beat, throttled to 10s.
- `components/WorldMap.tsx` — corridors as arcs over **the whole world**, drawn
  with **`d3-geo`** (`geoNaturalEarth1` + `geoPath` + `geoInterpolate`).
  Geometry is **Natural Earth, public domain**, simplified to ~0.18° and bundled
  into `lib/world.ts` by `scripts/build_world_geometry.py` (generated; do not
  hand-edit — regenerate). Bundled rather than fetched because the console's CSP
  is `connect-src 'self'`: a tile server or a CDN GeoJSON would be blocked, and
  allowing one would break the app's no-external-origins rule. So the "no
  in-app map" decision survives — it was about the public planner and the CSP,
  and both are respected.
  **`d3-geo`, and specifically not Leaflet or MapLibre**: those want a tile
  server, which the CSP forbids and self-hosting world tiles would mean shipping
  gigabytes. `d3-geo` is ~30 kB, does no I/O, and is the only part of a map
  stack this needs. It replaced a hand-rolled Robinson lookup table that was
  wrong in a way only visible once data left one hemisphere — no antimeridian
  handling, so a Tokyo → Los Angeles corridor was drawn the long way across
  Europe and Africa (1440 units instead of 693), and its arcs were beziers in
  projected space rather than great circles. `geoPath` clips to the sphere and
  cuts at the antimeridian; `geoInterpolate` gives the true geodesic. Country
  rings go through `geoPath` too, which is what stops Russia and Fiji smearing
  across the frame.
  **The world, not Europe, because the planner is worldwide.** The Europe-only
  version silently dropped every corridor outside its frame — which on the real
  dev database meant hiding live US, Japanese and Australian routes, including
  a US corridor at 0.76 stops/100 km. Two causes, both fixed: the frame, and
  `corridors.place()` emitting `?? 41.9,-87.6` when `geo.country_at` does not
  recognise a point (it only knows coarse boxes for eight European countries),
  which the old label parser rejected outright.
  The map's other job is to make one design decision visible: **countries the
  simulator has real motorway caps for are lit; everywhere else is hatched**,
  and arcs with an unrecognised or unmodelled endpoint are dashed. "The planner
  is worldwide, the speed caps are not" is otherwise only a sentence. The
  modelled set comes from `/api/meta`, derived server-side from
  `simulator._default_country_caps`, so it cannot drift from what the planner
  actually models. Endpoints are the geohash-4 centres `corridors.place()`
  emits, so nothing resolves finer than the table it draws.
  The view **frames itself on the data** (presets: "fit to trips" / "whole
  world"), because a permanently zoomed-out world map renders this app's actual
  traffic as a smudge over the Low Countries. It is also **draggable**: pan,
  wheel-zoom anchored on the cursor, double-click or "reset view" to go back,
  and arrow keys / `+` / `-` / `0` when focused. Five things that took getting
  right, all of them easy to reintroduce:
  - The fitted box is grown to the frame's aspect ratio (`toAspect`), or
    `preserveAspectRatio="meet"` letterboxes a tall box and shrinks everything.
  - Stroke widths go through `px()`, which converts rendered pixels to
    projection units against the **measured** frame width. Sizing them against
    the world extent draws sub-pixel borders; using the `max-w` constant makes
    the map drift away from the cursor on a narrow screen.
  - The wheel listener is registered with `addEventListener(..., {passive:
    false})`. React's `onWheel` is passive, so `preventDefault` there does
    nothing and zooming scrolls the dashboard underneath at the same time.
  - `touch-action: none` on the `svg`, or a touch drag scrolls the page and the
    map only moves once the browser has decided the gesture was not a scroll.
  - The manual view is **not** reset when data refreshes — the panels reload
    every ten seconds while the stream is live, and yanking the map back to
    centre mid-drag would break it exactly when it is most interesting.
  `MIN_SPAN`/`MAX_SPAN` and the fit padding are derived from the projected world
  (`kmSpan`), never written as raw units — moving off the hand-rolled projection
  changed the coordinate scale and silently invalidated the hardcoded ones.
  The floor is set by the data rather than by taste: outlines are simplified to
  ~20 km and endpoints are geohash-4, so zooming past ~2 km/px only invents
  precision.
- The role sends its own CSP (`app/main.py`) — the API's `default-src 'none'`
  would block the console's own bundle — plus `X-Robots-Tag: noindex`, since
  unlike the Next app there is no middleware in front to say so.

### Infra (`infra/`) + CI (`.github/workflows/`)

Bicep provisions an App Service Plan, api + web App Services (GHCR containers),
Azure SQL, and Log Analytics; `orsApiKey`/`ocmApiKey` land as api app settings.
The admin console is deliberately absent from all of this — it runs locally.
**One environment (`dev`), one workflow** (`deploy.yml`, push to `main`) — this
is a PoC. It runs `uv run pytest` + `tsc`/`next build`, then builds images; the
GHCR push and Azure deploy steps are gated on `AZURE_CLIENT_ID` being set, so
the pipeline is green before infra exists. Infra deploy is manual
(`az deployment group create`) — see `docs/DEPLOYMENT.md`.

## Key design decisions (do not re-litigate casually)

- **No auth, no user table.** Share links are unguessable UUIDs; `/trip/*` is
  noindexed. Adding accounts later is additive, not a refactor.
- **No in-app map.** The 3D journey scene is the visualization; per-stop
  Google Maps deep links cover navigation. Keeps CSP self-hosted.
- **DP over greedy** for stop planning — it must *discover* "arrive low,
  charge to ~60-80%", not hardcode it (tests assert this emerges).
- **The planner is worldwide, the speed caps are not.** Any place ORS can route
  between is plannable; `simulator._default_country_caps` only knows eight
  western-European countries and everywhere else falls back to
  `default_country_cap_kph` (130), which is too high for most of the world.
  The assumptions panel says so — keep it saying so until real caps exist.
- **The stop window scales with the route** (`chargers.stop_window`). Stops are
  excluded from the first 50 km and last 10 km, which is right for a holiday
  drive and nonsense below 60 km: flat values leave NO eligible window there, so
  a 26 km hop returned zero candidate chargers and the app told people in the
  Netherlands there were "no fast chargers along this route". The fractions
  (25% / 10%) bind only under ~200 km, so no real road trip changes. Failing on
  a short hop is now `route_too_short` and says the true thing — set off with
  more charge — because on that length the binding constraint is always the
  charge you left with, never the charger data.
- **OCM connector ids are regional** (`chargers.DC_CONNECTION_TYPES`). CCS Type 2
  alone is Europe; North America is CCS Type 1 + NACS, Japan CHAdeMO, China
  GB/T. Filtering to one plug is why a US route once found zero chargers.
  Changing that query means bumping `OCM_QUERY_VERSION` — it is part of the
  tile cache key, and without the bump every tile keeps serving its old answer
  for the full 14-day TTL.
- **Usage counting is first-party and deliberately forgetful.** "Is anyone
  using this?" is answered by our own `app_events` table rather than an
  analytics script, so the privacy page keeps its "no trackers, no third-party
  scripts" sentence and no visitor list leaves the app. Three properties are
  the whole reason that is defensible, and none is optional: the `visitor`
  pseudonym is salted with `SECRET_KEY` **and the current UTC date**, so it
  cannot be reversed and cannot be followed past midnight; `path` is reduced to
  a route pattern against `events.KNOWN_PATHS`, so a trip's share token never
  lands next to a visitor; and `name` is checked against
  `usage.ALLOWED_EVENTS`, so a public write endpoint can't become free storage.
  Rows expire at 90 days via `scripts.purge_old_events`, on the same
  `main._purge_loop` as the location trails.
- **`trip_stats` is derived, coarse, and deleted with its trip.** Every number
  in it already existed in `Trip.request`/`Trip.result` — but those are JSON
  blobs, and MSSQL cannot `GROUP BY` an origin inside one, so "which corridors
  do people plan, and where does charging fail them?" was unanswerable against
  data we had held since the first deploy. `services/trip_stats.py` derives the
  answerable version; `scripts/backfill_trip_stats.py` does history, which is
  why the corridor numbers start at launch rather than at the day the table
  shipped. Three properties are load-bearing. **It is coarse by construction**
  — geohash-4 (~20 × 25 km) and distance to 10 km, so the table we query,
  export, or might show a charging operator cannot carry a doorstep, and the
  visitor id a later tier adds will land on a row that never held one. **One
  function, two callers** — deriving on write and backfilling must produce
  identical rows, or the day the backfill ran shows up as a discontinuity that
  reads like a change in behaviour. **The delete cascades** (`api/trips.py`):
  the privacy page promises deleting a trip removes the whole record, and a
  derived table nobody deletes from is exactly how that becomes untrue.
  It never raises — `build_trip_stat` returns `None` on anything it cannot
  read, because a stats row is not worth failing a trip plan over.
- **Two pseudonyms, and neither replaces the other** (`core/visitor.py`).
  `visitor_hash` is derived from IP+UA with a nightly salt: it counts everyone,
  including browsers that send us nothing, and cannot be followed past midnight.
  `client_hash` wraps a random v4 UUID the *browser* generates and stores, sent
  as `X-Client-Id`, and does not rotate — it is what makes retention answerable
  at all. Keep both: drop the daily one and opting out stops costing a retention
  number and starts costing a headcount. Three rules. The id is **generated
  client-side, never derived from the request** — that is the only reason the
  privacy page can promise clearing site data ends it. It is **validated against
  a strict v4 pattern and discarded otherwise**, because it arrives on an
  unauthenticated public endpoint and an unvalidated header that gets written to
  the DB is free storage. It is **stored hashed**, so an id leaked into a URL or
  a screenshot cannot be used to find that person's rows.
- **The planner is recorded; the reader is not.** `X-Client-Id` rides
  `planTrip` only — not `apiFetch`, not `getTrip`, not the run endpoints — and
  `events.normalize_path` still collapses every trip id. Share links get
  forwarded to people who never used this app and chose nothing; attaching the
  pseudonym to reads would turn the share token into a record of who was sent
  it. `tests/test_client_id.py::TestTripStats` guards both halves.
- **`trip_stats.client_id` expires at 15 months, not 90 days.** This is a
  holiday app, so the cycle is annual: under ~13 months you cannot answer "did
  last August's users come back this August", which is the retention question
  this app actually has. `scripts/purge_old_trip_stat_ids.py` nulls the column
  and KEEPS the row — the corridor is history worth having, the person attached
  to it is not. On the same `main._purge_loop` as everything else.
- **Client context is buckets, never the raw string** (`core/client_context.py`).
  A full `User-Agent` is one of the strongest fingerprints a browser emits, and
  an exact viewport width is close behind — so device, browser, OS and screen
  width are reduced to a handful of allowlisted words before storage, and the
  raw values are never written. Country comes from Cloudflare's `CF-IPCountry`
  and only when `TRUSTED_PROXY_HEADERS` is on: with nothing overwriting it in
  front, the header is whatever the caller typed, and a silently
  caller-controlled number is worse than no number because you would believe
  it. `referrer_path` keeps the path so "which Reddit thread" is answerable but
  **always drops the query string** — that is where a referrer out of a webmail,
  a search or a password reset carries somebody else's token.
- **`services/corridors.py` is the one reader of `trip_stats`**, shared by
  `scripts.corridor_report` and `services.usage` (so the dashboard). Two
  renderings of the same query cannot disagree about how many stops a corridor
  takes. Charging gaps rank by stops per 100 km, not raw stops — raw stops is
  just a list of the longest routes, which is a fact about geography.
- **The free tiers are counted, because running out is silent.** ORS and OCM
  are daily-ceiling free tiers and the caches exist to stay under them — but
  nothing added the calls up, so "will tonight's traffic exhaust the quota?"
  had no answer until it already did, mid-spike, as errors. `UpstreamCall` +
  `services/quota.py` record **one row per app-level operation, not per HTTP
  request**: a cold corridor is one row with `calls=35`, because the quota is
  counted in requests and the table must not become the load it measures.
  `cache_hits` is recorded too — the hit rate is the number that predicts
  whether a launch survives its own front page. `quota.record` never raises and
  never commits: it is on the hot path of the thing it measures, and a
  telemetry row is worth strictly less than the trip plan it is counting.
  Headroom is withheld (None) when a provider publishes no daily cap rather
  than invented against a made-up denominator.
- **Campaign tags exist because the referrer cannot answer the question.**
  Reddit's mobile app strips referrers, so an untagged launch reports its best
  channel as "direct". `?src=<slug>` is stashed in **sessionStorage** — not
  local — and replayed on every event of that visit, because the interesting
  event is the plan two pages later, and a tag kept forever would attribute
  next month's trip to today's link. Server-side, the strict
  `[a-z0-9-]{1,32}` pattern is what stops a public endpoint becoming free-form
  storage; `CAMPAIGN_SOURCES` is an optional allowlist on top, empty by default
  so a launch cannot silently record nothing because a setting went unedited.
- **`plan_failed` is recorded by the SERVER, with a reason** (`core/failures.py`,
  `api/trips.py`). The browser cannot know why planning failed — it sees a
  message written for a human — and an event fired from a page can be blocked,
  so the old client-side count was measured against a different population than
  the trips table it was compared with. The reason travels on `PlanError`
  rather than being parsed out of the message, because messages get reworded
  and a chart built on them reorganises itself. One wrapper records every exit
  path: eight raise sites each remembering to log is eight chances to forget.
  `events.SERVER_ONLY_EVENTS` refuses `plan_failed` from the public endpoint —
  otherwise anyone could post reasonless failures and the funnel total would
  stop matching the reason breakdown, which is the cross-check that says the
  numbers are real. The client no longer fires it at all, so a request that
  never reaches the server shows up as the gap between `plan_submitted` and
  `trip_planned + plan_failed`, which is exactly where it belongs.
- **`UpstreamCall.ok` means "the provider failed us", not "the answer was
  negative".** ORS saying there is no road between two points is a successful
  call with a disappointing result: it still spends a request (`calls=1`), but
  marking it a failure makes the upstream-health signal fire every time
  somebody picks an island, and an alarm that cries wolf on ordinary user error
  is one nobody reads on the night it matters.
- **One `/ev` page per nameplate, not per variant.** Every word on a car's
  page except `source_note` is template or derived, so two variants of one car
  published separately are two documents with identical prose differing only in
  table numbers — the near-duplicate cluster a search engine picks one winner
  from. `/ev/vw-id4` covers every ID.4, opening with the comparison that is the
  whole reason the variants are modelled apart. Which variants group is a
  curation call carried on `Vehicle.nameplate_slug`, not derived from
  make + model: Enyaq 77 and Enyaq 85 are one car to a reader, e-3008 and
  e-5008 are two. Legacy `/ev/<variant-slug>` URLs 308 to their nameplate, and
  that redirect is derived from the catalog rather than a hand-kept map, so it
  keeps covering variants added later. The sitemap and `llms.txt` list
  nameplates only — a variant slug there advertises a URL that redirects.

- **The catalog pages are the SEO surface, and they are computed.** Before
  them the site had two indexable pages, one of which was the privacy notice,
  and an `<h1>` ("Should you really drive 100?") matching no query anyone types.
  `/ev/[slug]` exists because the hand-curated charge curves are the one thing
  here nobody else has — stated as server-rendered text and `Vehicle` JSON-LD,
  which is what answer engines cite. Every number on those pages is derived
  from the catalog by the ported simulator maths; none of it is hand-typed, so
  nothing can quietly go stale or contradict the planner. Keep the "what the
  model does not include" section honest — flat road, mild weather, no
  charger-side limits — it is why the numbers are worth trusting.
- **`robots.ts` names AI crawlers explicitly** even though `User-agent: *`
  already allows them. It is the artefact you point at when a bot is missing,
  and it is where the line goes if training-vs-retrieval ever needs splitting.
  A robots allow is necessary, not sufficient: Cloudflare's "block AI crawlers"
  rule would stop all of them before they reach the origin.

- **The dashboard is LOCAL ONLY, and is a process role rather than an app.**
  `PROCESS_ROLE=dashboard` (`./scripts/dashboard.sh`, or the "Mission control"
  VS Code task) serves the console on :8101 against whatever `DATABASE_URL`
  says — in practice the local database, since Azure SQL only admits the App
  Service outbound IPs. **Production numbers therefore still come from
  `usage_report --remote` / `usage_dashboard --remote`**, which read the
  token-gated `GET /api/events/stats`. Closing that gap means either putting
  the console on an App Service after all (see below) or adding your own IP to
  `sqlAllowedIps` — a real firewall change, not a convenience.
  Nothing about it is in `infra/` or `deploy.yml`: no App Service, no image
  content, no CI step, and `src/dashboard_static/` is gitignored so the API
  image never carries the bundle. **If that ever changes**, three things come
  with it: the SPA has to be built into the image (its build context is
  `./src`, and `dashboard/` sits outside it), the role must skip
  `db_bootstrap` in the Dockerfile CMD (a read-only console has no business
  migrating the schema it reads, and two apps running `alembic upgrade head`
  per deploy is a race), and the App Service needs `STATS_TOKEN` set or every
  route 404s.
  The role mounts the admin router and the built SPA and **none** of the public
  API routers, so an analytics query scanning 90 days of events cannot compete
  with a trip plan for the API's pool. It is deliberately **not part of `all`**
  (the local-dev default), so a misconfigured public instance cannot serve an
  admin surface, and an unrecognised `PROCESS_ROLE` still falls back to `all`.
  `app.main.create_app()` is a factory precisely so the role gating is testable
  in-process.
- **The dashboard's auth is a cookie, and its default-deny is `STATS_TOKEN`.**
  The token is exchanged once at `POST /api/login` for a Fernet-encrypted
  httpOnly cookie, so it never reaches client JavaScript — that is the whole
  reason the console is its own served app rather than a route in the Next
  frontend, where the token would have to be shipped to the browser or proxied
  by a second server. Fernet already carries an authenticated timestamp, so
  `decrypt(..., ttl=…)` is the entire expiry mechanism: no session table, no
  migration, nothing to purge. An empty `STATS_TOKEN` 404s every route
  including the login — same rule as `/api/events/stats`, and never keyed on
  `ENV`.
- **The live stream polls the database; it is not an in-process bus.**
  `GET /api/stream` tails `app_events` on an `(at, id)` cursor. A pub/sub would
  have to be published to from `POST /api/events` — the hot write path the
  stream exists to watch — and would only ever see rows written by its own
  process, so the moment the API tier has two instances the dashboard silently
  misses half the traffic. Polling an indexed column is correct regardless of
  topology. The frames carry only columns that already exist and **neither
  pseudonym**, so a live feed cannot become a feed of people.
- **Nothing on the dashboard collects anything.** Every route is a read over
  tables that already exist, which is why `frontend/app/privacy/page.tsx` needed
  no edit and there is no migration. Keep it that way: a new column for the
  console is a change to the privacy page first.
- **"Visitors" means browsers that viewed a page**, in `analytics.totals`, in
  `usage_stats`, in the terminal report and on the tile — everywhere. Counting
  every event's visitor instead is defensible in isolation and wrong here,
  because it silently includes anything that reported a plan without a page
  view and the two surfaces then print different numbers under the same word.
  `tests/test_analytics.py::TestUsageStatsParity` pins them together.
- **ORS free-flow under-caps fast roads**, so motorway-like segments
  (free-flow ≥ 105) use country legal caps instead; disclosed in the UI's
  assumptions accordion. The naive clamp survives behind
  `SimParams.freeflow_cap_only` for comparison.
- **Local ports 8100/3100** (8000/3000 collide with other local projects);
  local MSSQL on host port 14330.

## Common commands

```bash
# Everything (ports, DB, migrate, seed, API + web) — VS Code: ⇧⌘B "Run app"
./scripts/dev.sh
./scripts/dev.sh --no-web      # backend + DB only

# Backend on its own
cd src && uv sync
uv run uvicorn app.main:app --reload --port 8100
uv run pytest                      # simulator + API tests (no network, no MSSQL)
uv run python -m scripts.demo_sim  # Born NL→AT answer table in the terminal
uv run python -m scripts.dev_seed_trip  # keyless demo trip for frontend dev
uv run python -m scripts.backfill_trip_stats        # dry run; --apply to write
uv run python -m scripts.corridor_report            # where people drive + charging gaps
uv run python -m scripts.purge_old_trip_stat_ids    # dry run; --apply to clear old ids
uv run python -m scripts.usage_dashboard            # local HTML snapshot; --remote for prod

# Admin dashboard — live, filterable mission control on :8101. Local only.
# Builds the UI if needed, waits for the DB, opens the browser. The sign-in
# password is STATS_TOKEN from .env; empty means every route 404s by design.
# VS Code: ⇧⌘P → "Mission control".
#
# It reads DATABASE_URL, i.e. your LOCAL database — Azure SQL is firewalled to
# the App Service outbound IPs, so a laptop cannot reach it. For PRODUCTION use
# `usage_report --remote` / `usage_dashboard --remote` (above), which go
# through the token-gated GET /api/events/stats instead.
./scripts/dashboard.sh
./scripts/dashboard.sh --build        # force a UI rebuild first
./scripts/dashboard.sh --build-only   # compile the UI, don't serve
npm --prefix dashboard run dev        # Vite on 5273 w/ HMR, proxying /api to 8101
# Post launch links tagged: https://…/?src=r-electricvehicles (see CAMPAIGN_SOURCES)

# Migrations
cd src && uv run alembic upgrade head
cd src && uv run alembic revision --autogenerate -m "describe change"

# Frontend
cd frontend && npm install && npm run dev -- --port 3100
cd frontend && npx tsc --noEmit && npx next build   # one-shot compile check

# Local SQL Server (host port 14330)
docker compose -f docker-compose.local-db.yml up -d
```

## Working norms (load-bearing)

- **MSSQL boolean filters**: `.is_(True)` / `.is_(False)` compile to `IS 1` / `IS 0`
  — a T-SQL **syntax error**. Use `Column == True  # noqa: E712`.
- **You cannot CAST a comparison on MSSQL.** `cast(col == False, Float)` — to
  sum a boolean — emits `CAST(ok = 0 AS FLOAT)`, which SQLite accepts (a
  comparison there yields 0/1) and SQL Server rejects with "Incorrect syntax
  near the keyword 'AS'": it has no boolean *value* type, so `ok = 0` is a
  predicate, not something castable. Use `func.sum(case((col == False, 1),
  else_=0))`. This shipped once and no SQLite test could catch it; the guard is
  to compile the **real** statement against the MSSQL dialect and assert its
  shape (`upstream_health.daily_query`, and the test that pins it). A test that
  builds its own copy of the query only proves the test is well written.
- **Keep new columns MSSQL+SQLite portable** (tests run on aiosqlite): plain
  `JSON`, `GUID`, no filtered indexes.
- **Use `DATE`, not `Date`, when casting to truncate a day on MSSQL.**
  SQLAlchemy's `Date` renders as `CAST(x AS DATETIME)` whenever the dialect
  cannot read `server_version_info` (it assumes a pre-2008 server), and a cast
  to DATETIME does not truncate — every row lands in its own "day" and the
  chart draws one bar per event. SQLite needs `strftime` instead: CAST there
  has numeric affinity and returns the year as a number. Both branches live in
  `analytics.filters.day_bucket`, and `normalize_bucket` is what makes their
  two return types one shape again.
- **A `StaticFiles` mount at `"/"` must be registered LAST.** Starlette matches
  routes in registration order and a mount at the root matches every path
  beneath it — put it any earlier and it swallows `/health`, which is what
  Azure's probe hits, so the App Service reports itself unhealthy and cycles
  forever. For the same reason the dashboard role does not register the `/`
  JSON root: it would win over the mount and answer the console's front door
  with API JSON.
- **`func.avg` over an Integer column truncates on MSSQL** (integer `AVG`)
  while SQLite returns a float — the same query gives different answers on the
  test engine and in production. `cast(col, Float)` first; see
  `scripts/corridor_report._avg`.
- **`geohash_bbox` returns `(lat_lo, lon_lo, lat_hi, lon_hi)`** — interleaved,
  not the two lats then the two lons. Unpacking it the grouped way produces
  plausible-looking coordinates that are wrong.
- **A new table derived from `Trip` needs a line in `delete_trip`.** The
  privacy page promises deletion removes the whole record; the delete endpoint
  is where that promise is either kept or quietly broken.
- **Never put the client id in a request body that gets persisted.**
  `PlanRequest` is stored whole into `Trip.request`, which holds exact
  coordinates — a field there would write the person onto the row this design
  exists to keep them off. It travels as a header and lands on `TripStat` only.
- **Retention counts "seen before this window", not "seen twice in it".**
  Someone landing from a link and reading two pages is one visit; counting that
  as retention flatters every launch spike into looking like a product.
- **Values stored in a `String` column must be ASCII.** `String` is VARCHAR on
  MSSQL — a codepage, not Unicode — so a label like `"≤640"` comes back as
  `"<=640"` and anything outside CP1252 becomes `"?"`. The string you wrote and
  the string you can `GROUP BY` stop being the same one, and SQLite stores it
  fine so no test on the local engine notices. Use `Unicode`/`UnicodeText` for
  anything that must hold real text (as `Vehicle.make` does), and keep derived
  labels in the ASCII range.
- **A nested helper that does `lines += [...]` rebinds `lines` as a local.**
  Use `.append()` inside closures that write to an enclosing list, or the whole
  function dies with `UnboundLocalError` on the first call.
- **Two people adding a migration on the same day get two alembic heads.**
  `uv run alembic heads` must print exactly one line; if it prints two, renumber
  the later file and point its `down_revision` at the other. `db_bootstrap`
  fails outright on a branched history, so this breaks the deploy, not just a
  test.
- **Number inputs hold a string, not the clamped number** (`trip/fields.tsx`).
  Binding the box straight to a clamped `value` rewrites it on every keystroke:
  clearing it snaps back to `min` (hence the "040" people kept typing) and
  "0.59" is unreachable because "0." parses to 0 mid-word. An empty or
  out-of-range box stays what you typed, turns red, and blocks submit via
  `useNumberFieldValidity`.
- **Clock strings are hand-formatted** (`lib/format.ts`) — `toLocaleTimeString`
  differs between Node and browsers and breaks hydration.
- **Scripts that mutate the DB must guard against prod**: refuse to run unless
  the DB name contains `-dev` (see `scripts/dev_seed_trip.py`).
- **Brace shell variables that touch a non-ASCII character**: `"port $PORT…"`
  is a landmine. In a UTF-8 locale bash reads the ellipsis bytes as part of the
  identifier, so `set -u` kills the script with `PORT<junk>: unbound variable`;
  in the C locale it works fine. Write `"port ${PORT}…"`. These scripts are
  full of `…` and `→`, so the pattern is easy to hit — find them with
  `perl -ne 'print if /\$\{?\w+[^\x00-\x7F]/' scripts/*.sh`.
- **`env -i` is the right way to test a VS Code task's PATH and the wrong way
  to test everything else.** It strips `LANG` too, so the script runs in the C
  locale and any UTF-8-specific bug (see above) passes locally and fails for
  real. Reproduce a task with `LANG=en_US.UTF-8 /bin/zsh -l -c './scripts/x.sh'`
  once the PATH question is settled.
- **`./scripts/dev.sh` is the supported way to run locally** (VS Code: ⇧⌘B).
  It frees ports, waits for SQL Server, and CREATEs the database — the compose
  file only starts the server, so a fresh volume has no `evtripdb-dev`.
- **`npx tsc` only works from `frontend/`.** `npm run dev` is long-running — use
  `npx next build` for a one-shot compile check.
- **Never `source .env`** — `DATABASE_URL` contains an unquoted `&`, so the
  shell aborts parsing at that line and every variable after it is silently
  empty. This deployed empty ORS/OCM keys to Azure once. Read values with
  `grep -E '^KEY=' .env | cut -d= -f2-` instead.
- **Before `git add`-ing a file, `git diff` it** — stage only what you touched.
- **Never gate a security control on `ENV`.** The one publicly reachable
  deployment is the environment named `dev`, so `if ENV == "production"` is
  false exactly where it matters — that is how Swagger ended up published.
  Use an explicit default-deny setting (`EXPOSE_DOCS`) instead.
- **The rate-limit key must not be caller-chosen.** `_client_ip` reads the
  RIGHT-most `X-Forwarded-For` hop, because App Service appends the real peer
  and anything to the left is whatever the caller invented. `CF-Connecting-IP`
  and the left-most hop are only honoured behind `TRUSTED_PROXY_HEADERS`, which
  is only true once something that overwrites those headers is genuinely in
  front. Getting this wrong voids every IP limit and the ORS/OCM quota with it.
- **`frontend/app/privacy/page.tsx` is a promise, not prose.** Every claim on it
  has to be true of the code — "deleted after 90 days" was false for as long as
  nothing called the purge. Change the page and the behaviour together.
- **A new page route needs a line in `events.KNOWN_PATHS`.** It is an allowlist,
  so an unlisted route is counted as `/other` — it under-reports, which is the
  safe direction, and is why it is an allowlist rather than a blocklist. Adding
  a route whose URL carries an id means checking `normalize_path` collapses it
  first; the tests in `tests/test_events.py::TestPathScrubbing` are the guard.
- **Every `NEXT_PUBLIC_*` needs an `ARG` in `frontend/Dockerfile`.** Docker
  silently ignores a `--build-arg` with no matching `ARG`, and Next inlines
  these at build time, so the variable just quietly becomes its fallback.
- **Commits**: lowercase `<type>(<scope>): <imperative summary>` — `feat`, `fix`,
  `chore`, `docs`, `style`, `test`. Bodies explain the *why*.
- `main` is the only branch; pushing to it runs CI and (once Azure secrets
  exist) deploys the single `dev` environment.

## Environment

Copy `.env.example` → `.env` (init generated the secrets already). Vehicles,
simulator, and all tests work with **no external keys**. Live route planning
needs free keys: `ORS_API_KEY` (openrouteservice.org) and `OCM_API_KEY`
(openchargemap.org). Frontend env lives in `frontend/.env.local`
(see `frontend/.env.local.example`).
