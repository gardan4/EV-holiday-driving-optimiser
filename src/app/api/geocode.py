"""Geocode autocomplete proxy (keeps the ORS key server-side)."""

from fastapi import APIRouter, Depends, Query, Request
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
