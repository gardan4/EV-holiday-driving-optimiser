"""Database models for EV Trip Optimizer.

Domain: curated `Vehicle` catalog (consumption + DC charge curves), cached
external data (`Charger` from OpenChargeMap, `RouteCache` from OpenRouteService,
`OcmTile` cache bookkeeping), and `Trip` — a persisted plan result whose UUID id
doubles as the unguessable share token.

There is no auth. `Profile` is not an exception to that: it is a public handle
bound to the hash of a secret the browser made up, with no email, no password
and nothing to sign in to. Holding the secret authorises publishing under the
name; knowing the name is enough to read the list. See its docstring.

MSSQL note: UUID primary keys use the `GUID` TypeDecorator (CHAR(36)); a plain
`uuid` column type has no portable MSSQL mapping. Keep it for every id/FK.
Boolean filters must be written `== True  # noqa: E712` — `.is_(True)` compiles
to `IS 1`, a T-SQL syntax error. JSON columns use SQLAlchemy `JSON`
(NVARCHAR(MAX) on MSSQL, JSON-as-TEXT on the SQLite test engine).

JSON columns here are plain `JSON`, not `MutableDict.as_mutable(JSON)`, so
SQLAlchemy does NOT notice in-place edits: `run.state["soc"] = x` is silently
discarded on commit. Always reassign the whole value —
`run.state = {**run.state, "soc": x}` — as `routing.py` already does.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CHAR,
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    TypeDecorator,
    Unicode,
    UnicodeText,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class GUID(TypeDecorator):
    """Platform-independent GUID type. CHAR(36) for MSSQL compatibility."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


# ---------------------------------------------------------------------------
# Vehicle catalog (curated seed data — see scripts/seed_vehicles.py)
# ---------------------------------------------------------------------------


