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
        if not settings.EXPOSE_DOCS:
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

    # Idempotent: on a prod DB provisioned by Alembic it's a no-op; on a fresh
    # dev DB it creates the tables. (db_bootstrap handles the migration path.)
    logger.debug("Ensuring schema exists (init_db / create_all)...")
    await init_db()
    logger.info("Schema ensured via create_all")


async def _purge_loop() -> None:
    """Delete expired location traces once a day, forever.

    The privacy page promises drives are deleted after 90 days. `purge_old_runs`
    implemented that promise correctly and then nothing ever called it outside
    the tests, which made the promise false — the worst kind of privacy bug,
    because it reads as a policy. This is what makes it true.

    Deliberately in-process rather than a scheduled workflow: Azure SQL sits
    behind a firewall that a GitHub runner would have to be let through, and
    `alwaysOn` is set, so the app is the one thing guaranteed to be running.
    The delete is idempotent, so a second instance racing it is harmless.
    """
    from scripts.purge_old_runs import DEFAULT_RETENTION_DAYS, purge

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


# Hide Swagger UI + OpenAPI schema in production — the schema is a full map of
# every route. `EXPOSE_DOCS` overrides, because the publicly-reachable
# deployment is the one named "dev" and was therefore serving them.
_expose_docs = settings.EXPOSE_DOCS

app = FastAPI(
    title="EV Trip Optimizer API",
    description="EV Trip Optimizer backend — see docs/ARCHITECTURE.md",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _expose_docs else None,
    redoc_url="/redoc" if _expose_docs else None,
    openapi_url="/openapi.json" if _expose_docs else None,
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
# Never combine credentialed CORS with a wildcard origin. Auth is via bearer
# tokens (not cookies), so allow_credentials can safely be False under wildcard.
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

# API routers: web/all only. The worker (PROCESS_ROLE=worker) serves only / and
# /health below, so health probes pass without exposing the API surface.
if _serves_api():
    from app.api import geocode, runs, trips, vehicles

    app.include_router(vehicles.router, prefix="/api/vehicles", tags=["vehicles"])
    app.include_router(geocode.router, prefix="/api/geocode", tags=["geocode"])
    app.include_router(trips.router, prefix="/api/trips", tags=["trips"])
    # Live runs own routes under both /api/trips/{id}/… and /api/runs/{id}/…,
    # so they mount at /api rather than under either one.
    app.include_router(runs.router, prefix="/api", tags=["live"])


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
