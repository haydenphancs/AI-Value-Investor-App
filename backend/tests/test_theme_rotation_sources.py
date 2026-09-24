"""Theme-rotation data sources: strict FMP loads that either return real data or say they could not.

Covers `app/services/theme_rotation/sources.py` and `FMPClient.get_etf_holdings_strict`.

Hermetic: every FMP read goes through a small in-memory `FakeFMP` (the loaders only need
the five methods they call), or through a real `FMPClient` whose `_make_request` /
`_make_request_impl` is monkeypatched on the INSTANCE — the binding the public methods
actually call — so the real parameter mapping is exercised with no network.

The contract under test (module docstring of `sources.py`): a SYSTEMIC gap raises
`ThemeSourceError` and fails the whole run; a PER-TICKER gap is recorded as UNKNOWN
(`None`), never as zero, and never crashes the run.
"""
from __future__ import annotations

import logging
import math
import random
from datetime import date, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import httpx
import pytest

from app.integrations.fmp import (
    FMPClient,
    FMPNotEntitledException,
    FMPRateLimitException,
    FMPUnavailableException,
)
from app.integrations.fmp_entitlements import entitlement_error, is_blocked_symbol
from app.services.theme_rotation import sources
from app.services.theme_rotation.sources import (
    MIN_UNIVERSE_ROWS,
    SCREENER_MAX_PAGES,
    SCREENER_PAGE_LIMIT,
    SESSIONS_3M,
    SESSIONS_6M,
    CallCounter,
    PriceStats,
    ThemeSourceError,
    _approx_sessions,
    _bars,
    latest_segments,
    load_etf_holdings,
    load_price_stats,
    load_profiles,
    load_segments,
    load_universe,
    price_stats,
)

_LOGGER = "app.services.theme_rotation.sources"


# ── Fakes / helpers ───────────────────────────────────────────────────────────────────

def _http_error(status: int) -> httpx.HTTPStatusError:
    """A real HTTPStatusError carrying `.response.status_code` — built, never sent."""
    req = httpx.Request("GET", "https://fmp.invalid/stable/x")
    return httpx.HTTPStatusError(f"HTTP {status}", request=req,
                                 response=httpx.Response(status, request=req))


def _resolve(value: Any, *args: Any) -> Any:
    if isinstance(value, BaseException):
        raise value
    if callable(value):
        return value(*args)
    return value


class FakeFMP:
    """The five FMPClient methods `sources` calls, answered from per-symbol tables."""

    def __init__(self, *, screener_pages: Optional[List[Any]] = None,
                 holdings: Optional[Dict[str, Any]] = None,
                 profiles: Optional[Dict[str, Any]] = None,
                 segments: Optional[Dict[str, Any]] = None,
                 prices: Optional[Dict[str, Any]] = None,
                 default: Any = None):
        self.screener_pages = screener_pages or []
        self.holdings = holdings or {}
        self.profiles = profiles or {}
        self.segments = segments or {}
        self.prices = prices or {}
        self.default = default
        self.screener_calls: List[dict] = []
        self.holdings_calls: List[str] = []
        self.profile_calls: List[str] = []
        self.segment_calls: List[tuple] = []
        self.price_calls: List[tuple] = []

    async def get_company_screener(self, **kw: Any) -> Any:
        self.screener_calls.append(kw)
        page = kw["page"]
        value = self.screener_pages[page] if page < len(self.screener_pages) else []
        return _resolve(value, page)

    async def get_etf_holdings_strict(self, ticker: str) -> Any:
        self.holdings_calls.append(ticker)
        return _resolve(self.holdings.get(ticker, self.default), ticker)

    async def get_company_profile(self, ticker: str) -> Any:
        self.profile_calls.append(ticker)
        return _resolve(self.profiles.get(ticker, self.default), ticker)

    async def get_revenue_product_segmentation(self, ticker: str, period: str = "annual",
                                               structure: str = "flat") -> Any:
        self.segment_calls.append((ticker, period, structure))
        return _resolve(self.segments.get(ticker, self.default), ticker)

    async def get_historical_prices(self, ticker: str, from_date: Optional[str] = None,
                                    to_date: Optional[str] = None) -> Any:
        self.price_calls.append((ticker, from_date, to_date))
        return _resolve(self.prices.get(ticker, self.default), ticker)


def _screener_rows(n: int, start: int = 0, prefix: str = "S") -> List[dict]:
    return [{"symbol": f"{prefix}{i:06d}", "marketCap": 1e9} for i in range(start, start + n)]


