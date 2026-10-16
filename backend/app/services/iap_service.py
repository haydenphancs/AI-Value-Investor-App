"""
In-app purchase entitlement.

Turns an Apple-verified transaction into an entitlement:

    verified transaction
      -> product id -> tier
      -> upsert `subscriptions` (idempotent on original_transaction_id)
      -> mirror the WINNING tier onto `users.tier`
      -> ensure_credit_period(), which grants each Apple PAID PERIOD's allocation exactly
         once, read from that `subscriptions` row (migration 196)

Migration 100 built the data layer for exactly this and left the wiring to "a later
ENFORCEMENT phase": *"A server-side receipt validator / webhook updates it and mirrors the
winning tier onto users.tier."* This is that.

Two invariants worth stating up front, because both are easy to lose:

1. **Idempotency.** The same transaction arrives more than once by design — StoreKit's
   `Transaction.updates` replays on launch, restore-purchases re-submits, and Apple retries
   webhooks. Granting credits per delivery instead of per period would mint free credits on
   every app launch.

2. **The winning tier.** A user can hold several subscription rows (upgrade, re-subscribe,
   a lapsed one). `users.tier` must reflect the best CURRENTLY-ACTIVE entitlement, never
   just the most recently written row — otherwise a stale expired row can demote a paying
   customer.
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from app.config import settings
from app.database import get_supabase
from app.integrations.app_store import notification_subscription_status

logger = logging.getLogger(__name__)


class IAPError(Exception):
    """Entitlement could not be applied."""


class UnknownProduct(IAPError):
    """A verified transaction for a product we don't recognise.

    Verified but unmapped is a genuine anomaly — someone bought something real that we
    can't price. Loud, and never silently treated as a free tier.
    """


class PurchaseBoundToAnotherAccount(IAPError):
    """The transaction is already owned by a different user, and ownership never moves.

    TERMINAL, not retryable — and that distinction is the whole reason this class exists.
    Sharing the generic `IAPError` meant `billing.py` answered it with `SYSTEM_BUSY` / 503 and
    "Reopen the app shortly and it will be applied." StoreKit reads a 5xx as "try again", so
    the client never calls `Transaction.finish()`, and `Transaction.updates` re-delivers the
    same transaction on every single launch — forever, against a condition that by definition
    can never clear. The user is told to wait for something that will not happen.

    Answering 409 instead lets the client finish the transaction and show something true.
    """


class PurchaseRevoked(IAPError):
    """Apple has already refunded or cancelled this purchase. TERMINAL, and finishable.

    A sibling of `UnknownProduct`, deliberately NOT the same class, because the two demand
    opposite client behaviour and conflating them cost users a working app:

    `UnknownProduct` means we cannot MAP the product — a missing `credit_packs` row, an
    unmapped `IAP_PRODUCT_*`. That is our configuration bug against a purchase the user really
    paid for, so the transaction must stay UNFINISHED: once the row or mapping is fixed, the
    next redelivery grants it. Finishing it would destroy a paid purchase.

    This one means Apple took the money back. It can never become grantable, so leaving it
    unfinished re-delivers it on every launch forever, and each attempt surfaces an error to a
    user who did nothing wrong. It carries `ErrorCode.PURCHASE_REVOKED` so the client can
    finish it and stop.

    ⚠️ `billing.py` must catch this arm BEFORE the generic `IAPError`.
    """


class PurchaseAccountMismatch(PurchaseBoundToAnotherAccount):
    """The transaction's `appAccountToken` names a different account, and NOTHING was recorded.

    A subclass because it is still "this isn't your purchase" — but the money consequence is the
    opposite of its parent's, and conflating them destroys purchases.

    `PurchaseBoundToAnotherAccount` means we ALREADY HAVE this transaction against another
    account: somebody was credited, the condition can never clear, and the client SHOULD finish
    it so Apple stops redelivering.

    This one is raised BEFORE any grant, when the token proves the buyer is a different account
    from the one submitting. No `credit_purchases` row exists and NOBODY has been credited — so
    finishing it deletes a purchase the user paid for, with no redelivery left to repair it. The
    honest handling is to keep it unfinished until the buying account signs in, at which point
    the very same redelivery grants it.

    Carries its own `ErrorCode.PURCHASE_ACCOUNT_MISMATCH` so the client can tell the two apart;
    `billing.py` must catch this arm BEFORE the parent.
    """


# Tier ordering, worst → best. Used to pick the winning entitlement, so a user holding both
# Pro and Max gets Max. Kept explicit rather than relying on the DB enum's declaration order.
_TIER_RANK: Dict[str, int] = {"free": 0, "pro": 1, "premium": 2}

# Statuses that count as entitling. Apple keeps a subscription "active" through the billing
# retry grace period, so a failed renewal does not instantly strip access.
_ENTITLING_STATUSES = {"active", "grace_period", "billing_retry"}
# Entitling statuses that mean "the period HAS ended, Apple is still retrying billing". The
# expiry sweep gives them a long window instead of 24 h (`_sweep_windows`), and a same-period
# delivery that only DERIVES "expired" is held over them (`_billing_grace_hold_reason`).
_LONG_WINDOW_STATUSES = frozenset({"grace_period", "billing_retry"})

# PostgREST / Postgres codes for "that column does not exist" — how a SELECT of
# `users.comp_tier` fails before migration 177 is applied.
_MISSING_COLUMN_CODES = {"42703", "PGRST204"}


def product_tier_map() -> Dict[str, str]:
    """StoreKit product id → tier. Read from settings each call so a config change
    (or a test override) takes effect without a restart."""
    return {
        settings.IAP_PRODUCT_PRO_MONTHLY: "pro",
        settings.IAP_PRODUCT_MAX_MONTHLY: "premium",
    }


def _assert_app_account_token_matches(
    payload: Dict[str, Any],
    user_id: str,
    *,
    kind: str,
    transaction_id: Optional[str] = None,
    product_id: Optional[str] = None,
) -> Optional[str]:
    """Refuse a CLIENT-submitted transaction whose `appAccountToken` names another account.

    Returns the token when present (callers persist it alongside the purchase), else None.

    This is the FIRST-delivery guard, and it is the only one that exists. The second-delivery
    case is caught by a stored owner (consumables: the `credit_purchases` unique key;
    subscriptions: the `prior["user_id"] != user_id` rebind check) — but on a first delivery
    there is no stored owner to compare against, so without the token nothing distinguishes
    "B bought this" from "A bought this and B happens to be signed in now".

    That gap is reachable: if A's purchase completes at Apple but the verify never succeeds
    (offline, killed app, signed out), the transaction stays unfinished; when B signs in on
    the same device `AppState.onAuthenticated` → `drainUnfinishedTransactions()` re-submits it
    under B's bearer token. Apple returns the token INSIDE the signed payload, so the client
    cannot forge it independently of the transaction.

    ⚠️ Client-submitted paths only. An App Store Server Notification is not client-submitted
    and its `user_id` is resolved from our OWN subscriptions row rather than from a bearer
    token, so there is no cross-account question to answer — and raising there would return a
    non-200 that Apple retries for days.
    """
    token = payload.get("appAccountToken")
    if token:
        if str(token).lower() != str(user_id).lower():
            logger.error(
                "IAP: %s txn=%s carries appAccountToken=%s but was submitted by user=%s — "
                "refusing to credit a different account",
                kind, transaction_id, token, user_id,
            )
            # NOT `PurchaseBoundToAnotherAccount`: we are refusing BEFORE any grant, so no
            # row exists and nobody has been credited. The client must keep the transaction
            # unfinished — see `PurchaseAccountMismatch`.
            raise PurchaseAccountMismatch(
                "this purchase belongs to a different account"
            )
    else:
        # Older client, or a purchase made before the token was wired in. Not fatal — the
        # per-transaction dedup still prevents double-granting — but it means this particular
        # delivery cannot be proven to belong to the submitter.
        logger.warning(
            "IAP: %s txn=%s has no appAccountToken (product=%s user=%s) — proceeding without "
            "the cross-account proof",
            kind, transaction_id, product_id, user_id,
        )
    return str(token) if token else None


def tier_for_product(product_id: Optional[str]) -> str:
    """Resolve a product id to a tier, or raise.

    DELIBERATELY UNCHANGED by the credit-pack work. It answers exactly one question — "which
    SUBSCRIPTION tier is this?" — and every caller depends on it raising for anything else.
    Consumables are routed away from it by `product_kind` BEFORE it is reached; teaching this
    function about packs would make an unmapped product resolvable and lose the honest 400.
    """
    if not product_id:
        raise UnknownProduct("transaction has no productId")
    tier = product_tier_map().get(product_id)
    if tier is None:
        raise UnknownProduct(
            f"productId {product_id!r} is not mapped to a tier — check "
            "IAP_PRODUCT_* settings against App Store Connect"
        )
    return tier


def is_credit_pack_product(product_id: Optional[str]) -> bool:
    """Is this product id a consumable credit pack?

    Decided by NAMING PREFIX, not by a DB lookup, so the routing question is answerable
    without I/O and — more importantly — so a pack that has been retired from `credit_packs`
    is still recognised AS a pack. Otherwise a retired product would fall through to
    `tier_for_product` and be reported as an unmapped subscription, which is both the wrong
    diagnosis and the wrong HTTP status for a purchase someone actually made.
    """
    prefix = settings.IAP_CREDIT_PACK_PREFIX
    return bool(product_id) and bool(prefix) and str(product_id).startswith(prefix)


def product_kind(product_id: Optional[str]) -> str:
    """"credit_pack" | "subscription", or raise `UnknownProduct`.

    The single routing decision for a verified transaction. Packs are checked first because
    the prefix test is exact and cheap; anything else must be a mapped subscription or it is
    a genuine anomaly — a real purchase we cannot price.
    """
    if is_credit_pack_product(product_id):
        return "credit_pack"
    if product_id and product_id in product_tier_map():
        return "subscription"
    if not product_id:
        raise UnknownProduct("transaction has no productId")
    raise UnknownProduct(
        f"productId {product_id!r} is neither a mapped subscription nor a credit pack "
        f"(prefix {settings.IAP_CREDIT_PACK_PREFIX!r}) — check IAP_PRODUCT_* settings and "
        "the credit_packs table against App Store Connect"
    )


def _ms_to_dt(value: Any) -> Optional[datetime]:
    """Apple sends timestamps as milliseconds since epoch."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse a `timestamptz` string as Supabase returns it, tolerating junk.

    Always returns an AWARE datetime (or None) — comparing a naive one against the aware
    `expiresDate` derived from Apple's epoch-milliseconds raises TypeError, which on this
    path would 500 a webhook rather than degrade.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        # Postgres emits "+00:00"; some clients emit "Z". fromisoformat rejects "Z" < 3.11.
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _status_for_notification(notification_type: str, subtype: str) -> Optional[str]:
    """Subscription status implied by an App Store Server Notification, or None to derive it.

    The notification carries state the transaction alone cannot express. The costly case is
    `DID_FAIL_TO_RENEW` with subtype `GRACE_PERIOD`: the attached transaction's `expiresDate`
    has already passed, so `status_for_transaction` reads it as "expired" and the customer is
    demoted to free — while Apple explicitly keeps them entitled and is still retrying billing.
    A paying customer loses access for a failed card retry that Apple expects to succeed.

    `subtype` was already parsed and then never used. Returning None means "no better
    information than the payload", which is the correct answer for the client-verify path.

    `GRACE_PERIOD_EXPIRED` is EXPLICIT since 2026-10-14: Apple says the grace period ended
    without a renewal, so access ends. Derived, it read "expired" too (the end is past), but a
    derived "expired" over a grace_period row of the same period is now HELD
    (`_billing_grace_hold_reason`), and this is the notification that must end that hold. It is
    about the period in grace only, so one about an older period than the stored row's is still
    refused (`_PERIOD_SCOPED_NOTIFICATIONS`). `DID_FAIL_TO_RENEW` without a subtype stays
    derived on purpose: an override would cut short a period that is still running if Apple
    ever signed one before the end.
    """
    ntype = (notification_type or "").upper()
    stype = (subtype or "").upper()

    if ntype in {"REFUND", "REVOKE"}:
        return "revoked"
    if ntype in {"EXPIRED", "GRACE_PERIOD_EXPIRED"}:
        return "expired"
    if ntype in {"SUBSCRIBED", "DID_RENEW", "OFFER_REDEEMED"}:
        return "active"
    if ntype == "DID_FAIL_TO_RENEW":
        # GRACE_PERIOD → still entitled. No subtype → the grace period is over or was never
        # granted, so fall back to deriving from the payload rather than guessing entitlement.
        return "grace_period" if stype == "GRACE_PERIOD" else None
    if ntype == "DID_CHANGE_RENEWAL_STATUS":
        # Auto-renew turned on/off changes nothing about the CURRENT period's entitlement.
        return None
    return None


def status_for_transaction(payload: Dict[str, Any]) -> str:
    """Derive a subscription status from a verified transaction.

    Revocation and expiry are read from the payload rather than assumed active: a refunded
    purchase carries `revocationDate`, and a lapsed one an `expiresDate` in the past. Taking
    "we received a transaction" to mean "entitled" would keep refunded users on a paid tier.
    """
    if payload.get("revocationDate") or payload.get("revocationReason") is not None:
        return "revoked"
    expires = _ms_to_dt(payload.get("expiresDate"))
    if expires and expires <= datetime.now(timezone.utc):
        return "expired"
    return "active"


# Column added by migration 114. The code must work BEFORE it is applied, because migrations
# are applied by hand and the deploy order is not guaranteed — see the write path below.
_EVENT_AT_COLUMN = "last_event_at"

# Migration 198 makes `subscriptions` ONE ROW PER APPLE LINEAGE and adds this register of what we
# know about each Apple transaction (applied, refunded, refund reversed). Its presence is what
# switches `apply_transaction` to the per-lineage rules (`IAPService._lineage_schema`); without it
# every path below behaves exactly as before 198. Applied by hand, so the code must run both ways.
_REGISTER_TABLE = "subscription_transactions"
# PostgREST's schema-cache miss and Postgres' undefined_table: "198 is not applied". Read from
# `.code`, never from the message.
_MISSING_TABLE_CODES = frozenset({"PGRST205", "42P01"})
# How long a "not applied yet" answer is trusted before probing again (the owner may apply 198
# while the process runs). A "present" answer is kept for good: 198 is never rolled back.
_LINEAGE_REPROBE_SECONDS = 300.0

# Column added by migration 196, same posture. `ensure_credit_period` starts a paid credit
# period only for a Production row: Sandbox / TestFlight renew every few minutes, and each
# renewal would otherwise grant a full allocation.
_ENVIRONMENT_COLUMN = "environment"
_OPTIONAL_SUBSCRIPTION_COLUMNS = (_EVENT_AT_COLUMN, _ENVIRONMENT_COLUMN)


def _stale_delivery_reason(
    prior: Optional[Dict[str, Any]],
    *,
    status: str,
    event_at: Optional[datetime],
) -> Optional[str]:
    """Return why this delivery is older news than what we already stored, or None to apply.

    ORDERING IS ON APPLE'S `signedDate`, not on `expiresDate`. That distinction is the whole
    design and it is not obvious:

    `expiresDate` describes WHICH PERIOD a transaction is about; `signedDate` describes WHEN
    APPLE SAID IT. They disagree precisely in the case that matters — an EXPIRED notification
    is signed *late* and carries an *early* expiresDate, because it is telling you about the
    period that just ended. Ordering on `expiresDate` therefore classifies every ordinary
    expiry as "stale" and silently keeps lapsed subscribers on a paid tier: the opposite of
    the bug, and more expensive. (`test_expiry_notification_drops_the_tier` catches exactly
    that, which is how this was found.)

    Deliberately CONSERVATIVE — dropping a genuine event is as bad as applying a stale one:

      1. **No prior row, or no stored ordering key** — nothing to compare against, so apply.
         This is also the pre-migration-114 state, where the whole guard is inert by design.
      2. **No `signedDate` on the notification** — unorderable, so apply. (The CLIENT-verify
         path is ordered by `_stale_client_delivery_reason` instead, on the transaction's own
         `signedDate` — a verified JWS does carry one.)
      3. **Revocation always wins.** A refund/chargeback is authoritative, and leaving a
         refunded user entitled is the expensive direction. (It wins over ORDERING: a refund
         of an older period than the stored one is refused by
         `_older_period_reason`.) It never REWINDS the stored key, though:
         `apply_transaction` keeps the later one.
      4. **Equal timestamps apply.** Apple can re-sign an identical payload; re-applying it is
         idempotent, whereas dropping it on a tie could lose a real transition.
    """
    if not prior:
        return None
    if status == "revoked":
        return None
    if event_at is None:
        return None

    prior_event_at = _parse_iso(prior.get(_EVENT_AT_COLUMN))
    if prior_event_at is None:
        return None
    if event_at >= prior_event_at:
        return None

    return (
        f"incoming signedDate={event_at.isoformat()} predates the stored "
        f"{_EVENT_AT_COLUMN}={prior_event_at.isoformat()} "
        f"(stored status={prior.get('status')!r}, tier={prior.get('tier')!r})"
    )


def _stale_client_delivery_reason(
    prior: Optional[Dict[str, Any]],
    *,
    status: str,
    signed_at: Optional[datetime],
) -> Optional[str]:
    """Why a CLIENT-submitted transaction is older news than the stored row, or None to apply.

    The replay this closes: buy Max, get an Apple refund (the REFUND notification revokes the
    row and claws the allocation back), then re-POST the SAVED pre-refund JWS to
    `/billing/verify`. Apple's signature still verifies — online checks validate the chain at
    the CURRENT time — and that JWS has no `revocationDate` and a future `expiresDate`, so it
    reads "active". Unordered, it overwrote the revoked row and `grant_tier_upgrade` handed
    back the full 4000: the refund AND the tier, repeatable after every purchase.

    The ordering key is the JWS's own `signedDate` — when Apple asserted this state. Anything
    Apple signed after the refund carries the revocation, so a payload signed BEFORE the stored
    event is stale by construction; a genuine re-subscribe is a new transaction Apple signs
    later, and still applies.

    Rules, deliberately narrower than "never trust the client":

      * **Incoming revocation always applies** (same as the webhook guard) — unless it is for
        an older period than the stored one (`_older_period_reason`).
      * **No stored key → apply**, exactly as before — EXCEPT a REVOKED row, which falls back
        to its `updated_at` (when we wrote the revocation): rows revoked before migration 114,
        or by the client path, may carry no `last_event_at`.
      * **Compared across lineages.** `prior` may be the user's row for a DIFFERENT
        originalTransactionId (the owner fallback, before 198). Skipping those would re-open the
        replay through a cheap re-subscribe: refund Max, buy Pro, replay the Max JWS. Since 198
        the transaction register refuses that replay (Rules A, A′), and Rule A″ still calls this
        for every row of the account when the JWS predates the register (review #7/#23).
      * **Against a REVOKED row, fail closed:** a payload with no `signedDate`, or one signed
        at the same instant as the stored key, cannot prove it postdates the revocation.
        Otherwise ties apply, as on the webhook path.

    Accepted cost: a genuine NEW-lineage purchase whose verify is delayed past a later
    notification about the user's OLD lineage reads as stale. That needs a lineage switch, a
    failed verify, and an old-lineage event inside that window — against a free, repeatable
    replay on every refund.
    """
    if not prior:
        return None
    if status == "revoked":
        return None

    prior_status = str(prior.get("status") or "").lower()
    prior_revoked = prior_status == "revoked"

    key_name = _EVENT_AT_COLUMN
    stored = _parse_iso(prior.get(_EVENT_AT_COLUMN))
    if stored is None and prior_revoked:
        key_name = "updated_at"
        stored = _parse_iso(prior.get("updated_at"))
    if stored is None:
        return None

    context = (
        f"stored {key_name}={stored.isoformat()}, "
        f"status={prior.get('status')!r}, tier={prior.get('tier')!r}"
    )
    if signed_at is None:
        if prior_revoked:
            return (
                "client payload has no signedDate, so it cannot prove it postdates the "
                f"revocation ({context})"
            )
        return None
    if signed_at > stored:
        return None
    if signed_at == stored and not prior_revoked:
        return None
    return f"client signedDate={signed_at.isoformat()} does not postdate the {context}"


