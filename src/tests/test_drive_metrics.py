"""What happened after the plan, and how honest the numbers about it are.

`trip_runs` / `trip_events` had been recording every live drive since the
feature shipped and nothing read them. These are the definitions that matter
once something does — each of them is a place where a plausible-looking
alternative would quietly say something false.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import case
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import Trip, TripEvent, TripRun, TripStat, UpstreamCall, Vehicle
from app.services import drives, trip_shape, upstream_health

NOW = datetime.utcnow()


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def vehicle(db_session: AsyncSession) -> uuid.UUID:
    vid = uuid.uuid4()
    db_session.add(
        Vehicle(
            id=vid,
            slug="test-car",
            make="Test",
            model="Car",
            usable_kwh=60.0,
            consumption={"a": 55, "b": 0.0105},
            charge_curve=[[0, 50], [100, 10]],
            max_dc_kw=150.0,
            sort_order=1,
        )
    )
    await db_session.commit()
    return vid


async def _run(
    db: AsyncSession,
    vehicle_id: uuid.UUID,
    *,
    status: str,
    minutes: float | None = 120.0,
    offset_m: float = 200_000.0,
    n_replans: int = 0,
) -> uuid.UUID:
    tid, rid = uuid.uuid4(), uuid.uuid4()
    started = NOW - timedelta(hours=6)
    db.add(Trip(id=tid, vehicle_id=vehicle_id, request={}, result={}, created_at=started))
    db.add(
        TripRun(
            id=rid,
            trip_id=tid,
            status=status,
            started_at=started,
            finished_at=(started + timedelta(minutes=minutes)) if minutes else None,
            last_seen_at=started,
            planned_speed_kph=120.0,
            route_snapshot={},
            state={"offset_m": offset_m},
            n_pings=50,
            n_replans=n_replans,
        )
    )
    await db.commit()
    return rid


class TestCompletionRate:
    @pytest.mark.asyncio
    async def test_active_runs_are_not_counted_as_failures(
        self, db_session: AsyncSession, vehicle
    ):
        """A drive still in progress has not not-completed. Counting it in the
        denominator makes the rate sag every time somebody is mid-journey."""
        await _run(db_session, vehicle, status="finished")
        await _run(db_session, vehicle, status="abandoned")
        await _run(db_session, vehicle, status="active", minutes=None)

        out = await drives.summary(db_session, None)
        assert out.runs == 3
        assert out.active == 1
        # 1 finished of 2 settled — not 1 of 3.
        assert out.completion_rate == 0.5

    @pytest.mark.asyncio
    async def test_rate_is_none_with_nothing_settled(
        self, db_session: AsyncSession, vehicle
    ):
        await _run(db_session, vehicle, status="active", minutes=None)
        out = await drives.summary(db_session, None)
        assert out.completion_rate is None

    @pytest.mark.asyncio
    async def test_duration_is_a_median_not_a_mean(
        self, db_session: AsyncSession, vehicle
    ):
        """One drive left running overnight in a car park drags a mean into
        meaninglessness, and there are few enough runs that one outlier is a
        large share of them."""
        for minutes in (60.0, 90.0, 120.0, 3000.0):
            await _run(db_session, vehicle, status="finished", minutes=minutes)
        out = await drives.summary(db_session, None)
        assert out.median_minutes == 105.0  # not the 817.5 mean


class TestChargeStops:
    @pytest.mark.asyncio
    async def test_pairs_start_with_end(self, db_session: AsyncSession, vehicle):
        rid = await _run(db_session, vehicle, status="finished")
        base = NOW - timedelta(hours=5)
        db_session.add(TripEvent(run_id=rid, at=base, kind="charge_start", soc=0.12))
        db_session.add(
            TripEvent(
                run_id=rid, at=base + timedelta(minutes=26), kind="charge_end", soc=0.78
            )
        )
        await db_session.commit()

        stops = await drives.charge_stops(db_session, None)
        assert len(stops) == 1
        assert stops[0].minutes == 26.0
        assert stops[0].soc_start == 0.12
        assert stops[0].soc_end == 0.78

    @pytest.mark.asyncio
    async def test_an_unclosed_charge_is_dropped_not_invented(
        self, db_session: AsyncSession, vehicle
    ):
        """A run that stopped reporting mid-charge has a start with no end.
        Closing it at the run's end would invent the one number the pair exists
        to measure."""
        rid = await _run(db_session, vehicle, status="abandoned")
        db_session.add(
            TripEvent(run_id=rid, at=NOW - timedelta(hours=4), kind="charge_start", soc=0.1)
        )
        await db_session.commit()

        assert await drives.charge_stops(db_session, None) == []

    @pytest.mark.asyncio
    async def test_stops_do_not_leak_across_runs(
        self, db_session: AsyncSession, vehicle
    ):
        a = await _run(db_session, vehicle, status="finished")
        b = await _run(db_session, vehicle, status="finished")
        db_session.add(
            TripEvent(run_id=a, at=NOW - timedelta(hours=4), kind="charge_start", soc=0.1)
        )
        db_session.add(
            TripEvent(run_id=b, at=NOW - timedelta(hours=3), kind="charge_end", soc=0.8)
        )
        await db_session.commit()

        # An unclosed start in one run and a stray end in another must not pair.
        assert await drives.charge_stops(db_session, None) == []


class TestPlanVsActual:
    @pytest.mark.asyncio
    async def test_achieved_is_a_journey_average(
        self, db_session: AsyncSession, vehicle
    ):
        """It includes charging and rest, so it sits below the cruise speed by
        design — that gap is the quantity the planner's curve is built on."""
        await _run(
            db_session, vehicle, status="finished", minutes=120.0, offset_m=180_000.0
        )
        out = await drives.plan_vs_actual(db_session, None)
        assert out == [(120.0, 90.0)]

    @pytest.mark.asyncio
    async def test_unfinished_runs_are_excluded(
        self, db_session: AsyncSession, vehicle
    ):
        await _run(db_session, vehicle, status="active", minutes=None)
        assert await drives.plan_vs_actual(db_session, None) == []


