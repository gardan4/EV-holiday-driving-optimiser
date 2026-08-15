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
    # Which `/ev` page this variant belongs on. Null means "a page of its own".
    nameplate_slug: Optional[str] = None
    usable_kwh: float
    consumption: dict
    charge_curve: list
    max_dc_kw: float
    mass_kg: float = 1900.0
    top_speed_kph: float = 180.0
    # The `/ev` pages recompute charge times from the curve, and the curve is
    # cable-side while `usable_kwh` is battery-side — so they need the same loss
    # term the simulator uses or they publish times the planner disagrees with.
    charge_efficiency: float = 0.92
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
    """Anywhere on Earth. The planner used to refuse everything outside a
    European corridor; now the only geographic limit is whether ORS can find a
    drivable route, which it answers with a clear message of its own. Note the
    per-country speed caps only cover the European countries in
    `simulator._default_country_caps` — elsewhere the generic default applies,
    which the assumptions panel says out loud."""

    label: str = Field(max_length=200)
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)


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
    # Germany only — no effect on a route that never enters it.
    autobahn_open_share: float = Field(default=0.3, ge=0.0, le=1.0)
    # Motorway legal limit where we don't model the country's own. The eight
    # western-European countries in `simulator._default_country_caps` keep
    # their real limits; everywhere else uses this. Defaults to the 130 that
    # used to be hardcoded, so an old permalink replans to the same answer.
    motorway_cap_kph: float = Field(default=130.0, ge=80.0, le=200.0)
    # What-if: treat every motorway as derestricted, so the cruise speed itself
    # is the only limit. Answers "what would ignoring the limits actually buy
    # me?" — which is a question about charging, not about driving: past a
    # point the extra speed just buys another stop.
    ignore_speed_limits: bool = False
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
    # Winter tyres cost 5-10% in energy. Separate from `temperature_c` on
    # purpose: they are fitted for a season, not for the afternoon's weather,
    # so a mild day on winter rubber still pays for them.
    winter_tyres: bool = False


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
    # DC bays OpenChargeMap lists. Not live availability — nobody publishes
    # that to us — but a twelve-stall hub and a lone plug are different bets
    # and the model treats them identically.
    n_points: int = 1


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
    # ISO-2 codes the route actually crosses, in order of first appearance;
    # "" for stretches outside the countries we can identify. Lets the UI show
    # the German autobahn control only on a route that enters Germany, and say
    # which limits were real rather than assumed. Empty on trips planned
    # before this existed.
    countries: list[str] = []


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
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    # Abandon a drive that's already under way and start a fresh one. Requires
    # the caller to have been told one exists (409), so nobody replaces a live
    # drive by accident — including their own, from another device.
    supersede: bool = False


class PingRequest(BaseModel):
    # Worldwide, like the trip itself: a drive that could be planned must be
    # drivable, and a ping rejected mid-route strands the driver.
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
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
    # Chargers this driver has turned down. Merged into the set already on the
    # run and kept there, or the next re-plan would hand back the stop they
    # just rejected. Bounded because it is caller-supplied and persisted.
    exclude_charger_ids: list[str] = Field(default_factory=list, max_length=40)


class AlternativeOut(BaseModel):
    """One other charger the drive could stop at instead, and what it costs.

    `delta_min` is against the REMAINING journey, not the leg: swapping a
    charger changes where every later stop falls, and "+6 min to the
    destination" is the only version of that number worth acting on.
    """

    charger_id: str
    name: str
    operator: Optional[str] = None
    power_kw: float
    n_points: int = 1
    lat: float
    lon: float
    # On the whole route's axis, like every other stop the frontend draws.
    offset_m: float
    dist_from_here_m: float
    arrive_soc: float
    depart_soc: float
    charge_min: float
    delta_min: float
    # What to send to `replan` to make this one the next stop. The server
    # computed the alternative by turning these down, so the client does not
    # have to reason about the ranking to act on it.
    exclude_charger_ids: list[str] = []


