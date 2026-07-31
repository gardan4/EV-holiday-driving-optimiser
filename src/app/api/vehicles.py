"""Vehicle catalog endpoints (read-only; data comes from the seed script)."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import VehicleOut
from app.core.database import get_db
from app.models import Vehicle

router = APIRouter()


@router.get("", response_model=list[VehicleOut])
async def list_vehicles(db: AsyncSession = Depends(get_db)) -> list[VehicleOut]:
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
            source_note=v.source_note,
        )
        for v in result.scalars().all()
    ]
