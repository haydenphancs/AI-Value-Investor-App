"""
Internal narrator-worker API — `/api/v1/internal/learn/*` (plan "Wiser weekly content engine" §1.8).

ONE client calls these routes: `backend/learn_narrator/`, the standalone Railway cron service that
renders a Wiser weekly piece's narration (Chatterbox or Kokoro on CPU) and holds NO Supabase key.
It claims a job, heartbeats while it renders, asks where to put the audio, PUTs the bytes straight
to Storage through the signed upload URL minted here, and reports the timing table — or a failure.
Everything the worker may do to the ledger goes through `services/learn_weekly/narration.py`; this
module is only the HTTP surface (marketing_internal.py is the model).

Auth: a shared secret in `X-Learn-Narrator-Token`, compared constant-time against
`settings.LEARN_NARRATOR_TOKEN`, declared ONCE on the router so a route added here tomorrow is gated
by default (`.claude/rules/auth.md` §1 reasoning). Codes follow auth.md §2/§3: no header at all →
401 `AUTH_REQUIRED`; a header that does not match, or a server with the secret unset → 403
`AUTH_FORBIDDEN` (unset is fail-CLOSED and logged at ERROR once, so a forgotten Railway variable is
loud on the first cron tick, never silently open).

Claim: every route except `POST /jobs/claim` must present `X-Learn-Claim: <attempts>.<nonce>` — the
claim `/jobs/claim` handed out — and every ledger write is fenced on both halves, so a worker whose
lease ran out and was re-claimed writes nothing. A missing or malformed claim is a contract breach
(422 LEARN_NARRATOR_REQUEST_INVALID); a well-formed claim that no longer holds the job is 409
LEARN_NARRATOR_JOB_NOT_HELD (also once the job was cancelled — the worker stops rendering).

This router takes none of the iOS auth dependencies on purpose: iOS never calls it, and
`tests/test_ios_auth_policy_parity.py` classifies routes by those dependencies. Every error is the
`{error_code, message, user_message, …}` body through `error_response_from_exception`, with the
worker-facing LEARN_NARRATOR_* codes (404 / 409 / 422 terminal, 503 retried).
"""

from __future__ import annotations

import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from app.api.error_response import (
    ErrorCode,
    auth_error,
    error_response_from_exception,
    make_error_body,
)
from app.config import settings
from app.schemas.learn_weekly import (
    CompleteRequest,
    CompleteResponse,
    FailRequest,
    FailResponse,
    HeartbeatResponse,
    JobClaimRequest,
    JobClaimResponse,
    UploadRequest,
    UploadResponse,
)
from app.services.learn_weekly import narration
from app.services.learn_weekly.errors import LearnNarratorError, LearnWeeklyError
from app.services.learn_weekly.narration import WorkerClaim

logger = logging.getLogger(__name__)

_HEADER = "X-Learn-Narrator-Token"
_unset_warned = False


def require_learn_narrator(
    x_learn_narrator_token: Optional[str] = Header(default=None, alias=_HEADER),
) -> None:
    """Router-level gate. See the module docstring for the 401/403 split."""
    global _unset_warned
    expected = settings.LEARN_NARRATOR_TOKEN
    if not x_learn_narrator_token:
        raise auth_error(ErrorCode.AUTH_REQUIRED, message=f"narrator worker route reached without {_HEADER}")
    if not expected:
        if not _unset_warned:
            _unset_warned = True
            logger.error("LEARN_NARRATOR_TOKEN is not set: every narrator worker call answers 403 "
                         "(fail-closed). Set it on BOTH the web service and the learn_narrator service.")
        raise auth_error(ErrorCode.AUTH_FORBIDDEN, message="narrator worker token is not configured on this server")
    # Bytes, not str: `compare_digest` raises TypeError on non-ASCII str, and Starlette decodes
    # header values as latin-1 (marketing_internal.py / admin.py).
    if not secrets.compare_digest(x_learn_narrator_token.encode("utf-8", "ignore"),
                                  expected.encode("utf-8", "ignore")):
        logger.warning("narrator worker auth failed: header_len=%d server_len=%d",
                       len(x_learn_narrator_token), len(expected))
        raise auth_error(ErrorCode.AUTH_FORBIDDEN, message="narrator worker token does not match")


_CLAIM_HEADER = "X-Learn-Claim"
#: The one route a worker reaches BEFORE it holds a claim (POST only).
_CLAIM_ROUTE_PATH = "/jobs/claim"


