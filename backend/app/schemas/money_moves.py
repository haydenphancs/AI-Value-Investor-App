"""
Pydantic schemas for the Money Moves reading/listening content.

The endpoint serves authored case-study articles from the `money_move_articles`
table. Each row's `content` is a JSONB blob already shaped for the iOS decoder
(the MoneyMoveArticleDTO: {slug, title, subtitle, category, author, readTimeMinutes,
viewCount, tagLabel, isFeatured, hasAudioVersion, audioUrl, heroGradientColors,
keyHighlights[], sections[], statistics[], comments[], relatedArticles[]}, camelCase
keys). We pass `content` through as-is — overlaying the row's audio_url column when the
narration voice exists — so the iOS Codable models decode it directly.
"""

from typing import Any, Dict, List

from pydantic import BaseModel


class MoneyMovesResponse(BaseModel):
    """All Money Moves articles, ordered by sort_order.

    Each item is a row's `content` dict (camelCase passthrough). Kept as opaque dicts
    so the article shape lives in exactly one place — the iOS MoneyMoveArticleDTO.
    """

    articles: List[Dict[str, Any]]
