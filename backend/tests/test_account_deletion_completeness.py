"""Account deletion must actually delete everything it claims to.

The original handler called `supabase.auth.admin.delete_user()` and nothing else, relying
entirely on the `ON DELETE CASCADE` chain, while its docstring said that removed "all of
the user's data in one call". It did not. Four user-keyed tables omit the FK on purpose
(so the shared guest id can write them) and so the cascade never reaches them, and
generated report PDFs in Storage have no FK at all and were never deleted by anything.

Apple 5.1.1(i) requires the privacy policy to explain deletion and 5.1.1(v) requires
in-app deletion to work; promising deletion and silently keeping data is also just a false
statement to the user. These tests pin the fix.

Two things are asserted:
  1. Every un-FK'd table and the Storage prefix are targeted, with the ORDER that makes a
     partial failure recoverable.
  2. The FK-derived list is complete — a NEW user table added without an FK gets caught
     here instead of silently surviving deletion.

No network / Supabase: the client is a fake that records calls.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import json

from fastapi import HTTPException

from app.api.v1.endpoints import users as users_ep
from app.dependencies import GUEST_USER_ID

_USER_ID = "11111111-2222-4333-8444-555555555555"


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _FakeQuery:
    def __init__(self, log, table, fail):
        self._log, self._table, self._fail = log, table, fail
        self._col = self._val = None

    def delete(self):
        return self

    def eq(self, col, val):
        self._col, self._val = col, val
        return self

    def execute(self):
        if self._table in self._fail:
            raise RuntimeError(self._fail[self._table])
        self._log.append(("table.delete", self._table, self._col, self._val))
        return self


class _FakeBucket:
    """Models storage3's REAL behaviour, which the previous version of this fake did not.

    Two things it now gets right, both of which hid a live bug:

    1. ``list()`` takes an options dict and **caps the page at ``limit``** — storage3's
       ``DEFAULT_SEARCH_OPTIONS`` is ``{"limit": 100, "offset": 0, ...}``. The old fake
       returned every object in one call regardless, so an unpaginated implementation looked
       correct here while silently abandoning everything past the 100th object in production.
       A Max subscriber generates up to 200 reports a month, so the heaviest users were
       exactly the ones whose PDFs survived "account deletion".
    2. ``remove()`` actually removes. The old fake only logged, so a paginating caller would
       loop forever against it — a fake that cannot express deletion cannot test a
       delete-until-empty loop.
    """

    def __init__(self, log, objects, fail):
        self._log, self._objects, self._fail = log, objects, fail

    def list(self, prefix, options=None):
        if "list" in self._fail:
            raise RuntimeError(self._fail["list"])
        self._log.append(("storage.list", prefix))
        limit = (options or {}).get("limit", 100)
        offset = (options or {}).get("offset", 0)
        page = sorted(self._objects)[offset: offset + limit]
        return [{"name": n} for n in page]

    def remove(self, paths):
        if "remove" in self._fail:
            raise RuntimeError(self._fail["remove"])
        self._log.append(("storage.remove", tuple(paths)))
        for p in paths:
            name = p.rsplit("/", 1)[-1]
            if name in self._objects:
                self._objects.remove(name)


class _FakeStorage:
    def __init__(self, log, objects, fail):
        self._log, self._objects, self._fail = log, objects, fail

    def from_(self, bucket):
        self._log.append(("storage.from_", bucket))
        return _FakeBucket(self._log, self._objects, self._fail)


class _FakeAdmin:
    def __init__(self, log, fail):
        self._log, self._fail = log, fail

    def delete_user(self, user_id):
        if "auth" in self._fail:
            raise RuntimeError(self._fail["auth"])
        self._log.append(("auth.delete_user", user_id))


class _FakeAuth:
    def __init__(self, log, fail):
        self.admin = _FakeAdmin(log, fail)


class FakeSupabase:
    def __init__(self, objects=("a.pdf", "b.pdf"), fail=None):
        self.log: list[tuple] = []
        self._fail = fail or {}
        # Mutable: `_FakeBucket.remove` deletes from it, so a delete-until-empty loop
        # terminates the way it does against real storage.
        self._objects = list(objects)
        self.auth = _FakeAuth(self.log, self._fail)
        self.storage = _FakeStorage(self.log, self._objects, self._fail)

    def table(self, name):
        return _FakeQuery(self.log, name, self._fail)


async def _delete(sb, user_id=_USER_ID):
    return await users_ep.delete_account(user={"id": user_id}, supabase=sb)


# ── Happy path ────────────────────────────────────────────────────────────────


def _assert_incomplete(res):
    """The deletion refused to finish, in the structured shape iOS decodes.

    It used to `raise HTTPException(500, "Couldn't fully delete your account…")`, whose body
    renders as `{"detail": ...}` — and `APIClient.validateResponse`'s 5xx arm has NO `detail`
    fallback (only the 4xx arm does), so that message was DISCARDED and the user saw a generic
    transient server error for an operation that is neither transient nor complete. Their
    account still existed and nothing said so.
    """
    assert res.status_code == 500, f"expected 500, got {res.status_code}"
    body = json.loads(res.body)
    assert body["error_code"] == "ACCOUNT_DELETE_INCOMPLETE", body
    # The one thing the user must be told: the account is still here.
    assert "still" in body["user_message"].lower(), body["user_message"]
    return body


@pytest.mark.asyncio
async def test_deletes_every_unlinked_table():
    sb = FakeSupabase()
    assert await _delete(sb) == {"deleted": True}
    deleted = {t for kind, t, *_ in sb.log if kind == "table.delete"}
    expected = (
        set(users_ep._UNLINKED_USER_TABLES)
        | set(users_ep._UNLINKED_IDENTITY_TABLES)
    )
    assert deleted == expected


@pytest.mark.asyncio
async def test_scopes_every_delete_to_this_user():
    """Every purge must be scoped to THIS user, on whichever column that table uses
    to hold the id. `analytics_events` keys on `identity_key`; everything else on
    `user_id`. An unscoped delete here would wipe the table for every user."""
    sb = FakeSupabase()
    await _delete(sb)
    for kind, table, col, val in [e for e in sb.log if e[0] == "table.delete"]:
        expected_col = (
            "identity_key"
            if table in users_ep._UNLINKED_IDENTITY_TABLES
            else "user_id"
        )
        assert col == expected_col, f"{table} filtered on {col!r}, not {expected_col}"
        assert val == _USER_ID, f"{table} filtered on the wrong id: {val!r}"


def test_watchlist_is_purged_explicitly_now_that_its_cascade_is_gone():
    """Migration 108 drops watchlist_items_user_id_fkey (ON DELETE CASCADE) so guests
    can be partitioned per install. Removing a deleted user's watchlist therefore
    becomes THIS list's responsibility — if it fell off, a deleted account would keep
    its watchlist: the incomplete-deletion bug the launch prep already fixed once, and
    a privacy-policy violation."""
    assert "watchlist_items" in users_ep._UNLINKED_USER_TABLES


def test_analytics_history_is_purged_on_account_deletion():
    """`analytics_events.identity_key` IS the user id for a signed-in user, so their
    behavioural history would otherwise survive account deletion."""
    assert "analytics_events" in users_ep._UNLINKED_IDENTITY_TABLES


@pytest.mark.asyncio
async def test_removes_report_pdfs_under_the_user_prefix():
    sb = FakeSupabase(objects=("r1.pdf", "r2.pdf"))
    await _delete(sb)
    removes = [e for e in sb.log if e[0] == "storage.remove"]
    assert removes, "report PDFs were never removed"
    assert set(removes[0][1]) == {
        f"reports/{_USER_ID}/r1.pdf", f"reports/{_USER_ID}/r2.pdf"
    }


@pytest.mark.asyncio
async def test_storage_and_tables_are_purged_before_the_auth_row():
    """Order matters: the object path is the only handle on those PDFs, so if the auth row
    went first and a later step failed, they'd be orphaned and unfindable."""
    sb = FakeSupabase()
    await _delete(sb)
    kinds = [e[0] for e in sb.log]
    auth_at = kinds.index("auth.delete_user")
    assert kinds.index("storage.remove") < auth_at
    assert max(i for i, k in enumerate(kinds) if k == "table.delete") < auth_at


