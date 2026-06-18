"""
Structured error responses for the report pipeline.

The iOS `APIErrorResponse` schema (Core/Services/APIClient.swift) already
decodes:
    {error_code, message, user_message, action?, details?}
…on 403/422 paths. Phase 3 extends that contract to 502/503 errors from
the report-generation path so iOS can show a debuggable, actionable
message instead of "Server error (502)".

Public API:
  - `ErrorCode` — enum of every machine-readable code we emit
  - `make_error_response(...)` — build a `JSONResponse` with the
    structured body
  - `classify_exception(exc)` — inspect an exception and return
    `(ErrorCode, http_status)` based on its class + message regex
  - `error_response_from_exception(exc, ...)` — one-line wrapper that
    classifies + builds the response with `details.underlying` carrying
    the truncated underlying error text for production debugging

The classifier looks at exception class names and message keywords —
it does NOT import google.api_core or httpx at module load, so the
helper stays cheap to import and resilient to dependency changes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, Tuple

from fastapi.responses import JSONResponse


# ── Error code registry ───────────────────────────────────────────────


class ErrorCode(str, Enum):
    """Every code iOS may receive. Add new codes here so the iOS
    `userFriendlyError` switch covers them centrally."""

    # ── Input / lookup ────────────────────────────────────────────────
    TICKER_NOT_FOUND = "TICKER_NOT_FOUND"
    INVALID_PERSONA = "INVALID_PERSONA"
    INVALID_INPUT = "INVALID_INPUT"

    # ── Upstream services ────────────────────────────────────────────
    FMP_RATE_LIMITED = "FMP_RATE_LIMITED"
    FMP_UNAVAILABLE = "FMP_UNAVAILABLE"
    GEMINI_QUOTA_EXCEEDED = "GEMINI_QUOTA_EXCEEDED"
    GEMINI_UNAVAILABLE = "GEMINI_UNAVAILABLE"

    # ── Data / pipeline ──────────────────────────────────────────────
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    REPORT_GENERATION_FAILED = "REPORT_GENERATION_FAILED"

    # ── Research-flow specific ───────────────────────────────────────
    REPORT_NOT_FOUND = "REPORT_NOT_FOUND"
    REPORT_NOT_READY = "REPORT_NOT_READY"
    INSUFFICIENT_CREDITS = "INSUFFICIENT_CREDITS"
    TOO_MANY_CONCURRENT_REPORTS = "TOO_MANY_CONCURRENT_REPORTS"
    # Global overload backstop — distinct from the per-user cap above. The
    # whole service is at capacity, not just this user. 409 (not 429) so iOS
    # surfaces the user_message instead of a generic "wait 60s".
    SYSTEM_BUSY = "SYSTEM_BUSY"


# Default user-facing copy per code. Endpoints can override per-call.
_USER_MESSAGES: Dict[ErrorCode, str] = {
    ErrorCode.TICKER_NOT_FOUND: (
        "We couldn't find that ticker symbol. Check the spelling and try again."
    ),
    ErrorCode.INVALID_PERSONA: (
        "That investor persona isn't supported."
    ),
    ErrorCode.INVALID_INPUT: (
        "The request was missing or malformed."
    ),
    ErrorCode.FMP_RATE_LIMITED: (
        "Market data is rate-limited right now. Please try again in a minute."
    ),
    ErrorCode.FMP_UNAVAILABLE: (
        "Our market data provider is temporarily unavailable. Try again shortly."
    ),
    ErrorCode.GEMINI_QUOTA_EXCEEDED: (
        "AI analysis quota exceeded. Please try again in a few minutes."
    ),
    ErrorCode.GEMINI_UNAVAILABLE: (
        "The AI analysis engine is temporarily unavailable. Try again shortly."
    ),
    ErrorCode.DATA_INCOMPLETE: (
        "We couldn't gather enough data for this ticker to produce a full report."
    ),
    ErrorCode.REPORT_GENERATION_FAILED: (
        "The report failed to generate. Please try again."
    ),
    ErrorCode.REPORT_NOT_FOUND: (
        "That report no longer exists."
    ),
    ErrorCode.REPORT_NOT_READY: (
        "The report is still generating. Try again in a few seconds."
    ),
    ErrorCode.INSUFFICIENT_CREDITS: (
        "You don't have enough credits. Upgrade your tier or wait for the monthly reset."
    ),
    # Number-free default so the cap value never drifts here; the endpoint
    # overrides user_message with the live cap (e.g. "up to 4 at once").
    ErrorCode.TOO_MANY_CONCURRENT_REPORTS: (
        "You're already running the maximum number of analyses at once. "
        "Wait for one to finish, then try again."
    ),
    ErrorCode.SYSTEM_BUSY: (
        "Our analysis engine is at capacity right now. "
        "Please try again in a moment."
    ),
}


# Suggested user action per code (optional, shown as a button label / hint).
_DEFAULT_ACTIONS: Dict[ErrorCode, str] = {
    ErrorCode.TICKER_NOT_FOUND: "check_symbol",
    ErrorCode.FMP_RATE_LIMITED: "retry_later",
    ErrorCode.FMP_UNAVAILABLE: "retry_later",
    ErrorCode.GEMINI_QUOTA_EXCEEDED: "retry_later",
    ErrorCode.GEMINI_UNAVAILABLE: "retry_later",
    ErrorCode.REPORT_NOT_READY: "poll_again",
    ErrorCode.INSUFFICIENT_CREDITS: "upgrade",
    ErrorCode.TOO_MANY_CONCURRENT_REPORTS: "retry_later",
    ErrorCode.SYSTEM_BUSY: "retry_later",
}


# Default HTTP status per code (endpoints may override).
_DEFAULT_STATUS: Dict[ErrorCode, int] = {
    ErrorCode.TICKER_NOT_FOUND: 404,
    ErrorCode.INVALID_PERSONA: 400,
    ErrorCode.INVALID_INPUT: 400,
    ErrorCode.FMP_RATE_LIMITED: 502,
    ErrorCode.FMP_UNAVAILABLE: 502,
    ErrorCode.GEMINI_QUOTA_EXCEEDED: 502,
    ErrorCode.GEMINI_UNAVAILABLE: 502,
    ErrorCode.DATA_INCOMPLETE: 502,
    ErrorCode.REPORT_GENERATION_FAILED: 502,
    ErrorCode.REPORT_NOT_FOUND: 404,
    ErrorCode.REPORT_NOT_READY: 409,
    ErrorCode.INSUFFICIENT_CREDITS: 403,
    # 409 (NOT 429): iOS APIClient intercepts 429 before decoding the body and
    # shows a generic "wait 60s", discarding our user_message. 409 falls
    # through to the structured-body decode so the cap copy is surfaced.
    ErrorCode.TOO_MANY_CONCURRENT_REPORTS: 409,
    # Same 409 rationale — surface the SYSTEM_BUSY user_message, not a 429
    # generic. Semantically "Too Many Requests" but 429 would be swallowed.
    ErrorCode.SYSTEM_BUSY: 409,
}


# ── Builders ──────────────────────────────────────────────────────────


def make_error_response(
    code: ErrorCode,
    *,
    message: str,
    status_code: Optional[int] = None,
    user_message: Optional[str] = None,
    action: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    """Return a `JSONResponse` whose body matches the iOS
    `APIErrorResponse` schema.

    `message` is the developer-facing technical detail (logged + shown
    in dev builds). `user_message` is the end-user copy; defaults
    to the registered `_USER_MESSAGES` entry for the code.
    """
    body = {
        "error_code": code.value,
        "message": (message or "")[:500],
        "user_message": (
            user_message
            if user_message is not None
            else _USER_MESSAGES.get(
                code, "Something went wrong. Please try again."
            )
        ),
        "action": action if action is not None else _DEFAULT_ACTIONS.get(code),
        "details": details or {},
    }
    return JSONResponse(
        status_code=status_code or _DEFAULT_STATUS.get(code, 500),
        content=body,
    )


def make_error_body(
    code: ErrorCode,
    *,
    message: str,
    user_message: Optional[str] = None,
    action: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Same shape as `make_error_response` but returns a plain dict —
    used by background tasks that persist the error into Supabase
    (research_reports.error_message JSON-encoded) instead of returning
    an HTTP response."""
    return {
        "error_code": code.value,
        "message": (message or "")[:500],
        "user_message": (
            user_message
            if user_message is not None
            else _USER_MESSAGES.get(
                code, "Something went wrong. Please try again."
            )
        ),
        "action": action if action is not None else _DEFAULT_ACTIONS.get(code),
        "details": details or {},
    }