def _weekdays(start: date, n: int) -> List[date]:
    out: List[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _mk_bars(start: date, n: int, closes: Optional[List[float]] = None,
             volume: float = 1_000.0) -> List[dict]:
    ds = _weekdays(start, n)
    closes = closes if closes is not None else [float(i + 1) for i in range(n)]
    return [{"date": d.isoformat(), "close": float(c), "volume": volume}
            for d, c in zip(ds, closes)]


def _naive_sessions(start: date, end: date) -> int:
    if end < start:
        return 0
    return sum(1 for i in range((end - start).days + 1)
               if (start + timedelta(days=i)).weekday() < 5)


W = date(2026, 3, 2)   # a Monday; W+10 is a Thursday, W+11 a Friday


# ── CallCounter / ThemeSourceError ────────────────────────────────────────────────────

def test_call_counter_starts_at_zero_and_accumulates():
    c = CallCounter()
    assert c.calls == 0
    c.add()
    c.add(3)
    c.add(0)
    assert c.calls == 4


def test_theme_source_error_carries_source_and_detail():
    e = ThemeSourceError("company-screener", "only 5 rows")
    assert e.source == "company-screener"
    assert e.detail == "only 5 rows"
    assert str(e) == "company-screener: only 5 rows"
    assert isinstance(e, Exception)


def test_every_endpoint_the_rotation_reads_is_licensed():
    """A path dropped from the entitlement manifest would be refused PRE-flight on every
    call — the run would fail closed every month with no FMP traffic to show why."""
    for path in ("company-screener", "etf/holdings", "profile",
                 "revenue-product-segmentation", "historical-price-eod/full"):
        assert entitlement_error(path) is None, path


# ── load_universe ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_universe_single_short_page_stops_after_one_call_with_the_exact_filters():
    fmp = FakeFMP(screener_pages=[_screener_rows(2_500)])
    counter = CallCounter()
    rows = await load_universe(fmp, counter)
    assert len(rows) == 2_500
    assert counter.calls == 1
    assert len(fmp.screener_calls) == 1
    kw = fmp.screener_calls[0]
    assert kw == {
        "market_cap_more_than": 300_000_000,
        "exchange": "NASDAQ,NYSE,AMEX",
        "actively_trading": True,
        "is_fund": False,
        "is_etf": False,
        "limit": SCREENER_PAGE_LIMIT,
        "page": 0,
    }
    # `is False`, not falsy: None would silently drop the filter and let funds in.
    assert kw["is_fund"] is False and kw["is_etf"] is False


@pytest.mark.asyncio
async def test_universe_pages_until_a_short_page_and_merges():
    full = _screener_rows(SCREENER_PAGE_LIMIT, 0)
    short = _screener_rows(1_234, SCREENER_PAGE_LIMIT)
    fmp = FakeFMP(screener_pages=[full, short, _screener_rows(99, 90_000)])
    counter = CallCounter()
    rows = await load_universe(fmp, counter)
    assert len(rows) == SCREENER_PAGE_LIMIT + 1_234
    assert [k["page"] for k in fmp.screener_calls] == [0, 1]   # page 2 never requested
    assert counter.calls == 2


@pytest.mark.asyncio
async def test_universe_full_page_then_empty_page_is_complete_not_an_error():
    fmp = FakeFMP(screener_pages=[_screener_rows(SCREENER_PAGE_LIMIT), []])
    counter = CallCounter()
    rows = await load_universe(fmp, counter)
    assert len(rows) == SCREENER_PAGE_LIMIT
    assert counter.calls == 2


@pytest.mark.asyncio
async def test_universe_uppercases_strips_and_dedupes_symbols_across_pages():
    page0 = _screener_rows(SCREENER_PAGE_LIMIT - 2)
    page0 += [{"symbol": " nvda "}, {"symbol": "Amd"}]
    # page 1 overlaps page 0 (FMP pages can shift while being walked) and repeats a symbol
    # in a different case.
    page1 = _screener_rows(10) + [{"symbol": "NVDA"}, {"symbol": "amd"}, {"symbol": "tsm"}]
    fmp = FakeFMP(screener_pages=[page0, page1])
    rows = await load_universe(fmp, CallCounter())
    assert "NVDA" in rows and "AMD" in rows and "TSM" in rows
    assert " nvda " not in rows and "Amd" not in rows
    assert len(rows) == SCREENER_PAGE_LIMIT + 1        # only TSM is new
    assert all(k == k.strip().upper() for k in rows)


@pytest.mark.asyncio
async def test_universe_skips_malformed_rows_and_they_do_not_count_toward_the_minimum():
    good = _screener_rows(MIN_UNIVERSE_ROWS - 1)
    junk = [None, "AAPL", 42, {}, {"symbol": None}, {"symbol": ""}, {"symbol": "   "},
            {"symbol": 123}, {"symbol": ["X"]}, {"name": "no symbol"}]
    fmp = FakeFMP(screener_pages=[good + junk])
    with pytest.raises(ThemeSourceError) as ei:
        await load_universe(fmp, CallCounter())
    assert ei.value.source == "company-screener"
    assert str(MIN_UNIVERSE_ROWS - 1) in ei.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("first_page", [[], None])
async def test_universe_empty_first_page_is_a_failure_not_no_stocks(first_page):
    fmp = FakeFMP(screener_pages=[first_page])
    counter = CallCounter()
    with pytest.raises(ThemeSourceError) as ei:
        await load_universe(fmp, counter)
    assert ei.value.source == "company-screener"
    assert "first page" in ei.value.detail
    assert counter.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    FMPUnavailableException("503"),
    FMPRateLimitException("429", retry_after="30"),
    FMPNotEntitledException("nope"),
    _http_error(400),
    ValueError("bad json"),
])
async def test_universe_exception_on_first_page_raises_typed_source_error(exc):
    fmp = FakeFMP(screener_pages=[exc])
    with pytest.raises(ThemeSourceError) as ei:
        await load_universe(fmp, CallCounter())
    assert ei.value.source == "company-screener"
    assert "page 0" in ei.value.detail
    assert type(exc).__name__ in ei.value.detail
    assert ei.value.__cause__ is exc


@pytest.mark.asyncio
async def test_universe_exception_on_a_later_page_discards_the_partial_sweep():
    """A lost page 1 must not publish a universe missing its tail."""
    boom = FMPUnavailableException("timeout")
    fmp = FakeFMP(screener_pages=[_screener_rows(SCREENER_PAGE_LIMIT), boom])
    counter = CallCounter()
    with pytest.raises(ThemeSourceError) as ei:
        await load_universe(fmp, counter)
    assert "page 1" in ei.value.detail
    assert ei.value.__cause__ is boom
    assert counter.calls == 2      # the failed call is still counted


@pytest.mark.asyncio
async def test_universe_still_full_after_max_pages_raises():
    pages = [_screener_rows(SCREENER_PAGE_LIMIT, i * SCREENER_PAGE_LIMIT)
             for i in range(SCREENER_MAX_PAGES + 2)]
    fmp = FakeFMP(screener_pages=pages)
    counter = CallCounter()
    with pytest.raises(ThemeSourceError) as ei:
        await load_universe(fmp, counter)
    assert "still full" in ei.value.detail
    assert counter.calls == SCREENER_MAX_PAGES
    assert [k["page"] for k in fmp.screener_calls] == list(range(SCREENER_MAX_PAGES))


@pytest.mark.asyncio
async def test_universe_that_ignores_the_page_param_fails_closed():
    """FMP answering every page with page 0 would otherwise loop to the cap and — since the
    symbols dedupe — look like a 10,000-row universe. It must fail, not publish."""
    same = _screener_rows(SCREENER_PAGE_LIMIT)
    fmp = FakeFMP(screener_pages=[same] * SCREENER_MAX_PAGES)
    with pytest.raises(ThemeSourceError):
        await load_universe(fmp, CallCounter())


@pytest.mark.asyncio
async def test_universe_minimum_row_boundary():
    ok = await load_universe(FakeFMP(screener_pages=[_screener_rows(MIN_UNIVERSE_ROWS)]),
                             CallCounter())
    assert len(ok) == MIN_UNIVERSE_ROWS
    with pytest.raises(ThemeSourceError) as ei:
        await load_universe(FakeFMP(screener_pages=[_screener_rows(MIN_UNIVERSE_ROWS - 1)]),
                            CallCounter())
    assert f"< {MIN_UNIVERSE_ROWS}" in ei.value.detail


