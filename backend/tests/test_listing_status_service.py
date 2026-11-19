"""`listing_status_service` — the evidence behind the grey "Delisted" label (owner D2/D4).

ROKU (Fox Corp acquired Roku): FMP's delisted list has it (NASDAQ, 2026-11-09), its profile
says `isActivelyTrading: false`, its last real trade was 2026-11-06 at $160.52 — and FMP
keeps that price in the profile for months. The rule labels it only when ALL four hold:
  (b) not priced in the latest US session (`session_pricing`, checked FIRST, free);
  (a) FMP's inactive flag (the quote's, else the raw profile's);
  (c) a delisted-companies row dated on or before today, ET calendar;
  (d) no traded EOD day after that row (which also gives the last-trade date).
Anything unknown answers None — today's behaviour. Every outlier below asserts the
DEGRADED answer (None, no call), not merely "no crash".

Hermetic: the FMP client is a fake that counts calls (`listing_status_service.get_fmp_client`
is the binding the service uses), the registry is published with the 5,000-row filler, and
the clock is a module attribute moved by hand. The autouse conftest fixture resets both the
service and the registry around every test.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from app.integrations.fmp import FMPUnavailableException
from app.schemas.listing_status import LISTING_FIELDS
from app.services import listing_status_service as ls
from app.services import session_pricing as sp
from app.services.price_service import PriceService

ET = ZoneInfo("America/New_York")
# Friday 2026-11-13, 10:00 ET: the session is 11-13, so the registry must hold 11-12.
NOW = datetime(2026, 11, 13, 10, 0, tzinfo=ET)
SESSION = "2026-11-12"

ROKU_PROFILE: Dict[str, Any] = {
    "symbol": "ROKU", "companyName": "Roku, Inc.", "exchange": "NASDAQ", "price": 160.52,
    "change": 1.11, "changePercentage": 0.6963, "volume": 0, "averageVolume": 4_000_000,
    "marketCap": 23_000_000_000, "range": "52.43-162.10", "isActivelyTrading": False,
    "isEtf": False, "isFund": False, "currency": "USD",
}


def _profile(sym: str, *, active: bool, price: float = 20.0) -> Dict[str, Any]:
    return {"symbol": sym, "companyName": f"{sym} Inc.", "exchange": "NYSE", "price": price,
            "change": 0.5, "changePercentage": 2.5, "isActivelyTrading": active}


def _quote(sym: str, *, active: bool = False) -> Dict[str, Any]:
    return PriceService._from_profile(ROKU_PROFILE if sym == "ROKU" else _profile(sym, active=active))


def _row(sym, day, exchange="NYSE"):
    return {"symbol": sym, "companyName": f"{sym} Co", "exchange": exchange,
            "ipoDate": "2000-01-03", "delistedDate": day}


# FMP's list, newest first: a scheduled (future) row, ROKU, the TXSE moves, a reused symbol.
HEAD_ROWS = [
    _row("NB2.F", "2026-12-30", "OTC"),
    _row("TMRW", "2026-11-10", "NASDAQ"),
    _row("ROKU", "2026-11-09", "NASDAQ"),
    _row("ET", "2026-10-05"), _row("DDS", "2026-10-05"), _row("USAC", "2026-10-05"),
    _row("TCBI", "2026-10-08", "NASDAQ"),
    _row("REUSE", "2026-06-01", "NASDAQ"),
    _row("NOEXCH", "2026-09-01", ""),
]


def _pages(head=HEAD_ROWS, n_pages=ls._WINDOW_PAGES, deep: Optional[Dict[int, list]] = None):
    """`n_pages` full pages of 100: `head` on page 0, filler symbols after, dated backwards."""
    pages = []
    day = date(2026, 9, 30)
    for p in range(n_pages):
        rows = list(head) if p == 0 else []
        rows += list((deep or {}).get(p, []))
        while len(rows) < ls._PAGE_LIMIT:
            rows.append(_row(f"F{p:02d}{len(rows):03d}", day.isoformat()))
            day -= timedelta(days=0 if len(rows) % 3 else 1)
        pages.append(rows)
    return pages


def _eod(last_traded: str, *, zero_after: Optional[List[str]] = None, start="2026-09-20"):
    """Daily rows from `start` to `last_traded` with volume, then zero-volume carried rows."""
    rows = []
    d = date.fromisoformat(start)
    end = date.fromisoformat(last_traded)
    while d <= end:
        if d.weekday() < 5:
            rows.append({"date": d.isoformat(), "close": 100.0, "volume": 1_000})
        d += timedelta(days=1)
    for day in zero_after or []:
        rows.append({"date": day, "close": 100.0, "volume": 0})
    return list(reversed(rows))   # FMP answers newest first


EOD = {
    "ROKU": _eod("2026-11-06", zero_after=["2026-11-09", "2026-11-10", "2026-11-12"]),
    "DDS": _eod("2026-10-05"),
    "USAC": _eod("2026-10-05"),
    "ET": _eod("2026-11-12"),              # FMP prices ET again
    "REUSE": _eod("2026-11-11"),           # a new company trades under an old symbol
    "TCBI": _eod("2026-10-07"),
    "NOEXCH": _eod("2026-08-28"),
}


class _FMP:
    def __init__(self, pages=None, eod=None, *, page_fail=None, eod_fail=None, delay=0.0):
        self.pages = pages if pages is not None else _pages()
        self.eod = dict(EOD if eod is None else eod)
        self.page_fail = dict(page_fail or {})      # page -> exception or "empty"
        self.eod_fail = dict(eod_fail or {})        # symbol -> exception or raw body
        self.delay = delay
        self.page_calls: List[int] = []
        self.eod_calls: List[tuple] = []

    async def get_delisted_companies(self, page, limit=100):
        self.page_calls.append(page)
        if self.delay:
            await asyncio.sleep(self.delay)
        fail = self.page_fail.get(page)
        if isinstance(fail, BaseException):
            raise fail
        if fail == "empty":
            return []
        assert limit == 100
        return copy.deepcopy(self.pages[page]) if page < len(self.pages) else []

    async def get_historical_prices(self, sym, from_date=None, to_date=None):
        self.eod_calls.append((sym, from_date, to_date))
        if sym in self.eod_fail:
            body = self.eod_fail[sym]
            if isinstance(body, BaseException):
                raise body
            return body
        return copy.deepcopy(self.eod.get(sym, []))


class _Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(ls, "_clock", c)
    return c


@pytest.fixture
def fmp(monkeypatch):
    f = _FMP()
    monkeypatch.setattr(ls, "get_fmp_client", lambda: f)
    return f


def _publish(traded=("AAPL", "MSFT"), session=SESSION):
    filler = [{"symbol": f"Z{i:05d}", "close": 10.0, "volume": 100} for i in range(sp._MIN_PRICED_SYMBOLS)]
    assert sp.publish_priced_session(session, filler + [
        {"symbol": s, "close": 50.0, "volume": 1_000} for s in traded
    ])


async def _settle():
    while ls._inflight:
        await asyncio.gather(*list(ls._inflight.values()), return_exceptions=True)


@pytest_asyncio.fixture(autouse=True)
async def _drain_background_loads():
    """Let every background load a test left running finish ON ITS LOOP: pytest-asyncio
    closes each test's loop before the (sync) conftest reset runs, and a load still holding
    the page gate then dies noisily at garbage collection."""
    yield
    await _settle()


def _logs(caplog, level):
    return [r.getMessage() for r in caplog.records if r.levelno == level and r.name == ls.logger.name]


# ── the confirmed case ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_roku_is_confirmed_with_all_four_fields(fmp, clock):
    _publish()
    listing = await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW)
    assert listing == ls.DelistedListing("ROKU", date(2026, 11, 9), "NASDAQ", date(2026, 11, 6))
    assert listing.wire() == {
        "listing_status": "delisted", "delisted_date": "2026-11-09",
        "delisted_exchange": "NASDAQ", "last_trade_date": "2026-11-06",
    }
    assert tuple(listing.wire()) == LISTING_FIELDS
    assert sorted(fmp.page_calls) == list(range(ls._WINDOW_PAGES)), "ONE window load"
    assert fmp.eod_calls == [("ROKU", "2026-09-25", "2026-11-13")], "delisted − 45 d .. today"
    # Answered from memory afterwards: no further call, even with no wait at all.
    again = await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW, wait=0)
    assert again == listing
    assert len(fmp.page_calls) == ls._WINDOW_PAGES and len(fmp.eod_calls) == 1


@pytest.mark.asyncio
async def test_a_moved_listing_fmp_no_longer_prices_is_labelled_d4(fmp, clock):
    """D4 (owner, 2026-11-13): DDS moved NYSE → TXSE on 10-05 and FMP has no price since."""
    _publish()
    listing = await ls.get_delisted("DDS", quote=_quote("DDS"), now=NOW)
    assert listing.wire() == {"listing_status": "delisted", "delisted_date": "2026-10-05",
                              "delisted_exchange": "NYSE", "last_trade_date": "2026-10-05"}


@pytest.mark.asyncio
async def test_the_raw_profile_is_read_only_when_no_quote_is_in_hand(fmp, clock):
    _publish()
    assert (await ls.get_delisted("ROKU", profile=ROKU_PROFILE, now=NOW)) is not None
    assert (await ls.get_delisted("ROKU", quote={}, profile=ROKU_PROFILE, now=NOW)) is not None


@pytest.mark.asyncio
async def test_the_quotes_flag_wins_over_a_stale_profile(fmp, clock):
    """A fresh quote WITHOUT the flag (it trades) beats an inactive profile: no I/O."""
    _publish()
    active_quote = _quote("ROKU")
    active_quote = {k: v for k, v in active_quote.items() if k != "isActivelyTrading"}
    assert await ls.get_delisted("ROKU", quote=active_quote, profile=ROKU_PROFILE, now=NOW) is None
    assert fmp.page_calls == [] and fmp.eod_calls == []


# ── no I/O for anything that is not a candidate ───────────────────────────────

@pytest.mark.asyncio
async def test_an_active_quote_makes_no_io(fmp, clock, monkeypatch):
    _publish()
    built = []
    monkeypatch.setattr(ls, "get_fmp_client", lambda: built.append(1) or fmp)
    assert await ls.get_delisted("OBK", quote=_quote("OBK", active=True), now=NOW) is None
    assert await ls.get_delisted("OBK", profile=_profile("OBK", active=True), now=NOW) is None
    assert await ls.get_delisted("OBK", now=NOW) is None                  # nothing in hand
    assert built == [] and fmp.page_calls == [] and fmp.eod_calls == []


@pytest.mark.asyncio
async def test_et_on_the_list_but_priced_is_not_labelled(fmp, clock):
    """FMP prices ET again: (b) ends it before any list call."""
    _publish(traded=("AAPL", "ET"))
    assert await ls.get_delisted("ET", quote=_quote("ET"), now=NOW) is None
    assert fmp.page_calls == [] and fmp.eod_calls == []


@pytest.mark.asyncio
async def test_et_unpriced_but_traded_after_its_row_is_rejected_by_d(fmp, clock, caplog):
    """If FMP stopped pricing ET again for a session, its 10-05 row must not label it: it
    traded through 11-12."""
    _publish()
    with caplog.at_level(logging.INFO, logger=ls.logger.name):
        assert await ls.get_delisted("ET", quote=_quote("ET"), now=NOW) is None
    assert [c[0] for c in fmp.eod_calls] == ["ET"]
    assert any("ET traded on 2026-11-12" in m for m in _logs(caplog, logging.INFO))
    assert ls.peek_delisted("ET", NOW) is None


@pytest.mark.asyncio
async def test_a_reused_symbol_that_trades_is_not_labelled(fmp, clock):
    _publish()
    assert await ls.get_delisted("REUSE", quote=_quote("REUSE"), now=NOW) is None
    _publish(traded=("REUSE",), session="2026-11-13")       # it trades: (b) now True
    assert await ls.get_delisted("REUSE", quote=_quote("REUSE"), now=NOW) is None


@pytest.mark.asyncio
async def test_cold_and_stale_registries_are_unknown(fmp, clock):
    assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is None      # cold
    _publish(session="2026-11-10")                                                     # stale
    assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is None
    assert fmp.page_calls == [] and fmp.eod_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sym", ["^GSPC", "BTCUSD", "", "   ", None, 42])
async def test_blocked_crypto_and_non_symbols_are_never_candidates(fmp, clock, sym):
    _publish()
    q = {"symbol": sym, "price": 1.0, "isActivelyTrading": False}
    assert await ls.get_delisted(sym, quote=q, now=NOW) is None
    assert fmp.page_calls == []


# ── (c): the list ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_not_on_the_list_is_none_and_the_window_is_reused(fmp, clock):
    _publish()
    assert await ls.get_delisted("GONE", quote=_quote("GONE"), now=NOW) is None
    assert len(fmp.page_calls) == ls._WINDOW_PAGES and fmp.eod_calls == []
    assert await ls.get_delisted("GONE2", quote=_quote("GONE2"), now=NOW, wait=0) is None
    assert len(fmp.page_calls) == ls._WINDOW_PAGES, "the held window answers a second miss"


@pytest.mark.asyncio
async def test_a_future_dated_scheduled_row_never_matches(fmp, clock):
    _publish()
    assert await ls.get_delisted("NB2.F", quote=_quote("NB2.F"), now=NOW) is None
    assert fmp.eod_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("now", [
    datetime(2026, 11, 10, 3, 30, tzinfo=timezone.utc),     # 22:30 ET on 11-09
    datetime(2026, 11, 10, 3, 30),                           # naive = UTC (market_hours' rule)
])
async def test_today_is_the_et_calendar_date(fmp, clock, now):
    """At 03:30 UTC on 11-10 it is still 11-09 in New York: TMRW's 11-10 row is tomorrow."""
    _publish(session="2026-11-09")
    assert await ls.get_delisted("TMRW", quote=_quote("TMRW"), now=now) is None
    listing = await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=now)
    assert listing is not None and listing.delisted_date == date(2026, 11, 9)
    assert fmp.eod_calls[-1] == ("ROKU", "2026-09-25", "2026-11-09")


