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

    # ── Narration gate: PERMANENTLY OPEN for the Journey ─────────────────────
    # Journey narration is free on every tier including signed-out guests
    # (services/entitlements.JOURNEY_AUDIO_UNLOCKED_TIERS). Nothing writes these two
    # fields any more — `redact_journey` was the only writer and it was deleted — so
    # they are always False/None on the wire.
    #
    # Retained rather than removed for two reasons: already-shipped builds decode this
    # envelope, and MoneyMovesResponse still carries the identical pair for narration
    # that IS gated. Removing them here would be a wire break for no gain.
    audio_locked: bool = False
    tier_required: Optional[str] = None
