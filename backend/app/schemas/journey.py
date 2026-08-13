"""
Pydantic schemas for the Investor Journey learning content.

The endpoint serves the authored lessons from the `lessons` table. Each lesson's
`story_content` is a JSONB blob already shaped for the iOS decoder
({lessonLabel, lessonNumber, totalLessonsInLevel, estimatedMinutes, cards[]}),
where each card carries its media URLs (audioUrl / imageUrl / videoUrl). We pass
story_content through as-is so the iOS Codable models can decode it directly.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class JourneyLessonResponse(BaseModel):
    """One lesson row: skeleton metadata + its full story content."""

    id: str
    title: str
    description: Optional[str] = None
    level: str  # foundation | analysis | strategies | mastery
    duration_minutes: Optional[int] = None
    category: str = "standard"
    sort_order: int = 0
    # Passthrough JSONB (camelCase keys) — see module docstring.
    story_content: Optional[Dict[str, Any]] = None


class JourneyResponse(BaseModel):
    """All Investor Journey lessons, ordered by level then sort_order."""

    lessons: List[JourneyLessonResponse]

    # ── Narration gate (services/entitlements.learn_audio_unlocked) ──────────
    # TEXT is free on every tier; the produced narration is Pro/Max. When locked,
    # each card's `audioUrl` and `readAlongWords` are stripped server-side, so the
    # client renders the full lesson as unhighlighted prose and shows a lock on the
    # play control instead of a silent, broken one.
    #
    # Defaulted so an already-shipped build decodes this response unchanged.
    audio_locked: bool = False
    tier_required: Optional[str] = None      # "pro" when locked
