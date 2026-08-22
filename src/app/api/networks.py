"""The charging networks a plan can be told to avoid."""

from fastapi import APIRouter, Request, Response

from app.api.schemas import NetworkOut
from app.core.config import settings
from app.core.rate_limit import limiter
from app.services import networks as networks_svc

router = APIRouter()


@router.get("", response_model=list[NetworkOut])
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def list_networks(request: Request, response: Response) -> list[NetworkOut]:
    """Served rather than hardcoded in the browser.

    Same rule as the dashboard reading the modelled country caps from the
    server: the list the toggles are drawn from and the list the matcher acts
    on have to be one list. A copy in the frontend would go on offering a
    network the server had renamed, and the toggle would do nothing with no
    error anywhere — the exact failure that is impossible to notice, because
    the plan it produces is a perfectly good plan.

    No database, so no session. It changes on deploy, like the catalog.
    """
    response.headers["Cache-Control"] = "public, max-age=3600"
    return [NetworkOut(slug=n.slug, label=n.label) for n in networks_svc.NETWORKS]
