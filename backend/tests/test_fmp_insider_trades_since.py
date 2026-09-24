"""`FMPClient.get_insider_trades_since` — the market-wide Form 4 pager behind CEO Buys.

Contract (home E2, 2026-09-23), mirroring `_fetch_congress_pages`:

    walk pages newest-first, stop on: empty page · short page · page-min filingDate < since
    page 0 raises                     → the typed exception propagates
    page 0 answers 403/404            → []  (the documented "not on this plan" exemption)
    MARKET-WIDE short/empty-later page still inside the window → FMPPartialPageException
    a later page fails / non-list     → FMPPartialPageException(.partial = rows so far)
    max_pages hit inside the window   → WARNING + FMPPartialPageException (window uncovered)
    a row repeated from an EARLIER page → dropped (page-shift artefact); repeats WITHIN a
                                          page are kept (separate fills, no line numbers)

The HTTP layer is faked at `_make_request_impl`, so the real `_make_request` wrapper — the
licence pre-flight and the `request_failures` counter — still runs. Category 1 (pure).
"""
import asyncio
import logging

import httpx
import pytest

from app.integrations.fmp import (
    FMPClient,
    FMPPartialPageException,
    FMPRateLimitException,
    FMPUnavailableException,
)


def _row(i: int, filed: str, **extra):
    base = {
        "symbol": f"S{i}",
        "filingDate": f"{filed} 16:05:00",
        "transactionDate": filed,
        "transactionType": "P-Purchase",
        "typeOfOwner": "officer: Chief Executive Officer",
        "securityName": "Common Stock",
        "securitiesTransacted": 1000 + i,
        "price": 10.0,
        "reportingCik": f"000{i}",
        "reportingName": f"DOE JOHN {i}",
    }
    base.update(extra)
    return base


def _page(start: int, n: int, filed: str):
    return [_row(start + i, filed) for i in range(n)]


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://financialmodelingprep.com/stable/insider-trading/search")
    return httpx.HTTPStatusError(f"{status}", request=req, response=httpx.Response(status, request=req))


def _client(page_behavior, calls=None) -> FMPClient:
    c = FMPClient()

    async def _impl(endpoint, params=None):
        if calls is not None:
            calls.append((endpoint, dict(params or {})))
        outcome = page_behavior((params or {}).get("page"))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    c._make_request_impl = _impl  # type: ignore[method-assign]
    return c


def _run(coro):
    return asyncio.run(coro)


# ── stop rules ──────────────────────────────────────────────────────────────────────

def test_stops_once_a_pages_oldest_filing_predates_since():
    pages = {
        0: _page(0, 4, "2026-09-20"),
        1: _page(10, 3, "2026-09-01") + [_row(99, "2026-08-20")],   # oldest < since
        2: _page(20, 4, "2026-08-01"),
    }
    calls = []
    rows = _run(_client(lambda p: pages.get(p, []), calls).get_insider_trades_since(
        "2026-08-24", page_size=4))
    assert [c[1]["page"] for c in calls] == [0, 1]
    assert len(rows) == 8           # the last page is returned whole; windowing is the service's
    assert any(r["symbol"] == "S99" for r in rows)


def test_walks_three_full_pages_then_stops_on_the_bound():
    pages = {0: _page(0, 5, "2026-09-22"), 1: _page(5, 5, "2026-09-10"),
             2: _page(10, 4, "2026-08-30") + [_row(50, "2026-08-10")]}
    calls = []
    rows = _run(_client(lambda p: pages.get(p, []), calls).get_insider_trades_since(
        "2026-08-24", page_size=5))
    assert [c[1]["page"] for c in calls] == [0, 1, 2]
    assert len(rows) == 15


def test_an_empty_page_ends_a_per_ticker_walk():
    pages = {0: _page(0, 3, "2026-09-22"), 1: []}
    calls = []
    rows = _run(_client(lambda p: pages.get(p, []), calls).get_insider_trades_since(
        "2026-08-24", symbol="GME", page_size=3))
    assert len(rows) == 3 and [c[1]["page"] for c in calls] == [0, 1]


