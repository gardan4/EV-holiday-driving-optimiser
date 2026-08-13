"""Usernames: what they publish, what they refuse, and what stops.

The feature is an "account without identity" — a public name bound to the hash
of a secret the browser made up. That is only defensible if a short list of
properties holds, and each one has a test here:

* the secret is validated like the client id and hashed before storage, into a
  space that cannot be confused with the client id's;
* claiming is the CONSENT MOMENT — a trip is stamped only when the secret
  already holds a name, so trips planned before the claim stay unpublished;
* the public list carries no coordinates and no street addresses, because
  anybody can read it by guessing a name;
* releasing really unpublishes, and does not delete the journeys;
* the stamp never reaches `Trip.request`, which is where the exact coordinates
  live.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.users import locality, normalize_username
from app.core.database import Base, get_db
from app.core.visitor import client_hash, owner_hash, request_owner_hash
from app.main import app
from app.models import Profile, Trip, Vehicle
from app.services.geo import RouteGeometry
from app.services.routing import RouteData
from app.services.simulator import ChargerNode
from tests.fixtures import nl_to_austria_route
from tests.test_api import BORN_SEED, plan_body, vehicle_id

SECRET = "3f2a1b4c-5d6e-4f70-8192-a3b4c5d6e7f8"
SECRET2 = "9e8d7c6b-5a49-4382-b716-0f1e2d3c4b5a"


def _request_with(header_value: str | None):
    from starlette.requests import Request

    headers = []
    if header_value is not None:
        headers.append((b"x-owner-secret", header_value.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/users",
            "headers": headers,
            "client": ("1.2.3.4", 1234),
        }
    )


class TestSecret:
    def test_a_v4_uuid_is_accepted(self):
        assert request_owner_hash(_request_with(SECRET)) == owner_hash(SECRET)

    def test_case_is_normalised(self):
        assert request_owner_hash(_request_with(SECRET.upper())) == owner_hash(SECRET)

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "   ",
            "not-a-uuid",
            "3f2a1b4c5d6e4f708192a3b4c5d6e7f8",       # no dashes
            "3f2a1b4c-5d6e-1f70-8192-a3b4c5d6e7f8",   # v1, not v4
            "'; DROP TABLE profiles; --",
            "x" * 5000,
        ],
    )
    def test_anything_else_is_discarded(self, value):
        assert request_owner_hash(_request_with(value)) is None

    def test_the_stored_value_is_not_the_browsers_value(self):
        """The secret is a capability. A leaked database must not be a set of
        working keys."""
        assert owner_hash(SECRET) != SECRET
        assert SECRET not in owner_hash(SECRET)
        assert len(owner_hash(SECRET)) == 32

    def test_the_owner_space_is_separate_from_the_client_space(self):
        """Domain separation. One counts returning visitors, the other
        authorises writes; a value from one must never work as the other."""
        assert owner_hash(SECRET) != client_hash(SECRET)


class TestUsernameValidation:
    @pytest.mark.parametrize("name", ["marc", "marc-2026", "a1b", "x" * 24])
    def test_accepted(self, name):
        assert normalize_username(name) == name

    def test_case_is_folded_rather_than_rejected(self):
        """"Marc" should claim "marc", not fail for a reason no form explains."""
        assert normalize_username("Marc") == "marc"

    @pytest.mark.parametrize(
        "name",
        [
            None,
            "",
            "ab",                 # too short
            "x" * 25,             # too long
            "-marc",              # leading dash
            "marc-",              # trailing dash
            "marc_2026",          # underscore
            "marc 2026",          # space
            "märc",               # non-ASCII: the column is VARCHAR
            "me",                 # reserved — would shadow /api/users/me
            "admin",
            "../../etc/passwd",
            "'; DROP TABLE profiles; --",
            "<script>alert(1)</script>",
        ],
    )
    def test_refused(self, name):
        assert normalize_username(name) is None


class TestLocality:
    """The one function standing between a public page and somebody's doorstep.

    The shapes below are what ORS/Pelias really returns — checked against the
    live geocoder, not invented — because the whole rule depends on the city
    sitting third from the end.
    """

    @pytest.mark.parametrize(
        "label,expected",
        [
            # An address: the street is FIRST, which is why the naive
            # `split(",")[0]` this replaced published house numbers.
            ("Kerkstraat 12, Baarn, UT, Netherlands", "Baarn"),
            ("1600 Pennsylvania Avenue NW, Washington, DC, USA", "Washington"),
            # A POI resolves to the city it is in.
            ("Flughafen Innsbruck, Innsbruck, TR, Austria", "Innsbruck"),
            # A city, with and without a region code.
            ("Innsbruck, TR, Austria", "Innsbruck"),
            ("Utrecht, Netherlands", "Utrecht"),
            ("Innsbruck", "Innsbruck"),
            # A street that shares its name with a city must still resolve to
            # the city it is actually in.
            ("Utrecht, Leeuwarden, FR, Netherlands", "Leeuwarden"),
        ],
    )
    def test_the_town_is_what_comes_out(self, label, expected):
        assert locality(label) == expected

    def test_a_house_number_is_never_published(self):
        """The backstop, for a label that omits the city. Skipping forward can
        only ever make the answer coarser, which is the safe direction."""
        assert not any(ch.isdigit() for ch in locality("Kerkstraat 12, Netherlands"))

    def test_missing_labels_do_not_explode(self):
        assert locality(None) == "Unknown"
        assert locality("") == "Unknown"


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
        session.add(Vehicle(**BORN_SEED))
        await session.commit()
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session, monkeypatch):
    async def override_get_db():
        yield db_session

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


async def _claim(client, name: str, secret: str = SECRET):
    return await client.post(
        "/api/users", json={"username": name}, headers={"X-Owner-Secret": secret}
    )


async def _plan(client, secret: str | None = None):
    headers = {"X-Owner-Secret": secret} if secret else {}
    return await client.post(
        "/api/trips", json=plan_body(await vehicle_id(client)), headers=headers
    )


class TestClaiming:
    async def test_a_name_can_be_claimed(self, client, db_session):
        resp = await _claim(client, "marc")
        assert resp.status_code == 201, resp.text
        assert resp.json()["username"] == "marc"
        row = (await db_session.execute(select(Profile))).scalar_one()
        assert row.username == "marc"
        assert row.owner_hash == owner_hash(SECRET)

    async def test_reclaiming_your_own_name_is_idempotent(self, client):
        """What a retry looks like. It must not 409 the person who already
        owns the name."""
        assert (await _claim(client, "marc")).status_code == 201
        again = await _claim(client, "marc")
        assert again.status_code == 200
        assert again.json()["username"] == "marc"

    async def test_a_taken_name_is_refused(self, client):
        await _claim(client, "marc", SECRET)
        resp = await _claim(client, "marc", SECRET2)
        assert resp.status_code == 409

    async def test_one_name_per_browser(self, client):
        await _claim(client, "marc")
        resp = await _claim(client, "marc2")
        assert resp.status_code == 409
        assert "release" in resp.json()["detail"].lower()

    async def test_no_secret_is_rejected(self, client):
        resp = await client.post("/api/users", json={"username": "marc"})
        assert resp.status_code == 401

    async def test_a_junk_secret_is_rejected(self, client):
        resp = await _claim(client, "marc", "haha")
        assert resp.status_code == 401

    async def test_a_bad_name_is_rejected(self, client, db_session):
        resp = await _claim(client, "me")
        assert resp.status_code == 422
        assert (
            await db_session.execute(select(func.count()).select_from(Profile))
        ).scalar_one() == 0


class TestStamping:
    async def test_a_claimed_secret_publishes_the_trip(self, client, db_session):
        await _claim(client, "marc")
        resp = await _plan(client, SECRET)
        assert resp.status_code == 200, resp.text
        trip = await db_session.get(Trip, uuid.UUID(resp.json()["id"]))
        assert trip.owner_hash == owner_hash(SECRET)

    async def test_an_unclaimed_secret_leaves_no_trace(self, client, db_session):
        """Claiming is the consent moment. A secret that holds no name must not
        stamp anything, or picking a name later would publish history nobody
        agreed to publish."""
        resp = await _plan(client, SECRET)
        trip = await db_session.get(Trip, uuid.UUID(resp.json()["id"]))
        assert trip.owner_hash is None

    async def test_trips_planned_before_the_claim_stay_unpublished(self, client):
        before = await _plan(client, SECRET)
        await _claim(client, "marc")
        after = await _plan(client, SECRET)

        listed = (await client.get("/api/users/marc/trips")).json()["trips"]
        ids = {t["id"] for t in listed}
        assert after.json()["id"] in ids
        assert before.json()["id"] not in ids

    async def test_no_header_means_no_owner(self, client, db_session):
        resp = await _plan(client)
        trip = await db_session.get(Trip, uuid.UUID(resp.json()["id"]))
        assert trip.owner_hash is None

    async def test_the_secret_never_reaches_the_trip_json(self, client, db_session):
        """`Trip.request` holds exact coordinates. An identifier in there is the
        one join this design works hardest to prevent."""
        await _claim(client, "marc")
        resp = await _plan(client, SECRET)
        trip = await db_session.get(Trip, uuid.UUID(resp.json()["id"]))
        blob = f"{trip.request}{trip.result}"
        assert SECRET not in blob
        assert owner_hash(SECRET) not in blob


class TestPublicList:
    async def test_reading_needs_nothing(self, client):
        """The whole point: hand somebody a name and it works, with no secret
        and no state of their own."""
        await _claim(client, "marc")
        await _plan(client, SECRET)
        resp = await client.get("/api/users/marc/trips")
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "marc"
        assert len(body["trips"]) == 1
        assert body["trips"][0]["distance_km"] > 0
        assert body["trips"][0]["vehicle_label"]

    async def test_it_carries_no_coordinates(self, client):
        """Anyone can read this by guessing a name, so it must not be able to
        emit a position, a polyline, or a stop list."""

        def keys(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield k
                    yield from keys(v)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)

        await _claim(client, "marc")
        await _plan(client, SECRET)
        body = (await client.get("/api/users/marc/trips")).json()
        found = set(keys(body))
        assert not found & {"lat", "lon", "polyline", "timeline", "stops", "speeds"}

    async def test_labels_are_reduced_to_a_locality(self, client):
        """A geocoder hands back a front door when somebody picks their own
        address, and this list is readable by anyone who guesses the name."""
        await _claim(client, "marc")
        await client.post(
            "/api/trips",
            json=plan_body(
                await vehicle_id(client),
                origin={
                    "label": "Kerkstraat 12, Baarn, UT, Netherlands",
                    "lat": 52.09,
                    "lon": 5.12,
                },
            ),
            headers={"X-Owner-Secret": SECRET},
        )
        trip = (await client.get("/api/users/marc/trips")).json()["trips"][0]
        assert trip["origin_label"] == "Baarn"
        assert "Kerkstraat" not in trip["origin_label"]
        assert "12" not in trip["origin_label"]

    async def test_an_unknown_name_is_a_404(self, client):
        assert (await client.get("/api/users/nobody/trips")).status_code == 404

    async def test_a_malformed_name_is_also_a_404(self, client):
        """Not a 422: whether a name is unclaimed or unclaimable is not a
        distinction worth publishing."""
        assert (await client.get("/api/users/NOT_A_NAME/trips")).status_code == 404

    async def test_a_deleted_trip_leaves_the_list(self, client):
        await _claim(client, "marc")
        tid = (await _plan(client, SECRET)).json()["id"]
        assert (await client.delete(f"/api/trips/{tid}")).status_code == 204
        assert (await client.get("/api/users/marc/trips")).json()["trips"] == []


class TestWhoAmI:
    async def test_it_returns_your_own_name(self, client):
        await _claim(client, "marc")
        resp = await client.get("/api/users/me", headers={"X-Owner-Secret": SECRET})
        assert resp.status_code == 200
        assert resp.json()["username"] == "marc"

    async def test_an_unclaimed_browser_gets_404(self, client):
        resp = await client.get("/api/users/me", headers={"X-Owner-Secret": SECRET})
        assert resp.status_code == 404

    async def test_no_secret_is_rejected(self, client):
        assert (await client.get("/api/users/me")).status_code == 401


class TestRelease:
    async def test_the_wrong_browser_cannot_release(self, client):
        await _claim(client, "marc", SECRET)
        resp = await client.request(
            "DELETE", "/api/users/marc", headers={"X-Owner-Secret": SECRET2}
        )
        assert resp.status_code == 403

    async def test_no_secret_cannot_release(self, client):
        await _claim(client, "marc")
        assert (await client.delete("/api/users/marc")).status_code == 403

    async def test_releasing_unpublishes_but_keeps_the_journeys(
        self, client, db_session
    ):
        """Releasing a name is not asking us to delete your trips. Conflating
        the two would make the button too frightening to press."""
        await _claim(client, "marc")
        tid = (await _plan(client, SECRET)).json()["id"]

        resp = await client.request(
            "DELETE", "/api/users/marc", headers={"X-Owner-Secret": SECRET}
        )
        assert resp.status_code == 204

        assert (await client.get("/api/users/marc/trips")).status_code == 404
        # The trip survives, and its share link still opens it.
        assert (await client.get(f"/api/trips/{tid}")).status_code == 200
        trip = await db_session.get(Trip, uuid.UUID(tid))
        await db_session.refresh(trip)
        assert trip.owner_hash is None

    async def test_reclaiming_after_release_starts_empty(self, client):
        """The detach is what makes a release final — a re-claim must not
        resurrect a list somebody took down."""
        await _claim(client, "marc")
        await _plan(client, SECRET)
        await client.request(
            "DELETE", "/api/users/marc", headers={"X-Owner-Secret": SECRET}
        )
        assert (await _claim(client, "marc")).status_code == 201
        assert (await client.get("/api/users/marc/trips")).json()["trips"] == []

    async def test_a_released_name_is_free_for_somebody_else(self, client):
        await _claim(client, "marc", SECRET)
        await client.request(
            "DELETE", "/api/users/marc", headers={"X-Owner-Secret": SECRET}
        )
        assert (await _claim(client, "marc", SECRET2)).status_code == 201


class TestRetention:
    async def test_a_dormant_username_is_collected_and_an_active_one_is_not(
        self, client, db_session
    ):
        """The rule that matters is "nothing is published under it", not age:
        deleting a name that still holds trips would make somebody's live page
        vanish."""
        from datetime import datetime, timedelta

        import scripts.purge_old_profiles as mod
        from scripts.purge_old_profiles import DEFAULT_RETENTION_DAYS, purge

        await _claim(client, "dormant", SECRET)
        await _claim(client, "active", SECRET2)
        await _plan(client, SECRET2)

        old = datetime.utcnow() - timedelta(days=DEFAULT_RETENTION_DAYS + 10)
        for row in (await db_session.execute(select(Profile))).scalars().all():
            row.created_at = old
        await db_session.commit()

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
            assert await purge(days=DEFAULT_RETENTION_DAYS, apply=False) == 1
            assert (
                await db_session.execute(select(func.count()).select_from(Profile))
            ).scalar_one() == 2, "dry run deleted something"

            assert await purge(days=DEFAULT_RETENTION_DAYS, apply=True) == 1
            names = set(
                (await db_session.execute(select(Profile.username))).scalars().all()
            )
            assert names == {"active"}
        finally:
            mod.AsyncSessionLocal = original
