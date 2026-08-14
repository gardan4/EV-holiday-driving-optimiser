"""The admin dashboard's own API. Mounted only under `PROCESS_ROLE=dashboard`.

Separate from `api/events.py` on purpose. That module's `GET /stats` is a
machine-to-machine endpoint authenticated by a header, used by
`scripts.usage_report` and kept working unchanged. This one is a browser app:
same capability, but the token is exchanged once for a session cookie so it
never reaches client JavaScript, where a stray extension or an XSS could read
it back out.

Three properties are load-bearing.

**Default-deny at the role level.** An empty `STATS_TOKEN` means every route
here 404s, exactly as `/api/events/stats` already does. That is the same
reasoning as `EXPOSE_DOCS`: the one publicly reachable deployment is the
environment named `dev`, so a guard keyed on `ENV` would be false precisely
where it matters. There is no second secret to provision — "the usage numbers
are not readable" and "the dashboard does not exist" are the same statement.

**The session is stateless.** Fernet already carries an authenticated
timestamp, so `decrypt(..., ttl=...)` is the whole expiry mechanism — no
session table, no migration, and nothing to purge. `core/security.py` has held
these helpers unused since the repo was created; this is what they were for.

**Nothing here writes.** Every route is a read over tables that already exist,
which is why `frontend/app/privacy/page.tsx` needs no change for any of it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime, timedelta

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.rate_limit import limiter
from app.core.security import get_fernet
from app.models import AppEvent
from app.services import (
    corridors,
    drives,
    profiles,
    quota,
    trip_shape,
    upstream_health,
)
from app.services.analytics import (
    FILTERABLE,
    BadQuery,
    Filters,
    breakdown,
    compare,
    funnel,
    retention,
    timeseries,
    totals,
)
from app.services.analytics.filters import DIMENSIONS, MEASURES

logger = logging.getLogger(__name__)

router = APIRouter()

SESSION_COOKIE = "evtrip_dash"


# ── session ───────────────────────────────────────────────────────────


def _require_configured() -> None:
    """404 the whole surface when no token is set. See the module docstring."""
    if not settings.STATS_TOKEN:
        raise HTTPException(status_code=404, detail="Not found.")


def issue_session() -> str:
    payload = json.dumps({"iat": datetime.utcnow().isoformat()})
    return get_fernet().encrypt(payload.encode()).decode()


def session_valid(token: str | None) -> bool:
    if not token:
        return False
    try:
        get_fernet().decrypt(
            token.encode(), ttl=settings.DASHBOARD_SESSION_HOURS * 3600
        )
        return True
    except (InvalidToken, ValueError, TypeError):
        # Expired, tampered with, or signed by a different ENCRYPTION_KEY.
        # All three mean the same thing to the caller: sign in again.
        return False


async def require_session(request: Request) -> None:
    """Gate every data route.

    401 rather than 404 once a token IS configured: the SPA needs to tell
    "wrong password" apart from "there is no dashboard here" so it knows to
    show the sign-in form rather than an error page. Before configuration,
    `_require_configured` has already 404'd and the distinction cannot leak.
    """
    _require_configured()
    if not session_valid(request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status_code=401, detail="Sign in required.")


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=512)


@router.post("/api/login")
@limiter.limit(settings.RATE_LIMIT_DASHBOARD_LOGIN)
async def login(request: Request, body: LoginBody, response: Response) -> dict:
    _require_configured()
    # Constant-time: a plain != leaks the shared prefix to anyone patient.
    if not secrets.compare_digest(body.password, settings.STATS_TOKEN):
        raise HTTPException(status_code=401, detail="Wrong password.")

    forwarded = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        SESSION_COOKIE,
        issue_session(),
        max_age=settings.DASHBOARD_SESSION_HOURS * 3600,
        httponly=True,
        # Secure only where the connection actually is: setting it
        # unconditionally means the cookie is silently dropped on a plain-http
        # local run and the dashboard can never be signed into in development.
        secure=(forwarded == "https"),
        samesite="lax",
        path="/",
    )
    return {"ok": True, "hours": settings.DASHBOARD_SESSION_HOURS}


@router.post("/api/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/api/session")
async def session_state(request: Request) -> dict:
    """Whether the current cookie is still good — the SPA's boot check."""
    _require_configured()
    return {"authenticated": session_valid(request.cookies.get(SESSION_COOKIE))}


# ── filters shared by every data route ────────────────────────────────