class TestTripShape:
    @pytest_asyncio.fixture
    async def stats(self, db_session: AsyncSession, vehicle):
        for i, (km, total, charge, month, feasible) in enumerate(
            [
                (200, 200.0, 10.0, 7, True),
                (600, 400.0, 60.0, 8, True),
                (1200, 800.0, 160.0, 8, True),
                (900, 600.0, 60.0, 12, False),
            ]
        ):
            tid = uuid.uuid4()
            db_session.add(
                Trip(id=tid, vehicle_id=vehicle, request={}, result={}, created_at=NOW)
            )
            db_session.add(
                TripStat(
                    trip_id=tid,
                    vehicle_id=vehicle,
                    created_at=NOW - timedelta(hours=i),
                    origin_gh="u1hb",
                    dest_gh="u0qj",
                    origin_cc="NL",
                    dest_cc="AT",
                    countries=["NL", "DE", "AT"],
                    distance_km=km,
                    departure_month=month,
                    feasible=feasible,
                    optimum_speed_kph=110.0,
                    optimum_n_stops=i,
                    optimum_total_min=total,
                    optimum_charge_min=charge,
                )
            )
        await db_session.commit()
        return db_session

    @pytest.mark.asyncio
    async def test_charge_share_is_per_trip_not_pooled_minutes(self, stats):
        """Weighted per trip, so one 1200 km drive does not count for as much
        as ten weekend trips."""
        out = await trip_shape.summary(stats, None)
        # 5%, 15%, 20%, 10% → mean 12.5, median 12.5
        assert out.mean_charge_share == pytest.approx(12.5)
        assert out.median_charge_share == pytest.approx(12.5)

    @pytest.mark.asyncio
    async def test_distances_are_a_distribution_not_just_a_mean(self, stats):
        out = await trip_shape.summary(stats, None)
        assert sum(s.value for s in out.distances) == 4
        assert {s.label for s in out.distances} == set(trip_shape.DISTANCE_LABELS)

    @pytest.mark.asyncio
    async def test_feasible_rate(self, stats):
        out = await trip_shape.summary(stats, None)
        assert out.trips == 4
        assert out.feasible == 3
        assert out.feasible_rate == 0.75

    @pytest.mark.asyncio
    async def test_transit_countries_count_once_per_trip(self, stats):
        out = await trip_shape.summary(stats, None)
        counts = {s.label: s.value for s in out.transit_countries}
        assert counts == {"NL": 4, "DE": 4, "AT": 4}

    @pytest.mark.asyncio
    async def test_months_cover_the_whole_year(self, stats):
        """Seasonality for a holiday app: a month with no trips is a real
        answer, so all twelve are present."""
        out = await trip_shape.summary(stats, None)
        assert len(out.months) == 12
        counts = {s.label: s.value for s in out.months}
        assert counts["Aug"] == 2
        assert counts["Jan"] == 0

    @pytest.mark.asyncio
    async def test_labels_are_ascii(self, stats):
        """`String` is VARCHAR on MSSQL — a codepage, not Unicode."""
        out = await trip_shape.summary(stats, None)
        for s in out.distances + out.charge_shares + out.months:
            s.label.encode("ascii")


