"""Database models for EV Trip Optimizer.

Domain: curated `Vehicle` catalog (consumption + DC charge curves), cached
external data (`Charger` from OpenChargeMap, `RouteCache` from OpenRouteService,
`OcmTile` cache bookkeeping), and `Trip` — a persisted plan result whose UUID id
doubles as the unguessable share token. There is no auth and no user table.

MSSQL note: UUID primary keys use the `GUID` TypeDecorator (CHAR(36)); a plain
`uuid` column type has no portable MSSQL mapping. Keep it for every id/FK.
Boolean filters must be written `== True  # noqa: E712` — `.is_(True)` compiles
to `IS 1`, a T-SQL syntax error. JSON columns use SQLAlchemy `JSON`
(NVARCHAR(MAX) on MSSQL, JSON-as-TEXT on the SQLite test engine).
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

    usable_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    consumption: Mapped[dict] = mapped_column(JSON, nullable=False)
    charge_curve: Mapped[list] = mapped_column(JSON, nullable=False)
    max_dc_kw: Mapped[float] = mapped_column(Float, nullable=False)
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


class Trip(Base):
    """A computed trip plan. Created on every successful plan request; the
    unguessable UUID4 id is the permalink token (no auth, no ownership)."""

    __tablename__ = "trips"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("vehicles.id"), nullable=False, index=True
    )
    # The validated PlanRequest as submitted (origin/dest, departure, SoC params,
    # speed range, conditions factor) — enough to re-run the plan.
    request: Mapped[dict] = mapped_column(JSON, nullable=False)
    # PlanResult: encoded polyline, per-speed results incl. stops + timeline,
    # optimum speed. Schema versioned so old permalinks stay renderable.
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
