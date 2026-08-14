"""The admin dashboard: who can reach it, and what it refuses to say.

The security shape here is the same one `/api/events/stats` and `/api/feedback`
already use, and the tests exist for the same reason: an admin surface on a
publicly reachable host is only safe while its default-deny actually denies.

The role is read when the app is BUILT, so these tests set `PROCESS_ROLE` and
call `main.create_app()` rather than importing the module-level `app` — which
was built for whatever role the environment had at import time.
`conftest.py` disables rate limiting for the whole suite.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base, get_db
from app.models import AppEvent

TOKEN = "test-stats-token"


class _Maker:
    """Stands in for `AsyncSessionLocal` so a generator gets the test session.

    Callable, and an async context manager that hands back the same session
    every time. `_tail` re-enters it on every poll, so `__aexit__` must not
    close it.
    """

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def dashboard_app(monkeypatch):
    """A freshly built app running in the dashboard role."""
    from app import main

    monkeypatch.setattr(settings, "PROCESS_ROLE", "dashboard")
    monkeypatch.setattr(settings, "STATS_TOKEN", TOKEN)
    return main.create_app()


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        now = datetime.utcnow()
        for i in range(6):
            session.add(
                AppEvent(
                    at=now - timedelta(hours=i),
                    name="page_view",
                    path="/",
                    visitor=uuid.uuid4().hex[:32],
                    device="mobile" if i % 2 else "desktop",
                )
            )
        await session.commit()
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session, dashboard_app):
    async def override_get_db():
        yield db_session

    dashboard_app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=dashboard_app), base_url="http://dash.test"
    ) as c:
        yield c
    dashboard_app.dependency_overrides.clear()


async def _signed_in(client: AsyncClient) -> AsyncClient:
    res = await client.post("/api/login", json={"password": TOKEN})
    assert res.status_code == 200
    return client


DATA_ROUTES = [
    "/api/meta",
    "/api/overview",
    "/api/segments",
    "/api/journeys",
    "/api/upstream",
    "/api/query?dimension=device",
    "/api/series",
]


class TestDefaultDeny:
    """An unset STATS_TOKEN means the dashboard does not exist.

    Deliberately not keyed on ENV: the one publicly reachable deployment is the
    environment named `dev`, so an `if ENV == "production"` guard is false
    exactly where it matters. That is how Swagger ended up published.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route", DATA_ROUTES + ["/api/session", "/api/stream"])
    async def test_everything_404s_without_a_token(
        self, client: AsyncClient, monkeypatch, route: str
    ):
        monkeypatch.setattr(settings, "STATS_TOKEN", "")
        assert (await client.get(route)).status_code == 404

    @pytest.mark.asyncio
    async def test_login_404s_without_a_token(self, client: AsyncClient, monkeypatch):
        monkeypatch.setattr(settings, "STATS_TOKEN", "")
        res = await client.post("/api/login", json={"password": "anything"})
        assert res.status_code == 404


class TestAuth:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("route", DATA_ROUTES)
    async def test_data_routes_require_a_session(self, client: AsyncClient, route: str):
        assert (await client.get(route)).status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_password_is_rejected(self, client: AsyncClient):
        res = await client.post("/api/login", json={"password": "not-the-token"})
        assert res.status_code == 401
        assert "evtrip_dash" not in res.cookies

    @pytest.mark.asyncio
    async def test_right_password_sets_an_httponly_cookie(self, client: AsyncClient):
        res = await client.post("/api/login", json={"password": TOKEN})
        assert res.status_code == 200
        # httpOnly is the whole point: the token is exchanged for something no
        # script on the page can read back out.
        assert "httponly" in res.headers["set-cookie"].lower()
        assert "samesite=lax" in res.headers["set-cookie"].lower()

    @pytest.mark.asyncio
    async def test_session_survives_across_requests(self, client: AsyncClient):
        await _signed_in(client)
        assert (await client.get("/api/session")).json() == {"authenticated": True}
        assert (await client.get("/api/overview")).status_code == 200

    @pytest.mark.asyncio
    async def test_logout_ends_it(self, client: AsyncClient):
        await _signed_in(client)
        await client.post("/api/logout")
        assert (await client.get("/api/overview")).status_code == 401

    @pytest.mark.asyncio
    async def test_a_forged_cookie_is_refused(self, client: AsyncClient):
        client.cookies.set("evtrip_dash", "not-a-fernet-token")
        assert (await client.get("/api/overview")).status_code == 401

    @pytest.mark.asyncio
    async def test_an_expired_session_is_refused(self, client: AsyncClient):
        """Fernet carries its own authenticated timestamp, so expiry is the
        `ttl` argument on decrypt and needs no session table.

        Forged with a real signature and an old timestamp rather than by
        shrinking the TTL: `ttl=0` does not expire a token issued in the same
        second, so that version of this test passed against any implementation.
        """
        import json
        import time

        from app.core.security import get_fernet

        stale = get_fernet().encrypt_at_time(
            json.dumps({"iat": "then"}).encode(),
            current_time=int(time.time()) - (settings.DASHBOARD_SESSION_HOURS * 3600) - 60,
        )
        client.cookies.set("evtrip_dash", stale.decode())
        assert (await client.get("/api/overview")).status_code == 401