class Vehicle(Base):
    """A curated EV with the physics the simulator needs.

    `consumption` JSON: {"model": "quadratic", "a_wh_km": 55.0,
    "b_wh_km_per_kph2": 0.0105} → Wh/km(v) = a + b·v².
    `charge_curve` JSON: [[soc_pct, kw], ...] piecewise-linear DC charge power
    (cable-side, as measured by public fast-charge tests) sorted by soc_pct.
    """

    __tablename__ = "vehicles"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True, nullable=False)
    make: Mapped[str] = mapped_column(Unicode(80), nullable=False)
    model: Mapped[str] = mapped_column(Unicode(80), nullable=False)
    variant: Mapped[Optional[str]] = mapped_column(Unicode(80), nullable=True)
    # Which nameplate's page this variant belongs on, e.g. every ID.4 pack size
    # under "vw-id4". Grouping is a curation call, not a string operation —
    # Enyaq 77 and Enyaq 85 are one car to a reader while e-3008 and e-5008 are
    # two — so it is stated per entry rather than derived from make + model.
    # Null falls back to the car's own slug, i.e. a page of its own.
    nameplate_slug: Mapped[Optional[str]] = mapped_column(
        String(60), index=True, nullable=True
    )

    usable_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    consumption: Mapped[dict] = mapped_column(JSON, nullable=False)
    charge_curve: Mapped[list] = mapped_column(JSON, nullable=False)
    max_dc_kw: Mapped[float] = mapped_column(Float, nullable=False)
    # Kerb mass plus a nominal 180 kg of people and luggage — only the
    # elevation term uses it.
    mass_kg: Mapped[float] = mapped_column(Float, nullable=False, default=1900.0)
    # Electronically limited top speed; the sweep stops here because anything
    # beyond it is fiction for this car.
    top_speed_kph: Mapped[float] = mapped_column(Float, nullable=False, default=180.0)
    # Reserved for energy-cost display; charge *time* comes straight from the
    # cable-side curve and must not be scaled by this (no double counting).
    charge_efficiency: Mapped[float] = mapped_column(Float, nullable=False, default=0.92)

    source_note: Mapped[Optional[str]] = mapped_column(UnicodeText, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# External-data caches (OpenChargeMap / OpenRouteService)
# ---------------------------------------------------------------------------


class Charger(Base):
    """OpenChargeMap POI cache, upserted by `ocm_id` per corridor tile fetch."""

    __tablename__ = "chargers"
    __table_args__ = (Index("ix_chargers_lat_lon", "lat", "lon"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    ocm_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    operator: Mapped[Optional[str]] = mapped_column(Unicode(200), nullable=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    max_power_kw: Mapped[float] = mapped_column(Float, nullable=False)
    n_points: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    is_operational: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Trimmed OCM POI payload kept for debugging / future fields.
    raw: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # What OpenStreetMap maps within a few hundred metres of this site: a short
    # list of allowlisted category words ("restaurant", "supermarket"), never
    # names, never free text. Cached far longer than the charger itself,
    # because a services with a bakery in it is a fact about a building.
    # NULL means "never looked"; an empty list means "looked, found nothing
    # mapped" — a distinction the UI has to keep, since OSM coverage is uneven
    # and "nothing mapped" is not "nothing there".
    amenities: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    amenities_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class OcmTile(Base):
    """Bookkeeping: which geohash-4 corridor tiles (~39×20 km) have been fetched
    from OCM and when. Fresh tiles are served entirely from the chargers table."""

    __tablename__ = "ocm_tiles"

    tile_key: Mapped[str] = mapped_column(String(12), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RouteCache(Base):
    """ORS directions cache. Key = sha256 over geohash6-snapped origin/dest
    (~±600 m), so autocomplete-picked places hit reliably."""

    __tablename__ = "route_cache"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # Condensed route: polyline coords, per-segment {dist_m, dur_s}, totals.
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Trips — persisted plan results; the UUID id is the share token
# ---------------------------------------------------------------------------


class Profile(Base):
    """A public username, bound to the hash of a browser-generated secret.

    The smallest thing that answers "where are my trips?" without becoming an
    account. There is no email, no password and no session: the browser makes up
    a v4 UUID, keeps it, and sends it as `X-Owner-Secret` when it writes. We
    store only `owner_hash` (`core.visitor.owner_hash`), so a copy of this table
    is a list of names and unusable hashes rather than a set of working keys.

    Reading is public and stateless — anybody who knows the username can list
    the trips under it, with no secret and no state of their own. That is the
    feature, and it is why **claiming a username is the consent moment**: a trip
    is stamped only when the secret that planned it already belongs to a claimed
    name, so trips planned before the claim are not retroactively published and
    an unclaimed secret leaves no trace on any row.

    Two unique constraints carry the semantics. `username` unique is
    first-come-first-served. `owner_hash` unique is one name per secret — which
    is what makes "release, then claim again" the only way to rename, and keeps
    a single browser from quietly accumulating handles.

    Releasing (`DELETE /api/users/{username}`) deletes this row and nulls the
    stamp on every trip that carried it. The trips and their share links
    survive; the public page and the link between them do not.
    """

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    # Pattern-validated in `api/users.py` to `[a-z0-9-]`, so ASCII — `String`
    # (VARCHAR on MSSQL) is correct here, unlike the catalog's `Unicode` text.
    username: Mapped[str] = mapped_column(
        String(24), unique=True, index=True, nullable=False
    )
    # 32 hex chars = 128 bits, matching the other pseudonyms in this schema.
    owner_hash: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, index=True
    )


class Trip(Base):
    """A computed trip plan. Created on every successful plan request; the
    unguessable UUID4 id is the permalink token (no auth, no ownership)."""

    __tablename__ = "trips"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicles.id"), nullable=False, index=True
    )
    # The `Profile` this trip is published under, or NULL — which is what almost
    # every row is. Written only when the planner sent a secret that ALREADY
    # belongs to a claimed username, so opting in publishes what you plan next
    # rather than what you planned before.
    #
    # Deliberately not a foreign key. Releasing a username is `UPDATE trips SET
    # owner_hash = NULL` followed by deleting the profile, and an FK would drag
    # `delete_trip` and the purge scripts into a relationship that buys nothing.
    #
    # It arrives as a HEADER and lands here. Never in `request` — that column
    # stores the submitted `PlanRequest` whole, exact coordinates included, and
    # an identifier in there would write the person onto the one row this design
    # works hardest to keep them off.
    owner_hash: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, index=True
    )
    # The validated PlanRequest as submitted (origin/dest, departure, SoC params,
    # speed range, conditions factor) — enough to re-run the plan.
    request: Mapped[dict] = mapped_column(JSON, nullable=False)
    # PlanResult: encoded polyline, per-speed results incl. stops + timeline,
    # optimum speed. Schema versioned so old permalinks stay renderable.
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


# ---------------------------------------------------------------------------
# Live runs — an actual drive of a Trip, followed in real time
# ---------------------------------------------------------------------------


class TripRun(Base):
    """One live drive of a `Trip`.

    The UUID id is the DRIVER'S WRITE TOKEN and must never appear in a read
    response. The trip's own id is a public, read-only share link — anyone
    holding it can watch the journey, and nobody holding only it can move the
    car. Watchers read through `GET /api/trips/{trip_id}/live`, which resolves
    the active run itself and returns `run_ref` (a hash) instead of the id.
    """

    __tablename__ = "trip_runs"
    # Every lookup is "the live run of this trip"; nothing queries status alone.
    __table_args__ = (Index("ix_trip_runs_trip_status", "trip_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("trips.id"), nullable=False
    )
    # "active" | "finished" | "abandoned"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Last contact from the driver — drives "is this still live?" and lets a
    # stale ping be rejected without a row lock.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, index=True
    )
    # The cruise speed the driver said they'd hold; picks the benchmark plan
    # out of the trip's sweep.
    planned_speed_kph: Mapped[float] = mapped_column(Float, nullable=False)
    # Frozen simulator inputs for THIS run: segments, chargers, the geometry
    # axis map, polyline. Snapshotted at the start because `Trip` persists the
    # plan but NOT the segments and chargers behind it — without this, every
    # re-plan would re-hit ORS/OCM mid-drive and an expired cache could hand
    # back a different route, quietly making the original-plan benchmark a lie.
    route_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Last known position, battery, and the anchor the estimate is measured
    # from. Reassign wholesale — see the note below.
    state: Mapped[dict] = mapped_column(JSON, nullable=False)
    # The current revised plan for the remaining journey; null until the first
    # re-plan, after which the original stays available on the Trip.
    plan: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_pings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_replans: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_soc_readings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class TripEvent(Base):
    """Append-only log of the material moments in a run.

    Deliberately NOT one row per GPS ping. Pings overwrite `TripRun.state`;
    only decisions and coarse breadcrumbs land here, so a nine-hour drive is
    on the order of a hundred rows rather than two thousand — and every one of
    them is something the post-trip review actually wants to show.
    """

    __tablename__ = "trip_events"
    __table_args__ = (Index("ix_trip_events_run_at", "run_id", "at"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("trip_runs.id"), nullable=False
    )
    # SERVER clock. The phone's clock is not trusted for ordering; if the
    # device's own timestamp matters it goes in `payload`.
    at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # start | breadcrumb | soc_reading | arrive | arrive_undo | charge_start |
    # charge_end | replan | reroute | off_route | finish
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    # Distance driven in TOTAL, across every road this drive has been on — not
    # the offset along the current one. A re-route replaces the route and
    # restarts that offset, and a trail that jumps backwards mid-journey is
    # not a trail. See `runs._travelled_m`.
    offset_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    soc: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class TripStat(Base):
    """One planned trip, coarsened into something you can aggregate.

    Everything here is derived from a `Trip` that already exists — the request
    and result JSON hold all of it. Nothing new is collected. What this adds is
    the ability to *ask questions*: a `GROUP BY` over origin and destination is
    impossible against a JSON blob on MSSQL, so "where do people actually
    drive" had no answer despite the data being right there since the first
    deploy. That is also why it backfills.

    The coarsening is the point, and it is worth being precise about what it
    does and does not buy. While the trip exists, `trips` still holds the exact
    coordinates, so this table hides nothing from us today. What it does is
    make the surface we query, export, and might one day hand to a charging
    operator coarse *by construction* — and it means the visitor id that Tier
    4b will add lands on a row that has never held a precise home address. A
    dataset built on geohash-4 cannot leak a doorstep in an export somebody
    forgot to think about.

    Geohash-4 is ~39 × 20 km, which is the granularity a corridor map wants
    anyway: "Utrecht region → Innsbruck region" is the demand signal, and the
    house on either end is noise. Distance is rounded to 10 km for the same
    reason — the distribution survives, the fingerprint does not.

    `trip_id` is kept, and does not undo any of the above: the precise data is
    already reachable via `trips` for as long as the trip exists. It is here so
    deletion can be exact. The privacy page promises that deleting a trip
    removes the whole record, and a derived row nobody deletes is precisely how
    that sentence quietly stops being true.
    """

    __tablename__ = "trip_stats"
    # Corridor queries are "group by origin/dest over a window"; the report
    # scripts always bound by date first.
    __table_args__ = (Index("ix_trip_stats_created_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    # Unique: one stat per trip, so a re-run backfill cannot double-count.
    trip_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("trips.id"), nullable=False, unique=True, index=True
    )
    # Who planned it — the persistent pseudonym, never a name and never an IP.
    # It lives on this row rather than on `Trip` for one reason: this row has
    # already been coarsened, so "this person plans four trips a year" exists
    # without ever writing a person next to a precise address.
    #
    # Only the CREATOR. Viewers of a share link are deliberately not recorded
    # (`events.normalize_path` still collapses every trip id), because a link
    # gets forwarded to friends and family who never used this app and never
    # chose anything. Backfilled rows are NULL — history predates the id.
    #
    # Nulled after `purge_old_trip_stat_ids.RETENTION_DAYS`; the coarse row
    # survives, the person attached to it does not.
    client_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    # Copied from the trip rather than defaulted, so a backfilled row carries
    # the date it was really planned and history reads correctly.
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicles.id"), nullable=False, index=True
    )

    # Where. Geohash-4 (~39 × 20 km) and a coarse ISO country from
    # `geo.country_at`, which only knows western Europe — "" elsewhere, which
    # is itself the signal for "we do not model this place yet".
    origin_gh: Mapped[str] = mapped_column(String(4), nullable=False)
    dest_gh: Mapped[str] = mapped_column(String(4), nullable=False)
    origin_cc: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    dest_cc: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    # Every country the route passes through, in driving order — the result
    # already computes this list to say whose speed limits applied. Cross-
    # referenced against `simulator._default_country_caps` it answers "how much
    # demand is for roads we do not actually model".
    countries: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # How far, rounded to 10 km.
    distance_km: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The travelling month, not the planning month. On a holiday-driving app
    # those are different questions and the departure is the interesting one.
    departure_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # How it went. `feasible` false means no speed in the sweep could complete
    # the route — the strongest single charging-gap signal this app produces.
    feasible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    optimum_speed_kph: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    optimum_n_stops: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    optimum_total_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    optimum_charge_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class UpstreamCall(Base):
    """One trip through an external API, and whether we actually paid for it.

    ORS and OCM are free tiers on daily ceilings, and the whole caching design
    exists to stay under them. Until this table there was no way to know how
    close we were: the cache HIT/MISS lines went to the log at info level and
    nothing counted them. The failure mode that motivated it is silent and
    badly timed — the quota exhausts partway through a traffic spike, every
    visitor after that gets an error, and the first you hear is a comment
    saying the site is broken.

    **One row per app-level operation, not per HTTP request.** Planning a new
    corridor is one row saying `calls=35`, because a corridor fetch is 35 tiles
    and the quota is counted in requests. Keeping the row count low matters:
    this is written on the hot path of the thing it is measuring.

    `calls` is what the provider bills. `cache_hits` is what the cache saved.
    Together they give the hit rate, which is the number that predicts whether
    a launch survives its own front page.

    Nothing about a person is in here — no visitor, no client id, no
    coordinates. It is infrastructure telemetry that happens to be triggered by
    someone, and it must not quietly become a second record of what they
    planned. Rows expire on the same 90-day loop as everything else.
    """

    __tablename__ = "upstream_calls"
    # Every query is "this provider, over this window".
    __table_args__ = (Index("ix_upstream_calls_at_provider", "at", "provider"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # "ors" | "ocm"
    provider: Mapped[str] = mapped_column(String(8), nullable=False)
    # "directions" | "geocode" | "chargers"
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # Billable requests actually sent upstream. 0 when the cache covered it.
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Requests the cache answered instead.
    cache_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Feedback(Base):
    """A note somebody left about the app.

    Kept in our own database rather than pointed at a third-party form. Two
    reasons: a visitor who has to leave the site and load someone else's page
    mostly doesn't, and the privacy notice now says no third party is involved,
    which stops being true the moment feedback goes somewhere else.

    Deliberately thin. No IP, no user agent, no session — there is nothing here
    to correlate anyone with, and a feedback box is not a reason to start.
    `contact` is optional and only exists so a reply is possible when somebody
    wants one.
    """

    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    message: Mapped[str] = mapped_column(String(2000), nullable=False)
    contact: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # Which page it was sent from — the only context worth having when someone
    # writes "this is broken".
    path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)


# ---------------------------------------------------------------------------
# Usage counting — how many people, not which people
# ---------------------------------------------------------------------------


class AppEvent(Base):
    """One thing somebody did, with nobody attached to it.

    This exists because "is anyone using this?" had no answer that wasn't a
    guess. The alternative was a third-party analytics script, which would have
    cost the privacy page its "no trackers, no third-party scripts" sentence
    and handed a visitor list to someone else. Counting our own visits in our
    own database is the smaller thing to be doing.

    What makes it not a tracker is the constraints, so they are load-bearing:

    * `visitor` is a hash whose salt ROTATES DAILY (see `core.visitor`). The
      same person tomorrow is a different value, and there is no key kept
      anywhere that turns one back into an IP. It counts a day's uniques and
      is deliberately useless for anything longer.
    * `path` is normalised before it is stored — no query string, and every
      trip id is replaced with `:id`. A row saying someone opened `/trip/:id`
      cannot be joined to a trip. If the real id were kept here, this table
      would become a log of who looked at whose journey, which is exactly the
      correlation the privacy page promises does not exist.
    * `name` is checked against an allowlist at the route, so a public write
      endpoint can't be turned into free-form storage.
    * There is no session id, no user agent, no screen size, no country.

    Rows expire after 90 days (`scripts.purge_old_events`), on the same loop
    that expires location trails.
    """

    __tablename__ = "app_events"
    # Every query is "events in a window", usually narrowed by name. No index
    # on `visitor`: the unique-count query already scans the window, and an
    # index on the pseudonym is a lookup path nothing here should have.
    __table_args__ = (Index("ix_app_events_at_name", "at", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # Allowlisted in api/events.py — page_view, plan_submitted, trip_planned,
    # plan_failed, drive_started.
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    # Normalised route pattern ("/", "/trip/:id"), never a real URL.
    path: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # Daily-salted pseudonym. 32 hex chars = 128 bits.
    visitor: Mapped[str] = mapped_column(String(32), nullable=False)
    # The persistent pseudonym (`core.visitor.client_hash`), when the browser
    # sent one. NULL is common and expected — Global Privacy Control, a cleared
    # store, a first-ever visit mid-session — which is why `visitor` above was
    # kept rather than replaced: retention reads this column, headcounts read
    # that one, and only the latter can count somebody who sends us nothing.
    client_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Referring HOST only ("news.ycombinator.com").
    referrer: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # …and the PATH beside it, so "which Reddit thread sent them" is
    # answerable. Never the query string: that is where a link out of a webmail,
    # a password reset or a search carries a token or an address, and it is the
    # part of a URL that turns a referrer into a leak.
    referrer_path: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Coarse context (`core.client_context`) — country from the edge, and the
    # user agent reduced to allowlisted words. The raw UA is never stored; it
    # is one of the strongest fingerprints a browser emits and this table has
    # no business holding one.
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    device: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    browser: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    os: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # A breakpoint band ("641-768"), not a width — an exact viewport size is a
    # real fingerprinting signal and answers nothing extra.
    viewport: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    # Why a `plan_failed` row happened, as one word from
    # `core.failures.PLAN_FAILURE_REASONS`. Null on every other event.
    reason: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    # The `?src=` tag on the link somebody arrived through, carried for the
    # whole session so a plan two pages later is attributable. Reddit's mobile
    # app strips referrers, so without this the best channel reads as "direct".
    # Validated to a strict slug in `events.normalize_campaign`.
    campaign: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
