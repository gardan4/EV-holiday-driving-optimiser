"""Clerk session-token verification for the FastAPI backend.

Active when ``settings.AUTH_PROVIDER == "clerk"``. We verify the short-lived
Clerk **session JWT** (RS256) using the PEM public key from the Clerk Dashboard
— networkless, no JWKS round-trip per request — then ``api.auth.get_current_user``
JIT-provisions a local ``User`` keyed by ``clerk_user_id``.

IMPORTANT — Clerk's *default* session token does NOT carry the user's email or
name. Configure a session-token claim template in the Clerk Dashboard
(Sessions → "Customize session token") so these claims are present, e.g.::

    {
      "email": "{{user.primary_email_address}}",
      "name": "{{user.full_name}}",
      "email_verified": "{{user.email_verified}}"
    }

Without an ``email`` claim we cannot provision the local row (billing,
notifications, and admin checks all key on it) and sign-in is rejected with a
clear server log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from jose import JWTError, jwt

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ClerkIdentity:
    clerk_user_id: str
    email: Optional[str]
    name: Optional[str]
    email_verified: bool


def verify_clerk_token(token: str) -> Optional[ClerkIdentity]:
    """Verify a Clerk session JWT and extract identity claims.

    Returns None (never raises) on any verification failure so callers treat it
    as an anonymous request → 401.
    """
    if not token or not settings.CLERK_JWT_KEY:
        return None

    try:
        claims = jwt.decode(
            token,
            settings.CLERK_JWT_KEY,  # PEM public key
            algorithms=["RS256"],
            # Clerk session tokens use `azp`, not `aud`.
            issuer=settings.CLERK_ISSUER or None,
            options={
                "verify_aud": False,
                "verify_iss": bool(settings.CLERK_ISSUER),
            },
        )
    except JWTError as exc:
        logger.debug("clerk token verification failed: %s", exc)
        return None

    # Authorized-party check: confirms the token was minted for OUR frontend,
    # not replayed from another app sharing the same Clerk instance.
    allowed = settings.clerk_authorized_parties()
    if allowed:
        azp = claims.get("azp")
        if azp and azp not in allowed:
            logger.warning("clerk token rejected: azp %r not in authorized parties", azp)
            return None

    sub = claims.get("sub")
    if not sub:
        return None

    email = claims.get("email")
    if isinstance(email, str):
        email = email.strip().lower() or None

    name = claims.get("name") or claims.get("full_name")
    if not name:
        parts = [claims.get("first_name") or "", claims.get("last_name") or ""]
        name = " ".join(p for p in parts if p).strip() or None

    ev = claims.get("email_verified")
    email_verified = ev if isinstance(ev, bool) else str(ev).lower() == "true"

    return ClerkIdentity(
        clerk_user_id=sub,
        email=email,
        name=name,
        email_verified=email_verified,
    )


async def delete_clerk_user(clerk_user_id: str) -> bool:
    """Delete a user in Clerk via the Backend API (right-to-erasure).

    Best-effort: returns True on success or if the user is already gone (404),
    False otherwise. Never raises — the caller still scrubs local data.
    """
    if not settings.CLERK_SECRET_KEY or not clerk_user_id:
        logger.warning("delete_clerk_user skipped: missing secret key or id")
        return False
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                f"https://api.clerk.com/v1/users/{clerk_user_id}",
                headers={"Authorization": f"Bearer {settings.CLERK_SECRET_KEY}"},
            )
        if resp.status_code in (200, 404):
            return True
        logger.warning(
            "delete_clerk_user failed for %s: %s %s",
            clerk_user_id, resp.status_code, resp.text[:200],
        )
        return False
    except Exception as exc:  # network/timeout — log, don't block local erasure
        logger.warning("delete_clerk_user error for %s: %s", clerk_user_id, exc)
        return False


async def create_clerk_invitation(
    email: str, *, redirect_url: Optional[str] = None
) -> tuple[bool, Optional[str]]:
    """Send a Clerk invitation email via the Backend API.

    Best-effort, never raises: returns (ok, invitation_id). With no
    CLERK_SECRET_KEY (local dev / tests) returns (True, None) — the local
    BusinessInvite row still redeems on the invitee's first login, there's
    just no email. A 4xx saying the address already exists in Clerk (signed
    up but never logged in here) is also a soft success for the same reason.
    """
    if not settings.CLERK_SECRET_KEY:
        logger.info("clerk invitation skipped for %s: no secret key", email)
        return True, None
    import httpx

    body: dict = {"email_address": email, "notify": True, "ignore_existing": True}
    if redirect_url:
        body["redirect_url"] = redirect_url
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.clerk.com/v1/invitations",
                headers={"Authorization": f"Bearer {settings.CLERK_SECRET_KEY}"},
                json=body,
            )
        if resp.status_code in (200, 201):
            try:
                return True, resp.json().get("id")
            except Exception:
                return True, None
        if resp.status_code == 400 and "already" in resp.text.lower():
            # Existing Clerk user who never logged in here — first login redeems.
            logger.info("clerk invitation for %s: address already known to Clerk", email)
            return True, None
        logger.warning(
            "create_clerk_invitation failed for %s: %s %s",
            email, resp.status_code, resp.text[:200],
        )
        return False, None
    except Exception as exc:
        logger.warning("create_clerk_invitation error for %s: %s", email, exc)
        return False, None


async def revoke_clerk_invitation(invitation_id: str) -> bool:
    """Revoke a Clerk invitation. Best-effort, never raises."""
    if not settings.CLERK_SECRET_KEY or not invitation_id:
        return False
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"https://api.clerk.com/v1/invitations/{invitation_id}/revoke",
                headers={"Authorization": f"Bearer {settings.CLERK_SECRET_KEY}"},
            )
        return resp.status_code in (200, 404)
    except Exception as exc:
        logger.warning("revoke_clerk_invitation error for %s: %s", invitation_id, exc)
        return False
