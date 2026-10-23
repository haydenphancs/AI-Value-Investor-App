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


# ── published_at: the card renders a real date, so the column has to reach the client ──
#
# Before this, both the card and the article computed their date at DECODE time as
# `now - content.publishedDaysAgo` days. That offset is a constant baked into the blob, so the
# derived date never ages — an article authored with `publishedDaysAgo: 12` reads "12 days ago"
# today and "12 days ago" in 2030. Harmless while it only ordered rows; wrong once rendered.


def _row_with_published(raw, slug: str = "enron") -> dict:
    return {
        "slug": slug,
        "sort_order": 0,
        "published_at": raw,
        "content": {"slug": slug, "title": "Enron", "publishedDaysAgo": 12},
    }


@pytest.mark.asyncio
async def test_published_at_column_is_selected():
    """A column that is never SELECTed can never be overlaid — the bug this closes was
    literally that the value existed in the table and never reached the response."""
    import inspect

    from app.services import money_moves_content_service as mod

    src = inspect.getsource(mod.MoneyMovesContentService._fetch_rows)
    assert "published_at" in src, "published_at is not in the service's .select(...)"


@pytest.mark.asyncio
async def test_published_at_is_normalized_to_whole_seconds_utc():
    """PostgREST renders timestamptz with up to SIX fractional digits and a `+00:00` offset.
    Both iOS parsers use ISO8601DateFormatter with `.withFractionalSeconds`, which accepts
    exactly THREE — so the raw column value fails to decode client-side and the article falls
    back to the drifting estimate. Emit whole seconds with a `Z`."""
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [_row_with_published("2026-06-12T14:03:21.123456+00:00")]  # type: ignore[assignment]
    out = await svc._load()
    assert out.articles[0]["publishedAt"] == "2026-06-12T14:03:21Z"


@pytest.mark.asyncio
async def test_published_at_converts_a_non_utc_offset():
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [_row_with_published("2026-06-12T08:03:21-06:00")]  # type: ignore[assignment]
    out = await svc._load()
    assert out.articles[0]["publishedAt"] == "2026-06-12T14:03:21Z"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, "", "not-a-date", 12345, "0001-01-01T00:00:00+05:00"])
async def test_missing_or_malformed_published_at_leaves_the_estimate_intact(raw):
    """ONE DIRECTION, like image_url and deliberately NOT like audio_url.

    The column is nullable and only the seeder writes it, so a Studio-inserted row — or any row
    predating the seeder — is NULL. Under the audio rule that single row would have its date
    actively stripped. Unlike a narration URL a date has no affordance to leave dangling and
    cannot 404, so stale-but-plausible beats absent: leave the client on `publishedDaysAgo`.
    """
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [_row_with_published(raw)]  # type: ignore[assignment]
    out = await svc._load()
    article = out.articles[0]
    assert "publishedAt" not in article, f"a {raw!r} column must not publish a date key"
    assert article["publishedDaysAgo"] == 12, "the fallback estimate must survive untouched"


@pytest.mark.asyncio
async def test_one_bad_published_at_does_not_cost_the_other_articles_their_dates():
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [  # type: ignore[assignment]
        _row_with_published("garbage", slug="bad"),
        _row_with_published("2026-06-12T14:03:21+00:00", slug="good"),
    ]
    out = await svc._load()
    by_slug = {a["slug"]: a for a in out.articles}
    assert len(by_slug) == 2, "a malformed timestamp dropped an article"
    assert "publishedAt" not in by_slug["bad"]
    assert by_slug["good"]["publishedAt"] == "2026-06-12T14:03:21Z"


def test_published_at_survives_the_narration_redaction():
    """`redact_money_moves` strips narration for a locked caller. If `publishedAt` ever joined
    that list, free-tier readers alone would lose every date — a difference nothing else in the
    app would surface. Mirrors test_image_keys_survive_redaction."""
    from app.services.learn_audio_gate import (
        _MONEY_MOVES_ARTICLE_AUDIO_KEYS,
        _MONEY_MOVES_BLOCK_AUDIO_KEYS,
        redact_money_moves,
    )

    assert "publishedAt" not in _MONEY_MOVES_ARTICLE_AUDIO_KEYS
    assert "publishedAt" not in _MONEY_MOVES_BLOCK_AUDIO_KEYS

    from app.schemas.money_moves import MoneyMovesResponse

    payload = MoneyMovesResponse(articles=[{
        "slug": "enron", "title": "Enron",
        "publishedAt": "2026-06-12T14:03:21Z", "publishedDaysAgo": 12,
        "audioUrl": "https://media.example/enron.m4a", "audioDurationSeconds": 305,
    }])
    out = redact_money_moves(payload, tier_required="pro")
    article = out.articles[0]
    assert article["publishedAt"] == "2026-06-12T14:03:21Z", "redaction stripped the date"
    assert article["publishedDaysAgo"] == 12
    assert "audioUrl" not in article, "the redaction under test did not actually redact"


