"""The free welcome grant cannot be claimed again by deleting and re-creating an account (CC-01).

Before migration 211 every new `public.users` row got the free tier's 50 credits and
DELETE /users/me hard-deleted the auth row, so delete → sign up again (same email, Apple ID or
Google account), or a Gmail alias of a live account, minted 50 more — two AI reports each, as
often as anyone liked (audit CC-01, 2026-11-26).

Two halves, pinned here:
  * DELETE /users/me fingerprints the account (`record_deleted_account`) AFTER the purge
    succeeded and BEFORE `admin.delete_user` (the auth rows must still exist), fails OPEN
    (deleting is an App Store 5.1.1(v) / privacy obligation), and is rate limited.
  * Migration 211's SQL: a keyed (Vault) fingerprint, 90-day retention (owner, 2026-11-26), a
    DEFERRED sign-up trigger that withholds only the untouched welcome grant, only in the granted
    pool, and never blocks a sign-up. The suite has no database (testing.md), so the SQL is
    pinned at the source level here; its BEHAVIOUR was run against a local Supabase
    (SYSTEM_DESIGN_GUIDELINES §9b.13 records the run).

No network: the client is a fake that records calls in order.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import users as users_ep
from app.core.security import rate_limiter

@pytest.fixture(autouse=True)
def _fresh_deletion_limiter():
    """DELETE /users/me charges its per-account bucket inside the handler (after the re-auth
    check, review 2026-11-27); tests that call the handler directly, several times for one
    account, start each test with an empty limiter."""
    from app.core.security import rate_limiter as _rl
    _rl.clear()
    yield
    _rl.clear()


_USER_ID = "11111111-2222-4333-8444-555555555555"
_BACKEND = Path(__file__).resolve().parents[1]
_MIGRATION = _BACKEND / "database" / "migrations" / "211_deleted_account_fingerprints.sql"


# ── Fakes ────────────────────────────────────────────────────────────────────

class _Q:
    def __init__(self, log, table, fail):
        self._log, self._table, self._fail = log, table, fail

    def delete(self): return self
    def eq(self, *_a): return self

    def execute(self):
        if self._table in self._fail:
            raise RuntimeError(self._fail[self._table])
        self._log.append(("table.delete", self._table))
        return self


class _Bucket:
    def __init__(self, log):
        self._log = log

    def list(self, prefix, options=None):
        return []

    def remove(self, paths):
        self._log.append(("storage.remove", tuple(paths)))


class _Storage:
    def __init__(self, log):
        self._log = log

    def from_(self, _bucket):
        return _Bucket(self._log)


class _Admin:
    def __init__(self, log, fail):
        self._log, self._fail = log, fail

    def delete_user(self, user_id):
        if "auth" in self._fail:
            raise RuntimeError(self._fail["auth"])
        self._log.append(("auth.delete_user", user_id))


class _RPC:
    def __init__(self, sb, name, params):
        self._sb, self._name, self._params = sb, name, params

    def execute(self):
        self._sb.log.append(("rpc", self._name, dict(self._params)))
        if self._sb.rpc_raises is not None:
            raise self._sb.rpc_raises
        return type("R", (), {"data": self._sb.rpc_data})()


class SB:
    def __init__(self, *, fail=None, rpc_data=3, rpc_raises=None):
        self.log: list = []
        self._fail = fail or {}
        self.rpc_data = rpc_data
        self.rpc_raises = rpc_raises
        self.auth = type("A", (), {})()
        self.auth.admin = _Admin(self.log, self._fail)
        self.storage = _Storage(self.log)

    def table(self, name):
        return _Q(self.log, name, self._fail)

    def rpc(self, name, params):
        return _RPC(self, name, params)

    def index(self, kind):
        return next(i for i, e in enumerate(self.log) if e[0] == kind)


async def _delete(sb):
    return await users_ep.delete_account(user={"id": _USER_ID}, supabase=sb)


# ── The deletion side ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_fingerprint_is_recorded_after_the_purge_and_before_the_auth_row_goes():
    sb = SB()
    assert await _delete(sb) == {"deleted": True}

    assert ("rpc", "record_deleted_account", {"p_user_id": _USER_ID}) in sb.log
    rpc_at = sb.index("rpc")
    assert rpc_at > max(i for i, e in enumerate(sb.log) if e[0] == "table.delete"), (
        "fingerprinted before the purge: an aborted deletion would leave a live account fingerprinted"
    )
    assert rpc_at < sb.index("auth.delete_user"), (
        "fingerprinted after admin.delete_user: auth.users / auth.identities are already gone, "
        "so nothing can be read to fingerprint"
    )


@pytest.mark.asyncio
async def test_an_aborted_deletion_records_no_fingerprint():
    sb = SB(fail={"chat_sessions": "boom"})
    resp = await _delete(sb)
    assert getattr(resp, "status_code", None) == 500
    assert not [e for e in sb.log if e[0] in ("rpc", "auth.delete_user")]


@pytest.mark.asyncio
@pytest.mark.parametrize("raised, level, marker", [
    (RuntimeError("{'code': 'PGRST202', 'message': 'Could not find the function'}"), "WARNING", "apply migration 211"),
    (RuntimeError("function public.record_deleted_account(uuid) does not exist (42883)"), "WARNING", "apply migration 211"),
    (RuntimeError("deleted_account_fingerprint_key is missing from Vault (migration 211)"), "ERROR", "can claim the welcome credits once more"),
    (TimeoutError("read timed out"), "ERROR", "can claim the welcome credits once more"),
])
async def test_a_failed_fingerprint_never_blocks_the_deletion(raised, level, marker, caplog):
    sb = SB(rpc_raises=raised)
    with caplog.at_level("WARNING"):
        assert await _delete(sb) == {"deleted": True}
    assert ("auth.delete_user", _USER_ID) in sb.log
    hits = [r for r in caplog.records if marker in r.getMessage()]
    assert hits and hits[0].levelname == level, caplog.text
    assert _USER_ID in hits[0].getMessage(), "the log must name the account"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, level", [
    (0, "WARNING"), (-1, "WARNING"), (None, "ERROR"), ("3", "ERROR"), (True, "ERROR"),
    ([3], "ERROR"), (2.0, "ERROR"),
])
async def test_an_odd_rpc_answer_is_logged_and_the_deletion_completes(answer, level, caplog):
    sb = SB(rpc_data=answer)
    with caplog.at_level("INFO"):
        assert await _delete(sb) == {"deleted": True}
    worst = max((r.levelno for r in caplog.records if "fingerprint" in r.getMessage()), default=0)
    assert worst >= getattr(__import__("logging"), level)


def test_delete_account_declares_the_rate_limit_in_its_own_signature():
    params = inspect.signature(users_ep.delete_account).parameters
    assert params["_rate"].default is users_ep.AccountDeletionRateLimit


class _Req:
    def __init__(self, ip):
        self.client = type("C", (), {"host": ip})()
        self.headers = {}


@pytest.fixture
def _clean_limiter():
    rate_limiter.clear()
    yield
    rate_limiter.clear()


@pytest.mark.asyncio
async def test_deletion_is_limited_per_account(_clean_limiter):
    """Charged by `_charge_account_deletion`, which delete_account calls only AFTER the re-auth
    check passed (review 2026-11-27), never by the dependency."""
    per_user, window = users_ep.ACCOUNT_DELETE_LIMIT_PER_USER
    for i in range(per_user):
        users_ep._charge_account_deletion(_USER_ID)
    with pytest.raises(HTTPException) as exc:
        users_ep._charge_account_deletion(_USER_ID)
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == str(window)
    # Another account is unaffected, and the dependency no longer touches the account bucket.
    users_ep._charge_account_deletion("someone-else")
    await users_ep._account_deletion_rate_limit(_Req("198.51.100.201"), user_id="third-user")
    assert "delete_account:user:third-user" not in rate_limiter._protected


def test_the_account_bucket_is_charged_after_the_reauth_check_and_before_any_purge():
    import inspect as _inspect
    src = _inspect.getsource(users_ep.delete_account)
    assert src.index("_deletion_reauth_refusal(") < src.index("_charge_account_deletion(user_id)") \
        < src.index("_purge_research_pdfs")


@pytest.mark.asyncio
async def test_deletion_is_limited_per_address_and_short_circuits(_clean_limiter):
    per_ip, _ = users_ep.ACCOUNT_DELETE_LIMIT_PER_IP
    for i in range(per_ip):
        await users_ep._account_deletion_rate_limit(_Req("203.0.113.9"), user_id=f"user-{i}")
    with pytest.raises(HTTPException) as exc:
        await users_ep._account_deletion_rate_limit(_Req("203.0.113.9"), user_id="user-new")
    assert exc.value.status_code == 429
    # A caller the IP limiter refused must not create the second bucket (rate_limiter's eviction
    # attack — see `_enforce_credential_limits`).
    assert "delete_account:user:user-new" not in rate_limiter._protected


# ── Migration 211 (source level) ─────────────────────────────────────────────

def _sql() -> str:
    text = _MIGRATION.read_text(encoding="utf-8")
    return "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())


def _flat() -> str:
    return " ".join(_sql().split())


def _function(name: str) -> str:
    flat = _flat()
    start = flat.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
    end = flat.index("$$;", flat.index("AS $$", start)) + 3
    return flat[start:end]


def test_the_signup_trigger_is_deferred_to_commit():
    """GoTrue inserts the user row, THEN its identity row, in one transaction. Only a trigger
    that runs at COMMIT can see the Apple/Google subject."""
    assert re.search(
        r"CREATE CONSTRAINT TRIGGER trg_withhold_returning_welcome AFTER INSERT ON auth\.users "
        r"DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
        r"public\.withhold_returning_welcome_credits\(\)", _flat(),
    )
    assert "DROP TRIGGER IF EXISTS trg_withhold_returning_welcome ON auth.users" in _flat()


def test_the_trigger_never_blocks_a_signup():
    body = _function("withhold_returning_welcome_credits")
    assert re.search(r"EXCEPTION WHEN OTHERS THEN RAISE WARNING .*? RETURN NULL; END; \$\$;$", body), (
        "an error in the credit control must fall back to the normal grant, never fail the sign-up"
    )
    assert "RAISE EXCEPTION" not in body


def test_the_trigger_touches_only_the_granted_pool_of_an_untouched_opening_grant():
    """App Store 3.1.1: purchased credits never expire and the granted-pool writers never touch
    them (§9b.1). And only a brand-new grant is withheld: nothing spent, nothing bought, no paid
    period, total == tier_alloc, no ledger row but the opening grant."""
    body = _function("withhold_returning_welcome_credits")
    updates = re.findall(r"UPDATE public\.user_credits SET (.*?) WHERE", body)
    # `total` only: zeroing `tier_alloc` too made ensure_credit_period's mid-month top-up re-grant
    # the 50 as `tier_upgrade` on the next balance read (local end-to-end run, 2026-11-26).
    assert updates == ["total = 0, updated_at = now()"], updates
    for guard in (
        "v_row.used <> 0", "v_row.purchased_total <> 0", "v_row.purchased_used <> 0",
        "v_row.paid_period_id IS NOT NULL", "v_row.total <= 0", "v_row.total <> v_row.tier_alloc",
        "reason <> 'grant'",
    ):
        assert guard in body, f"the untouched-grant guard lost {guard!r}"
    # Append, never rewrite: the grant row stays and a granted-only reversal follows it.
    assert "DELETE FROM public.credit_transactions" not in body
    assert "UPDATE public.credit_transactions" not in body
    assert ("VALUES (NEW.id, -v_row.total, -v_row.total, 0, 'welcome_withheld', NULL, 0)" in body), (
        "the ledger row must be granted-only (purchased_delta 0) with ref_id NULL"
    )
    assert "_GUEST CONSTANT UUID := '00000000-0000-4000-8000-00000000dead'" in body


def test_aliases_of_a_live_account_are_withheld_too():
    body = _function("withhold_returning_welcome_credits")
    assert "public.canonical_account_email(u.email) = v_canon" in body
    assert "u.id <> NEW.id" in body
    # Two alias sign-ups committing together must not both keep the grant: the lock (held to
    # commit) is taken BEFORE the EXISTS, so the second one reads the first one's committed row.
    lock = body.index("PERFORM pg_advisory_xact_lock(hashtext('welcome:' || v_canon));")
    assert lock < body.index("SELECT EXISTS ( SELECT 1 FROM public.users u"), "lock after the check"
    assert "CREATE INDEX IF NOT EXISTS idx_users_canonical_email ON public.users (public.canonical_account_email(email))" in _flat()


def test_retention_is_ninety_days_and_expired_rows_never_match():
    """Owner, 2026-11-26: '90 days (Recommended)'."""
    flat = _flat()
    assert flat.count("now() + INTERVAL '90 days'") == 2, "both the insert and the refresh set 90 days"
    assert "INTERVAL" not in _function("withhold_returning_welcome_credits")
    assert "WHERE f.expires_at > now()" in _function("withhold_returning_welcome_credits")
    for fn in ("record_deleted_account", "withhold_returning_welcome_credits"):
        assert "DELETE FROM public.deleted_account_fingerprints WHERE expires_at <= now()" in _function(fn), fn


def test_fingerprints_are_keyed_and_the_key_never_appears_in_the_repo():
    flat = _flat()
    assert "vault.create_secret( encode(extensions.gen_random_bytes(32), 'hex')" in flat
    fp = _function("_account_fingerprint")
    assert "extensions.hmac(" in fp and "'sha256'" in fp
    assert "RAISE EXCEPTION 'deleted_account_fingerprint_key is missing from Vault" in fp, (
        "a missing key must raise, never fall back to an unkeyed (dictionary-reversible) hash"
    )
    assert "digest(" not in flat, "an unkeyed digest anywhere is a reversible fingerprint"
    assert not re.search(r"'[0-9a-f]{64}'", _MIGRATION.read_text()), "a literal key is in the migration"


def test_only_service_role_can_record_and_nobody_can_call_the_key_helpers():
    flat = _flat()
    assert "GRANT EXECUTE ON FUNCTION public.record_deleted_account(uuid) TO service_role;" in flat
    for helper in ("_account_fingerprint(text, text)", "_account_fingerprints(uuid)"):
        for role in ("PUBLIC", "anon", "authenticated", "service_role"):
            assert f"REVOKE ALL ON FUNCTION public.{helper} FROM {role};" in flat, (helper, role)
        assert f"GRANT EXECUTE ON FUNCTION public.{helper}" not in flat
    assert "REVOKE ALL ON public.deleted_account_fingerprints FROM anon, authenticated;" in flat


def test_the_ledger_holds_no_user_id_and_no_email():
    create = _flat()[_flat().index("CREATE TABLE IF NOT EXISTS public.deleted_account_fingerprints"):]
    create = create[:create.index(");")]
    assert "user_id" not in create and "email" not in create.replace("email_", "")
    assert "CHECK (fingerprint ~ '^[0-9a-f]{64}$')" in create


def test_the_provider_subject_excludes_email_and_phone_identities():
    """An email or phone identity's provider_id is the account's own uuid or number: a new
    account never repeats it, so it is not a key (and would only fill the ledger)."""
    body = _function("_account_fingerprints")
    assert "lower(i.provider) NOT IN ('email', 'phone')" in body
    assert "i.identity_data ->> 'email'" in body, "an identity's own email is a key too"


def test_the_withheld_row_has_a_readable_history_label():
    from app.services.credit_history_service import describe_reason
    kind, title, _ = describe_reason("welcome_withheld")
    assert kind == "revoke" and "Welcome credits" in title
