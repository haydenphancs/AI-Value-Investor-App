"""Regression tests for the 2026-08 whole-flow deep check of the five asset-detail screens.

Each test pins ONE defect that was live in production, named in its docstring. Grouped by
the failure mode rather than by file, because the same mode kept recurring across sibling
services — a fix applied to the stock screen and never propagated to its four siblings, or
to the second copy of a shared helper.

No network, no Supabase: pure helpers and `__init__`-bypassed instances.
"""

from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.encoders import jsonable_encoder

from app.schemas.common import (
    normalize_fmp_list,
    normalize_fmp_response,
    sanitize_non_finite,
)


def _renders(payload) -> bool:
    """True iff the payload survives Starlette's `allow_nan=False` renderer."""
    json.dumps(jsonable_encoder(payload), allow_nan=False)
    return True


# ── 1. Untyped pass-through endpoints must not carry a non-finite ────────────
#
# GET /stocks/{ticker}, /quote, /fundamentals and /financials-full return RAW FMP dicts.
# FMP emits bare NaN / Infinity tokens, `json.loads` parses them, and a NaN is truthy so
# it survives `x or 0` and every `x <= 0` guard. It then reaches JSONResponse, which
# renders with allow_nan=False — a hard 500 raised INSIDE the renderer, i.e. after the
# endpoint's try/except has already returned. Every Pydantic-modelled sibling had been
# hardened with a per-service `_safe_float`; these had no schema policing them at all.

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_normalize_fmp_response_strips_non_finite(bad):
    out = normalize_fmp_response({"marketCap": bad, "companyName": "X"})
    assert out["market_cap"] is None
    assert out["company_name"] == "X"
    assert _renders(out)


def test_sanitize_recurses_through_nested_dicts_and_lists():
    out = sanitize_non_finite(
        {"a": [1.0, float("nan"), {"b": float("inf")}], "c": {"d": [float("-inf")]}}
    )
    assert out == {"a": [1.0, None, {"b": None}], "c": {"d": [None]}}
    assert _renders(out)


def test_normalize_fmp_list_strips_non_finite_per_row():
    rows = normalize_fmp_list([{"grossProfit": float("nan")}, {"grossProfit": 5.0}])
    assert rows[0]["gross_profit"] is None
    assert rows[1]["gross_profit"] == 5.0
    assert _renders(rows)


def test_sanitize_leaves_ordinary_values_alone():
    """A guard that also mangles good data is not a guard."""
    payload = {"i": 3, "f": 1.5, "s": "x", "b": True, "n": None, "l": [1, 2]}
    assert sanitize_non_finite(payload) == payload


# ── 2. The iOS StockDetail contract ──────────────────────────────────────────
#
# Verified live against FMP /stable/profile for AAPL: it returns `change`,
# `changePercentage`, `lastDividend`, `averageVolume` and `range`, and sends
# `fullTimeEmployees` as the STRING "166000". The iOS decoder keys on the /api/v3
# spellings and declares `fullTimeEmployees: Int?` — and a Swift optional THROWS on a
# type mismatch rather than yielding nil, so one string field failed the whole
# `StockDetail` decode on every ticker.

def test_range_band_parses_the_52_week_string():
    from app.api.v1.endpoints.stocks import _parse_range_band

    assert _parse_range_band("223.78-344.57") == (223.78, 344.57)
    # inverted input is normalised rather than trusted
    assert _parse_range_band("344.57-223.78") == (223.78, 344.57)


@pytest.mark.parametrize("raw", [None, "", "abc", "1-2-3", "nan-5", "inf-2", 42, ["1-2"]])
def test_range_band_refuses_anything_unparseable(raw):
    """A missing band must leave the 52-week fields ABSENT, never invent a number."""
    from app.api.v1.endpoints.stocks import _parse_range_band

    assert _parse_range_band(raw) == (None, None)


# ── 3. Cache keys must include every parameter that shapes the response ──────
#
# `etf_detail_cache` was keyed on symbol ALONE while `chart_data` is built from
# range+interval, so the first range fetched won and every later range pill silently
# returned it — for 5 minutes in-process and 24 hours from Supabase, for every user.
# The index cache key had `chart_range` but not `interval`, with the same effect on the
# interval picker.

def test_etf_cache_key_separates_every_chart_shape():
    from app.services.etf_service import ETFService

    k = ETFService._cache_key
    assert k("SPY", "3M", None) != k("SPY", "1Y", None)
    assert k("SPY", "1D", "5min") != k("SPY", "1D", "1hour")
    assert k("SPY", "3M", None) != k("QQQ", "3M", None)
    # and is stable / case-insensitive on the symbol
    assert k("spy", "3M", "daily") == k("SPY", "3M", "daily")


def test_index_cache_key_separates_every_chart_shape():
    from app.services.index_service import IndexService

    k = IndexService._cache_key
    assert k("^GSPC", "1D", "5min") != k("^GSPC", "1D", "1hour")
    assert k("^GSPC", "3M", None) != k("^GSPC", "1Y", None)


def test_commodity_detail_dedups_and_caches_per_chart_shape():
    """Commodity had NO caching of any kind: every open ran the full FMP fan-out, and N
    concurrent viewers of gold ran N of them. The key must carry the chart shape for the
    same reason the ETF one must."""
    from app.services import commodity_service as M

    M._cache.clear()
    M._inflight.clear()
    svc = M.CommodityService.__new__(M.CommodityService)
    builds = []

    async def fake_build(symbol, chart_range="3M", interval=None):
        builds.append((symbol, chart_range, interval))
        await asyncio.sleep(0.01)
        return f"{symbol}|{chart_range}|{interval}"

    svc._build_commodity_detail = fake_build

    async def scenario():
        # two concurrent callers, same shape -> ONE build (in-flight dedup)
        a, b = await asyncio.gather(
            svc.get_commodity_detail("GC", "3M", None),
            svc.get_commodity_detail("GC", "3M", None),
        )
        assert a == b
        # a repeat is served from the memory tier -> still one build
        await svc.get_commodity_detail("GC", "3M", None)
        # a DIFFERENT range must not be served the cached one
        other = await svc.get_commodity_detail("GC", "1Y", None)
        return other

    other = asyncio.run(scenario())
    assert builds == [("GC", "3M", None), ("GC", "1Y", None)], builds
    assert other.endswith("|1Y|None")


def test_commodity_inflight_leader_failure_does_not_strand_joiners():
    """`except Exception` does not catch CancelledError, and an unresolved future hangs
    every joiner for the life of the process."""
    from app.services import commodity_service as M

    M._cache.clear()
    M._inflight.clear()
    svc = M.CommodityService.__new__(M.CommodityService)

    async def boom(symbol, chart_range="3M", interval=None):
        await asyncio.sleep(0.01)
        raise RuntimeError("upstream down")

    svc._build_commodity_detail = boom

    async def scenario():
        results = await asyncio.gather(
            svc.get_commodity_detail("CL", "3M", None),
            svc.get_commodity_detail("CL", "3M", None),
            return_exceptions=True,
        )
        return results

    results = asyncio.run(scenario())
    assert all(isinstance(r, Exception) for r in results), results
    # and the map is clean, so the next caller is not blocked by a dead entry
    assert not M._inflight


