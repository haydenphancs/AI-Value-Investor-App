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


# ---------------------------------------------------------------------------
# sort_order overlay — the column must survive into the served blob.
#
# The response carries ONLY the `content` blobs, so a column that never gets overlaid is a
# column the client cannot see. iOS re-sorts with
#   articles.sorted { ($0.sortOrder ?? .max) < ($1.sortOrder ?? .max) }
# (MoneyMovesContentStore.swift), so before the fix an authored row whose blob omitted
# `sortOrder` sank to the BOTTOM of the catalog no matter what `sort_order` said — the
# backend's ordering was computed and then thrown away.
# ---------------------------------------------------------------------------


def _ios_order(articles: list[dict]) -> list[dict]:
    """Re-sort the served articles exactly the way MoneyMovesContentStore.swift does.

    `?? .max` is modelled as a huge sentinel; the slug tiebreak mirrors the one added to the
    Swift comparator (Swift's `sorted(by:)` is not a stable sort, so without it two articles
    sharing a sortOrder can swap between launches).
    """
    return sorted(articles, key=lambda a: (a.get("sortOrder", 1 << 62), a.get("slug") or ""))


@pytest.mark.asyncio
async def test_served_order_survives_the_ios_resort():
    """THE regression guard: re-sorting the response the iOS way must be a no-op.

    Rows are handed over in scrambled fetch order, and NONE of the blobs carry a `sortOrder` of
    their own — the pre-fix shape, where every article tied at `.max` on iOS.

    The slugs are deliberately REVERSE-alphabetical to the intended order. Without that, every
    article tying at `.max` would fall back to the slug tiebreak and land in the right order by
    accident, and this test would pass against the very bug it exists to catch.
    """
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [  # type: ignore[assignment]
        _good_row("bravo", 30),
        _good_row("delta", 10),
        _good_row("alpha", 40),
        _good_row("charlie", 20),
    ]
    resp = await svc._load()
    assert [a["slug"] for a in resp.articles] == ["delta", "charlie", "bravo", "alpha"]
    assert _ios_order(resp.articles) == resp.articles, (
        "iOS re-sorts by content.sortOrder; if the column is not overlaid into the blob the "
        "client order diverges from the served order"
    )


@pytest.mark.asyncio
async def test_sort_order_column_is_overlaid_onto_content():
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [_good_row("alpha", 7)]  # type: ignore[assignment]
    resp = await svc._load()
    assert resp.articles[0]["sortOrder"] == 7


@pytest.mark.asyncio
async def test_column_wins_over_a_conflicting_blob_value():
    """A Studio edit to the column must not be silently overridden by a stale blob value."""
    svc = MoneyMovesContentService()
    row = _good_row("alpha", 2)
    row["content"]["sortOrder"] = 99  # stale value baked into the blob
    svc._fetch_rows = lambda: [row]  # type: ignore[assignment]
    resp = await svc._load()
    assert resp.articles[0]["sortOrder"] == 2


@pytest.mark.asyncio
async def test_null_sort_order_still_yields_a_present_deterministic_value():
    """NULL column is the nastiest case: it sorts FIRST here (0) but LAST on iOS (`?? .max`)."""
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [  # type: ignore[assignment]
        {"slug": "no-order", "sort_order": None, "content": {"slug": "no-order", "title": "N"}},
        _good_row("beta", 5),
    ]
    resp = await svc._load()
    assert resp.articles[0]["sortOrder"] == 0, "NULL must materialize as a concrete value, not absence"
    assert _ios_order(resp.articles) == resp.articles