@pytest.mark.asyncio
async def test_universe_minimum_counts_unique_symbols_not_raw_rows():
    rows = _screener_rows(MIN_UNIVERSE_ROWS // 2) * 3          # 3,000 rows, 1,000 symbols
    fmp = FakeFMP(screener_pages=[rows])
    with pytest.raises(ThemeSourceError):
        await load_universe(fmp, CallCounter())


@pytest.mark.asyncio
async def test_universe_through_the_real_client_sends_the_exact_query(monkeypatch):
    """The screener filters as they leave the REAL FMPClient — funds and ETFs excluded."""
    client = FMPClient()
    sent: List[tuple] = []

    async def fake_request(endpoint, params=None):
        sent.append((endpoint, dict(params or {})))
        page = (params or {}).get("page", 0)
        return _screener_rows(SCREENER_PAGE_LIMIT) if page == 0 else _screener_rows(5, 10**6)

    monkeypatch.setattr(client, "_make_request", fake_request)
    rows = await load_universe(client, CallCounter())
    assert len(rows) == SCREENER_PAGE_LIMIT + 5
    assert sent[0] == ("company-screener", {
        "limit": SCREENER_PAGE_LIMIT, "marketCapMoreThan": 300_000_000,
        "exchange": "NASDAQ,NYSE,AMEX", "isActivelyTrading": "true",
        "isFund": "false", "isEtf": "false",
    })
    assert sent[1][1]["page"] == 1


# ── FMPClient.get_etf_holdings_strict ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_strict_holdings_returns_every_row_no_truncation(monkeypatch):
    client = FMPClient()
    payload = [{"symbol": "BOTZ", "asset": f"T{i}", "weightPercentage": 0.1} for i in range(250)]
    req = AsyncMock(return_value=payload)
    monkeypatch.setattr(client, "_make_request", req)
    out = await client.get_etf_holdings_strict("botz")
    assert out == payload and len(out) == 250
    req.assert_awaited_once_with("etf/holdings", params={"symbol": "BOTZ"})
    # Contrast: the display helper truncates to 20 — exactly why the strict one exists.
    assert len(await client.get_etf_holders("botz")) == 20


@pytest.mark.asyncio
async def test_strict_holdings_returns_an_empty_list_as_is(monkeypatch):
    client = FMPClient()
    monkeypatch.setattr(client, "_make_request", AsyncMock(return_value=[]))
    assert await client.get_etf_holdings_strict("ARKQ") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    None, {"Error Message": "Limit Reach"}, {}, "rate limited", 0, 3.5,
])
async def test_strict_holdings_non_list_body_raises_unavailable(monkeypatch, body):
    client = FMPClient()
    monkeypatch.setattr(client, "_make_request", AsyncMock(return_value=body))
    with pytest.raises(FMPUnavailableException) as ei:
        await client.get_etf_holdings_strict("botz")
    assert "BOTZ" in str(ei.value)
    assert type(body).__name__ in str(ei.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    FMPRateLimitException("429", retry_after="60"),
    FMPUnavailableException("503"),
    FMPNotEntitledException("402"),
    _http_error(404),
    _http_error(403),
])
async def test_strict_holdings_propagates_typed_errors(monkeypatch, exc):
    """`get_etf_holders` swallows these to [] — the strict variant must not."""
    client = FMPClient()
    monkeypatch.setattr(client, "_make_request", AsyncMock(side_effect=exc))
    with pytest.raises(type(exc)) as ei:
        await client.get_etf_holdings_strict("BOTZ")
    assert ei.value is exc
    monkeypatch.setattr(client, "_make_request", AsyncMock(side_effect=exc))
    assert await client.get_etf_holders("BOTZ") == []


@pytest.mark.asyncio
async def test_strict_holdings_funnels_through_the_counted_request_path(monkeypatch):
    """Goes through `_make_request` (licence pre-flight + `request_failures`), not around it."""
    client = FMPClient()
    before = client.request_failures
    monkeypatch.setattr(client, "_make_request_impl",
                        AsyncMock(side_effect=FMPRateLimitException("429")))
    with pytest.raises(FMPRateLimitException):
        await client.get_etf_holdings_strict("BOTZ")
    assert client.request_failures == before + 1

    ok = AsyncMock(return_value=[{"asset": "NVDA"}])
    monkeypatch.setattr(client, "_make_request_impl", ok)
    assert await client.get_etf_holdings_strict("BOTZ") == [{"asset": "NVDA"}]
    ok.assert_awaited_once()          # not refused pre-flight: etf/holdings is licensed


# ── load_etf_holdings ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_etf_holdings_collects_weights_per_etf():
    fmp = FakeFMP(holdings={
        "BOTZ": [{"symbol": "BOTZ", "asset": "NVDA", "weightPercentage": 9.5},
                 {"symbol": "BOTZ", "asset": "isrg", "weightPercentage": 8.0}],
        "ROBO": [{"symbol": "ROBO", "asset": " abb ", "weightPercentage": "1.25"}],
    })
    counter = CallCounter()
    out, failed = await load_etf_holdings(fmp, ["BOTZ", "ROBO"], counter)
    assert out == {"BOTZ": {"NVDA": 9.5, "ISRG": 8.0}, "ROBO": {"ABB": 1.25}}
    assert failed == []
    assert counter.calls == 2


@pytest.mark.asyncio
async def test_etf_holdings_prefers_asset_and_skips_the_etf_own_symbol():
    """Stable `etf/holdings` puts the FUND in `symbol` and the holding in `asset`; a cash /
    unnamed line has an empty `asset`, which falls back to the fund's own symbol."""
    fmp = FakeFMP(holdings={"BOTZ": [
        {"symbol": "BOTZ", "asset": "", "weightPercentage": 1.1},      # cash line
        {"symbol": "BOTZ", "asset": None, "weightPercentage": 0.4},
        {"symbol": "botz", "weightPercentage": 0.2},                    # own symbol, other case
        {"symbol": "BOTZ", "asset": " botz ", "weightPercentage": 0.2},
        {"symbol": "BOTZ", "asset": "FANUY", "weightPercentage": 4.0},
        {"symbol": "TSLA", "weightPercentage": 2.0},                    # legacy shape
    ]})
    out, failed = await load_etf_holdings(fmp, ["BOTZ"], CallCounter())
    assert out == {"BOTZ": {"FANUY": 4.0, "TSLA": 2.0}}
    assert failed == []


@pytest.mark.asyncio
async def test_etf_holdings_skips_malformed_rows():
    fmp = FakeFMP(holdings={"ARKQ": [
        None, "TSLA", 7, ["TSLA"], {}, {"asset": 123}, {"asset": ["X"]}, {"asset": "   "},
        {"asset": "TSLA", "weightPercentage": 10.0},
    ]})
    out, failed = await load_etf_holdings(fmp, ["ARKQ"], CallCounter())
    assert out == {"ARKQ": {"TSLA": 10.0}}
    assert failed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("weight, expected", [
    (None, 0.0), (float("nan"), 0.0), (float("inf"), 0.0), (float("-inf"), 0.0),
    ("n/a", 0.0), ("", 0.0), (True, 0.0), ({"v": 1}, 0.0),
    (-3.2, 0.0),                    # a short line cannot give a negative theme weight
    (0, 0.0), ("4.5", 4.5), (12, 12.0),
])
async def test_etf_holding_weight_degrades_to_zero_never_nan(weight, expected):
    fmp = FakeFMP(holdings={"BOTZ": [{"asset": "NVDA", "weightPercentage": weight}]})
    out, failed = await load_etf_holdings(fmp, ["BOTZ"], CallCounter())
    assert failed == []
    w = out["BOTZ"]["NVDA"]
    assert w == expected and math.isfinite(w) and isinstance(w, float)