# ── 4. FMP /stable key renames ───────────────────────────────────────────────

def test_index_reads_the_stable_change_percentage_spelling():
    """`changesPercentage` (plural) is the dead /api/v3 spelling. index_service read ONLY
    that, so `price_change_percent` was 0.0 on every index response — the S&P 500 badge
    read "+0.00%" all day. Every sibling reads the singular first."""
    src = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "app/services/index_service.py"
    ).read_text()
    # strip comments so prose about the old key cannot satisfy the assertion
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )
    singular = code.index('quote.get("changePercentage")')
    plural = code.index('quote.get("changesPercentage")')
    assert singular < plural, "the /stable singular spelling must be read FIRST"


# ── 5. Null dates: `.get("date", "")` is None for a present-but-null key ─────

def test_sector_benchmark_extract_year_survives_a_null_date():
    """`len(None)` is a TypeError, and most callers are not inside a try — so one null
    date aborted a whole sector's benchmark recompute, which is what makes
    "vs Industry Avg" quietly disappear from the detail screens."""
    from app.services.sector_benchmark_service import _extract_year

    assert _extract_year({"date": None}) == ""
    assert _extract_year({}) == ""
    assert _extract_year({"date": 20260331}) == ""
    assert _extract_year({"date": "2026-03-31"}) == "2026"
    assert _extract_year({"calendarYear": 2025, "date": None}) == "2025"


def test_holders_price_extractors_survive_null_dates_and_junk_rows():
    """`rec.get("date", "")[:10]` -> `None[:10]` TypeError, in two extractors that are
    not inside a try — a 502 for the whole Holders tab."""
    from app.services.holders_service import HoldersService

    rows = [
        {"date": None, "close": 10.0},          # present-but-null date
        {"close": 11.0},                        # missing date
        "not-a-dict",                           # FMP error payload element
        {"date": "2026-03-31", "close": 12.5},  # the only usable row
    ]
    monthly = HoldersService._extract_monthly_prices(rows)
    daily = HoldersService._extract_daily_prices(rows)
    assert monthly == {"03/2026": 12.5}
    assert [p.date for p in daily] == ["2026-03-31"]


def test_sector_aggregates_survives_a_null_date():
    """`row.get("date", "")[:4]` -> `None[:4]`, swallowed by the outer `except Exception`
    so the ticker silently vanished from the sector aggregate."""
    src = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "app/services/sector_aggregates_service.py"
    ).read_text()
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )
    assert 'row.get("date", "")[:4]' not in code
    assert '(row.get("date") or "")[:4]' in code


# ── 6. Non-finite reaching a REQUIRED response float ─────────────────────────

def test_crypto_related_builder_survives_non_finite_quotes():
    """`x or 0` does not guard a NaN (it is truthy), so it reached the REQUIRED
    RelatedCryptoResponse floats and 500'd the whole crypto detail screen. The commodity
    twin was fixed for this; the crypto one was not."""
    from app.services.crypto_service import CryptoService

    svc = CryptoService.__new__(CryptoService)
    out = svc._build_related_cryptos(
        [{"symbol": "ETHUSD", "price": float("nan"), "changePercentage": float("inf")}],
        ["ETH"],
    )
    assert out and out[0].price == 0 and out[0].change_percent == 0
    assert _renders(out)


def test_crypto_related_builder_prefers_the_stable_spelling():
    from app.services.crypto_service import CryptoService

    svc = CryptoService.__new__(CryptoService)
    out = svc._build_related_cryptos(
        [{"symbol": "ETHUSD", "price": 2.0,
          "changePercentage": 1.5, "changesPercentage": 99.0}],
        ["ETH"],
    )
    assert out[0].change_percent == 1.5


# ── 7. The second writer of ticker_news_cache ────────────────────────────────
#
# Two whole-batch aborts, both swallowed by `except Exception: logger.warning(...)`, so
# the shared cache silently lost every row from that fetch.

def test_news_timestamp_validator_rejects_only_batch_aborting_shapes():
    from app.services.sentiment_service import _looks_like_timestamp

    assert _looks_like_timestamp("2026-01-15 09:30:00")
    assert _looks_like_timestamp("2026-01-15T09:30:00Z")
    for bad in ("", "   ", None, 123, "bogus", ["2026"]):
        assert not _looks_like_timestamp(bad), bad


def test_sentiment_persist_dedups_urls_and_drops_unusable_dates():
    """FMP returns the same URL twice in one batch; `on_conflict="ticker,external_id"`
    then raises Postgres 21000 ("cannot affect row a second time") and aborts the ENTIRE
    upsert. An empty publishedDate raises 22007 on the timestamptz column, same effect."""
    from app.services.sentiment_service import SentimentService

    svc = SentimentService.__new__(SentimentService)
    captured = {}

    class _Table:
        def upsert(self, rows, on_conflict=None):
            captured["rows"] = rows
            captured["on_conflict"] = on_conflict
            return self

        def execute(self):
            return None

    class _SB:
        def table(self, _name):
            return _Table()

    svc.supabase = _SB()
    svc._persist_articles(
        "AAPL",
        [
            {"url": "u1", "title": "a", "publishedDate": "2026-01-15 09:30:00"},
            {"url": "u1", "title": "a duplicate of the same article"},   # dup key
            {"url": "u2", "title": "b", "publishedDate": ""},            # bad date
            {"url": "", "title": "no url"},                              # skipped
        ],
    )
    rows = captured["rows"]
    ids = [r["external_id"] for r in rows]
    assert ids == ["u1", "u2"], ids
    assert len(set(ids)) == len(ids), "duplicate conflict keys abort the whole upsert"
    assert rows[1]["published_at"] is None, "an empty date must be NULL, not ''"


# ── 8. Insider sentiment: one verdict unit across three screens ──────────────
#
# The Holders badge judged on SHARES while TickerReportView's `_build_insider_sections`
# judged on DOLLARS, so the two screens could state opposite conclusions for one ticker.
# Product decision: the VERDICT (badge value, sign, colour, `is_positive`, and the
# Overview ownership rating) is dollar-denominated; the chart BARS stay share-denominated
# because Form 4 reports exact share counts and a price-less row would vanish from a
# dollar chart.

def _flow(buy_m: float, sell_m: float):
    from app.schemas.holders import SmartMoneyFlowDataPointSchema

    return [SmartMoneyFlowDataPointSchema(
        month="01/2026", buy_volume=buy_m, sell_volume=sell_m, has_activity=True
    )]