def filters_from_query(
    days: int = Query(7, ge=1, le=90),
    device: str | None = None,
    browser: str | None = None,
    os: str | None = None,
    country: str | None = None,
    campaign: str | None = None,
    viewport: str | None = None,
    path: str | None = None,
    referrer: str | None = None,
    referrer_path: str | None = None,
) -> Filters:
    """One dependency, so every panel is filtered by the same query string.

    The values themselves are not validated against an allowlist — they are
    bound as parameters, never interpolated, and the set of legal values for
    `device` is whatever `client_context` happened to store. An unknown value
    simply matches no rows, which is the correct answer to "show me traffic
    from a browser that has never visited".
    """
    return Filters.window(
        days,
        device=device,
        browser=browser,
        os=os,
        country=country,
        campaign=campaign,
        viewport=viewport,
        path=path,
        referrer=referrer,
        referrer_path=referrer_path,
    )


@router.get("/api/meta", dependencies=[Depends(require_session)])
async def meta() -> dict:
    """What the UI is allowed to ask for — it builds its own controls from this.

    Sending the allowlist rather than hardcoding it in the SPA means a
    dimension added in Python appears as a filter chip without a frontend
    change, and one removed cannot leave a control behind that 400s.
    """
    # Derived from the simulator, never hand-typed: the map shades countries by
    # whether the planner has a real motorway cap for them, and a hardcoded copy
    # of that list would go quietly wrong the day a country is added — showing
    # the map's own guess as if it were the model's.
    from app.services.simulator import SimParams, _default_country_caps

    return {
        "dimensions": sorted(DIMENSIONS),
        "filterable": sorted(FILTERABLE),
        "measures": sorted(MEASURES),
        "granularities": ["hour", "day"],
        "max_days": 90,
        "stream_poll_seconds": settings.DASHBOARD_STREAM_POLL_SECONDS,
        "modelled_countries": sorted(_default_country_caps()),
        # What everywhere else is assumed to be. The assumptions panel in the
        # app says this out loud; so should the map.
        "default_cap_kph": SimParams().default_country_cap_kph,
    }


@router.get("/api/query", dependencies=[Depends(require_session)])
async def query(
    dimension: str = Query(..., description="A name from /api/meta dimensions"),
    measure: str = "events",
    event: str | None = None,
    limit: int = Query(10, ge=1, le=50),
    f: Filters = Depends(filters_from_query),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """The drilldown endpoint: any allowlisted dimension, in the current segment."""
    try:
        rows = await breakdown(
            db, f, dimension, m=measure, event=event, limit=limit
        )
    except BadQuery as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "dimension": dimension,
        "measure": measure,
        "filters": f.active(),
        "rows": [{"label": r.label, "value": r.value} for r in rows],
    }


@router.get("/api/series", dependencies=[Depends(require_session)])
async def series(
    measure: str = "events",
    granularity: str = "day",
    event: str | None = "page_view",
    f: Filters = Depends(filters_from_query),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        points = await timeseries(
            db, f, m=measure, granularity=granularity, event=event
        )
    except BadQuery as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "measure": measure,
        "granularity": granularity,
        "points": [{"bucket": p.bucket, "value": p.value} for p in points],
    }


