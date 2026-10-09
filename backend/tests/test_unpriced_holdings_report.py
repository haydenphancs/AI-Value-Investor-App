"""`unpriced_holdings_service` — tell the owner when FMP stops pricing a held symbol.

The case behind it: ET, DDS and USAC moved NYSE → Texas Stock Exchange on 2026-10-05; FMP
has no TXSE prices, users' prices froze, and nobody noticed for three sessions. The report
must raise ONE ERROR per stopped holding (with a renamed ticker's hint) once it has missed
two sessions, repeat older ones as a WARNING, stay silent about what is merely illiquid,
crypto, an index or foreign — and never spend a session on a failed or premature read
(adversarial review, 2026-10-08).
"""

from __future__ import annotations

import ast
import inspect
import logging
from datetime import date, datetime
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import pytest

import app.services.stock_search_service as search_module
from app.services import session_pricing as sp
from app.services import unpriced_holdings_service as uh
from app.services.price_service import PriceService

ET_TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 9, 10, 0, tzinfo=ET_TZ)      # the latest settled session: 10-08
SESSION = date(2026, 10, 8)


def _session_rows(traded: List[str]) -> List[Dict[str, Any]]:
    filler = [{"symbol": f"Z{i:05d}", "close": 1.0, "volume": 1} for i in range(sp._MIN_PRICED_SYMBOLS)]
    return filler + [{"symbol": s, "close": 10.0, "volume": 1000} for s in traded]


class _FMP:
    def __init__(self, profiles=None, ciks=None, fail=()):
        self.profiles, self.ciks, self.fail = profiles or {}, ciks or {}, set(fail)
        self.profile_calls: List[str] = []

    async def get_company_profile(self, sym):
        self.profile_calls.append(sym)
        if sym in self.fail:
            raise RuntimeError("upstream down")
        return dict(self.profiles.get(sym, {"isActivelyTrading": False, "exchange": "NYSE"}))

    async def _make_request(self, endpoint, params=None):
        assert endpoint == "search-cik"
        return [{"symbol": s} for s in self.ciks.get(params["cik"], [])]


@pytest.fixture(autouse=True)
def _fresh():
    sp._reset_for_tests()
    uh._reset_for_tests()
    yield
    sp._reset_for_tests()
    uh._reset_for_tests()


def _install(monkeypatch, *, held, directory, stored, fmp=None):
    """`stored` = {symbol: trade_date}; served through the REAL `_stored_closes` →
    `PriceService._select_closes` path, so a failed read surfaces as it would in prod."""
    monkeypatch.setattr(uh, "_held_symbols", lambda: held)
    monkeypatch.setattr(search_module, "get_fresh_active_listings", lambda: directory)
    rows = [{"symbol": s, "close": 1.0, "previous_close": 1.0, "trade_date": d} for s, d in stored.items()]
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(
        lambda symbols: [r for r in rows if r["symbol"] in set(symbols)]))
    fmp = fmp or _FMP()
    monkeypatch.setattr(uh, "get_fmp_client", lambda: fmp)
    return fmp


HELD = {
    "ET": {"watchlist_items": 12, "portfolio_items": 3},   # stopped 2 sessions ago → ERROR
    "DDS": {"watchlist_items": 2, "price_alerts": 1},      # stopped long ago → WARNING
    "USAC": {"watchlist_items": 1},                        # missed ONE session → pending
    "ZPRT": {"watchlist_items": 1},                        # printed today without trading → skipped
    "ACTV": {"watchlist_items": 1},                        # profile still active → WARNING
    "NOCL": {"watchlist_items": 1},                        # no stored close at all → WARNING
    "AAPL": {"watchlist_items": 40},                       # trades
    "CASBF": {"watchlist_items": 1},                       # illiquid but on the list
    "BTCUSD": {"watchlist_items": 5}, "^GSPC": {"price_alerts": 1}, "SHOP.TO": {"watchlist_items": 1},
}
DIRECTORY = {"AAPL": "Apple Inc.", "CASBF": "Casino Guichard", "ETX": "Energy Transfer LP"}
STORED = {
    "AAPL": "2026-10-08",       # the bellwether: the 10-08 write has landed
    "ET": "2026-10-06", "DDS": "2026-09-28", "USAC": "2026-10-07", "ZPRT": "2026-10-08",
    "ACTV": "2026-10-06",
}
PROFILES = {
    "ET": {"exchange": "NYSE", "isActivelyTrading": False, "cik": "0001276187"},
    "ACTV": {"exchange": "NASDAQ", "isActivelyTrading": True},
}
CIKS = {"0001276187": ["ET", "ET-PC", "ETX"]}