@pytest.mark.asyncio
async def test_the_newest_row_on_or_before_today_wins(fmp, clock):
    """A symbol delisted twice (an old company, then the reuser): the newest past row."""
    head = HEAD_ROWS + [_row("TWICE", "2026-10-20", "NASDAQ"), _row("TWICE", "2026-01-15", "NYSE"),
                        _row("TWICE", "2026-12-01", "NYSE")]
    fmp.pages = _pages(head=head)
    fmp.eod["TWICE"] = _eod("2026-10-16")
    _publish()
    listing = await ls.get_delisted("TWICE", quote=_quote("TWICE"), now=NOW)
    assert (listing.delisted_date, listing.exchange) == (date(2026, 10, 20), "NASDAQ")


@pytest.mark.asyncio
async def test_symbols_are_normalised_on_both_sides(fmp, clock):
    fmp.pages = _pages(head=[_row(" Roku ", "2026-11-09", "NASDAQ")])
    _publish()
    listing = await ls.get_delisted(" roku ", quote=_quote("ROKU"), now=NOW)
    assert listing is not None and listing.symbol == "ROKU"


@pytest.mark.asyncio
async def test_malformed_rows_are_skipped_and_counted_once(fmp, clock, caplog):
    head = [
        "garbage", None, 7, [],
        {"symbol": 42, "delistedDate": "2026-11-09"},
        {"symbol": "", "delistedDate": "2026-11-09"},
        {"symbol": "BADDATE", "delistedDate": "N/A"},
        {"symbol": "BADDATE2", "delistedDate": "2026-13-40"},
        {"symbol": "NODATE"},
        {"symbol": "NONEDATE", "delistedDate": None},
        {"symbol": "LONGEX", "delistedDate": "2026-11-09", "exchange": "X" * 200},
        _row("ROKU", "2026-11-09T00:00:00", "  NASDAQ "),
    ]
    fmp.pages = _pages(head=head)
    fmp.eod["LONGEX"] = _eod("2026-11-06")
    _publish()
    with caplog.at_level(logging.INFO, logger=ls.logger.name):
        listing = await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW)
        longex = await ls.get_delisted("LONGEX", quote=_quote("LONGEX"), now=NOW)
    assert listing.delisted_date == date(2026, 11, 9) and listing.exchange == "NASDAQ"
    assert longex is not None and longex.exchange is None, "an absurd exchange is dropped"
    for sym in ("BADDATE", "BADDATE2", "NODATE", "NONEDATE"):
        assert await ls.get_delisted(sym, quote=_quote(sym), now=NOW) is None
    loads = [m for m in _logs(caplog, logging.INFO) if "load —" in m]
    assert len(loads) == 1 and "10 malformed skipped" in loads[0], loads


