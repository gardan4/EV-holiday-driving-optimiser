"""The segment a dashboard panel is looking at, and the columns it may use.

`services/usage.py` answers one fixed question per card. This answers an
arbitrary one — "plans from Reddit, on mobile, in Germany" — which means the
dimension and measure names arrive from a browser. So the whole surface is an
allowlist, for the same reason `usage.ALLOWED_EVENTS` and `events.KNOWN_PATHS`
are: a filter API reachable from a client must not become free-form SQL, and
default-deny is the only version of that which stays true as columns are added.

Nothing here reads a column the events table does not already hold. The
dashboard is a new way to look at the data, not a new thing to collect — which
is why `frontend/app/privacy/page.tsx` needs no edit for any of this.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

# `DATE` (the SQL-standard type), not `Date`. SQLAlchemy's `Date` renders as
# CAST(x AS DATETIME) whenever the MSSQL dialect cannot read server_version_info
# — it assumes a pre-2008 server with no DATE type — and a cast to DATETIME
# does not truncate, so every row would land in its own "day" bucket. Azure SQL
# is always new enough; the uppercase type says so unconditionally.
from sqlalchemy import DATE, cast, func, text

from app.models import AppEvent

logger = logging.getLogger(__name__)

# Dimensions you may group by or filter on. Every one is low-cardinality by
# construction (`core.client_context` buckets them before they are stored), so
# a GROUP BY is over a handful of values rather than over user-supplied text.
#
# `visitor` and `client_id` are deliberately absent. They are pseudonyms, and a
# GROUP BY on one turns a counting table into a per-person list — which is the
# exact thing the salted-daily-hash design exists to prevent.
DIMENSIONS: dict[str, object] = {
    "name": AppEvent.name,
    "path": AppEvent.path,
    "referrer": AppEvent.referrer,
    "referrer_path": AppEvent.referrer_path,
    "country": AppEvent.country,
    "device": AppEvent.device,
    "browser": AppEvent.browser,
    "os": AppEvent.os,
    "viewport": AppEvent.viewport,
    "reason": AppEvent.reason,
    "campaign": AppEvent.campaign,
}

# Which of those may also be constrained. Same list minus `name` and `reason`,
# which are the event's own identity rather than a property of the visit — the
# funnel needs to see every stage at once, so letting a filter pin `name` would
# make every funnel panel show a single bar.
FILTERABLE = frozenset(DIMENSIONS) - {"name", "reason"}

MEASURES = frozenset({"events", "visitors", "clients"})

# Matches `usage.MAX_DAYS`. The dashboard reads the live Azure SQL instance;
# an unbounded scan of the busiest table is not something a UI control should
# be able to ask for.
MAX_DAYS = 90
MAX_LIMIT = 50


class BadQuery(ValueError):
    """An unknown dimension, measure or granularity. Becomes a 400."""


@dataclass(frozen=True)
class Filters:
    """A window plus zero or more equality constraints.

    Frozen because panels share one instance per render and `previous()` derives
    from it — a panel that mutated the filter would silently retitle its
    neighbours.
    """

    since: datetime
    until: datetime
    device: str | None = None
    browser: str | None = None
    os: str | None = None
    country: str | None = None
    campaign: str | None = None
    viewport: str | None = None
    path: str | None = None
    referrer: str | None = None
    referrer_path: str | None = None

    @property
    def days(self) -> int:
        return max(1, (self.until - self.since).days)

    @classmethod
    def window(cls, days: int = 7, **kw) -> "Filters":
        days = max(1, min(int(days), MAX_DAYS))
        now = datetime.utcnow()
        return cls(since=now - timedelta(days=days), until=now, **kw)

    def previous(self) -> "Filters":
        """The same span immediately before this one, for period-over-period.

        Anchored on the span's own length rather than on `days`, so an hourly
        window compares against the previous hours and not the previous day.
        """
        span = self.until - self.since
        return replace(self, since=self.since - span, until=self.since)

    def active(self) -> dict[str, str]:
        """The constraints actually set, for echoing back to the UI as chips."""
        return {
            key: value
            for key in sorted(FILTERABLE)
            if (value := getattr(self, key, None)) is not None
        }

    def clauses(self) -> list:
        """SQLAlchemy WHERE clauses for this segment, window included."""
        where = [AppEvent.at >= self.since, AppEvent.at < self.until]
        for key, value in self.active().items():
            where.append(DIMENSIONS[key] == value)
        return where


def dimension(name: str):
    """Look up a groupable column, or refuse."""
    try:
        return DIMENSIONS[name]
    except KeyError:
        raise BadQuery(
            f"Unknown dimension {name!r}. Known: {', '.join(sorted(DIMENSIONS))}."
        ) from None


def measure(name: str):
    """Build the aggregate for a measure name.

    `visitors` counts the daily-rotating pseudonym and `clients` the persistent
    one, so the two disagree on purpose — over a week the first is visitor-days
    and the second is closer to people. Neither is "users"; see the note in
    `usage.usage_stats`.
    """
    if name == "events":
        return func.count()
    if name == "visitors":
        return func.count(func.distinct(AppEvent.visitor))
    if name == "clients":
        return func.count(func.distinct(AppEvent.client_id))
    raise BadQuery(f"Unknown measure {name!r}. Known: {', '.join(sorted(MEASURES))}.")


def day_bucket(dialect: str):
    """Truncate `AppEvent.at` to a day, on whichever engine we are on.

    `usage.usage_stats` runs one SELECT per day instead of grouping, because
    MSSQL and SQLite share no date-truncation function and `CAST(x AS DATE)`
    means different things on each — SQLite gives CAST numeric affinity, so
    casting a timestamp to DATE there yields the leading year as a number
    rather than a date. That is tolerable for one capped 90-row loop and not
    tolerable for a dashboard that redraws every panel on every filter change,
    so the branch that was avoided gets written down here once instead.

    The two branches return different Python types (str on SQLite, `date` on
    MSSQL) — `normalize_bucket` is what makes them one thing again.
    """
    if dialect == "sqlite":
        return func.strftime("%Y-%m-%d", AppEvent.at)
    return cast(AppEvent.at, DATE)


def hour_bucket(dialect: str):
    """As `day_bucket`, to the hour — what launch day is actually watched in.

    The MSSQL side is the standard truncation idiom: count whole hours since
    the zero epoch and add them back, which floors without string formatting.
    """
    if dialect == "sqlite":
        return func.strftime("%Y-%m-%dT%H:00", AppEvent.at)
    return func.dateadd(
        text("hour"),
        func.datediff(text("hour"), text("0"), AppEvent.at),
        text("0"),
    )


def bucket(granularity: str, dialect: str):
    if granularity == "day":
        return day_bucket(dialect)
    if granularity == "hour":
        return hour_bucket(dialect)
    raise BadQuery(f"Unknown granularity {granularity!r}. Known: day, hour.")


def normalize_bucket(value, granularity: str) -> str:
    """One ISO string per bucket, whichever engine produced it.

    Without this the same window renders as "2026-08-12" locally and
    "2026-08-12 00:00:00" in production, and the chart's x-axis quietly changes
    shape between the test engine and the real one.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        # SQLite's strftime already emits exactly the shape we want.
        return value if granularity == "hour" else value[:10]
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:00") if granularity == "hour" else value.date().isoformat()
    # datetime.date from the MSSQL day branch.
    return value.isoformat()


def clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def dialect_of(db) -> str:
    """The engine name, for the bucket branch. Defaults to the strict one.

    Falling back to the MSSQL expression rather than SQLite's is deliberate: if
    the bind is ever unreadable, an error on an unknown engine is better than
    silently emitting `strftime` against production and getting no rows.
    """
    try:
        return db.get_bind().dialect.name
    except Exception:  # pragma: no cover — bind is always present in practice
        logger.warning("Could not read dialect from session; assuming mssql")
        return "mssql"