@pytest.mark.asyncio
async def test_etf_holding_row_with_no_weight_key_is_kept_at_zero():
    fmp = FakeFMP(holdings={"BOTZ": [{"asset": "NVDA"}]})
    out, _ = await load_etf_holdings(fmp, ["BOTZ"], CallCounter())
    assert out == {"BOTZ": {"NVDA": 0.0}}


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    FMPUnavailableException("503"), FMPRateLimitException("429"),
    FMPNotEntitledException("402"), _http_error(404), RuntimeError("boom"),
])
async def test_etf_holdings_error_is_returned_as_failed_not_raised(exc, caplog):
    fmp = FakeFMP(holdings={"BAD": exc, "OK": [{"asset": "NVDA", "weightPercentage": 1}]})
    counter = CallCounter()
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        out, failed = await load_etf_holdings(fmp, ["BAD", "OK"], counter)
    assert out == {"OK": {"NVDA": 1.0}}
    assert failed == ["BAD"]
    assert counter.calls == 2                 # the failed call still counts
    assert any("BAD" in r.getMessage() and type(exc).__name__ in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [
    [],
    [{"symbol": "BOTZ", "asset": ""}],                    # only the fund's own line
    [None, "x", {"weightPercentage": 5}],                 # nothing usable
])
async def test_etf_with_no_usable_holdings_counts_as_failed(rows, caplog):
    fmp = FakeFMP(holdings={"BOTZ": rows})
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        out, failed = await load_etf_holdings(fmp, ["BOTZ"], CallCounter())
    assert out == {}
    assert failed == ["BOTZ"]
    assert any("no usable holdings" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_etf_names_are_deduped_case_insensitively_and_failed_is_sorted():
    fmp = FakeFMP(holdings={
        "ZZZ": FMPUnavailableException("x"), "AAA": [], "MMM": RuntimeError("y"),
        "BOTZ": [{"asset": "NVDA", "weightPercentage": 1.0}],
    })
    counter = CallCounter()
    out, failed = await load_etf_holdings(
        fmp, ["botz", "BOTZ", "Botz", "zzz", "ZZZ", "mmm", "AAA", "aaa"], counter)
    assert sorted(fmp.holdings_calls) == ["AAA", "BOTZ", "MMM", "ZZZ"]
    assert counter.calls == 4
    assert out == {"BOTZ": {"NVDA": 1.0}}
    assert failed == ["AAA", "MMM", "ZZZ"]


@pytest.mark.asyncio
async def test_etf_holdings_empty_request_makes_no_calls():
    fmp = FakeFMP()
    counter = CallCounter()
    assert await load_etf_holdings(fmp, [], counter) == ({}, [])
    assert counter.calls == 0 and fmp.holdings_calls == []


@pytest.mark.asyncio
async def test_etf_holdings_duplicate_holding_rows_collapse_to_one_ticker():
    fmp = FakeFMP(holdings={"BOTZ": [{"asset": "NVDA", "weightPercentage": 2.0},
                                     {"asset": "nvda", "weightPercentage": 3.0}]})
    out, _ = await load_etf_holdings(fmp, ["BOTZ"], CallCounter())
    assert list(out["BOTZ"]) == ["NVDA"]


@pytest.mark.asyncio
async def test_etf_holdings_through_the_real_strict_client(monkeypatch):
    """End to end over the real FMPClient: a non-list body and a 404 both land in `failed`."""
    client = FMPClient()
    bodies = {"BOTZ": [{"symbol": "BOTZ", "asset": "NVDA", "weightPercentage": 8.0}],
              "ROBO": {"Error Message": "x"}, "ARKQ": _http_error(404)}

    async def fake_request(endpoint, params=None):
        assert endpoint == "etf/holdings"
        return _resolve(bodies[params["symbol"]])

    monkeypatch.setattr(client, "_make_request", fake_request)
    out, failed = await load_etf_holdings(client, ["botz", "robo", "arkq"], CallCounter())
    assert out == {"BOTZ": {"NVDA": 8.0}}
    assert failed == ["ARKQ", "ROBO"]


# ── load_profiles ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_profiles_present_absent_and_uppercased():
    fmp = FakeFMP(profiles={
        "AAPL": {"symbol": "AAPL", "marketCap": 3e12},
        "EMPTY": {},
        "LIST": [{"symbol": "LIST"}],
        "NONE": None,
    })
    counter = CallCounter()
    out = await load_profiles(fmp, ["aapl", "AAPL", "empty", "LIST", "none"], counter)
    assert out == {"AAPL": {"symbol": "AAPL", "marketCap": 3e12}}
    assert sorted(fmp.profile_calls) == ["AAPL", "EMPTY", "LIST", "NONE"]
    assert counter.calls == 4


@pytest.mark.asyncio
async def test_profiles_empty_request():
    fmp = FakeFMP()
    counter = CallCounter()
    assert await load_profiles(fmp, [], counter) == {}
    assert counter.calls == 0


@pytest.mark.asyncio
async def test_profiles_failure_at_exactly_twenty_percent_warns_not_raises(caplog):
    fmp = FakeFMP(profiles={"E": FMPUnavailableException("503")}, default={"ok": 1})
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        out = await load_profiles(fmp, ["A", "B", "C", "D", "E"], CallCounter())
    assert set(out) == {"A", "B", "C", "D"}           # failed one is absent (unknown)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("profile" in m and "1/5" in m and "E:FMPUnavailableException" in m for m in msgs)


@pytest.mark.asyncio
async def test_profiles_failure_above_twenty_percent_raises():
    fmp = FakeFMP(profiles={"D": RuntimeError("x"), "E": FMPRateLimitException("429")},
                  default={"ok": 1})
    counter = CallCounter()
    with pytest.raises(ThemeSourceError) as ei:
        await load_profiles(fmp, ["A", "B", "C", "D", "E"], counter)
    assert ei.value.source == "profile"
    assert "2/5" in ei.value.detail
    assert "D:RuntimeError" in ei.value.detail and "E:FMPRateLimitException" in ei.value.detail
    assert counter.calls == 5


@pytest.mark.asyncio
async def test_profiles_empty_profile_is_not_a_failure():
    """Absent (unknown) — not counted, so a batch of empty profiles never aborts the run."""
    fmp = FakeFMP(default={})
    out = await load_profiles(fmp, [f"T{i}" for i in range(10)], CallCounter())
    assert out == {}


@pytest.mark.asyncio
async def test_profiles_failure_detail_is_truncated_after_eight():
    fmp = FakeFMP(default=FMPUnavailableException("down"))
    with pytest.raises(ThemeSourceError) as ei:
        await load_profiles(fmp, [f"T{i:02d}" for i in range(12)], CallCounter())
    assert "12/12" in ei.value.detail
    assert ei.value.detail.endswith(", ...)")


# ── load_segments ─────────────────────────────────────────────────────────────────────

_SEG = [{"symbol": "NVDA", "fiscalYear": 2025, "period": "FY", "reportedCurrency": None,
         "date": "2025-01-26", "data": {"Compute & Networking": 116.0, "Graphics": 14.0}}]


@pytest.mark.asyncio
async def test_segments_success_uses_annual_flat_and_latest_record():
    fmp = FakeFMP(segments={"NVDA": _SEG})
    counter = CallCounter()
    out = await load_segments(fmp, ["nvda"], counter)
    assert out == {"NVDA": {"Compute & Networking": 116.0, "Graphics": 14.0}}
    assert fmp.segment_calls == [("NVDA", "annual", "flat")]
    assert counter.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [FMPNotEntitledException("402"), _http_error(403),
                                 _http_error(404)])
