"""
Unified Learn progress schemas.

One completion log (user_learn_progress) backs all three Learn features. `content_type`
discriminates the feature ('book_core' | 'journey_lesson' | 'money_move') and `item_key` is
that feature's stable key (book: "<order>-<core>", journey: lesson title, money move: slug).
The iOS BookProgressStore / JourneyProgressStore / MoneyMovesProgressStore each hold their own
set of item_keys and mirror it into a local cache.
"""

from typing import List

from pydantic import BaseModel


class LearnProgressResponse(BaseModel):
    keys: List[str]


class CompleteLearnItemRequest(BaseModel):
    key: str
