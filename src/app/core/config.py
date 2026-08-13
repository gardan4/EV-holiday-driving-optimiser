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
    # Serve /docs, /redoc and /openapi.json — a full map of every route,
    # including the live-drive write endpoints.
    #
    # Default-deny, and deliberately NOT derived from ENV. The public
    # deployment runs with ENV=development (it is the environment named "dev"),
    # so anything keyed on ENV publishes Swagger to the internet while looking
    # correct in the template. `.env.example` turns it on for local work; the
    # only way it reaches production is somebody typing it there.
    EXPOSE_DOCS: bool = False

    # Process role — which half of the app this process runs (web/worker split).
    #   "web"       → serve the API; start NO background loops
    #   "worker"    → run background loops; serve only /health (no API routers)
    #   "dashboard" → serve ONLY the admin dashboard (its own app service, own
    #                 auth, own origin); mounts none of the public API routers
    #   "all"       → one process does both web and worker (local-dev default)
    #
    # "dashboard" is how the admin console is a separate served app without
    # being a separate image: same container, second App Service, this one
    # setting different. It reads the database directly through
    # `app.services.*`, so analytics queries never land on the public API tier
    # and cannot eat its rate limit. Note "all" does NOT include it — the
    # dashboard is opt-in, so a misconfigured public instance cannot serve it.
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
        if role not in {"web", "worker", "dashboard", "all"}:
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
    RATE_LIMIT_FEEDBACK: str = "5/hour"         # per IP: writing free text is rare and abusable
    # Claiming a username. Rare by nature — you do it once — and the abuse mode
    # is squatting a namespace, so this is tighter than the general limit.
    RATE_LIMIT_CLAIM: str = "10/hour"           # per IP
    # Usage events. Has to cover a real session — a page view per navigation
    # plus the funnel — without leaving a public write endpoint someone can
    # pour rows into. A busy visitor sees ~20 in an hour; a script hitting the
    # ceiling just stops being counted, which costs a statistic, not a user.
    RATE_LIMIT_EVENTS: str = "120/hour"         # per IP
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

    # ----- Free-tier ceilings, for the headroom figure on the dashboard -----
    # What the provider allows per day. 0 = no published daily limit, which
    # withholds the headroom percentage rather than inventing a denominator —
    # the calls are still counted and the cache hit rate still computed.
    ORS_DAILY_QUOTA: int = 2000   # openrouteservice free tier, directions/day
    OCM_DAILY_QUOTA: int = 0      # OpenChargeMap publishes no hard daily cap

    # Campaign tags accepted on `?src=`, comma-separated (e.g.
    # "r-electricvehicles,r-evcharging,hn"). Empty means any well-formed slug
    # is accepted — the strict pattern in `events.normalize_campaign` is doing
    # the safety work either way, and this list only exists for when you want
    # the tighter guarantee that nothing you did not post shows up in the
    # numbers. Set it before a launch, leave it empty for zero friction.
    CAMPAIGN_SOURCES: str = ""

    # Discord webhook posted to when feedback arrives. Empty = no notifications.
    #
    # Unlike an ntfy topic (public to anyone who guesses the name), a webhook
    # URL is a real secret and the channel behind it is private — so the message
    # itself can go in the notification, which is the entire point of having one.
    # The sender's email address still does not: see `_notify`.
    DISCORD_WEBHOOK_URL: str = ""

    # Read the feedback inbox by sending this as an X-Feedback-Token header.
    # Empty (the default) means the read route does not exist — better than a
    # route guarded by a secret nobody set.
    FEEDBACK_TOKEN: str = ""

    # Read the usage numbers by sending this as an X-Stats-Token header. Same
    # default-deny as above. Deliberately NOT the same value as FEEDBACK_TOKEN:
    # they are two different capabilities, and sharing one secret means you
    # cannot hand out or rotate either without the other.
    STATS_TOKEN: str = ""

    # ----- Admin dashboard (PROCESS_ROLE=dashboard) -----
    # The dashboard signs you in with STATS_TOKEN as the password and keeps the
    # session in a Fernet-encrypted httpOnly cookie, so the token itself never
    # reaches client JavaScript. There is no second secret to provision: an
    # empty STATS_TOKEN already means "the usage numbers are not readable", and
    # the dashboard is the same capability with a nicer front end.
    DASHBOARD_SESSION_HOURS: int = 12
    # Per IP. A password box on the public internet is exactly the thing worth
    # limiting, and there is only ever one person signing in here — so this can
    # be tight enough to make guessing pointless without ever inconveniencing
    # the only legitimate user.
    RATE_LIMIT_DASHBOARD_LOGIN: str = "10/hour"
    # How often the SSE stream looks for new rows. A poll rather than an
    # in-process bus on purpose: it touches neither the hot write path in
    # POST /api/events nor the multi-instance fan-out problem, and it works no
    # matter which process wrote the row.
    DASHBOARD_STREAM_POLL_SECONDS: float = 2.0

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
