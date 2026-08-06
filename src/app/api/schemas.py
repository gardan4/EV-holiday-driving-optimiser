"""Pydantic request/response schemas for the public API."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.geo import within_coverage


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
    mass_kg: float = 1900.0
    top_speed_kph: float = 180.0
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
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)

    # The corridor check lives here rather than on the fields: as `ge`/`le`
    # bounds it surfaced to the user as "Input should be greater than or equal
    # to -11" — three times over, once per offending coordinate — which says
    # nothing about what went wrong or what to do about it.
    @model_validator(mode="after")
    def _within_coverage(self) -> "PlacePoint":
        if not within_coverage(self.lat, self.lon):
            where = self.label or "That place"
            raise ValueError(f"{where} is outside the area this planner covers (mainland Europe).")
        return self


class PlanRequest(BaseModel):
    origin: PlacePoint
    dest: PlacePoint
    vehicle_id: str
    departure_iso: datetime
    depart_soc: float = Field(default=100.0, ge=10.0, le=100.0)
    target_soc: float = Field(default=10.0, ge=5.0, le=80.0)
    speed_min: float = Field(default=90.0, ge=60.0, le=220.0)
    speed_max: float = Field(default=170.0, ge=60.0, le=220.0)
    speed_step: float = Field(default=5.0, ge=1.0, le=20.0)
    # Consumption multiplier for conditions: 1.0 mild / 1.15 cold / 1.3 freezing.
    # Ambient temperature is what people actually know; when given it derives
    # both factors below, so callers don't have to invent multipliers.
    temperature_c: Optional[float] = Field(default=None, ge=-30.0, le=45.0)
    conditions_factor: float = Field(default=1.0, ge=0.8, le=1.6)
    # Cold packs also CHARGE slower — the dominant winter effect, and separate
    # from consumption. 1.0 mild / 0.8 cold / 0.55 freezing.
    charge_power_factor: float = Field(default=1.0, ge=0.3, le=1.0)
    # How much of the derestriction-eligible German autobahn is really open.
    autobahn_open_share: float = Field(default=0.3, ge=0.0, le=1.0)
    # Driving style: km/h above the legal cap, and the multiplier applied to an
    # ordinary road's own flow speed.
    over_cap_kph: float = Field(default=0.0, ge=0.0, le=30.0)
    over_freeflow_factor: float = Field(default=1.0, ge=1.0, le=1.3)
    # Minutes queuing per stop, and the plug-in overhead.
    # Fraction of a charger's rated power you actually receive (shared cabinet,
    # busy site). Separate from queue_min, which is time before you plug in.
    site_power_factor: float = Field(default=1.0, ge=0.3, le=1.0)
    queue_min: float = Field(default=0.0, ge=0.0, le=30.0)
    stop_overhead_min: float = Field(default=5.0, ge=0.0, le=30.0)
    # Rest rule: a break of `rest_min` after every `rest_interval_min` driving.
    # Time at a charger counts towards it. 0 disables.
    rest_interval_min: float = Field(default=0.0, ge=0.0, le=600.0)
    rest_min: float = Field(default=0.0, ge=0.0, le=120.0)
    price_per_kwh: float = Field(default=0.59, ge=0.0, le=3.0)
    # What the car is carrying. The defaults are 2 × 75 kg + 30 kg = 180 kg,
    # which is exactly the nominal load already inside every catalog
    # `mass_kg` — so a request that omits them plans identically to one made
    # before these fields existed, and old permalinks keep their answers.
    occupants: int = Field(default=2, ge=1, le=9)
    luggage_kg: float = Field(default=30.0, ge=0.0, le=400.0)


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
    # Break time the charging stops did not already absorb.
    rest_min: float = 0.0
    energy_kwh: float = 0.0
    cost_eur: float = 0.0
    stops: list[StopOut] = []
    timeline: list[TimelinePoint] = []


class PlanResult(BaseModel):
    polyline: str              # polyline5-encoded route geometry
    total_dist_m: float
    speeds: list[SpeedResultOut]
    optimum_speed: Optional[float] = None
    vehicle: VehicleOut
    # Total climb over the route, for the assumptions panel.
    climb_m: float = 0.0
    # True when the sweep stopped at the car's top speed and the curve was
    # still falling — i.e. the theoretical optimum is faster than the car goes.
    optimum_at_top_speed: bool = False


class TripOut(BaseModel):
    id: str
    request: PlanRequest
    result: PlanResult
    created_at: datetime


# ---------------------------------------------------------------------------
# Live runs
#
# Two capabilities, deliberately separated: the trip id is a public read-only
# share link, and the run id is the driver's write token. Anything returned to
# a watcher must therefore be free of the run id — see `LiveOut`.
# ---------------------------------------------------------------------------


class StartRunRequest(BaseModel):
    # Must be one of the speeds the trip actually simulated; it selects the
    # benchmark plan the whole drive is measured against.
    planned_speed_kph: float = Field(ge=60.0, le=220.0)
    # The battery at the wheel right now, which is routinely not the SoC the
    # plan was made with. `ge=0` on purpose — unlike `PlanRequest`, a live
    # driver can genuinely be at 6%.
    depart_soc: float = Field(ge=0.0, le=100.0)
    lat: float = Field(ge=35.0, le=62.0)
    lon: float = Field(ge=-11.0, le=25.0)
    # Abandon a drive that's already under way and start a fresh one. Requires
    # the caller to have been told one exists (409), so nobody replaces a live
    # drive by accident — including their own, from another device.
    supersede: bool = False


class PingRequest(BaseModel):
    lat: float = Field(ge=35.0, le=62.0)
    lon: float = Field(ge=-11.0, le=25.0)
    # Kinematics since the previous ping. Splitting moving from stationary is
    # what keeps the v² drag term honest: energy is convex in speed, so a plain
    # average over a traffic jam under-bills the motorway either side of it.
    moving_s: float = Field(default=0.0, ge=0.0, le=3600.0)
    stationary_s: float = Field(default=0.0, ge=0.0, le=3600.0)
    # Browser GPS accuracy. Vague fixes are recorded but do not move the car.
    accuracy_m: Optional[float] = Field(default=None, ge=0.0)


class SocReadingRequest(BaseModel):
    soc: float = Field(ge=0.0, le=100.0)


class ReplanRequest(BaseModel):
    # A narrow band around the planned speed by default: "hold 135 instead of
    # 125" is actionable six hours in, "you should have driven 155" is not.
    speed_span_kph: float = Field(default=20.0, ge=0.0, le=60.0)
    speed_step: float = Field(default=5.0, ge=1.0, le=20.0)
    full_range: bool = False


class LiveStateOut(BaseModel):
    at_min: float                  # minutes since departure
    offset_m: float
    lat: float
    lon: float
    soc: float
    soc_is_measured: bool
    # Widens with the age of the last human reading. The estimate is never
    # shown as a bare number.
    soc_uncertainty_pct: float
    anchor_age_min: float
    off_route_m: float
    stale: bool                    # off-route: position and battery unknown
    at_charger_id: Optional[str] = None
    run_factor: float = 1.0
    # Against the ORIGINAL plan. Positive = later than promised.
    ahead_behind_min: float = 0.0
    soc_vs_plan: float = 0.0
    needs_replan: bool = False
    replan_reasons: list[str] = []
    status: str = "active"


class BenchmarkOut(BaseModel):
    original_speed_kph: float
    original_total_min: float
    live_total_min: float
    delta_min: float
    original_stops_remaining: int
    live_stops_remaining: int


class ReplanOut(BaseModel):
    plan_version: int
    # The tail's offsets and arrival times are all relative to the cut, so the
    # frontend needs both of these to draw it on the original's axes.
    offset_base_m: float
    elapsed_min: float
    remaining: SpeedResultOut
    speeds: list[SpeedResultOut] = []
    optimum_speed: Optional[float] = None
    benchmark: BenchmarkOut


class StartRunOut(BaseModel):
    """The only response that ever carries the write token."""

    run_id: str
    run_ref: str
    trip_id: str
    state: LiveStateOut


class LiveOut(BaseModel):
    """The watcher's view. Contains no run id, at any depth."""

    run_ref: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    planned_speed_kph: float
    state: LiveStateOut
    plan: Optional[ReplanOut] = None
    trail: list[TimelinePoint] = []
    # How long since the driving phone last reported. Without this a page whose
    # driver has lost signal or run out of battery shows the same numbers
    # forever under a "live" badge — the worst failure for someone at home
    # waiting on an arrival time.
    seconds_since_ping: float = 0.0


