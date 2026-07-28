"""
Billing Endpoints
Frontend: GET /billing/plans  (public tier catalog for the paywall)

The current user's subscription lives under /users/me/subscription (auth-only).
This router only exposes the public, guest-safe tier catalog so the paywall
renders for signed-out users too.
"""

import logging

from fastapi import APIRouter

from app.config import settings
from app.schemas.subscription import PlanResponse, PlanCatalogResponse
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/plans", response_model=PlanCatalogResponse)
async def get_plans():
    """Public tier catalog (Free / Pro / Max) with live pricing + per-action
    credit costs. No auth — the paywall must render for guests."""
    plans = SubscriptionService().get_plan_catalog()
    return PlanCatalogResponse(
        plans=[PlanResponse(**p) for p in plans],
        report_cost=settings.REPORT_CREDIT_COST,
        chat_cost=settings.CHAT_CREDIT_COST,
    )
