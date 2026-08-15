"""Shared test configuration.

Two pieces of infrastructure are switched off for the whole suite.

**Overpass**, because the tests must not touch the network. `amenities.nearby`
already refuses to look up a charger it has no cache row for, which covers the
synthetic routes these tests build — but a setting is the guarantee, and a test
that wants the lookup turns it back on for itself.

**Rate limiting**, because slowapi's in-process store
is shared by every test in the run, so `RATE_LIMIT_PLAN` ("10/minute") is a
budget spread across the entire file rather than per test — the eleventh test
that plans a trip gets a 429 and fails somewhere unrelated to what it was
checking. It is infrastructure, not behaviour under test, and no test should
depend on a wall-clock window.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.rate_limit import limiter


@pytest.fixture(autouse=True)
def _no_upstream_amenities():
    was = settings.OVERPASS_ENABLED
    settings.OVERPASS_ENABLED = False
    yield
    settings.OVERPASS_ENABLED = was


@pytest.fixture(autouse=True)
def _no_rate_limiting():
    was = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = was
