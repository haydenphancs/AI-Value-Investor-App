"""
Emerging Frontiers (server-driven "Trending Themes") — schema parity + transforms.

Guard rails:

1. Schema parity (backend ↔ iOS): the snake_case keys the iOS
   `TrendingThemeDTO.CodingKeys` decodes must stay pinned to the Pydantic
   `TrendingThemeResponse` shape. A drift = a decode crash in the app.

2. The pure `_theme_change` average-change transform must behave correctly on the
   messy / outlier inputs the FMP quote fan-out actually produces (missing quotes,
   single ticker, negatives, signed -0.0, non-finite), and `_build_themes` must
   degrade honestly (empty on no rows, count = configured tickers, None change
   when nothing resolves) — never a wrong number, never a NaN token on the wire.

No network, no Supabase — rows are injected inline and the FMP client is a stub.
"""

import math

import pytest

from app.schemas.home_dashboard import (
    ThemesGroupResponse,
    TrendingThemeResponse,
)
from app.schemas.themes_detail import (
    ThemeConstituentResponse,
    ThemeDetailResponse,
)
from app.services.home_dashboard_service import (
    HomeDashboardService,
    _canonical_symbol,
    _theme_change,
)


# ── 1. Schema parity ──────────────────────────────────────────────────

# The exact snake_case keys the iOS `TrendingThemeDTO.CodingKeys` expects.
_THEME_KEYS = {"slug", "title", "image_url", "accent_hex", "ticker_count", "change_percent"}


def test_trending_theme_item_keys_match_ios_dto():
    t = TrendingThemeResponse(
        slug="silicon-rush",
        title="The Silicon Rush",
        image_url=None,
        accent_hex="22D3EE",
        ticker_count=8,
        change_percent=3.4,
    )
    assert set(t.model_dump().keys()) == _THEME_KEYS


def test_themes_group_default_is_empty():
    # An empty list is valid and → iOS hides the whole Emerging Frontiers section.
    assert ThemesGroupResponse().model_dump() == {"themes": []}


# ── 2. Pure average-change transform (`_theme_change`) ────────────────


def test_theme_change_empty_tickers_is_none():
    assert _theme_change([], {"NVDA": 2.0}) is None


def test_theme_change_all_unresolvable_is_none():
    # Card still shows "N stocks" (count elsewhere); the % badge hides.
    assert _theme_change(["NVDA", "AMD"], {}) is None


def test_theme_change_single_ticker_is_exact():
    assert _theme_change(["NVDA"], {"NVDA": 3.14}) == 3.14


def test_theme_change_averages_only_resolvable():
    # AMD has no quote → skipped from the average (but not from the count).
    result = _theme_change(["NVDA", "AMD", "TSM"], {"NVDA": 2.0, "TSM": 4.0})
    assert result == 3.0


def test_theme_change_negative_only_is_negative():
    result = _theme_change(["A", "B"], {"A": -1.0, "B": -3.0})
    assert result == -2.0
    assert result < 0  # → iOS colours the badge red (.bearish)


def test_theme_change_rounds_to_two_dp():
    result = _theme_change(["A", "B", "C"], {"A": 1.111, "B": 2.222, "C": 3.333})
    assert result == 2.22


def test_theme_change_never_returns_signed_negative_zero():
    # A tiny negative mean rounds to -0.0, which decodes as positive/green on iOS
    # unless collapsed. The helper must return a plain +0.0.
    result = _theme_change(["A"], {"A": -0.001})
    assert result == 0.0
    assert math.copysign(1.0, result) == 1.0  # positive zero, not -0.0


def test_theme_change_skips_none_values_in_map():
    # A None slipped into the map (defensive) is skipped, not treated as 0.
    result = _theme_change(["A", "B"], {"A": None, "B": 2.0})  # type: ignore[dict-item]
    assert result == 2.0


def test_theme_change_non_finite_poisons_to_none_not_nan():
    # A NaN that bypassed the map-build filter must degrade the theme to None,
    # never serialize as a JSON `NaN` token (which crashes the iOS decode).
    result = _theme_change(["A", "B"], {"A": float("nan"), "B": 2.0})  # type: ignore[dict-item]
    assert result is None


def test_theme_change_class_share_join_resolves():
    # Row stores "BRK.B"; FMP echoes/keys "BRK-B". _canonical_symbol folds both.
    change_map = {_canonical_symbol("BRK-B"): 1.5}
    assert _theme_change(["BRK.B"], change_map) == 1.5


