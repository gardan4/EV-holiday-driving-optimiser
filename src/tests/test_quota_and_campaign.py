"""Free-tier telemetry and campaign attribution.

Both exist for one evening: the launch. The quota half answers "are we about to
run out", which is the only failure here that ruins the post rather than merely
under-measuring it; the campaign half answers "which link worked", which the
referrer cannot because Reddit's app strips it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.events import normalize_campaign
from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.models import AppEvent, UpstreamCall
from app.services import quota
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


@pytest_asyncio.fixture
async def client(db_session):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


class TestCampaignValidation:
    def test_a_plain_tag_is_kept(self, monkeypatch):
        monkeypatch.setattr(settings, "CAMPAIGN_SOURCES", "")
        assert normalize_campaign("r-electricvehicles") == "r-electricvehicles"

    def test_case_is_normalised(self, monkeypatch):
        monkeypatch.setattr(settings, "CAMPAIGN_SOURCES", "")
        assert normalize_campaign("R-ElectricVehicles") == "r-electricvehicles"

    @pytest.mark.parametrize(
        "value",
        [
            None, "", "   ",
            "has space",
            "has_underscore",
            "-leading-dash",
            "UPPER!",
            "a" * 33,                       # too long
            "https://evil.example/x",
            "'; DROP TABLE app_events; --",
            "someone@example.com",
        ],
    )
    def test_anything_not_a_slug_is_discarded(self, value, monkeypatch):
        """It arrives on a public endpoint and is written to the database, so
        the pattern is what stops it becoming free-form storage."""
        monkeypatch.setattr(settings, "CAMPAIGN_SOURCES", "")
        assert normalize_campaign(value) is None

    def test_the_allowlist_is_enforced_when_set(self, monkeypatch):
        monkeypatch.setattr(settings, "CAMPAIGN_SOURCES", "r-evs, hn")
        assert normalize_campaign("r-evs") == "r-evs"
        assert normalize_campaign("hn") == "hn"
        assert normalize_campaign("not-mine") is None

    def test_an_empty_allowlist_accepts_any_well_formed_slug(self, monkeypatch):
        """Zero friction is the default so a launch cannot silently record
        nothing because a setting was not edited first."""
        monkeypatch.setattr(settings, "CAMPAIGN_SOURCES", "")
        assert normalize_campaign("whatever-i-posted") == "whatever-i-posted"


class TestCampaignAttribution:
    async def _visit(self, client, tag, name="page_view"):
        r = await client.post(
            "/api/events", json={"name": name, "path": "/", "campaign": tag}
        )
        assert r.status_code == 204

    async def test_the_tag_rides_the_whole_session_not_just_the_landing(
        self, client, db_session, monkeypatch
    ):
        """The point of carrying it: the interesting event is the plan, and
        that happens two pages after the link was clicked."""
        monkeypatch.setattr(settings, "CAMPAIGN_SOURCES", "")
        await self._visit(client, "r-evs")
        await self._visit(client, "r-evs", name="plan_submitted")

        stats = await usage_stats(db_session, days=7)
        (c,) = stats.campaigns
        assert c.label == "r-evs"
        assert c.page_views == 1
        assert c.plans == 1
        assert c.conversion_pct == 100.0

    async def test_conversion_separates_loud_channels_from_good_ones(
        self, client, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "CAMPAIGN_SOURCES", "")
        for _ in range(10):
            await self._visit(client, "loud")
        await self._visit(client, "loud", name="plan_submitted")
        for _ in range(2):
            await self._visit(client, "good")
        await self._visit(client, "good", name="plan_submitted")
        await self._visit(client, "good", name="plan_submitted")

        stats = await usage_stats(db_session, days=7)
        by = {c.label: c for c in stats.campaigns}
        assert by["loud"].page_views == 10 and by["loud"].conversion_pct == 10.0
        assert by["good"].page_views == 2 and by["good"].conversion_pct == 100.0

    async def test_a_junk_tag_does_not_reject_the_event(self, client, db_session):
        resp = await client.post(
            "/api/events", json={"name": "page_view", "path": "/", "campaign": "no spaces!"}
        )
        assert resp.status_code == 204
        row = (await db_session.execute(select(AppEvent))).scalars().one()
        assert row.campaign is None


async def _svc(db, provider: str, kind: str, days: int = 7):
    """One service's row out of the report.

    Provider alone stopped identifying a row when the report went per service —
    which it did because ORS meters directions and geocoding separately, and a
    combined gauge reported 96% headroom on an afternoon geocoding was refusing
    every request.
    """
    (row,) = [
        p
        for p in await quota.quota_report(db, days=days)
        if p.provider == provider and p.kind == kind
    ]
    return row


class TestQuota:
    async def test_a_cache_hit_costs_nothing_and_is_still_counted(self, db_session):
        """The hit rate is the number that predicts whether a spike survives,
        so the cheap half has to be recorded too."""
        await quota.record(db_session, provider="ors", kind="directions", cache_hits=1)
        await db_session.commit()
        p = await _svc(db_session, "ors", "directions")
        assert p.calls_today == 0
        assert p.cache_hits_today == 1
        assert p.hit_rate == 1.0

    async def test_headroom_falls_as_calls_are_spent(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "ORS_DAILY_QUOTA", 100)
        for _ in range(25):
            await quota.record(db_session, provider="ors", kind="directions", calls=1)
        await db_session.commit()
        p = await _svc(db_session, "ors", "directions")
        assert p.calls_today == 25
        assert p.headroom_pct == pytest.approx(75.0)

    async def test_a_corridor_records_its_whole_tile_count(self, db_session):
        """One row per operation, `calls` = tiles — the quota is counted in
        requests, and a 170-tile corridor must not put 170 rows in the table
        that measures it."""
        await quota.record(
            db_session, provider="ocm", kind="chargers", calls=35, cache_hits=5
        )
        await db_session.commit()
        assert (
            await db_session.execute(select(func.count()).select_from(UpstreamCall))
        ).scalar_one() == 1
        p = await _svc(db_session, "ocm", "chargers")
        assert p.calls_today == 35

    async def test_no_published_cap_withholds_headroom_rather_than_inventing_it(
        self, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "OCM_DAILY_QUOTA", 0)
        await quota.record(db_session, provider="ocm", kind="chargers", calls=10)
        await db_session.commit()
        p = await _svc(db_session, "ocm", "chargers")
        assert p.calls_today == 10
        assert p.headroom_pct is None

    async def test_yesterdays_spend_is_not_todays(self, db_session, monkeypatch):
        """The ceilings reset at midnight UTC, so a window total would say the
        tank is empty on a morning when it is full."""
        monkeypatch.setattr(settings, "ORS_DAILY_QUOTA", 100)
        await quota.record(db_session, provider="ors", kind="directions", calls=40)
        await db_session.commit()
        old = (await db_session.execute(select(UpstreamCall))).scalars().one()
        old.at = datetime.utcnow() - timedelta(days=1)
        await db_session.commit()

        p = await _svc(db_session, "ors", "directions", days=7)
        assert p.calls_today == 0
        assert p.headroom_pct == 100.0
        assert p.calls_window == 40  # still visible in the week

    async def test_failures_are_counted(self, db_session):
        """A burst of failures is what a blown quota looks like from in here."""
        await quota.record(db_session, provider="ors", kind="directions", calls=1, ok=False)
        await db_session.commit()
        p = await _svc(db_session, "ors", "directions")
        assert p.failures_today == 1

    async def test_no_route_spends_a_call_but_is_not_a_provider_failure(
        self, db_session, monkeypatch
    ):
        """`ok` means "the provider failed us", not "the answer was negative".

        ORS saying there is no road between two points is a successful call
        with a disappointing result. Marking it a failure would make the
        upstream-health signal fire whenever somebody picks an island, and an
        alarm that cries wolf on ordinary user error is one nobody reads on the
        night it matters.
        """
        from app.core.failures import PlanError
        from app.services import routing

        async def no_road(*a, **k):
            raise PlanError("no_route", 422, "No drivable route found.")

        monkeypatch.setattr(routing, "_fetch_route_payload", no_road)
        with pytest.raises(PlanError):
            await routing.get_route(db_session, 0.0, 0.0, 1.0, 1.0)

        p = await _svc(db_session, "ors", "directions")
        assert p.calls_today == 1, "the request was still spent"
        assert p.failures_today == 0, "a negative answer is not a provider failure"

    async def test_geocode_spend_is_counted(self, db_session, monkeypatch):
        """Autocomplete used to be invisible here.

        Every row this table held was written by `get_route`, so the largest
        consumer of the ORS free tier — the one call with no durable cache in
        front of it — contributed nothing to the gauge built to watch it.
        """
        monkeypatch.setattr(settings, "ORS_GEOCODE_DAILY_QUOTA", 100)
        await quota.record(db_session, provider="ors", kind="geocode", calls=1)
        await db_session.commit()
        p = await _svc(db_session, "ors", "geocode")
        assert p.calls_today == 1
        assert p.headroom_pct == pytest.approx(99.0)

    async def test_one_service_dying_is_not_hidden_by_the_other(
        self, db_session, monkeypatch
    ):
        """The exact failure this split exists for.

        Geocoding was exhausted while directions were untouched. Measured
        against a single provider-level ceiling the gauge read healthy, so the
        one instrument that should have said "your search box is down" said
        96% headroom instead.
        """
        monkeypatch.setattr(settings, "ORS_DAILY_QUOTA", 1000)
        monkeypatch.setattr(settings, "ORS_GEOCODE_DAILY_QUOTA", 10)
        await quota.record(db_session, provider="ors", kind="directions", calls=20)
        for _ in range(10):
            await quota.record(db_session, provider="ors", kind="geocode", calls=1)
        await db_session.commit()

        directions = await _svc(db_session, "ors", "directions")
        geocode = await _svc(db_session, "ors", "geocode")
        assert directions.headroom_pct == pytest.approx(98.0), "routing is fine"
        assert geocode.headroom_pct == 0.0, "and the search box is dead"

    async def test_each_service_has_its_own_ceiling(self):
        """Sharing one number between them is how the two get conflated again."""
        assert quota._quota("ors", "directions") == settings.ORS_DAILY_QUOTA
        assert quota._quota("ors", "geocode") == settings.ORS_GEOCODE_DAILY_QUOTA
        # An unknown pairing withholds headroom rather than borrowing a
        # denominator from its provider.
        assert quota._quota("ors", "something-new") == 0

    async def test_the_geocode_ENDPOINT_records_what_it_spent(
        self, client, db_session, monkeypatch
    ):
        """The wiring, not just the counter.

        `routing.geocode` had no database session for as long as it existed,
        which is the mundane reason its spend went unrecorded — so the session
        reaching it from the route is the thing worth pinning. A counter nobody
        passes a session to counts nothing.
        """
        from app.services import routing

        monkeypatch.setattr(settings, "ORS_API_KEY", "test-key")
        routing._geocode_cache.clear()

        class _Resp:
            @staticmethod
            def raise_for_status() -> None: ...

            @staticmethod
            def json() -> dict:
                return {
                    "features": [
                        {
                            "geometry": {"coordinates": [5.12, 52.09]},
                            "properties": {"label": "Utrecht, Netherlands", "country_a": "NLD"},
                        }
                    ]
                }

        async def fake_get(*a, **k):
            return _Resp()

        monkeypatch.setattr(routing._http(), "get", fake_get)

        resp = await client.get("/api/geocode?q=Utrecht")
        assert resp.status_code == 200, resp.text
        assert resp.json()[0]["label"] == "Utrecht, Netherlands"

        p = await _svc(db_session, "ors", "geocode")
        assert p.calls_today == 1

        # And the second, identical search is served from cache — counted as a
        # hit, so the rate that predicts whether a spike survives stays honest.
        assert (await client.get("/api/geocode?q=Utrecht")).status_code == 200
        p = await _svc(db_session, "ors", "geocode")
        assert p.calls_today == 1, "a cache hit must not spend a request"
        assert p.cache_hits_today == 1
        routing._geocode_cache.clear()

    async def test_a_two_letter_query_never_reaches_the_provider(self, client, db_session):
        """Autocomplete is the biggest consumer of the free tier and two-letter
        prefixes are its highest-volume, least-useful bucket. Rejecting them at
        the edge is the cheapest quota saving available."""
        resp = await client.get("/api/geocode?q=ut")
        assert resp.status_code == 422
        assert (
            await db_session.execute(select(func.count()).select_from(UpstreamCall))
        ).scalar_one() == 0

    async def test_recording_never_raises(self, db_session):
        """A telemetry row is worth strictly less than the request it measures."""
        class Broken:
            def add(self, _):
                raise RuntimeError("db is on fire")

        await quota.record(Broken(), provider="ors", kind="directions", calls=1)