# ═══════════════════════════════════════════════════════════════════════════════════════════
# Wiser weekly pieces (plan "Wiser weekly content engine" §1.10): the featured rule, the archive
# rule, the cache generation, `sync_with_stamp` and the pre-204 `source` fallback.
#
# The featured rule replaces swap writes: every published Deep Dive KEEPS `is_featured = true`
# and gets a more negative sort_order than the last, so the service alone decides the hero —
# the lowest sort_order among flagged rows — and unpublishing (deleting) the newest falls back to
# the previous one. iOS shows the first `isFeatured` row by ascending sortOrder, so the service
# must serve EXACTLY one, or two flagged rows would make the client and server disagree.
# ═══════════════════════════════════════════════════════════════════════════════════════════

import asyncio  # noqa: E402
import logging  # noqa: E402
import threading  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from postgrest.exceptions import APIError  # noqa: E402

from app.services import money_moves_content_service as mm_mod  # noqa: E402

_LOGGER = "app.services.money_moves_content_service"


@pytest.fixture
def fresh_cache():
    """Class-level cache state is process-wide: every test that drives `get_money_moves` starts
    and ends from a clean slate, so no test can see another's cached catalog."""
    cls = MoneyMovesContentService

    def reset():
        cls._cache = None
        cls._inflight = None
        cls._built_at = None
        cls._inflight_started_at = None
        cls._source_column_missing_logged = False

    reset()
    yield cls
    reset()


def _bundle(slug: str, order: int, featured=None, **extra) -> dict:
    row: dict = {"slug": slug, "title": slug.title(), "sort_order": order, "source": "bundle",
                 "content": {"slug": slug, "title": slug.title()}}
    if featured is not None:
        row["is_featured"] = featured
    row.update(extra)
    return row


def _weekly(slug: str, iso_week: int, published: str | None = None, featured=True,
            iso_year: int = 2026) -> dict:
    """A published Deep Dive: sort_order = -(iso_year*100 + iso_week), flag kept on."""
    return {
        "slug": slug, "title": slug.title(), "source": "weekly", "is_featured": featured,
        "sort_order": -(iso_year * 100 + iso_week),
        "published_at": published or f"2026-10-{min(28, iso_week - 20):02d}T03:00:00+00:00",
        "content": {"slug": slug, "title": slug.title(), "isFeatured": featured},
    }


async def _served(rows: list[dict]) -> list[dict]:
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: rows  # type: ignore[assignment]
    return (await svc._load()).articles


def _featured_slugs(articles: list[dict]) -> list[str]:
    return [a["slug"] for a in articles if a.get("isFeatured") is True]


def _ios_hero(articles: list[dict]):
    """MoneyMovesContentStore.swift: sort by `sortOrder ?? .max`, first `isFeatured == true`."""
    for a in _ios_order(articles):
        if a.get("isFeatured") is True:
            return a["slug"]
    return None


# ── featured rule ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_lowest_sort_order_wins_among_flagged_weekly_pieces():
    arts = await _served([
        _weekly("ai-chips-2026-10-09", 41),
        _weekly("power-for-ai-2026-10-23", 43),
        _weekly("data-centres-2026-10-16", 42),
        _bundle("how-amazon-built-its-moat", 0, featured=True),
    ])
    assert _featured_slugs(arts) == ["power-for-ai-2026-10-23"], "exactly ONE hero, the newest"
    assert _ios_hero(arts) == "power-for-ai-2026-10-23", "the client must pick the same hero"
    assert all(isinstance(a["isFeatured"], bool) for a in arts), "every article carries the flag"


@pytest.mark.asyncio
async def test_the_bundle_hero_loses_to_any_weekly_deep_dive():
    arts = await _served([
        _bundle("how-amazon-built-its-moat", 0, featured=True),
        _bundle("costcos-membership-magic", 1, featured=False),
        _weekly("ai-chips-2026-10-23", 43),
    ])
    assert _featured_slugs(arts) == ["ai-chips-2026-10-23"]
    amazon = next(a for a in arts if a["slug"] == "how-amazon-built-its-moat")
    assert amazon["isFeatured"] is False, "the losing bundle hero must be served un-featured"