async def test_segments_not_licensed_or_missing_is_unknown_and_not_a_failure(exc):
    """Every symbol refused → all None, and NO abort: those are per-ticker gaps."""
    fmp = FakeFMP(default=exc)
    out = await load_segments(fmp, ["A", "B", "C", "D", "E"], CallCounter())
    assert out == {s: None for s in "ABCDE"}


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [FMPUnavailableException("503"), FMPRateLimitException("429"),
                                 _http_error(500), _http_error(400), RuntimeError("x")])
async def test_segments_transient_error_is_none_but_counted(exc, caplog):
    fmp = FakeFMP(segments={"E": exc}, default=_SEG)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        out = await load_segments(fmp, ["A", "B", "C", "D", "E"], CallCounter())
    assert out["E"] is None
    assert all(out[s] is not None for s in "ABCD")
    assert any("revenue-product-segmentation" in r.getMessage() and "1/5" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_segments_transient_failures_above_twenty_percent_raise():
    fmp = FakeFMP(segments={"D": _http_error(500), "E": FMPUnavailableException("x")},
                  default=_SEG)
    with pytest.raises(ThemeSourceError) as ei:
        await load_segments(fmp, ["A", "B", "C", "D", "E"], CallCounter())
    assert ei.value.source == "revenue-product-segmentation"
    assert "2/5" in ei.value.detail


@pytest.mark.asyncio
async def test_segments_refusals_do_not_dilute_into_an_abort():
    """4 unlicensed + 1 transient of 5 = 1 counted failure (20%) → no raise."""
    fmp = FakeFMP(segments={"A": FMPNotEntitledException("x"), "B": _http_error(403),
                            "C": _http_error(404), "D": FMPNotEntitledException("y"),
                            "E": FMPUnavailableException("z")})
    out = await load_segments(fmp, ["A", "B", "C", "D", "E"], CallCounter())
    assert out == {s: None for s in "ABCDE"}


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [[], None, {}, "x", [None, 1]])
async def test_segments_empty_or_garbage_answer_is_unknown(raw):
    fmp = FakeFMP(segments={"X": raw})
    assert await load_segments(fmp, ["X"], CallCounter()) == {"X": None}


# ── latest_segments ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [None, {}, {"data": {"A": 1}}, "x", 5, [], [None, "x", 3]])
def test_latest_segments_non_list_or_empty_is_none(raw):
    assert latest_segments(raw) is None


def test_latest_segments_picks_latest_by_date_regardless_of_order():
    raw = [
        {"date": "2024-01-28", "fiscalYear": 2024, "data": {"Old": 1.0}},
        {"date": "2025-01-26", "fiscalYear": 2025, "data": {"New": 2.0}},
        {"date": "2023-01-29", "fiscalYear": 2023, "data": {"Oldest": 3.0}},
    ]
    assert latest_segments(raw) == {"New": 2.0}
    assert latest_segments(list(reversed(raw))) == {"New": 2.0}


def test_latest_segments_falls_back_to_fiscal_year_when_dates_are_missing():
    raw = [{"fiscalYear": 2025, "data": {"New": 2.0}},
           {"fiscalYear": "2023", "data": {"Old": 1.0}}]
    assert latest_segments(raw) == {"New": 2.0}


def test_latest_segments_strips_metadata_keys_in_the_flat_shape():
    raw = [{"symbol": "AAPL", "fiscalYear": 2024, "calendarYear": "2024", "period": "FY",
            "reportedCurrency": "USD", "date": "2024-09-28", "acceptedDate": "2024-11-01",
            "filingDate": "2024-11-01", "iPhone": 201.0, "Mac": "29.9"}]
    assert latest_segments(raw) == {"iPhone": 201.0, "Mac": 29.9}


def test_latest_segments_strips_metadata_keys_inside_data():
    raw = [{"date": "2024-09-28", "data": {"symbol": "AAPL", "date": "2024-09-28",
                                           "fiscalYear": 2024, "Services": 96.0}}]
    assert latest_segments(raw) == {"Services": 96.0}


@pytest.mark.parametrize("bad", [None, "n/a", "", float("nan"), float("inf"), True,
                                 {"x": 1}, [1]])
def test_latest_segments_drops_non_numeric_values(bad):
    raw = [{"date": "2025-01-01", "data": {"Good": 5, "Bad": bad}}]
    out = latest_segments(raw)
    assert out == {"Good": 5.0}
    assert all(isinstance(v, float) and math.isfinite(v) for v in out.values())


def test_latest_segments_all_values_unusable_is_none():
    assert latest_segments([{"date": "2025-01-01", "data": {"A": None, "B": "x"}}]) is None
    assert latest_segments([{"date": "2025-01-01", "data": {}}]) is None
    assert latest_segments([{"symbol": "X", "date": "2025-01-01", "fiscalYear": 2025}]) is None


def test_latest_segments_data_key_that_is_not_a_dict_uses_the_flat_shape():
    raw = [{"date": "2025-01-01", "data": None, "Segment A": 7}]
    assert latest_segments(raw) == {"Segment A": 7.0}


def test_latest_segments_skips_non_dict_records_and_keeps_zero():
    raw = [None, "x", {"date": "2025-01-01", "data": {"A": 0, "B": 1.5}}]
    assert latest_segments(raw) == {"A": 0.0, "B": 1.5}


def test_latest_segments_empty_latest_is_unknown_even_if_older_had_data():
    """The latest fiscal record governs; an older year is never silently substituted."""
    raw = [{"date": "2023-01-01", "data": {"Old": 9.0}},
           {"date": "2025-01-01", "data": {}}]
    assert latest_segments(raw) is None


def test_latest_segments_keys_are_strings():
    out = latest_segments([{"date": "2025-01-01", "data": {1: 2.0, "B": 3}}])
    assert out == {"1": 2.0, "B": 3.0}


# ── _approx_sessions ──────────────────────────────────────────────────────────────────

def test_approx_sessions_known_values():
    mon = date(2026, 3, 2)
    assert _approx_sessions(mon, mon) == 1
    assert _approx_sessions(mon, mon + timedelta(days=4)) == 5          # Mon..Fri
    assert _approx_sessions(mon, mon + timedelta(days=6)) == 5          # Mon..Sun
    assert _approx_sessions(mon, mon + timedelta(days=13)) == 10
    sat = date(2026, 3, 7)
    assert _approx_sessions(sat, sat) == 0
    assert _approx_sessions(sat, sat + timedelta(days=1)) == 0          # Sat..Sun
    assert _approx_sessions(sat, sat + timedelta(days=2)) == 1          # Sat..Mon


def test_approx_sessions_end_before_start_is_zero():
    assert _approx_sessions(date(2026, 3, 10), date(2026, 3, 9)) == 0
    assert _approx_sessions(date(2026, 3, 10), date(2020, 1, 1)) == 0


def test_approx_sessions_matches_a_naive_weekday_count():
    rng = random.Random(1234)
    base = date(2024, 1, 1)
    for _ in range(500):
        s = base + timedelta(days=rng.randint(0, 900))
        e = s + timedelta(days=rng.randint(-5, 400))
        assert _approx_sessions(s, e) == _naive_sessions(s, e), (s, e)


# ── _bars ─────────────────────────────────────────────────────────────────────────────

