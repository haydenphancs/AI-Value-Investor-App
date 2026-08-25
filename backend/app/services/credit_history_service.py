"""Read side of `credit_transactions` — the user-facing credit statement.

This is the FIRST reader of that table. `credit_service.py` only ever writes to it (via
the `spend_credits` / `refund_credits` / `add_purchased_credits` RPCs), and until now
nothing read it back, so a user could watch their balance drop with no way to reconcile
it. Chat is the sharp edge: SYSTEM_DESIGN_GUIDELINES §9b.8 deliberately renders no price
on a normally-charged turn ("putting a price on every answer turns chat into a meter"),
which is the right call and also exactly why a separate, opt-in statement has to exist.

STRICTLY READ-ONLY. Nothing here charges, refunds, or touches a balance.

NO CACHE LAYER, deliberately — same reasoning as `notification_inbox_service`: the
two-tier cache-aside pattern (CLAUDE.md #4) is for expensive UPSTREAM calls, and this is
one indexed Supabase read of a user's own rows. Caching per-user mutable state keyed by
user id would be all of that pattern's complexity for none of its benefit, and would
serve a stale statement right after a spend.

⚠️ The reason→label table below is the whole point of this module, and it is a
COMPLETENESS problem, not a correctness one: a `reason` with no entry renders as a row
the user cannot interpret. `tests/test_credit_history_reason_coverage.py` scans the repo
for reason literals and fails the build when one is not covered here.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.schemas.credit_history import (
    CreditHistoryResponse,
    CreditTransactionResponse,
)
from app.schemas.notifications import _iso

logger = logging.getLogger(__name__)

TABLE = "credit_transactions"
PURCHASES_TABLE = "credit_purchases"
PACKS_TABLE = "credit_packs"

# Hard ceiling on a page. Bounds both the query and the JSON handed to iOS.
MAX_PAGE = 100
DEFAULT_PAGE = 30

# ── kinds ────────────────────────────────────────────────────────────────────
# The vocabulary iOS switches on for the row's glyph and colour. Additive only:
# removing one silently changes an already-shipped build's rendering.
KIND_SPEND = "spend"
KIND_REFUND = "refund"
KIND_GRANT = "grant"
KIND_PURCHASE = "purchase"
KIND_REVOKE = "revoke"
KIND_OTHER = "other"

# ── how to read `ref_id` for a given reason ──────────────────────────────────
# `ref_id` has FOUR incompatible shapes depending on who wrote the row, and reading one
# with the wrong parser is how a month stamp or an Apple transaction id ends up rendered
# at a user as if it were a ticker.
_REF_NONE = "none"      # an ET month stamp ("2026-08") or nothing meaningful
_REF_REPORT = "report"  # "ORCL"  or  "ORCL:warren_buffett"
_REF_CHAT = "chat"      # "report_chat:ORCL:{uuid}"  or  "{session_uuid}:{uuid}"
_REF_PACK = "pack"      # an Apple StoreKit transaction id

_REPORT_CHAT_PREFIX = "report_chat:"

#: reason → (kind, title, how to read ref_id)
#:
#: Sources, so this stays auditable:
#:   report_charge            endpoints/research.py, endpoints/ticker_report.py
#:   chat_charge              endpoints/chat.py, endpoints/ticker_report.py
#:   report_refund*           endpoints/research.py, endpoints/ticker_report.py,
#:                            services/research_reconciliation_service.py
#:   chat_*                   endpoints/chat.py, endpoints/ticker_report.py
#:   grant / monthly_reset    SQL: create_user_credits(), ensure_credit_period()
#:   tier_upgrade/_revoked    SQL: grant_tier_upgrade(), revoke_tier_credits()
#:   pack_purchase/_revoked   SQL: add_purchased_credits(), revoke_purchased_credits()
#:   tester_grant             scripts/seed_testflight_testers.py  ← outside app/, easy to miss
_REASONS: Dict[str, Tuple[str, str, str]] = {
    # spends
    "report_charge": (KIND_SPEND, "Deep research report", _REF_REPORT),
    "chat_charge": (KIND_SPEND, "Ask Cay AI", _REF_CHAT),
    # report refunds
    "report_refund": (KIND_REFUND, "Refund · research report", _REF_REPORT),
    "report_refund_deleted": (KIND_REFUND, "Refund · report deleted", _REF_REPORT),
    "report_refund_reconciled": (KIND_REFUND, "Refund · report didn't finish", _REF_REPORT),
    # chat refunds — each names the actual reason, because "why did I get a credit back"
    # is the question this screen exists to answer
    "chat_refund": (KIND_REFUND, "Refund · Ask Cay AI", _REF_CHAT),
    "chat_cache_hit": (KIND_REFUND, "Refund · answer was already cached", _REF_CHAT),
    "chat_undelivered": (KIND_REFUND, "Refund · answer wasn't delivered", _REF_CHAT),
    "chat_stream_fallback_failed": (KIND_REFUND, "Refund · Ask Cay AI", _REF_CHAT),
    "chat_stream_empty": (KIND_REFUND, "Refund · no answer was returned", _REF_CHAT),
    "chat_stream_persist_failed": (KIND_REFUND, "Refund · answer wasn't saved", _REF_CHAT),
    "chat_stream_cancelled": (KIND_REFUND, "Refund · you stopped the answer", _REF_CHAT),
    # grants
    "grant": (KIND_GRANT, "Welcome credits", _REF_NONE),
    "monthly_reset": (KIND_GRANT, "Monthly credits", _REF_NONE),
    "tier_upgrade": (KIND_GRANT, "Plan credits", _REF_NONE),
    "tester_grant": (KIND_GRANT, "TestFlight credits", _REF_NONE),
    # removals
    "tier_revoked": (KIND_REVOKE, "Plan credits removed", _REF_NONE),
    # purchases
    "pack_purchase": (KIND_PURCHASE, "Credit pack", _REF_PACK),
    "pack_revoked": (KIND_REVOKE, "Credit pack refunded", _REF_PACK),
}

#: PREFIX families — matched only after an exact miss.
#:
#: ⚠️ `chat_degraded_*` is COMPOSED AT RUNTIME: `endpoints/chat.py` builds
#: `f"chat_degraded_{stream_signals['degraded']}"` from a suffix that `chat_service.py`
#: chooses. Two suffixes exist today (`no_specialists`, `unmerged`) and a third needs no
#: endpoint change at all — so a dict-only lookup here is a future blank row, not a
#: hypothetical one.
_REASON_PREFIXES: Tuple[Tuple[str, Tuple[str, str, str]], ...] = (
    ("chat_degraded_", (KIND_REFUND, "Refund · answer was incomplete", _REF_CHAT)),
)

_FALLBACK: Tuple[str, str, str] = (KIND_OTHER, "Credit adjustment", _REF_NONE)

#: Exported for the coverage guard.
KNOWN_REASONS = frozenset(_REASONS)
KNOWN_REASON_PREFIXES = tuple(prefix for prefix, _ in _REASON_PREFIXES)


class CreditHistoryUnavailable(Exception):
    """The statement could not be read.

    Raised rather than returning an empty page, for the same reason
    `NotificationInboxUnavailable` exists: an empty statement and a broken statement look
    identical to a user, and rendering "No credit activity yet" over a database error is
    a failure nobody reports because it looks like the intended empty state. Worse here
    than in the inbox — this screen is the one a user opens when they already believe
    their credits are wrong.
    """


# ── pure helpers ─────────────────────────────────────────────────────────────
# Module level and Supabase-free on purpose: the label mapping is the part most likely
# to be wrong, and it must be testable without a database or a service instance.

# Deliberately permissive on shape (BRK.B, RDS-A, BTCUSD all have to pass) and bounded
# on length, which is what actually separates a symbol from the other three ref_id
# shapes. The two explicit rejections below cover what the length bound does not.
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _looks_like_ticker(value: Any) -> bool:
    """Is this plausibly a market symbol, rather than some other `ref_id` shape?

    Guards, each earning its place:
      * `_MONTH_RE` — the ET month stamp "2026-08" is all digits and a hyphen, so it
        satisfies the symbol pattern. Grant rows use `_REF_NONE` and never reach here,
        but a future reason wired to the wrong parser would print a date as a ticker.
      * all-digits — an Apple StoreKit transaction id. Long ones fail the length bound
        anyway; short ones would not.
    """
    if not isinstance(value, str):
        return False
    candidate = value.strip().upper()
    if not candidate or not _TICKER_RE.match(candidate):
        return False
    if _MONTH_RE.match(candidate):
        return False
    if candidate.isdigit():
        return False
    return True


def _ticker_from_report_ref(ref_id: Any) -> Optional[str]:
    """`"ORCL"` → ORCL · `"ORCL:warren_buffett"` → ORCL · anything odd → None.

    The persona half is deliberately dropped: rendering it needs a key→display-name map,
    which is one more table to drift out of sync for something the reader did not ask.
    """
    if not isinstance(ref_id, str) or not ref_id:
        return None
    head = ref_id.split(":", 1)[0].strip()
    return head.upper() if _looks_like_ticker(head) else None


def _ticker_from_chat_ref(ref_id: Any) -> Optional[str]:
    """`"report_chat:ORCL:{uuid}"` → ORCL. Plain chat → None.

    A plain chat turn's ref is `{session_uuid}:{uuid4hex}` — the session id is there so a
    charge stays greppable back to its conversation, not to name anything. It is not a
    ticker and must never be shown as one.
    """
    if not isinstance(ref_id, str) or not ref_id.startswith(_REPORT_CHAT_PREFIX):
        return None
    head = ref_id[len(_REPORT_CHAT_PREFIX):].split(":", 1)[0].strip()
    return head.upper() if _looks_like_ticker(head) else None


def describe_reason(reason: Any) -> Tuple[str, str, str]:
    """reason → (kind, title, ref_style). Exact match, then prefix, then fallback."""
    key = reason.strip() if isinstance(reason, str) else ""
    if key in _REASONS:
        return _REASONS[key]
    for prefix, described in _REASON_PREFIXES:
        if key.startswith(prefix):
            return described
    if key:
        # Not an error — the row is rendered as a generic adjustment and the user still
        # sees the amount. It IS a signal that the coverage guard has drifted.
        logger.info("credit history: unmapped ledger reason %r — rendered generically", key)
    return _FALLBACK


def _pool_note(granted_delta: Any, purchased_delta: Any, kind: str) -> Optional[str]:
    """The granted/purchased split, but only when it tells the user something.

    Silent when nothing purchased moved — which also, for free, handles the pre-migration
    -118 rows that carry a 0/0 split beside a non-zero `delta`. Those splits are unknown,
    not zero, and stating "20 monthly" over one would be an invention.
    """
    try:
        granted = abs(int(granted_delta or 0))
        purchased = abs(int(purchased_delta or 0))
    except (TypeError, ValueError):
        return None
    if purchased == 0:
        return None
    if kind == KIND_PURCHASE:
        # Guideline 3.1.1 is the reason the second pool exists; say so where it lands.
        return "Never expires"
    if granted == 0:
        return f"{purchased} purchased"
    return f"{granted} monthly + {purchased} purchased"


def describe_transaction(
    row: Dict[str, Any],
    *,
    is_reversed: bool = False,
    pack_names: Optional[Dict[str, str]] = None,
) -> CreditTransactionResponse:
    """One ledger row → one DTO. Raises `ValueError` on a row with no usable id."""
    raw_id = row.get("id")
    if raw_id is None or not str(raw_id).strip():
        raise ValueError("credit_transactions row has no id")

    reason = row.get("reason")
    kind, title, ref_style = describe_reason(reason)
    ref_id = row.get("ref_id")

    subtitle: Optional[str] = None
    if ref_style == _REF_REPORT:
        subtitle = _ticker_from_report_ref(ref_id)
    elif ref_style == _REF_CHAT:
        subtitle = _ticker_from_chat_ref(ref_id)
    elif ref_style == _REF_PACK and pack_names:
        subtitle = pack_names.get(str(ref_id or "")) or None

    try:
        delta = int(row.get("delta") or 0)
    except (TypeError, ValueError):
        delta = 0

    return CreditTransactionResponse(
        id=str(raw_id).strip(),
        created_at=_iso(row.get("created_at")),
        delta=delta,
        kind=kind,
        title=title,
        subtitle=subtitle,
        pool_note=_pool_note(row.get("granted_delta"), row.get("purchased_delta"), kind),
        is_reversed=is_reversed,
        reason=(reason.strip() if isinstance(reason, str) else ""),
    )


class CreditHistoryService:
    def __init__(self) -> None:
        self.supabase = get_supabase()

    # ── enrichment (best-effort; never fails the page) ───────────────────

    def _reversed_ids(self, user_id: str, rows: List[Dict[str, Any]]) -> set:
        """Which of these debits already have a refund row pointing at them.

        Without this a cache-hit chat turn reads as an unexplained −1 next to a +1, and
        the whole screen looks like noise rather than a statement. Served by the existing
        partial `idx_credit_transactions_reverses`.

        Best-effort: on failure every row simply renders un-reversed, which is the
        pre-enrichment view rather than a wrong one.
        """
        debit_ids = [str(r.get("id")) for r in rows if _is_debit(r) and r.get("id") is not None]
        if not debit_ids:
            return set()
        try:
            found = (
                self.supabase.table(TABLE)
                .select("reverses_id")
                # Scoped to the caller IN ADDITION to the id filter. Ids are globally
                # unique so a cross-user match is not reachable today, but filtering on
                # ids alone is the IDOR shape this codebase refuses everywhere else
                # (notification_inbox_service.mark_read), and the service-role key means
                # RLS will not catch a regression here.
                .eq("user_id", user_id)
                .in_("reverses_id", debit_ids)
                .execute()
                .data
                or []
            )
            return {str(r.get("reverses_id")) for r in found if r.get("reverses_id") is not None}
        except Exception as e:
            logger.warning(
                "credit history: reversal probe failed (%s: %s) — rows render un-reversed",
                type(e).__name__, e,
            )
            return set()

    def _pack_names(self, user_id: str, rows: List[Dict[str, Any]]) -> Dict[str, str]:
        """Apple transaction id → pack display name, for `pack_*` rows only.

        A deliberate, narrow exception to "don't resolve against source tables": the
        alternative is rendering `2000000812345678` at a user. `credit_purchases` is the
        IAP idempotency ledger — its rows are never deleted — so unlike a report or a chat
        session this lookup does not need a "row is gone" story beyond the empty fallback.

        Two small queries, and only when the page actually contains a pack row.
        Best-effort throughout: a failure leaves the row as a bare "Credit pack".
        """
        txn_ids = [
            str(r.get("ref_id"))
            for r in rows
            if str(r.get("reason") or "").startswith("pack_") and r.get("ref_id")
        ]
        if not txn_ids:
            return {}
        try:
            # `credit_purchases` is unique on (environment, transaction_id), so filtering
            # by transaction_id alone can in principle match a sandbox row and a
            # production row. Either names the same pack, so last-wins is fine here.
            purchases = (
                self.supabase.table(PURCHASES_TABLE)
                .select("transaction_id, product_id")
                .eq("user_id", user_id)          # same scoping rule as above
                .in_("transaction_id", txn_ids)
                .execute()
                .data
                or []
            )
            if not purchases:
                return {}
            packs = (
                self.supabase.table(PACKS_TABLE)
                .select("product_id, display_name")
                .execute()
                .data
                or []
            )
            by_product = {
                str(p.get("product_id")): str(p.get("display_name") or "").strip()
                for p in packs
                if p.get("product_id")
            }
            names: Dict[str, str] = {}
            for purchase in purchases:
                txn = str(purchase.get("transaction_id") or "")
                label = by_product.get(str(purchase.get("product_id") or ""), "")
                if txn and label:
                    names[txn] = label
            return names
        except Exception as e:
            logger.warning(
                "credit history: pack name lookup failed (%s: %s) — packs render unnamed",
                type(e).__name__, e,
            )
            return {}

    # ── read ─────────────────────────────────────────────────────────────

    def list_for_user(
        self,
        user_id: str,
        *,
        limit: int = DEFAULT_PAGE,
        before: Optional[str] = None,
    ) -> CreditHistoryResponse:
        """Newest-first page of a user's credit movements.

        KEYSET pagination on `id`, not offset and not `created_at`. Offset would repeat or
        skip rows as new movements land at the head; `created_at` defaults to `now()`,
        which is TRANSACTION time, so `ensure_credit_period` firing inside a
        `spend_credits` call writes two rows sharing a timestamp and a timestamp cursor
        would drop one at a page boundary. `id` is a monotonic bigserial and unique.
        """
        size = max(1, min(_coerce_int(limit, DEFAULT_PAGE), MAX_PAGE))
        try:
            query = (
                self.supabase.table(TABLE)
                .select("id, delta, reason, ref_id, created_at, granted_delta, purchased_delta")
                # ⚠️ Scoped to the caller in code. The backend holds the service-role key,
                # so RLS is defence in depth and THIS filter is the wall
                # (SYSTEM_DESIGN_GUIDELINES §9).
                .eq("user_id", user_id)
            )
            # Applied BEFORE .limit() purely so the code reads in the order Postgres
            # evaluates it. PostgREST serializes filters and the limit into one request,
            # so builder-call order does not actually matter — but writing it the other
            # way invites a reader (or a test double) to believe it truncates first.
            if before:
                cursor = _coerce_int(before, None)
                if cursor is not None:
                    query = query.lt("id", cursor)
                else:
                    logger.warning(
                        "credit history: ignoring non-numeric cursor %r for user=%s",
                        before, user_id,
                    )
            query = query.order("id", desc=True).limit(size + 1)  # +1 probes for a next page
            rows = query.execute().data or []
        except Exception as e:
            logger.error(
                "credit history: list failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e, exc_info=True,
            )
            raise CreditHistoryUnavailable(str(e)) from e

        has_more = len(rows) > size
        rows = rows[:size]

        reversed_ids = self._reversed_ids(user_id, rows)
        pack_names = self._pack_names(user_id, rows)

        items: List[CreditTransactionResponse] = []
        for row in rows:
            try:
                items.append(
                    describe_transaction(
                        row,
                        is_reversed=str(row.get("id")) in reversed_ids,
                        pack_names=pack_names,
                    )
                )
            except Exception as e:
                # Skip rather than raise: one malformed row must not blank the statement.
                logger.warning(
                    "credit history: skipping unusable row id=%r (%s: %s)",
                    row.get("id"), type(e).__name__, e,
                )

        return CreditHistoryResponse(
            items=items,
            # Derived from the raw row, not from `items` — a skipped malformed row would
            # otherwise stall the cursor and make the client re-request the same page.
            next_cursor=(str(rows[-1].get("id")) if has_more and rows else None),
        )


def _is_debit(row: Dict[str, Any]) -> bool:
    try:
        return int(row.get("delta") or 0) < 0
    except (TypeError, ValueError):
        return False


def _coerce_int(value: Any, default: Optional[int]) -> Optional[int]:
    """Best-effort int, without letting a junk query param 500 the endpoint."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


_service: Optional[CreditHistoryService] = None


def get_credit_history_service() -> CreditHistoryService:
    global _service
    if _service is None:
        _service = CreditHistoryService()
    return _service