@pytest.mark.asyncio
async def test_no_objects_to_remove_is_not_an_error():
    sb = FakeSupabase(objects=())
    assert await _delete(sb) == {"deleted": True}
    assert not [e for e in sb.log if e[0] == "storage.remove"]
    assert [e for e in sb.log if e[0] == "auth.delete_user"]


# ── Failure paths ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_guest_account_can_never_be_deleted():
    sb = FakeSupabase()
    with pytest.raises(HTTPException) as ei:
        await _delete(sb, user_id=GUEST_USER_ID)
    assert ei.value.status_code == 403
    assert sb.log == [], "guest deletion must touch nothing at all"


@pytest.mark.asyncio
@pytest.mark.parametrize("table", users_ep._UNLINKED_USER_TABLES)
async def test_a_failed_table_purge_keeps_the_auth_row(table):
    """If any target fails, the identity row must survive so a retry can still reach the
    remaining data. Deleting it anyway would strand that data permanently."""
    sb = FakeSupabase(fail={table: "boom"})
    _assert_incomplete(await _delete(sb))
    assert not [e for e in sb.log if e[0] == "auth.delete_user"], (
        "auth row was deleted despite an incomplete purge"
    )


@pytest.mark.asyncio
async def test_one_failing_table_does_not_abandon_the_others():
    sb = FakeSupabase(fail={"chat_usage_budget": "boom"})
    _assert_incomplete(await _delete(sb))
    attempted = {t for kind, t, *_ in sb.log if kind == "table.delete"}
    all_purged = (
        set(users_ep._UNLINKED_USER_TABLES)
        | set(users_ep._UNLINKED_IDENTITY_TABLES)
    )
    assert attempted == all_purged - {"chat_usage_budget"}


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["list", "remove"])
async def test_a_storage_failure_keeps_the_auth_row(op):
    sb = FakeSupabase(fail={op: "boom"})
    _assert_incomplete(await _delete(sb))
    assert not [e for e in sb.log if e[0] == "auth.delete_user"]