def test_bars_accepts_list_and_historical_wrapper_and_sorts_ascending():
    rows = [{"date": "2026-03-04", "close": 3, "volume": 30},
            {"date": "2026-03-02", "close": 1, "volume": 10},
            {"date": "2026-03-03", "close": 2, "volume": 20}]
    expected = [{"date": "2026-03-02", "close": 1.0, "volume": 10.0},
                {"date": "2026-03-03", "close": 2.0, "volume": 20.0},
                {"date": "2026-03-04", "close": 3.0, "volume": 30.0}]
    assert _bars(rows) == expected
    assert _bars({"symbol": "X", "historical": rows}) == expected


@pytest.mark.parametrize("raw", [None, "x", 5, {}, {"historical": None},
                                 {"historical": "x"}, {"data": []}, []])
def test_bars_unusable_payload_is_empty(raw):
    assert _bars(raw) == []


def test_bars_dedupes_dates():
    rows = [{"date": "2026-03-02", "close": 1, "volume": 1},
            {"date": "2026-03-02 16:00:00", "close": 2, "volume": 2},
            {"date": "2026-03-03", "close": 3, "volume": 3}]
    out = _bars(rows)
    assert [b["date"] for b in out] == ["2026-03-02", "2026-03-03"]


@pytest.mark.parametrize("close", [0, -1.0, float("nan"), float("inf"), "abc", True,
                                   [1], {"v": 1}])
def test_bars_drops_non_positive_and_non_finite_closes(close):
    rows = [{"date": "2026-03-02", "close": close, "volume": 1},
            {"date": "2026-03-03", "close": 5.0, "volume": 1}]
    assert [b["date"] for b in _bars(rows)] == ["2026-03-03"]


def test_bars_falls_back_to_adj_close_only_when_close_is_absent():
    rows = [{"date": "2026-03-02", "adjClose": 4.0, "volume": 1},
            {"date": "2026-03-03", "close": None, "adjClose": 5.0, "volume": 1},
            {"date": "2026-03-04", "close": 0, "adjClose": 6.0, "volume": 1}]
    out = _bars(rows)
    assert [(b["date"], b["close"]) for b in out] == [("2026-03-02", 4.0), ("2026-03-03", 5.0)]


def test_bars_drops_malformed_rows():
    rows = [None, "2026-03-02", 7, ["2026-03-02", 1],
            {"close": 1.0},                          # no date
            {"date": None, "close": 1.0},
            {"date": "", "close": 1.0},
            {"date": "2026-3-2", "close": 1.0},      # short
            {"date": 20260302, "close": 1.0},        # int → 8 chars
            {"date": "2026-03-05", "close": 2.0, "volume": 3}]
    assert _bars(rows) == [{"date": "2026-03-05", "close": 2.0, "volume": 3.0}]


def test_bars_truncates_a_timestamp_to_the_date():
    out = _bars([{"date": "2026-03-02T00:00:00.000Z", "close": 1.0, "volume": 2}])
    assert out == [{"date": "2026-03-02", "close": 1.0, "volume": 2.0}]


def test_bars_numeric_strings_are_parsed():
    out = _bars([{"date": "2026-03-02", "close": "12.5", "volume": "100"}])
    assert out == [{"date": "2026-03-02", "close": 12.5, "volume": 100.0}]


def test_regression_bars_keeps_a_ten_char_date_that_is_not_a_date():
    """REGRESSION (fixed 2026-09-23). Was: `_bars` only checks `len(d) == 10`, so '0000-00-00' / '2026-13-45' / '2026/03/05'
    survive as bars. The correct degraded behaviour is to drop the malformed row."""
    rows = [{"date": "0000-00-00", "close": 1.0, "volume": 1},
            {"date": "2026-13-45", "close": 1.0, "volume": 1},
            {"date": "2026/03/05", "close": 1.0, "volume": 1},
            {"date": "2026-03-02", "close": 2.0, "volume": 1}]
    assert [b["date"] for b in _bars(rows)] == ["2026-03-02"]


# ── price_stats math ──────────────────────────────────────────────────────────────────

def test_price_stats_empty_is_none():
    assert price_stats([], window_start=W) is None


def test_ret_3m_needs_63_sessions_back():
    bars = _mk_bars(W, SESSIONS_3M + 1)                       # 64 bars, closes 1..64
    st = price_stats(bars, window_start=W)
    assert st.ret_3m == pytest.approx(64.0 / 1.0 - 1.0)
    assert st.ret_6m is None
    st = price_stats(_mk_bars(W, SESSIONS_3M), window_start=W)  # 63 bars: not enough
    assert st.ret_3m is None and st.ret_6m is None


def test_ret_6m_needs_126_sessions_back():
    bars = _mk_bars(W, SESSIONS_6M + 1)                       # 127 bars
    st = price_stats(bars, window_start=W)
    assert st.ret_6m == pytest.approx(127.0 / 1.0 - 1.0)
    assert st.ret_3m == pytest.approx(127.0 / 64.0 - 1.0)
    st = price_stats(_mk_bars(W, SESSIONS_6M), window_start=W)  # 126 bars
    assert st.ret_6m is None
    assert st.ret_3m == pytest.approx(126.0 / 63.0 - 1.0)


def test_returns_are_negative_on_a_decline():
    closes = [100.0] * 64 + [50.0] * 63 + [25.0]
    st = price_stats(_mk_bars(W, len(closes), closes=closes), window_start=W)
    assert st.ret_3m == pytest.approx(25.0 / 50.0 - 1.0)
    assert st.ret_6m == pytest.approx(25.0 / 100.0 - 1.0)


def test_adtv_is_mean_close_times_volume_over_last_126_bars():
    n = 140
    ds = _weekdays(W, n)
    bars = [{"date": d.isoformat(), "close": 2.0 + (i % 3), "volume": float(1_000 * (i + 1))}
            for i, d in enumerate(ds)]
    st = price_stats(bars, window_start=W)
    recent = bars[-SESSIONS_6M:]
    expected = sum(b["close"] * b["volume"] for b in recent) / len(recent)
    assert st.adtv_6m == pytest.approx(expected)


def test_adtv_ignores_bars_older_than_six_months():
    closes = [10.0] * 140
    bars = _mk_bars(W, 140, closes=closes, volume=1.0)
    for b in bars[:14]:
        b["volume"] = 1e12                                   # an ancient spike
    st = price_stats(bars, window_start=W)
    assert st.adtv_6m == pytest.approx(10.0)


def test_adtv_with_fewer_than_126_bars_averages_what_exists():
    st = price_stats(_mk_bars(W, 10, closes=[5.0] * 10, volume=20.0), window_start=W)
    assert st.adtv_6m == pytest.approx(100.0)


def test_adtv_overflow_is_unknown_not_inf():
    bars = _mk_bars(W, 70, closes=[1e200] * 70, volume=1e200)
    st = price_stats(bars, window_start=W)
    assert st.adtv_6m is None
    assert st.ret_3m == pytest.approx(0.0)


