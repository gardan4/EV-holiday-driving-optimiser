"""Clerk webhooks (Svix-signed).

Handles ``user.deleted`` — when a user is deleted from the Clerk dashboard, we
mirror the erasure locally. (The in-app case is covered by DELETE /api/auth/me;
this covers deletions initiated on Clerk's side.)

Signature verification is done manually (the Svix HMAC scheme) to avoid an extra
dependency. To enable: Clerk Dashboard → Webhooks → add endpoint
``https://api.evtrip.app/api/webhooks/clerk``, subscribe to ``user.deleted``,
and put the signing secret in ``CLERK_WEBHOOK_SECRET``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import erase_user_local
from app.core.config import settings
from app.core.database import get_db
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Reject events whose timestamp is skewed more than this (Svix replay defense).
_TOLERANCE_SECONDS = 300


def _verify_svix(secret: str, headers, body: bytes) -> bool:
    """Verify a Svix-signed webhook (Clerk uses Svix). Returns True if valid."""
    svix_id = headers.get("svix-id")
    svix_ts = headers.get("svix-timestamp")
    svix_sig = headers.get("svix-signature")
    if not (svix_id and svix_ts and svix_sig):
        return False

    # Replay protection: timestamp must be recent.
    try:
        if abs(time.time() - int(svix_ts)) > _TOLERANCE_SECONDS:
            return False
    except (ValueError, TypeError):
        return False

    # The signing secret is "whsec_<base64>"; the HMAC key is the decoded base64.
    try:
        raw = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
        key = base64.b64decode(raw)
    except Exception:
        return False

    signed_content = f"{svix_id}.{svix_ts}.{body.decode('utf-8')}".encode("utf-8")
    expected = base64.b64encode(hmac.new(key, signed_content, hashlib.sha256).digest()).decode()

    # The header is a space-separated list of "v1,<base64sig>" tokens.
    for token in svix_sig.split():
        _, _, sig = token.partition(",")
        if sig and hmac.compare_digest(expected, sig):
            return True
    return False


@router.post("/clerk")
async def clerk_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Receive Clerk webhook events (Svix-signed). Currently: user.deleted."""
    secret = settings.CLERK_WEBHOOK_SECRET
    if not secret:
        # Don't accept unverifiable events when no signing secret is configured.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook not configured",
        )

    body = await request.body()
    if not _verify_svix(secret, request.headers, body):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")

    evt_type = event.get("type")
    data = event.get("data") or {}

    if evt_type == "user.deleted":
        clerk_id = data.get("id")
        if clerk_id:
            user = (
                await db.execute(select(User).where(User.clerk_user_id == clerk_id))
            ).scalar_one_or_none()
            if user is not None and user.is_active:
                await erase_user_local(user, db)
                logger.info("clerk webhook: erased local user for deleted Clerk id %s", clerk_id)

    # Acknowledge all events (incl. unhandled types) with 200 so Clerk doesn't retry.
    return {"received": True}
