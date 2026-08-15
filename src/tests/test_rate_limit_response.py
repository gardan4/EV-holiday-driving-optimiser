"""What a rate-limited caller is actually told.

slowapi's stock handler answers `{"error": "Rate limit exceeded: 5 per 1
hour"}`. Nothing in this app reads `error` — the frontend funnels every failure
through `getErrorMessage(data.detail, …)` — so a refused request fell through
to the generic fallback and the drive screen showed "Request failed (429)" to
somebody at the wheel.

So the shape is the contract: `detail`, a string, plus `Retry-After`. Both are
asserted against the REAL app rather than a hand-built one, because the defect
was in how the app wires its handler up and a private copy of the wiring would
have passed while shipping the bug.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.rate_limit import _human_wait, limiter
from app.main import app


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
async def limited_client(db_session):
    """A client with the limiter ON — the suite disables it globally."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    was = limiter.enabled
    limiter.enabled = True
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        limiter.enabled = was
        app.dependency_overrides.clear()


class TestRefusalIsReadable:
    @pytest.mark.asyncio
    async def test_429_carries_detail_and_retry_after(self, limited_client):
        # Feedback is 5/hour, the tightest limit in the app, so this costs six
        # requests rather than sixty. The endpoint under it is irrelevant —
        # every limit in the app shares this handler.
        allowed = int(settings.RATE_LIMIT_FEEDBACK.split("/")[0])
        last = None
        for i in range(allowed + 1):
            last = await limited_client.post(
                "/api/feedback", json={"message": f"hello {i}"}
            )

        assert last is not None
        assert last.status_code == 429
        body = last.json()
        # The key the clients read. `error` is what slowapi sends and what
        # produced "Request failed (429)" on the drive screen.
        assert isinstance(body.get("detail"), str) and body["detail"]
        assert "try again" in body["detail"].lower()

        retry = last.headers.get("Retry-After")
        assert retry is not None and int(retry) >= 1


class TestHumanWait:
    """The number is a wait, not a window: it has to read as one."""

    @pytest.mark.parametrize(
        "seconds,expected",
        [(1, "1 second"), (12, "12 seconds"), (59, "59 seconds"),
         (60, "a minute"), (61, "2 minutes"), (3600, "60 minutes")],
    )
    def test_phrasing(self, seconds, expected):
        assert _human_wait(seconds) == expected