class TestWhichDatabase:
    """The console can now be pointed at the deployed database
    (`./scripts/dashboard.sh --prod`), so it has to say which one answered.

    The label is derived from the connection the process actually made rather
    than from the flag that started it — a label that can disagree with reality
    is worse than none — and it must never carry the credential that made it.
    """

    LOCAL = (
        "mssql+aioodbc://sa:LocalDev_Passw0rd!@localhost:14330/evtripdb-dev"
        "?driver=ODBC+Driver+18+for+SQL+Server"
    )
    REMOTE = (
        "mssql+aioodbc://dashboard_ro:hunter2-the-real-one@evtrip-sql-dev."
        "database.windows.net:1433/evtripdb-dev?driver=ODBC+Driver+18+for+SQL+Server"
    )

    def test_the_password_never_comes_back_out(self):
        """This is the whole risk of the feature: a running process that will
        hand its connection string to whoever asks."""
        from app.core.database import describe_url

        for url, secret in ((self.LOCAL, "LocalDev_Passw0rd!"), (self.REMOTE, "hunter2")):
            described = describe_url(url)
            blob = repr(described)
            assert secret not in blob
            assert "dashboard_ro" not in blob
            assert "sa:" not in blob

    def test_local_and_deployed_are_told_apart_by_host(self):
        """Not by the database NAME: the deployed one is also `evtripdb-dev`,
        because the single deployed environment is the one named dev. And not
        by `ENV` either, for the reason `EXPOSE_DOCS` exists."""
        from app.core.database import describe_url

        local = describe_url(self.LOCAL)
        remote = describe_url(self.REMOTE)
        assert local["local"] is True
        assert remote["local"] is False
        assert local["name"] == remote["name"] == "evtripdb-dev"
        assert remote["host"] == "evtrip-sql-dev.database.windows.net"

    def test_an_unreadable_url_is_not_called_local(self):
        """The safe direction: a console that warns about a database it turns
        out to own, never one that quietly calls a live database local."""
        from app.core.database import describe_url

        assert describe_url("::: not a url :::")["local"] is False

    @pytest.mark.asyncio
    async def test_meta_says_which_database_answered(self, client: AsyncClient):
        await _signed_in(client)
        meta = (await client.get("/api/meta")).json()
        assert set(meta["database"]) == {"host", "name", "local"}


