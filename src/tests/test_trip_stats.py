"""The derived corridor table: coarsening, tolerance, and deletion.

Three things are load-bearing and each has a test here:

* the coarsening actually coarsens (geohash-4, distance to 10 km) — the whole
  privacy argument for this table is that it cannot carry a doorstep;
* deriving never raises, because a stats row is not worth failing a plan over;
* deleting a trip deletes its stat, which is what keeps the privacy page's
  "the whole record" sentence true now that a second table derives from trips.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.main import app
from app.models import Trip, TripStat, Vehicle
from app.services.geo import RouteGeometry, geohash_bbox
from app.services.routing import RouteData
from app.services.simulator import ChargerNode
from app.services.trip_stats import DISTANCE_ROUNDING_KM, GEOHASH_PRECISION, build_trip_stat
from tests.fixtures import nl_to_austria_route
from tests.test_api import BORN_SEED, plan_body, vehicle_id

UTRECHT = {"label": "Utrecht", "lat": 52.0907, "lon": 5.1214}
INNSBRUCK = {"label": "Innsbruck", "lat": 47.2692, "lon": 11.4041}


def a_request(**overrides) -> dict:
    body = {
        "origin": dict(UTRECHT),
        "dest": dict(INNSBRUCK),
        "departure_iso": "2026-08-14T22:00:00",
    }
    body.update(overrides)
    return body


def a_result(**overrides) -> dict:
    body = {
        "total_dist_m": 947_312,
        "optimum_speed": 120.0,
        "countries": ["NL", "DE", "AT"],
        "speeds": [
            {"speed_kph": 110.0, "feasible": True, "n_stops": 3,
             "total_min": 600.0, "charge_min": 70.0},
            {"speed_kph": 120.0, "feasible": True, "n_stops": 4,
             "total_min": 580.0, "charge_min": 85.0},
        ],
    }
    body.update(overrides)
    return body


def build(request=None, result=None) -> TripStat | None:
    return build_trip_stat(
        trip_id=uuid.uuid4(),
        vehicle_id=uuid.uuid4(),
        request=request if request is not None else a_request(),
        result=result if result is not None else a_result(),
        created_at=datetime(2026, 8, 10, 12, 0, 0),
    )


class TestCoarsening:
    def test_geohash_is_city_sized_not_street_sized(self):
        stat = build()
        assert len(stat.origin_gh) == GEOHASH_PRECISION
        assert len(stat.dest_gh) == GEOHASH_PRECISION

    def test_the_stored_cell_is_tens_of_kilometres_across(self):
        """The property the privacy claim actually rests on.

        Not "two nearby points collapse" — geohash cells have hard boundaries,
        so neighbours can straddle one and that is not something the encoding
        promises. What it does promise is cell *size*: every point inside one
        is indistinguishable from every other, so if the cell is ~20 × 24 km
        then no address survives it, wherever the boundaries happen to fall.
        """
        lat_lo, lon_lo, lat_hi, lon_hi = geohash_bbox(build().origin_gh)
        assert (lat_hi - lat_lo) * 111.0 > 15.0, "cell is narrow enough to pin a suburb"
        assert (lon_hi - lon_lo) * 111.0 * 0.62 > 15.0  # cos(52°N)

    def test_distant_origins_do_not_collapse(self):
        """…but the bucket still has to be small enough to be a demand signal."""
        assert build(
            request=a_request(origin={"label": "Groningen", "lat": 53.2194, "lon": 6.5665})
        ).origin_gh != build().origin_gh

    def test_distance_is_rounded(self):
        stat = build()
        assert stat.distance_km % DISTANCE_ROUNDING_KM == 0
        assert stat.distance_km == 950  # 947.312 km → nearest 10

    def test_countries_and_endpoints(self):
        stat = build()
        assert stat.countries == ["NL", "DE", "AT"]
        assert stat.origin_cc == "NL"
        assert stat.dest_cc == "AT"

    def test_departure_month_is_the_travelling_month(self):
        """Not the planning month — for a holiday app they are different
        questions and the departure is the one with the seasonality in it."""
        assert build().departure_month == 8
        assert build(request=a_request(departure_iso="2026-12-23T06:00:00")).departure_month == 12

    def test_departure_month_tolerates_a_trailing_z(self):
        assert build(request=a_request(departure_iso="2026-02-01T06:00:00Z")).departure_month == 2


class TestOptimumCapture:
    def test_reads_the_recommended_plan_not_the_first_one(self):
        stat = build()
        assert stat.feasible is True
        assert stat.optimum_speed_kph == 120.0
        assert stat.optimum_n_stops == 4      # the 120 row, not the 110 row
        assert stat.optimum_charge_min == 85.0

    def test_infeasible_trip_records_the_gap(self):
        """No optimum means no speed could complete the route — the charging
        gap signal, so it must be stored as a row rather than dropped."""
        stat = build(result=a_result(optimum_speed=None, speeds=[
            {"speed_kph": 110.0, "feasible": False},
        ]))
        assert stat is not None
        assert stat.feasible is False
        assert stat.optimum_n_stops is None
        assert stat.distance_km == 950  # the corridor is still counted


class TestTolerance:
    """A stats row is never worth failing a trip plan over."""

    @pytest.mark.parametrize(
        "request_json",
        [
            {},
            {"origin": UTRECHT},                                  # no dest
            {"origin": None, "dest": INNSBRUCK},
            {"origin": {"lat": "north"}, "dest": INNSBRUCK},      # wrong types
            {"origin": {"lat": 999.0, "lon": 0.0}, "dest": INNSBRUCK},  # out of range
        ],
    )
    def test_underivable_requests_return_none_rather_than_raising(self, request_json):
        assert build(request=request_json) is None

    @pytest.mark.parametrize(
        "result_json",
        [
            {},
            {"total_dist_m": None, "speeds": None, "countries": "NL"},
            {"total_dist_m": -5, "optimum_speed": 120.0, "speeds": [{"nonsense": True}]},
        ],
    )
    def test_malformed_results_still_produce_a_row(self, result_json):
        """The corridor is the valuable part and it comes from the request, so
        a result we cannot read costs the plan detail, not the whole row."""
        stat = build(result=result_json)
        assert stat is not None
        assert stat.origin_gh
        assert stat.distance_km >= 0


# ---------------------------------------------------------------------------
# Against the API — derived on write, removed on delete
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def stats_db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        session.add(Vehicle(**BORN_SEED))
        await session.commit()
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def stats_client(stats_db, monkeypatch):
    async def override_get_db():
        yield stats_db

    app.dependency_overrides[get_db] = override_get_db
    segments, charger_nodes = nl_to_austria_route()
    route = RouteData(
        geometry=RouteGeometry([(52.09, 5.12), (47.26, 11.39)]),
        segments=segments,
        total_dist_m=sum(s.dist_m for s in segments),
        total_dur_s=sum(s.dist_m for s in segments) / 30.0,
    )

    async def fake_get_route(db, o_lat, o_lon, d_lat, d_lon):
        return route

    async def fake_chargers(db, r) -> list[ChargerNode]:
        return charger_nodes

    monkeypatch.setattr("app.api.trips.routing.get_route", fake_get_route)
    monkeypatch.setattr("app.api.trips.chargers_svc.chargers_for_route", fake_chargers)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


class TestApiIntegration:
    async def test_planning_writes_exactly_one_stat(self, stats_client, stats_db):
        vid = await vehicle_id(stats_client)
        resp = await stats_client.post("/api/trips", json=plan_body(vid))
        assert resp.status_code == 200, resp.text

        stat = (await stats_db.execute(select(TripStat))).scalar_one()
        assert str(stat.trip_id) == resp.json()["id"]
        assert stat.origin_cc == "NL"
        assert stat.dest_cc == "AT"
        assert stat.distance_km > 0
        assert stat.feasible is True

    async def test_stat_created_at_matches_the_trip(self, stats_client, stats_db):
        """Backfilled and live rows both carry the trip's own date — otherwise
        the day the backfill ran would read as a spike in every chart."""
        vid = await vehicle_id(stats_client)
        tid = (await stats_client.post("/api/trips", json=plan_body(vid))).json()["id"]

        trip = await stats_db.get(Trip, uuid.UUID(tid))
        stat = (await stats_db.execute(select(TripStat))).scalar_one()
        assert stat.created_at == trip.created_at

    async def test_deleting_a_trip_deletes_its_stat(self, stats_client, stats_db):
        """The privacy page says deleting removes the whole record."""
        vid = await vehicle_id(stats_client)
        tid = (await stats_client.post("/api/trips", json=plan_body(vid))).json()["id"]
        assert (await stats_db.execute(select(func.count()).select_from(TripStat))).scalar_one() == 1

        resp = await stats_client.delete(f"/api/trips/{tid}")
        assert resp.status_code == 204
        left = (await stats_db.execute(select(func.count()).select_from(TripStat))).scalar_one()
        assert left == 0, "a derived row survived the deletion it was promised to follow"

    async def test_no_precise_coordinates_are_stored(self, stats_client, stats_db):
        """The point of the table. Utrecht's real coordinates must not be
        recoverable from any column on the row."""
        vid = await vehicle_id(stats_client)
        await stats_client.post("/api/trips", json=plan_body(vid))
        stat = (await stats_db.execute(select(TripStat))).scalar_one()

        flattened = " ".join(str(v) for v in vars(stat).values())
        assert "52.09" not in flattened
        assert "5.12" not in flattened
