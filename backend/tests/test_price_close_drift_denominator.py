"""A screener price a hair off the official close is DRIFT, not a new session (2026-09-30).

Symptom (Home Screen widget, 2026-09-30 ~06:50 ET, pre-market Wednesday): "Nasdaq ETF"
(ONEQ) read exactly 0.00% while SPY read -0.18% under a "Tue close" label.

Cause: `_pick_denominator` decided "this price belongs to a LATER session" from
`price != stored close` alone. The screener's price and the official `batch-eod` close
disagree for about one row in seven, so such a row divided by its OWN session's close
(+0.002%) and `_change_session` stamped it with the coming day. Measured read-only on
2026-09-30 21:29 ET with Wednesday's close stored: 1,051 of 7,071 universe rows were off
the stored close (510 by under half a cent, 183 by more than a cent).

Fix: a price off the close counts as a later session only while one CAN exist
(`PriceService._no_session_since_close`). It cannot once the stored close is the
session the numbers describe (from the ingest to 04:00 ET, weekends, holidays). It also
cannot pre-market, before the next session opens. The pre-market window is the
screenshot's moment: `session_trading_date()` already names Wednesday at 06:50.

Hermetic: no FMP, no Supabase; every clock read is pinned to an explicit instant.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pytest

import app.services.price_service as ps
from app.services.price_service import PriceService
from app.utils.market_hours import ET, SESSION_PREMARKET

# 2026-09-29 is a Tuesday, 2026-09-30 a Wednesday.
TUE, WED, THU, FRI = "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"


def _et(y: int, m: int, d: int, hh: int, mi: int) -> datetime:
    return datetime(y, m, d, hh, mi, tzinfo=ET)


WED_0650 = _et(2026, 9, 30, 6, 50)     # pre-market: the screenshot
WED_1100 = _et(2026, 9, 30, 11, 0)     # regular session
WED_1700 = _et(2026, 9, 30, 17, 0)     # after hours, Wednesday not yet ingested
WED_2030 = _et(2026, 9, 30, 20, 30)    # closed, Wednesday not yet ingested
WED_2130 = _et(2026, 9, 30, 21, 30)    # closed, Wednesday's close ingested
THU_0359 = _et(2026, 10, 1, 3, 59)     # overnight
THU_0400 = _et(2026, 10, 1, 4, 0)      # pre-market opens
THU_0929 = _et(2026, 10, 1, 9, 29)     # last pre-market minute
THU_0930 = _et(2026, 10, 1, 9, 30)     # the open

# ONEQ / SPY figures from the reported morning: Tuesday's official closes, Monday's below.
ONEQ_TUE, ONEQ_MON = 105.54, 105.97
SPY_TUE, SPY_MON = 764.20, 765.58
ONEQ_DRIFTED = 105.5421                # sub-cent off Tuesday's close: +0.002% vs ONEQ_TUE


def _snap(close: Optional[float], prev: Optional[float], trade_date: Optional[str]) -> Dict[str, Any]:
    return {"close": close, "previous_close": prev, "trade_date": trade_date}


@pytest.fixture(autouse=True)
def _clear():
    ps._cache.clear()
    yield
    ps._cache.clear()


@pytest.fixture
def pin_clock(monkeypatch):
    """Pin price_service's clock to one instant, for the paths that take no `now`
    (`_from_screener`, `get_quotes`, the movers universe)."""
    real_std, real_phase = ps.session_trading_date, ps.session_phase

    def _pin(instant: datetime) -> None:
        monkeypatch.setattr(ps, "session_trading_date", lambda now=None: real_std(now or instant))
        monkeypatch.setattr(ps, "session_phase", lambda now=None: real_phase(now or instant))

    return _pin


# ── the premise ──────────────────────────────────────────────────────────────

def test_premarket_already_names_the_coming_session():
    """Why "trade_date == session_trading_date()" alone could not fix the screenshot:
    at 06:50 ET Wednesday that function returns WEDNESDAY, with Tuesday's close stored."""
    assert ps.session_phase(WED_0650) == SESSION_PREMARKET
    assert ps.session_trading_date(WED_0650) == date(2026, 9, 30)
    assert PriceService._no_session_since_close(_snap(ONEQ_TUE, ONEQ_MON, TUE), WED_0650)


