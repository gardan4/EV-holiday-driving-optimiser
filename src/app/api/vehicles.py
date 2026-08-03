"""Vehicle catalog endpoints (read-only; data comes from the seed script)."""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import VehicleOut
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models import Vehicle

router = APIRouter()


@router.get("", response_model=list[VehicleOut])
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def list_vehicles(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> list[VehicleOut]:
    # This was the one route with no limit on it, and it table-scans and
    # serialises every charge curve per call. The catalog only changes on
    # deploy, so let anything in front of us serve it.
    response.headers["Cache-Control"] = "public, max-age=3600"
    result = await db.execute(select(Vehicle).order_by(Vehicle.sort_order, Vehicle.slug))
    return [
        VehicleOut(
            id=str(v.id),
            slug=v.slug,
            make=v.make,
            model=v.model,
            variant=v.variant,
            usable_kwh=v.usable_kwh,
            consumption=v.consumption,
            charge_curve=v.charge_curve,
            max_dc_kw=v.max_dc_kw,
            mass_kg=v.mass_kg,
            top_speed_kph=v.top_speed_kph,
            source_note=v.source_note,
        )
        for v in result.scalars().all()
    ]
