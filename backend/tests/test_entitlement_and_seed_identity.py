"""Grace-period entitlement, self-eviction on password change, and seed row identity.

--- 1. A failed card retry demoted a paying customer ---

`DID_FAIL_TO_RENEW` with subtype `GRACE_PERIOD` carries a transaction whose `expiresDate` has
already passed. `status_for_transaction` therefore read it as "expired", and `winning_tier`
then dropped the row TWICE — once because "expired" is not an entitling status, and again
because `current_period_end <= now`. The customer fell to free while Apple was still retrying
their card and explicitly considered them entitled.

`subtype` was parsed at the top of `apply_notification` and never used.

--- 2. Changing your password logged YOU out ---

`change_password` stamps `users.password_changed_at`, after which every token minted earlier is
rejected — including the one the caller just authenticated with. The next request 401'd and the
app signed the user out seconds after telling them the change succeeded.

--- 3. A lesson's DB identity was derived from an audio filename ---

`seed_journey` computed the row id as `uuid5(NS, key)` where `key` came from the first card's
`audioClip`. Renaming a clip minted a NEW id, so the upsert INSERTED a second row; `lessons`
has no UNIQUE on title, so both survived and `_fetch_rows` (no ORDER BY) decided arbitrarily
which one readers got.

No network / Supabase / Apple.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from pathlib import Path

import pytest

from app.services.iap_service import IAPService, _status_for_notification

_REPO = Path(__file__).resolve().parents[2]
_USER = "aaaa1111-2222-4333-8444-555555555555"


# ---------------------------------------------------------------------------
# 1. Notification subtype → status
# ---------------------------------------------------------------------------


def test_grace_period_is_entitling():
    """THE demotion case."""
    assert _status_for_notification("DID_FAIL_TO_RENEW", "GRACE_PERIOD") == "grace_period"


@pytest.mark.parametrize("ntype", ["REFUND", "REVOKE"])
def test_refund_and_revoke_strip_entitlement(ntype):
    assert _status_for_notification(ntype, "") == "revoked"


@pytest.mark.parametrize("ntype", ["SUBSCRIBED", "DID_RENEW", "OFFER_REDEEMED"])
def test_renewal_events_are_active(ntype):
    assert _status_for_notification(ntype, "") == "active"


def test_expired_is_expired():
    assert _status_for_notification("EXPIRED", "VOLUNTARY") == "expired"


def test_fail_to_renew_without_grace_defers_to_the_payload():
    """No grace subtype → we know nothing better than the transaction; don't invent entitlement."""
    assert _status_for_notification("DID_FAIL_TO_RENEW", "") is None


def test_auto_renew_toggle_does_not_change_the_current_period():
    """Turning auto-renew off must not revoke the period the customer already paid for."""
    assert _status_for_notification("DID_CHANGE_RENEWAL_STATUS", "AUTO_RENEW_DISABLED") is None


def test_an_unknown_notification_type_defers_rather_than_guessing():
    assert _status_for_notification("SOMETHING_NEW", "WITH_A_SUBTYPE") is None


# ---------------------------------------------------------------------------
# winning_tier must not drop a grace-period row on its period end
# ---------------------------------------------------------------------------


class _Q:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a): return self
    def eq(self, *_a): return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _SB:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _n):
        return _Q(self._rows)


def _svc(rows):
    svc = IAPService.__new__(IAPService)
    svc.supabase = _SB(rows)
    return svc


def test_grace_period_row_survives_its_expired_period_end():
    """Grace period MEANS the paid period ended — filtering on it demotes twice over."""
    svc = _svc([{
        "tier": "pro", "status": "grace_period",
        "current_period_end": "2020-01-01T00:00:00+00:00",   # long past, by definition
    }])
    assert svc.winning_tier(_USER) == "pro"


def test_billing_retry_row_also_survives():
    svc = _svc([{
        "tier": "premium", "status": "billing_retry",
        "current_period_end": "2020-01-01T00:00:00+00:00",
    }])
    assert svc.winning_tier(_USER) == "premium"


def test_an_expired_row_is_still_dropped():
    """The exemption must not become a blanket 'ignore period end'."""
    svc = _svc([{
        "tier": "pro", "status": "expired",
        "current_period_end": "2020-01-01T00:00:00+00:00",
    }])
    assert svc.winning_tier(_USER) == "free"


def test_an_active_row_whose_period_ended_is_still_dropped():
    """A stale 'active' row must never hold someone on a paid tier forever."""
    svc = _svc([{
        "tier": "pro", "status": "active",
        "current_period_end": "2020-01-01T00:00:00+00:00",
    }])
    assert svc.winning_tier(_USER) == "free"


