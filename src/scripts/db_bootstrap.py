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
TARGET_HEAD_REVISION = "0001_initial"


def _make_alembic_config() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", get_sync_database_url())
    return cfg


def _seed_vehicles_if_empty(engine) -> None:
    """Populate the curated vehicle catalog on first boot (no-op otherwise)."""
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from app.models import Vehicle
    from scripts.seed_vehicles import seed

    with Session(engine) as session:
        count = session.execute(select(func.count()).select_from(Vehicle)).scalar_one()
        if count:
            return
        created, updated = seed(session)
        logger.info("Vehicle catalog empty → seeded %d vehicles", created + updated)


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
            _seed_vehicles_if_empty(engine)
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
        _seed_vehicles_if_empty(engine)
        return 0

    except Exception as exc:
        logger.exception("Bootstrap failed: %s", exc)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
