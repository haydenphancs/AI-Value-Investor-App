"""`session_pricing` — "FMP priced this symbol in the latest US session" — and its readers.

FMP's activity flag can say "inactive" for a security that trades (measured 2026-10-08:
SPAC units such as ARTCU, OTC ordinaries such as AHEXF, the ETF SHUS), and a listing that
moved exchange (ET, DDS, USAC → Texas Stock Exchange, 2026-10-05) drops off it entirely.
The registry is the evidence that overrules the flag, and it may only ever ADD liveness:
cold, stale or truncated, every reader falls back to the flag.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import pytest

import app.services.price_service as ps_module
from app.services import session_pricing as sp
from app.services import stock_search_service as search
from app.services.price_service import PriceService, profile_change_is_current
from app.schemas.stock import StockSearchResult
from app.utils.market_hours import previous_trading_day, session_trading_date

ET_TZ = ZoneInfo("America/New_York")
# Friday 2026-10-09, 10:00 ET: the session is 10-09, so the latest settled one is 10-08.
NOW = datetime(2026, 10, 9, 10, 0, tzinfo=ET_TZ)


@pytest.fixture(autouse=True)
def _cold_registry():
    sp._reset_for_tests()
    yield
    sp._reset_for_tests()


def _rows(extra: List[Dict[str, Any]], n: int = sp._MIN_PRICED_SYMBOLS) -> List[Dict[str, Any]]:
    """A full-size session: `n` traded filler symbols plus `extra`."""
    filler = [{"symbol": f"Z{i:05d}", "close": 10.0, "volume": 100} for i in range(n)]
    return filler + extra


def _current_session() -> date:
    """The session a publish must carry to be current on the REAL clock (readers that take
    no `now` — `profile_change_is_current`, search — read it)."""
    return previous_trading_day(session_trading_date())


# ── publish ───────────────────────────────────────────────────────────────────

def test_publish_counts_only_traded_licensed_symbols():
    n = sp.publish_priced_session("2026-10-08", _rows([
        {"symbol": " arTCu ", "close": 10.15, "volume": 403},        # normalised
        {"symbol": "ACGCU", "close": 11, "volume": 0},               # no trade: not proof
        {"symbol": "NOVOL", "close": 5.0},                           # volume missing
        {"symbol": "ZEROC", "close": 0, "volume": 10},
        {"symbol": "NANC", "close": float("nan"), "volume": 10},
        {"symbol": "INFC", "close": float("inf"), "volume": 10},
        {"symbol": "STRV", "close": "abc", "volume": 10},
        {"symbol": "^GSPC", "close": 5000, "volume": 10},            # outside the licence
        {"symbol": "BTCUSD", "close": 60000, "volume": 10},
        {"symbol": None, "close": 1, "volume": 1},
        "not-a-row",
    ]))
    assert n == sp._MIN_PRICED_SYMBOLS + 1
    assert sp.priced_in_latest_session("ARTCU", NOW) is True
    for sym in ("ACGCU", "NOVOL", "ZEROC", "NANC", "INFC", "STRV", "^GSPC", "BTCUSD"):
        assert sp.priced_in_latest_session(sym, NOW) is False, sym


def test_a_truncated_payload_is_refused_and_the_held_session_kept():
    assert sp.publish_priced_session("2026-10-07", _rows([{"symbol": "ET", "close": 20, "volume": 9}]))
    assert sp.publish_priced_session("2026-10-08", _rows([], n=sp._MIN_PRICED_SYMBOLS - 1)) is None
    held = sp.latest_priced_session(datetime(2026, 10, 8, 12, 0, tzinfo=ET_TZ))
    assert held is not None and held[0] == date(2026, 10, 7)


def test_an_older_session_never_rolls_the_registry_back():
    sp.publish_priced_session("2026-10-08", _rows([{"symbol": "NEW", "close": 1, "volume": 1}]))
    assert sp.publish_priced_session("2026-10-02", _rows([{"symbol": "OLD", "close": 1, "volume": 1}])) is None
    assert sp.priced_in_latest_session("NEW", NOW) is True
    assert sp.priced_in_latest_session("OLD", NOW) is False


@pytest.mark.parametrize("bad", ["", "not-a-date", None, "10/08/2026"])
def test_an_unreadable_session_date_is_not_published(bad):
    assert sp.publish_priced_session(bad, _rows([])) is None
    assert sp.latest_priced_session(NOW) is None


# ── read ──────────────────────────────────────────────────────────────────────

def test_cold_registry_answers_none():
    assert sp.priced_in_latest_session("ET", NOW) is None


def test_a_stale_registry_answers_none_not_false():
    """A registry older than the latest settled session cannot say a symbol is unpriced
    NOW — a failing ingest must not read as "nothing trades"."""
    sp.publish_priced_session("2026-10-07", _rows([{"symbol": "AAPL", "close": 1, "volume": 1}]))
    assert sp.priced_in_latest_session("AAPL", NOW) is None
    assert sp.latest_priced_session(NOW) is None
    # …but the same session is current on the day after it.
    assert sp.priced_in_latest_session("AAPL", datetime(2026, 10, 8, 10, 0, tzinfo=ET_TZ)) is True


@pytest.mark.parametrize("bad", [None, "", "   ", 42, ["ET"]])
def test_a_non_symbol_answers_none(bad):
    sp.publish_priced_session("2026-10-08", _rows([]))
    assert sp.priced_in_latest_session(bad, NOW) is None


# ── the profile-change rule ───────────────────────────────────────────────────

ET_INACTIVE = {"symbol": "ET", "price": 20.72, "change": 0.25, "changePercentage": 1.2213,
               "isActivelyTrading": False}
DDS_FROZEN = {"symbol": "DDS", "price": 695.35, "change": 41.24, "changePercentage": 6.30475,
              "isActivelyTrading": False}
# Friday 10-09 during the session (the registry can only hold 10-08 until ~20:00 ET), and
# the same evening once the 10-09 ingest has landed.
DURING = datetime(2026, 10, 9, 11, 0, tzinfo=ET_TZ)
EVENING = datetime(2026, 10, 9, 21, 0, tzinfo=ET_TZ)


def test_a_listing_that_stopped_today_keeps_an_unknown_change():
    """THE review finding (2026-10-08): DDS traded on 10-08 and FMP stopped pricing it on
    10-09. All of 10-09 the registry still holds 10-08, where DDS traded — but its profile
    price sits exactly on that close, so its frozen move must not read as 10-09's."""
    sp.publish_priced_session("2026-10-08", _rows([{"symbol": "DDS", "close": 695.35, "volume": 90514}]))
    assert sp.priced_in_latest_session("DDS", DURING) is True        # liveness: yes
    assert profile_change_is_current(DDS_FROZEN, DURING) is False      # its change: no
    assert sp.change_is_current("DDS", 695.35, DURING) is False


