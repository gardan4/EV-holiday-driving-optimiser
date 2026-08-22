"""Failures that say why, in one word you can count.

`plan_failed` recorded that planning broke and never what broke, which made the
most valuable table on launch day — "what did people ask for that we couldn't
do" — unanswerable. The answer is not in the message: those are written for the
person reading them and get reworded, so counting them would produce a chart
that reorganises itself whenever someone improves the copy.

So the reason travels on the exception, as a short allowlisted slug, and the
message stays free to be human.

`reason` is checked against `PLAN_FAILURE_REASONS` before storage for the same
reason every other label in this app is: it should stay possible to read one
list and know every value the column can hold.
"""

from __future__ import annotations

from fastapi import HTTPException

# Every way planning can fail, and nothing else. Grouped by who has to act:
#
#   the request      — the caller asked for something we don't do
#   the world        — we tried and the route or the chargers weren't there
#   us               — configuration or an upstream provider let us down
#
# The middle group is the interesting one on launch day: it is demand we could
# not serve, which is a roadmap rather than a bug list.
PLAN_FAILURE_REASONS = frozenset(
    {
        # The request
        "bad_speed_range",   # speed_max below speed_min
        "sweep_too_large",   # more simulated speeds than we allow
        "bad_soc",           # departure charge below the arrival target
        "bad_vehicle",       # unparseable vehicle id
        "unknown_vehicle",   # parseable, but not in the catalog
        "route_too_long",    # beyond MAX_ROUTE_M
        "bad_request",       # request-side, but nobody labelled it — see reason_of
        # The world
        "no_route",          # ORS can't drive between these two points
        "no_chargers",       # no feasible plan at any speed
        "route_too_short",   # …but on a hop with no room to put a stop in
        "networks_excluded",  # …and it was the driver's own network exclusions
        "corridor_cold",     # charger tiles still being gathered — retryable
        # Us
        "not_configured",    # ORS/OCM key missing
        "upstream_error",    # ORS/OCM refused or timed out (a blown quota looks like this)
        "server_error",      # anything unclassified — should stay near zero
    }
)


class PlanError(HTTPException):
    """An HTTPException carrying a countable reason.

    Subclasses rather than replaces `HTTPException` so every existing handler,
    and FastAPI's own, keep working untouched — the reason is extra, not a new
    protocol.
    """

    def __init__(self, reason: str, status_code: int, detail: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.reason = reason


def reason_of(exc: BaseException) -> str:
    """The reason on an exception, or a sensible guess from its status code.

    Guessing rather than dropping the row: an unclassified failure is still a
    failure, and a funnel that silently ignores the ones nobody labelled would
    under-report exactly when something new is going wrong.
    """
    reason = getattr(exc, "reason", None)
    if isinstance(reason, str) and reason in PLAN_FAILURE_REASONS:
        return reason
    status = getattr(exc, "status_code", 500)
    if status == 503:
        return "upstream_error"
    if status == 404:
        return "unknown_vehicle"
    if status == 422:
        return "bad_request"
    return "server_error"
