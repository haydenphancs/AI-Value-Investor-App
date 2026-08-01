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
    def __init__(self, log, objects, fail):
        self._log, self._objects, self._fail = log, objects, fail

    def list(self, prefix):
        if "list" in self._fail:
            raise RuntimeError(self._fail["list"])
        self._log.append(("storage.list", prefix))
        return [{"name": n} for n in self._objects]

    def remove(self, paths):
        if "remove" in self._fail:
            raise RuntimeError(self._fail["remove"])
        self._log.append(("storage.remove", tuple(paths)))


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
        self._objects = objects
        self.auth = _FakeAuth(self.log, self._fail)
        self.storage = _FakeStorage(self.log, self._objects, self._fail)

    def table(self, name):
        return _FakeQuery(self.log, name, self._fail)


async def _delete(sb, user_id=_USER_ID):
    return await users_ep.delete_account(user={"id": user_id}, supabase=sb)


# ── Happy path ────────────────────────────────────────────────────────────────

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
    with pytest.raises(HTTPException) as ei:
        await _delete(sb)
    assert ei.value.status_code == 500
    assert not [e for e in sb.log if e[0] == "auth.delete_user"], (
        "auth row was deleted despite an incomplete purge"
    )


@pytest.mark.asyncio
async def test_one_failing_table_does_not_abandon_the_others():
    sb = FakeSupabase(fail={"chat_usage_budget": "boom"})
    with pytest.raises(HTTPException):
        await _delete(sb)
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
    with pytest.raises(HTTPException) as ei:
        await _delete(sb)
    assert ei.value.status_code == 500
    assert not [e for e in sb.log if e[0] == "auth.delete_user"]


@pytest.mark.asyncio
async def test_auth_step_failure_surfaces_as_500():
    sb = FakeSupabase(fail={"auth": "boom"})
    with pytest.raises(HTTPException) as ei:
        await _delete(sb)
    assert ei.value.status_code == 500


# ── The list itself must stay complete ────────────────────────────────────────

_SCHEMA = Path(__file__).resolve().parents[1] / "database/schema_snapshot.sql"
_MIGRATIONS = Path(__file__).resolve().parents[1] / "database/migrations"

# Tables whose user_id is a shared/aggregate key rather than a per-user record, so they
# are intentionally NOT part of a user's deletion.
_NOT_USER_OWNED: set[str] = set()


def _tables_with_user_id_column() -> set[str]:
    """Every public table declaring a `user_id` column, from the schema snapshot."""
    sql = _SCHEMA.read_text()
    found = set()
    for m in re.finditer(
        r"CREATE TABLE public\.(\w+)\s*\((.*?)\n\);", sql, re.DOTALL
    ):
        name, body = m.group(1), m.group(2)
        if re.search(r"^\s*user_id\s", body, re.MULTILINE):
            found.add(name)
    return found


def _tables_with_fk_to_users() -> set[str]:
    """Tables whose user_id has an FK — the cascade covers these."""
    sql = _SCHEMA.read_text()
    linked = set()
    for m in re.finditer(
        r"ALTER TABLE ONLY public\.(\w+)\s+ADD CONSTRAINT \w+ FOREIGN KEY \(user_id\)",
        sql,
    ):
        linked.add(m.group(1))
    return linked


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