@pytest.mark.asyncio
async def test_unpublishing_the_newest_falls_back_to_the_previous_then_the_bundle_hero():
    """Unpublish = DELETE the row. No swap write: the next-lowest flagged row simply wins."""
    newest, previous = _weekly("b-2026-10-23", 43), _weekly("a-2026-10-16", 42)
    hero = _bundle("how-amazon-built-its-moat", 0, featured=True)
    assert _featured_slugs(await _served([newest, previous, hero])) == ["b-2026-10-23"]
    assert _featured_slugs(await _served([previous, hero])) == ["a-2026-10-16"]
    assert _featured_slugs(await _served([hero])) == ["how-amazon-built-its-moat"]


@pytest.mark.asyncio
async def test_a_never_feature_slug_never_wins_even_with_the_lowest_sort_order(caplog):
    rows = [
        _bundle("the-future-of-digital-finance", -999_999, featured=True),
        _bundle("how-amazon-built-its-moat", 0, featured=True),
    ]
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        arts = await _served(rows)
    assert _featured_slugs(arts) == ["how-amazon-built-its-moat"]
    assert any("NEVER_FEATURE" in r.getMessage() and "the-future-of-digital-finance" in r.getMessage()
               for r in caplog.records), "a suppressed flag must be named in the logs"


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", sorted(mm_mod.NEVER_FEATURE))
async def test_no_never_feature_slug_is_ever_served_as_the_hero(slug, caplog):
    """When the only flagged row is excluded there is NO hero (iOS falls back to its bundled
    one) — never a re-featured excluded article."""
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        arts = await _served([_bundle(slug, 0, featured=True), _bundle("plain", 1, featured=False)])
    assert _featured_slugs(arts) == []
    assert any("no served article is featured" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_never_feature_is_matched_on_the_row_column_slug_too():
    """A Studio edit that renamed the blob's slug must not smuggle an excluded row back."""
    row = _bundle("the-fall-of-enron", -5, featured=True)
    row["content"]["slug"] = "a-renamed-story"
    arts = await _served([row, _bundle("how-amazon-built-its-moat", 0, featured=True)])
    assert _featured_slugs(arts) == ["how-amazon-built-its-moat"]


@pytest.mark.asyncio
async def test_the_column_wins_over_a_disagreeing_blob_in_both_directions(caplog):
    blob_only = _bundle("blob-says-yes", -2, featured=False)
    blob_only["content"]["isFeatured"] = True               # stale blob flag, column cleared
    column_only = _bundle("column-says-yes", -1, featured=True)
    column_only["content"]["isFeatured"] = False            # blob authored before the flip
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        arts = await _served([blob_only, column_only])
    by_slug = {a["slug"]: a for a in arts}
    assert by_slug["blob-says-yes"]["isFeatured"] is False, "a cleared column must un-feature"
    assert by_slug["column-says-yes"]["isFeatured"] is True, "the served blob must say true"
    assert sum("disagrees with column is_featured" in r.getMessage() for r in caplog.records) == 2


@pytest.mark.asyncio
async def test_the_blob_flag_is_the_fallback_only_when_the_column_is_absent_or_null():
    absent = {"slug": "absent", "title": "A", "sort_order": 1,
              "content": {"slug": "absent", "title": "A", "isFeatured": True}}
    null = _bundle("null-col", 0, featured=None)
    null["is_featured"] = None
    null["content"]["isFeatured"] = True
    arts = await _served([absent, null])
    assert _featured_slugs(arts) == ["null-col"], "NULL column -> blob decides; lowest sort wins"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [1, 0, "true", "yes", [True], {"v": True}])