# ── 3. `_build_themes` end-to-end (stub FMP + injected rows) ───────────


class _FakeThemesFMP:
    """Stub FMP: canned per-symbol quotes; records the batch-quote call args."""

    def __init__(self, quotes_by_symbol):
        self.quotes_by_symbol = quotes_by_symbol
        self.batch_calls = []

    async def get_batch_quotes(self, symbols):
        self.batch_calls.append(list(symbols))
        return [self.quotes_by_symbol[s] for s in symbols if s in self.quotes_by_symbol]


def _service_with(rows, quotes_by_symbol):
    """A fresh service whose Supabase read + FMP client are stubbed (no network)."""
    HomeDashboardService._themes_cache.clear()
    HomeDashboardService._themes_inflight.clear()
    svc = HomeDashboardService()
    svc.fmp = _FakeThemesFMP(quotes_by_symbol)  # type: ignore[assignment]
    svc._read_theme_rows = lambda: rows  # type: ignore[assignment]
    return svc


@pytest.mark.asyncio
async def test_build_themes_empty_rows_returns_empty_group():
    svc = _service_with(rows=[], quotes_by_symbol={})
    result = await svc._build_themes()
    assert result == ThemesGroupResponse()
    assert svc.fmp.batch_calls == []  # no rows → no FMP fetch


@pytest.mark.asyncio
async def test_build_themes_maps_count_change_image_accent():
    rows = [
        {
            "slug": "silicon-rush",
            "title": "The Silicon Rush",
            "image_url": "https://x/silicon.jpg",
            "accent_hex": "22D3EE",
            "tickers": ["NVDA", "AMD"],
            "sort_order": 10,
        }
    ]
    quotes = {
        "NVDA": {"symbol": "NVDA", "changesPercentage": 2.0},
        "AMD": {"symbol": "AMD", "changesPercentage": 4.0},
    }
    svc = _service_with(rows, quotes)
    result = await svc._build_themes()

    assert len(result.themes) == 1
    t = result.themes[0]
    assert t.slug == "silicon-rush"
    assert t.title == "The Silicon Rush"
    assert t.image_url == "https://x/silicon.jpg"
    assert t.accent_hex == "22D3EE"
    assert t.ticker_count == 2                 # len(configured tickers)
    assert t.change_percent == 3.0             # avg(2.0, 4.0)


@pytest.mark.asyncio
async def test_build_themes_count_is_configured_not_resolvable():
    # Two tickers configured, only one quotes → count stays 2, avg over the one.
    rows = [{"slug": "s", "title": "T", "tickers": ["NVDA", "AMD"], "sort_order": 0}]
    quotes = {"NVDA": {"symbol": "NVDA", "changesPercentage": 5.0}}
    svc = _service_with(rows, quotes)
    t = (await svc._build_themes()).themes[0]
    assert t.ticker_count == 2
    assert t.change_percent == 5.0


@pytest.mark.asyncio
async def test_build_themes_no_resolvable_tickers_change_is_none():
    rows = [{"slug": "s", "title": "T", "tickers": ["AAA", "BBB"], "sort_order": 0}]
    svc = _service_with(rows, quotes_by_symbol={})  # nothing resolves
    t = (await svc._build_themes()).themes[0]
    assert t.ticker_count == 2
    assert t.change_percent is None             # → iOS hides the badge


@pytest.mark.asyncio
async def test_build_themes_missing_image_and_accent_default():
    rows = [{"slug": "s", "title": "T", "tickers": ["NVDA"], "sort_order": 0}]
    quotes = {"NVDA": {"symbol": "NVDA", "changesPercentage": 1.0}}
    svc = _service_with(rows, quotes)
    t = (await svc._build_themes()).themes[0]
    assert t.image_url is None
    assert t.accent_hex == "22D3EE"             # service default when the row omits it


@pytest.mark.asyncio
async def test_build_themes_skips_row_missing_slug_or_title():
    rows = [
        {"slug": "", "title": "No Slug", "tickers": ["NVDA"], "sort_order": 0},
        {"slug": "ok", "title": "Fine", "tickers": ["NVDA"], "sort_order": 1},
        {"slug": "no-title", "title": "  ", "tickers": ["NVDA"], "sort_order": 2},
    ]
    quotes = {"NVDA": {"symbol": "NVDA", "changesPercentage": 1.0}}
    svc = _service_with(rows, quotes)
    result = await svc._build_themes()
    assert [t.slug for t in result.themes] == ["ok"]   # the two invalid rows dropped


