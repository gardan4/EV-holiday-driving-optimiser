"""Geocode autocomplete proxy (keeps the ORS key server-side)."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import GeocodeHit
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.services import routing

router = APIRouter()


@router.get("", response_model=list[GeocodeHit])
@limiter.limit(settings.RATE_LIMIT_GEOCODE)
async def geocode_autocomplete(
    request: Request,
    q: str = Query(min_length=3, max_length=120),
    db: AsyncSession = Depends(get_db),
) -> list[GeocodeHit]:
    """Search-as-you-type, proxied so the key never reaches a browser.

    Three characters, not two. This endpoint is the app's largest consumer of
    the ORS free tier by a wide margin — it fires while somebody is still
    typing, so it cannot be cached before the fact the way a route can — and
    two-letter prefixes are both the highest-volume bucket and the least
    useful: "ut" matches half of Europe. The third character is where the
    results start being worth a request.

    The session is here only so `routing.geocode` can record what it spent.
    """
    hits = await routing.geocode(q, db=db)
    # The quota rows above are written but not committed by `quota.record`,
    # which is deliberate — it never commits on a caller's behalf. This is a
    # read-only endpoint otherwise, so nothing else would flush them.
    await db.commit()
    return [GeocodeHit(**h) for h in hits]


@router.get("/reverse", response_model=GeocodeHit)
@limiter.limit(settings.RATE_LIMIT_GEOCODE)
async def geocode_reverse(
    request: Request,
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    db: AsyncSession = Depends(get_db),
) -> GeocodeHit:
    """A coordinate → the place it is in. Backs "start from where I am".

    The browser already knows where it is; what it cannot do is NAME it, and a
    trip whose origin reads "52.09, 5.12" is one nobody can check before
    planning it or recognise afterwards in their own list. So this is a naming
    call, not a positioning one.

    404 rather than a coordinate label when Pelias matches nothing. Somewhere
    genuinely unnamed — mid-ocean, deep desert, a made-up pair — is a place
    this app cannot route from anyway, and inventing a label for it would move
    the failure to the routing call, where the message is about roads instead
    of about the button that was pressed.
    """
    hit = await routing.reverse_geocode(lat, lon, db=db)
    # As in the autocomplete above: `quota.record` writes but never commits.
    await db.commit()
    if hit is None:
        raise HTTPException(
            status_code=404,
            detail="Couldn't name that location. Type where you're starting from.",
        )
    return GeocodeHit(**hit)
