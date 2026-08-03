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

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import FeedbackIn, FeedbackItem
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models import Feedback

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("", status_code=201)
@limiter.limit(settings.RATE_LIMIT_FEEDBACK)
async def leave_feedback(
    request: Request, body: FeedbackIn, db: AsyncSession = Depends(get_db)
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