def _levels(caplog, level):
    return [r.getMessage() for r in caplog.records if r.levelno == level and r.name == uh.logger.name]


@pytest.mark.asyncio
async def test_the_report_classifies_every_held_symbol(monkeypatch, caplog):
    sp.publish_priced_session(SESSION, _session_rows(["AAPL", "ETX"]))
    fmp = _install(monkeypatch, held=HELD, directory=DIRECTORY, stored=STORED, fmp=_FMP(PROFILES, CIKS))
    with caplog.at_level(logging.INFO):
        report = await uh.report_unpriced_holdings(NOW)

    assert list(report["newly"]) == ["ET"]
    assert report["newly"]["ET"]["same_cik_trading"] == ["ETX"], "the renamed ticker's hint"
    assert sorted(report["still"]) == ["ACTV", "DDS", "NOCL"]
    assert report["still"]["ACTV"]["note"] == "profile still says actively trading"
    assert report["pending"] == ["USAC"]
    assert sorted(fmp.profile_calls) == ["ACTV", "ET"], "only ERROR candidates are looked up"

    [error] = _levels(caplog, logging.ERROR)
    [warning] = _levels(caplog, logging.WARNING)
    assert "ET (portfolio_items 3, watchlist_items 12; last close 2026-10-06" in error
    assert "same CIK now trading: ETX" in error and "isActivelyTrading False" in error
    assert "DDS" in warning and "ACTV" in warning and "NOCL (watchlist_items 1; no stored close)" in warning
    for quiet in ("ZPRT", "AAPL", "CASBF", "BTCUSD", "^GSPC", "SHOP.TO"):
        assert quiet not in error and quiet not in warning, quiet
    assert "USAC" not in error and "USAC" not in warning


@pytest.mark.asyncio
async def test_one_error_per_stop_per_process_then_warnings(monkeypatch, caplog):
    sp.publish_priced_session(SESSION, _session_rows(["AAPL"]))
    _install(monkeypatch, held={"ET": {"watchlist_items": 1}}, directory=DIRECTORY,
             stored={"AAPL": "2026-10-08", "ET": "2026-10-06"})
    assert list((await uh.report_unpriced_holdings(NOW))["newly"]) == ["ET"]
    assert await uh.report_unpriced_holdings(NOW) is None, "once per session"
    # Next session: still within the window, but already reported → WARNING.
    sp.publish_priced_session("2026-10-09", _session_rows(["AAPL"]))
    _install(monkeypatch, held={"ET": {"watchlist_items": 1}}, directory=DIRECTORY,
             stored={"AAPL": "2026-10-09", "ET": "2026-10-06"})
    later = datetime(2026, 10, 12, 10, 0, tzinfo=ET_TZ)
    report = await uh.report_unpriced_holdings(later)
    assert report["newly"] == {} and list(report["still"]) == ["ET"]


@pytest.mark.asyncio
async def test_a_late_list_drop_still_reaches_error(monkeypatch):
    """FMP's list dropped the symbol three sessions after its price stopped: the window,
    not "exactly two sessions back", is what still raises the ERROR."""
    sp.publish_priced_session(SESSION, _session_rows(["AAPL"]))
    _install(monkeypatch, held={"ET": {"watchlist_items": 1}}, directory=DIRECTORY,
             stored={"AAPL": "2026-10-08", "ET": "2026-10-01"})   # five sessions back
    assert list((await uh.report_unpriced_holdings(NOW))["newly"]) == ["ET"]


@pytest.mark.asyncio
async def test_a_symbol_that_traded_again_can_error_again(monkeypatch):
    sp.publish_priced_session(SESSION, _session_rows(["AAPL"]))
    _install(monkeypatch, held={"ET": {"w": 1}}, directory=DIRECTORY,
             stored={"AAPL": "2026-10-08", "ET": "2026-10-06"})
    await uh.report_unpriced_holdings(NOW)
    assert uh._errored == {"ET"}
    # 10-09: ET trades again (in the registry) → pruned.
    sp.publish_priced_session("2026-10-09", _session_rows(["AAPL", "ET"]))
    _install(monkeypatch, held={"ET": {"w": 1}}, directory=DIRECTORY, stored={"AAPL": "2026-10-09"})
    await uh.report_unpriced_holdings(datetime(2026, 10, 12, 10, 0, tzinfo=ET_TZ))
    assert uh._errored == set()