class TestUpstreamHealth:
    @pytest_asyncio.fixture
    async def calls(self, db_session: AsyncSession):
        for day in range(3):
            for i in range(4):
                db_session.add(
                    UpstreamCall(
                        at=NOW - timedelta(days=day, hours=i),
                        provider="ors",
                        kind="directions",
                        calls=0 if i else 10,
                        cache_hits=10 if i else 0,
                        duration_ms=100 * (i + 1),
                        ok=(i != 3),
                    )
                )
        await db_session.commit()
        return db_session

    @pytest.mark.asyncio
    async def test_daily_is_zero_filled(self, calls):
        out = await upstream_health.daily(calls, "ors", days=7)
        assert len(out) == 7
        assert [d.day for d in out] == sorted(d.day for d in out)
        # The four days before the fixture's window are real zeroes, not gaps.
        assert out[0].operations == 0

    @pytest.mark.asyncio
    async def test_hit_rate_is_none_on_a_quiet_day(self, calls):
        out = await upstream_health.daily(calls, "ors", days=7)
        assert out[0].hit_rate is None
        assert out[-1].hit_rate is not None

    @pytest.mark.asyncio
    async def test_failures_are_counted(self, calls):
        out = await upstream_health.daily(calls, "ors", days=7)
        assert sum(d.failures for d in out) == 3

    @pytest.mark.asyncio
    async def test_percentiles_not_a_mean(self, calls):
        """The population is bimodal — warm operations in milliseconds, cold
        sweeps in seconds — so an average lands in the gap and describes
        neither."""
        out = await upstream_health.latency(calls, days=7)
        assert len(out) == 1
        lat = out[0]
        assert lat.provider == "ors"
        assert lat.p50_ms is not None
        assert lat.p95_ms >= lat.p50_ms
        assert lat.max_ms == 400

    def test_failure_count_compiles_to_a_case_on_mssql(self):
        """`CAST(ok = 0 AS FLOAT)` is valid SQLite and a T-SQL syntax error.

        SQL Server has no boolean *value* type, so `ok = 0` is a predicate and
        cannot be cast — the server rejects it with "Incorrect syntax near the
        keyword 'AS'". Every test above passes on the SQLite engine with the
        broken version, so this compiles the REAL statement against the MSSQL
        dialect and asserts its shape. Compiling a query the test builds itself
        would only prove the test is well written.
        """
        from sqlalchemy.dialects import mssql

        sql = str(
            upstream_health.daily_query("ors", NOW, "mssql").compile(
                dialect=mssql.dialect()
            )
        )
        # The failure count is a CASE…
        assert "CASE WHEN" in sql
        # …and nothing casts a comparison.
        assert "AS FLOAT" not in sql
        # The truncation is a real DATE cast, not the DATETIME that
        # SQLAlchemy's `Date` degrades to and which does not truncate at all.
        assert "CAST(upstream_calls.at AS DATE)" in sql

    @pytest.mark.asyncio
    async def test_empty_is_not_an_error(self, db_session: AsyncSession):
        assert await upstream_health.latency(db_session, days=7) == []
        out = await upstream_health.daily(db_session, "ocm", days=3)
        assert [d.operations for d in out] == [0, 0, 0]