def test_an_empty_later_page_inside_the_window_fails_closed_market_wide():
    """A FULL page still inside the window, then nothing: the market feed stopped short."""
    pages = {0: _page(0, 3, "2026-09-22"), 1: []}
    with pytest.raises(FMPPartialPageException) as ei:
        _run(_client(lambda p: pages.get(p, [])).get_insider_trades_since("2026-08-24", page_size=3))
    assert ei.value.pages_failed == 0 and len(ei.value.partial) == 3


def test_an_empty_first_page_is_returned_for_the_caller_to_judge():
    """Page 0 empty: the pager cannot tell "nothing filed" from an outage for an arbitrary
    window, so it returns [] — `_build_ceo` knows 30 market-wide days are never empty."""
    assert _run(_client(lambda p: []).get_insider_trades_since("2026-08-24")) == []


def test_a_short_market_wide_page_inside_the_window_fails_closed(caplog):
    """FMP silently lowering its per-page cap (it caps congress pages at 250) would otherwise
    publish ~3 days of filings as "30 days"."""
    caplog.set_level(logging.WARNING, logger="app.integrations.fmp")
    with pytest.raises(FMPPartialPageException) as ei:
        _run(_client(lambda p: _page(0, 2, "2026-09-22") if p == 0 else [])
             .get_insider_trades_since("2026-08-24", page_size=1000))
    assert ei.value.pages_failed == 0 and len(ei.value.partial) == 2
    assert any("lowered its per-page cap" in r.getMessage() for r in caplog.records)


def test_a_short_market_wide_page_that_reaches_the_window_is_the_honest_end():
    rows = _run(_client(lambda p: _page(0, 2, "2026-09-22") + [_row(9, "2026-08-01")] if p == 0 else [])
                .get_insider_trades_since("2026-08-24", page_size=1000))
    assert len(rows) == 3


def test_a_short_page_is_the_normal_end_for_one_ticker(caplog):
    caplog.set_level(logging.WARNING, logger="app.integrations.fmp")
    rows = _run(_client(lambda p: _page(0, 2, "2026-09-22") if p == 0 else [])
                .get_insider_trades_since("2026-08-24", symbol="GME", page_size=1000))
    assert len(rows) == 2
    assert not any("lowered its per-page cap" in r.getMessage() for r in caplog.records)


def test_unparseable_filing_dates_neither_stop_nor_extend_the_walk():
    """"N/A" sorts after every real date and "" before — both must be IGNORED, not compared."""
    bad = [_row(1, "2026-09-22", filingDate="N/A"), _row(2, "2026-09-22", filingDate=""),
           _row(3, "2026-09-22", filingDate=None)]
    pages = {0: bad + _page(10, 1, "2026-09-21"), 1: _page(20, 4, "2026-08-01")}
    calls = []
    rows = _run(_client(lambda p: pages.get(p, []), calls).get_insider_trades_since(
        "2026-08-24", page_size=4))
    assert [c[1]["page"] for c in calls] == [0, 1]
    assert len(rows) == 8


# ── parameters ───────────────────────────────────────────────────────────────────────

def test_params_market_wide_and_per_symbol():
    calls = []
    _run(_client(lambda p: [], calls).get_insider_trades_since(
        "2026-08-24", transaction_type="P-Purchase", page_size=1000))
    endpoint, params = calls[0]
    assert endpoint == "insider-trading/search"
    assert params["page"] == 0 and params["limit"] == 1000
    assert params["transactionType"] == "P-Purchase"
    assert "symbol" not in params, "market-wide means NO symbol parameter"

    calls.clear()
    _run(_client(lambda p: [], calls).get_insider_trades_since("2026-08-24", symbol="  gme "))
    assert calls[0][1]["symbol"] == "GME"
    assert "transactionType" not in calls[0][1]

    calls.clear()
    _run(_client(lambda p: [], calls).get_insider_trades_since("2026-08-24", symbol="   "))
    assert "symbol" not in calls[0][1], "a blank symbol must not narrow the feed to ''"


def test_a_malformed_since_date_is_a_programming_error():
    for bad in ("2026/08/24", "yesterday", "", None, "2026-13-01"):
        with pytest.raises(ValueError):
            _run(_client(lambda p: []).get_insider_trades_since(bad))  # type: ignore[arg-type]


# ── fail closed ──────────────────────────────────────────────────────────────────────

