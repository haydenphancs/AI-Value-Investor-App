"""
Admin endpoints — operational triggers for background jobs.
"""

import asyncio
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api.error_response import ErrorCode, auth_error
from app.config import settings
from app.dependencies import GUEST_USER_ID, get_current_user_or_guest

logger = logging.getLogger(__name__)

router = APIRouter()


def _authorize_admin(
    user: Optional[dict],
    x_admin_token: Optional[str],
) -> None:
    """Allow either (a) an authenticated user whose `users.is_admin` flag is set
    or (b) an `X-Admin-Token` header that matches settings.ADMIN_TOKEN.

    ⚠️ **Authorization is on a DATABASE FLAG, never on the email address.** This used to be
    `user.get("email") in {"haiphan@caydexinvest.com", "admin@caydexinvest.com"}`, and an
    email claim is not a credential: Supabase auto-sets `email_confirmed_at` whenever the
    project's "Confirm email" setting is off, so `POST /auth/register` would mint a real
    session for an address nobody owns (`auth.py:300` — "Confirmation disabled project-side")
    and hand the registrant every route in this file. Do not reintroduce an email comparison
    here, and do not add a fallback to one "so it keeps working" — set `is_admin` in the
    database instead (migration 113). `tests/test_users_endpoint_guards.py` fails the build
    if an allowlist reappears.

    Raises 401 AUTH_REQUIRED when the caller presented no credential at all, and 403
    AUTH_FORBIDDEN when they did but are not an admin. The token path exists so
    dev/maintenance scripts can trigger benchmark recomputes without the iOS sign-in flow.

    The split matters and used to be missing. Every route here takes
    ``get_current_user_or_guest``, so a TOKENLESS caller resolves to the guest sentinel, fails
    the checks below, and used to receive a bare-string 403 — answering a missing credential
    with 403 is precisely the shape ``.claude/rules/auth.md`` rule 2 bans, because iOS only
    attempts recovery on 401 and so never tries. These were also the two sites the
    ``AUTH_FORBIDDEN`` enum comment named, while the code emitted the value nowhere: iOS has
    had a carefully-reasoned branch for it that could never execute.
    """
    token = settings.ADMIN_TOKEN
    # Compare BYTES, not str. `secrets.compare_digest` raises
    # `TypeError: comparing strings with non-ASCII characters is not supported` when either
    # side is a non-ASCII `str`, and Starlette decodes header values as latin-1 — so any byte
    # >0x7F in `X-Admin-Token` arrived here as a non-ASCII str and took down EVERY route in
    # this file with an unauthenticated 500 (nothing catches TypeError; it falls to the
    # generic handler). Encoding first keeps the comparison constant-time and makes a junk
    # header simply not match. `errors="ignore"` cannot raise on any input.
    if token and x_admin_token and secrets.compare_digest(
        x_admin_token.encode("utf-8", "ignore"), token.encode("utf-8", "ignore")
    ):
        return
    # `is True` deliberately: a Supabase row can carry the column as NULL (a row written
    # before migration 113) and `if user.get("is_admin")` would also accept the string
    # "false", which is what a JSON round-trip through some clients produces.
    if user and user.get("is_admin") is True:
        return
    # No credential of either kind → 401, not 403.
    #
    # ⚠️ `is_guest` alone is NOT enough here, and that made the 401 branch below dead code.
    # The four `*_identity` wrappers in dependencies.py stamp `is_guest` on their sentinels,
    # but the BASE `get_current_user_or_guest` — the one every route in this file actually
    # depends on — returns a bare `{"id": GUEST_USER_ID, "email": "guest@local", "tier":
    # "free"}` with no such key. So `user.get("is_guest")` read None for a completely
    # credential-less caller, `presented_nothing` was False, and a missing credential was
    # answered with 403 — exactly the shape `.claude/rules/auth.md` rule 2 bans, and the very
    # thing the comment above claimed to have fixed. Test the sentinel id as well.
    #
    # This also catches the "valid token but no public.users row" path, which returns the same
    # sentinel. 401 is the right answer there too: the credential is the problem, not the
    # permission.
    presented_nothing = not x_admin_token and (
        user is None
        or user.get("is_guest")
        or user.get("id") == GUEST_USER_ID
    )
    # Log enough to debug without leaking the actual secret.
    # `is_admin=None` (rather than False) is the tell that migration 113 has not been applied
    # to this database — worth distinguishing from a genuine "you are not an admin".
    logger.warning(
        "Admin auth failed: server_token_set=%s, header_present=%s, "
        "header_len=%d, server_len=%d, user_id=%r, is_guest=%s, is_admin=%r",
        bool(token),
        bool(x_admin_token),
        len(x_admin_token or ""),
        len(token or ""),
        user.get("id") if user else None,
        bool(user.get("is_guest")) if user else None,
        user.get("is_admin", None) if user else None,
    )
    if presented_nothing:
        raise auth_error(
            ErrorCode.AUTH_REQUIRED,
            message="admin route reached without any credential",
        )
    raise auth_error(
        ErrorCode.AUTH_FORBIDDEN,
        message="caller is authenticated but not an admin",
    )


