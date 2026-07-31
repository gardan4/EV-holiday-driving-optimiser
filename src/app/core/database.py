import re
import logging
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from app.core.config import settings

logger = logging.getLogger(__name__)

# Validate DATABASE_URL is configured. The engine is created lazily below, so a
# syntactically valid URL is enough to import this module (that's what CI does).
if not settings.DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not configured!\n"
        "Local development: run ./scripts/dev.sh — it starts SQL Server and\n"
        "creates .env from .env.example (which already points at it).\n"
        "In Azure the value is injected as an App Service setting by Bicep."
    )


def _detect_odbc_driver() -> str:
    """Detect the installed ODBC driver for SQL Server."""
    try:
        import pyodbc
        drivers = [d for d in pyodbc.drivers() if 'SQL Server' in d]
        # Prefer the highest version ODBC Driver
        for version in [18, 17]:
            driver_name = f"ODBC Driver {version} for SQL Server"
            if driver_name in drivers:
                return driver_name
        # Fall back to first available
        if drivers:
            return drivers[0]
    except Exception as e:
        logger.warning(f"Could not detect ODBC drivers: {e}")
    # Default to Driver 18 (standard in Docker/Azure)
    return "ODBC Driver 18 for SQL Server"


def _normalize_connection_string(url: str) -> str:
    """Normalize the connection string to use the detected ODBC driver."""
    detected_driver = _detect_odbc_driver()
    
    # Replace any ODBC Driver version with the detected one
    # Handles both URL-encoded (%20) and plain space formats
    patterns = [
        r'driver=ODBC%20Driver%20\d+%20for%20SQL%20Server',
        r'driver=ODBC\+Driver\+\d+\+for\+SQL\+Server',
        r'driver=ODBC Driver \d+ for SQL Server',
    ]
    
    # URL-encode the driver name for connection string
    encoded_driver = detected_driver.replace(' ', '+')
    replacement = f'driver={encoded_driver}'
    
    normalized_url = url
    for pattern in patterns:
        normalized_url = re.sub(pattern, replacement, normalized_url, flags=re.IGNORECASE)
    
    if url != normalized_url:
        logger.info(f"ODBC driver normalized: using '{detected_driver}'")
    
    return normalized_url


# Get normalized connection string
DATABASE_URL = _normalize_connection_string(settings.DATABASE_URL)

# Engine configuration for Azure SQL (MSSQL)
engine_kwargs = {
    "echo": False,  # Set to True for SQL query logging
    "pool_pre_ping": True,  # Test connections before checkout (detects Azure-killed connections)
    "pool_recycle": 300,  # Recycle connections every 5 min (Azure SQL kills idle connections)
    "pool_size": settings.DB_POOL_SIZE,
    "max_overflow": settings.DB_MAX_OVERFLOW,
    # Fail fast instead of wedging: bound the pool checkout wait and the login.
    "pool_timeout": settings.DB_POOL_TIMEOUT,
    "connect_args": {"timeout": settings.DB_LOGIN_TIMEOUT},
}

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    **engine_kwargs,
)


def _apply_query_timeout(dbapi_connection, connection_record):
    """Bound each pyodbc connection's per-query timeout so a stalled query
    errors (and frees the connection) instead of wedging the pool forever.
    0 = unlimited. Applied to both the async (aioodbc→pyodbc) and sync engines."""
    try:
        dbapi_connection.timeout = settings.DB_QUERY_TIMEOUT
    except Exception:
        pass


# aioodbc wraps pyodbc; this connect event fires on the underlying sync engine
# and hands us the raw pyodbc connection, whose `.timeout` is the query timeout.
event.listen(engine.sync_engine, "connect", _apply_query_timeout)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def get_sync_database_url() -> str:
    """Convert async database URL to sync equivalent."""
    # Map async driver to sync equivalent for MSSQL
    # aioodbc (async) -> pyodbc (sync)
    return DATABASE_URL.replace("+aioodbc", "+pyodbc")


# Create sync engine (lazy initialization)
_sync_engine = None


def get_sync_engine():
    """Get or create the synchronous database engine."""
    global _sync_engine
    if _sync_engine is None:
        sync_url = get_sync_database_url()
        if settings.DB_SYNC_POOL_BOUNDED:
            # Bounded QueuePool — opt-in for the Linux worker tier ONLY. Caps the
            # number of sync connections so a runaway agent run blocks (waits up to
            # DB_POOL_TIMEOUT) instead of opening unbounded connections and
            # exhausting the SQL active-worker cap under autoscale. pool_pre_ping +
            # LIFO + recycle keep connections healthy. NEVER enable on macOS/local
            # — a shared pyodbc connection across threads corrupts the ODBC driver.
            from sqlalchemy.pool import QueuePool

            sync_kwargs = {
                "echo": False,
                "poolclass": QueuePool,
                "pool_size": settings.DB_SYNC_POOL_SIZE,
                "max_overflow": settings.DB_SYNC_MAX_OVERFLOW,
                "pool_timeout": settings.DB_POOL_TIMEOUT,
                "pool_pre_ping": True,
                "pool_recycle": 300,
                "pool_use_lifo": True,
                "connect_args": {"timeout": settings.DB_LOGIN_TIMEOUT},
            }
        else:
            # Default: NullPool — open + close a fresh connection per session, no
            # pooling. The sync engine is hit from many `asyncio.to_thread` workers
            # during agent runs; a shared QueuePool reuses a pyodbc connection
            # across threads, which corrupts the MS ODBC driver ("pointer being
            # freed was not allocated"). NullPool avoids cross-thread reuse and is
            # the only safe option on macOS/local.
            from sqlalchemy.pool import NullPool

            sync_kwargs = {
                "echo": False,
                "poolclass": NullPool,
                "connect_args": {"timeout": settings.DB_LOGIN_TIMEOUT},
            }
        _sync_engine = create_engine(sync_url, **sync_kwargs)
        event.listen(_sync_engine, "connect", _apply_query_timeout)
    return _sync_engine


# Create sync session factory
SyncSessionLocal = sessionmaker(class_=Session, expire_on_commit=False)


def get_sync_session() -> Session:
    """Get a synchronous database session for background tasks."""
    return SyncSessionLocal(bind=get_sync_engine())


@contextmanager
def sync_session():
    """Yield a synchronous database session, always closed on exit.

    Use this as a context manager in background tasks and services::

        with sync_session() as session:
            session.query(...)
    """
    session = get_sync_session()
    try:
        yield session
    finally:
        session.close()


class Base(DeclarativeBase):
    """Base class for all database models."""
    pass


async def get_db():
    """Dependency for getting database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Initialize the database by creating all tables.

    Schema changes (new columns, indexes) are managed by Alembic migrations.
    Run `alembic upgrade head` to apply pending migrations.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
