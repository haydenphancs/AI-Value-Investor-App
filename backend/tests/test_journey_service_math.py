"""
Resilience + transform tests for JourneyContentService._load.

_load turns raw `lessons` rows into the served JourneyResponse. The Investor Journey screen must
degrade gracefully: ONE malformed / out-of-band lesson row must NOT collapse the entire journey.
A bare list comprehension building JourneyLessonResponse would let the FIRST bad row (a NOT-NULL
title/level absent because the row was hand-edited in Studio, or a story_content stored as a JSON
array/string rather than an object) raise a ValidationError, propagate out of _load, and on a cold
cache empty ALL 27 lessons for every user. _load instead skips+logs the bad row and coerces a
non-dict story_content to None, so every other lesson still renders.

No network / Supabase — rows are injected by monkeypatching _fetch_rows.
"""

import pytest

from app.services.journey_content_service import JourneyContentService


def _service_with_rows(rows):
    svc = JourneyContentService()
    svc._fetch_rows = lambda: rows  # bypass Supabase; _load calls it via asyncio.to_thread
    return svc


def _row(title, level="foundation", sort_order=0, story=..., **extra):
    r = {"id": f"id-{title}", "title": title, "level": level, "sort_order": sort_order}
    if story is not ...:  # sentinel: omit the key entirely vs. set it (incl. None)
        r["story_content"] = story
    r.update(extra)
    return r


@pytest.mark.asyncio
async def test_one_bad_row_does_not_drop_the_others():
    """A non-dict story_content in the middle must not take out its neighbors."""
    rows = [
        _row("Good A", sort_order=0, story={"cards": [{"type": "title"}]}),
        _row("Bad", sort_order=1, story=["not", "a", "dict"]),  # JSON array, not an object
        _row("Good B", sort_order=2, story={"cards": [{"type": "content"}]}),
    ]
    resp = await _service_with_rows(rows)._load()
    assert [l.title for l in resp.lessons] == ["Good A", "Bad", "Good B"]
    # The bad row survives with its unusable story_content degraded to None (tile still shows,
    # player falls back to bundled cards) — it is not silently dropped.
    bad = next(l for l in resp.lessons if l.title == "Bad")
    assert bad.story_content is None


@pytest.mark.asyncio
async def test_string_story_content_coerced_to_none():
    rows = [_row("S", story="oops this is a string not JSONB")]
    resp = await _service_with_rows(rows)._load()
    assert len(resp.lessons) == 1
    assert resp.lessons[0].story_content is None


@pytest.mark.asyncio
async def test_none_and_missing_story_content_are_valid():
    rows = [_row("explicit-none", story=None), _row("absent-key")]  # neither has cards yet
    resp = await _service_with_rows(rows)._load()
    assert {l.title for l in resp.lessons} == {"explicit-none", "absent-key"}
    assert all(l.story_content is None for l in resp.lessons)


@pytest.mark.asyncio
async def test_row_missing_required_field_is_skipped_not_fatal():
    """A NOT-NULL column absent (title / level) skips just that row — KeyError is caught."""
    rows = [
        _row("Good", story={"cards": [{"type": "title"}]}),
        {"id": "no-title", "level": "foundation", "sort_order": 5},  # missing title
        {"id": "no-level", "title": "Orphan", "sort_order": 6},      # missing level
    ]
    resp = await _service_with_rows(rows)._load()
    assert [l.title for l in resp.lessons] == ["Good"]


@pytest.mark.asyncio
async def test_all_rows_malformed_yields_empty_but_never_raises():
    rows = [{"id": "x"}, {"id": "y", "title": "t"}]  # both missing required fields
    resp = await _service_with_rows(rows)._load()
    assert resp.lessons == []  # empty, but no exception bubbled up


@pytest.mark.asyncio
async def test_no_rows_yields_empty_journey():
    resp = await _service_with_rows([])._load()
    assert resp.lessons == []


@pytest.mark.asyncio
async def test_ordering_is_by_level_then_sort_order():
    """Display order: foundation < analysis < strategies < mastery, then sort_order within a level."""
    rows = [
        _row("m1", level="mastery", sort_order=1),
        _row("f2", level="foundation", sort_order=2),
        _row("f1", level="foundation", sort_order=1),
        _row("s1", level="strategies", sort_order=1),
        _row("a1", level="analysis", sort_order=1),
    ]
    resp = await _service_with_rows(rows)._load()
    assert [l.title for l in resp.lessons] == ["f1", "f2", "a1", "s1", "m1"]


@pytest.mark.asyncio
async def test_unknown_level_sorts_last_without_crashing():
    """An out-of-enum level (shouldn't happen — it's a DB enum — but be defensive) sorts to the end."""
    rows = [
        _row("known", level="foundation", sort_order=1),
        _row("weird", level="???", sort_order=1),
    ]
    resp = await _service_with_rows(rows)._load()
    assert [l.title for l in resp.lessons] == ["known", "weird"]


@pytest.mark.asyncio
async def test_defaults_applied_for_optional_columns():
    rows = [_row("D", story={"cards": []})]  # no category / duration_minutes provided
    resp = await _service_with_rows(rows)._load()
    lesson = resp.lessons[0]
    assert lesson.category == "standard"       # NOT-NULL default mirrored in the schema
    assert lesson.duration_minutes is None      # genuinely absent
    assert lesson.sort_order == 0
