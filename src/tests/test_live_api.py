"""API tests for live journey tracking.

Same shape as `test_api.py`: FastAPI on in-memory SQLite with ORS/OCM stubbed
at the service layer. No network, no MSSQL.

The load-bearing test in here is `TestCapabilities` — the app has no auth, so
the only thing separating "anyone can watch this drive" from "anyone can drive
it" is that the run id never leaves the response that creates it.
"""

from __future__ import annotations

from dataclasses import fields

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.runs import _sim_params
from app.api.schemas import PlanRequest
from app.api.trips import sim_params_for
from app.core.database import Base, get_db
from app.main import app
from app.models import Vehicle
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
