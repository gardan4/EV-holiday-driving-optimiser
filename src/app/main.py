"""FastAPI application entry point for EV Trip Optimizer.

Process-role-aware lifespan (`PROCESS_ROLE`): `web` and `all` mount the API;
`worker` runs background loops only and serves `/health`. The skeleton ships no
background loops, so `web` and `all` behave identically today — the seam is kept
(see `_start_background_loops`) so adding async workers later is a copy-paste.
"""

import asyncio
from contextlib import asynccontextmanager

from app.core.config import INSTANCE_ID, settings
from app.core.logging_config import RequestIdMiddleware, setup_logging

# Configure logging BEFORE any other app imports that might log.
setup_logging(log_level=settings.LOG_LEVEL, json_logs=settings.LOG_JSON)

import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from app.core.database import AsyncSessionLocal, init_db
from app.core.rate_limit import limiter

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # HSTS: TLS is terminated at the proxy (Cloudflare / Azure App Service),
        # so request.url.scheme is "http" inside the container. Trust
        # x-forwarded-proto so the header actually reaches browsers.
        forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        if forwarded_proto == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # The API only ever serves JSON, unless Swagger is switched on. Tie the
        # strict policy to that rather than to ENV: the public deployment runs
        # with ENV=development, so keying on it skipped this everywhere it
        # actually mattered.
        #
        # The dashboard role is the one exception: it serves an HTML page, and
        # `default-src 'none'` would block its own stylesheet and bundle. It
        # gets a policy that is still fully self-hosted — no CDN, no external
        # font, and `connect-src 'self'` because the SPA only ever talks back
        # to the origin that served it.
        if _serves_dashboard():
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; base-uri 'self'; object-src 'none'; "
                "script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; font-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; form-action 'self'"
            )
            # Nothing here should ever be indexed, and unlike the Next app
            # there is no middleware in front to say so.
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        elif not settings.EXPOSE_DOCS:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
            )
        return response


# ── Process-role gating (web / worker split) ──────────────────────────
# `web`/`all` mount the API; `worker`/`all` run the background loops. The
# skeleton has no loops yet, so this only matters once you add them.


def _runs_loops() -> bool:
    return settings.PROCESS_ROLE in ("worker", "all")


def _serves_api() -> bool:
    return settings.PROCESS_ROLE in ("web", "all")


def _serves_dashboard() -> bool:
    """The admin console — its own App Service, same image.

    Deliberately NOT part of "all": the dashboard is opt-in, so a public
    instance that was started with a default config cannot end up serving an
    admin surface. It also mounts none of the public API routers, which means
    the two never share a rate-limit bucket or a connection pool.
    """
    return settings.PROCESS_ROLE == "dashboard"


async def _run_common_startup():
    """Startup work both roles need: widen the thread executor + ensure schema."""
    from concurrent.futures import ThreadPoolExecutor

    # Python's default to_thread executor is min(32, cpu_count + 4) — ~5 threads
    # on a 1-vCPU instance — and every blocking call (sync DB sessions) shares it.
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(
            max_workers=settings.THREAD_POOL_MAX_WORKERS,
            thread_name_prefix="to_thread",
        )
    )

    # The console never touches the schema it reads. `create_all` is idempotent
    # against a database Alembic already provisioned, but "idempotent" is not
    # "read-only": run from a checkout that is ahead of what is deployed, it
    # CREATES the missing tables — outside the migration history, silently, and
    # (since `./scripts/dashboard.sh --remote` exists) possibly against
    # production from a laptop. A read-only console has no business migrating
    # anything; `db_bootstrap` and `./scripts/dev.sh` own that path.
    if _serves_dashboard():
        logger.info("Dashboard role: skipping schema ensure (read-only console)")
        return

    # Idempotent: on a prod DB provisioned by Alembic it's a no-op; on a fresh
    # dev DB it creates the tables. (db_bootstrap handles the migration path.)
    logger.debug("Ensuring schema exists (init_db / create_all)...")
    await init_db()
    logger.info("Schema ensured via create_all")