@pytest.mark.asyncio
async def test_non_numeric_sort_order_does_not_collapse_the_catalog():
    """`rows.sort()` runs OUTSIDE the per-row try — a str/int comparison there would raise
    TypeError, propagate out of _load, and take the whole catalog down with it."""
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [  # type: ignore[assignment]
        {"slug": "junk", "sort_order": "not-a-number", "content": {"slug": "junk", "title": "J"}},
        _good_row("alpha", 1),
    ]
    resp = await svc._load()
    slugs = [a["slug"] for a in resp.articles]
    assert set(slugs) == {"junk", "alpha"}, "one bad sort_order must cost one row's position, not the catalog"
    assert all(isinstance(a["sortOrder"], int) for a in resp.articles)


@pytest.mark.asyncio
async def test_equal_sort_order_is_broken_deterministically_by_slug():
    """Ties must produce a total order, or the two sides (and two launches) can disagree."""
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [  # type: ignore[assignment]
        _good_row("zulu", 5),
        _good_row("alpha", 5),
        _good_row("mike", 5),
    ]
    resp = await svc._load()
    assert [a["slug"] for a in resp.articles] == ["alpha", "mike", "zulu"]
    assert _ios_order(resp.articles) == resp.articles


# ---------------------------------------------------------------------------
# hasAudioVersion must track the COLUMN in both directions.
#
# The overlay only ever set the flag TRUE. A row whose clip was removed (audio_url NULLed)
# but whose blob was authored `hasAudioVersion: true` with a baked `audioUrl` therefore kept
# advertising audio: iOS rendered a Listen button that streamed a dead URL. The column is
# authoritative — it is what the generate/seed pipeline writes.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_audio_claim_is_cleared_when_the_column_is_empty():
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [{                      # type: ignore[assignment]
        "slug": "ghost", "sort_order": 0, "audio_url": None,
        "content": {
            "slug": "ghost", "title": "Ghost",
            "hasAudioVersion": True,                       # blob claims audio…
            "audioUrl": "https://media.example/deleted.m4a",  # …at a URL that no longer exists
            "audioDurationSeconds": 300,
        },
    }]
    art = (await svc._load()).articles[0]
    assert art["hasAudioVersion"] is False, "a NULL audio_url must retract the Listen affordance"
    assert "audioUrl" not in art, "the stale baked URL must not survive — it 404s"
    assert "audioDurationSeconds" not in art


@pytest.mark.asyncio
async def test_present_audio_column_still_wins_over_a_false_blob():
    """The other direction: a blob authored before narration existed must not suppress it."""
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [{                      # type: ignore[assignment]
        "slug": "voiced", "sort_order": 0,
        "audio_url": "https://media.example/voiced.m4a", "audio_duration_seconds": 210,
        "content": {"slug": "voiced", "title": "Voiced", "hasAudioVersion": False},
    }]
    art = (await svc._load()).articles[0]
    assert art["hasAudioVersion"] is True
    assert art["audioUrl"] == "https://media.example/voiced.m4a"
    assert art["audioDurationSeconds"] == 210


# ---------------------------------------------------------------------------
# `title` overlay — same argument as `slug`.
#
# `title` is HARD-REQUIRED by the iOS DTO, and `money_move_articles.title` is NOT NULL, but the
# service never fetched the column: a blob that lost its title was served as-is, iOS dropped the
# article on decode, and the reader kept seeing the stale BUNDLED copy with nothing in the logs.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_title_is_overlaid_from_the_column_when_the_blob_lost_it():
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [{                      # type: ignore[assignment]
        "id": 7, "slug": "untitled", "title": "The Real Title", "sort_order": 0,
        "content": {"slug": "untitled"},              # Studio edit dropped `title`
    }]
    art = (await svc._load()).articles[0]
    assert art["title"] == "The Real Title", "iOS requires title — serve the column, don't drop the article"


@pytest.mark.asyncio
async def test_blank_or_wrong_type_blob_title_is_also_overlaid():
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [                       # type: ignore[assignment]
        {"id": 1, "slug": "a", "title": "Alpha", "sort_order": 0,
         "content": {"slug": "a", "title": "   "}},           # whitespace-only
        {"id": 2, "slug": "b", "title": "Beta", "sort_order": 1,
         "content": {"slug": "b", "title": 42}},              # wrong type
    ]
    titles = [a["title"] for a in (await svc._load()).articles]
    assert titles == ["Alpha", "Beta"]