@pytest.mark.asyncio
async def test_auth_step_failure_surfaces_as_a_structured_500():
    sb = FakeSupabase(fail={"auth": "boom"})
    _assert_incomplete(await _delete(sb))


# ── A dropped table must not break deletion ───────────────────────────────────
#
# A purge failure aborts deletion with a 500 (deliberately — the auth row is kept so a retry
# can still reach the data). That makes DROPPING a table in a migration a live hazard: between
# applying the migration and deploying the code that stops listing the table, EVERY account
# deletion 500s. That is an App Store 5.1.1(v) break introduced by a schema change that looks
# unrelated to account deletion.
#
# Found while writing migration 116 (`DROP TABLE user_book_progress`). `_purge_unlinked_rows`
# now treats "relation does not exist" as an already-completed purge — a table that is not
# there cannot be holding the user's rows — which makes the deploy/migrate ordering safe in
# both directions rather than code-first only.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        # PostgREST's schema-cache miss, verbatim in shape from a live probe.
        "{'message': \"Could not find the table 'public.gone' in the schema cache\", "
        "'code': 'PGRST205'}",
        # Postgres' own undefined_table, in case the call ever bypasses PostgREST.
        'relation "public.gone" does not exist (SQLSTATE 42P01)',
    ],
)
async def test_a_dropped_table_does_not_abort_deletion(message):
    victim = users_ep._UNLINKED_USER_TABLES[0]
    sb = FakeSupabase(fail={victim: message})
    assert await _delete(sb) == {"deleted": True}, (
        "a table that no longer exists must not fail the purge — otherwise dropping any "
        "table 500s every account deletion until the code deploys"
    )
    # The rest of the purge, and the auth delete, must still have happened.
    deleted = {t for kind, t, *_ in sb.log if kind == "table.delete"}
    assert victim not in deleted
    assert deleted == (
        set(users_ep._UNLINKED_USER_TABLES) | set(users_ep._UNLINKED_IDENTITY_TABLES)
    ) - {victim}
    assert any(kind == "auth.delete_user" for kind, *_ in sb.log)


@pytest.mark.asyncio
async def test_a_genuine_purge_failure_still_aborts():
    """Anti-vacuity for the branch above. Tolerating 'table is gone' must not become
    tolerating everything — a permission error or a network fault means the user's data may
    still be there, and deletion must fail loudly so a retry can reach it."""
    victim = users_ep._UNLINKED_USER_TABLES[0]
    for message in (
        "{'message': 'permission denied for table x', 'code': '42501'}",
        "Server disconnected without sending a response",
    ):
        sb = FakeSupabase(fail={victim: message})
        _assert_incomplete(await _delete(sb)), f"should have aborted on: {message}"
        assert not any(kind == "auth.delete_user" for kind, *_ in sb.log), (
            "the auth row must be KEPT when a purge fails, so a retry can still reach the data"
        )