def require_job_claim(
    request: Request,
    x_learn_claim: Optional[str] = Header(default=None, alias=_CLAIM_HEADER),
) -> None:
    """Router-level, after the token gate: every route but the claim itself presents the claim it
    holds, parsed onto `request.state.learn_claim`. Exempt by the MATCHED route template and method,
    never by a URL suffix: `/jobs/{job_id}/…` with job_id="claim" still needs a claim."""
    route = request.scope.get("route")
    if request.method == "POST" and str(getattr(route, "path", "")).endswith(_CLAIM_ROUTE_PATH):
        return
    try:
        request.state.learn_claim = WorkerClaim.parse(x_learn_claim)
    except ValueError as e:
        logger.warning("narrator worker call without a valid %s (%s) path=%s", _CLAIM_HEADER, e, request.url.path)
        raise _claim_error(str(e))


def _claim_error(why: str) -> HTTPException:
    return HTTPException(status_code=422, detail=make_error_body(
        ErrorCode.LEARN_NARRATOR_REQUEST_INVALID, message=f"{_CLAIM_HEADER}: {why}"))


def _claim(request: Request) -> WorkerClaim:
    claim = getattr(request.state, "learn_claim", None)
    if claim is None:  # a handler reached without the dependency having parsed a claim
        raise _claim_error("missing claim")
    return claim


router = APIRouter(dependencies=[Depends(require_learn_narrator), Depends(require_job_claim)])


def _failure(op: str, exc: BaseException, **ids: object):
    """Log, then answer the error contract. Known, mapped refusals (the LEARN_NARRATOR_* family) are
    WARNING — the worker retries a 503, and an ERROR per attempt would page Sentry for one Supabase
    blip (the service already logs the serious ones at ERROR). Anything else is unexpected: ERROR
    with the stack."""
    detail = ", ".join(f"{k}={v}" for k, v in ids.items())
    if isinstance(exc, (LearnNarratorError, LearnWeeklyError)):
        logger.warning("learn narrator %s refused (%s): %s: %s", op, detail, type(exc).__name__, exc)
    else:
        logger.error("learn narrator %s failed (%s): %s: %s", op, detail, type(exc).__name__, exc, exc_info=True)
    return error_response_from_exception(exc, step=f"learn_narrator_{op}")


@router.post("/jobs/claim", response_model=JobClaimResponse,
             responses={204: {"description": "No job this worker's engines can render"}})
async def claim_job(body: JobClaimRequest):
    """Claim the oldest narration job whose frozen engine this worker declared. 204 = nothing to do."""
    try:
        job = await narration.claim_for_worker(body.engines, worker_version=body.worker_version)
    except Exception as e:
        return _failure("claim", e, engines=body.engines)
    if job is None:
        return Response(status_code=204)
    return JobClaimResponse.model_validate({"job": job})


@router.post("/jobs/{job_id}/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(job_id: str, claim: WorkerClaim = Depends(_claim)):
    """Extend the lease. 409 once the job was cancelled or re-claimed: stop rendering."""
    try:
        out = await narration.heartbeat(job_id, claim)
    except Exception as e:
        return _failure("heartbeat", e, job_id=job_id)
    return HeartbeatResponse.model_validate(out)


@router.post("/jobs/{job_id}/upload", response_model=UploadResponse)
async def upload(job_id: str, body: UploadRequest, claim: WorkerClaim = Depends(_claim)):
    """Where to PUT the audio: a signed upload URL for the content-addressed path, or the path alone
    (`upload_url: null`) when the same object is already there."""
    try:
        out = await narration.upload_target(job_id, claim, content_type=body.content_type,
                                            size_bytes=body.bytes, sha256=body.sha256)
    except Exception as e:
        return _failure("upload", e, job_id=job_id)
    return UploadResponse.model_validate(out)


@router.post("/jobs/{job_id}/complete", response_model=CompleteResponse)
async def complete(job_id: str, body: CompleteRequest, claim: WorkerClaim = Depends(_claim)):
    """Verify the uploaded object and the timing table, mark the job ready and hand the piece to
    review. A cancelled job or a replay answers 200 and writes nothing."""
    try:
        out = await narration.complete(
            job_id, claim, path=body.path, size_bytes=body.bytes, duration_seconds=body.duration_seconds,
            engine=body.engine, pipeline_version=body.pipeline_version,
            timings=[b.model_dump() for b in body.blocks], checks=body.checks,
        )
    except Exception as e:
        return _failure("complete", e, job_id=job_id)
    return CompleteResponse.model_validate(out)


@router.post("/jobs/{job_id}/fail", response_model=FailResponse)
async def fail(job_id: str, body: FailRequest, claim: WorkerClaim = Depends(_claim)):
    """The worker gives up on this render (re-queued under the cap, failed at it)."""
    try:
        out = await narration.fail(job_id, claim, code=body.code, detail=body.detail)
    except Exception as e:
        return _failure("fail", e, job_id=job_id, code=body.code)
    return FailResponse.model_validate(out)
