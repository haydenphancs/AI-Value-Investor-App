"""
Book Library reading-progress schemas.

One row per (user, book, core) the learner has completed. Books are keyed by
`curriculum_order` (1..10) since the Book Library content lives in the iOS app, not the DB.
Served by the /api/v1/learn/books endpoints; the iOS BookProgressStore decodes
BookProgressResponse and mirrors it into a local UserDefaults cache.
"""

from typing import List, Optional

from pydantic import BaseModel


class BookCoreProgressItem(BaseModel):
    curriculum_order: int
    core_number: int
    completed_at: Optional[str] = None


class BookProgressResponse(BaseModel):
    items: List[BookCoreProgressItem]
