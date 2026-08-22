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
UUIDs (the share link). **There is no auth** — fully public by design; the one
handle a visitor can have is an opt-in public username over a browser-generated
secret (see the design decision below), which is not a sign-in.

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
  `asyncio.to_thread`), `runs.py` (the live drive — see below), `feedback.py`,
  `events.py`, `users.py` (claim/release a public username; the trips list
  under one). Schemas in `api/schemas.py` mirror `frontend/lib/client.ts`.
- **The live drive** `src/app/api/runs.py` + `src/app/services/live.py` —
  following a trip while it is actually being driven. `live.py` is pure like
  the simulator (plain dicts in, plain dicts out, no DB, no clock): a GPS fix
  snaps to the polyline, the battery is inferred from progress since the last
  figure the driver typed in, and the drive is scored against the plan's
  timeline. `runs.py` supplies persistence and the timestamps, and owns the
  security model — the **trip id** is a public read capability, the **run id**
  is the driver's write token, returned exactly once and never in a read
  response (`tests/test_live_api.py::TestCapabilities` greps every read
  response for it). Route + chargers are snapshotted at start, so a cache
  expiry mid-drive cannot change the road being benchmarked against.
  `POST /runs/{id}/replan` re-sweeps the remaining slice; the ping path stays
  deliberately cheap (one route profile, two lerps) so it can sit on the event
  loop. `POST /runs/{id}/alternatives` answers "not this one" — see the design
  decision below.
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
- **Usernames as numbers** `services/profiles.py` — the one reader of
  `profiles` + `trips.owner_hash` for reporting, behind `GET /api/names`, the
  terminal report and `usage_stats`. Counts only; see the design decision below.
- **The admin dashboard** `src/app/api/admin.py` + `dashboard/` — its own
  process role, its own origin, its own auth. See the design decision below.
- **Models** `src/app/models/__init__.py` — `Vehicle` (curated catalog with
  consumption/charge-curve JSON), `Charger`/`OcmTile`/`RouteCache` (caches),
  `Trip` (request+result JSON, id = share token), `TripRun`/`TripEvent` (live
  drives), `Feedback`, `AppEvent` (usage counts), `TripStat` (coarsened trip
  analytics), `Profile` (an opt-in public username bound to a hashed browser
  secret; `Trip.owner_hash` is its stamp), and the `GUID` type.
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
- `components/Names.tsx` — the username row, from `GET /api/names`. Like the
  map it reads tables with no facets on them, so the chips do not narrow it and
  the panels say which numbers are the window and which are all-time. It is the
  one row on the console with **no drilldown**, deliberately: the next question
  after "how many names" is "which names", and that is a list of people.
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
  and arrow keys / `+` / `-` / `0` when focused. Eight things that took getting
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
  - **A pan is not a selection.** `user-select: none` plus `preventDefault()` on
    `pointerdown` — both, and verified together — or a drag paints the country
    tooltips and the legend in selection blue and the browser treats the gesture
    as selecting text rather than as dragging. `preventDefault` there drops the
    implicit focus, so focus is taken by hand; `dblclick` still fires, which is
    what keeps double-click-to-reset working.
  - **The focus ring is keyboard-only**, tracked with a flag set in
    `pointerdown`. `:focus-visible` does not work here: a `focus()` call made
    from a pointer handler still matches it, so the ring appeared around the
    frame on every click — which reads as the map being selected, i.e. the exact
    thing the two rules above exist to prevent.
  - **The pan is clamped** (`clampBox`) so the view's centre stays on the
    planet, and a click under `DRAG_SLOP` never commits a view — otherwise a
    one-pixel click dimmed both preset buttons and raised a "reset view" chip
    for a gesture that moved nothing.
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

- **No auth, and one opt-in public handle.** Share links are unguessable UUIDs;
  `/trip/*` is noindexed. There is still no sign-in, no password and no session:
  `Profile` (`api/users.py`) binds a username to `hash(secret)` where the secret
  is a v4 UUID **the browser generates** and sends as `X-Owner-Secret`. Writing
  needs the secret; **reading needs only the name**, because handing somebody
  your username and having it work for them is the entire feature. Five things
  hold it together and none is optional. **Claiming is the consent moment** —
  `_plan` stamps a trip only when the secret ALREADY holds a name, so an
  unclaimed secret leaves no trace and a claim never publishes history
  retroactively; anything that stamped first and matched later would publish
  somebody's back catalogue on the day they picked a name. **The public list is
  coarsened server-side** (`users.locality`): a geocoded origin is usually a
  front door and this endpoint is readable by anyone who guesses a name, so the
  labels are cut to their locality before they leave the server — a truncation
  done in the browser is one a caller can decline to do. **The secret is a
  capability, so it is stored hashed** with a `:owner:` label distinct from
  `client_hash`'s `:client:`, keeping the two pseudonym spaces unjoinable.
  **Release is complete**: it deletes the profile AND nulls the stamp on every
  trip, so a re-claim of the same name starts empty rather than resurrecting a
  list somebody took down — while the trips and their share links survive,
  because releasing a name is not asking us to delete journeys. **The username
  is `[a-z0-9-]{3,24}` plus a reserved list** (`me` most of all — it would
  shadow `GET /api/users/me`); it is a public write endpoint, and the pattern is
  what stops it becoming free-form storage. Squatting is unbounded beyond
  `RATE_LIMIT_CLAIM`, which is accepted for a PoC. The counting toggle is
  deliberately **independent**: `evtrip.cid` is deleted when somebody opts out
  of counting, and deleting the owner secret alongside it would silently take
  away a username they asked for.
