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
from app.core.client_context import (
    classify_browser,
    classify_device,
    classify_os,
    classify_viewport,
    request_country,
)
from app.core.visitor import request_client_id, request_visitor
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
        "/u/:username",
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

# Public username pages, collapsed for the same reason as the catalog slugs and
# one stronger one. A username is a name somebody chose for themselves, and it
# is the only handle in this app that could plausibly be a real one — storing it
# next to a visitor pseudonym would build precisely the "who looked at whose"
# row this table is designed not to hold. Collapsed here, before `_OPAQUE_RE`
# gets a look in, so that "marc" and "marc-2026" land in the same bucket rather
# than splitting across "/other" and ":id" on whether the name has a digit.
_USERNAME_PATH_RE = re.compile(r"^/u/[a-z0-9-]+$")


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
    if _USERNAME_PATH_RE.match(path):
        return "/u/:username"

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


def normalize_referrer_path(raw: str | None) -> str | None:
    """The path of the referring page, with the query string thrown away.

    "reddit.com" tells you Reddit worked; "/r/electricvehicles/comments/1a2b3c/"
    tells you which post did, which is the difference between knowing a channel
    works and knowing which audience it was.

    **The query string is always dropped, and that is the important part.** A
    referrer arriving from a webmail, a search, a password reset or an intranet
    tool carries tokens, addresses and search terms in its query — the single
    most likely way for somebody else's secret to end up in this table. The path
    alone is a far smaller surface, and it is all the question needs.

    Returns None for the bare "/" of a site's front page: it adds nothing to the
    host already stored, and would otherwise be the most common row here.
    """
    if not normalize_referrer(raw):
        # Same gate as the host, so an internal navigation cannot leave a path
        # behind after its host was deliberately discarded.
        return None
    path = urlsplit(raw.strip()).path or ""
    if not path or path == "/":
        return None
    return path[:200]


# Events the SERVER writes, which this public endpoint therefore refuses.
#
# `plan_failed` is recorded by `api.trips` with the reason it actually failed
# for. Left acceptable here, anyone could post reasonless failures and inflate
# the count — and the funnel total would stop matching the reason breakdown,
# which is the one cross-check that says the failure numbers are real.
#
# The two profile events are here for a stronger version of the same reason.
# Each one records a state change that only the server can confirm happened:
# accepted from a browser, "names claimed" would be a number anybody could type
# into the database, and it is checked against the `profiles` table — where
# claims are also counted from `created_at` — precisely so the pair can be
# compared. Two counts that can be forged apart are not a cross-check.
SERVER_ONLY_EVENTS = frozenset(
    {"plan_failed", "profile_claimed", "profile_released"}
)

# A campaign tag: lowercase letters, digits and dashes. Deliberately narrow —
# this arrives on a public endpoint and is written to the database, so the
# pattern is what stops it becoming free-form storage.
_CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def normalize_campaign(raw: str | None) -> str | None:
    """The `?src=` tag a visitor arrived with, or None.

    This exists because the referrer cannot answer "which subreddit worked".
    Reddit's mobile app — where most of that traffic is — sends no referrer at
    all, so without a tag on the link the best channel of a launch reads as
    "direct" alongside everyone who typed the URL.

    Two gates, and the first is the one doing the work. The **pattern** is
    always enforced: 32 characters of `[a-z0-9-]` cannot carry a payload, a
    URL, or anybody's data. The **allowlist** (`CAMPAIGN_SOURCES`) is optional
    on top, for when you want the stronger guarantee that only tags you posted
    yourself appear in the numbers; left empty, any well-formed slug is kept,
    which is the difference between remembering to edit a setting before a
    launch and silently recording nothing.
    """
    if not raw:
        return None
    tag = raw.strip().lower()
    if not _CAMPAIGN_RE.match(tag):
        return None
    allowed = {s.strip().lower() for s in settings.CAMPAIGN_SOURCES.split(",") if s.strip()}
    if allowed and tag not in allowed:
        return None
    return tag


@router.post("", status_code=204)
@limiter.limit(settings.RATE_LIMIT_EVENTS)
async def record_event(
    request: Request,
    body: EventIn,
    db: AsyncSession = Depends(get_db),
) -> Response:
    if body.name not in ALLOWED_EVENTS or body.name in SERVER_ONLY_EVENTS:
        raise HTTPException(status_code=422, detail="Unknown event.")

    ua = request.headers.get("user-agent", "")
    db.add(
        AppEvent(
            name=body.name,
            path=normalize_path(body.path),
            visitor=request_visitor(request),
            # Read from a header, not the body: it is cross-cutting metadata
            # rather than part of what happened, and a header keeps it out of
            # any request-body logging that might get switched on later.
            client_id=request_client_id(request),
            referrer=normalize_referrer(body.referrer),
            referrer_path=normalize_referrer_path(body.referrer),
            country=request_country(request),
            device=classify_device(ua),
            browser=classify_browser(ua),
            os=classify_os(ua),
            viewport=classify_viewport(body.viewport_w),
            campaign=normalize_campaign(body.campaign),
        )
    )
    await db.commit()
    return Response(status_code=204)


@router.get("/stats", response_model=UsageStats)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def read_stats(
    request: Request,
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
