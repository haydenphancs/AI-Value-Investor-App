"""
Chat Endpoints — with RAG pipeline
Frontend: POST /chat/sessions, GET /chat/sessions,
          POST /chat/sessions/{id}/messages, GET /chat/sessions/{id},
          DELETE /chat/sessions/{id},
          PATCH /chat/sessions/{id} (update title)
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request
from fastapi.responses import StreamingResponse
from supabase import Client
import logging

from app.config import settings
from app.database import get_supabase
from app.dependencies import (
    get_chat_identity,
    ChatRateLimit,
    chat_identity_key,
)
from app.api.error_response import make_error_body, make_error_response, ErrorCode
from app.utils.supabase_errors import is_transient_supabase_error
from app.services.chat_security import (
    validate_message,
    sanitize_context,
    sanitize_symbol,
    scan_input,
    finalize_disclaimer,
    strip_trailing_disclaimer,
    neutralize_fences,
)
from app.core.security import trusted_client_ip
from app.services.chat_budget_service import get_chat_budget_service, ChatBudgetUnavailable
from app.services.credit_service import CreditService, CreditServiceUnavailable, refund_did_not_happen
from app.integrations.gemini import GeminiTimeoutError, _is_clean_finish, is_length_cut
import time as _time
from app.services.agents.chat_guardrails import scan_answer, enforce_answer
from app.services.chat_intent import is_trade_intent
from app.services.chat_chip_filter import filter_answerable_chips
from app.schemas.chat_starters import ChatStartersResponse
from app.services.chat_starters_service import get_chat_starters_service
from app.schemas.chat import (
    CreateChatSessionRequest,
    SendChatMessageRequest,
    UpdateChatSessionRequest,
    ChatSessionResponse,
    ChatMessageResponse,
    ChatSessionListResponse,
    ChatHistoryResponse,
)

logger = logging.getLogger(__name__)
# Dedicated logger so input-injection attempts + guardrail redactions are greppable
# (and scrubbed by the root SecretRedactingFilter, per app/log_redaction.py).
sec_logger = logging.getLogger("chat.security")

router = APIRouter()


# Fixed namespace for the IP-derived budget bucket. Random once, constant forever — changing
# it hands every caller a fresh allowance.
_IP_BUDGET_NAMESPACE = uuid.UUID("2b7f4e91-0c3d-4a86-9f52-8d1e6a04b7c3")


def _ip_budget_bucket(req) -> str:
    """The anti-rotation budget key: a uuid5 of the address OUR edge observed.

    ⚠️ UNREACHABLE since the account-only wall (2026-09-07): `get_chat_identity` is strict
    and returns `is_guest=False` for every caller, so no chat route resolves a guest. Kept
    because the money-path tests pin the semantics and a signed-out surface may return;
    do NOT model chat cost on the limits this enforces — they cannot fire today.

    `chat_usage_budget.user_id` is a bare uuid column with no FK, so this needs no migration.

    A one-way hash rather than the raw address, because this row outlives the request: it is
    a pseudonymous IP derivative, it is written for guests only, and the rows are per-day.
    """
    return str(uuid.uuid5(_IP_BUDGET_NAMESPACE, trusted_client_ip(req)))


def _claim_chat_turn_or_error(user: dict, x_guest_id, req=None):
    """Claim one daily chat turn for this caller's abuse/cost bucket.

    ⚠️ UNREACHABLE since the account-only wall (2026-09-07): `get_chat_identity` is strict
    and returns `is_guest=False` for every caller, so no chat route resolves a guest. Kept
    because the money-path tests pin the semantics and a signed-out surface may return;
    do NOT model chat cost on the limits this enforces — they cannot fire today.

    Returns a `JSONResponse` (409 CHAT_DAILY_LIMIT_REACHED) when the daily cap is
    reached, else None (proceed). FAILS OPEN on a budget-service transport error —
    a DB blip must never wall a user out of chat.

    TWO buckets for a guest, and the second is the point. The per-install bucket keys on
    `guest_user_id_for(X-Guest-Id)` — a uuid5 of a header the CLIENT picks — so sending a fresh
    header minted a fresh 60-turn allowance on every request, leaving this the one AI surface
    in the app with no effective cost ceiling (report generation went account-only; chat did
    not). The IP bucket keys on `trusted_client_ip`, which the caller cannot forge, so rotation
    now buys nothing. It sits far above the per-install cap so a shared network is unaffected
    in normal use.

    Signed-in callers get ONE bucket: their key is a real account id, which is not rotatable,
    and adding an IP ceiling there would throttle a household or office sharing an address.
    """
    service = get_chat_budget_service()
    bucket = chat_identity_key(user, x_guest_id)
    try:
        count = service.try_claim_turn(bucket)
    except ChatBudgetUnavailable as e:
        logger.warning("Chat budget unavailable for bucket=%s — failing open: %s", bucket, e)
        return None
    if count == -1:
        return make_error_response(
            ErrorCode.CHAT_DAILY_LIMIT_REACHED,
            message="daily chat turn budget exhausted",
        )

    if req is not None and user.get("is_guest"):
        ip_bucket = _ip_budget_bucket(req)
        try:
            ip_count = service.try_claim_turn(
                ip_bucket, limit=settings.CHAT_DAILY_TURN_LIMIT_PER_IP
            )
        except ChatBudgetUnavailable as e:
            # Fail open, same as above — but say so loudly. This is the anti-abuse ceiling,
            # so a persistent failure here means rotation is buying allowance again.
            logger.warning(
                "Chat IP budget unavailable for bucket=%s — failing open: %s", ip_bucket, e
            )
            return None
        if ip_count == -1:
            logger.warning(
                "Chat IP ceiling reached for bucket=%s (limit=%s) — likely X-Guest-Id rotation",
                ip_bucket, settings.CHAT_DAILY_TURN_LIMIT_PER_IP,
            )
            return make_error_response(
                ErrorCode.CHAT_DAILY_LIMIT_REACHED,
                message="daily chat turn budget exhausted for this network",
            )
    return None


def _record_chat_tokens(user: dict, x_guest_id, tokens) -> None:
    """Best-effort daily token accounting for this caller's bucket."""
    try:
        get_chat_budget_service().record_tokens(
            chat_identity_key(user, x_guest_id), int(tokens or 0)
        )
    except Exception as e:  # never let accounting affect the answered turn
        logger.warning("Chat token record failed (%s: %s)", type(e).__name__, e)


def _refund_chat_turn(user: dict, x_guest_id) -> None:
    """Best-effort: release the daily turn claimed for this caller when generation FAILED to
    produce a persisted answer, so a Gemini outage doesn't drain the daily cap (migration 097).

    ⚠️ UNREACHABLE since the account-only wall (2026-09-07): `get_chat_identity` is strict
    and returns `is_guest=False` for every caller, so no chat route resolves a guest. Kept
    because the money-path tests pin the semantics and a signed-out surface may return;
    do NOT model chat cost on the limits this enforces — they cannot fire today.

    Refunds the per-install bucket only, not the IP ceiling: the ceiling is an abuse bound
    (300/day) rather than a fair-use budget, and threading the request this far to release it
    is not worth the coupling. A sustained outage therefore erodes the ceiling for a shared
    network over one day, which self-heals at the daily rollover.
    """
    try:
        get_chat_budget_service().refund_turn(chat_identity_key(user, x_guest_id))
    except Exception as e:  # never let a refund failure change the error response
        logger.warning("Chat turn refund failed (%s: %s)", type(e).__name__, e)


class _ChatQuota:
    """One chat turn's metering, resolved per identity.

    - Authenticated user → CHAT_CREDIT_COST credits (the monthly wallet is the cap).
    - Guest → the durable per-install daily-turn budget (guests are never credit-metered:
      `user_credits` is FK-bound to `public.users`, so a per-install id has no wallet to
      debit — see migration 101).

    `refund_once` hands the quota back on non-delivery and is safe to call from every
    failure site + the finally backstop: a single-coroutine `_settled` flag fires the
    (non-idempotent) refund AT MOST ONCE.

    `ref_id` is PER-TURN (`"{session_id}:{uuid4}"`), not per-session. It used to be the
    bare session id, which made every turn in a conversation write an identical
    `(ref_id, delta = -CHAT_CREDIT_COST)` ledger row — and `refund_credits` pairs a refund
    to the NEWEST un-reversed match, so a refund could adopt a SIBLING turn's recorded pool
    split and hand a granted credit back into the purchased pool (or destroy a paid one).
    Migration 124 names this file as the reason its `reverses_id` anti-join exists; a unique
    ref per debit is what actually makes the pairing exact. The session id is kept as the
    prefix purely so a ledger row is still greppable back to its conversation.
    """

    def __init__(
        self,
        user: dict,
        x_guest_id,
        *,
        is_guest: bool,
        ref_id: Optional[str],
        session_id: Optional[str] = None,
        free: bool = False,
        balance_after: Optional[int] = None,
    ):
        self._user = user
        self._x_guest_id = x_guest_id
        self._is_guest = is_guest
        self._ref_id = ref_id
        self._session_id = session_id
        self._free = free
        self._settled = False
        self._refunded = False
        self._refund_reason: Optional[str] = None
        self._balance_after: Optional[int] = balance_after

    @property
    def outcome(self) -> str:
        """What this turn cost, for the `credits` SSE frame. Reflects the CLAIM, and is
        re-read after settlement — `refund_once` flips it to "refunded"."""
        if self._refunded:
            return "refunded"
        if self._free:
            return "free_followup"
        if self._is_guest:
            return "guest"
        return "charged"

    @property
    def charged(self) -> int:
        """Credits actually retained for this turn (0 for guests, free turns, refunds)."""
        if self._is_guest or self._free or self._refunded:
            return 0
        return settings.CHAT_CREDIT_COST

    def refund_once(self, reason: str) -> None:
        if self._settled:
            return
        self._settled = True
        self._refund_reason = reason
        if self._free:
            # ⚠️ MUST return before `refund_ledgered`. A free turn wrote NO debit, so a
            # refund against its ref_id finds no matching row, falls through to
            # `refund_credits`' granted-first fallback and pays out
            # `LEAST(amount, used)` — MINTING a credit the user never spent, on every
            # failed free turn. `_free` is checked first for exactly this reason.
            #
            # Deliberately does NOT set `_refunded`: nothing was refunded, so the turn's
            # reported outcome stays "free_followup" rather than claiming a credit came
            # back. What is restored is the ALLOWANCE — consumed by the claim for a turn
            # that never arrived. Bounded: this hands back what they already held, it
            # cannot mint a second one.
            get_chat_budget_service().grant_free_followup(self._session_id)
            return
        if self._is_guest:
            self._refunded = True
            _refund_chat_turn(self._user, self._x_guest_id)
        else:
            payload = CreditService().refund_ledgered(
                self._user["id"],
                settings.CHAT_CREDIT_COST,
                reason=reason,
                ref_id=self._ref_id,
            )
            # The chip says "refunded" ONLY when the ledger proved it. `refund_ledgered`
            # answers None on a transport fault and a no-op outcome (`no_matching_debit`,
            # `capped_to_zero`, `no_credits_row`) when nothing moved — `refund_did_not_happen`
            # is the one classifier every report site uses. The flag used to be set BEFORE
            # the call, so a REFUND LEAK rendered as "1 credit refunded".
            self._refunded = not refund_did_not_happen(payload)
            # `None` is strictly a transport fault, never a business outcome — leave the
            # balance as-is so the client refreshes rather than being shown a wrong number.
            if isinstance(payload, dict) and isinstance(payload.get("spendable"), int):
                self._balance_after = payload["spendable"]

    def settle_no_cost(self, reason: str) -> None:
        """Refund a turn that WAS delivered but cost nothing (cache replay) or delivered
        materially less than promised (a degraded shape).

        Distinct from `refund_once`, which is for a turn that never arrived: that one
        restores a claimed free-follow-up ALLOWANCE, because the claim was spent on nothing.
        Here the user received an answer, so a free turn simply stays free — re-granting
        would let a degraded free turn earn another free turn, and under a function-calling
        outage that chain is unbounded off one credit. A charged turn is refunded exactly as
        `refund_once` does. Idempotent through `_settled`, and `on_delivered` is a no-op
        afterwards, so a no-cost turn never earns a follow-up either.
        """
        if self._settled:
            return
        self._settled = True
        self._refund_reason = reason
        if self._free or self._is_guest:
            return
        payload = CreditService().refund_ledgered(
            self._user["id"],
            settings.CHAT_CREDIT_COST,
            reason=reason,
            ref_id=self._ref_id,
        )
        # Same rule as `refund_once`: the flag reflects what the ledger did, not what we asked.
        self._refunded = not refund_did_not_happen(payload)
        if isinstance(payload, dict) and isinstance(payload.get("spendable"), int):
            self._balance_after = payload["spendable"]

    def _label(self) -> Optional[str]:
        """The server-authored string iOS renders, or None when there is nothing to say.

        Server-authored on purpose (the same principle as `user_message` on errors): the
        wording can be changed without an App Store release, and the client never has to
        know how to pluralise a credit count it did not compute.
        """
        n = settings.CHAT_CREDIT_COST
        unit = "credit" if n == 1 else "credits"
        if self._refunded:
            # Kept SHORT. This renders in a capsule badge on the message's metadata line,
            # beside the timestamp — a long string wraps the capsule toward a square, which
            # `Capsule()` draws as a circle with the text clipped inside it.
            if self._refund_reason == "chat_cache_hit":
                return f"{n} {unit} refunded — no AI cost"
            if (self._refund_reason or "").startswith("chat_degraded_"):
                return f"{n} {unit} refunded — incomplete answer"
            return f"{n} {unit} refunded"
        if self._free:
            return "Free follow-up"
        return None

    def cost_payload(self) -> Optional[dict]:
        """The PERSISTED shape (`rich_content["credit"]`), or None when nothing is worth
        showing. A normal charge returns None deliberately — putting a price on every
        answer turns chat into a meter, and the asking is the product.

        Carries no balance: a spendable balance is an ACCOUNT fact, and this dict is
        replayed verbatim on every history reload, where it would be stale.
        """
        if self._label() is None:
            return None
        return {
            "outcome": self.outcome,
            "credits": self.charged,
            "reason": self._refund_reason if self._refunded else None,
            "label": self._label(),
        }

    def cost_frame(self) -> dict:
        """The LIVE shape (the `credits` SSE frame). Always emitted on a delivered turn,
        even for a plain charge — the client needs `balance` to keep its own copy honest,
        and decides for itself that a `charged` outcome renders no chip.
        """
        return {
            "outcome": self.outcome,
            "credits": self.charged,
            "reason": self._refund_reason if self._refunded else None,
            "label": self._label(),
            "balance": self._balance_after,
        }

    def on_delivered(self) -> None:
        """Called once the turn is durably persisted — grants the session's free follow-up.

        ONLY a turn that was actually charged grants one. That single rule is what bounds
        the whole feature to 2 turns per credit: a free turn earning another would let one
        credit be ridden indefinitely by always replying inside the window.

        Guarded on `_settled` because it runs AFTER the cache-hit refund check in both
        endpoints — a turn we handed the credit back for was not paid for either, so it
        must not earn an allowance on the way out.
        """
        if self._settled or self._free or self._is_guest:
            return
        get_chat_budget_service().grant_free_followup(self._session_id)