async def _purge_loop() -> None:
    """Enforce every retention window on the privacy page, once a day, forever.

    The privacy page promises drives are deleted after 90 days. `purge_old_runs`
    implemented that promise correctly and then nothing ever called it outside
    the tests, which made the promise false — the worst kind of privacy bug,
    because it reads as a policy. This is what makes it true.

    Everything else is on the same loop for the same reason. Usage events at 90
    days, so an analytics table does not quietly become a permanent record.
    Trips and feedback at two years, because "kept indefinitely" is not a
    retention period and `Trip.request` holds the coordinates of somebody's
    house. The planner pseudonym on old corridor rows at 15 months.

    Each purge has its own try/except so a broken query in one cannot stop the
    others. That matters more here than it looks: these are the only things
    standing between the privacy page and being wrong.

    Deliberately in-process rather than a scheduled workflow: Azure SQL sits
    behind a firewall that a GitHub runner would have to be let through, and
    `alwaysOn` is set, so the app is the one thing guaranteed to be running.
    The delete is idempotent, so a second instance racing it is harmless.
    """
    from scripts.purge_old_events import DEFAULT_RETENTION_DAYS as EVENT_RETENTION_DAYS
    from scripts.purge_old_events import purge as purge_events
    from scripts.purge_old_feedback import (
        DEFAULT_RETENTION_DAYS as FEEDBACK_RETENTION_DAYS,
    )
    from scripts.purge_old_feedback import purge as purge_feedback
    from scripts.purge_old_profiles import (
        DEFAULT_RETENTION_DAYS as PROFILE_RETENTION_DAYS,
    )
    from scripts.purge_old_profiles import purge as purge_profiles
    from scripts.purge_old_runs import DEFAULT_RETENTION_DAYS, purge
    from scripts.purge_old_trip_stat_ids import RETENTION_DAYS as STAT_ID_RETENTION_DAYS
    from scripts.purge_old_trip_stat_ids import purge as purge_stat_ids
    from scripts.purge_old_trips import DEFAULT_RETENTION_DAYS as TRIP_RETENTION_DAYS
    from scripts.purge_old_trips import purge as purge_trips

    # Let the cold start finish first — nothing here is urgent to the second.
    await asyncio.sleep(300)
    while True:
        try:
            runs, events = await purge(days=DEFAULT_RETENTION_DAYS, apply=True)
            if runs:
                logger.info(
                    "Retention purge deleted %d drive(s) and %d location point(s)",
                    runs,
                    events,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # A DB blip must not silently end retention for the process's life.
            logger.exception("Retention purge failed; retrying in 24h")

        try:
            n = await purge_events(days=EVENT_RETENTION_DAYS, apply=True)
            if n:
                logger.info("Retention purge deleted %d usage event(s)", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Usage-event purge failed; retrying in 24h")

        # Not a delete: this clears the planner's pseudonym off old corridor
        # rows and keeps the rows. On a much longer clock than the two above —
        # a holiday app's cycle is a year, so retention needs to see one.
        try:
            n = await purge_stat_ids(days=STAT_ID_RETENTION_DAYS, apply=True)
            if n:
                logger.info("Retention purge cleared the planner id on %d trip stat(s)", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Trip-stat id purge failed; retrying in 24h")

        # Trips carry exact coordinates, so this one deletes the most sensitive
        # thing that used to have no expiry at all. It cascades to the drives,
        # the location trail and the coarsened stat row.
        try:
            trips, runs = await purge_trips(days=TRIP_RETENTION_DAYS, apply=True)
            if trips:
                logger.info(
                    "Retention purge deleted %d trip(s) and %d drive(s)", trips, runs
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Trip purge failed; retrying in 24h")

        try:
            n = await purge_feedback(days=FEEDBACK_RETENTION_DAYS, apply=True)
            if n:
                logger.info("Retention purge deleted %d feedback item(s)", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Feedback purge failed; retrying in 24h")

        # After the trip purge above, deliberately: a username is only
        # collectable once nothing is published under it, and the trips have to
        # go first for that to ever become true.
        try:
            n = await purge_profiles(days=PROFILE_RETENTION_DAYS, apply=True)
            if n:
                logger.info("Retention purge deleted %d dormant username(s)", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Username purge failed; retrying in 24h")

        await asyncio.sleep(24 * 60 * 60)


def _start_background_loops() -> list[asyncio.Task]:
    """Create background loop tasks (worker + all).

    To add a recurring job: write an `async def my_loop()` (typically an
    `while True: ... await asyncio.sleep(N)` guarded by try/except), then return
    `[asyncio.create_task(my_loop())]` here. The lifespan cancels them cleanly
    on shutdown. Run a dedicated worker process with PROCESS_ROLE=worker.
    """
    return [asyncio.create_task(_purge_loop())]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown. `web` → API only; `worker` → loops only; `all` → both."""
    setup_logging(log_level=settings.LOG_LEVEL, json_logs=settings.LOG_JSON)
    logger.info("Starting instance", extra={"instance_id": INSTANCE_ID, "role": settings.PROCESS_ROLE})

    await _run_common_startup()

    background_tasks: list[asyncio.Task] = []
    if _runs_loops():
        background_tasks = _start_background_loops()

    yield

    for task in background_tasks:
        task.cancel()
    for task in background_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass

    logger.info("Shutting down instance", extra={"instance_id": INSTANCE_ID})


def create_app() -> FastAPI:
    """Build a fully configured app for the CURRENT `PROCESS_ROLE`.

    A factory rather than a module-level sequence because the role decides
    which routers exist, and a module-level `if` bakes that in at import — so
    the role gating (the thing keeping the admin console off the public API
    tier) could not be exercised by a test in the same process. Reading the
    role here instead means a test can set it and build an app.

    `app = create_app()` below keeps `from app.main import app` working for
    uvicorn and for every existing caller.
    """
    # Hide Swagger UI + OpenAPI schema in production — the schema is a full map
    # of every route. `EXPOSE_DOCS` overrides, because the publicly-reachable
    # deployment is the one named "dev" and was therefore serving them.
    expose_docs = settings.EXPOSE_DOCS

    app = FastAPI(
        title="EV Trip Optimizer API",
        description="EV Trip Optimizer backend — see docs/ARCHITECTURE.md",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
    )

    # Rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Security headers, then request-id (must be added before CORS).
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)

    # CORS
    cors_origins = settings.get_cors_origins()
    wildcard_cors = cors_origins == ["*"]
    # Never combine credentialed CORS with a wildcard origin. The public API
    # authenticates with header tokens (not cookies), so allow_credentials can
    # safely be False under wildcard. The dashboard's session cookie is
    # unaffected either way — it is same-origin, so CORS never enters into it.
    allow_credentials = not wildcard_cors
    if wildcard_cors:
        _log = logger.error if settings.ENV == "production" else logger.warning
        _log("CORS_ORIGINS is '*' — set an explicit allowlist for production. Credentialed CORS is disabled while wildcard is in effect.")
    else:
        logger.info("CORS configured", extra={"origins": cors_origins})

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routers: web/all only. The worker (PROCESS_ROLE=worker) serves only /
    # and /health below, so health probes pass without exposing the API surface.
    if _serves_api():
        from app.api import events, feedback, geocode, runs, trips, users, vehicles

        app.include_router(vehicles.router, prefix="/api/vehicles", tags=["vehicles"])
        app.include_router(geocode.router, prefix="/api/geocode", tags=["geocode"])
        app.include_router(trips.router, prefix="/api/trips", tags=["trips"])
        app.include_router(feedback.router, prefix="/api/feedback", tags=["feedback"])
        app.include_router(events.router, prefix="/api/events", tags=["events"])
        app.include_router(users.router, prefix="/api/users", tags=["users"])
        # Live runs own routes under both /api/trips/{id}/… and /api/runs/{id}/…,
        # so they mount at /api rather than under either one.
        app.include_router(runs.router, prefix="/api", tags=["live"])

    # The admin dashboard: its own role, its own origin, its own auth. Mounted
    # instead of the API routers above, never alongside them.
    if _serves_dashboard():
        from app.api import admin

        app.include_router(admin.router, tags=["dashboard"])

    # Not registered under the dashboard role: "/" there is the SPA's
    # index.html, served by the StaticFiles mount below. A route declared here
    # would win over the mount (Starlette matches in registration order) and
    # the dashboard would answer its own front door with API JSON.
    if not _serves_dashboard():

        @app.get("/")
        async def root():
            return {"message": "EV Trip Optimizer API is running"}

    @app.get("/health")
    async def health_check():
        return {"status": "healthy"}

    @app.get("/health/detailed")
    async def detailed_health_check():
        """Detailed health check that verifies dependencies (DB reachability)."""
        health: dict = {"status": "healthy", "checks": {}}
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(text("SELECT 1"))
            health["checks"]["database"] = {"status": "ok"}
        except Exception:
            health["checks"]["database"] = {"status": "error", "message": "connection_failed"}
            health["status"] = "degraded"
        return health

    # The dashboard SPA, mounted LAST and deliberately so. Starlette matches
    # routes in registration order and a mount at "/" matches every path
    # beneath it — put this any earlier and it swallows /health, which is what
    # Azure's probe hits, so the App Service would report itself unhealthy and
    # cycle forever.
    if _serves_dashboard():
        from pathlib import Path

        # Guarded rather than assumed, so the role can be run straight from a
        # source checkout for backend work: the API answers, the page 404s, and
        # nothing has to be built first.
        static = Path(__file__).resolve().parent.parent / "dashboard_static"
        if static.is_dir():
            from starlette.staticfiles import StaticFiles

            # html=True makes "/" serve index.html. It does NOT rewrite
            # arbitrary unknown paths to it — Starlette only does that for
            # directories — which is fine here: the console keeps its whole
            # state in the query string, so there are no path routes to lose on
            # a refresh. Anything else under "/" 404s, including stray /api
            # calls, which is the behaviour we want on this origin.
            app.mount(
                "/", StaticFiles(directory=str(static), html=True), name="dashboard"
            )
        else:
            logger.warning(
                "Dashboard role is running without a built UI at %s — API only. "
                "Build it with `npm --prefix dashboard run build`.",
                static,
            )

    return app


app = create_app()
