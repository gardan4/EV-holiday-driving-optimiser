"""Business (tenant) API — CRUD + membership.

A Business is the strict tenant boundary. The creator becomes the owner (and
gets an `owner`-role membership row). Nested resources (see api/items.py) mount
under `/api/businesses/{business_id}/...` and scope every query to the business.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_auth
from app.api.deps import audit, require_business_member, require_business_owner
from app.core.config import settings
from app.core.database import get_db
from app.core.db_utils import utcnow
from app.core.rate_limit import limiter
from app.models import Business, BusinessMembership, User

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BusinessCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class BusinessUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class MemberRoleUpdate(BaseModel):
    role: str = Field(..., pattern="^(owner|member)$")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT_WRITE)
async def create_business(
    request: Request,
    payload: BusinessCreate,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a business owned by the caller."""
    business = Business(name=payload.name.strip(), owner_user_id=user.id)
    db.add(business)
    await db.flush()  # assign business.id before writing the membership + audit

    db.add(BusinessMembership(business_id=business.id, user_id=user.id, role="owner"))
    await audit(
        db,
        business_id=business.id,
        user_id=user.id,
        action="business.create",
        target_type="business",
        target_id=business.id,
        payload={"name": business.name},
    )
    await db.commit()
    await db.refresh(business)
    return business.to_dict()


@router.get("")
async def list_businesses(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List active businesses the caller owns or is a member of."""
    result = await db.execute(
        select(Business)
        .outerjoin(
            BusinessMembership,
            (BusinessMembership.business_id == Business.id)
            & (BusinessMembership.user_id == user.id),
        )
        .where(
            Business.archived_at.is_(None),
            or_(Business.owner_user_id == user.id, BusinessMembership.id.isnot(None)),
        )
        .order_by(Business.created_at.desc())
        .distinct()
    )
    businesses = result.scalars().all()
    return [b.to_dict() for b in businesses]


@router.get("/{business_id}")
async def get_business(
    business: Business = Depends(require_business_member),
):
    return business.to_dict()


@router.patch("/{business_id}")
async def update_business(
    payload: BusinessUpdate,
    business: Business = Depends(require_business_owner),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    business.name = payload.name.strip()
    await audit(
        db,
        business_id=business.id,
        user_id=user.id,
        action="business.update",
        target_type="business",
        target_id=business.id,
        payload={"name": business.name},
    )
    await db.commit()
    await db.refresh(business)
    return business.to_dict()


@router.delete("/{business_id}")
async def delete_business(
    business: Business = Depends(require_business_owner),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete (archive) the business."""
    business.archived_at = utcnow()
    await audit(
        db,
        business_id=business.id,
        user_id=user.id,
        action="business.archive",
        target_type="business",
        target_id=business.id,
    )
    await db.commit()
    return {"archived": True}


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


@router.get("/{business_id}/members")
async def list_members(
    business: Business = Depends(require_business_member),
    db: AsyncSession = Depends(get_db),
):
    """List members of the business (the founder is always an implicit owner)."""
    rows = await db.execute(
        select(BusinessMembership, User)
        .join(User, User.id == BusinessMembership.user_id)
        .where(BusinessMembership.business_id == business.id)
        .order_by(BusinessMembership.created_at)
    )
    members = []
    for membership, member_user in rows.all():
        members.append(
            {
                "user_id": str(member_user.id),
                "email": member_user.email,
                "name": member_user.name,
                "role": "owner" if member_user.id == business.owner_user_id else membership.role,
                "is_founder": member_user.id == business.owner_user_id,
            }
        )
    return members


@router.patch("/{business_id}/members/{user_id}")
async def update_member_role(
    user_id: str,
    payload: MemberRoleUpdate,
    business: Business = Depends(require_business_owner),
    actor: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Promote/demote a member. The founding owner's role is immutable."""
    try:
        target_id = uuid.UUID(user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user id")

    if target_id == business.owner_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The founding owner's role cannot be changed",
        )

    membership = (
        await db.execute(
            select(BusinessMembership).where(
                BusinessMembership.business_id == business.id,
                BusinessMembership.user_id == target_id,
            )
        )
    ).scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    membership.role = payload.role
    await audit(
        db,
        business_id=business.id,
        user_id=actor.id,
        action="business.member.role",
        target_type="user",
        target_id=target_id,
        payload={"role": payload.role},
    )
    await db.commit()
    return {"user_id": user_id, "role": payload.role}