def _claim_chat_quota(user: dict, x_guest_id, *, session_id: Optional[str], req=None):
    """Reserve one chat turn's quota BEFORE any Gemini spend (pre-flight).

    Guests → daily-turn budget (fails open on DB blip). Authenticated users →
    an atomic CHAT_CREDIT_COST precharge: insufficient → 402 INSUFFICIENT_CREDITS
    (no generation), transient RPC failure → retryable 409 SYSTEM_BUSY (never 402 —
    a DB blip must not tell a paying user they're broke).

    Takes the SESSION id and mints the credit `ref_id` itself (see `_ChatQuota`): the
    ledger needs one unique ref per debit, and callers must not be able to pass the bare
    session id by accident. `ref_id` is also an overloaded name in this module — the
    streaming handler binds it to the chat CONTEXT reference (a ticker, a book id) a few
    lines after claiming quota — so the credit ref never travels as a caller argument.

    Returns `(quota, None)` to proceed, or `(None, JSONResponse)` to short-circuit.
    """
    # One ref per DEBIT. Prefixed with the session so a ledger row stays greppable back to
    # its conversation; the uuid is what makes the refund pairing exact.
    turn_ref = f"{session_id}:{uuid.uuid4().hex}"
    # `user.get("is_guest")`, NOT `user["id"] == GUEST_USER_ID`. Under migration 111 a guest
    # resolves to a per-INSTALL uuid5 that never equals the shared sentinel, so the old
    # comparison would have sent every guest into the credit precharge below — against a
    # `user_credits` row that does not exist — and answered 402 "insufficient credits" for a
    # feature that is supposed to be free for them. `get_chat_identity` documents this trap;
    # it is the same one `get_research_identity` and `get_watchlist_identity` carry.
    if user.get("is_guest"):
        err = _claim_chat_turn_or_error(user, x_guest_id, req)
        if err is not None:
            return None, err
        return _ChatQuota(
            user, x_guest_id, is_guest=True, ref_id=turn_ref, session_id=session_id,
        ), None

    # An earned free follow-up is claimed BEFORE the wallet is touched, and deliberately
    # bypasses the insufficient-credits gate: it was paid for by the previous turn, so a
    # user who has since hit 0 still gets the answer they are already owed. The claim is
    # atomic (UPDATE ... RETURNING) so two racing turns cannot both go free, and it fails
    # CLOSED — a DB blip charges normally and leaves the allowance for the next turn.
    if get_chat_budget_service().claim_free_followup(session_id):
        return _ChatQuota(
            user, x_guest_id, is_guest=False, ref_id=turn_ref,
            session_id=session_id, free=True,
        ), None

    try:
        remaining = CreditService().precharge(
            user["id"], settings.CHAT_CREDIT_COST, reason="chat_charge", ref_id=turn_ref
        )
    except CreditServiceUnavailable:
        # A transport failure is NOT proof the debit did not commit — a Cloudflare 520 is
        # "the edge could not parse what the origin said". Chat has no reconciliation row
        # for a sweep to find later, so compensate NOW with the per-turn ref_id: `refunded`
        # if the debit landed, `no_matching_debit` (quiet) if it never did. If the refund
        # transport is down too, the POSSIBLE LOST CHARGE line above carries the ref.
        try:
            CreditService().refund_ledgered(
                user["id"], settings.CHAT_CREDIT_COST,
                reason="chat_precharge_unconfirmed", ref_id=turn_ref, quiet_no_match=True,
            )
        except Exception as e:  # pragma: no cover - refund_ledgered never raises
            logger.error("chat precharge compensation failed for ref_id=%s: %s: %s",
                         turn_ref, type(e).__name__, e)
        return None, make_error_response(
            ErrorCode.SYSTEM_BUSY,
            status_code=409,
            message="spend_credits RPC unavailable (transient)",
            details={"user_id": user["id"], "step": "chat_credit_charge", "turn_ref": turn_ref},
        )
    if remaining is None:
        return None, make_error_response(
            ErrorCode.INSUFFICIENT_CREDITS,
            message="insufficient credits for chat turn",
            details={"user_id": user["id"], "required": settings.CHAT_CREDIT_COST},
        )
    return _ChatQuota(
        user, x_guest_id, is_guest=False, ref_id=turn_ref, session_id=session_id,
        balance_after=remaining,
    ), None


# ── Helpers ─────────────────────────────────────────────────────────

# Exactly the columns `_row_to_session` reads. The list endpoint used to `select("*")`,
# which drags every session's `context_snapshot` (up to CHAT_CONTEXT_MAX_CHARS = 8000)
# and `memory_summary` across the wire for a whole page — neither is serialized to iOS.
# Kept in sync with `_row_to_session` by tests/test_chat_session_list_columns.py.
# The single-session fetches deliberately keep `select("*")`: the turn path DOES read
# context_snapshot and memory_summary from that row.
_SESSION_LIST_COLUMNS = (
    "id, title, session_type, stock_id, context_type, reference_id, "
    "preview_message, message_count, is_saved, created_at, last_message_at"
)


def _row_to_session(row: dict) -> ChatSessionResponse:
    """Map a Supabase chat_sessions row to the response schema."""
    return ChatSessionResponse(
        id=row["id"],
        title=row.get("title"),
        session_type=row.get("session_type", "NORMAL"),
        stock_id=row.get("stock_id"),
        context_type=row.get("context_type"),
        reference_id=row.get("reference_id"),
        preview_message=row.get("preview_message"),
        message_count=row.get("message_count", 0),
        is_saved=row.get("is_saved", False),
        created_at=row["created_at"],
        last_message_at=row.get("last_message_at"),
    )


# Map the screen's context type → the session_type the iOS history badge knows
# (ChatConversationModels.historyItemType: STOCK/BOOK/CONCEPT/JOURNEY/REPORT/NORMAL).
_CONTEXT_TO_SESSION_TYPE = {
    "TICKER_REPORT": "REPORT",
    "STOCK": "STOCK",
    "ETF": "STOCK",
    "CRYPTO": "STOCK",
    "INDEX": "STOCK",
    "COMMODITY": "STOCK",
    "MONEY_MOVES_ARTICLE": "CONCEPT",
    "JOURNEY_LESSON": "JOURNEY",
    "BOOK": "BOOK",
}


def _session_type_for(context_type: Optional[str], stock_id: Optional[str]) -> str:
    """Derive the persisted session_type from context_type (falls back to the
    legacy stock_id → STOCK / NORMAL rule when no context type is sent)."""
    if context_type:
        mapped = _CONTEXT_TO_SESSION_TYPE.get(context_type.strip().upper())
        if mapped:
            return mapped
    return "STOCK" if stock_id else "NORMAL"


def _may_record_memory_facts(user: dict) -> bool:
    """Pure, no I/O: could this turn possibly write a memory fact?

    The mirror of `_may_have_reader_lens`, and it exists for the same reason: it lets the
    async call sites skip the threadpool hop entirely in the common case (feature off, or a
    guest / free caller) rather than paying one to discover there is nothing to do.

    Never raises — a malformed identity is not a reason to fail a delivered turn.
    """
    from app.config import settings
    from app.services.entitlements import signals_unlocked

    try:
        if not settings.CHAT_MEMORY_FACTS_ENABLED:
            return False
        if user.get("is_guest"):
            return False
        return bool(signals_unlocked(user.get("tier")))
    except Exception:  # noqa: BLE001
        return False


async def _record_memory_facts_async(
    user: dict, stock_id: Optional[str], route: Optional[dict]
) -> None:
    """`_record_memory_facts` without blocking the event loop. NEVER raises.

    The write side is worse than the read side that `_reader_lens_for_async` already fixed:
    a single call is up to ~7 sequential blocking Supabase round trips (profile read, then a
    select + upsert per fact, then the eviction select + delete). Run inline from an
    `async def`, that stalls the whole worker — every other in-flight request included — for
    the duration, once `CHAT_MEMORY_FACTS_ENABLED` is turned on.

    The gate is checked here rather than inside the thread so a free-tier turn pays nothing.
    """
    if not _may_record_memory_facts(user):
        return
    await asyncio.to_thread(_record_memory_facts, user, stock_id, route)


def _record_memory_facts(user: dict, stock_id: Optional[str], route: Optional[dict]) -> None:
    """Record what this turn was about. Best-effort, AFTER delivery. NEVER raises.

    Costs no LLM: both values were already computed for this turn — the router picked the
    specialist to shape the prompt, and the session carries the ticker. They were simply
    being discarded. Nothing here reads the user's text, which is what keeps the stored
    values inside a closed vocabulary and therefore safe to render unfenced later.

    Gated identically to the reading side, including CONSENT: memory is observed rather
    than stated, so it must not accumulate for a reader who declined personalization.
    """
    try:
        from app.config import settings
        from app.services.agents.investor_profile_prompt import may_apply_profile
        from app.services.entitlements import signals_unlocked
        from app.services.user_investor_profile_service import (
            get_user_investor_profile_service,
        )
        from app.services.user_memory_facts_service import (
            FACT_THEME,
            FACT_TICKER,
            get_user_memory_facts_service,
            sanitize_facts,
        )

        if not settings.CHAT_MEMORY_FACTS_ENABLED:
            return
        if user.get("is_guest") or not signals_unlocked(user.get("tier")):
            return

        pairs = []
        if stock_id:
            pairs.append((FACT_TICKER, stock_id))
        specialists = (route or {}).get("specialists") or []
        if specialists:
            pairs.append((FACT_THEME, specialists[0]))

        # Sanitize BEFORE the profile read, not after. `general` is both the router's
        # ordinary fallback and its degraded result, and it is deliberately excluded from
        # the stored theme vocabulary — so a ticker-less general turn builds a non-empty
        # `pairs` that validates to nothing. Checking `pairs` alone therefore paid a
        # Supabase profile round trip, on a very common path, to write nothing at all.
        facts = sanitize_facts(pairs)
        if not facts:
            return

        # Consent is checked against the stored profile, not inferred — same gate the
        # reading side uses, so the two can never disagree about who opted in.
        profile = get_user_investor_profile_service().get_profile(user["id"])
        if not may_apply_profile(profile, user.get("tier")):
            return
        get_user_memory_facts_service().record(user["id"], facts)
    except Exception as e:  # noqa: BLE001 — the turn is already delivered
        logger.warning(
            "Memory fact recording failed for user=%s (%s: %s)",
            user.get("id"), type(e).__name__, e,
        )


def _may_have_reader_lens(user: dict) -> bool:
    """Pure, no I/O: could this caller possibly get a lens at all?

    The first two arms of `may_apply_profile` — the feature flag and the tier — need
    nothing from the database. The other two (consent, a non-empty row) do. Splitting
    them out lets the async call sites answer the common case without touching a thread:
    with the feature off, or for a free/guest caller, the answer is None and no work of
    any kind should happen on the answer path.

    Never raises: a caller identity that is not a dict, or is missing `tier`, resolves to
    False rather than taking down the turn.
    """
    from app.config import settings
    from app.services.entitlements import signals_unlocked

    try:
        if not settings.CHAT_PERSONALIZATION_ENABLED:
            return False
        return bool(signals_unlocked(user.get("tier")))
    except Exception:  # noqa: BLE001 — a malformed identity is not a reason to fail a turn
        return False


async def _reader_lens_for_async(user: dict) -> Optional[str]:
    """`_reader_lens_for` without blocking the event loop.

    WHY THIS WRAPPER EXISTS. `_reader_lens_for` is synchronous and performs a Supabase
    round trip (two, once `CHAT_MEMORY_FACTS_ENABLED` is on: the profile, then the memory
    facts). Both call sites are `async def` on the answer path, so calling it directly
    stalls the whole event loop — every other in-flight request included — for the
    duration. That was invisible while the feature flag was off, because the function
    returns before the read; flipping the flag would have turned it on for every Pro/Max
    turn, and with no log drain in production it would not have shown up anywhere.

    The gate is checked HERE rather than inside the thread on purpose: `asyncio.to_thread`
    is not free, and wrapping the whole function would make free-tier turns pay a
    threadpool hop to be told "None" — a regression for the majority of traffic in service
    of a feature they cannot use.

    The sync function remains the single implementation, so its never-raises contract and
    its tests continue to cover this path.
    """
    if not _may_have_reader_lens(user):
        return None
    return await asyncio.to_thread(_reader_lens_for, user)


def _reader_lens_for(user: dict) -> Optional[str]:
    """The reader's rendered preference block for this turn, or None. NEVER raises.

    Best-effort by design: personalization is a presentation nicety, so a profile-store
    hiccup must degrade to the normal, impersonal answer rather than fail the turn. The
    tier / consent / feature-flag decision lives in `resolve_reader_lens` so it is a pure
    function both endpoints share and tests can exercise without a request.
    """
    try:
        from app.services.agents.investor_profile_prompt import resolve_reader_lens
        from app.services.user_investor_profile_service import (
            get_user_investor_profile_service,
        )

        from app.config import settings
        from app.services.entitlements import signals_unlocked

        # Skip the read entirely when it cannot matter — the common case (feature off,
        # or a free/guest caller) must not pay a Supabase round trip on the answer path.
        if not settings.CHAT_PERSONALIZATION_ENABLED or not signals_unlocked(user.get("tier")):
            return None
        profile = get_user_investor_profile_service().get_profile(user["id"])
        lens = resolve_reader_lens(profile, user.get("tier"))
        if lens is None:
            # The four-arm gate refused (no consent, empty profile, …). Memory rides the
            # SAME consent: it is observed rather than stated, so it must not apply to a
            # reader who declined personalization.
            return None

        if settings.CHAT_MEMORY_FACTS_ENABLED:
            from app.services.agents.investor_profile_prompt import render_memory_block
            from app.services.user_memory_facts_service import (
                get_user_memory_facts_service,
            )

            facts = get_user_memory_facts_service().top_facts(user["id"])
            lens += render_memory_block(facts)
        return lens
    except Exception as e:  # noqa: BLE001 — never let a preference break an answer
        logger.warning(
            "Reader lens unavailable for user=%s (%s: %s) — answering unpersonalized",
            user.get("id"), type(e).__name__, e,
        )
        return None


# Rows `GET /chat/sessions/{id}` returns: the newest N. Well above any real conversation;
# the point is that the number is DELIBERATE and the tail is always present (see the read).
CHAT_HISTORY_PAGE_ROWS = 400


def _session_lookup_failed(e: Exception) -> HTTPException:
    """The exception a session lookup raises when `.single()` did not return a row.

    PGRST116 (zero rows) and anything deterministic stay a 404 exactly as before. A
    TRANSIENT Supabase failure — a Cloudflare 520/525 edge page, a connect timeout — used to
    become the same 404 at all five lookup sites, so a datastore blip read as "session not
    found": iOS's stream-failure reconcile (`GET /chat/sessions/{id}` is its persistence
    oracle) took that as "not persisted" and re-POSTed a turn the server had already saved
    and charged. 409 `SYSTEM_BUSY`, not 503: the iOS SSE client decodes bodies only for
    400/402/403/409, `SYSTEM_BUSY` is in its terminal set (no reconcile, no re-POST), and it
    is the code `_claim_chat_quota` already answers a transient RPC failure with.
    """
    if is_transient_supabase_error(e):
        logger.warning("chat_sessions lookup transient (%s: %s)", type(e).__name__, e)
        return HTTPException(
            status_code=409,
            detail=make_error_body(
                ErrorCode.SYSTEM_BUSY,
                message="chat_sessions lookup unavailable (transient)",
                user_message="Cay AI can't reach this conversation right now. Please try again in a moment.",
                details={"step": "chat_session_lookup"},
            ),
        )
    return HTTPException(status_code=404, detail="Chat session not found")


