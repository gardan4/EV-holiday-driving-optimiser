"""Why planning failed, recorded server-side.

The old `plan_failed` said only that something broke. That made the most
valuable table on launch day — "what did people ask for that we could not do" —
unanswerable, and it counted a different population than the trips table it was
compared against, because an event fired from a page can be blocked.

Three properties, each with a test here:

* every failure path carries a reason, and the reason survives to the database;
* the reason is a countable slug, not the human message — messages get reworded
  and a chart built on them would reorganise itself;
* recording is best-effort: it never turns a handled 422 into a 500.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.core.failures import PLAN_FAILURE_REASONS, PlanError, reason_of
from app.main import app
from app.models import AppEvent, Vehicle
from app.services.geo import RouteGeometry
from app.services.routing import RouteData
from app.services.simulator import ChargerNode
from app.services.usage import usage_stats
from tests.fixtures import nl_to_austria_route
from tests.test_api import BORN_SEED, plan_body, vehicle_id


class TestReasonOf:
    def test_a_labelled_error_keeps_its_reason(self):
        assert reason_of(PlanError("no_chargers", 422, "…")) == "no_chargers"

    def test_an_unknown_label_is_not_trusted(self):
        """The column should only ever hold values from the allowlist, so a
        typo must fall back rather than write itself into the numbers."""
        assert reason_of(PlanError("typo_not_in_list", 422, "…")) not in {"typo_not_in_list"}

    def test_unlabelled_errors_are_guessed_not_dropped(self):
        """An unclassified failure is still a failure — dropping it would
        under-report exactly when something new is going wrong."""
        from fastapi import HTTPException

        assert reason_of(HTTPException(503, "…")) == "upstream_error"
        assert reason_of(HTTPException(404, "…")) == "unknown_vehicle"
        assert reason_of(HTTPException(422, "…")) == "bad_request"
        assert reason_of(RuntimeError("boom")) == "server_error"

    def test_every_guess_is_in_the_allowlist(self):
        from fastapi import HTTPException

        for exc in (
            HTTPException(503, "x"), HTTPException(404, "x"),
            HTTPException(422, "x"), RuntimeError("x"),
        ):
            assert reason_of(exc) in PLAN_FAILURE_REASONS


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


async def _reasons(db) -> list[str]:
    rows = (
        await db.execute(
            select(AppEvent.reason).where(AppEvent.name == "plan_failed")
        )
    ).scalars().all()
    return list(rows)


class TestRecording:
    async def test_a_bad_soc_is_named(self, client, db_session):
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips", json=plan_body(vid, depart_soc=20.0, target_soc=80.0)
        )
        assert resp.status_code == 422
        assert await _reasons(db_session) == ["bad_soc"]

    async def test_an_unknown_vehicle_is_named(self, client, db_session):
        resp = await client.post(
            "/api/trips",
            json=plan_body("2b1f0d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"),
        )
        assert resp.status_code == 404
        assert await _reasons(db_session) == ["unknown_vehicle"]

    async def test_an_unparseable_vehicle_is_named(self, client, db_session):
        resp = await client.post("/api/trips", json=plan_body("not-a-uuid"))
        assert resp.status_code == 422
        assert await _reasons(db_session) == ["bad_vehicle"]

    async def test_a_sweep_that_is_too_large_is_named(self, client, db_session):
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips",
            json=plan_body(vid, speed_min=60.0, speed_max=220.0, speed_step=1.0),
        )
        assert resp.status_code == 422
        assert await _reasons(db_session) == ["sweep_too_large"]

    async def test_no_feasible_plan_is_the_charging_gap_signal(
        self, client, db_session, monkeypatch
    ):
        """The reason that matters most: demand we could not serve."""
        async def no_chargers(db, r) -> list[ChargerNode]:
            return []

        monkeypatch.setattr(
            "app.api.trips.chargers_svc.chargers_for_route", no_chargers
        )
        vid = await vehicle_id(client)
        resp = await client.post("/api/trips", json=plan_body(vid))
        assert resp.status_code == 422
        assert await _reasons(db_session) == ["no_chargers"]

    async def test_a_route_that_is_too_long_is_named(
        self, client, db_session, monkeypatch
    ):
        from app.services.geo import RouteGeometry as RG

        async def very_long(db, *a):
            return RouteData(
                geometry=RG([(0.0, 0.0), (1.0, 1.0)]),
                segments=[],
                total_dist_m=9_000_000.0,
                total_dur_s=1.0,
            )

        monkeypatch.setattr("app.api.trips.routing.get_route", very_long)
        vid = await vehicle_id(client)
        resp = await client.post("/api/trips", json=plan_body(vid))
        assert resp.status_code == 422
        assert await _reasons(db_session) == ["route_too_long"]

    async def test_an_upstream_failure_is_named(self, client, db_session, monkeypatch):
        """What a blown free-tier quota looks like from in here."""
        async def upstream_down(db, *a):
            raise PlanError("upstream_error", 503, "Routing is temporarily unavailable.")

        monkeypatch.setattr("app.api.trips.routing.get_route", upstream_down)
        vid = await vehicle_id(client)
        resp = await client.post("/api/trips", json=plan_body(vid))
        assert resp.status_code == 503
        assert await _reasons(db_session) == ["upstream_error"]

    async def test_an_unexpected_error_is_still_counted(
        self, client, db_session, monkeypatch
    ):
        """`server_error` climbing is the signal that something broke in a way
        nobody anticipated — so it must not be the one case that goes unlogged."""
        async def boom(db, *a):
            raise RuntimeError("something nobody predicted")

        monkeypatch.setattr("app.api.trips.routing.get_route", boom)
        vid = await vehicle_id(client)
        with pytest.raises(RuntimeError):
            await client.post("/api/trips", json=plan_body(vid))
        assert await _reasons(db_session) == ["server_error"]

    async def test_a_successful_plan_records_no_failure(self, client, db_session):
        vid = await vehicle_id(client)
        resp = await client.post("/api/trips", json=plan_body(vid))
        assert resp.status_code == 200
        assert await _reasons(db_session) == []

    async def test_the_error_message_still_reaches_the_user(self, client):
        """The reason is for counting; the message is for the person. Adding
        the first must not have replaced the second."""
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips", json=plan_body(vid, depart_soc=20.0, target_soc=80.0)
        )
        assert "Departure charge" in resp.json()["detail"]

    async def test_every_recorded_reason_is_in_the_allowlist(
        self, client, db_session
    ):
        vid = await vehicle_id(client)
        await client.post("/api/trips", json=plan_body(vid, depart_soc=20.0, target_soc=80.0))
        await client.post("/api/trips", json=plan_body("not-a-uuid"))
        for r in await _reasons(db_session):
            assert r in PLAN_FAILURE_REASONS

    async def test_failures_show_up_in_the_stats(self, client, db_session):
        vid = await vehicle_id(client)
        await client.post("/api/trips", json=plan_body(vid, depart_soc=20.0, target_soc=80.0))
        await client.post("/api/trips", json=plan_body("not-a-uuid"))
        await client.post("/api/trips", json=plan_body("not-a-uuid"))

        stats = await usage_stats(db_session, days=7)
        by = {f.label: f.count for f in stats.failure_reasons}
        assert by == {"bad_vehicle": 2, "bad_soc": 1}
        # And the funnel total agrees with the breakdown.
        funnel = {f.label: f.count for f in stats.funnel}
        assert funnel["plan_failed"] == sum(by.values())
