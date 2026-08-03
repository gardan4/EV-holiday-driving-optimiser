"""Feedback: one public write, one private read.

Kept in-house rather than pointing at a third-party form. A visitor who has to
leave the site and load someone else's page mostly doesn't, and the privacy
notice now promises no third parties — a form somewhere else would make that
false the day it went up.

The read side is a header token rather than a login, because adding auth to an
app whose entire design premise is "no accounts" for the sake of an inbox would
be the tail wagging the dog. With no token configured the route does not exist
at all.
"""

from __future__ import annotations

import logging
import secrets

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import FeedbackIn, FeedbackItem
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models import Feedback

logger = logging.getLogger(__name__)

router = APIRouter()


async def _notify(message: str, path: str | None, contact: str | None) -> None:
    """Post the feedback to Discord so it is read rather than merely stored.

    The contact address goes in too, so a reply is one click rather than a
    round trip through the inbox. That does put the one identifying field into
    a chat channel — somewhere things get screenshotted and forwarded, and
    somewhere anyone added to the server can read it later. It is a private
    channel behind a secret URL, the address was volunteered specifically to
    get a reply, and the privacy page says plainly that this is where it goes.

    Best-effort by construction. It runs after the response is sent, and a dead
    webhook must never turn somebody's feedback into an error.
    """
    if not settings.DISCORD_WEBHOOK_URL:
        return
    # Discord rejects an embed description over 4096; keep well clear and mark
    # a truncation rather than silently dropping the tail.
    body = message if len(message) <= 1500 else message[:1500] + "…"
    fields = []
    if path:
        fields.append({"name": "Page", "value": path[:100], "inline": True})
    if contact:
        fields.append({"name": "Reply to", "value": contact[:200], "inline": True})
    payload = {
        "username": "evtrip.dev",
        "embeds": [
            {
                "title": "New feedback",
                "description": body,
                "color": 0x2ED3A0,
                "fields": fields,
            }
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(settings.DISCORD_WEBHOOK_URL, json=payload)
            resp.raise_for_status()
    except Exception:
        # No exc_info: a webhook failure embeds the URL — which is the secret —
        # in the exception message, and that would write it to the logs.
        logger.warning("Discord feedback notification failed")


@router.post("", status_code=201)
@limiter.limit(settings.RATE_LIMIT_FEEDBACK)
async def leave_feedback(
    request: Request,
    body: FeedbackIn,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Say something first.")

    db.add(
        Feedback(
            message=message,
            contact=(body.contact or "").strip() or None,
            path=(body.path or "").strip()[:300] or None,
        )
    )
    await db.commit()
    # The message itself is not logged — it may contain whatever someone chose
    # to type, including a way to contact them.
    logger.info("feedback received (%d chars)", len(message))
    # After the response, so a slow or dead ntfy costs the sender nothing.
    background.add_task(
        _notify,
        message,
        (body.path or "").strip()[:60] or None,
        (body.contact or "").strip() or None,
    )
    return {"ok": True}


@router.get("", response_model=list[FeedbackItem])
async def read_feedback(
    x_feedback_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[FeedbackItem]:
    if not settings.FEEDBACK_TOKEN:
        raise HTTPException(status_code=404, detail="Not found.")
    # Constant-time: a plain != leaks the shared prefix to anyone patient.
    if not x_feedback_token or not secrets.compare_digest(
        x_feedback_token, settings.FEEDBACK_TOKEN
    ):
        raise HTTPException(status_code=404, detail="Not found.")

    rows = (
        (
            await db.execute(
                select(Feedback).order_by(desc(Feedback.created_at)).limit(500)
            )
        )
        .scalars()
        .all()
    )
    return [
        FeedbackItem(
            created_at=r.created_at,
            message=r.message,
            contact=r.contact,
            path=r.path,
        )
        for r in rows
    ]
