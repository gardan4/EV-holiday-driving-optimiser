"""Coarse facts about the browser a request came from.

Answers three product questions the counters could not: is this a Dutch thing
or a European one or an American one; are people on phones or desktops (the 3D
scene is React Three Fiber, and that answer decides whether it is the
centrepiece or the reason the page is unusable); and how wide is the screen it
has to work on.

**Buckets, never the raw string.** A full `User-Agent` is one of the strongest
fingerprinting signals a browser emits — version numbers, build ids, device
models — and storing it would turn a counting table into a device-tracking one.
Everything here reduces to a handful of allowlisted words, so the whole column
has maybe eight distinct values and there is nothing in a row to re-identify
anyone with. The same rule the paths and referrers already follow.

Order matters in the matching: Edge announces itself as Chrome, Chrome
announces itself as Safari, and most things announce themselves as Mozilla. The
sequences below are written most-specific-first, which is why they are
sequences and not a dict.
"""

from __future__ import annotations

import re

from starlette.requests import Request

from app.core.config import settings

# Cloudflare resolves the country and puts it here. Two reasons to take it from
# the edge rather than geolocating ourselves: no IP database to ship, update and
# get wrong, and no code path anywhere in this app that turns an address into a
# place — the header arrives already coarse.
COUNTRY_HEADER = "cf-ipcountry"

_UNKNOWN_COUNTRIES = {"", "XX", "T1"}  # CF's own "don't know" / Tor values

# (label, pattern). First match wins — see the module note about Edge/Chrome.
_BROWSERS: tuple[tuple[str, re.Pattern], ...] = (
    ("Edge", re.compile(r"\bEdg[eiOSA]*/", re.I)),
    ("Opera", re.compile(r"\bOPR/|\bOpera", re.I)),
    ("Samsung", re.compile(r"\bSamsungBrowser/", re.I)),
    ("Firefox", re.compile(r"\bFirefox/|\bFxiOS/", re.I)),
    ("Chrome", re.compile(r"\bChrome/|\bCriOS/", re.I)),
    ("Safari", re.compile(r"\bSafari/", re.I)),
)

_OSES: tuple[tuple[str, re.Pattern], ...] = (
    # iPadOS 13+ claims to be a Mac, so iPad has to be tested before macOS.
    ("iPadOS", re.compile(r"\biPad\b", re.I)),
    ("iOS", re.compile(r"\biPhone\b|\biPod\b", re.I)),
    ("Android", re.compile(r"\bAndroid\b", re.I)),
    ("Windows", re.compile(r"\bWindows\b", re.I)),
    ("macOS", re.compile(r"\bMac OS X\b|\bMacintosh\b", re.I)),
    ("Linux", re.compile(r"\bLinux\b|\bX11\b", re.I)),
)

_TABLET = re.compile(r"\biPad\b|\bTablet\b|\bAndroid\b(?!.*\bMobile\b)", re.I)
_MOBILE = re.compile(r"\bMobi\b|\bMobile\b|\biPhone\b|\biPod\b", re.I)

# A bot that renders and fires analytics is rare, but "Chrome on Linux, 0 px
# wide" is not a person and should not sit in the device mix.
_BOT = re.compile(r"\bbot\b|\bcrawler\b|\bspider\b|\bheadless\b|\bpreview\b", re.I)

# Where the CSS actually breaks, not round numbers: Tailwind's sm/md/lg/xl.
#
# ASCII labels on purpose. These strings are stored in a `String` column, which
# is VARCHAR on MSSQL — a codepage, not Unicode — so "≤640" comes back as
# "<=640" and anything outside CP1252 comes back as "?". A label that changes
# depending on the database's collation is a label you cannot GROUP BY
# reliably, so they stay in the ASCII range and the prettifying happens at
# render time if it happens at all.
_VIEWPORT_BUCKETS = (
    (640, "up to 640"),
    (768, "641-768"),
    (1024, "769-1024"),
    (1440, "1025-1440"),
)
_VIEWPORT_WIDEST = "over 1440"


def classify_device(user_agent: str) -> str | None:
    """"mobile" | "tablet" | "desktop" | "bot", or None when unreadable."""
    if not user_agent:
        return None
    if _BOT.search(user_agent):
        return "bot"
    # Tablet first: every Android tablet also matches the mobile pattern.
    if _TABLET.search(user_agent):
        return "tablet"
    if _MOBILE.search(user_agent):
        return "mobile"
    return "desktop"


def classify_browser(user_agent: str) -> str | None:
    if not user_agent:
        return None
    for label, pattern in _BROWSERS:
        if pattern.search(user_agent):
            return label
    return "Other"


def classify_os(user_agent: str) -> str | None:
    if not user_agent:
        return None
    for label, pattern in _OSES:
        if pattern.search(user_agent):
            return label
    return "Other"


def classify_viewport(width: int | None) -> str | None:
    """A CSS-pixel width reduced to the breakpoint band it falls in.

    Banded rather than stored: an exact viewport width is a genuine
    fingerprinting signal (most people's browser windows are a slightly unusual
    size), and the question here — does the layout have room — is answered just
    as well by five buckets.
    """
    if width is None or width <= 0 or width > 20_000:
        return None
    for edge, label in _VIEWPORT_BUCKETS:
        if width <= edge:
            return label
    return _VIEWPORT_WIDEST


def request_country(request: Request) -> str | None:
    """The visitor's country, when something trustworthy in front says so.

    Gated on `TRUSTED_PROXY_HEADERS` for the same reason `_client_ip` gates the
    forwarding headers: with nothing in front that overwrites it, `CF-IPCountry`
    is whatever the caller typed. The consequence here is only a wrong bar on a
    chart rather than a bypassed rate limit — but a number that is silently
    caller-controlled is worse than no number, because you would believe it.
    """
    if not settings.TRUSTED_PROXY_HEADERS:
        return None
    raw = (request.headers.get(COUNTRY_HEADER) or "").strip().upper()
    if len(raw) != 2 or not raw.isalpha() or raw in _UNKNOWN_COUNTRIES:
        return None
    return raw
