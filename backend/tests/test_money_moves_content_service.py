"""
Service-behavior tests for MoneyMovesContentService._load (the runtime serve path).

Guards the degraded-path hardening added for the Learn-audio playback review: a single
malformed DB row (e.g. a non-dict `content` blob from a bad/out-of-band write) must NOT
collapse the ENTIRE Money Moves catalog to empty — it must be skipped + logged while every
well-formed article still serves and gets its audio overlay. Before the fix, the per-row
overlay `content["audioUrl"] = ...` raised TypeError on a non-dict content, propagated out of
_load, and on a cold cache returned `articles: []` (silent no-audio, just-published article
invisible).

No network / Supabase — `_fetch_rows` is stubbed with synthetic rows.
"""
from __future__ import annotations

import pytest

from app.services.money_moves_content_service import MoneyMovesContentService


def _good_row(slug: str, order: int, audio: bool = True) -> dict:
    row: dict = {"slug": slug, "sort_order": order, "content": {"slug": slug, "title": slug.title()}}
    if audio:
        row["audio_url"] = f"https://media.example/{slug}.m4a"
        row["audio_duration_seconds"] = 123
    return row


@pytest.mark.asyncio
async def test_load_skips_malformed_rows_keeps_catalog():
    """One bad row must not nuke the whole catalog — good articles still serve, in sort order."""
    svc = MoneyMovesContentService()
    rows = [
        _good_row("alpha", 0),
        {"slug": "bad-list", "sort_order": 1, "content": [{"oops": 1}]},  # truthy non-dict -> would TypeError on overlay
        {"slug": "bad-str", "sort_order": 2, "content": "not-a-dict"},     # truthy non-dict
        {"slug": "unseeded", "sort_order": 3, "content": None},            # not yet seeded
        _good_row("beta", 4, audio=False),
    ]
    svc._fetch_rows = lambda: rows  # type: ignore[assignment]
    resp = await svc._load()
    slugs = [a.get("slug") for a in resp.articles]
    assert slugs == ["alpha", "beta"]  # both well-formed rows survive; the 3 bad rows are skipped
    assert resp.articles, "catalog must not collapse to empty because of one malformed row"


@pytest.mark.asyncio
async def test_load_overlays_audio_url_and_duration_onto_content():
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [_good_row("alpha", 0)]  # type: ignore[assignment]
    resp = await svc._load()
    art = resp.articles[0]
    assert art["audioUrl"] == "https://media.example/alpha.m4a"
    assert art["hasAudioVersion"] is True
    assert art["audioDurationSeconds"] == 123


@pytest.mark.asyncio
async def test_load_no_audio_columns_leaves_content_untouched():
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [_good_row("beta", 0, audio=False)]  # type: ignore[assignment]
    resp = await svc._load()
    art = resp.articles[0]
    assert "audioUrl" not in art and "audioDurationSeconds" not in art


@pytest.mark.asyncio
async def test_load_empty_rows_returns_empty_list():
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: []  # type: ignore[assignment]
    resp = await svc._load()
    assert resp.articles == []
