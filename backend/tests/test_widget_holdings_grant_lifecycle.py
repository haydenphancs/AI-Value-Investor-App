"""The Holdings widget grant's life (migration 216), event by event, over the session fake.

Owner 2026-11-27: "Own key, 30-day life (Recommended)" and "Yes, issue it in refresh
(Recommended)". The grant reads the account's holdings for 30 days with the app closed, so every
way a session or an account ends MUST end it — and nothing else may:

    logout              → that session's grants (superseded ones included), nobody else's
    refresh-token reuse → that session's grants
    sign-out-all        → every grant (the trigger) — and the stamp evicts them even without it
    password stamp      → every grant minted before it (whole seconds, like 214)
    account deletion    → the FK cascade
    idle session        → NOTHING: the grant outlives a pruned family (owner: 30 days)
    renewal             → the predecessor keeps working ≤ 24 h, until the successor's first use

Everything runs through `_refresh_session_fake.SessionStoreFake`, a statement-by-statement model
of `216_widget_holdings_grants.sql` (and 214), driven through the REAL service module
(`app/services/widget_holdings_grant_service.py`) and the real 214 service calls. The SQL itself
is exercised on a real Postgres by the migration's VERIFY block / the local Supabase stack.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from _refresh_session_fake import SessionStoreFake, SupabaseFake
from app.core.security import decode_token, rate_limiter
from app.services import auth_session_service as sessions
from app.services import widget_holdings_grant_service as grants

UID = "11111111-2222-4333-8444-555555555555"
OTHER = "99999999-8888-4777-8666-555555555555"
EMAIL = "owner@example.com"
TOKEN_A = "ab" * 32
TOKEN_B = "cd" * 32


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    rate_limiter.clear()
    monkeypatch.setattr(grants, "_last_schema_missing_error", 0.0)
    monkeypatch.setattr(sessions, "_last_schema_missing_error", 0.0)
    yield


class World:
    """One database, a movable clock, and helpers that go through the real service code."""

    def __init__(self) -> None:
        self.base = datetime.now(timezone.utc).replace(microsecond=0)
        self.offset = timedelta(0)
        self.store = SessionStoreFake(
            users={
                UID: {"password_changed_at": None, "sessions_revoked_at": None},
                OTHER: {"password_changed_at": None, "sessions_revoked_at": None},
            },
            clock=lambda: self.base + self.offset,
        )
        self.db = SupabaseFake(store=self.store)

    def now(self) -> datetime:
        return self.base + self.offset

    def advance(self, **kw) -> None:
        self.offset += timedelta(**kw)

    def iat(self) -> int:
        return int(self.now().timestamp())

    def session(self, uid: str = UID) -> sessions.IssuedSession:
        issued = sessions.start_session(self.db, uid, EMAIL)
        assert issued.sid, "the fake must track sessions"
        return issued

    def mint(self, uid: str, sid: str, iat: int | None = None) -> grants.MintedGrant:
        outcome = grants.mint(self.db, uid, sid, iat if iat is not None else self.iat())
        assert outcome.status == "minted", outcome
        return outcome.grant

    def check(self, grant: grants.MintedGrant) -> grants.GrantCheck:
        return grants.check(self.db, grants.key_hash(grant.grant_token))

    def row(self, grant: grants.MintedGrant) -> dict:
        return self.store.grants[grant.grant_id]

    def targets(self, *, scopes=(), all_targets=False, grant_ids=(), after=None,
                limit=1000) -> list[dict]:
        """`widget_push_targets`, called as its one caller (`widget_push_service`) calls it."""
        return self.db.rpc("widget_push_targets", {
            "p_scopes": list(scopes), "p_all": all_targets, "p_grant_ids": list(grant_ids),
            "p_after": after, "p_limit": limit,
        }).execute().data

    def settle(self, outcomes: list) -> dict:
        return self.db.rpc("widget_push_settle", {"p_outcomes": outcomes}).execute().data


@pytest.fixture
def w() -> World:
    return World()


# ── key format ──────────────────────────────────────────────────────────────────────────────

def test_a_key_is_cwh1_plus_43_base64url_and_only_its_sha256_is_stored(w):
    s = w.session()
    g = w.mint(UID, s.sid)
    assert grants.is_well_formed(g.grant_token)
    assert len(g.grant_token) == grants.KEY_LENGTH == 48
    row = w.row(g)
    assert row["key_hash"] == grants.key_hash(g.grant_token)
    assert len(row["key_hash"]) == 64 and row["key_hash"] == row["key_hash"].lower()
    assert g.grant_token not in repr(row), "the raw key must never be stored"
    assert g.grant_token not in repr(g), "MintedGrant's repr must not carry the key"


@pytest.mark.parametrize("bad", [
    "", "cwh1_", "cwh1_" + "A" * 42, "cwh1_" + "A" * 44, "cwh1-" + "A" * 43,
    "CWH1_" + "A" * 43, "cwh1_" + "A" * 42 + "+", "cwh1_" + "A" * 42 + "/",
    " cwh1_" + "A" * 43, "cwh1_" + "A" * 43 + "\n", None, 7, b"cwh1_" + b"A" * 43,
    "cwh1_" + "A" * 4000,
])
def test_malformed_keys_are_refused_by_shape(bad):
    assert grants.is_well_formed(bad) is False


def test_two_keys_never_collide_and_hashes_differ():
    tokens = {grants.new_token() for _ in range(200)}
    assert len(tokens) == 200
    assert len({grants.key_hash(t) for t in tokens}) == 200


# ── mint gates ──────────────────────────────────────────────────────────────────────────────

def test_a_revoked_session_cannot_mint(w):
    s = w.session()
    sessions.revoke_session(w.db, UID, s.sid)
    assert grants.mint(w.db, UID, s.sid, w.iat()).status == "session_revoked"
    assert not w.store.grants


def test_another_accounts_session_cannot_mint(w):
    s = w.session(OTHER)
    assert grants.mint(w.db, UID, s.sid, w.iat()).status == "session_unknown"


def test_a_deleted_account_cannot_mint(w):
    s = w.session()
    w.store.delete_user(UID)
    assert grants.mint(w.db, UID, s.sid, w.iat()).status == "account_missing"


def test_an_iat_before_the_password_stamp_cannot_mint(w):
    s = w.session()
    w.store.users[UID]["password_changed_at"] = (w.now() + timedelta(seconds=5)).isoformat()
    assert grants.mint(w.db, UID, s.sid, w.iat()).status == "evicted"


@pytest.mark.parametrize("iat", [0, -5, True, 1.5, "1700000000", None])
def test_an_unusable_iat_is_refused_before_any_io(w, iat):
    s = w.session()
    calls = len(w.store.calls)
    with pytest.raises(ValueError):
        grants.mint(w.db, UID, s.sid, iat)
    assert len(w.store.calls) == calls


def test_a_non_uuid_sid_is_refused_before_any_io(w):
    calls = len(w.store.calls)
    with pytest.raises(ValueError):
        grants.mint(w.db, UID, "not-a-uuid", w.iat())
    assert len(w.store.calls) == calls


# ── logout / reuse / sign-out-all: the families trigger ─────────────────────────────────────

def test_logout_kills_only_that_sessions_grants_superseded_ones_included(w):
    a, b = w.session(), w.session()
    old = w.mint(UID, a.sid)
    new = w.mint(UID, a.sid)                      # renewal: `old` is superseded, still live
    other_device = w.mint(UID, b.sid)
    assert w.check(old).status == "live" and w.row(old)["superseded_by"] == new.grant_id

    assert sessions.revoke_session(w.db, UID, a.sid) == "revoked"

    assert w.check(old).status == "revoked" and w.row(old)["revoke_reason"] == "logout"
    assert w.check(new).status == "revoked" and w.row(new)["revoke_reason"] == "logout"
    assert w.check(other_device).status == "live", "another device's grant must survive"


def test_refresh_token_reuse_kills_the_sessions_grants(w):
    s = w.session()
    g = w.mint(UID, s.sid)
    payload = decode_token(s.refresh_token)
    assert sessions.rotate(w.db, UID, payload, s.refresh_token).status == "rotated"
    w.advance(seconds=600)                         # outside the grace window
    assert sessions.rotate(w.db, UID, payload, s.refresh_token).status == "reuse"
    assert w.check(g).status == "revoked"
    assert w.row(g)["revoke_reason"] == "reuse"


def test_sign_out_all_kills_every_grant_of_the_account(w):
    gs = [w.mint(UID, w.session().sid) for _ in range(3)]
    keep = w.mint(OTHER, w.session(OTHER).sid)
    sessions.revoke_all_sessions(w.db, UID)
    for g in gs:
        assert w.check(g).status == "revoked"
        assert w.row(g)["revoke_reason"] == "signout_all"
    assert w.check(keep).status == "live"


def test_sign_out_all_still_evicts_with_the_trigger_removed(w):
    """Defence in depth: `DROP TRIGGER` (the mutation) must not reopen a signed-out device — the
    sign-out-all STAMP evicts through the one predicate on its own."""
    w.store.trigger_enabled = False
    g = w.mint(UID, w.session().sid)
    sessions.revoke_all_sessions(w.db, UID)
    assert w.row(g)["revoked_at"] is None, "the mutation really removed the trigger"
    assert w.check(g).status == "evicted"


def test_the_trigger_maps_an_unknown_family_reason_to_session_ended(w):
    """The trigger can never fail on data (a failing trigger would roll back reuse detection)."""
    s = w.session()
    g = w.mint(UID, s.sid)
    fam = w.store.families[s.sid]
    fam.update(revoked_at=w.now(), revoke_reason="a_future_reason")
    w.store._on_family_revoked(fam)
    assert w.row(g)["revoke_reason"] == "session_ended"


# ── the stamps: one predicate, whole seconds ────────────────────────────────────────────────

def test_a_password_stamp_evicts_older_grants_and_spares_newer_ones(w):
    s1, s2 = w.session(), w.session()
    before = w.mint(UID, s1.sid, iat=w.iat() - 10)
    w.store.users[UID]["password_changed_at"] = (w.now() - timedelta(seconds=5)).isoformat()
    assert w.check(before).status == "evicted"
    after = w.mint(UID, s2.sid, iat=w.iat())
    assert w.check(after).status == "live"
    assert w.check(before).status == "revoked", "the first evicted check revoked it"


# ── eviction REVOKES (D4: no holding left behind on a dead row; review 2026-11-27) ────────────

def _tokened(w, uid=UID, *, iat=None, token=TOKEN_A, head="NVDA"):
    g = w.mint(uid, w.session(uid).sid, iat=iat)
    assert grants.register_push_token(w.db, uid, g.grant_id, token, "production") == (True, True)
    assert grants.note_head(w.db, g.grant_id, head) == "noted"
    return g


def _dead_and_empty(row: dict) -> bool:
    return (row["revoked_at"] is not None and row["revoke_reason"] == "session_ended"
            and row["push_token"] is None and row["push_environment"] is None
            and row["last_head_ticker"] is None)


@pytest.mark.parametrize("which", ["password_changed_at", "sessions_revoked_at"])
def test_an_evicted_grant_is_revoked_the_first_time_it_is_checked(w, which):
    """A password change (or a sign-out-all whose family was already pruned) evicts without the
    trigger. The device can no longer revoke by key, so the CHECK revokes it: no head ticker (a
    holding) and no push token stay behind for the rest of its 30 days."""
    g = _tokened(w, iat=w.iat() - 30)
    w.store.users[UID][which] = w.now().isoformat()
    assert w.row(g)["revoked_at"] is None, "eviction alone writes nothing"
    assert w.check(g).status == "evicted"
    assert _dead_and_empty(w.row(g)), w.row(g)
    assert w.check(g).status == "revoked"
    assert w.targets(all_targets=True) == []
    # Revoked, it is now pruned like any dead row (a day later, at any mint).
    w.store.users[UID][which] = None
    w.advance(days=2)
    w.mint(OTHER, w.session(OTHER).sid)
    assert g.grant_id not in w.store.grants


def test_a_later_mint_sweeps_every_evicted_grant_of_the_account_and_nobody_elses(w):
    phone = _tokened(w, iat=w.iat() - 30, token=TOKEN_A, head="NVDA")
    ipad = _tokened(w, iat=w.iat() - 20, token="ef" * 32, head="AAPL")
    theirs = _tokened(w, OTHER, iat=w.iat() - 30, token=TOKEN_B, head="MSFT")
    second = int(w.now().timestamp())
    same_second = _tokened(w, iat=second, token="12" * 32, head="TSLA")
    # A stamp 0.6 s into the current second: whole seconds, as the predicate.
    stamp = datetime.fromtimestamp(second, tz=timezone.utc) + timedelta(microseconds=600_000)
    w.store.users[UID]["password_changed_at"] = stamp.isoformat()

    new = w.mint(UID, w.session().sid)              # another device, after the change
    assert _dead_and_empty(w.row(phone)) and _dead_and_empty(w.row(ipad))
    assert w.row(same_second)["revoked_at"] is None, "minted in the stamp's second: spared"
    assert w.row(theirs)["revoked_at"] is None and w.row(theirs)["push_token"] == TOKEN_B
    assert w.check(new).status == "live" and w.check(same_second).status == "live"
    assert {t["grant_id"] for t in w.targets(all_targets=True)} == {
        same_second.grant_id, theirs.grant_id}


def test_a_refused_evicted_mint_still_sweeps(w):
    """The sweep runs before the mint judges its OWN iat: a mint answered 'evicted' (an access
    token older than the stamp) leaves no evicted grant behind either."""
    g = _tokened(w, iat=w.iat() - 30)
    s = w.session()
    w.store.users[UID]["password_changed_at"] = w.now().isoformat()
    outcome = grants.mint(w.db, UID, s.sid, w.iat() - 10)
    assert outcome.status == "evicted" and outcome.grant is None
    assert _dead_and_empty(w.row(g))


def test_a_mint_with_no_stamp_sweeps_nothing(w):
    g = _tokened(w)
    w.mint(UID, w.session().sid)
    assert w.row(g)["revoked_at"] is None and w.row(g)["push_token"] == TOKEN_A


def test_the_stamp_is_floored_to_whole_seconds_like_214(w):
    """iat has no fraction: a grant minted in the SAME second as the stamp survives; one from
    the previous second does not (dependencies.py / 214:269-273)."""
    s1, s2 = w.session(), w.session()
    second = int(w.now().timestamp())
    same = w.mint(UID, s1.sid, iat=second)
    earlier = w.mint(UID, s2.sid, iat=second - 1)
    stamp = datetime.fromtimestamp(second, tz=timezone.utc) + timedelta(microseconds=600_000)
    w.store.users[UID]["password_changed_at"] = stamp.isoformat()
    assert w.check(same).status == "live"
    assert w.check(earlier).status == "evicted"


@pytest.mark.parametrize("which", ["password_changed_at", "sessions_revoked_at"])
def test_either_stamp_alone_evicts(w, which):
    """GREATEST ignores NULLs: one stamp set and the other NULL still evicts."""
    g = w.mint(UID, w.session().sid, iat=w.iat() - 30)
    w.store.users[UID][which] = w.now().isoformat()
    assert w.check(g).status == "evicted"


# ── deletion and idle ───────────────────────────────────────────────────────────────────────

def test_account_deletion_cascades(w):
    g = w.mint(UID, w.session().sid)
    w.store.delete_user(UID)
    assert w.check(g).status == "unknown"


@pytest.mark.parametrize("day,expected", [(8, "live"), (9, "live"), (29, "live"), (31, "expired")])
def test_an_idle_session_is_pruned_but_its_grant_lives_30_days(w, day, expected):
    """The owner's decision: the tile keeps updating with the app unopened. 214 prunes an idle
    family a day after its 7-day refresh token expires; the grant has no FK to it and must
    survive the prune (and is never resurrected or killed by it)."""
    s = w.session()
    g = w.mint(UID, s.sid)
    w.advance(days=day)
    # Any 214 call of the account runs its prune; another sign-in does.
    w.session()
    if day >= 9:
        assert s.sid not in w.store.families, "the idle family was pruned"
    assert w.check(g).status == expected


def test_the_expiry_is_exactly_now_plus_30_days_and_now_is_expired(w):
    g = w.mint(UID, w.session().sid)
    row = w.row(g)
    assert row["expires_at"] - row["created_at"] == timedelta(days=30)
    w.offset = row["expires_at"] - w.base
    assert w.check(g).status == "expired"


# ── renewal ─────────────────────────────────────────────────────────────────────────────────

def test_renewal_keeps_the_old_key_working_until_the_successors_first_check(w):
    s = w.session()
    old = w.mint(UID, s.sid)
    grants.register_push_token(w.db, UID, old.grant_id, TOKEN_A, "sandbox")
    grants.note_head(w.db, old.grant_id, "nvda")
    w.store.grants[old.grant_id]["push_sent_count"] = 4
    new = w.mint(UID, s.sid)

    o, n = w.row(old), w.row(new)
    assert o["superseded_by"] == new.grant_id
    assert o["expires_at"] <= w.now() + timedelta(hours=24)
    assert o["push_token"] is None and o["last_head_ticker"] is None
    assert n["push_token"] == TOKEN_A and n["push_environment"] == "sandbox"
    assert n["last_head_ticker"] == "NVDA" and n["push_sent_count"] == 4

    assert w.check(old).status == "live" and w.check(old).superseded is True
    assert w.check(new).status == "live"
    assert w.check(old).status == "revoked" and o["revoke_reason"] == "renewed"


def test_an_unused_successor_caps_the_old_key_at_24_hours(w):
    """A lost refresh answer: the successor has no holder. The old key keeps the tile alive for
    at most a day — never the rest of its 30."""
    s = w.session()
    old = w.mint(UID, s.sid)
    w.mint(UID, s.sid)
    w.advance(hours=23)
    assert w.check(old).status == "live"
    w.advance(hours=2)
    assert w.check(old).status == "expired"


def test_a_third_mint_retires_the_first_key_at_once(w):
    s = w.session()
    first = w.mint(UID, s.sid)
    second = w.mint(UID, s.sid)
    third = w.mint(UID, s.sid)
    assert w.row(first)["revoke_reason"] == "renewed"
    assert w.row(second)["superseded_by"] == third.grant_id
    live = [r for r in w.store.grants.values() if r["sid"] == s.sid and r["revoked_at"] is None]
    assert len(live) == 2, "at most two keys per session are ever live"


def test_the_cap_counts_only_non_superseded_rows(w):
    sids = [w.session().sid for _ in range(grants.MAX_LIVE)]
    firsts = []
    for sid in sids:
        firsts.append(w.mint(UID, sid))
        w.advance(seconds=1)
        w.mint(UID, sid)                           # a renewal per device: 2 rows each
        w.advance(seconds=1)
    assert all(w.row(g)["revoked_at"] is None for g in firsts), (
        "superseded rows must not count against the cap"
    )
    sixth = w.mint(UID, w.session().sid)
    oldest_sid = sids[0]
    dead = [r for r in w.store.grants.values() if r["sid"] == oldest_sid]
    assert dead and all(r["revoke_reason"] == "cap" for r in dead), (
        "the oldest device's grant AND its superseded predecessor are capped"
    )
    assert w.check(sixth).status == "live"
    live = [r for r in w.store.grants.values()
            if r["revoked_at"] is None and r["superseded_by"] is None]
    assert len(live) == grants.MAX_LIVE


# ── revoke-by-key and the head ticker ───────────────────────────────────────────────────────

def test_revoke_by_key_is_scoped_to_the_owner_and_idempotent(w):
    g = w.mint(UID, w.session().sid)
    assert grants.revoke(w.db, g.grant_id, OTHER, "app_revoked") == "already_revoked_or_unknown"
    assert w.check(g).status == "live"
    assert grants.revoke(w.db, g.grant_id, UID, "widget_removed") == "revoked"
    assert grants.revoke(w.db, g.grant_id, UID, "app_revoked") == "already_revoked_or_unknown"
    assert w.row(g)["revoke_reason"] == "widget_removed"


def test_a_device_cannot_choose_a_server_reason(w):
    g = w.mint(UID, w.session().sid)
    for reason in ("logout", "reuse", "signout_all", "cap", "renewed", ""):
        with pytest.raises(ValueError):
            grants.revoke(w.db, g.grant_id, UID, reason)


def test_every_revocation_nulls_the_push_token_and_head_ticker(w):
    """No holding is left behind on a dead row (spec D4)."""
    tokens = iter([("a%01x" % i) * 32 for i in range(10)])

    def tokened(uid=UID, sid=None):
        g = w.mint(uid, sid or w.session(uid).sid)
        grants.register_push_token(w.db, uid, g.grant_id, next(tokens), "production")
        grants.note_head(w.db, g.grant_id, "AAPL")
        return g

    a = tokened()
    sessions.revoke_session(w.db, UID, w.row(a)["sid"])           # logout
    b = tokened()
    grants.revoke(w.db, b.grant_id, UID, "app_revoked")           # by key
    c = tokened()
    sessions.revoke_all_sessions(w.db, UID)                        # sign-out-all
    w.store.users[UID]["sessions_revoked_at"] = None
    s = w.session()
    d = tokened(sid=s.sid)
    e = w.mint(UID, s.sid)                                         # renewal supersedes d …
    w.check(e)                                                     # … and revokes it 'renewed'
    assert w.row(d)["revoke_reason"] == "renewed"
    for r in w.store.grants.values():
        if r["revoked_at"] is not None or r["superseded_by"] is not None:
            assert r["push_token"] is None and r["last_head_ticker"] is None, r["revoke_reason"]
    assert {w.row(x)["revoke_reason"] for x in (a, b, c)} == {"logout", "app_revoked", "signout_all"}


def test_note_head_writes_only_on_a_change_and_never_on_a_dead_grant(w):
    g = w.mint(UID, w.session().sid)
    assert grants.note_head(w.db, g.grant_id, " msft ") == "noted"
    assert w.row(g)["last_head_ticker"] == "MSFT"
    assert grants.note_head(w.db, g.grant_id, "MSFT") == "unchanged"
    assert grants.note_head(w.db, g.grant_id, None) == "noted"
    assert w.row(g)["last_head_ticker"] is None
    grants.revoke(w.db, g.grant_id, UID, "app_revoked")
    assert grants.note_head(w.db, g.grant_id, "NVDA") == "unchanged"
    assert w.row(g)["last_head_ticker"] is None


# ── the prune ───────────────────────────────────────────────────────────────────────────────

def test_the_cross_user_prune_is_bounded_to_500_rows_per_mint(w):
    old = w.now() - timedelta(days=3)
    for i in range(600):
        gid = f"00000000-0000-4000-8000-{i:012d}"
        w.store.grants[gid] = {
            "grant_id": gid, "user_id": OTHER, "key_hash": f"{i:064x}", "sid": gid,
            "minted_iat": 1, "created_at": old - timedelta(days=1), "expires_at": old,
            "last_used_at": None, "revoked_at": None, "revoke_reason": None,
            "superseded_by": None, "last_head_ticker": None, "push_token": None,
            "push_environment": None, "push_token_updated_at": None, "push_last_sent_at": None,
            "push_sent_day": None, "push_sent_count": 0, "push_bad_since": None,
            "push_last_error": None,
        }
    w.mint(UID, w.session().sid)
    dead_left = sum(1 for r in w.store.grants.values() if r["user_id"] == OTHER)
    assert dead_left == 100


# ── push columns on the grant row ───────────────────────────────────────────────────────────

def test_register_refuses_foreign_dead_superseded_and_unknown_grants_alike(w):
    s = w.session()
    g = w.mint(UID, s.sid)
    assert grants.register_push_token(w.db, OTHER, g.grant_id, TOKEN_A, "sandbox") == (False, False)
    assert grants.register_push_token(
        w.db, UID, "00000000-0000-4000-8000-000000000000", TOKEN_A, "sandbox") == (False, False)
    w.mint(UID, s.sid)                              # g is now superseded
    assert grants.register_push_token(w.db, UID, g.grant_id, TOKEN_A, "sandbox") == (False, False)
    assert w.row(g)["push_token"] is None


def test_a_token_moves_to_the_grant_that_registers_it_last(w):
    g1 = w.mint(UID, w.session().sid)
    g2 = w.mint(OTHER, w.session(OTHER).sid)
    assert grants.register_push_token(w.db, UID, g1.grant_id, TOKEN_A, "production") == (True, True)
    assert grants.register_push_token(w.db, OTHER, g2.grant_id, TOKEN_A, "production") == (True, True)
    assert w.row(g1)["push_token"] is None and w.row(g2)["push_token"] == TOKEN_A
    assert grants.register_push_token(w.db, OTHER, g2.grant_id, None, "production") == (True, True)
    assert w.row(g2)["push_token"] is None


@pytest.mark.parametrize("bad", ["AB" * 16, "ab" * 15, "a" * 33, "zz" * 16, "ab" * 201, ""])
def test_a_malformed_push_token_never_reaches_the_store(w, bad):
    g = w.mint(UID, w.session().sid)
    calls = len(w.store.calls)
    with pytest.raises(ValueError):
        grants.register_push_token(w.db, UID, g.grant_id, bad, "sandbox")
    assert len(w.store.calls) == calls


def test_targets_are_live_non_superseded_tokened_grants_matching_the_scope(w):
    s = w.session()
    g = w.mint(UID, s.sid)
    grants.register_push_token(w.db, UID, g.grant_id, TOKEN_A, "sandbox")
    grants.note_head(w.db, g.grant_id, "NVDA")
    other = w.mint(OTHER, w.session(OTHER).sid)
    grants.register_push_token(w.db, OTHER, other.grant_id, TOKEN_B, "production")
    grants.note_head(w.db, other.grant_id, "AAPL")

    hit = w.targets(scopes=["nvda"])
    assert [t["grant_id"] for t in hit] == [g.grant_id]
    assert hit[0]["push_token"] == TOKEN_A and hit[0]["push_environment"] == "sandbox"
    assert {t["grant_id"] for t in w.targets(all_targets=True)} == {g.grant_id, other.grant_id}
    assert [t["grant_id"] for t in w.targets(grant_ids=[other.grant_id])] == [other.grant_id]
    # Dead and superseded rows are never targets.
    sessions.revoke_session(w.db, UID, s.sid)
    assert [t["grant_id"] for t in w.targets(all_targets=True)] == [other.grant_id]


def test_settle_counts_per_et_day_and_fences_on_the_token_sent(w):
    g = w.mint(UID, w.session().sid)
    grants.register_push_token(w.db, UID, g.grant_id, TOKEN_A, "sandbox")
    ok = {"grant_id": g.grant_id, "token": TOKEN_A, "outcome": "accepted"}
    w.settle([ok, ok])
    assert w.row(g)["push_sent_count"] == 2
    w.settle([{**ok, "count": False}])          # dry run: stamped, not counted
    assert w.row(g)["push_sent_count"] == 2
    w.advance(days=1)
    w.settle([ok])
    assert w.row(g)["push_sent_count"] == 1, "a new ET day resets the count"
    # A stale outcome for a token re-registered meanwhile clears nothing.
    grants.register_push_token(w.db, UID, g.grant_id, TOKEN_B, "sandbox")
    res = w.settle([{"grant_id": g.grant_id, "token": TOKEN_A, "outcome": "gone"}])
    assert res["unmatched"] == 1 and w.row(g)["push_token"] == TOKEN_B


def test_a_410_clears_only_a_token_not_registered_after_apns_timestamp(w):
    g = w.mint(UID, w.session().sid)
    grants.register_push_token(w.db, UID, g.grant_id, TOKEN_A, "sandbox")
    earlier_ms = int((w.now() - timedelta(minutes=5)).timestamp() * 1000)
    later_ms = int((w.now() + timedelta(minutes=5)).timestamp() * 1000)
    gone = {"grant_id": g.grant_id, "token": TOKEN_A, "outcome": "gone"}
    w.settle([{**gone, "apns_ts": earlier_ms}])
    assert w.row(g)["push_token"] == TOKEN_A, "re-registered after APNs' timestamp: kept"
    w.settle([{**gone, "apns_ts": later_ms}])
    assert w.row(g)["push_token"] is None


def test_a_bad_token_is_counted_and_cleared_after_24_hours(w):
    g = w.mint(UID, w.session().sid)
    grants.register_push_token(w.db, UID, g.grant_id, TOKEN_A, "production")
    bad = {"grant_id": g.grant_id, "token": TOKEN_A, "outcome": "bad", "error": "BadDeviceToken"}
    w.settle([bad])
    assert w.row(g)["push_sent_count"] == 1 and w.row(g)["push_token"] == TOKEN_A
    w.advance(hours=25)
    res = w.settle([bad])
    assert res["cleared_bad"] == 1 and w.row(g)["push_token"] is None


def test_settle_refuses_an_oversized_batch(w):
    """216 RAISEs 22023 past 100 outcomes (the sweeper batches ≤ 100); an empty batch settles
    nothing."""
    from _refresh_session_fake import PgError

    with pytest.raises(PgError) as e:
        w.settle([{}] * 101)
    assert e.value.code == "22023"
    res = w.settle([])
    assert res["status"] == "settled" and res["accepted"] == res["skipped"] == 0


def test_settle_skips_malformed_elements_and_settles_the_rest(w):
    g = w.mint(UID, w.session().sid)
    grants.register_push_token(w.db, UID, g.grant_id, TOKEN_A, "sandbox")
    res = w.settle([
        "scalar", {"grant_id": "nope", "token": TOKEN_A, "outcome": "accepted"},
        {"grant_id": g.grant_id, "token": "zz", "outcome": "accepted"},
        {"grant_id": g.grant_id, "token": TOKEN_A, "outcome": "accepted", "count": "weird"},
        {"grant_id": g.grant_id, "token": TOKEN_A, "outcome": "teleported"},
    ])
    assert res["skipped"] == 4 and res["accepted"] == 1
    assert w.row(g)["push_sent_count"] == 1


# ── failures: typed, never "allowed" ────────────────────────────────────────────────────────

def test_a_store_failure_is_typed_and_never_live(w):
    w.store.fail = {"check_widget_holdings_grant"}
    with pytest.raises(grants.GrantStoreUnavailable) as e:
        grants.check(w.db, "0" * 64)
    assert not isinstance(e.value, grants.GrantSchemaMissing)


def test_a_missing_migration_is_schema_missing_and_its_error_is_throttled(w, caplog):
    w.store.grants_missing = {"*"}
    caplog.set_level(logging.WARNING, logger=grants.__name__)
    for _ in range(3):
        with pytest.raises(grants.GrantSchemaMissing):
            grants.check(w.db, "0" * 64)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(errors) == 1 and len(warnings) == 2
    assert "216_widget_holdings_grants.sql" in errors[0].getMessage()


@pytest.mark.parametrize("answer", [
    None, [], [1, 2], "live", {"status": "live"},
    {"status": "live", "grant_id": "x", "user_id": UID},
    {"status": "maybe"}, {"no": "status"},
])
def test_an_unusable_check_answer_is_a_store_failure(answer):
    class _DB:
        def rpc(self, *_a, **_k):
            class _C:
                def execute(self_inner):
                    class _R:
                        data = answer
                    return _R()
            return _C()

    with pytest.raises(grants.GrantStoreUnavailable):
        grants.check(_DB(), "0" * 64)


def test_logs_never_carry_the_key_or_the_full_hash(w, caplog):
    caplog.set_level(logging.DEBUG)
    s = w.session()
    g = w.mint(UID, s.sid)
    w.check(g)
    grants.revoke(w.db, g.grant_id, UID, "app_revoked")
    w.store.grants_missing = {"*"}
    with pytest.raises(grants.GrantSchemaMissing):
        grants.mint(w.db, UID, s.sid, w.iat())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert g.grant_token not in text
    assert grants.key_hash(g.grant_token) not in text
    assert grants.hash_label(grants.key_hash(g.grant_token)) in text, "the 8-hex label is logged"
