# EV Trip Optimizer

**Should you really drive 100?** Enter your route and your EV, and find the
cruise speed that actually gets you to your holiday destination first —
charging stops, taper curves and all. Then race the result against your
friend's plan.

## What it does

- Fetches the real driving route (OpenRouteService) and the fast chargers along
  it (OpenChargeMap, CCS ≥ 100 kW).
- Simulates the whole trip at every cruise speed from 90–160 km/h with an exact
  optimizer over charging stops: quadratic consumption per car, measured DC
  charge curves, per-country speed caps (incl. derestricted German autobahn),
  arrival-charge targets, and winter conditions.
- Shows the total-time-vs-speed curve with the optimum highlighted, a
  stop-by-stop itinerary (arrive 23:41 at 12%, charge 18 min to 60%…) that
  opens in Google Maps as one route with every charging stop already in it,
  and a **low-poly 3D journey diorama** — play the overnight
  drive, watch the battery drain, and put a second car at your friend's speed
  on the road with a live gap timer.
- Every plan gets a shareable permalink — send it to your co-driver.

Curated car catalog (Cupra Born 58/77 first, plus ID.3/ID.4, Model 3 RWD/LR,
Enyaq, Ioniq 5, Mégane) with charging curves hand-curated from public
fast-charge tests.

## Stack

FastAPI (Python 3.12, uv) + async SQLAlchemy on Azure SQL · Next.js 16
(React 19, Tailwind 4, React Three Fiber) · Bicep + GitHub Actions → GHCR →
Azure App Service containers. No auth — fully public by design.

## Quick start

```bash
./scripts/dev.sh
```

That frees the dev ports, starts SQL Server (waiting until it actually accepts
connections), creates + migrates the database, seeds the car catalog, and runs
the API on **:8100** and the site on **:3100**. Ctrl-C stops everything.
In VS Code it's the default build task — **⇧⌘B → "Run app"**.

Prerequisites: Docker (or OrbStack), [uv](https://docs.astral.sh/uv/), Node 20.

The simulator, tests, and vehicle catalog work with **no external keys**. Live
route planning needs two free keys in `.env`:

- `ORS_API_KEY` — <https://openrouteservice.org/dev/>
- `OCM_API_KEY` — <https://openchargemap.org/site/develop/api>

No keys yet? Seed a demo trip instead: `uv run python -m scripts.dev_seed_trip`.

```bash
# The physics, in your terminal
cd src && uv run python -m scripts.demo_sim

# Tests (pure Python + in-memory SQLite — no network, no MSSQL)
cd src && uv run pytest
```

## Docs

- `CLAUDE.md` / `AGENTS.md` — architecture map + working norms (agent-oriented)
- `docs/DEPLOYMENT.md` — Azure deployment walkthrough. One environment, one
  workflow: pushes to `main` test + build, and deploy once the Azure secrets
  are set (until then the deploy steps skip and CI stays green)
- `docs/ARCHITECTURE.md` — template heritage notes