@pytest.mark.asyncio
async def test_build_themes_dedupes_ticker_union_to_single_fetch():
    # Two themes share NVDA → the batch quote must request NVDA only once.
    rows = [
        {"slug": "a", "title": "A", "tickers": ["NVDA", "AMD"], "sort_order": 0},
        {"slug": "b", "title": "B", "tickers": ["NVDA", "TSM"], "sort_order": 1},
    ]
    quotes = {
        "NVDA": {"symbol": "NVDA", "changesPercentage": 2.0},
        "AMD": {"symbol": "AMD", "changesPercentage": 4.0},
        "TSM": {"symbol": "TSM", "changesPercentage": 6.0},
    }
    svc = _service_with(rows, quotes)
    result = await svc._build_themes()

    assert len(svc.fmp.batch_calls) == 1                 # one fan-out
    fetched = svc.fmp.batch_calls[0]
    assert sorted(fetched) == ["AMD", "NVDA", "TSM"]     # deduped union, sorted
    assert fetched.count("NVDA") == 1                    # not fetched twice
    by_slug = {t.slug: t for t in result.themes}
    assert by_slug["a"].change_percent == 3.0            # avg(NVDA 2, AMD 4)
    assert by_slug["b"].change_percent == 4.0            # avg(NVDA 2, TSM 6)


@pytest.mark.asyncio
async def test_build_themes_non_finite_quote_is_dropped():
    # A NaN change from FMP must be filtered at map-build → treated as unresolvable.
    rows = [{"slug": "s", "title": "T", "tickers": ["NVDA", "AMD"], "sort_order": 0}]
    quotes = {
        "NVDA": {"symbol": "NVDA", "changesPercentage": float("nan")},  # poison → dropped
        "AMD": {"symbol": "AMD", "changesPercentage": 4.0},
    }
    svc = _service_with(rows, quotes)
    t = (await svc._build_themes()).themes[0]
    assert t.ticker_count == 2
    assert t.change_percent == 4.0              # NaN excluded; averages the clean one


# ── 4. Theme DETAIL (drill-down) — schema parity ──────────────────────

_DETAIL_KEYS = {"slug", "title", "subtitle", "image_url", "accent_hex", "constituents"}
_CONSTITUENT_KEYS = {"ticker", "company_name", "price", "change_percent", "market_cap"}


def test_theme_detail_keys_match_ios_dto():
    d = ThemeDetailResponse(slug="silicon-rush", title="The Silicon Rush",
                            subtitle="…", image_url=None, accent_hex="22D3EE")
    assert set(d.model_dump().keys()) == _DETAIL_KEYS
    assert d.model_dump()["constituents"] == []   # default empty → iOS empty state


def test_theme_constituent_keys_match_ios_dto():
    c = ThemeConstituentResponse(ticker="NVDA", company_name="NVIDIA",
                                 price=120.0, change_percent=2.1, market_cap=3.0e12)
    assert set(c.model_dump().keys()) == _CONSTITUENT_KEYS


# ── 5. `_build_constituents` end-to-end (stub FMP) ────────────────────


def _detail_service(quotes_by_symbol, row="__unset__"):
    """A fresh service with a stubbed FMP; optionally a stubbed `_read_theme_row`."""
    HomeDashboardService._theme_detail_cache.clear()
    HomeDashboardService._theme_detail_inflight.clear()
    svc = HomeDashboardService()
    svc.fmp = _FakeThemesFMP(quotes_by_symbol)  # type: ignore[assignment]
    if row != "__unset__":
        svc._read_theme_row = lambda slug: row  # type: ignore[assignment]
    return svc


@pytest.mark.asyncio
async def test_build_constituents_empty_tickers():
    svc = _detail_service(quotes_by_symbol={})
    assert await svc._build_constituents([]) == []
    assert svc.fmp.batch_calls == []


