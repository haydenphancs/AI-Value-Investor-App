"""Phase 2 of the "Delisted" label — money and polling stop at a CONFIRMED delisting.

Owner, 2026-11-13: "approve phase 2 too. finish everything". What was approved:
  (1) a NEW AI report on a delisted ticker is refused BEFORE any charge on BOTH report doors
      (`POST /research/generate`, `GET /stocks/{t}/report`) with `TICKER_DELISTED` (409) and
      "ROKU was delisted from NASDAQ on Nov 9, 2026, so a new AI report can't be generated.
      You haven't been charged. Reports you already have stay available."; a cached report
      stays free; the report pre-warms stop spending FMP calls on it; a check that cannot
      decide within 8 s proceeds exactly as before (fail open);
  (2) new price alerts on it are refused ("… so it has no live price to watch."); existing
      rules are PAUSED — not quoted, not persisted, nothing deleted — shown with
      `paused_reason: "delisted"`, and resume by themselves once the ticker trades again.

Every refusal asserts the money fakes were NEVER called, and every unknown asserts the door
proceeded exactly as before. Hermetic: the listing service's FMP client and price source are
counting fakes; the registry is published for the REAL clock (the doors pass no `now`).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

import app.api.v1.endpoints.price_alerts as pa
import app.api.v1.endpoints.research as research
import app.api.v1.endpoints.stocks as stocks
import app.api.v1.endpoints.ticker_report as tr
from app.api.error_response import (
    ErrorCode,
    _DEFAULT_ACTIONS,
    _DEFAULT_STATUS,
    _USER_MESSAGES,
    format_listing_day,
    ticker_delisted_error,
)
from app.config import settings
from app.schemas.listing_status import LISTING_FIELDS
from app.schemas.price_alerts import (
    CreatePriceAlertRequest,
    PriceAlertResponse,
    UpdatePriceAlertRequest,
    price_alert_from_row,
)
from app.schemas.research import GenerateResearchRequest
from app.services import listing_status_service as ls
from app.services import session_pricing as sp
from app.services.price_alert_service import PriceAlertService
from app.services.price_service import PriceService
from app.utils.market_hours import previous_trading_day, session_trading_date
from tests.test_listing_status_service import ROKU_PROFILE, _FMP, _profile, _settle
from tests.test_ticker_report_credits import _install_fakes

USER = {"id": "authed-user-1"}
REPORT_COPY = (
    "ROKU was delisted from NASDAQ on Nov 9, 2026, so a new AI report can't be generated. "
    "You haven't been charged. Reports you already have stay available."
)
ALERT_COPY = "ROKU was delisted from NASDAQ on Nov 9, 2026, so it has no live price to watch."
ROKU_DETAILS = {"ticker": "ROKU", "delisted_date": "2026-11-09",
                "delisted_exchange": "NASDAQ", "step": "delisted"}
ROKU_WIRE = {"listing_status": "delisted", "delisted_date": "2026-11-09",
             "delisted_exchange": "NASDAQ", "last_trade_date": "2026-11-06"}


def _publish_current(traded=("AAPL", "OBK")):
    """A full-size session the REAL clock reads as current (the doors pass no `now`)."""
    session = previous_trading_day(session_trading_date())
    filler = [{"symbol": f"Z{i:05d}", "close": 10.0, "volume": 100}
              for i in range(sp._MIN_PRICED_SYMBOLS)]
    assert sp.publish_priced_session(session, filler + [
        {"symbol": s, "close": 50.0, "volume": 1_000} for s in traded
    ])


def _roku_quote() -> Dict[str, Any]:
    return PriceService._from_profile(dict(ROKU_PROFILE))


class _Quotes:
    """`price_source()` stand-in for the listing service: counts every `get_quote`."""

    def __init__(self, quotes=None, *, delay=0.0, raises=None):
        self.quotes = {"ROKU": _roku_quote()} if quotes is None else dict(quotes)
        self.delay = delay
        self.raises = raises
        self.calls: List[str] = []

    async def get_quote(self, symbol):
        self.calls.append(symbol)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        q = self.quotes.get(symbol)
        return dict(q) if q else {}


@pytest.fixture
def listing_fmp(monkeypatch):
    f = _FMP()
    monkeypatch.setattr(ls, "get_fmp_client", lambda: f)
    return f


@pytest.fixture
def quotes(monkeypatch):
    q = _Quotes()
    monkeypatch.setattr(ls, "price_source", lambda *a, **k: q)
    return q


@pytest_asyncio.fixture(autouse=True)
async def _drain_background_loads():
    yield
    await _settle()


@pytest.fixture(autouse=True)
def _clean_report_door():
    tr._USER_GENERATIONS.clear()
    tr._INFLIGHT_REPORTS = 0
    yield
    tr._USER_GENERATIONS.clear()
    tr._INFLIGHT_REPORTS = 0


async def _confirm_roku(listing_fmp, quotes) -> ls.DelistedListing:
    """Drive ROKU to a CONFIRMED delisting the way a screen would, so `peek_delisted` holds it."""
    _publish_current()
    got = (await ls.get_delisted_for_symbols(["ROKU"])).get("ROKU")
    assert got is not None and got.wire() == ROKU_WIRE
    return got


def _body(resp) -> Dict[str, Any]:
    return json.loads(bytes(resp.body))


# ── the error code ────────────────────────────────────────────────────────────

def test_the_code_is_registered_409_with_default_copy_and_no_action():
    assert ErrorCode.TICKER_DELISTED.value == "TICKER_DELISTED"
    assert _DEFAULT_STATUS[ErrorCode.TICKER_DELISTED] == 409
    assert _USER_MESSAGES[ErrorCode.TICKER_DELISTED] == (
        "This company is no longer listed, so a new AI report can't be generated. "
        "You haven't been charged."
    )
    # No action, matching THEME_NOT_FOUND / TRILLION_CLUB_COMPANY_NOT_FOUND: retrying can
    # never help and iOS has no "none" action (it would decode as an unknown one).
    assert ErrorCode.TICKER_DELISTED not in _DEFAULT_ACTIONS


@pytest.mark.parametrize("purpose,copy", [("report", REPORT_COPY), ("price_alert", ALERT_COPY)])
def test_the_refusal_body_matches_the_ios_contract(purpose, copy):
    resp = ticker_delisted_error(ticker="ROKU", delisted_date=date(2026, 11, 9),
                                 exchange="NASDAQ", purpose=purpose)
    body = _body(resp)
    assert resp.status_code == 409
    assert set(body) == {"error_code", "message", "user_message", "action", "details"}
    assert body["error_code"] == "TICKER_DELISTED"
    assert body["user_message"] == copy
    assert body["action"] is None
    assert body["details"] == ROKU_DETAILS
    for value in body["details"].values():
        assert isinstance(value, (str, int, float, bool)), "details must be flat scalars"
    assert "ROKU" in body["message"] and "2026-11-09" in body["message"]


def test_an_exchange_fmp_does_not_name_drops_from_and_stays_a_scalar():
    body = _body(ticker_delisted_error(ticker="NOEXCH", delisted_date=date(2026, 9, 1),
                                       exchange="  ", purpose="report"))
    assert body["user_message"].startswith("NOEXCH was delisted on Sep 1, 2026, so a new AI report")
    assert body["details"]["delisted_exchange"] == ""


def test_an_unknown_purpose_is_a_programming_error_not_a_default():
    with pytest.raises(ValueError):
        ticker_delisted_error(ticker="ROKU", delisted_date=date(2026, 11, 9),
                              exchange="NASDAQ", purpose="chat")


@pytest.mark.parametrize("day,text", [
    (date(2026, 11, 9), "Nov 9, 2026"), (date(2026, 1, 1), "Jan 1, 2026"),
    (date(2026, 12, 31), "Dec 31, 2026"), (date(2026, 10, 5), "Oct 5, 2026"),
])
def test_the_date_is_printed_as_the_calendar_date_it_is(day, text):
    """A US Eastern calendar date printed as-is: no time-zone shift, no locale, no zero pad."""
    assert format_listing_day(day) == text


# ── get_delisted_for_symbols: the check for doors that hold no quote ──────────

@pytest.mark.asyncio
async def test_a_ticker_that_traded_costs_no_io(listing_fmp, quotes):
    _publish_current()
    assert await ls.get_delisted_for_symbols(["AAPL", "obk"]) == {}
    assert quotes.calls == [] and listing_fmp.page_calls == [] and listing_fmp.eod_calls == []


@pytest.mark.asyncio
async def test_a_cold_registry_is_unknown_logged_and_costs_nothing(listing_fmp, quotes, caplog):
    with caplog.at_level(logging.DEBUG, logger=ls.logger.name):
        assert await ls.get_delisted_for_symbols(["ROKU"], purpose="GET /stocks/ROKU/report") == {}
    assert quotes.calls == [] and listing_fmp.page_calls == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ROKU" in m and "GET /stocks/ROKU/report" in m and "unknown" in m for m in warnings)


@pytest.mark.asyncio
async def test_a_display_caller_logs_the_unknown_quietly(listing_fmp, quotes, caplog):
    with caplog.at_level(logging.DEBUG, logger=ls.logger.name):
        await ls.get_delisted_for_symbols(["ROKU"], unknown_level=logging.DEBUG)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("ROKU" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_roku_is_confirmed_with_one_quote_read_then_from_memory(listing_fmp, quotes):
    _publish_current()
    got = await ls.get_delisted_for_symbols(["roku", " ROKU "])
    assert list(got) == ["ROKU"] and got["ROKU"].wire() == ROKU_WIRE
    assert quotes.calls == ["ROKU"], "ONE quote read, de-duplicated"
    loads = len(listing_fmp.page_calls)
    # Held now: the next door asks nothing upstream at all.
    again = await ls.get_delisted_for_symbols(["ROKU"])
    assert again["ROKU"] == got["ROKU"]
    assert quotes.calls == ["ROKU"] and len(listing_fmp.page_calls) == loads


@pytest.mark.asyncio
async def test_a_bare_string_is_one_symbol_never_its_letters(listing_fmp, quotes):
    _publish_current()
    got = await ls.get_delisted_for_symbols("ROKU")
    assert list(got) == ["ROKU"] and quotes.calls == ["ROKU"]


@pytest.mark.parametrize("junk", [None, 42, [None, 7, "", "   "], {"x": 1}])
@pytest.mark.asyncio
async def test_junk_symbols_answer_nothing_and_never_raise(listing_fmp, quotes, junk):
    _publish_current()
    got = await ls.get_delisted_for_symbols(junk)
    assert isinstance(got, dict)
    assert quotes.calls == [] or quotes.calls == ["X"]


@pytest.mark.asyncio
async def test_crypto_and_unlicensed_symbols_are_never_quoted(listing_fmp, quotes):
    _publish_current()
    assert await ls.get_delisted_for_symbols(["BTCUSD", "^GSPC", "EURUSD"]) == {}
    assert quotes.calls == []


@pytest.mark.asyncio
async def test_an_active_quote_decides_not_delisted_without_the_list(listing_fmp, monkeypatch):
    """Registry False (no trade yesterday — a halt, a thin name) but FMP's flag is active."""
    _publish_current()
    q = _Quotes({"THIN": PriceService._from_profile(_profile("THIN", active=True))})
    monkeypatch.setattr(ls, "price_source", lambda *a, **k: q)
    assert await ls.get_delisted_for_symbols(["THIN"]) == {}
    assert q.calls == ["THIN"] and listing_fmp.page_calls == [], "no list load for an active flag"