@pytest.mark.asyncio
async def test_a_row_without_an_exchange_is_labelled_with_none(fmp, clock):
    _publish()
    listing = await ls.get_delisted("NOEXCH", quote=_quote("NOEXCH"), now=NOW)
    assert listing.exchange is None and listing.wire()["delisted_exchange"] is None


# ── failures fail open, loudly ────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [FMPUnavailableException("503"), RuntimeError("boom"),
                                     asyncio.TimeoutError(), "empty"])
async def test_a_page0_failure_warns_memoises_then_retries(fmp, clock, caplog, failure):
    fmp.page_fail = {0: failure}
    _publish()
    with caplog.at_level(logging.WARNING, logger=ls.logger.name):
        assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is None
    warns = _logs(caplog, logging.WARNING)
    assert any("page 0" in m and "ROKU" in m for m in warns), warns
    first = len(fmp.page_calls)
    assert first <= ls._LOAD_CONCURRENCY, "pages queued behind a failed page 0 are not fetched"
    # Inside the degraded memo: no call at all.
    clock.t += ls._DEGRADED_TTL - 1
    assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is None
    assert len(fmp.page_calls) == first and fmp.eod_calls == []
    # After it, FMP is back: labelled.
    fmp.page_fail = {}
    clock.t += 2
    assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is not None


