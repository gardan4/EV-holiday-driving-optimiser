"""Invariants that stop the app being trivially abused once it is public.

Each of these pins a hole that was open at some point: they are cheap to
re-open by "simplifying" the code they guard, and expensive to notice, because
nothing breaks — the protection just quietly stops applying.
"""

from types import SimpleNamespace

import pytest

from app.core.rate_limit import _client_ip


def _req(headers: dict[str, str], peer: str = "10.0.0.1"):
    """Enough of a Starlette request for the key function."""
    return SimpleNamespace(
        headers={k.lower(): v for k, v in headers.items()},
        client=SimpleNamespace(host=peer, port=1234),
        scope={"client": (peer, 1234)},
    )


class TestRateLimitKey:
    """Every IP-keyed limit in the app is only as good as this function. If a
    caller can choose their own bucket, the limits are decoration and the free
    ORS/OCM quota is one loop away from gone."""

    def test_a_spoofed_cloudflare_header_cannot_choose_the_bucket(self):
        a = _client_ip(_req({"CF-Connecting-IP": "1.2.3.4"}))
        b = _client_ip(_req({"CF-Connecting-IP": "5.6.7.8"}))
        assert a == b, "caller-supplied CF-Connecting-IP changed the bucket"

    def test_a_spoofed_forwarded_for_prefix_cannot_choose_the_bucket(self):
        # App Service APPENDS the peer it observed, so the attacker's invented
        # left-hand entries have to be ignored.
        a = _client_ip(_req({"X-Forwarded-For": "1.2.3.4, 203.0.113.9:5000"}))
        b = _client_ip(_req({"X-Forwarded-For": "9.9.9.9, 203.0.113.9:5000"}))
        assert a == b == "203.0.113.9"

    def test_two_real_clients_still_get_different_buckets(self):
        a = _client_ip(_req({"X-Forwarded-For": "203.0.113.9:5000"}))
        b = _client_ip(_req({"X-Forwarded-For": "198.51.100.4:5000"}))
        assert a != b, "everyone collapsed into one bucket — limits unusable"

    def test_the_proxy_headers_are_honoured_when_explicitly_trusted(self, monkeypatch):
        from app.core import rate_limit

        monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_HEADERS", True)
        got = rate_limit._client_ip(_req({"CF-Connecting-IP": "1.2.3.4"}))
        assert got == "1.2.3.4"


class TestDocsExposure:
    def test_swagger_is_off_even_under_a_development_env(self):
        """The publicly-reachable deployment runs with ENV=development, because
        it is the environment named "dev". Anything keyed on ENV therefore
        publishes a map of every route — including the live-drive writes — to
        the internet while looking correct in the template. Default-deny."""
        from app.core.config import Settings

        kw = dict(SECRET_KEY="x" * 40, ENCRYPTION_KEY="y" * 40)
        assert Settings(ENV="development", **kw).EXPOSE_DOCS is False
        assert Settings(ENV="production", **kw).EXPOSE_DOCS is False
        assert Settings(ENV="development", EXPOSE_DOCS=True, **kw).EXPOSE_DOCS is True


class TestGeocodeCache:
    """Autocomplete was the only upstream call with no cache, which made it the
    first thing to exhaust the free ORS quota — and the first thing a visitor
    touches, so exhausting it makes the app look broken on arrival."""

    async def test_a_repeated_query_does_not_hit_the_upstream_twice(self, monkeypatch):
        from app.services import routing

        routing._geocode_cache.clear()
        calls = []

        class _Resp:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"features": []}

        class _Client:
            async def get(self, url, **kw):
                calls.append(kw.get("params", {}).get("text"))
                return _Resp()

        monkeypatch.setattr(routing, "_http", lambda: _Client())
        monkeypatch.setattr(routing, "_require_key", lambda: "k")

        await routing.geocode("Utrecht")
        await routing.geocode("utrecht")  # same query, different case
        await routing.geocode("  Utrecht ")  # and whitespace
        assert len(calls) == 1, f"expected 1 upstream call, made {len(calls)}"