@pytest.mark.asyncio
async def test_build_constituents_sorts_by_market_cap_desc():
    quotes = {
        "AMD":  {"symbol": "AMD",  "name": "AMD",    "price": 100.0, "changesPercentage": 1.0, "marketCap": 500_000_000.0},
        "NVDA": {"symbol": "NVDA", "name": "NVIDIA", "price": 120.0, "changesPercentage": 2.0, "marketCap": 3_000_000_000_000.0},
        "TSM":  {"symbol": "TSM",  "name": "TSMC",   "price": 180.0, "changesPercentage": 3.0, "marketCap": 45_000_000_000.0},
    }
    svc = _detail_service(quotes)
    rows = await svc._build_constituents(["AMD", "NVDA", "TSM"])
    assert [c.ticker for c in rows] == ["NVDA", "TSM", "AMD"]   # 3T, 45B, 500M
    assert rows[0].company_name == "NVIDIA"
    assert rows[0].price == 120.0
    assert rows[0].change_percent == 2.0
    assert rows[0].market_cap == 3_000_000_000_000.0


@pytest.mark.asyncio
async def test_build_constituents_missing_cap_sinks_to_bottom():
    quotes = {
        "A": {"symbol": "A", "name": "A", "price": 1.0, "changesPercentage": 0.0},                   # no marketCap
        "B": {"symbol": "B", "name": "B", "price": 2.0, "changesPercentage": 0.0, "marketCap": 9e9},
    }
    svc = _detail_service(quotes)
    rows = await svc._build_constituents(["A", "B"])
    assert [c.ticker for c in rows] == ["B", "A"]   # capped B first, uncapped A last
    assert rows[1].market_cap is None


@pytest.mark.asyncio
async def test_build_constituents_missing_quote_dropped_and_name_change_fallback():
    quotes = {
        "NVDA": {"symbol": "NVDA", "price": 120.0, "marketCap": 3e12},   # no name, no change
        # AMD intentionally absent → dropped
    }
    svc = _detail_service(quotes)
    rows = await svc._build_constituents(["NVDA", "AMD"])
    assert [c.ticker for c in rows] == ["NVDA"]      # unresolved AMD dropped
    assert rows[0].company_name == ""                # iOS falls back to the ticker
    assert rows[0].change_percent is None            # iOS hides the % line


@pytest.mark.asyncio
async def test_build_constituents_non_finite_price_and_change_become_none():
    quotes = {"X": {"symbol": "X", "name": "X", "price": float("inf"),
                    "changesPercentage": float("nan"), "marketCap": 1e9}}
    svc = _detail_service(quotes)
    rows = await svc._build_constituents(["X"])
    assert rows[0].price is None                     # inf rejected
    assert rows[0].change_percent is None            # NaN rejected
    assert rows[0].market_cap == 1e9


@pytest.mark.asyncio
async def test_build_theme_detail_maps_header_and_constituents():
    row = {
        "slug": "silicon-rush", "title": "The Silicon Rush",
        "subtitle": "The chips powering the AI era",
        "image_url": "https://x/hero.jpg", "accent_hex": "22D3EE",
        "tickers": ["NVDA", "AMD"],
    }
    quotes = {
        "NVDA": {"symbol": "NVDA", "name": "NVIDIA", "price": 120.0, "changesPercentage": 2.0, "marketCap": 3e12},
        "AMD":  {"symbol": "AMD",  "name": "AMD",    "price": 100.0, "changesPercentage": 1.0, "marketCap": 2e11},
    }
    svc = _detail_service(quotes, row=row)
    detail = await svc._build_theme_detail("silicon-rush")
    assert detail is not None
    assert detail.slug == "silicon-rush"
    assert detail.title == "The Silicon Rush"
    assert detail.subtitle == "The chips powering the AI era"
    assert detail.image_url == "https://x/hero.jpg"
    assert detail.accent_hex == "22D3EE"
    assert [c.ticker for c in detail.constituents] == ["NVDA", "AMD"]   # cap desc


@pytest.mark.asyncio
async def test_build_theme_detail_missing_row_is_none():
    svc = _detail_service(quotes_by_symbol={}, row=None)   # _read_theme_row → None
    assert await svc._build_theme_detail("ghost") is None


@pytest.mark.asyncio
async def test_build_theme_detail_missing_subtitle_defaults_empty():
    row = {"slug": "s", "title": "T", "tickers": []}       # no subtitle/image/accent
    svc = _detail_service(quotes_by_symbol={}, row=row)
    detail = await svc._build_theme_detail("s")
    assert detail.subtitle == ""
    assert detail.image_url is None
    assert detail.accent_hex == "22D3EE"                   # service default
    assert detail.constituents == []