def _effective_context(req_context: Optional[str], session_row: dict) -> Optional[str]:
    """The on-screen grounding snapshot to feed the LLM this turn.

    Prefer the per-message value iOS sends from a LIVE detail screen; on a
    history reopen iOS sends none, so fall back to the snapshot persisted from
    when the chat was first opened (migration 087). Returns None when neither
    exists — pre-migration rows read no column, so behavior is identical to today.
    """
    if req_context:
        return req_context
    stored = session_row.get("context_snapshot")
    return stored or None


def _persist_context_snapshot(
    supabase: Client, session_id: str, req_context: Optional[str], session_row: dict
) -> None:
    """Best-effort: persist the live on-screen snapshot so a later history reopen
    can re-ground on the exact data the user saw (migration 087).

    Deliberately ISOLATED + guarded: a missing column (a code deploy that raced
    ahead of the migration) or any transient DB error must NEVER break the chat
    turn — worst case the reopen simply isn't grounded on the snapshot, which is
    today's behavior. Skips the write when there's nothing new to store (reopen
    turns send no context; live turns resend the same frozen snapshot every
    message — so this writes once, then no-ops for the rest of the session).
    """
    # Whitespace-only counts as ABSENT: `sanitize_context` already reads it as none for the
    # prompt, so persisting it here overwrote a real stored snapshot with spaces (and the
    # replay flag read the turn as "live" because the raw string was truthy).
    if not (req_context or "").strip() or req_context == session_row.get("context_snapshot"):
        return
    try:
        supabase.table("chat_sessions").update(
            {"context_snapshot": req_context}
        ).eq("id", session_id).execute()
    except Exception as e:
        logger.warning(
            "Chat context_snapshot persist failed (%s: %s) — history reopen won't re-ground on it",
            type(e).__name__, e,
        )


async def _replay_cached_answer(text: str, chunk_size: int = 240):
    """Yield a stored answer as ``("answer", chunk)`` events, matching `stream_agentic`'s shape.

    Lets a cached deep dive reuse the entire live-stream path (token frames, persistence, the
    terminal `done` frame) instead of needing a parallel branch through the endpoint.

    Chunked rather than sent whole so the text reveals like a real answer — the iOS reveal
    buffer meters chunks, and one 4KB frame would land as an instant wall of text. No sleeps:
    pacing belongs to the client, and an artificial delay here would hold a worker open.
    """
    for i in range(0, len(text), chunk_size):
        yield "answer", text[i:i + chunk_size]


def _keepalive_seconds() -> float:
    return float(getattr(settings, "CHAT_STREAM_KEEPALIVE_SECONDS", 15.0) or 15.0)


def _stream_budget_seconds() -> float:
    return float(getattr(settings, "CHAT_STREAM_BUDGET_SECONDS", 150.0) or 150.0)


async def _with_keepalive(agen, deadline: Optional[float] = None):
    """Re-yield `agen`'s events, interleaving ``("keepalive", None)`` whenever
    `CHAT_STREAM_KEEPALIVE_SECONDS` pass with nothing to send — until `deadline`
    (a `time.monotonic()` instant), past which the wait is abandoned with a
    `GeminiTimeoutError` so the caller's transient-error handling settles the turn.

    The keepalives reset iOS's 120 s idle timeout indefinitely, so without a deadline
    the only bound on a streamed turn was the SUM of every inner ceiling — up to four
    tool rounds of sequential 8–75 s tools plus 90 s reads — and a stalled turn held the
    user, the worker and the credit for many minutes. The non-streaming door has had
    `CHAT_SEND_BUDGET_SECONDS` since 2026-09-16; this is its twin.

    Why: the synthesis path buffers its specialists and yields NOTHING until the gather
    completes, and a single tool may legitimately run for `_TOOL_TIMEOUTS` (75 s for a
    grounded web search). iOS's stream request times out after 120 s of silence; two
    quiet rounds could cross it and the client would fall back — re-POSTing a turn the
    server was still answering. A comment frame costs nothing and resets that clock.
    """
    it = agen.__aiter__()
    pending = asyncio.ensure_future(it.__anext__())
    try:
        while True:
            wait_for = _keepalive_seconds()
            if deadline is not None:
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    raise GeminiTimeoutError(
                        f"streamed turn exceeded CHAT_STREAM_BUDGET_SECONDS={_stream_budget_seconds():.0f}"
                    )
                wait_for = min(wait_for, remaining)
            done, _ = await asyncio.wait({pending}, timeout=wait_for)
            if not done:
                if deadline is not None and _time.monotonic() >= deadline:
                    continue   # the top of the loop raises with the remaining ≤ 0
                yield "keepalive", None
                continue
            try:
                item = pending.result()
            except StopAsyncIteration:
                return
            yield item
            pending = asyncio.ensure_future(it.__anext__())
    finally:
        if not pending.done():
            pending.cancel()
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except BaseException:  # noqa: BLE001 — closing a dead generator must not mask the cause
                pass


