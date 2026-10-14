"""
Ticker Report Endpoints — generates a comprehensive stock analysis report
for the TickerReportView screen.

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
"""

import asyncio
import copy
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple
from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from app.services.dcf_report_gate import report_dcf_source_matches, strip_caydex_if_disabled
from app.services.report_degradation import GROUNDING_FREE_KEY, report_is_grounding_free
from app.api.error_response import (
    ErrorCode,
    error_response_from_exception,
    make_error_response,
)
from app.config import settings
from app.database import get_supabase
from app.dependencies import (
    get_current_user,
    ReportRateLimit,
)
from app.schemas.ticker_report import TickerReportResponse
from app.services.credit_service import (
    CreditService,
    CreditServiceUnavailable,
    charge_off_loop,
    direct_report_charge_ref,
    refund_did_not_happen,
    settle_detached,
    settle_off_loop,
)
from app.services.agents.persona_config import PERSONA_KEYS
from app.services.agents.ticker_report_data_collector import (
    patch_wall_street_consensus_live,
    report_degraded_sections,
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

# Per-user single-flight for the BILLABLE path: (user_id, TICKER, persona) → the leader's
# future, resolved with whatever the leader returned. Process-local, so correct only under
# ONE uvicorn worker (pinned by tests/test_deploy_command_parity.py). See `get_ticker_report`.
_USER_GENERATIONS: Dict[Tuple[str, str, str], "asyncio.Future[Any]"] = {}
# The leader's result when it never returned (an exception escaped, or it was cancelled).
_LEADER_DIED = object()


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
    non-delivery (generation error, a degraded report, schema drift, or a deploy cancelling
    the request). A same-user request for a generation that is still running joins it and is
    never charged a second time.

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

    # ── PER-USER SINGLE-FLIGHT: one billable generation per (user, ticker, persona).
    #    A same-user request that finds its own generation still running JOINS it — no
    #    admission slot, no second precharge — and gets the leader's answer (its report, or
    #    its error). Without this a re-send of a slow generation (iOS's Retry after its
    #    120 s timeout, or an auto-retry after a bare edge 5xx) precharged 20 credits again
    #    and joined the SAME agent run (`run_agent_deduped` shares compute across callers
    #    but cannot know about billing): 40 credits for one report.
    #
    #    Placement: AFTER both free cache paths (a hit costs nothing to serve twice) and
    #    BEFORE the admission gate and the precharge. The get and the set below have no
    #    `await` between them, so they are atomic on the one event loop — correct only
    #    under ONE uvicorn worker, which `tests/test_deploy_command_parity.py` pins. Accepted
    #    residual: a retry whose cache read started before the leader's cache write, and
    #    whose map check runs after the leader popped, becomes a new leader and charges.
    key = (str(user["id"]), ticker, persona)
    leader = _USER_GENERATIONS.get(key)
    if leader is not None:
        logger.info(
            "Report %s/%s already generating for user=%s — joining it (no second charge)",
            ticker, persona, user["id"],
        )
        return await _join_user_generation(leader, ticker, persona)
    fut: "asyncio.Future[Any]" = asyncio.get_running_loop().create_future()
    _USER_GENERATIONS[key] = fut
    result: Any = _LEADER_DIED
    try:
        result = await _billable_generation(ticker, persona, user)
        return result
    finally:
        if _USER_GENERATIONS.get(key) is fut:
            _USER_GENERATIONS.pop(key, None)
        if not fut.done():
            fut.set_result(result)


async def _join_user_generation(leader: "asyncio.Future[Any]", ticker: str, persona: str):
    """A follower's answer: a COPY of what the leader returned, never a second charge.

    Shielded, so a follower that is itself cancelled cannot cancel the future the leader and
    any other follower share. A leader that died (an exception escaped, or it was cancelled)
    answers a retryable 409 SYSTEM_BUSY — its own `finally` already handed its charge back.
    """
    value = await asyncio.shield(leader)
    if value is _LEADER_DIED:
        return make_error_response(
            ErrorCode.SYSTEM_BUSY,
            status_code=409,
            message=f"the joined generation for {ticker}/{persona} did not finish",
            details={"ticker": ticker, "persona": persona, "step": "joined_generation"},
        )
    if isinstance(value, dict):
        return copy.deepcopy(value)
    body = getattr(value, "body", None)
    if body is None:
        return value
    # A Response is one-shot (Starlette streams its body to ONE client), so each follower
    # gets its own copy of the leader's error.
    return Response(
        content=bytes(body),
        status_code=getattr(value, "status_code", 500),
        media_type="application/json",
    )


async def _billable_generation(ticker: str, persona: str, user: dict):
    """The billable half of `get_ticker_report`: admission, charge, generate, settle.

    Only ever entered by a single-flight LEADER. Every exit settles the charge exactly once:
    delivered → kept; anything else → refunded (inline in the degraded branch, else in
    `finally`), on the ONE unique ref the charge used.
    """
    global _INFLIGHT_REPORTS

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

    credit_service = CreditService()
    # ONE local for the charge, its compensation and its refund. Unique per generation
    # (`TICKER:persona:{uuid}`): the old shared `TICKER:persona` ref meant a refund could
    # pair with an EARLIER delivered report's debit, so a compensation on it minted credits.
    charge_ref = direct_report_charge_ref(ticker, persona)
    charged = False
    delivered = False
    unwinding = False
    # Set BEFORE a refund is awaited: `settle_off_loop` finishes it even if that wait is
    # cancelled, so the `finally` must not issue a second one.
    refund_issued = False

    def _refund_sync():
        return credit_service.refund_ledgered(
            user["id"], CreditService.DEEP_RESEARCH_COST,
            reason="report_refund", ref_id=charge_ref,
        )

    def _hand_back_abandoned_charge(balance, exc):
        # This frame was cancelled WHILE the precharge ran, so it never learned the outcome
        # (and `charged` stays False): hand back whatever landed, on the same unique ref.
        if exc is None and balance is None:
            return None                                   # insufficient: nothing debited
        if exc is None:
            return credit_service.refund_ledgered(
                user["id"], CreditService.DEEP_RESEARCH_COST,
                reason="report_refund", ref_id=charge_ref,
            )
        return credit_service.refund_ledgered(
            user["id"], CreditService.DEEP_RESEARCH_COST,
            reason="report_precharge_unconfirmed", ref_id=charge_ref, quiet_no_match=True,
        )

    # Taken immediately before the try (no await since the cap check), so the slot is
    # released on EVERY exit — including the two credit early-returns below, which would
    # otherwise leak a slot per failure and permanently brick the endpoint.
    _INFLIGHT_REPORTS += 1
    try:
        # ── BILLABLE PATH: cache miss = a fresh AI generation. Authenticated users
        #    are charged upfront via the unified gate (reset-if-due + atomic debit +
        #    ledger, one round-trip). Refund on ANY non-delivery via the finally below.
        #    One request settles its own charge, so the refund fires at most once without
        #    the is_refunded CAS the deep door needs.
        #
        #    The guest branch that used to live here claimed against `guest_report_budget`
        #    keyed on `identity_key(user, x_guest_id)` — a UUID5 of a CLIENT-supplied header.
        #    Rotating it bought a fresh allowance per request, so this endpoint (the same
        #    ~17 Gemini + ~20 FMP cost as /research/generate on a miss) was unmetered against
        #    anyone willing to send a new header. Account-only closes that.
        #
        #    Off the loop (a sync RPC, invariant #6) through `charge_off_loop`, which also
        #    refunds a charge that lands after a deploy cancelled this wait.
        try:
            new_remaining = await charge_off_loop(
                lambda: credit_service.precharge(
                    user["id"], CreditService.DEEP_RESEARCH_COST,
                    reason="report_charge", ref_id=charge_ref,
                ),
                on_abandoned=_hand_back_abandoned_charge,
                what=f"report_charge {charge_ref}",
            )
        except CreditServiceUnavailable:
            # Transient Supabase/RPC failure — retryable SYSTEM_BUSY (never
            # INSUFFICIENT_CREDITS: a DB blip must not tell a paying user
            # they're broke). A transport failure does NOT prove nothing was debited (a 520
            # after the commit lands here too), so compensate NOW on the same unique ref:
            # `refunded` if the debit landed, a quiet `no_matching_debit` if it never did.
            # `charged` stays False, so the `finally` cannot refund a second time.
            await settle_off_loop(
                lambda: credit_service.refund_ledgered(
                    user["id"], CreditService.DEEP_RESEARCH_COST,
                    reason="report_precharge_unconfirmed", ref_id=charge_ref,
                    quiet_no_match=True,
                ),
                what=f"report_precharge_unconfirmed {charge_ref}",
            )
            return make_error_response(
                ErrorCode.SYSTEM_BUSY,
                status_code=409,
                message="spend_credits RPC unavailable (transient)",
                details={"user_id": user["id"], "ticker": ticker, "step": "credit_charge",
                         "charge_ref": str(charge_ref)},
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
        # `generate_fresh_report` has already declined to cache it.
        #
        # Settled HERE, before answering, so the copy can state what actually happened (the
        # `finally` then skips it). The copy is hygiene for a web client: iOS maps
        # GEMINI_UNAVAILABLE to its own text and re-reads the balance itself.
        degraded = report.get("_degraded") if isinstance(report, dict) else None
        if degraded:
            logger.warning(
                "Degraded report for %s/%s (%s) — refunding rather than delivering a shell",
                ticker, persona, degraded,
            )
            refund_issued = True
            refund_result = await settle_off_loop(
                _refund_sync, what=f"report_refund {charge_ref}",
            )
            returned = not refund_did_not_happen(refund_result)
            return make_error_response(
                ErrorCode.GEMINI_UNAVAILABLE,
                message=f"report generation degraded for {ticker}/{persona}: {degraded}",
                status_code=503,
                user_message=(
                    "Analysis is temporarily unavailable. Your credits were returned — "
                    "please try again in a few minutes."
                    if returned else
                    "Analysis is temporarily unavailable — please try again in a few minutes."
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
    except BaseException:
        # Records the fact only (CancelledError / GeneratorExit / KeyboardInterrupt): the
        # `finally` below must not await while this frame is being cancelled.
        unwinding = True
        raise
    finally:
        _INFLIGHT_REPORTS -= 1
        # `charged`, not `not is_guest`: a precharge that never succeeded must not
        # be "refunded" (that would CREDIT a user who was never debited). And a refund
        # already settled (the degraded branch) is not issued twice.
        if charged and not delivered:
            # Non-delivery: generation error, schema drift, or a CANCELLATION. Refund the
            # charge. This is the ONLY backstop — unlike /research/generate there is no
            # research_reports row for the reconciliation sweep — so a failed refund gets a
            # greppable REFUND LEAK marker (logged inside refund_ledgered, with the ref) for
            # manual correction.
            #
            # ⚠️ Cancellation IS real here, on a deploy. A client disconnect does not cancel
            # this handler on the pinned stack (starlette 0.41.3's `request_response` is a
            # bare `await f(request)` with no task group and no `http.disconnect` listener,
            # and this GET never reads `receive()`), but uvicorn
            # 0.34 DOES cancel in-flight requests when `--timeout-graceful-shutdown` expires
            # — and the Dockerfile CMD sets it to 30 s. A cancelled frame cannot await (the
            # next await is cancelled at once), so the refund goes to a strongly-held
            # detached task that the lifespan shutdown drains.
            #
            # ⚠️ NO "your report didn't finish" PUSH HERE. A backgrounded app is not a
            # non-delivery: the handler finishes, `delivered` is True, the report is CACHED,
            # and the user gets it free on their next open — a "we couldn't finish this
            # analysis" push would be factually wrong. It was added and removed once.
            #
            # The one hole left is a Railway SIGKILL past the stop grace (or the drain's own
            # bound), which runs no Python at all.
            #
            # Skipped only when a refund was already issued (the degraded branch settles
            # before answering, so its copy can state the real outcome).
            if not refund_issued:
                refund_issued = True
                if unwinding:
                    settle_detached(_refund_sync, what=f"report_refund {charge_ref}")
                else:
                    await settle_off_loop(_refund_sync, what=f"report_refund {charge_ref}")


def _validate_report(report: dict, ticker: str, persona: str):
    """Validate a report dict against TickerReportResponse.

    Returns (dumped_dict, None) on success, or (None, error_response) on schema
    drift — so callers return a structured DATA_INCOMPLETE instead of a Pydantic
    500. model_dump() also strips internal-only fields (e.g. _scoring_inputs).
    """
    # Kill switch: every return of a stored or cached report passes through here, so the
    # published estimate is dropped while settings.DCF_ENABLED is off (dcf_report_gate).
    report = strip_caydex_if_disabled(report)
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
            # This row is ANOTHER user's report, served free. Without the provenance stamp
            # the pre-retirement (Google Search grounded) code wrote it, after
            # CACHE_SCHEMA_FLOOR as well as before — skip it, the close-aligned cache or a
            # fresh generation answers instead.
            if not report_is_grounding_free(rpt):
                logger.warning(
                    "Legacy report for %s/%s is a pre-retirement report (no %s stamp — "
                    "possibly Google Search grounded) — skipping",
                    ticker, persona, GROUNDING_FREE_KEY,
                )
                return None
            if _short_interest_payload_stale(rpt):
                logger.info(
                    f"Legacy report for {ticker}/{persona} has short-interest "
                    f"change_3m but empty history — skipping stale row"
                )
                return None
            if not report_dcf_source_matches(rpt):
                logger.info(
                    f"Legacy report for {ticker}/{persona} was built under the other "
                    f"DCF_ENABLED setting — skipping"
                )
                return None
            # A deep-door report that lost a Financials section to a degraded upstream
            # build is the BUYER's report only: it is delivered (and billed) to its
            # caller but never shared, so this free path must not hand it to anyone else.
            partial = report_degraded_sections(rpt)
            if partial:
                logger.info(
                    "[report-partial-not-shared] legacy report for %s/%s lost sections "
                    "%s — skipping", ticker, persona, partial,
                )
                return None
            return rpt
        return None

    return await asyncio.to_thread(_query)