@pytest.mark.asyncio
async def test_get_theme_detail_blank_slug_is_none():
    svc = _detail_service(quotes_by_symbol={})
    assert await svc.get_theme_detail("") is None
    assert await svc.get_theme_detail("   ") is None


@pytest.mark.asyncio
async def test_get_theme_detail_preserves_slug_casing_for_db_lookup():
    """Regression (adversarial review): the dashboard emits the slug VERBATIM and
    the `trending_themes.slug` UNIQUE constraint is case-sensitive, so
    get_theme_detail must query the DB with the ORIGINAL casing. Lowercasing here
    404'd a legitimately-uppercase slug (e.g. "AI-Boom") that renders fine on the
    dashboard. Assert `_read_theme_row` receives the original-cased slug."""
    HomeDashboardService._theme_detail_cache.clear()
    HomeDashboardService._theme_detail_inflight.clear()
    svc = HomeDashboardService()
    svc.fmp = _FakeThemesFMP({})  # type: ignore[assignment]  # no tickers → no quotes

    seen = {}

    def _spy(slug):
        seen["slug"] = slug
        # Row exists ONLY under its exact (uppercase) casing — mimics a case-
        # sensitive DB `.eq("slug", ...)`. A lowercased query would miss it.
        if slug == "AI-Boom":
            return {"slug": "AI-Boom", "title": "AI Boom", "tickers": []}
        return None

    svc._read_theme_row = _spy  # type: ignore[assignment]

    detail = await svc.get_theme_detail("AI-Boom")
    assert seen["slug"] == "AI-Boom"      # queried with original casing, NOT "ai-boom"
    assert detail is not None             # would be None (404) under the old lowercasing bug
    assert detail.slug == "AI-Boom"


@pytest.mark.asyncio
async def test_build_themes_dedupes_duplicate_tickers_in_row():
    """Regression (adversarial review): a duplicate in a theme's editorial tickers[]
    must NOT inflate ticker_count or double-weight the avg % change — and must match
    the detail path (_build_constituents dedupes its union)."""
    rows = [{"slug": "s", "title": "T", "tickers": ["NVDA", "NVDA", "AMD"], "sort_order": 0}]
    quotes = {
        "NVDA": {"symbol": "NVDA", "changesPercentage": 10.0},
        "AMD": {"symbol": "AMD", "changesPercentage": 4.0},
    }
    svc = _service_with(rows, quotes)
    t = (await svc._build_themes()).themes[0]
    assert t.ticker_count == 2          # NVDA counted once, not twice
    assert t.change_percent == 7.0      # avg(10, 4) — NOT (10+10+4)/3 = 8.0


@pytest.mark.asyncio
async def test_get_themes_caches_empty_successful_read():
    """Regression (adversarial review): a SUCCESSFUL 'no active themes' read is
    cached (mirrors get_scanners) — the dashboard must not re-hit Supabase every
    request while the section is legitimately empty."""
    HomeDashboardService._themes_cache.clear()
    HomeDashboardService._themes_inflight.clear()
    svc = HomeDashboardService()
    svc.fmp = _FakeThemesFMP({})  # type: ignore[assignment]
    calls = {"n": 0}

    def _read():
        calls["n"] += 1
        return []                        # successful read, zero active rows

    svc._read_theme_rows = _read  # type: ignore[assignment]

    r1 = await svc.get_themes()
    r2 = await svc.get_themes()
    assert r1.themes == [] and r2.themes == []
    assert calls["n"] == 1               # 2nd call served from cache — NOT re-read


@pytest.mark.asyncio
async def test_get_themes_does_not_cache_on_read_error():
    """Regression (adversarial review): a Supabase read ERROR must NOT be pinned as
    empty for 10 min — it degrades UNcached and retries next request."""
    HomeDashboardService._themes_cache.clear()
    HomeDashboardService._themes_inflight.clear()
    svc = HomeDashboardService()
    svc.fmp = _FakeThemesFMP({})  # type: ignore[assignment]
    calls = {"n": 0}

    def _read():
        calls["n"] += 1
        raise RuntimeError("supabase down")

    svc._read_theme_rows = _read  # type: ignore[assignment]

    r1 = await svc.get_themes()
    r2 = await svc.get_themes()
    assert r1.themes == [] and r2.themes == []   # degrades to empty both times
    assert calls["n"] == 2                        # NOT cached → re-attempted