def test_insider_verdict_follows_dollars_while_bars_stay_shares():
    """The canonical contradiction: buy 1M shares at $1, sell 100K at $50. Shares say
    net BUY (+0.9M); dollars say net SELL (-$4M). The bars must keep the share figure
    and the verdict must be bearish."""
    from app.services.holders_service import HoldersService

    summary = HoldersService._build_summary(
        _flow(1.0, 0.1), usd_buy_millions=1.0, usd_sell_millions=5.0
    )
    assert summary.total_net_flow == 0.9, "bars stay share-denominated"
    assert summary.net_flow_usd_millions == -4.0
    assert summary.is_positive is False, "the verdict follows the dollars"


def test_share_and_dollar_verdicts_agree_when_they_should():
    from app.services.holders_service import HoldersService

    s = HoldersService._build_summary(
        _flow(2.0, 0.5), usd_buy_millions=30.0, usd_sell_millions=4.0
    )
    assert s.total_net_flow == 1.5 and s.net_flow_usd_millions == 26.0
    assert s.is_positive is True


def test_summary_without_dollar_totals_keeps_the_share_verdict():
    """Institutions and Congress send no dollar totals; they must be unaffected."""
    from app.services.holders_service import HoldersService

    s = HoldersService._build_summary(_flow(0.2, 1.2))
    assert s.net_flow_usd_millions is None
    assert s.total_net_flow == -1.0 and s.is_positive is False


def test_a_flat_series_is_neutral_not_bullish():
    """`is_positive` is `>= 0`, so an exactly-flat series used to paint bullish green."""
    from app.services.holders_service import HoldersService

    s = HoldersService._build_summary(
        _flow(1.0, 1.0), usd_buy_millions=3.0, usd_sell_millions=3.0
    )
    assert s.net_flow_usd_millions == 0.0
    assert s.is_positive is True  # >= 0 by contract; iOS renders 0 as its own state


def test_overview_insider_rating_scores_on_the_same_unit_as_its_direction():
    """The 40%-weight factor took its DIRECTION from `is_positive` (now dollar-derived)
    and its MAGNITUDE from a share count. Mixing units scores a company by one unit's
    direction and another's size."""
    from app.services.ownership_snapshot_service import OwnershipSnapshotService

    svc = OwnershipSnapshotService.__new__(OwnershipSnapshotService)
    heavy_sell = svc._compute_rating(
        5.0, 60.0, 0.9, False, 1.0, True, insider_usd_millions=-40.0
    )
    modest_buy = svc._compute_rating(
        5.0, 60.0, 0.9, True, 1.0, True, insider_usd_millions=5.0
    )
    assert heavy_sell < modest_buy, (heavy_sell, modest_buy)


def test_overview_insider_row_prints_the_unit_it_judged_on():
    from app.services.ownership_snapshot_service import _fmt_usd_flow, _fmt_share_flow

    assert _fmt_usd_flow(-4.0, False) == "Net Sell $4.00M"
    assert _fmt_usd_flow(0.0, True) == "Neutral"
    assert _fmt_usd_flow(float("nan"), True) == "—"
    assert _fmt_usd_flow(0.008, True) == "Net Buy $8K"
    # the share formatter is untouched — it still serves the Institutions row
    assert "shares" in _fmt_share_flow(2.5, True)


# ── 9. A cache hit must not serve a 24-hour-old price ────────────────────────
#
# `etf_detail_cache` / `index_detail_cache` rows carry the REQUIRED price fields and live
# for 24h, and both cache-hit paths returned them verbatim. The index screen has no
# live-price WebSocket at all, so nothing corrected it afterwards. The fix re-quotes only
# the volatile fields on a hit — and must degrade to the cached values, never to zero.

class _StubFMP:
    def __init__(self, quote):
        self._quote = quote

    async def get_stock_price_quote(self, _symbol):
        if isinstance(self._quote, Exception):
            raise self._quote
        return self._quote


def _stale_response():
    import types

    return types.SimpleNamespace(
        current_price=100.0, price_change=1.0,
        price_change_percent=1.0, market_status=None,
    )


def test_etf_cache_hit_is_requoted_in_place():
    from app.services.etf_service import ETFService

    svc = ETFService.__new__(ETFService)
    svc.fmp = _StubFMP({"price": 212.5, "change": -3.5, "changePercentage": -1.62})
    r = _stale_response()
    asyncio.run(svc._refresh_volatile(r, "SPY"))
    assert (r.current_price, r.price_change, r.price_change_percent) == (212.5, -3.5, -1.62)
    assert r.market_status is not None, "the badge must be recomputed for NOW, not the row's age"


@pytest.mark.parametrize("bad_quote", [
    RuntimeError("FMP down"),
    {},
    {"price": float("nan")},
    {"price": 0},
    {"price": -1},
])
def test_a_failed_requote_keeps_the_cached_price_rather_than_zeroing_it(bad_quote):
    """Degrading to 0 would be worse than the staleness it replaces."""
    from app.services.etf_service import ETFService

    svc = ETFService.__new__(ETFService)
    svc.fmp = _StubFMP(bad_quote)
    r = _stale_response()
    asyncio.run(svc._refresh_volatile(r, "SPY"))
    assert r.current_price == 100.0


def test_index_requote_computes_the_percent_when_fmp_omits_it():
    """FMP /stable sometimes sends neither spelling; commodity already falls back to
    change/previousClose and the index path now does too."""
    from app.services.index_service import IndexService

    svc = IndexService.__new__(IndexService)
    svc.fmp = _StubFMP({"price": 5000.0, "change": 25.0, "previousClose": 4975.0})
    r = _stale_response()
    asyncio.run(svc._refresh_volatile(r, "^GSPC"))
    assert r.current_price == 5000.0
    assert r.price_change_percent == pytest.approx(0.5025, abs=1e-3)


def test_etf_market_status_follows_the_real_new_york_clock():
    """A hardcoded UTC-5 put every session boundary an hour late from March to November
    — about eight months a year — and stamped "EST" in August."""
    from app.services.etf_service import _get_market_status

    st = _get_market_status()
    if st.status == "closed":
        assert st.timezone in ("EST", "EDT")
        assert st.date and ("-04:00" in st.date or "-05:00" in st.date)
        # the label and the offset must agree with each other
        assert (st.timezone == "EDT") == ("-04:00" in st.date)


# ── 10. A stock split is not a purchase ──────────────────────────────────────
#
# FMP's 13F deltas (`changeInSharesNumber`, `numberOf13FsharesChange`) are a raw
# `current - last`, and `last` is the PRE-split count. `_compute_quarter_flow` (the
# quarterly chart) already restated across a split and suppressed an implausible
# magnitude; Recent Activities did neither, so on the same tab the chart drew no bar
# while the card announced tens of billions of buying.

_KLAC_BLACKROCK = {
    "investorName": "BLACKROCK INC",
    "sharesNumber": 126_198_653,          # post 10:1
    "lastSharesNumber": 12_596_207,       # pre-split
    "changeInSharesNumber": 113_602_446,  # raw, i.e. mostly the multiplication
    "changeInSharesNumberPercentage": 901.8782,
    "marketValue": 38_075_395_547,
    "filingDate": "2026-08-14",
}


