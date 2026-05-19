"""
Admin endpoints — operational triggers for background jobs.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from app.config import settings
from app.dependencies import get_current_user_or_guest

logger = logging.getLogger(__name__)

router = APIRouter()

# Emails authorised to call admin endpoints
_ADMIN_EMAILS: set[str] = {"haiphan@caydex.com", "admin@caydex.com"}


def _authorize_admin(
    user: Optional[dict],
    x_admin_token: Optional[str],
) -> None:
    """Allow either (a) authenticated user whose email is on the allowlist
    or (b) an `X-Admin-Token` header that matches settings.ADMIN_TOKEN.

    Raises HTTPException(403) when neither path passes. The token path
    exists so dev/maintenance scripts can trigger benchmark recomputes
    without going through the iOS sign-in flow.
    """
    token = settings.ADMIN_TOKEN
    if token and x_admin_token and x_admin_token == token:
        return
    if user and user.get("email") in _ADMIN_EMAILS:
        return
    raise HTTPException(status_code=403, detail="Admin access required")


@router.post("/refresh-sector-benchmarks")
async def refresh_sector_benchmarks(
    backfill: bool = False,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    user: dict = Depends(get_current_user_or_guest),
):
    """Manually trigger sector benchmark recomputation. Returns immediately.

    Auth: pass `X-Admin-Token: <settings.ADMIN_TOKEN>` OR sign in with an
    email on the admin allowlist.

    Args:
        backfill: If True, forces deep historical computation (16 annual, 80 quarterly).
                  If False (default), only refreshes recent periods.
    """
    _authorize_admin(user, x_admin_token)
    try:
        from app.services.sector_benchmark_service import get_sector_benchmark_service

        service = get_sector_benchmark_service()
        asyncio.create_task(service.compute_all_benchmarks(force=True, backfill=backfill))
        mode = "backfill (full history)" if backfill else "daily (recent periods)"
        return {"status": "started", "message": f"Sector benchmark computation started in background — mode: {mode}"}
    except Exception as e:
        logger.error(f"Manual benchmark refresh failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to start benchmark refresh")
