"""Shared test configuration.

Rate limiting is switched off for the whole suite. slowapi's in-process store
is shared by every test in the run, so `RATE_LIMIT_PLAN` ("10/minute") is a
budget spread across the entire file rather than per test — the eleventh test
that plans a trip gets a 429 and fails somewhere unrelated to what it was
checking. It is infrastructure, not behaviour under test, and no test should
depend on a wall-clock window.
"""

from __future__ import annotations

import pytest

from app.core.rate_limit import limiter


@pytest.fixture(autouse=True)
def _no_rate_limiting():
    was = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = was