def test_a_flag_wrong_symbol_that_trades_today_has_a_current_change():
    """The self-healing half: FMP resumes ET's prices but keeps the flag false. During the
    session its price has moved off the stored close, so the change is today's."""
    sp.publish_priced_session("2026-10-08", _rows([{"symbol": "ET", "close": 20.50, "volume": 5_000_000}]))
    assert profile_change_is_current(ET_INACTIVE, DURING) is True
    assert profile_change_is_current(ET_INACTIVE) is (sp.change_is_current("ET", 20.72) is True)


@pytest.mark.parametrize("price, current", [
    (695.35, False),           # exactly on the stored close
    (695.354, False),          # inside $0.005
    (695.35 + 0.06, False),    # inside 0.01% of the close (0.0695)
    (695.35 + 0.08, True),     # off it: it traded today
    (650.00, True),
    (None, False), ("abc", False), (0, False), (-5, False), (float("nan"), False), (True, False),
])
def test_the_moved_off_the_close_test(price, current):
    sp.publish_priced_session("2026-10-08", _rows([{"symbol": "DDS", "close": 695.35, "volume": 1}]))
    assert sp.change_is_current("DDS", price, DURING) is current


def test_once_the_current_session_is_held_having_traded_is_enough():
    sp.publish_priced_session("2026-10-09", _rows([{"symbol": "ET", "close": 20.72, "volume": 5_000_000}]))
    assert profile_change_is_current(ET_INACTIVE, EVENING) is True     # price == close, still current
    q = PriceService._from_profile(dict(ET_INACTIVE))
    assert q["price"] == 20.72


