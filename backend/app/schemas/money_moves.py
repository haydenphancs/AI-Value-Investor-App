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

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class MoneyMovesResponse(BaseModel):
    """All Money Moves articles, ordered by sort_order.

    Each item is a row's `content` dict (camelCase passthrough). Kept as opaque dicts
    so the article shape lives in exactly one place — the iOS MoneyMoveArticleDTO.
    """

    articles: List[Dict[str, Any]]

    # ── Narration gate (services/entitlements.learn_audio_unlocked) ──────────
    # TEXT is free on every tier; narration is Pro/Max. When locked, each article's
    # `audioUrl` / `audioDurationSeconds` and every block's read-along spans are
    # stripped server-side.
    #
    # This is SEPARATE from each article's `hasAudioVersion`, and the distinction is
    # load-bearing: that flag means "narration exists" and the client hides the Listen
    # control entirely when it is false. Locked is a third state — narration exists,
    # you can't play it yet — so it needs its own signal, or the upgrade offer is
    # invisible on exactly the articles that would sell it.
    #
    # Defaulted so an already-shipped build decodes this response unchanged.
    audio_locked: bool = False
    tier_required: Optional[str] = None      # "pro" when locked
