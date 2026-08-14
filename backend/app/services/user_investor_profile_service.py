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


def is_empty_profile(profile: Dict[str, Any]) -> bool:
    """True when the user expressed no preference at all.

    Scalars have defaults, so 'empty' means every ARRAY is empty AND every scalar is
    still its default. Used to decide whether there is anything worth applying — an
    all-default profile should not claim to personalize anything.
    """
    if any(profile.get(f) for f in ARRAY_FIELDS):
        return False
    return all(profile.get(f) == DEFAULTS[f] for f in SCALAR_FIELDS)


class UserInvestorProfileService:
    """Read/write the profile. One row per identity, keyed by `user_id` (PK)."""

    def __init__(self, supabase=None):
        self.supabase = supabase or get_supabase()

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
            self.supabase.table(TABLE).upsert(
                payload, on_conflict="user_id",
            ).execute()
        except Exception as e:
            logger.warning(
                "Investor profile write failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e,
            )
            raise ProfileUnreadable(str(e)) from e
        return self.get_profile(user_id)


_service: Optional[UserInvestorProfileService] = None


def get_user_investor_profile_service() -> UserInvestorProfileService:
    global _service
    if _service is None:
        _service = UserInvestorProfileService()
    return _service
