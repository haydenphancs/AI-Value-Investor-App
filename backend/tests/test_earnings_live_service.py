"""Live earnings results: the per-process records, the hot refresh and the 60 s tick.

What is pinned here, each a way the LIVE card could lie or cost too much:
  * records are STICKY — a later FMP answer without the actuals never takes a figure back;
  * a landing is a landing only when the record was SEEN pending first (a restart must not
    re-announce a morning's results in the afternoon);
  * the live memo fetches TODAY only while a watched row is pending in 06:00–22:00 ET, and
    the previous trading day at 5 min while hot (until 12:00 ET), else every 30 min;
  * feed builds and the sheet never fetch — only the loop does;
  * the tick runs the SEC watch only for watched symbols still pending today.
Hermetic: inline fakes for FMP, SEC and the dispatcher; the clock is passed in.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

import app.services.earnings_filing_service as efs
import app.services.earnings_live_service as els
import app.services.tracking_service as ts
from app.utils.market_hours import ET

REPORT = date(2026, 10, 29)            # Thursday
PREV = date(2026, 10, 28)              # Wednesday — the previous trading day
NEXT = date(2026, 10, 30)


def _et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET).astimezone(timezone.utc)


def _row(symbol: str, day: date, *, eps=None, rev=None, eps_est=0.41, rev_est=16.6e9) -> dict:
    return {"symbol": symbol, "date": day.isoformat(), "epsActual": eps, "epsEstimated": eps_est,
            "revenueActual": rev, "revenueEstimated": rev_est, "lastUpdated": day.isoformat()}


class _FMP:
    def __init__(self, by_day: Optional[Dict[date, List[dict]]] = None):
        self.by_day = by_day or {}
        self.calls: List[str] = []

    async def get_earnings_calendar(self, from_date=None, to_date=None):
        self.calls.append(from_date)
        return [dict(r) for r in self.by_day.get(date.fromisoformat(from_date), [])]


def _reset_all() -> None:
    els.get_earnings_live_service().reset()
    if efs._watcher is not None:
        efs._watcher.reset()
    ts.reset_earnings_calendar_cache()


@pytest.fixture(autouse=True)
def _clean():
    _reset_all()
    yield
    _reset_all()


def _svc() -> els.EarningsLiveService:
    return els.get_earnings_live_service()


# ── records ──────────────────────────────────────────────────────────────────


def test_a_later_answer_without_actuals_never_takes_a_figure_back():
    svc = _svc()
    now = _et(REPORT, 16, 30)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44, rev=17.1e9)], now)
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], now + timedelta(minutes=5))
    snap = svc.snapshot("INTC", REPORT, now + timedelta(minutes=5))
    assert (snap.eps.actual, snap.revenue.actual) == (0.44, 17.1e9)


def test_a_reschedule_duplicate_pair_reads_as_reported():
    """FMP can carry a pending AND a reported row for one (symbol, date)."""
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [_row("INTC", REPORT), _row("INTC", REPORT, eps=0.44, rev=17.1e9)],
                     now)
    assert svc.snapshot("INTC", REPORT, now).status == els.STATUS_REPORTED


def test_rows_of_another_day_are_never_folded_under_this_one():
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", NEXT, eps=0.44)], _et(REPORT, 17))
    assert svc.snapshot("INTC", REPORT, _et(REPORT, 17)) is None


def test_a_landing_needs_the_record_to_have_been_seen_pending():
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    svc.observe_rows(REPORT, [_row("AAPL", REPORT, eps=1.9, rev=1e11)], _et(REPORT, 9))
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44)], _et(REPORT, 16, 30))
    landed = {s.symbol for s in svc.landed_snapshots(_et(REPORT, 16, 31))}
    assert landed == {"INTC"}, "AAPL was already reported when first seen — not a landing"


def test_a_filing_landing_counts_and_is_marked_preliminary():
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    figures = efs.FilingFigures(accession="0000050863-26-000123", accepted_at=None, url=None,
                                revenue=17.1e9, eps_adjusted=0.45)
    svc.absorb_filing("INTC", REPORT, figures, _et(REPORT, 16, 3))
    (snap,) = svc.landed_snapshots(_et(REPORT, 16, 4))
    assert snap.preliminary and snap.eps.actual == 0.45 and snap.status == els.STATUS_REPORTED


def test_pending_symbols_keep_the_callers_order():
    svc = _svc()
    now = _et(REPORT, 9)
    svc.observe_rows(REPORT, [_row("AAPL", REPORT), _row("INTC", REPORT),
                              _row("MSFT", REPORT, eps=3.0, rev=7e10, eps_est=2.9,
                                   rev_est=6.9e10)], now)
    assert svc.pending_symbols(REPORT, ["INTC", "MSFT", "AAPL", "NONE"]) == ["INTC", "AAPL"]


# ── the hot gate ─────────────────────────────────────────────────────────────


def test_today_is_fetched_only_while_a_watched_row_is_pending_in_the_window():
    svc = _svc()
    svc._fetched[PREV] = __import__("time").monotonic()        # prev already fresh
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    assert REPORT in svc.due_days(_et(REPORT, 9), ["INTC"])
    assert REPORT not in svc.due_days(_et(REPORT, 9), ["AAPL"]), "not watched → not hot"
    assert REPORT not in svc.due_days(_et(REPORT, 5), ["INTC"]), "before 06:00 ET"
    assert REPORT not in svc.due_days(_et(REPORT, 22, 30), ["INTC"]), "after 22:00 ET"


def test_today_stops_being_hot_once_every_watched_row_is_complete():
    svc = _svc()
    svc._fetched[PREV] = __import__("time").monotonic()
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44, rev=17.1e9)], _et(REPORT, 17))
    assert svc.due_days(_et(REPORT, 17), ["INTC"]) == []


def test_the_previous_day_is_hot_until_noon_then_cold(monkeypatch):
    import time as _time

    svc = _svc()
    now_mono = _time.monotonic()
    svc.observe_rows(PREV, [_row("INTC", PREV)], _et(REPORT, 8))
    svc._fetched[PREV] = now_mono - (els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1)
    assert PREV in svc.due_days(_et(REPORT, 8), ["INTC"]), "hot before noon: 5 min"
    assert PREV not in svc.due_days(_et(REPORT, 13), ["INTC"]), "cold after noon: 30 min"
    svc._fetched[PREV] = now_mono - (els.EARNINGS_LIVE_COLD_TTL_SECONDS + 1)
    assert PREV in svc.due_days(_et(REPORT, 13), ["INTC"])


def test_the_previous_day_stops_being_fetched_once_every_watched_report_is_complete():
    import time as _time

    svc = _svc()
    svc.observe_rows(PREV, [_row("INTC", PREV, eps=0.44, rev=17.1e9)], _et(REPORT, 13),
                     fetched=True)
    svc._fetched[PREV] = _time.monotonic() - (els.EARNINGS_LIVE_COLD_TTL_SECONDS + 1)
    assert PREV not in svc.due_days(_et(REPORT, 13), ["INTC"]), "nothing left to ask"
    assert PREV not in svc.due_days(_et(REPORT, 2), ["INTC"]), "nor overnight"


def test_only_the_live_memos_own_fetch_moves_checked_at():
    """Rows folded in from the Tracking memo can be 30 min old: "updated just now" over them
    would be false."""
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    assert svc.snapshot("INTC", REPORT, _et(REPORT, 9)).checked_at is None
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9, 5), fetched=True)
    assert svc.snapshot("INTC", REPORT, _et(REPORT, 9, 5)).checked_at == _et(REPORT, 9, 5)
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9, 30))
    assert svc.snapshot("INTC", REPORT, _et(REPORT, 9, 30)).checked_at == _et(REPORT, 9, 5)


@pytest.mark.asyncio
async def test_refresh_makes_one_single_day_call_per_due_day_and_dedups():
    fmp = _FMP({PREV: [_row("INTC", PREV, eps=0.30, rev=1.3e10)]})
    svc = _svc()
    now = _et(REPORT, 9)
    await asyncio.gather(svc.refresh(now, fmp=fmp, symbols=["INTC"]),
                         svc.refresh(now, fmp=fmp, symbols=["INTC"]))
    assert fmp.calls == [PREV.isoformat()], "one round, one call, shared by both callers"
    assert svc.snapshot("INTC", PREV, now).status == els.STATUS_REPORTED


@pytest.mark.asyncio
async def test_a_failed_refresh_keeps_the_records_and_waits():
    class _Down(_FMP):
        async def get_earnings_calendar(self, from_date=None, to_date=None):
            self.calls.append(from_date)
            raise RuntimeError("FMP 503")

    svc = _svc()
    now = _et(REPORT, 9)
    svc.observe_rows(PREV, [_row("INTC", PREV, eps=0.30)], now)
    down = _Down()
    await svc.refresh(now, fmp=down, symbols=["INTC"])
    await svc.refresh(now, fmp=down, symbols=["INTC"])
    assert len(down.calls) == 1, "the failure wait holds the next round"
    assert svc.snapshot("INTC", PREV, now).eps.actual == 0.30


@pytest.mark.asyncio
async def test_records_outside_the_two_live_days_are_pruned_at_the_day_roll():
    svc = _svc()
    svc.observe_rows(PREV, [_row("INTC", PREV, eps=0.3)], _et(PREV, 17))
    fmp = _FMP({REPORT: [], NEXT: []})
    await svc.refresh(_et(NEXT, 9), fmp=fmp, symbols=["INTC"])
    assert svc.snapshot("INTC", PREV, _et(NEXT, 9)) is None


# ── readers never fetch ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_feed_build_reads_the_live_records_and_never_fetches(monkeypatch):
    monkeypatch.setattr(ts, "_et_today", lambda: REPORT)
    fmp = _FMP({REPORT: [_row("INTC", REPORT), _row("AAPL", NEXT)],
                **{REPORT + timedelta(days=i): [] for i in range(1, 15)}})
    fmp.by_day[REPORT] = [_row("INTC", REPORT)]
    svc_obj = ts.TrackingService.__new__(ts.TrackingService)
    svc_obj.fmp = fmp
    live = _svc()
    # The live loop already saw the filing for INTC.
    live.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    live.absorb_filing("INTC", REPORT, efs.FilingFigures(
        accession="0000050863-26-000123", accepted_at=None, url=None, revenue=17.1e9),
        _et(REPORT, 16, 3))
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_et(REPORT, 16, 4)))
    alerts = await svc_obj._get_earnings_alerts(["INTC"])
    (card,) = alerts
    assert card.earnings_status == "live"
    assert card.revenue_actual == 17.1e9 and card.results_preliminary is True
    assert card.report_date == "2026-10-29"
    # 15 calls is the Tracking memo's own round (today .. +14); the live memo made none.
    assert live.fetches == 0


class _FrozenDatetime:
    """`datetime` stand-in for tracking_service: `now()` is frozen, the rest delegates."""

    def __init__(self, at: datetime):
        self._at = at

    def now(self, tz=None):
        return self._at.astimezone(tz) if tz else self._at.replace(tzinfo=None)

    def __getattr__(self, name):
        return getattr(datetime, name)

    def __call__(self, *a, **kw):
        return datetime(*a, **kw)


@pytest.mark.asyncio
async def test_the_previous_days_cards_come_from_the_live_records(monkeypatch):
    live = _svc()
    live.observe_rows(PREV, [_row("INTC", PREV, eps=0.44, rev=17.1e9),
                             _row("AAPL", PREV)], _et(REPORT, 8))
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_et(REPORT, 8)))
    svc_obj = ts.TrackingService.__new__(ts.TrackingService)
    svc_obj.fmp = _FMP()
    recent = await svc_obj._get_recent_earnings_alerts(["INTC", "AAPL", "MSFT"])
    by = {a.ticker: a for a in recent}
    assert set(by) == {"AAPL", "INTC"}
    assert by["INTC"].earnings_status == "reported" and by["INTC"].day == 28
    assert by["INTC"].description == "INTC reported earnings on OCT 28."
    assert by["AAPL"].earnings_status == "awaiting"
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_et(REPORT, 12, 30)))
    recent = await svc_obj._get_recent_earnings_alerts(["INTC", "AAPL"])
    assert [a.ticker for a in recent] == ["INTC"], "an awaiting card drops at noon ET"


@pytest.mark.asyncio
async def test_live_off_means_no_recent_cards(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", False)
    _svc().observe_rows(PREV, [_row("INTC", PREV, eps=0.44)], _et(REPORT, 8))
    svc_obj = ts.TrackingService.__new__(ts.TrackingService)
    svc_obj.fmp = _FMP()
    assert await svc_obj._get_recent_earnings_alerts(["INTC"]) == []


# ── the tick ─────────────────────────────────────────────────────────────────


class _Watcher:
    def __init__(self, figures=None):
        self.figures = figures or {}
        self.calls: List[tuple] = []

    def prune(self, days):
        pass

    async def run_once(self, now, symbols, day, *, sec=None, gemini=None):
        self.calls.append((list(symbols), day))
        return {s: f for s, f in self.figures.items() if s in symbols}


@pytest.mark.asyncio
async def test_the_tick_watches_sec_only_for_pending_watched_symbols_and_dispatches(monkeypatch):
    monkeypatch.setattr(ts, "_et_today", lambda: REPORT)
    fmp = _FMP({REPORT: [_row("INTC", REPORT),
                         _row("AAPL", REPORT, eps=1.9, rev=1e11, eps_est=1.8, rev_est=9.8e10),
                         _row("TSLA", REPORT)],
                PREV: []})
    figures = efs.FilingFigures(accession="0000050863-26-000123", accepted_at=None, url=None,
                                revenue=17.1e9, eps_adjusted=0.45)
    watcher = _Watcher({"INTC": figures})
    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: watcher)
    sent: List[List[Any]] = []

    async def _dispatch(snaps, now):
        sent.append([s.symbol for s in snaps])

    now = _et(REPORT, 16, 3)
    stats = await els.earnings_live_tick(now, fmp=fmp, watched=["INTC", "AAPL"],
                                         dispatch=_dispatch)
    assert watcher.calls == [(["INTC"], REPORT)], "AAPL is complete, TSLA is not watched"
    assert stats["filings"] == 1
    # INTC was seen pending in the same tick (the Tracking rows were folded first), so the
    # filing is a landing.
    assert sent == [["INTC"]]


@pytest.mark.asyncio
async def test_the_tick_does_nothing_without_watched_tickers():
    stats = await els.earnings_live_tick(_et(REPORT, 9), fmp=_FMP(), watched=[],
                                         dispatch=lambda s, n: None)
    assert stats == {"watched": 0, "fetched": 0, "filings": 0, "landed": 0}


@pytest.mark.asyncio
async def test_history_extras_are_computed_once_per_report_and_kept(monkeypatch):
    from types import SimpleNamespace

    calls = []

    async def _loader(sym):
        calls.append(sym)
        return SimpleNamespace(eps_quarters=[], revenue_quarters=[], degraded=[])

    svc = _svc()
    await svc.history_extras("INTC", REPORT, loader=_loader)
    await svc.history_extras("INTC", REPORT, loader=_loader)
    assert calls == ["INTC"], "never per sheet poll"


@pytest.mark.asyncio
async def test_a_failed_history_load_is_retried_after_a_short_wait(monkeypatch):
    calls = []

    async def _boom(sym):
        calls.append(sym)
        raise RuntimeError("FMP 503")

    svc = _svc()
    assert await svc.history_extras("INTC", REPORT, loader=_boom) is None
    assert await svc.history_extras("INTC", REPORT, loader=_boom) is None
    assert calls == ["INTC"]
    key = ("INTC", REPORT)
    svc._extras[key] = (0.0, None)                    # the 5-min wait has passed
    await svc.history_extras("INTC", REPORT, loader=_boom)
    assert calls == ["INTC", "INTC"]


@pytest.mark.asyncio
async def test_a_slow_history_build_is_not_cancelled_and_is_read_on_the_next_try(monkeypatch):
    """`get_earnings` is a SHARED build: a timeout here must not cancel it for the tab."""
    from types import SimpleNamespace

    monkeypatch.setattr(els, "_EXTRAS_DEADLINE_SECONDS", 0.01)
    finished = asyncio.Event()
    calls = []

    async def _slow(sym):
        calls.append(sym)
        await asyncio.sleep(0.05)
        finished.set()
        return SimpleNamespace(eps_quarters=[], revenue_quarters=[], degraded=[])

    svc = _svc()
    assert await svc.history_extras("INTC", REPORT, loader=_slow) is None
    await asyncio.wait_for(finished.wait(), timeout=2.0)   # it kept running in the background
    key = ("INTC", REPORT)
    assert svc._extras[key][0] - __import__("time").monotonic() <= els._EXTRAS_SLOW_RETRY_SECONDS