@pytest.mark.asyncio
async def test_a_lost_later_page_installs_the_rest_and_reloads_soon(fmp, clock, caplog):
    deep_only = _row("DEEP", "2026-05-01", "NYSE")
    fmp.pages = _pages(deep={5: [deep_only]})
    fmp.eod["DEEP"] = _eod("2026-04-30", start="2026-03-01")
    fmp.page_fail = {5: FMPUnavailableException("blip")}
    _publish()
    with caplog.at_level(logging.WARNING, logger=ls.logger.name):
        assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is not None
        assert await ls.get_delisted("DEEP", quote=_quote("DEEP"), now=NOW) is None
    assert any("lost page(s) 5" in m for m in _logs(caplog, logging.WARNING))
    assert ls._window.partial is True
    # A partial window is reloaded once the degraded interval passes; coverage never shrinks.
    fmp.page_fail = {}
    clock.t += ls._DEGRADED_TTL + 1
    before = len(fmp.page_calls)
    assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is not None
    await _settle()
    assert len(fmp.page_calls) == before + ls._WINDOW_PAGES
    assert await ls.get_delisted("DEEP", quote=_quote("DEEP"), now=NOW) is not None
    assert ls._window.partial is False


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [FMPUnavailableException("eod down"), {"Error Message": "x"},
                                  "oops", None])