# ── Classifier ────────────────────────────────────────────────────────


def classify_exception(exc: BaseException) -> Tuple[ErrorCode, int]:
    """Inspect an exception and return (error_code, default http status).

    Detection is class-name + message-keyword based so we don't have to
    import `httpx` or `google.api_core` at module load. False positives
    (e.g. an FMP exception text mentioning "quota" mistakenly mapped to
    Gemini) are acceptable — the underlying message is still preserved
    in `details.underlying` for production debugging.
    """
    cls = type(exc).__name__.lower()
    cls_module = type(exc).__module__.lower()
    msg = str(exc).lower()

    # ── Profile-not-found from collector / service ────────────────────
    if isinstance(exc, ValueError) and "profile" in msg:
        return ErrorCode.TICKER_NOT_FOUND, _DEFAULT_STATUS[ErrorCode.TICKER_NOT_FOUND]

    # ── Gemini / Google generative AI errors ──────────────────────────
    if (
        "google" in cls_module
        or "genai" in cls_module
        or "vertexai" in cls_module
    ):
        if "resourceexhausted" in cls or "quota" in msg or "429" in msg or "rate limit" in msg:
            return ErrorCode.GEMINI_QUOTA_EXCEEDED, _DEFAULT_STATUS[ErrorCode.GEMINI_QUOTA_EXCEEDED]
        return ErrorCode.GEMINI_UNAVAILABLE, _DEFAULT_STATUS[ErrorCode.GEMINI_UNAVAILABLE]

    # ── FMP typed exceptions (from app.integrations.fmp) ──────────────
    if "fmpauthexception" in cls or "fmpratelimitexception" in cls or "fmpexception" in cls:
        if "ratelimit" in cls:
            return ErrorCode.FMP_RATE_LIMITED, _DEFAULT_STATUS[ErrorCode.FMP_RATE_LIMITED]
        return ErrorCode.FMP_UNAVAILABLE, _DEFAULT_STATUS[ErrorCode.FMP_UNAVAILABLE]

    # ── httpx upstream errors (FMP) ───────────────────────────────────
    if "httpx" in cls_module or "httpstatus" in cls or "httperror" in cls:
        # FMP-specific 429 detection
        if "429" in msg or "rate limit" in msg or "too many requests" in msg:
            return ErrorCode.FMP_RATE_LIMITED, _DEFAULT_STATUS[ErrorCode.FMP_RATE_LIMITED]
        return ErrorCode.FMP_UNAVAILABLE, _DEFAULT_STATUS[ErrorCode.FMP_UNAVAILABLE]

    # ── Generic message-keyword heuristic ─────────────────────────────
    if "quota" in msg or "resource_exhausted" in msg:
        return ErrorCode.GEMINI_QUOTA_EXCEEDED, _DEFAULT_STATUS[ErrorCode.GEMINI_QUOTA_EXCEEDED]
    if "rate limit" in msg or "429" in msg:
        return ErrorCode.FMP_RATE_LIMITED, _DEFAULT_STATUS[ErrorCode.FMP_RATE_LIMITED]
    if "timeout" in cls or "timeout" in msg:
        return ErrorCode.FMP_UNAVAILABLE, _DEFAULT_STATUS[ErrorCode.FMP_UNAVAILABLE]

    return (
        ErrorCode.REPORT_GENERATION_FAILED,
        _DEFAULT_STATUS[ErrorCode.REPORT_GENERATION_FAILED],
    )