def test_an_inactive_profile_stays_unknown_when_the_symbol_did_not_trade():
    sp.publish_priced_session("2026-10-08", _rows([]))
    assert profile_change_is_current(ET_INACTIVE, DURING) is False
    assert sp.change_is_current("ET", 25.0, DURING) is False


def test_an_inactive_profile_stays_unknown_on_a_cold_or_stale_registry():
    assert profile_change_is_current(ET_INACTIVE, DURING) is False                 # cold
    sp.publish_priced_session("2026-10-07", _rows([{"symbol": "ET", "close": 20.0, "volume": 1}]))
    assert sp.change_is_current("ET", 20.72, DURING) is None                       # stale
    assert profile_change_is_current(ET_INACTIVE, DURING) is False


def test_from_profile_drops_the_frozen_change_on_the_real_clock():
    """`_from_profile` reads the real clock; whatever day it is, a symbol absent from the
    registry keeps an unknown change."""
    sp.publish_priced_session(_current_session(), _rows([]))
    q = PriceService._from_profile(dict(DDS_FROZEN))
    assert q["price"] == 695.35 and q["changePercentage"] is None


def test_an_active_profile_never_consults_the_registry(monkeypatch):
    called = []
    monkeypatch.setattr(ps_module, "change_is_current", lambda *a, **k: called.append(a))
    assert profile_change_is_current({"symbol": "AAPL", "isActivelyTrading": True}) is True
    assert profile_change_is_current({"symbol": "AAPL"}) is True
    assert called == []


# ── search liveness ───────────────────────────────────────────────────────────

def _res(sym: str, name: str, ex: str = "NYSE") -> StockSearchResult:
    return StockSearchResult(symbol=sym, name=name, currency="USD",
                             exchange_short_name=ex, exchange_full_name=ex, type="stock")


def test_search_hides_an_unpriced_listing_and_revives_it_once_it_trades():
    rows = [_res("ET", "Energy Transfer LP"), _res("ETE", "Some Other Co")]
    directory = {"ETE": "Some Other Co", "AAPL": "Apple Inc."}
    # Cold registry: the list alone decides, as before.
    assert [r.symbol for r in search.refine_listings(rows, "ENERGY", directory)] == ["ETE"]
    assert search.would_keep(rows[0], "ENERGY", directory) is False
    # A session where ET did NOT trade: still hidden.
    sp.publish_priced_session(_current_session(), _rows([]))
    assert [r.symbol for r in search.refine_listings(rows, "ENERGY", directory)] == ["ETE"]
    # The first session FMP prices ET again: back, whatever the list says.
    sp._reset_for_tests()
    sp.publish_priced_session(_current_session(), _rows([{"symbol": "ET", "close": 21, "volume": 7_000_000}]))
    assert sorted(r.symbol for r in search.refine_listings(rows, "ENERGY", directory)) == ["ET", "ETE"]
    assert search.would_keep(rows[0], "ENERGY", directory) is True


def test_a_zero_volume_print_does_not_revive_a_search_row():
    sp.publish_priced_session(_current_session(), _rows([{"symbol": "FERAU", "close": 10.76, "volume": 0}]))
    rows = [_res("FERAU", "Fifth Era Acquisition Corp I Units", "NASDAQ")]
    assert search.refine_listings(rows, "FIFTH", {"AAPL": "Apple Inc."}) == []


