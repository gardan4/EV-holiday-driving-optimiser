"""What is around a charger, as opposed to what it is called.

`food_hint` reads the site's name, and on a motorway corridor that answers
nothing: every fast charger is "Tesla Supercharger <town>" or "IONITY <town>".
This is the other half — what OpenStreetMap maps within a few hundred metres —
and these tests pin the three things that make it safe to show a driver:

* a MISSING answer and an EMPTY one are different, and stay different;
* it is cache-first, so the same question is not asked upstream twice;
* every failure degrades to the behaviour the app had before it existed.

Nothing here touches the network: `overpass.fetch` is the seam, and it is
stubbed. The parser and query builder are exercised directly against fixtures,
because those are the parts a live call could not check anyway — Overpass
returning a body we misread is exactly the bug an integration test would hide
behind a green tick.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base
from app.models import Charger, UpstreamCall
from app.services import amenities, overpass

pytestmark = pytest.mark.asyncio

LAT, LON = 49.7712, 10.4661  # Geiselwind, on the A3


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _charger(db, lat=LAT, lon=LON, **kw) -> Charger:
    row = Charger(
        ocm_id=int(uuid.uuid4().int % 1_000_000),
        name="Tesla Supercharger Geiselwind",
        lat=lat,
        lon=lon,
        max_power_kw=250.0,
        n_points=8,
        fetched_at=datetime.utcnow(),
        **kw,
    )
    db.add(row)
    await db.commit()
    return row


class TestReadingTheGround:
    async def test_it_stores_what_osm_maps_and_shows_it(self, db_session, monkeypatch):
        row = await _charger(db_session)

        async def fake_fetch(points):
            assert [p.key for p in points] == [str(row.id)]
            return {str(row.id): ["motorway services", "restaurant"]}, 1

        monkeypatch.setattr(overpass, "fetch", fake_fetch)
        got = await amenities.nearby(db_session, [(str(row.id), LAT, LON)])
        assert got == {str(row.id): ["motorway services", "restaurant"]}

        # And it is cached on the charger, so the next drive asks nobody.
        again = await db_session.get(Charger, row.id)
        assert again.amenities == ["motorway services", "restaurant"]
        assert again.amenities_at is not None

    async def test_the_name_says_nothing_where_the_ground_says_plenty(self):
        """The whole reason this exists. Geiselwind is an Autohof with a
        kitchen and its charger is called "Tesla Supercharger Geiselwind"."""
        assert amenities.food_hint("Tesla Supercharger Geiselwind") is None
        assert amenities.describe(["motorway services", "restaurant"]) == (
            "motorway services · restaurant"
        )

    async def test_a_cached_answer_is_not_asked_again(self, db_session, monkeypatch):
        row = await _charger(
            db_session, amenities=["café"], amenities_at=datetime.utcnow()
        )

        async def boom(points):  # pragma: no cover - must not run
            raise AssertionError("cache miss on a fresh row")

        monkeypatch.setattr(overpass, "fetch", boom)
        got = await amenities.nearby(db_session, [(str(row.id), LAT, LON)])
        assert got == {str(row.id): ["café"]}

    async def test_a_stale_answer_is_refreshed(self, db_session, monkeypatch):
        old = datetime.utcnow() - timedelta(days=settings.AMENITY_CACHE_DAYS + 1)
        row = await _charger(db_session, amenities=["café"], amenities_at=old)

        async def fake_fetch(points):
            return {str(row.id): ["supermarket"]}, 1

        monkeypatch.setattr(overpass, "fetch", fake_fetch)
        got = await amenities.nearby(db_session, [(str(row.id), LAT, LON)])
        assert got == {str(row.id): ["supermarket"]}


class TestNothingMappedIsNotNothingThere:
    async def test_an_empty_answer_is_stored_and_returned_as_empty(
        self, db_session, monkeypatch
    ):
        """`[]` is an answer: we looked, OSM maps nothing. It is cached like
        any other, or every lay-by on the continent is re-asked for ever."""
        row = await _charger(db_session)

        async def fake_fetch(points):
            return {str(row.id): []}, 1

        monkeypatch.setattr(overpass, "fetch", fake_fetch)
        got = await amenities.nearby(db_session, [(str(row.id), LAT, LON)])
        assert got == {str(row.id): []}
        assert (await db_session.get(Charger, row.id)).amenities == []

    async def test_a_failure_is_absent_rather_than_empty(
        self, db_session, monkeypatch
    ):
        """The distinction the whole design rests on. A provider that did not
        answer must not be recorded as "nothing here" — that is an absence
        claim built out of a timeout."""
        row = await _charger(db_session)

        async def fake_fetch(points):
            return None, 1

        monkeypatch.setattr(overpass, "fetch", fake_fetch)
        got = await amenities.nearby(db_session, [(str(row.id), LAT, LON)])
        assert got == {}
        assert (await db_session.get(Charger, row.id)).amenities is None

    async def test_a_failure_is_still_counted(self, db_session, monkeypatch):
        row = await _charger(db_session)

        async def fake_fetch(points):
            return None, 1

        monkeypatch.setattr(overpass, "fetch", fake_fetch)
        await amenities.nearby(db_session, [(str(row.id), LAT, LON)])
        await db_session.commit()
        calls = (
            await db_session.execute(
                select(UpstreamCall).where(UpstreamCall.provider == "overpass")
            )
        ).scalars().all()
        assert len(calls) == 1 and calls[0].ok is False


class TestItNeverGetsInTheWay:
    async def test_a_charger_we_have_no_row_for_is_skipped(
        self, db_session, monkeypatch
    ):
        """Synthetic routes have no cache row, and looking anyway would mean
        asking Overpass the same question on every request for ever."""

        async def boom(points):  # pragma: no cover - must not run
            raise AssertionError("looked up a charger with nowhere to cache it")

        monkeypatch.setattr(overpass, "fetch", boom)
        assert await amenities.nearby(db_session, [("chg-3", LAT, LON)]) == {}

    async def test_it_is_bounded_per_request(self, db_session, monkeypatch):
        rows = [await _charger(db_session) for _ in range(4)]
        monkeypatch.setattr(settings, "AMENITY_MAX_PER_REQUEST", 2)
        seen: list[int] = []

        async def fake_fetch(points):
            seen.append(len(points))
            return {p.key: [] for p in points}, 1

        monkeypatch.setattr(overpass, "fetch", fake_fetch)
        await amenities.nearby(db_session, [(str(r.id), LAT, LON) for r in rows])
        assert seen == [2]

    async def test_no_sites_asks_nobody(self, db_session):
        assert await amenities.nearby(db_session, []) == {}


class TestTheQueryAndTheAnswer:
    def test_the_query_asks_about_every_point(self):
        pts = [overpass.Point("a", 50.0, 6.0), overpass.Point("b", 51.0, 7.0)]
        q = overpass.build_query(pts)
        assert q.startswith("[out:json]")
        assert "50.00000,6.00000" in q and "51.00000,7.00000" in q
        # `center` is what gives a way or a relation a position to match on;
        # `out` defaults to `body`, so the tags come with it.
        assert q.endswith("out center;")

    def test_elements_land_on_the_points_they_are_near(self):
        pts = [overpass.Point("a", 50.0, 6.0), overpass.Point("b", 51.0, 7.0)]
        got = overpass.parse(
            pts,
            [
                {"lat": 50.0005, "lon": 6.0, "tags": {"amenity": "restaurant"}},
                {"center": {"lat": 51.0, "lon": 7.0}, "tags": {"shop": "supermarket"}},
                # Far from both — a result the bounding query returned that is
                # not within the radius of any point we asked about.
                {"lat": 40.0, "lon": 1.0, "tags": {"amenity": "cafe"}},
                # A tag we do not have a word for.
                {"lat": 50.0, "lon": 6.0, "tags": {"amenity": "bench"}},
            ],
        )
        assert got == {"a": ["restaurant"], "b": ["supermarket"]}

    def test_every_point_asked_about_gets_a_key(self):
        """Absent would be indistinguishable from "never asked"."""
        pts = [overpass.Point("a", 50.0, 6.0)]
        assert overpass.parse(pts, []) == {"a": []}

    def test_categories_are_ranked_not_arbitrary(self):
        """Two chargers with the same amenities must read identically, and the
        thing you can eat comes before the toilets."""
        pts = [overpass.Point("a", 50.0, 6.0)]
        got = overpass.parse(
            pts,
            [
                {"lat": 50.0, "lon": 6.0, "tags": {"amenity": "toilets"}},
                {"lat": 50.0, "lon": 6.0, "tags": {"amenity": "restaurant"}},
                {"lat": 50.0, "lon": 6.0, "tags": {"highway": "services"}},
            ],
        )
        assert got["a"] == ["motorway services", "restaurant", "toilets"]

    def test_only_two_categories_reach_the_chip(self):
        assert (
            amenities.describe(["restaurant", "supermarket", "toilets", "café"])
            == "restaurant · supermarket"
        )
        assert amenities.describe([]) is None
