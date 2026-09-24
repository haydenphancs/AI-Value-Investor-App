"""
Internal marketing-worker API — `/api/v1/internal/marketing/*` (SYSTEM_DESIGN_GUIDELINES §12).

ONE client calls these routes: `marketing/main.py`, the media worker, which runs as
its own Railway cron service from `marketing/Dockerfile` (Kokoro, torch-cpu, Pillow, ffmpeg)
and holds NO Supabase key. Everything it needs from the database — claim the day, checkpoint
a stage, register an artefact, hand over the day's posts — goes through here, and its media
bytes go straight to Storage through a signed upload URL this API mints per object. So the
ML-heavy image can never read a user row or post to a brand account, whatever is in its
transitive dependencies. (A scoped Postgres role behind a custom JWT was the first design;
the project's legacy JWT API keys are disabled and the HS256 signing key revoked, so no such
JWT can be minted — see migration 170's header.)

Auth: a shared secret in `X-Marketing-Worker-Token`, compared constant-time against
`settings.MARKETING_WORKER_TOKEN`, declared ONCE on the router so a route added here
tomorrow is gated by default (the same reason the market-data routers gate at router level —
`.claude/rules/auth.md` §1). Codes follow auth.md §2/§3 exactly: no header at all → 401
`AUTH_REQUIRED`; a header that does not match, or a server with the secret unset → 403
`AUTH_FORBIDDEN`. Unset-on-server is fail-CLOSED and logged at ERROR, so a forgotten Railway
variable is loud on the first cron tick rather than silently open.

This is deliberately NOT one of the iOS-facing auth dependencies: `tests/test_ios_auth_policy_parity.py`
classifies routes by `get_current_user*` / `get_identity_only_user`, and this router takes
neither, so it is invisible to the iOS `authPolicy` contract — correctly, since iOS never
calls it.

Errors from the ledger are mapped through `error_response_from_exception`, so the worker
reads the same `{error_code, message, …}` body every other client does.
"""

from __future__ import annotations

import logging
import secrets
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header

from app.api.error_response import (
    ErrorCode,
    auth_error,
    error_response_from_exception,
    make_error_response,
)
from app.config import settings
from app.schemas.marketing import (
    AssetCompleteResponse,
    AssetRegisterRequest,
    AssetRegisterResponse,
    MarketingAsset,
    MarketingPost,
    MarketingRun,
    PostsCreateRequest,
    PostsCreateResponse,
    RunClaimRequest,
    RunClaimResponse,
    RunUpdateRequest,
    ScriptKickResponse,
    SignedUpload,
)
from app.services.marketing.run_service import (
    claim_window_ok,
    get_marketing_run_service,
    run_date_et,
)
from app.services.marketing.script_service import get_marketing_script_service

logger = logging.getLogger(__name__)

_HEADER = "X-Marketing-Worker-Token"
_unset_warned = False


def require_marketing_worker(
    x_marketing_worker_token: Optional[str] = Header(default=None, alias=_HEADER),
) -> None:
    """Router-level gate. See the module docstring for the 401/403 split."""
    global _unset_warned
    expected = settings.MARKETING_WORKER_TOKEN
    if not x_marketing_worker_token:
        raise auth_error(
            ErrorCode.AUTH_REQUIRED,
            message=f"marketing worker route reached without {_HEADER}",
        )
    if not expected:
        if not _unset_warned:
            _unset_warned = True
            logger.error(
                "MARKETING_WORKER_TOKEN is not set: every marketing worker call answers 403 "
                "(fail-closed). Set it in the Railway environment of BOTH services."
            )
        raise auth_error(
            ErrorCode.AUTH_FORBIDDEN,
            message="marketing worker token is not configured on this server",
        )
    # Bytes, not str: `compare_digest` raises TypeError on non-ASCII str, and Starlette
    # decodes header values as latin-1 (admin.py has the full story).
    if not secrets.compare_digest(
        x_marketing_worker_token.encode("utf-8", "ignore"),
        expected.encode("utf-8", "ignore"),
    ):
        logger.warning(
            "marketing worker auth failed: header_len=%d server_len=%d",
            len(x_marketing_worker_token), len(expected),
        )
        raise auth_error(
            ErrorCode.AUTH_FORBIDDEN,
            message="marketing worker token does not match",
        )


router = APIRouter(dependencies=[Depends(require_marketing_worker)])


def _log_ledger_failure(op: str, exc: BaseException, **ids: object) -> None:
    """Known, mapped failures (`MarketingRunError` and subclasses → MARKETING_* codes) are
    WARNING: the worker retries 5xx three times per stage, and an ERROR here pages Sentry
    once per attempt for one Supabase blip. Anything else is unexpected → ERROR with stack."""
    from app.services.marketing.run_service import MarketingRunError

    detail = ", ".join(f"{k}={v}" for k, v in ids.items())
    if isinstance(exc, MarketingRunError):
        logger.warning("marketing %s failed (%s): %s: %s", op, detail, type(exc).__name__, exc)
    else:
        logger.error(
            "marketing %s failed (%s): %s: %s", op, detail, type(exc).__name__, exc, exc_info=True,
        )


# `claim_window_ok` (imported above) lives in run_service since the kick uses the same window to
# decide whether a run is held: a claim for a date outside today/yesterday ET is refused here,
# and a kick that would start writer spend for such a run is refused there.


