"""
The ONE saved Money Move topic — GET/PUT/DELETE /api/v1/learn/money-move-bookmark.

Exercises the REAL handlers in app.api.v1.endpoints.learn against an in-memory fake of the
Supabase query builder (no live Supabase, per .claude/rules/testing.md). The fake mirrors exactly
the semantics the handlers rely on: (user_id, content_type, item_key) uniqueness, ON CONFLICT DO
NOTHING on upsert, composite ORDER BY, LIMIT, and filtered delete including `neq`.

What is actually at stake in each case:

  * PUT is REPLACE, and it is the only reason the iOS store can be 200 lines instead of 450 —
    if the delete-others half regressed, two rows would coexist and the client would render a
    saved topic that flips between requests.
  * A failure must NEVER answer 200 with `bookmark: null`. The client treats a 2xx as
    authoritative and clears its unsynced flag, so a soft failure silently discards the save.
  * The delete is scoped to a NAMED slug and to this content_type — an unscoped one would erase
    a completion row, or another device's newer bookmark.
"""

import pytest
from fastapi.responses import JSONResponse

from app.api.v1.endpoints.learn import (
    MONEY_MOVE_BOOKMARK_CONTENT_TYPE,
    add_book_bookmark,
    complete_learn_item,
    get_learn_progress,
    get_money_move_bookmark,
    remove_money_move_bookmark,
    set_money_move_bookmark,
)
from app.schemas.bookmarks import (
    BookmarkRequest,
    MoneyMoveBookmarkRequest,
    MoneyMoveBookmarkResponse,
)
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
        self._eq = {}
        self._neq = {}
        self._op = "select"
        self._payload = None
        # LIST, not a single pair: PostgREST accumulates chained `.order(...)` into a composite
        # ORDER BY. A fake that overwrote would sort by the LAST key alone and silently report a
        # bug in correct production code.
        self._order: list[tuple[str, bool]] = []
        self._limit = None

    def select(self, *_cols):
        self._op = "select"
        return self

    def eq(self, col, val):
        self._eq[col] = val
        return self

    def neq(self, col, val):
        self._neq[col] = val
        return self

    def order(self, col, desc=False):
        self._order.append((col, desc))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def upsert(self, payload, on_conflict=None, ignore_duplicates=False):
        self._op = "upsert"
        self._payload = payload
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
        # Forces a timestamp collision on the next N upserts, so the tie-break can be tested.
        self.freeze_clock = False

    def _matches(self, r, q):
        return all(r.get(k) == v for k, v in q._eq.items()) and all(
            r.get(k) != v for k, v in q._neq.items()
        )

    def _run(self, q):
        matched = [r for r in self.rows if self._matches(r, q)]
        if q._op == "select":
            # Least-significant key first; Python's sort is stable, so this is a true composite
            # ordering rather than last-key-wins.
            for col, desc in reversed(q._order):
                matched = sorted(matched, key=lambda r: r.get(col), reverse=desc)
            if q._limit is not None:
                matched = matched[: q._limit]
            return _Result([dict(r) for r in matched])
        if q._op == "upsert":
            p = q._payload
            uniq = (p["user_id"], p["content_type"], p["item_key"])
            if not any((r["user_id"], r["content_type"], r["item_key"]) == uniq for r in self.rows):
                if not self.freeze_clock:
                    self._seq += 1
                self.rows.append({**p, "completed_at": self._seq})
            return _Result([])
        if q._op == "delete":
            self.rows = [r for r in self.rows if not self._matches(r, q)]
            return _Result([])
        raise AssertionError(f"unsupported op {q._op!r}")  # never silently no-op


class FakeSupabase:
    def __init__(self):
        self._tables = {}

    def table(self, name):
        return _Query(self._tables.setdefault(name, _FakeTable()))

    def raw(self, name=TABLE):
        return self._tables.setdefault(name, _FakeTable())

    def rows(self, name=TABLE):
        return list(self.raw(name).rows)


class ExplodingSupabase:
    """Every query raises — stands in for a Supabase outage / transient 520."""

    def __init__(self, exc=None):
        self._exc = exc or RuntimeError("supabase is down")

    def table(self, _name):
        raise self._exc


# --- helpers ----------------------------------------------------------------------------------


def _user(uid=U1):
    return {"id": uid}


async def _get(fake, uid=U1):
    return await get_money_move_bookmark(user=_user(uid), supabase=fake)