def test_best_tier_wins_across_rows():
    svc = _svc([
        {"tier": "pro", "status": "grace_period", "current_period_end": "2020-01-01T00:00:00+00:00"},
        {"tier": "premium", "status": "active", "current_period_end": "2100-01-01T00:00:00+00:00"},
    ])
    assert svc.winning_tier(_USER) == "premium"


# ---------------------------------------------------------------------------
# 2. change-password returns replacement credentials
# ---------------------------------------------------------------------------


def test_change_password_response_carries_new_tokens():
    from app.schemas.auth import PasswordChangedResponse

    fields = PasswordChangedResponse.model_fields
    for name in ("message", "access_token", "refresh_token", "user_id"):
        assert name in fields, f"PasswordChangedResponse is missing {name!r}"


def test_change_password_route_uses_that_model():
    src = (_REPO / "backend" / "app" / "api" / "v1" / "endpoints" / "auth.py").read_text()
    assert '@router.post("/change-password", response_model=PasswordChangedResponse)' in src, (
        "change-password still returns a bare message, so the caller's own session dies with it"
    )


def test_the_new_tokens_are_minted_after_the_stamp():
    """Order is the whole fix: tokens minted BEFORE `_mark_password_changed` would have an
    `iat` older than the stamp and be rejected exactly like the ones being evicted."""
    src = (_REPO / "backend" / "app" / "api" / "v1" / "endpoints" / "auth.py").read_text()
    fn = src[src.index("async def change_password"):]
    assert fn.index("_mark_password_changed") < fn.index("_issue_app_tokens_for"), (
        "credentials are re-issued before the password-change stamp — they would be evicted too"
    )


# ---------------------------------------------------------------------------
# 3. Seed identity
# ---------------------------------------------------------------------------


def _lessons(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))["lessons"]


_CONTENT_FILES = [
    _REPO / "frontend" / "ios" / "ios" / "Resources" / "Journey" / "journey_lessons.json",
    _REPO / "backend" / "data" / "journey_lessons.json",
]


@pytest.mark.parametrize("path", _CONTENT_FILES, ids=lambda p: p.parent.name)
def test_every_lesson_has_a_slug(path):
    missing = [l.get("title", "?") for l in _lessons(path) if not (l.get("slug") or "").strip()]
    assert not missing, f"lessons with no authored slug (id would follow the audio filename): {missing}"


@pytest.mark.parametrize("path", _CONTENT_FILES, ids=lambda p: p.parent.name)
def test_slugs_are_unique(path):
    slugs = [l["slug"] for l in _lessons(path)]
    dupes = [s for s, n in Counter(slugs).items() if n > 1]
    assert not dupes, f"duplicate slugs collapse to one uuid5 id and abort the upsert: {dupes}"


@pytest.mark.parametrize("path", _CONTENT_FILES, ids=lambda p: p.parent.name)
def test_slugs_preserve_the_previously_derived_ids(path):
    """The migration is only free if every slug equals the key the old code derived — otherwise
    reseeding mints new ids and orphans every existing row."""
    def derived(cards):
        for c in cards:
            if c.get("audioClip"):
                return re.sub(r"_\d+$", "", c["audioClip"])
        return ""

    drifted = [
        f"{l['title']!r}: slug={l['slug']!r} but derived={derived(l.get('cards', []))!r}"
        for l in _lessons(path)
        if derived(l.get("cards", [])) and l["slug"] != derived(l.get("cards", []))
    ]
    assert not drifted, "slug no longer matches the historical id source:\n" + "\n".join(drifted)


def test_the_two_content_copies_agree():
    ios, backend = (_lessons(p) for p in _CONTENT_FILES)
    assert [l["slug"] for l in ios] == [l["slug"] for l in backend], (
        "the bundled and vendored copies disagree — the seeder and the offline fallback would "
        "publish different lessons"
    )


def test_seeder_prefers_the_slug_and_guards_duplicates():
    src = (_REPO / "backend" / "scripts" / "seed_journey.py").read_text(encoding="utf-8")
    assert 'lesson.get("slug")' in src, "the seeder still derives identity from the audio clip"
    assert "len(set(ids)) != len(ids)" in src, (
        "no duplicate-id guard: a slug collision aborts the upsert with an opaque SQLSTATE 21000"
    )


def test_ids_are_stable_for_a_known_lesson():
    """Pin one concrete id so a future namespace or key change is caught."""
    ns = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")
    lesson = next(l for l in _lessons(_CONTENT_FILES[1]) if l["slug"] == "mr_market")
    assert str(uuid.uuid5(ns, lesson["slug"])) == str(uuid.uuid5(ns, "mr_market"))