async def test_an_eod_failure_fails_open_and_is_retried_later(fmp, clock, caplog, body):
    fmp.eod_fail = {"ROKU": body}
    _publish()
    with caplog.at_level(logging.WARNING, logger=ls.logger.name):
        assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is None
    assert any("ROKU EOD read failed" in m for m in _logs(caplog, logging.WARNING))
    assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is None
    assert len(fmp.eod_calls) == 1, "a failure is not re-read on every request"
    fmp.eod_fail = {}
    clock.t += ls._EOD_FAILURE_TTL + 1
    assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is not None
    assert len(fmp.eod_calls) == 2


@pytest.mark.asyncio
async def test_the_legacy_historical_dict_shape_is_read(fmp, clock):
    fmp.eod["ROKU"] = {"symbol": "ROKU", "historical": EOD["ROKU"]}
    _publish()
    listing = await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW)
    assert listing.last_trade_date == date(2026, 11, 6)


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [
    [],                                                                  # nothing in the window
    [{"date": "2026-11-09", "volume": 0, "close": 160.52}],             # carried rows only
    [{"date": "2026-11-09", "volume": None}, {"date": "2026-11-10", "volume": float("nan")},
     {"date": "2026-11-11", "volume": "x"}, {"date": "bad", "volume": 5}, {"volume": 9},
     {"date": "2026-11-12", "volume": True}, "row", None],
])
async def test_no_traded_row_labels_with_an_unknown_last_trade(fmp, clock, rows):
    """No evidence of a trade after the row is (d) holding; the date is simply unknown."""
    fmp.eod["ROKU"] = rows
    _publish()
    listing = await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW)
    assert listing is not None and listing.last_trade_date is None
    assert listing.wire()["last_trade_date"] is None


