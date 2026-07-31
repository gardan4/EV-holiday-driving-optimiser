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
        # The API only ever serves JSON (and, in dev, Swagger). Lock responses
        # down hard in production — docs are disabled there, so a strict policy
        # can't break anything. Skip it in dev so /docs keeps working.
        if settings.ENV == "production":
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


def _start_background_loops() -> list[asyncio.Task]:
    """Create background loop tasks (worker + all). Empty in the skeleton.

    To add a recurring job: write an `async def my_loop()` (typically an
    `while True: ... await asyncio.sleep(N)` guarded by try/except), then return
    `[asyncio.create_task(my_loop())]` here. The lifespan cancels them cleanly
    on shutdown. Run a dedicated worker process with PROCESS_ROLE=worker.
    """
    return []


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
# every route. Keep it for dev; auth still 401s any call.
_expose_docs = settings.ENV != "production"

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
    from app.api import geocode, trips, vehicles

    app.include_router(vehicles.router, prefix="/api/vehicles", tags=["vehicles"])
    app.include_router(geocode.router, prefix="/api/geocode", tags=["geocode"])
    app.include_router(trips.router, prefix="/api/trips", tags=["trips"])


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