@router.post("/runs/claim", response_model=RunClaimResponse)
async def claim_run(body: RunClaimRequest):
    """Claim (or re-claim) the run for one ET day. Never 409s: a run that cannot be claimed
    comes back with `claimed=false` and a `reason` the worker exits 0 on."""
    requested = date.fromisoformat(body.run_date)
    today = run_date_et()
    if not claim_window_ok(requested, today):
        logger.warning("marketing claim REFUSED outside the window run_date=%s today_et=%s",
                       requested, today)
        return make_error_response(
            ErrorCode.INVALID_INPUT, status_code=422,
            message=f"run_date {requested} is outside the claim window ({today - timedelta(days=1)}..{today} ET)",
            details={"run_date": body.run_date, "today_et": today.isoformat()},
        )
    svc = get_marketing_run_service()
    try:
        row, reason = await svc.claim_run(
            date.fromisoformat(body.run_date),
            worker_version=body.worker_version,
            dry_run=body.dry_run,
            claim_nonce=body.claim_nonce,
            resume_only=body.resume_only,
        )
    except Exception as e:
        _log_ledger_failure("claim_run", e, run_date=body.run_date)
        return error_response_from_exception(e, step="marketing_claim_run")
    return RunClaimResponse(
        claimed=(reason == "claimed"),
        reason=reason,
        run=MarketingRun.model_validate(row) if row is not None else None,
    )


#: Fields the worker may NOT set, though the request schema still accepts them (wire
#: compatibility with a pinned worker image). The selection is made and mirrored by the web
#: side (`script_service`); a worker that could rewrite `source_ref` could point the writer at
#: an excluded item.
_SERVER_OWNED_RUN_FIELDS = ("content_class", "template_id", "source_ref")


@router.patch("/runs/{run_id}", response_model=MarketingRun)
async def update_run(run_id: str, body: RunUpdateRequest):
    ignored = [f for f in _SERVER_OWNED_RUN_FIELDS if getattr(body, f) is not None]
    if ignored:
        logger.warning("marketing update_run: ignoring server-owned field(s) %s from the worker "
                       "run_id=%s", ignored, run_id)
    svc = get_marketing_run_service()
    try:
        # worker=True: only its own in_progress run, only WORKER_RUN_STATUSES, stage forward
        # only, and no server-owned metadata key (claim_nonce) — see run_service.update_run. The
        # worker retries this call, so a terminal PATCH whose effect is already there (its first
        # response was lost) answers 200 with the row unchanged instead of 409.
        row = await svc.update_run(
            run_id,
            stage=body.stage,
            status=body.status,
            last_error=body.last_error,
            timings=body.timings,
            metadata=body.metadata,
            finished=body.finished,
            worker=True,
        )
    except Exception as e:
        _log_ledger_failure("update_run", e, run_id=run_id)
        return error_response_from_exception(e, step="marketing_update_run")
    return MarketingRun.model_validate(row)


@router.post("/runs/{run_id}/assets", response_model=AssetRegisterResponse)
async def register_asset(run_id: str, body: AssetRegisterRequest):
    """Insert the asset row and mint a signed upload URL. The worker PUTs the bytes to
    `upload.url` itself; nothing large ever transits this process."""
    svc = get_marketing_run_service()
    try:
        row, upload = await svc.register_asset(
            run_id,
            kind=body.kind,
            ext=body.ext,
            sha256=body.sha256,
            size_bytes=body.bytes,
            duration_seconds=body.duration_seconds,
            metadata=body.metadata,
        )
    except Exception as e:
        _log_ledger_failure("register_asset", e, run_id=run_id, kind=body.kind)
        return error_response_from_exception(e, step="marketing_register_asset")
    return AssetRegisterResponse(
        asset=MarketingAsset.model_validate(row),
        upload=SignedUpload.model_validate(upload) if upload else None,
    )


@router.post("/assets/{asset_id}/complete", response_model=AssetCompleteResponse)
async def complete_asset(asset_id: str):
    """Verify the object landed (HEAD on the bucket) and mark the row `ready`."""
    svc = get_marketing_run_service()
    try:
        row = await svc.complete_asset(asset_id)
    except Exception as e:
        _log_ledger_failure("complete_asset", e, asset_id=asset_id)
        return error_response_from_exception(e, step="marketing_complete_asset")
    return AssetCompleteResponse(asset=MarketingAsset.model_validate(row))


@router.post("/runs/{run_id}/script", response_model=ScriptKickResponse)
async def kick_script(run_id: str):
    """Idempotent kick-and-poll for the day's script (see `script_service`). Answers at once —
    the Gemini work runs in the background, never inside this request."""
    svc = get_marketing_script_service()
    try:
        state = await svc.kick(run_id)
    except Exception as e:
        _log_ledger_failure("kick_script", e, run_id=run_id)
        return error_response_from_exception(e, step="marketing_kick_script")
    return ScriptKickResponse.model_validate(state)


@router.post("/runs/{run_id}/posts", response_model=PostsCreateResponse)
async def create_posts(run_id: str, body: PostsCreateRequest):
    """Record the day's outlets. Rows are born `pending_review` (or `approved` under
    MARKETING_AUTO_PUBLISH, text-only); the publisher loop in the web lifespan does the rest.
    Captions come from the run's accepted script, not from this request (`create_posts`)."""
    svc = get_marketing_run_service()
    try:
        rows = await svc.create_posts(run_id, [p.model_dump() for p in body.posts])
    except Exception as e:
        _log_ledger_failure("create_posts", e, run_id=run_id)
        return error_response_from_exception(e, step="marketing_create_posts")
    return PostsCreateResponse(posts=[MarketingPost.model_validate(r) for r in rows])
