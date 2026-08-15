"""API tests for live journey tracking.

Same shape as `test_api.py`: FastAPI on in-memory SQLite with ORS/OCM stubbed
at the service layer. No network, no MSSQL.

The load-bearing test in here is `TestCapabilities` — the app has no auth, so
the only thing separating "anyone can watch this drive" from "anyone can drive
it" is that the run id never leaves the response that creates it.
"""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.runs import _sim_params
from app.api.schemas import PlanRequest
from app.api.trips import sim_params_for
from app.core.database import Base, get_db
from app.main import app
from app.models import TripEvent, TripRun, Vehicle
from app.services.geo import RouteGeometry
from app.services.routing import RouteData
from app.services.simulator import ChargerNode, SimParams
from tests.fixtures import nl_to_austria_route
from tests.test_api import BORN_SEED, plan_body

# The stubbed geometry is a single straight leg, so a "position" has to be an
# interpolation of its endpoints — pick a plausible-looking lat/lon off the top
# of your head and it lands tens of kilometres off-route, where the tracker
# correctly refuses to guess anything at all.
ORIGIN = (52.09, 5.12)
DEST = (47.26, 11.39)


def along(frac: float) -> dict:
    """A point exactly on the stubbed route, `frac` of the way along."""
    return {
        "lat": ORIGIN[0] + (DEST[0] - ORIGIN[0]) * frac,
        "lon": ORIGIN[1] + (DEST[1] - ORIGIN[1]) * frac,
    }


