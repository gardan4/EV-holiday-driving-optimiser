"""A public username, and the secret that authorises writing to it.

This is the smallest thing that answers "where are the trips I planned?"
without becoming an account. There is no email, no password, no session and
nothing to sign in to. The browser makes up a v4 UUID, keeps it, and sends it
as `X-Owner-Secret`; the database stores only `owner_hash` of it. Claiming a
name binds hash → name, and from then on every trip that secret plans is
stamped with it.

The asymmetry is the whole design:

* **Writing needs the secret.** Claiming, releasing, and being stamped as the
  planner all require it, and it never leaves the device that generated it.
* **Reading needs only the name.** `GET /{username}/trips` takes no header and
  no state, because the point is to hand somebody a name and have it work.

That makes claiming a username the CONSENT MOMENT, and the code has to keep it
that way. A trip is stamped only when the secret behind it already belongs to a
claimed name (see `api.trips._plan`), so an unclaimed secret leaves no trace on
any row and trips planned before the claim are never retroactively published.
Anything that stamped first and asked later would publish somebody's history on
the day they picked a name, which is not what they agreed to.

Everything here is hostile input — the endpoints are public and unauthenticated
in the ordinary sense. `USERNAME_RE` is what stops a public write endpoint from
becoming free-form storage, exactly as the campaign-tag pattern does in
`events.normalize_campaign`; the strict v4 gate in `core.visitor` does the same
for the secret.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import desc, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    ClaimUsernameIn,
    ProfileOut,
    TripSummaryOut,
    UserTripsOut,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.core.visitor import request_owner_hash, request_visitor
from app.models import AppEvent, Profile, Trip

logger = logging.getLogger(__name__)

router = APIRouter()

# Lowercase letters, digits and dashes; 3–24 characters; no leading or trailing
# dash. Narrow on purpose — this string ends up in a URL, in a page title, and
# in the database, and a pattern this tight cannot carry a payload, a path
# traversal, or anybody's data.
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,22}[a-z0-9]$")

# Names that would collide with a route, or imply an authority the holder does
# not have. `me` matters most: `GET /api/users/me` is the whoami endpoint, so a
# profile called "me" would be shadowed by it and unreachable.
RESERVED_USERNAMES = frozenset(
    {
        "me",
        "admin",
        "administrator",
        "api",
        "u",
        "user",
        "users",
        "trip",
        "trips",
        "ev",
        "privacy",
        "new",
        "about",
        "help",
        "support",
        "root",
        "system",
        "evtrip",
        "null",
        "undefined",
    }
)

# How many trips a public list returns. A cap rather than pagination: this is a
# holiday planner, the list is for finding a trip you made, and nobody needs
# page 4 of their own road trips more than they need one fast query.
LIST_LIMIT = 100


def normalize_username(raw: str | None) -> str | None:
    """The stored form of a username, or None if it isn't one.

    Lowercased before validation rather than rejected for case, so "Marc" claims
    "marc" instead of failing for a reason nobody would guess from the form.
    """
    if not raw:
        return None
    name = raw.strip().lower()
    if not USERNAME_RE.match(name) or name in RESERVED_USERNAMES:
        return None
    return name


def locality(label: str | None) -> str:
    """The town out of a geocoded label, for a list anyone can read.

    The public list must never carry a doorstep, and this is the function that
    decides whether it does. Done SERVER-SIDE on purpose: a truncation performed
    in the browser is one a caller can simply decline to perform.

    **Not the first comma-separated part.** That is what `ResultsView` shows in
    the results headline, and it is right there because that page is behind the
    share link — but on a page anybody can reach by guessing a name it is
    exactly wrong. ORS/Pelias formats an address as
    `"Kerkstraat 12, Baarn, UT, Netherlands"`, so the first part IS the street
    and the house number.

    The city is consistently third from the end, because the label ends with the
    country and (above two parts) a region code:

        Kerkstraat 12, Baarn, UT, Netherlands   → Baarn
        Flughafen Innsbruck, Innsbruck, TR, Austria → Innsbruck
        Innsbruck, TR, Austria                  → Innsbruck
        Utrecht, Netherlands                    → Utrecht
        Innsbruck                               → Innsbruck

    Every way this can be wrong makes the answer COARSER — a region instead of a
    town — because it moves toward the country end of the label. The digit guard
    is the backstop for a provider that omits the city from an address: a house
    number always has a digit and a town essentially never does, so a candidate
    containing one is skipped rather than published.
    """
    if not label:
        return "Unknown"
    parts = [p.strip() for p in label.split(",") if p.strip()]
    if not parts:
        return "Unknown"

    i = max(0, len(parts) - 3)
    while i < len(parts) - 1 and any(ch.isdigit() for ch in parts[i]):
        i += 1
    return parts[i][:60] or "Unknown"


def _summary(trip: Trip) -> TripSummaryOut | None:
    """A public-safe summary of one trip, or None if the row can't be read.

    Never raises. A trip whose JSON predates a schema change, or is malformed
    for any other reason, is skipped — one unreadable row must not turn
    somebody's whole list into a 500. Same posture as `build_trip_stat`.
    """
    try:
        req = trip.request or {}
        res = trip.result or {}
        veh = res.get("vehicle") or {}
        vehicle_label = " ".join(
            str(p) for p in (veh.get("make"), veh.get("model"), veh.get("variant")) if p
        ).strip()

        optimum = res.get("optimum_speed")
        n_stops = None
        if optimum is not None:
            for s in res.get("speeds") or []:
                if s.get("speed_kph") == optimum:
                    n_stops = s.get("n_stops")
                    break

        departure = req.get("departure_iso")
        if isinstance(departure, str):
            try:
                departure = datetime.fromisoformat(departure.replace("Z", "+00:00"))
            except ValueError:
                departure = None

        return TripSummaryOut(
            id=str(trip.id),
            created_at=trip.created_at,
            origin_label=locality((req.get("origin") or {}).get("label")),
            dest_label=locality((req.get("dest") or {}).get("label")),
            distance_km=round(float(res.get("total_dist_m") or 0.0) / 1000.0),
            vehicle_label=vehicle_label or "Unknown car",
            departure_iso=departure,
            optimum_speed_kph=optimum,
            n_stops=n_stops,
        )
    except Exception:
        logger.warning("skipped unreadable trip %s… in a public list", str(trip.id)[:8])
        return None


async def _count(db: AsyncSession, request: Request, name: str) -> None:
    """Count a claim or a release, with nobody attached to it.

    Recorded here rather than from the browser for the reason
    `api.trips._record_plan_failure` gives: the server is the only party that
    knows the state change actually happened, and an event fired from a page can
    be blocked — which would measure claims against a different population than
    the `profiles` table they get compared with. `events.SERVER_ONLY_EVENTS`
    refuses both names from the public endpoint, so the two counts cannot be
    pushed apart by anybody but us.

    Deliberately thinner than every other row in `app_events`. **No username** —
    that is the whole point, and it is the same rule that makes
    `events.normalize_path` collapse `/u/<name>` before storage. No path either:
    this is an API call, not a page, and inventing one would put a fake entry in
    the page breakdown. And no persistent client id, which is the interesting
    omission: attached to a claim it would say which browser holds a name, and
    a browser holding a name is a person. `visitor` is non-null in the schema
    and is the daily-rotating pseudonym, so what remains cannot be followed past
    midnight.

    Never raises. Somebody's username is worth more than the row counting it.
    """
    try:
        db.add(AppEvent(name=name, path=None, visitor=request_visitor(request)))
        await db.commit()
    except Exception:  # noqa: BLE001 — counting must not fail the operation
        logger.warning("could not count %s", name, exc_info=True)
        await db.rollback()


async def _profile_by_name(db: AsyncSession, username: str) -> Profile:
    """The profile, or a 404 that says nothing about which part was wrong."""
    row = (
        await db.execute(select(Profile).where(Profile.username == username))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No such username.")
    return row


@router.post("", response_model=ProfileOut, status_code=201)
@limiter.limit(settings.RATE_LIMIT_CLAIM)
async def claim_username(
    request: Request,
    body: ClaimUsernameIn,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> ProfileOut:
    """Bind a name to the caller's secret. This is the opt-in.

    The pre-check SELECTs below are for the error message; the UNIQUE
    constraints are the actual guarantee. Two browsers claiming the same name in
    the same millisecond both pass the check and one of them loses at the
    insert, which is exactly what `IntegrityError` → 409 is for.
    """
    owner = request_owner_hash(request)
    if owner is None:
        # Deliberately not saying which of "missing" and "malformed" it was.
        raise HTTPException(status_code=401, detail="No usable owner secret.")

    name = normalize_username(body.username)
    if name is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Use 3–24 characters: lowercase letters, numbers and dashes, "
                "not starting or ending with a dash. Some names are reserved."
            ),
        )

    mine = (
        await db.execute(select(Profile).where(Profile.owner_hash == owner))
    ).scalar_one_or_none()
    if mine is not None:
        if mine.username == name:
            # Idempotent: re-claiming your own name is what a retry looks like.
            # 200 rather than the route's 201 — nothing was created this time.
            response.status_code = 200
            return ProfileOut(username=mine.username, created_at=mine.created_at)
        raise HTTPException(
            status_code=409,
            detail=f"This browser already has the username “{mine.username}”. "
            "Release it first.",
        )

    taken = (
        await db.execute(select(Profile.id).where(Profile.username == name))
    ).scalar_one_or_none()
    if taken is not None:
        raise HTTPException(status_code=409, detail="That username is taken.")

    profile = Profile(username=name, owner_hash=owner, created_at=datetime.utcnow())
    db.add(profile)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="That username is taken.")

    # The name is public, so logging it leaks nothing the URL doesn't. The
    # secret and its hash are never logged.
    logger.info("username claimed: %s", name)
    # Counted only on the path that created something. The idempotent re-claim
    # above returns before this, because a retry is not a second person picking
    # a name and counting it would make claims exceed the profiles table by
    # however many times a flaky connection was retried.
    await _count(db, request, "profile_claimed")
    return ProfileOut(username=profile.username, created_at=profile.created_at)


@router.get("/me", response_model=ProfileOut)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def who_am_i(
    request: Request, db: AsyncSession = Depends(get_db)
) -> ProfileOut:
    """Which username this secret holds — the second-device flow, and nothing else.

    A deliberate, narrow exception to "the planner is recorded; the reader is
    not". Every other read in this app is anonymous; this one takes the secret
    because it is the owner asking about their own name, having just pasted
    their code into a second browser. It writes nothing and records no event.
    """
    owner = request_owner_hash(request)
    if owner is None:
        raise HTTPException(status_code=401, detail="No usable owner secret.")
    row = (
        await db.execute(select(Profile).where(Profile.owner_hash == owner))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="This browser has no username.")
    return ProfileOut(username=row.username, created_at=row.created_at)


@router.get("/{username}/trips", response_model=UserTripsOut)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def list_user_trips(
    request: Request, username: str, db: AsyncSession = Depends(get_db)
) -> UserTripsOut:
    """Everything published under a name. Public, stateless, no secret.

    Takes no owner header on purpose. Someone reading a friend's list should not
    have to hold anything, and an endpoint that accepted a secret it does not
    need would be an endpoint that could start depending on one.
    """
    name = (username or "").strip().lower()
    if not USERNAME_RE.match(name):
        # Same 404 as an unknown name: a malformed one is not a different fact.
        raise HTTPException(status_code=404, detail="No such username.")
    profile = await _profile_by_name(db, name)

    rows = (
        (
            await db.execute(
                select(Trip)
                .where(Trip.owner_hash == profile.owner_hash)
                .order_by(desc(Trip.created_at))
                .limit(LIST_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    summaries = [s for s in (_summary(t) for t in rows) if s is not None]
    return UserTripsOut(
        username=profile.username, created_at=profile.created_at, trips=summaries
    )


@router.delete("/{username}", status_code=204)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def release_username(
    request: Request, username: str, db: AsyncSession = Depends(get_db)
) -> Response:
    """Give up a name and unpublish everything under it.

    This is the "stop" the privacy page promises, so it has to be complete: the
    profile row goes, and every trip that carried the stamp is detached. The
    trips themselves and their share links survive — releasing a name is not
    asking us to delete your journeys, and conflating the two would make the
    button too frightening to use. Deleting a trip is its own control.

    Detaching rather than keeping the stamp is what makes the release final: a
    later claim of the same name by the same browser starts empty rather than
    resurrecting a list the owner thought they had taken down.
    """
    owner = request_owner_hash(request)
    name = (username or "").strip().lower()
    if not USERNAME_RE.match(name):
        raise HTTPException(status_code=404, detail="No such username.")
    profile = await _profile_by_name(db, name)

    # Both sides are already salted hashes, so this is not guarding a secret
    # prefix; compare_digest is here so the comparison stays constant-time if
    # this ever grows into something where that matters.
    if owner is None or not secrets.compare_digest(owner, profile.owner_hash):
        raise HTTPException(
            status_code=403, detail="This browser does not hold that username."
        )

    await db.execute(
        update(Trip)
        .where(Trip.owner_hash == profile.owner_hash)
        .values(owner_hash=None)
    )
    await db.delete(profile)
    await db.commit()
    logger.info("username released: %s", name)
    # The only trace a release leaves. Deleting the profile takes its
    # `created_at` with it, so without this row a name claimed and given back
    # inside one window is invisible in both directions — the claim count and
    # the live count would agree, and churn would read as nothing happening.
    await _count(db, request, "profile_released")
    return Response(status_code=204)
