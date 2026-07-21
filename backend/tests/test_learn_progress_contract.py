"""
Learn progress + bookmark endpoints — contract and degradation.

These routes back all three Learn features (Books / Investor Journey / Money Moves) through one
unified `user_learn_progress` table. The iOS stores treat their local cache as the source of truth
and union-merge the server's set in, so the shapes below are load-bearing:

  * `keys` / `bookmarks` must ALWAYS be a list of strings — never null, never a list containing
    null. The Swift DTOs declare `let keys: [String]` (non-Optional), so a null element is a
    decode crash that silently wipes the whole synced set.
  * A backend hiccup must degrade to an EMPTY list, not a 500 — the iOS side keeps its local
    progress and retries, but a 500 surfaces as an error banner on a screen that is otherwise fine.

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


def test_backend_failure_degrades_to_empty_not_a_500():
    # iOS keeps its local progress and retries; a 500 would show an error on a working screen.
    sb = _FakeSupabase(raises=RuntimeError("supabase down"))
    assert _run(get_learn_progress(content_type="book_core", user=USER, supabase=sb)).keys == []


def test_bookmarks_failure_degrades_to_empty():
    sb = _FakeSupabase(raises=RuntimeError("supabase down"))
    assert _run(get_book_bookmarks(user=USER, supabase=sb)).bookmarks == []


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


def test_a_failed_write_still_returns_a_valid_shape():
    # The iOS store union-merges whatever comes back, so a degraded response must still be a
    # well-formed list — and because the key is absent, the client's reconcile retries it.
    sb = _FakeSupabase(raises=RuntimeError("write failed"))
    resp = _run(complete_learn_item(
        content_type="book_core", request=CompleteLearnItemRequest(key="1-1"),
        user=USER, supabase=sb,
    ))
    assert resp.keys == []


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
