"""Turning a request into a number you can count, and nothing else.

Counting unique visitors needs *some* way to tell two people apart. Storing the
IP would do it and is the one thing we are not willing to store — so this hashes
the IP together with a salt that changes every midnight UTC.

Two properties do the work, and both matter:

* **It cannot be reversed.** The salt includes `SECRET_KEY`, so the hash is not
  guessable by anyone who doesn't have it. Without a secret in there, IPv4 is a
  32-bit space and a rainbow table over the whole internet is an afternoon's
  work — a plain `sha256(ip)` is a pseudonym in name only.
* **It cannot be followed.** The salt rotates daily, so the same person on
  Tuesday and Wednesday produces two unrelated values with nothing linking them.
  That makes "unique visitors today" answerable and "what has this person done
  over the past month" unanswerable.

The second question turned out to be worth answering — "did anyone come back?"
is the difference between a busy afternoon and a product — so `client_hash`
below adds a stable pseudonym alongside this one. The two are deliberately
separate and neither replaces the other: the daily hash is derived from the
request and so counts everybody, including browsers that send nothing; the
persistent one is a random id the browser generates and can clear, and exists
only when the visitor's client actually cooperates. A feature wanting a stable
id reaches for `client_hash`, never for this function.

The user agent goes in as well. It costs nothing, and it separates the several
people who genuinely share one address behind a household NAT or a carrier's
CGNAT — the same CGNAT problem `rate_limit.run_key` already works around.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime

from starlette.requests import Request

from app.core.config import settings
from app.core.rate_limit import _client_ip


def visitor_hash(ip: str, user_agent: str, day: date | None = None) -> str:
    """A 128-bit pseudonym for (ip, user agent) that is only valid for `day`."""
    if day is None:
        day = datetime.utcnow().date()
    # SECRET_KEY is validated non-default at startup (core.config), so this is
    # never salted with a known constant.
    material = f"{settings.SECRET_KEY}:{day.isoformat()}:{ip}:{user_agent}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def request_visitor(request: Request) -> str:
    """`visitor_hash` for the caller of this request.

    Reuses `rate_limit._client_ip` rather than reading `X-Forwarded-For` again.
    The header-trust rules there are subtle and got that way for a reason; a
    second, slightly different copy of them is how the two drift apart.
    """
    return visitor_hash(_client_ip(request), request.headers.get("user-agent", ""))


# ---------------------------------------------------------------------------
# The persistent id — the thing `visitor_hash` deliberately refuses to be
# ---------------------------------------------------------------------------

# A v4 UUID as `crypto.randomUUID()` writes it. Strict on purpose: this arrives
# on an unauthenticated public endpoint, so the only safe posture is that
# anything not matching exactly is discarded rather than stored. Without this,
# a header that gets written to the database is free storage for whatever a
# caller feels like putting in it.
_CLIENT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

CLIENT_ID_HEADER = "x-client-id"


def client_hash(raw: str) -> str:
    """A stable 128-bit pseudonym for a browser's own random id.

    Hashed rather than stored verbatim, salted with `SECRET_KEY` and **no
    date** — the whole point of this one is that it does not rotate.

    The salt is not protecting against a rainbow table: the input is already a
    random v4 UUID, so there is nothing to guess. It is so the value in our
    database is not the same value sitting in the browser. If an id ever escapes
    into a URL, a screenshot, or a support email, nobody can take it and go
    looking for that person's rows without also holding `SECRET_KEY`.
    """
    return hashlib.sha256(
        f"{settings.SECRET_KEY}:client:{raw}".encode("utf-8")
    ).hexdigest()[:32]


def request_client_id(request: Request) -> str | None:
    """The caller's persistent pseudonym, or None if they didn't send a usable one.

    None is an ordinary, expected answer, not a failure: it is what a browser
    sending Global Privacy Control produces, and what any client that has never
    stored an id produces. Everything downstream treats it as "uncounted for
    retention" and carries on — the daily `visitor` pseudonym still counts them,
    which is why that column was kept rather than replaced.
    """
    raw = (request.headers.get(CLIENT_ID_HEADER) or "").strip().lower()
    if not _CLIENT_ID_RE.match(raw):
        return None
    return client_hash(raw)