def _next_event_key(
    prior: Optional[Dict[str, Any]], incoming: Optional[datetime]
) -> Optional[str]:
    """The ordering key to STORE for a delivery carrying `incoming`, or None to leave the row's.

    Monotonic: a key is stored only when it is later than the stored one, or no readable key is
    stored. A revocation skips the stale checks ("revocation always wins"), so it can be signed
    BEFORE the stored key; stored as-is, the late refund of an OLDER period (signed T3), applied
    over a row the refund of the current period (T5) had revoked, rewound the key to T3 — and
    anything signed between the two then read as newer than the refund: the saved pre-refund JWS
    of the current period brought back the tier and migration 196 reinstated the refunded
    period's credits (2026-10-13).

    Compared with `last_event_at` ALONE, never the client guard's `updated_at` fallback for a
    keyless revoked row: the notification guard has no such fallback, so leaving a keyless row
    keyless (because the incoming signedDate fell below it) let a stale notification through
    that the stored key would have refused.
    """
    if incoming is None:
        return None
    stored = _parse_iso((prior or {}).get(_EVENT_AT_COLUMN))
    if stored is not None and incoming <= stored:
        return None
    return incoming.isoformat()


# The columns a subscriptions write decides. A write whose values all equal the stored ones
# (and stores no newer ordering key) changes nothing but `updated_at`, and is skipped.
# `transaction_id` (198) is written only once 198 is applied; a row that lacks it compares as "".
_DECIDED_COLUMNS = ("user_id", "tier", "status", "store", "original_transaction_id",
                    _ENVIRONMENT_COLUMN, "transaction_id")


def _row_unchanged(prior: Optional[Dict[str, Any]], row: Dict[str, Any]) -> bool:
    """Would writing `row` over `prior` change nothing but `updated_at`?

    StoreKit delivers the same transaction from several places at once (`Transaction.updates`,
    the unfinished-transaction drain, Restore), and every write moves `updated_at` — which is
    what the write fence below compares. Rewriting identical values would make those benign
    bursts miss each other's fences until one runs out of attempts (a 503 "Purchase Failed" for
    a purchase that WAS applied), and would let a client replaying one JWS keep a REFUND's fence
    missing. Compared on parsed period ends: the database returns its own timestamp format.
    """
    if not prior or _EVENT_AT_COLUMN in row:
        return False
    for column in _DECIDED_COLUMNS:
        if str(prior.get(column) or "") != str(row.get(column) or ""):
            return False
    return _parse_iso(prior.get("current_period_end")) == _parse_iso(
        row.get("current_period_end")
    )


# The subscriptions write is FENCED on the `updated_at` the decision was made against (see
# `IAPService._fenced_update`). A miss means another delivery wrote first, so the whole apply
# runs again from the read. A revocation gets more attempts: if it runs out, its 503 waits for
# Apple's next retry (an hour or more), and the refunded credits stay spendable meanwhile.
_FENCE_ATTEMPTS = 4
_FENCE_ATTEMPTS_REVOCATION = 6

# How many times `reconcile_user_tier` writes `users.tier` before it stops re-checking it.
_MIRROR_WRITES = 3


def _fence_backoff(attempt: int) -> None:
    """A short, jittered pause before re-reading after a fence miss, so two deliveries that
    collided do not collide again in lockstep. Both callers run in worker threads
    (`asyncio.to_thread`), so sleeping never blocks the event loop."""
    time.sleep(random.uniform(0.02, 0.1) * attempt)


# How much earlier than the stored period a delivery's period must END to count as an OLDER one.
# A delivery about the period in force carries the very same `expiresDate`, so this only absorbs
# representation drift, and drift must never turn a refund of that period into an "older" one:
# that is the direction that leaves a refunded user entitled. Apple's shortest period is a
# Sandbox renewal (3-5 minutes); only Xcode's accelerated StoreKit testing goes below the
# margin, and there an older period's delivery simply applies, as before.
_OLDER_PERIOD_MARGIN = timedelta(seconds=60)

# An upgrade bought inside Apple's early-renewal overlap (it charges the renewal up to 24 h before
# the period ends; month lengths add up to ~4 days of skew) ends a little BEFORE the lower-tier
# renewal it supersedes. If the row never recorded that upgrade, its REFUND is the only delivery
# about it, and within this bound a revoked higher tier is that refund: the superseded renewal,
# which Apple refunded pro rata at the upgrade, must not keep the account entitled. Production
# periods only — a Sandbox month lasts minutes, so the bound means nothing there.
_UPGRADE_OVERLAP_MAX = timedelta(days=7)

# The status overrides (`_status_for_notification`) that `_older_period_reason` honours whatever
# transaction they carry: EXPIRED's "expired", which states the SUBSCRIPTION's condition. The
# other notification types that produce these values ("expired" from GRACE_PERIOD_EXPIRED,
# "grace_period" from DID_FAIL_TO_RENEW/GRACE_PERIOD) are about one period and excluded by type
# (`_PERIOD_SCOPED_NOTIFICATIONS`).
_SUBSCRIPTION_STATE_OVERRIDES = frozenset({"expired", "grace_period"})

# Notification types whose explicit status is about ONE period, the one that failed to renew,
# so `_older_period_reason` still refuses one about an older period than the stored row's:
# - GRACE_PERIOD_EXPIRED ("expired" since 2026-10-14, so the billing-grace hold never swallows
#   it). Exempted like EXPIRED, it demoted a RECOVERED subscriber: Apple's retry of a failed
#   GRACE_PERIOD_EXPIRED landed after the app relayed the renewal (a client write stores no
#   ordering key), and wrote the old, expired period over the paid one (verifier, 2026-10-14).
# - DID_FAIL_TO_RENEW, whose GRACE_PERIOD subtype is "grace_period" (2026-10-15). Apple can send
#   it AFTER the BILLING_RECOVERY that superseded it, with a later signedDate (Apple staff,
#   forums 745515). Exempted, it rewound a recovered row to the failed period in grace: the
#   recovered period's allocation never started, and the sweep expired the paying subscriber
#   at the old end + IAP_BILLING_GRACE_DAYS + 24 h, before the next DID_RENEW.
_PERIOD_SCOPED_NOTIFICATIONS = frozenset({"GRACE_PERIOD_EXPIRED", "DID_FAIL_TO_RENEW"})


def _older_period_reason(
    prior: Optional[Dict[str, Any]],
    *,
    status: str,
    status_override: Optional[str],
    tier: str,
    environment: Optional[str],
    original_transaction_id: str,
    period_end: Optional[datetime],
    notification_type: Optional[str] = None,
    unrecorded: Optional[bool] = None,
) -> Optional[str]:
    """Why a delivery is about an OLDER paid period than the stored row's, or None to apply it.

    Each renewal is its own transaction with its own, fixed `expiresDate`, and Apple sends older
    ones long after they ended. A REFUND of a past renewal carries that renewal; so do
    REFUND_REVERSED, REFUND_DECLINED and CONSUMPTION_REQUEST about it, and a JWS the app relays
    from `Transaction.updates`. Written over the ONE `subscriptions` row, any of them moved it
    back to the old period, and `reconcile_user_tier` dropped to free (revoked, or expired) a
    subscriber whose current period was paid for and not refunded, until the current
    transaction was delivered again (Restore, or the next DID_RENEW). Migration 196 already kept
    their CREDITS (it claws back only the period in force); the tier had no guard. When the
    refunded older period is itself still the credit period in force — after an early renewal,
    or a refund delivery that failed until a resubscribe — its unspent credits now last until it
    ends: SYSTEM_DESIGN_GUIDELINES §9b.12 "Known, accepted gaps".

    Decided on WHICH period (`expiresDate`), not on when Apple said it (`signedDate`): a refund
    is signed late by nature, so its signedDate cannot tell an old period from the current one.

    Narrow on purpose; everything else applies exactly as before:

      * **Same lineage only.** The owner fallback can hand over the user's row for a DIFFERENT
        originalTransactionId, whose period end says nothing about this transaction's periods
        (one row per user cannot hold two lineages; see §9b.12).
      * **The stored row must entitle** (`_ENTITLING_STATUSES`). Its end is compared, never the
        clock: an `active` row past its end is still a newer period than the older one, and the
        expiry sweep or the next delivery ends it.
      * **Earlier by at least `_OLDER_PERIOD_MARGIN`.** An EQUAL `expiresDate` is the period in
        force, a LATER one a renewal the row has not recorded yet; both apply. So does a payload
        or a row with no readable date.
      * **Not a subscription-state notification** (`_SUBSCRIPTION_STATE_OVERRIDES`: EXPIRED).
        GRACE_PERIOD_EXPIRED and DID_FAIL_TO_RENEW/GRACE_PERIOD are NOT, although each carries
        an explicit status (`_PERIOD_SCOPED_NOTIFICATIONS`): about an older period than the
        stored row, they are refused.
      * **Never an entitling HIGHER tier** that the lineage has not recorded. An upgrade starts
        at once and its period can be any length (an offer, a trial, out of a long intro period,
        after an early renewal), so it can end well before the stored period; it must apply.
        Since 198 a RECORDED one (`unrecorded=False`) is an older period: the REFUND_REVERSED or a
        relay of a refunded Max must not overwrite the Pro the user resubscribed to since, and
        drop them to Free when the Max period ends (review #8, 2026-10-15). A period-scoped
        notice (`_PERIOD_SCOPED_NOTIFICATIONS`) is never an upgrade: it keeps this exemption
        only inside the overlap bound below.
      * **Not the refund of an overlap upgrade** (`_UPGRADE_OVERLAP_MAX`, Production only) —
        that is, of an upgrade the row NEVER recorded. Since migration 198 the caller knows:
        `unrecorded=False` (the transaction was applied to this lineage's row, or its period was
        granted credits) makes a revoked higher tier an OLDER period, so a refunded Max's late or
        duplicate refund cannot drop a Pro resubscriber to Free. `None` (before 198) keeps the
        7-day exemption as it was.
    """
    if not prior:
        return None
    if (status_override in _SUBSCRIPTION_STATE_OVERRIDES
            and (notification_type or "").upper() not in _PERIOD_SCOPED_NOTIFICATIONS):
        return None
    stored_original = str(prior.get("original_transaction_id") or "")
    if not original_transaction_id or stored_original != original_transaction_id:
        return None
    if str(prior.get("status") or "").lower() not in _ENTITLING_STATUSES:
        return None
    stored_end = _parse_iso(prior.get("current_period_end"))
    if period_end is None or stored_end is None:
        return None
    earlier_by = stored_end - period_end
    if earlier_by < _OLDER_PERIOD_MARGIN:
        return None
    stored_tier = str(prior.get("tier") or "free").lower()
    if _TIER_RANK.get(tier, 0) > _TIER_RANK.get(stored_tier, 0):
        in_overlap = (earlier_by <= _UPGRADE_OVERLAP_MAX
                      and (environment or "Production") == "Production")
        # A period-scoped notice (DID_FAIL_TO_RENEW/GRACE_PERIOD) is about a period that failed
        # to renew, never an upgrade starting now. Exempted at any distance, a late Max grace
        # notice rewound the Pro row a downgrade's billing recovery had written (review,
        # 2026-10-15). It keeps the exemption only inside the overlap bound: an upgrade bought in
        # the early-renewal overlap, superseded on the row by the lower-tier renewal it replaced.
        period_scoped = (notification_type or "").upper() in _PERIOD_SCOPED_NOTIFICATIONS
        if (status in _ENTITLING_STATUSES and unrecorded is not False
                and (in_overlap or not period_scoped)):
            return None
        if status == "revoked" and in_overlap and unrecorded is not False:
            return None
    return (
        f"expiresDate={period_end.isoformat()} (tier={tier!r}, status={status!r}) is before the "
        f"stored current_period_end={stored_end.isoformat()} "
        f"(stored status={prior.get('status')!r}, tier={prior.get('tier')!r})"
    )


def _billing_grace() -> timedelta:
    """App Store Connect's Billing Grace Period (`IAP_BILLING_GRACE_DAYS`). Read from settings each
    call so a config change (or a test override) takes effect without a restart."""
    return timedelta(days=settings.IAP_BILLING_GRACE_DAYS)


# Apple's `data.status` values (app_store.notification_subscription_status) that say the
# subscription no longer entitles: 2 expired, 3 billing retry outside grace, 5 revoked.
_APPLE_STATUS_UNENTITLED = frozenset({2, 3, 5})


def _ends_service_by_itself(
    notification_type: Optional[str], notification_subtype: Optional[str],
    apple_status: Optional[int],
) -> Optional[str]:
    """Why a notification with a DERIVED "expired" is Apple's own word that service has ended
    (so `_billing_grace_hold_reason` must not hold it), or None.

    * **DID_FAIL_TO_RENEW without a subtype** (with GRACE_PERIOD it carries an explicit status):
      "the subscription isn't in a grace period and you can stop providing the subscription
      service". Its status stays derived, so it never cuts a running period short.
    * **DID_CHANGE_RENEWAL_STATUS / AUTO_RENEW_DISABLED**: a cancellation during the billing
      grace period ends access AT ONCE (Apple's failed-renewal testing guide), and Apple then
      sends EXPIRED, not GRACE_PERIOD_EXPIRED. By type, because Apple does not document which
      `data.status` that cancellation carries — except one Apple signed while the subscription
      was active (`data.status` 1), which only cancels the next renewal. AUTO_RENEW_ENABLED is
      still held.
    * **Apple's `data.status` 2, 3 or 5** on any notification: expired, billing retry outside
      grace, revoked, as of its signedDate. It only ever ENDS a hold: 1 and 4 change nothing.
    """
    ntype = (notification_type or "").upper()
    stype = (notification_subtype or "").upper()
    if ntype == "DID_FAIL_TO_RENEW":
        return "a DID_FAIL_TO_RENEW without a grace period subtype"
    # Not one Apple signed while the subscription was still ACTIVE (data.status 1): that
    # cancellation only stops the next renewal, and arrives "expired" only because its delivery
    # was retried past the period's end (review, 2026-10-15). EXPIRED/VOLUNTARY, or the derived
    # expiry past the hold's window, ends that period. With no data.status it still ends service.
    if (ntype == "DID_CHANGE_RENEWAL_STATUS" and stype == "AUTO_RENEW_DISABLED"
            and apple_status != 1):
        return "a cancellation (DID_CHANGE_RENEWAL_STATUS/AUTO_RENEW_DISABLED)"
    if apple_status in _APPLE_STATUS_UNENTITLED:
        return f"Apple's data.status={apple_status}"
    return None


