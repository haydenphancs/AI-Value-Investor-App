"""
Book bookmark schemas.

Per-user book bookmarks, stored in the unified user_learn_progress table (migration 067) under
content_type 'book_bookmark'. Bookmarks are toggleable, keyed by the book TITLE (book_key) — the
stable id shared across LibraryBook / EducationBook / SearchBookItem on iOS. The iOS BookmarkStore
holds an ordered (most-recent-first) list of titles and mirrors it into a local cache; the backend
is the cross-device source of truth.
"""

from typing import List

from pydantic import BaseModel


class BookmarkListResponse(BaseModel):
    # Bookmarked book titles, most-recent-first.
    bookmarks: List[str]


class BookmarkRequest(BaseModel):
    book_key: str