# ── The list itself must stay complete ────────────────────────────────────────

_SCHEMA = Path(__file__).resolve().parents[1] / "database/schema_snapshot.sql"
_MIGRATIONS = Path(__file__).resolve().parents[1] / "database/migrations"

# Tables whose user_id is a shared/aggregate key rather than a per-user record, so they
# are intentionally NOT part of a user's deletion.
_NOT_USER_OWNED: set[str] = set()


def _tables_dropped_by_migrations() -> set[str]:
    """Tables a migration drops.

    The exact mirror of the snapshot-lag handling below. `_tables_with_user_id_column` reads
    CREATEs from the migrations folder because the snapshot lags behind new tables — but it
    ignored DROPs, so it also lagged behind *removed* ones in the opposite direction: a table
    dropped by a migration stayed in the derived set until someone regenerated the snapshot,
    and the purge-list guard then demanded an entry for a table that is on its way out.

    That is not hypothetical: it is what migration 116 (`DROP TABLE user_book_progress`) hit.
    The snapshot cannot be regenerated first, because it is a dump of the LIVE database and
    the user applies migrations by hand.
    """
    if not _MIGRATIONS.is_dir():
        return set()
    dropped = set()
    for f in _MIGRATIONS.glob("*.sql"):
        for m in re.finditer(
            r"DROP TABLE\s+(?:IF EXISTS\s+)?(?:public\.)?(\w+)", f.read_text(), re.IGNORECASE
        ):
            dropped.add(m.group(1))
    return dropped


def _tables_with_user_id_column() -> set[str]:
    """Every public table declaring a `user_id` column, minus any a migration drops.

    Reads the schema snapshot AND the migrations folder. The snapshot is regenerated
    only every few migrations (see .claude/rules/database.md), so it legitimately lags
    — a table created by a migration that has not been snapshotted yet is still real,
    and treating it as unknown made this guard fail on correct code. The same lag applies
    to drops, hence `_tables_dropped_by_migrations`.
    """
    sources = [_SCHEMA.read_text()]
    migrations = _SCHEMA.parent / "migrations"
    if migrations.is_dir():
        sources.extend(f.read_text() for f in migrations.glob("*.sql"))

    found = set()
    for sql in sources:
        # `CREATE TABLE public.x (` in the snapshot; `CREATE TABLE IF NOT EXISTS x (`
        # in migrations.
        for m in re.finditer(
            r"CREATE TABLE (?:IF NOT EXISTS )?(?:public\.)?(\w+)\s*\((.*?)\n\);",
            sql, re.DOTALL | re.IGNORECASE,
        ):
            name, body = m.group(1), m.group(2)
            if re.search(r"^\s*user_id\s", body, re.MULTILINE):
                found.add(name)
    # A dropped table must ALSO leave `_UNLINKED_USER_TABLES` — subtracting here makes
    # `test_purge_list_has_no_stale_entries` fail if it does not, which is the coupling we want.
    return found - _tables_dropped_by_migrations()