# ── the matrix: drift, exact equality, and genuine new sessions ───────────────────

@pytest.mark.parametrize("now, trade_date, price, close, prev, want_denom, want_stamp, why", [
    # Drift, the reported case and its evening twin.
    (WED_0650, TUE, ONEQ_DRIFTED, ONEQ_TUE, ONEQ_MON, ONEQ_MON, TUE,
     "pre-market sub-cent drift (ONEQ): Tuesday's move, not +0.002% stamped Wednesday"),
    (WED_2130, WED, 105.8421, 105.84, ONEQ_TUE, ONEQ_TUE, WED,
     "evening drift after Wednesday's ingest: Wednesday's move"),
    (WED_2130, WED, 261.79, 261.59, 263.00, 263.00, WED,
     "a 20-cent gap (ABBV, measured) is still drift once the session is stored"),
    (THU_0359, WED, 105.8421, 105.84, ONEQ_TUE, ONEQ_TUE, WED, "overnight drift"),
    (THU_0400, WED, 105.8421, 105.84, ONEQ_TUE, ONEQ_TUE, WED, "pre-market opens: still Wednesday's numbers"),
    (THU_0929, WED, 105.8421, 105.84, ONEQ_TUE, ONEQ_TUE, WED, "last pre-market minute"),
    # Exact equality: unchanged in every window.
    (WED_0650, TUE, SPY_TUE, SPY_TUE, SPY_MON, SPY_MON, TUE, "pre-market exact (SPY): unchanged"),
    (WED_2130, WED, 762.63, 762.63, SPY_TUE, SPY_TUE, WED, "evening exact: unchanged"),
    (WED_1100, TUE, ONEQ_TUE, ONEQ_TUE, ONEQ_MON, ONEQ_MON, TUE,
     "intraday, not yet printed: the prior session's move, as before"),
    # A genuine new session: the stored close is the base, stamped with the live day.
    (THU_0930, WED, 105.90, 105.84, ONEQ_TUE, 105.84, THU, "the open: a price off the close is Thursday's"),
    (WED_1100, TUE, 106.20, ONEQ_TUE, ONEQ_MON, ONEQ_TUE, WED, "intraday move"),
    (WED_1700, TUE, 105.84, ONEQ_TUE, ONEQ_MON, ONEQ_TUE, WED, "after hours, before the ingest"),
    (WED_2030, TUE, 105.84, ONEQ_TUE, ONEQ_MON, ONEQ_TUE, WED, "closed, before the ingest"),
    (WED_1100, TUE, 0.01, ONEQ_TUE, ONEQ_MON, ONEQ_TUE, WED, "an extreme intraday move is still the live session"),
    # Weekends and holidays.
    (_et(2026, 10, 3, 12, 0), FRI, 50.004, 50.00, 49.00, 49.00, FRI, "Saturday drift: Friday's move"),
    (_et(2026, 10, 5, 7, 0), FRI, 50.004, 50.00, 49.00, 49.00, FRI, "Monday pre-market drift: Friday's move"),
    (_et(2026, 10, 5, 10, 0), FRI, 50.50, 50.00, 49.00, 50.00, "2026-10-05", "Monday intraday move"),
    (_et(2026, 9, 7, 10, 0), "2026-09-04", 50.004, 50.00, 49.00, 49.00, "2026-09-04", "Labor Day: Friday's move"),
    (_et(2026, 9, 8, 7, 0), "2026-09-04", 50.004, 50.00, 49.00, 49.00, "2026-09-04",
     "pre-market after a holiday: still Friday's move"),
    (_et(2026, 9, 8, 10, 0), "2026-09-04", 50.50, 50.00, 49.00, 50.00, "2026-09-08", "intraday after a holiday"),
    # Half-day (2026-11-27 closes 13:00 ET; Thanksgiving 11-26 is a holiday).
    (_et(2026, 11, 27, 14, 0), "2026-11-27", 50.004, 50.00, 49.00, 49.00, "2026-11-27", "after a half-day close, ingested"),
    (_et(2026, 11, 27, 12, 0), "2026-11-25", 50.50, 50.00, 49.00, 50.00, "2026-11-27", "half-day intraday move"),
])
def test_the_denominator_and_stamp_follow_whether_a_later_session_can_exist(
        now, trade_date, price, close, prev, want_denom, want_stamp, why):
    snap = _snap(close, prev, trade_date)
    denom = PriceService._pick_denominator(price, snap, now=now)
    assert denom == want_denom, why
    assert PriceService._change_session(denom, snap, price, now=now) == want_stamp, why