class TestQueries:
    @pytest.mark.asyncio
    async def test_overview_carries_its_window_and_filters(self, client: AsyncClient):
        await _signed_in(client)
        body = (await client.get("/api/overview?days=30&device=mobile")).json()
        assert body["window"]["days"] == 30
        assert body["window"]["filters"] == {"device": "mobile"}

    @pytest.mark.asyncio
    async def test_a_facet_narrows_the_result(self, client: AsyncClient):
        await _signed_in(client)
        everything = (await client.get("/api/overview?days=7")).json()
        mobile = (await client.get("/api/overview?days=7&device=mobile")).json()
        assert mobile["totals"]["events"]["value"] < everything["totals"]["events"]["value"]

    @pytest.mark.asyncio
    async def test_unknown_dimension_is_a_400_not_a_500(self, client: AsyncClient):
        await _signed_in(client)
        res = await client.get("/api/query?dimension=drop_table")
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_pseudonyms_cannot_be_queried(self, client: AsyncClient):
        """The endpoint is the last line of the same defence as the service
        allowlist: no route from a browser to a per-person list."""
        await _signed_in(client)
        for column in ("visitor", "client_id"):
            res = await client.get(f"/api/query?dimension={column}")
            assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_days_is_clamped_by_validation(self, client: AsyncClient):
        await _signed_in(client)
        assert (await client.get("/api/overview?days=9999")).status_code == 422
        assert (await client.get("/api/overview?days=0")).status_code == 422

    @pytest.mark.asyncio
    async def test_meta_advertises_the_allowlist(self, client: AsyncClient):
        await _signed_in(client)
        meta = (await client.get("/api/meta")).json()
        assert "device" in meta["dimensions"]
        assert "visitor" not in meta["dimensions"]
        assert "name" not in meta["filterable"]

    @pytest.mark.asyncio
    async def test_journeys_carries_the_username_numbers(self, client: AsyncClient):
        """The console and `scripts.usage_report` read the same functions, so
        the shape is pinned here rather than the arithmetic (which
        `test_profile_reporting.py` owns). An empty database must still answer
        with every key the panel reads — a missing one renders as NaN."""
        await _signed_in(client)
        body = (await client.get("/api/journeys?days=7")).json()
        assert set(body["profiles"]) == {
            "live",
            "claimed",
            "released",
            "with_trips",
            "empty",
            "published",
            "trips",
            "publish_rate",
            "active_rate",
            "list_views",
            "per_name",
        }
        # No trips at all: a share of nothing is withheld, not drawn as 0%.
        assert body["profiles"]["publish_rate"] is None
        assert body["erased"]["trips_deleted"] == 0

    @pytest.mark.asyncio
    async def test_modelled_countries_come_from_the_simulator(
        self, client: AsyncClient
    ):
        """The map shades countries by whether the planner has a real motorway
        cap for them. A hardcoded copy of that list in the frontend would go
        quietly wrong the day a country is added — showing the map's own guess
        as if it were the model's."""
        from app.services.simulator import _default_country_caps

        await _signed_in(client)
        meta = (await client.get("/api/meta")).json()
        assert meta["modelled_countries"] == sorted(_default_country_caps())
        assert meta["default_cap_kph"] > 0


class TestStreamPayload:
    """The ticker shows a route pattern, a bucketed device and a country — every
    one a column that already exists. Neither pseudonym is selected, so the live
    feed cannot become a feed of individual people even for the one person
    allowed to look at it.

    Asserted on the parsed frames rather than by substring. The substring
    version passed for the wrong reason: it searched for "visitor", which also
    matches the aggregate `"visitors": 12` in the counters frame, and only
    stayed green because it happened to stop reading before that frame arrived.
    """

    @pytest_asyncio.fixture(autouse=True)
    async def _stream_reads_the_seeded_db(self, db_session, monkeypatch):
        """Point `_tail` at the test database.

        `_tail` opens its own session on purpose, because it outlives the
        request handler, so it does not go through the `get_db` override the
        rest of these tests rely on. Left alone it polls whatever
        `DATABASE_URL` points at. On a developer's machine that is a real dev
        database with real events in it, so the assertions below pass on rows
        the fixture never seeded, and then fail in CI where that database has
        nothing recent. Exactly the wrong-reason pass this class was rewritten
        to stop happening.
        """
        import app.api.admin as admin

        monkeypatch.setattr(admin, "AsyncSessionLocal", _Maker(db_session))

    @staticmethod
    async def _frames(n: int = 6) -> list[str]:
        from app.api.admin import _tail

        agen = _tail(datetime.utcnow() - timedelta(days=1))
        out: list[str] = []
        try:
            for _ in range(n):
                out.append(await agen.__anext__())
        finally:
            await agen.aclose()
        return out

    @pytest.mark.asyncio
    async def test_event_frames_carry_only_allowlisted_columns(
        self, client: AsyncClient
    ):
        import json

        allowed = {"at", "name", "path", "country", "device", "campaign", "reason"}
        seen = 0
        for frame in await self._frames():
            for line in frame.splitlines():
                if not line.startswith("data: "):
                    continue
                payload = json.loads(line[len("data: ") :])
                if "name" in payload and "at" in payload:  # an event frame
                    assert set(payload) <= allowed, f"unexpected field: {set(payload) - allowed}"
                    seen += 1
        # The fixture seeds six page views inside the window, so this is not
        # vacuously true — a stream that emitted nothing would fail here.
        assert seen > 0

    @pytest.mark.asyncio
    async def test_no_pseudonym_field_anywhere_in_the_stream(
        self, client: AsyncClient
    ):
        blob = "".join(await self._frames())
        # The keys, not the words: `"visitors"` is a count and is fine.
        assert '"visitor"' not in blob
        assert '"client_id"' not in blob


