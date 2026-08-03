"""Rate limiting configuration using slowapi."""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.config import settings


def _client_ip(request: Request) -> str:
    """Resolve the real client IP for rate-limit bucketing.

    Getting this wrong in the trusting direction voids every IP-keyed limit in
    the app, so the rule is: only believe a header a trusted proxy is known to
    overwrite.

    ``request.client.host`` is the App Service front-end, not the user, so it
    would collapse everyone into one bucket. Azure App Service *appends* the
    real peer to ``X-Forwarded-For``, which makes the RIGHT-most entry the one
    the platform wrote and the left-most entries whatever the caller invented.
    So we read from the right, and a spoofed `X-Forwarded-For: 1.2.3.4` buys
    the caller nothing.

    ``CF-Connecting-IP`` and the left-most XFF hop are only meaningful with
    Cloudflare actually in front — and nothing is, today, since the App Service
    is directly reachable on its azurewebsites.net name. Behind a proxy that
    strips and rewrites these headers, set TRUSTED_PROXY_HEADERS=true; until
    then trusting them means anyone can mint a fresh rate-limit bucket per
    request just by varying a header.
    """
    if settings.TRUSTED_PROXY_HEADERS:
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip:
            return cf_ip.strip()
        xff = request.headers.get("x-forwarded-for")
        if xff:
            # Left-most entry is the original client (proxies append on the right).
            return xff.split(",")[0].strip()
        return get_remote_address(request)

    xff = request.headers.get("x-forwarded-for")
    if xff:
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        if hops:
            # App Service writes "ip:port"; the port would split one user into
            # a new bucket per connection.
            return hops[-1].rsplit(":", 1)[0] if hops[-1].count(":") == 1 else hops[-1]
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