@router.get("/api/overview", dependencies=[Depends(require_session)])
async def overview(
    granularity: str = "day",
    f: Filters = Depends(filters_from_query),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Everything the front page of the dashboard needs, in one round trip.

    One request rather than fifteen because every panel shares the same filter
    — issuing them separately would let the tiles and the chart briefly
    disagree while the second response was in flight, which reads as a bug in
    the numbers rather than in the loading.
    """
    try:
        views = await timeseries(db, f, m="events", granularity=granularity)
        visitors = await timeseries(db, f, m="visitors", granularity=granularity)
    except BadQuery as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    stages, failures = await funnel(db, f)
    seen = await retention(db, f)
    deltas = await compare(db, f)

    providers = [
        {
            "provider": p.provider,
            "calls_today": p.calls_today,
            "cache_hits_today": p.cache_hits_today,
            "failures_today": p.failures_today,
            "avg_ms": round(p.avg_ms, 1),
            "calls_window": p.calls_window,
            "daily_quota": p.daily_quota,
            "hit_rate": p.hit_rate,
            # None when the provider publishes no ceiling. Withheld rather than
            # invented against a made-up denominator (see `quota._quota`).
            "headroom_pct": p.headroom_pct,
        }
        for p in await quota.quota_report(db, days=f.days)
    ]

    return {
        "window": {
            "days": f.days,
            "since": f.since.isoformat(),
            "until": f.until.isoformat(),
            "granularity": granularity,
            "filters": f.active(),
        },
        "totals": deltas,
        "series": {
            "page_views": [{"bucket": p.bucket, "value": p.value} for p in views],
            "visitors": [{"bucket": p.bucket, "value": p.value} for p in visitors],
        },
        "funnel": [
            {
                "name": s.name,
                "label": s.label,
                "count": s.count,
                "from_previous": s.from_previous,
            }
            for s in stages
        ],
        "plan_failures": failures,
        "retention": {
            "identified": seen.identified,
            "returning": seen.returning,
            # Quote the ratio with its denominator, never `returning` alone.
            "rate": seen.rate,
        },
        "providers": providers,
    }


@router.get("/api/segments", dependencies=[Depends(require_session)])
async def segments(
    f: Filters = Depends(filters_from_query),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Who is using it, and where they arrived from."""
    async def rows(dim: str, **kw):
        return [
            {"label": s.label, "value": s.value}
            for s in await breakdown(db, f, dim, **kw)
        ]

    return {
        "countries": await rows("country"),
        "devices": await rows("device"),
        "browsers": await rows("browser"),
        "operating_systems": await rows("os"),
        "viewports": await rows("viewport"),
        "paths": await rows("path", event="page_view", null_label="unknown"),
        "referrers": await rows("referrer"),
        "referrer_paths": await rows("referrer_path", limit=15),
        "campaigns": await rows("campaign"),
        "failure_reasons": await rows("reason", event="plan_failed", limit=50),
    }


@router.get("/api/journeys", dependencies=[Depends(require_session)])
async def journeys(
    f: Filters = Depends(filters_from_query),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Corridors, trip shape, and what the drives actually did.

    Note these read `trip_stats` and `trip_runs`, not `app_events`, so the
    event-side facet chips do not apply — there is no device or campaign on a
    corridor row, by design (see the `trip_stats` note in CLAUDE.md). Only the
    window carries over, which the UI says out loud.
    """
    cutoff = f.since
    top = await corridors.top_corridors(db, cutoff, 12)
    hardest, infeasible = await corridors.charging_gaps(db, cutoff, 12)
    unmodelled, unplaceable = await corridors.coverage_gap(db, cutoff)
    repeat = await corridors.repeat_planners(db, cutoff)
    cars = await corridors.by_vehicle(db, cutoff, limit=12)
    shape = await trip_shape.summary(db, cutoff)
    speeds = await trip_shape.speed_choice(db, cutoff)
    drive = await drives.summary(db, cutoff)
    stops = await drives.charge_stops(db, cutoff)
    pva = await drives.plan_vs_actual(db, cutoff)

    def corr(c) -> dict:
        return {
            "label": c.label,
            "trips": c.trips,
            "distance_km": c.distance_km,
            "avg_stops": c.avg_stops,
            "stops_per_100km": c.stops_per_100km,
        }

    return {
        "corridors": {
            "top": [corr(c) for c in top],
            # Ranked by stops per 100 km, not raw stops — raw stops just lists
            # the longest routes, which is a fact about geography.
            "hardest": [corr(c) for c in hardest],
            "infeasible": [corr(c) for c in infeasible],
            "unmodelled_countries": [
                {"label": cc, "value": n} for cc, n in unmodelled[:12]
            ],
            "unplaceable_trips": unplaceable,
        },
        "shape": {
            "trips": shape.trips,
            "feasible": shape.feasible,
            "feasible_rate": shape.feasible_rate,
            "mean_km": round(shape.mean_km, 1),
            "mean_charge_share": shape.mean_charge_share,
            "median_charge_share": shape.median_charge_share,
            "distances": [{"label": s.label, "value": s.value} for s in shape.distances],
            "charge_shares": [
                {"label": s.label, "value": s.value} for s in shape.charge_shares
            ],
            "months": [{"label": s.label, "value": s.value} for s in shape.months],
            "transit_countries": [
                {"label": s.label, "value": s.value} for s in shape.transit_countries
            ],
            "speeds": [{"label": f"{int(s)}", "value": n} for s, n in speeds],
        },
        "drives": {
            "runs": drive.runs,
            "finished": drive.finished,
            "active": drive.active,
            "abandoned": drive.abandoned,
            "completion_rate": drive.completion_rate,
            "median_minutes": drive.median_minutes,
            "replan_rate": drive.replan_rate,
            "off_route_rate": drive.off_route_rate,
            "total_replans": drive.total_replans,
            "median_pings": drive.median_pings,
            "charge_stops": [
                {"minutes": round(c.minutes, 1), "soc_start": c.soc_start, "soc_end": c.soc_end}
                for c in stops
            ],
            "plan_vs_actual": [
                {"planned_kph": round(p, 1), "achieved_kph": round(a, 1)} for p, a in pva
            ],
        },
        "planners": [{"label": k, "value": n} for k, n in repeat],
        "cars": [
            {"label": name, "value": n, "avg_optimum_kph": speed}
            for name, n, speed in cars
        ],
    }


@router.get("/api/names", dependencies=[Depends(require_session)])
async def names(
    f: Filters = Depends(filters_from_query),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Whether the username feature is used — counts, never a list of names.

    Its own endpoint rather than a block inside `/api/overview` for the same
    reason `/api/journeys` is separate: half of it reads `profiles` and `trips`,
    which carry no device, campaign or referrer, so the facet chips cannot
    narrow it. Folded into the filtered overview it would sit next to tiles that
    respond to a chip while it silently did not.

    There is deliberately no drilldown here. Every other panel on the console
    can be clicked into a narrower question; this one bottoms out at a count,
    because the next question down is "which name", and that is a person.
    """
    return profiles.as_dict(await profiles.name_stats(db, f))


@router.get("/api/upstream", dependencies=[Depends(require_session)])
async def upstream(
    days: int = Query(14, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Provider health as a trend, not just today's totals."""
    return {
        "daily": {
            provider: [
                {
                    "day": d.day,
                    "calls": d.calls,
                    "cache_hits": d.cache_hits,
                    "failures": d.failures,
                    "operations": d.operations,
                    "hit_rate": d.hit_rate,
                }
                for d in await upstream_health.daily(db, provider, days=days)
            ]
            for provider in upstream_health.PROVIDERS
        },
        "latency": [
            {
                "provider": lat.provider,
                "kind": lat.kind,
                "operations": lat.operations,
                "p50_ms": lat.p50_ms,
                "p95_ms": lat.p95_ms,
                "max_ms": lat.max_ms,
            }
            for lat in await upstream_health.latency(db, days=days)
        ],
    }


# ── the live stream ───────────────────────────────────────────────────


def _sse(event: str, data: dict, event_id: str | None = None) -> str:
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _tail(last_seen: datetime):
    """Yield SSE frames forever, from a cursor on `app_events.at`.

    A poll rather than an in-process pub/sub, deliberately. A bus would have to
    be published to from `POST /api/events`, which is the hot write path this
    stream exists to watch — and it would only ever see rows written by its own
    process, so the moment the API tier has two instances the dashboard starts
    silently missing half the traffic. Polling an indexed column costs one cheap
    range scan every couple of seconds and is correct regardless of topology.

    Its own session, not the request's: this generator outlives the request
    handler, and a session closed underneath it would fail on the next tick.
    """
    poll = max(0.5, settings.DASHBOARD_STREAM_POLL_SECONDS)
    beat = 0.0

    # Tell the browser how long to wait before reconnecting. EventSource
    # defaults to about 3s, so a server that is down — restarting, or being
    # rebuilt — collects a retry every three seconds from every open tab. Five
    # seconds is still faster than anyone notices and a third of the noise.
    yield "retry: 5000\n\n"

    while True:
        try:
            async with AsyncSessionLocal() as db:
                rows = (
                    await db.execute(
                        select(
                            AppEvent.at,
                            AppEvent.name,
                            AppEvent.path,
                            AppEvent.country,
                            AppEvent.device,
                            AppEvent.campaign,
                            AppEvent.reason,
                        )
                        .where(AppEvent.at > last_seen)
                        .order_by(AppEvent.at)
                        .limit(200)
                    )
                ).all()

                for at, name, path, country, device, campaign, reason in rows:
                    last_seen = max(last_seen, at)
                    # Exactly the columns already stored — the stream cannot
                    # show more about a visitor than the table holds, and the
                    # pseudonyms are not among the columns selected.
                    yield _sse(
                        "event",
                        {
                            "at": at.isoformat(),
                            "name": name,
                            "path": path,
                            "country": country,
                            "device": device,
                            "campaign": campaign,
                            "reason": reason,
                        },
                        event_id=at.isoformat(),
                    )

                if rows or beat >= 10:
                    f = Filters.window(1)
                    yield _sse("counters", await totals(db, f))
                    beat = 0.0
        except asyncio.CancelledError:
            raise
        except Exception:
            # A DB blip must not end the stream for the life of the page — the
            # client would reconnect anyway, but a quiet retry is cheaper than
            # a reconnect storm if the database is what is struggling.
            logger.warning("dashboard stream tick failed", exc_info=True)

        # A comment frame: keeps App Service's idle timeout from cutting a
        # connection that is legitimately quiet at 3am.
        yield ": heartbeat\n\n"
        await asyncio.sleep(poll)
        beat += poll


@router.get("/api/stream", dependencies=[Depends(require_session)])
async def stream(request: Request) -> StreamingResponse:
    """Server-sent events: new rows as they land.

    Resumes from `Last-Event-ID` when the browser reconnects, so a dropped
    connection replays what was missed instead of leaving a hole in the ticker.
    """
    resume = request.headers.get("last-event-id")
    start = datetime.utcnow() - timedelta(minutes=5)
    if resume:
        try:
            start = datetime.fromisoformat(resume)
        except ValueError:
            pass

    return StreamingResponse(
        _tail(start),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # Tell any reverse proxy not to buffer — nginx in particular will
            # hold the whole response otherwise and the stream never arrives.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
