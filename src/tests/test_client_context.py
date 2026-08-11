"""Coarse client context: the buckets, and what must never be stored.

The privacy argument for this table is that every column is low-cardinality by
construction. These tests are what keep that true — a regression that let the
raw user agent, an exact viewport width, or a referrer's query string through
would not fail anything else in the suite.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.events import normalize_referrer_path
from app.core.client_context import (
    classify_browser,
    classify_device,
    classify_os,
    classify_viewport,
    request_country,
)
from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.models import AppEvent

IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)
IPAD = (
    "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)
ANDROID_PHONE = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Mobile Safari/537.36"
)
ANDROID_TABLET = (
    "Mozilla/5.0 (Linux; Android 13; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
WIN_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
WIN_EDGE = WIN_CHROME + " Edg/124.0.0.0"
MAC_SAFARI = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)
MAC_FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0"
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


class TestDevice:
    @pytest.mark.parametrize(
        "ua,expected",
        [
            (IPHONE, "mobile"),
            (ANDROID_PHONE, "mobile"),
            (IPAD, "tablet"),
            # Android tablets omit "Mobile" — and also match the mobile pattern
            # via "Android", which is why tablet has to be tested first.
            (ANDROID_TABLET, "tablet"),
            (WIN_CHROME, "desktop"),
            (MAC_SAFARI, "desktop"),
            (GOOGLEBOT, "bot"),
            ("", None),
        ],
    )
    def test_classification(self, ua, expected):
        assert classify_device(ua) == expected


class TestBrowserAndOs:
    @pytest.mark.parametrize(
        "ua,expected",
        [
            # Edge says "Chrome" and Chrome says "Safari", so ordering is the
            # whole game here.
            (WIN_EDGE, "Edge"),
            (WIN_CHROME, "Chrome"),
            (MAC_SAFARI, "Safari"),
            (MAC_FIREFOX, "Firefox"),
            (ANDROID_PHONE, "Chrome"),
            ("something else entirely", "Other"),
        ],
    )
    def test_browser(self, ua, expected):
        assert classify_browser(ua) == expected

    @pytest.mark.parametrize(
        "ua,expected",
        [
            (IPHONE, "iOS"),
            # iPadOS claims to be a Mac; iPad must win over macOS.
            (IPAD, "iPadOS"),
            (ANDROID_PHONE, "Android"),
            (WIN_CHROME, "Windows"),
            (MAC_SAFARI, "macOS"),
        ],
    )
    def test_os(self, ua, expected):
        assert classify_os(ua) == expected


class TestViewport:
    @pytest.mark.parametrize(
        "width,expected",
        [
            (375, "up to 640"),
            (640, "up to 640"),
            (720, "641-768"),
            (1024, "769-1024"),
            (1280, "1025-1440"),
            (2560, "over 1440"),
            (None, None),
            (0, None),
            (-5, None),
            (99_999, None),
        ],
    )
    def test_bands(self, width, expected):
        assert classify_viewport(width) == expected

    def test_the_exact_width_is_not_recoverable(self):
        """An exact viewport is a real fingerprinting signal; the question —
        does the layout have room — needs only the band."""
        assert classify_viewport(1281) == classify_viewport(1439)

    def test_every_label_is_ascii(self):
        """These land in a `String` column, which is VARCHAR on MSSQL — a
        codepage, not Unicode. "≤640" came back as "<=640" from a real SQL
        Server, so the label you wrote and the label you can GROUP BY were
        different strings. SQLite stores anything, so nothing else here would
        have caught it.
        """
        labels = [classify_viewport(w) for w in (100, 700, 900, 1200, 3000)]
        assert all(lbl.isascii() for lbl in labels), labels


class TestCountry:
    def _req(self, value: str | None):
        from starlette.requests import Request

        headers = [(b"cf-ipcountry", value.encode())] if value is not None else []
        return Request(
            {
                "type": "http", "method": "POST", "path": "/api/events",
                "headers": headers, "client": ("1.2.3.4", 1234),
            }
        )

    def test_ignored_when_nothing_trustworthy_is_in_front(self, monkeypatch):
        """Same rule as the forwarding headers: with nothing overwriting it,
        the header is whatever the caller typed. A silently caller-controlled
        number is worse than no number, because you would believe it."""
        monkeypatch.setattr(settings, "TRUSTED_PROXY_HEADERS", False)
        assert request_country(self._req("NL")) is None

    def test_read_when_trusted(self, monkeypatch):
        monkeypatch.setattr(settings, "TRUSTED_PROXY_HEADERS", True)
        assert request_country(self._req("nl")) == "NL"

    @pytest.mark.parametrize("value", [None, "", "N", "NLD", "12", "XX", "T1"])
    def test_junk_and_unknowns_are_dropped(self, value, monkeypatch):
        monkeypatch.setattr(settings, "TRUSTED_PROXY_HEADERS", True)
        assert request_country(self._req(value)) is None


class TestReferrerPath:
    def test_the_path_is_kept(self):
        assert (
            normalize_referrer_path(
                "https://www.reddit.com/r/electricvehicles/comments/1a2b3c/title/"
            )
            == "/r/electricvehicles/comments/1a2b3c/title/"
        )

    def test_the_query_string_is_always_dropped(self):
        """The single most likely way somebody else's secret lands in this
        table: a referrer out of a webmail, a search or a password reset."""
        got = normalize_referrer_path(
            "https://mail.example.com/inbox?token=abc123&user=someone@example.com"
        )
        assert got == "/inbox"
        assert "token" not in got and "@" not in got

    def test_the_fragment_goes_too(self):
        assert normalize_referrer_path("https://example.com/a/b#section") == "/a/b"

    def test_a_bare_front_page_adds_nothing(self):
        assert normalize_referrer_path("https://reddit.com/") is None

    def test_internal_navigation_leaves_no_path(self, monkeypatch):
        """The host is discarded for self-referrals; the path must not survive
        it, or every internal navigation shows up here instead."""
        monkeypatch.setattr(settings, "FRONTEND_URL", "https://evtrip.dev")
        assert normalize_referrer_path("https://evtrip.dev/trip/abc-123") is None

    def test_it_is_capped(self):
        assert len(normalize_referrer_path("https://x.com/" + "a" * 500)) <= 200


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------


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


class TestRecording:
    async def test_context_is_stored_in_buckets(self, client, db_session):
        resp = await client.post(
            "/api/events",
            json={"name": "page_view", "path": "/", "viewport_w": 390},
            headers={"User-Agent": IPHONE},
        )
        assert resp.status_code == 204
        row = (await db_session.execute(select(AppEvent))).scalars().one()
        assert row.device == "mobile"
        assert row.browser == "Safari"
        assert row.os == "iOS"
        assert row.viewport == "up to 640"

    async def test_the_raw_user_agent_is_never_stored(self, client, db_session):
        """The property the whole module exists to keep."""
        await client.post(
            "/api/events",
            json={"name": "page_view", "path": "/", "viewport_w": 1280},
            headers={"User-Agent": WIN_CHROME},
        )
        row = (await db_session.execute(select(AppEvent))).scalars().one()
        blob = " ".join(str(v) for v in vars(row).values())
        assert "Mozilla" not in blob
        assert "537.36" not in blob
        assert "1280" not in blob  # the width was banded, not kept

    async def test_an_absurd_viewport_is_rejected_with_the_body(self, client):
        resp = await client.post(
            "/api/events", json={"name": "page_view", "path": "/", "viewport_w": 999_999}
        )
        assert resp.status_code == 422

    async def test_a_missing_viewport_is_fine(self, client, db_session):
        resp = await client.post("/api/events", json={"name": "page_view", "path": "/"})
        assert resp.status_code == 204
        row = (await db_session.execute(select(AppEvent))).scalars().one()
        assert row.viewport is None
