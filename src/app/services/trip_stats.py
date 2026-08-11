"""Turning a stored trip into a row you can aggregate.

`Trip.request` and `Trip.result` have held every number in here since the first
deploy, as JSON. On MSSQL that is a blob — you cannot `GROUP BY` an origin, so
"which corridors do people actually plan?" was unanswerable against data we
already had. This derives the answerable version.

Two callers, one function, deliberately: `api.trips.plan_trip` derives on write
and `scripts.backfill_trip_stats` derives over history. If those two disagreed,
the day the backfill ran would show up as a discontinuity in every chart, and
it would look like a change in behaviour rather than a change in code.

**It takes dicts, not models.** Both callers hand over exactly what is stored —
`plan_trip` already has `model_dump(mode="json")` in hand, and the backfill
reads the column. Going through `PlanRequest`/`PlanResult` would be stricter,
but it would also mean a schema change three versions from now retroactively
breaks the backfill of trips planned today. Reading defensively costs a few
`.get()` calls and makes old rows permanently readable.

Nothing here raises. A malformed or half-migrated row returns `None` and is
skipped, because a stats table is never worth failing a trip plan over.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from app.models import TripStat
from app.services.geo import country_at, geohash_encode

logger = logging.getLogger(__name__)

# ~39 × 20 km. City-region, which is the granularity a corridor map wants:
# "Utrecht area → Innsbruck area" is the demand signal and the street on either
# end is noise. It is also the precision `chargers.py` already tiles OCM at, so
# the number is not a new invention.
GEOHASH_PRECISION = 4

# Round distance to this, for the same reason the coordinates are coarsened: a
# distribution of trip lengths is useful, a distance exact to the kilometre
# alongside two coarse endpoints starts narrowing them back down.
DISTANCE_ROUNDING_KM = 10


def _coord(point: Any) -> tuple[float, float] | None:
    """`(lat, lon)` from a stored PlacePoint, or None if it isn't one."""
    if not isinstance(point, dict):
        return None
    lat, lon = point.get("lat"), point.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return float(lat), float(lon)


def _round_km(metres: Any) -> int:
    if not isinstance(metres, (int, float)) or metres < 0:
        return 0
    km = metres / 1000.0
    return int(round(km / DISTANCE_ROUNDING_KM) * DISTANCE_ROUNDING_KM)


def build_trip_stat(
    *,
    trip_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    request: dict,
    result: dict,
    created_at: datetime,
    client_id: str | None = None,
) -> TripStat | None:
    """Derive the coarsened analytics row for one trip.

    Returns None when the trip cannot be reduced to a corridor — which in
    practice means a row whose request predates or postdates this shape. The
    caller logs and moves on.

    `client_id` defaults to None because the backfill genuinely has none: the
    persistent pseudonym did not exist when those trips were planned, and there
    is nothing to reconstruct it from. Historical rows therefore count toward
    every corridor question and toward no retention question, which is the
    honest shape of the data rather than a gap to paper over.
    """
    try:
        origin = _coord(request.get("origin"))
        dest = _coord(request.get("dest"))
        if origin is None or dest is None:
            return None

        # `country_at` only knows coarse western-European boxes and answers ""
        # everywhere else. Stored as NULL rather than "": the absence IS the
        # finding — it marks demand for somewhere the simulator has no real
        # speed caps for, which is exactly the gap worth counting.
        origin_cc = country_at(*origin) or None
        dest_cc = country_at(*dest) or None

        countries = result.get("countries")
        if not isinstance(countries, list):
            countries = None
        else:
            countries = [c for c in countries if isinstance(c, str) and c][:20]

        departure_month: int | None = None
        raw_departure = request.get("departure_iso")
        if isinstance(raw_departure, str):
            try:
                # Stored as ISO by `model_dump(mode="json")`; a trailing Z is
                # not something fromisoformat took before 3.11, and this file
                # has to keep reading rows written by older code.
                departure_month = datetime.fromisoformat(
                    raw_departure.replace("Z", "+00:00")
                ).month
            except ValueError:
                departure_month = None

        # The optimum is the plan the app actually recommends, so it is the one
        # whose stop count means anything. `speeds` also carries the infeasible
        # entries, which is why feasibility is read from the optimum's presence
        # rather than from the list being non-empty.
        optimum_speed = result.get("optimum_speed")
        best: dict | None = None
        speeds = result.get("speeds")
        if isinstance(speeds, list) and isinstance(optimum_speed, (int, float)):
            for s in speeds:
                if isinstance(s, dict) and s.get("speed_kph") == optimum_speed:
                    best = s
                    break

        return TripStat(
            trip_id=trip_id,
            vehicle_id=vehicle_id,
            created_at=created_at,
            client_id=client_id,
            origin_gh=geohash_encode(*origin, GEOHASH_PRECISION),
            dest_gh=geohash_encode(*dest, GEOHASH_PRECISION),
            origin_cc=origin_cc,
            dest_cc=dest_cc,
            countries=countries,
            distance_km=_round_km(result.get("total_dist_m")),
            departure_month=departure_month,
            feasible=best is not None,
            optimum_speed_kph=float(optimum_speed) if best is not None else None,
            optimum_n_stops=(
                int(best["n_stops"])
                if best is not None and isinstance(best.get("n_stops"), int)
                else None
            ),
            optimum_total_min=(
                float(best["total_min"])
                if best is not None and isinstance(best.get("total_min"), (int, float))
                else None
            ),
            optimum_charge_min=(
                float(best["charge_min"])
                if best is not None and isinstance(best.get("charge_min"), (int, float))
                else None
            ),
        )
    except Exception:  # noqa: BLE001 — analytics must never break a plan
        logger.warning("could not derive trip stat for %s…", str(trip_id)[:8], exc_info=True)
        return None
