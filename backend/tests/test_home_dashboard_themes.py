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