def _tables_with_fk_to_users() -> set[str]:
    """Tables whose user_id has an FK — the cascade covers these.

    Reads the snapshot AND, for tables the snapshot has never heard of, the migrations.

    That second half closes an ASYMMETRY that made this guard cry wolf:
    `_tables_with_user_id_column` learns about a new table from the migrations (the snapshot
    lags by design), but FK detection read only the dump — so a NEW table with a perfectly
    good `REFERENCES public.users(id) ON DELETE CASCADE` failed the guard until someone
    regenerated the snapshot. The obvious way to make that failure go away is to add the table
    to `_UNLINKED_USER_TABLES`, which then DOUBLE-deletes: once by cascade, once by hand. The
    guard would have taught the exact wrong lesson.

    ⚠️ TWO scopes keep the migration half from making this test vacuous, and the second is the
    load-bearing one:

    1. Only tables ABSENT from the snapshot. The snapshot is a dump of the live database, so
       for anything it knows it is the truth — including that a constraint was later dropped.

    2. Only `CREATE TABLE (...)` BODIES — never `ALTER TABLE ... ADD CONSTRAINT`. This is what
       actually protects the guest-writable tables. Migration 047 adds
       `FOREIGN KEY (user_id) REFERENCES public.users(id)` to `watchlist_items`, `portfolios`,
       `research_reports` and `chat_sessions` via ALTER, and migrations 108/110/111 later DROP
       exactly those so guests can be partitioned per install (see .claude/rules/auth.md §1a).
       A scanner that read ALTER statements would resurrect all four as "linked" and this test
       would pass while account deletion silently left their rows behind.

    `test_fk_scanner_still_sees_the_deliberately_unlinked_tables` below pins both, and has been
    mutation-tested against exactly that loosening.
    """
    sql = _SCHEMA.read_text()
    linked = set()
    for m in re.finditer(
        r"ALTER TABLE ONLY public\.(\w+)\s+ADD CONSTRAINT \w+ FOREIGN KEY \(user_id\)",
        sql,
    ):
        linked.add(m.group(1))

    snapshotted = {
        m.group(1)
        for m in re.finditer(r"CREATE TABLE public\.(\w+)\s*\(", sql, re.IGNORECASE)
    }

    migrations = _SCHEMA.parent / "migrations"
    if not migrations.is_dir():
        return linked

    for f in sorted(migrations.glob("*.sql")):
        for m in re.finditer(
            r"CREATE TABLE (?:IF NOT EXISTS )?(?:public\.)?(\w+)\s*\((.*?)\n\);",
            f.read_text(), re.DOTALL | re.IGNORECASE,
        ):
            name, body = m.group(1), m.group(2)
            if name in snapshotted:
                continue  # the dump is authoritative — see the warning above
            # Whitespace-flattened so a column definition wrapped across lines still matches;
            # `[^,]*?` confines the match to ONE column definition, so a REFERENCES on some
            # other column cannot satisfy it.
            flat = re.sub(r"\s+", " ", body)
            inline = re.search(
                r"\buser_id\b[^,]*?REFERENCES\s+(?:public\.)?users\b", flat, re.IGNORECASE
            )
            table_level = re.search(
                r"FOREIGN KEY\s*\(\s*user_id\s*\)\s*REFERENCES\s+(?:public\.)?users\b",
                flat, re.IGNORECASE,
            )
            if inline or table_level:
                linked.add(name)
    return linked


def test_fk_scanner_still_sees_the_deliberately_unlinked_tables():
    """Mutation guard for `_tables_with_fk_to_users`.

    The migration-reading half above is one `if name in snapshotted: continue` away from
    marking every guest-writable table as FK-linked — migration 047 declares FKs for all four,
    and 108/110/111 drop them. If that scope is ever loosened, this test fails instead of
    `test_every_unlinked_user_table_is_in_the_purge_list` silently passing for the wrong
    reason. See .claude/rules/auth.md §1a for why those FKs are gone.
    """
    linked = _tables_with_fk_to_users()
    for table in ("watchlist_items", "portfolios", "research_reports", "chat_sessions"):
        assert table not in linked, (
            f"{table} deliberately has NO FK to users (migrations 108/110/111 dropped it so "
            "guests can be partitioned per install), but the scanner reports it as linked — "
            "the purge guard is now vacuous for it and a deleted account keeps its rows."
        )


def test_fk_scanner_sees_a_new_migration_only_table():
    """The other half: a table created by a migration and not yet snapshotted, WITH an FK,
    must be recognised — otherwise the purge guard demands a `_UNLINKED_USER_TABLES` entry
    that would double-delete alongside the cascade."""
    linked = _tables_with_fk_to_users()
    assert "credit_purchases" in linked, (
        "credit_purchases (migration 117) declares `user_id UUID NOT NULL REFERENCES "
        "public.users(id) ON DELETE CASCADE` but the scanner did not see it — the inline / "
        "multi-line column form has stopped matching."
    )


def test_every_unlinked_user_table_is_in_the_purge_list():
    """A new user-keyed table added WITHOUT an FK must be added to
    `_UNLINKED_USER_TABLES`, or it silently survives account deletion. This is the guard
    that the original implementation lacked."""
    with_user_id = _tables_with_user_id_column()
    assert with_user_id, "schema scan found no user_id tables — the regex has rotted"

    unlinked = with_user_id - _tables_with_fk_to_users() - _NOT_USER_OWNED
    missing = unlinked - set(users_ep._UNLINKED_USER_TABLES)
    assert not missing, (
        "these tables have a user_id but no FK to users, and are NOT purged on account "
        f"deletion: {sorted(missing)}. Add them to _UNLINKED_USER_TABLES (or to "
        "_NOT_USER_OWNED with a reason if they are shared/aggregate)."
    )


