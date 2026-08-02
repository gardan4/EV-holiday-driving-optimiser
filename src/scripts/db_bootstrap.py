"""Startup-time DB bootstrap.

Replaces the bare ``alembic upgrade head`` in the container CMD. Handles
three cases so the same image works on prod (existing schema), on dev
(freshly-provisioned empty DB), and on restart (already at head):

1. **alembic_version table missing** → truly fresh DB. Run
   ``Base.metadata.create_all()`` to materialize the current schema,
   then stamp Alembic at the 0001_initial revision so future
   migrations apply incrementally.

2. **alembic_version table present** → established DB. Run
   ``alembic upgrade head`` (skips already-applied migrations).

3. **Anything goes wrong** → log + exit non-zero so App Service surfaces
   the failure clearly (rather than the container dying inside the
   migration mid-startup).

Then, on every boot, it reconciles the curated vehicle catalog with the
repo. That step is best-effort: the schema must be right or nothing works,
but the app serves perfectly well with a stale car list, and this file has
already cost one crash loop.
"""

from __future__ import annotations

import sys
import logging

from sqlalchemy import inspect

from alembic import command
from alembic.config import Config

from app.core.config import settings
from app.core.database import Base, get_sync_database_url
from app import models  # noqa: F401 — load all models onto Base.metadata


logger = logging.getLogger("db_bootstrap")
# create_all() materialises whatever the models currently describe — i.e. the
# LATEST schema — so a freshly created DB must be stamped at "head", not at a
# pinned revision. Pinning it to 0001_initial meant the next boot ran
# `upgrade head`, replayed 0002's ADD COLUMN against columns create_all had
# already made, and the container exited 1 in a crash loop.
TARGET_HEAD_REVISION = "head"


def _make_alembic_config() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", get_sync_database_url())
    return cfg


def _sync_vehicle_catalog(engine) -> None:
    """Reconcile the curated vehicle catalog with the repo, on every boot.

    The catalog is seed data, not user data: `scripts/seed_vehicles.py` is the
    single source of truth and `seed()` is an idempotent upsert by slug. This
    used to run only when the table was empty, which meant a deployed database
    was frozen at whatever it was first seeded with — ten new cars, or a
    corrected charge curve, could never reach it without someone running the
    seeder by hand against production. Since the upsert is idempotent, gating
    it on emptiness bought nothing and cost exactly that.

    Rows are never deleted. `trips.vehicle_id` references them, and a shared
    permalink must keep resolving to the car it was planned with, so a vehicle
    dropped from VEHICLES stays in the table and simply stops being offered.

    Best-effort by design. A stale car list is a much smaller problem than an
    API that will not start, so a failure here is logged loudly and swallowed —
    unlike the migration above, which is fatal.
    """
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    from scripts.seed_vehicles import seed

    # Two containers restarting together can both find a slug missing and both
    # insert it; the loser trips the unique index. Retrying is enough — the row
    # exists by then, so the second pass takes the update branch.
    for attempt in (1, 2):
        try:
            with Session(engine) as session:
                created, updated = seed(session)
            logger.info(
                "Vehicle catalog synced: %d created, %d updated", created, updated
            )
            return
        except IntegrityError:
            if attempt == 2:
                logger.exception("Vehicle catalog sync lost a race twice — skipping")
                return
            logger.warning("Vehicle catalog sync raced another boot; retrying")
        except Exception:
            logger.exception("Vehicle catalog sync failed — serving the existing list")
            return


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="db_bootstrap: %(message)s")
    if not settings.DATABASE_URL:
        logger.error("DATABASE_URL is not set")
        return 2

    url = get_sync_database_url()
    from sqlalchemy import create_engine

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        has_alembic = inspector.has_table("alembic_version")

        if has_alembic:
            logger.info("alembic_version present → running upgrade head")
            cfg = _make_alembic_config()
            command.upgrade(cfg, "head")
            logger.info("alembic upgrade head complete")
            _sync_vehicle_catalog(engine)
            return 0

        logger.info(
            "alembic_version missing → fresh DB. Creating schema via "
            "create_all and stamping at %s",
            TARGET_HEAD_REVISION,
        )
        Base.metadata.create_all(engine)
        cfg = _make_alembic_config()
        command.stamp(cfg, TARGET_HEAD_REVISION)
        logger.info("Fresh DB bootstrapped at revision %s", TARGET_HEAD_REVISION)
        _sync_vehicle_catalog(engine)
        return 0

    except Exception as exc:
        logger.exception("Bootstrap failed: %s", exc)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
