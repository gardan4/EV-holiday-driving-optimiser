"""Geocode autocomplete proxy (keeps the ORS key server-side)."""

from fastapi import APIRouter, Query, Request

from app.api.schemas import GeocodeHit
from app.core.config import settings
from app.core.rate_limit import limiter
from app.services import routing

router = APIRouter()


@router.get("", response_model=list[GeocodeHit])
@limiter.limit(settings.RATE_LIMIT_GEOCODE)
async def geocode_autocomplete(
    request: Request, q: str = Query(min_length=2, max_length=120)
) -> list[GeocodeHit]:
    hits = await routing.geocode(q)
    return [GeocodeHit(**h) for h in hits]