def test_purge_list_has_no_stale_entries():
    """Every listed table must still exist, or the delete call errors at runtime."""
    known = _tables_with_user_id_column()
    stale = [t for t in users_ep._UNLINKED_USER_TABLES if t not in known]
    assert not stale, f"listed for purge but not in the schema: {stale}"


# ── The transaction register's ownerless rows (migration 201, IAP audit F17) ─────────────────
#
# subscription_transactions rows with a NULL owner never match the FK cascade, so the rows of a
# deleted account's lineages recorded with no owner used to survive the deletion. 201's BEFORE
# DELETE trigger on public.users purges them. Read through the shared SQL reader (comments of both
# kinds stripped, the LATEST definition, a later DROP fails loudly).

_PURGE_FN = "purge_ownerless_subscription_transactions"
_PURGE_TRIGGER = re.compile(
    r"CREATE\s+TRIGGER\s+users_purge_ownerless_subscription_transactions\s+BEFORE\s+DELETE\s+ON\s+"
    r"public\.users\s+FOR\s+EACH\s+ROW\s+EXECUTE\s+FUNCTION\s+public\.purge_ownerless_subscription_transactions\(\)\s*;",
    re.I)
_DROP_TRIGGER = re.compile(
    r"DROP\s+TRIGGER\s+(?:IF\s+EXISTS\s+)?users_purge_ownerless_subscription_transactions\b", re.I)


_DISABLE_TRIGGER = re.compile(
    r"ALTER\s+TABLE\s+(?:ONLY\s+)?(?:public\.)?users\s+DISABLE\s+TRIGGER\s+"
    r"(?:users_purge_ownerless_subscription_transactions|ALL|USER)\b", re.I)

# The live body, EXACT (whitespace-normalised): an `EXISTS` for a `NOT EXISTS`, a broken
# correlation (`s2.original_transaction_id IS NOT NULL`), an early `RETURN NULL` or a dropped
# exception handler all keep every fragment a substring check looks for (review of 201,
# 2026-10-21).
_PURGE_BODY = (
    "BEGIN DELETE FROM public.subscription_transactions t WHERE t.user_id IS NULL "
    "AND t.original_transaction_id IN ("
    "SELECT s.original_transaction_id FROM public.subscriptions s "
    "WHERE s.user_id = OLD.id AND s.original_transaction_id IS NOT NULL "
    "UNION SELECT p.original_transaction_id FROM public.subscription_credit_periods p "
    "WHERE p.user_id = OLD.id "
    "UNION SELECT o.original_transaction_id FROM public.subscription_transactions o "
    "WHERE o.user_id = OLD.id) "
    "AND NOT EXISTS (SELECT 1 FROM public.subscriptions s2 "
    "WHERE s2.original_transaction_id = t.original_transaction_id AND s2.user_id <> OLD.id) "
    "AND NOT EXISTS (SELECT 1 FROM public.subscription_credit_periods p2 "
    "WHERE p2.original_transaction_id = t.original_transaction_id AND p2.user_id <> OLD.id) "
    "AND NOT EXISTS (SELECT 1 FROM public.subscription_transactions o2 "
    "WHERE o2.original_transaction_id = t.original_transaction_id AND o2.user_id IS NOT NULL "
    "AND o2.user_id <> OLD.id); "
    "RETURN OLD; "
    "EXCEPTION WHEN OTHERS THEN RAISE WARNING 'purge_ownerless_subscription_transactions: could not "
    "purge the ownerless register rows of user % (%: %) — the account is still deleted; the rows "
    "stay', OLD.id, SQLSTATE, SQLERRM; RETURN OLD; END;")