def test_coverage_full_history_is_bars_over_126_capped_at_one():
    st = price_stats(_mk_bars(W, 138), window_start=W)
    assert st.session_coverage == 1.0
    assert st.sessions_listed is None
    st = price_stats(_mk_bars(W, 100), window_start=W)
    assert st.session_coverage == pytest.approx(100 / 126)
    assert st.sessions_listed is None
    st = price_stats(_mk_bars(W, 1), window_start=W)
    assert st.session_coverage == pytest.approx(1 / 126)


def test_coverage_full_history_with_first_bar_a_few_days_in_is_not_young():
    """A holiday/weekend at the window start must not reclassify an old stock as young."""
    for offset in (0, 1, 3, 4, 10):
        start = W + timedelta(days=offset)
        st = price_stats(_mk_bars(start, 60), window_start=W)
        assert st.sessions_listed is None, offset
        assert st.session_coverage == pytest.approx(60 / 126), offset


def test_young_listing_boundary_is_more_than_ten_days():
    first = W + timedelta(days=11)
    st = price_stats(_mk_bars(first, 20), window_start=W)
    assert st.sessions_listed == 20
    assert st.session_coverage == 1.0                        # judged against its own life


def test_young_listing_with_gaps_is_judged_against_its_own_life():
    first = W + timedelta(days=60)
    days = _weekdays(first, 40)[::2]                          # every other session
    bars = [{"date": d.isoformat(), "close": 10.0, "volume": 1.0} for d in days]
    st = price_stats(bars, window_start=W)
    expected = _naive_sessions(days[0], days[-1])
    assert st.sessions_listed == 20
    assert st.session_coverage == pytest.approx(20 / expected)
    assert st.session_coverage < 0.9


def test_young_listing_expected_sessions_capped_at_126():
    first = W + timedelta(days=11)
    st = price_stats(_mk_bars(first, 130), window_start=W)
    assert st.sessions_listed == 130
    assert st.session_coverage == 1.0


def test_young_listing_single_weekend_bar_has_unknown_coverage():
    sat = W + timedelta(days=12)                              # a Saturday
    assert sat.weekday() == 5
    st = price_stats([{"date": sat.isoformat(), "close": 3.0, "volume": 2.0}], window_start=W)
    assert st.session_coverage is None                        # never a ZeroDivisionError
    assert st.sessions_listed == 1
    assert st.adtv_6m == pytest.approx(6.0)
    assert st.ret_3m is None and st.ret_6m is None


def test_price_stats_coverage_never_exceeds_one():
    for n in (1, 50, 126, 127, 200):
        for off in (0, 11, 30):
            st = price_stats(_mk_bars(W + timedelta(days=off), n), window_start=W)
            assert st.session_coverage is None or 0 < st.session_coverage <= 1.0


def test_regression_missing_volume_reads_as_zero_liquidity():
    """REGRESSION (fixed 2026-09-23). Was: Module contract: a per-ticker gap is UNKNOWN (None), never zero. A history whose rows
    carry no usable `volume` yields `adtv_6m == 0.0` — a confident zero that trips the
    member liquidity floor (scoring.floor_failure → BELOW_FLOORS) instead of the neutral
    unknown path (`adtv is not None and adtv < incumbent_min_adtv`)."""
    rows = [{"date": d.isoformat(), "close": 50.0, "volume": v}
            for d, v in zip(_weekdays(W, 130), [None, float("nan"), "n/a"] * 44)]
    rows += [{"date": d.isoformat(), "close": 50.0}
             for d in _weekdays(W + timedelta(days=300), 5)]
    st = price_stats(_bars(rows), window_start=W)
    assert st is not None
    assert st.adtv_6m is None


# ── load_price_stats ──────────────────────────────────────────────────────────────────

AS_OF = date(2026, 9, 1)
START = AS_OF - timedelta(days=sources.HISTORY_LOOKBACK_DAYS)


@pytest.mark.asyncio
async def test_price_stats_request_window_and_uppercasing():
    fmp = FakeFMP(default=_mk_bars(START, 138))
    counter = CallCounter()
    out = await load_price_stats(fmp, ["nvda", "NVDA", "amd"], as_of=AS_OF, counter=counter)
    assert set(out) == {"AMD", "NVDA"}
    assert sorted(fmp.price_calls) == [("AMD", START.isoformat(), AS_OF.isoformat()),
                                       ("NVDA", START.isoformat(), AS_OF.isoformat())]
    assert counter.calls == 2
    assert isinstance(out["NVDA"], PriceStats)
    assert out["NVDA"].session_coverage == 1.0 and out["NVDA"].sessions_listed is None


@pytest.mark.asyncio
async def test_price_stats_newest_first_payload_is_sorted_before_returns():
    """FMP's `historical-price-eod/full` answers newest-first."""
    bars = _mk_bars(START, 138)                                # closes rise 1..138
    fmp = FakeFMP(prices={"UP": list(reversed(bars)),
                          "WRAP": {"symbol": "WRAP", "historical": list(reversed(bars))}})
    out = await load_price_stats(fmp, ["UP", "WRAP"], as_of=AS_OF, counter=CallCounter())
    for sym in ("UP", "WRAP"):
        assert out[sym].ret_3m == pytest.approx(138 / 75 - 1)
        assert out[sym].ret_6m == pytest.approx(138 / 12 - 1)
        assert out[sym].ret_3m > 0


@pytest.mark.asyncio
async def test_price_stats_window_start_is_as_of_minus_lookback():
    """A listing whose first bar is > 10 days after (as_of - 200d) is young."""
    fmp = FakeFMP(prices={
        "OLD": _mk_bars(START + timedelta(days=3), 60),
        "NEW": _mk_bars(START + timedelta(days=40), 60),
    })
    out = await load_price_stats(fmp, ["OLD", "NEW"], as_of=AS_OF, counter=CallCounter())
    assert out["OLD"].sessions_listed is None
    assert out["NEW"].sessions_listed == 60
    assert out["NEW"].session_coverage == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("sym", ["^GSPC", "^ixic", "BTCUSD", "GCUSD", "EURUSD"])
async def test_price_stats_blocked_symbol_is_none_without_an_fmp_call(sym):
    assert is_blocked_symbol(sym)
    fmp = FakeFMP(default=AssertionError("must not be called"))
    counter = CallCounter()
    out = await load_price_stats(fmp, [sym], as_of=AS_OF, counter=counter)
    assert out == {sym.upper(): None}
    assert fmp.price_calls == []
    assert counter.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [FMPNotEntitledException("x"), _http_error(402),
                                 _http_error(403), _http_error(404)])
async def test_price_stats_unlicensed_or_missing_is_none_and_not_a_failure(exc):
    fmp = FakeFMP(default=exc)
    out = await load_price_stats(fmp, list("ABCDE"), as_of=AS_OF, counter=CallCounter())
    assert out == {s: None for s in "ABCDE"}


@pytest.mark.asyncio
async def test_price_stats_transient_failure_at_twenty_percent_warns(caplog):
    fmp = FakeFMP(prices={"E": FMPUnavailableException("503")}, default=_mk_bars(START, 138))
    counter = CallCounter()
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        out = await load_price_stats(fmp, list("ABCDE"), as_of=AS_OF, counter=counter)
    assert out["E"] is None
    assert all(isinstance(out[s], PriceStats) for s in "ABCD")
    assert counter.calls == 5
    assert any("historical-price-eod" in r.getMessage() and "1/5" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [FMPUnavailableException("503"), FMPRateLimitException("429"),
                                 _http_error(500), RuntimeError("x")])
