"""
Learn bookmark schemas.

Per-user book bookmarks, stored in the unified user_learn_progress table (migration 067) under
content_type 'book_bookmark'. Bookmarks are toggleable, keyed by the book TITLE (book_key) — the
stable id shared across LibraryBook / EducationBook / SearchBookItem on iOS. The iOS BookmarkStore
holds an ordered (most-recent-first) list of titles and mirrors it into a local cache; the backend
is the cross-device source of truth.
"""

from typing import List, Optional

from pydantic import BaseModel


class BookmarkListResponse(BaseModel):
    # Bookmarked book titles, most-recent-first.
    bookmarks: List[str]


class BookmarkRequest(BaseModel):
    book_key: str


# --- Money Moves bookmark -----------------------------------------------------------------------
# Same unified table, content_type 'money_move_bookmark', item_key = article SLUG (the canonical id,
# matching the money_move completion log). Unlike book bookmarks this resource is SINGLE-VALUED on
# the wire — the client holds one optional slug, not a list — because the Money Moves screen shows
# exactly one saved topic and PUT replaces rather than appends. `bookmark` is null when nothing is
# saved; iOS decodes it as `String?`.


class MoneyMoveBookmarkResponse(BaseModel):
    bookmark: Optional[str] = None


class MoneyMoveBookmarkRequest(BaseModel):
    slug: str
