"""Counting what we spend at ORS and OpenChargeMap, and how close the ceiling is.

The caches in `routing.py` and `chargers.py` exist to keep this app inside two
free tiers. They were doing it blind: the HIT/MISS decisions went to the log and
nothing added them up, so "will a busy day exhaust the quota?" had no answer
until it already had.

Writing side is `record`, called from the three places that can reach an
external API. Reading side is `quota_report`, shared by `services.usage` (so the
dashboard) and the terminal report.

**Recording never fails a request.** A telemetry row is worth strictly less
than the trip plan it is measuring, so every write is wrapped and a failure is
logged and swallowed. The counter going missing is a bad afternoon; a planning
request dying because the counter could not be written is a bad product.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import UpstreamCall

logger = logging.getLogger(__name__)

# What each provider allows per day on the free tier. These are the numbers the
# headroom figure is measured against, so they are settings rather than
# constants — a plan change upstream should not need a code change here.
#
# 0 means "no published daily ceiling": the calls are still counted and the hit
# rate still computed, only the headroom percentage is withheld. Inventing a
# denominator would produce a reassuring bar with nothing behind it.
def _quota(provider: str) -> int:
    return {
        "ors": settings.ORS_DAILY_QUOTA,
        "ocm": settings.OCM_DAILY_QUOTA,
    }.get(provider, 0)


async def record(
    db: AsyncSession,
    *,
    provider: str,
    kind: str,
    calls: int = 0,
    cache_hits: int = 0,
    duration_ms: int = 0,
    ok: bool = True,
) -> None:
    """Add one row for one app-level operation.

    Deliberately does NOT commit: the callers are mid-transaction (a route
    fetch commits its own cache row moments later), and committing here would
    split that into two transactions for the sake of a counter.
    """
    try:
        db.add(
            UpstreamCall(
                provider=provider,
                kind=kind,
                calls=max(0, calls),
                cache_hits=max(0, cache_hits),
                duration_ms=max(0, int(duration_ms)),
                ok=ok,
            )
        )
    except Exception:  # noqa: BLE001 — never break the request being measured
        logger.warning("could not record upstream call for %s", provider, exc_info=True)


class ProviderUsage:
    """What one provider cost today, and how much room is left."""

    def __init__(
        self, provider: str, calls_today: int, cache_hits_today: int,
        failures_today: int, avg_ms: float, calls_window: int,
    ):
        self.provider = provider
        self.calls_today = calls_today
        self.cache_hits_today = cache_hits_today
        self.failures_today = failures_today
        self.avg_ms = avg_ms
        self.calls_window = calls_window
        self.daily_quota = _quota(provider)

    @property
    def hit_rate(self) -> float | None:
        total = self.calls_today + self.cache_hits_today
        return (self.cache_hits_today / total) if total else None

    @property
    def headroom_pct(self) -> float | None:
        """How much of today's allowance is still unspent, or None when the
        provider publishes no daily ceiling."""
        if not self.daily_quota:
            return None
        return max(0.0, 100.0 * (1.0 - self.calls_today / self.daily_quota))


async def quota_report(db: AsyncSession, days: int = 7) -> list[ProviderUsage]:
    """Today's spend per provider, plus the window total for context.

    "Today" is the unit that matters because the ceilings are daily and reset
    at midnight UTC — a weekly total cannot tell you whether tonight's post
    will run the tank dry.
    """
    now = datetime.utcnow()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_start = now - timedelta(days=max(1, days))

    out: list[ProviderUsage] = []
    for provider in ("ors", "ocm"):
        today = (
            await db.execute(
                select(
                    func.coalesce(func.sum(UpstreamCall.calls), 0),
                    func.coalesce(func.sum(UpstreamCall.cache_hits), 0),
                    func.count(),
                    # Cast before averaging: `func.avg` over an Integer column
                    # does integer division on MSSQL and returns a float on
                    # SQLite, so the two engines would disagree.
                    func.coalesce(func.avg(cast(UpstreamCall.duration_ms, Float)), 0.0),
                ).where(UpstreamCall.at >= midnight, UpstreamCall.provider == provider)
            )
        ).one()
        failures = (
            await db.execute(
                select(func.count()).where(
                    UpstreamCall.at >= midnight,
                    UpstreamCall.provider == provider,
                    UpstreamCall.ok == False,  # noqa: E712 — `.is_(False)` is a T-SQL syntax error
                )
            )
        ).scalar_one()
        window = (
            await db.execute(
                select(func.coalesce(func.sum(UpstreamCall.calls), 0)).where(
                    UpstreamCall.at >= window_start, UpstreamCall.provider == provider
                )
            )
        ).scalar_one()

        out.append(
            ProviderUsage(
                provider=provider,
                calls_today=int(today[0]),
                cache_hits_today=int(today[1]),
                failures_today=int(failures),
                avg_ms=float(today[3]),
                calls_window=int(window),
            )
        )
    return out
