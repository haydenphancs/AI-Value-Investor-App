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

**That applies just as hard to the ENTITLED path** (`sign_journey` / `sign_money_moves`).
The endpoints used to answer an entitled caller with a bare `return response`, i.e. the
cache entry itself; rewriting URLs on it in place would pin one caller's short-lived signed
URLs into the shared payload for the next hour, and hand them to everyone.

The precise rule, because the two halves differ and a blanket "everything is copied" would
get one of them "fixed" later:

  • The REDACTORS return an all-new object graph, top to bottom.
  • The SIGNERS return the ORIGINAL sub-objects wherever nothing changed — an empty mapping
    returns `response` itself, and an article with no signable URL is passed through by
    reference. That is safe for exactly the same reason the old bare `return response` was:
    nothing is modified. It is also the reason the entitled path must never grow a
    downstream mutation.

Both are pinned by tests; neither may be relaxed into the other.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from app.schemas.journey import JourneyLessonResponse, JourneyResponse
from app.schemas.money_moves import MoneyMovesResponse
from app.services.learn_audio_urls import parse_storage_url, sign_many

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


# ── The entitled half: hand out SIGNED urls, never the public ones ──────────────────────
#
# Stripping the URL only withholds narration if the object needs a signature to fetch. All
# three buckets are public today (migrations 061/065/068) and every path is derivable from
# the clip names shipped in the app bundle, so the strip above is obscurity until migration
# 128 flips them private — and that flip only works if entitled callers are already being
# served signed URLs. See `learn_audio_urls`.
#
# Shape of every signer here: **collect (pure) → await sign_many → rewrite onto a deep copy
# (pure)**. Keeping the I/O in the middle means neither walk can touch the shared cache
# entry, and a path that fails to sign is simply left as it was found.


def _journey_audio_urls(response: JourneyResponse) -> List[str]:
    """Pure: every card-level ``audioUrl`` in the payload, in no particular order."""
    urls: List[str] = []
    for lesson in response.lessons:
        story = lesson.story_content
        if not isinstance(story, dict):
            continue
        cards = story.get("cards")
        if not isinstance(cards, list):
            continue
        for card in cards:
            if isinstance(card, dict) and isinstance(card.get("audioUrl"), str):
                urls.append(card["audioUrl"])
    return urls


def _money_moves_audio_urls(response: MoneyMovesResponse) -> List[str]:
    """Pure: every article-level ``audioUrl`` in the payload."""
    return [
        a["audioUrl"]
        for a in response.articles
        if isinstance(a, dict) and isinstance(a.get("audioUrl"), str)
    ]


async def _signed_by_url(urls: List[str]) -> Dict[str, str]:
    """Map each public Storage URL to its signed replacement.

    A URL that isn't a public Storage object (already signed, third-party, malformed) and a
    URL whose signing failed are both simply ABSENT — callers leave those untouched, which
    is today's behaviour rather than a broken or missing clip.
    """
    by_pair: Dict[Tuple[str, str], List[str]] = {}
    for url in urls:
        parsed = parse_storage_url(url)
        if parsed is not None:
            by_pair.setdefault(parsed, []).append(url)
    if not by_pair:
        return {}
    signed = await sign_many(by_pair.keys())
    out: Dict[str, str] = {}
    for pair, originals in by_pair.items():
        replacement = signed.get(pair)
        if replacement:
            for original in originals:
                out[original] = replacement
    return out


async def sign_journey(response: JourneyResponse) -> JourneyResponse:
    """Return a NEW JourneyResponse whose card ``audioUrl``s are signed. Never mutates.

    Returned unchanged (and NOT copied) when there is nothing to rewrite — safe precisely
    because nothing was modified.
    """
    mapping = await _signed_by_url(_journey_audio_urls(response))
    if not mapping:
        return response
    lessons: List[JourneyLessonResponse] = []
    for lesson in response.lessons:
        lessons.append(
            lesson.model_copy(
                update={"story_content": _sign_story_content(lesson.story_content, mapping)}
            )
        )
    return JourneyResponse(
        lessons=lessons,
        audio_locked=response.audio_locked,
        tier_required=response.tier_required,
    )


def _sign_story_content(
    story: Optional[Dict[str, Any]], mapping: Dict[str, str]
) -> Optional[Dict[str, Any]]:
    """Copy a lesson's story_content with each card's ``audioUrl`` swapped for its signed form.

    Tolerant of shape for the same reason `_strip_story_content` is: this blob is authored
    content a bad row edit can malform, and a 500 on the Learn tab is worse than one
    un-signed card.
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
        signed = mapping.get(card.get("audioUrl"))
        if signed:
            card["audioUrl"] = signed
    return out


async def sign_money_moves(response: MoneyMovesResponse) -> MoneyMovesResponse:
    """Return a NEW MoneyMovesResponse whose article ``audioUrl``s are signed. Never mutates."""
    mapping = await _signed_by_url(_money_moves_audio_urls(response))
    if not mapping:
        return response
    articles = []
    for article in response.articles:
        if not isinstance(article, dict):
            articles.append(article)
            continue
        signed = mapping.get(article.get("audioUrl"))
        if not signed:
            articles.append(article)
            continue
        out = copy.deepcopy(article)
        out["audioUrl"] = signed
        articles.append(out)
    return MoneyMovesResponse(
        articles=articles,
        audio_locked=response.audio_locked,
        tier_required=response.tier_required,
    )