def test_a_revived_base_hides_only_an_off_list_twin():
    """ETPC ("Energy Transfer LP", same issuer) is a root twin of ET. A base revived only by
    the registry may hide an OFF-list twin, never a listed one: ETPC on the list stays
    beside a revived ET; an off-list ETPC collapses onto it."""
    rows = [_res("ET", "Energy Transfer LP"), _res("ETPC", "Energy Transfer LP")]
    sp.publish_priced_session(_current_session(), _rows([
        {"symbol": "ET", "close": 21, "volume": 7_000_000},
        {"symbol": "ETPC", "close": 25, "volume": 1_000},
    ]))
    listed = {"ETPC": "Energy Transfer LP"}
    assert sorted(r.symbol for r in search.refine_listings(rows, "ENERGY TRANSFER", listed)) == ["ET", "ETPC"]
    off_list = {"AAPL": "Apple Inc."}
    assert [r.symbol for r in search.refine_listings(rows, "ENERGY TRANSFER", off_list)] == ["ET"]


def test_a_registry_only_base_never_hides_a_listed_row():
    """Rename day (review 2026-10-08): the registry still holds yesterday, when the dead
    old ticker JBT traded, while the list already carries JBTM. JBT must not become the
    live base that hides JBTM as its "twin" (the JBT/JBTM bug)."""
    rows = [_res("JBT", "JBT Marel Corporation"), _res("JBTM", "JBT Marel Corporation")]
    directory = {"JBTM": "JBT Marel Corporation"}
    sp.publish_priced_session(_current_session(), _rows([{"symbol": "JBT", "close": 120.0, "volume": 900}]))
    out = [r.symbol for r in search.refine_listings(rows, "JBT MAREL", directory)]
    assert "JBTM" in out
    out_exact = [r.symbol for r in search.refine_listings(rows, "JBT", directory)]
    assert out_exact[0] == "JBT" and "JBTM" in out_exact
    assert search.would_keep(rows[1], "JBT MAREL", directory) is True


def test_the_registry_is_not_read_for_a_listed_symbol(monkeypatch):
    calls = []
    monkeypatch.setattr(search, "priced_in_latest_session", lambda s: calls.append(s) or None)
    assert search._is_live("AAPL", {"AAPL": "Apple Inc."}) is True
    assert calls == []
    assert search._is_live("ET", {"AAPL": "Apple Inc."}) is False
    assert calls == ["ET"]


# ── the ingest feeds it ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_close_ingest_publishes_before_its_abort_paths(monkeypatch):
    """Published from the LATEST session even when the prior session then fails and the
    write is skipped: a missing prior close does not make today's prices less real."""
    session = _current_session().isoformat()
    latest = _rows([{"symbol": "ET", "close": 21, "volume": 7_000_000, "date": session}])
    calls = iter([(session, latest), (session, None)])

    async def fake_fetch(self, start_date=None):
        return next(calls)

    monkeypatch.setattr(PriceService, "_fetch_latest_session", fake_fetch)
    monkeypatch.setattr(ps_module, "_record_failed_fetch_cycle", lambda *a, **k: None)
    assert await PriceService().refresh_close_snapshot() == 0
    assert sp.priced_in_latest_session("ET") is True


@pytest.mark.asyncio
async def test_a_registry_failure_never_costs_the_ingest(monkeypatch, caplog):
    session = _current_session().isoformat()
    calls = iter([(session, _rows([])), (session, None)])

    async def fake_fetch(self, start_date=None):
        return next(calls)

    def boom(*a, **k):
        raise RuntimeError("registry bug")

    monkeypatch.setattr(PriceService, "_fetch_latest_session", fake_fetch)
    monkeypatch.setattr(ps_module, "_record_failed_fetch_cycle", lambda *a, **k: None)
    monkeypatch.setattr(ps_module, "publish_priced_session", boom)
    with caplog.at_level("WARNING"):
        assert await PriceService().refresh_close_snapshot() == 0   # reached the prior-session step
    assert any("priced-session registry not updated" in r.getMessage() for r in caplog.records)