def test_a_split_quarter_is_not_reported_as_a_34_billion_dollar_purchase():
    """KLAC did 10:1 on 2026-06-12, inside the quarter `latest_filed_13f_quarter()`
    selects. Live analytics for that quarter made BlackRock's row read
    `+$34,275.0M / +901.88%`; the true move was +236,583 shares (~+$71M)."""
    from app.services.holders_service import HoldersService

    svc = HoldersService.__new__(HoldersService)
    unrestated = svc._build_institutional_activities([_KLAC_BLACKROCK], split_ratio=1.0)[0]
    restated = svc._build_institutional_activities([_KLAC_BLACKROCK], split_ratio=10.0)[0]

    # the shape of the old bug, kept as the contrast
    assert unrestated.change_in_millions > 30_000
    assert unrestated.change_percent > 900

    # restated: a rounding-level move, and the percent recomputed from the new basis
    assert restated.change_in_millions == pytest.approx(71.4, abs=1.0)
    assert restated.change_percent == pytest.approx(0.19, abs=0.05)


def test_no_split_leaves_the_reported_numbers_untouched():
    """The restatement must be inert in the ordinary case, or it becomes its own bug."""
    from app.services.holders_service import HoldersService

    svc = HoldersService.__new__(HoldersService)
    ordinary = {
        "investorName": "VANGUARD GROUP INC",
        "sharesNumber": 10_500_000,
        "lastSharesNumber": 10_000_000,
        "changeInSharesNumber": 500_000,
        "changeInSharesNumberPercentage": 5.0,
        "marketValue": 1_050_000_000,
        "filingDate": "2026-08-14",
    }
    row = svc._build_institutional_activities([ordinary], split_ratio=1.0)[0]
    assert row.change_percent == pytest.approx(5.0, abs=0.01)
    assert row.change_in_millions == pytest.approx(50.0, abs=0.5)


def test_an_implausible_aggregate_delta_is_suppressed_not_rendered():
    """`|change| >= half the total position` is a restatement artefact, not a quarter's
    trading. The chart already suppressed it; the flow summary rendered it as
    '+$343.1B in'."""
    from app.services.holders_service import HoldersService
    from app.schemas.holders import DailyPricePointSchema

    svc = HoldersService.__new__(HoldersService)
    agg = {
        "numberOf13Fshares": 1_201_868_820,
        "lastNumberOf13Fshares": 115_612_040,
        "numberOf13FsharesChange": 1_086_256_780,
        "newPositions": 50, "increasedPositions": 500,
        "closedPositions": 20, "reducedPositions": 300,
    }
    prices = [DailyPricePointSchema(date="2026-06-30", price=301.71)]
    out = svc._build_institutional_flow_summary(
        [], aggregate_data=agg, daily_prices=prices, split_ratio=1.0
    )
    # with no per-holder activities to fall back on, the honest answer is zero flow —
    # never the $343B the raw delta implies
    assert out.in_flow_in_billions < 1.0 and out.out_flow_in_billions < 1.0


# ── 11. Round-2 confirmations ────────────────────────────────────────────────


def test_sentiment_is_crypto_is_a_parameter_not_singleton_state():
    """`get_sentiment_service()` returns a PROCESS-WIDE singleton, and the per-request
    crypto flag was stored on it. There are real awaits between the write and the read
    (`_get_articles` does `await asyncio.to_thread(self._load_from_db, …)`), so a
    concurrent request flipped it: an AAPL request that read the flipped True called
    `/news/crypto?symbols=AAPL`, which returns ZERO articles — and the news-less
    sentiment was then pinned in the 15-minute cache for every viewer of that ticker."""
    import inspect

    from app.services.sentiment_service import SentimentService

    for fn in (SentimentService._get_articles, SentimentService._fetch_news):
        assert "is_crypto" in inspect.signature(fn).parameters, fn.__name__

    src = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "app/services/sentiment_service.py"
    ).read_text()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "_is_crypto" not in code, "request state is back on the singleton"


def test_a_partial_ttm_is_omitted_rather_than_published_as_twelve_months():
    from app.services.health_check_service import _sum_ttm_income, _compute_z_score

    two = [
        {"date": "2026-06-30", "operatingIncome": 56_689_000, "revenue": 1_490_286_000,
         "interestExpense": 0, "netIncome": 1, "ebitda": 1},
        {"date": "2026-03-31", "operatingIncome": 222_510_000, "revenue": 1_457_576_000,
         "interestExpense": 0, "netIncome": 1, "ebitda": 1},
    ]
    assert _sum_ttm_income(two) == {}
    four = two + [dict(two[0], date="2025-12-31"), dict(two[1], date="2025-09-30")]
    assert _sum_ttm_income(four)  # a full year does produce totals

    bs = {"totalAssets": 7_429_258_000, "totalLiabilities": 6_639_995_000,
          "totalCurrentAssets": 1, "totalCurrentLiabilities": 1, "retainedEarnings": 1}
    assert _compute_z_score(bs, _sum_ttm_income(two), 1e9) is None


def test_zero_shares_is_unknown_not_a_measurement():
    """FMP returns `weightedAverageShsOut: 0` on real rows (verified live on CD). Treated
    as a measurement it produced a flat -100% share-count change — a spectacular fake
    buyback — and plunged the shares line to the axis."""
    from app.schemas.signal_of_confidence import SignalOfConfidenceDataPointSchema

    import inspect
    from app.services import signal_of_confidence_service as M

    src = inspect.getsource(M)
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "shares_raw is not None and shares_raw > 0" in code
    field = SignalOfConfidenceDataPointSchema.model_fields["shares_outstanding"]
    assert field.default is None, "0.0 is not a share count any listed company has"


def test_sub_cent_prices_survive_the_technical_detail_rounding():
    """SHIB trades near $0.00000495. Every level was `round(x, 2)`, so the pivots,
    fibonacci levels, support/resistance bands and current price all serialised as 0.0 —
    a full sheet of actionable price levels, all zero."""
    from app.services.technical_analysis_service import _round_price

    assert _round_price(0.00000495) == pytest.approx(0.00000495, rel=1e-6)
    assert _round_price(0.0001234) == pytest.approx(0.000123, rel=1e-3)
    assert _round_price(212.5) == 212.5          # equities keep 2dp
    assert _round_price(1.239) == 1.24
    for bad in (None, float("nan"), float("inf"), "x"):
        assert _round_price(bad) == 0.0


def test_a_chat_launched_from_the_etf_screen_is_not_treated_as_a_stock():
    """`detect_asset_class` has no ETF branch, so every ETF chat classified as STOCK and
    attached equity-fundamental snapshot ratings — profit margins and a moat verdict —
    computed as though the fund were an operating company."""
    from app.services.chat_service import ChatService

    assert ChatService._detect_asset_type("SPY") == "STOCK"          # symbol alone
    assert ChatService._detect_asset_type("SPY", "ETF") == "ETF"     # screen wins
    assert ChatService._detect_asset_type("AAPL", "STOCK") == "STOCK"
    assert ChatService._detect_asset_type("BTCUSD") == "CRYPTO"
    # an unknown declaration must not override the heuristic
    assert ChatService._detect_asset_type("AAPL", "NORMAL") == "STOCK"


