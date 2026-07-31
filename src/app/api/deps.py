"""Shared FastAPI dependencies for tenant scoping.

Permission model:
- `owner` (Business.owner_user_id, or a member promoted to the `owner` role):
  full access to everything inside the business.
- `member` (BusinessMembership.role): access to the business and its resources.

To scope a nested resource (like Item) you resolve the business first via
`require_business_member`, then look the child up under `business.id`. See
`require_item` below for the reference pattern, and `api/items.py` for usage.
"""

import uuid
from typing import Optional

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_auth
from app.core.database import get_db
from app.models import Business, BusinessMembership, Item, User, UserAuditLog


# ---------------------------------------------------------------------------
# Business-level scope
# ---------------------------------------------------------------------------


async def _load_business(business_id: str, db: AsyncSession) -> Business:
    try:
        bid = uuid.UUID(business_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid business id")
    result = await db.execute(select(Business).where(Business.id == bid))
    business = result.scalar_one_or_none()
    if not business or business.archived_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")
    return business


async def require_business_member(
    business_id: str = Path(...),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Business:
    """Caller must be the owner or a member of this business."""
    business = await _load_business(business_id, db)
    if business.owner_user_id == user.id:
        return business
    membership = await db.execute(
        select(BusinessMembership).where(
            BusinessMembership.business_id == business.id,
            BusinessMembership.user_id == user.id,
        )
    )
    if membership.scalar_one_or_none():
        return business
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this business")


async def require_business_owner(
    business_id: str = Path(...),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Business:
    """Caller must be an owner of this business (founder or owner-role member)."""
    business = await _load_business(business_id, db)
    if business.owner_user_id == user.id:
        return business
    membership = await db.execute(
        select(BusinessMembership).where(
            BusinessMembership.business_id == business.id,
            BusinessMembership.user_id == user.id,
            BusinessMembership.role == "owner",
        )
    )
    if membership.scalar_one_or_none():
        return business
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner-only action")


# ---------------------------------------------------------------------------
# Item scope — the reference nested-resource dependency
# ---------------------------------------------------------------------------


async def require_item(
    item_id: str = Path(...),
    business: Business = Depends(require_business_member),
    db: AsyncSession = Depends(get_db),
) -> tuple[Business, Item]:
    """Resolve an Item that belongs to the caller's business.

    Copy this pattern for your own child resources: depend on
    `require_business_member` first (which enforces tenancy + 404s archived
    businesses), then look the child up scoped to `business.id`.
    """
    try:
        iid = uuid.UUID(item_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid item id")
    result = await db.execute(
        select(Item).where(Item.id == iid, Item.business_id == business.id)
    )
    item = result.scalar_one_or_none()
    if not item or item.archived_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return business, item


# ---------------------------------------------------------------------------
# Audit log helper
# ---------------------------------------------------------------------------


async def audit(
    db: AsyncSession,
    *,
    business_id: uuid.UUID,
    user_id: uuid.UUID,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[uuid.UUID] = None,
    payload: Optional[dict] = None,
) -> None:
    """Record a user-initiated action. Caller must commit the session."""
    entry = UserAuditLog(
        business_id=business_id,
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
    )
    db.add(entry)