async def _set(fake, slug, uid=U1):
    return await set_money_move_bookmark(MoneyMoveBookmarkRequest(slug=slug), _user(uid), fake)


async def _remove(fake, slug, uid=U1):
    return await remove_money_move_bookmark(MoneyMoveBookmarkRequest(slug=slug), _user(uid), fake)


def _bookmark_rows(fake, uid=U1):
    return [
        r
        for r in fake.rows()
        if r["user_id"] == uid and r["content_type"] == MONEY_MOVE_BOOKMARK_CONTENT_TYPE
    ]


# --- happy path + the single-value invariant --------------------------------------------------


@pytest.mark.asyncio
async def test_nothing_saved_reads_as_null():
    assert (await _get(FakeSupabase())).bookmark is None


@pytest.mark.asyncio
async def test_put_saves_and_get_reads_it_back():
    fake = FakeSupabase()
    assert (await _set(fake, "nvidia-ai-dominance")).bookmark == "nvidia-ai-dominance"
    assert (await _get(fake)).bookmark == "nvidia-ai-dominance"


@pytest.mark.asyncio
async def test_put_REPLACES_rather_than_appending():
    """The whole design rests on this: saving B leaves exactly ONE row, and it is B."""
    fake = FakeSupabase()
    await _set(fake, "a-slug")
    await _set(fake, "b-slug")

    assert (await _get(fake)).bookmark == "b-slug"
    assert [r["item_key"] for r in _bookmark_rows(fake)] == ["b-slug"]


@pytest.mark.asyncio
async def test_put_is_idempotent():
    fake = FakeSupabase()
    await _set(fake, "a-slug")
    await _set(fake, "a-slug")
    assert len(_bookmark_rows(fake)) == 1
    assert (await _get(fake)).bookmark == "a-slug"


@pytest.mark.asyncio
async def test_get_is_deterministic_when_two_rows_share_a_timestamp():
    """
    Two devices can PUT concurrently, leaving two rows with the same `completed_at` for the
    moment before the deletes land. Without the `item_key` tiebreak, which one this endpoint
    returns would be up to Postgres — the saved topic would flip between two identical requests.
    """
    fake = FakeSupabase()
    fake.raw().freeze_clock = True
    await _set(fake, "zebra-slug")
    # Second PUT deleted the first, so re-insert it directly to model the racing write.
    fake.raw().rows.append(
        {
            "user_id": U1,
            "content_type": MONEY_MOVE_BOOKMARK_CONTENT_TYPE,
            "item_key": "alpha-slug",
            "completed_at": fake.raw()._seq,
        }
    )
    assert len(_bookmark_rows(fake)) == 2
    first = (await _get(fake)).bookmark
    assert first == "alpha-slug"          # completed_at ties -> item_key ASC decides
    assert (await _get(fake)).bookmark == first   # and it is the SAME answer every call


# --- removal ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_clears_the_saved_topic():
    fake = FakeSupabase()
    await _set(fake, "a-slug")
    assert (await _remove(fake, "a-slug")).bookmark is None
    assert _bookmark_rows(fake) == []


@pytest.mark.asyncio
async def test_delete_of_a_stale_slug_leaves_the_current_one_alone():
    """
    An un-bookmark queued offline on one device lands long after the user saved something else on
    another. Scoping the delete to the NAMED slug is what stops it wiping a topic the user never
    touched — a bare "clear everything for this content_type" would.
    """
    fake = FakeSupabase()
    await _set(fake, "old-slug")
    await _set(fake, "new-slug")
    assert (await _remove(fake, "old-slug")).bookmark == "new-slug"
    assert [r["item_key"] for r in _bookmark_rows(fake)] == ["new-slug"]


@pytest.mark.asyncio
async def test_delete_of_an_unknown_slug_is_a_noop():
    fake = FakeSupabase()
    await _set(fake, "a-slug")
    assert (await _remove(fake, "never-saved")).bookmark == "a-slug"


# --- outlier inputs ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", ["", "   ", "\t\n"])
@pytest.mark.asyncio
async def test_a_blank_slug_never_creates_a_row(slug):
    """
    A blank slug is not "clear the bookmark" — DELETE is. Writing it would create a row no
    article can ever resolve, i.e. a permanently dangling saved topic. It must also not wipe an
    existing one on its way through.
    """
    fake = FakeSupabase()
    await _set(fake, "real-slug")
    assert (await _set(fake, slug)).bookmark == "real-slug"
    assert [r["item_key"] for r in _bookmark_rows(fake)] == ["real-slug"]