def test_no_analyst_coverage_is_stated_not_rendered_as_a_hold():
    """FMP returns `[]` for both /grades and /price-target-consensus on real listings
    (AACT). The zero-defaults rendered as a confident HOLD at a $0.00 target."""
    from app.schemas.analyst import AnalystAnalysisResponse

    assert AnalystAnalysisResponse.model_fields["has_coverage"].default is True
    src = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "app/services/analyst_service.py"
    ).read_text()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "has_coverage = bool(total_analysts > 0 or target_consensus_price > 0)" in code
    # and the distribution is windowed rather than counting a decade of dead coverage
    assert "dist_cutoff" in code and "timedelta(days=730)" in code


def test_a_degraded_fundamentals_bundle_is_not_pinned_for_24h():
    """The poison gate checked ONLY the profile, while the heaviest slice —
    `get_historical_prices(…, "1900-01-01", …)` — is the one FMP 429s first. A good
    profile plus no history was cached for a day, silently emptying the Overview tab's
    performance periods and benchmark comparison."""
    import inspect
    from app.services import stock_overview_service as M

    code = "\n".join(
        l for l in inspect.getsource(M).splitlines() if not l.strip().startswith("#")
    )
    assert '"stock_historical": bool(data.get("stock_historical"))' in code
    assert '"key_metrics": bool(data.get("key_metrics"))' in code


def test_the_stream_meta_frame_carries_the_normalized_user_message():
    """iOS reconciles a failed stream by matching its RAW typed string against history,
    but the server persists `normalize_text(raw)` (NFKC + invisible/bidi/control
    stripping). Anything normalisation touched — a smart quote from the iOS keyboard, an
    emoji variation selector, a full-width character — never matched, so the reconcile
    concluded the turn had not persisted and RE-SENT it: a duplicated Q+A in history and
    a second credit charged for one message."""
    from app.services.chat_security import normalize_text

    src = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "app/api/v1/endpoints/chat.py"
    ).read_text()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert '"user_message": user_message,' in code, (
        "the meta frame must carry the server's own normalized copy"
    )

    # and normalisation really does change text an iOS keyboard produces
    smart = "What’s the outlook？"          # curly apostrophe + full-width ?
    assert normalize_text(smart) != smart, (
        "if this ever stops being true the reconcile mismatch cannot occur"
    )


def test_sub_penny_closes_survive_the_crypto_chart_rounding():
    """The DEFAULT crypto chart path (`range=3M` → daily → `_extract_chart_data`) emitted
    `round(close, 2)`, so SHIB/PEPE/BONK drew a flat line on the axis."""
    from app.services.crypto_service import _round_close

    assert _round_close(0.00000495) == pytest.approx(0.00000495, rel=1e-6)
    assert _round_close(0.0001234) == pytest.approx(0.000123, rel=1e-3)
    assert _round_close(212.567) == 212.57


def test_a_zero_price_is_omitted_from_the_chat_grounding_not_asserted():
    """`_price(0.0)` returned the TRUTHY string "0.00", and the caller's `if px and …`
    then wrote "Price $0.00" into the grounding lead — telling Cay AI the asset is
    worthless. The degraded gates elsewhere all treat `price <= 0` as absent."""
    from app.services.chat_context_resolver import _price

    assert _price(0.0) is None
    assert _price(-5.0) is None
    assert _price(float("nan")) is None
    assert _price(212.5) == "212.50"
    assert _price(0.00000495) == "0.00000495"   # meme coins keep significant figures


def test_a_price_free_holders_build_is_treated_as_degraded():
    """Every dollar figure on the Holders tab derives from these closes, but the fetch
    was not marked `critical`, so a build with NO price data at all was pinned in the 24h
    cache. Its five neighbours were already critical."""
    import inspect
    from app.services import holders_service as M

    code = "\n".join(
        l for l in inspect.getsource(M).splitlines() if not l.strip().startswith("#")
    )
    assert 'historical_prices, "Historical prices", [], critical=True' in code


# ── 15. An index is not its own benchmark ────────────────────────────
#
# `avg_annual_return` and `sp_benchmark` both read the SAME per-index constant, so
# the Nasdaq screen rendered "S&P 500 Benchmark 12.2%" — the Nasdaq's own long-run
# return wearing the S&P's label (the real S&P constant is 10.5). It also disabled
# the Outperforming/Underperforming badge on EVERY index, because the UI's
# `abs(avg - benchmark) > threshold` test can never be true when both sides are the
# same number. `_build_performance_periods` had already made the opposite (correct)
# call for `vs_market_percent` on the same screen.

def test_index_does_not_benchmark_itself():
    import inspect
    from app.services import index_service

    src = inspect.getsource(index_service.IndexService._build_response) \
        if hasattr(index_service.IndexService, "_build_response") else \
        inspect.getsource(index_service)
    # The two-fields-one-constant construction must be gone.
    assert 'sp_benchmark=profile_meta.get("avg_annual_return"' not in src, (
        "index benchmark_summary is echoing the index's own return as the S&P's"
    )


def test_index_profiles_do_not_all_share_the_sp_return():
    """Anti-vacuity control for the test above.

    If every index carried the same `avg_annual_return`, echoing it would be
    harmless and the fix meaningless. Prove the constants genuinely differ.
    """
    from app.services.index_service import _INDEX_PROFILES

    returns = {
        sym: p.get("avg_annual_return")
        for sym, p in _INDEX_PROFILES.items()
        if p.get("avg_annual_return") is not None
    }
    assert len(set(returns.values())) > 1, (
        f"expected differing per-index returns, got {returns}"
    )
    # The S&P's own constant is the only correct value for an S&P benchmark row.
    assert returns.get("^GSPC") != returns.get("^IXIC")


# ── 16. Market status knows the market calendar ──────────────────────
#
# Three services derived the session from weekday + hour only, so all three reported
# "open" at 11:00 ET on Thanksgiving and kept reporting "open" until 16:00 on the
# half-days that shut at 13:00. `app/utils/market_hours` already knew both facts and
# `home_dashboard_service` already delegated to it.

_ET = ZoneInfo("America/New_York")


@pytest.mark.parametrize("service_name", ["stock_overview_service", "etf_service", "index_service"])
def test_all_three_detail_services_share_one_market_calendar(service_name):
    """Every copy must delegate — a surviving local copy is the whole defect."""
    import importlib, inspect

    mod = importlib.import_module(f"app.services.{service_name}")
    src = inspect.getsource(mod._get_market_status)
    assert "market_status_fields" in src, f"{service_name} still hand-rolls the session"
    # The hand-rolled boundary arithmetic must be gone, not merely supplemented.
    assert "weekday()" not in src, f"{service_name} still branches on weekday"


