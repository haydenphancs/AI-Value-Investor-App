"""
Isolation test for the shared user_learn_progress table.

All four Learn ("Wiser") features write to ONE table (user_learn_progress), discriminated by
content_type:

    book_core      book reading progress     key = "<order>-<core>"   (e.g. "1-3")
    journey_lesson Investor Journey progress  key = lesson title
    money_move     Money Moves progress       key = article slug
    book_bookmark  book bookmarks (NEW)       key = book title         (toggleable: add + remove)

This exercises the REAL endpoint handlers in app.api.v1.endpoints.learn against an in-memory
fake of the Supabase query builder, proving the four content types never collide — and in
particular that un-bookmarking (the only DELETE against this table) can never erase a completion,
even when a bookmarked book title is identical to a completed lesson key.

No live Supabase (per tests/ rules). The fake mirrors exactly the semantics the handlers rely on:
the (user_id, content_type, item_key) uniqueness, ON CONFLICT DO NOTHING on upsert, filtered
select, and filtered delete. If the handlers ever dropped a content_type / user_id filter, these
tests would fail.
"""

import pytest

from app.api.v1.endpoints.learn import (
    BOOKMARK_CONTENT_TYPE,
    LEARN_CONTENT_TYPES,
    add_book_bookmark,
    complete_learn_item,
    get_book_bookmarks,
    get_learn_progress,
    remove_book_bookmark,
)
from app.schemas.bookmarks import BookmarkRequest
from app.schemas.learn_progress import CompleteLearnItemRequest

TABLE = "user_learn_progress"
U1 = "user-1"
U2 = "user-2"


# --- Minimal in-memory fake of the supabase-py query builder ----------------------------------


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table):
        self._table = table
        self._filters = {}
        self._op = "select"
        self._payload = None
        self._order = None

    def select(self, *_cols):
        self._op = "select"
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def upsert(self, payload, on_conflict=None, ignore_duplicates=False):
        self._op = "upsert"
        self._payload = payload
        # on_conflict / ignore_duplicates encode ON CONFLICT DO NOTHING; the fake always does
        # nothing on a uniqueness clash, mirroring ignore_duplicates=True.
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        return self._table._run(self)


class _FakeTable:
    def __init__(self):
        self.rows = []
        self._seq = 0

    def _run(self, q):
        matched = [r for r in self.rows if all(r.get(k) == v for k, v in q._filters.items())]
        if q._op == "select":
            if q._order:
                col, desc = q._order
                matched = sorted(matched, key=lambda r: r.get(col), reverse=desc)
            return _Result([dict(r) for r in matched])
        if q._op == "upsert":
            p = q._payload
            uniq = (p["user_id"], p["content_type"], p["item_key"])
            exists = any(
                (r["user_id"], r["content_type"], r["item_key"]) == uniq for r in self.rows
            )
            if not exists:
                self._seq += 1
                self.rows.append({**p, "completed_at": self._seq})  # int stands in for a timestamp
            return _Result([])
        if q._op == "delete":
            self.rows = [
                r for r in self.rows if not all(r.get(k) == v for k, v in q._filters.items())
            ]
            return _Result([])
        raise AssertionError(f"unsupported op {q._op!r}")  # never silently no-op


class FakeSupabase:
    def __init__(self):
        self._tables = {}

    def table(self, name):
        return _Query(self._tables.setdefault(name, _FakeTable()))

    def rows(self, name=TABLE):
        return list(self._tables.setdefault(name, _FakeTable()).rows)


# --- helpers ----------------------------------------------------------------------------------


def _user(uid):
    return {"id": uid}


async def _mark(fake, content_type, key, uid=U1):
    await complete_learn_item(
        content_type=content_type,
        request=CompleteLearnItemRequest(key=key),
        user=_user(uid),
        supabase=fake,
    )


async def _add_bookmark(fake, title, uid=U1):
    await add_book_bookmark(BookmarkRequest(book_key=title), _user(uid), fake)


async def _remove_bookmark(fake, title, uid=U1):
    await remove_book_bookmark(BookmarkRequest(book_key=title), _user(uid), fake)


async def _progress(fake, content_type, uid=U1):
    resp = await get_learn_progress(content_type=content_type, user=_user(uid), supabase=fake)
    return set(resp.keys)


async def _bookmarks(fake, uid=U1):
    resp = await get_book_bookmarks(user=_user(uid), supabase=fake)
    return resp.bookmarks