async def test_price_stats_transient_failures_above_twenty_percent_raise(exc):
    fmp = FakeFMP(prices={"D": exc, "E": exc}, default=_mk_bars(START, 138))
    with pytest.raises(ThemeSourceError) as ei:
        await load_price_stats(fmp, list("ABCDE"), as_of=AS_OF, counter=CallCounter())
    assert ei.value.source == "historical-price-eod"
    assert "2/5" in ei.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [[], None, {}, {"historical": []}, "x",
                                 [{"date": "2026-01-01", "close": 0}]])
async def test_price_stats_empty_history_is_unknown(raw):
    fmp = FakeFMP(prices={"X": raw})
    assert await load_price_stats(fmp, ["X"], as_of=AS_OF, counter=CallCounter()) == {"X": None}


@pytest.mark.asyncio
async def test_price_stats_empty_request():
    fmp = FakeFMP()
    counter = CallCounter()
    assert await load_price_stats(fmp, [], as_of=AS_OF, counter=counter) == {}
    assert counter.calls == 0


@pytest.mark.asyncio
async def test_price_stats_through_the_real_client_params(monkeypatch):
    client = FMPClient()
    sent: List[tuple] = []

    async def fake_request(endpoint, params=None):
        sent.append((endpoint, dict(params or {})))
        return _mk_bars(START, 138)

    monkeypatch.setattr(client, "_make_request", fake_request)
    out = await load_price_stats(client, ["nvda"], as_of=AS_OF, counter=CallCounter())
    assert out["NVDA"].ret_6m is not None
    assert sent == [("historical-price-eod/full",
                     {"symbol": "NVDA", "from": START.isoformat(), "to": AS_OF.isoformat()})]


@pytest.mark.asyncio
async def test_regression_one_malformed_price_row_crashes_the_whole_price_load():
    """REGRESSION (fixed 2026-09-23). Was: A per-ticker gap must be UNKNOWN, never a crash. `price_stats` runs OUTSIDE the
    try in `load_price_stats.one()`, and calls `date.fromisoformat` on bars[0] (and on
    bars[-1] for a young listing). One row dated '0000-00-00' — which `_bars` keeps and
    sorts first — raises an untyped ValueError out of `asyncio.gather`, failing every
    theme's rotation instead of degrading that one ticker."""
    good = _mk_bars(START, 138)
    bad = good + [{"date": "0000-00-00", "close": 10.0, "volume": 1.0}]
    fmp = FakeFMP(prices={"BAD": bad}, default=good)
    out = await load_price_stats(fmp, ["AAA", "BAD", "CCC"], as_of=AS_OF,
                                 counter=CallCounter())
    assert isinstance(out["AAA"], PriceStats) and isinstance(out["CCC"], PriceStats)
    # Correct degraded behaviour: the malformed row is dropped (full stats) or the ticker
    # is unknown — either way, no exception.
    assert out["BAD"] is None or isinstance(out["BAD"], PriceStats)


# ── CallCounter across a full load ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_counter_counts_every_fmp_call_across_all_loaders():
    fmp = FakeFMP(
        screener_pages=[_screener_rows(SCREENER_PAGE_LIMIT), _screener_rows(10, 10**6)],
        holdings={"BOTZ": [{"asset": "NVDA", "weightPercentage": 1}],
                  "ROBO": FMPUnavailableException("x")},
        profiles={"A": FMPUnavailableException("x")}, segments={}, prices={},
        default=None,
    )
    fmp.prices = {"A": _mk_bars(START, 138), "B": _mk_bars(START, 138),
                  "C": FMPNotEntitledException("x"), "D": _mk_bars(START, 138),
                  "E": _mk_bars(START, 138)}
    counter = CallCounter()
    await load_universe(fmp, counter)                                    # 2
    await load_etf_holdings(fmp, ["BOTZ", "ROBO"], counter)              # 2 (1 failed)
    await load_profiles(fmp, list("ABCDE"), counter)                     # 5 (1 failed)
    await load_segments(fmp, list("ABCDE"), counter)                     # 5
    await load_price_stats(fmp, list("ABCDE") + ["^GSPC"], as_of=AS_OF,
                           counter=counter)                              # 5 (^GSPC skipped)
    assert counter.calls == 2 + 2 + 5 + 5 + 5
    total_fake_calls = (len(fmp.screener_calls) + len(fmp.holdings_calls)
                        + len(fmp.profile_calls) + len(fmp.segment_calls)
                        + len(fmp.price_calls))
    assert counter.calls == total_fake_calls


# ── 2026-09-23 fix pass ───────────────────────────────────────────────────────────────

def test_adtv_averages_only_the_days_whose_volume_is_known():
    bars = _mk_bars(W, 130, closes=[10.0] * 130, volume=2_000.0)
    for b in bars[::3]:                          # a third of the days carry no volume
        b["volume"] = None
    st = price_stats(_bars(bars), window_start=W)
    assert st.adtv_6m == pytest.approx(20_000.0)  # not dragged down by zeros


def test_adtv_is_unknown_when_most_days_lack_volume():
    bars = _mk_bars(W, 130, closes=[10.0] * 130, volume=2_000.0)
    for i, b in enumerate(bars):
        if i % 10:                               # only one day in ten has a volume
            b.pop("volume")
    assert price_stats(_bars(bars), window_start=W).adtv_6m is None


def test_negative_volume_is_unknown_not_a_number():
    bars = _mk_bars(W, 130, closes=[10.0] * 130, volume=-5.0)
    assert price_stats(_bars(bars), window_start=W).adtv_6m is None


@pytest.mark.asyncio
async def test_blocked_symbols_do_not_dilute_the_price_failure_rate():
    """1 of 4 REQUESTED histories failing is 25% (> 20%) and must abort, however many
    never-requested blocked symbols ride along in the list."""
    blocked = ["^GSPC", "^DJI", "^IXIC", "BTCUSD", "ETHUSD", "^RUT"]
    assert all(is_blocked_symbol(s) for s in blocked)
    fmp = FakeFMP(prices={"AAA": RuntimeError("502")}, default=_mk_bars(START, 138))
    with pytest.raises(ThemeSourceError):
        await load_price_stats(fmp, ["AAA", "BBB", "CCC", "DDD"] + blocked, as_of=AS_OF,
                               counter=CallCounter())


@pytest.mark.asyncio
async def test_an_unparseable_history_degrades_one_ticker_and_is_counted(monkeypatch, caplog):
    def boom(bars, *, window_start):
        raise ValueError("odd history")

    monkeypatch.setattr(sources, "price_stats", boom)
    fmp = FakeFMP(default=_mk_bars(START, 138))
    with pytest.raises(ThemeSourceError):            # every ticker failed → over threshold
        await load_price_stats(fmp, ["AAA", "BBB"], as_of=AS_OF, counter=CallCounter())
