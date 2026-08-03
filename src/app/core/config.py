import os
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings

_INSECURE_DEFAULTS = frozenset({
    "your-secret-key-change-in-production",
    "your-32-byte-encryption-key-here",
})


class Settings(BaseSettings):
    PROJECT_NAME: str = "EV Trip Optimizer"
    ENV: str = "production"  # "development" | "production" — controls docs exposure + prod guards
    # Serve /docs, /redoc and /openapi.json. Defaults to "whatever ENV implies",
    # but is settable on its own: the public deployment runs under the `dev`
    # environment name, which was quietly publishing a full map of every route —
    # including the live-drive write endpoints — to anyone who asked.
    EXPOSE_DOCS: bool | None = None

    # Process role — which half of the app this process runs (web/worker split).
    #   "web"    → serve the API; start NO background loops
    #   "worker" → run background loops; serve only /health (no API routers)
    #   "all"    → one process does both (local-dev default)
    # The skeleton ships no background loops, so "web" and "all" behave the same
    # today. The seam is kept (see main.py `_start_background_loops`) so adding
    # async workers later is a copy-paste. Set "worker" on a dedicated worker app.
    PROCESS_ROLE: str = "all"

    # Database — Azure SQL (MSSQL) for both local dev and production.
    # Format: mssql+aioodbc://user:pass@server:1433/db?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no
    # Local dev points at the docker-compose SQL Server (see docker-compose.local-db.yml).
    DATABASE_URL: str = ""

    # Token/signing settings (no auth in this app; kept for signed local tokens if ever needed).
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Fernet key (32-byte base64) for encrypting any at-rest secrets (e.g. stored
    # provider tokens if you add integrations). Generated per-clone by init.sh.
    ENCRYPTION_KEY: str = ""

    # External data providers (server-side only; the frontend never sees these).
    # Free keys: https://openrouteservice.org/dev/ and https://openchargemap.org/site/develop/api
    ORS_API_KEY: str = ""             # OpenRouteService — geocoding + directions
    OCM_API_KEY: str = ""             # OpenChargeMap — charger POIs
    # Cache TTLs. Routes barely change; chargers change rarely. Aggressive caching
    # keeps us far under the ORS free-tier daily quota.
    ROUTE_CACHE_TTL_HOURS: int = 24 * 7
    CHARGER_CACHE_TTL_HOURS: int = 24 * 14

    # Azure AI Foundry (OPTIONAL, inert in the skeleton). Nothing here calls it —
    # the config is kept so wiring an LLM feature in later is drop-in. Fill these
    # and add a client in a service to use it.
    AZURE_AI_ENDPOINT: str = ""
    AZURE_AI_API_KEY: str = ""
    AZURE_AI_API_VERSION: str = "2024-10-21"

    @model_validator(mode="after")
    def _reject_insecure_defaults(self) -> "Settings":
        """Refuse to start with placeholder secrets or empty critical keys."""
        if self.SECRET_KEY in _INSECURE_DEFAULTS or not self.SECRET_KEY:
            raise ValueError(
                "SECRET_KEY is missing or still set to the insecure default. "
                "Set a strong, random SECRET_KEY in your environment / .env file."
            )
        if self.ENCRYPTION_KEY in _INSECURE_DEFAULTS or not self.ENCRYPTION_KEY:
            raise ValueError(
                "ENCRYPTION_KEY is missing or still set to the insecure default. "
                "Set a strong, random ENCRYPTION_KEY in your environment / .env file."
            )
        return self

    @field_validator("PROCESS_ROLE")
    @classmethod
    def _normalize_process_role(cls, v: str) -> str:
        # Fall back to "all" on anything unexpected so a typo in an App Setting
        # can't start a process that neither serves the API nor runs the loops.
        role = (v or "").strip().lower()
        if role not in {"web", "worker", "all"}:
            import logging

            logging.getLogger(__name__).warning(
                "Invalid PROCESS_ROLE %r — falling back to 'all'", v
            )
            return "all"
        return role

    # ----- Database connection pool (ASYNC engine — the web/API tier) -----
    # Per web instance the async ceiling = DB_POOL_SIZE + DB_MAX_OVERFLOW. Size so
    # an autoscaled web tier stays under the SQL connection budget.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    # Fail-fast timeouts so a stalled connection errors + frees itself instead of
    # wedging the pool until a restart (esp. the macOS ODBC driver).
    DB_POOL_TIMEOUT: int = 30       # max seconds to wait for a pooled connection
    DB_LOGIN_TIMEOUT: int = 10      # pyodbc connect/login timeout (seconds)
    DB_QUERY_TIMEOUT: int = 60      # pyodbc per-query timeout (seconds); 0 = unlimited
    # SYNC engine pool (kept for parity with the worker seam). False = NullPool
    # (fresh connection per sync session) — REQUIRED on macOS/local, where a
    # shared QueuePool corrupts the MS ODBC driver via cross-thread pyodbc reuse.
    # Set True only on a Linux worker tier for a bounded pool. See database.py.
    DB_SYNC_POOL_BOUNDED: bool = False
    DB_SYNC_POOL_SIZE: int = 20       # only used when DB_SYNC_POOL_BOUNDED
    DB_SYNC_MAX_OVERFLOW: int = 10    # only used when DB_SYNC_POOL_BOUNDED

    # Default asyncio to_thread executor width, installed at startup (main.py).
    # Python's stock executor is min(32, cpu_count + 4) — only ~5 threads on a
    # 1-vCPU instance — and every blocking call (sync DB sessions, SDK calls)
    # shares it. Threads are cheap (waiting on I/O); the DB pool is the real cap.
    THREAD_POOL_MAX_WORKERS: int = 40

    # ----- Rate limiting -----
    RATE_LIMIT_PLAN: str = "10/minute"          # trip planning (may trigger ORS/OCM calls) per IP
    RATE_LIMIT_GEOCODE: str = "30/minute"       # geocode autocomplete proxy per IP
    RATE_LIMIT_DEFAULT: str = "60/minute"       # general API per IP
    # Live runs. The write buckets are keyed on the RUN ID rather than the IP
    # (see rate_limit.run_key) — a carful shares one address and a mobile
    # carrier's CGNAT shares one with thousands of strangers.
    RATE_LIMIT_RUN_START: str = "6/hour"         # per IP: starting a drive is rare
    RATE_LIMIT_LIVE_PING: str = "12/minute"      # per run: ~25 s cadence + retries
    RATE_LIMIT_LIVE_REPLAN: str = "6/minute"     # per run: a real CPU sweep
    RATE_LIMIT_LIVE_READ: str = "120/minute"     # per IP: watchers poll
    # Shared rate-limit backend. Empty = in-process memory (per-instance, resets
    # on restart). Set to a redis://… URI in production so the limit is global
    # across App Service instances and survives restarts.
    RATE_LIMIT_STORAGE_URI: str = ""
    # Trust CF-Connecting-IP / the left-most X-Forwarded-For hop for rate-limit
    # bucketing. ONLY turn this on when a proxy that overwrites those headers is
    # the sole route to the app — otherwise any caller can spoof a fresh bucket
    # per request and every IP-keyed limit below becomes decorative.
    TRUSTED_PROXY_HEADERS: bool = False

    # ----- CORS / URLs -----
    # Comma-separated allowed origins (e.g. "https://evtrip.app,https://www.evtrip.app").
    # "*" for development only.
    CORS_ORIGINS: str = "*"
    # Frontend URL (redirects, links in emails). API and frontend live on
    # different hosts, so API_PUBLIC_URL is separate.
    FRONTEND_URL: str = "http://localhost:3000"
    API_PUBLIC_URL: str = "http://localhost:8000"

    # ----- Logging -----
    LOG_LEVEL: str = "INFO"    # DEBUG, INFO, WARNING, ERROR
    LOG_JSON: bool = True      # False for human-readable dev output

    def get_cors_origins(self) -> list[str]:
        """Parse CORS_ORIGINS string into a list."""
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    class Config:
        # .env lives in the project root (4 dirs up: core → app → src → root).
        env_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            ".env",
        )
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra fields in .env file


settings = Settings()

# Unique identifier for this instance (generated once at import time). Useful for
# multi-instance coordination (ownership/heartbeats) once you add worker loops.
import uuid as _uuid

INSTANCE_ID: str = str(_uuid.uuid4())