class TestResponseHeaders:
    @pytest.mark.asyncio
    async def test_the_dashboard_is_never_indexable(self, client: AsyncClient):
        """Unlike the Next app there is no middleware in front to say so."""
        res = await client.get("/health")
        assert "noindex" in res.headers["x-robots-tag"]

    @pytest.mark.asyncio
    async def test_csp_allows_the_spa_but_no_external_origin(self, client: AsyncClient):
        """The API's `default-src 'none'` would block the dashboard's own bundle;
        this policy has to permit self and nothing else."""
        csp = (await client.get("/health")).headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "connect-src 'self'" in csp
        assert "http" not in csp  # no external origin of any kind

    @pytest.mark.asyncio
    async def test_health_is_not_shadowed_by_the_spa_mount(self, client: AsyncClient):
        """A StaticFiles mount at "/" matches every path beneath it. Registered
        before the health route it swallows the probe Azure hits, and the App
        Service reports itself unhealthy and cycles forever."""
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.json() == {"status": "healthy"}


class TestRoleIsolation:
    def test_the_dashboard_role_mounts_no_public_api(self, dashboard_app):
        """Analytics queries must not land on the tier that serves trip plans —
        and the public write endpoints must not be reachable from the console's
        origin."""
        from app.main import _serves_api, _serves_dashboard

        assert _serves_dashboard() is True
        assert _serves_api() is False

    @pytest.mark.asyncio
    async def test_public_write_endpoints_are_absent(self, client: AsyncClient):
        # Not asserting an exact code: unmatched paths fall through to the
        # static mount, which answers 404 for a GET it has no file for and 405
        # for a POST it does not accept. Both mean the same thing — there is no
        # such endpoint on this origin.
        assert (await client.post("/api/events", json={"name": "page_view"})).status_code >= 400
        assert (await client.get("/api/vehicles")).status_code >= 400

    @pytest.mark.asyncio
    async def test_the_api_role_does_not_serve_the_dashboard(self, monkeypatch):
        """The other direction, and the one that matters more: the public API
        must not carry an admin console just because the image contains one."""
        from app import main

        monkeypatch.setattr(settings, "PROCESS_ROLE", "web")
        monkeypatch.setattr(settings, "STATS_TOKEN", TOKEN)
        api = main.create_app()
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url="http://api.test"
        ) as c:
            assert (await c.post("/api/login", json={"password": TOKEN})).status_code == 404
            assert (await c.get("/api/overview")).status_code == 404

    def test_dashboard_is_not_part_of_all(self):
        """`all` is the local-dev default. If it included the dashboard, a
        misconfigured public instance would serve an admin surface."""
        from app.core.config import Settings

        assert Settings.model_fields["PROCESS_ROLE"].default == "all"

    def test_an_unknown_role_falls_back_to_all_not_dashboard(self):
        """A typo in an App Setting must not be able to produce a console."""
        from app.core.config import Settings

        assert Settings(PROCESS_ROLE="dashbaord").PROCESS_ROLE == "all"
        assert Settings(PROCESS_ROLE="dashboard").PROCESS_ROLE == "dashboard"