class AlternativesOut(BaseModel):
    speed_kph: float
    #: The stop currently planned, as the thing the alternatives are priced
    #: against. Null when the plan has no stops left to swap.
    current: Optional[AlternativeOut] = None
    alternatives: list[AlternativeOut] = []


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
# ── Usernames ───────────────────────────────────────────────────────────────
class ClaimUsernameIn(BaseModel):
    """The name somebody wants. Shape is re-checked in the route against
    `users.USERNAME_RE` — the bounds here only reject an oversized body before
    the real validation runs."""

    username: str = Field(min_length=3, max_length=24)


class ProfileOut(BaseModel):
    username: str
    created_at: datetime


class TripSummaryOut(BaseModel):
    """One trip as it appears on a PUBLIC list.

    Deliberately not a `TripOut`. Two reasons, and both are load-bearing:

    * **Coordinates never appear here.** A trip's origin is usually somebody's
      house, and this list is readable by anyone who knows the username. The
      labels are truncated to their locality (`users.locality`) server-side, so
      the API cannot emit a street address even if a caller asks for the list of
      a name they guessed. The full label stays on the trip page, which is
      reachable only with the share link.
    * **It is small.** A full `PlanResult` carries the polyline and every
      speed's timeline — around a megabyte on a long route. A hundred of those
      would make "show me my trips" the heaviest request in the app.
    """

    id: str
    created_at: datetime
    origin_label: str
    dest_label: str
    distance_km: int
    vehicle_label: str
    departure_iso: Optional[datetime] = None
    optimum_speed_kph: Optional[float] = None
    n_stops: Optional[int] = None


class UserTripsOut(BaseModel):
    username: str
    created_at: datetime
    trips: list[TripSummaryOut] = []


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


# ── Usage events ────────────────────────────────────────────────────────────
class EventIn(BaseModel):
    """What the browser is allowed to tell us it did.

    Everything here is treated as hostile — it arrives on an unauthenticated
    public endpoint. `name` is checked against an allowlist in the route, and
    `path`/`referrer` are rewritten there rather than stored as sent; the
    lengths below are only the first cut so an oversized body is rejected
    before any of that runs.
    """

    name: str = Field(min_length=1, max_length=32)
    path: str | None = Field(default=None, max_length=300)
    referrer: str | None = Field(default=None, max_length=300)
    # CSS-pixel width, banded to a breakpoint before storage. Bounded here so a
    # nonsense number is rejected with the body rather than reasoned about
    # later; `classify_viewport` discards anything left over.
    viewport_w: int | None = Field(default=None, ge=0, le=20_000)
    # The `?src=` tag the visit started with, carried for the whole session so
    # a plan two pages later is still attributable to the link that brought
    # them. Bounded here; shape-checked in `events.normalize_campaign`.
    campaign: str | None = Field(default=None, max_length=64)


class DayCount(BaseModel):
    day: str
    visitors: int
    page_views: int


class LabelCount(BaseModel):
    label: str
    count: int


class CampaignOut(BaseModel):
    """One `?src=` tag, and what the people who arrived through it did.

    `page_views` alone ranks channels by how loud they are; the pair is what
    says which audience the tool is actually for.
    """

    label: str
    page_views: int
    plans: int

    @property
    def conversion_pct(self) -> float | None:
        return (100.0 * self.plans / self.page_views) if self.page_views else None


class ProviderOut(BaseModel):
    """Today's spend against one external free tier.

    One row per SERVICE (`provider` + `kind`), not per provider: ORS meters
    directions and geocoding against separate ceilings, and a combined gauge
    reads healthy while one of them is refusing every request.

    `headroom_pct` is None when the provider publishes no daily ceiling —
    withheld rather than invented, because a reassuring bar with no denominator
    behind it is worse than an empty space.
    """

    provider: str
    kind: str = ""
    calls_today: int
    cache_hits_today: int
    failures_today: int
    avg_ms: float
    calls_window: int
    daily_quota: int
    hit_rate: Optional[float] = None
    headroom_pct: Optional[float] = None


