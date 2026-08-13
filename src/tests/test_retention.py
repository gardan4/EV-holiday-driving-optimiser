"""The two retention windows that did not exist until they were written.

Trips held exact coordinates forever and feedback held an email address
forever, both because deleting them was never implemented rather than because
anyone decided to keep them. `purge_old_runs` had already shown how that goes
wrong: a correct purge that nothing ever calls makes the privacy page a
statement about intentions.

So the tests here are about the two things that can quietly break.

* **The cascade.** A trip's drives, its location trail and its coarsened
  `TripStat` all have to go with it. The foreign keys have no cascade on
  purpose, so the order lives in code and a missed table leaves an orphaned GPS
  trace with nothing left pointing at it.
* **The window.** Under about thirteen months a share link dies between one
  summer and the next, which for a holiday app means the feature broke.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import Feedback, Trip, TripEvent, TripRun, TripStat, Vehicle
from tests.test_api import BORN_SEED

pytestmark = pytest.mark.asyncio


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


class _Maker:
    """Stands in for `AsyncSessionLocal` so a purge runs on the test session.

    Same shape the trip-stat id purge test uses: callable, and an async context
    manager that hands back the one session the assertions can see.
    """

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


async def _vehicle_id(db) -> uuid.UUID:
    return (await db.execute(select(Vehicle.id))).scalars().first()


async def _make_trip(db, *, age_days: int, with_drive: bool = False) -> uuid.UUID:
    """A trip `age_days` old, optionally with a drive, a breadcrumb and a stat."""
    created = datetime.utcnow() - timedelta(days=age_days)
    vid = await _vehicle_id(db)
    trip = Trip(
        id=uuid.uuid4(),
        vehicle_id=vid,
        request={"origin": "Utrecht", "destination": "Innsbruck"},
        result={"speeds": []},
        created_at=created,
    )
    db.add(trip)
    db.add(
        TripStat(
            trip_id=trip.id,
            created_at=created,
            vehicle_id=vid,
            origin_gh="u17b",
            dest_gh="u0mu",
            distance_km=950,
        )
    )
    if with_drive:
        run = TripRun(
            id=uuid.uuid4(),
            trip_id=trip.id,
            planned_speed_kph=120.0,
            route_snapshot={},
            state={},
            started_at=created,
            last_seen_at=created,
            created_at=created,
        )
        db.add(run)
        db.add(TripEvent(run_id=run.id, at=created, kind="breadcrumb", lat=52.0, lon=5.1))
    await db.commit()
    return trip.id


async def _count(db, model) -> int:
    return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


class TestTripRetention:
    async def test_an_expired_trip_takes_its_drive_trail_and_stat_with_it(
        self, db_session, monkeypatch
    ):
        """The whole point. An orphaned GPS trace with no trip left pointing at
        it is worse than the trip was."""
        import scripts.purge_old_trips as mod
        from scripts.purge_old_trips import DEFAULT_RETENTION_DAYS, purge

        old = await _make_trip(
            db_session, age_days=DEFAULT_RETENTION_DAYS + 10, with_drive=True
        )
        fresh = await _make_trip(db_session, age_days=5, with_drive=True)
        monkeypatch.setattr(mod, "AsyncSessionLocal", _Maker(db_session))

        trips, runs = await purge(days=DEFAULT_RETENTION_DAYS, apply=True)
        assert (trips, runs) == (1, 1)

        remaining = (await db_session.execute(select(Trip.id))).scalars().all()
        assert remaining == [fresh], "purged the wrong trip"
        assert old not in remaining

        # Nothing may survive that pointed at the deleted trip.
        assert await _count(db_session, TripRun) == 1
        assert await _count(db_session, TripEvent) == 1
        assert await _count(db_session, TripStat) == 1

    async def test_dry_run_changes_nothing(self, db_session, monkeypatch):
        import scripts.purge_old_trips as mod
        from scripts.purge_old_trips import DEFAULT_RETENTION_DAYS, purge

        await _make_trip(
            db_session, age_days=DEFAULT_RETENTION_DAYS + 10, with_drive=True
        )
        monkeypatch.setattr(mod, "AsyncSessionLocal", _Maker(db_session))

        trips, runs = await purge(days=DEFAULT_RETENTION_DAYS, apply=False)
        assert (trips, runs) == (1, 1), "dry run must still report what it would do"
        assert await _count(db_session, Trip) == 1
        assert await _count(db_session, TripEvent) == 1

    async def test_a_backlog_larger_than_one_batch_clears_completely(
        self, db_session, monkeypatch
    ):
        """Batching is why this loops. A run that stops after one batch leaves
        rows behind and nobody notices, because the log line still looks fine."""
        import scripts.purge_old_trips as mod
        from scripts.purge_old_trips import DEFAULT_RETENTION_DAYS, purge

        monkeypatch.setattr(mod, "BATCH", 3)
        for _ in range(7):
            await _make_trip(db_session, age_days=DEFAULT_RETENTION_DAYS + 1)
        monkeypatch.setattr(mod, "AsyncSessionLocal", _Maker(db_session))

        trips, _ = await purge(days=DEFAULT_RETENTION_DAYS, apply=True)
        assert trips == 7
        assert await _count(db_session, Trip) == 0
        assert await _count(db_session, TripStat) == 0

    def test_the_window_survives_a_holiday_cycle(self):
        """A link shared last August has to still open this August."""
        from scripts.purge_old_trips import DEFAULT_RETENTION_DAYS

        assert DEFAULT_RETENTION_DAYS >= 395


class TestFeedbackRetention:
    async def test_old_feedback_goes_and_recent_feedback_stays(
        self, db_session, monkeypatch
    ):
        import scripts.purge_old_feedback as mod
        from scripts.purge_old_feedback import DEFAULT_RETENTION_DAYS, purge

        db_session.add(
            Feedback(
                message="ancient",
                contact="someone@example.com",
                created_at=datetime.utcnow()
                - timedelta(days=DEFAULT_RETENTION_DAYS + 1),
            )
        )
        db_session.add(Feedback(message="recent", created_at=datetime.utcnow()))
        await db_session.commit()
        monkeypatch.setattr(mod, "AsyncSessionLocal", _Maker(db_session))

        assert await purge(days=DEFAULT_RETENTION_DAYS, apply=False) == 1
        assert await _count(db_session, Feedback) == 2, "dry run deleted something"

        assert await purge(days=DEFAULT_RETENTION_DAYS, apply=True) == 1
        left = (await db_session.execute(select(Feedback.message))).scalars().all()
        assert left == ["recent"]


class TestTheLoopActuallyCallsThem:
    """`purge_old_runs` was correct and uncalled for months, which made the
    privacy page wrong. The guard against a repeat is asserting the wiring."""

    def test_purge_loop_imports_both_new_purges(self):
        import inspect

        from app.main import _purge_loop

        src = inspect.getsource(_purge_loop)
        assert "purge_old_trips" in src
        assert "purge_old_feedback" in src
        assert "purge_trips(" in src
        assert "purge_feedback(" in src