class TestCorridorPrefilter:
    """The charger projection was the reason a warm repeat plan measured slower
    than a cold one: O(bbox rows x polyline vertices) of pure Python, on the
    event loop. The prefilter that fixes it must never cost a real charger —
    dropping one silently turns into 'no feasible plan at any speed'."""

    def test_it_never_rejects_a_charger_that_is_actually_on_the_route(self):
        import math
        import random

        from app.services.chargers import MAX_PERP_M, SAMPLE_SPACING_M
        from app.services.geo import RouteGeometry

        random.seed(7)
        coords = []
        lat, lon = 52.09, 5.12
        for _ in range(1500):
            lat -= 0.0012 + random.uniform(-0.0004, 0.0004)
            lon += 0.0016 + random.uniform(-0.0006, 0.0006)
            coords.append((lat, lon))
        geom = RouteGeometry(coords)
        corridor = [(la, lo) for la, lo in geom.sample_every(SAMPLE_SPACING_M)]

        reach_m = SAMPLE_SPACING_M / 2.0 + MAX_PERP_M
        reach = reach_m / 111_320.0

        def survives(clat: float, clon: float) -> bool:
            lon_scale = max(0.2, math.cos(math.radians(clat)))
            for sla, slo in corridor:
                dlat = clat - sla
                if dlat > reach or dlat < -reach:
                    continue
                dlon = (clon - slo) * lon_scale
                if dlat * dlat + dlon * dlon <= reach * reach:
                    return True
            return False

        lats = [a for a, _ in coords]
        lons = [b for _, b in coords]
        pad = 0.1
        dropped = 0
        kept = 0
        for _ in range(6000):
            clat = random.uniform(min(lats) - pad, max(lats) + pad)
            clon = random.uniform(min(lons) - pad, max(lons) + pad)
            near = survives(clat, clon)
            kept += near
            _, perp = geom.project(clat, clon)
            if perp <= MAX_PERP_M and not near:
                dropped += 1

        assert dropped == 0, f"{dropped} on-route chargers wrongly filtered out"
        # And it has to actually save work, or it is pure overhead.
        assert kept < 6000 * 0.25, "prefilter barely narrows the candidate set"


class TestFeedbackNotification:
    """The Discord ping is a convenience. It must never cost the sender their
    message, and it must never carry their email address into a chat channel."""

    async def test_the_email_address_never_reaches_discord(self, monkeypatch):
        from app.api import feedback as fb

        sent = {}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, **kw):
                sent["url"] = url
                sent["json"] = json

                class _R:
                    @staticmethod
                    def raise_for_status():
                        return None

                return _R()

        monkeypatch.setattr(fb.settings, "DISCORD_WEBHOOK_URL", "https://discord/x")
        monkeypatch.setattr(fb.httpx, "AsyncClient", lambda **kw: _Client())

        await fb._notify("the curve looks off", "/trip/abc", has_contact=True)

        blob = str(sent["json"])
        assert "the curve looks off" in blob, "the message should be in the ping"
        assert "someone@example.com" not in blob
        # Only the fact that a reply is wanted travels — never the address.
        assert "address is in the inbox" in blob

    async def test_a_dead_webhook_does_not_raise(self, monkeypatch):
        from app.api import feedback as fb

        class _Boom:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                raise RuntimeError("discord is down")

        monkeypatch.setattr(fb.settings, "DISCORD_WEBHOOK_URL", "https://discord/x")
        monkeypatch.setattr(fb.httpx, "AsyncClient", lambda **kw: _Boom())
        await fb._notify("hello", None, False)  # must not raise

    async def test_nothing_is_sent_when_no_webhook_is_configured(self, monkeypatch):
        from app.api import feedback as fb

        called = False

        def _boom(**kw):
            nonlocal called
            called = True
            raise AssertionError("should not have built a client")

        monkeypatch.setattr(fb.settings, "DISCORD_WEBHOOK_URL", "")
        monkeypatch.setattr(fb.httpx, "AsyncClient", _boom)
        await fb._notify("hello", None, False)
        assert not called