@pytest.mark.asyncio
async def test_a_bogus_future_or_too_old_eod_row_is_ignored(fmp, clock):
    fmp.eod["ROKU"] = EOD["ROKU"] + [
        {"date": "2026-12-01", "volume": 500, "close": 1.0},   # after today: not a trade
        {"date": "2026-01-01", "volume": 500, "close": 1.0},   # before the window
    ]
    _publish()
    listing = await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW)
    assert listing is not None and listing.last_trade_date == date(2026, 11, 6)


@pytest.mark.asyncio
async def test_get_delisted_never_raises(fmp, clock, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(ls, "_candidate", boom)
    with caplog.at_level(logging.WARNING, logger=ls.logger.name):
        assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is None
        assert await ls.get_delisted_many({"ROKU": _quote("ROKU")}, now=NOW) == {}
    assert len(_logs(caplog, logging.WARNING)) == 2


# ── concurrency: one load, bounded waits, nothing cancelled ───────────────────

@pytest.mark.asyncio
async def test_concurrent_lookups_share_one_load_and_one_eod_read(fmp, clock):
    _publish()
    syms = ["ROKU", "ROKU", "DDS", "USAC", "GONE"]
    out = await asyncio.gather(*(ls.get_delisted(s, quote=_quote(s), now=NOW) for s in syms))
    assert [o is not None for o in out] == [True, True, True, True, False]
    assert sorted(fmp.page_calls) == list(range(ls._WINDOW_PAGES))
    assert sorted(c[0] for c in fmp.eod_calls) == ["DDS", "ROKU", "USAC"]


@pytest.mark.asyncio
async def test_the_window_is_fetched_under_the_concurrency_gate(fmp, clock):
    live, peak = 0, 0
    real = fmp.get_delisted_companies

    async def gated(page, limit=100):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.001)
        try:
            return await real(page, limit)
        finally:
            live -= 1

    fmp.get_delisted_companies = gated
    _publish()
    assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is not None
    assert 1 < peak <= ls._LOAD_CONCURRENCY


