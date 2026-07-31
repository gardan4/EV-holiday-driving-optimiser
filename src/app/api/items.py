"""Item API — the reference nested tenant-scoped resource.

Mounted at `/api/businesses/{business_id}/items`. Every route resolves the
business first (enforcing tenancy) and scopes queries to it. Copy this file +
the `Item` model + `require_item` dependency to build your own child resource.
"""

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_auth
from app.api.deps import audit, require_business_member, require_item
from app.core.config import settings
from app.core.database import get_db
from app.core.db_utils import utcnow
from app.core.rate_limit import limiter
from app.models import Business, Item, User

# NB: mounted with prefix `/api/businesses/{business_id}/items` in main.py, so
# routes here are relative to that. `require_business_member` / `require_item`
# read `business_id` (and `item_id`) from the path.
router = APIRouter()


class ItemCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    status: str = Field(default="active", max_length=40)
    data: dict | None = None


class ItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    status: str | None = Field(default=None, max_length=40)
    data: dict | None = None


@router.get("")
async def list_items(
    business: Business = Depends(require_business_member),
    db: AsyncSession = Depends(get_db),
):
    """List active items for the business."""
    rows = await db.execute(
        select(Item)
        .where(Item.business_id == business.id, Item.archived_at.is_(None))
        .order_by(Item.created_at.desc())
    )
    return [i.to_dict() for i in rows.scalars().all()]


@router.post("", status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT_WRITE)
async def create_item(
    request: Request,
    payload: ItemCreate,
    business: Business = Depends(require_business_member),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    item = Item(
        business_id=business.id,
        name=payload.name.strip(),
        description=payload.description,
        status=payload.status,
        data=payload.data,
    )
    db.add(item)
    await db.flush()
    await audit(
        db,
        business_id=business.id,
        user_id=user.id,
        action="item.create",
        target_type="item",
        target_id=item.id,
        payload={"name": item.name},
    )
    await db.commit()
    await db.refresh(item)
    return item.to_dict()


@router.get("/{item_id}")
async def get_item(
    scope: tuple[Business, Item] = Depends(require_item),
):
    _business, item = scope
    return item.to_dict()


@router.patch("/{item_id}")
async def update_item(
    payload: ItemUpdate,
    scope: tuple[Business, Item] = Depends(require_item),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    business, item = scope
    updates = payload.dict(exclude_unset=True)
    for key, value in updates.items():
        setattr(item, key, value.strip() if key == "name" and isinstance(value, str) else value)
    await audit(
        db,
        business_id=business.id,
        user_id=user.id,
        action="item.update",
        target_type="item",
        target_id=item.id,
        payload=updates,
    )
    await db.commit()
    await db.refresh(item)
    return item.to_dict()


@router.delete("/{item_id}")
async def delete_item(
    scope: tuple[Business, Item] = Depends(require_item),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete (archive) the item."""
    business, item = scope
    item.archived_at = utcnow()
    item.status = "archived"
    await audit(
        db,
        business_id=business.id,
        user_id=user.id,
        action="item.archive",
        target_type="item",
        target_id=item.id,
    )
    await db.commit()
    return {"archived": True}
