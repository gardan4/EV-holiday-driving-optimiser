"""Usage counting: what it stores, what it refuses to store, and who can read it.

The privacy claims on `/privacy` are the specification here. Most of these tests
exist to keep a specific sentence on that page true, and they say which.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.events import normalize_path, normalize_referrer
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.visitor import visitor_hash
from app.main import app
from app.models import AppEvent

TRIP_ID = "0189d4b1-2c3e-4f5a-8b9c-0d1e2f3a4b5c"


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


class TestPathScrubbing:
    """"the page you opened — as a pattern, never which trip"."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("/", "/"),
            ("/privacy", "/privacy"),
            ("/privacy/", "/privacy"),
            (f"/trip/{TRIP_ID}", "/trip/:id"),
            (f"/trip/{TRIP_ID}/live", "/trip/:id/live"),
            (f"/trip/{TRIP_ID}/drive", "/trip/:id/drive"),
            (f"/trip/{TRIP_ID}/runs/a1b2c3d4e5f6a7b8", "/trip/:id/runs/:id"),
            ("/ev", "/ev"),
            ("/ev/", "/ev"),
            # Unknown shapes collapse rather than being stored verbatim.
            ("/admin/whatever", "/other"),
            ("relative/path", "/other"),
            (None, None),
        ],
    )
    def test_paths_reduce_to_known_patterns(self, raw, expected):
        assert normalize_path(raw) == expected

    @pytest.mark.parametrize(
        "slug",
        [
            # Long-with-a-digit, which _OPAQUE_RE would have caught…
            "vw-id3-pro-58",
            "hyundai-ioniq6-74-rwd",
            # …and short or digit-free, which it would not. Both must land in
            # the same bucket or the catalog's traffic is split across two rows.
            "tesla-modely-rwd",
            "renault-5-52",
            "cupra-born-58",
        ],
    )
    def test_vehicle_slugs_all_collapse_to_one_pattern(self, slug):
        assert normalize_path(f"/ev/{slug}") == "/ev/:slug"
        assert normalize_path(f"/ev/{slug}?utm_source=linkedin") == "/ev/:slug"

    def test_ev_rule_does_not_swallow_deeper_paths(self):
        """The rule is one segment deep — anything else takes the normal route."""
        assert normalize_path("/ev/foo/bar") == "/other"

    def test_query_string_never_survives(self):
        """The query string is where a typed place name or a stray id would be."""
        assert normalize_path("/?origin=Utrecht&dest=Innsbruck") == "/"
        assert normalize_path(f"/trip/{TRIP_ID}/live?token=secret") == "/trip/:id/live"

    def test_no_trip_id_can_reach_the_column(self):
        """The one that matters: whatever is sent, the id does not get stored.

        A table pairing a share token with a visitor pseudonym would be a record
        of who looked at whose journey.
        """
        for raw in (
            f"/trip/{TRIP_ID}",
            f"/trip/{TRIP_ID}/live",
            f"/trip/{TRIP_ID}?x={TRIP_ID}",
            f"/{TRIP_ID}",
        ):
            assert TRIP_ID not in (normalize_path(raw) or "")


class TestReferrer:
    def test_keeps_host_drops_path(self):
        """"we keep the site's name and not the page"."""
        assert normalize_referrer("https://news.ycombinator.com/item?id=42") == (
            "news.ycombinator.com"
        )

    def test_drops_our_own_site(self, monkeypatch):
        monkeypatch.setattr(settings, "FRONTEND_URL", "https://evtrip.dev")
        assert normalize_referrer("https://evtrip.dev/trip/x") is None
        assert normalize_referrer("https://www.evtrip.dev/") is None

    def test_junk_is_dropped(self):
        assert normalize_referrer("not a url") is None
        assert normalize_referrer(None) is None


class TestVisitorHash:
    def test_same_visitor_same_day_is_one_pseudonym(self):
        day = date(2026, 8, 8)
        assert visitor_hash("1.2.3.4", "Firefox", day) == visitor_hash(
            "1.2.3.4", "Firefox", day
        )

    def test_different_visitors_differ(self):
        day = date(2026, 8, 8)
        assert visitor_hash("1.2.3.4", "Firefox", day) != visitor_hash(
            "5.6.7.8", "Firefox", day
        )
        # Two people behind one household NAT or a carrier CGNAT.
        assert visitor_hash("1.2.3.4", "Firefox", day) != visitor_hash(
            "1.2.3.4", "Safari", day
        )

    def test_the_salt_rotates_nightly(self):
        """"the code you get tomorrow has nothing linking it to today's".

        This is what stops the table becoming a behavioural record, so it is
        the single most load-bearing property in the feature.
        """
        assert visitor_hash("1.2.3.4", "Firefox", date(2026, 8, 8)) != visitor_hash(
            "1.2.3.4", "Firefox", date(2026, 8, 9)
        )

    def test_the_ip_is_not_recoverable_from_the_hash(self):
        assert "1.2.3.4" not in visitor_hash("1.2.3.4", "Firefox", date(2026, 8, 8))