async def test_a_non_boolean_featured_flag_never_features(bad, caplog):
    """bool-as-int and string flags from an out-of-band edit are malformed, never a hero."""
    col = _bundle("bad-column", -1)
    col["is_featured"] = bad
    blob = {"slug": "bad-blob", "title": "B", "sort_order": -2,
            "content": {"slug": "bad-blob", "title": "B", "isFeatured": bad}}
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        arts = await _served([col, blob, _bundle("real-hero", 0, featured=True)])
    assert _featured_slugs(arts) == ["real-hero"]
    assert any("non-boolean is_featured" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_more_than_one_flagged_bundle_row_warns(caplog):
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        arts = await _served([
            _bundle("how-amazon-built-its-moat", 0, featured=True),
            _bundle("costcos-membership-magic", 1, featured=True),
        ])
    assert _featured_slugs(arts) == ["how-amazon-built-its-moat"]
    assert any("2 bundle rows are flagged" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_rows_with_no_source_count_as_bundle_rows_for_the_warning(caplog):
    """Pre-204 rows have no `source` at all: they are bundle rows."""
    a = _bundle("a", 0, featured=True)
    b = _bundle("b", 1, featured=True)
    del a["source"], b["source"]
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _served([a, b])
    assert any("2 bundle rows are flagged" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_many_flagged_weekly_rows_are_normal_and_do_not_warn(caplog):
    rows = [_weekly(f"w{i}-2026-10-{10 + i:02d}", 30 + i) for i in range(6)]
    rows.append(_bundle("how-amazon-built-its-moat", 0, featured=True))
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        arts = await _served(rows)
    assert len(_featured_slugs(arts)) == 1
    assert not any("bundle rows are flagged" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_equal_sort_orders_break_the_featured_tie_by_slug():
    arts = await _served([_bundle("zulu", 0, featured=True), _bundle("alpha", 0, featured=True)])
    assert _featured_slugs(arts) == ["alpha"]
    assert _ios_hero(arts) == "alpha"


@pytest.mark.asyncio
async def test_a_flagged_row_ios_would_drop_cannot_be_the_hero():
    """No usable title (or slug) = dropped on decode: featuring it would leave NO hero."""
    nameless = {"id": 9, "slug": "nameless", "title": None, "sort_order": -10, "is_featured": True,
                "content": {"slug": "nameless"}}
    arts = await _served([nameless, _bundle("how-amazon-built-its-moat", 0, featured=True)])
    assert _featured_slugs(arts) == ["how-amazon-built-its-moat"]


@pytest.mark.asyncio
async def test_a_malformed_unhashable_blob_slug_costs_one_row_not_the_catalog():
    weird = _bundle("weird", -1, featured=True)
    weird["slug"] = None                           # no column slug to overlay…
    weird["content"]["slug"] = {"oops": 1}         # …and an unhashable blob slug
    arts = await _served([weird, _bundle("fine", 0, featured=True)])
    assert "fine" in [a.get("slug") for a in arts if isinstance(a.get("slug"), str)]
    assert _featured_slugs(arts) == ["fine"]


@pytest.mark.asyncio
async def test_no_flagged_row_at_all_serves_every_article_unfeatured():
    arts = await _served([_good_row("alpha", 0), _good_row("beta", 1)])
    assert [a["isFeatured"] for a in arts] == [False, False]


@pytest.mark.asyncio
async def test_week_53_to_week_1_stays_monotonic_for_the_featured_rule():
    """2026-W53 (Dec 31) then 2027-W01: the new year's piece must be the lower sort_order."""
    w53 = _weekly("a-2026-12-31", 53, published="2027-01-01T04:00:00+00:00")
    w01 = _weekly("b-2027-01-08", 1, published="2027-01-09T04:00:00+00:00", iso_year=2027)
    assert w01["sort_order"] < w53["sort_order"]
    assert _featured_slugs(await _served([w53, w01])) == ["b-2027-01-08"]


# ── archive rule ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_the_newest_weekly_pieces_are_served_and_bundle_rows_always(monkeypatch):
    monkeypatch.setattr(mm_mod, "settings", SimpleNamespace(LEARN_SERVED_WEEKLY_MAX=3))
    weekly = [_weekly(f"w{w}-2026", w, published=f"2026-{w // 4 + 1:02d}-0{w % 4 + 1}T03:00:00+00:00")
              for w in range(10, 16)]
    bundle = [_bundle(f"bundle-{i}", i) for i in range(30)]
    arts = await _served(weekly + bundle)
    served = [a["slug"] for a in arts]
    assert [s for s in served if s.startswith("w")] == ["w15-2026", "w14-2026", "w13-2026"]
    assert sum(s.startswith("bundle-") for s in served) == 30, "bundle rows are never capped"
    assert _featured_slugs(arts) == ["w15-2026"]


@pytest.mark.asyncio
async def test_archive_rule_ranks_by_published_at_then_sort_order(monkeypatch):
    monkeypatch.setattr(mm_mod, "settings", SimpleNamespace(LEARN_SERVED_WEEKLY_MAX=2))
    same = "2026-10-20T03:00:00+00:00"
    rows = [
        _weekly("older", 40, published="2026-10-01T03:00:00+00:00"),
        _weekly("tie-high-sort", 41, published=same),   # -202641
        _weekly("tie-low-sort", 42, published=same),    # -202642 → wins the tie
        _weekly("newest", 43, published="2026-10-21T03:00:00Z"),
    ]
    served = {a["slug"] for a in await _served(rows)}
    assert served == {"newest", "tie-low-sort"}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_date", [None, "", "not-a-date", 12345, "0001-01-01T00:00:00+05:00"])
async def test_an_undated_weekly_piece_is_the_first_one_dropped(bad_date, monkeypatch):
    monkeypatch.setattr(mm_mod, "settings", SimpleNamespace(LEARN_SERVED_WEEKLY_MAX=2))
    undated = _weekly("undated", 50)          # the LOWEST sort_order of the three…
    undated["published_at"] = bad_date        # …but no usable date → ranks oldest
    rows = [undated, _weekly("a", 40, published="2026-10-01T03:00:00Z"),
            _weekly("b", 41, published="2026-10-08T03:00:00Z")]
    served = {a["slug"] for a in await _served(rows)}
    assert served == {"a", "b"}


@pytest.mark.asyncio
async def test_a_capped_out_flagged_piece_is_never_the_hero(monkeypatch):
    """The hero must be an article the response actually contains."""
    monkeypatch.setattr(mm_mod, "settings", SimpleNamespace(LEARN_SERVED_WEEKLY_MAX=1))
    # A rebuilt OLD period published late can carry the lower sort_order but the older date.
    early = _weekly("low-sort-old-date", 50, published="2026-09-01T03:00:00Z")
    late = _weekly("high-sort-new-date", 30, published="2026-10-20T03:00:00Z")
    arts = await _served([early, late])
    assert [a["slug"] for a in arts] == ["high-sort-new-date"]
    assert _featured_slugs(arts) == ["high-sort-new-date"]


@pytest.mark.asyncio
@pytest.mark.parametrize("junk", [0, -3, True, False, "40", None, 2.5])
async def test_a_junk_cap_setting_falls_back_to_the_default(junk, monkeypatch, caplog):
    monkeypatch.setattr(mm_mod, "settings", SimpleNamespace(LEARN_SERVED_WEEKLY_MAX=junk))
    rows = [_weekly(f"w{i}", 1 + i, published=f"2026-01-{1 + i:02d}T03:00:00Z") for i in range(25)]
    with caplog.at_level(logging.ERROR, logger=_LOGGER):
        arts = await _served(rows)
    assert len(arts) == 25, "25 < the default of 40: nothing is capped"
    assert any("LEARN_SERVED_WEEKLY_MAX" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_the_cap_reads_the_real_settings_default():
    from app.config import settings

    assert mm_mod._served_weekly_max() == settings.LEARN_SERVED_WEEKLY_MAX


@pytest.mark.asyncio
async def test_rows_without_a_source_column_are_never_capped(monkeypatch):
    """Before migration 204 no row has `source`: every row is a bundle row and is served."""
    monkeypatch.setattr(mm_mod, "settings", SimpleNamespace(LEARN_SERVED_WEEKLY_MAX=4))
    rows = [_good_row(f"r{i}", i) for i in range(12)]
    assert len(await _served(rows)) == 12


# ── cache generation ─────────────────────────────────────────────────────────────────────────


class _GatedFetch:
    """`_fetch_rows` stand-in: each call returns the next scripted answer, and a call scripted
    with a gate BLOCKS (in asyncio.to_thread's worker thread) until the test releases it."""

    def __init__(self, script: list[tuple[list[dict], threading.Event | None]]):
        self._script = list(script)
        self.calls = 0
        self.started: list[threading.Event] = [threading.Event() for _ in script]

    def __call__(self) -> list[dict]:
        idx = self.calls
        self.calls += 1
        rows, gate = self._script[idx]
        self.started[idx].set()
        if gate is not None:
            assert gate.wait(timeout=5), "test gate never released"
        return [dict(r, content=dict(r["content"])) for r in rows]


async def _wait_started(fetch: _GatedFetch, idx: int) -> None:
    for _ in range(500):
        if fetch.started[idx].is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"fetch #{idx} never started")


@pytest.mark.asyncio
async def test_a_load_invalidated_mid_flight_is_not_cached_and_the_next_call_reloads(fresh_cache):
    old_rows = [_good_row("old", 0)]
    new_rows = [_good_row("old", 0), _good_row("brand-new", 1)]
    gate = threading.Event()
    fetch = _GatedFetch([(old_rows, gate), (new_rows, None)])
    svc = MoneyMovesContentService()
    svc._fetch_rows = fetch  # type: ignore[assignment]

    leader = asyncio.create_task(svc.get_money_moves())
    await _wait_started(fetch, 0)
    joiner = asyncio.create_task(svc.get_money_moves())   # joins the doomed load
    await asyncio.sleep(0.01)

    MoneyMovesContentService.invalidate("test: publish")
    assert MoneyMovesContentService._inflight is None, "invalidate must detach the in-flight load"
    gate.set()

    stale, joined = await leader, await joiner
    assert [a["slug"] for a in stale.articles] == ["old"], "its own caller still gets an answer"
    assert [a["slug"] for a in joined.articles] == ["old"], "so does a joiner from before"
    assert MoneyMovesContentService._cache is None, "the pre-change rows must NOT be cached"

    fresh = await svc.get_money_moves()
    assert [a["slug"] for a in fresh.articles] == ["old", "brand-new"]
    assert fetch.calls == 2, "the next call reloads"
    assert MoneyMovesContentService._cache is not None
    assert [a["slug"] for a in MoneyMovesContentService._cache[1].articles] == ["old", "brand-new"]


@pytest.mark.asyncio
async def test_a_caller_after_invalidate_starts_a_fresh_load_and_the_old_one_cannot_clobber_it(
        fresh_cache):
    """The detached load finishes AFTER the fresh one started: its `finally` must not clear the
    fresh load's in-flight slot, and its stale rows must not overwrite the fresh cache."""
    gate_old, gate_new = threading.Event(), threading.Event()
    fetch = _GatedFetch([([_good_row("old", 0)], gate_old), ([_good_row("new", 0)], gate_new)])
    svc = MoneyMovesContentService()
    svc._fetch_rows = fetch  # type: ignore[assignment]

    first = asyncio.create_task(svc.get_money_moves())
    await _wait_started(fetch, 0)
    MoneyMovesContentService.invalidate("test: unpublish")
    second = asyncio.create_task(svc.get_money_moves())
    await _wait_started(fetch, 1)                         # a SECOND fetch: it did not join
    fresh_slot = MoneyMovesContentService._inflight
    assert fresh_slot is not None

    gate_old.set()
    assert [a["slug"] for a in (await first).articles] == ["old"]
    assert MoneyMovesContentService._inflight is fresh_slot, \
        "the detached load's finally cleared the fresh load's in-flight slot"
    assert MoneyMovesContentService._cache is None

    gate_new.set()
    assert [a["slug"] for a in (await second).articles] == ["new"]
    assert [a["slug"] for a in MoneyMovesContentService._cache[1].articles] == ["new"]
    assert MoneyMovesContentService._inflight is None


@pytest.mark.asyncio
async def test_invalidate_clears_a_warm_cache_and_bumps_the_generation(fresh_cache, caplog):
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [_good_row("alpha", 0)]  # type: ignore[assignment]
    await svc.get_money_moves()
    assert MoneyMovesContentService._cache is not None
    before = MoneyMovesContentService._generation
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        MoneyMovesContentService.invalidate("publish of power-for-ai-2026-10-23")
    assert MoneyMovesContentService._generation == before + 1
    assert MoneyMovesContentService._cache is None and MoneyMovesContentService._built_at is None
    assert any("publish of power-for-ai-2026-10-23" in r.getMessage() for r in caplog.records), \
        "the reason must be in the log line"


@pytest.mark.asyncio
async def test_a_cancelled_leader_after_invalidate_still_releases_its_joiners(fresh_cache):
    """The BaseException arm must still settle the DETACHED future, or its joiners hang."""
    gate = threading.Event()
    fetch = _GatedFetch([([_good_row("old", 0)], gate)])
    svc = MoneyMovesContentService()
    svc._fetch_rows = fetch  # type: ignore[assignment]
    leader = asyncio.create_task(svc.get_money_moves())
    await _wait_started(fetch, 0)
    joiner = asyncio.create_task(svc.get_money_moves())
    await asyncio.sleep(0.01)
    MoneyMovesContentService.invalidate("test")
    leader.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await leader
        # Any terminal state is fine (the leader hands joiners an error); waiting forever is the
        # defect. TimeoutError first: it is an Exception too, and must not pass as "terminal".
        try:
            await asyncio.wait_for(joiner, timeout=2.0)
        except asyncio.TimeoutError:
            pytest.fail("the joiner of a detached, cancelled load was left waiting forever")
        except (asyncio.CancelledError, Exception):
            pass
    finally:
        gate.set()
        await asyncio.sleep(0.05)


# ── sync_with_stamp ──────────────────────────────────────────────────────────────────────────


def _warm(built_at: datetime) -> None:
    """A cache built by a load that STARTED at `built_at`."""
    MoneyMovesContentService._cache = (0.0, mm_mod.MoneyMovesResponse(articles=[{"slug": "x"}]))
    MoneyMovesContentService._built_at = built_at


def test_sync_with_stamp_invalidates_when_the_ledger_changed_after_the_cache(fresh_cache):
    built = datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc)
    _warm(built)
    gen = MoneyMovesContentService._generation
    assert MoneyMovesContentService.sync_with_stamp("2026-10-24T03:30:00.123456+00:00") is True
    assert MoneyMovesContentService._cache is None
    assert MoneyMovesContentService._generation == gen + 1


@pytest.mark.parametrize("stamp", ["2026-10-24T02:00:00Z", "2026-10-23T21:00:00-05:00"])
def test_sync_with_stamp_keeps_a_cache_built_after_the_change(fresh_cache, stamp):
    _warm(datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc))
    assert MoneyMovesContentService.sync_with_stamp(stamp) is False
    assert MoneyMovesContentService._cache is not None


def test_sync_with_stamp_covers_clock_skew_just_before_the_build(fresh_cache):
    """The stamp comes from another clock: a change a few seconds BEFORE our build may still be
    one our load missed, so anything inside the skew margin invalidates."""
    built = datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc)
    _warm(built)
    inside = built - timedelta(seconds=mm_mod._STAMP_SKEW_SECONDS - 5)
    assert MoneyMovesContentService.sync_with_stamp(inside.isoformat()) is True
    _warm(built)
    outside = built - timedelta(seconds=mm_mod._STAMP_SKEW_SECONDS + 5)
    assert MoneyMovesContentService.sync_with_stamp(outside.isoformat()) is False


def test_sync_with_stamp_accepts_a_datetime_and_a_naive_stamp_as_utc(fresh_cache):
    built = datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc)
    _warm(built)
    assert MoneyMovesContentService.sync_with_stamp(built + timedelta(hours=1)) is True
    _warm(built)
    assert MoneyMovesContentService.sync_with_stamp("2026-10-24T05:00:00") is True


