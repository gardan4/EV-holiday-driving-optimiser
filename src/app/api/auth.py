"""Authentication / account API endpoints (Clerk-backed).

Authentication itself is owned by **Clerk** (sign-in/up, social, password,
email verification, MFA). These endpoints cover only the *local* account: the
profile (`/me`), a simple onboarding flag, and right-to-erasure (`DELETE /me`).

The Clerk session token arrives as `Authorization: Bearer <token>`; it is
verified and just-in-time provisioned into a local `User` (keyed by
`clerk_user_id`) in `get_current_user`.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy import func as sa_func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.db_utils import utcnow
from app.models import Business, User, UserAuditLog

logger = logging.getLogger(__name__)

router = APIRouter()

# Extracts the Bearer token from the Authorization header. tokenUrl is only
# OpenAPI metadata (there is no local token endpoint — Clerk mints tokens).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    email_verified: bool = False
    onboarded: bool = False
    created_at: str
    tos_accepted_at: Optional[str] = None
    owned_business_count: int = 0

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Current-user dependency (Clerk session token → local User)
# ---------------------------------------------------------------------------


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Resolve the authenticated user from the Clerk session token.

    Verifies the token (RS256, networkless) and JIT-provisions / links the local
    User keyed by clerk_user_id. The frontend sends the token as
    `Authorization: Bearer <token>` (Clerk `getToken()`).
    """
    if not token:
        return None

    from app.core.clerk_auth import verify_clerk_token

    identity = verify_clerk_token(token)
    if identity is None:
        return None

    # NB: do NOT swallow DB errors here. A failed lookup (wedged DB / exhausted
    # pool) is INFRA, not an auth failure — let it propagate (→ 500) so the
    # client retries instead of being wrongly treated as logged out.
    user = (
        await db.execute(select(User).where(User.clerk_user_id == identity.clerk_user_id))
    ).scalar_one_or_none()
    if user is None:
        user = await _provision_clerk_user(identity, db)
        if user is None:
            return None

    # Local ban switch still applies (deactivate without touching Clerk).
    if not user.is_active:
        return None

    # Keep the local mirror of Clerk-owned fields fresh (writes only on change).
    changed = False
    if identity.email and user.email != identity.email:
        user.email = identity.email
        changed = True
    if identity.email_verified and not user.email_verified:
        user.email_verified = True
        changed = True
    if changed:
        await db.commit()

    return user


async def _provision_clerk_user(identity, db: AsyncSession) -> Optional[User]:
    """Create the local User row the first time we see a given Clerk user.

    Returns None (→ 401) if the token carries no email claim — see
    app/core/clerk_auth.py for the required session-token template.
    """
    if not identity.email:
        logger.warning(
            "clerk provisioning skipped for %s: no email claim — add `email` to "
            "the Clerk session-token template (see app/core/clerk_auth.py).",
            identity.clerk_user_id,
        )
        return None

    user = User(
        clerk_user_id=identity.clerk_user_id,
        email=identity.email,
        name=identity.name or identity.email.split("@")[0],
        email_verified=identity.email_verified,
        # Consent is captured at Clerk sign-up (configure legal links there);
        # stamp first-seen here so the local record has a timestamp.
        tos_accepted_at=utcnow(),
    )
    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
    except IntegrityError:
        # The insert collided. Two cases:
        #   1) a concurrent first request created the same clerk_user_id row, or
        #   2) a local row already exists for this email (legacy / seeded data).
        await db.rollback()

        existing = (
            await db.execute(select(User).where(User.clerk_user_id == identity.clerk_user_id))
        ).scalar_one_or_none()
        if existing is not None:
            return existing  # case 1 — race; use the winner

        # case 2: link the Clerk identity onto the existing email row — but ONLY
        # if Clerk has verified the address. Otherwise an unverified Clerk signup
        # reusing someone's email could hijack their existing account.
        by_email = (
            await db.execute(select(User).where(User.email == identity.email))
        ).scalar_one_or_none()
        if by_email is not None and by_email.clerk_user_id is None and identity.email_verified:
            by_email.clerk_user_id = identity.clerk_user_id
            if not by_email.email_verified:
                by_email.email_verified = True
            await db.commit()
            logger.info(
                "linked Clerk identity %s to existing user %s",
                identity.clerk_user_id, by_email.email,
            )
            return by_email

        logger.warning(
            "clerk provisioning conflict for email %s (verified=%s) — refusing to link",
            identity.email, identity.email_verified,
        )
        return None

    return user


async def require_auth(
    user: Optional[User] = Depends(get_current_user),
) -> User:
    """Require authentication - raises 401 if not authenticated."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Current user's profile, plus a count of owned (active) businesses."""
    owned = (
        await db.execute(
            select(sa_func.count(Business.id)).where(
                Business.owner_user_id == user.id,
                Business.archived_at.is_(None),
            )
        )
    ).scalar() or 0

    return UserResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        email_verified=bool(user.email_verified),
        onboarded=bool(user.onboarded),
        created_at=user.created_at.isoformat(),
        tos_accepted_at=user.tos_accepted_at.isoformat() if user.tos_accepted_at else None,
        owned_business_count=owned,
    )


@router.post("/onboarded")
async def mark_onboarded(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Mark onboarding complete for the current user (example lifecycle flag)."""
    user.onboarded = True
    await db.commit()
    return {"onboarded": True}


async def erase_user_local(user: User, db: AsyncSession) -> None:
    """Irreversibly scrub a local User + archive their businesses (GDPR Art. 17).

    CRYPTO-SHRED rather than row-delete: audit logs carry a non-nullable FK to
    users.id, so a hard delete would fail an integrity check. We instead scrub
    the personal data + identifiers, deactivate the login, and archive owned
    businesses. Does NOT touch Clerk — callers handle the Clerk side.
    """
    tombstone = f"deleted+{user.id.hex}@deleted.invalid"

    owned = (
        await db.execute(select(Business).where(Business.owner_user_id == user.id))
    ).scalars().all()
    for biz in owned:
        if biz.archived_at is None:
            biz.archived_at = utcnow()

    if user.email:
        # The erased identity must not survive in audit payloads.
        audit_rows = (
            await db.execute(select(UserAuditLog).where(UserAuditLog.user_id == user.id))
        ).scalars().all()
        for row in audit_rows:
            if isinstance(row.payload, dict):
                row.payload = _redact_value(row.payload, user.email, tombstone)

    user.email = tombstone
    user.name = "Deleted user"
    user.clerk_user_id = None  # unlink any Clerk identity
    user.email_verified = False
    user.is_active = False  # blocks any future provisioning/login on this row
    await db.commit()

    logger.info(
        "account erased (GDPR Art.17): user %s (%d businesses archived)",
        user.id, len(owned),
    )


def _redact_value(value, needle: str, replacement: str):
    """Recursively replace a string inside a JSON payload (dict/list/str)."""
    if isinstance(value, str):
        return value.replace(needle, replacement)
    if isinstance(value, dict):
        return {k: _redact_value(v, needle, replacement) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v, needle, replacement) for v in value]
    return value


@router.delete("/me")
async def delete_account(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Right-to-erasure (GDPR Art. 17): irreversibly erase the caller's account.

    The authenticated Clerk session is the authorization. We delete the Clerk
    user (so it can't be re-provisioned on next sign-in), then crypto-shred the
    local row + archive owned businesses (see erase_user_local).
    """
    if user.clerk_user_id:
        from app.core.clerk_auth import delete_clerk_user

        await delete_clerk_user(user.clerk_user_id)

    await erase_user_local(user, db)
    return {"message": "Your account and personal data have been permanently erased."}
