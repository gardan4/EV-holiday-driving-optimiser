"""Where a charging stop is allowed to go, and what we say when nowhere is.

Found from the launch: five plans failed, two of them `no_chargers`. Sparse
corridors turned out to be fine (rural Montana and northern Norway both plan),
but a 26 km hop in the Netherlands came back as "no fast chargers found along
this route" — because the flat 50 km / 10 km end exclusions leave no eligible
window at all on anything under 60 km.

That message was false in the densest charging region in Europe, which is worse
than unhelpful. These tests pin both halves of the fix: the window scales, and
when it genuinely cannot hold a stop we say the true thing instead.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.core.failures import PLAN_FAILURE_REASONS
from app.main import app
from app.models import AppEvent, Vehicle
from app.services.chargers import (
    END_MARGIN_M,
    MIN_OFFSET_M,
    stop_window,
)
from app.services.geo import RouteGeometry
from app.services.routing import RouteData
from app.services.simulator import ChargerNode, RouteSegment
from tests.test_api import BORN_SEED, plan_body, vehicle_id


class TestStopWindow:
    def test_a_long_trip_keeps_the_flat_exclusions(self):
        """The whole point of the fractions is that they do not disturb the
        case the constants were chosen for."""
        start, end = stop_window(950_000.0)
        assert start == MIN_OFFSET_M
        assert end == 950_000.0 - END_MARGIN_M

    def test_the_flat_values_bind_from_about_200_km(self):
        start, _ = stop_window(200_000.0)
        assert start == MIN_OFFSET_M

    @pytest.mark.parametrize("km", [10, 26, 40, 63, 100])
    def test_a_short_route_still_has_a_usable_window(self, km):
        """The regression itself: under 60 km the old rule left start > end,
        so every charger in the country was filtered out."""
        total = km * 1000.0
        start, end = stop_window(total)
        assert start < end, f"{km} km has no eligible window"
        assert (end - start) > total * 0.5, "window is too narrow to be useful"

    def test_the_window_never_runs_off_the_route(self):
        for km in (1, 5, 26, 60, 200, 4000):
            start, end = stop_window(km * 1000.0)
            assert 0.0 <= start < end <= km * 1000.0

    def test_a_26km_hop_can_hold_a_stop_in_the_middle(self):
        """Utrecht to Amersfoort, the case from the launch."""
        start, end = stop_window(26_000.0)
        assert start <= 13_000.0 <= end


# ---------------------------------------------------------------------------
# What the API says when there is genuinely nowhere to stop
# ---------------------------------------------------------------------------


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


def _short_route(km: float) -> RouteData:
    segs = [RouteSegment(dist_m=km * 1000.0, freeflow_kph=100.0, country="NL")]
    return RouteData(
        geometry=RouteGeometry([(52.09, 5.12), (52.16, 5.39)]),
        segments=segs,
        total_dist_m=km * 1000.0,
        total_dur_s=km * 36.0,
    )


@pytest_asyncio.fixture
async def client(db_session, monkeypatch):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async def fake_route(db, *a):
        return _short_route(26.0)

    async def one_unreachable_node(db, r) -> list[ChargerNode]:
        # The realistic shape after the window fix: a charger IS found, the
        # car just cannot reach it from a nearly-flat battery. The message
        # must still not blame the charger data.
        return [
            ChargerNode(
                charger_id="chg-1", name="Fastned Amersfoort",
                offset_m=13_000.0, power_kw=175.0, detour_min=2.0,
            )
        ]

    monkeypatch.setattr("app.api.trips.routing.get_route", fake_route)
    monkeypatch.setattr(
        "app.api.trips.chargers_svc.chargers_for_route", one_unreachable_node
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


class TestShortRouteMessage:
    async def test_it_no_longer_claims_there_are_no_chargers(self, client):
        """The sentence that was false in the Randstad."""
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips", json=plan_body(vid, depart_soc=11.0, target_soc=10.0)
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "no fast chargers" not in detail.lower()
        assert "26 km" in detail
        assert "short" in detail.lower()
        assert "11%" in detail

    async def test_it_is_recorded_under_its_own_reason(self, client, db_session):
        """So it stops hiding inside `no_chargers` and becomes countable."""
        vid = await vehicle_id(client)
        await client.post(
            "/api/trips", json=plan_body(vid, depart_soc=11.0, target_soc=10.0)
        )
        reasons = (
            await db_session.execute(
                select(AppEvent.reason).where(AppEvent.name == "plan_failed")
            )
        ).scalars().all()
        assert list(reasons) == ["route_too_short"]
        assert "route_too_short" in PLAN_FAILURE_REASONS

    async def test_a_long_charger_less_corridor_still_says_no_chargers(
        self, client, db_session, monkeypatch
    ):
        """The new reason must not swallow a real charging desert."""
        async def long_route(db, *a):
            return _short_route(600.0)

        async def no_nodes(db, r) -> list[ChargerNode]:
            return []

        monkeypatch.setattr("app.api.trips.routing.get_route", long_route)
        monkeypatch.setattr("app.api.trips.chargers_svc.chargers_for_route", no_nodes)
        vid = await vehicle_id(client)
        resp = await client.post("/api/trips", json=plan_body(vid))
        assert resp.status_code == 422
        assert "no fast chargers" in resp.json()["detail"].lower()

        reasons = (
            await db_session.execute(
                select(AppEvent.reason).where(AppEvent.name == "plan_failed")
            )
        ).scalars().all()
        assert list(reasons) == ["no_chargers"]