def test_the_reported_oneq_row_no_longer_reads_as_flat():
    """The number itself: ONEQ is Tuesday's -0.40%, never something displayed as 0.00%."""
    snap = _snap(ONEQ_TUE, ONEQ_MON, TUE)
    denom = PriceService._pick_denominator(ONEQ_DRIFTED, snap, now=WED_0650)
    pct = (ONEQ_DRIFTED / denom - 1) * 100
    assert abs(pct) >= 0.005, f"{pct:.4f}% would render as 0.00%"
    assert pct == pytest.approx((ONEQ_DRIFTED / ONEQ_MON - 1) * 100)


# ── degraded rows ─────────────────────────────────────────────────────────────

def test_drift_without_a_previous_close_is_unknown_not_zero():
    """Before the fix this returned the close itself, so the change was a fabricated ~0%."""
    snap = _snap(105.84, None, WED)
    assert PriceService._pick_denominator(105.8421, snap, now=WED_2130) is None
    assert PriceService._change_session(None, snap, 105.8421, now=WED_2130) is None


@pytest.mark.parametrize("now", [WED_0650, WED_1100, WED_2130])
def test_a_pre_158_row_without_a_date_keeps_the_price_comparison(now):
    snap = _snap(105.54, 105.97, None)
    assert PriceService._no_session_since_close(snap, now) is False
    assert PriceService._pick_denominator(105.5421, snap, now=now) == 105.54


@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-45", "", "   "])
def test_an_unreadable_trade_date_keeps_the_price_comparison(bad):
    assert PriceService._no_session_since_close(_snap(105.54, 105.97, bad), WED_2130) is False


def test_no_snapshot_means_no_session_answer():
    assert PriceService._no_session_since_close(None, WED_2130) is False
    assert PriceService._no_session_since_close({}, WED_2130) is False


def test_a_stale_row_stays_unknown_whatever_the_drift():
    """Monday's close read on Wednesday evening is two sessions old: None, as before."""
    snap = _snap(105.84, 105.54, "2026-09-28")
    assert PriceService._pick_denominator(105.8421, snap, now=WED_2130) is None


def test_a_future_dated_close_admits_no_later_session():
    """A `trade_date` ahead of the clock (skew) cannot be followed by a later session."""
    snap = _snap(105.84, 105.54, THU)
    assert PriceService._pick_denominator(105.8421, snap, now=WED_2130) == 105.54


def test_a_missing_close_still_falls_back_to_the_previous_close():
    assert PriceService._pick_denominator(105.8421, _snap(None, 105.54, WED), now=WED_2130) == 105.54


# ── end to end: the batch path and the movers universe agree ─────────────────────

class _FakeFMP:
    def __init__(self, rows: List[Dict[str, Any]]):
        self.rows = rows

    async def get_company_screener(self, **kw):
        return self.rows if kw.get("page", 0) == 0 else []