def _sse(event: str, data: dict) -> str:
    """Format a single Server-Sent Events frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# Characters that end / begin a WORD at the join: a cut at a token boundary lands after
# a complete word (Gemini's tokens carry their leading space), and the continuation's first
# token arrives without one — "printing" + "more" read "printingmore" on the first live run.
_JOIN_TAIL = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789)]:;,.!?%\"'")
_JOIN_HEAD = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789([\"'*")
_JOIN_MIN_OVERLAP = 12


def _join_continuation(partial: str, continuation: str) -> str:
    """Make a continuation read as one answer with the partial it follows.

    Two deterministic repairs, both on the FIRST chunk only:
      * a repeated tail — the model re-emitted the end of the partial before going on
        ("…the supply is" + "the supply is uncapped") — is trimmed when at least
        `_JOIN_MIN_OVERLAP` characters of the partial's tail equal the continuation's
        head (case-insensitive; shorter overlaps are too often coincidence);
      * a missing space at a word boundary is inserted when the partial ends in a
        word/closing character and the continuation opens with a word/opening one.
        A cut that landed mid-word gets a spurious space; a cut lands mid-word only
        when the ceiling hit inside a multi-token word, which is the rare case.
    Pure; never raises; an empty continuation comes back empty.
    """
    if not continuation:
        return continuation
    tail = partial.rstrip()
    if tail:
        limit = min(len(tail), len(continuation), 200)
        for k in range(limit, _JOIN_MIN_OVERLAP - 1, -1):
            if tail[-k:].lower() == continuation[:k].lower():
                continuation = continuation[k:]
                break
    if not continuation:
        return continuation
    if partial and not partial[-1].isspace() and not continuation[0].isspace():
        # "$13." + "49" and "13," + "000" are one number, not two words.
        numeric_seam = partial[-1] in ".,:" and continuation[0].isdigit()
        if partial[-1] in _JOIN_TAIL and continuation[0] in _JOIN_HEAD and not numeric_seam:
            continuation = " " + continuation
    return continuation


async def _continue_cut_answer(
    chat_service,
    prep: dict,
    partial: str,
    *,
    model_name: Optional[str],
    max_output_tokens: int,
    usage_tag: str,
    signals: dict,
):
    """ONE continuation round for an answer the model CUT mid-sentence.

    Yields ``("answer", text)`` chunks only — thoughts are dropped on purpose: the
    thinking card has already settled by the time the answer text was cut, and a
    second burst of `reasoning` frames would re-activate it under a half-written
    bubble. The verdict lands in ``signals["continuation"]``:

      * ``"clean"``  — text arrived and the round finished with STOP: the answer is
                       complete, the turn is NOT truncated.
      * ``"cut"``    — text arrived but this round hit the ceiling too (the finish
                       reason is in ``signals["continuation_finish"]``): still truncated.
      * ``"empty"``  — no answer text (thoughts only / safety-filtered): still truncated.
      * ``"failed"`` — the call raised; logged, never re-raised, still truncated.

    The prompt is the turn's own fenced prompt (history + RAG context + the user
    message) plus the partial answer, fenced as DATA so nothing in it can close the
    spotlight. It goes out on the tool-less instruction: the data was gathered in the
    first round and a fresh tool round here would spend the budget the same way twice.
    Never raises — the caller settles the turn from `signals` whatever happens here.
    """
    signals["continuation"] = "failed"
    got_text = False
    cut_again: Optional[str] = None
    from app.services.chat_service import _chat_thinking_budget
    prompt = (
        f"{prep.get('prompt') or ''}\n\n"
        "Your answer to the USER MESSAGE above was cut off by a length limit. What "
        "reached the user so far is below, as data:\n"
        "<<<PARTIAL_ANSWER>>>\n"
        f"{neutralize_fences(partial)}\n"
        "<<<END_PARTIAL_ANSWER>>>\n"
        "Continue that answer from the exact point it stops. Do not repeat any word "
        "already written, do not restart or summarise it, and add no preamble. If it "
        "stopped in the middle of a word, write only the rest of that word first; "
        "otherwise begin with the very next word. Finish the thought, then stop."
    )
    # The first chunk is buffered (briefly — this is the rare path) so the join with
    # the partial can be repaired before anything reaches the bubble.
    head: List[str] = []
    head_len = 0
    head_flushed = False
    try:
        async for kind, text in chat_service.gemini.stream_text(
            prompt,
            system_instruction=prep.get("system_instruction_no_tools") or prep.get("system_instruction"),
            model_name=model_name,
            max_output_tokens=max_output_tokens,
            thinking_budget=_chat_thinking_budget(model_name),
            usage_tag=usage_tag,
        ):
            if kind == "answer" and text:
                if not head_flushed:
                    head.append(text)
                    head_len += len(text)
                    if head_len < 120:
                        continue
                    joined = _join_continuation(partial, "".join(head))
                    head_flushed = True
                    if joined:
                        # Whitespace is forwarded (it may be the space before the next
                        # word) but does not count as a continuation on its own.
                        got_text = got_text or bool(joined.strip())
                        yield "answer", joined
                    continue
                got_text = got_text or bool(text.strip())
                yield "answer", text
            elif kind == "finish":
                cut_again = str(text)
        if not head_flushed and head:
            joined = _join_continuation(partial, "".join(head))
            head_flushed = True
            if joined:
                got_text = got_text or bool(joined.strip())
                yield "answer", joined
    except Exception as e:
        logger.warning(
            "Chat continuation round failed (%s: %s) for %s — keeping the turn truncated",
            type(e).__name__, e, usage_tag,
        )
        signals["continuation"] = "failed"
        return
    if not got_text:
        signals["continuation"] = "empty"
    elif cut_again:
        signals["continuation"] = "cut"
        signals["continuation_finish"] = cut_again
    else:
        signals["continuation"] = "clean"


def _session_has_card(supabase, session_id: str) -> Optional[bool]:
    """Whether any persisted assistant row of this session already carries a card.

    The once-per-session card (E7) is keyed on `message_count` — but a first turn whose
    card fetch timed out (`_deterministic_widget` answers None and logs) persisted a
    cardless row, and the count then kept every later turn cardless too (review
    finding, 2026-09-19). On a later grounded turn this probe asks the rows instead:
    no prior card → the next answer carries it. One small select, off the loop, only
    on later turns of grounded sessions. None when the probe itself fails — the caller
    then falls back to the count rule (no card) rather than guessing.
    """
    try:
        result = (
            supabase.table("chat_messages")
            .select("id")
            .eq("session_id", session_id)
            .eq("role", "assistant")
            .not_.is_("rich_content->widget", "null")
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        logger.warning(
            "chat card probe failed for session %s (%s: %s) — keeping the count rule",
            session_id, type(e).__name__, e,
        )
        return None


async def _should_attach_base_card(supabase, session_row: dict, session_id: str) -> bool:
    """The card rule for BOTH doors: the first turn, or a later turn whose earlier
    answers never managed to render one."""
    if _is_first_turn(session_row):
        return True
    grounded = bool(session_row.get("stock_id") or session_row.get("reference_id"))
    if not grounded:
        return False
    has_card = await asyncio.to_thread(_session_has_card, supabase, session_id)
    if has_card is False:
        logger.info("Chat: no card persisted yet for session %s — attaching it on this turn", session_id)
        return True
    return False


def _is_first_turn(session_row: Optional[dict]) -> bool:
    """True when no message has been persisted on this session yet.

    Reads the trigger-maintained `message_count` (see `trg_chat_message_count`), which
    both doors already use for the auto-title gate. Tolerant of the column arriving as
    None / a string / garbage: anything that is not a positive integer reads as "first
    turn", so a malformed row degrades to showing the card rather than never showing it.
    """
    try:
        count = int((session_row or {}).get("message_count") or 0)
    except (TypeError, ValueError):
        return True
    return count <= 0


# The one follow-up chip a CUT answer gets instead of model-written suggestions. It is
# a normal chip on the wire (`suggestions`), so a build that predates the `truncated`
# flag still renders the way out; tapping it sends this text as the next user message,
# and the model — which sees its own half answer in the conversation history — picks
# up where it stopped. First person, under 60 chars, like every other chip.
_CONTINUE_CHIP = "Continue your answer"


def _rich_content_for_turn(
    thinking: dict, widgets: Optional[list], sources: Optional[list],
    *, truncated: bool = False, finish_reason: Optional[str] = None,
) -> dict:
    """The `rich_content` blob BOTH doors persist, built in one place.

    The non-stream door used to write `{"widget": w}` alone, so a turn re-POSTed through
    it after a stream verdict rendered on the next history load as a bare bubble beside
    neighbours that all had a thinking card and source pills (F03-9 / F01-7). The
    `widget` key stays for old iOS builds; `widgets` is the list new ones read.

    `truncated` marks an answer the model CUT (MAX_TOKENS / SAFETY / RECITATION after
    real text) that no continuation completed. It is written only when true — a clean
    turn's blob is byte-identical to before — and `_row_to_message` lifts it onto the
    wire so a history reload shows the same "cut short" state the live turn did.
    """
    rich: dict = {"thinking": thinking}
    if widgets:
        rich["widgets"] = list(widgets)
        rich["widget"] = widgets[0]
    if sources:
        rich["sources"] = sources
    if truncated:
        rich["truncated"] = True
        if finish_reason:
            rich["finish_reason"] = str(finish_reason)[:40]
    return rich


def _persist_turn(supabase, session_id: str, user_row: dict, ai_msg: dict) -> dict:
    """Insert the user + assistant rows in ONE statement and return the assistant row.

    Ids are PRE-MINTED so a lost REPLY can be told apart from a lost WRITE: on a transient
    Supabase failure (an edge 520/503 after the statement committed) the assistant row
    is re-selected by its id — found means the turn IS durable and the caller continues
    as delivered; absent means the write never landed and the original error propagates.
    Before this, a blip on the insert response refunded a turn that had committed, the
    user re-sent the same question, was charged again, and history held two identical
    exchanges — the first unacknowledged and free (F03-6).
    """
    user_row.setdefault("id", str(uuid.uuid4()))
    ai_msg.setdefault("id", str(uuid.uuid4()))
    try:
        inserted = supabase.table("chat_messages").insert([user_row, ai_msg]).execute()
    except Exception as e:
        if not is_transient_supabase_error(e):
            raise
        logger.warning(
            "chat_messages insert reply lost for session=%s assistant=%s (%s: %s) — "
            "re-reading the row to tell a lost reply from a lost write",
            session_id, ai_msg["id"], type(e).__name__, e,
        )
        try:
            found = (
                supabase.table("chat_messages").select("*")
                .eq("id", ai_msg["id"]).limit(1).execute()
            )
        except Exception as e2:  # noqa: BLE001
            logger.warning(
                "chat_messages re-read failed for assistant=%s (%s: %s) — treating the "
                "turn as NOT persisted", ai_msg["id"], type(e2).__name__, e2,
            )
            raise e
        rows = getattr(found, "data", None) or []
        if rows:
            logger.warning(
                "chat_messages insert HAD committed for session=%s assistant=%s — "
                "Supabase lost the reply, not the write; continuing as delivered",
                session_id, ai_msg["id"],
            )
            return dict(rows[0])
        raise e
    assistant_row = next(
        (r for r in (getattr(inserted, "data", None) or []) if r.get("role") == "assistant"),
        None,
    )
    if assistant_row is None:
        raise RuntimeError("assistant row missing from chat_messages insert result")
    return assistant_row


def _attach_turn_cost(supabase, assistant_row: dict, quota, rich: Optional[dict] = None):
    """Record what this turn cost into the assistant row's `rich_content`, best-effort.

    Returns the merged rich_content dict (or `rich` unchanged when there is nothing to
    record) so the streaming path can rebind its local copy — the suggestions step later
    writes that same local dict back, and would otherwise clobber the key we just added.

    Writes nothing for a normally-charged turn: `cost_payload()` returns None there, by
    design. Only a free or refunded turn has anything worth telling the user.

    A failure here is deliberately silent to the user beyond a warning: the live `credits`
    frame has already shown the chip, so the only thing lost is its replay on a history
    reload. Never let it touch the answer, which is already durably persisted.
    """
    payload = quota.cost_payload()
    if payload is None:
        return rich

    if isinstance(rich, dict):
        merged = dict(rich)
    elif isinstance(assistant_row.get("rich_content"), dict):
        merged = dict(assistant_row["rich_content"])
    else:
        merged = {}
    merged["credit"] = payload
    assistant_row["rich_content"] = merged

    try:
        supabase.table("chat_messages").update(
            {"rich_content": merged}
        ).eq("id", assistant_row["id"]).execute()
    except Exception as e:
        logger.warning(
            "Chat turn-cost persist failed for message=%s (%s: %s) — chip shown live only",
            assistant_row.get("id"), type(e).__name__, e,
        )
    return merged


def _object_citations(raw: Any) -> Optional[list]:
    """Keep only the dict elements of a stored `citations` list (None when nothing is left)."""
    if not isinstance(raw, list):
        return None
    kept = [c for c in raw if isinstance(c, dict)]
    if len(kept) != len(raw):
        logger.warning("chat history: dropped %d non-object citation(s) from a stored row",
                       len(raw) - len(kept))
    return kept or None


def _row_to_message(row: dict, *, strip_disclaimer: bool = False) -> ChatMessageResponse:
    """Map a Supabase chat_messages row to the response schema.

    `strip_disclaimer` is OPT-IN, and only `get_chat_history` opts in. A blanket strip
    here would be wrong: this same function also serves the fresh non-streaming response
    and the `done` frame, whose content already went through `finalize_disclaimer` in
    THIS request — stripping again would delete a disclaimer that was required.
    """
    rc = row.get("rich_content") if isinstance(row.get("rich_content"), dict) else None
    # `widgets` (list) is the Phase-2 multi-widget field; `widget` (single) stays for back-compat
    # with old iOS builds. Fall the list back to the single widget for legacy rows.
    stored_widgets = rc.get("widgets") if rc else None
    stored_widget = rc.get("widget") if rc else None
    if not stored_widgets and stored_widget:
        stored_widgets = [stored_widget]
    # Futuristic-chat fields live in rich_content (no schema migration). Absent → None,
    # so legacy rows and old iOS builds decode unchanged.
    sources = rc.get("sources") if rc else None
    # Chips stored BEFORE the answerable-scope filter existed can still propose a question
    # the chat declines; filtering on read keeps history honest without rewriting rows
    # (the raw `rich_content` echo is untouched — iOS reads `suggestions`). Idempotent on
    # the `done` frame, which was filtered at generation. The Continue chip passes.
    stored_chips = rc.get("suggestions") if rc else None
    suggestions = (filter_answerable_chips(stored_chips) or None) if stored_chips else None
    thinking = rc.get("thinking") if rc else None
    # Present only on a turn that was free or refunded, so a history reload re-shows the
    # chip. Absent on every legacy row and every normally-charged turn → None → no chip.
    credit = rc.get("credit") if rc else None
    # Written only on a CUT answer (`_rich_content_for_turn`); `True` or None on the
    # wire, never False, so legacy rows and old builds see nothing new.
    truncated = True if (rc and rc.get("truncated") is True) else None

    # Replay strip. Rows persisted before the disclaimer became conditional carry the
    # line on EVERY answer, including "Hi". Rewriting `chat_messages` was rejected —
    # destructive, and the policy may change again — so history is corrected on READ and
    # the durable row is left exactly as it was written.
    content = row["content"]
    if strip_disclaimer and row.get("role") == "assistant":
        content = strip_trailing_disclaimer(content)

    return ChatMessageResponse(
        id=row["id"],
        session_id=row["session_id"],
        role=row["role"],
        content=content,
        widget=stored_widget,
        widgets=stored_widgets,
        rich_content=row.get("rich_content"),
        # Objects only. The schema is `List[Any]` (a scalar would pass), and iOS decodes
        # each element as `ChatCitationDTO` — array decoding is all-or-nothing, so one
        # non-object element in one message used to blank the whole history. The DTO is
        # now a total decoder too; this is the belt.
        citations=_object_citations(row.get("citations")),
        tokens_used=row.get("tokens_used"),
        sources=sources,
        suggestions=suggestions,
        thinking=thinking,
        credit=credit,
        truncated=truncated,
        created_at=row["created_at"],
    )


# ── Endpoints ───────────────────────────────────────────────────────

@router.get("/starters", response_model=ChatStartersResponse)
async def get_chat_starters(
    user: dict = Depends(get_chat_identity),   # strict: a real account, or 401
):
    """Daily-rotating starter questions for the empty chat state and the detail AI bars.

    The chips above "Ask Cay AI…" were five hardcoded strings until this route existed.
    They now change every ET day and a few of them name what actually moved today.

    **Why it is authenticated.** The live slots are FMP-derived, and the signed Order
    Form grants End-User Display Rights only "through the Licensee's authenticated
    platform" (`.claude/rules/auth.md` §1a). `chat.py`'s router carries no blanket
    dependency, so this must be declared here — and the corresponding iOS case must be
    `.signInRequired`.

    ⚠️ **The body is IMPERSONAL, and one cache entry serves everybody.** The dependency
    authenticates the caller but the cache key does not include them, so any per-user
    slot — watchlist, tier, holdings — would be handed to whoever asked next. If
    personalisation is ever wanted here it needs a separate per-user-keyed cache, not a
    field on this response. `test_chat_starters_endpoint.py` fails the build if the
    service starts reading the caller.

    ⚠️ Related, and the reason App-Exclusive Signals are excluded from the composition:
    `signals_v3` tickers are Pro-gated and `redact_signals()` masks them PER REQUEST. A
    globally cached set carrying one would show Free users the ticker the paywall hides.

    Never errors. Every live source is optional and every slot degrades to an evergreen
    question, with the bundled catalogue as the floor — so there is no `ErrorCode` for
    this route and no iOS `AppError` branch to keep in sync.
    """
    return await get_chat_starters_service().get_starters()


@router.get("/sessions", response_model=ChatSessionListResponse)
async def list_chat_sessions(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_chat_identity),  # per-INSTALL guest partition (migration 111)
    supabase: Client = Depends(get_supabase),
):
    """One page of the current user's chat sessions, newest activity first.

    Fetches `limit + 1` rows so `has_more` is exact without a second COUNT query; the
    client pages on it until every session is listed (E6). Off the event loop like the
    other chat reads — a sync postgrest call on the single Railway worker stalls every
    other request for a Supabase RTT.
    """
    def _fetch():
        return (
            supabase.table("chat_sessions")
            .select(_SESSION_LIST_COLUMNS)
            .eq("user_id", user["id"])
            # No NULLS clause: `last_message_at` is NOT NULL DEFAULT now() (chat_sessions DDL),
            # so `nullsfirst=False` bought nothing semantically and cost the planner the
            # (user_id, last_message_at DESC) index — DESC defaults to NULLS FIRST, and a
            # mismatched NULLS flag forces an explicit sort of the user's whole list per page.
            .order("last_message_at", desc=True)
            .range(offset, offset + limit)   # one extra row = the has_more probe
            .execute()
        )

    try:
        result = await asyncio.to_thread(_fetch)
    except Exception as e:
        # The bare select used to surface as a 500 with no body iOS could read, and the
        # history panel then kept whatever list it last loaded with nothing telling the
        # user it was stale (E6). Same contract as `_session_lookup_failed`: SYSTEM_BUSY
        # (in the client's terminal set) with a step marker. WARNING for a datastore blip,
        # ERROR (with the stack) for anything deterministic.
        if is_transient_supabase_error(e):
            logger.warning("chat_sessions list transient (%s: %s)", type(e).__name__, e)
        else:
            logger.error("chat_sessions list failed (%s: %s)", type(e).__name__, e, exc_info=True)
        return make_error_response(
            ErrorCode.SYSTEM_BUSY,
            message=f"chat_sessions list failed: {type(e).__name__}: {e}"[:300],
            user_message="Cay AI can't reach your chats right now. Please try again in a moment.",
            details={"step": "chat_session_list"},
        )

    rows = list(result.data or [])
    has_more = len(rows) > limit
    sessions = [_row_to_session(r) for r in rows[:limit]]
    return ChatSessionListResponse(sessions=sessions, total=len(sessions), has_more=has_more)


@router.post("/sessions", response_model=ChatSessionResponse)
async def create_chat_session(
    request: CreateChatSessionRequest,
    user: dict = Depends(get_chat_identity),  # per-INSTALL guest partition (migration 111)
    supabase: Client = Depends(get_supabase),
    # Was the one chat route with NO limiter. It writes a row per call, so an unthrottled
    # caller could fill `chat_sessions` without ever sending a message — cheap for them,
    # unbounded for us. Shares the "chat" window with the two message routes on purpose:
    # alternating between endpoints must not buy extra budget.
    _rate: None = ChatRateLimit,
):
    """Create a new chat session."""
    now_iso = datetime.now(timezone.utc).isoformat()
    # `stock_id` is the one caller-supplied value that reaches the SYSTEM instruction
    # unfenced (chat_service._build_system_instruction). Normalize it to a real symbol
    # at the door so nothing else in the app ever handles the raw string — the title
    # below and every later turn read the stored value. `sanitize_symbol` drops anything
    # that is not symbol-shaped, which downgrades a crafted payload to a generic chat
    # rather than rejecting a legitimate request. See its docstring for the full story.
    stock_id = sanitize_symbol(request.stock_id)
    session_data = {
        "user_id": user["id"],
        "session_type": _session_type_for(request.context_type, stock_id),
        "stock_id": stock_id,
        "context_type": request.context_type,
        "reference_id": request.reference_id,
        "title": f"Chat about {stock_id}" if stock_id else "New Chat",
        "last_message_at": now_iso,
    }

    try:
        result = supabase.table("chat_sessions").insert(session_data).execute()
    except Exception as e:
        # supabase-py RAISES APIError on a non-2xx insert rather than returning empty `.data`,
        # so the `if not result.data` check below never saw a rejected insert — it fell through
        # to the global handler as a bare 500 "internal server error".
        #
        # There is one predictable cause worth naming: a guest `user_id` is a synthetic uuid5
        # with no `public.users` row, which violates `chat_sessions_user_id_fkey` until
        # migration 111 drops it. Deploying this code without applying 111 therefore breaks
        # chat creation for every signed-out user — and a generic 500 would send whoever
        # debugs it looking at Gemini instead of at a pending migration.
        logger.error(
            "chat_sessions insert failed for user=%s (is_guest=%s): %s: %s — if this is a "
            "foreign-key violation, migration 111 (chat_sessions_guest_partition) has not "
            "been applied yet",
            user["id"], user.get("is_guest"), type(e).__name__, e, exc_info=True,
        )
        return make_error_response(
            ErrorCode.SYSTEM_BUSY,
            status_code=409,
            message=f"chat_sessions insert failed: {type(e).__name__}: {str(e)[:200]}",
            details={"step": "create_session"},
        )

    if not result.data:
        logger.error(
            "chat_sessions insert returned no rows for user=%s (is_guest=%s)",
            user["id"], user.get("is_guest"),
        )
        return make_error_response(
            ErrorCode.SYSTEM_BUSY,
            status_code=409,
            message="chat_sessions insert returned no rows",
            details={"step": "create_session"},
        )

    return _row_to_session(result.data[0])


@router.post("/sessions/{session_id}/messages", response_model=ChatMessageResponse)
async def send_chat_message(
    session_id: str,
    request: SendChatMessageRequest,
    req: Request,
    user: dict = Depends(get_chat_identity),  # per-INSTALL guest partition (migration 111)
    supabase: Client = Depends(get_supabase),
    x_guest_id: Optional[str] = Header(None, alias="X-Guest-Id"),
    _rate: None = ChatRateLimit,
):
    """Send a message and get AI response with RAG."""
    # Input hygiene (OWASP LLM01/LLM10): normalize away invisible/bidi injection
    # characters + enforce the friendly length ceiling BEFORE any DB or model work.
    msg, msg_err = validate_message(request.message)
    if msg_err is not None:
        return make_error_response(msg_err, message="chat message rejected by input validation")
    inj = scan_input(msg)
    if inj:
        sec_logger.warning(
            "Chat input injection markers %s (session=%s user=%s): %r",
            inj, session_id, user.get("id"), msg[:200],
        )

    # Verify session ownership
    try:
        session = (
            supabase.table("chat_sessions")
            .select("*")
            .eq("id", session_id)
            .eq("user_id", user["id"])
            .single()
            .execute()
        )
    except Exception as e:
        raise _session_lookup_failed(e)

    if not session.data:
        raise HTTPException(status_code=404, detail="Chat session not found")

    # Reserve this turn's quota BEFORE spending Gemini tokens: authenticated users are
    # charged CHAT_CREDIT_COST credits (pre-flight + atomic deduction → 402 if broke);
    # guests use the durable per-install daily-turn budget.
    quota, quota_err = _claim_chat_quota(user, x_guest_id, session_id=session_id, req=req)
    if quota_err is not None:
        return quota_err

    # Generate the AI response FIRST, then persist the user + assistant rows TOGETHER in one insert.
    # A generation failure therefore leaves NOTHING persisted (no orphaned user row for the client's
    # stream-failure reconcile to later duplicate), and the two rows commit atomically — matching the
    # streaming endpoint's persist contract.
    delivered = False  # True once the answer is durably persisted → gates the finally refund
    try:
        from app.services.chat_service import ChatService, _chat_thinking_budget

        chat_service = ChatService()

        # Prefer the session-persisted context (so a history reload re-grounds),
        # but let a per-message request value override (e.g. the seed message).
        ctx_type = request.context_type or session.data.get("context_type")
        ref_id = request.reference_id or session.data.get("reference_id")
        # On a live turn iOS ships the on-screen snapshot; on a history reopen it
        # sends none → replay the snapshot persisted at open time (migration 087).
        # Sanitize + bound the client grounding blob (it lands in the SYSTEM
        # instruction — an injection surface).
        # Sanitised ONCE, up front: a whitespace-only `context` is absent for every
        # reader — the prompt, the replay flag and the snapshot persist — or the three
        # disagreed (prompt saw none, flag said live, persist wrote spaces).
        req_ctx = sanitize_context(request.context)
        effective_context = sanitize_context(_effective_context(req_ctx, session.data))
        # True only when a stored snapshot is being replayed (reopen) — so the
        # prompt labels it as a point-in-time copy, not live data.
        context_is_replayed = not req_ctx and bool(effective_context)

        # Skips the DB round trip when it cannot apply, and never blocks the loop
        # when it does (see _reader_lens_for_async).
        reader_lens = await _reader_lens_for_async(user)

        started = _time.monotonic()
        attach_base_card = await _should_attach_base_card(supabase, session.data, session_id)
        try:
            ai_result = await asyncio.wait_for(
                chat_service.generate_response(
                    session_id=session_id,
                    user_message=msg,
                    session_type=session.data.get("session_type", "NORMAL"),
                    stock_id=session.data.get("stock_id"),
                    context=effective_context,
                    context_type=ctx_type,
                    reference_id=ref_id,
                    context_is_replayed=context_is_replayed,
                    reader_lens=reader_lens,
                    # Owner-scoped grounding: lets TICKER_REPORT read THIS user's frozen
                    # report row instead of only the close-aligned shared cache.
                    user_id=user["id"],
                    # The grounded asset's card once per session, on the first answer —
                    # the same rule as the stream door (see `_should_attach_base_card`).
                    attach_base_widget=attach_base_card,
                ),
                timeout=settings.CHAT_SEND_BUDGET_SECONDS,
            )
        except asyncio.TimeoutError as e:
            if isinstance(e, GeminiTimeoutError):
                # A per-CALL Gemini timeout (the SDK's own ceiling) is a subclass of
                # `asyncio.TimeoutError`, so this arm swallowed it and reported a "budget
                # overrun" for a turn that never got near the budget. Let the classifier
                # below map it to GEMINI_UNAVAILABLE with the right step.
                raise
            # Past the client's ceiling nobody is listening: iOS has already reported the
            # turn failed. Answer BEFORE any write so `delivered` stays False and the
            # `finally` refunds — a charged, persisted answer the user never saw is the
            # worst outcome this door has.
            logger.warning(
                "Non-stream chat turn exceeded CHAT_SEND_BUDGET_SECONDS=%.0f for session=%s",
                settings.CHAT_SEND_BUDGET_SECONDS, session_id,
            )
            return make_error_response(
                ErrorCode.GEMINI_UNAVAILABLE,
                message="chat generation exceeded the non-streaming budget",
                details={"session_id": session_id, "step": "chat_send_budget"},
            )

        # Output enforcement (OWASP LLM02/LLM07): redact high-confidence provider /
        # secret / internal-schema leaks, log any advice-boundary drift, then apply the
        # intent-gated disclaimer policy in code (not prompt-hope).
        clean_answer, enforced = enforce_answer(ai_result.get("content") or "")
        if not clean_answer.strip():
            # Empty generation → non-delivery. Don't persist a disclaimer-only row or bill
            # it; the finally refunds the turn (mirrors the stream path's empty-content guard).
            logger.warning(
                "Chat (send) empty generation for session=%s — refunding turn", session_id
            )
            return make_error_response(
                ErrorCode.GEMINI_UNAVAILABLE,
                message="empty chat generation",
                user_message="Cay AI couldn't respond right now. Please try again.",
            )
        advice_flags = scan_answer(clean_answer)
        if enforced or advice_flags:
            sec_logger.warning(
                "Chat guardrail (send) session=%s enforced=%r flags=%r: %r",
                session_id, enforced, advice_flags, clean_answer[:200],
            )
        # Disclaimer, gated on trade-action intent. The user's question is the primary
        # signal; `advice_directive` is OR'd in so a volunteered "you should buy it"
        # forces the line on even when the question itself was informational.
        # `suitability_claim` is deliberately NOT a trigger — chat_guardrails documents
        # that it fires on the model COMPLYING ("depends on circumstances I can't see"),
        # so using it would re-attach the line almost everywhere and undo the gate.
        trade_intent = is_trade_intent(msg) or ("advice_directive" in advice_flags)
        ai_result["content"], _ = finalize_disclaimer(clean_answer, trade_intent=trade_intent)

        # Build the widget payload (if Gemini triggered the stock tool)
        widget_payload = ai_result.get("widget")

        # A plain JSON route is NEVER cancelled on a client disconnect (Starlette only
        # watches for `http.disconnect` on streaming responses), so a turn the client
        # abandoned — phone locked, Wi-Fi lost, or its own 60 s ceiling — used to be
        # persisted and charged for nobody. Ask BEFORE the write, never after: `delivered`
        # must stay False so the `finally` refunds. Best-effort: the probe answers False
        # when it cannot tell, and then the turn simply proceeds as it always did.
        try:
            gone = await req.is_disconnected()
        except Exception as e:  # noqa: BLE001 — a probe failure must not fail the turn
            logger.warning("Chat (send) disconnect probe failed (%s: %s)", type(e).__name__, e)
            gone = False
        if gone:
            logger.warning(
                "Chat (send) client gone before persist for session=%s — not saving, refunding",
                session_id,
            )
            return make_error_response(
                ErrorCode.GEMINI_UNAVAILABLE,
                message="client disconnected before the answer was persisted",
                user_message="Cay AI couldn't respond right now. Please try again.",
                details={"step": "chat_send_disconnected"},
            )

        # Explicit created_at keeps user-before-assistant ordering: a single multi-row insert would
        # otherwise stamp both rows with the same now() default, and get_chat_history orders by
        # created_at asc — the assistant could sort ahead of the question.
        now = datetime.now(timezone.utc)
        user_msg: dict = {
            "session_id": session_id,
            "role": "user",
            "content": msg,
            "created_at": now.isoformat(),
        }
        # Same shape the stream door persists (`_rich_content_for_turn`): a thinking card
        # with no streamed reasoning, the source pills, and the widget under both keys.
        sources = ai_result.get("sources") or None
        thinking_payload = {
            "stages": [],
            "reasoning": "",
            "source_count": len(sources) if sources else 0,
            "elapsed_ms": int((_time.monotonic() - started) * 1000),
        }
        # A cut answer (the model hit its ceiling after real text) is marked and gets the
        # single "Continue" chip — persisted with the row so history replays the same
        # state, and the identical shape the stream door writes for the same verdict.
        truncated = bool(ai_result.get("truncated"))
        rich_content = _rich_content_for_turn(
            thinking_payload, [widget_payload] if widget_payload else None, sources,
            truncated=truncated, finish_reason=ai_result.get("finish_reason"),
        )
        if truncated and is_length_cut(ai_result.get("finish_reason")):
            # The way out of a LENGTH cut; a SAFETY / RECITATION stop gets no chip.
            rich_content["suggestions"] = [_CONTINUE_CHIP]
        ai_msg: dict = {
            "session_id": session_id,
            "role": "assistant",
            "content": ai_result["content"],
            "citations": ai_result.get("citations"),
            "tokens_used": ai_result.get("tokens_used"),
            "rich_content": rich_content,
            "created_at": (now + timedelta(milliseconds=1)).isoformat(),
        }

        assistant_row = _persist_turn(supabase, session_id, user_msg, ai_msg)

        # The answer is durably persisted → the turn was delivered. Past this point the
        # finally must NOT refund (a disconnect during the best-effort session/token steps
        # below is not a failed turn).
        delivered = True
        # Off the hot path by construction: the answer is already persisted, so a failure
        # here cannot affect the turn. (No router on this path — only the ticker is known.)
        await _record_memory_facts_async(user, session.data.get("stock_id"), None)
        # Zero Gemini cost (deep-dive cache HIT) → refund the charge: the user still got the
        # answer, but we don't bill a turn that incurred no AI cost. `== 0` (not falsy) so a
        # real generation reporting None/unknown usage is never wrongly refunded.
        if ai_result.get("tokens_used") == 0:
            quota.settle_no_cost("chat_cache_hit")
        elif ai_result.get("degraded"):
            # `generate_response` fell back to a tool-less plain-text call (or every tool the
            # model called failed): the answer to a stock question with none of its live data.
            # The stream path already refunds its degraded shapes; this one only logged.
            quota.settle_no_cost(f"chat_degraded_{ai_result['degraded']}")
        # A charged turn earns this session one free follow-up (no-op after a refund).
        quota.on_delivered()
        # Settlement is final now → record it on the row so the response (and a later
        # history reload) can show the user they were not charged.
        _attach_turn_cost(supabase, assistant_row, quota)

        # Update session metadata. message_count + last_message_at are maintained atomically by the
        # trg_chat_message_count AFTER-INSERT trigger (one +1 per inserted row), so we do NOT set them
        # here — an absolute `current_count + 2` from a request-start snapshot both double-counts the
        # trigger and races/undercounts on concurrent same-session sends.
        preview = ai_result["content"][:100]
        current_count = session.data.get("message_count", 0)

        # Auto-title from the user's first question so history search-by-name matches the topic.
        # This upgrades the auto-generated defaults ONLY — "New Chat"/None (general chats) AND the
        # "Chat about <TICKER>" default given to asset/report chats — and only on the first exchange
        # (message_count == 0), so a later message or a user rename is never clobbered. Guard against
        # an empty/whitespace first message so we never blank a useful title.
        update_payload: dict = {
            "preview_message": preview,
        }
        existing_title = session.data.get("title")
        is_generic_title = (
            existing_title in ("New Chat", None)
            or (isinstance(existing_title, str) and existing_title.startswith("Chat about "))
        )
        first_question = msg
        if current_count == 0 and is_generic_title and first_question:
            update_payload["title"] = first_question[:80]

        # Best-effort post-delivery write: the turn is already persisted + charged, so a
        # failure here must NOT surface as a retryable 500 (a client retry would re-charge +
        # duplicate the turn). Guard it like the snapshot / token writes below.
        try:
            supabase.table("chat_sessions").update(update_payload).eq(
                "id", session_id
            ).execute()
        except Exception as e:
            logger.warning(
                "Chat (send) session-metadata update failed for %s (%s: %s) — ignoring",
                session_id, type(e).__name__, e,
            )

        # Persist the on-screen snapshot (best-effort, guarded) so a later reopen re-grounds.
        _persist_context_snapshot(supabase, session_id, req_ctx, session.data)

        # Best-effort daily token accounting for spend observability.
        _record_chat_tokens(user, x_guest_id, ai_result.get("tokens_used"))

        return _row_to_message(assistant_row)

    except Exception as e:
        # Classified like the stream door, so the code the SERVER knows reaches the user:
        # a quota outage used to be a bare 500 "Failed to generate response" here, and
        # iOS rendered its generic "technical difficulties" copy for what is a known,
        # retry-later condition with its own user_message.
        from app.integrations.gemini import _is_quota_error, is_transient_gemini_error
        if is_transient_gemini_error(e):
            code = ErrorCode.GEMINI_QUOTA_EXCEEDED if _is_quota_error(e) else ErrorCode.GEMINI_UNAVAILABLE
            logger.warning("Chat response failed (%s: %s) — %s", type(e).__name__, e, code.value)
            return make_error_response(
                code,
                message=f"{type(e).__name__}: {e}"[:300],
                details={"session_id": session_id, "step": "chat_generate"},
            )
        logger.error(f"Chat response failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate response")
    finally:
        # Any non-delivery — generation/persist error, the budget, the disconnect probe
        # above — hands the turn's quota back exactly once (credit for authed, daily turn
        # for guest) so an outage never burns a paid turn. (No CancelledError arrives on
        # this door: Starlette cancels only STREAMING responses on a client disconnect,
        # which is why the explicit `is_disconnected()` probe before the persist exists.)
        if not delivered:
            quota.refund_once("chat_undelivered")


@router.post("/sessions/{session_id}/messages/stream")
async def stream_chat_message(
    session_id: str,
    request: SendChatMessageRequest,
    req: Request,
    user: dict = Depends(get_chat_identity),  # per-INSTALL guest partition (migration 111)
    supabase: Client = Depends(get_supabase),
    x_guest_id: Optional[str] = Header(None, alias="X-Guest-Id"),
    _rate: None = ChatRateLimit,
):
    """Stream an AI response over SSE (``text/event-stream``).

    Frames: ``meta`` → ``token``* → ``done``, or ``reset`` (discard partial
    tokens) before a fallback ``done``, or ``error``. Nothing is persisted until
    a COMPLETE answer exists (streamed, or via the server-side full-generation
    fallback), so a dropped stream leaves no half-message.

    ⚠️ Persistence and the charge happen BEFORE the suggestions call, ``credits`` and
    ``done`` — so a drop in that window leaves a saved, charged turn the client never
    acknowledged. The iOS reconcile (``GET /chat/sessions/{id}``) is the oracle that tells
    "saved" from "lost" before any retry; it must never regenerate on a failed GET (the
    transient-aware 409 above exists so that GET can say "unavailable" rather than "not
    found").
    """
    # Input hygiene BEFORE constructing the stream, so an oversize/empty message is a
    # normal JSON error (like the 404s below), not a mid-stream SSE frame.
    msg, msg_err = validate_message(request.message)
    if msg_err is not None:
        return make_error_response(msg_err, message="chat message rejected by input validation")
    inj = scan_input(msg)
    if inj:
        sec_logger.warning(
            "Chat input injection markers %s (session=%s user=%s): %r",
            inj, session_id, user.get("id"), msg[:200],
        )

    # Verify ownership up front so a bad session is a real 404 (not an SSE frame).
    try:
        session = (
            supabase.table("chat_sessions")
            .select("*")
            .eq("id", session_id)
            .eq("user_id", user["id"])
            .single()
            .execute()
        )
    except Exception as e:
        raise _session_lookup_failed(e)
    if not session.data:
        raise HTTPException(status_code=404, detail="Chat session not found")

    # Reserve this turn's quota BEFORE the stream starts (pre-flight): authenticated users
    # are charged CHAT_CREDIT_COST credits (→ 402 if broke), guests use the daily-turn
    # budget. Returning a clean JSON error here (not an SSE frame) mirrors the 404s above;
    # iOS surfaces INSUFFICIENT_CREDITS via its non-stream fallback decode.
    quota, quota_err = _claim_chat_quota(user, x_guest_id, session_id=session_id, req=req)
    if quota_err is not None:
        return quota_err

    sdata = session.data
    ctx_type = request.context_type or sdata.get("context_type")
    ref_id = request.reference_id or sdata.get("reference_id")
    # Live turn → the iOS on-screen snapshot; history reopen (context=None) →
    # the snapshot persisted at open time (migration 087). Sanitized + bounded
    # since it lands in the SYSTEM instruction (injection surface).
    req_ctx = sanitize_context(request.context)   # whitespace-only → absent, everywhere
    effective_context = sanitize_context(_effective_context(req_ctx, sdata))
    # True only when a stored snapshot is being replayed (reopen) — labels it as
    # a point-in-time copy in the prompt so stale figures aren't answered as live.
    context_is_replayed = not req_ctx and bool(effective_context)
    session_type = sdata.get("session_type", "NORMAL")
    stock_id = sdata.get("stock_id")
    # "First turn of this session" — the same trigger-maintained counter the auto-title
    # keys on (`trg_chat_message_count` adds one per persisted row, both rows of a turn
    # land in one insert, and this row was read before this turn's write). Drives the
    # once-per-session price card (E7): the grounded asset's card is attached to the
    # FIRST answer only and stays on screen; later one-line follow-ups no longer arrive
    # under a full-height repeat of it. A first turn that failed to persist leaves the
    # counter at 0, so the next attempt shows the card again — acceptable. A later turn
    # whose earlier answers never rendered a card (a timed-out fetch) is treated as the
    # first for the card's purposes — see `_session_has_card`.
    first_turn = await _should_attach_base_card(supabase, sdata, session_id)
    user_message = msg

    # Non-delivery backstop for the stream: `_metered_stream`'s finally refunds this turn
    # exactly once if the generator exits without a durably-persisted answer (incl. a
    # client-disconnect CancelledError the inner except-Exception guards miss). event_gen
    # flips it True right after the persist.
    delivered = False

    async def event_gen():
        nonlocal delivered
        import time as _time
        from app.services.chat_service import ChatService, _chat_thinking_budget
        from app.integrations.gemini import (
            _is_quota_error,
            is_transient_gemini_error,
        )

        chat_service = ChatService()
        started = _time.monotonic()

        grounded = (
            f"{ctx_type}:{ref_id}"
            if ctx_type and ctx_type.strip().upper() != "NONE"
            else ""
        )
        # `user_message` is the NORMALIZED text the server actually persists
        # (`validate_message` → `normalize_text`: NFKC + invisible/bidi stripping +
        # control stripping + blank-line collapse). The client reconciles a failed
        # stream by matching its own RAW typed string against history, so any message
        # normalisation touched — a smart quote from the iOS keyboard, an emoji
        # variation selector, a full-width character, a zero-width joiner — never
        # matched, and the reconcile concluded the turn had NOT persisted and re-sent
        # it. That stores the turn twice AND charges a second credit. Sending the
        # server's own copy lets the client compare like with like.
        yield _sse("meta", {
            "session_id": session_id,
            "grounded_on": grounded,
            "user_message": user_message,
        })

        content: Optional[str] = None
        citations = None
        widgets: list = []
        tokens_used = None
        sources = None
        suggestions = None
        streamed_any = False
        used_fallback = False   # set when the full-generation fallback replaces the stream
        # Sink `stream_synthesis` writes a degradation reason into (an async generator
        # cannot return a value alongside yield). Read once the turn is persisted.
        stream_signals: dict = {}
        # The model that served the answer (single mode picks it via `select_model`);
        # a continuation round reuses it. None → the client default.
        answer_model: Optional[str] = None

        # The model streams REAL reasoning: stream_text tags each chunk as ("thought"|"answer", text).
        # Thoughts → the thinking card (`reasoning` frames), answer → the bubble (`token` frames).
        # Reasoning is model text → it rides the same identity-guarded system instruction which
        # forbids "AI/model" mentions.
        reasoning_text = ""
        answer_parts: list = []
        reasoning_parts: list = []
        # Bound BEFORE the try, because both are read from paths that run when the try
        # raised early. `prepare_stream_generation` does Supabase + RAG + Gemini + FMP work,
        # so a transient blip there left `route` unassigned — and the fallback handler below
        # reads `reader_lens`, while the persist block reads `route`. Python evaluates call
        # arguments before entering the callee, so `_record_memory_facts(user, stock_id, route)`
        # raised UnboundLocalError at the CALL SITE (regardless of the memory feature flag),
        # inside the persist `try`, AFTER `delivered = True` — turning a turn that was saved
        # into "Your answer was generated but couldn't be saved" and skipping the `done` frame,
        # the auto-title, the snapshot and the suggestions.
        #
        # `degraded: True` is the honest default: nothing was classified, so `select_model`
        # must keep the flagship model rather than downgrade an unclassified turn.
        reader_lens: Optional[str] = None
        # `warmed` is read AFTER the try (the suggestions step reuses the warmed chips), so it
        # must be bound before it: when prep or the router raises, the gather that binds it
        # never runs, the fallback still delivers and charges the turn, and an unbound name
        # here would kill the stream after persistence with no `done` frame.
        warmed: Optional[dict] = None
        # Settlement inputs that the fallback / replay branches set; read after the try.
        fallback_degraded: Optional[str] = None   # generate_response's own degraded marker
        replayed_warm = False                     # the starter-warm answer was actually SERVED
        tool_calls_seen = 0                       # single-mode agentic tool calls this turn
        tool_calls_failed = 0                     # ...of which came back as {error: …}
        route: dict = {
            "specialists": ["general"], "mode": "single", "labels": ["General"], "degraded": True,
        }

        try:
            # Multi-agent (Phase 3): a cheap router picks the specialist lens(es). Run it in PARALLEL
            # with prep so the router's ~400ms hides behind the RAG/widget work. Never raises → general.
            from app.services.agents.chat_router import route_question, select_model
            from app.services.agents.chat_specialists import apply_specialist
            reader_lens = await _reader_lens_for_async(user)
            prep_coro = chat_service.prepare_stream_generation(
                session_id=session_id,
                user_message=user_message,
                session_type=session_type,
                stock_id=stock_id,
                context=effective_context,
                context_type=ctx_type,
                reference_id=ref_id,
                context_is_replayed=context_is_replayed,
                reader_lens=reader_lens,
                user_id=user["id"],
            )
            # A pre-computed answer to one of today's suggestion chips, if this is one.
            #
            # Looked up ALONGSIDE prep and the router (one gather, below) so it costs no
            # wall-clock; on a hit the router's result is simply discarded. Never raises; a
            # miss means "answer live".
            #
            # ONLY for an UNGROUNDED turn. The warmed rows are written by the global chat
            # with no screen behind them, and the lookup keys on the question text alone —
            # so the same words typed inside a STOCK / BOOK / report session used to replay
            # the global, ungrounded answer and throw away the grounding block, the live
            # quote line and the enrichment prep had just built.
            from app.services.chat_context_resolver import _NO_CONTEXT
            from app.services.chat_starter_warm_service import lookup as _warm_lookup

            ungrounded_turn = (
                not stock_id
                and not (effective_context or "").strip()
                and (ctx_type or "").strip().upper() in _NO_CONTEXT
            )

            async def _warm_if_ungrounded():
                return await _warm_lookup(user_message) if ungrounded_turn else None

            if settings.CHAT_MULTI_AGENT_ENABLED:
                # All three in ONE gather. Awaiting the warm lookup first to decide whether
                # to route would serialise the router's ~400ms behind prep on every MISS —
                # i.e. slow down the common case to save a cheap flash-lite call in the rare
                # one. The wasted classification on a hit is the right side of that trade.
                prep, route, warmed = await asyncio.gather(
                    prep_coro,
                    route_question(chat_service.gemini, user_message),
                    _warm_if_ungrounded(),
                )
                if warmed is not None or prep.get("deep_dive_cached"):
                    # The stored answer was written by the general path (a starter-warm row
                    # or the 24 h deep-dive cache), so labelling the turn with a specialist
                    # the replay never consulted would put a false "Consulting Macro" stage
                    # on the thinking card.
                    route = {"specialists": ["general"], "mode": "single", "labels": ["General"]}
            else:
                prep, warmed = await asyncio.gather(prep_coro, _warm_if_ungrounded())
                route = {"specialists": ["general"], "mode": "single", "labels": ["General"]}

            # Capture sources up-front so they survive even if streaming later fails and we
            # fall back to full generation below.
            sources = prep.get("sources")
            if sources:
                yield _sse("sources", {"sources": sources})
            # Surface the routing decision (a real specialist / a synthesis) for the thinking card.
            if route["specialists"] != ["general"]:
                yield _sse("routing", {
                    "specialists": route["specialists"],
                    "labels": route["labels"],
                    "mode": route["mode"],
                })

            # Agentic streaming: the model may call tools (analyst / sentiment / chart / …)
            # mid-stream. thought → reasoning card, answer → bubble, tool → progress + widget.
            from app.services.agents.chat_tools import (
                build_chat_tool_declarations, build_chat_tool_handlers,
                tools_for_asset_type, widget_from_tool_result, widget_key,
            )
            asset_type = prep.get("asset_type") or "NORMAL"
            # Filtered to the tools that MEAN something for this asset class — not just
            # "equity three, plus the index one for INDEX". See `chat_tools._TOOLS_BY_ASSET_TYPE`.
            tools = build_chat_tool_declarations(asset_type)
            # The HANDLER map is filtered too: the declarations decide what the model is
            # offered, but a handler left in the map for an undeclared tool would still
            # run if the model named it from memory (a second door around the class table).
            allowed = tools_for_asset_type(asset_type)
            handlers = {
                name: fn
                for name, fn in build_chat_tool_handlers(
                    chat_service, screen_symbol=stock_id, screen_asset_type=asset_type,
                    user_id=user["id"],
                ).items()
                if name in allowed
            }

            # Start with the deterministic base widget on the FIRST turn only (so an
            # asset-detail chat shows its chart once, under the first answer, where it
            # stays on screen); agentic tool calls add more, deduped by (widget_type,
            # ticker). On later turns of a grounded session the screen asset's key is
            # pre-seeded into the dedup set, so a tool card for the SAME asset is skipped
            # too — the tester's "only show the chart at the first question" — while a
            # tool card for a different ticker still attaches. The LIVE QUOTE line in the
            # instruction is untouched: the prose keeps its numbers either way.
            seen_widgets: set = set()
            base_widget = prep.get("widget")
            if base_widget:
                seen_widgets.add(widget_key(base_widget))
                if first_turn:
                    widgets.append(base_widget)
                else:
                    logger.info(
                        "Chat stream: base card %s skipped on a later turn of session %s",
                        widget_key(base_widget), session_id,
                    )

            # Single mode: one specialist streams its focused agentic answer. Synthesize mode: several
            # specialists run in parallel + a merged answer streams (their widgets arrive as
            # ("widget", …) events since the specialist runs aren't streamed to the client directly).
            # A cached "AI Analyst" brief — replay it instead of paying Gemini again.
            #
            # This cache (24h, `market_deep_dive_cache`) existed but was consulted ONLY by the
            # non-streaming path, and streaming is on by default — so no real user ever hit it.
            # The button's prompt is a constant per symbol, so a second tap now costs nothing.
            logger.info(
                "CHAT_TURN session=%s asset=%s deep_dive=%s cached=%s cap=%s",
                session_id, asset_type, prep.get("is_deep_dive"),
                bool(prep.get("deep_dive_cached")),
                settings.CHAT_DEEP_DIVE_MAX_OUTPUT_TOKENS
                if prep.get("is_deep_dive") else settings.CHAT_MAX_OUTPUT_TOKENS,
            )
            deep_dive_cached = prep.get("deep_dive_cached")
            if deep_dive_cached:
                logger.info(
                    "Deep dive cache HIT (stream) for %s — replaying %d chars, no Gemini call",
                    stock_id, len(deep_dive_cached),
                )
                answer_stream = _replay_cached_answer(deep_dive_cached)
                # Zero Gemini cost. `tokens_used` was only ever assigned on the
                # stream→non-stream FALLBACK, so on the default streaming path this replay
                # stayed `None`, the `== 0` refund gate below never fired, and a user tapping
                # "AI Analyst" twice paid a second credit for a cached answer — while the
                # non-streaming endpoint refunded the very same hit.
                tokens_used = 0
            elif warmed is not None:
                # A suggestion chip whose answer was pre-computed this morning. Replayed
                # through the SAME path as a cached deep dive, so it persists, streams and
                # renders identically to a generated turn — it just arrives immediately.
                #
                # Still CHARGED, deliberately: one credit buys one answer regardless of how
                # fast it arrived. Making a set of shared questions free would create a
                # second, farmable price for the same product.
                logger.info(
                    "Starter warm HIT — replaying %d chars, no Gemini call",
                    len(warmed["answer"]),
                )
                replayed_warm = True
                w = warmed.get("widget")
                if w and warmed.get("widget_stale"):
                    # The card was rendered at warm time. Re-fetched by symbol so the
                    # price and the Live/Closed dot are this minute's; dropped if that
                    # fails — never a stale price replayed as live.
                    w = await chat_service.refresh_widget(w)
                if w and widget_key(w) not in seen_widgets:
                    seen_widgets.add(widget_key(w))
                    widgets.append(w)
                answer_stream = _replay_cached_answer(warmed["answer"])
            elif route["mode"] == "synthesize":
                answer_stream = chat_service.stream_synthesis(
                    prep, user_message, route, tools, handlers, signals=stream_signals,
                )
            else:
                system_instruction = apply_specialist(prep["system_instruction"], route["specialists"][0])
                # Free cost lever: the classification above is already paid for. A
                # ticker-less conceptual question does not need the flagship model.
                # Anything unproven falls back to it — see select_model.
                answer_model = select_model(
                    route,
                    has_ticker=bool(stock_id),
                    has_client_context=bool(effective_context),
                )
                answer_stream = chat_service.gemini.stream_agentic(
                    prep["prompt"], tools=tools, tool_handlers=handlers,
                    system_instruction=system_instruction,
                    model_name=answer_model,
                    # A deep dive is deliberately long; the ordinary ceiling assumes the
                    # brevity directive and cut the brief off mid-sentence.
                    max_output_tokens=(
                        settings.CHAT_DEEP_DIVE_MAX_OUTPUT_TOKENS
                        if prep.get("is_deep_dive")
                        else settings.CHAT_MAX_OUTPUT_TOKENS
                    ),
                    # The ceiling above bounds thoughts + answer TOGETHER; without this
                    # the model spent 1150 of 1200 thinking and the answer got 40 tokens.
                    # Resolved per model: the cheap route must not have thinking switched ON.
                    thinking_budget=_chat_thinking_budget(answer_model),
                    # Correlates the GEMINI_USAGE line to a turn: without the route you
                    # cannot tell which lens (and so which model) served this answer.
                    usage_tag=f"{session_id}:{route['specialists'][0]}",
                )

            async for kind, payload in _with_keepalive(
                answer_stream, deadline=started + _stream_budget_seconds()
            ):
                if kind == "keepalive":
                    # An SSE comment line: every client ignores it, and it keeps the
                    # connection off iOS's 120 s idle timeout while a synthesis round
                    # buffers its specialists or a long tool (a grounded web search) runs.
                    yield ": keepalive\n\n"
                    continue
                streamed_any = True
                if kind == "thought":
                    reasoning_parts.append(payload)
                    yield _sse("reasoning", {"delta": payload})
                elif kind == "answer":
                    answer_parts.append(payload)
                    yield _sse("token", {"delta": payload})
                elif kind == "tool":
                    # Real progress into the thinking card + collect any renderable widget.
                    # `_run_tool_handler` never raises: a failed / timed-out tool arrives as
                    # `{"error": …}` — surfaced on the frame (iOS ignores unknown keys) and
                    # counted, so a turn whose EVERY tool failed settles as degraded below,
                    # exactly like the non-streaming door.
                    _res = payload.get("result")
                    _err = _res.get("error") if isinstance(_res, dict) else None
                    tool_calls_seen += 1
                    # Only an UPSTREAM failure (timeout, FMP/CoinGecko/Supabase error —
                    # tagged `upstream` at the source) counts toward the refund. A result
                    # the MODEL shaped — an invalid ticker, a symbol the provider does
                    # not cover — is answered ("not covered") and stays charged: counted,
                    # a decoy `get_stock_chart_data("QQQQQ")` in every message made every
                    # turn free, at 15/min, with the balance never moving.
                    if _err and isinstance(_res, dict) and _res.get("upstream"):
                        tool_calls_failed += 1
                    yield _sse("tool_step", {
                        "name": payload.get("name"), "args": payload.get("args"),
                        "error": str(_err)[:200] if _err else None,
                    })
                    w = widget_from_tool_result(_res)
                    if w is not None and widget_key(w) not in seen_widgets:
                        seen_widgets.add(widget_key(w))
                        widgets.append(w)
                elif kind == "widget":
                    # Synthesis path: a specialist's widget (already the full payload).
                    if payload is not None and widget_key(payload) not in seen_widgets:
                        seen_widgets.add(widget_key(payload))
                        widgets.append(payload)
                elif kind == "finish":
                    # The model CUT the answer (MAX_TOKENS / SAFETY / RECITATION) after
                    # real text had streamed. It used to end cleanly: charged in full, and
                    # for a deep dive cached for every user for 24 h with its last sentence
                    # missing. Marked degraded → refunded, never cached.
                    #
                    # The reason is recorded on its own key as well: `degraded` is
                    # first-wins (a `partial_specialists` turn keeps that label for the
                    # ledger), but the truncation MARK on the wire and the continuation
                    # below key off `finish_reason`, so a cut that lands on an already
                    # degraded turn is still continued and still marked.
                    if payload:
                        stream_signals["finish_reason"] = str(payload)
                        if not stream_signals.get("degraded"):
                            logger.warning(
                                "Chat stream: answer cut by finish_reason=%s for session %s — "
                                "settling as degraded", payload, session_id,
                            )
                            stream_signals["degraded"] = "truncated"

            # ── Auto-continue a cut answer (E1) ──────────────────────────────
            # One continuation round, streamed into the SAME turn, so the user reads a
            # complete answer without a tap. Only after real answer text (an empty cut is
            # the "empty stream result" path below), never on a cached replay (nothing
            # was generated, nothing can be continued), and guarded so that NOTHING it
            # does can reach the full-regenerate fallback in the except below — a
            # failed continuation must leave the partial answer in place, not discard it.
            if (
                is_length_cut(stream_signals.get("finish_reason"))
                and answer_parts
                and getattr(settings, "CHAT_AUTO_CONTINUE_ENABLED", True)
                and not prep.get("deep_dive_cached")
                and not replayed_warm
            ):
                cont_signals: dict = {}
                try:
                    async for kind, payload in _with_keepalive(
                        _continue_cut_answer(
                            chat_service, prep, "".join(answer_parts),
                            model_name=answer_model,
                            max_output_tokens=(
                                settings.CHAT_DEEP_DIVE_MAX_OUTPUT_TOKENS
                                if prep.get("is_deep_dive")
                                else settings.CHAT_MAX_OUTPUT_TOKENS
                            ),
                            usage_tag=f"{session_id}:continue",
                            signals=cont_signals,
                        ),
                        deadline=started + _stream_budget_seconds(),
                    ):
                        if kind == "keepalive":
                            yield ": keepalive\n\n"
                        elif kind == "answer":
                            answer_parts.append(payload)
                            yield _sse("token", {"delta": payload})
                except Exception as e:
                    # A deadline (`GeminiTimeoutError` from the keepalive wrapper) or
                    # anything the helper did not swallow: the partial answer stands.
                    logger.warning(
                        "Chat continuation abandoned (%s: %s) for session %s — turn stays truncated",
                        type(e).__name__, e, session_id,
                    )
                    cont_signals["continuation"] = "failed"
                verdict = cont_signals.get("continuation", "failed")
                if verdict == "clean":
                    logger.info(
                        "Chat stream: cut answer completed by a continuation round for session %s",
                        session_id,
                    )
                    stream_signals["continued"] = True
                    stream_signals.pop("finish_reason", None)
                    if stream_signals.get("degraded") == "truncated":
                        stream_signals.pop("degraded", None)
                else:
                    logger.warning(
                        "Chat stream: continuation %s for session %s (finish=%s) — turn stays truncated",
                        verdict, session_id,
                        cont_signals.get("continuation_finish") or stream_signals.get("finish_reason"),
                    )
                    if cont_signals.get("continuation_finish"):
                        stream_signals["finish_reason"] = cont_signals["continuation_finish"]

            content = "".join(answer_parts)
            reasoning_text = "".join(reasoning_parts)
            if not content.strip():
                raise RuntimeError("empty stream result")
            if (
                tool_calls_seen
                and tool_calls_failed == tool_calls_seen
                and stream_signals.get("degraded") in (None, "truncated")
            ):
                # Every tool the model called failed (FMP rate limit, timeouts): the answer
                # has none of its live data. The non-streaming door already settles this
                # shape as `no_tools`; without this the default door charged it in full.
                # `no_tools` outranks `truncated` for the LEDGER label (the non-stream door
                # sets it first-wins in that order); the truncation MARK is separate.
                logger.warning(
                    "Chat stream: all %d tool call(s) failed for session %s — settling as degraded",
                    tool_calls_seen, session_id,
                )
                stream_signals["degraded"] = "no_tools"
            citations = prep.get("citations")

            # Persist a freshly-generated brief so the next tap replays it for free. Best-effort
            # by design — a cache write must never cost the user an answer they already received
            # — but never silent: `_upsert_deep_dive_cache` logs its own failure. Off the event
            # loop because it is a synchronous Supabase call.
            if (
                prep.get("is_deep_dive")
                and not deep_dive_cached
                and prep.get("deep_dive_context")
                and stock_id
                and len(content) > 100
                # A DEGRADED brief (no specialists, unmerged, every tool failed) is refunded
                # below — caching it would replay it for 24 h as a zero-cost "hit".
                and not stream_signals.get("degraded")
            ):
                await asyncio.to_thread(
                    chat_service._upsert_deep_dive_cache,
                    stock_id, prep["deep_dive_context"], content, user_message,
                    prep.get("asset_type") or "",
                )

        except Exception as e:
            # Stream failed (quota / timeout / empty / disconnect). Fall back to
            # the full non-streaming generation so the user still gets an answer.
            logger.warning(
                "Chat stream failed (%s: %s) — falling back to full generation",
                type(e).__name__, e,
            )
            used_fallback = True
            try:
                _fallback_task = asyncio.ensure_future(chat_service.generate_response(
                    session_id=session_id,
                    user_message=user_message,
                    session_type=session_type,
                    stock_id=stock_id,
                    context=effective_context,
                    context_type=ctx_type,
                    reference_id=ref_id,
                    context_is_replayed=context_is_replayed,
                    # Same lens the aborted stream used — a fallback that answered
                    # differently would be visible to the user as a personality change.
                    reader_lens=reader_lens,
                    user_id=user["id"],
                    # Same once-per-session card rule as the stream it replaces.
                    attach_base_widget=first_turn,
                ))
                # The fallback is one awaited call with tools inside it — nothing reaches
                # the client until it returns, so heartbeat it the same way as the pump —
                # under the SAME turn deadline: a pump that spent the budget must not be
                # followed by a fallback that spends it again.
                _fallback_deadline = max(
                    _time.monotonic() + 5.0,
                    min(started + _stream_budget_seconds(),
                        _time.monotonic() + settings.CHAT_SEND_BUDGET_SECONDS),
                )
                try:
                    while True:
                        _remaining = _fallback_deadline - _time.monotonic()
                        if _remaining <= 0:
                            raise GeminiTimeoutError(
                                "stream fallback exceeded the turn budget "
                                f"(CHAT_STREAM_BUDGET_SECONDS={_stream_budget_seconds():.0f})"
                            )
                        _done, _ = await asyncio.wait(
                            {_fallback_task}, timeout=min(_keepalive_seconds(), _remaining)
                        )
                        if _done:
                            break
                        yield ": keepalive\n\n"
                except BaseException:
                    _fallback_task.cancel()
                    raise
                ai_result = _fallback_task.result()
                content = ai_result.get("content")
                citations = ai_result.get("citations")
                fb_widget = ai_result.get("widget")
                widgets = [fb_widget] if fb_widget else []  # discard streamed widgets; fallback replaces
                tokens_used = ai_result.get("tokens_used")
                # The aborted stream's thoughts don't correspond to this fallback answer — drop them
                # so the persisted thinking card matches (the `reset` frame clears the live display).
                reasoning_text = ""
                # Likewise its degraded SIGNAL: `stream_synthesis` sets `no_specialists` BEFORE
                # its rescue run, so a rescue that then raises would leave a stale marker that
                # refunded a perfectly healthy fallback answer. The fallback's OWN marker
                # (`generate_response` fell to plain text, or every tool it called failed) is
                # what settlement must see — the identical result the non-streaming door refunds.
                stream_signals.pop("degraded", None)
                fallback_degraded = ai_result.get("degraded")
                # …and the aborted stream's cut marker: the fallback answer is a new
                # generation. Its OWN cut (the non-stream door reads `finish_reason` too)
                # is what the truncation mark below must reflect.
                stream_signals.pop("finish_reason", None)
                stream_signals.pop("continued", None)
                if ai_result.get("truncated"):
                    stream_signals["finish_reason"] = str(ai_result.get("finish_reason") or "MAX_TOKENS")
                if streamed_any:
                    # Discard any partial tokens before the full answer replaces them.
                    yield _sse("reset", {})
            except Exception as e2:
                # A transient Gemini condition (quota or "high demand" overload) is
                # a retry-later, not a code bug — WARNING, not an ERROR Sentry page.
                if is_transient_gemini_error(e2):
                    logger.warning("Chat stream fallback degraded (transient): %s", e2)
                    code = "GEMINI_QUOTA_EXCEEDED" if _is_quota_error(e2) else "GEMINI_UNAVAILABLE"
                else:
                    logger.error("Chat stream fallback failed: %s", e2, exc_info=True)
                    code = "INTERNAL_ERROR"
                quota.refund_once("chat_stream_fallback_failed")  # no answer → hand the turn back
                yield _sse("error", {
                    "error_code": code,
                    "user_message": "Cay AI couldn't respond right now. Please try again.",
                })
                return

        if not (content or "").strip():
            # Whitespace-only is empty: the non-stream door refunds it, and persisting
            # "\n" as an answer charged the user for a blank bubble.
            quota.refund_once("chat_stream_empty")  # no answer produced → hand the turn back
            yield _sse("error", {
                "error_code": "INTERNAL_ERROR",
                "user_message": "Cay AI couldn't respond right now. Please try again.",
            })
            return

        # Output enforcement (OWASP LLM02/LLM07): redact high-confidence provider /
        # secret / internal-schema leaks from the finished answer, then log any
        # advice-boundary drift (monitor-only — a false positive dropping a good
        # answer is worse than a flag). The redacted `content` is what gets persisted
        # and carried in the authoritative `done` frame.
        content, enforced = enforce_answer(content)
        advice_flags = scan_answer(content)
        if enforced or advice_flags:
            sec_logger.warning(
                "Chat guardrail (stream) session=%s enforced=%r flags=%r: %r",
                session_id, enforced, advice_flags, content[:200],
            )

        # REASONING IS A SECOND OUTPUT CHANNEL and was never enforced. It is rendered in
        # the thinking card and carried in the `done` frame + the persisted turn, so an
        # identity/secret leak there is durable — it survives a reload and is re-served
        # on history load. Model reasoning is in fact the MORE likely place to name the
        # underlying provider (invariant #7), because the identity rule shapes the answer
        # far more strongly than the scratchpad.
        #
        # Enforced HERE, next to the answer, rather than per-chunk at the `reasoning`
        # yield above. Per-chunk is actively wrong: a pattern spanning a chunk boundary
        # is never matched, and rewriting a chunk can split a token mid-redaction. This
        # site sees the whole finalized string.
        if reasoning_text:
            reasoning_text, reasoning_enforced = enforce_answer(reasoning_text)
            if reasoning_enforced:
                sec_logger.warning(
                    "Chat guardrail (stream reasoning) session=%s enforced=%r: %r",
                    session_id, reasoning_enforced, reasoning_text[:200],
                )

        # Disclaimer policy in code, gated on trade-action intent — same two signals and
        # the same helper as the non-streaming path, so the two cannot drift. The helper
        # may SHORTEN `content` (the strip), hence assign-then-yield rather than append.
        # The suffix is streamed live only on the pure-streamed path; a fallback answer
        # arrives whole via `done`, so there is no live token to chase there.
        trade_intent = is_trade_intent(user_message) or ("advice_directive" in advice_flags)
        content, _suffix = finalize_disclaimer(content, trade_intent=trade_intent)
        if _suffix and streamed_any and not used_fallback:
            yield _sse("token", {"delta": _suffix})

        elapsed_ms = int((_time.monotonic() - started) * 1000)
        thinking_payload = {
            "stages": [],                    # canned steps replaced by the streamed reasoning below
            "reasoning": reasoning_text,
            "source_count": len(sources) if sources else 0,
            "elapsed_ms": elapsed_ms,
        }
        # The answer the user is reading is INCOMPLETE: the model cut it and no
        # continuation finished it (`finish_reason` is cleared by a clean continuation
        # and re-set by the fallback's own verdict). Derived from the reason, not from
        # `degraded`, which is first-wins and may carry another label for the ledger.
        truncated = bool(stream_signals.get("finish_reason"))
        # The Continue chip is the way out of a LENGTH cut only. A SAFETY / RECITATION
        # stop is marked and refunded like any cut, but re-asking the model to resume a
        # blocked passage is a dead end — such a turn gets no chips at all.
        continuable = truncated and is_length_cut(stream_signals.get("finish_reason"))

        # Persist the turn FIRST — BEFORE the best-effort follow-up-suggestions call below. That
        # call can park for minutes on a throttled Gemini (retry × 90s timeout); the user has
        # already read the streamed answer, so a disconnect in that window CANCELS this generator
        # (CancelledError is a BaseException — uncaught by the except-Exception guards). Writing the
        # durable turn up-front guarantees the answered exchange is never lost from history.
        try:
            # rich_content carries the widget + futuristic-chat fields (thinking / sources /
            # suggestions) in one JSONB column — no schema migration. Suggestions are added AFTER
            # this durable write (below), so they can never block or drop it.
            rich_content: dict = _rich_content_for_turn(
                thinking_payload, widgets, sources,
                truncated=truncated, finish_reason=stream_signals.get("finish_reason"),
            )
            if continuable:
                # Known before the write, so it rides the ATOMIC insert like the non-stream
                # door's — not the later best-effort update a disconnect can skip.
                rich_content["suggestions"] = [_CONTINUE_CHIP]

            # Persist the user + assistant rows TOGETHER in ONE insert so the turn is atomic: a
            # failing assistant write can never leave an orphaned user row for the client's
            # stream-failure reconcile to later duplicate. Explicit created_at preserves
            # user-before-assistant ordering (a single multi-row insert would otherwise stamp both
            # rows with the same now() default, and get_chat_history orders by created_at asc).
            now = datetime.now(timezone.utc)
            user_row: dict = {
                "session_id": session_id, "role": "user", "content": user_message,
                "created_at": now.isoformat(),
            }
            ai_msg: dict = {
                "session_id": session_id,
                "role": "assistant",
                "content": content,
                "citations": citations,
                "tokens_used": tokens_used,
                "rich_content": rich_content,
                "created_at": (now + timedelta(milliseconds=1)).isoformat(),
            }
            assistant_row = _persist_turn(supabase, session_id, user_row, ai_msg)

            # Durably persisted → delivered. The finally backstop must not refund past this
            # point (a disconnect during the best-effort steps below is not a failed turn).
            delivered = True
            # Zero Gemini cost (deep-dive cache HIT — replayed here, or via the fallback) →
            # refund the charge. `== 0` (not falsy) so a normal stream (tokens_used=None) is
            # never refunded. The starter-warm replay deliberately stays charged (see above).
            degraded_reason = stream_signals.get("degraded") or fallback_degraded
            if tokens_used == 0:
                quota.settle_no_cost("chat_cache_hit")
            elif degraded_reason:
                # Delivered, but materially less than promised: a synthesis that lost its
                # lenses (`no_specialists` / `unmerged` — the `routing` frame already named
                # them), a single-mode turn whose every tool failed (`no_tools`), or a
                # fallback answer that `generate_response` itself marked degraded.
                #
                # A HEALTHY stream→non-stream fallback is deliberately NOT here: it answers
                # from the SAME prompt and costs us MORE, not less — the user lost latency
                # and the thinking card, not the answer, and refunding it would hand back a
                # credit on a large share of turns every time the network is flaky. Only the
                # fallback's OWN degraded marker (none of its live data) settles no-cost.
                quota.settle_no_cost(f"chat_degraded_{degraded_reason}")
            # A charged turn earns this session one free follow-up (no-op after a refund).
            quota.on_delivered()
            # Settlement is final → fold it into the SAME local `rich_content` the
            # suggestions step writes back below, or that write would clobber the key.
            rich_content = _attach_turn_cost(
                supabase, assistant_row, quota, rich_content,
            ) or rich_content
        except Exception as e:
            logger.error("Chat stream persist failed: %s", e, exc_info=True)
            # ONLY the delivery-critical insert is in this try, so a failure here means the turn
            # was NOT durably recorded. Guard on `delivered` anyway so this can never hand back a
            # charge for an already-persisted turn.
            if not delivered:
                quota.refund_once("chat_stream_persist_failed")  # not recorded → hand it back
            yield _sse("error", {
                "error_code": "INTERNAL_ERROR",
                "user_message": "Your answer was generated but couldn't be saved. Please try again.",
            })
            return

        # OUTSIDE the delivery-critical try on purpose. This is best-effort bookkeeping that
        # runs after the answer is durably stored, so it must never be able to turn a saved
        # turn into an error frame — which is exactly what happened while it lived inside the
        # try above. Also off the event loop: see `_record_memory_facts_async`.
        await _record_memory_facts_async(user, stock_id, route)

        # Best-effort post-delivery writes (turn already persisted + charged): session metadata +
        # first-question auto-title + the on-screen snapshot. A failure here must NEVER refund or
        # error the stream — the user already has the answer (mirrors send_chat_message).
        try:
            current_count = sdata.get("message_count", 0)
            update_payload: dict = {
                "preview_message": content[:100],
            }
            existing_title = sdata.get("title")
            is_generic_title = (
                existing_title in ("New Chat", None)
                or (isinstance(existing_title, str) and existing_title.startswith("Chat about "))
            )
            first_question = user_message.strip()
            if current_count == 0 and is_generic_title and first_question:
                update_payload["title"] = first_question[:80]
            supabase.table("chat_sessions").update(update_payload).eq(
                "id", session_id
            ).execute()
            _persist_context_snapshot(supabase, session_id, req_ctx, sdata)
        except Exception as e:
            logger.warning(
                "Chat stream post-delivery metadata write failed for %s (%s: %s) — ignoring",
                session_id, type(e).__name__, e,
            )

        # Best-effort daily token accounting (streaming rarely reports usage → char estimate).
        # `is not None`, not `or`: 0 is a FACT (a replay cost nothing) and must not be
        # replaced by a character estimate; only an unknown (None) is estimated.
        _record_chat_tokens(
            user, x_guest_id,
            tokens_used if tokens_used is not None
            # A starter-warm replay is CHARGED (see the replay branch) but cost no Gemini:
            # record 0, not a character estimate of a call that never happened. Keyed on
            # the replay having been SERVED, not on the lookup having hit — a warm hit whose
            # turn then fell back to a live `generate_response` cost real tokens.
            else (0 if (replayed_warm and not used_fallback) else (len(content) // 4)),
        )

        # Follow-up suggestions — best-effort, AFTER the durable write. Being slow or cancelled here
        # can no longer drop the saved turn (worst case: no chips, which degrade gracefully).
        # Only when the warm answer was actually SERVED: a hit whose turn then fell back to
        # a live `generate_response` has a different answer, and chips stored for the
        # replay would be attached to it.
        warm_suggestions = [
            str(x).strip() for x in ((warmed or {}).get("suggestions") or []) if str(x).strip()
        ] if (replayed_warm and not used_fallback) else []
        try:
            if truncated:
                # A cut answer gets at most ONE chip — the way out — and no model call:
                # chips written off a half sentence proposed follow-ups to an answer the
                # user never got, and burnt a flash-lite call doing it. The chip is a
                # normal suggestion on the wire, so builds that predate `truncated`
                # render it too. Already persisted with the row (above); a SAFETY /
                # RECITATION cut gets none.
                suggestions = [_CONTINUE_CHIP] if continuable else None
            elif warm_suggestions:
                # The warm job generates and stores the chips with the answer; paying a
                # live suggestions call on a replay was the one Gemini call the warm path
                # could have saved for free and did not.
                suggestions = warm_suggestions[:2]
            else:
                # HEARTBEAT this call like the answer pump and the fallback arm. It is one
                # awaited Gemini call — `generate_json` under a 2-attempt retry with a 90 s
                # ceiling each, plus the overload/quota ladder's 5 s/10 s backoffs — and
                # nothing reached the client while it ran. An overloaded flash-lite (~60 s
                # hold, 503, back off, ~60 s hold) crossed iOS's 120 s idle timeout AFTER the
                # last `token` frame: the client dropped the socket, `ChatStreamError
                # .incomplete` removed the bubble the user was already reading, re-adopted
                # the saved turn from history — and the `credits` frame never arrived, so the
                # balance shown stayed one credit stale. Bounded by the SAME turn deadline
                # the fallback arm uses: chips are best-effort, and a turn that has spent
                # its budget gets a short grace, not another budget.
                _sugg_task = asyncio.ensure_future(chat_service.generate_followup_suggestions(
                    user_message=user_message,
                    answer=content,
                    context_type=ctx_type,
                    reference_id=ref_id,
                ))
                _sugg_deadline = max(
                    _time.monotonic() + 5.0,
                    min(started + _stream_budget_seconds(),
                        _time.monotonic() + settings.CHAT_SEND_BUDGET_SECONDS),
                )
                try:
                    while True:
                        _remaining = _sugg_deadline - _time.monotonic()
                        if _remaining <= 0:
                            _sugg_task.cancel()
                            logger.warning(
                                "Chat suggestions step exceeded the turn budget for "
                                "session %s — skipping chips (answer already persisted)",
                                session_id,
                            )
                            break
                        _done, _ = await asyncio.wait(
                            {_sugg_task}, timeout=min(_keepalive_seconds(), _remaining)
                        )
                        if _done:
                            break
                        yield ": keepalive\n\n"
                except BaseException:
                    # A client disconnect must not orphan the Gemini call.
                    _sugg_task.cancel()
                    raise
                # `.result()` ONLY on a task that finished cleanly. After the deadline's
                # `cancel()` the task is still PENDING (cancellation lands on the next loop
                # tick), so `cancelled()` is False and `.result()` raised InvalidStateError
                # into the except below — the right outcome (no chips) reached through a
                # misleading "suggestions step failed (InvalidStateError)" warning on every
                # budget overrun (W2 regress-A-1). `generate_followup_suggestions` never
                # raises, but the exception check keeps `.result()` from re-raising if it
                # ever does.
                suggestions = (
                    _sugg_task.result()
                    if _sugg_task.done() and not _sugg_task.cancelled()
                    and _sugg_task.exception() is None
                    else None
                )
            if suggestions:
                yield _sse("suggestions", {"questions": suggestions})
                # Reflect them in the terminal `done` message + persist so a reload shows the chips.
                rich_content["suggestions"] = suggestions
                assistant_row["rich_content"] = rich_content
                if not continuable:
                    # The Continue chip already rode the atomic insert; only model /
                    # warm chips need the follow-up write.
                    try:
                        supabase.table("chat_messages").update(
                            {"rich_content": rich_content}
                        ).eq("id", assistant_row["id"]).execute()
                    except Exception as e:
                        logger.warning(
                            "Chat suggestions persist failed (%s: %s) — chips shown live only",
                            type(e).__name__, e,
                        )
        except Exception as e:
            logger.warning("Chat suggestions step failed (%s: %s) — skipping", type(e).__name__, e)
            suggestions = None

        # What this turn cost, and the balance it left behind. Emitted on the DELIVERED
        # path only, once, immediately before `done`. Shipped iOS builds ignore an unknown
        # frame (`default: continue`), so this is additive in both directions.
        #
        # Always sent, even for a plain charge: the chip is the client's decision, but the
        # BALANCE is what stops its local copy going stale — chat is the one metered
        # surface that never refreshed it.
        yield _sse("credits", quota.cost_frame())
        yield _sse("done", {"message": _row_to_message(assistant_row).model_dump()})

    async def _metered_stream():
        # Wrap event_gen so a client disconnect (CancelledError/GeneratorExit) — which the
        # inner except-Exception guards miss — still refunds the turn exactly once. No-op if
        # an error site already settled, or if the turn was delivered.
        try:
            async for frame in event_gen():
                yield frame
        finally:
            if not delivered:
                quota.refund_once("chat_stream_cancelled")

    return StreamingResponse(
        _metered_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # defeat proxy buffering (Railway/nginx)
            "Connection": "keep-alive",
            # Defeat OUR OWN buffering. `GZipMiddleware` (main.py) compresses any response whose
            # request advertised gzip — iOS's URLSession does by default — and Starlette's
            # streaming gzip path never flushes the compressor between chunks, so every frame
            # (and every `: keepalive` comment) sat inside zlib until the generator closed:
            # measured on prod 2026-09-12, `meta` arrived at the END of a 14 s turn, with the
            # identical turn streaming from 0.56 s once gzip was declined. A response that
            # already carries a Content-Encoding is passed through untouched by the middleware.
            # Pinned by tests/test_chat_stream_endpoint.py::
            # test_the_stream_is_never_gzip_buffered_for_a_gzip_accepting_client.
            "Content-Encoding": "identity",
        },
    )


@router.get("/sessions/{session_id}", response_model=ChatHistoryResponse)
async def get_chat_history(
    session_id: str,
    user: dict = Depends(get_chat_identity),  # per-INSTALL guest partition (migration 111)
    supabase: Client = Depends(get_supabase),
):
    """Get chat session with full message history."""
    # postgrest-py RAISES APIError when `.single()` matches zero rows (PGRST116/406), so an
    # unwrapped call never reached the `if not session.data` check below — it escaped to the
    # global handler as a bare 500 "internal server error", making that check dead code.
    # A missing session, another user's session, or a guest session viewed after sign-in all
    # took that path. It matters beyond tidiness: iOS uses GET /chat/sessions/{id} as the
    # persistence oracle in `reconcileAfterStreamFailure`, and a 500 there is indistinguishable
    # from "server broken", so it retries generation — re-charging a credit and duplicating a
    # turn that was already saved. The two message routes already wrap the identical call.
    try:
        session = (
            supabase.table("chat_sessions")
            .select("*")
            .eq("id", session_id)
            .eq("user_id", user["id"])
            .single()
            .execute()
        )
    except Exception as e:
        raise _session_lookup_failed(e)

    if not session.data:
        raise HTTPException(status_code=404, detail="Chat session not found")

    # The NEWEST rows, explicitly bounded. An unbounded ascending select was clamped by
    # PostgREST to its first ~1,000 rows, so a session past that returned its OLDEST turns
    # forever — and iOS's persistence oracle (`historyContainsTurn` looks at the tail) judged a
    # saved-and-charged turn absent and re-POSTed it. Read desc, reverse in Python.
    try:
        messages = (
            supabase.table("chat_messages")
            .select("*")
            .eq("session_id", session_id)
            .order("created_at", desc=True)
            .limit(CHAT_HISTORY_PAGE_ROWS)
            .execute()
        )
    except Exception as e:
        if is_transient_supabase_error(e):
            raise HTTPException(
                status_code=409,
                detail=make_error_body(
                    ErrorCode.SYSTEM_BUSY,
                    message="chat_messages read unavailable (transient)",
                    user_message="Cay AI can't reach this conversation right now. Please try again in a moment.",
                    details={"step": "chat_history_read"},
                ),
            )
        raise
    rows = list(reversed(messages.data or []))

    # Pair each assistant row with the question it answered, so the replay strip is
    # INTENT-AWARE: a stored "should I buy AAPL?" answer keeps its disclaimer while a
    # stored "Hi" loses one. Rows arrive `created_at asc` and the insert stamps the user
    # row 1 ms ahead of the assistant row (see both persist sites), so "nearest preceding
    # user row" is exact rather than a guess.
    replayed: List[ChatMessageResponse] = []
    last_question = ""
    for row in rows:
        if row.get("role") == "user":
            last_question = row.get("content") or ""
            replayed.append(_row_to_message(row))
            continue
        stored = row.get("content") or ""
        # `scan_answer` is re-run because the tag was never persisted; a handful of
        # compiled regexes is what keeps replay symmetric with the live decision.
        keep = is_trade_intent(last_question) or ("advice_directive" in scan_answer(stored))
        replayed.append(_row_to_message(row, strip_disclaimer=not keep))

    return ChatHistoryResponse(
        session=_row_to_session(session.data),
        messages=replayed,
    )


@router.patch("/sessions/{session_id}", response_model=ChatSessionResponse)
async def update_chat_session(
    session_id: str,
    request: UpdateChatSessionRequest,
    user: dict = Depends(get_chat_identity),  # per-INSTALL guest partition (migration 111)
    supabase: Client = Depends(get_supabase),
):
    """Update a chat session (title, is_saved)."""
    # Verify ownership
    # postgrest-py RAISES APIError when `.single()` matches zero rows (PGRST116/406), so an
    # unwrapped call never reached the `if not session.data` check below — it escaped to the
    # global handler as a bare 500 "internal server error", making that check dead code.
    # A missing session, another user's session, or a guest session viewed after sign-in all
    # took that path. It matters beyond tidiness: iOS uses GET /chat/sessions/{id} as the
    # persistence oracle in `reconcileAfterStreamFailure`, and a 500 there is indistinguishable
    # from "server broken", so it retries generation — re-charging a credit and duplicating a
    # turn that was already saved. The two message routes already wrap the identical call.
    try:
        session = (
            supabase.table("chat_sessions")
            .select("id")
            .eq("id", session_id)
            .eq("user_id", user["id"])
            .single()
            .execute()
        )
    except Exception as e:
        raise _session_lookup_failed(e)
    if not session.data:
        raise HTTPException(status_code=404, detail="Chat session not found")

    update_data = {}
    if request.title is not None:
        update_data["title"] = request.title
    if request.is_saved is not None:
        update_data["is_saved"] = request.is_saved

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = (
        supabase.table("chat_sessions")
        .update(update_data)
        .eq("id", session_id)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to update session")

    return _row_to_session(result.data[0])


@router.delete("/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    user: dict = Depends(get_chat_identity),  # per-INSTALL guest partition (migration 111)
    supabase: Client = Depends(get_supabase),
):
    """Delete a chat session and all its messages."""
    # Verify ownership
    # postgrest-py RAISES APIError when `.single()` matches zero rows (PGRST116/406), so an
    # unwrapped call never reached the `if not session.data` check below — it escaped to the
    # global handler as a bare 500 "internal server error", making that check dead code.
    # A missing session, another user's session, or a guest session viewed after sign-in all
    # took that path. It matters beyond tidiness: iOS uses GET /chat/sessions/{id} as the
    # persistence oracle in `reconcileAfterStreamFailure`, and a 500 there is indistinguishable
    # from "server broken", so it retries generation — re-charging a credit and duplicating a
    # turn that was already saved. The two message routes already wrap the identical call.
    try:
        session = (
            supabase.table("chat_sessions")
            .select("id")
            .eq("id", session_id)
            .eq("user_id", user["id"])
            .single()
            .execute()
        )
    except Exception as e:
        raise _session_lookup_failed(e)
    if not session.data:
        raise HTTPException(status_code=404, detail="Chat session not found")

    # Delete messages first (child records)
    supabase.table("chat_messages").delete().eq(
        "session_id", session_id
    ).execute()

    # Delete session
    supabase.table("chat_sessions").delete().eq("id", session_id).execute()

    return {"status": "deleted", "session_id": session_id}