class RunSummaryOut(BaseModel):
    """One past drive, listed. No run id — `run_ref` is the public handle."""

    run_ref: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    planned_speed_kph: float
    distance_m: float = 0.0
    n_replans: int = 0


class ReviewStopOut(BaseModel):
    charger_id: str
    name: str
    planned_arrive_min: Optional[float] = None
    actual_arrive_min: Optional[float] = None
    planned_charge_min: Optional[float] = None
    actual_charge_min: Optional[float] = None
    planned_soc: Optional[float] = None
    actual_soc: Optional[float] = None


class ReviewOut(BaseModel):
    run_ref: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    planned_speed_kph: float
    planned_total_min: float
    actual_total_min: Optional[float] = None
    delta_min: Optional[float] = None
    n_replans: int = 0
    n_soc_readings: int = 0
    final_run_factor: float = 1.0
    stops: list[ReviewStopOut] = []
    trail: list[TimelinePoint] = []


# ── Feedback ────────────────────────────────────────────────────────────────
class FeedbackIn(BaseModel):
    """Bounded on every field: this is the one endpoint that stores free text a
    stranger typed, so the caps are the whole validation story."""

    message: str = Field(min_length=1, max_length=2000)
    contact: str | None = Field(default=None, max_length=200)
    path: str | None = Field(default=None, max_length=300)


class FeedbackItem(BaseModel):
    created_at: datetime
    message: str
    contact: str | None = None
    path: str | None = None