@pytest.mark.asyncio
async def test_a_wait_timeout_answers_none_without_cancelling_the_load(fmp, clock, caplog):
    fmp.delay = 0.02
    _publish()
    with caplog.at_level(logging.WARNING, logger=ls.logger.name):
        assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW, wait=0.001) is None
        assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW, wait=0.001) is None
    warns = [m for m in _logs(caplog, logging.WARNING) if "still resolving" in m]
    assert len(warns) == 1, "one WARNING per resolution, not per waiting request"
    await _settle()
    assert ls._window is not None, "the load ran to completion"
    listing = await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW, wait=0)
    assert listing is not None and len(fmp.page_calls) == ls._WINDOW_PAGES
    assert len(fmp.eod_calls) == 1


@pytest.mark.asyncio
async def test_a_cancelled_caller_does_not_cancel_the_shared_load(fmp, clock):
    fmp.delay = 0.01
    _publish()
    caller = asyncio.ensure_future(ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW))
    await asyncio.sleep(0.002)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    await _settle()
    assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW, wait=0) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("wait", [-1, float("nan"), float("inf"), "x", None])
async def test_a_bad_wait_never_blocks_or_raises(fmp, clock, wait):
    fmp.delay = 0.01
    _publish()
    got = await asyncio.wait_for(ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW, wait=wait), 5)
    assert got is None or got.symbol == "ROKU"


# ── freshness of the window ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_head_refresh_after_six_hours_merges_new_rows(fmp, clock):
    _publish()
    assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is not None
    fmp.pages = _pages(head=[_row("NEWCO", "2026-11-12", "NYSE")] + HEAD_ROWS)
    fmp.eod["NEWCO"] = _eod("2026-11-11")
    clock.t += ls._HEAD_REFRESH_TTL + 1
    before = len(fmp.page_calls)
    # The held window answers; the refresh runs in the background (ROKU's own EOD memo
    # expired at the same 6 h, so it is re-read — not the window).
    assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is not None
    await _settle()
    assert sorted(fmp.page_calls[before:]) == list(range(ls._HEAD_PAGES))
    assert await ls.get_delisted("NEWCO", quote=_quote("NEWCO"), now=NOW) is not None
    assert await ls.get_delisted("DDS", quote=_quote("DDS"), now=NOW) is not None, "old rows kept"


@pytest.mark.asyncio
async def test_a_full_reload_after_a_day_and_a_drop_after_three(fmp, clock, caplog):
    _publish()
    assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is not None
    clock.t += ls._FULL_RELOAD_TTL + 1
    before = len(fmp.page_calls)
    await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW, wait=0)
    await _settle()
    assert len(fmp.page_calls) == before + ls._WINDOW_PAGES
    # Three days with every reload failing: the window is dropped, never served forever.
    fmp.page_fail = {0: FMPUnavailableException("down")}
    clock.t += ls._WINDOW_MAX_STALE + 1
    with caplog.at_level(logging.WARNING, logger=ls.logger.name):
        assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is None
    assert any("days old" in m and "dropped" in m for m in _logs(caplog, logging.WARNING))
    assert ls._window is None


# ── the batch, peek and the pre-warm helpers ──────────────────────────────────

@pytest.mark.asyncio
async def test_the_batch_labels_only_confirmed_symbols_and_never_mutates(fmp, clock):
    _publish(traded=("AAPL", "ET"))
    quotes = {
        "ROKU": _quote("ROKU"), "dds": _quote("DDS"), "AAPL": _quote("AAPL", active=True),
        "ET": _quote("ET"), "GONE": _quote("GONE"), "BAD": "not-a-quote", "": _quote("USAC"),
    }
    snapshot = copy.deepcopy(quotes)
    out = await ls.get_delisted_many(quotes, now=NOW)
    assert sorted(out) == ["DDS", "ROKU", "USAC"], "keys are UPPERCASE; a blank key uses the quote's symbol"
    assert quotes == snapshot, "the quote dicts are shared by reference with price_service"
    assert len(fmp.page_calls) == ls._WINDOW_PAGES


