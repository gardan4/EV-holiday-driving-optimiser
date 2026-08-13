"""What happened *after* the plan.

`trip_stats` records what the planner promised. `trip_runs` and `trip_events`
record what the car actually did, and until now nothing read them — every live
drive ever taken sat in the database unexamined. That is the only evidence in
the system that the model is right about the real world, which makes it the
most valuable thing on the dashboard and the least visible.

Three deliberate limits, so nobody reads more into these numbers than they hold:

- **Runs are purged at 90 days** (`main._purge_loop`), so anything here is a
  rolling window by construction and cannot be compared year-on-year the way
  `trip_stats` can.
- **`abandoned` does not mean the drive failed.** It means the phone stopped
  reporting — a dead battery, a closed tab, a tunnel at the wrong moment. It is
  a lower bound on completion, not a defect count.
- **Charge durations come from paired events**, and a run that ends mid-charge
  has a `charge_start` with no `charge_end`. Those are dropped rather than
  closed at the run's end: inventing an end time would quietly invent the one
  number the pair exists to measure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TripEvent, TripRun


@dataclass(frozen=True)
class DriveSummary:
    runs: int
    finished: int
    active: int
    abandoned: int
    # Median rather than mean: one drive left running overnight in a car park
    # drags a mean into meaninglessness, and there are few enough runs that a
    # single outlier is a large share of them.
    median_minutes: float | None
    total_replans: int
    runs_with_replan: int
    runs_off_route: int
    median_pings: float | None

    @property
    def completion_rate(self) -> float | None:
        """Finished as a share of runs that are no longer active.

        Active runs are excluded from the denominator rather than counted as
        failures — a drive still in progress has not not-completed, and
        including them makes the rate sag every time somebody is mid-journey.
        """
        settled = self.finished + self.abandoned
        return (self.finished / settled) if settled else None

    @property
    def replan_rate(self) -> float | None:
        return (self.runs_with_replan / self.runs) if self.runs else None

    @property
    def off_route_rate(self) -> float | None:
        return (self.runs_off_route / self.runs) if self.runs else None


@dataclass(frozen=True)
class ChargeStop:
    """One completed charge, as actually driven."""

    minutes: float
    soc_start: float | None
    soc_end: float | None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _window(q, cutoff: datetime | None):
    return q if cutoff is None else q.where(TripRun.started_at >= cutoff)


async def summary(db: AsyncSession, cutoff: datetime | None) -> DriveSummary:
    rows = (
        await db.execute(
            _window(
                select(
                    TripRun.status,
                    func.count(),
                    # cast before avg: `func.avg` over an Integer column does
                    # integer division on MSSQL and returns a float on SQLite,
                    # so the same query answers differently on the test engine
                    # than in production.
                    func.sum(cast(TripRun.n_replans, Float)),
                    func.sum(cast(TripRun.n_pings, Float)),
                ).group_by(TripRun.status),
                cutoff,
            )
        )
    ).all()
    by_status = {str(s): int(n) for s, n, _, _ in rows}
    total_replans = int(sum((r[2] or 0) for r in rows))

    # Durations and per-run counters need the rows themselves, not aggregates,
    # because the interesting statistics are medians. Runs are purged at 90
    # days and a drive is a rare event, so this stays a small result set.
    detail = (
        await db.execute(
            _window(
                select(
                    TripRun.id,
                    TripRun.started_at,
                    TripRun.finished_at,
                    TripRun.n_replans,
                    TripRun.n_pings,
                ),
                cutoff,
            )
        )
    ).all()

    durations = [
        (fin - start).total_seconds() / 60.0
        for _, start, fin, _, _ in detail
        if fin and start and fin > start
    ]
    runs_with_replan = sum(1 for _, _, _, n, _ in detail if (n or 0) > 0)
    pings = [float(p or 0) for *_, p in detail]

    run_ids = [r[0] for r in detail]
    off_route = 0
    if run_ids:
        off_route = int(
            (
                await db.execute(
                    select(func.count(func.distinct(TripEvent.run_id))).where(
                        TripEvent.kind == "off_route",
                        TripEvent.run_id.in_(run_ids),
                    )
                )
            ).scalar_one()
            or 0
        )

    return DriveSummary(
        runs=len(detail),
        finished=by_status.get("finished", 0),
        active=by_status.get("active", 0),
        abandoned=by_status.get("abandoned", 0),
        median_minutes=_median(durations),
        total_replans=total_replans,
        runs_with_replan=runs_with_replan,
        runs_off_route=off_route,
        median_pings=_median(pings),
    )


async def charge_stops(db: AsyncSession, cutoff: datetime | None) -> list[ChargeStop]:
    """Real charge-stop durations, by pairing start/end events within a run.

    Paired in Python rather than with a window function: MSSQL and SQLite both
    have `LAG`, but the pairing rule ("the next charge_end after this
    charge_start, in the same run, if there is one") is the kind of thing that
    is much easier to get right — and to read — as a loop over an ordered list
    than as SQL. The event log is deliberately sparse (see `TripEvent`), so the
    scan is small.
    """
    runs = _window(select(TripRun.id), cutoff).subquery()
    rows = (
        await db.execute(
            select(TripEvent.run_id, TripEvent.at, TripEvent.kind, TripEvent.soc)
            .where(
                TripEvent.kind.in_(("charge_start", "charge_end")),
                TripEvent.run_id.in_(select(runs.c.id)),
            )
            .order_by(TripEvent.run_id, TripEvent.at)
        )
    ).all()

    out: list[ChargeStop] = []
    open_start: dict = {}
    for run_id, at, kind, soc in rows:
        if kind == "charge_start":
            # A second start with no end between them means the first was never
            # closed — the run stopped reporting mid-charge. Drop it.
            open_start[run_id] = (at, soc)
        elif (started := open_start.pop(run_id, None)) is not None:
            began, soc_start = started
            minutes = (at - began).total_seconds() / 60.0
            if minutes >= 0:
                out.append(
                    ChargeStop(minutes=minutes, soc_start=soc_start, soc_end=soc)
                )
    return out


async def plan_vs_actual(
    db: AsyncSession, cutoff: datetime | None
) -> list[tuple[float, float]]:
    """(planned cruise speed, achieved average speed) per finished run.

    The achieved figure comes from the run's own last known distance offset
    over its wall-clock duration, so it INCLUDES charging and rest time — it is
    a journey average, not a driving average, and will sit well below the
    planned cruise speed for any trip that stopped. That gap is the point: it
    is the same quantity the planner's total-time curve is built on, so a
    systematic difference here is the model being wrong about the real world.
    """
    rows = (
        await db.execute(
            _window(
                select(
                    TripRun.planned_speed_kph,
                    TripRun.started_at,
                    TripRun.finished_at,
                    TripRun.state,
                ).where(TripRun.status == "finished"),
                cutoff,
            )
        )
    ).all()

    out: list[tuple[float, float]] = []
    for planned, started, finished, state in rows:
        if not (planned and started and finished and finished > started):
            continue
        offset_m = (state or {}).get("offset_m") if isinstance(state, dict) else None
        if not offset_m:
            continue
        hours = (finished - started).total_seconds() / 3600.0
        if hours <= 0:
            continue
        out.append((float(planned), (float(offset_m) / 1000.0) / hours))
    return out