@pytest.mark.parametrize(
    "when,expected",
    [
        # Thanksgiving 2026 — a weekday, mid-session by the clock, tape SHUT.
        (datetime(2026, 11, 26, 11, 0, tzinfo=_ET), "closed"),
        # Day after Thanksgiving 2026 closes at 13:00 ET.
        (datetime(2026, 11, 27, 14, 0, tzinfo=_ET), "closed"),
        # ...but the morning of a half-day is a genuine regular session.
        (datetime(2026, 11, 27, 11, 0, tzinfo=_ET), "open"),
        # Ordinary trading Tuesday.
        (datetime(2026, 8, 25, 11, 0, tzinfo=_ET), "open"),
        (datetime(2026, 8, 25, 8, 0, tzinfo=_ET), "pre_market"),
        (datetime(2026, 8, 25, 17, 0, tzinfo=_ET), "after_hours"),
        (datetime(2026, 8, 22, 12, 0, tzinfo=_ET), "closed"),  # Saturday
    ],
)
def test_market_status_respects_holidays_and_half_days(when, expected):
    from app.utils.market_hours import market_status_fields

    assert market_status_fields(when)["status"] == expected


def test_market_status_wire_values_match_the_ios_contract():
    """The wire strings are a CONTRACT with three iOS switches.

    `session_phase` speaks `premarket`/`regular`/`afterhours`; the client decodes
    `pre_market`/`open`/`after_hours`. A silent mismatch degrades to an unrecognised
    status on the client, which is exactly what the mapping layer exists to prevent.
    """
    from app.utils.market_hours import (
        _PHASE_TO_WIRE_STATUS,
        SESSION_AFTERHOURS,
        SESSION_CLOSED,
        SESSION_PREMARKET,
        SESSION_REGULAR,
    )

    # Total over every phase the module can emit — no phase may fall through.
    assert set(_PHASE_TO_WIRE_STATUS) == {
        SESSION_PREMARKET, SESSION_REGULAR, SESSION_AFTERHOURS, SESSION_CLOSED,
    }
    assert set(_PHASE_TO_WIRE_STATUS.values()) == {
        "pre_market", "open", "after_hours", "closed",
    }


def test_closed_status_names_the_last_real_close():
    """A weekend must name FRIDAY's close, and a half-day its 13:00 close.

    The old copies stamped `now.date()` at a hardcoded 16:00, so on a Saturday they
    announced a Saturday close that never happened.
    """
    from app.utils.market_hours import market_status_fields

    sat = market_status_fields(datetime(2026, 8, 22, 12, 0, tzinfo=_ET))
    assert sat["date"].startswith("2026-08-21")  # Friday, not Saturday
    assert sat["time"] == "4:00 PM"
    assert sat["timezone"] == "EDT"  # August

    half = market_status_fields(datetime(2026, 11, 27, 14, 0, tzinfo=_ET))
    assert half["date"].startswith("2026-11-27T13:00")
    assert half["time"] == "1:00 PM"
    assert half["timezone"] == "EST"  # November


# ── 17. A non-dividend payer still gets its buyback verdict ──────────
#
# `_build_dividend_info` returns None the moment dividend history is empty — which is
# every non-payer, including AMZN, BRK-B and NFLX, three of the largest repurchasers
# on the market. The buyback verdict depends ONLY on buyback yield + share-count
# change, so it was computed and thrown away, and the report's fallback then asserted
# a flat "Low".

def test_buyback_verdict_survives_an_empty_dividend_history():
    from app.services.signal_of_confidence_service import SignalOfConfidenceService

    svc = SignalOfConfidenceService.__new__(SignalOfConfidenceService)
    # An AMZN-shaped non-payer: no dividend, real buybacks, shrinking share count.
    assert svc._classify_buyback(3.0, -4.0) == "High"
    # Dilution is detected before yield, so issuing shares outranks a token buyback.
    assert svc._classify_buyback(5.0, 3.0) == "Diluting"
    assert svc._classify_buyback(0.0, 0.5) == "Diluting (Mild)"
    assert svc._classify_buyback(0.5, -0.1) == "Low"


def test_summary_always_carries_a_buyback_status():
    """It lives on the SUMMARY precisely because that is never None."""
    from app.schemas.signal_of_confidence import SignalOfConfidenceSummarySchema

    s = SignalOfConfidenceSummarySchema()
    assert s.buyback_status  # defaulted, never absent


def test_report_reads_buyback_from_the_summary_not_the_dividend_block():
    """The report used to hardcode "Low" whenever `dividend_info` was None."""
    from app.schemas.signal_of_confidence import (
        SignalOfConfidenceResponse,
        SignalOfConfidenceSummarySchema,
    )
    from app.services.agents.ticker_report_data_collector import (
        _build_capital_allocation_block,
    )

    soc = SignalOfConfidenceResponse(
        symbol="AMZN",
        data_points=[],
        summary=SignalOfConfidenceSummarySchema(
            total_yield=3.0, dividend_yield=0.0, buyback_yield=3.0,
            share_count_change=-4.0, buyback_status="High",
        ),
        dividend_info=None,  # a non-payer — the whole point
    )
    block = _build_capital_allocation_block(soc)
    assert block is not None
    assert block["buyback_status"] == "High", "report fell back to the hardcoded 'Low'"


# ── 18. Shared-layer: dedup, ingest, cache poisoning, cache keys ─────


def test_ticker_and_index_news_share_the_market_path_dedup():
    """`_inflight` was consulted ONLY by get_market_news.

    N users opening a cold ticker fired N identical FMP fetches. Source-scan because the
    real paths need Supabase; the behavioural proof is the two tests below.
    """
    import inspect
    from app.services import news_cache_service

    for fn in ("get_ticker_news", "get_index_news"):
        src = inspect.getsource(getattr(news_cache_service.NewsCacheService, fn))
        assert "_deduped(" in src, f"{fn} still fetches without herd protection"


@pytest.mark.asyncio
async def test_deduped_collapses_a_herd_to_one_fetch():
    from app.services.news_cache_service import NewsCacheService

    svc = NewsCacheService.__new__(NewsCacheService)
    svc._inflight = {}
    calls = {"n": 0}

    async def build():
        calls["n"] += 1
        await asyncio.sleep(0.02)
        return ["article"]

    results = await asyncio.gather(*[svc._deduped("AAPL", build) for _ in range(5)])
    assert calls["n"] == 1, "five concurrent viewers produced more than one FMP fetch"
    assert all(r == ["article"] for r in results)
    assert svc._inflight == {}, "the in-flight entry was not released"


