"""
Admin endpoints — operational triggers for background jobs.
"""

import asyncio
import logging
import math
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api.error_response import (
    ErrorCode,
    auth_error,
    error_response_from_exception,
    make_error_response,
)
from app.config import settings
from app.dependencies import GUEST_USER_ID, get_current_user_or_guest
from app.utils.supabase_async import sb_exec

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Background work started by the routes below ─────────────────────────────
#
# `asyncio.create_task` keeps only a WEAK reference to its task, so a bare fire-and-forget
# recompute can be garbage-collected mid-run (the documented CPython caveat), and nothing
# retrieves its exception: a recompute that raised after the route answered
# `200 {"status": "started"}` left no trace at all. This is the per-request counterpart of
# `main._spawn` — hold the handle until it finishes, and say how it ended.
_admin_tasks: set[asyncio.Task] = set()


def _on_admin_task_done(task: asyncio.Task) -> None:
    _admin_tasks.discard(task)
    if task.cancelled():
        logger.warning("Admin background task %r was CANCELLED before it finished", task.get_name())
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Admin background task %r FAILED (%s: %s)",
            task.get_name(), type(exc).__name__, exc, exc_info=exc,
        )
    else:
        logger.info("Admin background task %r completed: %.500s", task.get_name(), task.result())


def _spawn_admin_task(coro, name: str) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    _admin_tasks.add(task)
    task.add_done_callback(_on_admin_task_done)
    return task


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

    🔴 Re-pointed at `industry_benchmark_service.recompute_all`, which is the LIVE producer
    of these rows. It previously called `sector_benchmark_service.compute_all_benchmarks`,
    and that path is now a data-corruption button:

      * `sp500-constituent` is a BLOCKED path under the current FMP entitlement;
      * `get_sp500_constituents` swallows the refusal into `[]` (warning, no exception);
      * the service then fell back to 55 hardcoded tickers — 5 per sector against
        `MIN_SAMPLE_SIZE = 5`, so each cleared the sample gate at exactly the boundary;
      * and upserted those 5-company medians over the ~5,700-company rows.

    Because the work is dispatched with `asyncio.create_task`, the caller got
    `200 {"status": "started"}` and the damage happened silently in the background. Thirteen
    services read `sector_benchmarks`, including moat scoring, health check, the
    valuation/growth snapshots and the AI report collector.

    `recompute_all` writes the industry rows AND the `industry = ''` sector aggregate in one
    pass from `benchmark_universe.json` — see the retirement note in `main.py`.

    Args:
        backfill: If True, recompute every period regardless of freshness.
                  If False (default), skip rows refreshed in the last 24h.
    """
    _authorize_admin(user, x_admin_token)
    try:
        from app.services.industry_benchmark_service import get_industry_benchmark_service

        service = get_industry_benchmark_service()
        # `force` has no analogue here; freshness is expressed as a window.
        skip_hours = None if backfill else 24
        _spawn_admin_task(
            service.recompute_all(skip_if_fresh_hours=skip_hours),
            "admin_refresh_sector_benchmarks",
        )
        mode = "backfill (ignore freshness)" if backfill else "daily (skip rows fresher than 24h)"
        return {"status": "started", "message": f"Sector + industry benchmark computation started in background — mode: {mode}"}
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
        _spawn_admin_task(
            service.recompute_all(skip_if_fresh_hours=skip),
            "admin_refresh_industry_benchmarks",
        )
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

        degraded = False

        def _count(label: str, query) -> Optional[int]:
            """The count, or None when we could not read it — NEVER 0.

            `0` here was indistinguishable from a wiped `sector_benchmarks` table, with
            nothing logged anywhere: a transient PostgREST failure (the h2 stale-connection
            reuse and Cloudflare 520 shapes `app/database.py` documents) rendered as
            "the benchmarks are gone", inviting an operator to re-trigger a 1-3 hour
            throttled FMP recompute against a table that was fine. The partial case was
            worse still — one failed read gave `total_rows: 5704, industry_rows: 0`, a
            state the table cannot be in.
            """
            nonlocal degraded
            try:
                return query.execute().count or 0
            except Exception as e:
                degraded = True
                logger.warning(
                    "industry-benchmarks-status: %s count failed (%s: %s)",
                    label, type(e).__name__, e,
                )
                return None

        total = (await asyncio.to_thread(_count, "total", sb.table("sector_benchmarks").select("id", count="exact").limit(1)))
        industry_rows = (await asyncio.to_thread(_count, "industry",
            sb.table("sector_benchmarks").select("id", count="exact").neq("industry", "").limit(1),))
        sector_rows = (await asyncio.to_thread(_count, "sector",
            sb.table("sector_benchmarks").select("id", count="exact").eq("industry", "").limit(1),))
        latest = None
        try:
            r = (
                (await sb_exec(
                    sb.table("sector_benchmarks")
                    .select("computed_at").order("computed_at", desc=True).limit(1)
                ))
            )
            latest = r.data[0]["computed_at"] if r.data else None
        except Exception as e:
            degraded = True
            logger.warning(
                "industry-benchmarks-status: latest computed_at read failed (%s: %s)",
                type(e).__name__, e, exc_info=True,
            )
        return {
            "total_rows": total,
            "industry_rows": industry_rows,
            "sector_rows": sector_rows,
            "latest_computed_at": latest,
            # True when ANY read above failed, so the operator reads "unknown" rather than
            # inferring "empty" from the zeros this used to fabricate.
            "degraded": degraded,
        }
    except Exception as e:
        logger.error(f"industry-benchmarks-status failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to read industry benchmark status")


# Operator copy for a refused claim, keyed by the reason `_dossier_claim_refusal` reads off
# the job ledger. Admin-facing (curl / scripts), never shown in the app.
_DOSSIER_REFUSAL_COPY: dict[str, str] = {
    "held": (
        "An industry dossier recompute is already running (the quarterly chain or an earlier "
        "manual refresh). Nothing new was started — wait for it to finish."
    ),
    "already_ran_today": (
        "The industry dossier already completed a run today (UTC). A second run would repeat "
        "the whole Census/FRED recompute, so nothing was started. Retry after 00:00 UTC, or "
        "clear run_day for this job in notification_job_state if a re-run is truly needed."
    ),
    "disabled": (
        "The industry dossier job is switched off (notification_job_state.enabled = false). "
        "Nothing was started."
    ),
    "claim_failed": (
        "Could not take the industry dossier job claim (the ledger RPC failed). Nothing was "
        "started — retry shortly."
    ),
    "ledger_unreadable": (
        "Could not take or read the industry dossier job claim. Nothing was started — "
        "retry shortly."
    ),
}


def _dossier_claim_refusal(state: Optional[dict], today: str) -> str:
    """Why `claim_scheduled_job` said no, read off the ledger row — the same three-way split
    `main._run_claimed_phase` makes (ran today / held / RPC failed), plus the kill switch.
    `state` is `notification_jobs.scheduled_job_state(...)`: None when unreadable."""
    if state is None:
        return "ledger_unreadable"
    if not state.get("enabled", True):
        return "disabled"
    if str(state.get("run_day") or "")[:10] == today:
        return "already_ran_today"
    if state.get("claim_at"):
        return "held"
    # Enabled, not run today, nobody holding it — and still refused: `claim_scheduled`
    # returns False on any RPC error (fail closed). (Or the holder released it between our
    # claim and this read; either way a retry is the right answer.)
    return "claim_failed"


async def _run_claimed_dossier_refresh(job: str, claimed_at: datetime) -> dict:
    """Body of the manual refresh: run `recompute_all` while holding `job`'s claim, then
    release it with the outcome.

    This is `notification_jobs.claimed_scheduled_job` split across the request and the
    background task — the claim must be taken IN the request, so a refusal can be answered
    with a 409 instead of a `200 started` that silently does nothing. Same semantics: the
    release is stamped with the claim time, shielded from cancellation, and records a
    failure (so the day stays open for a retry) unless the run provably did its job.
    """
    from app.services import notification_jobs
    from app.services.industry_dossier_service import (
        IndustryDossierRecomputeSkipped,
        get_industry_dossier_service,
    )

    success, items, error = False, 0, None
    try:
        summary = await get_industry_dossier_service().recompute_all(force=True)
        status = summary.get("status") if isinstance(summary, dict) else None
        upserted = summary.get("rows_upserted") if isinstance(summary, dict) else None
        items = upserted if isinstance(upserted, int) and not isinstance(upserted, bool) else 0
        # Success is "it ran AND wrote rows". A run that returned but upserted nothing (the
        # pre-read failed, every upsert failed, or every industry resolved to a placeholder
        # over a real row) leaves the day OPEN: marking it would refuse a same-day re-run
        # and, on a quarter-start Sunday, make the scheduled phase skip a run that changed
        # nothing.
        success = status == "ok" and items > 0
        if not success:
            error = f"manual refresh returned status={status!r}, rows_upserted={upserted!r}"
            logger.warning(
                "Manual industry dossier refresh did not settle the %s claim: %s", job, error,
            )
        return summary
    except IndustryDossierRecomputeSkipped as exc:
        # The service already logged the cause at ERROR; this ties it to the trigger. Not
        # re-raised: it is a refusal to run, not a crash, and the done-callback would
        # otherwise log it a second time at ERROR.
        error = str(exc)
        logger.warning("Manual industry dossier refresh did not run: %s", exc)
        return {"status": "skipped", "reason": error}
    except asyncio.CancelledError:
        error = "cancelled (shutdown)"
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise  # logged with its stack by `_on_admin_task_done`
    finally:
        # SHIELDED for the same reason as `claimed_scheduled_job`: CancelledError would
        # otherwise abort the release and park the claim for the whole stale window, which
        # the quarterly chain then waits out.
        await asyncio.shield(
            asyncio.to_thread(
                notification_jobs.finish_scheduled,
                job,
                success=success,
                items=items,
                error=error,
                now=claimed_at,
            )
        )


@router.post("/refresh-industry-dossier")
async def refresh_industry_dossier(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    user: dict = Depends(get_current_user_or_guest),
):
    """Manually trigger the industry_dossier recompute (Census/FRED; the Gemini grounded
    Phase B was retired 2026-10-02). Returns immediately; the recompute runs in the
    background and takes ~5-10 minutes.

    Runs under the QUARTERLY CHAIN'S OWN day-keyed claim (`JOB_INDUSTRY_DOSSIER_QUARTERLY`,
    migration 147). It used to run outside it, so a manual refresh could overlap the
    scheduled phase — two upserts racing on the same rows. Now:

      * the scheduled phase is running (or an earlier manual one is) → 409, nothing started;
      * a run already completed today (UTC) → 409; a second would only repeat it;
      * the job's kill switch is off → 409;
      * the scheduled phase starting while this runs finds the claim HELD, waits, and —
        when this run settled the day — skips instead of re-running.

    Refusals are `SYSTEM_BUSY` bodies whose `details.reason` is one of `held`,
    `already_ran_today`, `disabled`, `claim_failed` (409) or `ledger_unreadable` (503).

    Auth: `X-Admin-Token: <settings.ADMIN_TOKEN>` OR a signed-in user with `users.is_admin`.
    """
    _authorize_admin(user, x_admin_token)
    try:
        from app.main import _CHAIN_PHASE_STALE_SECONDS, JOB_INDUSTRY_DOSSIER_QUARTERLY
        from app.services import notification_jobs
        # Imported BEFORE the claim, though only the task uses it: the task imports it again
        # outside its try/finally, and an import that failed THERE would leave the claim
        # unreleased for the whole stale window. Here it fails as a 500 with nothing held.
        from app.services.industry_dossier_service import (  # noqa: F401
            IndustryDossierRecomputeSkipped,
            get_industry_dossier_service,
        )

        job = JOB_INDUSTRY_DOSSIER_QUARTERLY
        claimed_at = datetime.now(timezone.utc)
        granted = await asyncio.to_thread(
            notification_jobs.claim_scheduled,
            job,
            now=claimed_at,
            stale_seconds=_CHAIN_PHASE_STALE_SECONDS,
        )
        if not granted:
            state = await asyncio.to_thread(notification_jobs.scheduled_job_state, job)
            reason = _dossier_claim_refusal(state, claimed_at.date().isoformat())
            details: dict[str, Any] = {"job": job, "reason": reason}
            # `details` values must be flat scalars (auth.md §3) — omit the absent ones.
            for key in ("claim_at", "run_day"):
                if state and state.get(key):
                    details[key] = str(state[key])
            logger.warning(
                "Manual industry dossier refresh REFUSED: job=%s reason=%s claim_at=%s run_day=%s",
                job, reason, details.get("claim_at"), details.get("run_day"),
            )
            return make_error_response(
                ErrorCode.SYSTEM_BUSY,
                status_code=503 if reason == "ledger_unreadable" else 409,
                message=f"industry dossier claim refused: {reason}",
                user_message=_DOSSIER_REFUSAL_COPY[reason],
                details=details,
            )

        try:
            _spawn_admin_task(
                _run_claimed_dossier_refresh(job, claimed_at), "admin_refresh_industry_dossier",
            )
        except BaseException:
            # The claim is ours and nothing will release it — do it now, or the quarterly
            # chain waits out the whole stale window.
            await asyncio.to_thread(
                notification_jobs.finish_scheduled,
                job, success=False, error="manual refresh failed to start", now=claimed_at,
            )
            raise
        logger.info("Manual industry dossier refresh started under claim %s", job)
        return {
            "status": "started",
            "job": job,
            "claimed_at": claimed_at.isoformat(),
            "message": "Industry dossier recompute started in background — typically ~5-10 minutes",
        }
    except Exception as e:
        logger.error(
            "Manual industry dossier refresh failed to start (%s: %s)",
            type(e).__name__, e, exc_info=True,
        )
        return error_response_from_exception(e, step="refresh_industry_dossier")


def _row_tam_is_placeholder(row: dict) -> bool:
    """True when an `industry_dossier` row carries no usable TAM: the "No public data
    available — FRED/Census unreachable at compute time" placeholder (`current_tam_b = 0`),
    a NULL, or anything non-finite / non-numeric.

    Decided on the NUMBER, never the label text — the same rule as the read path's
    `industry_dossier_service._is_placeholder` (a parity test pins the two together), so the
    audit counts exactly the rows the self-heal treats as a miss. A malformed value counts as
    a placeholder rather than raising: one bad row must not 500 the audit that exists to
    find bad rows.
    """
    v = row.get("current_tam_b")
    if v is None or isinstance(v, bool):
        return True
    try:
        f = float(v)
    except (TypeError, ValueError):
        return True
    return not (math.isfinite(f) and f > 0)


def _dossier_tam_audit(rows: list[dict]) -> dict[str, Any]:
    """The TAM-health half of the audit summary.

    `summary` counts rows per `source_grain` only, and the zero-TAM placeholder is written
    with `source_grain = 'all_industry'` — so from 2026-07-05, when 138 of 158 rows were
    placeholders, the view read as a healthy "all_industry: 1xx" and hid the outage. These
    counts make a placeholder visible on its own line.
    """
    from collections import Counter

    placeholders = [r for r in rows if _row_tam_is_placeholder(r)]
    global_rows = [r for r in rows if r.get("tam_scope") == "global"]
    return {
        "tam_placeholder_count": len(placeholders),
        "tam_placeholder_industries": sorted(str(r.get("industry") or "") for r in placeholders),
        "tam_placeholder_by_grain": dict(Counter(r.get("source_grain") for r in placeholders)),
        # Rows still holding a retired Gemini grounded-research TAM (`tam_scope='global'`,
        # never served — `_withdraw_grounded_tam`). Expected 0 once migration 188 has run
        # and a recompute has replaced them; kept as the operator's check that it did.
        "global_scope_count": len(global_rows),
        "global_scope_industries": sorted(str(r.get("industry") or "") for r in global_rows),
    }


@router.get("/industry-dossier")
async def list_industry_dossier(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    user: dict = Depends(get_current_user_or_guest),
):
    """Audit view — every industry_dossier row plus a per-grain summary.

    Returns:
        {
          "summary": {"industry": 65, "sector": 83, "all_industry": 8},
          "total": 156,
          "tam_placeholder_count": 2,
          "tam_placeholder_industries": ["...", "..."],
          "tam_placeholder_by_grain": {"all_industry": 2},
          "global_scope_count": 0,
          "global_scope_industries": [],
          "computed_at_latest": "...",
          "rows": [...]
        }

    `summary` is per `source_grain` and counts zero-TAM placeholder rows like real ones
    (the placeholder is stored as `all_industry`); read `tam_placeholder_count` for TAM
    health. Use this after triggering /refresh-industry-dossier to verify the recompute
    produced sane values. The table is service-role only (migration 164), so this endpoint
    is the operator's read path.
    """
    _authorize_admin(user, x_admin_token)
    try:
        from collections import Counter
        from app.database import get_supabase

        sb = get_supabase()
        res = (
            (await sb_exec(
                sb.table("industry_dossier")
                .select("*")
                .order("sector", desc=False)
                .order("industry", desc=False)
            ))
        )
        rows = [r for r in (res.data or []) if isinstance(r, dict)]
        summary = dict(Counter(r.get("source_grain") for r in rows))
        latest_computed = max(
            (r.get("computed_at") for r in rows if r.get("computed_at")),
            default=None,
        )

        return {
            "summary": summary,
            "total": len(rows),
            **_dossier_tam_audit(rows),
            "computed_at_latest": latest_computed,
            "rows": rows,
        }
    except Exception as e:
        logger.error(
            "List industry dossier failed (%s: %s)", type(e).__name__, e, exc_info=True,
        )
        return error_response_from_exception(e, step="list_industry_dossier")


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
        _spawn_admin_task(
            service.recompute_all(skip_if_fresh_hours=skip),
            "admin_refresh_industry_moat_benchmarks",
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
        total = (await sb_exec(
                    sb.table("industry_moat_benchmarks").select(
                    "id", count="exact",
                    )
                ))
        # Per-pillar counts
        rows = (await sb_exec(
                   sb.table("industry_moat_benchmarks").select(
                   "industry,pillar_name,sample_size,computed_at",
                   )
               ))
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