- **Usernames are counted, and never listed** (`services/profiles.py`). The
  drawer shipped measurable by nothing — trips were counted, drives were
  counted, and the one feature with a stated purpose ("stop people replanning a
  journey they already planned") had no number at all. Five things make the fix
  work. **Adoption is read from the `trips` table**, not from `trip_planned`
  events: the share of trips carrying an `owner_hash` cannot be moved by an ad
  blocker, and a ratio of two differently-sampled populations would not be a
  ratio. **Claims and releases are server-side events** (`SERVER_ONLY_EVENTS`),
  because a claim is a state change only the server can confirm and it is
  cross-checked against `profiles.created_at` — two counts that can be forged
  apart are not a cross-check. **A release leaves nothing else behind**: the row
  is deleted, so without `profile_released` a name claimed and given back inside
  one window is invisible in both directions. **`trips_opened` is the one
  browser-sent event**, because the drawer is a panel and not a route, so
  opening it produces no page view; it counts the deliberate open only, not the
  restore from localStorage, or a panel left open would answer once per page
  load instead of once per intention. **Two clocks, both labelled**: claims,
  releases and adoption are the window; live, dormant, returning and the spread
  are all-time, because a rare event scoped to seven days reads zero on a quiet
  week and looks like a dead feature. And the whole surface is aggregate —
  **no per-name row, no drilldown, no username in `app_events`**, which is the
  same rule `events.normalize_path` enforces on `/u/<name>` and would be
  pointless there if the console gave the names back.
- **No in-app map, and the hand-off is the WHOLE route** (`frontend/lib/maps.ts`).
  The 3D journey scene is the visualization and Google Maps deep links cover
  navigation, which keeps the CSP self-hosted — a link is not a request. Those
  links used to be per-stop only, which is right for reading a plan and useless
  for driving one: re-entering the next charger at every stop, in the car, is
  where a plan gets abandoned. `mapsRouteLegs` sends origin → every stop →
  destination as one route, from the results page, the start screen and
  mid-drive. Three things it must keep doing. **Nine waypoints is Google's
  limit**, so a longer plan is split into consecutive legs that share their
  endpoints and are LABELLED as parts — truncating to the first nine would
  navigate somebody to a road short of where they are going. **The live one
  omits `origin`**, which is what makes Maps start from the car rather than from
  this morning's departure point. **Stops go over as coordinates, never names**:
  a charger's name here is whatever OpenChargeMap calls the site, and the
  coordinate is what the plan was computed against.
- **DP over greedy** for stop planning — it must *discover* "arrive low,
  charge to ~60-80%", not hardcode it (tests assert this emerges).
- **A drive is simulated under the assumptions its own plan was made with, and
  there is ONE function that says what those are.** `trips.sim_params_for` is
  the only place a `PlanRequest` becomes a `SimParams`; `runs._sim_params`
  calls it and adds nothing but `run_factor`, the consumption calibration this
  drive has learned from the driver's own SoC readings. It used to be a second
  copy of the same mapping, which is only correct on the day it is written: it
  was missing `motorway_cap_kph` and `ignore_speed_limits`, both added to the
  planner later and neither wired into the drive, and **nothing failed either
  time** — both halves still ran, they just stopped being about the same
  journey. The cost is not visible in a diff. The replan was benchmarked
  against a promise made under other speed rules, so a trip planned with the
  limits ignored was re-planned under legal caps and `benchmark.delta_min`
  reported a delay nobody took; and `live.advance` prices every segment through
  these same params, so the inferred battery went with it. Adding a planning
  field therefore means adding it to `sim_params_for` and nowhere else, and
  `tests/test_live_api.py::TestPlanParity` fails until every field on
  `PlanRequest` is classified as reaching the simulator directly, reaching it
  transformed, or not being a simulator setting — because that decision being
  implicit is what the bug was.
- **A driver can rule out a charging NETWORK, and that changes the plan and
  not what the app can see** (`services/networks.py`). "Not Ionity" is an
  ordinary sentence and the plan had no way to hear it: the reasons are real
  and none of them is in the data — a subscription that halves the price at one
  network, a card that has never once worked at another, a bad night at a site
  still on the corridor, a company somebody would rather not pay. The DP
  optimises minutes, so it proposed the same stop for ever, and the only lever
  was to reject that ONE charger and be handed the next one from the same
  network twenty kilometres later. Six things hold it up. **The route snapshot
  keeps every charger** — only the DP's candidates are filtered, because
  `live.nearest_charger` and "I'm plugged in here now" have to recognise a site
  whatever its badge, and a driver who takes the only free stall in town has
  not stopped being at a charger; exactly the split that "arriving anywhere is
  noticed" already draws. **`_remaining_slice` is the choke point and its
  `networks_off` argument is keyword-only and REQUIRED**, so a new DP caller on
  the live path is a TypeError rather than a silent offer of the brand the
  driver ruled out. **A slug, never free text**: `NETWORKS` is closed and the
  request is validated against it — this body is stored whole into
  `Trip.request` from an unauthenticated endpoint — and an unknown slug is
  REFUSED rather than dropped, because silently ignoring a network somebody
  asked to avoid routes them straight to it with nothing on screen to explain
  why. **The operator AND the name, which is the opposite call from
  `amenities`**: that module reads the name and refuses the operator, because
  "Shell Recharge" is a network that puts posts in car parks; here the question
  IS the network, so the operator is authoritative and the name is needed
  because OCM's operator is missing on a large minority of sites while the
  title says "Tesla Supercharger Geiselwind" in plain sight. **No needle that
  is a word in a language** — `mer` is a real network and the French for sea,
  `total` is a common noun, `bp` is two letters — because a filter that quietly
  drops the chargers on a French coastal corridor is worse than one that has
  never heard of Mer, and nothing on the screen would say so. And **failing
  because of it is its own reason** (`networks_excluded`), named in the message
  and counted apart from the charging gaps, but only when the exclusion
  actually bit: blaming a preference for a charging desert sends people to turn
  off a toggle that was never the problem. The list is served from
  `GET /api/networks` rather than copied into the browser, same rule as the
  dashboard reading the modelled country caps from the server — a copy would go
  on offering a network the matcher had renamed, and the toggle would do
  nothing with no error anywhere.
- **A stop can be wrong for reasons the model cannot see, so the driver can
  turn one down — ANY of them** (`runs.alternatives`). The DP optimises minutes, and the
  quickest charger on a corridor is regularly a lay-by behind a warehouse with
  nowhere to eat — which matters at nine in the evening with a car full of
  people, and is not in OpenChargeMap. So the app does not invent amenities it
  has no data for: it hands back the next few candidates with the cost in
  arrival time and links each to Maps so a human can look. It DOES read the
  site's name for whether you could eat there (`services/amenities.py`) —
  "Raststätte" and "Lidl" mean something a driver at nine in the evening badly
  wants to know — and keeps searching down the ranking until it finds one,
  because ranked by minutes the quickest few stops are regularly a lay-by, a
  car park and another lay-by, so the question goes unanswered by a list that
  is working perfectly. The name and never the OPERATOR: "Shell Recharge" is a
  network that puts posts in car parks, and matching it would mark half of
  Europe as having a bakery. It is a guess from a string, labelled as one
  ("reads like motorway services", never "has a restaurant"), and a null hint
  means the name says nothing rather than that there is nothing there. That
  last part has a UI half: on a German motorway every fast charger is called
  "Tesla Supercharger Geiselwind" or "IONITY Holzkirchen Süd" — network plus
  place — so the reading comes back empty for the WHOLE list however far down
  the ranking the search goes, and Geiselwind is an Autohof with a kitchen.
  A panel that promises to mention food and then shows no chips has made the
  absence claim by omission, so `AlternativeStops` says so in words and points
  at Maps. Widening `_FOOD_MARKERS` never fixes that corridor, because the name
  is the wrong place to look.
  **So the question moved off the name and onto the ground**
  (`services/overpass.py`, `amenities.nearby`, `Charger.amenities`). The app
  still invents nothing — it asks OpenStreetMap what is MAPPED within
  `RADIUS_M` of the plug and shows a fixed vocabulary of category words, so
  "restaurant · supermarket here" is a place that exists rather than a word in
  a title, and the two claims are separate fields rendered differently.
  This is the one design decision that was reversed by use: the earlier
  "no amenity data" line was about not INVENTING data, and Overpass is data.
  Five things hold it up. **NULL and `[]` are different answers** — never
  looked, versus looked and nothing mapped — because OSM coverage is uneven and
  a timeout recorded as "nothing here" is an absence claim built out of a
  failure; the cache, the schema and the panel's three-way footer all keep them
  apart. **It decorates a plan and is never part of one**: every failure is
  swallowed, a breaker stops a slow Overpass costing every request its full
  timeout, and the app falls back to reading names exactly as before.
  **Cache-first with a months-long TTL and no row, no lookup** — a services
  with a bakery is a fact about a building, and a charger with nowhere to store
  the answer would be re-asked on every request for ever, which is what the
  fair-use policy is about. **Only the shortlist is looked up**, after the DP
  has finished, so the candidates it tried and discarded cost nothing.
  And **it is counted like every other upstream** (`quota.SERVICES`), with the
  headroom withheld because Overpass publishes no per-key ceiling. Short markers live
  in `_FOOD_WORDS` and match as WORDS, because "Spar" is inside "Sparkasse",
  "Hofer" inside "Strohofer" and "Moto" inside "Motorway" — a food chip over a
  savings bank is not a weak guess, it is a wrong one.
  **The plan list carries the reading too, and the split is fetch versus
  read** (`StopOut.nearby`, `trips.fill_stop_amenities`). It was rendered only
  where the alternatives panel offered a swap, so the SAME charger said
  "restaurant here" while it was an option and nothing at all once it was the
  stop being driven to — one card apart, on one screen, reported from the road
  that way. That is backwards: by the time a site is IN the plan, whether
  there is anywhere to eat at it is the question actually being asked, and the
  panel had already made the absence claim by omission that the hedged copy
  exists to avoid. So every itinerary stop carries `nearby`, and `PlanAhead`
  renders it as the strong chip with `food_hint` as the hedged fallback
  beneath, exactly as `AlternativeStops` does. Unlike `food_hint` it cannot be
  derived in a validator — it needs the cache row — so it is filled by whoever
  serves the plan, and that is where the rule lives: **planning fetches, every
  read is cache-only** (`nearby(..., fetch=False)`). `GET /live` is polled
  every twenty-five seconds for the whole drive, so a read that could call
  Overpass would re-ask about the same four chargers for six hours, which is
  the fair-use concern in the paragraph above. And only the plan-in-force
  speed's stops are fetched — the other feasible speeds are filled from what
  that put in the cache — because a sweep touches every candidate on the
  corridor and the reader is looking at one plan.
  **The stop the plan is already heading for is never offered as an
  alternative to itself** — the baseline pass is recomputed from where the car
  is NOW and must therefore use the same first-leg floor the plan was made
  under, or a driver who accepted a stretch stop gets it handed back as an
  option; it is filtered by charger id as well, because that row reads as a bug
  however it got there. **`reject_charger_id` names the stop**, so a charger
  four hours away can be swapped from the plan list — which is where the food
  question actually lands, since by the time a stop is the NEXT one the choice
  about it has been made for you. Swapping the next stop picks `stops[0]` from
  each candidate plan; swapping a later one picks whichever stop now stands
  nearest the rejected one's position, because reporting `stops[0]` there would
  answer about a stop nobody mentioned. Stretch options are offered only for
  the next stop: the relaxed floor is a first-leg idea, and that pass has to
  rule out everything up to and including the rejected charger or the DP simply
  stops before it — a fine plan, and not an answer to "what if I push past it".
  Three more properties hold it up. **Ranked by successive exclusion, not by proximity** —
  turn the current stop down, re-run the DP, and what comes back is the real
  next-best plan with every later stop re-optimised around the swap; a nearest
  neighbours list would offer chargers no plan can use. **`delta_min` is
  against the remaining JOURNEY, not the leg**, because swapping a charger
  moves everything after it and a per-leg number flatters the swap. **And the
  panel is a SNAPSHOT of a moving car, so what it can render live and what it
  cannot must be told apart.** `dist_from_here_m` is measured from the DP
  slice, which starts wherever the car stood when the server answered, and the
  panel does not close itself: left open on a long leg it said a charger was
  "in 136 km" directly under a stop card saying 261 km, about the same charger,
  on the same screen. Distances therefore render from `offset_m` (the whole
  route's axis) against the live position, exactly as `PlanAhead` does, and
  cannot drift again. The arrival percentages and the minute costs cannot be
  re-derived in the browser, so past `STALE_AFTER_M` the panel says how far
  back they come from and offers "Redo from here". A re-plan closes it
  outright — those were options against a plan that no longer exists. **A
  rejection persists** on `run.state["excluded_charger_ids"]` — a rejection
  that lasts one request is not a rejection, since the standing "Re-plan"
  button would hand back the stop the driver walked away from. That state is a
  plain `JSON` column, so it is ASSIGNED and never mutated in place; the test
  that re-plans twice is really a test of that assignment.
  The list also reaches PAST the reserve. `reserve_soc` is right for a plan and
  wrong for a menu: it hides every charger further on that you could reach by
  arriving under it, which is the exact thing a driver asks about ("can I not
  just push on to the services?"). A second pass runs the same DP with
  `SimParams.first_leg_reserve_soc` lowered to `STRETCH_FLOOR_SOC`, so those
  appear, marked, with the arrival percentage as the headline. Three things
  make it safe. The floor applies to the LEG BEING DRIVEN and nowhere else, so
  one accepted risk cannot become a journey planned on fumes — the test asserts
  every later stop still arrives on the full reserve. It is a floor and not
  zero, because a plan arriving on vapour is a breakdown with extra steps. And
  it travels with the choice (`min_arrival_soc`) and is PERSISTED, or the next
  re-plan applies the full reserve and refuses the stop just chosen. A stretch
  option can be FASTER than the optimum — going further before stopping is what
  the lower floor buys — so the UI renders a negative delta rather than
  flattening it to "same".
- **The panel prices the plan you are ON, not a fresh optimum**
  (`runs._plan_in_force`). "Somewhere else to charge" makes exactly one claim
  about the PLAN — the row saying which stop you are heading for — and it was
  built from a free re-optimisation from the current position, which is a claim
  about the model instead. The two diverge for ordinary reasons: the plan was
  made further back, the driver has corrected the battery upwards since, and a
  DP run from here may prefer to push straight past the next stop. The panel
  then named a charger 64 km away as planned, directly beneath a card saying
  the next stop was 43 km away, about a different charger. Restricting the DP's
  candidates to the plan's own stops is NOT enough — with a better battery it
  simply skips the first of them and takes the second, which is a third plan —
  so the stop being swapped is FORCED: its leg is priced straight off the route
  profile at the departure percentage the plan chose, and only the tail after
  it goes back through the DP. The tail is re-optimised on purpose, because
  every alternative is costed that way and the two have to be comparable. A
  genuinely quicker option is not lost, it appears in the list with a NEGATIVE
  delta — which is why `delta_min >= 0` is no longer an invariant for ordinary
  alternatives. `_planned_stops_ahead` falls through to the original itinerary
  when nothing has been re-planned, or "never offer the stop you are already
  going to" silently stops applying to every drive before its first re-plan.
- **Off the route, the drive asks for a new road rather than waiting**
  (`runs.reroute`, `live.rebase`). Leaving the route is ordinary — a diversion,
  a closed slip road, a supermarket, a nav app that knows about a jam ORS does
  not — and every one of them got the same answer: freeze every figure and say
  "rejoin the route". `/replan` refused outright for the same reason, which is
  correct for what it does (it slices the road the car is on, and the car is
  not on it) and useless as the only option, because the plan for the road you
  are ACTUALLY on is the one thing worth having at that moment. Six things hold
  the fix up. **It fires on sustained absence, not on a fix** — two minutes
  (`REROUTE_AFTER_MIN`), because it is the only mid-drive call that spends
  UPSTREAM quota and a single wild GPS reading must not buy a directions
  request; on the route it refuses outright and points at `/replan`, which
  answers the same question for free. **The axis moves and the history does
  not**: `offset_m` is measured along the snapshot being replaced, so the car
  lands at zero on a road that did not exist a moment ago — `distance_before_m`
  banks what came before, and each consumer takes the one it means (the profile
  and charger offsets read `offset_m`, the trail, the review and `drives.py`
  read `_travelled_m`). **The detour is billed and bounded**: while off-route
  `advance` spends nothing, which is right while the position is unknown and
  wrong the moment it is known again, so the crow-flies gap is priced as flat
  road — capped by what the car could have driven in the unbilled minutes,
  because a phone that slept through the detour reappears wherever it
  reappears and billing 500 km literally empties the battery. **It is an
  estimate and says so**: `soc_is_measured` drops and the toast offers the
  battery prompt, because there is no geometry for road we were not following.
  **What the driver chose survives, what the road decided does not** —
  rejected chargers stay rejected (a judgement about a site, and sites do not
  move), an accepted stretch below the reserve does not (it was about reaching
  one plug on a leg that no longer exists). And **the geometry is versioned,
  not polled**: `route_version` rides the cheap `GET /live`, the polyline sits
  behind `GET /live/route`, because a client still projecting fixes onto the
  old polyline reports the car as permanently off-route on a road it is
  driving perfectly — and a watcher needs the new road too, or their screen
  shows the car driving through fields.
- **"Start from where I am" is a NAMING call** (`routing.reverse_geocode`,
  `GET /api/geocode/reverse`). The browser has the coordinates already; what it
  cannot do is name them, and a trip whose origin reads "52.09, 5.12" is one
  nobody can check before planning it or recognise afterwards in their own
  list. Four things. It goes through `GeocodeInput.select` like a picked
  suggestion, so "a place is only chosen when it resolves" keeps holding and
  there is no second way for `origin` to be set. It answers **404 rather than a
  coordinate label** when Pelias matches nothing — somewhere unnameable is
  somewhere the router cannot start from either, and a made-up label just moves
  the failure to the routing call where the message is about roads instead of
  about the button that was pressed. It returns the point that came BACK, not
  the one that went in, so the itinerary matches the label on screen. And the
  cache key is rounded to ~110 m: at five decimal places every GPS reading is
  its own request against the free tier. **Origin only** — a destination you
  are already standing at is not a journey. It is metered as `geocode` because
  HeiGIT counts Pelias against one allowance whichever endpoint spends it.
- **A battery correction ASKS about the next stop, it does not re-route around
  it** (`runs._next_stop_risk`, `POST /runs/{id}/stretch`). Typing the real
  figure is the moment the plan's next stop can stop fitting inside
  `reserve_soc` — and "I'll take it at 6% anyway" is an ordinary judgement
  about a road the driver can see, made with knowledge the model does not have
  (what is at that services, who is asleep in the back, whether the next one is
  a lay-by). Re-planning on their behalf sends them to a charger they never
  chose, silently, at the exact moment they were being helpful. Five things
  hold it up. **Accepting does NOT re-plan**: the plan already goes to that
  stop, so agreeing to reach it on less than the reserve changes nothing about
  where the car is heading — only `first_leg_reserve_soc`, which is what stops
  the standing "Re-plan" button, the alternatives panel and any later re-route
  from quietly refusing it. That is also what makes the undo honest: there is
  no superseded plan to restore and nothing recomputed from a position the car
  has since left, so taking it back is removing one number. **Undo recomputes
  the question rather than restoring it**, because by the time somebody changes
  their mind they have driven on, and handing back the arrival percentage from
  before those kilometres would be worse than saying nothing. **Out of range is
  refused, not offered**: lowering the floor cannot make an unreachable stop
  reachable, and an "accept" button there would be the app agreeing to a
  journey it knows ends on the hard shoulder — so that case gets a different
  sentence and only "somewhere closer". **It is computed at the reading and
  held**, never per ping: it is the same `RouteProfile` slice `_soc_needed_next`
  builds, and keeping that off the ping path is deliberate. And **the drift
  panel goes quiet** once the question is answered (`needs_replan`'s
  `soc_accepted`) and while it is still open — two amber panels about the same
  battery, one asking and one offering a blunt re-plan, answers the question on
  the driver's behalf, which is the whole thing this exchange exists not to do.
  The floor expires the way every accepted stretch does, on arriving at any
  charger (`_clear_first_leg_floor`).
- **An accepted stretch is about ONE leg, and ends when that leg does**
  (`runs._clear_first_leg_floor`). `first_leg_reserve_soc` is persisted so the
  standing "Re-plan" button cannot refuse the stop the driver just chose — and
  nothing ever took it off again, so a risk accepted at nine in the morning was
  still lowering the reserve on the fourth leg that evening. Every later plan
  was computed against a floor nobody had agreed to, and the alternatives panel
  inherited it. Cleared on arriving at a charger, ANY charger: the floor was
  about reaching one, and one has been reached.
- **The battery is smoothed between server updates** (`lib/liveSoc.ts`). The
  server owns the model and nothing here anchors anything — this only carries
  the server's own last answer forward over the seconds since it arrived, using
  the same ported maths in `lib/vehicles.ts`. Without it the position moves
  smoothly, because the GPS watch projects it locally, and the percentage next
  to it jumps a whole point every ~25 s and reads as stale. Three things keep
  it honest: it always starts from `state.soc` (or, at a plug, from the
  server's own `charging.start_soc` and `since_min`), so no second opinion can
  form; it is capped at `MAX_GAP_S`, past which the phone has stopped hearing
  from the server and extrapolating would be guessing; and the gap is measured
  on the SERVER's clock — `state.at_min` against `started_at` — rather than on
  when a reply reached the phone, so a slow connection cannot inflate it.
  `LiveView` computes it once per second and every card reads that one derived
  state, so nothing on the screen can show a different battery from anything
  else.
- **Arriving anywhere is noticed, not just at the four stops the plan chose**
  (`live.nearest_charger`). The ping path matched `nearest_planned_stop` — the
  plan's own stops and nothing else — so a driver who stopped at any of the
  hundreds of other chargers on the corridor was invisible to their own drive:
  the screen kept counting down to a charger 77 km up the road while they stood
  plugged in somewhere else. Reported twice, from two different chargers,
  before the cause was found. Matched against the SNAPSHOT's chargers now, and
  on straight-line distance rather than along the route, because snapshot
  offsets are geometry-space while a ping's position is segment-space — a few
  tenths of a percent apart, which over 500 km is kilometres and no radius
  meaning "standing at this one" survives it. Being near a charger is only half
  the test: `advance` still requires the car to have stopped, so a site passed
  at 130 km/h fails on `dx`. `need_soc_next` is priced once on the transition
  in, never per ping.
- **A locked phone reports nothing, so arriving has to be tellable by hand**
  (`POST /runs/{id}/arrive`). Position comes from GPS, GPS comes from a page
  being looked at, and a phone whose car is charging is in a pocket — so the
  drive sat believing the driver was 69 km short of the charger they were
  plugged into, showing a plan for road already driven under a "no update for
  15 minutes" banner. The button says which stop. Two properties matter. The
  skipped kilometres are BILLED through `live.advance`, not jumped: moving the
  offset forward without pricing the drag returns a battery that never spent
  the energy to get there, which is a worse lie than the stale position. And
  the charger is marked BY HAND afterwards, because `advance` only recognises a
  stop the car creeps up on (`dx < AT_CHARGER_M`) and this one arrives in a
  single leap. **The phone's POSITION beats the stop on screen**: a driver
  charging somewhere the plan never chose — a services they liked the look of,
  the only free stall in town — must not be told they are at whichever stop the
  card happened to be showing, and the snapshot holds every candidate charger
  on the corridor rather than only the planned stops, so the site they are
  standing at is usually in it. `charger_id` is the fallback for when no fix
  arrives; nothing within `ARRIVE_MATCH_M` still moves the car and says no
  charger was recognised, because being somewhere we have no data for is a
  fact about our data and not a reason to leave the drive 69 km back.
  **And it can be taken back** (`POST /runs/{id}/arrive/undo`). It is the only
  write on the drive screen the model cannot correct on its own: `live.advance`
  clamps the offset forward so a wobbly fix never walks the car backwards, and
  that same rule means a mis-tap survives every later ping — the drive insists
  you are at a charger you are 69 km short of until you physically arrive. The
  undo restores the WHOLE prior state and not just the offset, because the
  arrival billed the skipped kilometres: putting the position back and leaving
  the energy spent hands back a battery that paid for road it is about to drive
  again, which is the mirror of the bug the billing exists to prevent. One
  level deep, so a tap taken back three stops ago cannot rewind a drive that
  has moved on, and offered in two places — the toast, and the stop card while
  `can_undo_arrive` holds — because a toast is gone in five seconds and a
  mis-tap is usually noticed after it.
  **An unplanned charger is described like any other** (`LiveStateOut.
  at_charger`, from the SNAPSHOT rather than the plan) and carries the one
  number the plan cannot give: `need_soc_next`, the battery required to reach
  the next planned stop with the reserve intact. It is a floor and says so —
  charging past it is the driver's call — and it is priced through the same
  `RouteProfile` the simulator uses so it cannot become a second opinion. Only
  computed when the driver says where they are, never per ping: it is a
  property of the position and the route, and the car is not moving while it
  matters. The
  offset comes from the PLAN's stop when it has one, not the snapshot node —
  the two axes differ by a few hundred metres over a long route, and landing
  anywhere else leaves the car "0 km away" and still not there.
- **A stop ends when the car MOVES or the driver says so — never because a fix
  stopped matching** (`live.advance`, `LEFT_CHARGER_M`). `parked` was decided
  from what `nearest_charger` matched on the CURRENT ping, so a fix drifting
  past its 250 m radius closed the stop under a car that had not moved a metre.
  That is an ordinary thing for a fix to do at a services — a stationary phone
  between buildings drifts, and the offset is clamped forward so the drift only
  ever accumulates — and it is close to guaranteed after a HAND-DECLARED
  arrival, where the driver pressed "I'm plugged in here now" precisely because
  the phone did not know where it was. The next automatic ping then quietly
  overruled them: the stop card vanished mid-charge, the session was banked,
  and the screen went back to counting down to the next charger. Reported from
  the road while plugged in. Three parts to the rule. A held arrival SURVIVES a
  ping that matches nothing, and a fresh match still wins when there is one,
  because moving from one plug to another is real and the position is what
  knows. Going off-route no longer revokes it either — a phone carried into the
  services is a fact about the fix, not about the car. And the distance that
  counts as "left" is wider than the one that counts as "arrived"
  (`LEFT_CHARGER_M` vs `AT_CHARGER_M`), because spotting an arrival and ending
  a stop are not the same risk: a car that is charging is not moving, so the
  evidence for having left should be unambiguous, and at any real speed one
  ping still covers several hundred metres.
- **A charging car's phone is in a pocket, so the charge runs on the CLOCK**
  (`live.charging_soc`, `live.bank_charge`, `ChargingOut`). Charging used to be
  integrated per ping, over the window each fix reported — which works only
  while the phone is awake, and the phone is never awake for the one event this
  models. `soc_gained` stopped growing the moment the screen locked, so the
  battery froze at the value it had on arrival and the "12 min left" under it
  never counted down; coming back twenty minutes later showed the screen that
  had been left behind, which is worse than showing nothing because it looks
  live. Elapsed time needs no telemetry: the SoC at plug-in, the site's power
  and `datetime.utcnow()` are the whole model, and `advance` now ANCHORS the
  charge instead of integrating it. Five things hold it up. **There is exactly
  one thing modelling the charge** — while the anchor is live `current_soc`
  deliberately does not include it, so a ping and the clock cannot disagree.
  **Banking says when the cable came out, not when we found out**: a ping
  arriving twenty minutes into the next leg subtracts its own moving minutes,
  or a phone waking on the motorway banks half an hour of charging done at
  130 km/h. **`arrive` anchors it itself**, because the tap is the last thing a
  driver does before pocketing the phone and waiting for a ping would model the
  entire stop as not charging. **A reading typed at the plug settles the
  projection FIRST and restarts it from the figure** — `apply_reading`
  calibrates by adding charging back to compare consumption with consumption,
  and a live projection holds zero there, so the arrival reading would have
  been scored against half a picture. And **it is labelled an estimate**,
  because it assumes the cable is still in.
- **Pulling in is not plugging in** (`live.plug_in`, `POST /runs/{id}/charging`).
  The first ping that found the car stopped beside a plug used to ANCHOR the
  charge, so arriving WAS charging — and the several minutes at the front of a
  real stop are exactly the ones where that is false: the queue for a free
  stall, the walk over to read the screen, the post that turns out to be dead.
  The battery climbed through all of them, silently, and upwards, which is the
  direction that costs somebody a leg they thought they could make. `advance`
  still sets `at_charger_id` — the position is a thing it genuinely knows — and
  no longer touches the anchor; the claim is made by the only party that ever
  knew, and the card says in words that nothing is being counted until it is,
  because a projection that has quietly stopped is worse than one that never
  started. Four things come with it. **One function sets the anchor**
  (`live.plug_in`), because `charging_soc` reads three keys together and an
  anchor set three ways ends up meaning three things. **`/arrive` still
  anchors**: that button says "I'm plugged in here now", which is the driver's
  own claim — what stopped anchoring is the ping that merely finds the car
  nearby. **The wider "has it left" radius moved off the anchor and onto
  `at_charger_id`** (`LEFT_CHARGER_M`), or the change would have quietly
  reintroduced the drifting-fix bug for every car that had pulled in and not
  yet plugged in — that radius was always about STANDING there, and a car
  waiting for a stall is as stationary as one charging. And **the countdown
  goes with it**: `NextStopCard` takes `charging` separately from `here`, since
  "~16 min plugged in" under a car that is not plugged in is the number a
  driver walks away from the screen trusting.
- **A third target, and it is the driver's own** (`ChargingOut.leave_at_soc`).
  The stop card had two — the floor the road needs and the plan's own figure —
  and neither is "I'll sit for another ten minutes, the next hundred kilometres
  are through the Alps". Kept on the run (`run.state["leave_at_soc"]`) so the
  standing "Re-plan" button and the next ping cannot quietly revert it, the
  same rule the exclusions and the held speed follow, and DROPPED when the car
  leaves — by `bank_charge` when the cable comes out, and by `advance` when it
  never went in at all, because that early return is how a target set at a dead
  post would have ridden along to the next charger. Absent, a number and an
  explicit null are three different requests, separated by `model_fields_set`,
  or "I've plugged in" would clear a target set ten seconds earlier. It is
  CHIPS and not a stepper: this card's own note says why a second stepper would
  be wrong, and nobody sits at a plug deciding to leave on 73%.
- **The arrival time is priced on when the stop ENDS, not on what the clock
  says** (`live.schedule_delta_min`'s `leaving_in_min`). `ahead_behind_min` is
  what the drive screen adds to the plan's total, and it read the schedule
  alone — where am I, what time is it — so `plan_window_at` answered "on time"
  for the whole of a charge stop however far the real battery was from what the
  plan assumed. A driver who plugged in eight points low was told by the card
  in front of them that the stop had just grown by four minutes and looked up
  at an arrival time that had not moved; it did not start moving until the car
  had ALREADY overrun the planned departure, by which point the delay had
  happened. Reported from the road. The projected departure is
  `elapsed + leaving_in`, and it is late or early against `hi`. Three things.
  It **degrades exactly to the old answer** when the charge is going to plan —
  the projected departure is then `hi` and the delta is zero — which is the
  check that it is a generalisation and not a second rule, and it is a test.
  **`_leaving_in_min` is the one place that decides WHICH target ends the
  stop** (the driver's, then the plan's, then the floor), because the card
  counts down to it and the arrival time is priced off it, and two surfaces
  answering that differently is the defect this whole entry is about.
  And it costs nothing: no DP, no upstream, just the projection
  `_charging_out` already computed, so it stays on the ping path.
- **A stop ends when the driver says what they are leaving on**
  (`SocReadingRequest.leaving`). Absent it, the only way to end a charge was to
  drive far enough for a ping to notice, so the app followed a car onto the
  motorway still believing it was plugged in and still projecting it upwards.
  It is a FLAG and not something read off the number: "I'm on 62 now" and "I'm
  leaving on 62" are different sentences — one asks the estimate to keep
  counting up from a corrected figure, the other says stop — and the drive
  screen offers both on one stepper, because they are the same gesture ten
  minutes apart and two steppers is how a stop turns into a form.
- **The stop card describes the charger the car is AT, planned or not**
  (`ChargingHere`). It used to render only for chargers the plan had not
  chosen, on the reasoning that a planned stop was already described above it.
  That was exactly backwards where it mattered: parked at a planned stop, the
  card above is already counting down to the NEXT charger, so the screen
  described a site 77 km up the road and said nothing about the one the car was
  plugged into. Reported twice from the road, from two different chargers.
  It shows **two targets and both of them** — `min_soc`, the floor that reaches
  the next stop with the reserve intact ("you could go now"), and
  `target_soc`, what the plan chose to leave on, which is usually higher
  because leaving later at a fast charger beats arriving somewhere slower.
  Only the target sits a driver through minutes they did not have to spend;
  only the floor quietly throws away the optimisation the app exists for.
- **`charge_start` / `charge_end` are written now** (`runs._log_charge_edges`).
  `services/drives.charge_stops` has paired those two kinds since it shipped to
  report how long real charge stops take, and nothing ever wrote either one —
  so the panel answered with an empty list for every drive ever recorded. The
  charge anchor IS that pair, so the events are derived from it rather than
  from each of the four call sites that can start or end a stop, which is what
  stops them drifting apart. Both fire on one call when a car moves straight
  from one plug to another: a pair keyed on "is it charging" alone would see
  "still charging" and record neither.
- **A battery reading is about a PLACE, so it travels with one**
  (`runs.record_soc`). The figure a driver types off the dashboard is the only
  ground truth this app ever gets, and it was anchored to whatever position the
  phone last reported — which after a charge stop is tens of kilometres back,
  because a locked phone reports nothing. Nothing looked wrong until the next
  re-plan sent a fresh fix, correctly moved the car forward, and billed the drag
  for that gap: road already covered BEFORE the reading, and therefore energy
  already inside the number just typed. The battery visibly re-adjusted itself
  moments after being corrected, always downwards, and `apply_reading`
  calibrated against the same understated window — so `run_factor` came out
  believing the car was thriftier than it is, and every later prediction
  inherited it. `POST /soc` now takes the fix and resyncs BEFORE anchoring. An
  off-route fix is ignored rather than refused: a reading taken at a
  supermarket two streets off the corridor is still the best number anyone has,
  and the position is the only part of it that cannot be used.
- **A re-plan starts from the fix the phone sends with it, and is the only
  call that may move the car BACKWARDS** (`runs.replan`, `live.resync`). Two
  separate holes closed by one change. GPS runs while the page is being looked
  at, so a phone unlocked at a services has not reported for twenty minutes and
  "Re-plan" was re-planning a stretch of road already driven — the button whose
  whole promise is "from where I am" was the one that did not ask. And
  `advance` clamps the offset forward, which is right on a ping (a wobbly fix
  must not rewrite the drive twenty times an hour) and is precisely why a
  mis-tapped "I'm plugged in here now" was permanent: no later ping could
  correct it, so the only ways out were `arrive/undo` — which exists, and only
  helps arrivals made after it shipped — or ending the drive. A re-plan is the
  one moment the driver is explicitly saying "from HERE", so a fix sent with it
  is taken at face value in either direction. The energy is priced through the
  same profile in both: forward it bills the drag exactly as a ping would,
  backward it hands back energy billed for road never driven, so the battery
  after a correction is the battery the drive would have had if the wrong
  position had never been entered. Priced at the PLAN's cruise, because a
  correction has no window to observe a speed over. An off-route fix is refused
  rather than used — planning from a position that is not on the road being
  driven is a guess — and a request with no fix still plans from the last known
  position, because a phone indoors or with permission refused must not lose
  the button.
- **The driver picks the speed they are going to hold, mid-drive**
  (`runs.replan`, `HoldSpeed.tsx`). The re-plan swept a band and took its
  optimum, which decides something the person in the car is better placed to
  decide: six hours in, "I'm going to sit at 120" is a fact about the road, the
  weather and who is asleep in the back, and the plan's job is to price it, not
  to overrule it. Three things make it work. **It is kept on the run**
  (`run.state["hold_speed_kph"]`), because the standing "Re-plan" button says
  nothing about speed and would otherwise quietly revert a decision made ten
  minutes ago — the same defect as the exclusions, and the same fix. **Absent,
  a number and an explicit null are three different requests** — "leave my
  speed alone", "hold 135", "you pick" — separated by `model_fields_set`, or
  every ordinary re-plan would read as a request to clear the choice. And
  **`optimum_speed` stays the speed of the plan in force**, held or not, so
  every existing reader keeps working; `held_speed_kph` is how the UI tells the
  difference and `hold_costs_min` is what it costs. The stepper previews from
  the sweep already in the browser (`ReplanOut.speeds`), so only committing
  costs a request — a phone with one bar must not lag per tap.
- **The phone drives the plan the reader chose, not the optimum.** "Drive this"
  hands `/trip/[id]/drive?speed=` to the phone, appended only when the
  selection is not `optimum_speed` so ordinary links and the QR for an
  untouched plan stay bare. It matters because the drive is *followed and
  benchmarked* against that plan: starting the optimum for somebody who
  deliberately dialled down swaps their journey silently. The parameter is
  resolved against the trip's OWN feasible speeds rather than parsed as a
  number — it is a public URL that gets scanned, forwarded and edited, and a
  speed the trip never simulated has no itinerary behind it — with the optimum
  as the fallback, which is what the button did before it existed.
- **Charge TIME carries the cable→battery loss, and the curves are measured.**
  The curve is what a charger delivers; `usable_kwh` is what reaches the
  battery. Dividing one by the other with no loss term made every car in the
  catalog charge ~7% faster than any published test — `charge_efficiency`
  existed but was spent only on pricing energy. It is now applied in
  `charge_minutes` AFTER the site cap (a car pinned at a 50 kW post still banks
  only ~93% of it) and mirrored in `frontend/lib/vehicles.ts`, or the `/ev`
  pages publish times the planner disagrees with. Separately, the curves
  themselves were hand-fitted and drifted up to 26% from measured behaviour in
  BOTH directions — a Model Y 20% fast, a Model S 21% slow, because one curve
  served eight Teslas. `scripts/refit_curves.py` rebuilds them from a CC BY 4.0
  dataset of measured profiles (Morin et al., doi:10.1184/R1/30570653); the
  licence is attribution-only, hence `_MEASURED_CURVE_NOTE` on every car it
  touches. Two rules there are load-bearing: a measured peak is NOT adopted as
  `max_dc_kw` (sessions start where the driver plugged in, so the Model Y
  profile "peaks" at 230 kW simply because it began at 8% SoC), and any curve
  disagreeing with the catalog's advertised peak by >3% is refused rather than
  applied, because that is a product claim and not a curve tweak.
- **No plan sends you back up a road you were already on** (`chargers.needs_u_turn`).
  A charger on the far carriageway of a motorway is a hundred metres away and
  unreachable: you carry on to the next junction, come off, cross, and drive
  back. `detour_min` is derived from perpendicular distance alone, so it priced
  that at the same 2.4 minutes as the services you are about to pass, and the
  DP took it — an itinerary that instructed a driver to double back. Such sites
  are DROPPED, not priced, because "never" was the actual requirement and a
  cost is a thing an optimiser will pay when it is in a hurry. The rule is
  narrow on purpose, since a hard filter that fires too widely takes chargers
  away from routes that need them: only within `SIDE_MATTERS_M` of the line
  (further out you leave at a junction whatever side it is), only above
  `DIVIDED_ROAD_KPH` (below that, turning round is a junction), and only where
  `geo.country_at` actually knows the country — guessing the traffic side would
  drop precisely the reachable chargers in a left-hand-traffic country and keep
  the unreachable ones, so `LEFT_HAND_TRAFFIC` exists to make growing that
  coverage correct rather than backwards. It is applied BEFORE the dedup, which
  keeps the most powerful site within 500 m of an offset — a services pair
  straddling a motorway is two rows at nearly one offset, and filtering after
  would let the far-side 350 kW evict the near-side 150 kW. No `OCM_QUERY_VERSION`
  bump: the OCM query is unchanged and projection happens per request, so
  cached tiles stay valid. A drive already under way keeps the chargers it
  snapshotted at the start; new plans are filtered.
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
  **Counted per SERVICE, not per provider** (`quota.SERVICES`), because that is
  how the ceilings are enforced: ORS meters directions and geocoding against
  separate allowances. A single provider-level gauge reported 96% headroom on
  the afternoon geocoding was returning 403 to every visitor, which is the
  precise failure this table exists to prevent. Autocomplete was also the one
  caller that never recorded anything — every row came from `get_route` — so
  the biggest consumer of the free tier was invisible to its own meter; it now
  takes a session purely so it can be counted.
- **ORS lives at `api.heigit.org`, and the two services sit under different
  prefixes.** HeiGIT retired `api.openrouteservice.org` (announced 2026-04-28,
  shut off 2026-08-24): directions moved to
  `api.heigit.org/openrouteservice/v2/…` and geocoding to
  `api.heigit.org/pelias/v1/…`, hence two constants in `routing.py` rather than
  one base. Same key, same request and response shapes, no regeneration. This
  arrived as an outage rather than as housekeeping because quota on the OLD
  host is throttled: geocoding there began returning 403 "Quota exceeded" while
  the account's real allowance was untouched and the same key worked instantly
  against the new host — and directions kept working, so the app half-failed.
  No trailing slash on either base: a double slash answers 405 "Method 'GET' is
  not supported", which reads like an auth or endpoint fault and is neither.
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
  says. Plain `dashboard.sh` is the **local** database, which holds dev rows and
  whatever a session seeded to build the panels — not traffic, and mistaking one
  for the other is a real hazard on a screen that looks this authoritative, so
  the task descriptions say so.
  **`./scripts/dashboard.sh --remote` reads production**, and three things make
  that safe rather than convenient. The connection string is **resolved from the
  API App Service at run time** (`az webapp config appsettings list`) instead of
  copied into `.env` — a second copy of a production DB password on a laptop
  goes stale, gets committed, or gets pasted, and `az login` is an authorisation
  gate that a file is not. The console **never migrates**: `_run_common_startup`
  skips `init_db()` for this role, because `create_all` is idempotent but not
  read-only — from a checkout ahead of the deployment it silently creates tables
  outside the migration history. And the firewall hole is **its own deliberate
  command** (`./scripts/sql_allow_me.sh`, ⇧⌘P → "Azure SQL: allow my IP"), a
  named per-machine rule that `--remove` takes away. It is deliberately **not**
  `sqlAllowedIps`: that parameter is resolved by the infra job from the live App
  Service's outbound set, so a laptop added there is wiped on the next deploy
  and makes the source claim an App Service IP that is really somebody's
  kitchen table. Incremental deployments leave the separate rule alone.
  `usage_report --remote` / `usage_dashboard --remote` are still the
  no-firewall path, through the token-gated `GET /api/events/stats`.
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
uv run python -m scripts.purge_old_trips            # dry run; --apply to delete >24mo trips
uv run python -m scripts.purge_old_feedback         # dry run; --apply to delete >24mo feedback
uv run python -m scripts.usage_dashboard            # local HTML snapshot; --remote for prod

# Admin dashboard — live, filterable mission control on :8101. Local only.
# Builds the UI if needed, waits for the DB, opens the browser. The sign-in
# password is STATS_TOKEN from .env; empty means every route 404s by design.
# VS Code: ⇧⌘P → "Mission control".
#
# Plain, it reads DATABASE_URL — your LOCAL database, i.e. dev rows and seeded
# demo data, NOT traffic. `--remote` reads production instead: it resolves the
# connection string from the API App Service (needs `az login`, never stores
# it) and needs the SQL firewall opened to this machine first. The console
# never migrates in either mode.
./scripts/dashboard.sh
./scripts/dashboard.sh --build        # force a UI rebuild first
./scripts/dashboard.sh --build-only   # compile the UI, don't serve
./scripts/sql_allow_me.sh             # let this machine through Azure SQL…
./scripts/dashboard.sh --remote       # …then the console on LIVE data
./scripts/sql_allow_me.sh --remove    # close it again when you're done
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

- **A drive's `offset_m` is along the CURRENT road, and re-routing replaces it**
  (`runs._travelled_m`). Anything that indexes INTO the route — `RouteProfile`,
  charger offsets, `slice_route`, the plan's `offset_base_m` — wants
  `offset_m`. Anything that is a HISTORY — the breadcrumb trail, the review,
  `RunSummaryOut.distance_m`, `drives.achieved_speeds` — wants
  `offset_m + distance_before_m`, or it draws a journey that jumps backwards
  the moment a driver goes round a closure. Getting it wrong is silent: both
  numbers exist, both look plausible, and only a re-routed drive tells them
  apart.
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
- **The web image build reads the LIVE API, so it must not build while the API
  is deploying.** `/ev` is server-rendered from `GET /api/vehicles` at build
  time — the whole point of those pages — which makes `next build` a client of
  production. The frontend and backend jobs used to run in parallel, so a
  commit touching both raced its own deploy: the API container restarted
  mid-prerender, three fetches timed out, and the web image failed to build
  while the API deploy stood green. Green main, two tiers drifted apart, and
  nothing in the run said so. `frontend` now `needs: [changes, backend]` under
  `always()` — an ORDERING, not a requirement, or frontend-only commits (most
  of them) would skip themselves when `backend` never runs — and the backend
  job waits for `/health` to answer before finishing, because `az webapp
  config container set` returns when ARM accepts the swap and not when the app
  is serving.
- **Two people adding a migration on the same day get two alembic heads.**
  `uv run alembic heads` must print exactly one line; if it prints two, renumber
  the later file and point its `down_revision` at the other. `db_bootstrap`
  fails outright on a branched history, so this breaks the deploy, not just a
  test.
- **A naive timestamp from the API is UTC; JavaScript reads it as local.**
  `datetime.utcnow()` serialises as `"2026-08-15T08:36:17"` with no zone, and
  the ECMAScript parser treats a date-TIME form without an offset as LOCAL
  time — so east of Greenwich it lands in the future and anything measuring
  elapsed time against it gets a negative answer. The failure is silent: code
  that guards against a nonsense gap just stops doing its job for everyone
  outside UTC. Use `format.parseServerTime`, which is deliberately the OPPOSITE
  convention from `parseLocalIso` next to it — that one reads a departure the
  user typed, which is naive local by design.
- **Number inputs hold a string, not the clamped number** (`trip/fields.tsx`).
  Binding the box straight to a clamped `value` rewrites it on every keystroke:
  clearing it snaps back to `min` (hence the "040" people kept typing) and
  "0.59" is unreachable because "0." parses to 0 mid-word. An empty or
  out-of-range box stays what you typed, turns red, and blocks submit via
  `useNumberFieldValidity`. Three rules come with blocking submit that way,
  because between them they are how "the button is always greyed out" gets
  reported. **A disabled primary action has to say why**: nothing else on the
  screen distinguishes a form waiting on you from a broken one, so `badFields`
  carries the LABEL of every field holding it up and the line under the button
  names them — and is a button itself, since the reader may have folded the
  fine print back over the box in question. **A field that no longer applies is
  unmounted, not hidden**: a `hidden` box still holds the empty string somebody
  left in it, so half-clearing the motorway limit and then ticking "ignore
  speed limits" locked the form for ever, pointing at a red field that was not
  on the screen. NumberField's cleanup hands the flag back on unmount, which is
  what makes that safe. And **an invalid field opens every `<details>` above
  it**, walked from the DOM rather than from a list of which id lives in which
  panel — that list is the kind that silently stops being true.
- **A place is only chosen when it is picked from the list** (`GeocodeInput`).
  `origin`/`dest` are set by `select()` alone, so a box reading "Utrecht" with
  nothing selected looks filled and submits nothing. Typing shows an amber
  border and says to pick one; Enter takes the highlighted hit or the top one.
  Keep both — the geocoder is a free tier that answers 403 when it is out of
  quota, and that failure surfaces here as a form that cannot be submitted.
- **Anything drawn from "today" must be resolved after mount**
  (`trip/DepartureField.tsx`). The departure picker's day strip, its presets and
  its "in 3 days" all start from the current date, and a client component is
  still rendered on the server first — with the server's clock and timezone. Any
  of it computed during render hydrates onto a different fortnight for everyone
  whose date is not UTC's, which around midnight is most of the world. `now` is
  set in a `useEffect` and those parts render a placeholder until it lands.
  Everything derived from the VALUE (`parseLocalIso`, `fmtDeparture`) is safe:
  the string is naive local, so it reads the same on both sides.
- **The planner form's helper text lives in the tips, not under the fields.**
  `NumberField`'s `readout` renders at the top of its `InfoTip`, section hints
  render as a tip on the heading, and the only grey line left on the form is
  the departure readout — which sits on the DEPARTURE label's own row. Nine
  live readouts plus four section hints plus a checkbox paragraph is a column
  of grey prose the reader scrolls past to reach the fields, and it made a
  seven-field form look like a tax return. Keep new copy in the tip; the
  visible line is for the value, and red validation text is the exception.
  The label went the same way: it is the box's own first line (`NumberField`,
  `GeocodeInput`), which is a Material "filled" field with the label parked
  permanently in its floated position. It is deliberately NOT a floating label
  — the usability case against those is the animation and the empty field that
  reads as pre-filled, and neither applies when every field ships with a value.
  Keep it static, keep the real `<label for>`, and keep the input at 16px on a
  phone or WebKit zooms the page on focus.
- **A grid item needs `min-w-0`, or a text input drags the card wider than the
  screen** (`GeocodeInput`, `NumberField`). A grid item defaults to
  `min-width: auto`, so an auto-sized track takes the item's MAX-content width
  — and an `<input>` carries an intrinsic ~207 px from its default `size` that
  `min-w-0 flex-1` on the input itself cannot undo while nothing upstream is
  squeezing the row. Both field components therefore rendered wider than the
  card holding them and were clipped on a 320 px phone: "…Netherlan", "peopl",
  "€/k". It survived because `sm:grid-cols-2` is `minmax(0, 1fr)` and DOES cap
  the track, so every desktop check passed — it was 15 px over until the From
  box gained a button and 80 px after, which is the only reason anybody looked.
  Set on the component roots rather than by adding `grid-cols-1` at each call
  site, because that is a list somebody has to remember to extend; a wrapper
  `<div>` introduced around a field is a new grid item and needs it too.
- **Anything a thumb presses is 44 px, and an invisible hit area is how you get
  there without redrawing the control.** The phone guidance is 44 (Apple) / 48
  (Android), against a 24 px WCAG floor; several controls shipped at 22-32,
  including "Here" beside the origin box, the network chips, the off-route
  "Plan from where I am" button and sonner's 24 px action chip — which is
  pressed one-handed in a moving car. Two techniques, and which one is safe
  depends on the neighbours: a `before:` pseudo-element grows the target
  without touching layout, but only where it cannot steal taps from something
  else (on "Here" it is expanded VERTICALLY only, because the input's text runs
  up to its left edge), while wrapped rows like the network chips need real
  padding, since an expanded box would overlap the row above and hand the tap
  to the wrong network. Toast actions are set once in `providers.tsx`, so no
  call site can ship a target the others have grown out of.
- **Two controls that submit the same value are one control** (`BatteryCheck`).
  The battery panel opened with a green "Spot on" beside a greyed-out "It's
  26%" — and until the stepper was touched those two buttons sent the identical
  reading, so a form that was ready to submit showed a disabled primary action
  anyway. That is the same shape as the planner's "always greyed out" button
  and here it had no cause at all: the field could not be invalid, there was
  nothing to say. One button now, reading "Spot on" until the stepper moves and
  "It's 24%" after, never disabled — agreeing with the estimate still costs one
  tap, which was the whole point of "Spot on". Layout was the other half: three
  controls in a wrapping row want about 400 px, so on a 390 px phone they broke
  into three lines — a green button alone, a bare stepper, a greyed-out twin —
  which reads as a broken form rather than as one question with a number in it.
  Stepper across the full width, one button under it, which is what
  `ChargingHere` already did for the same question.
- **The drive screen's one-line facts belong at the top, and side by side**
  (`LiveView`, `BatteryCheck.Shell`). The battery row and the location-sharing
  row sat two thirds of the way down, under the plan and the navigation card,
  as two separate containers — which put the control the app's whole accuracy
  depends on below the fold on every phone. They are two facts about the SAME
  PHONE, so they are one card now, directly under the header, and the halves
  sit BESIDE each other: two 44 px rows stacked is a band of a phone screen
  spent on two short sentences, and the targets cannot shrink to buy it back.
  `BatteryCheck` owns the chrome and takes the sharing line as a `footer`,
  because only it knows which shape it is in — a one-line row goes beside, an
  open editor is a panel and stacks. Three details hold it up. The split is
  NOT even: the sharing side is fixed and short while the battery side carries
  a percentage AND how old it is, and half each clipped that to "65% · …" at
  320 px — dropping the one word that says whether the number was measured or
  guessed. The whole battery half is the button, because a status line with a
  small button beside it needs the label's width twice over, once to read and
  once to press. And the word "Correct" gives way to its pencil below 360 px
  while the pencil never does, so there is an affordance at every width.
- **Anything that depends on the READER's clock or timezone must resolve after
  mount** (`lib/mounted.useMounted`). The rule already existed for "today" in
  `DepartureField`; the drive screen's clocks are the same rule one step
  further out. They count from `run.started_at`, an instant the server recorded
  in UTC, so rendering it as a wall clock needs a timezone the server does not
  have — the container renders 18:58 and a phone in CEST renders 20:58, which
  is a hydration mismatch and a visible flash of the wrong time. Worth knowing
  WHY it only appeared once the clocks were correct: parsing a naive UTC string
  as local and formatting it as local is zero-sum in its digits, so both sides
  used to produce the same wrong answer and matched. `useSyncExternalStore`
  rather than `useState` + `useEffect`, so it does not lint as a cascading
  render.
- **A clock needs an EPOCH, not a timestamp string** (`lib/format.clockAtMs`).
  This app has two kinds of naive timestamp and they are identical as text:
  `departure_iso` is naive LOCAL because a person typed it, `started_at` is
  naive UTC because `datetime.utcnow()` recorded it. `clockAt(iso, minutes)`
  had to guess and guessed local, so EVERY clock on the drive screen — the
  arrival on the plan list, the HUD, the revised panel, the hold-speed preview,
  the watcher's banner — was out by the viewer's UTC offset: two hours early in
  CEST, four late in New York, and exactly right on the UTC machines that run
  the tests and the container that renders the page. Reported from the road as
  "the arrival time is wrong". `clockAt` now takes only a departure the user
  typed, `clockSince` only an instant the server recorded, and anything holding
  both (`JourneyHero`'s clock base) carries an epoch so there is nothing left
  to guess. `parseServerTime` already existed and already said all this; what
  was missing was making the choice impossible to skip.
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
- **A 429 has to say `detail`, and a limit has to fit the gesture it meters.**
  slowapi's stock handler answers `{"error": …}` and nothing in this app reads
  `error` — every client funnels a failure through
  `getErrorMessage(data.detail, …)` — so a refused request fell through to the
  generic fallback and the drive screen showed "Request failed (429)" to
  somebody at the wheel. `rate_limit.rate_limit_handler` sends `detail` plus a
  `Retry-After` computed from the bucket's real reset, not from the window
  length. The limit itself was the other half: `/runs/{id}/alternatives`
  borrowed `RATE_LIMIT_LIVE_REPLAN`, a budget sized for committing a decision
  and spent by BROWSING one — every stop in the plan has a swap button and
  reading the list for each is the feature working, so a six-stop plan looked
  over once exhausted it. And the third half is the button: the lookup is
  several DP passes, the panel shows nothing until it lands, so a control with
  no pending state got pressed again and again. Any control that fires a
  request needs a disabled state and a spinner, or the rate limit is measuring
  the UI's silence rather than the driver's intent.
- **Nothing on the site is kept indefinitely any more, and the legal paperwork
  lives in `docs/`.** Trips used to be kept forever so share links would keep
  working, which is a fine goal and was not a retention period: `Trip.request`
  holds the exact coordinates of a start and a destination, and one of those is
  usually somebody's house. Two years (`scripts/purge_old_trips.py`) clears a
  holiday app's annual cycle with room and still ends; feedback matches it,
  because `Feedback.contact` is the one field where somebody hands over an
  address. Both are on `main._purge_loop`, because a purge nothing calls is how
  `purge_old_runs` made the privacy page false for months.
  `docs/ROPA.md` (Art 30), `docs/DPIA-SCREENING.md` (Art 35),
  `docs/BREACH-PROCEDURE.md` (Art 33) and `docs/LIA-CLIENT-ID.md` are the
  written record. Read the LIA before touching `evtrip.cid`: keeping it is a
  **deliberately accepted** gap against ePrivacy Art 5(3), which wants opt-in
  consent for device storage that is not strictly necessary, and the whole
  argument rests on the mitigations listed there. The `/privacy#counting`
  toggle is one of them, not a nicety, and it must keep deleting the id rather
  than merely declining to send it.
- **`frontend/app/privacy/page.tsx` is a promise, not prose.** Every claim on it
  has to be true of the code — "deleted after 90 days" was false for as long as
  nothing called the purge. Change the page and the behaviour together.
- **Every control on the drive screen needs an `isDriver` gate, and nothing
  fails when it is missing.** The server is safe on its own — a watcher holds
  the trip id, writes need the run id, and `TestCapabilities` pins that — so an
  ungated control does not leak anything or break: the click reaches
  `requestReplan`, finds no token, returns null, and the passenger who just
  pressed "Hold 135" believes they changed the driver's plan. That silence is
  why `HoldSpeed` shipped visible to watchers while every sibling was gated.
  There is no frontend test runner to catch it, so the rule is the guard: if it
  calls a `live.*` write, gate it at the call site (or handle `!isDriver`
  inside, as `BatteryCheck` does when the read-only version is worth showing).
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