def test_ownerless_register_rows_of_an_account_are_purged_before_its_cascade():
    """Every lineage source, the exclusions that keep another live account's evidence, and RETURN
    OLD on every path. A NULL return from a BEFORE DELETE trigger under auth.users' cascade does not
    raise: it silently skips the public.users delete (orphaned data, the deletion reported done —
    App Store 5.1.1(v)); an error would fail every deletion, so it is caught and logged. The
    trigger is created after its last DROP and never disabled, so it is live."""
    from _migration_sql import latest_definition, migration_sources, norm_sql, strip_sql_comments

    d = latest_definition(_PURGE_FN)
    assert re.search(r"\bSECURITY\s+DEFINER\b", d.header, re.I), d.header
    assert re.search(r"SET\s+search_path\s*=\s*public,\s*pg_temp", d.header, re.I), d.header
    assert re.search(r"SET\s+row_security\s*=\s*off", d.header, re.I), (
        f"{d.file}: without row_security = off the purge filters (or raises) on RLS")
    assert norm_sql(d.body) == _PURGE_BODY, f"{d.file}: the purge changed:\n  {norm_sql(d.body)}"
    returns = re.findall(r"\bRETURN\s+(\w+)", d.body, re.I)
    assert returns and {r.upper() for r in returns} == {"OLD"}, (
        f"{d.file}: every path must RETURN OLD: {returns}")

    created = dropped = disabled = None
    for i, (_name, text) in enumerate(migration_sources()):
        stripped = strip_sql_comments(text)
        for m in _DROP_TRIGGER.finditer(stripped):
            dropped = (i, m.start())
        for m in _PURGE_TRIGGER.finditer(stripped):
            created = (i, m.start())
        for m in _DISABLE_TRIGGER.finditer(stripped):
            disabled = (i, m.start())
    assert created is not None, "no migration creates the users BEFORE DELETE purge trigger"
    assert dropped is None or created > dropped, "the purge trigger is dropped after it was created"
    assert disabled is None or created > disabled, "the purge trigger is disabled after it was created"


_MUTATIONS = [
    ("RETURN OLD;", "RETURN NULL;"),                                   # orphans the account's data
    ("AND p2.user_id <> OLD.id", "AND TRUE"),                          # purges another account's evidence
    ("WHERE s.user_id = OLD.id AND s.original_transaction_id IS NOT NULL",
     "WHERE s.original_transaction_id IS NOT NULL"),                   # purges every account's rows
    ("AND NOT EXISTS (SELECT 1 FROM public.subscriptions s2",
     "AND EXISTS (SELECT 1 FROM public.subscriptions s2"),             # inverts an exclusion
    ("WHERE s2.original_transaction_id = t.original_transaction_id",
     "WHERE s2.original_transaction_id IS NOT NULL"),                  # breaks the correlation
    ("BEGIN\n    DELETE", "BEGIN\n    IF OLD.id IS NOT NULL THEN RETURN NULL; END IF;\n    DELETE"),
    ("EXCEPTION WHEN OTHERS THEN", "-- (handler removed)\n    IF FALSE THEN"),  # an error fails deletion
    ("SET row_security = off\n", ""),
]


@pytest.mark.parametrize("old,new", _MUTATIONS)
def test_the_purge_guard_turns_red_on_a_later_broken_redefinition(old, new, monkeypatch):
    import _migration_sql

    real = _migration_sql.migration_sources()
    text = next(t for n, t in real if n.startswith("201_"))
    start = text.index("CREATE OR REPLACE FUNCTION public.purge_ownerless_subscription_transactions()")
    fn = text[start:text.index("$$;", text.index("AS $$", start)) + 3]
    assert old in fn, f"the mutation anchor {old!r} is gone — re-derive it"
    later = "BEGIN;\n" + fn.replace(old, new, 1) + "\nCOMMIT;\n"
    monkeypatch.setattr(_migration_sql, "migration_sources", lambda: real + [("999_synthetic.sql", later)])
    with pytest.raises(AssertionError):
        test_ownerless_register_rows_of_an_account_are_purged_before_its_cascade()


def test_the_purge_guard_turns_red_when_a_later_migration_disables_the_trigger(monkeypatch):
    import _migration_sql

    real = _migration_sql.migration_sources()
    later = ("BEGIN;\nALTER TABLE public.users DISABLE TRIGGER "
             "users_purge_ownerless_subscription_transactions;\nCOMMIT;\n")
    monkeypatch.setattr(_migration_sql, "migration_sources", lambda: real + [("999_synthetic.sql", later)])
    with pytest.raises(AssertionError, match="disabled"):
        test_ownerless_register_rows_of_an_account_are_purged_before_its_cascade()
