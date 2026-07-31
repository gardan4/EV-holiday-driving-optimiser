"""Pydantic request/response schemas for the public API."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Vehicles
# ---------------------------------------------------------------------------


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    make: str
    model: str
    variant: Optional[str] = None
    usable_kwh: float
    consumption: dict
    charge_curve: list
    max_dc_kw: float
    source_note: Optional[str] = None


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------


class GeocodeHit(BaseModel):
    label: str
    lat: float
    lon: float
    country: Optional[str] = None


# ---------------------------------------------------------------------------
# Trip planning
# ---------------------------------------------------------------------------


class PlacePoint(BaseModel):
    label: str = Field(max_length=200)
    # EU corridor bounds — this is a European road-trip planner.
    lat: float = Field(ge=35.0, le=62.0)
    lon: float = Field(ge=-11.0, le=25.0)


class PlanRequest(BaseModel):
    origin: PlacePoint
    dest: PlacePoint
    vehicle_id: str
    departure_iso: datetime
    depart_soc: float = Field(default=100.0, ge=10.0, le=100.0)
    target_soc: float = Field(default=10.0, ge=5.0, le=80.0)
    speed_min: float = Field(default=90.0, ge=60.0, le=180.0)
    speed_max: float = Field(default=160.0, ge=60.0, le=180.0)
    speed_step: float = Field(default=5.0, ge=1.0, le=20.0)
    # Consumption multiplier for conditions: 1.0 mild / 1.15 cold / 1.3 freezing.
    conditions_factor: float = Field(default=1.0, ge=0.8, le=1.6)


class StopOut(BaseModel):
    charger_id: str
    name: str
    operator: Optional[str] = None
    lat: float
    lon: float
    power_kw: float
    offset_m: float
    arrive_min: float          # minutes since departure
    arrive_soc: float
    depart_soc: float
    charge_min: float


class TimelinePoint(BaseModel):
    t_min: float
    dist_m: float
    soc: float


class SpeedResultOut(BaseModel):
    speed_kph: float
    feasible: bool
    total_min: Optional[float] = None
    drive_min: Optional[float] = None
    charge_min: Optional[float] = None
    n_stops: Optional[int] = None
    stops: list[StopOut] = []
    timeline: list[TimelinePoint] = []


class PlanResult(BaseModel):
    polyline: str              # polyline5-encoded route geometry
    total_dist_m: float
    speeds: list[SpeedResultOut]
    optimum_speed: Optional[float] = None
    vehicle: VehicleOut


class TripOut(BaseModel):
    id: str
    request: PlanRequest
    result: PlanResult
    created_at: datetime