@pytest.mark.asyncio
async def test_no_current_registry_means_no_report(monkeypatch):
    _install(monkeypatch, held=HELD, directory=DIRECTORY, stored=STORED)
    assert await uh.report_unpriced_holdings(NOW) is None                      # cold
    sp.publish_priced_session("2026-10-06", _session_rows([]))                 # stale at NOW
    assert await uh.report_unpriced_holdings(NOW) is None


_DEFER_LOG = {
    "directory": "no fresh active-listing directory yet",
    "holdings": "holdings read failed",
    # Its own message, not the bellwether's: a swallowed failure would also defer (no
    # bellwether row), but the log would then blame a write that did land.
    "closes": "stored-close read failed",
    "write_not_landed": "has not landed",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("break_it", ["directory", "holdings", "closes", "write_not_landed"])
async def test_every_premature_or_failed_read_defers_without_spending_the_session(monkeypatch, caplog, break_it):
    sp.publish_priced_session(SESSION, _session_rows(["AAPL"]))
    _install(monkeypatch, held=HELD, directory=DIRECTORY, stored=STORED, fmp=_FMP(PROFILES, CIKS))

    def boom(*a, **k):
        raise RuntimeError("supabase down")

    if break_it == "directory":
        monkeypatch.setattr(search_module, "get_fresh_active_listings", lambda: None)
    elif break_it == "holdings":
        monkeypatch.setattr(uh, "_held_symbols", boom)
    elif break_it == "closes":
        monkeypatch.setattr(PriceService, "_select_closes", staticmethod(boom))
    else:
        _install(monkeypatch, held=HELD, directory=DIRECTORY, stored={**STORED, "AAPL": "2026-10-07"},
                 fmp=_FMP(PROFILES, CIKS))
    with caplog.at_level(logging.INFO):
        assert await uh.report_unpriced_holdings(NOW) is None
    assert uh._reported_session is None and uh._errored == set()
    msgs = [r.getMessage() for r in caplog.records if r.name == uh.logger.name]
    assert any(_DEFER_LOG[break_it] in m for m in msgs), msgs
    # The next cycle, with the read healthy, still raises the ERROR.
    _install(monkeypatch, held=HELD, directory=DIRECTORY, stored=STORED, fmp=_FMP(PROFILES, CIKS))
    assert list((await uh.report_unpriced_holdings(NOW))["newly"]) == ["ET"]


@pytest.mark.asyncio
async def test_a_failed_lookup_still_reports_the_symbol(monkeypatch, caplog):
    sp.publish_priced_session(SESSION, _session_rows(["AAPL"]))
    _install(monkeypatch, held={"ET": {"watchlist_items": 1}}, directory=DIRECTORY,
             stored={"AAPL": "2026-10-08", "ET": "2026-10-06"}, fmp=_FMP(fail={"ET"}))
    with caplog.at_level(logging.ERROR):
        report = await uh.report_unpriced_holdings(NOW)
    assert report["newly"]["ET"]["lookup_error"] == "profile RuntimeError"
    assert "lookup failed: profile RuntimeError" in _levels(caplog, logging.ERROR)[0]


@pytest.mark.asyncio
async def test_lookups_are_capped_and_the_rest_still_reported(monkeypatch):
    held = {f"DX{i:03d}": {"watchlist_items": 1} for i in range(30)}
    stored = {"AAPL": "2026-10-08", **{s: "2026-10-06" for s in held}}
    sp.publish_priced_session(SESSION, _session_rows(["AAPL"]))
    fmp = _install(monkeypatch, held=held, directory=DIRECTORY, stored=stored)
    report = await uh.report_unpriced_holdings(NOW)
    assert len(fmp.profile_calls) == uh._MAX_LOOKUPS
    assert len(report["newly"]) == 30
    unlooked = [s for s, v in report["newly"].items() if v.get("note") == "not looked up (lookup cap)"]
    assert len(unlooked) == 30 - uh._MAX_LOOKUPS


@pytest.mark.asyncio
async def test_the_most_held_symbols_are_looked_up_first(monkeypatch):
    held = {f"DX{i:03d}": {"watchlist_items": 1} for i in range(15)}
    held["BIG"] = {"watchlist_items": 99}
    stored = {"AAPL": "2026-10-08", **{s: "2026-10-06" for s in held}}
    sp.publish_priced_session(SESSION, _session_rows(["AAPL"]))
    fmp = _install(monkeypatch, held=held, directory=DIRECTORY, stored=stored)
    await uh.report_unpriced_holdings(NOW)
    assert fmp.profile_calls[0] == "BIG"


@pytest.mark.asyncio
async def test_nothing_unpriced_is_an_info_line(monkeypatch, caplog):
    sp.publish_priced_session(SESSION, _session_rows(["AAPL"]))
    _install(monkeypatch, held={"AAPL": {"watchlist_items": 1}}, directory=DIRECTORY, stored={})
    with caplog.at_level(logging.INFO):
        report = await uh.report_unpriced_holdings(NOW)
    assert report["newly"] == {} and report["still"] == {}
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ── the holdings read ─────────────────────────────────────────────────────────

class _Query:
    def __init__(self, log, table):
        self.log, self.table = log, table

    def select(self, cols):
        self.log.append((self.table, "select", cols))
        return self

    def eq(self, col, val):
        self.log.append((self.table, "eq", (col, val)))
        return self


class _SB:
    def __init__(self):
        self.log = []

    def table(self, name):
        return _Query(self.log, name)


def test_the_holdings_read_builds_the_right_queries_and_counts(monkeypatch):
    sb = _SB()
    data = {
        "watchlist_items": [{"ticker": "et"}, {"ticker": "ET "}, {"ticker": None}, {"ticker": ""}],
        "portfolio_items": [{"ticker": "ET"}],
        "price_alerts": [{"ticker": "DDS"}, "garbage"],
    }

    def fake_fetch_all_rows(build, *, order_by, what, **kw):
        q = build()          # actually runs the builder against the recording fake
        assert order_by == "id"
        return data[q.table]

    monkeypatch.setattr(uh, "fetch_all_rows", fake_fetch_all_rows)
    monkeypatch.setattr(uh, "get_supabase", lambda: sb)
    held = uh._held_symbols()
    assert held == {"ET": {"watchlist_items": 2, "portfolio_items": 1}, "DDS": {"price_alerts": 1}}
    assert ("price_alerts", "eq", ("is_active", True)) in sb.log
    assert [e for e in sb.log if e[1] == "eq"] == [("price_alerts", "eq", ("is_active", True))]
    assert all(e[2] == "id,ticker" for e in sb.log if e[1] == "select")
    assert {e[0] for e in sb.log} == {"watchlist_items", "portfolio_items", "price_alerts"}


def test_the_bellwether_is_the_ingests_own():
    from app.services import price_service
    assert uh._BELLWETHER == price_service._US_SESSION_BELLWETHERS[0]


# ── the loop ──────────────────────────────────────────────────────────────────

def test_the_close_snapshot_loop_runs_the_report_in_its_own_try():
    """Brace-bound: the INNERMOST try around the call must be the report's own, not the
    loop's outer `try` (which also wraps `refresh_close_snapshot()` and would pass a scan
    for "any try around it" after the dedicated one were deleted — review 2026-10-08)."""
    import app.main as main_module

    tree = ast.parse(inspect.getsource(main_module._run_close_snapshot_loop).lstrip())

    def innermost_try(node, target, current=None):
        if isinstance(node, ast.Try):
            current = node
        if isinstance(node, ast.Await) and "report_unpriced_holdings" in ast.unparse(node):
            return current
        for child in ast.iter_child_nodes(node):
            found = innermost_try(child, target, current)
            if found is not None:
                return found
        return None

    t = innermost_try(tree, "report_unpriced_holdings")
    assert t is not None, "report_unpriced_holdings() must be awaited inside a try"
    body = ast.unparse(ast.Module(body=t.body, type_ignores=[]))
    assert "refresh_close_snapshot" not in body, "it must be its OWN try, not the loop's"
    assert any(isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in t.handlers)
    src = inspect.getsource(main_module._run_close_snapshot_loop)
    assert src.index("refresh_close_snapshot()") < src.index("report_unpriced_holdings()")