def _screener(symbol: str, price: float) -> Dict[str, Any]:
    return {"symbol": symbol, "companyName": f"{symbol} ETF", "price": price,
            "marketCap": 1e10, "volume": 1e6, "avgVolume": 1e6,
            "exchangeShortName": "NASDAQ", "isEtf": True, "isFund": False}


_CLOSES = [
    {"symbol": "SPY", "close": SPY_TUE, "previous_close": SPY_MON, "trade_date": TUE},
    {"symbol": "ONEQ", "close": ONEQ_TUE, "previous_close": ONEQ_MON, "trade_date": TUE},
    {"symbol": "DIA", "close": 508.10, "previous_close": 509.00, "trade_date": TUE},
]


def _wire_batch(monkeypatch, prices: Dict[str, float]) -> None:
    monkeypatch.setattr(ps, "get_fmp_client",
                        lambda: _FakeFMP([_screener(s, p) for s, p in prices.items()]))
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(
        lambda symbols: [r for r in _CLOSES if r["symbol"] in symbols]))


@pytest.mark.asyncio
async def test_the_widget_batch_premarket_carries_one_session(monkeypatch, pin_clock):
    """The screenshot, end to end: SPY exact, ONEQ and DIA a hair off Tuesday's close.
    Every row is Tuesday's move, stamped Tuesday, so the payload has one session."""
    pin_clock(WED_0650)
    _wire_batch(monkeypatch, {"SPY": SPY_TUE, "ONEQ": ONEQ_DRIFTED, "DIA": 508.1049})
    out = await PriceService().get_quotes(["SPY", "ONEQ", "DIA"])
    assert {q["changeSession"] for q in out.values()} == {TUE}
    assert out["SPY"]["changePercentage"] == pytest.approx((SPY_TUE / SPY_MON - 1) * 100)   # -0.18%
    assert out["ONEQ"]["changePercentage"] == pytest.approx((ONEQ_DRIFTED / ONEQ_MON - 1) * 100)
    assert out["ONEQ"]["previousClose"] == ONEQ_MON
    assert out["DIA"]["changePercentage"] == pytest.approx((508.1049 / 509.00 - 1) * 100)


@pytest.mark.asyncio
async def test_the_widget_batch_intraday_is_the_live_session(monkeypatch, pin_clock):
    """Anti-vacuity: once Wednesday opens, the same rows off the close are Wednesday's move."""
    pin_clock(WED_1100)
    _wire_batch(monkeypatch, {"SPY": 763.00, "ONEQ": 105.90, "DIA": 507.00})
    out = await PriceService().get_quotes(["SPY", "ONEQ", "DIA"])
    assert {q["changeSession"] for q in out.values()} == {WED}
    assert out["ONEQ"]["changePercentage"] == pytest.approx((105.90 / ONEQ_TUE - 1) * 100)
    assert out["ONEQ"]["previousClose"] == ONEQ_TUE


@pytest.mark.asyncio
async def test_the_movers_universe_agrees_with_the_batch_path(monkeypatch, pin_clock):
    """One derivation: `market_movers_service` calls the same two helpers."""
    from app.services import market_movers_service as mm

    mm._cache.clear()
    pin_clock(WED_0650)
    _wire_batch(monkeypatch, {"ONEQ": ONEQ_DRIFTED})

    class _PS:
        async def _get_universe(self):
            return {"ONEQ": _screener("ONEQ", ONEQ_DRIFTED)}

    monkeypatch.setattr(mm, "price_source", lambda owner=None: _PS())
    monkeypatch.setattr(mm.MarketMoversService, "_select_all_closes", staticmethod(
        lambda: {r["symbol"]: dict(r) for r in _CLOSES}))
    try:
        row = (await mm.MarketMoversService().get_universe())["ONEQ"]
        batch = (await PriceService().get_quotes(["ONEQ"]))["ONEQ"]
    finally:
        mm._cache.clear()
    assert row["changeSession"] == batch["changeSession"] == TUE
    assert row["changePercentage"] == pytest.approx(batch["changePercentage"])