@pytest.mark.asyncio
@pytest.mark.parametrize("quotes", [None, [], "x", {}, {"A": None}])
async def test_the_batch_tolerates_junk(fmp, clock, quotes):
    _publish()
    assert await ls.get_delisted_many(quotes, now=NOW) == {}
    assert fmp.page_calls == []


@pytest.mark.asyncio
async def test_peek_follows_the_registry_and_expires(fmp, clock):
    _publish()
    assert ls.peek_delisted("ROKU", NOW) is None, "nothing confirmed yet"
    listing = await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW)
    assert ls.peek_delisted(" roku ", NOW) == listing
    _publish(traded=("ROKU",), session="2026-11-13")      # a relisted / reused symbol trades
    assert ls.peek_delisted("ROKU", NOW) is None
    sp._reset_for_tests()
    _publish()
    assert ls.peek_delisted("ROKU", NOW) == listing
    clock.t += ls._EOD_TTL + 1
    assert ls.peek_delisted("ROKU", NOW) is None


@pytest.mark.asyncio
async def test_delisted_row_is_condition_c_alone(fmp, clock):
    """The unpriced-holdings hint: the list row only, no registry or flag."""
    assert await ls.delisted_row("roku", now=NOW) == (date(2026, 11, 9), "NASDAQ")
    assert await ls.delisted_row("ET", now=NOW) == (date(2026, 10, 5), "NYSE")
    assert await ls.delisted_row("NB2.F", now=NOW) is None
    assert await ls.delisted_row("GONE", now=NOW) is None
    assert await ls.delisted_row(None, now=NOW) is None
    assert len(fmp.page_calls) == ls._WINDOW_PAGES and fmp.eod_calls == []


@pytest.mark.asyncio
async def test_kick_window_load_runs_in_the_background(fmp, clock):
    ls.kick_window_load("ROKU")
    assert ls._WINDOW_KEY in ls._inflight
    await _settle()
    assert ls._window is not None and len(fmp.page_calls) == ls._WINDOW_PAGES
    ls.kick_window_load("ROKU")                     # fresh: nothing to do
    assert ls._WINDOW_KEY not in ls._inflight


def test_kick_without_a_loop_is_a_no_op(fmp):
    ls.kick_window_load("ROKU")                     # sync caller: nothing scheduled, no raise
    assert ls._inflight == {}


# ── a bug inside a background task is never swallowed ─────────────────────────

@pytest.mark.asyncio
async def test_a_crashing_load_is_logged_with_its_stack_and_degrades(fmp, clock, monkeypatch, caplog):
    def boom(rows):
        raise KeyError("parser bug")

    monkeypatch.setattr(ls, "_parse_page", boom)
    _publish()
    with caplog.at_level(logging.WARNING, logger=ls.logger.name):
        assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is None
    crashed = [r for r in caplog.records if "load crashed" in r.getMessage()]
    assert crashed and crashed[0].exc_info is not None and "ROKU" in crashed[0].getMessage()
    assert ls._degraded(), "a crash degrades like a failed page 0 (no reload storm)"


@pytest.mark.asyncio
async def test_a_crashing_resolution_is_logged_never_silent(fmp, clock, monkeypatch, caplog):
    def boom(*a, **k):
        raise ZeroDivisionError("decide bug")

    monkeypatch.setattr(ls, "_decide", boom)
    _publish()
    with caplog.at_level(logging.WARNING, logger=ls.logger.name):
        assert await ls.get_delisted("ROKU", quote=_quote("ROKU"), now=NOW) is None
    crashed = [r for r in caplog.records if "resolving ROKU crashed" in r.getMessage()]
    assert crashed and crashed[0].exc_info is not None


def test_the_eod_memo_is_bounded(monkeypatch, clock):
    monkeypatch.setattr(ls, "_EOD_MEMO_MAX", 3)
    for i in range(10):
        ls._remember_eod(f"S{i}", date(2026, 11, 9), True, None)
    assert len(ls._eod) <= 3
    assert ("S9", date(2026, 11, 9)) in ls._eod, "the newest entry is kept"