def _billing_grace_hold_reason(
    prior: Optional[Dict[str, Any]],
    *,
    status: str,
    status_override: Optional[str],
    original_transaction_id: str,
    period_end: Optional[datetime],
    notification_type: Optional[str] = None,
    notification_subtype: Optional[str] = None,
    apple_status: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Why a delivery must not end the period the stored row still entitles from, or None.

    **Billing grace** (2026-10-14). Apple's DID_FAIL_TO_RENEW/GRACE_PERIOD means "continue to
    provide service through the grace period", and the row reads `grace_period` with the period
    that failed to renew. Every later delivery about that SAME period carries its fixed
    `expiresDate`, which has passed, and the ones with no status of their own DERIVED "expired"
    from it: a Restore (`Transaction.currentEntitlements` returns a subscription in grace),
    PRICE_INCREASE, DID_CHANGE_RENEWAL_PREF, AUTO_RENEW_ENABLED, CONSUMPTION_REQUEST,
    REFUND_DECLINED. Written, it dropped the subscriber to Free, and migration 196 lapsed the
    balance it was holding, until billing recovered or the grace period ended.

    **The renewal verdict not in yet** (2026-10-15). When the grace notification's own delivery
    fails, the row still reads `active`, past its end, until Apple's retry lands (+1, +12, +24 h
    after each attempt). A derived expiry in that window — the same Restore, or a webhook like
    the ones above — was written: Free, and the held balance lapsed for good (Apple's retry then
    restored the tier only). A webhook did worse: its later signedDate became the ordering key,
    so the retry was refused as stale and the user stayed Free for the whole grace period. Held
    for the expiry sweep's own `active` window, the delivery changes nothing the sweep would not
    have: `winning_tier` already drops an `active` row past its end, and the balance is HELD
    (196/198) until the sweep expires the row.

    Held only when ALL of these are true; everything else applies exactly as before:

      * **The status is a DERIVED "expired"** (`status_override` is None). An explicit EXPIRED
        or GRACE_PERIOD_EXPIRED ends the grace period, and a revocation is never held. An
        explicit "active" about a period that has already ended counts as a derived expiry.
      * **It is not Apple's own word that service has ended** (`_ends_service_by_itself`).
      * **The stored row still entitles from that period**: `grace_period` / `billing_retry`
        (`_LONG_WINDOW_STATUSES`), or `active`.
      * **Same lineage, same period.** The same original transaction (the owner fallback's row
        is another lineage), and an `expiresDate` within `_OLDER_PERIOD_MARGIN` of the stored
        end. An earlier one is an older period (`_older_period_reason`); a later one is a newer
        period, and that one may really have expired.
      * **Inside the window**: `now` before the stored end + `IAP_BILLING_GRACE_DAYS` (grace,
        retry) or + the sweep's `active` window, 24 h (`_sweep_windows`). The bound keeps a LOST
        notification from making the hold stick: past it, a derived expiry applies again, and
        the expiry sweep ends the row.

    Edges, accepted: a lost GRACE_PERIOD_EXPIRED with no later delivery keeps the tier until the
    bound + 24 h (the sweep). In Sandbox, Apple's accelerated grace lasts minutes, so there it is
    GRACE_PERIOD_EXPIRED, not the day bound, that ends the hold.
    """
    now = now or datetime.now(timezone.utc)
    if (status_override == "active" and period_end is not None and period_end <= now):
        # An explicit "active" about a period that has ENDED — an OFFER_REDEEMED for the next
        # renewal, sent during grace, carries the period in grace — says of THAT period only that
        # it ended: a derived expiry. Written as `active` with the past end, `winning_tier`
        # dropped a subscriber in grace to Free (review, 2026-10-15).
        status, status_override = "expired", None
    if not prior or status != "expired" or status_override is not None:
        return None
    if _ends_service_by_itself(notification_type, notification_subtype, apple_status):
        return None
    stored_status = str(prior.get("status") or "").lower()
    if stored_status in _LONG_WINDOW_STATUSES:
        window, what = _billing_grace(), "the billing grace period"
        bound = f"IAP_BILLING_GRACE_DAYS={settings.IAP_BILLING_GRACE_DAYS}"
    elif stored_status == "active":
        window, what = _sweep_windows()["active"], "Apple's word on the renewal is due"
        bound = "the expiry sweep's active window"
    else:
        return None
    stored_original = str(prior.get("original_transaction_id") or "")
    if not original_transaction_id or stored_original != original_transaction_id:
        return None
    stored_end = _parse_iso(prior.get("current_period_end"))
    if period_end is None or stored_end is None:
        return None
    if abs(stored_end - period_end) >= _OLDER_PERIOD_MARGIN:
        return None
    window_ends = stored_end + window
    if now >= window_ends:
        return None
    return (
        f"expiresDate={period_end.isoformat()} is the stored period "
        f"(current_period_end={stored_end.isoformat()}, stored status={prior.get('status')!r}, "
        f"tier={prior.get('tier')!r}); {what} until {window_ends.isoformat()} ({bound})"
    )


def _payload_revoked(payload: Dict[str, Any]) -> bool:
    """Does the TRANSACTION itself say it was refunded/revoked? (Not the notification's status:
    a DID_RENEW is "active" whatever it carries.) The same test `status_for_transaction` uses."""
    return bool(payload.get("revocationDate")) or payload.get("revocationReason") is not None


def _register_refunded(rec: Optional[Dict[str, Any]]) -> bool:
    """Migration 198's predicate: refunded while the latest refund is newer than the latest
    reversal of it. Mirrors the SQL (`record_subscription_transaction`'s `refunded`)."""
    if not rec:
        return False
    revoked = _parse_iso(rec.get("revoked_signed_at"))
    reversed_at = _parse_iso(rec.get("reversed_signed_at"))
    return revoked is not None and (reversed_at is None or reversed_at < revoked)


def _newer_period_reason(
    prior: Optional[Dict[str, Any]],
    *,
    status: str,
    payload_revoked: bool,
    tier: str,
    environment: Optional[str],
    period_end: Optional[datetime],
) -> Optional[str]:
    """Why a delivery the signedDate check would refuse is NEWER information after all, or None.

    Migration 198 (lineage mode only; `prior` is this lineage's own row). The refund of the
    running period, delivered BEFORE the early renewal's DID_RENEW, revoked the row and stored its
    late signedDate; the DID_RENEW — signed earlier, retried later, Apple keeps no order — was then
    refused as stale and a paid renewal never landed (design review, 2026-10-13). A payload about a
    period this lineage has never recorded cannot be older news, so it skips the signedDate check
    when ALL of these hold:

      * **The stored row does not entitle** (revoked or expired) — exactly the states a misordered
        refund or expiry leaves. Over an ENTITLING row the check stays: a superseded early renewal
        retried after an overlap upgrade the row recorded would otherwise overwrite the upgrade.
      * **The payload entitles and carries no revocation.**
      * **Its period ends at least `_OLDER_PERIOD_MARGIN` after `max_period_end`**, the latest
        period end the row has EVER held (kept by a trigger), not `current_period_end`, which an
        overlap upgrade and a late older refund both move back. The mark must be KNOWN: a row that
        did not entitle when 198 was applied holds 'infinity' (its history may have been rewound
        before the trigger existed), and never takes this exemption (review #17).
      * **Its tier is not below the stored one**, unless (Production) it ends more than
        `_UPGRADE_OVERLAP_MAX` after the stored period: a downgrade's next period, a whole period
        later, versus a lower-tier renewal that an upgrade superseded, which ends days apart.

    A refunded transaction never gets here: the register refuses it first (Rule A).
    """
    if not prior or status not in _ENTITLING_STATUSES or payload_revoked or period_end is None:
        return None
    if str(prior.get("status") or "").lower() in _ENTITLING_STATUSES:
        return None
    mark = _parse_iso(prior.get("max_period_end"))
    if mark is None:
        # No KNOWN high-water mark: 'infinity' — 198's backfill of a row that did not entitle
        # when 198 was applied, whose end may have been rewound by a late older refund — or NULL
        # (no period ever recorded). Without every period this lineage recorded, the pre-198
        # ordering stands: a refunded renewal's saved JWS must not pass as "newer" (review #17).
        return None
    high = max(e for e in (mark, _parse_iso(prior.get("current_period_end"))) if e is not None)
    if period_end - high < _OLDER_PERIOD_MARGIN:
        return None
    stored_tier = str(prior.get("tier") or "free").lower()
    if _TIER_RANK.get(tier, 0) < _TIER_RANK.get(stored_tier, 0):
        stored_end = _parse_iso(prior.get("current_period_end"))
        if ((environment or "Production") != "Production" or stored_end is None
                or period_end - stored_end <= _UPGRADE_OVERLAP_MAX):
            return None
    return (
        f"expiresDate={period_end.isoformat()} (tier={tier!r}) is later than every period this "
        f"lineage has recorded (max_period_end={high.isoformat()}, stored status="
        f"{prior.get('status')!r}, tier={prior.get('tier')!r})"
    )


def _grace_recovery_reason(
    prior: Optional[Dict[str, Any]],
    *,
    status: str,
    payload_revoked: bool,
    original_transaction_id: str,
    period_end: Optional[datetime],
) -> Optional[str]:
    """Why a payload the signedDate check would refuse is the RECOVERY of the period in grace,
    or None.

    Apple can sign the DID_FAIL_TO_RENEW/GRACE_PERIOD of a period AFTER the BILLING_RECOVERY that
    superseded it (Apple staff, forums 745515). Delivered first, the grace notice stores its later
    signedDate, and the recovery — signed earlier, delivered later, or relayed by the app — was
    refused as stale: the row stayed in the failed period, the recovered period's allocation never
    started, and the sweep expired a paying subscriber at the old end + grace + 24 h (review,
    2026-10-15). `_newer_period_reason` (Rule D) does not cover it: the grace row entitles.

    A newer period of the SAME lineage over a row in grace or billing retry cannot be older news:
    that row's period ended without renewing, so any later period is the renewal Apple has not
    told us about yet. So it skips the signedDate check when ALL of these hold:

      * **The stored row is in grace or billing retry** (`_LONG_WINDOW_STATUSES`).
      * **The payload entitles, carries no revocation, and is of the same original transaction.**
      * **Its period ends at least `_OLDER_PERIOD_MARGIN` after the stored one.**

    Lineage mode only, after the register's refusals (Rules A, A′, A″) and the `isUpgraded` one,
    and only for Apple's word from after the register began (the caller applies Rule D's cut): a
    refunded renewal's saved JWS is refused before it gets here. Before 198 the pre-198 ordering
    stands — that replay would re-entitle a refunded period and reinstate its credits.
    """
    if not prior or status not in _ENTITLING_STATUSES or payload_revoked or period_end is None:
        return None
    if str(prior.get("status") or "").lower() not in _LONG_WINDOW_STATUSES:
        return None
    stored_original = str(prior.get("original_transaction_id") or "")
    if not original_transaction_id or stored_original != original_transaction_id:
        return None
    stored_end = _parse_iso(prior.get("current_period_end"))
    if stored_end is None or period_end - stored_end < _OLDER_PERIOD_MARGIN:
        return None
    return (
        f"expiresDate={period_end.isoformat()} is a newer period than the one in "
        f"{prior.get('status')!r} (stored current_period_end={stored_end.isoformat()}): the "
        "renewal of the period that failed"
    )


def pick_display_subscription(
    rows: list, now: Optional[datetime] = None
) -> Optional[Dict[str, Any]]:
    """The row `/users/me/subscription` shows, now that an account can hold one row per Apple
    lineage (migration 198): the one `winning_tier` would pick — entitling (an `active` row only
    before its period end; grace and billing retry always), best tier, later end — else the most
    recently written. One row: that row, exactly as before."""
    if not rows:
        return None
    now = now or datetime.now(timezone.utc)
    floor = datetime.min.replace(tzinfo=timezone.utc)

    def entitles(row: Dict[str, Any]) -> bool:
        status = str(row.get("status") or "").lower()
        if status not in _ENTITLING_STATUSES:
            return False
        if status in _LONG_WINDOW_STATUSES:
            return True
        end = _parse_iso(row.get("current_period_end"))
        return end is None or end > now

    live = [r for r in rows if entitles(r)]
    if live:
        return max(live, key=lambda r: (_TIER_RANK.get(str(r.get("tier") or "free").lower(), 0),
                                        _parse_iso(r.get("current_period_end")) or floor))
    return max(rows, key=lambda r: _parse_iso(r.get("updated_at")) or floor)


# How long past `current_period_end` the expiry sweep leaves an entitling row (`_sweep_windows`).
_SWEEP_MARGIN = timedelta(hours=24)
_BILLING_RETRY_WINDOW = timedelta(days=60)   # Apple retries billing for 60 days

# What the sweep's per-row WARNING says never arrived, by the status it expired.
_SWEEP_NEVER_ARRIVED = {
    "active": "DID_RENEW, EXPIRED or DID_FAIL_TO_RENEW",
    "grace_period": "GRACE_PERIOD_EXPIRED, EXPIRED or BILLING_RECOVERY",
    "billing_retry": "EXPIRED or BILLING_RECOVERY",
}


def _sweep_windows() -> Dict[str, timedelta]:
    """Each entitling status → how long past its period end `sweep_expired_subscriptions`
    leaves it entitled.

    * `active`: 24 h, for renewal latency and clock skew.
    * `grace_period`: the billing grace period (`IAP_BILLING_GRACE_DAYS`) + the same 24 h. Apple's
      GRACE_PERIOD_EXPIRED ends it; this catches a lost one. It was 60 days until 2026-10-14,
      which kept an unpaid user on the tier for up to ~32 days after Apple stopped serving.
    * `billing_retry`: Apple's 60-day retry window (no writer sets this status today).
    * Any other entitling status: 24 h.
    """
    windows = {status: _SWEEP_MARGIN for status in _ENTITLING_STATUSES}
    windows["grace_period"] = _billing_grace() + _SWEEP_MARGIN
    windows["billing_retry"] = _BILLING_RETRY_WINDOW
    return windows


def _is_upgraded(payload: Dict[str, Any]) -> bool:
    """Apple's `isUpgraded`: this transaction was SUPERSEDED by an upgrade in its group.

    The upgrade's own transaction carries the entitlement; the upgraded one describes nothing
    current, and applying it overwrote the row with the LOWER tier (or, if it carries a
    revocation, revoked a paying Max subscriber and clawed back their credits). A bool from
    `_to_dict`; a string is tolerated so a JSON-shaped payload cannot slip past as truthy junk.
    """
    value = payload.get("isUpgraded")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _uuid_or_none(value: Any) -> Optional[str]:
    """Canonical lowercase UUID string, or None for anything that is not a UUID."""
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        return None


# Postgres SQLSTATEs the consumable refund tombstone branches on (read from `.code`, never
# from the message text).
_UNIQUE_VIOLATION = "23505"
_FOREIGN_KEY_VIOLATION = "23503"


class IAPService:
    def __init__(self):
        self.supabase = get_supabase()

    # ── Routing ───────────────────────────────────────────────────────────────

    def apply_verified_transaction(
        self,
        user_id: str,
        payload: Dict[str, Any],
        *,
        status_override: Optional[str] = None,
        event_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """THE entry point for any Apple-verified transaction. Routes by product kind.

        `apply_transaction` below is left byte-identical to what shipped: the subscription
        path carries two rounds of money-bug fixes (cross-account rebind, out-of-order
        deliveries) and 35 tests pin it, so consumables get a SIBLING rather than a branch
        inside it. If this dispatcher is wrong, subscriptions are provably unaffected.

        `payload` MUST already be Apple-verified (integrations/app_store.py).
        """
        kind = product_kind(payload.get("productId"))
        if kind == "credit_pack":
            # status_override / event_at are subscription-lifecycle concepts (grace periods,
            # out-of-order renewals). A consumable has no lifecycle and is idempotent by
            # transaction id, so ordering does not apply and neither is forwarded.
            return self.apply_consumable_transaction(user_id, payload)
        return self.apply_transaction(
            user_id, payload, status_override=status_override, event_at=event_at
        )

    # ── Consumable credit packs ───────────────────────────────────────────────

    def credit_pack_for_product(self, product_id: str) -> Dict[str, Any]:
        """Look up how many credits a pack grants, from the `credit_packs` table.

        The AMOUNT is read here and nowhere else — never from the client, never inferred from
        the product id string. Raises `UnknownProduct` if the row is missing, so a verified
        purchase of something we cannot price gets the same honest 400 a bad subscription id
        gets, rather than silently granting 0.

        Deliberately ignores `is_active`: retiring a pack hides it from the storefront, but a
        user who already bought one (a queued transaction, a cached catalog) must still be
        credited. Refusing there would take money for nothing.
        """
        try:
            result = (
                self.supabase.table("credit_packs")
                .select("product_id, credits, price_cents, display_name")
                .eq("product_id", product_id)
                .limit(1)
                .execute()
            )
            rows = result.data or []
        except Exception as e:
            # A read failure is NOT "unknown product" — the user paid and this is our outage.
            # IAPError → 503 → the client leaves the transaction unfinished and Apple
            # redelivers, which is exactly the recovery we want.
            logger.error(
                "IAP: credit_packs lookup failed for product=%s: %s: %s",
                product_id, type(e).__name__, e,
            )
            raise IAPError("could not read credit pack catalog") from e

        if not rows:
            raise UnknownProduct(
                f"productId {product_id!r} looks like a credit pack but has no row in "
                "credit_packs — apply migration 117 or add the row"
            )

        pack = rows[0]
        credits = int(pack.get("credits") or 0)

        # The catalog is a DB table, so a bad hand-edit in Studio is the realistic failure
        # mode. `credits <= 0` would take money and grant nothing; an absurd value would mint
        # an unbounded balance on a $1.99 purchase. Both are refused as OUR fault (503, the
        # transaction stays unfinished and is repaired once the row is fixed), not as a bad
        # receipt. The SQL CHECK covers the low end too; this covers the high end and the gap
        # where the value passes through Python.
        if credits <= 0 or credits > settings.IAP_MAX_PACK_CREDITS:
            logger.error(
                "IAP: credit_packs row for product=%s has implausible credits=%s "
                "(bounds 1..%s) — refusing to grant",
                product_id, credits, settings.IAP_MAX_PACK_CREDITS,
            )
            raise IAPError(f"credit pack {product_id} is misconfigured (credits={credits})")

        return pack

    def apply_consumable_transaction(
        self, user_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Grant a verified consumable credit-pack purchase, exactly once.

        Unlike the subscription path this writes NO `subscriptions` row and does NOT reconcile
        the tier — a pack is not an entitlement, it is a balance. It also skips the
        stale-delivery guard, which is a `subscriptions`-row ordering concept: consumables are
        idempotent by transaction id, so ordering is irrelevant.

        Returns the shape `billing.py` turns into a `VerifyPurchaseResponse`.
        """
        product_id = str(payload.get("productId") or "")

        # REFUSE AN ALREADY-REVOKED PURCHASE — first, before any DB read.
        #
        # The subscription path gets this for free: it routes through `status_for_transaction`,
        # which reads `revocationDate` / `revocationReason` and yields "revoked". This path
        # never consulted them, so a transaction Apple has ALREADY REFUNDED was granted as an
        # ordinary purchase — the user got their money back AND kept the credits.
        #
        # No race is needed to reach it. `Transaction.updates` redelivers unfinished
        # transactions on every launch, so one that was refunded while unfinished arrives here
        # carrying its revocation. The webhook covers the OTHER order — a REFUND that lands
        # before any grant — with a revoked tombstone row the late grant collides with
        # (`_record_refund_tombstone`), but only when the transaction carries an
        # `appAccountToken`; this check is the only guard for a JWS that already shows the
        # revocation.
        #
        # `UnknownProduct` is the right exception: billing.py maps it to a terminal 400, so the
        # client finishes the transaction and stops redelivering something that can never be
        # granted.
        if status_for_transaction(payload) == "revoked":
            logger.warning(
                "IAP: refusing REVOKED consumable txn=%s product=%s user=%s — Apple already "
                "refunded or cancelled this purchase",
                payload.get("transactionId"), product_id, user_id,
            )
            # NOT `UnknownProduct`: that maps to a 400 the client treats as non-terminal, so
            # this transaction was redelivered on every launch forever with a user-visible
            # error. A revocation can never become grantable — the client must finish it.
            raise PurchaseRevoked(
                f"consumable transaction {payload.get('transactionId')!r} is revoked "
                "(refunded or cancelled); refusing to grant credits for it"
            )

        pack = self.credit_pack_for_product(product_id)
        credits = int(pack["credits"])

        # For a consumable, EACH purchase mints a fresh `transactionId` — that is the dedup
        # key. `originalTransactionId` is deliberately NOT used: reusing the subscription
        # path's coalescing would collapse ten purchases of the same pack into one grant.
        transaction_id = str(payload.get("transactionId") or "")
        if not transaction_id:
            raise UnknownProduct("consumable transaction has no transactionId")
        original_txn_id = payload.get("originalTransactionId")

        # `_to_dict` (app_store.py:355-360) DROPS None values, so `environment` may be absent.
        # It is part of the unique dedup key, so it must never be NULL — fall back to how this
        # deploy is configured rather than letting the index stop deduping.
        environment = str(payload.get("environment") or settings.IAP_ENVIRONMENT)

        purchased_at = _ms_to_dt(payload.get("purchaseDate"))

        # THE CROSS-ACCOUNT GUARD FOR A *FIRST* DELIVERY.
        #
        # `add_purchased_credits` refuses a transaction already recorded against another user,
        # but that only catches a SECOND delivery. The dangerous case has no prior row: user A
        # buys, the verify call fails, A signs out, B signs in on the same device, and
        # `Transaction.updates` redelivers into B's session. The insert succeeds under B's id
        # and B receives credits A paid for.
        #
        token = _assert_app_account_token_matches(
            payload, user_id, kind="consumable", transaction_id=transaction_id,
            product_id=product_id,
        )

        from app.services.credit_service import CreditService, PackGrantConflict

        try:
            outcome = CreditService().grant_purchased(
                user_id=user_id,
                transaction_id=transaction_id,
                product_id=product_id,
                credits=credits,
                environment=environment,
                original_transaction_id=str(original_txn_id) if original_txn_id else None,
                price_cents=pack.get("price_cents"),
                app_account_token=str(token) if token else None,
                purchased_at=purchased_at.isoformat() if purchased_at else None,
            )
        except PackGrantConflict as e:
            # Translate to the IAP-layer exception billing.py already maps to 409, so the
            # client finishes the transaction instead of redelivering forever.
            raise PurchaseBoundToAnotherAccount(str(e)) from e
        except Exception as e:
            # CreditServiceUnavailable and anything else: the user WAS charged, so this must
            # surface as retryable (503) and the transaction must stay unfinished.
            raise IAPError(f"could not apply credit pack {product_id}: {e}") from e

        was_replay = outcome.get("outcome") == "replay"

        return {
            "kind": "credit_pack",
            # The user's real tier, NOT this product's. A pack has no tier, and answering
            # "free" would render on the client as a demotion for a paying subscriber.
            "tier": self._current_tier(user_id),
            "status": "duplicate" if was_replay else "granted",
            "winning_tier": self._current_tier(user_id),
            "current_period_end": None,
            "was_replay": was_replay,
            # 0 on a replay: the client must not claim "1,200 credits added" for something the
            # user can check against their balance.
            "credits_granted": 0 if was_replay else credits,
            "credits_spendable": outcome.get("spendable"),
            "original_transaction_id": str(original_txn_id or transaction_id),
        }

    def _current_tier(self, user_id: str) -> str:
        """The user's mirrored tier, for reporting on a non-subscription purchase.

        Reads `users.tier` (kept current by `reconcile_user_tier`) rather than recomputing
        via `winning_tier`, because a pack purchase must not fail on a `subscriptions` read
        and because this is the same value every other surface shows. On a read error it
        degrades to "free" with a loud log — the client re-reads `/users/me` moments later via
        `.caydexEntitlementChanged`, so a transient wrong value self-corrects.
        """
        try:
            result = (
                self.supabase.table("users")
                .select("tier")
                .eq("id", user_id)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            if rows:
                return str(rows[0].get("tier") or "free")
        except Exception as e:
            logger.error(
                "IAP: could not read users.tier for user=%s (%s: %s) — reporting 'free' on "
                "this credit-pack response; the client's own /users/me read will correct it",
                user_id, type(e).__name__, e,
            )
        return "free"

    # ── Entitlement application ───────────────────────────────────────────────

    def apply_transaction(
        self,
        user_id: str,
        payload: Dict[str, Any],
        *,
        status_override: Optional[str] = None,
        event_at: Optional[datetime] = None,
        client_submitted: bool = True,
        notification_type: Optional[str] = None,
        notification_subtype: Optional[str] = None,
        apple_status: Optional[int] = None,
        _attempt: int = 1,
    ) -> Dict[str, Any]:
        """Record a verified transaction and reconcile the user's entitlement.

        `payload` MUST already be Apple-verified (integrations/app_store.py). This method
        does not re-check signatures — it trusts its caller to have done so, which is why
        nothing but the verified path may call it.

        `client_submitted` defaults to True so the cross-account guard is FAIL-CLOSED for any
        new caller; the App Store Server Notification path opts out explicitly.

        `notification_type` is the notification's own type (None on the client path). The
        older-period guard reads it (one `status_override` can mean two things there,
        `_PERIOD_SCOPED_NOTIFICATIONS`), and so does the billing-grace hold, with
        `notification_subtype` and `apple_status` (Apple's `data.status`, 1..5, or None): they
        say when a derived expiry is Apple's own word that service has ended
        (`_ends_service_by_itself`). All three are None on the client path.

        Read, decide, write — and the write is FENCED on the row as read: when another
        delivery wrote in between, nothing of this one lands and it starts again from the read
        (`_attempt`, internal), so no decision is ever applied over a row it did not see.

        Returns a summary: the resolved tier, the winning tier actually applied, and whether
        this delivery was new or a replay.
        """
        tier = tier_for_product(payload.get("productId"))

        # FIRST-delivery cross-account guard. The rebind check further down only fires when a
        # `subscriptions` row already exists for this transaction; on a first delivery there is
        # none, so a transaction A paid for but never got recorded would be granted — tier AND
        # the tier's credit allocation — to whoever happens to be signed in when
        # `drainUnfinishedTransactions` re-submits it. The consumable path has always checked
        # this; the subscription path did not, while §9b.3 described the protection as general.
        if client_submitted:
            _assert_app_account_token_matches(
                payload, user_id, kind="subscription",
                transaction_id=str(payload.get("transactionId") or ""),
                product_id=payload.get("productId"),
            )
        # An App Store Server Notification knows things the transaction alone does not: a
        # DID_FAIL_TO_RENEW/GRACE_PERIOD carries a transaction whose expiresDate has already
        # passed, so deriving status from the payload reads it as "expired" — exactly when
        # Apple says the customer IS still entitled. The webhook passes the subtype's meaning
        # in here; the client-verify path leaves it None and derives as before.
        status = status_override or status_for_transaction(payload)
        original_txn_id = str(
            payload.get("originalTransactionId") or payload.get("transactionId")
        )
        period_end = _ms_to_dt(payload.get("expiresDate"))

        row = {
            "user_id": user_id,
            "tier": tier,
            "status": status,
            "store": "app_store",
            "original_transaction_id": original_txn_id,
            "current_period_end": period_end.isoformat() if period_end else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            # `_to_dict` drops None, so a payload may carry no environment; fall back to how
            # this deploy is configured, exactly as the consumable path does.
            _ENVIRONMENT_COLUMN: str(payload.get("environment") or settings.IAP_ENVIRONMENT),
        }

        # When Apple told us WHEN it said this (a notification's `signedDate`), that is the
        # ordering key. The client-verify path is ordered on the transaction's OWN `signedDate`.
        # What gets STORED is decided once `prior` is known, further down: never a lower key.
        client_path = event_at is None and client_submitted
        client_signed_at = _ms_to_dt(payload.get("signedDate")) if client_path else None

        # MIGRATION 198 (lineage mode): one subscriptions row per Apple lineage, plus the register
        # of what we know about each transaction. Ordered on the transaction's OWN signedDate on
        # both paths (a notification's inner JWS is signed when it is sent), else the
        # notification's. Before 198 none of this runs.
        lineage = self._lineage_schema()
        transaction_id = str(payload.get("transactionId") or "")
        payload_revoked = _payload_revoked(payload)
        txn_signed_at = client_signed_at if client_path else (
            _ms_to_dt(payload.get("signedDate")) or event_at)
        ntype = (notification_type or "").upper()
        environment = row[_ENVIRONMENT_COLUMN]
        if lineage and transaction_id:
            row["transaction_id"] = transaction_id

        # Idempotent on original_transaction_id: a replayed delivery updates the existing
        # row rather than inserting a duplicate entitlement.
        try:
            existing = (
                self.supabase.table("subscriptions")
                .select("*")
                .eq("original_transaction_id", original_txn_id)
                .limit(1)
                .execute()
            )
            prior = (existing.data or [None])[0]
        except Exception as e:
            logger.error(
                "IAP: subscriptions lookup failed for txn=%s user=%s: %s: %s",
                original_txn_id, user_id, type(e).__name__, e,
            )
            raise IAPError("could not read existing subscription") from e

        # "Replay" means specifically: we have seen THIS transaction before. Captured now,
        # before the owner-fallback below can also set `prior` — otherwise a genuinely new
        # re-subscribe would be reported to the client as a replay.
        txn_replay = bool(prior)

        # REFUSE A CROSS-ACCOUNT REBIND. The lookup above matches on the transaction alone,
        # while `row` carries `user_id` — so submitting a transaction that already belongs to
        # someone else used to UPDATE their row's user_id, silently MOVING the entitlement:
        # the original owner drops to free at the next reconcile and the submitter is granted
        # the tier and its credit allocation. `subscriptions_user_id_key` does not catch it,
        # because the recipient had no row of their own. An Apple-signed transaction proves a
        # purchase happened, never who is entitled to it, so ownership is decided here.
        if prior and prior.get("user_id") and prior["user_id"] != user_id:
            logger.error(
                "IAP: txn=%s is already bound to user=%s — refusing to rebind to user=%s",
                original_txn_id, prior["user_id"], user_id,
            )
            raise PurchaseBoundToAnotherAccount(
                "this purchase is already linked to another account"
            )

        # BEFORE MIGRATION 198 a user holds AT MOST ONE subscriptions row
        # (`subscriptions_user_id_key UNIQUE (user_id)`). So when the transaction is new to us
        # but the user already has a row —
        # a purchase on another Apple ID, or a move to a different subscription group, both of
        # which mint a fresh originalTransactionId (a re-subscribe on the same Apple ID in the
        # same group keeps it) — an INSERT violates that constraint and
        # the whole apply fails with 503. The user has already been charged by Apple at that
        # point. Update their existing row instead.
        #
        # CLIENT path only. A NOTIFICATION names its own lineage, and its user was resolved from
        # a row carrying THIS original transaction moments ago (`user_id_for_transaction`), so
        # no such row now means another write replaced it — typically a purchase on a new
        # lineage, the very write that makes this delivery's fence miss and re-run. Falling
        # back would write the old lineage's EXPIRED or REFUND over the subscription that just
        # paid (see the superseded-lineage return below).
        #
        # BEFORE MIGRATION 198 only. Since 198 a new lineage INSERTs its own row (a second Apple
        # ID on the account) and no row is ever re-pointed: re-pointing is what sent the live
        # lineage's webhooks to no user and let a worse lineage demote a paying account.
        if not prior and client_submitted and not lineage:
            try:
                by_user = (
                    self.supabase.table("subscriptions")
                    .select("*")
                    .eq("user_id", user_id)
                    .limit(1)
                    .execute()
                )
                owned = (by_user.data or [None])[0]
            except Exception as e:
                logger.error(
                    "IAP: subscriptions owner lookup failed for user=%s: %s: %s",
                    user_id, type(e).__name__, e,
                )
                raise IAPError("could not read existing subscription") from e
            if owned:
                logger.info(
                    "IAP: new txn=%s for user=%s replaces existing subscription row %s",
                    original_txn_id, user_id, owned["id"],
                )
                prior = owned

        # REFUSE A STALE, OUT-OF-ORDER DELIVERY. Apple does not guarantee notification
        # ordering and retries a failed delivery 5 times over about 6.5 days (+1, +12, +24, +48,
        # +72 h after each attempt; Sandbox makes one attempt), so a
        # redelivered EXPIRED can easily land AFTER the DID_RENEW that superseded it. The
        # write below is a blind overwrite of tier/status/current_period_end, so the LAST
        # delivery won regardless of which one described the newer state — silently demoting
        # a paying subscriber (and, symmetrically, able to re-entitle a refunded one).
        #
        # The ordering key is Apple's `signedDate` (see `_stale_delivery_reason`), never
        # `expiresDate`. The client-verify path is ordered too, on the transaction's own
        # `signedDate`: without it a refunded subscriber could re-POST the saved pre-refund JWS
        # and get the tier and the allocation back (`_stale_client_delivery_reason`).
        # What migration 198's keyed credit steps must do once the row decision is made — on
        # every return path below, applied or not (`_lineage_credit_steps`).
        post = {"claw": False, "reversal": False}
        # 198: this transaction's register row once read or recorded (below); `_unapplied` reports it.
        reg: Optional[Dict[str, Any]] = None

        def _lineage_credit_steps() -> None:
            # A recorded refund claws back the credit period it paid for (or the carry an upgrade
            # took from it), whichever row the decision left — the older-period refusal, another
            # lineage, the superseded half of an upgrade. A recorded reversal gives a clawed-back
            # carry back, and flags a period it can no longer restore (D6).
            if post["claw"]:
                self._revoke_paid_period(
                    user_id, environment=environment, transaction_id=transaction_id,
                    original_txn_id=original_txn_id, period_end=period_end,
                )
            if post["reversal"]:
                # Not when a refund Apple signed AFTER this reversal is recorded: it refunded the
                # period again, so the carry stays clawed back (the SQL re-checks under its lock).
                if not _register_refunded(reg):
                    self._reinstate_paid_carry(
                        user_id, original_txn_id=original_txn_id, period_end=period_end,
                    )
                self._warn_unreinstated_reversal(
                    user_id, transaction_id=transaction_id, original_txn_id=original_txn_id,
                    period_end=period_end, refunded=_register_refunded(reg),
                )

        def _unapplied(refused_by: str) -> Dict[str, Any]:
            # Nothing is written; report the stored state and re-mirror the effective tier.
            # `refused_by` says WHICH rule refused it: apply_notification's REFUND_REVERSED
            # stop-gap is about the signedDate refusal ("stale") only (review #13, 2026-10-15).
            winning = self.reconcile_user_tier(user_id)
            _lineage_credit_steps()
            kept = prior or {}
            return {
                "tier": kept.get("tier") or tier,
                "status": kept.get("status") or status,
                "winning_tier": winning,
                "original_transaction_id": original_txn_id,
                "current_period_end": kept.get("current_period_end"),
                "was_replay": txn_replay,
                "was_stale": True,
                "refused_by": refused_by,
                # 198: whether the register (after this delivery's own record) still holds a
                # refund of the transaction; None before 198.
                "register_refunded": _register_refunded(reg) if lineage else None,
            }

        # A NOTIFICATION whose lineage no longer has a row (see the owner fallback above): the
        # row was replaced or deleted after `user_id_for_transaction` found it. Nothing to
        # write it over. A revocation is logged at ERROR: its refunded period, if it is still
        # the credit period in force, is not clawed back from here. BEFORE 198 only: since 198
        # the lineage gets its own row (its user was resolved from our own records).
        if not prior and not client_submitted and not lineage:
            logger.log(
                logging.ERROR if status == "revoked" else logging.WARNING,
                "IAP: IGNORING notification for txn=%s original=%s user=%s status=%s — no "
                "subscriptions row carries this original transaction any more (replaced by "
                "another lineage, or deleted); writing it over the user's current row would "
                "describe the wrong subscription",
                payload.get("transactionId"), original_txn_id, user_id, status,
            )
            return {**_unapplied("superseded_lineage"), "lineage_superseded": True}

        # THE TRANSACTION REGISTER (198): what we know about THIS Apple transaction, whichever
        # row it reaches. Written BEFORE any row decision, so a refund is remembered even when
        # no row is written for it (an older period, another lineage, a superseded half).
        reversal_bypass = False
        upgraded = _is_upgraded(payload)
        refund_notification = ntype in {"REFUND", "REVOKE"}
        if lineage and transaction_id:
            reg_fields = dict(
                environment=environment, transaction_id=transaction_id,
                original_txn_id=original_txn_id, user_id=user_id, tier=tier,
                period_end=period_end, signed_at=txn_signed_at,
            )
            # A superseded (isUpgraded) transaction counts as refunded only on Apple's REFUND /
            # REVOKE notification: StoreKit 1 receipts marked superseded transactions cancelled,
            # and a relayed one with a revocation stamp must never take an upgrade's carry.
            if status == "revoked" and (not upgraded or refund_notification):
                reg = self._record_transaction(
                    "revoked", revocation_date=_ms_to_dt(payload.get("revocationDate")),
                    **reg_fields)
                # D4 (owner, 2026-10-14): Apple has since REVERSED this refund (a REFUND_REVERSED
                # signed after it is already recorded — gap 6, its arrival order reversed). The
                # one exception to "revocation always wins": nothing is written, nothing clawed.
                if not reg.get("refunded"):
                    logger.warning(
                        "IAP: IGNORING a refund Apple has since REVERSED: txn=%s original=%s "
                        "user=%s refund signed=%s, reversal recorded=%s",
                        transaction_id, original_txn_id, user_id, reg.get("revoked_signed_at"),
                        reg.get("reversed_signed_at"),
                    )
                    return _unapplied("reversed_refund")
                post["claw"] = True
            else:
                reg = self._read_register(environment, transaction_id)
                reversal = ntype == "REFUND_REVERSED" or (
                    not payload_revoked and reg is not None and txn_signed_at is not None
                    and _parse_iso(reg.get("revoked_signed_at")) is not None
                    and txn_signed_at > _parse_iso(reg["revoked_signed_at"]))
                # REVERSAL EVIDENCE: a REFUND_REVERSED, or any delivery of this transaction
                # WITHOUT a revocation that Apple signed after its recorded refund (Apple signs
                # current state). Recorded so a late retry of the refund cannot re-revoke, and so
                # the reinstated period's credits are not blocked by the register.
                if reversal and not payload_revoked and txn_signed_at is not None:
                    reg = self._record_transaction("reversed", revocation_date=None, **reg_fields)
                    post["reversal"] = True
            revoked_at = _parse_iso((reg or {}).get("revoked_signed_at"))

            # RULE A — a replay of a REFUNDED transaction. An entitling payload Apple signed at or
            # before the recorded refund (or with no signedDate) is older news, whichever lineage
            # or row it reaches: the saved pre-refund JWS of a lineage never bound to this
            # account included (its REFUND webhook was recorded with no user).
            if (status in _ENTITLING_STATUSES and _register_refunded(reg)
                    and (txn_signed_at is None or revoked_at is None or txn_signed_at <= revoked_at)):
                # The row still ENTITLES from this very transaction: the refund never reached it
                # (it raced the purchase's first verify and that verify's check failed, or the
                # refund's own write failed after it was recorded). Revoke it now, as Apple's REFUND
                # would have, so any later relay or retry heals the account (fix-pass review).
                if (prior is not None
                        and str(prior.get("original_transaction_id") or "") == original_txn_id
                        and str(prior.get("status") or "").lower() in _ENTITLING_STATUSES
                        and (prior.get("transaction_id") == transaction_id
                             or (not prior.get("transaction_id")
                                 and _parse_iso(prior.get("current_period_end")) == period_end))):
                    logger.error(
                        "IAP: REFUNDED txn=%s original=%s user=%s still entitles its row — revoking "
                        "it (refund signed %s)", transaction_id, original_txn_id, user_id, revoked_at,
                    )
                    return self.apply_transaction(
                        user_id, payload, status_override="revoked", event_at=revoked_at,
                        client_submitted=False, notification_type="REFUND",
                    )
                logger.warning(
                    "IAP: IGNORING a replay of REFUNDED txn=%s original=%s user=%s source=%s "
                    "(signedDate=%s, refund recorded=%s)",
                    transaction_id, original_txn_id, user_id,
                    "client" if client_path else "notification",
                    txn_signed_at.isoformat() if txn_signed_at else None, revoked_at,
                )
                return _unapplied("refunded_replay")
            # RULE A′ — a refund from BEFORE 198 is not in the register, but migration 196 marked
            # the credit period it clawed back. The same refusal, keyed on that period.
            if reg is None and status in _ENTITLING_STATUSES and period_end is not None:
                clawed = self._credit_period(original_txn_id, period_end)
                clawed_at = _parse_iso((clawed or {}).get("revoked_at"))
                if clawed_at is not None and (txn_signed_at is None or txn_signed_at <= clawed_at):
                    logger.warning(
                        "IAP: IGNORING a replay of txn=%s original=%s user=%s — its credit period "
                        "(end %s) was refunded and clawed back at %s",
                        transaction_id, original_txn_id, user_id, period_end.isoformat(),
                        clawed_at.isoformat(),
                    )
                    return _unapplied("refunded_replay")
            # RULE A″ (review #7/#23 and the fix-pass review, 2026-10-15) — history from BEFORE the
            # register. A CLIENT relay of a transaction the register has no record of, signed
            # before the register's first record, may be a lineage the owner fallback displaced
            # from the account's one row before 198 and refunded then (a refund before 196 left no
            # credit period). Before 198 it was ordered against the account's row whatever its
            # lineage; it still is, against each of its rows — whether or not its lineage has a row
            # by now (the client can create one by relaying an older, expired JWS of it first). It
            # refuses such a replay only when some row of the account holds a key at or after the
            # JWS's signedDate. A JWS signed after the register began cannot hide an unrecorded
            # refund, so once the register holds its first record a genuine purchase on a new Apple
            # ID never pays legacy's cost (§9b.3).
            if client_path and reg is None and status in _ENTITLING_STATUSES:
                since = self._register_since()
                if since is None or client_signed_at is None or client_signed_at < since:
                    for owned in self._account_rows(user_id):
                        displaced = _stale_client_delivery_reason(
                            owned, status=status, signed_at=client_signed_at)
                        if displaced:
                            logger.warning(
                                "IAP: IGNORING stale delivery for txn=%s original=%s user=%s "
                                "source=client (%s, lineage %s) — a transaction the register "
                                "has no record of, signed before the transaction register began, "
                                "is ordered against the account's rows as before 198",
                                transaction_id, original_txn_id, user_id, displaced,
                                owned.get("original_transaction_id"),
                            )
                            return _unapplied("stale")
            # RULE B — the reversal of a recorded refund is compared with THAT refund, not with
            # the row's latest key: an unrelated notification signed after the reversal used to
            # leave it refused as stale for good (gap 6). Decided on the merged register row, so
            # a fence re-run or Apple's retry decides the same way.
            reversal_bypass = bool(
                revoked_at is not None and not payload_revoked and txn_signed_at is not None
                and txn_signed_at > revoked_at and (ntype == "REFUND_REVERSED" or client_path))

        # An UPGRADED transaction is superseded by the upgrade's own transaction, which carries
        # the entitlement. Applying it wrote the lower tier over the row (demoting a Max
        # subscriber to Pro) or — if it carries a revocation — revoked them outright and
        # clawed back the allocation they paid for. Since 198 Apple's REFUND of it takes back,
        # once, the leftovers the upgrade carried from its period, and its REFUND_REVERSED gives
        # them back (`_lineage_credit_steps`).
        if upgraded:
            logger.warning(
                "IAP: IGNORING superseded (isUpgraded) txn=%s original=%s user=%s product=%s "
                "status=%s source=%s — the upgrade's own transaction carries the entitlement",
                payload.get("transactionId"), original_txn_id, user_id,
                payload.get("productId"), status,
                "client" if client_submitted else "notification",
            )
            return _unapplied("superseded_transaction")

        # RULE D (198): a period this lineage has never recorded is not older news, whatever its
        # signedDate (`_newer_period_reason`).
        newer = _newer_period_reason(
            prior, status=status, payload_revoked=payload_revoked, tier=tier,
            environment=environment, period_end=period_end,
        ) if lineage else None
        # ...and the RECOVERY of a period in grace is not older news than its grace notice
        # (`_grace_recovery_reason`, lineage only, under the same cut below).
        if lineage and not newer:
            newer = _grace_recovery_reason(
                prior, status=status, payload_revoked=payload_revoked,
                original_transaction_id=original_txn_id, period_end=period_end,
            )
        if newer:
            # Only Apple's word from AFTER the register began: a JWS signed before it may be a
            # renewal refunded before 198 that nothing recorded, and the high-water mark cannot
            # cover pre-198 history (a resubscribe or a re-point before 198 moved it). Such a
            # payload keeps the pre-198 ordering (fix-pass review of #17, 2026-10-15).
            since = self._register_since()
            if txn_signed_at is None or since is None or txn_signed_at < since:
                logger.info(
                    "IAP: txn=%s original=%s user=%s — Rule D not applied: signed %s, before the "
                    "transaction register began (%s)", transaction_id, original_txn_id, user_id,
                    txn_signed_at.isoformat() if txn_signed_at else None,
                    since.isoformat() if since else None,
                )
                newer = None
        if reversal_bypass or newer:
            logger.info(
                "IAP: txn=%s original=%s user=%s source=%s skips the signedDate check — %s",
                transaction_id, original_txn_id, user_id,
                "client" if client_path else "notification",
                "it reverses the recorded refund" if reversal_bypass else newer,
            )
            stale = None
        elif client_path:
            stale = _stale_client_delivery_reason(
                prior, status=status, signed_at=client_signed_at
            )
        else:
            stale = _stale_delivery_reason(prior, status=status, event_at=event_at)
        if stale:
            logger.warning(
                "IAP: IGNORING stale delivery for txn=%s user=%s source=%s (%s) — stored "
                "state is newer; tier/status/current_period_end left unchanged",
                original_txn_id, user_id, "client" if client_path else "notification", stale,
            )
            return _unapplied("stale")

        # A DELIVERY ABOUT AN OLDER PERIOD (a refund of last month's renewal, its reversal, a
        # relayed old JWS) says nothing about the one in force: applied, it wrote the old period
        # over the newer row and dropped a paying subscriber to free (see `_older_period_reason`).
        # Nothing is written, not even the ordering key: recording its late signedDate would
        # refuse a genuine renewal signed before it but delivered after it. No
        # `revoke_tier_credits` either — the stored period was not refunded. Since 198 a refund
        # of that older period still claws back ITS credits when it is the credit period in force
        # (after an early renewal, or a resubscribe), by its own key: `_lineage_credit_steps`.
        older = _older_period_reason(
            prior, status=status, status_override=status_override, tier=tier,
            environment=row[_ENVIRONMENT_COLUMN], original_transaction_id=original_txn_id,
            period_end=period_end, notification_type=notification_type,
            # Rule E: for a revocation always; for an ENTITLING higher tier only when the register
            # shows the transaction was refunded at some point (#8's case: the reversal or relay of
            # a refunded Max after a Pro resubscribe). A recorded, never-refunded higher tier is
            # the CURRENT upgrade: refusing it left a paying Max subscriber on Pro, past Restore,
            # once a stale pre-upgrade renewal had overwritten the row (fix-pass review).
            unrecorded=(self._unrecorded_upgrade(reg, original_txn_id, period_end)
                        if lineage and prior is not None
                        and str(prior.get("original_transaction_id") or "") == original_txn_id
                        and _TIER_RANK.get(tier, 0)
                        > _TIER_RANK.get(str(prior.get("tier") or "free").lower(), 0)
                        and (status == "revoked" or (reg or {}).get("revoked_signed_at"))
                        else None),
        )
        if older:
            logger.warning(
                "IAP: IGNORING a delivery about an OLDER period for txn=%s original=%s user=%s "
                "source=%s (%s) — the stored period is newer; tier/status/current_period_end "
                "left unchanged%s",
                payload.get("transactionId"), original_txn_id, user_id,
                "client" if client_path else "notification", older,
                "; its own credit period is clawed back by key if still in force"
                if post["claw"] else ", no credit clawback",
            )
            if (post["reversal"] and status in _ENTITLING_STATUSES
                    and _TIER_RANK.get(tier, 0)
                    > _TIER_RANK.get(str((prior or {}).get("tier") or "free").lower(), 0)
                    and period_end is not None and period_end > datetime.now(timezone.utc)):
                # Apple reversed the refund of a period the lineage has moved on from (a
                # resubscribe since): the newer paid period keeps the row, so the reversed
                # tier's remaining days are NOT reinstated. Support comps them (review #8).
                logger.error(
                    "IAP REFUND_REVERSED tier NOT reinstated — a newer paid period is in force; "
                    "restore by hand: txn=%s original=%s user=%s reversed tier=%s period end=%s, "
                    "stored tier=%s end=%s",
                    transaction_id, original_txn_id, user_id, tier,
                    period_end.isoformat() if period_end else None, (prior or {}).get("tier"),
                    (prior or {}).get("current_period_end"),
                )
            return _unapplied("older_period")

        # A DELIVERY ABOUT THE SAME PERIOD DURING APPLE'S BILLING GRACE PERIOD (a Restore,
        # PRICE_INCREASE, a renewal-preference change, a refund request or its decline) carries
        # that period's past end, so its status is DERIVED "expired": written, it demoted a
        # subscriber Apple still serves. So did the same delivery over an `active` row whose
        # grace notification had not arrived yet (see `_billing_grace_hold_reason`). Nothing is
        # written, exactly as above: no ordering key either, or its late signedDate would refuse
        # a grace notification or BILLING_RECOVERY signed before it but delivered after it.
        grace = _billing_grace_hold_reason(
            prior, status=status, status_override=status_override,
            original_transaction_id=original_txn_id, period_end=period_end,
            notification_type=notification_type, notification_subtype=notification_subtype,
            apple_status=apple_status,
        )
        if grace:
            logger.warning(
                "IAP: HOLDING a derived expiry for txn=%s original=%s user=%s source=%s type=%s/%s "
                "data.status=%s (%s) — it does not end the stored period; "
                "tier/status/current_period_end left unchanged, no ordering key stored",
                payload.get("transactionId"), original_txn_id, user_id,
                "client" if client_path else "notification", notification_type,
                notification_subtype, apple_status, grace,
            )
            return _unapplied("billing_grace")

        # A GRACE NOTIFICATION THAT ARRIVED TOO LATE (Apple's retry after the row was already
        # expired — by a derived expiry outside the hold above, or by the expiry sweep): it
        # brings the tier back as before, but the balance 196/198 had held lapsed when the row
        # expired, and nothing reinstates it. Logged once its write has landed, so the cases can
        # be counted: if they show up, the fix is a keyed lapse reversal in SQL
        # (SYSTEM_DESIGN_GUIDELINES §9b.12).
        stored_end = _parse_iso((prior or {}).get("current_period_end"))
        same_period = bool(
            prior and str(prior.get("original_transaction_id") or "") == original_txn_id
            and period_end is not None and stored_end is not None
            and abs(stored_end - period_end) < _OLDER_PERIOD_MARGIN)
        late_grace = bool(status_override == "grace_period" and same_period
                          and str(prior.get("status") or "").lower() == "expired")

        # THE ORDERING KEY. A notification records its `signedDate`. A revocation seen on the
        # CLIENT path records its own `signedDate`, so an older JWS — or an older notification
        # redelivered late — cannot re-entitle the row even when the REFUND notification never
        # arrives; no other client payload records one.
        #
        # Monotonic on BOTH paths: never lowers a key a later event already stored. A
        # revocation skips the stale check, so it can be signed BEFORE the stored key. The
        # webhook wrote it regardless: the late refund of an OLDER period (signed T3), applied
        # over a row the refund of the current period (T5) had already revoked, rewound the key
        # to T3 — and the saved pre-refund JWS of the current period, signed at T4, then
        # applied: the tier and the refunded period's credits back (2026-10-13).
        incoming_key = event_at if event_at is not None else (
            client_signed_at if client_path and status == "revoked" else None
        )
        # ...except a derived expiry that Apple itself stamped "in billing grace" (`data.status`
        # 4) about the stored period: it applies (past the hold's window), but its signedDate is
        # not stored. Stored, it made Apple's own grace notification or BILLING_RECOVERY —
        # signed earlier, delivered later — stale, so the tier never came back (review,
        # 2026-10-15). Apple says grace as of that signedDate, so those are consistent with it.
        if (incoming_key is not None and not client_path and status_override is None
                and status == "expired" and apple_status == 4 and same_period):
            logger.warning(
                "IAP: txn=%s original=%s user=%s type=%s applies its derived expiry WITHOUT an "
                "ordering key — Apple's data.status=4 says billing grace as of %s",
                payload.get("transactionId"), original_txn_id, user_id, notification_type,
                incoming_key.isoformat(),
            )
            incoming_key = None
        new_key = _next_event_key(prior, incoming_key)
        if new_key is not None:
            row[_EVENT_AT_COLUMN] = new_key

        source = "client" if client_path else "notification"

        if _row_unchanged(prior, row):
            # Nothing to write (see `_row_unchanged`); the tier and the credits still reconcile.
            logger.info(
                "IAP: txn=%s user=%s source=%s changes nothing on the stored row — not "
                "rewriting it", original_txn_id, user_id, source,
            )
        else:
            # SETTLE BEFORE OVERWRITING A REVOKED ROW. Migration 196 claws a refund back only
            # while the row still names the refunded period (same original transaction AND
            # period end as the credit period in force). A write that replaces a revoked row —
            # the late refund of an OLDER period, a new lineage, a resubscribe — landing before
            # the refund's own `ensure_credit_period` ran (it runs AFTER its write, below), or
            # after that call failed (it is best-effort), left the refunded period's credits
            # spendable until it ended. Settle first; it is idempotent. Required: a failure
            # answers 503 rather than overwrite the only record the clawback reads.
            if prior and str(prior.get("status") or "").lower() == "revoked":
                self._settle_credit_period(user_id, original_txn_id)

            if not self._write_subscription(
                prior, row, user_id=user_id, original_txn_id=original_txn_id
            ):
                # Another delivery wrote the row after we read it, so every decision above was
                # made on a stale copy. Nothing of ours landed: read again and decide again.
                attempts = (
                    _FENCE_ATTEMPTS_REVOCATION if status == "revoked" else _FENCE_ATTEMPTS
                )
                if _attempt >= attempts:
                    logger.error(
                        "IAP fence exhausted: txn=%s original=%s user=%s source=%s status=%s — "
                        "the subscriptions row changed under each of %d attempts; answering "
                        "503 so the delivery is retried",
                        payload.get("transactionId"), original_txn_id, user_id, source, status,
                        attempts,
                    )
                    raise IAPError("the subscription changed while it was being applied")
                logger.warning(
                    "IAP fence miss: txn=%s original=%s user=%s source=%s status=%s attempt "
                    "%d/%d — the row changed after it was read; reading it again",
                    payload.get("transactionId"), original_txn_id, user_id, source, status,
                    _attempt, attempts,
                )
                _fence_backoff(_attempt)
                return self.apply_transaction(
                    user_id, payload, status_override=status_override, event_at=event_at,
                    client_submitted=client_submitted, notification_type=notification_type,
                    notification_subtype=notification_subtype, apple_status=apple_status,
                    _attempt=_attempt + 1,
                )
            if late_grace:
                logger.warning(
                    "IAP: billing grace arrived AFTER the row was expired: txn=%s original=%s "
                    "user=%s period end=%s — the tier comes back, the held balance already "
                    "lapsed (no reinstatement; a billing recovery grants a fresh allocation)",
                    payload.get("transactionId"), original_txn_id, user_id,
                    period_end.isoformat() if period_end else None,
                )

        # 198: this transaction is now RECORDED on its lineage row (Rule E reads it) — only once
        # the row holds it: recorded before a write that then failed (a 503) or missed the fence,
        # Apple's retry of the same refund read it as recorded and refused it as an older period
        # (review #6, 2026-10-15). BEST-EFFORT, unlike a refund or a reversal: only Rule E reads
        # it, and that falls back to "was its period granted?", so failing a purchase Apple has
        # charged over it is wrong.
        if lineage and transaction_id:
            try:
                applied = self._record_transaction(
                    "applied", environment=environment, transaction_id=transaction_id,
                    original_txn_id=original_txn_id, user_id=user_id, tier=tier,
                    period_end=period_end, revocation_date=None, signed_at=txn_signed_at,
                )
            except IAPError as e:
                logger.warning(
                    "IAP: txn=%s original=%s user=%s applied WITHOUT its register record (%s) — "
                    "Rule E falls back to the granted credit period for it",
                    transaction_id, original_txn_id, user_id, e,
                )
                # The race check below must not be skipped silently: read the register instead,
                # and answer 503 (the row write is idempotent, so the retry is safe) when that
                # fails too.
                applied = self._read_register(environment, transaction_id)
            # A refund recorded between this delivery's register read and its write (an
            # unattributed REFUND racing the purchase's first verify, review #12): Rule A on the
            # merged record. The row just written entitles a refunded transaction, so it is
            # revoked now, as Apple's REFUND would have done.
            refunded_at = _parse_iso((applied or {}).get("revoked_signed_at"))
            if (status in _ENTITLING_STATUSES and _register_refunded(applied)
                    and (txn_signed_at is None or refunded_at is None
                         or txn_signed_at <= refunded_at)):
                logger.error(
                    "IAP: txn=%s original=%s user=%s was REFUNDED while it was being applied "
                    "(refund signed %s) — revoking the row it just wrote",
                    transaction_id, original_txn_id, user_id, refunded_at,
                )
                return self.apply_transaction(
                    user_id, payload, status_override="revoked", event_at=refunded_at,
                    client_submitted=False, notification_type="REFUND",
                )

        winning_tier = self.reconcile_user_tier(user_id)

        # A REFUND must take the unspent credits back, or a refunded Max subscriber keeps 4000
        # credits, ~200 AI reports, having paid nothing. Since migration 196 the
        # `ensure_credit_period` inside `reconcile_user_tier` claws back a refunded paid period
        # itself (the row's transaction is the one the balance was granted from); this call is
        # the backstop for a database without 196 or a balance already in calendar mode, and
        # is a no-op after it.
        #
        # Gated on `winning_tier == "free"` so a user who holds a SECOND, still-active
        # subscription is not stripped because an old one was refunded.
        #
        # Since 198 the KEYED clawback replaces it, ungated: it takes back exactly the period the
        # refund paid for (a no-op once ensure_credit_period did), so a refund of lineage A's
        # period claws back even while lineage B entitles — and B's period then starts.
        if lineage:
            _lineage_credit_steps()
            # A Sandbox balance (TestFlight, App Review) is calendar-sized by users.tier and never
            # in a paid period, so the keyed clawback finds nothing there: it keeps its pre-198
            # refund behaviour, as it keeps its pre-196 sizing.
            if status == "revoked" and winning_tier == "free" and environment != "Production":
                self._revoke_tier_credits(user_id, original_txn_id)
        elif status == "revoked" and winning_tier == "free":
            self._revoke_tier_credits(user_id, original_txn_id)
        return {
            "tier": tier,
            "status": status,
            "winning_tier": winning_tier,
            "original_transaction_id": original_txn_id,
            "current_period_end": row["current_period_end"],
            "was_replay": txn_replay,
            "was_stale": False,
        }

    # ── The fenced subscriptions write ────────────────────────────────────────

    def _fenced_update(self, prior: Dict[str, Any], values: Dict[str, Any]) -> bool:
        """UPDATE the row `prior` was read as, only if nobody has written it since. True when
        it landed; False when it matched no row (another writer moved `updated_at`, or the row
        is gone).

        Fenced on the OBSERVED `updated_at`, passed back exactly as the database returned it,
        never re-serialized. Every writer sets it (`apply_transaction`, and the expiry sweep,
        whose own update re-checks its selection instead; the column is NOT NULL and no trigger
        sets it — 198's maintains only max_period_end), so any write between our read and this one
        changes it. Each PostgREST call is its own transaction, so no lock can span
        the read and the write; this compare-and-set is what orders them.

        A row with no `updated_at` (never in production) is written unfenced, with a WARNING:
        a filter on a missing value goes out as `eq.None`, which Postgres rejects for every row.
        """
        query = self.supabase.table("subscriptions").update(values).eq("id", prior["id"])
        observed = prior.get("updated_at")
        if observed is None:
            logger.warning(
                "IAP: subscriptions row %s has no updated_at — writing it UNFENCED",
                prior.get("id"),
            )
        else:
            query = query.eq("updated_at", observed)
        return bool(query.execute().data)

    def _write_subscription(
        self,
        prior: Optional[Dict[str, Any]],
        row: Dict[str, Any],
        *,
        user_id: str,
        original_txn_id: str,
    ) -> bool:
        """Write `row` — an UPDATE fenced on `prior`, or an INSERT — and say whether it landed.

        False means ANOTHER WRITER GOT THERE FIRST and nothing of ours landed: the fenced update
        matched no row, or the insert collided on a unique key — `subscriptions_user_id_key`
        before migration 198 (two first deliveries for one account, e.g. the
        `Transaction.updates` listener and the unfinished-transaction drain at a cold launch),
        `subscriptions_original_transaction_id_key` since (two first deliveries of one lineage).
        The caller reads again and decides again.

        Migrations 114 (`last_event_at`) and 196 (`environment`) add optional columns, and
        migrations here are applied BY HAND — so a deploy can legitimately run this code against
        a database that does not yet have them. Failing the whole apply there would reject a
        purchase Apple has ALREADY charged for, to protect an ordering guard and a credit rule
        that degrade safely without them (a NULL environment counts as Production). So a
        MISSING-COLUMN error, and only that (classified by `.code`), retries once without them,
        under the same fence. Anything else raises: retrying a transient error without the
        columns stored a revocation with no ordering key, and a Sandbox purchase as Production.
        """
        def _once(values: Dict[str, Any]) -> bool:
            if prior:
                return self._fenced_update(prior, values)
            try:
                self.supabase.table("subscriptions").insert(values).execute()
            except Exception as e:
                if getattr(e, "code", None) == _UNIQUE_VIOLATION:
                    return False
                raise
            return True

        try:
            return _once(row)
        except Exception as e:
            optional = [c for c in _OPTIONAL_SUBSCRIPTION_COLUMNS if c in row]
            if not optional or getattr(e, "code", None) not in _MISSING_COLUMN_CODES:
                logger.error(
                    "IAP: subscriptions write failed for txn=%s user=%s: %s: %s",
                    original_txn_id, user_id, type(e).__name__, e,
                )
                raise IAPError("could not record the subscription") from e
            logger.warning(
                "IAP: subscriptions write with %s failed (%s: %s) — retrying without them. "
                "Apply migrations 114 (out-of-order notification protection) and 196 "
                "(Production-only paid credit periods).",
                ", ".join(optional), type(e).__name__, e,
            )
            try:
                return _once({k: v for k, v in row.items() if k not in optional})
            except Exception as e2:
                logger.error(
                    "IAP: subscriptions write failed for txn=%s user=%s: %s: %s",
                    original_txn_id, user_id, type(e2).__name__, e2,
                )
                raise IAPError("could not record the subscription") from e2

    def _settle_credit_period(self, user_id: str, original_txn_id: str) -> None:
        """Run `ensure_credit_period` NOW, before a write replaces a REVOKED row (see its call
        in `apply_transaction`). Raises `IAPError` (503, retried) on failure: unlike the
        best-effort call in `reconcile_user_tier`, nothing repeats this one once the row stops
        naming the refunded period."""
        try:
            self.supabase.rpc("ensure_credit_period", {"p_user_id": user_id}).execute()
        except Exception as e:
            logger.error(
                "IAP: could not settle the credit period for user=%s before replacing its "
                "revoked subscriptions row (txn=%s): %s: %s — answering 503 so the delivery is "
                "retried",
                user_id, original_txn_id, type(e).__name__, e,
            )
            raise IAPError("could not settle the credit period") from e

    # ── Migration 198: one row per lineage, the transaction register, the keyed clawback ──

    def _lineage_schema(self) -> bool:
        """Is migration 198 applied (one `subscriptions` row per Apple lineage)?

        Probed on `subscription_transactions`, the table 198 creates in the same transaction as
        the constraint swap. "Present" is cached for good (198 is never rolled back); "absent" is
        re-probed after `_LINEAGE_REPROBE_SECONDS`, so applying 198 takes effect without a
        restart. Any other failure raises `IAPError` (503, retried), like every lookup here:
        guessing "absent" against a 198 database would re-point rows again.
        """
        state = getattr(self, "_lineage", None)
        if state is True:
            return True
        if state is False and (time.monotonic() - getattr(self, "_lineage_probed_at", 0.0)
                               < _LINEAGE_REPROBE_SECONDS):
            return False
        try:
            self.supabase.table(_REGISTER_TABLE).select("id").limit(1).execute()
        except Exception as e:
            if getattr(e, "code", None) in _MISSING_TABLE_CODES:
                if state is None:
                    logger.warning(
                        "IAP: %s is missing (%s) — migration 198 is not applied, so "
                        "subscriptions stays one row per account and no keyed clawback runs",
                        _REGISTER_TABLE, getattr(e, "code", None),
                    )
                self._lineage, self._lineage_probed_at = False, time.monotonic()
                return False
            logger.error(
                "IAP: could not probe %s (%s: %s)", _REGISTER_TABLE, type(e).__name__, e,
            )
            raise IAPError("could not read the subscription schema") from e
        if state is not True:
            logger.info("IAP: migration 198 detected — one subscriptions row per Apple lineage")
        self._lineage = True
        return True

    def _record_transaction(
        self,
        kind: str,
        *,
        environment: str,
        transaction_id: str,
        original_txn_id: str,
        user_id: Optional[str],
        tier: Optional[str],
        period_end: Optional[datetime],
        revocation_date: Optional[datetime],
        signed_at: Optional[datetime],
    ) -> Dict[str, Any]:
        """`record_subscription_transaction` (198): 'applied' / 'revoked' / 'reversed'. Returns
        the merged register row. REQUIRED: a failure raises `IAPError` (503, retried; the RPC
        only moves columns forward, so a retry is idempotent)."""
        params = {
            "p_environment": environment,
            "p_transaction_id": transaction_id,
            "p_original_transaction_id": original_txn_id,
            "p_user_id": user_id,
            "p_tier": tier,
            "p_period_end": period_end.isoformat() if period_end else None,
            "p_revocation_date": revocation_date.isoformat() if revocation_date else None,
            "p_kind": kind,
            "p_signed_at": signed_at.isoformat() if signed_at else None,
        }
        try:
            result = self.supabase.rpc("record_subscription_transaction", params).execute()
        except Exception as e:
            logger.error(
                "IAP: could not record %s for txn=%s original=%s user=%s: %s: %s",
                kind, transaction_id, original_txn_id, user_id, type(e).__name__, e,
            )
            raise IAPError("could not record the transaction") from e
        data = getattr(result, "data", None)
        return data if isinstance(data, dict) else {}

    def _read_register(self, environment: str, transaction_id: str) -> Optional[Dict[str, Any]]:
        try:
            result = (
                self.supabase.table(_REGISTER_TABLE)
                .select("*")
                .eq("environment", environment)
                .eq("transaction_id", transaction_id)
                .limit(1)
                .execute()
            )
        except Exception as e:
            logger.error(
                "IAP: %s lookup failed for txn=%s: %s: %s",
                _REGISTER_TABLE, transaction_id, type(e).__name__, e,
            )
            raise IAPError("could not read the transaction register") from e
        return (result.data or [None])[0]

    def _register_since(self) -> Optional[datetime]:
        """When the transaction register began recording: its first row's `created_at` (198 is
        applied and lineage-mode code has written once), or None while it holds nothing. Cached
        once known: it can only move LATER (a deleted account's rows), and the earliest value is
        the closer bound. Raises `IAPError` on a failed read (Rule A″ must not guess)."""
        cached = getattr(self, "_register_since_at", None)
        if cached is not None:
            return cached
        try:
            result = (
                self.supabase.table(_REGISTER_TABLE)
                .select("created_at")
                .order("created_at")
                .limit(1)
                .execute()
            )
        except Exception as e:
            logger.error(
                "IAP: %s first-record lookup failed: %s: %s",
                _REGISTER_TABLE, type(e).__name__, e,
            )
            raise IAPError("could not read the transaction register") from e
        first = _parse_iso(((result.data or [None])[0] or {}).get("created_at"))
        if first is not None:
            self._register_since_at = first
        return first

    def _account_rows(self, user_id: str) -> list[Dict[str, Any]]:
        """Every `subscriptions` row of the account (one per lineage since 198). Raises
        `IAPError` on a failed read."""
        try:
            result = (
                self.supabase.table("subscriptions")
                .select("*")
                .eq("user_id", user_id)
                .execute()
            )
        except Exception as e:
            logger.error(
                "IAP: subscriptions lookup failed for user=%s: %s: %s",
                user_id, type(e).__name__, e,
            )
            raise IAPError("could not read existing subscription") from e
        return list(result.data or [])

    def _credit_period(
        self, original_txn_id: str, period_end: Optional[datetime]
    ) -> Optional[Dict[str, Any]]:
        """The `subscription_credit_periods` row granted for (original transaction, period end),
        or None. Raises `IAPError` on a failed read."""
        if period_end is None:
            return None
        try:
            result = (
                self.supabase.table("subscription_credit_periods")
                .select("id, user_id, revoked_at, period_end")
                .eq("original_transaction_id", original_txn_id)
                .eq("period_end", period_end.isoformat())
                .limit(1)
                .execute()
            )
        except Exception as e:
            logger.error(
                "IAP: subscription_credit_periods lookup failed for txn=%s end=%s: %s: %s",
                original_txn_id, period_end.isoformat(), type(e).__name__, e,
            )
            raise IAPError("could not read the credit periods") from e
        return (result.data or [None])[0]

    def _revoke_paid_period(
        self,
        user_id: str,
        *,
        environment: str,
        transaction_id: str,
        original_txn_id: str,
        period_end: Optional[datetime],
    ) -> str:
        """198's keyed clawback of the credit period the refunded transaction paid for — or of the
        leftovers an upgrade carried from it. Never touches `subscriptions` or the tier.
        REQUIRED: a failure raises `IAPError` (503); the RPC is idempotent."""
        if period_end is None:
            return "no_period_end"
        try:
            result = self.supabase.rpc("revoke_paid_period", {
                "p_user_id": user_id,
                "p_environment": environment,
                "p_transaction_id": transaction_id,
                "p_original_transaction_id": original_txn_id,
                "p_period_end": period_end.isoformat(),
            }).execute()
        except Exception as e:
            logger.error(
                "IAP: revoke_paid_period FAILED for user=%s txn=%s original=%s end=%s (%s: %s) — "
                "answering 503 so the refund is retried; its credits stay spendable meanwhile",
                user_id, transaction_id, original_txn_id, period_end.isoformat(),
                type(e).__name__, e,
            )
            raise IAPError("could not claw back the refunded period") from e
        data = getattr(result, "data", None)
        outcome = str((data or {}).get("outcome") if isinstance(data, dict) else "unknown")
        if outcome == "still_in_force":
            logger.error(
                "IAP CLAWBACK DID NOT HOLD — restore by hand: user=%s txn=%s original=%s end=%s is "
                "still the credit period in force after revoke_paid_period",
                user_id, transaction_id, original_txn_id, period_end.isoformat(),
            )
        else:
            logger.info(
                "IAP: revoke_paid_period user=%s txn=%s original=%s end=%s → %s%s",
                user_id, transaction_id, original_txn_id, period_end.isoformat(), outcome,
                f" (carry={data.get('carry')})" if isinstance(data, dict) and "carry" in data else "",
            )
        return outcome

    def _reinstate_paid_carry(
        self, user_id: str, *, original_txn_id: str, period_end: Optional[datetime]
    ) -> str:
        """REFUND_REVERSED of a period an upgrade carried leftovers from: gives back what
        `revoke_paid_period` took, once (no-op otherwise). REQUIRED, like the clawback."""
        if period_end is None:
            return "no_period_end"
        try:
            result = self.supabase.rpc("reinstate_paid_carry", {
                "p_user_id": user_id,
                "p_original_transaction_id": original_txn_id,
                "p_period_end": period_end.isoformat(),
            }).execute()
        except Exception as e:
            logger.error(
                "IAP: reinstate_paid_carry FAILED for user=%s original=%s end=%s (%s: %s) — 503",
                user_id, original_txn_id, period_end.isoformat(), type(e).__name__, e,
            )
            raise IAPError("could not reinstate the upgrade carry") from e
        data = getattr(result, "data", None)
        return str((data or {}).get("outcome") if isinstance(data, dict) else "unknown")

    def _unrecorded_upgrade(
        self, reg: Optional[Dict[str, Any]], original_txn_id: str, period_end: Optional[datetime]
    ) -> bool:
        """RULE E (198): was this higher-tier transaction NEVER recorded on its lineage?
        Recorded = its row write landed since 198 (`applied_at`, written only AFTER the write), or
        its period granted credits (history from before 198). Only a never-recorded one can be an
        upgrade the row missed (or that upgrade's refund); a recorded one is an older period — a
        refunded Max's late or duplicate refund (gap 3), or its reversal or relay after a Pro
        resubscribe (review #8), which must not drop the Pro resubscriber to Free."""
        if reg and reg.get("applied_at"):
            return False
        return self._credit_period(original_txn_id, period_end) is None

    def _warn_unreinstated_reversal(
        self, user_id: str, *, transaction_id: str, original_txn_id: str,
        period_end: Optional[datetime], refunded: bool = False,
    ) -> None:
        """D6 (owner, 2026-10-14): a reversed refund whose credit period the keyed path clawed
        back, and which is no longer in force while it still runs, is restored BY HAND (the next
        period already started). Loud, never raised: the delivery itself was handled.

        Only that case (review #25, 2026-10-15): never while the register still holds a refund of
        the transaction (Apple refunded it again after the reversal: nothing to restore), and an
        ERROR only while the balance is in ANOTHER paid period. With no paid period in force the
        period is reinstated by the next credit read or a Restore once its row entitles again — a
        WARNING; a one-off correction there would grant it twice."""
        if refunded:
            return
        try:
            period = self._credit_period(original_txn_id, period_end)
        except IAPError:
            return
        if not period or not period.get("revoked_at") or period_end is None:
            return
        if period_end <= datetime.now(timezone.utc):
            return
        try:
            balance = (
                self.supabase.table("user_credits")
                .select("paid_period_id")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            in_force = ((balance.data or [None])[0] or {}).get("paid_period_id")
        except Exception as e:
            # Not the D6 line: with no paid period in force a hand correction would grant the
            # period twice (fix-pass review, 2026-10-15). Loud, and distinct.
            logger.error(
                "IAP REFUND_REVERSED: could not tell whether another paid period is in force for "
                "user=%s txn=%s original=%s period_end=%s (%s: %s) — the period is still clawed "
                "back; check user_credits.paid_period_id and credit_transactions plan_reinstated "
                "before ANY correction", user_id, transaction_id, original_txn_id,
                period_end.isoformat(), type(e).__name__, e,
            )
            return
        if in_force is None:
            logger.warning(
                "IAP REFUND_REVERSED: user=%s txn=%s original=%s period_end=%s is still clawed "
                "back with NO paid period in force — the next credit read or a Restore Purchases "
                "reinstates it once its subscription row entitles again; do not correct by hand",
                user_id, transaction_id, original_txn_id, period_end.isoformat(),
            )
            return
        if str(in_force) == str(period.get("id")):
            return
        logger.error(
            "IAP REFUND_REVERSED credits NOT reinstated — restore by hand: user=%s txn=%s "
            "original=%s period_end=%s was clawed back and the next paid period has started; Apple "
            "reversed the refund (OWNER_TASKS runbook: never point paid_period_id back without "
            "clearing revoke_requested_at)",
            user_id, transaction_id, original_txn_id, period_end.isoformat(),
        )

    # ── Tier reconciliation ───────────────────────────────────────────────────

    def winning_tier(self, user_id: str) -> str:
        """Best currently-active tier across all of the user's subscription rows.

        Expired and revoked rows are ignored, and so is any row whose period has already
        ended — a stale row must never hold a user on a paid tier, nor demote them.
        """
        try:
            result = (
                self.supabase.table("subscriptions")
                .select("tier, status, current_period_end, original_transaction_id, environment")
                .eq("user_id", user_id)
                .execute()
            )
            rows = result.data or []
        except Exception as e:
            # Fail SAFE for the user: on a read error, leave the tier alone rather than
            # guessing "free" and stripping a paying customer's access.
            logger.error(
                "IAP: could not read subscriptions for user=%s (%s: %s) — tier unchanged",
                user_id, type(e).__name__, e,
            )
            raise IAPError("could not read subscriptions") from e

        now = datetime.now(timezone.utc)
        best = "free"
        entitling_lineages: set = set()
        for row in rows:
            if (row.get("status") or "").lower() not in _ENTITLING_STATUSES:
                continue
            row_status = (row.get("status") or "").lower()
            # `grace_period` and `billing_retry` mean precisely "the paid period HAS ended and
            # the customer is still entitled while Apple retries billing". Applying the
            # period-end filter to them dropped the row a second time and demoted a customer
            # Apple considers active — the exact demotion this method exists to prevent.
            end = row.get("current_period_end")
            if end and row_status not in {"grace_period", "billing_retry"}:
                try:
                    end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=timezone.utc)
                    if end_dt <= now:
                        continue
                except ValueError:
                    # Unparseable date: treat as NOT entitling. Better to under-grant and
                    # have the user contact support than to grant on malformed data.
                    logger.warning(
                        "IAP: unparseable current_period_end %r for user=%s — ignoring row",
                        end, user_id,
                    )
                    continue
            tier = (row.get("tier") or "free").lower()
            if _TIER_RANK.get(tier, 0) > _TIER_RANK.get(best, 0):
                best = tier
            # Paying = PRODUCTION: a Sandbox (TestFlight / App Review) row beside a Production one
            # is a tester, not a second payment (review #29, 2026-10-15).
            if (_TIER_RANK.get(tier, 0) > 0
                    and (row.get("environment") or "Production") == "Production"):
                entitling_lineages.add(str(row.get("original_transaction_id") or "?"))
        if len(entitling_lineages) > 1:
            # Migration 198 (owner, 2026-10-14, "One allocation"): two Apple IDs paying for two
            # subscriptions on one account get the better tier and ONE credit allocation. Support
            # asks the user to cancel the duplicate through Apple (OWNER_TASKS §2.4).
            logger.warning(
                "IAP TWO PAID SUBSCRIPTIONS on one account: user=%s lineages=%s — one allocation "
                "(the better plan); two Production subscriptions entitle at once (two Apple IDs, "
                "or one in billing grace/retry beside a new one)",
                user_id, sorted(entitling_lineages),
            )
        return best

    def comp_tier(self, user_id: str) -> str:
        """The account's complimentary tier FLOOR (`users.comp_tier`, migration 177), or "free".

        Exists for the App Review demo account and TestFlight testers, which sit on a paid tier
        with no subscription behind it. Without a floor, `reconcile_user_tier` re-tiered the
        demo account to whatever the reviewer bought in sandbox and dropped it to Free when
        that sandbox subscription expired (~1 hour) — locking the narration the 2.5.4 answer
        depends on.

        A MISSING COLUMN is "no floor" (warned): migration 177 is applied by hand, so this code
        can run against a database without it, and raising there would fail every purchase to
        protect two test accounts. Any OTHER read failure raises, exactly like `winning_tier`:
        guessing "no floor" on a transient error would demote the demo account, which is the
        bug this exists to prevent. Classified by `.code`, never by message text.
        """
        try:
            result = (
                self.supabase.table("users")
                .select("comp_tier")
                .eq("id", user_id)
                .limit(1)
                .execute()
            )
        except Exception as e:
            if getattr(e, "code", None) in _MISSING_COLUMN_CODES:
                logger.warning(
                    "IAP: users.comp_tier missing for user=%s (%s) — no complimentary floor "
                    "applied. Apply migration 177.",
                    user_id, getattr(e, "code", None),
                )
                return "free"
            logger.error(
                "IAP: could not read users.comp_tier for user=%s (%s: %s) — tier unchanged",
                user_id, type(e).__name__, e,
            )
            raise IAPError("could not read the complimentary tier") from e
        rows = result.data or []
        raw = (rows[0].get("comp_tier") if rows else None) or "free"
        tier = str(raw).lower()
        if tier not in _TIER_RANK:
            logger.warning("IAP: unknown comp_tier %r for user=%s — ignoring it", raw, user_id)
            return "free"
        return tier

    def effective_tier(self, user_id: str) -> str:
        """max(winning subscription tier, complimentary floor) — what `users.tier` should be.

        A floor, never a ceiling: a comp-Pro account that buys Max gets Max, and when that
        subscription lapses it returns to Pro, not Free.
        """
        paid = self.winning_tier(user_id)
        floor = self.comp_tier(user_id)
        return floor if _TIER_RANK.get(floor, 0) > _TIER_RANK.get(paid, 0) else paid

    def _revoke_tier_credits(self, user_id: str, original_txn_id: str = "") -> None:
        """Claw back the UNSPENT portion of a revoked tier's allocation. Best-effort.

        A refund is not a downgrade: the money went back, so the unspent allocation goes too.
        `revoke_tier_credits` floors at `GREATEST(free_allocation, used)` so `remaining`
        (a GENERATED column, `total - used`) can never go negative, and since migration 196 it
        also leaves paid-period mode. `ensure_credit_period` claws back a refunded paid period
        on its own since 196, so this is the backstop and a no-op after it.

        Never raises: the entitlement itself is already recorded and the tier has already
        dropped, so a failure here costs credits, not correctness — and raising would make the
        webhook return non-2xx, which makes Apple redeliver a notification we DID apply.
        """
        try:
            self.supabase.rpc("revoke_tier_credits", {"p_user_id": user_id}).execute()
            logger.info(
                "IAP: revoked unspent credits for user=%s after txn=%s was refunded",
                user_id, original_txn_id,
            )
        except Exception as e:
            logger.error(
                "IAP: entitlement REVOKED for user=%s (txn=%s) but revoke_tier_credits FAILED "
                "(%s: %s) — unless migration 196's ensure_credit_period already clawed the "
                "period back, the user keeps their unspent credits until it ends. If this is "
                "PGRST202/undefined function, migration 114 has not been applied.",
                user_id, original_txn_id, type(e).__name__, e,
            )

    def reconcile_user_tier(self, user_id: str) -> str:
        """Mirror the EFFECTIVE tier onto `users.tier` and refresh the credit period.

        Effective = the winning subscription tier, floored by `users.comp_tier` (migration 177)
        so a complimentary account (App Review's demo account, testers) is never demoted by a
        sandbox purchase or its expiry. See `effective_tier`.

        Since migration 196 `ensure_credit_period` sizes a PAID allocation from the
        `subscriptions` row itself — one allocation per Apple paid period, granted once — and
        reads `users.tier` only for an account with no subscriptions row. The mirror still runs
        first: before 196 that function read `users.tier` for every allocation.

        The mirror is RE-CHECKED after it is written. It is a read-then-write outside the
        subscriptions fence: a reconcile that read the rows before a concurrent REFUND landed,
        and wrote after that refund's own reconcile, left a refunded account on paid features,
        and nothing came back to fix it (the expiry sweep reads only entitling rows). Every
        subscriptions writer reconciles after its own write, so re-reading once ours is written
        converges: whichever reconcile writes last has read after the last row write.
        """
        tier = self.effective_tier(user_id)
        for write in range(1, _MIRROR_WRITES + 1):
            try:
                self.supabase.table("users").update({"tier": tier}).eq("id", user_id).execute()
            except Exception as e:
                logger.error(
                    "IAP: could not mirror tier=%s onto users for user=%s: %s: %s",
                    tier, user_id, type(e).__name__, e,
                )
                raise IAPError("could not update the account tier") from e
            if write == _MIRROR_WRITES:
                break
            # A failed confirmation RAISES, like a failed mirror write: breaking out would keep
            # the very mirror this re-check exists to correct, and nothing else re-reconciles a
            # revoked row. The delivery answers 503 and its retry reconciles again.
            confirmed = self.effective_tier(user_id)
            if confirmed == tier:
                break
            logger.warning(
                "IAP: users.tier=%s for user=%s was decided on subscriptions rows that changed "
                "before it was written (now %s) — mirroring again",
                tier, user_id, confirmed,
            )
            tier = confirmed

        # Roll the period over if it is due, and create the balance row on a first touch.
        # Best-effort: the entitlement itself is already recorded, and the same RPC runs
        # lazily on the next credit read, so a failure here delays credits rather than
        # losing the purchase.
        try:
            self.supabase.rpc("ensure_credit_period", {"p_user_id": user_id}).execute()
        except Exception as e:
            logger.error(
                "IAP: tier=%s applied for user=%s but ensure_credit_period FAILED "
                "(%s: %s) — credits will land on the next read",
                tier, user_id, type(e).__name__, e,
            )

        # THEN `grant_tier_upgrade`. Since migration 196 it just runs `ensure_credit_period`
        # again — the call above already granted any new paid period, so this is a cheap
        # no-op kept for a database that predates 196, where it is what lands an upgrade's
        # credits mid-month (112: without it the buyer sat on an exhausted balance until the
        # 1st). Replays cannot farm credits either way: 196 grants each Apple paid period at
        # most once (subscription_credit_periods), 112 no-ops once the allocation is granted.
        try:
            self.supabase.rpc("grant_tier_upgrade", {"p_user_id": user_id}).execute()
        except Exception as e:
            # Warned, not raised: the entitlement is recorded and the next credit read runs
            # the same reconciliation. Before 196 this IS the difference between a working
            # upgrade and one whose credits wait for the 1st.
            logger.error(
                "IAP: tier=%s applied for user=%s but grant_tier_upgrade FAILED (%s: %s) — "
                "the credits land on the next credit read (or, before migration 196, at the "
                "next monthly reset). If this is PGRST202/undefined function, migration 112 "
                "has not been applied.",
                tier, user_id, type(e).__name__, e,
            )

        return tier

    def sweep_expired_subscriptions(self, limit: int = 500) -> Dict[str, int]:
        """Expire subscription rows whose paid period ended, and re-reconcile those users.

        WHY THIS EXISTS: `reconcile_user_tier` is the only writer of `users.tier`, and it runs
        only when a client `POST /billing/verify` or an App Store Server Notification arrives.
        Nothing re-evaluated entitlement on its own. So a single lost EXPIRED or REFUND
        notification — Apple stops retrying, the webhook URL is an unticked checklist item, a
        deploy eats the delivery window — left the account on the paid tier permanently. Before
        migration 196 that was worse than "keeps access": `ensure_credit_period` read
        `users.tier` at every monthly boundary, so a cancelled Max subscriber kept getting 4000
        fresh credits on the 1st of every month. Since 196 a row left `active` past its period
        end is a HOLD (the balance stays, nothing new is granted), and this sweep is what ends
        it: expired here, the next `ensure_credit_period` lapses the account to the calendar
        allowance. The client only ever reports *purchases*, so nothing else can notice.

        This makes entitlement self-correcting with zero notifications delivered.

        Windows are deliberately generous, because the cost of expiring a PAYING customer early
        is far worse than carrying a lapsed one for another day (`_sweep_windows`):

        * `active` — 24h past `current_period_end`. Apple renews slightly before the boundary,
          and clock skew plus notification latency both push the other way.
        * `grace_period` — the billing grace period (`IAP_BILLING_GRACE_DAYS`, which must equal
          App Store Connect's) + 24 h. `billing_retry` — 60 days, Apple's retry window. These
          statuses mean "the period HAS ended and the customer is still entitled while Apple
          retries", so the period-end filter must not apply to them at the short window; that
          is exactly the demotion `winning_tier` documents avoiding.

        Idempotent: a row already `expired` is not selected, so a re-run is a no-op.
        """
        now = datetime.now(timezone.utc)
        cutoffs = {status: (now - window).isoformat()
                   for status, window in _sweep_windows().items()}

        # ONE read per status, each with its own cutoff IN SQL and oldest-first. One unordered
        # `.limit(limit)` read with the 60-day grace filter applied in Python afterwards let
        # grace/billing-retry rows that are still inside their window fill every page once
        # there are `limit` of them — and the lapsed `active` rows behind them, the leak this
        # sweep exists to close, were then never expired.
        try:
            rows = []
            for status in sorted(cutoffs):
                result = (
                    self.supabase.table("subscriptions")
                    .select("id, user_id, status, current_period_end, tier, "
                            "original_transaction_id, environment")
                    .in_("status", [status])
                    .not_.is_("current_period_end", "null")
                    .lt("current_period_end", cutoffs[status])
                    .order("current_period_end")
                    .limit(limit)
                    .execute()
                )
                rows.extend(result.data or [])
        except Exception as e:
            logger.error(
                "IAP sweep: could not read subscriptions (%s: %s) — no rows expired this pass",
                type(e).__name__, e,
            )
            return {"scanned": 0, "expired": 0, "users_reconciled": 0, "errors": 1}

        # Belt and braces on the SQL cutoffs above (and on a status whose case differs). A
        # status with no window of its own is never expired here.
        stale: list[dict] = []
        for row in rows:
            cutoff = cutoffs.get((row.get("status") or "").lower())
            end = row.get("current_period_end")
            if cutoff and end and str(end) < cutoff:
                stale.append(row)

        expired = 0
        errors = 0
        affected: set[str] = set()
        for row in stale:
            status = (row.get("status") or "").lower()
            try:
                # The UPDATE re-checks what SELECTED the row. It used to match on `id` alone, so
                # a renewal that landed after the read above — a late DID_RENEW moving the
                # period end — was overwritten to `expired`, and a subscriber who had just paid
                # dropped to free until their next renewal or a Restore. Postgres evaluates an
                # UPDATE's WHERE against the newest committed version of the row, so a row that
                # no longer qualifies matches nothing; one still overdue after an unrelated
                # write is expired as before. Only rows the UPDATE returned are counted and
                # reconciled. Its window is the one of the status that SELECTED it: a row that
                # moved to another status meanwhile waits for the next pass.
                result = (
                    self.supabase.table("subscriptions")
                    .update({"status": "expired", "updated_at": now.isoformat()})
                    .eq("id", row["id"])
                    .in_("status", [status])
                    .lt("current_period_end", cutoffs[status])
                    .execute()
                )
                if not result.data:
                    logger.warning(
                        "IAP sweep: subscription id=%s user=%s no longer qualified when it was "
                        "written (renewed or changed after the read) — not expired",
                        row.get("id"), row.get("user_id"),
                    )
                    continue
                expired += 1
                # Every row expired HERE is a notification that never arrived: Apple sends one
                # for each way a period ends, and retries a failed delivery for ~6.5 days
                # (Sandbox: once — filter on env). Logged per row so lost notifications can be
                # counted; repeated Production ones are the trigger for an App Store Server API
                # reconcile, which needs an In-App Purchase key (SYSTEM_DESIGN_GUIDELINES §9b.12).
                logger.warning(
                    "IAP sweep: EXPIRED subscription id=%s user=%s original_txn=%s env=%s "
                    "status=%s tier=%s period end=%s — no %s arrived within its window",
                    row.get("id"), row.get("user_id"), row.get("original_transaction_id"),
                    row.get("environment"), status, row.get("tier"),
                    row.get("current_period_end"), _SWEEP_NEVER_ARRIVED.get(
                        status, "notification about the period's end"),
                )
                if row.get("user_id"):
                    affected.add(str(row["user_id"]))
            except Exception as e:
                errors += 1
                logger.error(
                    "IAP sweep: could not expire subscription id=%s user=%s (%s: %s) — outcome "
                    "unknown, reconciling the user anyway",
                    row.get("id"), row.get("user_id"), type(e).__name__, e,
                )
                # The UPDATE may have COMMITTED with its reply lost. Then no later pass selects
                # the row (it is `expired`), and users.tier kept the paid tier for good.
                # Reconciling reads the rows as they are, so it is right either way.
                if row.get("user_id"):
                    affected.add(str(row["user_id"]))

        reconciled = 0
        for user_id in affected:
            try:
                # Recomputes from the user's REMAINING rows, so someone holding a second,
                # still-valid subscription keeps their tier rather than being dropped to free.
                new_tier = self.reconcile_user_tier(user_id)
                reconciled += 1
                logger.info(
                    "IAP sweep: user=%s reconciled to tier=%s after expiry", user_id, new_tier
                )
            except Exception as e:
                errors += 1
                logger.error(
                    "IAP sweep: expired rows for user=%s but reconcile FAILED (%s: %s) — "
                    "users.tier still shows the lapsed tier and will keep allocating its "
                    "credits until the next sweep",
                    user_id, type(e).__name__, e,
                )

        if expired or errors:
            logger.info(
                "IAP sweep: scanned=%d expired=%d users_reconciled=%d errors=%d",
                len(rows), expired, reconciled, errors,
            )
        return {
            "scanned": len(rows),
            "expired": expired,
            "users_reconciled": reconciled,
            "errors": errors,
        }

    # ── Webhook handling ──────────────────────────────────────────────────────

    def user_id_for_transaction(self, original_transaction_id: str) -> Optional[str]:
        """Find which user a transaction belongs to.

        Server notifications identify the transaction, not our user. The mapping only exists
        because the client's verified purchase created the row first — so a notification for
        an unknown transaction is expected (e.g. it arrived before the client call) and is
        reported rather than guessed at.

        Checks `subscriptions` first so the pre-existing behaviour is byte-identical, then
        `credit_purchases`. Without the second lookup a consumable REFUND resolved to None and
        was dropped as "ignored_unknown_transaction" — the refunded user silently kept every
        credit they bought.

        Returns None ONLY when every lookup succeeded and found nothing. A lookup that FAILS
        raises `IAPError`: it used to return None too, which `apply_notification` cannot tell
        from "the notification beat the verify call", so a PostgREST 520 during a REFUND was
        answered 200 `ignored_unknown_transaction` and Apple — which retries only non-2xx —
        never redelivered it. The refunded subscriber kept the tier and the allocation.
        """
        try:
            result = (
                self.supabase.table("subscriptions")
                .select("user_id")
                .eq("original_transaction_id", original_transaction_id)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            if rows:
                return rows[0]["user_id"]
        except Exception as e:
            logger.error(
                "IAP: subscriptions user lookup failed for txn=%s: %s: %s",
                original_transaction_id, type(e).__name__, e,
            )
            raise IAPError(
                f"subscriptions user lookup failed for txn={original_transaction_id}"
            ) from e

        # Consumables have no `subscriptions` row. Apple may identify the purchase by either
        # id, so try both — for the FIRST purchase of a consumable the two are equal, and for
        # later ones `original_transaction_id` links back to the first.
        for column in ("transaction_id", "original_transaction_id"):
            try:
                packs = (
                    self.supabase.table("credit_purchases")
                    .select("user_id")
                    .eq(column, original_transaction_id)
                    .limit(1)
                    .execute()
                )
                pack_rows = packs.data or []
                if pack_rows:
                    return pack_rows[0]["user_id"]
            except Exception as e:
                logger.error(
                    "IAP: credit_purchases user lookup failed on %s for txn=%s: %s: %s",
                    column, original_transaction_id, type(e).__name__, e,
                )
                raise IAPError(
                    f"credit_purchases user lookup failed for txn={original_transaction_id}"
                ) from e
        return None

    def _lineage_owner(
        self, original_transaction_id: str, transaction: Dict[str, Any]
    ) -> Optional[str]:
        """198: the account an UNBOUND lineage belongs to, for a notification
        `user_id_for_transaction` could not place. Never before 198: with one row per account,
        routing another lineage's notification to its owner re-pointed (and demoted) the row.

          1. `subscription_credit_periods`: a lineage displaced from the one row before 198 was
             granted a period under this account.
          2. Its `appAccountToken` (owner decision 2026-10-14, "Yes"), when it names an existing
             account that holds NO entitling subscription: Apple signs it into the transaction,
             and our app sets it to the buyer's account id at purchase — but the VALUE is chosen
             by the purchasing client and never checked by Apple. So a paid purchase whose verify
             never landed is entitled from the webhook too, while a token can never put a
             stranger's lineage over a paying account (review #11).

        Its row is INSERTed by `apply_transaction`. A failed lookup raises `IAPError` (503).
        """
        try:
            result = (
                self.supabase.table("subscription_credit_periods")
                .select("user_id")
                .eq("original_transaction_id", original_transaction_id)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            if rows:
                return rows[0]["user_id"]
        except Exception as e:
            logger.error(
                "IAP: subscription_credit_periods user lookup failed for txn=%s: %s: %s",
                original_transaction_id, type(e).__name__, e,
            )
            raise IAPError("credit period user lookup failed") from e

        owner = _uuid_or_none(transaction.get("appAccountToken"))
        if owner is None:
            return None
        try:
            found = self.supabase.table("users").select("id").eq("id", owner).limit(1).execute()
        except Exception as e:
            logger.error(
                "IAP: users lookup for appAccountToken failed (txn=%s): %s: %s",
                original_transaction_id, type(e).__name__, e,
            )
            raise IAPError("account lookup failed") from e
        if not (found.data or []):
            logger.warning(
                "IAP webhook for unbound txn=%s: appAccountToken %s names no account",
                original_transaction_id, owner,
            )
            return None
        # The token is chosen by the PURCHASING client (Apple signs it but never checks it), so
        # on its own it may only entitle an account that has no subscription to lose: bound onto
        # a paying account, a stranger's Max would start over its period and its refund would
        # claw the lot back (review #11, 2026-10-15). Such an account's own verify binds it — it
        # runs `_assert_app_account_token_matches` under the buyer's bearer.
        entitling = [r for r in self._account_rows(owner)
                     if str(r.get("status") or "").lower() in _ENTITLING_STATUSES]
        if entitling:
            logger.warning(
                "IAP webhook for unbound txn=%s: appAccountToken names user=%s, who already holds "
                "an entitling subscription (lineage %s) — NOT bound by the token alone; that "
                "account's own verify binds it",
                original_transaction_id, owner, entitling[0].get("original_transaction_id"),
            )
            return None
        logger.info(
            "IAP webhook for unbound txn=%s bound to user=%s by its appAccountToken",
            original_transaction_id, owner,
        )
        return owner

    def _record_unattributed_refund(
        self,
        notification_type: str,
        notification: Dict[str, Any],
        transaction: Dict[str, Any],
        original_txn_id: str,
    ) -> str:
        """198: a REFUND / REVOKE / REFUND_REVERSED of a lineage no account can be found for.
        It used to be answered 200 `ignored_unknown_transaction` and forgotten, so the saved
        pre-refund JWS of that lineage still granted the tier and a fresh allocation later
        (design review: a verify blocked at purchase, then refunded). Recorded with no owner, it
        makes Rule A refuse that replay. A failed write raises `IAPError` (503, Apple retries)."""
        transaction_id = str(transaction.get("transactionId") or original_txn_id)
        signed_at = (_ms_to_dt(transaction.get("signedDate"))
                     or _ms_to_dt(notification.get("signedDate")))
        kind = "reversed" if notification_type.upper() == "REFUND_REVERSED" else "revoked"
        if kind == "reversed" and signed_at is None:
            logger.error(
                "IAP webhook REFUND_REVERSED for unbound txn=%s carries no signedDate — not "
                "recorded", transaction_id,
            )
            return "ignored_unknown_transaction"
        try:
            tier = tier_for_product(transaction.get("productId"))
        except UnknownProduct:
            tier = None
        self._record_transaction(
            kind,
            environment=str(transaction.get("environment") or settings.IAP_ENVIRONMENT),
            transaction_id=transaction_id, original_txn_id=original_txn_id, user_id=None,
            tier=tier, period_end=_ms_to_dt(transaction.get("expiresDate")),
            revocation_date=_ms_to_dt(transaction.get("revocationDate")), signed_at=signed_at,
        )
        logger.warning(
            "IAP webhook %s for UNBOUND txn=%s original=%s recorded in %s with no account — a "
            "later replay of its pre-refund transaction is refused",
            notification_type, transaction_id, original_txn_id, _REGISTER_TABLE,
        )
        return "recorded_refund_unknown_lineage"

    def _record_refund_tombstone(
        self,
        notification_type: str,
        transaction: Dict[str, Any],
        *,
        transaction_id: str,
        environment: str,
    ) -> str:
        """Record a REVOKED `credit_purchases` row for a pack refunded BEFORE it was granted.

        `revoke_purchased_credits` answers `unknown` and writes nothing when the REFUND lands
        before any grant. The pre-refund JWS then still verifies and carries no revocation, so
        re-POSTing it to `/billing/verify` granted the pack in full — money back AND credits.
        This row occupies the `(environment, transaction_id)` key `add_purchased_credits`
        claims with `ON CONFLICT DO NOTHING`, so that late grant becomes a replay (same
        account) or a conflict (another account) and grants nothing; a redelivered REFUND
        finds `revoked_at` set and answers `already_revoked`.

        The owner comes from `appAccountToken` inside Apple's signed transaction: no other
        source exists yet (`user_id` is NOT NULL and FK-bound). Returns the webhook outcome.
        Raises `IAPError` (→ 503, Apple redelivers) only on a transient failure. Gaps that
        need a schema change, and are logged as errors: no token, a token naming no account,
        and a product with no `credit_packs` row.
        """
        from app.services.credit_service import CreditService

        product_id = str(transaction.get("productId") or "")
        raw_token = transaction.get("appAccountToken")
        owner = _uuid_or_none(raw_token)
        if owner is None:
            logger.error(
                "IAP webhook %s for credit pack txn=%s product=%s arrived BEFORE any grant "
                "and carries no usable appAccountToken (%r) — cannot record a revocation "
                "tombstone (credit_purchases.user_id is NOT NULL); a later client replay of "
                "the pre-refund transaction WOULD be granted",
                notification_type, transaction_id, product_id, raw_token,
            )
            return "credit_pack_unknown"

        try:
            pack = self.credit_pack_for_product(product_id)
        except UnknownProduct as e:
            logger.error(
                "IAP webhook %s for credit pack txn=%s user=%s arrived before any grant, but "
                "the product cannot be priced (%s) — no tombstone recorded",
                notification_type, transaction_id, owner, e,
            )
            return "credit_pack_unknown"
        # Any other IAPError (catalog read failed, misconfigured row) propagates → 503.

        purchased_at = _ms_to_dt(transaction.get("purchaseDate"))
        original = transaction.get("originalTransactionId")
        tombstone = {
            "user_id": owner,
            "transaction_id": transaction_id,
            "environment": environment,
            "original_transaction_id": str(original) if original else None,
            "product_id": product_id,
            "credits": int(pack["credits"]),
            "price_cents": pack.get("price_cents"),
            "app_account_token": owner,
            "purchased_at": purchased_at.isoformat() if purchased_at else None,
            "revoked_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.supabase.table("credit_purchases").insert(tombstone).execute()
        except Exception as e:
            code = str(getattr(e, "code", "") or "")
            if code == _UNIQUE_VIOLATION:
                # A grant landed between the revoke RPC's lookup and this insert. Revoke THAT.
                retried = CreditService().revoke_purchased(
                    transaction_id=transaction_id, environment=environment
                )
                if retried is None:
                    raise IAPError(
                        f"revoke_purchased_credits RPC failed on the retry for "
                        f"txn={transaction_id} — returning 503 so Apple redelivers"
                    ) from e
                logger.warning(
                    "IAP webhook %s for credit pack txn=%s: a grant raced the tombstone — "
                    "revoked the granted row instead (outcome=%s)",
                    notification_type, transaction_id, retried.get("outcome"),
                )
                return f"credit_pack_{retried.get('outcome', 'revoked')}"
            if code == _FOREIGN_KEY_VIOLATION:
                logger.error(
                    "IAP webhook %s for credit pack txn=%s: appAccountToken %s names no "
                    "account — no tombstone recorded",
                    notification_type, transaction_id, owner,
                )
                return "credit_pack_unknown"
            logger.error(
                "IAP webhook %s for credit pack txn=%s user=%s: tombstone insert failed "
                "(%s: %s, code=%s) — returning 503 so Apple redelivers",
                notification_type, transaction_id, owner, type(e).__name__, e, code or None,
            )
            raise IAPError(
                f"could not record the refund tombstone for txn={transaction_id}"
            ) from e

        logger.warning(
            "IAP webhook %s for credit pack txn=%s product=%s landed BEFORE the grant — "
            "recorded a revoked tombstone for user=%s, so a replay of the pre-refund "
            "transaction cannot be granted",
            notification_type, transaction_id, product_id, owner,
        )
        return "credit_pack_revoked_before_grant"

    def _apply_consumable_notification(
        self,
        notification_type: str,
        subtype: str,
        transaction: Dict[str, Any],
        original_txn_id: str,
    ) -> Tuple[str, Optional[str]]:
        """Server-notification handling for a CREDIT PACK.

        Only refunds matter. A consumable has no renewal, no expiry and no grace period, and
        the purchase itself already landed through `POST /billing/verify` — the webhook is
        purely the channel through which Apple tells us money went back.

        Answers an outcome (→ 200) on every path EXCEPT a transient failure to record a
        refund — the revoke RPC or the tombstone write — which raises `IAPError` (→ 503) so
        Apple redelivers. Both writes are idempotent, so a redelivery is safe.
        """
        from app.services.credit_service import CreditService

        # The per-purchase id is the dedup/revoke key for a consumable, NOT the original.
        transaction_id = str(transaction.get("transactionId") or original_txn_id)
        environment = str(transaction.get("environment") or settings.IAP_ENVIRONMENT)
        # For logs and the webhook's response only — the revoke itself is keyed on the
        # transaction id — so a failed lookup degrades to "unknown user" rather than a 503.
        try:
            user_id = self.user_id_for_transaction(transaction_id) or \
                self.user_id_for_transaction(original_txn_id)
        except IAPError as e:
            logger.warning(
                "IAP webhook %s for credit pack txn=%s: user lookup failed (%s) — continuing "
                "without a user id; the revoke is keyed on the transaction id",
                notification_type, transaction_id, e,
            )
            user_id = None

        ntype = (notification_type or "").upper()

        # Reuses the same notification types `_status_for_notification` maps to "revoked", so
        # the two cannot drift apart.
        if ntype in {"REFUND", "REVOKE"}:
            outcome = CreditService().revoke_purchased(
                transaction_id=transaction_id, environment=environment
            )
            if outcome is None:
                # The RPC round-trip FAILED (its own outcomes are invalid/unknown/
                # already_revoked/revoked, so `None` is strictly a DB fault). Apple only
                # retries non-2xx, so answering 200 here consumed the REFUND notification
                # permanently and the refunded user kept up to 1,300 never-expiring credits —
                # `revoke_purchased` has exactly one caller and nothing sweeps
                # `credit_purchases`, so there was no repair path at all.
                #
                # Raising is safe precisely because the revoke is idempotent: `revoked_at` is
                # the tombstone, so a redelivery of an already-applied revoke short-circuits.
                raise IAPError(
                    f"revoke_purchased_credits RPC failed for txn={transaction_id} — "
                    "returning 503 so Apple redelivers"
                )
            if outcome.get("outcome") == "unknown":
                # The refund beat the grant: there is no `credit_purchases` row to revoke.
                return self._record_refund_tombstone(
                    notification_type, transaction,
                    transaction_id=transaction_id, environment=environment,
                ), user_id
            return f"credit_pack_{outcome.get('outcome', 'revoked')}", user_id

        if ntype == "CONSUMPTION_REQUEST":
            # Apple wants consumption data within 12 hours to adjudicate a refund request.
            # Answering requires PUT /inApps/v1/transactions/consumption/{id} on the App Store
            # Server API, which needs an ASC API key and a signed JWT — this repo has neither
            # (app_store.py is verification-only). Consequence: Apple decides these refunds
            # without our input and approves more of them. Logged distinctly so the exposure
            # is measurable before deciding to build that client. NEVER answer non-2xx.
            logger.warning(
                "IAP webhook CONSUMPTION_REQUEST for credit pack txn=%s user=%s — not "
                "answered (no App Store Server API client); Apple will adjudicate this "
                "refund without our consumption data",
                transaction_id, user_id,
            )
            return "credit_pack_consumption_request_unanswered", user_id

        if ntype == "REFUND_REVERSED":
            # Apple reversed a refund it had granted. The credits were clawed back by the
            # REFUND above and are NOT restored here: re-granting means re-running the grant
            # against a purchase row whose `revoked_at` is set, which `add_purchased_credits`
            # would report as a replay rather than re-crediting. Rare enough to handle by
            # hand; logged loudly so it is not silent.
            logger.error(
                "IAP webhook REFUND_REVERSED for credit pack txn=%s user=%s — credits were "
                "clawed back and are NOT automatically restored; grant them manually",
                transaction_id, user_id,
            )
            return "credit_pack_refund_reversed_unhandled", user_id

        # ONE_TIME_CHARGE, REFUND_DECLINED, and anything else. The purchase path is the
        # client's verify call plus StoreKit's redelivery of unfinished transactions, so there
        # is nothing to do here.
        logger.info(
            "IAP webhook %s/%s for credit pack txn=%s — no action needed",
            notification_type, subtype, transaction_id,
        )
        return f"credit_pack_ignored:{notification_type}", user_id

    def apply_notification(
        self, notification: Dict[str, Any], transaction: Optional[Dict[str, Any]]
    ) -> Tuple[str, Optional[str]]:
        """Apply a verified App Store Server Notification.

        Returns `(outcome, user_id)` where outcome describes what was done, for the webhook's
        response and logs. Never raises for an unknown transaction: Apple retries on
        non-2xx, and retrying forever on a notification we can't map helps nobody.
        """
        notification_type = str(notification.get("notificationType") or "")
        subtype = str(notification.get("subtype") or "")

        if not transaction:
            logger.info(
                "IAP webhook %s/%s carried no transaction — nothing to apply",
                notification_type, subtype,
            )
            return "ignored_no_transaction", None

        original_txn_id = str(
            transaction.get("originalTransactionId") or transaction.get("transactionId") or ""
        )
        if not original_txn_id:
            return "ignored_no_transaction_id", None

        # Consumables branch BEFORE the user lookup: a credit pack has no `subscriptions` row
        # and no lifecycle, so routing it through the subscription machinery would write a
        # bogus entitlement row. Everything here answers 200 — Apple retries non-2xx for days,
        # and a notification we have decided to ignore must not generate retries forever.
        if is_credit_pack_product(transaction.get("productId")):
            return self._apply_consumable_notification(
                notification_type, subtype, transaction, original_txn_id
            )

        user_id = self.user_id_for_transaction(original_txn_id)
        lineage = self._lineage_schema()
        if not user_id and lineage:
            user_id = self._lineage_owner(original_txn_id, transaction)
        if not user_id:
            ntype = notification_type.upper()
            if lineage and ntype in {"REFUND", "REVOKE", "REFUND_REVERSED"}:
                recorded = self._record_unattributed_refund(
                    notification_type, notification, transaction, original_txn_id)
                # The purchase's first verify may have INSERTed its row since the lookup above
                # (review #12, 2026-10-15): answered 200 here, the refund was lost and the row
                # stayed entitled. Looked up again AFTER the record, so either this sees that row,
                # or the verify's own 'applied' record sees this refund and revokes what it wrote.
                user_id = self.user_id_for_transaction(original_txn_id)
                if not user_id:
                    return recorded, None
                logger.warning(
                    "IAP webhook %s for txn=%s raced its purchase's first verify — the row now "
                    "exists; applying it to user=%s",
                    notification_type, original_txn_id, user_id,
                )
            else:
                # Expected race: the notification can beat the client's verify call.
                logger.info(
                    "IAP webhook %s/%s for unknown txn=%s — no user mapping yet",
                    notification_type, subtype, original_txn_id,
                )
                return "ignored_unknown_transaction", None

        status_override = _status_for_notification(notification_type, subtype)
        # Apple's own status for the subscription as of signedDate (verified with the outer
        # JWS). Read only to END a billing-grace hold (`_ends_service_by_itself`).
        apple_status = notification_subscription_status(notification)

        # Apple's `signedDate` — when Apple ASSERTED this, which is the only correct ordering
        # key for out-of-order redeliveries (in production Apple retries a failed delivery 5
        # times, +1, +12, +24, +48 and +72 h after each attempt, about 6.5 days, resending the
        # SAME payload; Sandbox makes one attempt; no order is guaranteed, so a redelivered
        # EXPIRED can land after the DID_RENEW that superseded it). NOT `expiresDate`: that says
        # which period the transaction is about, and an EXPIRED notification is signed late while
        # carrying an early expiresDate.
        event_at = _ms_to_dt(notification.get("signedDate"))

        try:
            result = self.apply_transaction(
                user_id, transaction,
                status_override=status_override,
                event_at=event_at,
                # Apple called us; `user_id` came from our own subscriptions row, not from a
                # bearer token, so there is no cross-account question. Enforcing here would
                # also mean a non-200 that Apple retries for days on a legacy mis-bound row.
                client_submitted=False,
                notification_type=notification_type,
                notification_subtype=subtype,
                apple_status=apple_status,
            )
        except UnknownProduct as e:
            logger.error("IAP webhook: %s", e)
            return "ignored_unknown_product", user_id
        except IAPError as e:
            logger.error(
                "IAP webhook %s/%s failed to apply for user=%s: %s",
                notification_type, subtype, user_id, e,
            )
            raise

        if result.get("lineage_superseded"):
            # Answered 200: a redelivery would find the same replaced row (logged above).
            return "ignored_superseded_lineage", user_id
        if (
            notification_type.upper() == "REFUND_REVERSED"
            and result.get("refused_by") == "stale"
            and str(result.get("status") or "").lower() == "revoked"
            and not result.get("register_refunded")
        ):
            # Known gap (SYSTEM_DESIGN_GUIDELINES §9b.3): the ordering key never moves backwards,
            # so a REFUND that arrived late, after an unrelated notification signed after this
            # reversal, left the row revoked under a key this reversal cannot beat. Answered 200
            # (a redelivery would be refused the same way), so it needs a person. Only the
            # signedDate refusal: a superseded (isUpgraded) or older-period refusal over a row
            # revoked for ANOTHER transaction is handled, not lost (review #13, 2026-10-15). Since
            # 198 it can only be a refund the register does not hold (recorded before 198): Rule B
            # compares a recorded one with this reversal, and Rule A already refuses a reversal
            # that a later recorded refund supersedes — `register_refunded` is a second guard.
            logger.error(
                "IAP REFUND_REVERSED NOT APPLIED — restore by hand: user=%s txn=%s original=%s "
                "is still revoked although Apple reversed the refund (the stored ordering key "
                "is later than this reversal's signedDate). Check App Store Connect, then ask "
                "the user to Restore Purchases or re-apply the transaction",
                user_id, transaction.get("transactionId"), original_txn_id,
            )
            return "ignored_stale_refund_reversal", user_id
        logger.info(
            "IAP webhook applied: type=%s subtype=%s user=%s txn=%s",
            notification_type, subtype, user_id, original_txn_id,
        )
        return f"applied:{notification_type}", user_id


_iap_service: Optional[IAPService] = None


def get_iap_service() -> IAPService:
    """Process-wide singleton (matches the get_supabase() lifecycle)."""
    global _iap_service
    if _iap_service is None:
        _iap_service = IAPService()
    return _iap_service
