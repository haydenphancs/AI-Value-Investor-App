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
    # Log enough to debug without leaking the actual secret.
    logger.warning(
        "Admin auth failed: server_token_set=%s, header_present=%s, "
        "header_len=%d, server_len=%d, user_email=%r",
        bool(token),
        bool(x_admin_token),
        len(x_admin_token or ""),
        len(token or ""),
        user.get("email") if user else None,
    )
    raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/auth-debug")
async def auth_debug(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
):
    """Public diagnostic: confirms whether ADMIN_TOKEN env var is loaded
    on the server and whether the token in the request matches. Does NOT
    reveal either value.
    """
    server = settings.ADMIN_TOKEN
    return {
        "server_token_configured": server is not None and len(server) > 0,
        "server_token_length": len(server) if server else 0,
        "header_token_provided": x_admin_token is not None,
        "header_token_length": len(x_admin_token) if x_admin_token else 0,
        "match": bool(server and x_admin_token and server == x_admin_token),
    }


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


@router.post("/refresh-industry-dossier")
async def refresh_industry_dossier(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    user: dict = Depends(get_current_user_or_guest),
):
    """Manually trigger the industry_dossier weekly recompute. Returns
    immediately; the recompute runs in the background and takes ~5-10
    minutes depending on universe size and FMP rate.

    Auth: pass `X-Admin-Token: <settings.ADMIN_TOKEN>` OR sign in with an
    email on the admin allowlist.
    """
    _authorize_admin(user, x_admin_token)
    try:
        from app.services.industry_dossier_service import get_industry_dossier_service

        service = get_industry_dossier_service()
        asyncio.create_task(service.recompute_all(force=True))
        return {
            "status": "started",
            "message": "Industry dossier recompute started in background — typically ~5-10 minutes",
        }
    except Exception as e:
        logger.error(f"Manual industry dossier refresh failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to start industry dossier refresh")


@router.get("/industry-dossier")
async def list_industry_dossier(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    user: dict = Depends(get_current_user_or_guest),
):
    """Audit view — every industry_dossier row plus a per-grain summary
    AND the latest Phase B (AI override) run summary.

    Returns:
        {
          "summary": {"industry": 65, "sector": 83, "all_industry": 8},
          "total": 156,
          "computed_at_latest": "...",
          "last_override_run": {
              "run_id": "...",
              "computed_at": "...",
              "status_counts": {"applied": 7, "rejected_sanity": 1, ...},
              "rows": [...]
          },
          "rows": [...]
        }

    Use this after triggering /refresh-industry-dossier to verify the
    quarterly recompute produced sane values. Public-readable from
    Supabase too — this endpoint bundles it with the summary counts.
    """
    _authorize_admin(user, x_admin_token)
    try:
        from collections import Counter
        from app.database import get_supabase

        sb = get_supabase()
        res = (
            sb.table("industry_dossier")
            .select("*")
            .order("sector", desc=False)
            .order("industry", desc=False)
            .execute()
        )
        rows = res.data or []
        summary = dict(Counter(r.get("source_grain") for r in rows))
        latest_computed = max(
            (r.get("computed_at") for r in rows if r.get("computed_at")),
            default=None,
        )

        # Latest Phase B (AI override) run summary
        last_override_run = None
        try:
            audit_res = (
                sb.table("industry_override_audit")
                .select("*")
                .order("computed_at", desc=True)
                .limit(50)  # ≥ 9 curated industries; 50 leaves room for growth
                .execute()
            )
            audit_rows = audit_res.data or []
            if audit_rows:
                most_recent_run = audit_rows[0].get("run_id")
                run_rows = [r for r in audit_rows if r.get("run_id") == most_recent_run]
                last_override_run = {
                    "run_id": most_recent_run,
                    "computed_at": run_rows[0].get("computed_at"),
                    "status_counts": dict(Counter(r.get("status") for r in run_rows)),
                    "rows": run_rows,
                }
        except Exception as audit_exc:
            logger.warning(f"Failed to load override audit log: {audit_exc}")

        return {
            "summary": summary,
            "total": len(rows),
            "computed_at_latest": latest_computed,
            "last_override_run": last_override_run,
            "rows": rows,
        }
    except Exception as e:
        logger.error(f"List industry dossier failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to load industry dossier")


@router.post("/refresh-industry-overrides")
async def refresh_industry_overrides(
    dry_run: bool = False,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    user: dict = Depends(get_current_user_or_guest),
):
    """Manually trigger Phase B (AI-driven research overrides) only —
    without re-running Phase A. Useful for:

    - Smoke-testing Gemini prompts + validation gates after a code change
    - `dry_run=true` to see what Gemini would produce WITHOUT writing
      anything to Supabase (no audit log, no dossier mutations).

    Auth: `X-Admin-Token` OR email-allowlisted user.

    Returns the per-industry summary immediately (this is synchronous —
    9 industries × ~5 sec each = ~45 sec total).
    """
    _authorize_admin(user, x_admin_token)
    try:
        from app.services.industry_override_service import get_industry_override_service

        service = get_industry_override_service()
        summary = await service.refresh_all_overrides(dry_run=dry_run)
        return summary
    except Exception as e:
        logger.error(f"Manual industry override refresh failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to refresh industry overrides: {e}")