@pytest.mark.asyncio
async def test_authored_title_wins_over_the_column():
    """The blob is the authored artifact; the column is only the fallback."""
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [{                      # type: ignore[assignment]
        "id": 1, "slug": "a", "title": "Column Title", "sort_order": 0,
        "content": {"slug": "a", "title": "Authored Title"},
    }]
    assert (await svc._load()).articles[0]["title"] == "Authored Title"


@pytest.mark.asyncio
async def test_row_with_no_usable_title_anywhere_still_serves_the_rest():
    """Nothing to overlay: the row is logged as an iOS-will-drop case but must not take the
    catalog down with it."""
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [                       # type: ignore[assignment]
        {"id": 1, "slug": "nameless", "title": None, "sort_order": 0, "content": {"slug": "nameless"}},
        _good_row("fine", 1),
    ]
    slugs = [a.get("slug") for a in (await svc._load()).articles]
    assert "fine" in slugs, "one untitled row must not collapse the catalog"


# ---------------------------------------------------------------------------
# An EMPTY load must not latch for the full hour.
#
# Only exceptions were kept out of the cache. A successful-but-empty `_load()` — a reseed window
# where rows are briefly deleted, or every row skipped as malformed — was cached for the full
# 3600s TTL, so every client got an empty catalog (silently falling back to the BUNDLED copy,
# losing DB-only articles and their audio) for up to an hour after the table was healthy again.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_empty_load_does_not_replace_a_good_cached_catalog():
    MoneyMovesContentService._cache = None
    MoneyMovesContentService._inflight = None
    svc = MoneyMovesContentService()

    svc._fetch_rows = lambda: [_good_row("alpha", 0)]  # type: ignore[assignment]
    first = await svc.get_money_moves()
    assert len(first.articles) == 1

    # Force a refresh, then simulate the reseed window (rows momentarily gone).
    MoneyMovesContentService._cache = (0.0, first)      # expire it
    svc._fetch_rows = lambda: []                        # type: ignore[assignment]
    during_reseed = await svc.get_money_moves()

    assert len(during_reseed.articles) == 1, "must keep serving the real catalog, not an empty one"
    assert MoneyMovesContentService._cache is not None
    assert MoneyMovesContentService._cache[1].articles, "the good cache must not be overwritten"

    # And the very next request (rows back) serves fresh content — no hour-long lockout.
    svc._fetch_rows = lambda: [_good_row("alpha", 0), _good_row("beta", 1)]  # type: ignore[assignment]
    MoneyMovesContentService._cache = (0.0, during_reseed)
    recovered = await svc.get_money_moves()
    assert len(recovered.articles) == 2

    MoneyMovesContentService._cache = None


@pytest.mark.asyncio
async def test_an_empty_result_with_no_prior_cache_uses_the_short_ttl():
    """Nothing to fall back to: serve empty, but let it expire in seconds so it self-heals."""
    MoneyMovesContentService._cache = None
    MoneyMovesContentService._inflight = None
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: []                        # type: ignore[assignment]

    resp = await svc.get_money_moves()
    assert resp.articles == []
    assert MoneyMovesContentService._ttl_for(resp) == MoneyMovesContentService._EMPTY_TTL_SECONDS
    assert (MoneyMovesContentService._EMPTY_TTL_SECONDS
            < MoneyMovesContentService._TTL_SECONDS), "an empty catalog must not hold for the full hour"

    MoneyMovesContentService._cache = None


@pytest.mark.asyncio
async def test_a_real_catalog_still_gets_the_full_ttl():
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [_good_row("alpha", 0)]  # type: ignore[assignment]
    resp = await svc._load()
    assert MoneyMovesContentService._ttl_for(resp) == MoneyMovesContentService._TTL_SECONDS
