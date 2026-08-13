"""Composable analytics — the query layer behind the admin dashboard.

`services/usage.py` answers a fixed list of questions and is rebuilt on top of
this. Anything a panel wants that is not expressible here should become a new
primitive, not a new bespoke query — the whole point is that the terminal
report and the dashboard cannot disagree about what a number means.
"""

from app.services.analytics.filters import (
    DIMENSIONS,
    FILTERABLE,
    MAX_DAYS,
    MEASURES,
    BadQuery,
    Filters,
)
from app.services.analytics.queries import (
    FAILURE_EVENT,
    FUNNEL_STAGES,
    Point,
    Slice,
    Stage,
    breakdown,
    compare,
    funnel,
    histogram,
    timeseries,
    totals,
)
from app.services.analytics.retention import Retention, retention

__all__ = [
    "BadQuery",
    "DIMENSIONS",
    "FAILURE_EVENT",
    "FILTERABLE",
    "FUNNEL_STAGES",
    "Filters",
    "MAX_DAYS",
    "MEASURES",
    "Point",
    "Retention",
    "Slice",
    "Stage",
    "breakdown",
    "compare",
    "funnel",
    "histogram",
    "retention",
    "timeseries",
    "totals",
]
