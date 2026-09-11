"""Four session-semantics defects on the batch price path, found by the 2026-09-11 sweep.

1. A STALE close is not "yesterday's close". The snapshot row is written by an hourly job
   whose every abort path leaves the previous row in place — right for a holiday, wrong
   for a real session that was never ingested — and nothing on the read path checked
   `trade_date`. After two missed sessions every batch day-change was a multi-session
   move presented as today's. `_pick_denominator` now refuses a row older than the
   session before the one the current numbers describe (holiday-aware).

2. `price: 0` is not a price. FMP reports it for halted/delisted listings; `_finite(0)`
   is a real 0.0, so the screener path computed a -100.0% day change against the stored
   close and the profile path shipped `$0.00 +0.00%`. Both now yield `price: None`.

3. WHICH SESSION a batch change describes. At 07:00 ET Monday the screener still reports
   Friday's close, so the change is FRIDAY's move; batch quotes carried no stamp and the
   widget printed it as "Down 4.8% today" under a Monday date. `_from_screener` now stamps
   `changeSession` with the same derivation the movers service uses.

4. The prior-session coverage guard divided GLOBAL row counts, so a prior session on a
   day most non-US exchanges were shut could trip the 50% floor and abort a good US
   ingest. Coverage is now measured over the entitled universe when it is known.

Hermetic: FMP is a fake; the clock is injected.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

import pytest

import app.services.price_service as ps
from app.services.price_service import PriceService


class _FakeFMP:
    def __init__(self, screener=None, profiles=None, eod=None):
        self.screener_rows = screener or []
        self.profiles = profiles or {}
        self.eod = eod or {}

    async def get_company_screener(self, **kw):
        return self.screener_rows if kw.get("page", 0) == 0 else []

    async def get_company_profile(self, ticker):
        return self.profiles.get(ticker.upper(), {})

    async def get_batch_eod(self, trade_date):
        return self.eod.get(trade_date, [])


@pytest.fixture(autouse=True)
def _clear():
    ps._cache.clear()
    yield
    ps._cache.clear()


# ── 1. staleness gate ────────────────────────────────────────────────────────

def _snap(close, prev, trade_date):
    return {"close": close, "previous_close": prev, "trade_date": trade_date}


@pytest.mark.parametrize("session, stored, expect_current", [
    # Ordinary Monday intraday: Friday's close is the reference.
    (date(2026, 9, 14), "2026-09-11", True),
    # Tuesday after Labor Day (2026-09-07): Friday's close is still the reference.
    (date(2026, 9, 8), "2026-09-04", True),
    # The row already holds the CURRENT session's close (ingest ran after the bell).
    (date(2026, 9, 10), "2026-09-10", True),
    # One missed session: Thursday numbers against Tuesday's close → stale.
    (date(2026, 9, 10), "2026-09-08", False),
    # Two missed sessions.
    (date(2026, 9, 10), "2026-09-04", False),
])
def test_a_snapshot_is_current_only_up_to_one_session_back(monkeypatch, session, stored, expect_current):
    monkeypatch.setattr(ps, "session_trading_date", lambda now=None: session)
    snap = _snap(230.0, 228.0, stored)
    assert PriceService._snapshot_is_current(snap) is expect_current
    denom = PriceService._pick_denominator(240.0, snap)
    assert (denom is not None) is expect_current, (session, stored, denom)


def test_a_pre_158_row_without_a_date_is_trusted(monkeypatch):
    monkeypatch.setattr(ps, "session_trading_date", lambda now=None: date(2026, 9, 10))
    assert PriceService._pick_denominator(240.0, {"close": 230.0, "previous_close": 228.0}) == 230.0


@pytest.mark.asyncio
async def test_a_stale_close_leaves_the_batch_day_change_unknown(monkeypatch):
    """End to end: Thursday's live price against Monday's close renders no change."""
    monkeypatch.setattr(ps, "session_trading_date", lambda now=None: date(2026, 9, 10))
    fake = _FakeFMP(screener=[{"symbol": "AAPL", "companyName": "Apple", "price": 240.0,
                               "marketCap": 3e12, "volume": 1e6, "avgVolume": 1e6,
                               "exchangeShortName": "NASDAQ"}])
    monkeypatch.setattr(ps, "get_fmp_client", lambda: fake)
    svc = PriceService()
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(
        lambda symbols: [{"symbol": "AAPL", "close": 230.0, "previous_close": 228.0,
                          "trade_date": "2026-09-07"}]))
    q = (await svc.get_quotes(["AAPL"]))["AAPL"]
    assert q["price"] == 240.0
    assert q["changePercentage"] is None and q["change"] is None
    assert "changeSession" not in q


@pytest.mark.asyncio
async def test_the_close_select_carries_trade_date(monkeypatch):
    seen: List[str] = []

    class _Q:
        def select(self, cols): seen.append(cols); return self
        def in_(self, *a): return self
        def execute(self): return type("R", (), {"data": []})()

    class _SB:
        def table(self, _n): return _Q()

    monkeypatch.setattr(ps, "get_supabase", lambda: _SB())
    PriceService._select_closes(["AAPL"])
    assert seen and "trade_date" in seen[0], seen


# ── 2. price <= 0 is not a price ─────────────────────────────────────────────

@pytest.mark.parametrize("bad", [0, 0.0, -5.0, float("nan"), None, "0"])
def test_a_non_positive_screener_price_is_unknown_not_minus_100_percent(monkeypatch, bad):
    monkeypatch.setattr(ps, "session_trading_date", lambda now=None: date(2026, 9, 10))
    q = PriceService._from_screener(
        {"symbol": "XYZ", "companyName": "X", "price": bad, "marketCap": 3e8},
        _snap(12.0, 11.8, "2026-09-09"),
    )
    assert q["price"] is None
    assert q["changePercentage"] is None and q["change"] is None


@pytest.mark.parametrize("bad", [0, 0.0, -1, None])
def test_a_non_positive_profile_price_is_unknown(bad):
    q = PriceService._from_profile({"symbol": "ABCQ", "price": bad, "change": 0, "changePercentage": 0})
    assert q["price"] is None


@pytest.mark.asyncio
async def test_a_zero_price_row_is_absent_from_the_batch_result(monkeypatch):
    """The screener row has no price, so the profile fallback runs; it also says 0. The
    documented contract is a PRESENT key holding None (every consumer tests `price is
    None`), and the change must be None beside it — never `$0.00 -100%` or `+0.00%`."""
    monkeypatch.setattr(ps, "session_trading_date", lambda now=None: date(2026, 9, 10))
    fake = _FakeFMP(
        screener=[{"symbol": "XYZ", "companyName": "X", "price": 0, "marketCap": 3e8,
                   "exchangeShortName": "NASDAQ"}],
        profiles={"XYZ": {"symbol": "XYZ", "price": 0, "change": 0, "changePercentage": 0}},
    )
    monkeypatch.setattr(ps, "get_fmp_client", lambda: fake)
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(
        lambda symbols: [{"symbol": "XYZ", "close": 12.0, "previous_close": 11.8,
                          "trade_date": "2026-09-09"}]))
    out = await PriceService().get_quotes(["XYZ"])
    q = out.get("XYZ") or {}
    assert q.get("price") is None
    assert q.get("change") is None and q.get("changePercentage") is None


# ── 3. the session stamp ─────────────────────────────────────────────────────

def test_a_price_still_at_the_stored_close_is_stamped_with_that_close_date(monkeypatch):
    """07:00 ET Monday: the screener still shows Friday's close → the change is FRIDAY's."""
    monkeypatch.setattr(ps, "session_trading_date", lambda now=None: date(2026, 9, 14))
    q = PriceService._from_screener(
        {"symbol": "NVDA", "companyName": "NVIDIA", "price": 100.0, "marketCap": 3e12},
        _snap(100.0, 105.0, "2026-09-11"),
    )
    assert q["changePercentage"] == pytest.approx(-4.7619, rel=1e-4)
    assert q["changeSession"] == "2026-09-11"


def test_a_price_that_moved_off_the_close_is_stamped_with_the_live_session(monkeypatch):
    monkeypatch.setattr(ps, "session_trading_date", lambda now=None: date(2026, 9, 14))
    q = PriceService._from_screener(
        {"symbol": "NVDA", "companyName": "NVIDIA", "price": 103.0, "marketCap": 3e12},
        _snap(100.0, 105.0, "2026-09-11"),
    )
    assert q["changePercentage"] == pytest.approx(3.0)
    assert q["changeSession"] == "2026-09-14"


def test_the_stamp_is_absent_when_there_is_no_change_to_describe():
    q = PriceService._from_screener({"symbol": "NEW", "companyName": "New Co", "price": 10.0}, None)
    assert q["changePercentage"] is None and "changeSession" not in q


@pytest.mark.asyncio
async def test_the_movers_service_and_the_batch_path_stamp_identically(monkeypatch):
    """One derivation. The movers universe and the widget's batch quotes must agree.

    Behavioural, not an identity check on the import (that was true at HEAD too):
    `PriceService._change_session` is replaced with a sentinel, and the universe row
    must carry the sentinel — proving `get_universe` derives its stamp THROUGH the
    shared helper rather than through a private copy of the close/prev comparison."""
    from app.services import market_movers_service as mm
    mm._cache.clear()
    monkeypatch.setattr(ps, "session_trading_date", lambda now=None: date(2026, 9, 14))
    snap = _snap(100.0, 105.0, "2026-09-11")
    prev = PriceService._pick_denominator(100.0, snap)
    assert PriceService._change_session(prev, snap) == "2026-09-11"

    class _PS:
        async def _get_universe(self):
            return {"AAPL": {"symbol": "AAPL", "companyName": "Apple", "price": 100.0,
                             "marketCap": 3e12, "volume": 1e6, "avgVolume": 5e5,
                             "sector": "Technology", "industry": "Consumer Electronics",
                             "exchangeShortName": "NASDAQ", "isEtf": False, "isFund": False}}

    monkeypatch.setattr(mm, "price_source", lambda owner=None: _PS())
    monkeypatch.setattr(
        mm.MarketMoversService, "_select_all_closes",
        staticmethod(lambda: {"AAPL": {"symbol": "AAPL", "close": 100.0, "previous_close": 105.0,
                                       "trade_date": "2026-09-11"}}),
    )
    monkeypatch.setattr(PriceService, "_change_session", classmethod(lambda cls, p, s: "SENTINEL"))
    universe = await mm.MarketMoversService().get_universe()
    assert universe["AAPL"]["changeSession"] == "SENTINEL", (
        "market_movers derives the session stamp on its own instead of through "
        "PriceService._change_session"
    )
    mm._cache.clear()


# ── 4. coverage over the entitled universe ───────────────────────────────────

def _eod(date_iso: str, symbols: List[str]) -> List[Dict[str, Any]]:
    return [{"symbol": s, "date": date_iso, "close": 10.0 + i, "volume": 1} for i, s in enumerate(symbols)]


US = ["AAPL", "MSFT", "SPY", "NVDA", "AMZN", "META"]
INTL = [f"INTL{i}.L" for i in range(40)]


@pytest.mark.asyncio
async def test_an_international_closure_on_the_prior_session_does_not_abort_a_us_ingest(monkeypatch):
    """Latest = US + 40 international rows; prior = US only (Europe/Asia shut). Global
    coverage is 6/46 ≈ 13% — below the floor — but every US symbol is covered."""
    captured: List[Dict[str, Any]] = []
    fake = _FakeFMP(
        screener=[{"symbol": s, "price": 1.0, "marketCap": 1e9} for s in US],
        eod={"2026-09-04": _eod("2026-09-04", US + INTL), "2026-09-03": _eod("2026-09-03", US)},
    )
    monkeypatch.setattr(ps, "get_fmp_client", lambda: fake)
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: captured.extend(p) or len(p)))
    written = await PriceService().refresh_close_snapshot("2026-09-04")
    assert written == len(US + INTL)
    assert all(r["previous_close"] is not None for r in captured if r["symbol"] in US)


@pytest.mark.asyncio
async def test_a_truncated_prior_session_still_aborts_with_the_universe_known(monkeypatch):
    """Anti-over-correction: prior covers 1 of 6 US symbols → abort, nothing written."""
    captured: List[Dict[str, Any]] = []
    fake = _FakeFMP(
        screener=[{"symbol": s, "price": 1.0, "marketCap": 1e9} for s in US],
        eod={"2026-09-04": _eod("2026-09-04", US + INTL),
             "2026-09-03": _eod("2026-09-03", ["AAPL", "MSFT"] + INTL)},   # bellwether quorum holds
    )
    monkeypatch.setattr(ps, "get_fmp_client", lambda: fake)
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: captured.extend(p) or len(p)))
    assert await PriceService().refresh_close_snapshot("2026-09-04") == 0
    assert captured == []


@pytest.mark.asyncio
async def test_without_a_universe_the_global_ratio_is_the_fallback(monkeypatch):
    """The screener is down: the ingest must still run its (global) guard, not crash."""
    captured: List[Dict[str, Any]] = []
    fake = _FakeFMP(eod={"2026-09-04": _eod("2026-09-04", US), "2026-09-03": _eod("2026-09-03", US)})

    async def _boom(**kw):
        raise RuntimeError("screener down")

    fake.get_company_screener = _boom
    monkeypatch.setattr(ps, "get_fmp_client", lambda: fake)
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: captured.extend(p) or len(p)))
    assert await PriceService().refresh_close_snapshot("2026-09-04") == len(US)


def test_a_majority_stale_batch_warns_once_per_newest_date(monkeypatch, caplog):
    """The read-side trace of a MISSED INGEST: without it, every batch day change going
    'unknown' app-wide left only the job's own ERROR hours earlier to correlate. Once per
    newest stale date, so it cannot page on every 30 s Home poll."""
    import logging
    monkeypatch.setattr(ps, "session_trading_date", lambda now=None: date(2026, 9, 16))   # Wednesday
    PriceService._stale_snapshot_dates.clear()
    stale = {"close": 100.0, "previous_close": 99.0, "trade_date": "2026-09-11"}           # Friday: 2 sessions old
    assert PriceService._stale_trade_date({**stale, "symbol": "AAPL"}) == "2026-09-11"
    assert PriceService._stale_trade_date({**stale, "trade_date": "2026-09-15"}) is None
    assert PriceService._pick_denominator(101.0, {**stale, "symbol": "AAPL"}) is None
    with caplog.at_level(logging.INFO, logger=ps.logger.name):
        PriceService._report_stale_snapshots({"AAPL": "2026-09-11", "MSFT": "2026-09-11", "NVDA": "2026-09-10"}, 4)
        PriceService._report_stale_snapshots({"AAPL": "2026-09-11", "MSFT": "2026-09-11"}, 3)   # same newest date: silent
    warn = [r for r in caplog.records if r.levelno == logging.WARNING and "STALE" in r.getMessage()]
    assert len(warn) == 1 and "3 of 4" in warn[0].getMessage() and "2026-09-11" in warn[0].getMessage()
    PriceService._stale_snapshot_dates.clear()


def test_a_few_stale_illiquid_rows_are_an_info_line_not_a_warning(caplog):
    """CCZ / FEMD-class listings always carry an old last close; three of them in a
    200-symbol batch are not a missed ingest and must not read like one."""
    import logging
    PriceService._stale_snapshot_dates.clear()
    with caplog.at_level(logging.INFO, logger=ps.logger.name):
        PriceService._report_stale_snapshots({"CCZ": "2026-09-09", "FEMD": "2026-09-04"}, 200)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    info = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and "stale close snapshot" in r.getMessage()]
    assert len(info) == 1 and "2 of 200" in info[0] and "CCZ" in info[0]
    PriceService._report_stale_snapshots({}, 200)                       # nothing stale: silent
    assert len([r for r in caplog.records if "stale" in r.getMessage().lower()]) == 1
