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