@pytest.mark.parametrize("q", [
    _Quotes({}),                                       # empty answer
    _Quotes(raises=RuntimeError("profile blew up")),  # a raise that get_quote let through
])
@pytest.mark.asyncio
async def test_no_quote_is_unknown_and_proceeds(listing_fmp, monkeypatch, caplog, q):
    _publish_current()
    monkeypatch.setattr(ls, "price_source", lambda *a, **k: q)
    with caplog.at_level(logging.WARNING, logger=ls.logger.name):
        assert await ls.get_delisted_for_symbols(["ROKU"], purpose="the door") == {}
    assert listing_fmp.page_calls == []
    assert any("ROKU" in r.getMessage() and "the door" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_slow_quote_is_bounded_by_the_wait_and_cancelled(listing_fmp, monkeypatch, caplog):
    _publish_current()
    q = _Quotes(delay=5.0)
    monkeypatch.setattr(ls, "price_source", lambda *a, **k: q)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    with caplog.at_level(logging.WARNING, logger=ls.logger.name):
        assert await ls.get_delisted_for_symbols(["ROKU"], wait=0.05, purpose="the door") == {}
    assert loop.time() - t0 < 1.0, "never waits past `wait`"
    assert any("did not arrive within" in r.getMessage() and "ROKU" in r.getMessage()
               for r in caplog.records)
    await asyncio.sleep(0.01)
    assert not [t for t in asyncio.all_tasks() if t.get_name() == "listing-status-quote-ROKU"
                and not t.done()], "the slow quote read is cancelled, not leaked"


@pytest.mark.asyncio
async def test_a_slow_resolution_proceeds_then_lands_for_the_next_request(monkeypatch, quotes, caplog):
    """The window takes longer than the wait: the door proceeds (fail open), the WARNING names
    the ticker and the door, and the load finishes in the background for the next request."""
    _publish_current()
    slow = _FMP(delay=0.1)
    monkeypatch.setattr(ls, "get_fmp_client", lambda: slow)
    with caplog.at_level(logging.WARNING, logger=ls.logger.name):
        first = await ls.get_delisted_for_symbols(["ROKU"], wait=0.05, purpose="GET /stocks/ROKU/report")
    assert first == {}
    assert any("ROKU" in r.getMessage() and "GET /stocks/ROKU/report proceeds as listed (fail open)"
               in r.getMessage() for r in caplog.records)
    await _settle()
    assert (await ls.get_delisted_for_symbols(["ROKU"], wait=0))["ROKU"].wire() == ROKU_WIRE


@pytest.mark.asyncio
async def test_a_symbol_that_trades_again_is_no_longer_answered(listing_fmp, quotes):
    await _confirm_roku(listing_fmp, quotes)
    _publish_current(traded=("AAPL", "OBK", "ROKU"))
    assert await ls.get_delisted_for_symbols(["ROKU"]) == {}
    assert ls.peek_delisted("ROKU") is None


def _active_roku() -> Dict[str, Any]:
    """ROKU relisted / reused / priced again: FMP's profile now says actively trading."""
    return PriceService._from_profile({**ROKU_PROFILE, "isActivelyTrading": True})


@pytest.mark.parametrize("how", ["get_delisted", "get_delisted_many", "profile"])
@pytest.mark.asyncio
async def test_a_fresh_active_quote_retires_the_held_confirmation(listing_fmp, quotes, how):
    """Review finding (2026-11-13): the registry holds day D-1 for all of day D, and the memo
    outlived a quote that read ACTIVE — so the alert create door (seed quote) accepted a rule
    the list then showed "Paused: delisted" and the loop skipped, and both report doors kept
    refusing a ticker that was trading. A fresh active quote now forgets the memo at once."""
    await _confirm_roku(listing_fmp, quotes)
    assert ls.peek_delisted("ROKU") is not None
    if how == "get_delisted":
        assert await ls.get_delisted("ROKU", quote=_active_roku()) is None
    elif how == "get_delisted_many":
        assert await ls.get_delisted_many({"ROKU": _active_roku()}) == {}
    else:
        assert await ls.get_delisted("ROKU", profile={**ROKU_PROFILE, "isActivelyTrading": True}) is None
    assert ls.peek_delisted("ROKU") is None
    # Every door that holds no quote now asks again — and the fresh quote decides "active".
    quotes.quotes["ROKU"] = _active_roku()
    assert await ls.get_delisted_for_symbols(["ROKU"]) == {}
    assert ls.peek_delisted("ROKU") is None


@pytest.mark.parametrize("kwargs", [
    {},                                                         # nothing in hand
    {"quote": {}},                                              # an empty quote
    {"quote": None, "profile": {}},                             # an empty profile
    {"profile": {"symbol": "ROKU", "price": 160.52}},           # a profile with no flag
    {"quote": _roku_quote()},                                   # still reads INACTIVE
])
@pytest.mark.asyncio
async def test_no_evidence_or_an_inactive_quote_keeps_the_confirmation(listing_fmp, quotes, kwargs):
    held = await _confirm_roku(listing_fmp, quotes)
    await ls.get_delisted("ROKU", **kwargs)
    assert ls.peek_delisted("ROKU") == held, "only a row that READS active is evidence"


@pytest.mark.asyncio
async def test_an_active_quote_for_another_symbol_never_touches_roku(listing_fmp, quotes):
    held = await _confirm_roku(listing_fmp, quotes)
    await ls.get_delisted_many({"AAPL": PriceService._from_profile(_profile("AAPL", active=True)),
                                "MSFT": "not a quote", None: {}})
    assert ls.peek_delisted("ROKU") == held
    await ls.get_delisted_many({"ROKU": "not a quote"})
    assert ls.peek_delisted("ROKU") == held


@pytest.mark.asyncio
async def test_after_an_active_quote_the_alert_doors_and_the_report_door_agree(
    monkeypatch, listing_fmp, quotes,
):
    """The create door's seed quote reads active → the list and the loop stop pausing ROKU and
    the report door generates: every surface agrees, none waits for the next registry publish."""
    await _confirm_roku(listing_fmp, quotes)
    quotes.quotes["ROKU"] = _active_roku()
    assert await ls.get_delisted("ROKU", quote=_active_roku(), wait=ls.PAID_DOOR_WAIT) is None
    assert ls.peek_delisted("ROKU") is None                       # the loop polls it again
    credit, service = _install_fakes(monkeypatch, cached=None)
    await tr.get_ticker_report("ROKU", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    service.generate_fresh_report.assert_awaited_once()


# ── GET /stocks/{t}/report ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_door_refuses_a_delisting_before_any_charge(monkeypatch, listing_fmp, quotes):
    _publish_current()
    credit, service = _install_fakes(monkeypatch, cached=None)
    resp = await tr.get_ticker_report("ROKU", "warren_buffett", user=USER)
    assert resp.status_code == 409
    body = _body(resp)
    assert body["error_code"] == "TICKER_DELISTED" and body["user_message"] == REPORT_COPY
    assert body["details"] == ROKU_DETAILS
    credit.precharge.assert_not_called()
    credit.refund_ledgered.assert_not_called()
    service.generate_fresh_report.assert_not_called()
    assert tr._INFLIGHT_REPORTS == 0, "never reached the admission gate"
    assert tr._USER_GENERATIONS == {}, "never reached the single-flight"


@pytest.mark.asyncio
async def test_report_door_still_serves_a_cached_report_free_for_a_delisting(
    monkeypatch, listing_fmp, quotes,
):
    await _confirm_roku(listing_fmp, quotes)
    credit, service = _install_fakes(monkeypatch, cached={"ok": True})
    checked: List[Any] = []

    async def _spy(*a, **k):
        checked.append(a)
        return {}

    monkeypatch.setattr(tr, "get_delisted_for_symbols", _spy)
    result = await tr.get_ticker_report("ROKU", "warren_buffett", user=USER)
    assert result == {"ok": True}
    assert checked == [], "a cache hit is served before the delisted check"
    credit.precharge.assert_not_called()
    service.generate_fresh_report.assert_not_called()


@pytest.mark.asyncio
async def test_report_door_proceeds_when_the_answer_is_unknown(monkeypatch, listing_fmp, quotes):
    """A cold registry (~2 min after a restart) cannot decide: charge and generate as before."""
    credit, service = _install_fakes(monkeypatch, cached=None)
    await tr.get_ticker_report("ROKU", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    service.generate_fresh_report.assert_awaited_once()
    assert quotes.calls == [] and listing_fmp.page_calls == []


@pytest.mark.asyncio
async def test_report_door_proceeds_when_the_check_times_out(monkeypatch, quotes):
    _publish_current()
    slow = _FMP(delay=0.1)
    monkeypatch.setattr(ls, "get_fmp_client", lambda: slow)
    monkeypatch.setattr(tr, "PAID_DOOR_WAIT", 0.05)
    credit, service = _install_fakes(monkeypatch, cached=None)
    await tr.get_ticker_report("ROKU", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    service.generate_fresh_report.assert_awaited_once()


@pytest.mark.asyncio
async def test_report_door_for_a_trading_ticker_adds_no_io(monkeypatch, listing_fmp, quotes):
    _publish_current()
    credit, service = _install_fakes(monkeypatch, cached=None)
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    assert quotes.calls == [] and listing_fmp.page_calls == [] and listing_fmp.eod_calls == []


def test_report_door_checks_after_every_free_path_and_before_registering_a_leader():
    """Order: both free caches → the FREE join of a running generation → the delisted check →
    the map re-read (after the check's await) → registering a new leader → the charge."""
    src = inspect.getsource(tr.get_ticker_report)
    check = src.index("await _refuse_if_delisted(")
    assert src.index("await get_cached_report(") < check
    assert src.index("await _check_legacy_report_cache(") < check
    first_read = src.index("_USER_GENERATIONS.get(key)")
    second_read = src.index("_USER_GENERATIONS.get(key)", first_read + 1)
    assert first_read < check < second_read, "join first (free), then refuse, then re-read"
    assert second_read < src.index("_USER_GENERATIONS[key] = fut")
    assert check < src.index("await _billable_generation(")
    # Nothing awaits between the re-read and the registration: the get and the set stay atomic.
    between = src[second_read:src.index("_USER_GENERATIONS[key] = fut")]
    assert between.count("await ") == 1 and "await _join_logged(" in between


@pytest.mark.asyncio
async def test_a_retry_joins_a_generation_its_failed_open_check_let_through(monkeypatch, quotes):
    """Review finding (2026-11-13): the leader's check could not decide within the wait, so it
    failed open, was charged 20 and started generating; the background resolution then
    confirmed the delisting. A same-user retry must JOIN that leader (free, its report) — not
    answer 409 "You haven't been charged" while the leader's charge stands. A NEW generation
    after the leader finished is still refused before any charge."""
    from tests.test_ticker_report_credits import _GatedService, _gated, _until

    _publish_current()
    slow = _FMP(delay=0.1)
    monkeypatch.setattr(ls, "get_fmp_client", lambda: slow)
    monkeypatch.setattr(tr, "PAID_DOOR_WAIT", 0.02)
    gate = asyncio.Event()
    credit = _gated(monkeypatch, gate)

    leader = asyncio.create_task(tr.get_ticker_report("ROKU", "warren_buffett", user=USER))
    await _until(lambda: _GatedService.calls == 1)
    assert credit.precharge.call_count == 1, "the undecided check failed open and charged"
    await _until(lambda: ls.peek_delisted("ROKU") is not None)

    follower = asyncio.create_task(tr.get_ticker_report("ROKU", "warren_buffett", user=USER))
    await asyncio.sleep(0.01)
    assert not follower.done(), "the retry answered (a 409?) instead of joining the leader"
    gate.set()
    a, b = await asyncio.wait_for(asyncio.gather(leader, follower), 2)
    assert a == b == {"ok": True, "t": "ROKU"} and a is not b
    assert credit.precharge.call_count == 1 and _GatedService.calls == 1
    credit.refund_ledgered.assert_not_called()
    assert tr._USER_GENERATIONS == {}

    fresh = await tr.get_ticker_report("ROKU", "warren_buffett", user=USER)
    assert fresh.status_code == 409 and _body(fresh)["error_code"] == "TICKER_DELISTED"
    assert credit.precharge.call_count == 1, "the refusal never charges"
    assert _GatedService.calls == 1


@pytest.mark.asyncio
async def test_a_leader_registered_during_the_check_is_joined_not_doubled(monkeypatch):
    """The map is re-read after the check's await: a leader that registered while this
    request's check ran is joined, never shadowed by a second charged generation."""
    from tests.test_ticker_report_credits import _GatedService, _gated, _until

    gate = asyncio.Event()
    credit = _gated(monkeypatch, gate)
    release = asyncio.Event()
    entered: List[int] = []

    async def _check(symbols, **kw):
        entered.append(len(entered))
        if len(entered) == 1:
            await release.wait()          # the FIRST request's check is slow …
        return {}                         # … the second's answers at once

    monkeypatch.setattr(tr, "get_delisted_for_symbols", _check)
    slow = asyncio.create_task(tr.get_ticker_report("AAPL", "warren_buffett", user=USER))
    await _until(lambda: len(entered) == 1)
    quick = asyncio.create_task(tr.get_ticker_report("AAPL", "warren_buffett", user=USER))
    await _until(lambda: _GatedService.calls == 1)       # `quick` registered and is generating
    release.set()                                        # `slow`'s check returns: it must join
    await asyncio.sleep(0.01)
    assert not slow.done(), "the slow request answered without the leader"
    gate.set()
    a, b = await asyncio.wait_for(asyncio.gather(slow, quick), 2)
    assert a == b and a is not b
    assert credit.precharge.call_count == 1, "the slow request was charged a second time"
    assert _GatedService.calls == 1 and tr._USER_GENERATIONS == {}


# ── POST /research/generate ───────────────────────────────────────────────────

def _supabase() -> MagicMock:
    query = MagicMock()
    for method in ("select", "eq", "in_", "gte", "insert"):
        getattr(query, method).return_value = query
    query.execute.return_value = MagicMock(count=0, data=[])
    sb = MagicMock()
    sb.table.return_value = query
    return sb


def _credit(monkeypatch, precharge_return=None):
    instance = MagicMock()
    instance.precharge.return_value = precharge_return
    cls = MagicMock(return_value=instance)
    cls.DEEP_RESEARCH_COST = 20
    monkeypatch.setattr(research, "CreditService", cls)
    return instance


@pytest.mark.asyncio
async def test_deep_door_refuses_a_delisting_before_the_cap_read_and_the_charge(
    monkeypatch, listing_fmp, quotes,
):
    _publish_current()
    credit = _credit(monkeypatch)
    sb = _supabase()
    resp = await research.generate_research_report(
        GenerateResearchRequest(stock_id="roku", investor_persona="warren_buffett"),
        user=USER, supabase=sb,
    )
    assert resp.status_code == 409
    body = _body(resp)
    assert body["error_code"] == "TICKER_DELISTED" and body["user_message"] == REPORT_COPY
    assert body["details"] == ROKU_DETAILS
    sb.table.assert_not_called()          # no cap read, no reuse lookup, no row
    credit.precharge.assert_not_called()
    credit.refund_ledgered.assert_not_called()


@pytest.mark.asyncio
async def test_deep_door_validates_the_persona_first(monkeypatch, listing_fmp, quotes):
    _publish_current()
    credit = _credit(monkeypatch)
    resp = await research.generate_research_report(
        GenerateResearchRequest(stock_id="ROKU", investor_persona="not_a_persona"),
        user=USER, supabase=_supabase(),
    )
    assert _body(resp)["error_code"] == "INVALID_PERSONA"
    assert quotes.calls == [], "a caller error costs no listing check"
    credit.precharge.assert_not_called()


@pytest.mark.asyncio
async def test_deep_door_proceeds_to_the_charge_when_unknown(monkeypatch, listing_fmp, quotes):
    credit = _credit(monkeypatch, precharge_return=None)   # insufficient → 402, after the charge
    resp = await research.generate_research_report(
        GenerateResearchRequest(stock_id="ROKU", investor_persona="warren_buffett"),
        user=USER, supabase=_supabase(),
    )
    credit.precharge.assert_called_once()
    assert _body(resp)["error_code"] == "INSUFFICIENT_CREDITS"


@pytest.mark.asyncio
async def test_deep_door_for_a_trading_ticker_adds_no_io(monkeypatch, listing_fmp, quotes):
    _publish_current()
    credit = _credit(monkeypatch, precharge_return=None)
    await research.generate_research_report(
        GenerateResearchRequest(stock_id="AAPL", investor_persona="warren_buffett"),
        user=USER, supabase=_supabase(),
    )
    credit.precharge.assert_called_once()
    assert quotes.calls == [] and listing_fmp.page_calls == []


def test_deep_door_checks_before_the_cap_read_and_the_precharge():
    src = inspect.getsource(research.generate_research_report)
    check = src.index("get_delisted_for_symbols(")
    assert src.index("if request.investor_persona not in PERSONA_KEYS") < check
    assert check < src.index('supabase.table("research_reports")')
    assert check < src.index("credit_service.precharge(")


# ── the report pre-warms ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prewarm_route_returns_early_for_a_held_delisting(monkeypatch, listing_fmp, quotes):
    await _confirm_roku(listing_fmp, quotes)
    monkeypatch.setattr(settings, "REPORT_PREWARM_ON_VIEW_ENABLED", True)
    monkeypatch.setattr(settings, "REPORT_PREWARM_MAX_INFLIGHT", 50)
    warmed: List[str] = []

    async def _warm(t):
        warmed.append(t)

    monkeypatch.setattr(stocks, "warm_ticker_collection", _warm)
    stocks._PREWARM_TASKS.clear()
    resp = await stocks.prewarm_report_collection(ticker="roku", user=USER, _rate_limit=None)
    assert resp.status_code == 202 and _body(resp)["status"] == "delisted"
    await asyncio.sleep(0)
    assert stocks._PREWARM_TASKS == set() and warmed == []


@pytest.mark.asyncio
async def test_prewarm_background_check_catches_a_delisting_not_yet_held(
    monkeypatch, listing_fmp, quotes,
):
    """The detail screen fires the pre-warm beside its first overview read, so the delisting
    is usually not confirmed yet when the route runs: the background warm checks first."""
    _publish_current()
    monkeypatch.setattr(settings, "REPORT_PREWARM_ON_VIEW_ENABLED", True)
    monkeypatch.setattr(settings, "REPORT_PREWARM_MAX_INFLIGHT", 50)
    warmed: List[str] = []

    async def _warm(t):
        warmed.append(t)

    monkeypatch.setattr(stocks, "warm_ticker_collection", _warm)
    stocks._PREWARM_TASKS.clear()
    resp = await stocks.prewarm_report_collection(ticker="ROKU", user=USER, _rate_limit=None)
    assert _body(resp)["status"] == "warming", "the route still answers at once"
    for _ in range(50):
        if not stocks._PREWARM_TASKS:
            break
        await asyncio.sleep(0.01)
    assert warmed == [], "no ~20-call warm for a confirmed delisting"
    assert ls.peek_delisted("ROKU") is not None


@pytest.mark.asyncio
async def test_prewarm_still_warms_a_trading_ticker_with_no_extra_io(monkeypatch, listing_fmp, quotes):
    _publish_current()
    monkeypatch.setattr(settings, "REPORT_PREWARM_ON_VIEW_ENABLED", True)
    monkeypatch.setattr(settings, "REPORT_PREWARM_MAX_INFLIGHT", 50)
    called = asyncio.Event()

    async def _warm(t):
        assert t == "AAPL"
        called.set()

    monkeypatch.setattr(stocks, "warm_ticker_collection", _warm)
    resp = await stocks.prewarm_report_collection(ticker="aapl", user=USER, _rate_limit=None)
    assert _body(resp)["status"] == "warming"
    await asyncio.wait_for(called.wait(), timeout=1)
    assert quotes.calls == [] and listing_fmp.page_calls == []


@pytest.mark.asyncio
async def test_the_report_prewarmer_skips_held_delistings_from_memory(listing_fmp, quotes, caplog):
    import app.main as main

    await _confirm_roku(listing_fmp, quotes)
    q_before, p_before = list(quotes.calls), list(listing_fmp.page_calls)
    with caplog.at_level(logging.INFO):
        kept = main._without_confirmed_delistings(["AAPL", "ROKU", "MSFT"], "Report pre-warm")
    assert kept == ["AAPL", "MSFT"]
    assert quotes.calls == q_before and listing_fmp.page_calls == p_before, "no call to decide"
    assert any("ROKU" in r.getMessage() and "Report pre-warm" in r.getMessage()
               for r in caplog.records)
    assert main._without_confirmed_delistings([], "x") == []


def test_the_report_prewarmer_filters_before_it_warms():
    import app.main as main

    tree = ast.parse(inspect.getsource(main._run_report_pre_warmer))
    awaited = [n.value for n in ast.walk(tree) if isinstance(n, ast.Await)]
    names = [getattr(c.func, "id", None) for c in awaited if isinstance(c, ast.Call)]
    assert "_without_delistings" in names, "the hourly loop must run the REAL check, awaited"
    src = inspect.getsource(main._run_report_pre_warmer)
    assert src.index("_without_delistings(") < src.index("warm_ticker_collection(t)")
    helper = ast.parse(inspect.getsource(main._without_delistings))
    called = [getattr(n.func, "id", None) for n in ast.walk(helper) if isinstance(n, ast.Call)]
    assert called.index("_without_confirmed_delistings") < called.index("get_delisted_for_symbols")


@pytest.mark.asyncio
async def test_the_report_prewarmer_confirms_a_delisting_nothing_on_screen_held(
    listing_fmp, quotes, caplog,
):
    """Review finding (2026-11-13): after a restart (or 6 h with no screen on it) the memory
    pass holds nothing, so a delisted top-watchlist ticker was warmed (~20 FMP calls a close).
    The hourly pass now checks it for real: free for the traded tickers, one quote for ROKU."""
    import app.main as main

    _publish_current(traded=("AAPL", "MSFT"))
    assert ls.peek_delisted("ROKU") is None
    with caplog.at_level(logging.INFO):
        kept = await main._without_delistings(["AAPL", "ROKU", "MSFT"], "Report pre-warm")
    assert kept == ["AAPL", "MSFT"]
    assert quotes.calls == ["ROKU"], "a ticker that traded costs no quote"
    assert ls.peek_delisted("ROKU") is not None
    assert any("ROKU" in r.getMessage() and "Report pre-warm" in r.getMessage()
               for r in caplog.records)
    q_before = list(quotes.calls)
    assert await main._without_delistings(["ROKU", "AAPL"], "Report pre-warm") == ["AAPL"]
    assert quotes.calls == q_before, "a held delisting is answered from memory"
    assert await main._without_delistings([], "Report pre-warm") == []


@pytest.mark.asyncio
async def test_the_report_prewarmer_matches_case_and_survives_junk(monkeypatch, listing_fmp, quotes):
    import app.main as main

    _publish_current(traded=("AAPL",))

    async def _found_roku(symbols, **kw):
        return {"ROKU": ls.DelistedListing("ROKU", date(2026, 11, 9), "NASDAQ", date(2026, 11, 6))}

    monkeypatch.setattr(ls, "get_delisted_for_symbols", _found_roku)
    kept = await main._without_delistings([" roku ", "AAPL", None, 7], "Report pre-warm")
    assert kept == ["AAPL", None, 7], "junk is left for the warm to refuse, never a crash"


@pytest.mark.asyncio
async def test_the_report_prewarmer_warms_what_it_cannot_decide(monkeypatch, listing_fmp, quotes):
    """Fail open: a cold registry (the 45 s first pass after boot), a slow check and a check
    that raises all warm every ticker, as before."""
    import app.main as main

    # Cold registry: unknown for all, no I/O.
    assert await main._without_delistings(["ROKU", "AAPL"], "Report pre-warm") == ["ROKU", "AAPL"]
    assert quotes.calls == [] and listing_fmp.page_calls == []

    _publish_current()
    slow = _Quotes(delay=0.2)
    monkeypatch.setattr(ls, "price_source", lambda *a, **k: slow)
    monkeypatch.setattr(ls, "PAID_DOOR_WAIT", 0.02)
    assert await main._without_delistings(["ROKU", "AAPL"], "Report pre-warm") == ["ROKU", "AAPL"]

    async def _boom(*a, **k):
        raise RuntimeError("belt")

    monkeypatch.setattr(ls, "get_delisted_for_symbols", _boom)
    assert await main._without_delistings(["ROKU"], "Report pre-warm") == ["ROKU"]


# ── price alerts: create ──────────────────────────────────────────────────────

class _SeedQuotes:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def get_quotes_list(self, symbols):
        self.calls += 1
        return [dict(r) for r in self.rows]


def _alert_service(monkeypatch, *, rows=None, update_row=None):
    svc = MagicMock()
    svc.create.side_effect = lambda user_id, **kw: {
        "id": "a1", "ticker": kw["ticker"], "kind": kw["kind"], "threshold": kw["threshold"],
        "last_price": kw.get("seed_price"),
    }
    svc.list_for_user.return_value = rows or []
    svc.update.return_value = update_row
    monkeypatch.setattr(pa, "get_price_alert_service", lambda: svc)
    return svc


@pytest.mark.asyncio
async def test_a_new_alert_on_a_delisting_is_refused_and_nothing_is_created(
    monkeypatch, listing_fmp, quotes,
):
    _publish_current()
    monkeypatch.setattr(pa, "price_source", lambda *a, **k: _SeedQuotes([_roku_quote()]))
    svc = _alert_service(monkeypatch)
    resp = await pa.create_price_alert(
        CreatePriceAlertRequest(ticker="ROKU", kind="price_above", threshold=200.0), user=USER,
    )
    assert resp.status_code == 409
    body = _body(resp)
    assert body["error_code"] == "TICKER_DELISTED" and body["user_message"] == ALERT_COPY
    assert body["details"] == ROKU_DETAILS and body["action"] is None
    svc.create.assert_not_called()


@pytest.mark.asyncio
async def test_with_no_seed_quote_only_a_held_delisting_refuses(monkeypatch, listing_fmp, quotes):
    monkeypatch.setattr(pa, "price_source", lambda *a, **k: _SeedQuotes([]))
    svc = _alert_service(monkeypatch)
    req = lambda: CreatePriceAlertRequest(ticker="ROKU", kind="price_above", threshold=200.0)  # noqa: E731
    # Not held → created as before (fail open), with no extra I/O.
    _publish_current()
    created = await pa.create_price_alert(req(), user=USER)
    assert isinstance(created, PriceAlertResponse) and created.paused_reason is None
    assert quotes.calls == []
    # Held (a screen confirmed it) → refused from memory.
    await _confirm_roku(listing_fmp, quotes)
    svc.create.reset_mock()
    resp = await pa.create_price_alert(req(), user=USER)
    assert _body(resp)["error_code"] == "TICKER_DELISTED"
    svc.create.assert_not_called()


@pytest.mark.asyncio
async def test_an_alert_is_created_as_before_when_the_answer_is_unknown(
    monkeypatch, listing_fmp, quotes,
):
    """Cold registry: the inactive seed quote alone never refuses."""
    monkeypatch.setattr(pa, "price_source", lambda *a, **k: _SeedQuotes([_roku_quote()]))
    svc = _alert_service(monkeypatch)
    out = await pa.create_price_alert(
        CreatePriceAlertRequest(ticker="ROKU", kind="price_above", threshold=200.0), user=USER,
    )
    assert isinstance(out, PriceAlertResponse) and out.last_price == 160.52
    svc.create.assert_called_once()
    assert listing_fmp.page_calls == []


@pytest.mark.asyncio
async def test_a_trading_ticker_alert_costs_no_listing_io(monkeypatch, listing_fmp, quotes):
    _publish_current()
    aapl = PriceService._from_profile(_profile("AAPL", active=True, price=250.0))
    monkeypatch.setattr(pa, "price_source", lambda *a, **k: _SeedQuotes([aapl]))
    svc = _alert_service(monkeypatch)
    await pa.create_price_alert(
        CreatePriceAlertRequest(ticker="AAPL", kind="price_above", threshold=300.0), user=USER,
    )
    svc.create.assert_called_once()
    assert quotes.calls == [] and listing_fmp.page_calls == []


# ── price alerts: list / update ───────────────────────────────────────────────

ROWS = [
    {"id": "r1", "ticker": "ROKU", "kind": "price_above", "threshold": "200", "is_active": True},
    {"id": "r2", "ticker": "aapl", "kind": "price_below", "threshold": 100, "is_active": True},
]


@pytest.mark.asyncio
async def test_the_list_marks_a_delisted_rule_paused_and_leaves_the_rest(
    monkeypatch, listing_fmp, quotes,
):
    _publish_current()
    _alert_service(monkeypatch, rows=ROWS)
    resp = await pa.list_price_alerts(ticker=None, user=USER)
    by_id = {i.id: i for i in resp.items}
    roku = by_id["r1"].model_dump()
    assert {k: roku[k] for k in LISTING_FIELDS} == ROKU_WIRE
    assert roku["paused_reason"] == "delisted"
    aapl = by_id["r2"].model_dump()
    assert all(aapl[k] is None for k in (*LISTING_FIELDS, "paused_reason"))
    assert quotes.calls == ["ROKU"], "AAPL traded: no read for it"


@pytest.mark.asyncio
async def test_the_list_reads_as_before_when_the_answer_is_unknown(monkeypatch, listing_fmp, quotes):
    _alert_service(monkeypatch, rows=ROWS)                     # cold registry
    resp = await pa.list_price_alerts(ticker=None, user=USER)
    for item in resp.items:
        assert item.paused_reason is None and item.listing_status is None
    assert quotes.calls == [] and listing_fmp.page_calls == []


@pytest.mark.asyncio
async def test_an_edited_rule_keeps_its_paused_caption(monkeypatch, listing_fmp, quotes):
    await _confirm_roku(listing_fmp, quotes)
    _alert_service(monkeypatch, update_row=dict(ROWS[0], note="watch"))
    out = await pa.update_price_alert("r1", UpdatePriceAlertRequest(note="watch"), user=USER)
    assert out.paused_reason == "delisted" and out.delisted_date == "2026-11-09"


# ── PriceAlertResponse: the contract iOS decodes ──────────────────────────────

def test_the_alert_response_appends_the_four_fields_then_paused_reason_last():
    import typing

    fields = PriceAlertResponse.model_fields
    assert list(fields)[-5:] == [*LISTING_FIELDS, "paused_reason"]
    for name in (*LISTING_FIELDS, "paused_reason"):
        info = fields[name]
        assert not info.is_required() and info.default is None
        assert type(None) in set(typing.get_args(info.annotation))
    for name in ("listing_status", "paused_reason"):
        literal = next(a for a in typing.get_args(fields[name].annotation) if a is not type(None))
        assert typing.get_args(literal) == ("delisted",)


@pytest.mark.parametrize("listing", [
    None, {}, "delisted", 7, {"listing_status": "suspended", "delisted_date": "2026-11-09"},
    {"listing_status": None, "delisted_date": "2026-11-09"},
])
def test_anything_but_a_delisted_wire_leaves_the_row_unpaused(listing):
    dto = price_alert_from_row({"id": "x", "ticker": "AAPL", "threshold": 1}, listing=listing)
    PriceAlertResponse.model_validate(dto.model_dump())
    assert all(getattr(dto, k) is None for k in (*LISTING_FIELDS, "paused_reason"))


def test_a_wire_with_junk_values_still_validates():
    wire = {"listing_status": "delisted", "delisted_date": "2026-11-09",
            "delisted_exchange": 42, "last_trade_date": ""}
    dto = price_alert_from_row({"id": "x", "ticker": "ROKU", "threshold": "1"}, listing=wire)
    PriceAlertResponse.model_validate(dto.model_dump())
    assert dto.paused_reason == "delisted" and dto.delisted_exchange is None
    assert dto.last_trade_date is None


def test_the_worst_case_row_with_a_delisting_validates():
    row = {k: None for k in ("asset_type", "kind", "repeat_mode", "is_active", "armed",
                              "last_price", "last_triggered_at", "trigger_count", "note",
                              "created_at")}
    row.update(id="x", ticker="roku", threshold="n/a")
    dto = price_alert_from_row(row, listing=ROKU_WIRE)
    PriceAlertResponse.model_validate(dto.model_dump())
    assert dto.ticker == "ROKU" and dto.paused_reason == "delisted"


def test_an_unknown_paused_reason_is_refused_at_the_boundary():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PriceAlertResponse.model_validate({"id": "x", "ticker": "X", "kind": "price_above",
                                           "threshold": 1.0, "paused_reason": "halted"})


# ── price alerts: the evaluation loop ─────────────────────────────────────────

class _BatchQuotes:
    def __init__(self, quotes):
        self.quotes = quotes
        self.asked: List[List[str]] = []

    async def get_quotes_list(self, tickers):
        self.asked.append(list(tickers))
        return [dict(self.quotes[t]) for t in tickers if t in self.quotes]


def _rule(rid, ticker, threshold=1000.0):
    return {"id": rid, "user_id": "u1", "ticker": ticker, "asset_type": "stock",
            "kind": "price_above", "threshold": threshold, "repeat_mode": "once",
            "armed": True, "last_price": 150.0, "trigger_count": 0}


async def _evaluate(batch, rules, persisted):
    svc = PriceAlertService()
    svc.price = batch
    tickers = list(dict.fromkeys(r["ticker"] for r in rules))
    with patch.object(PriceAlertService, "_active_universe", lambda self, *a, **k: list(tickers)), \
         patch.object(PriceAlertService, "_active_rules", lambda self, t: [dict(r) for r in rules]), \
         patch.object(PriceAlertService, "_persist",
                      lambda self, rule, decision: persisted.append(rule["id"])):
        return svc, await svc.evaluate_once()


@pytest.mark.asyncio
async def test_a_paused_rule_is_neither_quoted_nor_persisted(listing_fmp, quotes):
    await _confirm_roku(listing_fmp, quotes)
    page_calls = len(listing_fmp.page_calls)
    aapl = PriceService._from_profile(_profile("AAPL", active=True, price=150.0))
    batch = _BatchQuotes({"AAPL": aapl, "ROKU": _roku_quote()})
    persisted: List[str] = []
    _, stats = await _evaluate(batch, [_rule("r1", "ROKU"), _rule("r2", "AAPL"),
                                       _rule("r3", "ROKU")], persisted)
    assert batch.asked == [["AAPL"]], "ROKU is not quoted"
    assert persisted == ["r2"], "no state write for a paused rule"
    assert stats["paused"] == 2 and stats["rules"] == 3 and stats["tickers"] == 2
    assert len(listing_fmp.page_calls) == page_calls, "no FMP call to decide"


@pytest.mark.asyncio
async def test_all_paused_spends_no_quote_call(listing_fmp, quotes):
    await _confirm_roku(listing_fmp, quotes)
    batch = _BatchQuotes({"ROKU": _roku_quote()})
    persisted: List[str] = []
    _, stats = await _evaluate(batch, [_rule("r1", "ROKU")], persisted)
    assert batch.asked == [] and persisted == [] and stats["paused"] == 1


@pytest.mark.asyncio
async def test_a_paused_ticker_that_trades_again_resumes_by_itself(listing_fmp, quotes, caplog):
    await _confirm_roku(listing_fmp, quotes)
    batch = _BatchQuotes({"ROKU": _roku_quote()})
    persisted: List[str] = []
    svc, stats = await _evaluate(batch, [_rule("r1", "ROKU")], persisted)
    assert stats["paused"] == 1
    # The next session prices ROKU (a relisting, or a reused symbol): the registry says True.
    _publish_current(traded=("AAPL", "OBK", "ROKU"))
    traded = dict(_roku_quote(), isActivelyTrading=True)
    batch2 = _BatchQuotes({"ROKU": traded})
    svc.price = batch2
    with caplog.at_level(logging.INFO), \
         patch.object(PriceAlertService, "_active_universe", lambda self, *a, **k: ["ROKU"]), \
         patch.object(PriceAlertService, "_active_rules", lambda self, t: [_rule("r1", "ROKU")]), \
         patch.object(PriceAlertService, "_persist",
                      lambda self, rule, decision: persisted.append(rule["id"])):
        stats2 = await svc.evaluate_once()
    assert batch2.asked == [["ROKU"]] and persisted == ["r1"] and stats2["paused"] == 0
    assert any("ROKU no longer reads as a confirmed delisting" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_the_loop_learns_a_delisting_in_the_background_then_pauses(listing_fmp, quotes):
    """First cycle: not confirmed yet → evaluated exactly as before (wait=0, no blocking);
    the lookup runs in the background; the next cycle pauses the rule."""
    _publish_current()
    batch = _BatchQuotes({"ROKU": _roku_quote()})
    persisted: List[str] = []
    svc, stats = await _evaluate(batch, [_rule("r1", "ROKU")], persisted)
    assert stats["paused"] == 0 and persisted == ["r1"] and batch.asked == [["ROKU"]]
    await _settle()
    assert ls.peek_delisted("ROKU") is not None
    persisted.clear()
    _, stats2 = await _evaluate(batch, [_rule("r1", "ROKU")], persisted)
    assert stats2["paused"] == 1 and persisted == [] and batch.asked == [["ROKU"]]


@pytest.mark.asyncio
async def test_pause_transitions_log_once_not_every_cycle(listing_fmp, quotes, caplog):
    await _confirm_roku(listing_fmp, quotes)
    svc = PriceAlertService()
    svc.price = _BatchQuotes({})
    with caplog.at_level(logging.INFO), \
         patch.object(PriceAlertService, "_active_universe", lambda self, *a, **k: ["ROKU"]), \
         patch.object(PriceAlertService, "_active_rules", lambda self, t: [_rule("r1", "ROKU")]), \
         patch.object(PriceAlertService, "_persist", lambda self, rule, decision: None):
        for _ in range(3):
            await svc.evaluate_once()
    pauses = [r for r in caplog.records if "pausing the rules on ROKU" in r.getMessage()]
    assert len(pauses) == 1


@pytest.mark.asyncio
async def test_a_crypto_only_cycle_never_reads_as_a_resume(listing_fmp, quotes, caplog):
    await _confirm_roku(listing_fmp, quotes)
    svc = PriceAlertService()
    svc.price = _BatchQuotes({})
    svc._note_paused(["ROKU"], {"ROKU": ls.peek_delisted("ROKU")})
    with caplog.at_level(logging.INFO):
        svc._note_paused(["BTCUSD"], {})
    assert not [r for r in caplog.records if "no longer reads" in r.getMessage()]
    assert "ROKU" in svc._paused_tickers
