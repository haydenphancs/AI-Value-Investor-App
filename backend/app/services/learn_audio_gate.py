"""Wiser (Learn) narration gate — read free, listen with Pro.

Withholds the produced narration from a caller whose plan doesn't include it, while leaving
every word of text intact. See `entitlements.LEARN_AUDIO_UNLOCKED_TIERS` for why the line is
drawn between text and audio rather than around the content.

What is withheld, per product:
  • Journey     — each card's ``audioUrl`` and ``readAlongWords``
  • Money Moves — ``audioUrl`` / ``audioDurationSeconds``, and every block's ``readAlong`` /
                  ``itemsReadAlong``

The read-along timings go with the audio deliberately: they are unusable without it (both
clients derive the highlight from playback time), and they are bulk payload — Journey alone
carries 6,435 word timings.

⚠️ **``hasAudioVersion`` is left ALONE.** It means "narration exists for this article", and
the client hides the Listen control entirely when it is false. Locked has to be a THIRD
state — narration exists, you can't play it yet — or the upgrade offer is invisible on
exactly the articles that would sell it. `audio_locked` on the response envelope carries
that, and an article that genuinely has no narration keeps `hasAudioVersion == false` and
shows nothing at all.

⚠️ **Nothing here may mutate its argument.** Both content services hold a process-wide
1-hour cache of ONE shared object (`journey_content_service._cache`,
`money_moves_content_service._cache`), so an in-place strip would withhold narration from
every PAYING user until the next rebuild — the identical trap `redact_signals` and
`redact_whale_profile` are shaped around. Every path here deep-copies.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional

from app.schemas.journey import JourneyLessonResponse, JourneyResponse
from app.schemas.money_moves import MoneyMovesResponse

logger = logging.getLogger(__name__)

# Card-level (Journey) and block-level (Money Moves) keys that carry narration.
_JOURNEY_CARD_AUDIO_KEYS = ("audioUrl", "readAlongWords")
_MONEY_MOVES_ARTICLE_AUDIO_KEYS = ("audioUrl", "audioDurationSeconds")
_MONEY_MOVES_BLOCK_AUDIO_KEYS = ("readAlong", "itemsReadAlong")


def redact_journey(response: JourneyResponse, tier_required: str) -> JourneyResponse:
    """Return a NEW JourneyResponse with narration withheld. Never mutates ``response``.

    Journey's ``audioUrl`` is not a request-time overlay — `seed_journey.py` bakes it into
    the `story_content` JSONB per card — so this walks the cards rather than clearing a
    column.
    """
    lessons: List[JourneyLessonResponse] = []
    for lesson in response.lessons:
        lessons.append(
            lesson.model_copy(update={"story_content": _strip_story_content(lesson.story_content)})
        )
    return JourneyResponse(lessons=lessons, audio_locked=True, tier_required=tier_required)


def _strip_story_content(story: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Copy a lesson's story_content with every card's narration removed.

    Tolerant of shape: a lesson with no story_content, no `cards` key, or a non-dict card
    passes through untouched rather than raising — this blob is authored content that a bad
    row edit can malform, and a 500 on the Learn tab is worse than one un-stripped card.
    """
    if not isinstance(story, dict):
        return story
    out = copy.deepcopy(story)
    cards = out.get("cards")
    if not isinstance(cards, list):
        return out
    for card in cards:
        if not isinstance(card, dict):
            continue
        for key in _JOURNEY_CARD_AUDIO_KEYS:
            card.pop(key, None)
    return out


def redact_money_moves(
    response: MoneyMovesResponse, tier_required: str
) -> MoneyMovesResponse:
    """Return a NEW MoneyMovesResponse with narration withheld. Never mutates ``response``."""
    articles = [_strip_article(a) for a in response.articles]
    return MoneyMovesResponse(
        articles=articles, audio_locked=True, tier_required=tier_required
    )


def _strip_article(article: Any) -> Any:
    """Copy one article dict with its narration URL and read-along spans removed."""
    if not isinstance(article, dict):
        return article
    out = copy.deepcopy(article)
    for key in _MONEY_MOVES_ARTICLE_AUDIO_KEYS:
        out.pop(key, None)

    sections = out.get("sections")
    if not isinstance(sections, list):
        return out
    for section in sections:
        if not isinstance(section, dict):
            continue
        blocks = section.get("content")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for key in _MONEY_MOVES_BLOCK_AUDIO_KEYS:
                block.pop(key, None)
    return out
