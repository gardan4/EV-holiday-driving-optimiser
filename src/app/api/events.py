"""Counting usage without building a tracker.

One public write (`POST /api/events`) and one token-gated read
(`GET /api/events/stats`), mirroring the shape `feedback.py` already uses: a
header token rather than a login, because bolting auth onto an app whose whole
premise is "no accounts" so the owner can read a number would be the tail
wagging the dog.

The write endpoint is unauthenticated and public, so everything it receives is
hostile input. Three rules keep the table from becoming something worse than
the third-party script it replaced:

1. `name` must be in `ALLOWED_EVENTS`. Otherwise a public POST endpoint that
   writes a caller-supplied string to the database is just free storage.
2. `path` is rewritten to a known route pattern, never stored as sent. Trip ids
   are share tokens; a table pairing them with a visitor pseudonym would be a
   record of who looked at whose journey — precisely the correlation the
   privacy page says does not exist.
3. `referrer` is reduced to a bare host. The path of the page somebody arrived
   from can be more identifying than anything on ours.

The counting itself is in `services.usage`; the query logic is shared with
`scripts.usage_report`.
"""

from __future__ import annotations

import logging
import re
import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import EventIn, UsageStats
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.core.visitor import request_visitor
from app.models import AppEvent
from app.services.usage import ALLOWED_EVENTS, usage_stats

logger = logging.getLogger(__name__)

router = APIRouter()

# Every normalised path the app can produce. An allowlist rather than a
# blocklist, so a route added later leaks nothing until somebody consciously
# adds it here; anything unrecognised is stored as "/other".
KNOWN_PATHS = frozenset(
    {
        "/",
        "/privacy",
        "/ev",
        "/ev/:slug",
        "/trip/:id",
        "/trip/:id/live",
        "/trip/:id/drive",
        "/trip/:id/runs/:id",
    }
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
# Run refs are opaque hashes rather than UUIDs, so match on shape: long, and not
# a word. Anything caught by mistake just becomes ":id", which is safe — the
# failure mode of this regex is losing detail, never leaking an id.
_OPAQUE_RE = re.compile(r"^(?=.*\d)[A-Za-z0-9_-]{12,}$")

# Vehicle-catalog pages, collapsed before the generic id rules get a look in.
#
# The slug is public catalog data, not an identifier for anybody, so nothing
# here is about hiding it — it is about counting it consistently. Left to
# `_OPAQUE_RE`, "vw-id3-pro-58" would collapse to ":id" (long, has a digit)
# while "tesla-modely-rwd" would not (no digit), so half the catalog would land
# on "/ev/:slug" and the other half on "/other". One rule, one bucket.
_EV_SLUG_RE = re.compile(r"^/ev/[a-z0-9-]+$")


def normalize_path(raw: str | None) -> str | None:
    """Reduce a URL path to one of `KNOWN_PATHS`, or "/other"."""
    if not raw:
        return None
    # Query and fragment go first: the query string is where a stray id or a
    # typed place name would be, and neither belongs in this table.
    path = urlsplit(raw.strip()).path or "/"
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1:
        path = path.rstrip("/") or "/"

    if _EV_SLUG_RE.match(path):
        return "/ev/:slug"

    parts = [
        ":id" if (_UUID_RE.match(seg) or _OPAQUE_RE.match(seg)) else seg
        for seg in path.split("/")
    ]
    pattern = "/".join(parts) or "/"
    return pattern if pattern in KNOWN_PATHS else "/other"


def normalize_referrer(raw: str | None) -> str | None:
    """Keep the host somebody came from; drop the rest, and drop ourselves.

    Self-referrals are every internal navigation, which would bury the handful
    of rows that answer the only question a referrer is for: how do people find
    this?
    """
    if not raw:
        return None
    host = (urlsplit(raw.strip()).hostname or "").lower()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    own = (urlsplit(settings.FRONTEND_URL).hostname or "").lower()
    if own.startswith("www."):
        own = own[4:]
    if host == own or host in ("localhost", "127.0.0.1"):
        return None
    return host[:120]


@router.post("", status_code=204)
@limiter.limit(settings.RATE_LIMIT_EVENTS)
async def record_event(
    request: Request,
    body: EventIn,
    db: AsyncSession = Depends(get_db),
) -> Response:
    if body.name not in ALLOWED_EVENTS:
        raise HTTPException(status_code=422, detail="Unknown event.")

    db.add(
        AppEvent(
            name=body.name,
            path=normalize_path(body.path),
            visitor=request_visitor(request),
            referrer=normalize_referrer(body.referrer),
        )
    )
    await db.commit()
    return Response(status_code=204)


@router.get("/stats", response_model=UsageStats)
async def read_stats(
    days: int = 7,
    x_stats_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> UsageStats:
    if not settings.STATS_TOKEN:
        raise HTTPException(status_code=404, detail="Not found.")
    # Constant-time: a plain != leaks the shared prefix to anyone patient.
    if not x_stats_token or not secrets.compare_digest(
        x_stats_token, settings.STATS_TOKEN
    ):
        raise HTTPException(status_code=404, detail="Not found.")

    return await usage_stats(db, days=days)