@pytest.mark.parametrize("slug", ["", "   "])
@pytest.mark.asyncio
async def test_a_blank_slug_delete_does_not_clear_the_saved_topic(slug):
    fake = FakeSupabase()
    await _set(fake, "real-slug")
    assert (await _remove(fake, slug)).bookmark == "real-slug"


@pytest.mark.asyncio
async def test_a_slug_is_trimmed_on_the_way_in():
    fake = FakeSupabase()
    await _set(fake, "  padded-slug  ")
    assert [r["item_key"] for r in _bookmark_rows(fake)] == ["padded-slug"]


# --- failures must be LOUD --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_failure_is_an_error_response_not_a_null_bookmark():
    """
    A 200-with-null reads to the client as "nothing is saved". It adopts that, clears its local
    value, and the user's saved topic is gone with no error anywhere. Same reasoning the book
    bookmark routes carry.
    """
    result = await _get(ExplodingSupabase())
    assert isinstance(result, JSONResponse)
    assert result.status_code >= 400


@pytest.mark.asyncio
async def test_put_failure_is_an_error_response():
    result = await _set(ExplodingSupabase(), "a-slug")
    assert isinstance(result, JSONResponse)
    assert result.status_code >= 400


@pytest.mark.asyncio
async def test_delete_failure_is_an_error_response():
    """A 2xx here makes the client retire its pending removal, and the next hydrate resurrects
    the bookmark the user just cleared."""
    result = await _remove(ExplodingSupabase(), "a-slug")
    assert isinstance(result, JSONResponse)
    assert result.status_code >= 400


# --- isolation from the other content types ---------------------------------------------------


@pytest.mark.asyncio
async def test_saving_a_topic_never_touches_its_completion_row():
    """
    `money_move` (completed) and `money_move_bookmark` (saved) share the table AND the slug key
    space, so PUT's delete-others half runs across rows whose item_key differs but whose
    content_type must protect them.
    """
    fake = FakeSupabase()
    await complete_learn_item(
        content_type="money_move",
        request=CompleteLearnItemRequest(key="finished-slug"),
        user=_user(),
        supabase=fake,
    )
    await _set(fake, "saved-slug")
    await _set(fake, "another-slug")     # the replace pass runs twice

    progress = await get_learn_progress(content_type="money_move", user=_user(), supabase=fake)
    assert set(progress.keys) == {"finished-slug"}
    assert (await _get(fake)).bookmark == "another-slug"


@pytest.mark.asyncio
async def test_removing_a_topic_never_touches_a_book_bookmark_with_the_same_key():
    fake = FakeSupabase()
    await add_book_bookmark(BookmarkRequest(book_key="shared-key"), _user(), fake)
    await _set(fake, "shared-key")
    await _remove(fake, "shared-key")

    from app.api.v1.endpoints.learn import get_book_bookmarks

    assert (await get_book_bookmarks(user=_user(), supabase=fake)).bookmarks == ["shared-key"]
    assert (await _get(fake)).bookmark is None


@pytest.mark.asyncio
async def test_one_users_save_does_not_disturb_another_users():
    """PUT's `neq(item_key, ...)` delete must still be scoped by user_id."""
    fake = FakeSupabase()
    await _set(fake, "u1-slug", uid=U1)
    await _set(fake, "u2-slug", uid=U2)

    assert (await _get(fake, uid=U1)).bookmark == "u1-slug"
    assert (await _get(fake, uid=U2)).bookmark == "u2-slug"


# --- schema parity (iOS decodes `bookmark` as String?) ----------------------------------------


def test_response_shape_is_exactly_what_ios_decodes():
    dumped = MoneyMoveBookmarkResponse(bookmark="a-slug").model_dump()
    assert set(dumped.keys()) == {"bookmark"}
    assert dumped["bookmark"] == "a-slug"


def test_response_null_is_valid():
    assert MoneyMoveBookmarkResponse().model_dump() == {"bookmark": None}
    assert MoneyMoveBookmarkResponse.model_validate({"bookmark": None}).bookmark is None
    # iOS declares `let bookmark: String?`, so a MISSING key must decode too.
    assert MoneyMoveBookmarkResponse.model_validate({}).bookmark is None


def test_request_shape_is_what_ios_sends():
    assert MoneyMoveBookmarkRequest.model_validate({"slug": "a-slug"}).slug == "a-slug"
