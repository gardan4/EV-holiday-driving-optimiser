"""API component tests: FastAPI routes on an in-memory SQLite engine with the
ORS/OCM services stubbed at the service layer (no network, no MSSQL)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.main import app
from app.models import Vehicle
from app.services.geo import RouteGeometry
from app.services.networks import network_of
from app.services.routing import RouteData
from app.services.simulator import ChargerNode
from tests.fixtures import nl_to_austria_route

BORN_SEED = dict(
    slug="cupra-born-58",
    make="Cupra",
    model="Born",
    variant="58 kWh",
    nameplate_slug="cupra-born",
    usable_kwh=58.0,
    consumption={"model": "quadratic", "a_wh_km": 55.0, "b_wh_km_per_kph2": 0.0105},
    charge_curve=[[0, 50], [10, 120], [30, 120], [45, 85], [55, 68], [75, 42], [100, 10]],
    max_dc_kw=120.0,
    mass_kg=1900.0,
    top_speed_kph=160.0,   # the real Born's limiter — the sweep must respect it
    sort_order=10,
)


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
    total = sum(s.dist_m for s in segments)
    # Trivial 2-point geometry — chargers are stubbed so projection never runs.
    geometry = RouteGeometry([(52.09, 5.12), (47.26, 11.39)])
    route = RouteData(
        geometry=geometry, segments=segments, total_dist_m=total, total_dur_s=total / 30.0
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


def plan_body(vehicle_id: str, **overrides) -> dict:
    body = {
        "origin": {"label": "Utrecht", "lat": 52.09, "lon": 5.12},
        "dest": {"label": "Innsbruck", "lat": 47.26, "lon": 11.39},
        "vehicle_id": vehicle_id,
        "departure_iso": "2026-08-14T22:00:00",
        "depart_soc": 100.0,
        "target_soc": 10.0,
        "speed_min": 100.0,
        "speed_max": 140.0,
        "speed_step": 10.0,
    }
    body.update(overrides)
    return body


async def vehicle_id(client) -> str:
    resp = await client.get("/api/vehicles")
    assert resp.status_code == 200
    return resp.json()[0]["id"]


class TestVehicles:
    async def test_list(self, client):
        resp = await client.get("/api/vehicles")
        assert resp.status_code == 200
        (v,) = resp.json()
        assert v["slug"] == "cupra-born-58"
        assert v["charge_curve"][0] == [0, 50]

    async def test_every_field_survives_serialisation(self, client):
        """`VehicleOut` is built field by field, not from the ORM object.

        So a column added to the model and to the schema still arrives as its
        default until someone also adds it to both construction sites — in
        `api/vehicles.py` and `api/trips.py`. That failure is silent: the key is
        present, the value is null, and the page that needed it just renders
        nothing. `nameplate_slug` did exactly this, hence the test.
        """
        resp = await client.get("/api/vehicles")
        (v,) = resp.json()
        unset = [
            field
            for field in ("slug", "make", "model", "variant", "nameplate_slug")
            if v.get(field) is None
        ]
        assert not unset, (
            f"{unset} came back null for a car that has them. Check both "
            "VehicleOut(...) call sites, not just the schema."
        )
        assert v["nameplate_slug"] == "cupra-born"


class TestPlanTrip:
    async def test_plan_and_permalink_roundtrip(self, client):
        vid = await vehicle_id(client)
        resp = await client.post("/api/trips", json=plan_body(vid))
        assert resp.status_code == 200, resp.text
        trip = resp.json()

        result = trip["result"]
        assert result["vehicle"]["slug"] == "cupra-born-58"
        assert len(result["speeds"]) == 5  # 100..140 step 10
        assert result["optimum_speed"] is not None
        feasible = [s for s in result["speeds"] if s["feasible"]]
        assert feasible, "at least some speeds must be feasible"
        for s in feasible:
            assert s["total_min"] > 0
            assert s["timeline"][0]["t_min"] == 0.0
            assert s["timeline"][-1]["soc"] >= 10.0 - 1e-6
        assert result["polyline"]

        # Permalink: fetching by id reproduces the exact stored result.
        resp2 = await client.get(f"/api/trips/{trip['id']}")
        assert resp2.status_code == 200
        assert resp2.json()["result"] == result

    async def test_unknown_vehicle_404(self, client):
        resp = await client.post(
            "/api/trips",
            json=plan_body("00000000-0000-0000-0000-000000000000"),
        )
        assert resp.status_code == 404

    async def test_bad_speed_range_422(self, client):
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips", json=plan_body(vid, speed_min=140.0, speed_max=100.0)
        )
        assert resp.status_code == 422

    async def test_impossible_coords_422(self, client):
        vid = await vehicle_id(client)
        body = plan_body(vid)
        body["origin"]["lat"] = 200.0  # not a latitude at all
        resp = await client.post("/api/trips", json=body)
        assert resp.status_code == 422

    async def test_plans_outside_europe(self, client):
        """The planner was boxed into a European corridor and refused
        everything else outright. Geography is now ORS's call — if it can find
        a drivable route, we plan it."""
        vid = await vehicle_id(client)
        body = plan_body(vid)
        body["origin"] |= {"label": "Los Angeles, CA", "lat": 34.05, "lon": -118.24}
        body["dest"] |= {"label": "Las Vegas, NV", "lat": 36.17, "lon": -115.14}
        resp = await client.post("/api/trips", json=body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["result"]["optimum_speed"] is not None

    async def test_motorway_cap_is_honoured_and_countries_reported(self, client):
        """The cap used to be hardcoded at 130, which is a western-European
        motorway and too fast for most of the world. Setting it lower must
        actually bind, or the answer is optimised against a limit that isn't
        there."""
        vid = await vehicle_id(client)
        fast = await client.post("/api/trips", json=plan_body(vid, motorway_cap_kph=130.0))
        slow = await client.post("/api/trips", json=plan_body(vid, motorway_cap_kph=100.0))
        assert fast.status_code == 200 and slow.status_code == 200

        def drive_min(resp):
            r = resp.json()["result"]
            top = max(s["speed_kph"] for s in r["speeds"] if s["feasible"])
            return next(s["drive_min"] for s in r["speeds"] if s["speed_kph"] == top)

        assert drive_min(slow) > drive_min(fast), "a lower cap must cost driving time"
        # The fixture route is built with country codes, so the UI can tell
        # which limits were real rather than assumed.
        assert fast.json()["result"]["countries"]

    async def test_ignore_speed_limits_round_trips_and_is_faster(self, client):
        """The what-if people asked for: what does ignoring the limits buy?
        It must reach the simulator, not just be stored on the request."""
        vid = await vehicle_id(client)
        capped = await client.post(
            "/api/trips", json=plan_body(vid, motorway_cap_kph=100.0)
        )
        free = await client.post(
            "/api/trips", json=plan_body(vid, motorway_cap_kph=100.0,
                                         ignore_speed_limits=True)
        )
        assert capped.status_code == 200 and free.status_code == 200
        assert free.json()["request"]["ignore_speed_limits"] is True

        def drive_min(resp):
            r = resp.json()["result"]
            top = max(s["speed_kph"] for s in r["speeds"] if s["feasible"])
            return next(s["drive_min"] for s in r["speeds"] if s["speed_kph"] == top)

        assert drive_min(free) < drive_min(capped)

    async def test_default_cap_unchanged_for_old_permalinks(self, client):
        """A request that predates the field must plan exactly as it used to."""
        vid = await vehicle_id(client)
        body = plan_body(vid)
        assert "motorway_cap_kph" not in body
        resp = await client.post("/api/trips", json=body)
        assert resp.status_code == 200
        assert resp.json()["request"]["motorway_cap_kph"] == 130.0

    async def test_cold_corridor_says_try_again_not_no_chargers(self, client, monkeypatch):
        """A corridor nobody has planned before is fetched in capped batches, so
        the first attempt can see only part of it. Reporting that as "no fast
        chargers found" is false, and it is the normal first experience of any
        route outside the warm European tiles."""
        from app.api import trips as trips_mod
        from app.services import chargers as chargers_mod

        async def no_chargers(db, r):
            chargers_mod.corridor_incomplete.set(True)
            return []

        monkeypatch.setattr(trips_mod.chargers_svc, "chargers_for_route", no_chargers)
        vid = await vehicle_id(client)
        resp = await client.post("/api/trips", json=plan_body(vid))
        assert resp.status_code == 503
        assert "try again" in resp.json()["detail"]

    async def test_live_run_accepts_non_european_gps(self, client):
        """A drive that can be planned must be drivable: the ping bounds used
        to reject any fix outside the corridor, stranding the driver."""
        from app.api.schemas import PingRequest

        PingRequest(lat=34.05, lon=-118.24)  # no ValidationError

    async def test_depart_below_target_422(self, client):
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips", json=plan_body(vid, depart_soc=20.0, target_soc=50.0)
        )
        assert resp.status_code == 422


class TestExcludingNetworks:
    """"Not Ionity" is an ordinary sentence and the plan had no way to hear it.

    The corridor fixture names its chargers "Ionity kmNNN", "Fastned kmNNN" and
    "Raststätte kmNNN", which is exactly the mix a real motorway gives: two
    identifiable networks and a third of the sites carrying no brand at all.
    """

    async def test_the_catalog_is_served_rather_than_hardcoded(self, client):
        resp = await client.get("/api/networks")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert {"slug", "label"} <= set(rows[0])
        assert "ionity" in {r["slug"] for r in rows}
        # Changes on deploy only, like the vehicle catalog.
        assert "max-age" in (resp.headers.get("cache-control") or "")

    async def test_an_excluded_network_is_not_in_the_plan(self, client):
        vid = await vehicle_id(client)
        plain = await client.post("/api/trips", json=plan_body(vid))
        assert plain.status_code == 200, plain.text
        # Whichever network this corridor's optimum actually uses — asserting
        # about a hardcoded one only tests the fixture. The Born is a 120 kW
        # car, so the DP has no use for the 300 kW sites and the answer is not
        # the one you would guess.
        target = _a_used_network(plain.json())
        assert target, _stop_names(plain.json())

        resp = await client.post(
            "/api/trips", json=plan_body(vid, exclude_networks=[target])
        )
        assert resp.status_code == 200, resp.text
        after = _stop_names(resp.json())
        assert after, "excluding one network must not empty the plan"
        assert target not in _networks_used(resp.json()), after

    async def test_it_applies_to_every_speed_and_not_just_the_optimum(self, client):
        """The sweep is the product: a reader who dials the speed down gets a
        different itinerary, and it has to honour the same exclusion."""
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips", json=plan_body(vid, exclude_networks=["fastned"])
        )
        assert resp.status_code == 200, resp.text
        for speed in resp.json()["result"]["speeds"]:
            for stop in speed["stops"]:
                assert network_of(stop.get("operator"), stop["name"]) != "fastned", (
                    speed["speed_kph"],
                    stop["name"],
                )

    async def test_an_unknown_network_is_refused_not_ignored(self, client):
        """Silently dropping a network somebody asked to avoid is the one
        failure this feature cannot have: they would be routed straight to it
        and never told why."""
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips", json=plan_body(vid, exclude_networks=["ionity", "shell"])
        )
        assert resp.status_code == 422
        assert "shell" in resp.text

    async def test_the_stored_request_does_not_depend_on_list_order(self, client):
        """`Trip.request` is what a re-plan replays and what gets counted, so
        the same intention has to store the same row."""
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips",
            json=plan_body(vid, exclude_networks=["tesla", "ionity", "ionity"]),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["request"]["exclude_networks"] == ["ionity", "tesla"]

    async def test_omitting_it_plans_exactly_as_before(self, client):
        """Old permalinks replan to the same answer — the field defaults to
        empty and `usable` returns the list untouched."""
        vid = await vehicle_id(client)
        a = await client.post("/api/trips", json=plan_body(vid))
        b = await client.post("/api/trips", json=plan_body(vid, exclude_networks=[]))
        assert a.json()["result"]["optimum_speed"] == b.json()["result"]["optimum_speed"]
        assert _stop_names(a.json()) == _stop_names(b.json())

    async def test_ruling_out_too_much_says_so_and_names_them(self, client):
        """A preference the driver set themselves is the one cause of failure
        they can undo in a second, so it is named rather than reported as a
        charging desert."""
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips",
            json=plan_body(
                vid,
                depart_soc=40.0,
                target_soc=30.0,
                exclude_networks=["ionity", "fastned"],
            ),
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert "IONITY" in detail and "Fastned" in detail, detail

    async def test_a_route_that_was_impossible_anyway_is_not_blamed_on_them(
        self, client
    ):
        """Excluding a network the corridor never had must not turn an ordinary
        charging gap into "it's your toggles" — that sends people to switch off
        something that was never the problem."""
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips",
            json=plan_body(
                vid, depart_soc=12.0, target_soc=10.0, exclude_networks=["evgo"]
            ),
        )
        assert resp.status_code == 422, resp.text
        assert "EVgo" not in resp.json()["detail"]


def _optimum(trip: dict) -> dict:
    return next(
        s
        for s in trip["result"]["speeds"]
        if s["speed_kph"] == trip["result"]["optimum_speed"]
    )


def _stop_names(trip: dict) -> list[str]:
    return [s["name"] for s in _optimum(trip)["stops"]]


def _networks_used(trip: dict) -> set[str]:
    return {
        n
        for n in (
            network_of(s.get("operator"), s["name"]) for s in _optimum(trip)["stops"]
        )
        if n
    }


def _a_used_network(trip: dict) -> str | None:
    used = _networks_used(trip)
    return sorted(used)[0] if used else None


class TestTripNotFound:
    async def test_unknown_trip_404(self, client):
        resp = await client.get("/api/trips/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    async def test_malformed_trip_id_404(self, client):
        resp = await client.get("/api/trips/not-a-uuid")
        assert resp.status_code == 404


class TestFactorsThroughTheApi:
    async def test_new_fields_are_returned(self, client):
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips",
            json=plan_body(vid, conditions_factor=1.3, charge_power_factor=0.55,
                           autobahn_open_share=0.3, queue_min=5, stop_overhead_min=8,
                           rest_interval_min=180, rest_min=20, price_per_kwh=0.60),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["result"]
        assert "climb_m" in result and "optimum_at_top_speed" in result
        for s in (x for x in result["speeds"] if x["feasible"]):
            assert s["energy_kwh"] > 0
            assert s["cost_eur"] == pytest.approx(s["energy_kwh"] * 0.60, rel=0.02)
            assert s["rest_min"] >= 0

    async def test_sweep_stops_at_the_cars_top_speed(self, client):
        # The seeded Born is limited to 160; asking for 200 must not invent it.
        vid = await vehicle_id(client)
        resp = await client.post(
            "/api/trips", json=plan_body(vid, speed_min=100.0, speed_max=200.0, speed_step=10.0)
        )
        assert resp.status_code == 200, resp.text
        speeds = [s["speed_kph"] for s in resp.json()["result"]["speeds"]]
        assert max(speeds) <= 160.0

    async def test_cold_conditions_slow_the_trip(self, client):
        vid = await vehicle_id(client)
        warm = await client.post("/api/trips", json=plan_body(vid))
        cold = await client.post(
            "/api/trips", json=plan_body(vid, conditions_factor=1.3, charge_power_factor=0.55)
        )
        assert warm.status_code == 200 and cold.status_code == 200

        def best(resp):
            r = resp.json()["result"]
            return next(s for s in r["speeds"] if s["speed_kph"] == r["optimum_speed"])

        assert best(cold)["total_min"] > best(warm)["total_min"]
        # And the winter answer should not be faster than the summer one.
        assert best(cold)["speed_kph"] <= best(warm)["speed_kph"]

    async def test_vehicle_columns_have_usable_defaults(self, client, db_session):
        # A row written before these columns existed must still plan — the
        # simulator falls back rather than crashing on a missing attribute.
        from app.models import Vehicle

        legacy = Vehicle(
            slug="legacy-car", make="Legacy", model="Car", variant="x",
            usable_kwh=60.0,
            consumption={"model": "quadratic", "a_wh_km": 55.0, "b_wh_km_per_kph2": 0.0105},
            charge_curve=[[0, 50], [10, 120], [50, 80], [100, 10]],
            max_dc_kw=120.0, sort_order=99,
        )
        db_session.add(legacy)
        await db_session.commit()
        resp = await client.post("/api/trips", json=plan_body(str(legacy.id)))
        assert resp.status_code == 200, resp.text


class TestPublicSafetyLimits:
    """Guards that only matter once the URL is somewhere public."""

    async def test_an_absurdly_long_route_is_refused(self, client, monkeypatch):
        """The coordinate bounds alone permit Portugal→Finland: ~200 upstream
        charger-tile fetches and a multi-megabyte result row, from one request."""
        from app.api import trips as trips_api

        monkeypatch.setattr(trips_api, "MAX_ROUTE_M", 1000.0)  # 1 km
        vehicles = (await client.get("/api/vehicles")).json()
        resp = await client.post("/api/trips", json=_plan_body(vehicles[0]["id"]))
        assert resp.status_code == 422
        assert "km" in resp.text

    async def test_a_trip_can_be_deleted_by_anyone_holding_its_link(self, client):
        """With no accounts the link is the only key there is, so it has to be
        what authorises erasure — otherwise 'ask and we'll delete it' is the
        only answer available to someone who wants their trip gone."""
        vehicles = (await client.get("/api/vehicles")).json()
        trip = (await client.post("/api/trips", json=_plan_body(vehicles[0]["id"]))).json()

        assert (await client.delete(f"/api/trips/{trip['id']}")).status_code == 204
        assert (await client.get(f"/api/trips/{trip['id']}")).status_code == 404
        # Idempotent-ish: a second delete is a clean 404, not a 500.
        assert (await client.delete(f"/api/trips/{trip['id']}")).status_code == 404

    async def test_deleting_a_trip_takes_its_location_trail_with_it(
        self, client, db_session
    ):
        """A drive's GPS breadcrumbs outliving the trip would orphan a location
        trace with no link left that could ever reach it."""
        from sqlalchemy import func, select

        from app.models import TripEvent, TripRun

        vehicles = (await client.get("/api/vehicles")).json()
        trip = (await client.post("/api/trips", json=_plan_body(vehicles[0]["id"]))).json()
        started = await client.post(
            f"/api/trips/{trip['id']}/runs",
            json={
                "planned_speed_kph": trip["result"]["optimum_speed"],
                "depart_soc": 90.0, "lat": 52.09, "lon": 5.12,
            },
        )
        assert started.status_code == 200

        await client.delete(f"/api/trips/{trip['id']}")
        runs = (await db_session.execute(select(func.count()).select_from(TripRun))).scalar_one()
        events = (await db_session.execute(select(func.count()).select_from(TripEvent))).scalar_one()
        assert (runs, events) == (0, 0)


def _plan_body(vehicle_id: str) -> dict:
    return {
        "origin": {"label": "Utrecht", "lat": 52.09, "lon": 5.12},
        "dest": {"label": "Innsbruck", "lat": 47.26, "lon": 11.39},
        "vehicle_id": vehicle_id,
        "departure_iso": "2026-08-10T07:00:00",
    }


class TestFeedback:
    """Feedback is stored here rather than at a third-party form, which means
    this is the one endpoint that persists free text a stranger typed."""

    async def test_anyone_can_leave_feedback(self, client):
        r = await client.post("/api/feedback", json={"message": "the Born curve looks off"})
        assert r.status_code == 201

    async def test_an_empty_message_is_refused(self, client):
        assert (await client.post("/api/feedback", json={"message": "   "})).status_code == 422
        assert (await client.post("/api/feedback", json={"message": ""})).status_code == 422

    async def test_an_overlong_message_is_refused_not_truncated(self, client):
        r = await client.post("/api/feedback", json={"message": "x" * 2001})
        assert r.status_code == 422, "a 2001-char message must not reach a 2000-char column"

    async def test_the_inbox_is_invisible_without_a_token(self, client):
        """With no FEEDBACK_TOKEN configured the route should not exist at all —
        a 403 would confirm there is something there to guess at."""
        await client.post("/api/feedback", json={"message": "secret note"})
        r = await client.get("/api/feedback")
        assert r.status_code == 404
        assert "secret note" not in r.text

    async def test_a_wrong_token_is_indistinguishable_from_no_inbox(
        self, client, monkeypatch
    ):
        from app.core.config import settings

        monkeypatch.setattr(settings, "FEEDBACK_TOKEN", "s3cret")
        await client.post("/api/feedback", json={"message": "hello"})
        assert (await client.get("/api/feedback")).status_code == 404
        bad = await client.get("/api/feedback", headers={"X-Feedback-Token": "wrong"})
        assert bad.status_code == 404
        good = await client.get("/api/feedback", headers={"X-Feedback-Token": "s3cret"})
        assert good.status_code == 200
        assert good.json()[0]["message"] == "hello"
