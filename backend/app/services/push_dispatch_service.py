"""Push dispatch — deciding WHO gets an alert, and making sure they get it once.

`PushService` knows how to talk to APNs. This layer sits above it and owns the
decisions that determine whether a notification is welcome or an uninstall:

  1. **Who.** An audience selector — reverse lookup from a ticker to the users
     watching it (`idx_watchlist_items_ticker`, migration 108), or an explicit
     list of user ids for the senders that already know their recipients.
  2. **Whether.** The user's own `notify_*` preference AND the group master, synced
     from the app into `user_settings.preferences`. Absent → the kind's declared iOS
     default (`notification_kinds.preference_defaults`).
  3. **How many.** A per-CATEGORY daily ceiling counted in the user's OWN timezone.
  4. **When.** Quiet hours DEFER a notification; they never drop it. The ledger row
     is written either way, so the inbox has it immediately and only the buzz waits.
  5. **Once.** A unique claim in `notification_events` (migration 119) inserted BEFORE
     the send, so a retry, a re-trip of the same scope, or two overlapping Railway
     instances cannot produce a second buzz.

A duplicate cache write is invisible; a duplicate push is a buzz in someone's pocket,
and it cannot be taken back. That asymmetry is why the claim comes first and why
every failure path here declines to send rather than risking a repeat.

WHAT CHANGED IN THE OVERHAUL, AND WHAT DELIBERATELY DID NOT
-----------------------------------------------------------
Unchanged (each of these is a property with a test behind it):
  * the claim is INSERTed before the send, and a failed claim round-trip means DO NOT
    send — if we cannot prove an alert is unsent, we don't send it;
  * the volume cap is checked BEFORE the claim, so a suppressed alert does not burn
    that key's dedup slot and silently cost the user tomorrow's alert too;
  * a preference read failure fails OPEN to the declared default — a DB blip must not
    be indistinguishable from a broken feature;
  * a count read failure fails OPEN — the dedup key still prevents real repeats;
  * one bad recipient never abandons the rest of the fan-out, and this module never
    raises into the sweep that called it.

Changed:
  * the cap is PER CATEGORY, not global. It used to count every row in
    `push_send_log`, so the instant a second kind existed an earnings reminder and a
    price move competed for the same three slots.
  * the day boundary is the USER'S midnight, not ET. "3 per day" resetting at ET
    midnight resets at 2pm in Tokyo, which is not a day.
  * the per-recipient lookups are BATCHED. Four sequential Supabase round-trips per
    recipient × 500 recipients was 2,000 serialized `to_thread` calls for one ticker;
    the new fan-out senders would have multiplied that by ticker count.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

from app.config import settings
from app.database import get_supabase
from app.services import quiet_hours as qh
from app.services.notification_kinds import (
    KIND_TICKER_MOVE,
    NotificationKind,
    category_cap,
    get_kind,
    kind_for_preference_key,
    preference_defaults,
)
from app.services.push_service import PushService

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

TABLE = "notification_events"

# Hard ceiling on how many users one ticker's alert fans out to in a single cycle.
# Each recipient costs a claim INSERT + an APNs POST, and a mega-cap moving on a busy
# day could otherwise stall the sweeper behind thousands of sequential sends. Anything
# skipped is LOGGED, never silently dropped — a truncated fan-out that looked complete
# would read as "push works" while most users got nothing.
MAX_RECIPIENTS_PER_SCOPE = 500

# Upper bound on the daily-count probe. Only the comparison against the cap
# matters, so there is no reason to pull more rows than could change it.
_DAILY_COUNT_PROBE = 50

# How many user ids go into one `.in_(...)` batch. PostgREST puts the filter in the
# URL, so an unbounded IN list eventually 414s.
_BATCH = 200

# Lookback for the per-category count probe. Must span the widest possible gap between
# "now" and some user's local midnight: UTC+14 to UTC-11 is a 25-hour spread, plus a
# DST hour. 48h is comfortably clear and the rows are filtered per user afterwards.
_COUNT_LOOKBACK_HOURS = 48

# Push states (mirrors the CHECK constraint in migration 119).
STATE_PENDING = "pending"
STATE_DEFERRED = "deferred"
STATE_SENT = "sent"
STATE_NO_DEVICE = "no_device"
STATE_DRY_RUN = "dry_run"
STATE_FAILED = "failed"


def trading_date_et() -> str:
    """Today's ET date — the dedup bucket, matching the app's trading-day convention.

    Deliberately NOT the user's timezone. A dedup key answers "is this the same
    market event?", which is a property of the market, not of the reader: two users in
    different timezones watching the same ticker must share one bucket or the alert
    fires twice for the same move.
    """
    return datetime.now(_ET).date().isoformat()


def _chunks(items: Sequence[str], size: int = _BATCH) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


@dataclass
class _Recipient:
    """Everything needed to decide and deliver for one user, fetched in bulk."""

    user_id: str
    preferences: Dict[str, Any] = field(default_factory=dict)
    devices: List[dict] = field(default_factory=list)
    category_sent_today: int = 0
    unread: int = 0
    # False when the bulk read failed for this user, so per-user code can fail OPEN in
    # the same direction the single-user reads always did.
    preferences_known: bool = True


@dataclass(frozen=True)
class Decision:
    """Why a recipient was or wasn't notified. Surfaced by the admin preview endpoint
    and logged, because "the user says they didn't get it" is otherwise unanswerable."""

    user_id: str
    send: bool
    reason: str
    deliver_after: Optional[datetime] = None


class PushDispatchService:
    RETENTION_DAYS = 30

    def __init__(self, push: Optional[PushService] = None):
        self.supabase = get_supabase()
        self._push = push

    @property
    def push(self) -> PushService:
        # Lazy so constructing this service never builds an APNs client it may not use.
        if self._push is None:
            self._push = PushService()
        return self._push

    # ── who ──────────────────────────────────────────────────────────

    def watchers_of(self, ticker: str) -> List[str]:
        """User ids watching `ticker`, capped and de-duplicated.

        Uses the ticker-leading index added in migration 108; before that this was a
        sequential scan of the whole watchlist table.
        """
        try:
            rows = (
                self.supabase.table("watchlist_items")
                .select("user_id")
                .eq("ticker", ticker.upper())
                .limit(MAX_RECIPIENTS_PER_SCOPE + 1)
                .execute()
                .data
                or []
            )
        except Exception as e:
            logger.warning(
                "push: watcher lookup failed for %s (%s: %s) — no alerts this cycle",
                ticker, type(e).__name__, e,
            )
            return []

        users = list(dict.fromkeys(r["user_id"] for r in rows if r.get("user_id")))
        if len(users) > MAX_RECIPIENTS_PER_SCOPE:
            logger.warning(
                "push: %s has %d+ watchers — notifying the first %d only this cycle",
                ticker, len(users), MAX_RECIPIENTS_PER_SCOPE,
            )
            users = users[:MAX_RECIPIENTS_PER_SCOPE]
        return users

    def followers_of_whale(self, whale_id: str) -> List[str]:
        """User ids following a whale. The second audience selector, used by the
        smart-money sender alongside `watchers_of` (a user may qualify via either, and
        the union is de-duplicated before fan-out so they get ONE notification)."""
        try:
            rows = (
                self.supabase.table("whale_follows")
                .select("user_id")
                .eq("whale_id", whale_id)
                .limit(MAX_RECIPIENTS_PER_SCOPE + 1)
                .execute()
                .data
                or []
            )
        except Exception as e:
            logger.warning(
                "push: whale-follower lookup failed for %s (%s: %s) — no alerts this cycle",
                whale_id, type(e).__name__, e,
            )
            return []
        users = list(dict.fromkeys(r["user_id"] for r in rows if r.get("user_id")))
        if len(users) > MAX_RECIPIENTS_PER_SCOPE:
            logger.warning(
                "push: whale %s has %d+ followers — notifying the first %d only",
                whale_id, len(users), MAX_RECIPIENTS_PER_SCOPE,
            )
            users = users[:MAX_RECIPIENTS_PER_SCOPE]
        return users

    # ── whether ──────────────────────────────────────────────────────

    # Per-key default for an ABSENT preference, mirroring each toggle's declared iOS
    # default. A blanket `True` was wrong for exactly two keys, and it was latent rather
    # than harmless: the client's `currentBlob()` omits any key the user has never
    # touched, so a user who never opened Notification Settings has NO row entry for
    # these — and both render as OFF in the UI. The instant a dispatcher is wired up,
    # that user is opted INTO something the app shows as off.
    #
    # Derived from the registry rather than hand-written, so a new kind cannot forget
    # to declare its default. `NotificationsSettingsView.swift`'s `preferenceDefaults`
    # is the iOS mirror; `tests/test_notification_kinds.py` pins them together.
    _PREFERENCE_DEFAULTS = {k: v for k, v in preference_defaults().items() if v is False}

    @staticmethod
    def _coerce_toggle(value: Any, default: bool, *, key: str, user_id: str) -> bool:
        """Read a preference value as a bool WITHOUT truthiness-casting it.

        `bool("false")` is True, so a string-typed toggle — from another client, a
        hand-edited row, or a future client bug — would silently RE-ENABLE a
        notification the user turned off. Nothing between here and the DB enforces the
        value's type.
        """
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in {"false", "0", "no", "off", ""}
        if isinstance(value, (int, float)):
            return value != 0
        logger.warning(
            "push: preference %s for user=%s has unreadable type %s — "
            "falling back to the declared default (%s)",
            key, user_id, type(value).__name__, default,
        )
        return default

    def preference_enabled(self, user_id: str, key: str) -> bool:
        """Whether `user_id` wants this category (single-user read).

        Kept for the single-recipient path and for callers outside a fan-out. The
        batched path uses `_decide` against a pre-fetched blob instead.

        Absent preference → that key's declared iOS default. A FAILED read returns the
        same default: the alternative is silently withholding notifications a user
        asked for because of a transient DB blip, which is indistinguishable from the
        feature being broken.
        """
        default = self._PREFERENCE_DEFAULTS.get(key, True)
        try:
            rows = (
                self.supabase.table("user_settings")
                .select("preferences")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
                .data
                or []
            )
        except Exception as e:
            logger.warning(
                "push: settings read failed for user=%s key=%s (%s: %s) — "
                "falling back to the declared default (%s)",
                user_id, key, type(e).__name__, e, default,
            )
            return default
        if not rows:
            return default
        return self._coerce_toggle(
            (rows[0].get("preferences") or {}).get(key),
            default, key=key, user_id=user_id,
        )

    # ── batched resolve ──────────────────────────────────────────────

    def _preferences_bulk(self, user_ids: Sequence[str]) -> Dict[str, Optional[dict]]:
        """user_id → preferences blob. `None` means the read FAILED for that batch
        (distinct from `{}`, which means the user genuinely has no saved preferences)."""
        out: Dict[str, Optional[dict]] = {}
        for chunk in _chunks(list(user_ids)):
            try:
                rows = (
                    self.supabase.table("user_settings")
                    .select("user_id, preferences")
                    .in_("user_id", list(chunk))
                    .execute()
                    .data
                    or []
                )
            except Exception as e:
                logger.warning(
                    "push: bulk settings read failed for %d user(s) (%s: %s) — "
                    "falling back to declared defaults",
                    len(chunk), type(e).__name__, e,
                )
                for uid in chunk:
                    out[uid] = None
                continue
            by_id = {}
            for r in rows:
                prefs = r.get("preferences")
                # A hand-edited row can hold a string or a list here. Anything that is
                # not a mapping is unusable as a preference blob; treat it as empty so
                # every key falls back to its declared default rather than raising
                # inside the fan-out.
                by_id[r["user_id"]] = prefs if isinstance(prefs, dict) else {}
            for uid in chunk:
                out[uid] = by_id.get(uid, {})
        return out

    def _devices_bulk(self, user_ids: Sequence[str]) -> Dict[str, List[dict]]:
        """user_id → its APNs tokens with their per-token environment.

        The environment matters: a token minted by a DEBUG build is bound to the APNs
        sandbox host, and routing it to production yields BadDeviceToken.
        """
        out: Dict[str, List[dict]] = {uid: [] for uid in user_ids}
        for chunk in _chunks(list(user_ids)):
            try:
                rows = (
                    self.supabase.table("device_tokens")
                    .select("user_id, token, environment")
                    .in_("user_id", list(chunk))
                    .execute()
                    .data
                    or []
                )
            except Exception as e:
                logger.warning(
                    "push: bulk device-token read failed for %d user(s) (%s: %s)",
                    len(chunk), type(e).__name__, e,
                )
                continue
            for r in rows:
                if r.get("token"):
                    out.setdefault(r["user_id"], []).append(
                        {"token": r["token"], "environment": r.get("environment")}
                    )
        return out

    def _category_counts_bulk(
        self,
        user_ids: Sequence[str],
        category: str,
        cutoffs: Dict[str, datetime],
    ) -> Dict[str, int]:
        """How many notifications in `category` each user has been SENT since their own
        local midnight.

        One query per batch over a fixed 48-hour window, then bucketed per user against
        their individual cutoff — the cutoffs differ per user (each is their own
        midnight), so a single `gte` cannot express them all.

        Counts only rows with `sent_at IS NOT NULL`: a deferred, capped or dry-run row
        must not consume the budget it was never delivered against.
        """
        counts: Dict[str, int] = {uid: 0 for uid in user_ids}
        window_start = (
            datetime.now(timezone.utc) - timedelta(hours=_COUNT_LOOKBACK_HOURS)
        ).isoformat()
        for chunk in _chunks(list(user_ids)):
            # Bounded, but generously: under-counting would silently OVER-send, which is
            # the direction that costs a user's trust. `_DAILY_COUNT_PROBE` rows per user
            # is far more than any cap.
            limit = len(chunk) * _DAILY_COUNT_PROBE
            try:
                rows = (
                    self.supabase.table(TABLE)
                    .select("user_id, sent_at")
                    .in_("user_id", list(chunk))
                    .eq("category", category)
                    .not_.is_("sent_at", "null")
                    .gte("sent_at", window_start)
                    .limit(limit)
                    .execute()
                    .data
                    or []
                )
            except Exception as e:
                # Fail OPEN (0 = "none yet"). A DB blip silencing someone's alerts is a
                # worse, harder-to-diagnose failure than one extra notification, and the
                # dedup claim still prevents actual repeats.
                logger.warning(
                    "push: bulk %s count read failed for %d user(s) (%s: %s) — not capping",
                    category, len(chunk), type(e).__name__, e,
                )
                continue
            if len(rows) >= limit:
                logger.warning(
                    "push: %s count probe hit its %d-row limit — per-user counts may be "
                    "under-reported this cycle (fails open, so at worst a few extra sends)",
                    category, limit,
                )
            for r in rows:
                uid = r.get("user_id")
                cutoff = cutoffs.get(uid)
                raw = r.get("sent_at")
                if not uid or cutoff is None or not raw:
                    continue
                try:
                    stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                if stamp >= cutoff:
                    counts[uid] = counts.get(uid, 0) + 1
        return counts

    def unread_counts_bulk(self, user_ids: Sequence[str]) -> Dict[str, int]:
        """user_id → unread inbox count, for the APNs badge.

        Server-computed on purpose. Incrementing client-side drifts the moment a
        notification is delivered and never opened, and the badge is the one piece of
        notification state a user sees without launching the app.
        """
        counts: Dict[str, int] = {uid: 0 for uid in user_ids}
        for chunk in _chunks(list(user_ids)):
            try:
                rows = (
                    self.supabase.table(TABLE)
                    .select("user_id")
                    .in_("user_id", list(chunk))
                    .is_("read_at", "null")
                    .limit(len(chunk) * _DAILY_COUNT_PROBE)
                    .execute()
                    .data
                    or []
                )
            except Exception as e:
                logger.warning(
                    "push: bulk unread count failed for %d user(s) (%s: %s) — badge omitted",
                    len(chunk), type(e).__name__, e,
                )
                continue
            for r in rows:
                uid = r.get("user_id")
                if uid:
                    counts[uid] = counts.get(uid, 0) + 1
        return counts

    def resolve_recipients(
        self,
        user_ids: Sequence[str],
        kind: NotificationKind,
        now: datetime,
    ) -> Dict[str, _Recipient]:
        """Three bulk queries instead of four per recipient. See the module docstring."""
        ids = list(dict.fromkeys(user_ids))
        prefs = self._preferences_bulk(ids)

        recipients: Dict[str, _Recipient] = {}
        cutoffs: Dict[str, datetime] = {}
        for uid in ids:
            blob = prefs.get(uid)
            known = blob is not None
            blob = blob or {}
            recipients[uid] = _Recipient(
                user_id=uid, preferences=blob, preferences_known=known
            )
            cutoffs[uid] = qh.local_day_start_utc(now, qh.resolve_timezone(blob))

        cap = category_cap(kind.category, settings.PUSH_MAX_ALERTS_PER_USER_PER_DAY)
        if cap is not None:
            for uid, count in self._category_counts_bulk(ids, kind.category, cutoffs).items():
                if uid in recipients:
                    recipients[uid].category_sent_today = count

        for uid, devices in self._devices_bulk(ids).items():
            if uid in recipients:
                recipients[uid].devices = devices
        for uid, unread in self.unread_counts_bulk(ids).items():
            if uid in recipients:
                recipients[uid].unread = unread

        return recipients

    # ── the decision ─────────────────────────────────────────────────

    def decide(
        self, recipient: _Recipient, kind: NotificationKind, now: datetime
    ) -> Decision:
        """Pure-ish: preference → cap → quiet hours. No I/O, no claim, no send.

        Exposed separately so the admin preview endpoint can answer "who WOULD get
        this, and why not?" without claiming or sending anything. That is where the
        bugs actually live — the audience selector and the materiality gate, not the
        APNs POST.

        ORDER IS LOAD-BEARING. The cap is evaluated before the claim (which happens in
        the caller) so a suppressed alert does not burn the dedup slot: the user should
        still be able to receive that key's alert tomorrow.
        """
        uid = recipient.user_id
        prefs = recipient.preferences

        # 1. The user's own toggle, then the group master. Either being off suppresses:
        #    turning off "Smart Money" must silence all three of its children even
        #    though each keeps its own key.
        for key in (kind.preference_key, kind.master_preference_key):
            if not key:
                continue
            default = self._PREFERENCE_DEFAULTS.get(key, True)
            if not self._coerce_toggle(prefs.get(key), default, key=key, user_id=uid):
                return Decision(uid, False, f"preference_off:{key}")

        # 2. Per-category daily volume, counted in the user's own timezone.
        cap = category_cap(kind.category, settings.PUSH_MAX_ALERTS_PER_USER_PER_DAY)
        if cap is not None and recipient.category_sent_today >= cap:
            return Decision(uid, False, f"cap_reached:{kind.category}:{cap}")

        # 3. Quiet hours DEFER; they never drop. The ledger row is still written, so
        #    the in-app inbox shows it immediately — only the buzz waits.
        if kind.respects_quiet_hours:
            try:
                tz = qh.resolve_timezone(prefs)
                window = qh.resolve_window(prefs)
                if qh.is_within(window, now.astimezone(tz)):
                    wake = qh.next_end_utc(window, now, tz)
                    if wake is not None:
                        return Decision(uid, False, "quiet_hours", deliver_after=wake)
            except Exception as e:
                # Fail to NOT QUIET. A bug in the wraparound math must not silently
                # queue every notification forever; the caps still bound the volume.
                logger.warning(
                    "push: quiet-hours evaluation failed for user=%s (%s: %s) — "
                    "treating as not quiet",
                    uid, type(e).__name__, e,
                )

        return Decision(uid, True, "ok")

    # ── once ─────────────────────────────────────────────────────────

    def claim_send(
        self,
        user_id: str,
        dedup_key: str,
        *,
        kind: Optional[str] = None,
        category: Optional[str] = None,
        title: str = "",
        body: str = "",
        route: Optional[Dict[str, Any]] = None,
        push_state: str = STATE_PENDING,
        deliver_after: Optional[datetime] = None,
    ) -> bool:
        """Claim the right to send. True = you may send; False = already sent.

        The INSERT is the lock: a unique (user_id, dedup_key) means a concurrent or
        repeat attempt conflicts and returns None, with no coordination between
        instances. Claimed BEFORE the send, deliberately — the failure mode of
        claiming first is a rare missed alert, and of sending first is a duplicate.
        Missing one is forgivable; buzzing twice is not.

        A failed claim round-trip returns None (do NOT send) for the same reason.
        """
        row: Dict[str, Any] = {
            "user_id": user_id,
            "dedup_key": dedup_key,
            "kind": kind or "unknown",
            "category": category or "unknown",
            "title": title,
            "body": body,
            "route": route or {},
            "push_state": push_state,
        }
        if deliver_after is not None:
            row["deliver_after"] = deliver_after.isoformat()
        try:
            self.supabase.table(TABLE).insert(row).execute()
            return True
        except Exception as e:
            # Prefer the STRUCTURED code: supabase-py raises `APIError` with
            # `.code == "23505"` (unique_violation) — verified empirically against a
            # live composite-PK insert. Matching on the message text alone would be
            # one wording change away from treating every duplicate as an unknown
            # error, which fails safe (no send) but would silently suppress alerts
            # forever with only a warning to show for it. The substring check stays as
            # a fallback for any client that surfaces the error differently.
            if getattr(e, "code", None) == "23505":
                return False   # already sent — the normal, expected path
            msg = str(e).lower()
            if "duplicate key" in msg or "23505" in msg:
                return False
            logger.warning(
                "push: dedup claim failed for user=%s key=%s (%s: %s) — NOT sending",
                user_id, dedup_key, type(e).__name__, e,
            )
            return False

    def mark_state(
        self,
        user_id: str,
        dedup_key: str,
        state: str,
        *,
        error: Optional[str] = None,
        sent: bool = False,
    ) -> None:
        """Stamp the outcome on a claimed row. Best-effort.

        `sent_at` is what the per-category cap counts, so it is set ONLY on a real
        delivery — a deferred, dry-run or failed row must not consume the budget.

        A failure here is deliberately non-fatal but LOUD: the notification did go out,
        and losing the stamp costs an inbox state and a cap increment, not a duplicate
        (the claim row already exists and blocks a repeat).
        """
        patch: Dict[str, Any] = {"push_state": state}
        if sent:
            patch["sent_at"] = datetime.now(timezone.utc).isoformat()
        if error:
            patch["last_error"] = error[:500]
        try:
            (
                self.supabase.table(TABLE)
                .update(patch)
                .eq("user_id", user_id)
                .eq("dedup_key", dedup_key)
                .execute()
            )
        except Exception as e:
            logger.warning(
                "push: could not stamp state=%s for user=%s key=%s (%s: %s) — "
                "the push itself was unaffected",
                state, user_id, dedup_key, type(e).__name__, e,
            )

    # ── how many already ─────────────────────────────────────────────

    def alerts_sent_today(self, user_id: str, category: Optional[str] = None) -> int:
        """How many alerts this user has already received today (single-user read).

        Counts by `sent_at`, not by parsing the dedup key: the key's shape is the
        caller's business and changes as alert kinds are added, but "when did we buzz
        them" is stable.

        Fails OPEN (returns 0). A DB blip silencing someone's alerts is a worse,
        harder-to-diagnose failure than one extra notification, and the dedup claim
        still prevents actual repeats.
        """
        try:
            start = datetime.now(_ET).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            query = (
                self.supabase.table(TABLE)
                .select("dedup_key")
                .eq("user_id", user_id)
                .not_.is_("sent_at", "null")
                .gte("sent_at", start)
            )
            if category:
                query = query.eq("category", category)
            return len(query.limit(_DAILY_COUNT_PROBE).execute().data or [])
        except Exception as e:
            logger.warning(
                "push: daily-count read failed for user=%s (%s: %s) — not capping",
                user_id, type(e).__name__, e,
            )
            return 0

    # ── send ─────────────────────────────────────────────────────────

    async def _deliver(
        self,
        recipient: _Recipient,
        kind: NotificationKind,
        *,
        title: str,
        body: str,
        dedup_key: str,
        route: Dict[str, Any],
        collapse_id: Optional[str] = None,
    ) -> bool:
        """Send an already-claimed notification and stamp the outcome. Returns
        True only when APNs accepted at least one device."""
        uid = recipient.user_id

        if settings.PUSH_DRY_RUN:
            # The whole pipeline ran; only the APNs POST is replaced. This is what makes
            # every sender verifiable with no Apple configuration and no device — the
            # ledger row is the evidence.
            logger.info(
                "push[DRY RUN] would send kind=%s to user=%s: %r / %r (route=%s)",
                kind.key, uid, title, body, route,
            )
            await asyncio.to_thread(
                self.mark_state, uid, dedup_key, STATE_DRY_RUN, sent=False
            )
            return False

        if not recipient.devices:
            # Not an error: a signed-in user who denied the iOS permission, or has not
            # opened the app since installing, has no token. The INBOX row is still
            # valid and is the whole point of writing it before delivery.
            await asyncio.to_thread(
                self.mark_state, uid, dedup_key, STATE_NO_DEVICE, sent=False
            )
            return False

        accepted = await self.push.send_to_user(
            uid,
            title=title,
            body=body,
            data={**route, "kind": kind.key},
            devices=recipient.devices,
            interruption_level=kind.interruption_level,
            thread_id=kind.thread_id,
            collapse_id=collapse_id,
            category=kind.thread_id,
            badge=recipient.unread + 1,
        )
        if accepted:
            await asyncio.to_thread(
                self.mark_state, uid, dedup_key, STATE_SENT, sent=True
            )
            return True

        await asyncio.to_thread(
            self.mark_state, uid, dedup_key, STATE_FAILED,
            error="APNs accepted no device", sent=False,
        )
        return False

    async def notify_users(
        self,
        user_ids: Sequence[str],
        *,
        kind: str,
        title: str,
        body: str,
        dedup_key: str | Callable[[str], str],
        route: Optional[Dict[str, Any]] = None,
        collapse_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> int:
        """Notify an explicit audience. Returns how many were actually delivered.

        Never raises: a push failure must not break the job that triggered it.

        `dedup_key` may be a callable so a per-user key (a price alert's own id) and a
        shared key (one market event) use the same primitive.

        `route` must contain FLAT SCALARS ONLY — the iOS `AnyCodable` decoder handles
        String/Int/Double/Bool and silently yields "" for anything nested, so a nested
        dict arrives as garbage (.claude/rules/auth.md §3).
        """
        try:
            return await self._notify_users_inner(
                user_ids, kind=kind, title=title, body=body,
                dedup_key=dedup_key, route=route, collapse_id=collapse_id, now=now,
            )
        except Exception as e:
            # This method PROMISES never to raise — it is called from inside sender
            # jobs and the sweeper's generation path, where an escape would mark
            # successful work as failed.
            logger.exception(
                "push: notify_users(kind=%s) raised (%s: %s) — no further alerts this call",
                kind, type(e).__name__, e,
            )
            return 0

    async def _notify_users_inner(
        self,
        user_ids: Sequence[str],
        *,
        kind: str,
        title: str,
        body: str,
        dedup_key: str | Callable[[str], str],
        route: Optional[Dict[str, Any]],
        collapse_id: Optional[str],
        now: Optional[datetime],
    ) -> int:
        nkind = get_kind(kind)
        now = now or datetime.now(timezone.utc)
        route = dict(route or {})

        users = list(dict.fromkeys(u for u in user_ids if u))
        if not users:
            return 0
        if len(users) > MAX_RECIPIENTS_PER_SCOPE:
            logger.warning(
                "push: kind=%s has %d recipients — notifying the first %d only this cycle",
                kind, len(users), MAX_RECIPIENTS_PER_SCOPE,
            )
            users = users[:MAX_RECIPIENTS_PER_SCOPE]

        # DRY RUN still needs `push.enabled` to be irrelevant: the whole point is to
        # exercise the pipeline without APNs configured.
        if not settings.PUSH_DRY_RUN and not self.push.enabled:
            # Expected until the APNs key is configured. Debug, not warning — this
            # would otherwise log on every material move, forever.
            logger.debug("push: APNs not configured — skipping %s alert", kind)
            return 0

        recipients = await asyncio.to_thread(self.resolve_recipients, users, nkind, now)

        sent = 0
        suppressed: Dict[str, int] = {}
        for uid in users:
            recipient = recipients.get(uid) or _Recipient(user_id=uid)
            key = dedup_key(uid) if callable(dedup_key) else dedup_key
            try:
                decision = self.decide(recipient, nkind, now)
                if not decision.send:
                    bucket = decision.reason.split(":", 1)[0]
                    suppressed[bucket] = suppressed.get(bucket, 0) + 1
                    if decision.deliver_after is None:
                        # Preference or cap: no row, no claim. Claiming for someone we
                        # will not message would burn their dedup slot and silently
                        # suppress a LATER alert they did want.
                        continue
                    # Quiet hours: claim and PARK it. The claim first makes the
                    # deferral idempotent — a second trigger of the same event finds
                    # the row already claimed instead of queueing a duplicate.
                    await asyncio.to_thread(
                        self.claim_send, uid, key,
                        kind=nkind.key, category=nkind.category,
                        title=title, body=body, route={**route, "kind": nkind.key},
                        push_state=STATE_DEFERRED, deliver_after=decision.deliver_after,
                    )
                    continue

                claimed = await asyncio.to_thread(
                    self.claim_send, uid, key,
                    kind=nkind.key, category=nkind.category,
                    title=title, body=body, route={**route, "kind": nkind.key},
                    push_state=STATE_PENDING,
                )
                if not claimed:
                    suppressed["duplicate"] = suppressed.get("duplicate", 0) + 1
                    continue

                if await self._deliver(
                    recipient, nkind, title=title, body=body,
                    dedup_key=key, route=route, collapse_id=collapse_id,
                ):
                    sent += 1
            except Exception as e:
                # One bad recipient must not abandon the rest of the fan-out.
                logger.warning(
                    "push: send to user=%s (kind=%s) failed (%s: %s)",
                    uid, kind, type(e).__name__, e,
                )

        if sent or suppressed:
            # Suppression is logged explicitly and by reason — a silent cap reads as
            # "push is broken" when someone asks why they didn't get an alert.
            logger.info(
                "push: kind=%s delivered %d/%d (suppressed: %s)",
                kind, sent, len(users), suppressed or "none",
            )
        return sent

    async def notify_watchers(
        self,
        *,
        ticker: str,
        title: str,
        body: str,
        dedup_key: str,
        preference_key: str = "notify_watchlist_changes",
        data: Optional[Dict[str, Any]] = None,
        kind: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> int:
        """Alert everyone watching `ticker`. Returns how many were actually sent.

        Signature preserved from before the registry existed so the sweeper and its
        tests did not have to change in the same commit. `preference_key` still selects
        the kind (via the registry's reverse index) for any caller that passes one.

        Never raises: a push failure must not break the sweep that triggered it.
        """
        resolved = kind or kind_for_preference_key(preference_key) or KIND_TICKER_MOVE
        try:
            users = await asyncio.to_thread(self.watchers_of, ticker)
        except Exception as e:
            # `watchers_of` handles its own errors today, but this method PROMISES
            # never to raise — and it is called from inside the sweeper's generation
            # path, where an escape would mark a successfully generated card as failed.
            logger.warning(
                "push: watcher lookup raised for %s (%s: %s) — no alerts this cycle",
                ticker, type(e).__name__, e,
            )
            return 0
        if not users:
            return 0

        route = dict(data or {})
        route.setdefault("ticker", ticker.upper())
        return await self.notify_users(
            users, kind=resolved, title=title, body=body,
            dedup_key=dedup_key, route=route,
            # One collapse id per ticker per day: a second alert for the same ticker
            # REPLACES an undelivered first rather than stacking two banners.
            collapse_id=f"{resolved}:{ticker.upper()}:{trading_date_et()}",
            now=now,
        )

    # ── quiet-hours flush ────────────────────────────────────────────

    def _claim_due(self, limit: int) -> List[dict]:
        """Atomically hand this instance a batch of due deferred rows.

        `claim_due_notifications` uses FOR UPDATE SKIP LOCKED (migration 119) so two
        dispatch loops firing in the same second never grab the same row.
        """
        try:
            return (
                self.supabase.rpc(
                    "claim_due_notifications",
                    {
                        "p_now": datetime.now(timezone.utc).isoformat(),
                        "p_limit": limit,
                    },
                ).execute().data
                or []
            )
        except Exception as e:
            logger.warning(
                "push: claim_due_notifications failed (%s: %s) — no flush this cycle",
                type(e).__name__, e,
            )
            return []

    async def flush_deferred(self) -> Dict[str, int]:
        """Deliver notifications whose quiet-hours window has ended.

        Runs 24/7, NOT gated on market hours: a European user's 07:00 quiet-end is
        01:00 ET, when the market sweeper is asleep.

        A row deferred longer than `NOTIFICATION_MAX_DEFER_HOURS` is marked `failed`
        and never sent. A 14-hour-late "AAPL moved 8%" is misinformation, not a
        notification — and the INBOX row survives either way, so the information is
        not lost, only the buzz.
        """
        stats = {"claimed": 0, "sent": 0, "stale": 0, "no_device": 0,
                 "failed": 0, "suppressed": 0}
        rows = await asyncio.to_thread(
            self._claim_due, settings.NOTIFICATION_DISPATCH_BATCH
        )
        if not rows:
            return stats
        stats["claimed"] = len(rows)

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=settings.NOTIFICATION_MAX_DEFER_HOURS)
        user_ids = list(dict.fromkeys(r["user_id"] for r in rows if r.get("user_id")))
        devices = await asyncio.to_thread(self._devices_bulk, user_ids)
        unread = await asyncio.to_thread(self.unread_counts_bulk, user_ids)
        # RE-READ the preferences. The decision that parked these rows was made hours
        # ago, and the two things it depends on can both have moved since:
        #
        #   * the user may have turned the category off during the night, and
        #   * the DAILY CAP was never charged — `sent_at` is stamped only on real
        #     delivery, so five alerts deferred at 22:30 during an after-hours selloff
        #     would ALL fire at 07:00 and blow straight past a 3/day ceiling. That is
        #     the exact "ten notifications in one morning" failure the cap exists to
        #     prevent, arriving through the back door.
        preferences = await asyncio.to_thread(self._preferences_bulk, user_ids)
        # Counted ONCE up front and incremented locally as this batch delivers, so a
        # single flush cannot exceed the ceiling within itself.
        charged: Dict[str, int] = {}

        for row in rows:
            uid, key = row.get("user_id"), row.get("dedup_key")
            if not uid or not key:
                continue
            try:
                claimed_raw = str(row.get("claimed_at") or "").replace("Z", "+00:00")
                claimed_at = datetime.fromisoformat(claimed_raw) if claimed_raw else now
                if claimed_at.tzinfo is None:
                    claimed_at = claimed_at.replace(tzinfo=timezone.utc)
            except ValueError:
                claimed_at = now

            if claimed_at < cutoff:
                stats["stale"] += 1
                await asyncio.to_thread(
                    self.mark_state, uid, key, STATE_FAILED,
                    error="stale: deferred past the max window", sent=False,
                )
                continue

            try:
                nkind = get_kind(row.get("kind") or "")
            except KeyError:
                # A kind removed from the registry while rows were parked. Fail the row
                # loudly rather than guessing a preference key and buzzing an opted-out
                # user.
                stats["failed"] += 1
                await asyncio.to_thread(
                    self.mark_state, uid, key, STATE_FAILED,
                    error=f"unknown kind {row.get('kind')!r}", sent=False,
                )
                continue

            prefs = preferences.get(uid) or {}
            if uid not in charged:
                # The user's own day boundary, not ET — same rule the claim path uses.
                charged[uid] = await asyncio.to_thread(
                    self.alerts_sent_today, uid, nkind.category
                )
            recipient = _Recipient(
                user_id=uid,
                preferences=prefs,
                devices=devices.get(uid, []),
                unread=unread.get(uid, 0),
                category_sent_today=charged[uid],
            )

            # Re-run preference + cap ONLY. Quiet hours are deliberately NOT re-checked:
            # this row is being flushed precisely because its window ended, and asking
            # again would re-defer it forever on a window that spans the flush.
            gate = self.decide(recipient, nkind, now)
            if not gate.send and gate.deliver_after is None:
                stats["suppressed"] = stats.get("suppressed", 0) + 1
                await asyncio.to_thread(
                    self.mark_state, uid, key, STATE_FAILED,
                    error=f"suppressed on flush: {gate.reason}", sent=False,
                )
                continue

            route = row.get("route") if isinstance(row.get("route"), dict) else {}
            try:
                if await self._deliver(
                    recipient, nkind,
                    title=row.get("title") or "",
                    body=row.get("body") or "",
                    dedup_key=key,
                    route=route,
                ):
                    stats["sent"] += 1
                    # Charge it locally so the rest of THIS batch sees the new total.
                    # Re-reading per row would be N queries and would still race the
                    # `mark_state` write that stamps `sent_at`.
                    charged[uid] = charged.get(uid, 0) + 1
                elif not recipient.devices:
                    stats["no_device"] += 1
                else:
                    stats["failed"] += 1
            except Exception as e:
                stats["failed"] += 1
                logger.warning(
                    "push: deferred delivery to user=%s key=%s failed (%s: %s)",
                    uid, key, type(e).__name__, e,
                )

        logger.info("push: quiet-hours flush %s", stats)
        return stats

    # ── housekeeping ─────────────────────────────────────────────────

    def sweep_expired(self) -> int:
        """Drop ledger rows older than the retention window. Best-effort.

        The window has to outlive BOTH the dedup horizon (one trading day today) and
        what a user reasonably expects the in-app inbox to remember. 30 days serves
        both; without a sweep this table grows one row per notification forever.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.RETENTION_DAYS)).isoformat()
        deleted = 0
        for table, column in ((TABLE, "claimed_at"), ("push_send_log", "sent_at")):
            try:
                result = (
                    self.supabase.table(table).delete().lt(column, cutoff).execute()
                )
                n = len(result.data or [])
                deleted += n
                if n:
                    logger.info("%s sweep: deleted %d row(s) older than %s", table, n, cutoff)
            except Exception as e:
                # `push_send_log` is dropped in a later migration; a missing table here
                # is expected after that and must not abort the sweep of the live one.
                logger.warning(
                    "%s sweep failed (cutoff=%s): %s: %s",
                    table, cutoff, type(e).__name__, e,
                )
        return deleted


_service: Optional[PushDispatchService] = None


def get_push_dispatch_service() -> PushDispatchService:
    global _service
    if _service is None:
        _service = PushDispatchService()
    return _service