def test_a_lost_later_page_raises_partial_with_the_rows_that_arrived():
    boom = FMPUnavailableException("502 x3")
    pages = {0: _page(0, 4, "2026-09-22"), 1: boom}
    with pytest.raises(FMPPartialPageException) as ei:
        _run(_client(lambda p: pages.get(p, [])).get_insider_trades_since("2026-08-24", page_size=4))
    e = ei.value
    assert e.endpoint == "insider-trading/search"
    assert len(e.partial) == 4 and e.pages_failed == 1 and e.pages_total == 2
    assert e.__cause__ is boom


@pytest.mark.parametrize("exc", [
    FMPRateLimitException("429"), FMPUnavailableException("503"), _http_status_error(400),
])
def test_page_zero_failures_propagate_typed(exc):
    with pytest.raises(type(exc)):
        _run(_client(lambda p: exc).get_insider_trades_since("2026-08-24"))


@pytest.mark.parametrize("status", [403, 404])
def test_page_zero_403_404_is_the_honest_empty(status):
    assert _run(_client(lambda p: _http_status_error(status)).get_insider_trades_since(
        "2026-08-24")) == []


@pytest.mark.parametrize("status", [403, 404])
def test_a_later_403_404_is_a_lost_page_not_an_empty_feed(status):
    pages = {0: _page(0, 4, "2026-09-22"), 1: _http_status_error(status)}
    with pytest.raises(FMPPartialPageException) as ei:
        _run(_client(lambda p: pages.get(p, [])).get_insider_trades_since("2026-08-24", page_size=4))
    assert len(ei.value.partial) == 4


@pytest.mark.parametrize("body", [{"Error Message": "x"}, "oops", None, 7])
def test_a_non_list_body_is_a_lost_page_on_any_page(body):
    with pytest.raises(FMPPartialPageException) as ei:
        _run(_client(lambda p: body).get_insider_trades_since("2026-08-24"))
    assert ei.value.partial == []
    pages = {0: _page(0, 4, "2026-09-22"), 1: body}
    with pytest.raises(FMPPartialPageException) as ei:
        _run(_client(lambda p: pages.get(p, [])).get_insider_trades_since("2026-08-24", page_size=4))
    assert len(ei.value.partial) == 4


def test_the_page_cap_inside_the_window_raises_and_warns(caplog):
    caplog.set_level(logging.WARNING, logger="app.integrations.fmp")
    with pytest.raises(FMPPartialPageException) as ei:
        _run(_client(lambda p: _page(p * 10, 3, "2026-09-22")).get_insider_trades_since(
            "2026-08-24", page_size=3, max_pages=2))
    assert ei.value.pages_failed == 0 and len(ei.value.partial) == 6
    assert any("window NOT covered" in r.getMessage() for r in caplog.records)


def test_request_failures_counter_moves_through_the_real_wrapper():
    c = _client(lambda p: FMPUnavailableException("down"))
    before = c.request_failures
    with pytest.raises(FMPUnavailableException):
        _run(c.get_insider_trades_since("2026-08-24"))
    assert c.request_failures == before + 1


# ── page-shift repeats ──────────────────────────────────────────────────────────────

def test_a_row_repeated_from_an_earlier_page_is_dropped():
    """New filings mid-walk push the feed down: the head of page 1 repeats page 0's tail."""
    p0 = _page(0, 4, "2026-09-22")
    p1 = [p0[-1], p0[-2]] + _page(20, 2, "2026-08-01")
    rows = _run(_client(lambda p: {0: p0, 1: p1}.get(p, [])).get_insider_trades_since(
        "2026-08-24", page_size=4))
    assert len(rows) == 6
    assert sum(1 for r in rows if r["symbol"] == "S3") == 1


def test_identical_rows_within_one_page_are_separate_fills_and_kept():
    fill = _row(1, "2026-09-22")
    rows = _run(_client(lambda p: [fill, dict(fill), _row(2, "2026-09-22")] if p == 0 else [])
                .get_insider_trades_since("2026-08-24", symbol="GME", page_size=1000))
    assert len(rows) == 3


def test_non_dict_rows_are_skipped_not_fatal():
    rows = _run(_client(lambda p: ["x", None, 3, _row(1, "2026-09-22")] if p == 0 else [])
                .get_insider_trades_since("2026-08-24", symbol="GME", page_size=1000))
    assert rows == [_row(1, "2026-09-22")]
