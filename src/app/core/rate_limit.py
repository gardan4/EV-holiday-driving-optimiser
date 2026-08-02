"""Rate limiting configuration using slowapi."""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.config import settings


def _client_ip(request: Request) -> str:
    """Resolve the real client IP for rate-limit bucketing.

    In production the path is client -> Cloudflare -> Azure App Service -> app,
    so ``request.client.host`` (what slowapi's get_remote_address returns) is the
    PROXY's IP — collapsing every user into one shared bucket. We instead trust
    Cloudflare's ``CF-Connecting-IP`` (the single most reliable value), then the
    first hop of ``X-Forwarded-For``. These headers are only trustworthy because
    the app is reachable solely through the proxy in production; locally they're
    absent and we fall back to the socket peer.
    """
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # Left-most entry is the original client (proxies append on the right).
        return xff.split(",")[0].strip()
    return get_remote_address(request)


# storage_uri lets the limit be shared across App Service instances and survive
# restarts (point RATE_LIMIT_STORAGE_URI at Azure Cache for Redis in prod). When
# unset, slowapi falls back to per-process in-memory storage.
limiter = Limiter(
    key_func=_client_ip,
    storage_uri=settings.RATE_LIMIT_STORAGE_URI or None,
)


def run_key(request: Request) -> str:
    """Bucket live-run writes by the run id in the path, not by IP.

    A carful of phones shares one address, and a mobile carrier's CGNAT puts
    thousands of unrelated drivers behind a single one — an IP bucket tight
    enough to be worth having would throttle legitimate drivers off the road.
    The run id is already a per-driver capability, so it is the honest bucket.
    Falls back to the IP if the path somehow has no run id.
    """
    run_id = request.path_params.get("run_id")
    return f"run:{run_id}" if run_id else _client_ip(request)