@pytest.mark.parametrize("stamp", [None, "", "garbage", 12345, ["2026-10-24"],
                                   "0001-01-01T00:00:00+05:00"])
def test_sync_with_stamp_ignores_a_missing_or_unusable_stamp(fresh_cache, stamp, caplog):
    _warm(datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc))
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert MoneyMovesContentService.sync_with_stamp(stamp) is False  # type: ignore[arg-type]
    assert MoneyMovesContentService._cache is not None
    if stamp is not None:
        assert any("unparseable ledger stamp" in r.getMessage() for r in caplog.records)


def test_sync_with_stamp_with_no_cache_and_no_load_is_a_no_op(fresh_cache):
    gen = MoneyMovesContentService._generation
    assert MoneyMovesContentService.sync_with_stamp("2026-10-24T03:30:00Z") is False
    assert MoneyMovesContentService._generation == gen


def test_a_future_stamp_still_invalidates_but_warns(fresh_cache, caplog):
    _warm(datetime.now(timezone.utc))
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert MoneyMovesContentService.sync_with_stamp("2099-01-01T00:00:00Z") is True
    assert any("in the future" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_sync_with_stamp_detaches_an_in_flight_load_that_started_before_the_change(
        fresh_cache):
    gate = threading.Event()
    fetch = _GatedFetch([([_good_row("old", 0)], gate), ([_good_row("new", 0)], None)])
    svc = MoneyMovesContentService()
    svc._fetch_rows = fetch  # type: ignore[assignment]
    leader = asyncio.create_task(svc.get_money_moves())
    await _wait_started(fetch, 0)
    later = datetime.now(timezone.utc) + timedelta(seconds=1)
    assert MoneyMovesContentService.sync_with_stamp(later.isoformat()) is True
    gate.set()
    await leader
    assert MoneyMovesContentService._cache is None, "the load that read pre-change rows stored"
    assert [a["slug"] for a in (await svc.get_money_moves()).articles] == ["new"]


@pytest.mark.asyncio
async def test_the_cache_records_when_its_load_started(fresh_cache):
    svc = MoneyMovesContentService()
    svc._fetch_rows = lambda: [_good_row("alpha", 0)]  # type: ignore[assignment]
    before = datetime.now(timezone.utc)
    await svc.get_money_moves()
    built = MoneyMovesContentService._built_at
    assert built is not None and before <= built <= datetime.now(timezone.utc)


# ── `source` column missing (migration 204 not applied yet) ──────────────────────────────────


class _FakeQuery:
    def __init__(self, client: "_FakeSupabase", columns: str):
        self._client, self._columns = client, columns

    def execute(self):
        self._client.selects.append(self._columns)
        if "source" in self._columns and self._client.error is not None:
            raise self._client.error
        return SimpleNamespace(data=[dict(r) for r in self._client.rows])


class _FakeTable:
    def __init__(self, client: "_FakeSupabase"):
        self._client = client

    def select(self, columns: str) -> _FakeQuery:
        return _FakeQuery(self._client, columns)


class _FakeSupabase:
    def __init__(self, rows, error=None):
        self.rows, self.error, self.selects = rows, error, []

    def table(self, name: str) -> _FakeTable:
        assert name == "money_move_articles"
        return _FakeTable(self)


_MISSING_SOURCE = APIError({"code": "42703", "message": "column money_move_articles.source does not exist",
                            "hint": None, "details": None})


def test_fetch_rows_selects_is_featured_and_source(monkeypatch):
    fake = _FakeSupabase([_bundle("a", 0, featured=True)])
    monkeypatch.setattr(mm_mod, "get_supabase", lambda: fake)
    MoneyMovesContentService()._fetch_rows()
    assert len(fake.selects) == 1
    cols = {c.strip() for c in fake.selects[0].split(",")}
    assert {"is_featured", "source", "published_at", "slug", "title", "content"} <= cols


def test_a_missing_source_column_falls_back_and_logs_error_once(fresh_cache, monkeypatch, caplog):
    fake = _FakeSupabase([_bundle("a", 0, featured=True)], error=_MISSING_SOURCE)
    monkeypatch.setattr(mm_mod, "get_supabase", lambda: fake)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        rows1 = MoneyMovesContentService()._fetch_rows()
        rows2 = MoneyMovesContentService()._fetch_rows()
    assert [r["slug"] for r in rows1] == ["a"] and [r["slug"] for r in rows2] == ["a"]
    assert len(fake.selects) == 4 and "source" not in fake.selects[1]
    first = {c.strip() for c in fake.selects[0].split(",")}
    fallback = {c.strip() for c in fake.selects[1].split(",")}
    assert fallback == first - {"source"}, "the fallback must read every other column"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "migration 204" in errors[0].getMessage(), "ERROR exactly once"
    assert any(r.levelno == logging.WARNING and "migration 204" in r.getMessage()
               for r in caplog.records), "later fallbacks still leave a (quieter) trace"


@pytest.mark.asyncio
async def test_the_catalog_serves_through_the_missing_source_fallback(fresh_cache, monkeypatch):
    fake = _FakeSupabase([_bundle("how-amazon-built-its-moat", 0, featured=True),
                          _bundle("b", 1)], error=_MISSING_SOURCE)
    monkeypatch.setattr(mm_mod, "get_supabase", lambda: fake)
    resp = await MoneyMovesContentService().get_money_moves()
    assert [a["slug"] for a in resp.articles] == ["how-amazon-built-its-moat", "b"]
    assert _featured_slugs(resp.articles) == ["how-amazon-built-its-moat"]


@pytest.mark.parametrize("error", [
    APIError({"code": "42501", "message": "permission denied for table money_move_articles"}),
    APIError({"code": "42703", "message": "column money_move_articles.is_featured does not exist"}),
    APIError({"code": "42P01", "message": 'relation "money_move_articles" does not exist'}),
    ConnectionError("network down"),
    APIError({"code": "XX000", "message": "internal error near source"}),
])
def test_any_other_select_failure_still_raises(error, monkeypatch, fresh_cache):
    """Only the `source`-does-not-exist gap is papered over: everything else must reach the
    stale-cache fallback and its logger.exception, not be retried into a half-read."""
    fake = _FakeSupabase([_bundle("a", 0)], error=error)
    monkeypatch.setattr(mm_mod, "get_supabase", lambda: fake)
    with pytest.raises(type(error)):
        MoneyMovesContentService()._fetch_rows()
    assert len(fake.selects) == 1, "no retry for a failure that is not the missing column"


def test_the_pgrst204_schema_cache_variant_also_falls_back(monkeypatch, fresh_cache):
    err = APIError({"code": "PGRST204",
                    "message": "Could not find the 'source' column of 'money_move_articles' in the schema cache"})
    fake = _FakeSupabase([_bundle("a", 0)], error=err)
    monkeypatch.setattr(mm_mod, "get_supabase", lambda: fake)
    assert [r["slug"] for r in MoneyMovesContentService()._fetch_rows()] == ["a"]