# NOTE: `GET /admin/auth-debug` was removed (2026-07-30). It was public and
# returned both `server_token_length` and `"match": server == x_admin_token` —
# an exact length disclosure plus an unthrottled equality ORACLE on
# ADMIN_TOKEN, callable by anyone. Nothing is lost: every other route here
# already authorizes, so a legitimate token-holder can simply call one and read
# the 200-vs-403, and `_authorize_admin` logs the same diagnosis server-side.


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


# ── Notification verification ────────────────────────────────────────────────
#
# The hard question about a notification system is not "does APNs work" — it is
# "would THIS sender pick the right people, and if it skipped someone, why?".
# That decision is where the bugs live, and until now it was unobservable: the
# Simulator cannot receive remote push, `main.py` skips every background job in
# local dev, and the old `push_send_log` recorded only (user, key, timestamp).
#
# These two endpoints close that gap, cheapest first:
#   * /preview — runs the audience selector and the full decision ladder
#     (child preference AND group master → per-category cap → quiet hours) and
#     returns the per-user verdict WITHOUT claiming or sending anything. Safe to
#     run against production repeatedly; it writes nothing.
#   * /test — the real path to the CALLING ADMIN's own devices only. The only
#     thing that proves the APNs leg, the entitlement, and the tap route on a
#     physical phone.


