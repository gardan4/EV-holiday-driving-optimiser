"""What OpenStreetMap maps around a charger.

The food hint was a reading of the site's NAME, which is the only text
OpenChargeMap gives us — and on a motorway corridor every fast charger is
called "Tesla Supercharger Geiselwind" or "IONITY Holzkirchen Süd". Network
plus place, saying nothing, for an entire list. Geiselwind is an Autohof with a
kitchen and the name has no way to say so.

So the question moves off the name and onto the ground: what is mapped within a
few hundred metres. Two nullable columns on the existing cache row rather than
a table of their own — it is one list per charger, read on exactly the same
path as the charger, and a join would buy nothing.

`amenities` NULL means never looked; `[]` means looked and found nothing
mapped. Those are different answers and the UI keeps them apart.

Revision ID: 0013_charger_amenities
Revises: 0012_owner_profiles
"""

from alembic import op
import sqlalchemy as sa

revision = "0013_charger_amenities"
down_revision = "0012_owner_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chargers", sa.Column("amenities", sa.JSON(), nullable=True))
    op.add_column(
        "chargers", sa.Column("amenities_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("chargers", "amenities_at")
    op.drop_column("chargers", "amenities")