@pytest.mark.asyncio
async def test_a_cancelled_joiner_does_not_kill_the_leader():
    """The shielded join is the load-bearing part.

    Awaiting the shared future UNSHIELDED means a cancelled joiner cancels the future the
    leader is about to publish into — killing the fetch for everyone still waiting.
    """
    from app.services.news_cache_service import NewsCacheService

    svc = NewsCacheService.__new__(NewsCacheService)
    svc._inflight = {}

    async def build():
        await asyncio.sleep(0.05)
        return ["article"]

    leader = asyncio.create_task(svc._deduped("MSFT", build))
    await asyncio.sleep(0.01)
    joiner = asyncio.create_task(svc._deduped("MSFT", build))
    await asyncio.sleep(0.01)
    joiner.cancel()

    assert await leader == ["article"]
    assert svc._inflight == {}


def test_ingest_path_inserts_related_tickers_on_a_new_row():
    """`ingest_only=True` skips the first-write columns so a refresh cannot clobber
    enrichment — correct for an EXISTING row, wrong for a new one, where the upsert is an
    INSERT and `related_tickers` takes its `[]` default forever."""
    import inspect
    from app.services import news_cache_service

    src = inspect.getsource(news_cache_service.NewsCacheService._build_and_cache_rows)
    assert "insert_only_rows" in src
    # Create-only: it must never overwrite an existing (possibly enriched) row.
    assert "ignore_duplicates=True" in src, (
        "the create-only pre-pass must be ON CONFLICT DO NOTHING, or it re-clobbers "
        "the Gemini-extracted related tickers on every refresh"
    )


def test_a_transient_supabase_failure_is_not_cached():
    """`_query` swallowed every exception and returned the same empty dict a successful
    zero-row query returns, so ONE blip erased every sector comparison for a full hour."""
    from app.services import sector_benchmark_lookup as sbl

    svc = sbl.SectorBenchmarkLookup.__new__(sbl.SectorBenchmarkLookup)
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("supabase 520")

    svc._fetch_rows = boom
    first = svc.get_sector_benchmarks("TestSector", ["eps_yoy"], "annual")
    second = svc.get_sector_benchmarks("TestSector", ["eps_yoy"], "annual")

    assert first == {"eps_yoy": {}} == second, "caller contract changed"
    assert calls["n"] == 2, "a failure was cached and the retry never happened"


def test_a_genuinely_empty_benchmark_result_is_still_cached():
    """Anti-vacuity control for the test above: a peer group with no rows is a real
    answer and must still be negatively cached, or every miss re-queries forever."""
    from app.services import sector_benchmark_lookup as sbl

    svc = sbl.SectorBenchmarkLookup.__new__(sbl.SectorBenchmarkLookup)
    calls = {"n": 0}

    def empty(*a, **k):
        calls["n"] += 1
        return []

    svc._fetch_rows = empty
    svc.get_sector_benchmarks("EmptySector", ["eps_yoy"], "annual")
    svc.get_sector_benchmarks("EmptySector", ["eps_yoy"], "annual")
    assert calls["n"] == 1, "a valid empty result should be cached"


def test_deep_dive_cache_separates_two_different_questions():
    """The key was md5(context) alone and ignored the message, while the gate is a bare
    substring test — so a second, different "deep dive ..." question on the same screen
    was answered with the first one's cached report."""
    from app.services.chat_service import ChatService

    ctx = "S&P 500 snapshot: level 6100, +0.4%"
    a = ChatService._deep_dive_cache_key(ctx, "Give me a comprehensive Market Deep Dive")
    b = ChatService._deep_dive_cache_key(ctx, "deep dive on the risks of a rate cut")
    assert a != b, "two different questions still collide on one cache entry"

    # ...but trivial formatting differences must still share an entry, or the cache
    # never hits for the canned prompt it exists to serve.
    assert ChatService._deep_dive_cache_key(ctx, "Deep Dive On Gold") == \
        ChatService._deep_dive_cache_key(ctx, "  deep   dive on gold ")
    # And the context still participates.
    assert ChatService._deep_dive_cache_key(ctx, "deep dive") != \
        ChatService._deep_dive_cache_key(ctx + "!", "deep dive")


def test_streamed_reasoning_passes_output_enforcement():
    """Reasoning is a SECOND rendered+persisted channel that bypassed enforcement.

    It is the likelier place to name the underlying provider, because the identity rule
    shapes the answer far more strongly than the scratchpad.
    """
    import inspect
    from app.api.v1.endpoints import chat as chat_ep

    src = inspect.getsource(chat_ep)
    assert "reasoning_text, reasoning_enforced = enforce_answer(reasoning_text)" in src, (
        "streamed reasoning is not passed through enforce_answer"
    )


def test_enforce_answer_actually_redacts_what_reasoning_would_leak():
    """Anti-vacuity control: proves the guardrail the test above wires up does something.

    The identity patterns are deliberately FIRST-PERSON anchored so an AI-investing
    product can still discuss the AI sector ("NVIDIA, as an AI chip maker"). These two
    classes are exactly what a model scratchpad plausibly emits.
    """
    from app.services.agents.chat_guardrails import enforce_answer

    identity, id_tags = enforce_answer("I was created by Google.")
    assert "Google" not in identity, f"identity leak survived: {identity!r}"
    assert "identity_redacted" in id_tags

    schema, sc_tags = enforce_answer("My service_role key is in chat_messages.")
    assert "service_role" not in schema and "chat_messages" not in schema
    assert "schema_redacted" in sc_tags


# ── 19. iOS source-scan guards for the detail-screen defects ─────────
#
# There is no XCTest target, so these iOS invariants are pinned from Python by reading
# the Swift source. They go vacuous VERY easily, so all three rules from
# `.claude/rules/testing.md` apply: strip comments first (the explanatory comment beside
# each fix contains every token the scan looks for, so an un-stripped scan passes on
# prose after the code is reverted), brace-bound the declaration, and mutation-test once.

import re as _re
from pathlib import Path as _Path

_IOS = _Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"


def _swift_code(path: _Path) -> str:
    """Swift source with `//` comments and block comments removed.

    Load-bearing: every fix below is documented by a comment that names the exact symbol
    the assertion greps for, so scanning the raw file would pass even after the code was
    reverted. `_control_comment_stripping_actually_works` proves this still bites.
    """
    src = path.read_text()
    src = _re.sub(r"/\*.*?\*/", "", src, flags=_re.S)
    return "\n".join(_re.sub(r"//.*$", "", line) for line in src.splitlines())


