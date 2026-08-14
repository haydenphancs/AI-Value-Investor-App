"""Investor-profile store — how a reader prefers to LEARN.

Phase 3 CAPTURES only; nothing renders this into a prompt yet.

⚠️ Scope is a compliance boundary, not a product one. This holds *content*
preferences: experience level, explanation style, answer depth, topics of interest.
It deliberately holds NO finances, risk tolerance, time horizon, tax situation or
goals — the five things `persona_config.ADVICE_BOUNDARY` and the live Terms §2 both
say the app does not know. Collecting them would turn a relevance layer into a
suitability profile. Do not add a risk-tolerance field here.

⚠️ Every value is a CLOSED ENUM, and that is a security property. The rendered
preference block is destined for the chat SYSTEM instruction UNFENCED — fencing it
would tell the model not to be steered by it, which is the opposite of the point — so
it is only safe while no user-authored byte can reach the string. The user taps chips;
the server maps enum → server-authored English. Three independent layers enforce it:
Pydantic `Literal` at the edge, `sanitize_profile()` here, and CHECK constraints in
migration 131.

Structure mirrors `user_settings_service.py`: module-level pure validators (trivially
testable, no I/O) plus a thin service class over the raw Supabase SDK.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.database import get_supabase
from app.utils.supabase_errors import is_unknown_column_error

logger = logging.getLogger(__name__)

TABLE = "user_investor_profile"

# ── The vocabulary. Kept in lockstep with migration 131's CHECK constraints by
# tests/test_investor_profile_validation.py, which parses the .sql file — a drift
# between the two would be a 500 on save (Postgres rejects what Python allowed).
SCALAR_FIELDS: Dict[str, tuple[str, ...]] = {
    "experience_level": ("new", "learning", "experienced"),
    "explanation_style": ("plain_language", "balanced", "technical"),
    "answer_depth": ("brief", "standard", "deep"),
}

ARRAY_FIELDS: Dict[str, tuple[str, ...]] = {
    "topics": (
        "value", "growth", "dividends", "technology", "energy",
        "healthcare", "crypto", "etf_index", "macro", "small_cap",
    ),
    "learning_goals": (
        "read_financials", "value_a_business", "understand_charts",
        "understand_macro", "follow_smart_money",
    ),
    "follow_signals": ("whales", "congress", "insiders", "earnings"),
}

DEFAULTS: Dict[str, Any] = {
    "experience_level": "learning",
    "explanation_style": "balanced",
    "answer_depth": "brief",
    "topics": [],
    "learning_goals": [],
    "follow_signals": [],
}

# A skipped question is a valid answer, so an array may be empty. The ceiling only
# exists so a malicious client cannot post the vocabulary 10,000 times; it is not a
# product limit and is never reachable through the UI.
MAX_ARRAY_LENGTH = 32


class ProfileUnreadable(RuntimeError):
    """The store could not be read. Distinct from 'no profile yet' (which is a valid
    empty state) so the API can answer 503 instead of pretending the user has none —
    the same distinction `user_settings_service.PreferencesUnreadable` draws."""


def sanitize_profile(raw: Any) -> Dict[str, Any]:
    """Coerce arbitrary input to a valid profile dict. NEVER raises.

    Unknown values are DROPPED to their default rather than rejected, deliberately:
    this also runs on the way OUT of the database, where a value could predate a
    vocabulary change, and a read must not fail because of it. The write path rejects
    unknown values loudly at the Pydantic edge before reaching here, so a client typo
    still gets a 422 rather than a silent default.
    """
    out: Dict[str, Any] = dict(DEFAULTS)
    out["topics"] = []
    out["learning_goals"] = []
    out["follow_signals"] = []
    if not isinstance(raw, dict):
        return out

    for field, allowed in SCALAR_FIELDS.items():
        value = raw.get(field)
        if isinstance(value, str) and value.strip().lower() in allowed:
            out[field] = value.strip().lower()

    for field, allowed in ARRAY_FIELDS.items():
        value = raw.get(field)
        if not isinstance(value, (list, tuple)):
            continue
        seen: List[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            item = item.strip().lower()
            # Dedupe preserving order: the order is the user's tap order, and a
            # duplicate would render the same interest twice in the prompt block.
            if item in allowed and item not in seen:
                seen.append(item)
            if len(seen) >= MAX_ARRAY_LENGTH:
                break
        out[field] = seen

    return out


def sanitize_updates(raw: Any) -> Dict[str, Any]:
    """Sanitize only the fields ACTUALLY PRESENT in `raw`. NEVER raises.

    Distinct from `sanitize_profile`, and the distinction is load-bearing:
    `sanitize_profile` fills every absent field with its default, which is right for a
    READ (a missing column should render as the default) and catastrophic for a WRITE.
    The upsert writes whatever columns the payload carries, so passing a defaults-filled
    dict silently CLEARS every field the caller did not mention — a Settings screen
    editing one preference would wipe the other five.

    Caught by an end-to-end round-trip, not by a unit test: with the table absent the
    endpoint answered 503, so nothing ever observed the stored row.
    """
    out: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    clean = sanitize_profile(raw)
    for field in list(SCALAR_FIELDS) + list(ARRAY_FIELDS):
        if field in raw:
            out[field] = clean[field]
    return out


#: Every field a reader can answer. The `answered_fields` vocabulary, and the CHECK in
#: migration 134, are both this list.
ANSWERABLE_FIELDS: tuple[str, ...] = tuple(SCALAR_FIELDS) + tuple(ARRAY_FIELDS)


def answered_fields_in(raw: Any) -> List[str]:
    """Which answerable fields this payload actually SUBMITTED. PURE, never raises.

    Presence, not value. A reader who picks "Still learning" has answered
    `experience_level` even though that equals the column default — and the whole reason
    this exists is that those two cases are indistinguishable in the value columns, so
    the most likely pair of onboarding answers read back as "stated nothing". See
    migration 134.
    """
    if not isinstance(raw, dict):
        return []
    return [f for f in ANSWERABLE_FIELDS if f in raw]


def would_personalize(profile: Any) -> bool:
    """True when these answers would actually change an answer.

    The RENDER question, kept separate from `is_empty_profile`'s "did they state
    anything" question. They are not the same: an at-default scalar renders nothing (see
    `investor_profile_prompt._scalar`), so a reader can have answered every question and
    still produce an empty block. Conflating the two told such a reader they had stated
    nothing — see migration 134.

    Imported lazily: `investor_profile_prompt` imports `DEFAULTS` from this module, so a
    module-level import here would be circular.
    """
    from app.services.agents.investor_profile_prompt import render_profile_block

    return bool(render_profile_block(profile))


def is_empty_profile(profile: Any) -> bool:
    """True when the user has stated nothing.

    'Nothing' means: no field was ever submitted (`answered_fields`) AND every array is
    empty. It deliberately does NOT ask whether the stored values differ from the column
    defaults — that is `would_personalize`, and answering it here is what made a reader
    who chose "Still learning" + "A bit of both" read back as having said nothing at all.

    `answered_fields` is absent on rows written before migration 134, so the array check
    is retained as the fallback signal: an older row with topics is still non-empty.

    Non-dict input answers True rather than raising, matching `sanitize_profile`'s and
    `sanitize_updates`' "never raises" contract — the three sit together and were
    inconsistent about it (`is_empty_profile(None)` raised `AttributeError`, and
    `is_empty_profile({})` answered False because `None != "learning"`).
    """
    if not isinstance(profile, dict):
        return True
    answered = profile.get("answered_fields")
    if isinstance(answered, (list, tuple)) and any(
        f in ANSWERABLE_FIELDS for f in answered if isinstance(f, str)
    ):
        return False

    # FALLBACK — and it is not just for old rows. `answered_fields` is absent until
    # migration 134 is applied, and the code must be safe to deploy in either order, so
    # without this a live row holding `experience_level='experienced'` would read as
    # "stated nothing" for the window between deploy and migrate. This is exactly what the
    # function used to do, so pre-134 behaviour is preserved rather than regressed: infer
    # an answer from content. It can only under-report (a chosen DEFAULT is invisible),
    # which is the bug `answered_fields` exists to fix — not one it introduces.
    if any(profile.get(f) for f in ARRAY_FIELDS):
        return False
    return all(profile.get(f, DEFAULTS[f]) == DEFAULTS[f] for f in SCALAR_FIELDS)


def merge_profiles(account: Any, guest: Any) -> Dict[str, Any]:
    """Fold a guest profile into an account one. PURE — returns only the CHANGED columns.

    Used by `POST /users/me/claim-guest-data`, which previously deleted the guest row
    whenever the account had any row at all. That threw away real answers — and, in the
    `.restoring` window, a real CONSENT grant, which is the compliance artifact the whole
    feature hangs on.

    The rules, and why:
      * **arrays** — the account's list wins when it is non-empty; an empty account list
        takes the guest's. Deliberately not a union: a user who curated their topics on the
        account should not silently regain ones they removed, and the guest row is at most
        first-run onboarding answers.
      * **scalars** — the account wins unless it is still at the column default, in which
        case the guest's explicit answer fills the gap.
      * **`consented_at`** — the EARLIER of the two, because that is when the reader
        actually accepted. A grant on either side is a grant; this is the one field where
        losing the guest's value is a compliance regression rather than a data-loss nit.

    Returns `{}` when the guest row adds nothing, so the caller can skip a pointless write.
    """
    if not isinstance(account, dict) or not isinstance(guest, dict):
        return {}

    changed: Dict[str, Any] = {}
    for field in ARRAY_FIELDS:
        if not account.get(field) and guest.get(field):
            changed[field] = guest[field]
    for field in SCALAR_FIELDS:
        acct = account.get(field, DEFAULTS[field])
        their = guest.get(field)
        if acct == DEFAULTS[field] and their and their != DEFAULTS[field]:
            changed[field] = their

    acct_consent, guest_consent = account.get("consented_at"), guest.get("consented_at")
    if guest_consent and (not acct_consent or str(guest_consent) < str(acct_consent)):
        changed["consented_at"] = guest_consent

    # UNION, and it is not optional. Without it the claim reproduced the exact bug
    # migration 134 exists to fix, one boundary later: a guest who tapped "Still learning"
    # + "A bit of both" stores those as answered with both values equal to the defaults,
    # so every value-based rule above finds nothing to merge, the guest row is deleted,
    # and the account reads `is_empty: true` again — told they had stated nothing, having
    # answered both questions before signing up.
    merged_answered = {
        f for f in (list(account.get("answered_fields") or [])
                    + list(guest.get("answered_fields") or []))
        if isinstance(f, str) and f in ANSWERABLE_FIELDS
    }
    if merged_answered != set(account.get("answered_fields") or []):
        changed["answered_fields"] = sorted(merged_answered)

    return changed


class UserInvestorProfileService:
    """Read/write the profile. One row per identity, keyed by `user_id` (PK)."""

    def __init__(self, supabase=None):
        self.supabase = supabase or get_supabase()

    def _upsert(self, payload: Dict[str, Any]) -> None:
        self.supabase.table(TABLE).upsert(payload, on_conflict="user_id").execute()

    def get_profile(self, user_id: str) -> Dict[str, Any]:
        """Return the stored profile, or all-defaults when there is none.

        Raises `ProfileUnreadable` on a store failure so the endpoint can answer 503
        rather than handing back defaults that look like a deliberate empty profile —
        which the client would then happily overwrite the real row with.
        """
        try:
            res = (
                self.supabase.table(TABLE)
                .select("*").eq("user_id", user_id).limit(1).execute()
            )
        except Exception as e:
            logger.warning(
                "Investor profile read failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e,
            )
            raise ProfileUnreadable(str(e)) from e

        row = (res.data or [None])[0]
        profile = sanitize_profile(row or {})
        profile["profile_version"] = (row or {}).get("profile_version", 1)
        profile["consented_at"] = (row or {}).get("consented_at")
        profile["has_profile"] = bool(row)
        # Filtered to the known vocabulary rather than passed through: this feeds
        # `is_empty_profile`, and a stale value from a future vocabulary change must not
        # make a profile look answered. Absent on rows written before migration 134, and
        # absent entirely until it is applied — `[]` degrades to the array-only check,
        # which is the pre-134 behaviour, so the code is safe to deploy in either order.
        stored_answered = (row or {}).get("answered_fields")
        profile["answered_fields"] = [
            f for f in (stored_answered or [])
            if isinstance(f, str) and f in ANSWERABLE_FIELDS
        ]
        return profile

    def upsert_profile(
        self, user_id: str, raw: Dict[str, Any], *, consent: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Write the profile and return the sanitized result.

        Raises `ProfileUnreadable` on failure: onboarding treats the save as
        best-effort, but the endpoint still needs to know it did not land so it can
        say so rather than echoing back a profile that was never stored.
        """
        # PARTIAL: only the fields the caller actually sent. PostgREST's upsert writes
        # exactly the columns in the payload (INSERT … ON CONFLICT DO UPDATE SET those),
        # so omitting a field leaves the stored value alone on update and takes the
        # column DEFAULT on insert — which is what the API documents.
        payload = sanitize_updates(raw)
        payload["user_id"] = user_id

        # UNION, never replace. A later partial write must not un-answer an earlier one:
        # editing only `topics` in the Settings editor would otherwise erase the fact that
        # `experience_level` was answered during onboarding, and the reader would be told
        # again that they have stated nothing. Read-then-write rather than an RPC, matching
        # this file's raw-SDK style; two truly simultaneous PUTs can lose one side's
        # bookkeeping, which costs a re-ask at worst and never loses a stored VALUE.
        submitted = answered_fields_in(raw)
        if submitted:
            try:
                existing = self.get_profile(user_id).get("answered_fields") or []
            except ProfileUnreadable:
                # A read blip must not block the write. The union degrades to just this
                # payload's fields — a later edit re-adds the rest.
                existing = []
            payload["answered_fields"] = sorted(set(existing) | set(submitted))
        # Matches every other service in the repo (iap_service, price_alert_service,
        # updates_insight_sweeper). PostgREST posts this as JSON, so Postgres casts the
        # STRING to timestamptz — the literal "now()" happens to parse too (verified on
        # PG 17), but an explicit ISO instant is unambiguous and keeps one convention.
        # Set explicitly rather than relying on the column DEFAULT, which fires only on
        # INSERT and would freeze `updated_at` at creation time for every later upsert.
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        # THREE states, not two. `None` = the caller said nothing about consent, so the
        # stored value is left alone (an ordinary preference edit must not silently
        # re-consent anyone). `True` = granted now. `False` = REVOKED, which has to write
        # an explicit NULL — consent the user cannot withdraw is not consent, and a
        # `truthy?` check would make the toggle one-way.
        if consent is True:
            payload["consented_at"] = datetime.now(timezone.utc).isoformat()
        elif consent is False:
            payload["consented_at"] = None
        try:
            self._upsert(payload)
        except Exception as e:
            # ⚠️ DEPLOY-ORDER TOLERANCE, not politeness. PostgREST rejects a payload naming
            # a column absent from its schema cache (PGRST204), and `answered_fields` does
            # not exist until migration 134 is applied. Without this, a deploy that landed
            # BEFORE the migration would fail the ENTIRE profile write — losing the
            # reader's actual answers over a bookkeeping column. Migrations here are
            # applied by hand, so "code first" is a real ordering, not a hypothetical.
            #
            # Retried WITHOUT the new column: the answers are what matter, and
            # `is_empty_profile` still infers correctly from content in the meantime.
            if "answered_fields" in payload and is_unknown_column_error(e):
                logger.warning(
                    "Investor profile write for user=%s rejected `answered_fields` (%s) — "
                    "migration 134 is not applied yet; retrying without it. Apply 134 to "
                    "restore chosen-default tracking.",
                    user_id, type(e).__name__,
                )
                payload.pop("answered_fields", None)
                try:
                    self._upsert(payload)
                except Exception as retry_error:
                    logger.warning(
                        "Investor profile write failed for user=%s (%s: %s)",
                        user_id, type(retry_error).__name__, retry_error,
                    )
                    raise ProfileUnreadable(str(retry_error)) from retry_error
            else:
                logger.warning(
                    "Investor profile write failed for user=%s (%s: %s)",
                    user_id, type(e).__name__, e,
                )
                raise ProfileUnreadable(str(e)) from e

        # The write is COMMITTED. A failure in the read-back below is a read problem, and
        # surfacing it as `ProfileUnreadable` made the endpoint answer 503 — telling the
        # user their consent grant or preference edit failed when the row was durably
        # stored. They then retry, or worse, treat the 503 as "the server has no profile".
        # Reconstruct the response locally instead; it is the same data the read would have
        # returned, minus any concurrent change by another device.
        try:
            return self.get_profile(user_id)
        except ProfileUnreadable as e:
            logger.warning(
                "Investor profile read-back failed for user=%s after a SUCCESSFUL write "
                "(%s: %s) — returning the written values rather than reporting a failure",
                user_id, type(e).__name__, e,
            )
            echoed = sanitize_profile(payload)
            echoed["profile_version"] = 1
            echoed["consented_at"] = payload.get("consented_at")
            echoed["has_profile"] = True
            echoed["answered_fields"] = payload.get("answered_fields") or submitted
            return echoed


_service: Optional[UserInvestorProfileService] = None


def get_user_investor_profile_service() -> UserInvestorProfileService:
    global _service
    if _service is None:
        _service = UserInvestorProfileService()
    return _service
