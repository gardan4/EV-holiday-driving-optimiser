"""The composable query layer, and the parity that keeps the reports honest.

Two things are being protected here.

The first is the **allowlist**: dimension and measure names arrive from a
browser, so anything not explicitly permitted has to be refused rather than
passed to SQL. The pseudonym columns are the ones that matter — a GROUP BY on
`visitor` or `client_id` would turn a counting table into a per-person list.

The second is **parity**. `usage_stats` was rebuilt on top of these primitives,
and `scripts.usage_report` prints its output in a terminal. If the recomposed
version disagrees with the old per-day loop, the dashboard and the terminal
start quoting different numbers for the same window — which is the exact
failure the shared service layer exists to prevent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import AppEvent
from app.services import analytics as A
from app.services.analytics.filters import BadQuery, bucket, normalize_bucket
from app.services.usage import usage_stats


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def _event(at: datetime, name: str = "page_view", **kw) -> AppEvent:
    kw.setdefault("visitor", uuid.uuid4().hex[:32])
    kw.setdefault("path", "/")
    return AppEvent(at=at, name=name, **kw)


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession):
    """Five days of traffic across two devices and two countries."""
    now = datetime.utcnow()
    for day in range(5):
        at = now - timedelta(days=day, hours=2)
        for i in range(4):
            db_session.add(
                _event(
                    at,
                    visitor=f"v{day}-{i % 2}",
                    client_id=f"c{i % 3}",
                    device="mobile" if i % 2 else "desktop",
                    country="NL" if i % 2 else "DE",
                    campaign="r-electricvehicles" if i < 2 else None,
                )
            )
        db_session.add(
            _event(at, name="plan_submitted", device="mobile", country="NL")
        )
        db_session.add(_event(at, name="trip_planned", device="mobile", country="NL"))
    await db_session.commit()
    return db_session


class TestAllowlist:
    """A filter API reachable from a browser must not become free-form SQL."""

    @pytest.mark.asyncio
    async def test_unknown_dimension_is_refused(self, seeded: AsyncSession):
        with pytest.raises(BadQuery):
            await A.breakdown(seeded, A.Filters.window(7), "definitely_not_a_column")

    @pytest.mark.asyncio
    async def test_unknown_measure_is_refused(self, seeded: AsyncSession):
        with pytest.raises(BadQuery):
            await A.breakdown(seeded, A.Filters.window(7), "device", m="secrets")

    @pytest.mark.asyncio
    async def test_unknown_granularity_is_refused(self, seeded: AsyncSession):
        with pytest.raises(BadQuery):
            await A.timeseries(seeded, A.Filters.window(7), granularity="century")

    @pytest.mark.parametrize("pseudonym", ["visitor", "client_id"])
    def test_pseudonyms_are_not_groupable(self, pseudonym: str):
        """The whole point of a salted daily hash is that it cannot be used to
        build a per-person list. Exposing it as a GROUP BY dimension would hand
        exactly that back."""
        assert pseudonym not in A.DIMENSIONS
        assert pseudonym not in A.FILTERABLE

    def test_event_name_is_groupable_but_not_filterable(self):
        """The funnel needs every stage at once; a filter pinning `name` would
        collapse every funnel panel to a single bar."""
        assert "name" in A.DIMENSIONS
        assert "name" not in A.FILTERABLE


class TestBuckets:
    """The MSSQL/SQLite date-truncation split, which has no other test surface."""

    def test_both_dialects_compile(self):
        from sqlalchemy.dialects import mssql, sqlite

        for name, dialect in (("sqlite", sqlite.dialect()), ("mssql", mssql.dialect())):
            for granularity in ("day", "hour"):
                sql = str(
                    select(bucket(granularity, name)).compile(dialect=dialect)
                )
                assert sql

    def test_mssql_day_truncates(self):
        """`CAST(x AS DATE)`, not DATETIME.

        SQLAlchemy's `Date` renders as CAST(... AS DATETIME) whenever the MSSQL
        dialect cannot read `server_version_info` — and a cast to DATETIME does
        not truncate, so every row would land in its own "day" and the chart
        would show one bar per event.
        """
        from sqlalchemy.dialects import mssql

        sql = str(select(bucket("day", "mssql")).compile(dialect=mssql.dialect()))
        assert "AS DATE)" in sql
        assert "DATETIME" not in sql

    @pytest.mark.parametrize(
        "value,granularity,expected",
        [
            ("2026-08-12", "day", "2026-08-12"),
            ("2026-08-12T14:00", "hour", "2026-08-12T14:00"),
            (datetime(2026, 8, 12, 14, 30), "day", "2026-08-12"),
            (datetime(2026, 8, 12, 14, 30), "hour", "2026-08-12T14:00"),
        ],
    )
    def test_normalize_is_one_shape_per_engine(self, value, granularity, expected):
        """SQLite returns a string and MSSQL a date/datetime for the same query.
        Without normalising, the x-axis changes shape between the test engine
        and production."""
        assert normalize_bucket(value, granularity) == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize("days", [1, 7, 30])
    async def test_series_has_one_bucket_per_day(self, seeded: AsyncSession, days: int):
        points = await A.timeseries(seeded, A.Filters.window(days))
        assert len(points) == days
        # Zero-filled and ordered: a quiet day is a real answer, and a chart
        # that omits it draws a straight line over the gap.
        assert [p.bucket for p in points] == sorted(p.bucket for p in points)


class TestFilters:
    @pytest.mark.asyncio
    async def test_a_facet_narrows_every_measure(self, seeded: AsyncSession):
        everything = await A.totals(seeded, A.Filters.window(7))
        mobile = await A.totals(seeded, A.Filters.window(7, device="mobile"))
        assert mobile["events"] < everything["events"]
        assert mobile["events"] > 0

    @pytest.mark.asyncio
    async def test_filters_compose(self, seeded: AsyncSession):
        one = await A.totals(seeded, A.Filters.window(7, device="mobile"))
        two = await A.totals(
            seeded, A.Filters.window(7, device="mobile", country="DE")
        )
        # Every mobile event in the fixture is NL, so adding DE empties it.
        assert two["events"] == 0
        assert one["events"] > 0

    @pytest.mark.asyncio
    async def test_unknown_value_matches_nothing_rather_than_erroring(
        self, seeded: AsyncSession
    ):
        """Values are bound parameters, not an allowlist — 'show me traffic from
        a browser that has never visited' is a legitimate question with the
        answer zero."""
        out = await A.totals(seeded, A.Filters.window(7, device="fridge"))
        assert out["events"] == 0

    def test_previous_window_is_the_same_length_and_abuts(self):
        f = A.Filters.window(7)
        p = f.previous()
        assert p.until == f.since
        assert (p.until - p.since) == (f.until - f.since)

    @pytest.mark.asyncio
    async def test_compare_withholds_a_ratio_against_zero(self, seeded: AsyncSession):
        """"Up 100%" from nothing reads as growth and means "it started"."""
        out = await A.compare(seeded, A.Filters.window(1))
        for stat in out.values():
            if stat["previous"] == 0:
                assert stat["change"] is None


class TestFunnelAndRetention:
    @pytest.mark.asyncio
    async def test_funnel_is_in_journey_order(self, seeded: AsyncSession):
        stages, _ = await A.funnel(seeded, A.Filters.window(7))
        assert [s.name for s in stages] == [
            "page_view",
            "plan_submitted",
            "trip_planned",
            "drive_started",
        ]
        # The first stage has nothing to drop off from.
        assert stages[0].from_previous is None
        assert stages[1].from_previous is not None

    @pytest.mark.asyncio
    async def test_failures_are_not_a_stage(self, seeded: AsyncSession):
        stages, failures = await A.funnel(seeded, A.Filters.window(7))
        assert "plan_failed" not in {s.name for s in stages}
        assert failures == 0

    @pytest.mark.asyncio
    async def test_returning_means_seen_before_the_window(self, db_session: AsyncSession):
        now = datetime.utcnow()
        # One browser seen last month and again today; one brand new one that
        # visits twice inside the window.
        db_session.add(_event(now - timedelta(days=40), client_id="old"))
        db_session.add(_event(now - timedelta(hours=1), client_id="old"))
        db_session.add(_event(now - timedelta(hours=3), client_id="new"))
        db_session.add(_event(now - timedelta(hours=2), client_id="new"))
        await db_session.commit()

        seen = await A.retention(db_session, A.Filters.window(7))
        assert seen.identified == 2
        # Twice inside the window is one visit, not a return — counting it would
        # flatter every launch spike into looking like a product.
        assert seen.returning == 1
        assert seen.rate == 0.5

    @pytest.mark.asyncio
    async def test_rate_is_none_without_a_denominator(self, db_session: AsyncSession):
        seen = await A.retention(db_session, A.Filters.window(7))
        assert seen.identified == 0
        assert seen.rate is None


class TestHistogram:
    def test_bins_and_overflow(self):
        out = A.histogram([1, 120, 300, 900], [100, 500])
        assert [s.value for s in out] == [1, 2, 1]

    def test_labels_are_ascii(self):
        """Derived labels end up in `String` columns and query strings — VARCHAR
        on MSSQL is a codepage, not Unicode."""
        for s in A.histogram([1], [10, 20]):
            s.label.encode("ascii")


class TestUsageStatsParity:
    """The recomposed `usage_stats` must still answer what the old one did.

    The daily series is checked against a from-scratch reimplementation of the
    per-day loop it replaced, rather than against a recorded snapshot — a
    snapshot would only prove the output has not changed since somebody pasted
    it in.
    """

    @pytest.mark.asyncio
    async def test_daily_matches_a_per_day_loop(self, seeded: AsyncSession):
        days = 7
        stats = await usage_stats(seeded, days=days)
        assert len(stats.daily) == days

        now = datetime.utcnow()
        for i in range(days):
            start = (now - timedelta(days=days - 1 - i)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            row = (
                await seeded.execute(
                    select(func.count(distinct(AppEvent.visitor)), func.count()).where(
                        AppEvent.at >= start,
                        AppEvent.at < start + timedelta(days=1),
                        AppEvent.name == "page_view",
                    )
                )
            ).one()
            assert stats.daily[i].day == start.date().isoformat()
            assert stats.daily[i].visitors == int(row[0])
            assert stats.daily[i].page_views == int(row[1])

    @pytest.mark.asyncio
    async def test_totals_and_usage_stats_agree_on_visitors(
        self, db_session: AsyncSession
    ):
        """One word, one query.

        `totals` backs the dashboard tile and `usage_stats` the terminal
        report. Counting every event's visitor in one and only page-view
        visitors in the other makes them print different numbers under the same
        label — which is precisely the drift the shared service layer exists to
        stop. The fixture includes a browser that reported a plan without a
        page view, because that is the case where the two definitions split.
        """
        now = datetime.utcnow()
        db_session.add(_event(now, visitor="viewer-a"))
        db_session.add(_event(now, visitor="viewer-b"))
        db_session.add(_event(now, name="plan_submitted", visitor="never-viewed"))
        await db_session.commit()

        f = A.Filters.window(7)
        counters = await A.totals(db_session, f)
        stats = await usage_stats(db_session, days=7)

        assert counters["visitors"] == stats.visitors == 2
        assert counters["page_views"] == stats.page_views == 2
        # The all-events count is still available where it is the right
        # question — it is just not called "visitors".
        assert counters["events"] == 3

    @pytest.mark.asyncio
    async def test_funnel_still_lists_every_allowed_event(self, seeded: AsyncSession):
        from app.services.usage import ALLOWED_EVENTS

        stats = await usage_stats(seeded, days=7)
        assert {f.label for f in stats.funnel} == set(ALLOWED_EVENTS)

    @pytest.mark.asyncio
    async def test_null_paths_are_kept_as_unknown(self, db_session: AsyncSession):
        """`top_paths` sits next to the page-view headline. Dropping NULL rows
        would make the column add up to less than the number above it."""
        now = datetime.utcnow()
        db_session.add(_event(now, path=None))
        db_session.add(_event(now, path="/"))
        await db_session.commit()

        stats = await usage_stats(db_session, days=7)
        assert "unknown" in {p.label for p in stats.top_paths}
        assert sum(p.count for p in stats.top_paths) == stats.page_views

    @pytest.mark.asyncio
    async def test_breakdowns_drop_nulls(self, db_session: AsyncSession):
        """Unlike `path`: a NULL device is an absent header, and counting it as
        a category invents a segment nobody asked about."""
        now = datetime.utcnow()
        db_session.add(_event(now, device=None))
        db_session.add(_event(now, device="mobile"))
        await db_session.commit()

        stats = await usage_stats(db_session, days=7)
        assert [d.label for d in stats.devices] == ["mobile"]

    @pytest.mark.asyncio
    async def test_window_is_clamped(self, seeded: AsyncSession):
        assert (await usage_stats(seeded, days=0)).days == 1
        assert (await usage_stats(seeded, days=9999)).days == A.MAX_DAYS