def _func_body(code: str, signature: str) -> str:
    """The brace-bounded body of one declaration.

    Asserting against a whole file passes when the token lives in a DIFFERENT type —
    exactly how a fix to a preview-only duplicate once looked like a fix to the live one.
    """
    start = code.index(signature)
    depth, i = 0, code.index("{", start)
    body_start = i
    while i < len(code):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[body_start:i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces after {signature!r}")


def test_control_comment_stripping_actually_works():
    """Anti-vacuity control for `_swift_code` itself.

    If the stripper silently stopped stripping, every scan below would start passing on
    its own explanatory prose and prove nothing at all.
    """
    code = _swift_code(_IOS / "ViewModels" / "CommodityDetailViewModel.swift")
    assert "//" not in code
    # A phrase that exists ONLY inside a comment in that file must be gone.
    assert "frozen for the entire life of the screen" not in code


@pytest.mark.parametrize("vm", [
    "CryptoDetailViewModel", "ETFDetailViewModel",
    "IndexDetailViewModel", "CommodityDetailViewModel", "TickerDetailViewModel",
])
def test_every_detail_screen_guards_its_favorite_against_a_slow_watchlist_get(vm):
    """A watchlist snapshot already in flight when the user taps the star must not
    overwrite their intent. TickerDetailViewModel had this; the four siblings shipped
    without it, so the star silently reverted with no error and no trace."""
    code = _swift_code(_IOS / "ViewModels" / f"{vm}.swift")

    toggle = _func_body(code, "func toggleFavorite()")
    assert "favoriteToggleGeneration &+= 1" in toggle, f"{vm} does not bump the guard on tap"

    check = _func_body(code, "func checkWatchlistStatus()")
    assert "let generation = favoriteToggleGeneration" in check, \
        f"{vm} does not capture the generation before its GET"
    assert "guard generation == self.favoriteToggleGeneration else" in check, \
        f"{vm} applies a possibly-stale watchlist snapshot over the user's tap"


@pytest.mark.parametrize("vm", ["IndexDetailViewModel", "CommodityDetailViewModel"])
def test_a_failed_technical_fetch_is_recoverable(vm):
    """`isTechnicalLoaded = true` with nil data is TERMINAL, and neither refresh()
    retried it — so one failed fetch blanked the Analysis tab for the life of the
    screen, with no gesture anywhere in the app able to recover it."""
    code = _swift_code(_IOS / "ViewModels" / f"{vm}.swift")

    retry = _func_body(code, "func retryTechnicalAnalysis()")
    assert "isTechnicalLoaded = false" in retry, f"{vm} retry does not clear the terminal flag"
    assert "fetchTechnicalAnalysis()" in retry

    refresh = _func_body(code, "func refresh()")
    assert "retryTechnicalAnalysis()" in refresh, \
        f"{vm}.refresh() still cannot un-stick a blank Analysis tab"

    # And the failure must be CLASSIFIED, so the tab can tell "no data for this asset"
    # (the backend 404s) from "the fetch failed" — a retry button on the former lies.
    fetch = _func_body(code, "func fetchTechnicalAnalysis()")
    assert "technicalUnavailableMessage" in fetch
    assert "technicalIsRetryable" in fetch


@pytest.mark.parametrize("screen", ["IndexDetailView", "CommodityDetailView"])
def test_the_analysis_tab_renders_something_when_it_has_nothing(screen):
    """Both else-branches rendered literally nothing, which reads as a broken app."""
    code = _swift_code(_IOS / "Views" / "Screens" / f"{screen}.swift")
    assert "technicalUnavailableMessage" in code, f"{screen} still renders a blank Analysis tab"
    assert "InlineRetryNotice" in code and "ChartUnavailableView" in code, \
        f"{screen} does not distinguish retryable from unsupported"


def test_chat_clears_grounding_when_switching_conversations():
    """`currentSessionId` moved to the new session before the fetch while the six
    grounding fields were rewritten only on SUCCESS — so a failed history load left
    session B selected while still carrying session A's ticker."""
    code = _swift_code(_IOS / "ViewModels" / "ChatViewModel.swift")
    body = _func_body(code, "func loadConversation(sessionId: String)")

    assign = body.index("currentSessionId = sessionId")
    fetch = body.index("getChatHistory")
    for field in ("currentStockId = nil", "currentContext = nil",
                  "currentContextType = nil", "currentReferenceId = nil"):
        assert field in body, f"loadConversation never clears {field}"
        assert assign < body.index(field) < fetch, \
            f"{field} is cleared outside the pre-fetch window (a failed load keeps it)"


def test_a_failed_session_create_retry_keeps_its_grounding():
    """The nil-session retry called `startNewConversation(firstMessage:)`, whose nil
    DEFAULTS then overwrote the grounding still held in instance state — turning a
    retried "Ask Cay AI about NVDA" into a generic chat about nothing."""
    code = _swift_code(_IOS / "ViewModels" / "ChatViewModel.swift")
    body = _func_body(code, "func sendMessage(_ text: String)")

    guard_idx = body.index("guard let sessionId = currentSessionId else")
    tail = body[guard_idx:guard_idx + 900]
    for arg in ("stockId: currentStockId", "context: currentContext",
                "contextType: currentContextType", "referenceId: currentReferenceId"):
        assert arg in tail, f"the retry path drops grounding: missing {arg}"


def test_a_failed_history_load_is_not_an_empty_account():
    """The catch set no state at all, so a network failure rendered "No conversations
    yet" — indistinguishable from a new user, and offering no retry."""
    vm = _swift_code(_IOS / "ViewModels" / "ChatViewModel.swift")
    assert "historyLoadFailed = true" in _func_body(vm, "func loadHistory()")

    view = _swift_code(_IOS / "Views" / "Screens" / "ChatHistoryView.swift")
    assert "loadFailed" in view and "failedState" in view, \
        "ChatHistoryView cannot distinguish a failure from an empty account"


def test_commodity_screen_actually_refreshes():
    """`chartRefreshTask` was declared and NEVER assigned — the field implied a refresh
    that never ran, and the quote was frozen for the life of the screen."""
    code = _swift_code(_IOS / "ViewModels" / "CommodityDetailViewModel.swift")
    assert "chartRefreshTask = Task" in code, "chartRefreshTask is still a dead stub"
    assert "LivePriceWebSocketManager()" in code

    timer = _func_body(code, "func startChartRefreshTimer()")
    # NOT gated on the US EQUITY session: these are continuously-quoted futures, and
    # `isMarketActive()` alone would freeze the screen for most of the day.
    assert "symbolTradesAroundTheClock" in timer, \
        "the commodity refresh is gated on US equity hours"

    view = _swift_code(_IOS / "Views" / "Screens" / "CommodityDetailView.swift")
    assert "disconnectLivePrice()" in view, "the socket outlives the screen"


def test_one_shared_compact_number_formatter():
    """Eight private `formatLargeNumber` copies had grown with different tier rules, and
    two of them sat on the same screen disagreeing with each other."""
    for rel in ("Views/Molecules/RevenueBreakdownChartView.swift",
                "Views/Molecules/GrowthChartView.swift",
                "Models/RevenueBreakdownModels.swift"):
        code = _swift_code(_IOS / rel)
        body = _func_body(code, "func formatLargeNumber(_ number: Double) -> String")
        assert "CompactNumberFormat.string" in body, f"{rel} still hand-rolls its formatter"
        assert "%.0f" not in body, f"{rel} still rounds every tier to whole units"

    earnings = _swift_code(_IOS / "Views" / "Organisms" / "EarningsSurpriseBarChart.swift")
    y = _func_body(earnings, "func formatYValue(_ value: Double) -> String")
    assert "CompactNumberFormat.percentString" in y
    assert "9_999" not in y, "the caption is still clamped while the plot domain is not"