@router.post("/notifications/preview")
async def preview_notification(
    kind: str,
    ticker: Optional[str] = None,
    limit: int = 25,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    user: dict = Depends(get_current_user_or_guest),
):
    """Dry-run a notification's AUDIENCE and DECISIONS. Writes nothing, sends nothing.

    Args:
        kind: a `NotificationKind` key, e.g. `ticker_move`, `earnings_upcoming`.
        ticker: required for the ticker-fan-out kinds; selects the watchers.
        limit: cap on the number of per-user verdicts returned.

    Returns the resolved audience size plus a per-user `{send, reason}`, where
    `reason` is one of `ok`, `preference_off:<key>`, `cap_reached:<category>:<n>`,
    or `quiet_hours` (with the wake time). That vocabulary is what makes "I didn't
    get my alert" answerable.
    """
    _authorize_admin(user, x_admin_token)

    from datetime import datetime, timezone

    from app.services.notification_kinds import NOTIFICATION_KINDS, get_kind
    from app.services.push_dispatch_service import get_push_dispatch_service

    try:
        nkind = get_kind(kind)
    except KeyError:
        raise HTTPException(
            status_code=400,
            detail=f"unknown kind {kind!r}; registered: {sorted(NOTIFICATION_KINDS)}",
        )

    service = get_push_dispatch_service()
    now = datetime.now(timezone.utc)

    if ticker:
        audience = await asyncio.to_thread(service.watchers_of, ticker)
    else:
        # No ticker: preview against the caller alone. Enumerating every user would be
        # both slow and pointless — the interesting question for a non-fan-out kind is
        # "what would I get?".
        audience = [user["id"]] if user and user.get("id") else []

    if not audience:
        return {
            "kind": kind, "ticker": ticker, "audience": 0,
            "note": "no recipients — for a ticker kind this means nobody watches it",
            "decisions": [],
        }

    recipients = await asyncio.to_thread(service.resolve_recipients, audience, nkind, now)

    decisions = []
    would_send = 0
    for uid in audience[:max(limit, 0)]:
        recipient = recipients.get(uid)
        if recipient is None:
            continue
        d = service.decide(recipient, nkind, now)
        would_send += 1 if d.send else 0
        decisions.append({
            "user_id": uid,
            "send": d.send,
            "reason": d.reason,
            "deliver_after": d.deliver_after.isoformat() if d.deliver_after else None,
            "devices": len(recipient.devices),
            "category_sent_today": recipient.category_sent_today,
        })

    return {
        "kind": kind,
        "ticker": ticker,
        "category": nkind.category,
        "preference_key": nkind.preference_key,
        "master_preference_key": nkind.master_preference_key,
        "interruption_level": nkind.interruption_level,
        "audience": len(audience),
        "would_send_in_sample": would_send,
        "sampled": len(decisions),
        "decisions": decisions,
    }


@router.post("/notifications/test")
async def test_notification(
    kind: str,
    title: str = "Caydex test",
    body: str = "If you can read this, push is working end to end.",
    ticker: Optional[str] = None,
    dedup_suffix: str = "manual",
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    user: dict = Depends(get_current_user_or_guest),
):
    """Send a REAL notification through the full pipeline, to the calling admin only.

    Deliberately restricted to `user["id"]`: a test-send endpoint that accepts an
    arbitrary recipient is a spam primitive behind one credential. Verifying someone
    else's device means signing in as them.

    `dedup_suffix` makes the key unique per invocation — without it the second call
    conflicts on the claim and silently does nothing, which reads exactly like a
    broken APNs configuration.

    Honours `PUSH_DRY_RUN`: with it on, the ledger row is written and no APNs POST is
    made, so this is safe to call before Apple is configured.
    """
    _authorize_admin(user, x_admin_token)

    if not user or not user.get("id") or user.get("is_guest"):
        # The X-Admin-Token path authorizes without identifying anyone, so there is no
        # device to send to. Say so plainly rather than returning a confusing sent=0.
        raise HTTPException(
            status_code=400,
            detail="test-send requires a signed-in admin (the X-Admin-Token path has "
                   "no associated device); sign in and retry",
        )

    from app.services.notification_kinds import NOTIFICATION_KINDS, get_kind
    from app.services.push_dispatch_service import get_push_dispatch_service

    try:
        get_kind(kind)
    except KeyError:
        raise HTTPException(
            status_code=400,
            detail=f"unknown kind {kind!r}; registered: {sorted(NOTIFICATION_KINDS)}",
        )

    route = {"ticker": (ticker or "AAPL").upper()}
    sent = await get_push_dispatch_service().notify_users(
        [user["id"]],
        kind=kind,
        title=title,
        body=body,
        dedup_key=f"admintest:{kind}:{dedup_suffix}",
        route=route,
    )
    return {
        "kind": kind,
        "user_id": user["id"],
        "sent": sent,
        "dry_run": settings.PUSH_DRY_RUN,
        "note": (
            "sent=0 with dry_run=false usually means: no registered device, the "
            "preference is off, the category cap is reached, quiet hours deferred it, "
            "or this dedup_suffix was already used. Call /notifications/preview for "
            "the exact reason."
        ),
    }