class TestRecording:
    async def test_a_page_view_is_stored(self, client, db_session):
        resp = await client.post(
            "/api/events", json={"name": "page_view", "path": "/"}
        )
        assert resp.status_code == 204

        row = (await db_session.execute(select(AppEvent))).scalars().one()
        assert row.name == "page_view"
        assert row.path == "/"
        assert len(row.visitor) == 32

    async def test_a_trip_id_sent_anyway_is_not_stored(self, client, db_session):
        """The browser scrubs the path too, so this is the server refusing to
        rely on that."""
        resp = await client.post(
            "/api/events",
            json={"name": "page_view", "path": f"/trip/{TRIP_ID}/live?t=1"},
        )
        assert resp.status_code == 204

        row = (await db_session.execute(select(AppEvent))).scalars().one()
        assert row.path == "/trip/:id/live"

    async def test_unknown_event_names_are_refused(self, client, db_session):
        """Without the allowlist this is a public endpoint that writes a
        caller-supplied string to the database."""
        resp = await client.post(
            "/api/events", json={"name": "arbitrary_junk", "path": "/"}
        )
        assert resp.status_code == 422
        assert (
            await db_session.execute(select(func.count()).select_from(AppEvent))
        ).scalar_one() == 0

    async def test_oversized_fields_are_refused(self, client):
        resp = await client.post(
            "/api/events", json={"name": "page_view", "path": "/" + "x" * 5000}
        )
        assert resp.status_code == 422


class TestStatsAccess:
    async def test_route_does_not_exist_without_a_token_configured(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(settings, "STATS_TOKEN", "")
        assert (await client.get("/api/events/stats")).status_code == 404

    async def test_wrong_token_is_a_404_not_a_403(self, client, monkeypatch):
        """A 403 would confirm the route exists and that a secret guards it."""
        monkeypatch.setattr(settings, "STATS_TOKEN", "s3cret")
        resp = await client.get(
            "/api/events/stats", headers={"X-Stats-Token": "wrong"}
        )
        assert resp.status_code == 404

    async def test_the_numbers_come_back(self, client, monkeypatch, db_session):
        monkeypatch.setattr(settings, "STATS_TOKEN", "s3cret")
        await client.post("/api/events", json={"name": "page_view", "path": "/"})
        await client.post("/api/events", json={"name": "plan_submitted", "path": "/"})

        resp = await client.get(
            "/api/events/stats?days=7", headers={"X-Stats-Token": "s3cret"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["days"] == 7
        assert len(body["daily"]) == 7
        assert body["page_views"] == 1
        assert body["visitors"] == 1
        funnel = {f["label"]: f["count"] for f in body["funnel"]}
        assert funnel["plan_submitted"] == 1
        assert funnel["drive_started"] == 0
        assert body["top_paths"] == [{"label": "/", "count": 1}]


class TestRetention:
    async def test_events_past_the_window_are_deleted(self, client, db_session):
        """"These counts are deleted after 90 days." The runs purge was correct
        and uncalled for a while, which made the same promise false — so this
        checks the deletion, and main._purge_loop is what calls it."""
        import scripts.purge_old_events as mod
        from scripts.purge_old_events import purge

        await client.post("/api/events", json={"name": "page_view", "path": "/"})
        row = (await db_session.execute(select(AppEvent))).scalars().one()
        row.at = datetime.utcnow() - timedelta(days=120)
        await db_session.commit()

        # The purge opens its own session, so point it at this one.
        class _Maker:
            def __call__(self):
                return self

            async def __aenter__(self):
                return db_session

            async def __aexit__(self, *a):
                return False

        original = mod.AsyncSessionLocal
        mod.AsyncSessionLocal = _Maker()
        try:
            assert await purge(days=90, apply=False) == 1
            assert (
                await db_session.execute(select(func.count()).select_from(AppEvent))
            ).scalar_one() == 1  # dry run changed nothing

            assert await purge(days=90, apply=True) == 1
            assert (
                await db_session.execute(select(func.count()).select_from(AppEvent))
            ).scalar_one() == 0
        finally:
            mod.AsyncSessionLocal = original

    async def test_recent_events_are_kept(self, client, db_session):
        import scripts.purge_old_events as mod
        from scripts.purge_old_events import purge

        await client.post("/api/events", json={"name": "page_view", "path": "/"})

        class _Maker:
            def __call__(self):
                return self

            async def __aenter__(self):
                return db_session

            async def __aexit__(self, *a):
                return False

        original = mod.AsyncSessionLocal
        mod.AsyncSessionLocal = _Maker()
        try:
            assert await purge(days=90, apply=True) == 0
        finally:
            mod.AsyncSessionLocal = original
