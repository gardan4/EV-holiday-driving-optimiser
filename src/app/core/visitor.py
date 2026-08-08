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
  over the past month" unanswerable, which is exactly the trade we want. Any
  future feature wanting a stable id must not reach for this function.

The user agent goes in as well. It costs nothing, and it separates the several
people who genuinely share one address behind a household NAT or a carrier's
CGNAT — the same CGNAT problem `rate_limit.run_key` already works around.
"""

from __future__ import annotations

import hashlib
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