# --- tests ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_four_content_types_coexist_with_identical_item_key():
    """The SAME item_key under every content_type stays in four independent rows."""
    fake = FakeSupabase()
    await _mark(fake, "book_core", "shared-key")
    await _mark(fake, "journey_lesson", "shared-key")
    await _mark(fake, "money_move", "shared-key")
    await _add_bookmark(fake, "shared-key")

    assert await _progress(fake, "book_core") == {"shared-key"}
    assert await _progress(fake, "journey_lesson") == {"shared-key"}
    assert await _progress(fake, "money_move") == {"shared-key"}
    assert await _bookmarks(fake) == ["shared-key"]
    assert len(fake.rows()) == 4  # one row per content_type, none merged


@pytest.mark.asyncio
async def test_unbookmark_never_erases_any_progress():
    """THE safety guarantee: removing a bookmark deletes only book_bookmark rows — even when the
    bookmarked title is byte-for-byte identical to a completed lesson/book/money-move key."""
    fake = FakeSupabase()
    await _mark(fake, "book_core", "1-3")
    await _mark(fake, "journey_lesson", "Compounding Basics")
    await _mark(fake, "money_move", "the-big-short")
    await _add_bookmark(fake, "Rich Dad Poor Dad")
    await _add_bookmark(fake, "Compounding Basics")  # same string as a completed lesson
    await _add_bookmark(fake, "1-3")                  # same string as a completed book core

    await _remove_bookmark(fake, "Rich Dad Poor Dad")
    await _remove_bookmark(fake, "Compounding Basics")
    await _remove_bookmark(fake, "1-3")

    # Every completion survived untouched.
    assert await _progress(fake, "book_core") == {"1-3"}
    assert await _progress(fake, "journey_lesson") == {"Compounding Basics"}
    assert await _progress(fake, "money_move") == {"the-big-short"}
    # Bookmarks all gone.
    assert await _bookmarks(fake) == []


@pytest.mark.asyncio
async def test_progress_endpoint_is_walled_off_from_bookmarks():
    """/progress only accepts completion types; it can neither write nor read book_bookmark."""
    fake = FakeSupabase()
    await _mark(fake, "book_bookmark", "Rich Dad Poor Dad")  # try to sneak a bookmark in
    assert fake.rows() == []                                 # nothing written
    assert await _bookmarks(fake) == []                      # bookmarks still empty
    # The firewall constant excludes the bookmark type.
    assert "book_bookmark" not in LEARN_CONTENT_TYPES
    assert BOOKMARK_CONTENT_TYPE == "book_bookmark"


@pytest.mark.asyncio
async def test_bookmarks_returned_most_recent_first():
    fake = FakeSupabase()
    await _add_bookmark(fake, "A")
    await _add_bookmark(fake, "B")
    await _add_bookmark(fake, "C")
    assert await _bookmarks(fake) == ["C", "B", "A"]  # ORDER BY completed_at DESC


@pytest.mark.asyncio
async def test_add_and_complete_are_idempotent():
    fake = FakeSupabase()
    await _add_bookmark(fake, "A")
    await _add_bookmark(fake, "A")
    await _mark(fake, "book_core", "1-1")
    await _mark(fake, "book_core", "1-1")
    assert await _bookmarks(fake) == ["A"]
    assert await _progress(fake, "book_core") == {"1-1"}
    assert len(fake.rows()) == 2


@pytest.mark.asyncio
async def test_per_user_isolation():
    """Identical titles for different users are independent; removing one user's row leaves the
    other's intact, and never touches the other user's progress."""
    fake = FakeSupabase()
    await _add_bookmark(fake, "Shared Title", uid=U1)
    await _mark(fake, "journey_lesson", "U2 Lesson", uid=U2)
    await _add_bookmark(fake, "Shared Title", uid=U2)

    await _remove_bookmark(fake, "Shared Title", uid=U1)

    assert await _bookmarks(fake, uid=U1) == []
    assert await _bookmarks(fake, uid=U2) == ["Shared Title"]
    assert await _progress(fake, "journey_lesson", uid=U2) == {"U2 Lesson"}
    assert await _progress(fake, "journey_lesson", uid=U1) == set()


@pytest.mark.asyncio
async def test_blank_keys_are_ignored():
    fake = FakeSupabase()
    await _add_bookmark(fake, "   ")
    await _mark(fake, "book_core", "")
    assert fake.rows() == []


@pytest.mark.asyncio
async def test_money_moves_progress_unaffected_by_book_features():
    """Money Moves progress is independent today; a future money-move bookmark would be its own
    content_type and equally isolated (none exists yet)."""
    fake = FakeSupabase()
    await _mark(fake, "money_move", "psychology-of-money")
    await _add_bookmark(fake, "psychology-of-money")
    await _remove_bookmark(fake, "psychology-of-money")
    assert await _progress(fake, "money_move") == {"psychology-of-money"}
    assert await _bookmarks(fake) == []
