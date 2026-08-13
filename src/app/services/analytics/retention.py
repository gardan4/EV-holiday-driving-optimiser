"""Did anybody come back?

Lifted out of `usage.usage_stats` unchanged in meaning, so the dashboard and
the terminal report keep quoting the same number. The rule it encodes is the
one worth not losing: **"returning" means seen BEFORE the window opened**, not
seen twice inside it. Somebody who lands from a link and reads two pages is one
visit, and counting that as retention flatters every launch spike into looking
like a product.

The denominator is only browsers that sent a persistent id, so anyone in a
private window, with storage cleared, or opted out sits outside it entirely.
That biases toward under-counting returns — a real returner who cleared storage
reads as new — which is the right direction for a number whose whole job is to
stop you believing your own launch.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppEvent
from app.services.analytics.filters import Filters


@dataclass(frozen=True)
class Retention:
    identified: int
    returning: int

    @property
    def rate(self) -> float | None:
        """Quote this, never `returning` alone — it is meaningless without its
        denominator, and the denominator is not the visitor count."""
        return (self.returning / self.identified) if self.identified else None


def _ids(*where):
    # GROUP BY rather than DISTINCT so both sides are subqueries of the same
    # shape and the join below is plain SQL on either engine.
    return (
        select(AppEvent.client_id)
        .where(AppEvent.client_id.isnot(None), *where)
        .group_by(AppEvent.client_id)
        .subquery()
    )


async def retention(db: AsyncSession, f: Filters) -> Retention:
    in_window = _ids(AppEvent.at >= f.since, AppEvent.at < f.until)
    before_window = _ids(AppEvent.at < f.since)

    identified = (
        await db.execute(select(func.count()).select_from(in_window))
    ).scalar_one()
    returning = (
        await db.execute(
            select(func.count())
            .select_from(in_window)
            .join(before_window, in_window.c.client_id == before_window.c.client_id)
        )
    ).scalar_one()
    return Retention(identified=int(identified), returning=int(returning))
