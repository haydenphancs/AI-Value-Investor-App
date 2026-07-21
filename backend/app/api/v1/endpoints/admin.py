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
_ADMIN_EMAILS: set[str] = {"haiphan@caydexinvest.com", "admin@caydexinvest.com"}


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


@router.post("/refresh-industry-benchmarks")
async def refresh_industry_benchmarks(
    skip_recent_hours: int = 24,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    user: dict = Depends(get_current_user_or_guest),
):
    """Trigger the broad-universe INDUSTRY + sector benchmark recompute (rebuilds
    BOTH levels in `sector_benchmarks` over the small-cap-inclusive universe).
    Returns immediately; runs in the background (~1-3 hrs at FMP Premium, throttled).
    Resumable: re-trigger to resume — sectors with a '' aggregate row newer than
    `skip_recent_hours` are skipped (pass 0 to force a full recompute).

    Auth: `X-Admin-Token: <settings.ADMIN_TOKEN>` OR sign in with an admin email.
    """
    _authorize_admin(user, x_admin_token)
    try:
        from app.services.industry_benchmark_service import (
            get_industry_benchmark_service,
        )

        service = get_industry_benchmark_service()
        skip = skip_recent_hours if skip_recent_hours and skip_recent_hours > 0 else None
        asyncio.create_task(service.recompute_all(skip_if_fresh_hours=skip))
        return {
            "status": "started",
            "message": "Industry benchmark recompute started in background — ~1-3 hrs; re-trigger to resume.",
            "skip_if_fresh_hours": skip,
        }
    except Exception as e:
        logger.error(f"Manual industry benchmark refresh failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to start industry benchmark refresh")


@router.get("/industry-benchmarks-status")
async def industry_benchmarks_status(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    user: dict = Depends(get_current_user_or_guest),
):
    """Live progress of the broad-universe benchmark table: total rows, industry
    rows (industry<>''), sector-aggregate rows (industry=''), and latest computed_at.
    """
    _authorize_admin(user, x_admin_token)
    try:
        from app.database import get_supabase

        sb = get_supabase()

        def _count(query) -> int:
            try:
                return query.execute().count or 0
            except Exception:
                return 0

        total = _count(sb.table("sector_benchmarks").select("id", count="exact").limit(1))
        industry_rows = _count(
            sb.table("sector_benchmarks").select("id", count="exact").neq("industry", "").limit(1)
        )
        sector_rows = _count(
            sb.table("sector_benchmarks").select("id", count="exact").eq("industry", "").limit(1)
        )
        latest = None
        try:
            r = (
                sb.table("sector_benchmarks")
                .select("computed_at").order("computed_at", desc=True).limit(1).execute()
            )
            latest = r.data[0]["computed_at"] if r.data else None
        except Exception:
            pass
        return {
            "total_rows": total,
            "industry_rows": industry_rows,
            "sector_rows": sector_rows,
            "latest_computed_at": latest,
        }
    except Exception as e:
        logger.error(f"industry-benchmarks-status failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to read industry benchmark status")


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


@router.post("/refresh-industry-moat-benchmarks")
async def refresh_industry_moat_benchmarks(
    skip_recent_hours: int = 24,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    user: dict = Depends(get_current_user_or_guest),
):
    """Manually trigger the industry_moat_benchmarks recompute on the
    Railway worker. Returns immediately; the recompute runs in the
    background and writes one row per (industry, pillar) to Supabase.

    Auth: pass `X-Admin-Token: <settings.ADMIN_TOKEN>` OR sign in with
    an email on the admin allowlist.

    Args:
        skip_recent_hours: Skip any industry that already has a
            benchmark row newer than this many hours. Lets a previously
            interrupted run resume without redoing finished work.
            Default 24. Pass 0 to force a full recompute.

    Notes:
      - With FMP Premium (3000/min) the full 156-industry backfill
        takes ~60-90 min at the service's tuned concurrency.
      - Progress can be inspected via:
            GET /api/v1/admin/industry-moat-benchmarks-status
      - The same code runs quarterly inside `_run_industry_dossier_job`
        in app.main lifespan — this endpoint just lets you trigger it
        on-demand.
    """
    _authorize_admin(user, x_admin_token)
    try:
        from app.services.industry_moat_benchmark_service import (
            get_industry_moat_benchmark_service,
        )

        service = get_industry_moat_benchmark_service()
        # Coerce 0/negative to None so the service treats it as "no skip".
        skip = skip_recent_hours if skip_recent_hours and skip_recent_hours > 0 else None
        asyncio.create_task(
            service.recompute_all(skip_if_fresh_hours=skip)
        )
        return {
            "status": "started",
            "message": (
                "Industry moat benchmark recompute started in background — "
                "typically ~60-90 minutes at FMP Premium (3000/min). "
                "Poll /admin/industry-moat-benchmarks-status for progress."
            ),
            "skip_if_fresh_hours": skip,
        }
    except Exception as e:
        logger.error(f"Manual industry moat benchmark refresh failed: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to start industry moat benchmark refresh",
        )


@router.get("/industry-moat-benchmarks-status")
async def industry_moat_benchmarks_status(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    user: dict = Depends(get_current_user_or_guest),
):
    """Live progress view for the moat benchmark recompute. Returns row
    count, distinct industry count, and per-pillar coverage so the
    operator can watch the backfill fill in.
    """
    _authorize_admin(user, x_admin_token)
    try:
        from app.database import get_supabase

        sb = get_supabase()
        # Total rows
        total = sb.table("industry_moat_benchmarks").select(
            "id", count="exact",
        ).execute()
        # Per-pillar counts
        rows = sb.table("industry_moat_benchmarks").select(
            "industry,pillar_name,sample_size,computed_at",
        ).execute()
        pillar_counts: dict[str, int] = {}
        industries: set[str] = set()
        latest_computed: Optional[str] = None
        for r in rows.data or []:
            pillar_counts[r["pillar_name"]] = pillar_counts.get(r["pillar_name"], 0) + 1
            industries.add(r["industry"])
            ts = r.get("computed_at")
            if ts and (latest_computed is None or ts > latest_computed):
                latest_computed = ts
        return {
            "total_rows": total.count,
            "distinct_industries": len(industries),
            "pillar_coverage": pillar_counts,
            "latest_computed_at": latest_computed,
        }
    except Exception as e:
        logger.error(f"Industry moat benchmark status failed: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to read benchmark status",
        )


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
