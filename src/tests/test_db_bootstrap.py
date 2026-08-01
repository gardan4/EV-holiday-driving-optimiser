"""The stamp revision must track the migration chain's head.

Regression: db_bootstrap creates a fresh schema with ``create_all()``, which
materialises whatever the models currently describe — the LATEST schema. It was
stamping a hardcoded ``0001_initial``, so the next container start ran
``alembic upgrade head``, replayed 0002's ADD COLUMN against columns that
already existed, and exited 1. On Azure that was a crash loop; locally it hid,
because the DB there predated the columns and the migration applied cleanly.
"""

from alembic.script import ScriptDirectory

from scripts.db_bootstrap import TARGET_HEAD_REVISION, _make_alembic_config


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