class NameStatsOut(BaseModel):
    """Whether the username feature is used, as counts and nothing else.

    Every field here is an aggregate over names — there is no per-name row and
    no username anywhere in this shape, deliberately (see
    `services/profiles.py`). The two clocks are labelled because they answer
    different questions: `claimed`/`released`/`named_trips` describe the
    window, while `live`/`dormant`/`returning`/`spread` describe every name
    that exists, which is the only scale a rare event reads honestly at.
    """

    # This window
    claimed: int
    released: int
    list_views: int
    drawer_opens: int
    trips: int
    named_trips: int
    active: int
    # Share of the window's trips published under a name. None when nothing was
    # planned — "0% of nothing" and "nobody wants this" must not look alike.
    named_share: Optional[float] = None

    # Lifetime
    live: int
    with_trips: int
    dormant: int
    returning: int
    # Quoted against `with_trips`, never against `live`: a name claimed
    # yesterday has not yet had the chance to come back.
    return_rate: Optional[float] = None
    spread: list[LabelCount] = []


class CorridorOut(BaseModel):
    """One coarsened origin→destination pair. `label` is already rendered
    ("NL 52.1,5.1 → AT 47.2,11.4") because the geohash it comes from is not
    something a reader can place, and turning it into a name would cost an
    ORS call per row against the quota the planner depends on."""

    label: str
    trips: int
    distance_km: int
    avg_stops: Optional[float] = None
    stops_per_100km: Optional[float] = None


class UsageStats(BaseModel):
    """The answer to "is anyone using this?".

    `since_launch` counts come from the `trips` table, which has been recording
    `created_at` since the first deploy — so those numbers are real history.
    Everything derived from `app_events` only starts the day events shipped.
    """

    days: int
    generated_at: datetime
    daily: list[DayCount]
    visitors: int
    page_views: int
    funnel: list[LabelCount]
    top_paths: list[LabelCount]
    top_referrers: list[LabelCount]
    trips_planned: int
    drives_started: int
    trips_planned_since_launch: int
    # Retention. `identified_visitors` is the denominator — browsers that sent
    # a persistent id at all — and is smaller than `visitors` above, because
    # opting out, private windows and cleared storage all land outside it.
    # Quote the ratio, never `returning_visitors` on its own.
    identified_visitors: int = 0
    returning_visitors: int = 0

    # Who and what, coarsely (`core.client_context`). `countries` is empty
    # unless something trustworthy sits in front setting `CF-IPCountry`, so on
    # a local run it is expected to be blank rather than broken.
    countries: list[LabelCount] = []
    devices: list[LabelCount] = []
    browsers: list[LabelCount] = []
    operating_systems: list[LabelCount] = []
    viewports: list[LabelCount] = []
    top_referrer_paths: list[LabelCount] = []

    # Where people drive, from `trip_stats` — so these reach back to the first
    # deploy, like the trip totals and unlike anything counted from events.
    top_corridors: list[CorridorOut] = []
    hardest_corridors: list[CorridorOut] = []
    infeasible_corridors: list[CorridorOut] = []
    unmodelled_countries: list[LabelCount] = []
    unplaceable_trips: int = 0
    repeat_planners: list[LabelCount] = []
    top_cars: list[LabelCount] = []

    # Usernames: how many exist, how many are used, and whether the trips list
    # is opened at all. Optional so a reader running against an API that
    # predates it still parses — `usage_report --remote` is exactly that case
    # between a local checkout and the deployed image.
    names: Optional[NameStatsOut] = None

    # Which posted link worked, and whether the free tiers will survive it.
    campaigns: list[CampaignOut] = []
    providers: list[ProviderOut] = []
    # Why planning failed, from `core.failures.PLAN_FAILURE_REASONS`. Recorded
    # server-side, so this counts every failure rather than only the ones a
    # browser was able to report.
    failure_reasons: list[LabelCount] = []
