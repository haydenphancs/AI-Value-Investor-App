"""
Ticker Report Endpoints — generates a comprehensive stock analysis report
for the TickerReportView screen, plus a chat endpoint for follow-up Q&A.

Cache layer:
  - `ticker_report_cache` table (24h TTL, keyed by ticker+persona) is
    consulted inside `TickerReportService.generate_ticker_report`.
  - Legacy fallback below also peeks at recent completed
    `research_reports` rows so reports generated before the new cache
    table existed still serve instantly during the migration window.

Phase 3 error contract:
  All non-200 responses use the structured shape from
  `app.api.error_response` so iOS sees `{error_code, message,
  user_message, action, details}` instead of "HTTP 502". Endpoint code
  delegates exception classification to `error_response_from_exception`
  which inspects exception class + message keywords (FMP rate limits,
  Gemini quota, ValueError "no profile", etc.).

Endpoints:
  GET  /stocks/{ticker}/report?persona=warren_buffett
  POST /stocks/{ticker}/report/chat
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from app.api.error_response import (
    ErrorCode,
    error_response_from_exception,
    make_error_response,
)
from app.config import settings
from app.database import get_supabase
from app.dependencies import (
    get_current_user,
    ChatRateLimit,
    ReportRateLimit,
)
from app.schemas.ticker_report import TickerReportResponse
from app.services.credit_service import CreditService, CreditServiceUnavailable
from app.services.agents.persona_config import PERSONA_KEYS
from app.services.agents.ticker_report_data_collector import (
    patch_wall_street_consensus_live,
)
from app.services.ticker_report_cache import (
    CACHE_SCHEMA_FLOOR,
    _short_interest_payload_stale,
    current_close_cycle_start,
    get_cached_report,
    patch_legacy_price_action,
)
from app.services.ticker_report_service import TickerReportService

logger = logging.getLogger(__name__)

router = APIRouter()

# Single source of truth for valid persona keys — alias the research agent's
# registry (persona_config.PERSONA_KEYS) so this endpoint and
# /research/generate can never validate against diverging sets when a new
# persona is added. Parity is guarded by tests/test_persona_set_parity.py.
VALID_PERSONAS = PERSONA_KEYS

# Legacy cache TTL (kept for back-compat with older research_reports rows
# that pre-date the dedicated `ticker_report_cache` table).
LEGACY_CACHE_TTL_HOURS = 24

# Global in-flight counter for BILLABLE generations on this endpoint. The handler
# is synchronous (it awaits the pipeline to completion), so a plain int on the
# single event loop is an exact concurrency gauge — no lock needed, since the
# check and the increment below have no `await` between them.
_INFLIGHT_REPORTS = 0


@router.get("/{ticker}/report")
async def get_ticker_report(
    ticker: str,
    persona: str = Query("warren_buffett", description="Investor persona key"),
    user: dict = Depends(get_current_user),  # account-only: a cache miss is a paid generation
    _rate: None = ReportRateLimit,
):
    """
    Generate a comprehensive ticker report.

    Billing: a cache HIT is free; a cache MISS runs the full AI pipeline and costs
    `CreditService.DEEP_RESEARCH_COST` credits, charged upfront and refunded on ANY
    non-delivery (generation error, schema drift, or client disconnect).

    ACCOUNT-ONLY. This used to accept guests, metered per install against
    `GUEST_REPORT_MONTHLY_LIMIT` (migration 106) keyed off the `X-Guest-Id`-derived
    uuid — but that header is chosen by the CLIENT, so rotating it handed out a fresh
    allowance per request and left the most expensive call in the product effectively
    unmetered. Credits are FK-bound to a real `public.users` row and cannot be rotated.
    Two in-process controls still bound the blast radius even when Supabase is
    unreachable:
      * `ReportRateLimit` — a per-INSTALL window, so one abusive install cannot
        spend anyone else's allowance.
      * `REPORT_GET_MAX_INFLIGHT` — a global admission gate returning 409
        SYSTEM_BUSY past a safe backlog.

    Cache strategy: a recent completed `research_reports` row (legacy) AND the
    dedicated `ticker_report_cache` (24h close-aligned) are both checked here — so
    the charge and the admission gate fire strictly on a genuine generation, never
    on a cache hit.
    """
    global _INFLIGHT_REPORTS
    ticker = ticker.upper().strip()

    if not ticker or len(ticker) > 10:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Invalid ticker symbol: {ticker!r}",
            details={"ticker": ticker},
        )

    if persona not in VALID_PERSONAS:
        return make_error_response(
            ErrorCode.INVALID_PERSONA,
            message=f"Unsupported persona key: {persona!r}",
            # Joined, not a list: iOS `AnyCodable` decodes String/Int/Double/Bool and silently
            # falls through to "" for anything else, so a list arrived as an empty string and
            # the hint the user needed was destroyed without an error anywhere.
            details={"persona": persona, "valid": ", ".join(sorted(VALID_PERSONAS))},
        )

    # ── FREE PATH 1: legacy back-compat cache (recent completed research_reports).
    try:
        cached = await _check_legacy_report_cache(ticker, persona)
        if cached:
            logger.info(
                f"Legacy cache HIT for {ticker}/{persona} — serving stored report"
            )
            # Overlay live Wall Street Consensus so saved reports match
            # what `/stocks/{ticker}/analyst-analysis` and
            # `/stocks/{ticker}/holders` are showing right now.
            cached = await patch_wall_street_consensus_live(cached, ticker)
            patched = patch_legacy_price_action(cached)
            # VALIDATE, like every other return on this endpoint. This row came out of
            # another user's `research_reports.ticker_report_data`, which the DEEP
            # pipeline writes WITHOUT ever checking it against TickerReportResponse —
            # so this was the one path that could hand iOS a payload nothing had
            # type-checked. Swift's synthesized decoder is all-or-nothing, so a single
            # bad field (a null `moat_competition.dimensions[].score`, say) would fail
            # the whole screen with "Received unexpected data from the server".
            #
            # A row that fails is treated as a MISS, not as an error: the request falls
            # through to the close-aligned cache and, failing that, to a fresh
            # generation — the same handling `_short_interest_payload_stale` already
            # gives a legacy row it doesn't trust, a few lines below.
            result, err = _validate_report(patched, ticker, persona)
            if err is None:
                return result
            logger.warning(
                "Legacy cached report for %s/%s failed schema validation — treating as "
                "a MISS rather than serving a payload iOS cannot decode",
                ticker, persona,
            )
    except Exception as e:
        # Cache lookup failures must never break the request — log and fall through.
        logger.warning(
            f"Legacy cache check failed for {ticker}: "
            f"{type(e).__name__}: {e}"
        )

    # ── FREE PATH 2: the dedicated 24h ticker_report_cache. Checking it HERE (not
    #    only inside the service) is what lets us charge strictly on a real
    #    generation: a hit returns free, a miss is billable. get_cached_report
    #    never raises (returns None on any error).
    cached = await get_cached_report(ticker, persona)
    if cached is not None:
        logger.info(f"ticker_report_cache HIT for {ticker}/{persona} — free serve")
        result, err = _validate_report(cached, ticker, persona)
        return err if err is not None else result

    # ── ADMISSION GATE: shed load past a safe backlog. Placement is load-bearing
    #    on three counts:
    #      1. AFTER both free cache paths — shedding a cache hit would turn a
    #         capacity blip into a total outage on already-generated reports.
    #      2. BEFORE the precharge — a rejected request must never burn credits
    #         (the invariant test_research_concurrency_cap pins for the other path).
    #      3. Released in the `finally` below, which also runs on CancelledError.
    #         A leaked slot permanently bricks the endpoint after N disconnects.
    cap = settings.REPORT_GET_MAX_INFLIGHT
    if cap and _INFLIGHT_REPORTS >= cap:
        logger.warning(
            "Report admission REJECTED for %s/%s — %d in flight (cap %d)",
            ticker, persona, _INFLIGHT_REPORTS, cap,
        )
        return make_error_response(
            ErrorCode.SYSTEM_BUSY,
            status_code=409,
            message=f"{_INFLIGHT_REPORTS} report generations already in flight (cap {cap})",
            details={"ticker": ticker, "persona": persona, "step": "admission"},
        )
    _INFLIGHT_REPORTS += 1

    # Everything from here on lives inside the try, so the slot is released on
    # EVERY exit — including the two credit early-returns below, which would
    # otherwise leak a slot per failure and permanently brick the endpoint.
    credit_service = CreditService()
    charged = False
    delivered = False
    try:
        # ── BILLABLE PATH: cache miss = a fresh AI generation. Authenticated users
        #    are charged upfront via the unified gate (reset-if-due + atomic debit +
        #    ledger, one round-trip). Refund on ANY non-delivery via the finally below.
        #    Synchronous endpoint → the refund fires at most once, so the non-idempotent
        #    refund RPC is safe without the is_refunded CAS.
        #
        #    The guest branch that used to live here claimed against `guest_report_budget`
        #    keyed on `identity_key(user, x_guest_id)` — a UUID5 of a CLIENT-supplied header.
        #    Rotating it bought a fresh allowance per request, so this endpoint (the same
        #    ~17 Gemini + ~20 FMP cost as /research/generate on a miss) was unmetered against
        #    anyone willing to send a new header. Account-only closes that.
        try:
            new_remaining = credit_service.precharge(
                user["id"], CreditService.DEEP_RESEARCH_COST,
                reason="report_charge", ref_id=f"{ticker}:{persona}",
            )
        except CreditServiceUnavailable:
            # Transient Supabase/RPC failure — retryable SYSTEM_BUSY (never
            # INSUFFICIENT_CREDITS: a DB blip must not tell a paying user
            # they're broke). Nothing was debited, so `charged` stays False.
            return make_error_response(
                ErrorCode.SYSTEM_BUSY,
                status_code=409,
                message="spend_credits RPC unavailable (transient)",
                details={"user_id": user["id"], "ticker": ticker, "step": "credit_charge"},
            )
        if new_remaining is None:
            return make_error_response(
                ErrorCode.INSUFFICIENT_CREDITS,
                message=(
                    f"User has fewer than {CreditService.DEEP_RESEARCH_COST} "
                    f"credits remaining"
                ),
                details={
                    "user_id": user["id"],
                    "required": CreditService.DEEP_RESEARCH_COST,
                },
            )
        charged = True

        service = TickerReportService()
        report = await service.generate_fresh_report(ticker, persona)

        # A DEGRADED report is a non-delivery too. When Gemini is unavailable (quota circuit
        # open, 429s exhausted, 5xx) Stage A falls back to an empty shell and every Stage B
        # narrative uses its sentinel. The result still validates, so it used to be charged
        # 20 credits and returned as a success — the user paid full price for a blank report.
        # Returning an error here leaves `delivered` False, so the `finally` refunds.
        # `generate_fresh_report` has already declined to cache it.
        degraded = report.get("_degraded") if isinstance(report, dict) else None
        if degraded:
            logger.warning(
                "Degraded report for %s/%s (%s) — refunding rather than delivering a shell",
                ticker, persona, degraded,
            )
            return make_error_response(
                ErrorCode.GEMINI_UNAVAILABLE,
                message=f"report generation degraded for {ticker}/{persona}: {degraded}",
                status_code=503,
                user_message=(
                    "Analysis is temporarily unavailable. You have not been charged — "
                    "please try again in a few minutes."
                ),
                action="retry",
            )

        # A malformed report is a non-delivery → the finally refunds (the user
        # must not pay for a report they can't use).
        result, err = _validate_report(report, ticker, persona)
        if err is not None:
            return err
        delivered = True
        return result
    except ValueError as e:
        # Collector raises ValueError when the FMP profile lookup is empty —
        # a "ticker not found" condition, not a server error. Refunded in finally.
        logger.info(
            f"Ticker {ticker} rejected at collector (profile lookup empty): {e}"
        )
        return error_response_from_exception(
            e, ticker=ticker, persona=persona, step="collector",
        )
    except Exception as e:
        logger.error(
            f"Ticker report generation failed for {ticker}/{persona}: "
            f"{type(e).__name__}: {e}",
            exc_info=True,
        )
        return error_response_from_exception(
            e, ticker=ticker, persona=persona, step="report_generation",
        )
    finally:
        _INFLIGHT_REPORTS -= 1
        # `charged`, not `not is_guest`: a precharge that never succeeded must not
        # be "refunded" (that would CREDIT a user who was never debited).
        if charged and not delivered:
            # Non-delivery: generation error, schema drift, or a client disconnect
            # / CancelledError (a BaseException that `except` misses, but `finally`
            # always runs). Refund the charge. This is the ONLY backstop — unlike
            # /research/generate there is no research_reports row for the
            # reconciliation sweep — so a failed refund gets a greppable REFUND
            # LEAK marker (logged inside refund_ledgered) for manual correction.
            credit_service.refund_ledgered(
                user["id"], CreditService.DEEP_RESEARCH_COST,
                reason="report_refund", ref_id=f"{ticker}:{persona}",
            )


def _validate_report(report: dict, ticker: str, persona: str):
    """Validate a report dict against TickerReportResponse.

    Returns (dumped_dict, None) on success, or (None, error_response) on schema
    drift — so callers return a structured DATA_INCOMPLETE instead of a Pydantic
    500. model_dump() also strips internal-only fields (e.g. _scoring_inputs).
    """
    try:
        validated = TickerReportResponse(**report)
    except ValidationError as ve:
        logger.error(
            f"Report schema validation failed for {ticker}/{persona}: {ve}"
        )
        return None, make_error_response(
            ErrorCode.DATA_INCOMPLETE,
            message=f"Report shape failed validation: {ve.error_count()} issues",
            details={
                "ticker": ticker,
                "persona": persona,
                "step": "schema_validation",
                "issues": ve.error_count(),
            },
        )
    return validated.model_dump(), None


async def _check_legacy_report_cache(ticker: str, persona: str):
    """Legacy fallback that reads `research_reports` directly.

    Used only as a back-compat bridge for reports generated before the
    dedicated `ticker_report_cache` table existed. The new cache lives
    inside `TickerReportService` and `ResearchService`.
    """
    # Honor the same schema floor as `ticker_report_cache`: when the floor
    # is more recent than the 24h TTL cutoff, use it instead so legacy rows
    # generated under an older payload shape don't get served.
    #
    # AND the close-cycle boundary. This path is FREE PATH 1 — it runs BEFORE the
    # close-aligned `get_cached_report`, so a rolling 24h TTL here silently overrode
    # the freshness rule the whole report design is built on: a report completed at
    # 09:45 ET Monday is pinned to FRIDAY's close, yet this served it to every viewer
    # until 09:45 Tuesday — a full day past the Monday 18:00 boundary at which the
    # dedicated cache would have regenerated. The result was a report whose header
    # says "Previous Close · <Friday>" being handed out on Tuesday morning, with the
    # newer, correct row sitting unused one branch below.
    ttl_cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=LEGACY_CACHE_TTL_HOURS)
    )
    cutoff = max(
        ttl_cutoff, CACHE_SCHEMA_FLOOR, current_close_cycle_start()
    ).isoformat()

    def _query():
        supabase = get_supabase()
        result = (
            supabase.table("research_reports")
            .select("ticker_report_data, completed_at")
            .eq("ticker", ticker)
            .eq("investor_persona", persona)
            .eq("status", "completed")
            .gte("completed_at", cutoff)
            .order("completed_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data and result.data[0].get("ticker_report_data"):
            rpt = result.data[0]["ticker_report_data"]
            if _short_interest_payload_stale(rpt):
                logger.info(
                    f"Legacy report for {ticker}/{persona} has short-interest "
                    f"change_3m but empty history — skipping stale row"
                )
                return None
            return rpt
        return None

    return await asyncio.to_thread(_query)


# ── Chat with Report ──────────────────────────────────────────────────────────


class TickerReportChatRequest(BaseModel):
    ticker: str
    message: str
    persona: str = "warren_buffett"


class TickerReportChatResponseModel(BaseModel):
    reply: str
    ticker: str


@router.post("/{ticker}/report/chat")
async def chat_with_ticker_report(
    ticker: str,
    body: TickerReportChatRequest,
    user: dict = Depends(get_current_user),
    _rate: None = ChatRateLimit,
):
    """
    Ask a follow-up question about a stock using the same persona.

    This is a lightweight AI Q&A endpoint — it fetches a quick snapshot
    of FMP data and sends the user's question to Gemini with the
    persona context. Much faster than a full report (5-15 seconds).
    """
    ticker = ticker.upper().strip()

    if not ticker or len(ticker) > 10:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Invalid ticker symbol: {ticker!r}",
            details={"ticker": ticker},
        )

    persona = body.persona
    if persona not in VALID_PERSONAS:
        # Soft-coerce for chat — keep the prior behavior of falling back
        # to Buffett rather than rejecting outright. Logged so we still
        # see the bad request in production.
        logger.info(
            f"chat_with_ticker_report: unknown persona {persona!r}, "
            f"falling back to warren_buffett"
        )
        persona = "warren_buffett"

    message = body.message.strip()
    if not message:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message="Empty chat message",
            user_message="Type a question to send.",
        )

    if len(message) > 2000:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Chat message too long ({len(message)} chars > 2000)",
            user_message="Your message is too long — keep it under 2000 characters.",
            details={"length": len(message), "limit": 2000},
        )

    # Meter this Q&A like the main chat: CHAT_CREDIT_COST per turn (this is a full Gemini
    # answer — otherwise a free denial-of-wallet bypass). Refund on any non-delivery via the
    # finally. The guest no-op that used to sit here is gone with the endpoint's move to
    # account-only, which also removes the need for the per-install daily-turn budget this
    # surface never had.
    credit_service = CreditService()
    try:
        remaining = credit_service.precharge(
            user["id"], settings.CHAT_CREDIT_COST,
            reason="chat_charge", ref_id=f"report_chat:{ticker}",
        )
    except CreditServiceUnavailable:
        return make_error_response(
            ErrorCode.SYSTEM_BUSY, status_code=409,
            message="spend_credits RPC unavailable (transient)",
            details={"user_id": user["id"], "ticker": ticker, "step": "chat_credit_charge"},
        )
    if remaining is None:
        return make_error_response(
            ErrorCode.INSUFFICIENT_CREDITS,
            message="insufficient credits for report chat",
            details={"user_id": user["id"], "required": settings.CHAT_CREDIT_COST},
        )

    delivered = False
    try:
        service = TickerReportService()
        reply = await service.chat_about_ticker(ticker, message, persona)
        result = TickerReportChatResponseModel(reply=reply, ticker=ticker).model_dump()
        delivered = True
        return result
    except ValueError as e:
        return error_response_from_exception(
            e, ticker=ticker, persona=persona, step="chat_collector",
        )
    except Exception as e:
        logger.error(
            f"Ticker report chat failed for {ticker}/{persona}: "
            f"{type(e).__name__}: {e}",
            exc_info=True,
        )
        return error_response_from_exception(
            e, ticker=ticker, persona=persona, step="chat_generation",
        )
    finally:
        # Non-delivery (error or client-disconnect CancelledError) → refund the charge once.
        if not delivered:
            credit_service.refund_ledgered(
                user["id"], settings.CHAT_CREDIT_COST,
                reason="chat_refund", ref_id=f"report_chat:{ticker}",
            )
