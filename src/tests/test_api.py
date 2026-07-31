"""API component tests: FastAPI routes on an in-memory SQLite engine with the
ORS/OCM services stubbed at the service layer (no network, no MSSQL)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.main import app
from app.models import Vehicle
from app.services.geo import RouteGeometry
from app.services.routing import RouteData
from app.services.simulator import ChargerNode
from tests.fixtures import nl_to_austria_route

BORN_SEED = dict(
    slug="cupra-born-58",
    make="Cupra",
    model="Born",
    variant="58 kWh",
    usable_kwh=58.0,
    consumption={"model": "quadratic", "a_wh_km": 55.0, "b_wh_km_per_kph2": 0.0105},
    charge_curve=[[0, 50], [10, 120], [30, 120], [45, 85], [55, 68], [75, 42], [100, 10]],
    max_dc_kw=120.0,
    sort_order=10,
)


@pytest_asyncio.fixture
async def db_session():
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
async def client(db_session, monkeypatch):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    segments, charger_nodes = nl_to_austria_route()
    total = sum(s.dist_m for s in segments)
    # Trivial 2-point geometry — chargers are stubbed so projection never runs.
    geometry = RouteGeometry([(52.09, 5.12), (47.26, 11.39)])
    route = RouteData(
        geometry=geometry, segments=segments, total_dist_m=total, total_dur_s=total / 30.0
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


def plan_body(vehicle_id: str, **overrides) -> dict:
    body = {
        "origin": {"label": "Utrecht", "lat": 52.09, "lon": 5.12},
        "dest": {"label": "Innsbruck", "lat": 47.26, "lon": 11.39},
        "vehicle_id": vehicle_id,
        "departure_iso": "2026-08-14T22:00:00",
        "depart_soc": 100.0,
        "target_soc": 10.0,
        "speed_min": 100.0,
        "speed_max": 140.0,
        "speed_step": 10.0,
    }
    body.update(overrides)
    return body


async def vehicle_id(client) -> str:
    resp = await client.get("/api/vehicles")
    assert resp.status_code == 200
    return resp.json()[0]["id"]


class TestVehicles:
    async def test_list(self, client):
        resp = await client.get("/api/vehicles")
        assert resp.status_code == 200
        (v,) = resp.json()
        assert v["slug"] == "cupra-born-58"
        assert v["charge_curve"][0] == [0, 50]


class TestPlanTrip:
    async def test_plan_and_permalink_roundtrip(self, client):
        vid = await vehicle_id(client)
        resp = await client.post("/api/trips", json=plan_body(vid))
        assert resp.status_code == 200, resp.text
        trip = resp.json()

        result = trip["result"]
        assert result["vehicle"]["slug"] == "cupra-born-58"
        assert len(result["speeds"]) == 5  # 100..140 step 10
        assert result["optimum_speed"] is not None
        feasible = [s for s in result["speeds"] if s["feasible"]]
        assert feasible, "at least some speeds must be feasible"
        for s in feasible:
            assert s["total_min"] > 0
            assert s["timeline"][0]["t_min"] == 0.0
            assert s["timeline"][-1]["soc"] >= 10.0 - 1e-6
        assert result["polyline"]

        # Permalink: fetching by id reproduces the exact stored result.
        resp2 = await client.get(f"/api/trips/{trip['id']}")
        assert resp2.status_code == 200
        assert resp2.json()["result"] == result

    async def test_unknown_vehicle_404(self, client):
        resp = await client.post(
            "/api/trips",
            json=plan_body("00000000-0000-0000-0000-000000000000"),
        )
        assert resp.status_code == 404

    async def test_bad_speed_range_422(self, client):
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips", json=plan_body(vid, speed_min=140.0, speed_max=100.0)
        )
        assert resp.status_code == 422

    async def test_out_of_bounds_coords_422(self, client):
        vid = await vehicle_id(client)
        body = plan_body(vid)
        body["origin"]["lat"] = 10.0  # outside the EU corridor bounds
        resp = await client.post("/api/trips", json=body)
        assert resp.status_code == 422

    async def test_depart_below_target_422(self, client):
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips", json=plan_body(vid, depart_soc=20.0, target_soc=50.0)
        )
        assert resp.status_code == 422


class TestTripNotFound:
    async def test_unknown_trip_404(self, client):
        resp = await client.get("/api/trips/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    async def test_malformed_trip_id_404(self, client):
        resp = await client.get("/api/trips/not-a-uuid")
        assert resp.status_code == 404
