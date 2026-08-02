"""The stamp revision must track the migration chain's head.

Regression: db_bootstrap creates a fresh schema with ``create_all()``, which
materialises whatever the models currently describe — the LATEST schema. It was
stamping a hardcoded ``0001_initial``, so the next container start ran
``alembic upgrade head``, replayed 0002's ADD COLUMN against columns that
already existed, and exited 1. On Azure that was a crash loop; locally it hid,
because the DB there predated the columns and the migration applied cleanly.
"""

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import Vehicle
from scripts.db_bootstrap import (
    TARGET_HEAD_REVISION,
    _make_alembic_config,
    _sync_vehicle_catalog,
)
from scripts.seed_vehicles import VEHICLES


def test_fresh_db_is_stamped_at_the_migration_head() -> None:
    script = ScriptDirectory.from_config(_make_alembic_config())
    head = script.get_current_head()

    resolved = (
        head
        if TARGET_HEAD_REVISION == "head"
        else script.get_revision(TARGET_HEAD_REVISION).revision
    )
    assert resolved == head, (
        f"db_bootstrap stamps {TARGET_HEAD_REVISION!r} (-> {resolved}) but the "
        f"migration head is {head}. create_all() builds the latest schema, so a "
        "stale stamp makes the next boot replay migrations that already applied."
    )


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def test_catalog_sync_reaches_an_already_populated_database(engine) -> None:
    """The bug: seeding only ran when the table was empty.

    A deployed database is never empty after its first boot, so every later
    change to the curated catalog — ten new cars, a corrected charge curve —
    stopped at the door. Nothing short of running the seeder by hand against
    production could move it.
    """
    with Session(engine) as s:
        s.add(Vehicle(**{**VEHICLES[0], "usable_kwh": 1.0, "max_dc_kw": 1.0}))
        s.commit()
        stale_id = s.execute(select(Vehicle.id)).scalar_one()

    _sync_vehicle_catalog(engine)

    with Session(engine) as s:
        assert s.execute(select(func.count()).select_from(Vehicle)).scalar_one() == len(
            VEHICLES
        ), "every curated car must land, not just the ones that were missing"
        row = s.execute(
            select(Vehicle).where(Vehicle.slug == VEHICLES[0]["slug"])
        ).scalar_one()
        assert row.usable_kwh == VEHICLES[0]["usable_kwh"], "stale row must be corrected"
        assert row.max_dc_kw == VEHICLES[0]["max_dc_kw"]
        assert row.id == stale_id, (
            "the row must be updated in place — trips.vehicle_id points at this id, "
            "so replacing it would orphan every permalink planned with that car"
        )


def test_catalog_sync_is_idempotent(engine) -> None:
    _sync_vehicle_catalog(engine)
    with Session(engine) as s:
        ids = set(s.execute(select(Vehicle.id)).scalars())

    _sync_vehicle_catalog(engine)  # a restart, a redeploy, a scale event
    with Session(engine) as s:
        assert set(s.execute(select(Vehicle.id)).scalars()) == ids
        assert (
            s.execute(select(func.count()).select_from(Vehicle)).scalar_one()
            == len(VEHICLES)
        ), "re-running must not duplicate the catalog"


def test_catalog_sync_never_takes_the_api_down(caplog) -> None:
    """Best-effort: the schema is fatal, the car list is not.

    This file's history is a crash loop on boot. An API serving nine cars beats
    an API serving none, so a broken sync must log and stand down.
    """
    broken = create_engine("sqlite://")  # no create_all -> "no such table: vehicles"
    try:
        with caplog.at_level("ERROR", logger="db_bootstrap"):
            _sync_vehicle_catalog(broken)  # must not raise
        assert any(
            "catalog sync failed" in r.message for r in caplog.records
        ), "a swallowed failure has to be loud, or a stale catalog is invisible"
    finally:
        broken.dispose()