def error_response_from_exception(
    exc: BaseException,
    *,
    ticker: Optional[str] = None,
    persona: Optional[str] = None,
    step: Optional[str] = None,
    extra_details: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    """Convenience wrapper: classify, build details, return JSONResponse.

    `step` records the pipeline phase that blew up — e.g. "collector",
    "stage_a", "agentic_research", "narratives". Carried in
    `details.step` so production logs and iOS error views can show
    exactly where in the pipeline it failed.
    """
    code, status_code = classify_exception(exc)
    details: Dict[str, Any] = {
        "underlying": f"{type(exc).__name__}: {str(exc)[:200]}",
    }
    if ticker is not None:
        details["ticker"] = ticker
    if persona is not None:
        details["persona"] = persona
    if step is not None:
        details["step"] = step
    if extra_details:
        details.update(extra_details)

    return make_error_response(
        code,
        status_code=status_code,
        message=f"{type(exc).__name__}: {str(exc)[:300]}",
        details=details,
    )


def error_body_from_exception(
    exc: BaseException,
    *,
    ticker: Optional[str] = None,
    persona: Optional[str] = None,
    step: Optional[str] = None,
    extra_details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Like `error_response_from_exception` but returns the dict
    body — used by `_run_research_task` to persist a structured
    error blob into `research_reports.error_message`."""
    code, _status = classify_exception(exc)
    details: Dict[str, Any] = {
        "underlying": f"{type(exc).__name__}: {str(exc)[:200]}",
    }
    if ticker is not None:
        details["ticker"] = ticker
    if persona is not None:
        details["persona"] = persona
    if step is not None:
        details["step"] = step
    if extra_details:
        details.update(extra_details)

    return make_error_body(
        code,
        message=f"{type(exc).__name__}: {str(exc)[:300]}",
        details=details,
    )
