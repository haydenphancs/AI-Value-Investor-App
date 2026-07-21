"""
Learn progress + bookmark endpoints — contract and degradation.

These routes back all three Learn features (Books / Investor Journey / Money Moves) through one
unified `user_learn_progress` table. The iOS stores treat their local cache as the source of truth
and union-merge the server's set in, so the shapes below are load-bearing:

  * `keys` / `bookmarks` must ALWAYS be a list of strings — never null, never a list containing
    null. The Swift DTOs declare `let keys: [String]` (non-Optional), so a null element is a
    decode crash that silently wipes the whole synced set.
  * A backend FAILURE must surface as a typed error, NOT as `{"keys": []}`. An empty list is
    indistinguishable from "this user has completed nothing", so the iOS reconcile would treat
    every local key as unsynced and re-POST it against an already-failing backend. An unknown
    `content_type` still degrades to an empty list — that is a stale client, not a failure.

Also pins that every route runs its synchronous Supabase call off the event loop: these are hit on
every Learn screen open, and blocking here stalls every other in-flight request on the instance.
"""

import ast
import asyncio
import inspect
import pathlib

import pytest

from app.api.v1.endpoints import learn as learn_module
from app.api.v1.endpoints.learn import (
    BOOKMARK_CONTENT_TYPE,
    LEARN_CONTENT_TYPES,
    add_book_bookmark,
    complete_learn_item,
    get_book_bookmarks,
    get_learn_progress,
    remove_book_bookmark,
    uncomplete_learn_item,
)
from app.schemas.learn_progress import CompleteLearnItemRequest, LearnProgressResponse

USER = {"id": "11111111-1111-1111-1111-111111111111"}


class _FakeQuery:
    """Chainable stand-in for the Supabase query builder."""

    def __init__(self, table, rows=None, raises=None):
        self._table, self._rows, self._raises = table, rows or [], raises

    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def delete(self, *_a, **_k): return self
    def upsert(self, payload, **_k):
        self._table.upserts.append(payload)
        return self

    def execute(self):
        if self._raises:
            raise self._raises
        return type("R", (), {"data": self._rows})()


class _FakeSupabase:
    def __init__(self, rows=None, raises=None):
        self.rows, self.raises, self.upserts = rows, raises, []

    def table(self, _name):
        return _FakeQuery(self, self.rows, self.raises)


def _run(coro):
    return asyncio.run(coro)


# ── shape: keys is always a clean [String] ────────────────────────────

def test_progress_returns_the_keys_ios_expects():
    sb = _FakeSupabase(rows=[{"item_key": "1-2"}, {"item_key": "3-4"}])
    resp = _run(get_learn_progress(content_type="book_core", user=USER, supabase=sb))
    assert isinstance(resp, LearnProgressResponse)
    assert resp.keys == ["1-2", "3-4"]
    assert all(isinstance(k, str) for k in resp.keys)


def test_progress_empty_table_returns_empty_list_not_null():
    # Swift decodes `let keys: [String]` — a null here is a crash, not an empty state.
    sb = _FakeSupabase(rows=[])
    assert _run(get_learn_progress(content_type="money_move", user=USER, supabase=sb)).keys == []


@pytest.mark.parametrize("bad_type", ["", "nope", "BOOK_CORE", "book core", "../etc", "x" * 200])
def test_unknown_content_type_degrades_to_empty_never_errors(bad_type):
    # A stale app version asking for a retired content_type must not 500.
    sb = _FakeSupabase(rows=[{"item_key": "leak"}])
    assert _run(get_learn_progress(content_type=bad_type, user=USER, supabase=sb)).keys == []


def test_backend_read_failure_returns_a_typed_error_not_200_with_empty():
    """A failed READ must be distinguishable from "nothing completed".

    Returning `{"keys": []}` on a Supabase failure made iOS `hydrate()` succeed
    with an empty remote set — so the reconcile pass concluded that every locally
    known key was unsynced and re-POSTed up to 25 of them per store (3 stores per
    Learn open) against a backend that was already failing. A typed error lets the
    client keep its local cache and back off.
    """
    sb = _FakeSupabase(raises=RuntimeError("supabase down"))
    resp = _run(get_learn_progress(content_type="book_core", user=USER, supabase=sb))
    assert not isinstance(resp, LearnProgressResponse)
    assert getattr(resp, "status_code", 200) >= 400


def test_bookmarks_read_failure_returns_a_typed_error():
    sb = _FakeSupabase(raises=RuntimeError("supabase down"))
    resp = _run(get_book_bookmarks(user=USER, supabase=sb))
    assert getattr(resp, "status_code", 200) >= 400


# ── writes: idempotent, validated, and never 500 ──────────────────────

def test_complete_writes_the_expected_row():
    sb = _FakeSupabase(rows=[{"item_key": "slug-a"}])
    _run(complete_learn_item(
        content_type="money_move", request=CompleteLearnItemRequest(key="slug-a"),
        user=USER, supabase=sb,
    ))
    assert sb.upserts == [{
        "user_id": USER["id"], "content_type": "money_move", "item_key": "slug-a",
    }]


