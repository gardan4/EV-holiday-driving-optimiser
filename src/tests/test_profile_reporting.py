"""What the username feature reports, and what a delete would have erased.

Two halves, and the second is the reason the first can be trusted.

`services/profiles.py` counts rows that exist: names, names that publish
something, and the share of a window's trips planned under one. Those are
ordinary aggregates and the tests here mostly pin the arithmetic and the
stock/flow split, which is the easy thing to get subtly wrong.

`services/counting.py` counts two things that stop existing — a released
username and a deleted trip. Every other number in the reporting is a count of
surviving rows, so without these a corridor that shrank looks like people
stopped driving it. Their tests are about what the row does NOT contain: no
username, no trip id, and nothing a public caller could have posted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.events import KNOWN_PATHS, SERVER_ONLY_EVENTS, normalize_path
from app.core.database import Base, get_db
from app.core.visitor import owner_hash
from app.main import app
from app.models import AppEvent, Profile, Trip, Vehicle
from app.services import corridors, counting, profiles
from app.services.geo import RouteGeometry
from app.services.routing import RouteData
from app.services.simulator import ChargerNode
from app.services.usage import ALLOWED_EVENTS, usage_stats
from tests.fixtures import nl_to_austria_route
from tests.test_api import BORN_SEED, plan_body, vehicle_id
from tests.test_users import SECRET, SECRET2

OWNER = owner_hash(SECRET)
OWNER2 = owner_hash(SECRET2)


# ---------------------------------------------------------------------------
# The allowlists these numbers depend on
# ---------------------------------------------------------------------------


class TestAllowlists:
    def test_the_erasure_events_are_countable(self):
        """An event outside `ALLOWED_EVENTS` is refused at the write endpoint
        and absent from the funnel, so it would be written and never read."""
        assert counting.TRIP_DELETED in ALLOWED_EVENTS
        assert counting.USERNAME_RELEASED in ALLOWED_EVENTS

    def test_the_erasure_events_are_server_only(self):
        """They exist to explain a table that shrank, which only works if they
        match it. A count anyone can post is not a cross-check."""
        assert counting.TRIP_DELETED in SERVER_ONLY_EVENTS
        assert counting.USERNAME_RELEASED in SERVER_ONLY_EVENTS

    def test_the_public_list_path_is_the_one_events_store(self):
        """`profiles.USER_LIST_PATH` is a copy of a constant in the API layer —
        pinned here, because if the two ever diverge the reach number silently
        becomes zero rather than wrong in a way anyone would notice."""
        assert profiles.USER_LIST_PATH in KNOWN_PATHS
        assert normalize_path("/u/marc") == profiles.USER_LIST_PATH
        assert normalize_path("/u/marc-2026?from=x") == profiles.USER_LIST_PATH


# ---------------------------------------------------------------------------
# The summary, against a seeded database
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def _trip(owner: str | None, days_ago: float = 0.0) -> Trip:
    return Trip(
        vehicle_id=uuid.uuid4(),
        owner_hash=owner,
        request={},
        result={},
        created_at=datetime.utcnow() - timedelta(days=days_ago),
    )


async def _seed(db: AsyncSession) -> None:
    db.add_all(
        [
            Profile(username="marc", owner_hash=OWNER, created_at=datetime.utcnow()),
            Profile(
                username="ann",
                owner_hash=OWNER2,
                created_at=datetime.utcnow() - timedelta(days=40),
            ),
            _trip(OWNER, 0.5),
            _trip(OWNER, 1.0),
            _trip(OWNER, 2.0),
            _trip(None, 1.0),
            # Outside a 7-day window, and the only trip its owner has.
            _trip(OWNER2, 30.0),
        ]
    )
    await db.commit()


class TestSummary:
    async def test_stock_is_all_time_and_flow_is_the_window(self, db):
        await _seed(db)
        s = await profiles.summary(db, datetime.utcnow() - timedelta(days=7))

        # Stock ignores the window: both names still exist.
        assert s.live == 2
        assert s.with_trips == 2
        assert s.empty == 0
        # Flow does not: only the four trips inside the window are counted.
        assert s.trips == 4
        assert s.published == 3
        # …and only the name claimed inside it.
        assert s.claimed == 1

    async def test_the_publish_rate_is_a_share_not_a_count(self, db):
        await _seed(db)
        s = await profiles.summary(db, datetime.utcnow() - timedelta(days=7))
        assert s.publish_rate == pytest.approx(0.75)
        assert s.active_rate == pytest.approx(1.0)

    async def test_an_empty_window_has_no_rate(self, db):
        """None, not 0.0. A quiet week has no share, and drawing one as 0%
        reads as everybody having stopped."""
        await _seed(db)
        s = await profiles.summary(db, datetime.utcnow() + timedelta(days=1))
        assert s.trips == 0
        assert s.publish_rate is None

    async def test_a_name_that_publishes_nothing_is_visible(self, db):
        """The feature's real drop-off: somebody opted in and then never
        planned again. No page-view count shows that."""
        db.add(Profile(username="quiet", owner_hash="f" * 32, created_at=datetime.utcnow()))
        await db.commit()
        s = await profiles.summary(db, None)
        assert s.live == 1
        assert s.with_trips == 0
        assert s.empty == 1
        assert s.active_rate == pytest.approx(0.0)

    async def test_trips_per_name_lands_in_the_shared_buckets(self, db):
        await _seed(db)
        s = await profiles.summary(db, datetime.utcnow() - timedelta(days=7))
        # marc has three trips in the window; ann's one is outside it, so she
        # contributes no bucket at all rather than a misleading "1 trip".
        assert s.per_name == [("3–5 trips", 1)]

    async def test_the_buckets_are_the_anonymous_planner_ones(self, db):
        """The two lists sit side by side on the dashboard, so they come out of
        one function. Different vocabularies would mean comparing nothing."""
        await _seed(db)
        s = await profiles.summary(db, None)
        assert {label for label, _ in s.per_name} <= set(corridors.TRIP_COUNT_LABELS)
        # Same bucketing, same edges: five trips is "3–5", six is "6+".
        assert corridors.trip_count_buckets([5.0]) == [("3–5 trips", 1)]
        assert corridors.trip_count_buckets([6.0]) == [("6+ trips", 1)]

    async def test_reach_counts_only_the_public_list_page(self, db):
        db.add_all(
            [
                AppEvent(name="page_view", path=profiles.USER_LIST_PATH, visitor="a" * 32),
                AppEvent(name="page_view", path=profiles.USER_LIST_PATH, visitor="b" * 32),
                AppEvent(name="page_view", path="/trip/:id", visitor="c" * 32),
                # Not a page view: a stray event on the same path must not
                # inflate "somebody opened a list".
                AppEvent(name="plan_submitted", path=profiles.USER_LIST_PATH, visitor="d" * 32),
            ]
        )
        await db.commit()
        s = await profiles.summary(db, datetime.utcnow() - timedelta(days=7))
        assert s.list_views == 2


class TestItCompilesOnTheRealEngine:
    def test_the_publish_share_is_a_case_not_a_cast(self):
        """Compiled against MSSQL, because the suite runs on SQLite and this is
        exactly the shape that shipped broken once: a comparison is a value on
        SQLite and a predicate on SQL Server, so casting one is silently fine
        here and a syntax error in production."""
        from sqlalchemy.dialects import mssql

        sql = str(profiles.flow_query(datetime.utcnow()).compile(dialect=mssql.dialect()))
        assert "CASE WHEN" in sql
        assert "AS INTEGER" not in sql
        assert "AS FLOAT" not in sql


class TestErasureCounts:
    async def test_both_kinds_are_counted_separately(self, db):
        db.add_all(
            [
                AppEvent(name=counting.TRIP_DELETED, visitor="a" * 32),
                AppEvent(name=counting.TRIP_DELETED, visitor="b" * 32),
                AppEvent(name=counting.USERNAME_RELEASED, visitor="c" * 32),
                AppEvent(name="page_view", visitor="d" * 32),
            ]
        )
        await db.commit()
        gone = await counting.erasures(db, datetime.utcnow() - timedelta(days=7))
        assert gone.trips_deleted == 2
        assert gone.usernames_released == 1

    async def test_nothing_erased_is_zero_not_none(self, db):
        gone = await counting.erasures(db, datetime.utcnow() - timedelta(days=7))
        assert (gone.trips_deleted, gone.usernames_released) == (0, 0)

    async def test_the_window_applies(self, db):
        db.add(
            AppEvent(
                name=counting.TRIP_DELETED,
                visitor="a" * 32,
                at=datetime.utcnow() - timedelta(days=30),
            )
        )
        await db.commit()
        recent = await counting.erasures(db, datetime.utcnow() - timedelta(days=7))
        ever = await counting.erasures(db, None)
        assert recent.trips_deleted == 0
        assert ever.trips_deleted == 1


# ---------------------------------------------------------------------------
# Through the API — what the rows actually contain
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def api_db():
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
async def client(api_db, monkeypatch):
    async def override_get_db():
        yield api_db

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


async def _events(db: AsyncSession, name: str) -> list[AppEvent]:
    return list(
        (await db.execute(select(AppEvent).where(AppEvent.name == name))).scalars().all()
    )


class TestReleaseIsRecorded:
    async def test_releasing_leaves_a_count_behind(self, client, api_db):
        """The profile row is gone, so this is the only trace. Without it the
        stock number drops with nothing saying whether a name was given up or
        never claimed at all."""
        await client.post(
            "/api/users", json={"username": "marc"}, headers={"X-Owner-Secret": SECRET}
        )
        resp = await client.delete(
            "/api/users/marc", headers={"X-Owner-Secret": SECRET}
        )
        assert resp.status_code == 204

        rows = await _events(api_db, counting.USERNAME_RELEASED)
        assert len(rows) == 1
        assert (
            await api_db.execute(select(func.count()).select_from(Profile))
        ).scalar_one() == 0

    async def test_the_row_carries_no_username(self, client, api_db):
        """A name is the one handle here that could be somebody's real one.
        Beside a pseudonym it would be exactly the join `normalize_path`
        collapses `/u/:username` to avoid."""
        await client.post(
            "/api/users", json={"username": "marc"}, headers={"X-Owner-Secret": SECRET}
        )
        await client.delete("/api/users/marc", headers={"X-Owner-Secret": SECRET})

        row = (await _events(api_db, counting.USERNAME_RELEASED))[0]
        assert row.path is None
        assert row.reason is None
        assert "marc" not in (row.visitor or "")
        assert row.visitor != owner_hash(SECRET)

    async def test_a_refused_release_counts_nothing(self, client, api_db):
        await client.post(
            "/api/users", json={"username": "marc"}, headers={"X-Owner-Secret": SECRET}
        )
        resp = await client.delete(
            "/api/users/marc", headers={"X-Owner-Secret": SECRET2}
        )
        assert resp.status_code == 403
        assert await _events(api_db, counting.USERNAME_RELEASED) == []


class TestDeleteIsRecorded:
    async def test_deleting_a_trip_leaves_a_count_behind(self, client, api_db):
        planned = await client.post("/api/trips", json=plan_body(await vehicle_id(client)))
        trip_id = planned.json()["id"]
        assert (await client.delete(f"/api/trips/{trip_id}")).status_code == 204

        rows = await _events(api_db, counting.TRIP_DELETED)
        assert len(rows) == 1
        assert (
            await api_db.execute(select(func.count()).select_from(Trip))
        ).scalar_one() == 0

    async def test_the_row_carries_no_trip_id(self, client, api_db):
        """The id is the capability that opens the trip. Paired with a
        pseudonym it would be a record of who looked at whose journey."""
        planned = await client.post("/api/trips", json=plan_body(await vehicle_id(client)))
        trip_id = planned.json()["id"]
        await client.delete(f"/api/trips/{trip_id}")

        row = (await _events(api_db, counting.TRIP_DELETED))[0]
        assert row.path is None
        stored = [
            str(getattr(row, c.key)) for c in AppEvent.__table__.columns if c.key != "id"
        ]
        assert not any(trip_id[:8] in value for value in stored)

    async def test_deleting_a_missing_trip_counts_nothing(self, client, api_db):
        resp = await client.delete(f"/api/trips/{uuid.uuid4()}")
        assert resp.status_code == 404
        assert await _events(api_db, counting.TRIP_DELETED) == []

    @pytest.mark.parametrize(
        "name", [counting.TRIP_DELETED, counting.USERNAME_RELEASED]
    )
    async def test_the_public_endpoint_refuses_to_post_one(self, client, api_db, name):
        resp = await client.post("/api/events", json={"name": name, "path": "/"})
        assert resp.status_code == 422
        assert await _events(api_db, name) == []


class TestUsageStatsCarriesIt:
    async def test_the_report_reaches_the_username_numbers(self, client, api_db):
        await client.post(
            "/api/users", json={"username": "marc"}, headers={"X-Owner-Secret": SECRET}
        )
        vid = await vehicle_id(client)
        await client.post(
            "/api/trips", json=plan_body(vid), headers={"X-Owner-Secret": SECRET}
        )
        anon = await client.post("/api/trips", json=plan_body(vid))
        await client.delete(f"/api/trips/{anon.json()['id']}")

        stats = await usage_stats(api_db, days=7)
        assert stats.profiles.live == 1
        assert stats.profiles.published == 1
        assert stats.profiles.trips == 1  # the anonymous one was deleted
        assert stats.profiles.publish_rate == pytest.approx(1.0)
        assert stats.trips_deleted == 1

    async def test_the_erasures_appear_in_the_funnel_list(self, client, api_db):
        """`usage_stats.funnel` renders every allowlisted name, so a new event
        that is written and never listed would be invisible in the terminal
        report."""
        stats = await usage_stats(api_db, days=7)
        listed = {f.label for f in stats.funnel}
        assert counting.TRIP_DELETED in listed
        assert counting.USERNAME_RELEASED in listed