# One plausible hour of driving: ~10% along the geometry, which is ~96 km in
# the segment space the simulator measures in, at a bit over 130 km/h. The
# distance and the duration have to agree — hand the tracker 400 km in an hour
# and it will faithfully bill 160 km/h of drag over the lot and report a flat
# battery, which is correct arithmetic and a useless test.
FIRST_LEG = {**along(0.1), "moving_s": 2650.0}


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
    geometry = RouteGeometry([ORIGIN, DEST])
    # Put the chargers somewhere. `nl_to_austria_route` leaves lat/lon at their
    # (0, 0) default because the DP only ever reads offsets — but anything that
    # matches a POSITION to a charger (arriving by GPS) then has every site in
    # the Gulf of Guinea, and a test of it would pass or fail for the wrong
    # reason. Interpolated along the same straight line the geometry uses.
    _total_seg = sum(s.dist_m for s in segments)
    charger_nodes = [
        replace(c, **along(c.offset_m / _total_seg)) for c in charger_nodes
    ]
    # Segment-space and geometry-space totals differ wildly on this synthetic
    # pair, so give the run an explicit map instead of letting it guess.
    total_seg = sum(s.dist_m for s in segments)
    scale = geometry.total_m / total_seg
    offsets, acc = [0.0], 0.0
    for s in segments:
        acc += s.dist_m
        offsets.append(acc * scale)
    route = RouteData(
        geometry=geometry,
        segments=segments,
        seg_geom_offsets=offsets,
        total_dist_m=total_seg,
        total_dur_s=total_seg / 30.0,
    )

    async def fake_get_route(db, o_lat, o_lon, d_lat, d_lon):
        return route

    async def fake_chargers(db, r) -> list[ChargerNode]:
        return charger_nodes

    for mod in ("app.api.trips", "app.api.runs"):
        monkeypatch.setattr(f"{mod}.routing.get_route", fake_get_route)
        monkeypatch.setattr(f"{mod}.chargers_svc.chargers_for_route", fake_chargers)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def make_trip(client, **overrides) -> dict:
    vehicles = await client.get("/api/vehicles")
    vid = vehicles.json()[0]["id"]
    resp = await client.post("/api/trips", json=plan_body(vid, **overrides))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def start_run(client, trip: dict, **overrides) -> dict:
    speed = trip["result"]["optimum_speed"] or trip["result"]["speeds"][0]["speed_kph"]
    body = {
        "planned_speed_kph": speed,
        "depart_soc": 100.0,
        "lat": ORIGIN[0],
        "lon": ORIGIN[1],
    }
    body.update(overrides)
    resp = await client.post(f"/api/trips/{trip['id']}/runs", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestStartingADrive:
    async def test_it_returns_the_write_token_once(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        assert run["run_id"]
        assert run["run_ref"] and run["run_ref"] != run["run_id"]
        assert run["state"]["soc"] == 100.0
        assert run["state"]["soc_is_measured"] is True

    async def test_a_speed_the_trip_never_simulated_is_rejected(self, client):
        trip = await make_trip(client)
        resp = await client.post(
            f"/api/trips/{trip['id']}/runs",
            json={"planned_speed_kph": 137.0, "depart_soc": 100.0,
                  "lat": ORIGIN[0], "lon": ORIGIN[1]},
        )
        assert resp.status_code == 422

    async def test_a_driver_can_start_below_the_planner_s_soc_floor(self, client):
        """`PlanRequest` requires ≥10% because planning from 6% is a silly
        question. Starting a drive at 6% is not — it's Tuesday."""
        trip = await make_trip(client)
        run = await start_run(client, trip, depart_soc=6.0)
        assert run["state"]["soc"] == 6.0

    async def test_an_unknown_trip_is_404(self, client):
        resp = await client.post(
            "/api/trips/2b3a4c5d-0000-0000-0000-000000000000/runs",
            json={"planned_speed_kph": 120.0, "depart_soc": 90.0,
                  "lat": ORIGIN[0], "lon": ORIGIN[1]},
        )
        assert resp.status_code == 404


class TestCapabilities:
    """The whole security model, in three tests."""

    async def test_the_live_read_never_leaks_the_write_token(self, client):
        """Do not delete or weaken this.

        There is no auth. A watcher holds the trip's share link; the driver
        holds the run id. If the run id ever appears in a read response — at
        any depth, including inside `plan` — then anyone the link was forwarded
        to can steer someone else's drive.
        """
        trip = await make_trip(client)
        run = await start_run(client, trip)
        resp = await client.get(f"/api/trips/{trip['id']}/live")
        assert resp.status_code == 200
        assert run["run_id"] not in resp.text

        await client.post(f"/api/runs/{run['run_id']}/replan", json={})
        again = await client.get(f"/api/trips/{trip['id']}/live")
        assert run["run_id"] not in again.text

    async def test_a_watcher_cannot_write_with_the_trip_id(self, client):
        """404, not 403 — a probe shouldn't learn that the id exists at all."""
        trip = await make_trip(client)
        await start_run(client, trip)
        resp = await client.post(
            f"/api/runs/{trip['id']}/ping",
            json={**along(0.3), "moving_s": 60},
        )
        assert resp.status_code == 404

    @pytest.mark.parametrize(
        "bad", ["not-a-uuid", "2b3a4c5d-0000-0000-0000-000000000000"]
    )
    async def test_unknown_or_malformed_run_ids_are_404(self, client, bad):
        resp = await client.post(
            f"/api/runs/{bad}/ping",
            json={**along(0.3), "moving_s": 60},
        )
        assert resp.status_code == 404

    async def test_the_review_needs_the_right_ref(self, client):
        trip = await make_trip(client)
        await start_run(client, trip)
        bad = await client.get(f"/api/trips/{trip['id']}/runs/deadbeefdeadbeef/review")
        assert bad.status_code == 404

    async def test_a_live_position_is_never_cached(self, client):
        trip = await make_trip(client)
        await start_run(client, trip)
        resp = await client.get(f"/api/trips/{trip['id']}/live")
        assert resp.headers.get("cache-control") == "no-store"


class TestOneDriveAtATime:
    """A trip can only be under way once.

    Without this the laptop starts a run, the phone starts another, and the
    first keeps accepting pings from a token that still works while every
    reader follows the second — a silently forked drive that looks fine on
    both screens.
    """

    async def test_a_second_start_is_refused(self, client):
        trip = await make_trip(client)
        await start_run(client, trip)
        resp = await client.post(
            f"/api/trips/{trip['id']}/runs",
            json={
                "planned_speed_kph": trip["result"]["optimum_speed"],
                "depart_soc": 80.0, "lat": ORIGIN[0], "lon": ORIGIN[1],
            },
        )
        assert resp.status_code == 409
        assert "already being driven" in resp.text

    async def test_superseding_a_drive_that_is_still_reporting_is_refused(self, client):
        """`supersede` is for a driver who lost their token — not for anyone the
        share link reached. Superseding ends the real driver's run and points
        every watcher at the newcomer's position, so a drive that is actively
        sending pings is not available to take."""
        trip = await make_trip(client)
        await start_run(client, trip)
        resp = await client.post(
            f"/api/trips/{trip['id']}/runs",
            json={
                "planned_speed_kph": trip["result"]["optimum_speed"],
                "depart_soc": 80.0, "lat": ORIGIN[0], "lon": ORIGIN[1],
                "supersede": True,
            },
        )
        assert resp.status_code == 409
        assert "still sending its position" in resp.text

    async def _go_quiet(self, db_session, minutes: float):
        from datetime import datetime, timedelta

        from sqlalchemy import select

        from app.models import TripRun

        run = (await db_session.execute(select(TripRun))).scalars().first()
        run.last_seen_at = datetime.utcnow() - timedelta(minutes=minutes)
        await db_session.commit()

    async def test_superseding_a_quiet_drive_replaces_it(self, client, db_session):
        from app.api.runs import TAKEOVER_QUIET_MIN

        trip = await make_trip(client)
        first = await start_run(client, trip)
        await self._go_quiet(db_session, TAKEOVER_QUIET_MIN + 1)
        second = await start_run(client, trip, supersede=True)
        assert second["run_id"] != first["run_id"]
        live = (await client.get(f"/api/trips/{trip['id']}/live")).json()
        assert live["run_ref"] == second["run_ref"]

    async def test_a_superseded_drive_stops_accepting_pings(self, client, db_session):
        from app.api.runs import TAKEOVER_QUIET_MIN

        trip = await make_trip(client)
        first = await start_run(client, trip)
        await self._go_quiet(db_session, TAKEOVER_QUIET_MIN + 1)
        await start_run(client, trip, supersede=True)
        resp = await client.post(f"/api/runs/{first['run_id']}/ping", json=FIRST_LEG)
        assert resp.status_code == 409

    async def test_a_drive_gone_quiet_stops_blocking_a_new_one(self, client, db_session):
        """A phone that runs flat must not lock the trip out for ever."""
        from datetime import datetime, timedelta

        from sqlalchemy import select

        from app.api.runs import ABANDON_AFTER_MIN
        from app.models import TripRun

        trip = await make_trip(client)
        first = await start_run(client, trip)
        run = (await db_session.execute(select(TripRun))).scalars().first()
        run.last_seen_at = datetime.utcnow() - timedelta(minutes=ABANDON_AFTER_MIN + 1)
        await db_session.commit()

        second = await start_run(client, trip)  # no 409, no supersede needed
        assert second["run_id"] != first["run_id"]

    async def test_starting_again_after_finishing_is_fine(self, client):
        trip = await make_trip(client)
        first = await start_run(client, trip)
        await client.post(f"/api/runs/{first['run_id']}/finish")
        second = await start_run(client, trip)
        assert second["run_id"] != first["run_id"]


class TestPastDrivesStayFindable:
    async def test_an_earlier_review_survives_a_later_drive(self, client):
        """The bug this pins: resolving a review through "the active run"
        meant every drive but the most recent had a dead permalink."""
        trip = await make_trip(client)
        first = await start_run(client, trip)
        await client.post(f"/api/runs/{first['run_id']}/finish")
        second = await start_run(client, trip)

        old = await client.get(
            f"/api/trips/{trip['id']}/runs/{first['run_ref']}/review"
        )
        assert old.status_code == 200
        assert old.json()["run_ref"] == first["run_ref"]
        assert first["run_id"] not in old.text
        assert second["run_id"] not in old.text

    async def test_every_drive_is_listed(self, client):
        trip = await make_trip(client)
        a = await start_run(client, trip)
        await client.post(f"/api/runs/{a['run_id']}/finish")
        b = await start_run(client, trip)
        resp = await client.get(f"/api/trips/{trip['id']}/runs")
        assert resp.status_code == 200
        refs = [r["run_ref"] for r in resp.json()]
        assert refs == [b["run_ref"], a["run_ref"]]  # newest first
        assert a["run_id"] not in resp.text and b["run_id"] not in resp.text

    async def test_a_finished_drive_still_renders_for_watchers(self, client):
        """Arriving shouldn't 404 the page everyone was following."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/finish")
        resp = await client.get(f"/api/trips/{trip['id']}/live")
        assert resp.status_code == 200
        assert resp.json()["status"] == "finished"

    async def test_a_drive_gone_quiet_no_longer_reads_as_live(self, client, db_session):
        """The banner on the trip page must not say "being driven right now"
        about a drive the rest of the API has already written off."""
        from datetime import datetime, timedelta

        from sqlalchemy import select

        from app.api.runs import ABANDON_AFTER_MIN
        from app.models import TripRun

        trip = await make_trip(client)
        await start_run(client, trip)
        run = (await db_session.execute(select(TripRun))).scalars().first()
        run.last_seen_at = datetime.utcnow() - timedelta(minutes=ABANDON_AFTER_MIN + 5)
        await db_session.commit()

        live = (await client.get(f"/api/trips/{trip['id']}/live")).json()
        assert live["status"] == "abandoned"

    async def test_the_live_view_reports_how_stale_it_is(self, client):
        trip = await make_trip(client)
        await start_run(client, trip)
        resp = await client.get(f"/api/trips/{trip['id']}/live")
        assert resp.json()["seconds_since_ping"] >= 0.0


class TestRetention:
    async def test_old_drives_and_their_traces_are_purged(self, client, db_session):
        """The location trail is the most sensitive thing here and nothing else
        in this app expires, so the purge has to actually delete both tables."""
        from datetime import datetime, timedelta

        from sqlalchemy import func, select

        from app.models import TripEvent, TripRun
        from scripts.purge_old_runs import purge

        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)

        row = (await db_session.execute(select(TripRun))).scalars().first()
        row.started_at = datetime.utcnow() - timedelta(days=120)
        await db_session.commit()

        # The purge opens its own session, so point it at this one.
        import scripts.purge_old_runs as mod

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
            runs, _ = await purge(days=90, apply=False)
            assert runs == 1
            assert (
                await db_session.execute(select(func.count()).select_from(TripRun))
            ).scalar_one() == 1  # dry run changed nothing

            runs, _ = await purge(days=90, apply=True)
            assert runs == 1
            assert (
                await db_session.execute(select(func.count()).select_from(TripRun))
            ).scalar_one() == 0
            assert (
                await db_session.execute(select(func.count()).select_from(TripEvent))
            ).scalar_one() == 0
        finally:
            mod.AsyncSessionLocal = original

    async def test_a_recent_drive_is_left_alone(self, client, db_session):
        from scripts.purge_old_runs import purge
        import scripts.purge_old_runs as mod

        trip = await make_trip(client)
        await start_run(client, trip)

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
            runs, events = await purge(days=90, apply=True)
            assert (runs, events) == (0, 0)
        finally:
            mod.AsyncSessionLocal = original


class TestDriving:
    async def test_a_ping_moves_the_car_and_drains_the_battery(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        resp = await client.post(
            f"/api/runs/{run['run_id']}/ping",
            json=FIRST_LEG,
        )
        assert resp.status_code == 200, resp.text
        st = resp.json()
        assert st["offset_m"] > 0
        assert st["soc"] < 100.0
        assert st["soc_is_measured"] is False
        assert st["soc_uncertainty_pct"] > 0.0

    async def test_a_vague_fix_does_not_move_the_car(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        resp = await client.post(
            f"/api/runs/{run['run_id']}/ping",
            json={**FIRST_LEG, "accuracy_m": 5000.0},
        )
        assert resp.json()["offset_m"] == run["state"]["offset_m"]

    async def test_a_reading_re_anchors_the_estimate(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(
            f"/api/runs/{run['run_id']}/ping",
            json=FIRST_LEG,
        )
        resp = await client.post(f"/api/runs/{run['run_id']}/soc", json={"soc": 61.0})
        assert resp.status_code == 200
        st = resp.json()
        assert st["soc"] == 61.0
        assert st["soc_is_measured"] is True
        assert st["soc_uncertainty_pct"] == 0.0

    async def test_state_survives_a_reload(self, client, db_session):
        """Catches the silent-JSON-mutation trap: these columns are plain
        `JSON`, so an in-place edit is dropped on commit and the drive would
        quietly never advance."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(
            f"/api/runs/{run['run_id']}/ping",
            json=FIRST_LEG,
        )
        db_session.expire_all()
        fresh = await client.get(f"/api/trips/{trip['id']}/live")
        assert fresh.json()["state"]["offset_m"] > 0

    async def test_finishing_closes_the_drive(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        resp = await client.post(f"/api/runs/{run['run_id']}/finish")
        assert resp.status_code == 200
        assert resp.json()["status"] == "finished"

    async def test_a_finished_drive_rejects_further_pings(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/finish")
        resp = await client.post(
            f"/api/runs/{run['run_id']}/ping",
            json={**along(0.3), "moving_s": 60.0},
        )
        assert resp.status_code == 409


class TestReplanning:
    async def test_it_answers_with_a_benchmark_against_the_original(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(
            f"/api/runs/{run['run_id']}/ping",
            json=FIRST_LEG,
        )
        resp = await client.post(f"/api/runs/{run['run_id']}/replan", json={})
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["plan_version"] == 1
        assert out["remaining"]["feasible"] is True
        b = out["benchmark"]
        assert b["original_speed_kph"] == pytest.approx(
            trip["result"]["optimum_speed"]
        )
        assert b["original_total_min"] > 0
        assert b["live_total_min"] == pytest.approx(
            out["elapsed_min"] + out["remaining"]["total_min"], abs=0.2
        )
        assert b["delta_min"] == pytest.approx(
            b["live_total_min"] - b["original_total_min"], abs=0.2
        )

    async def test_the_revised_plan_is_offset_from_where_you_are(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(
            f"/api/runs/{run['run_id']}/ping",
            json=FIRST_LEG,
        )
        out = (await client.post(f"/api/runs/{run['run_id']}/replan", json={})).json()
        assert out["offset_base_m"] > 0
        # The tail's own axes start at zero; the frontend re-bases them.
        assert out["remaining"]["timeline"][0]["dist_m"] == 0

    async def test_a_worse_battery_than_planned_is_flagged(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(
            f"/api/runs/{run['run_id']}/ping",
            json=FIRST_LEG,
        )
        st = (
            await client.post(f"/api/runs/{run['run_id']}/soc", json={"soc": 12.0})
        ).json()
        assert st["soc_vs_plan"] < 0
        assert st["needs_replan"] is True
        assert any("battery" in r for r in st["replan_reasons"])

    async def test_the_drive_is_then_measured_against_the_revised_plan(
        self, client
    ):
        """A re-plan replaces the plan of record, and "the plan" has to mean
        the new one afterwards.

        Measured against the original forever, the shortfall that triggered the
        re-plan stays on screen after it: the original expected charging the
        revision has re-arranged, so `soc_vs_plan` cannot recover and the amber
        "reality has drifted" panel offers to re-plan a journey that was just
        re-planned. The revision starts from the battery the car actually has,
        so immediately after one the drive is by construction on plan.
        """
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        before = (
            await client.post(f"/api/runs/{run['run_id']}/soc", json={"soc": 20.0})
        ).json()
        assert before["needs_replan"] is True
        assert before["soc_vs_plan"] < -8

        resp = await client.post(f"/api/runs/{run['run_id']}/replan", json={})
        assert resp.status_code == 200, resp.text

        after = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        assert after["soc"] == pytest.approx(before["soc"], abs=0.1)
        # Same battery, different yardstick: the revision was computed FROM
        # this battery, so the shortfall against the dead plan is gone.
        assert after["soc_vs_plan"] == pytest.approx(0.0, abs=1.0)
        assert not any("battery" in r for r in after["replan_reasons"])

    async def test_a_refused_replan_leaves_the_original_yardstick(self, client):
        """A re-plan that cannot reach the destination changes nothing, and the
        drive must keep being measured against the plan it is still on —
        otherwise the failed attempt would clear the very warning that is the
        reason to look for a charger."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        before = (
            await client.post(f"/api/runs/{run['run_id']}/soc", json={"soc": 12.0})
        ).json()

        resp = await client.post(f"/api/runs/{run['run_id']}/replan", json={})
        assert resp.status_code == 422

        after = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        assert after["soc_vs_plan"] == pytest.approx(before["soc_vs_plan"], abs=0.1)
        assert after["needs_replan"] is True

    async def test_a_replan_is_recorded_on_the_run(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(
            f"/api/runs/{run['run_id']}/ping",
            json=FIRST_LEG,
        )
        await client.post(f"/api/runs/{run['run_id']}/replan", json={})
        live = (await client.get(f"/api/trips/{trip['id']}/live")).json()
        assert live["plan"] is not None
        assert live["plan"]["plan_version"] == 1


class TestSayingYouAreThere:
    """A phone that is locked reports nothing, and a phone whose car is
    charging is locked. Without a way to say so, the drive sits believing you
    are still short of the stop you are plugged into."""

    async def test_it_moves_the_car_to_the_stop_and_marks_it(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        before = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        speed = next(
            s
            for s in trip["result"]["speeds"]
            if s["speed_kph"] == trip["result"]["optimum_speed"]
        )
        stop = next(s for s in speed["stops"] if s["offset_m"] > before["offset_m"])

        resp = await client.post(
            f"/api/runs/{run['run_id']}/arrive",
            json={"charger_id": stop["charger_id"]},
        )
        assert resp.status_code == 200, resp.text
        out = resp.json()
        st = out["state"]
        assert out["matched_name"] == stop["name"]
        assert st["at_charger_id"] == stop["charger_id"]
        assert st["offset_m"] == pytest.approx(stop["offset_m"], abs=1500)
        assert st["stale"] is False

    async def test_the_kilometres_it_skips_are_billed(self, client):
        """Moving the car forward without pricing the drag would hand back a
        battery that never spent the energy to get there — a worse lie than the
        stale position it replaces."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        before = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        speed = next(
            s
            for s in trip["result"]["speeds"]
            if s["speed_kph"] == trip["result"]["optimum_speed"]
        )
        stop = next(s for s in speed["stops"] if s["offset_m"] > before["offset_m"] + 20_000)

        after = (
            await client.post(
                f"/api/runs/{run['run_id']}/arrive",
                json={"charger_id": stop["charger_id"]},
            )
        ).json()["state"]
        assert after["soc"] < before["soc"] - 1.0

    async def test_the_phone_s_position_beats_the_stop_on_screen(self, client):
        """A driver charging somewhere the plan never chose — a services they
        liked the look of, the only free stall in town — must not be told they
        are at the stop the card happened to be showing. The snapshot holds
        every candidate on the corridor, not just the planned stops, so the
        site they are standing at is usually in it."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        speed = next(
            s
            for s in trip["result"]["speeds"]
            if s["speed_kph"] == trip["result"]["optimum_speed"]
        )
        on_screen = speed["stops"][0]
        # Standing at a DIFFERENT charger, and naming the on-screen one as the
        # fallback exactly as the button does.
        segments, nodes = nl_to_austria_route()
        total_seg = sum(x.dist_m for x in segments)
        node = next(
            n
            for n in nodes
            if n.charger_id != on_screen["charger_id"]
            and n.offset_m > on_screen["offset_m"]
        )
        elsewhere = replace(node, **along(node.offset_m / total_seg))
        out = (
            await client.post(
                f"/api/runs/{run['run_id']}/arrive",
                json={
                    "charger_id": on_screen["charger_id"],
                    "lat": elsewhere.lat,
                    "lon": elsewhere.lon,
                },
            )
        ).json()
        assert out["state"]["at_charger_id"] == elsewhere.charger_id
        assert out["matched_name"] == elsewhere.name

    async def test_a_position_at_no_known_charger_still_moves_the_car(self, client):
        """Being somewhere we have no charger for is a fact about our data, not
        a reason to leave the drive believing the car is 69 km back."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        before = (await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)).json()
        # Halfway between two consecutive chargers — tens of kilometres from
        # either, so nothing can match however generous the radius.
        segments, nodes = nl_to_austria_route()
        total_seg = sum(x.dist_m for x in segments)
        ahead = sorted(n.offset_m for n in nodes if n.offset_m > before["offset_m"])
        middle = (ahead[0] + ahead[1]) / 2
        out = (
            await client.post(
                f"/api/runs/{run['run_id']}/arrive", json=along(middle / total_seg)
            )
        ).json()
        assert out["matched_name"] is None
        assert out["state"]["at_charger_id"] is None
        assert out["state"]["offset_m"] > before["offset_m"]

    async def test_an_unplanned_charger_is_still_described(self, client):
        """The plan's stops are four sites out of the hundreds on the corridor.
        Stopping at one of the others is ordinary, and the screen has to be
        able to say what the car is standing at — with the one number the plan
        cannot give: how much has to go in before you can leave."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        before = (
            await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        ).json()
        speed = next(
            s
            for s in trip["result"]["speeds"]
            if s["speed_kph"] == trip["result"]["optimum_speed"]
        )
        planned = {s["charger_id"] for s in speed["stops"]}
        segments, nodes = nl_to_austria_route()
        total_seg = sum(x.dist_m for x in segments)
        node = next(
            n
            for n in nodes
            if n.charger_id not in planned and n.offset_m > before["offset_m"]
        )
        here = replace(node, **along(node.offset_m / total_seg))

        out = (
            await client.post(
                f"/api/runs/{run['run_id']}/arrive",
                json={"lat": here.lat, "lon": here.lon},
            )
        ).json()
        at = out["state"]["at_charger"]
        assert at is not None
        assert at["charger_id"] == node.charger_id
        assert at["planned"] is False, "this one is not in the plan"
        assert at["power_kw"] > 0
        # And the answer to "how much do I need before I can go".
        need = out["state"]["need_soc_next"]
        assert need is not None and 0 < need <= 100

    async def test_a_charger_that_is_not_on_this_route_is_refused(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        resp = await client.post(
            f"/api/runs/{run['run_id']}/arrive", json={"charger_id": "nope"}
        )
        assert resp.status_code == 404

    async def test_a_watcher_cannot_move_the_car(self, client):
        trip = await make_trip(client)
        await start_run(client, trip)
        resp = await client.post(
            f"/api/trips/{trip['id']}/arrive", json={"charger_id": "chg-3"}
        )
        assert resp.status_code == 404


class TestReplanningFromHere:
    """A re-plan is the one moment the driver explicitly means "from where I
    am", so it takes the fix the phone sends with it — and it is the only call
    allowed to move the car backwards.

    Both halves matter. GPS runs while the page is being looked at, so a phone
    unlocked at a services has not reported for twenty minutes and the drive
    would otherwise re-plan a stretch of road already driven. And `advance`
    clamps the offset forward, which is right for pings and is exactly why a
    mis-tapped arrival used to be permanent."""

    async def test_it_plans_from_the_fix_it_is_given(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        before = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]

        # A leg further on — the drive has heard nothing since the fix above,
        # which is exactly what a phone in a pocket looks like.
        resp = await client.post(
            f"/api/runs/{run['run_id']}/replan", json=along(0.15)
        )
        assert resp.status_code == 200, resp.text
        after = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        assert after["offset_m"] > before["offset_m"] + 20_000

    async def test_it_can_move_the_car_back(self, client):
        """The mis-tapped arrival, recovered without ending the drive. Nothing
        else in the app may walk the car backwards; this may, because the
        driver is asking for it by name."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        real = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]

        # "I'm plugged in here now" at a stop far ahead — the wrong one.
        speed = next(
            s
            for s in trip["result"]["speeds"]
            if s["speed_kph"] == trip["result"]["optimum_speed"]
        )
        stop = next(
            s for s in speed["stops"] if s["offset_m"] > real["offset_m"] + 20_000
        )
        wrong = (
            await client.post(
                f"/api/runs/{run['run_id']}/arrive",
                json={"charger_id": stop["charger_id"]},
            )
        ).json()["state"]
        assert wrong["offset_m"] > real["offset_m"] + 20_000

        # A ping from the real position cannot fix it — that is the clamp.
        pinged = (
            await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        ).json()
        assert pinged["offset_m"] == pytest.approx(wrong["offset_m"], abs=1.0)

        # A re-plan can.
        resp = await client.post(
            f"/api/runs/{run['run_id']}/replan", json=FIRST_LEG
        )
        assert resp.status_code == 200, resp.text
        back = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        assert back["offset_m"] == pytest.approx(real["offset_m"], abs=1500)
        assert back["at_charger_id"] is None

    async def test_moving_back_hands_the_energy_back(self, client):
        """The arrival billed the kilometres it skipped. Correcting the
        position without refunding them leaves a battery that paid for road
        the car is about to drive — the same defect as never billing it, in
        the other direction."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        real = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        speed = next(
            s
            for s in trip["result"]["speeds"]
            if s["speed_kph"] == trip["result"]["optimum_speed"]
        )
        stop = next(
            s for s in speed["stops"] if s["offset_m"] > real["offset_m"] + 20_000
        )
        wrong = (
            await client.post(
                f"/api/runs/{run['run_id']}/arrive",
                json={"charger_id": stop["charger_id"]},
            )
        ).json()["state"]
        assert wrong["soc"] < real["soc"] - 1.0

        await client.post(f"/api/runs/{run['run_id']}/replan", json=FIRST_LEG)
        back = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        # Back to roughly the battery it had before the wrong tap — not exact,
        # because the jump was billed at the clamped observed speed and the
        # refund is priced at the plan's cruise, and the residue is on the
        # pessimistic side.
        assert back["soc"] > wrong["soc"] + 1.0
        assert back["soc"] <= real["soc"] + 0.1

    async def test_a_fix_off_the_route_is_refused(self, client):
        """Planning from a position that is not on the road being driven is
        not a re-plan, it is a guess."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        resp = await client.post(
            f"/api/runs/{run['run_id']}/replan",
            json={"lat": ORIGIN[0] + 4.0, "lon": ORIGIN[1] + 4.0},
        )
        assert resp.status_code == 409

    async def test_without_a_fix_it_still_plans_from_the_last_one(self, client):
        """The phone may have no position to give — indoors, or permission
        refused. The button has to keep working."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        before = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        resp = await client.post(f"/api/runs/{run['run_id']}/replan", json={})
        assert resp.status_code == 200, resp.text
        after = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        assert after["offset_m"] == pytest.approx(before["offset_m"], abs=1.0)


class TestTakingAnArrivalBack:
    """"I'm plugged in here now" is one tap under the stop card, next to
    another button, pressed on a phone in a moving car — and it is the only
    write on this screen the model cannot correct on its own. `live.advance`
    clamps the offset forward so a wobbly fix cannot walk the car backwards,
    and that same rule means a wrong arrival survives every later ping: the
    drive insists you are at a charger you are 69 km short of until you
    physically get there."""

    async def _arrive_at_a_stop_ahead(self, client, trip, run):
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        before = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        speed = next(
            s
            for s in trip["result"]["speeds"]
            if s["speed_kph"] == trip["result"]["optimum_speed"]
        )
        stop = next(
            s for s in speed["stops"] if s["offset_m"] > before["offset_m"] + 20_000
        )
        after = (
            await client.post(
                f"/api/runs/{run['run_id']}/arrive",
                json={"charger_id": stop["charger_id"]},
            )
        ).json()["state"]
        return before, after

    async def test_it_puts_the_drive_back_where_it_was(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        before, after = await self._arrive_at_a_stop_ahead(client, trip, run)
        assert after["offset_m"] > before["offset_m"] + 20_000
        assert after["can_undo_arrive"] is True

        resp = await client.post(f"/api/runs/{run['run_id']}/arrive/undo")
        assert resp.status_code == 200, resp.text
        back = resp.json()
        assert back["offset_m"] == pytest.approx(before["offset_m"], abs=1.0)
        assert back["at_charger_id"] is None
        assert back["can_undo_arrive"] is False

    async def test_it_hands_the_billed_kilometres_back_too(self, client):
        """The arrival prices the stretch it skips. Restoring the position and
        leaving the energy spent would hand back a battery that paid for road
        it is about to drive again — the mirror of the bug the billing exists
        to prevent, and just as invisible."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        before, after = await self._arrive_at_a_stop_ahead(client, trip, run)
        assert after["soc"] < before["soc"] - 1.0

        back = (
            await client.post(f"/api/runs/{run['run_id']}/arrive/undo")
        ).json()
        assert back["soc"] == pytest.approx(before["soc"], abs=0.05)

    async def test_the_car_can_drive_on_normally_afterwards(self, client):
        """The point of the undo is that the drive continues, not that it is
        merely rewound: the next ping has to move the car again from the
        restored position rather than being clamped by the arrival's offset."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        before, _ = await self._arrive_at_a_stop_ahead(client, trip, run)
        await client.post(f"/api/runs/{run['run_id']}/arrive/undo")

        st = (
            await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        ).json()
        assert st["offset_m"] == pytest.approx(before["offset_m"], abs=2000)

    async def test_there_is_nothing_to_undo_before_an_arrival(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        st = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        assert st["can_undo_arrive"] is False

        resp = await client.post(f"/api/runs/{run['run_id']}/arrive/undo")
        assert resp.status_code == 409

    async def test_undoing_twice_does_not_walk_further_back(self, client):
        """One undo, for the last arrival. A chain of them would let a tap
        taken back three stops ago rewind a drive that has since moved on."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await self._arrive_at_a_stop_ahead(client, trip, run)
        first = (
            await client.post(f"/api/runs/{run['run_id']}/arrive/undo")
        ).json()
        resp = await client.post(f"/api/runs/{run['run_id']}/arrive/undo")
        assert resp.status_code == 409
        again = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        assert again["offset_m"] == pytest.approx(first["offset_m"], abs=1.0)

    async def test_a_watcher_cannot_undo(self, client):
        trip = await make_trip(client)
        await start_run(client, trip)
        resp = await client.post(f"/api/trips/{trip['id']}/arrive/undo")
        assert resp.status_code == 404


class TestArrivingIsNoticed:
    """Standing at a charger the plan did not choose used to be invisible.

    `nearest_planned_stop` matched the plan's four stops and nothing else, so
    the drive kept counting down to a charger 77 km up the road while its
    driver stood plugged in somewhere else — reported twice, from two different
    chargers, before the cause was found.
    """

    async def test_a_ping_at_an_unplanned_charger_marks_it(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        speed = next(
            s
            for s in trip["result"]["speeds"]
            if s["speed_kph"] == trip["result"]["optimum_speed"]
        )
        planned = {s["charger_id"] for s in speed["stops"]}
        segments, nodes = nl_to_austria_route()
        total_seg = sum(x.dist_m for x in segments)
        state = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        node = next(
            n
            for n in nodes
            if n.charger_id not in planned and n.offset_m > state["offset_m"]
        )
        here = along(node.offset_m / total_seg)

        # Parked: the car reaches it and stops, which is two pings — one that
        # drives there, one that finds it has not moved since.
        await client.post(
            f"/api/runs/{run['run_id']}/ping", json={**here, "moving_s": 1800.0}
        )
        st = (
            await client.post(
                f"/api/runs/{run['run_id']}/ping",
                json={**here, "moving_s": 0.0, "stationary_s": 120.0},
            )
        ).json()

        assert st["at_charger_id"] == node.charger_id
        assert st["at_charger"] is not None
        assert st["at_charger"]["planned"] is False
        # …and the number the driver is standing there asking for.
        assert st["need_soc_next"] is not None

    async def test_driving_past_one_does_not_count_as_stopping(self, client):
        """A charger passed at 130 km/h is not a stop. Being NEAR one is only
        half the test; `advance` requires the car to have stopped moving."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        segments, nodes = nl_to_austria_route()
        total_seg = sum(x.dist_m for x in segments)
        state = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        node = next(n for n in nodes if n.offset_m > state["offset_m"] + 50_000)

        st = (
            await client.post(
                f"/api/runs/{run['run_id']}/ping",
                json={**along(node.offset_m / total_seg), "moving_s": 1800.0},
            )
        ).json()
        assert st["at_charger_id"] is None


class TestAReadingIsAboutAPlace:
    """A dashboard figure is ground truth about a battery AT A PLACE.

    Anchored to wherever a sleeping phone last reported, it attaches to the
    wrong point on the route — and the next re-plan, which correctly moves the
    car forward on a fresh fix, then bills the drag for road already covered
    BEFORE the reading was taken. The driver watches the number they just
    typed correct itself downwards, and the same understated window teaches
    `run_factor` that the car is thriftier than it is.
    """

    async def test_a_reading_with_a_fix_survives_the_next_re_plan(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        # The phone reports once, then goes quiet while the car drives on.
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        here = along(0.25)

        typed = 45.0
        after_reading = (
            await client.post(
                f"/api/runs/{run['run_id']}/soc", json={"soc": typed, **here}
            )
        ).json()
        assert after_reading["soc"] == pytest.approx(typed, abs=0.1)

        # The re-plan sends the same fix — the car has not moved since — so it
        # has nothing to bill and must leave the figure alone.
        plan = await client.post(f"/api/runs/{run['run_id']}/replan", json=here)
        assert plan.status_code == 200, plan.text
        after_replan = (
            await client.get(f"/api/trips/{trip['id']}/live")
        ).json()["state"]
        assert after_replan["soc"] == pytest.approx(typed, abs=0.1)

    async def test_without_a_fix_the_gap_is_still_billed_twice(self, client):
        """The shape of the old bug, kept as a description of what the position
        buys. A reading with no position anchors where the drive last thought
        the car was; a re-plan from further on then charges that stretch to a
        battery already measured past it."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)

        typed = 45.0
        await client.post(f"/api/runs/{run['run_id']}/soc", json={"soc": typed})
        await client.post(f"/api/runs/{run['run_id']}/replan", json=along(0.25))
        blind = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        assert blind["soc"] < typed - 0.5

    async def test_an_off_route_fix_is_ignored_not_refused(self, client):
        """A reading taken at a supermarket two streets off the corridor is
        still the best number anyone has. The position is the only part of it
        that cannot be used."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        before = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]

        resp = await client.post(
            f"/api/runs/{run['run_id']}/soc",
            json={"soc": 33.0, "lat": 40.0, "lon": -3.0},
        )
        assert resp.status_code == 200, resp.text
        st = resp.json()
        assert st["soc"] == pytest.approx(33.0, abs=0.1)
        assert st["offset_m"] == pytest.approx(before["offset_m"], abs=1.0)


class TestHoldingASpeed:
    """The re-plan swept a band and took its optimum, which decides for the
    driver. Six hours in, "I'm going to sit at 120" is a fact about the road
    and the passengers, and the plan's job is to tell them what it costs — not
    to overrule it."""

    async def test_a_held_speed_is_the_plan(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        free = (await client.post(f"/api/runs/{run['run_id']}/replan", json={})).json()

        hold = free["optimum_speed"] - 10.0
        resp = await client.post(
            f"/api/runs/{run['run_id']}/replan", json={"hold_speed_kph": hold}
        )
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["held_speed_kph"] == pytest.approx(hold)
        # `optimum_speed` is what every reader treats as "the speed to hold",
        # so it is the plan's own speed — held or not.
        assert out["optimum_speed"] == pytest.approx(hold)
        # Slower than the optimum costs time, and the driver is told how much
        # BEFORE they are asked to live with it.
        assert out["hold_costs_min"] > 0
        assert out["benchmark"]["live_total_min"] > free["benchmark"]["live_total_min"]

    async def test_it_survives_the_next_plain_re_plan(self, client):
        """The standing "Re-plan" button says nothing about speed, so it must
        not silently overrule a decision made ten minutes ago."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        first = (await client.post(f"/api/runs/{run['run_id']}/replan", json={})).json()
        hold = first["optimum_speed"] - 10.0

        await client.post(
            f"/api/runs/{run['run_id']}/replan", json={"hold_speed_kph": hold}
        )
        again = (await client.post(f"/api/runs/{run['run_id']}/replan", json={})).json()
        assert again["held_speed_kph"] == pytest.approx(hold)
        assert again["optimum_speed"] == pytest.approx(hold)

    async def test_an_explicit_null_hands_the_choice_back(self, client):
        """Absent means "leave it alone" and null means "you pick" — a
        distinction the field alone cannot carry, hence `model_fields_set`."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        free = (await client.post(f"/api/runs/{run['run_id']}/replan", json={})).json()
        await client.post(
            f"/api/runs/{run['run_id']}/replan",
            json={"hold_speed_kph": free["optimum_speed"] - 10.0},
        )

        back = (
            await client.post(
                f"/api/runs/{run['run_id']}/replan", json={"hold_speed_kph": None}
            )
        ).json()
        assert back["held_speed_kph"] is None
        assert back["hold_costs_min"] == 0.0
        assert back["optimum_speed"] == pytest.approx(free["optimum_speed"])

    async def test_a_speed_the_car_cannot_do_is_clamped_not_refused(self, client):
        """"Hold 220" in a car that stops at 160 is a request for everything it
        has, not an error to hand back at the wheel."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        resp = await client.post(
            f"/api/runs/{run['run_id']}/replan", json={"hold_speed_kph": 220.0}
        )
        assert resp.status_code == 200, resp.text
        top = trip["result"]["vehicle"]["top_speed_kph"]
        assert resp.json()["held_speed_kph"] == pytest.approx(top)


class TestTurningDownAStop:
    """The planner optimises minutes; a stop can be bad for reasons minutes
    cannot see — nowhere to eat, nowhere to sit, not at nine in the evening.
    So the driver can turn one down, and what comes back has to be a real plan
    rather than the same one re-ordered."""

    async def test_it_offers_other_chargers_priced_against_the_current_one(
        self, client
    ):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)

        resp = await client.post(f"/api/runs/{run['run_id']}/alternatives")
        assert resp.status_code == 200, resp.text
        out = resp.json()

        assert out["current"] is not None
        assert out["current"]["delta_min"] == 0.0
        assert out["alternatives"], "expected at least one other charger"
        ids = {a["charger_id"] for a in out["alternatives"]}
        assert out["current"]["charger_id"] not in ids
        assert len(ids) == len(out["alternatives"])
        for a in out["alternatives"]:
            # Priced against the whole remaining journey. Under the same
            # reserve the current stop IS the optimum, so nothing can be
            # quicker — but a stretch option is computed under a lower floor
            # for the leg being driven, and going further before stopping is
            # exactly how it can come back faster. That is the trade being
            # offered, not a bug in the ranking.
            if not a["below_reserve"]:
                assert a["delta_min"] >= 0
            # What to send to `replan` to take this one: everything ranked
            # above it, including the stop being replaced.
            assert out["current"]["charger_id"] in a["exclude_charger_ids"]

    async def test_taking_an_alternative_re_plans_around_it(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        alts = (
            await client.post(f"/api/runs/{run['run_id']}/alternatives")
        ).json()
        rejected = alts["current"]["charger_id"]
        chosen = alts["alternatives"][0]

        resp = await client.post(
            f"/api/runs/{run['run_id']}/replan",
            json={"exclude_charger_ids": chosen["exclude_charger_ids"]},
        )
        assert resp.status_code == 200, resp.text
        stops = resp.json()["remaining"]["stops"]
        assert stops[0]["charger_id"] == chosen["charger_id"]
        assert all(s["charger_id"] != rejected for s in stops)

    async def test_a_rejection_outlives_the_re_plan_that_made_it(self, client):
        """A charger turned down stays turned down.

        `run.state` is a plain JSON column, so this is really a test that the
        exclusion was ASSIGNED rather than mutated in place — mutated, it is
        dropped at commit, and the standing "Re-plan" button hands back the
        stop the driver just walked away from.
        """
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        alts = (
            await client.post(f"/api/runs/{run['run_id']}/alternatives")
        ).json()
        rejected = alts["current"]["charger_id"]

        await client.post(
            f"/api/runs/{run['run_id']}/replan",
            json={"exclude_charger_ids": [rejected]},
        )
        # A later re-plan asking for nothing in particular.
        again = await client.post(f"/api/runs/{run['run_id']}/replan", json={})
        assert again.status_code == 200, again.text
        assert all(
            s["charger_id"] != rejected for s in again.json()["remaining"]["stops"]
        )
        # …and the alternatives no longer offer it either.
        after = (await client.post(f"/api/runs/{run['run_id']}/alternatives")).json()
        assert after["current"]["charger_id"] != rejected
        assert all(a["charger_id"] != rejected for a in after["alternatives"])

    async def test_it_offers_chargers_further_on_that_the_reserve_hid(
        self, client
    ):
        """"Can I not just push on to the services?"

        The DP refuses any leg that lands under `reserve_soc`, which is right
        for a plan and wrong for a menu: it also hides every charger further
        along that a driver could reach by arriving low. Those are offered,
        marked, and priced — and they can come back FASTER than the optimum,
        because going further before stopping is what a lower floor buys.
        """
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        out = (await client.post(f"/api/runs/{run['run_id']}/alternatives")).json()

        stretch = [a for a in out["alternatives"] if a["below_reserve"]]
        assert stretch, "expected at least one charger beyond the reserve"
        for a in stretch:
            # The reserve is what was hiding it — otherwise it is an ordinary
            # alternative dressed up as a risk.
            assert a["arrive_soc"] < 10.0
            # …and not a breakdown with extra steps.
            assert a["arrive_soc"] >= 4.0 - 1e-6
            # Further along the route than the stop it replaces.
            assert a["offset_m"] > out["current"]["offset_m"]
            # The floor it was computed under has to travel with it, or the
            # re-plan that takes it applies the full reserve and refuses.
            assert a["min_arrival_soc"] == 4.0

    async def test_taking_a_stretch_option_is_planned_under_its_own_floor(
        self, client
    ):
        """The floor is accepted for the leg being driven and persists, so the
        next re-plan does not refuse the stop the driver just chose."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        out = (await client.post(f"/api/runs/{run['run_id']}/alternatives")).json()
        stretch = next(a for a in out["alternatives"] if a["below_reserve"])

        resp = await client.post(
            f"/api/runs/{run['run_id']}/replan",
            json={
                "exclude_charger_ids": stretch["exclude_charger_ids"],
                "min_arrival_soc": stretch["min_arrival_soc"],
            },
        )
        assert resp.status_code == 200, resp.text
        stops = resp.json()["remaining"]["stops"]
        assert stops[0]["charger_id"] == stretch["charger_id"]
        assert stops[0]["arrive_soc"] < 10.0
        # Only the FIRST leg is relaxed: everything after it keeps the full
        # reserve, so one accepted risk cannot become a journey on fumes.
        assert all(s["arrive_soc"] >= 10.0 - 1e-6 for s in stops[1:])

        # And it sticks — a plain re-plan afterwards still reaches it.
        again = await client.post(f"/api/runs/{run['run_id']}/replan", json={})
        assert again.status_code == 200, again.text
        assert again.json()["remaining"]["stops"][0]["charger_id"] == (
            stretch["charger_id"]
        )

    async def test_the_bay_count_reaches_the_driver(self, client):
        """Capacity is the one thing about a site that separates a hub from a
        lone plug, and it sat in the `chargers` table since the first deploy
        without ever leaving it — `ChargerNode` did not carry it, so no plan,
        itinerary or alternative could show it. It is NOT availability; see the
        note in `AlternativeStops`."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        out = (await client.post(f"/api/runs/{run['run_id']}/alternatives")).json()

        seen = [a["n_points"] for a in [out["current"], *out["alternatives"]] if a]
        assert seen, "expected at least the current stop"
        assert any(n > 1 for n in seen), "the fixture's hubs lost their bays"

    async def test_a_stop_already_chosen_is_not_offered_as_an_alternative(
        self, client
    ):
        """The one row guaranteed to look like a bug.

        Taking a STRETCH option was the case that broke it: the plan in force
        reaches that charger only under the relaxed floor, and this endpoint
        re-planned with the full reserve — so the baseline pass picked
        something else and handed the driver's own chosen stop back as an
        alternative to itself. The endpoint now plans under the same floor the
        plan was made with, and filters the planned stop out regardless.
        """
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        first = (await client.post(f"/api/runs/{run['run_id']}/alternatives")).json()
        chosen = next(a for a in first["alternatives"] if a["below_reserve"])

        await client.post(
            f"/api/runs/{run['run_id']}/replan",
            json={
                "exclude_charger_ids": chosen["exclude_charger_ids"],
                "min_arrival_soc": chosen["min_arrival_soc"],
            },
        )

        after = (await client.post(f"/api/runs/{run['run_id']}/alternatives")).json()
        assert after["current"]["charger_id"] == chosen["charger_id"]
        assert all(
            a["charger_id"] != chosen["charger_id"] for a in after["alternatives"]
        )

    async def test_it_keeps_looking_until_it_finds_somewhere_to_eat(self, client):
        """The list is ranked by minutes, and the quickest few stops are
        regularly a lay-by, a car park and another lay-by — so "where can I
        actually eat" can go unanswered by a list that is working perfectly."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        out = (await client.post(f"/api/runs/{run['run_id']}/alternatives")).json()

        rows = [out["current"], *out["alternatives"]]
        assert any(r["food_hint"] for r in rows), (
            "expected at least one stop the name says you can eat at"
        )
        # And the guess is labelled with what it read, not a bare boolean —
        # "motorway services" is checkable by a human, "true" is not.
        assert all(
            isinstance(r["food_hint"], str) for r in rows if r["food_hint"]
        )

    async def test_it_can_swap_a_stop_further_down_the_plan(self, client):
        """The food question lands on a stop hours away — by the time it is the
        next one, the choice has been made for you. Naming a charger asks the
        same question about a different position, and the answer has to be
        about THAT position: a replacement near where the rejected stop was,
        not whatever happens to be first."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        plan = (await client.post(f"/api/runs/{run['run_id']}/replan", json={})).json()
        stops = plan["remaining"]["stops"]
        assert len(stops) >= 3, "need a plan with a stop worth calling 'later'"
        later = stops[2]

        out = (
            await client.post(
                f"/api/runs/{run['run_id']}/alternatives",
                json={"reject_charger_id": later["charger_id"]},
            )
        ).json()

        assert out["current"]["charger_id"] == later["charger_id"]
        assert out["alternatives"], "expected somewhere else for that stop"
        # Answers about that position, not about the next stop.
        assert all(
            a["charger_id"] != stops[0]["charger_id"] for a in out["alternatives"]
        )
        for a in out["alternatives"]:
            assert a["charger_id"] != later["charger_id"]
            # Near where the rejected one was, rather than anywhere on the
            # route — a "replacement" 400 km away is a different journey.
            assert abs(a["offset_m"] - later["offset_m"] - plan["offset_base_m"]) < 250_000
            # Pushing past the reserve is a first-leg idea; it cannot be
            # offered about a stop four hours away.
            assert a["below_reserve"] is False

    async def test_a_watcher_cannot_ask_for_alternatives(self, client):
        """It is a write-token route like every other run endpoint: the trip id
        reads the drive, it does not steer it."""
        trip = await make_trip(client)
        await start_run(client, trip)
        resp = await client.post(f"/api/runs/{trip['id']}/alternatives")
        assert resp.status_code == 404


class TestPlanParity:
    """A drive must be simulated under the assumptions its plan was made with.

    This is the seam that has broken twice. A field is added to `PlanRequest`,
    wired into the planner, and NOT into the live path — and nothing fails,
    because both halves still run. The drive just quietly plans a different
    journey than the driver is on: the mid-drive replan benchmarks against a
    promise made under other rules, and `live.advance` prices every segment
    through the same params, so the battery estimate goes with it.
    `motorway_cap_kph` and `ignore_speed_limits` were both missing this way.

    So the guard is not "these two agree today" — it is that there is one
    builder, and that every field on `PlanRequest` has been consciously placed
    on one side of the line.
    """

    # request field → the `SimParams` field it becomes, unchanged.
    DIRECT = {
        "depart_soc": "depart_soc",
        "target_soc": "target_soc",
        "autobahn_open_share": "autobahn_open_share",
        "motorway_cap_kph": "default_country_cap_kph",
        "ignore_speed_limits": "ignore_speed_limits",
        "over_cap_kph": "over_cap_kph",
        "over_freeflow_factor": "over_freeflow_factor",
        "site_power_factor": "site_power_factor",
        "queue_min": "queue_min",
        "stop_overhead_min": "stop_overhead_min",
        "rest_interval_min": "rest_interval_min",
        "rest_min": "rest_min",
        "price_per_kwh": "price_per_kwh",
        "winter_tyres": "winter_tyres",
    }

    # Reaches the simulator, but transformed on the way — asserted by value
    # below rather than by name.
    DERIVED = {
        "temperature_c",       # → consumption_factor, charge_power_factor, aux_kw
        "conditions_factor",   # → consumption_factor, when no temperature is given
        "charge_power_factor",  # → charge_power_factor, likewise
        "occupants",           # ┐
        "luggage_kg",          # ┘→ extra_mass_kg
    }

    # Not simulator settings: they choose the route, the car, or which speeds
    # to sweep, none of which a `SimParams` carries.
    NOT_SIM = {
        "origin",
        "dest",
        "vehicle_id",
        "departure_iso",
        "speed_min",
        "speed_max",
        "speed_step",
    }

    # Every direct field, set to something that is NOT the simulator's own
    # default — so a mapping that silently falls back to the default fails.
    LOUD = {
        "depart_soc": 90.0,
        "target_soc": 25.0,
        "autobahn_open_share": 0.85,
        "motorway_cap_kph": 112.0,
        "ignore_speed_limits": True,
        "over_cap_kph": 14.0,
        "over_freeflow_factor": 1.12,
        "site_power_factor": 0.62,
        "queue_min": 7.0,
        "stop_overhead_min": 11.0,
        "rest_interval_min": 210.0,
        "rest_min": 33.0,
        "price_per_kwh": 0.81,
        "winter_tyres": True,
    }

    def _request(self, **overrides) -> PlanRequest:
        return PlanRequest.model_validate(
            plan_body("f1a0e4d2-0000-4000-8000-000000000000", **{**self.LOUD, **overrides})
        )

    def test_every_request_field_is_classified(self):
        """Adding a field to `PlanRequest` fails this until somebody decides
        whether a drive needs it. That decision being implicit is the bug."""
        classified = set(self.DIRECT) | self.DERIVED | self.NOT_SIM
        assert set(PlanRequest.model_fields) == classified

    def test_every_direct_field_reaches_the_simulator(self):
        p = sim_params_for(self._request())
        defaults = SimParams()
        for req_field, sim_field in self.DIRECT.items():
            want = self.LOUD[req_field]
            # Guard against a vacuous pass: if the "loud" value ever equals the
            # simulator default, this test would hold with nothing wired up.
            assert getattr(defaults, sim_field) != want, req_field
            assert getattr(p, sim_field) == want, f"{req_field} → {sim_field}"

    def test_the_derived_fields_reach_it_too(self):
        warm = sim_params_for(self._request(temperature_c=20.0, occupants=2, luggage_kg=30.0))
        cold = sim_params_for(self._request(temperature_c=-12.0, occupants=5, luggage_kg=120.0))
        assert cold.consumption_factor > warm.consumption_factor
        assert cold.charge_power_factor < warm.charge_power_factor
        assert cold.aux_kw > warm.aux_kw
        assert cold.extra_mass_kg > warm.extra_mass_kg

        # Without a temperature the legacy multipliers are used as given, and
        # no conditioning load is implied.
        legacy = sim_params_for(
            self._request(temperature_c=None, conditions_factor=1.25, charge_power_factor=0.7)
        )
        assert legacy.consumption_factor == pytest.approx(1.25)
        assert legacy.charge_power_factor == pytest.approx(0.7)
        assert legacy.aux_kw == 0.0

    def test_the_drive_builds_the_same_params_as_the_plan(self):
        req = self._request()
        assert _sim_params(req) == sim_params_for(req)

    def test_the_learned_calibration_is_the_only_difference(self):
        """`run_factor` is what a drive knows that a plan cannot — this car, on
        this day, in this wind. It must be the ONLY thing it changes."""
        req = self._request()
        plan = sim_params_for(req)
        drive = _sim_params(req, 1.3)
        assert drive.consumption_factor == pytest.approx(plan.consumption_factor * 1.3)
        for f in fields(SimParams):
            if f.name == "consumption_factor":
                continue
            assert getattr(drive, f.name) == getattr(plan, f.name), f.name

    async def test_a_replan_honours_the_what_if_the_plan_was_made_under(self, client):
        """End to end, through the endpoints: the same drive, replanned, is
        faster on a trip planned with the limits ignored. This is the assertion
        that would have caught the original bug — the mapping was fine in
        isolation and wrong where it was used."""

        async def replan_total(**plan_overrides) -> float:
            trip = await make_trip(client, **plan_overrides)
            run = await start_run(client, trip)
            await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
            resp = await client.post(f"/api/runs/{run['run_id']}/replan", json={})
            assert resp.status_code == 200, resp.text
            return resp.json()["remaining"]["total_min"]

        # Same cruise speed band either way, so the only thing that can move
        # the answer is whether the caps are being applied.
        legal = await replan_total(speed_min=140.0, speed_max=140.0)
        free = await replan_total(speed_min=140.0, speed_max=140.0, ignore_speed_limits=True)
        assert free < legal


class TestReview:
    async def test_it_compares_the_drive_with_the_promise(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await client.post(
            f"/api/runs/{run['run_id']}/ping",
            json=FIRST_LEG,
        )
        await client.post(f"/api/runs/{run['run_id']}/soc", json={"soc": 40.0})
        await client.post(f"/api/runs/{run['run_id']}/finish")

        resp = await client.get(
            f"/api/trips/{trip['id']}/runs/{run['run_ref']}/review"
        )
        assert resp.status_code == 200, resp.text
        rv = resp.json()
        assert rv["status"] == "finished"
        assert rv["planned_total_min"] > 0
        assert rv["n_soc_readings"] == 1
        assert rv["stops"]  # the planned stops, with actuals where we have them
        assert run["run_id"] not in resp.text

    async def test_no_live_drive_is_404(self, client):
        trip = await make_trip(client)
        resp = await client.get(f"/api/trips/{trip['id']}/live")
        assert resp.status_code == 404


async def _wind_clock_back(db_session, minutes: float) -> None:
    """Move the run's departure back, which is the only way to make wall-clock
    time pass inside a test. The charge is projected from `datetime.utcnow()`
    minus `started_at` on purpose — that is what a sleeping phone cannot stop
    — so there is nothing to fake but the start."""
    run = (await db_session.execute(select(TripRun))).scalars().first()
    run.started_at = run.started_at - timedelta(minutes=minutes)
    await db_session.commit()


class TestTheStopFromArrivalToDeparture:
    """The three questions a driver has while standing at a plug: how much do
    I need, how much do I have, and what do I actually have when I unplug.

    The middle one used to be unanswerable. Charging was integrated per ping,
    and a phone whose car is charging is in a pocket — so the estimate froze at
    the value it had on arrival and the minutes-remaining under it never
    counted down. Reopening the page after twenty minutes showed the screen
    that had been left behind, which is worse than showing nothing.
    """

    async def _at_a_stop(self, client, trip, run):
        await client.post(f"/api/runs/{run['run_id']}/ping", json=FIRST_LEG)
        before = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        speed = next(
            s
            for s in trip["result"]["speeds"]
            if s["speed_kph"] == trip["result"]["optimum_speed"]
        )
        stop = next(
            s for s in speed["stops"] if s["offset_m"] > before["offset_m"] + 20_000
        )
        st = (
            await client.post(
                f"/api/runs/{run['run_id']}/arrive",
                json={"charger_id": stop["charger_id"]},
            )
        ).json()["state"]
        return stop, st

    async def test_arriving_starts_the_session_without_waiting_for_a_ping(
        self, client
    ):
        """The tap is the last thing a driver does before pocketing the phone.
        Anchoring the charge on the NEXT ping would model the whole stop — the
        one this button exists for — as not charging at all."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        _stop, st = await self._at_a_stop(client, trip, run)
        assert st["charging"] is not None
        assert st["charging"]["power_kw"] > 0
        assert st["charging"]["start_soc"] == pytest.approx(st["soc"], abs=0.2)

    async def test_it_says_both_what_is_needed_and_what_the_plan_wanted(
        self, client
    ):
        """Two targets, because they answer different questions. The floor says
        "you could go now"; the plan's target is what leaving on costs you
        later. Showing only the target sits a driver through minutes they did
        not have to; showing only the floor quietly drops the optimisation."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        stop, st = await self._at_a_stop(client, trip, run)
        ch = st["charging"]
        assert ch["min_soc"] is not None
        assert ch["target_soc"] == pytest.approx(stop["depart_soc"], abs=0.1)
        # The plan leaves higher than the bare minimum to reach the next stop.
        assert ch["target_soc"] >= ch["min_soc"]
        assert ch["to_target_min"] is not None and ch["to_target_min"] > 0

    async def test_the_estimate_climbs_on_the_clock_with_no_pings(
        self, client, db_session
    ):
        """No ping, no fix, nothing from the phone — only time."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        _stop, st = await self._at_a_stop(client, trip, run)

        await _wind_clock_back(db_session, 15)

        later = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        assert later["soc"] > st["soc"] + 1.0
        assert later["charging"]["since_min"] >= 14.0
        # And the countdown really counts down — to nothing at all once the
        # target is reached, which is the answer "you can go".
        left = later["charging"]["to_target_min"]
        assert left is None or left < st["charging"]["to_target_min"]

    async def test_leaving_takes_the_typed_figure_and_ends_the_stop(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        _stop, st = await self._at_a_stop(client, trip, run)

        out = (
            await client.post(
                f"/api/runs/{run['run_id']}/soc",
                json={"soc": 71.0, "leaving": True},
            )
        ).json()
        assert out["soc"] == pytest.approx(71.0, abs=0.05)
        assert out["charging"] is None
        assert out["at_charger_id"] is None
        assert out["at_charger"] is None
        assert out["need_soc_next"] is None
        assert out["soc_is_measured"] is True

    async def test_a_reading_that_is_not_a_departure_keeps_charging(
        self, client, db_session
    ):
        """"I'm on 40 now" and "I'm leaving on 40" are different sentences.
        One asks the estimate to keep counting up from the corrected figure."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await self._at_a_stop(client, trip, run)

        out = (
            await client.post(
                f"/api/runs/{run['run_id']}/soc", json={"soc": 40.0}
            )
        ).json()
        assert out["charging"] is not None
        assert out["charging"]["start_soc"] == pytest.approx(40.0, abs=0.05)
        assert out["at_charger_id"] is not None

        await _wind_clock_back(db_session, 10)
        later = (await client.get(f"/api/trips/{trip['id']}/live")).json()["state"]
        assert later["soc"] > 40.0

    async def test_the_stop_is_written_to_the_event_log_as_a_pair(
        self, client, db_session
    ):
        """`services/drives.charge_stops` has always paired `charge_start` with
        `charge_end` to report how long real stops take, and nothing ever wrote
        either one — so the panel answered with an empty list for every drive
        since it shipped. The anchor IS that pair."""
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await self._at_a_stop(client, trip, run)
        await client.post(
            f"/api/runs/{run['run_id']}/soc", json={"soc": 71.0, "leaving": True}
        )

        rows = list(
            (
                await db_session.execute(
                    select(TripEvent.kind).order_by(TripEvent.at, TripEvent.id)
                )
            ).scalars()
        )
        assert "charge_start" in rows
        assert "charge_end" in rows
        assert rows.index("charge_start") < rows.index("charge_end")

    async def test_a_watcher_sees_the_session_but_cannot_end_it(self, client):
        trip = await make_trip(client)
        run = await start_run(client, trip)
        await self._at_a_stop(client, trip, run)

        watched = (await client.get(f"/api/trips/{trip['id']}/live")).json()
        assert watched["state"]["charging"] is not None
        assert run["run_id"] not in str(watched)

        resp = await client.post(
            f"/api/trips/{trip['id']}/soc", json={"soc": 71.0, "leaving": True}
        )
        assert resp.status_code == 404