@pytest.mark.parametrize("key", ["", "   ", "\n\t"])
def test_blank_key_is_not_written(key):
    # A blank item_key would be an unremovable ghost row in the completion log.
    sb = _FakeSupabase(rows=[])
    _run(complete_learn_item(
        content_type="book_core", request=CompleteLearnItemRequest(key=key),
        user=USER, supabase=sb,
    ))
    assert sb.upserts == []


def test_key_is_trimmed_before_writing():
    # iOS trims too; if only one side trimmed, "  slug" and "slug" would be different rows and a
    # completion would never appear complete.
    sb = _FakeSupabase(rows=[])
    _run(complete_learn_item(
        content_type="money_move", request=CompleteLearnItemRequest(key="  slug-a  "),
        user=USER, supabase=sb,
    ))
    assert sb.upserts[0]["item_key"] == "slug-a"


def test_unknown_content_type_is_not_written():
    sb = _FakeSupabase(rows=[])
    _run(complete_learn_item(
        content_type="not_a_type", request=CompleteLearnItemRequest(key="k"),
        user=USER, supabase=sb,
    ))
    assert sb.upserts == []


def test_a_failed_write_surfaces_rather_than_reporting_success():
    """A write failure must not come back as a cheerful 200.

    The follow-up read fails too here, so the endpoint surfaces a typed error and
    the client keeps the completion locally and retries on the next hydrate.
    """
    sb = _FakeSupabase(raises=RuntimeError("write failed"))
    resp = _run(complete_learn_item(
        content_type="book_core", request=CompleteLearnItemRequest(key="1-1"),
        user=USER, supabase=sb,
    ))
    assert getattr(resp, "status_code", 200) >= 400


def test_a_failed_delete_never_reports_the_key_as_still_present():
    """The resurrection bug.

    The delete raises but the follow-up select succeeds and still contains the
    key. Returning 200 with that key made iOS read it as "delete confirmed": it
    dropped its tombstone and the next union-merge resurrected the item, flipping
    it back to Completed against the user's explicit tap.
    """
    class _DeleteFails(_FakeSupabase):
        def table(self, _name):
            outer = self

            class _Q(_FakeQuery):
                def delete(self, *_a, **_k):
                    raise RuntimeError("delete failed")

            return _Q(outer, outer.rows)

    sb = _DeleteFails(rows=[{"item_key": "1-1"}])
    resp = _run(uncomplete_learn_item(
        content_type="book_core", request=CompleteLearnItemRequest(key="1-1"),
        user=USER, supabase=sb,
    ))
    assert getattr(resp, "status_code", 200) >= 400, (
        "a failed DELETE reported as 200-with-the-key resurrects the item on iOS"
    )


def test_uncomplete_and_bookmark_writes_are_scoped_to_the_caller():
    # Every write must be filtered by user_id — one user must never mutate another's progress.
    src = inspect.getsource(learn_module)
    for fn in ("uncomplete_learn_item", "remove_book_bookmark"):
        body = src.split(f"async def {fn}")[1].split("\nasync def ")[0]
        assert 'eq("user_id"' in body, f"{fn} does not scope its delete by user_id"


def test_bookmark_add_writes_the_bookmark_content_type():
    sb = _FakeSupabase(rows=[])
    _run(add_book_bookmark(
        request=learn_module.BookmarkRequest(book_key="The Intelligent Investor"),
        user=USER, supabase=sb,
    ))
    assert sb.upserts[0]["content_type"] == BOOKMARK_CONTENT_TYPE
    assert sb.upserts[0]["item_key"] == "The Intelligent Investor"


@pytest.mark.parametrize("key", ["", "  "])
def test_blank_bookmark_key_is_not_written(key):
    sb = _FakeSupabase(rows=[])
    _run(add_book_bookmark(
        request=learn_module.BookmarkRequest(book_key=key), user=USER, supabase=sb,
    ))
    assert sb.upserts == []


# ── event loop: the sync SDK must never run on it ─────────────────────

def test_every_learn_route_runs_supabase_off_the_event_loop():
    """The Supabase SDK is synchronous.

    Called straight from an `async def`, it blocks the event loop for the whole round-trip and
    stalls every other in-flight request on the instance. These routes are hit on every Learn
    screen open, so a direct `.execute()` at the top level of a coroutine is a real regression.
    """
    path = pathlib.Path(learn_module.__file__)
    tree = ast.parse(path.read_text())

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        # Collect the nested sync helpers — an `.execute()` inside one of those is correct,
        # because it is what gets handed to asyncio.to_thread.
        nested = {
            n for fn in ast.walk(node)
            if isinstance(fn, ast.FunctionDef)
            for n in ast.walk(fn)
        }
        for call in ast.walk(node):
            if call in nested:
                continue
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "execute"):
                offenders.append(node.name)

    assert not offenders, (
        f"synchronous Supabase .execute() on the event loop in: {sorted(set(offenders))}"
    )


def test_content_types_are_the_three_learn_features():
    assert LEARN_CONTENT_TYPES == {"book_core", "journey_lesson", "money_move"}
    # Bookmarks deliberately live under their own content_type so they can be REMOVED without
    # the append-only /progress routes being able to "complete" one.
    assert BOOKMARK_CONTENT_TYPE not in LEARN_CONTENT_TYPES
