"""Live earnings results: the per-process records, the hot refresh and the 60 s tick.

What is pinned here, each a way the LIVE card could lie or cost too much:
  * records are STICKY — a later FMP answer without the actuals never takes a figure back;
  * a landing is a landing only when the record was SEEN pending first (a restart must not
    re-announce a morning's results in the afternoon), and a filing accepted well before the
    record's first sight is not one either;
  * FMP's duplicate rows act as ONE report: one landing decision, one card, and never a
    pending twin's estimate beside a reported row's actual, while the guards still read that
    twin's consensus;
  * a repeat of one response decides what its first fold decided, in any row order;
  * a zero revenue is a result only against a tiny consensus with no figure held, and an EPS
    of 0.0 beside a refused revenue is the same unfilled field;
  * the newest FMP answer wins: a memo row older than a record's last own fetch is skipped at
    the tick, the card and the sheet, and a memo refreshed after that fetch still folds;
  * the live memo fetches TODAY only while a watched report still wants FMP in 06:00–22:00 ET,
    and the previous trading day at 5 min while hot (until 12:00 ET, on the next TRADING day
    only), else every 30 min, and every 2 h while today is a closed day;
  * a filing-complete report leaves the SEC watch but keeps asking FMP until FMP confirms it;
  * before 06:00 ET and on a closed day the tick folds what the memo holds and pays for no
    refresh, and the FMP calls of a busy report day and a weekend stay on budget;
  * ticks start exactly a minute apart while each fetch takes a moment, and every cadence still
    lands on the tick it falls on: 5 min, 30 min, 2 h and the wait after a failed fetch;
  * a memo row held 12 h opens no record at any fold, so a report FMP moved over a weekend is
    never a report day: the tick asks FMP first, one round, only when such a row would open one;
  * a report own fetches stop listing ends after 10 min, unless a figure from either source is
    held, so one short answer costs nothing, and an answer that lists nothing on the day, or
    sits at FMP's row cap, ends nothing;
  * a sheet says `none` only after asking for the days the report may have moved to, and only
    about a day that was read: the days the sheet waits on are the ones the loop's next round
    takes, whatever is watched, and a load ends the wait unless it came back blank on a trading
    day, which is asked again for a while and then settled;
  * with the live switch off a feed card carries no report date, on every shape of card;
  * feed builds and the sheet never fetch — only the loop does;
  * the SEC watch runs on its own loop, only for watched symbols still pending today: each
    figure reaches its record the moment it verifies, stamped with that moment, and a stuck
    round holds up neither FMP nor the push, which goes out on the next FMP tick;
  * a failed, stuck or cancelled tick in either loop never stops the other, and shutdown
    reaches both and waits for both;
  * one window, `live_days` (every calendar day from the previous trading day through today),
    holds the push memory and every reader but the sheet: a landing is sent once however the
    days roll, and a report dated on a closed day lands, keeps its card and answers the sheet
    like any other;
  * a record outlives that window by one trading day (`sheet_days`), for the sheet alone, since
    a held push can wake on that day: no card, no landing and no cadence reads it, and after a
    restart only a sheet asking for it pays for a look;
  * an awaiting card stays up until 12:00 ET on the next trading day and drops once;
  * a report still awaiting never hides an older reported one, on the card or in the sheet
    opened with no date, which always name the same report;
  * the sender gets watched landings only, while the fold stays whole-market: cards and the
    sheet read every record;
  * a landing older than yesterday tells nobody, and keeps its card and its sheet;
  * a failed read of the watched list serves the old list and says so;
  * a bad cadence or SEC budget fails the deploy, and both loops run on the cadence that passed.
Hermetic: inline fakes for FMP, SEC and the dispatcher; the clock is passed in.
"""
from __future__ import annotations

import asyncio
import dataclasses
import itertools
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import pytest
from pydantic import ValidationError

import app.services.earnings_filing_service as efs
import app.services.earnings_live_service as els
import app.services.earnings_window_service as ews
import app.services.notification_senders.earnings_live_sender as sender
import app.services.push_dispatch_service as pds
import app.services.tracking_service as ts
from app.config import Settings, settings
from app.integrations.fmp import EmptyAfterFailure
from app.services.notification_kinds import KIND_EARNINGS_LIVE, KIND_EARNINGS_RESULT
from app.utils import market_hours as mh
from app.utils.market_hours import ET, previous_trading_day

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


# ── FMP's duplicate rows ─────────────────────────────────────────────────────

_ORDERS = {"argvalues": [False, True], "ids": ["pending_first", "reported_first"]}


def _pair(reported_first: bool, day: date = REPORT) -> List[dict]:
    """One report listed twice: a stale pending row beside the reported one."""
    pair = [_row("INTC", day, eps_est=0.35, rev_est=15.9e9),
            _row("INTC", day, eps=0.44, rev=17.1e9)]
    return pair[::-1] if reported_first else pair


def _assert_the_reported_rows_figures(snap: els.LiveSnapshot) -> None:
    assert (snap.eps.actual, snap.eps.estimate, snap.eps.surprise_pct) == (0.44, 0.41, 7.32)
    assert (snap.revenue.actual, snap.revenue.estimate,
            snap.revenue.surprise_pct) == (17.1e9, 16.6e9, 3.01)


@pytest.mark.parametrize("reported_first", **_ORDERS)
def test_a_duplicate_pair_at_first_sight_is_reported_not_a_landing(reported_first):
    """The whole response decides, as in `earnings_window_service._absorb`: FMP's row order
    must decide neither the landing nor the surprise."""
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, _pair(reported_first), now)
    assert svc.landed_snapshots(now) == []
    _assert_the_reported_rows_figures(svc.snapshot("INTC", REPORT, now))


@pytest.mark.parametrize("reported_first", **_ORDERS)
def test_a_duplicate_pair_after_a_pending_batch_still_lands_on_the_reported_rows_figures(
        reported_first):
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    svc.observe_rows(REPORT, _pair(reported_first), _et(REPORT, 17))
    (snap,) = svc.landed_snapshots(_et(REPORT, 17, 1))
    _assert_the_reported_rows_figures(snap)


def test_a_later_pending_row_never_moves_the_estimate_behind_a_reported_actual():
    """The 30-min memo can hand back a pending row after the actual is in, and grouping one
    batch alone would not stop it: the surprise must keep its own consensus."""
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44, rev=17.1e9)], now)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps_est=0.35, rev_est=15.9e9)],
                     now + timedelta(minutes=5))
    _assert_the_reported_rows_figures(svc.snapshot("INTC", REPORT, now + timedelta(minutes=5)))


def test_an_estimate_keeps_moving_while_its_metric_has_no_actual():
    """Only a metric with an FMP actual is frozen: revenue in, the EPS consensus still moves."""
    svc = _svc()
    now = _et(REPORT, 16)
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], now)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps_est=0.43, rev_est=16.9e9)], now)
    snap = svc.snapshot("INTC", REPORT, now)
    assert (snap.eps.estimate, snap.revenue.estimate) == (0.43, 16.9e9)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, rev=17.1e9, eps_est=0.45, rev_est=17.0e9)], now)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps_est=0.46, rev_est=15.0e9)], now)
    snap = svc.snapshot("INTC", REPORT, now)
    assert (snap.eps.estimate, snap.revenue.estimate) == (0.46, 17.0e9)


@pytest.mark.parametrize("reported_first", **_ORDERS)
def test_the_reported_row_beats_a_pending_twin_fmp_touched_later(reported_first):
    pair = _pair(reported_first)
    (pending,) = [r for r in pair if r["epsActual"] is None]
    pending["lastUpdated"] = (REPORT + timedelta(days=1)).isoformat()
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, pair, now)
    _assert_the_reported_rows_figures(svc.snapshot("INTC", REPORT, now))


@pytest.mark.parametrize("reported_first", **_ORDERS)
@pytest.mark.parametrize("newest", ["eps_row", "revenue_row"])
def test_two_reported_rows_keep_each_metric_with_its_own_estimate(reported_first, newest):
    """Picking one row would drop a metric; folding both must not cross their estimates."""
    eps_row = _row("INTC", REPORT, eps=0.44, eps_est=0.41, rev_est=15.9e9)
    revenue_row = _row("INTC", REPORT, rev=17.1e9, rev_est=16.6e9, eps_est=0.35)
    {"eps_row": eps_row, "revenue_row": revenue_row}[newest]["lastUpdated"] = "2026-10-30"
    rows = [eps_row, revenue_row]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, rows[::-1] if reported_first else rows, now)
    _assert_the_reported_rows_figures(svc.snapshot("INTC", REPORT, now))


@pytest.mark.parametrize("reported_first", **_ORDERS)
def test_a_breakeven_eps_of_zero_still_ranks_as_the_reported_row(reported_first):
    pair = [_row("INTC", REPORT, eps_est=-0.05), _row("INTC", REPORT, eps=0.0, eps_est=-0.02)]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, pair[::-1] if reported_first else pair, now)
    snap = svc.snapshot("INTC", REPORT, now)
    assert (snap.eps.actual, snap.eps.estimate) == (0.0, -0.02)
    assert svc.landed_snapshots(now) == []


@pytest.mark.parametrize("reported_first", **_ORDERS)
def test_a_reported_row_with_no_estimate_takes_none_from_its_pending_twin(reported_first):
    """No consensus beats a stale one: a surprise against the twin's would be invented."""
    pair = [_row("INTC", REPORT, eps_est=0.35, rev_est=15.9e9),
            _row("INTC", REPORT, eps=0.44, rev=17.1e9, eps_est=None, rev_est=None)]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, pair[::-1] if reported_first else pair, now)
    snap = svc.snapshot("INTC", REPORT, now)
    assert (snap.eps.estimate, snap.eps.surprise_pct) == (None, None)
    assert (snap.revenue.estimate, snap.revenue.surprise_pct) == (None, None)
    assert (snap.eps.actual, snap.revenue.actual) == (0.44, 17.1e9)


@pytest.mark.parametrize("reported_first", **_ORDERS)
def test_a_dropped_digit_eps_is_held_by_its_pending_twins_consensus_at_first_sight(reported_first):
    """The twin's estimate stays out of the surprise, never out of the guard: without it the
    dropped digit prints and the hot refresh and the SEC watch stop."""
    pair = [_row("AVGO", REPORT, eps_est=1.65), _row("AVGO", REPORT, eps=0.169, eps_est=None,
                                                     rev_est=None)]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, pair[::-1] if reported_first else pair, now)
    snap = svc.snapshot("AVGO", REPORT, now)
    assert snap.eps.checking and snap.eps.actual is None
    assert (snap.eps.estimate, snap.eps.surprise_pct) == (None, None)
    assert snap.status == els.STATUS_LIVE
    assert svc.pending_symbols(REPORT, ["AVGO"]) == ["AVGO"]
    assert svc.landed_snapshots(now) == []


@pytest.mark.parametrize("reported_first", **_ORDERS)
def test_a_dropped_digit_revenue_is_held_by_its_pending_twins_consensus_at_first_sight(
        reported_first):
    pair = [_row("AVGO", REPORT, eps_est=1.65), _row("AVGO", REPORT, rev=1.71e9, eps_est=None,
                                                     rev_est=None)]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, pair[::-1] if reported_first else pair, now)
    snap = svc.snapshot("AVGO", REPORT, now)
    assert snap.revenue.checking and snap.revenue.actual is None
    assert (snap.revenue.estimate, snap.revenue.surprise_pct) == (None, None)
    assert svc.pending_symbols(REPORT, ["AVGO"]) == ["AVGO"]


@pytest.mark.parametrize("newest", ["eps_row", "revenue_row"])
@pytest.mark.parametrize("reported_first", **_ORDERS)
def test_a_dropped_digit_revenue_is_held_whichever_reported_row_fmp_touched_last(
        newest, reported_first):
    eps_row = _row("AVGO", REPORT, eps=1.70, eps_est=1.65, rev_est=16.6e9)
    revenue_row = _row("AVGO", REPORT, rev=1.71e9, eps_est=None, rev_est=None)
    {"eps_row": eps_row, "revenue_row": revenue_row}[newest]["lastUpdated"] = "2026-10-30"
    rows = [eps_row, revenue_row]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, rows[::-1] if reported_first else rows, now)
    snap = svc.snapshot("AVGO", REPORT, now)
    assert snap.revenue.checking and snap.revenue.actual is None and snap.eps.actual == 1.70
    assert snap.status == els.STATUS_LIVE, "a held revenue is not in"
    assert svc.pending_symbols(REPORT, ["AVGO"]) == ["AVGO"]


@pytest.mark.parametrize("newest", ["eps_row", "revenue_row"])
def test_a_reported_row_never_lends_its_consensus_to_another_rows_figure(newest):
    """FMP's touch date must not decide the surprise: the revenue's own row has no consensus."""
    eps_row = _row("INTC", REPORT, eps=0.44, eps_est=0.41, rev_est=15.9e9)
    revenue_row = _row("INTC", REPORT, rev=17.1e9, eps_est=None, rev_est=None)
    {"eps_row": eps_row, "revenue_row": revenue_row}[newest]["lastUpdated"] = "2026-10-30"
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [eps_row, revenue_row], now)
    snap = svc.snapshot("INTC", REPORT, now)
    assert (snap.revenue.actual, snap.revenue.estimate, snap.revenue.surprise_pct) == (
        17.1e9, None, None)
    assert (snap.eps.actual, snap.eps.estimate) == (0.44, 0.41)


@pytest.mark.parametrize("newest_first", [False, True])
def test_the_newest_figure_keeps_its_own_missing_consensus(newest_first):
    old = {**_row("INTC", REPORT, eps=0.40, eps_est=0.35), "lastUpdated": "2026-10-01"}
    new = {**_row("INTC", REPORT, eps=0.44, eps_est=None), "lastUpdated": "2026-10-20"}
    rows = [new, old] if newest_first else [old, new]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, rows, now)
    snap = svc.snapshot("INTC", REPORT, now)
    assert (snap.eps.actual, snap.eps.estimate, snap.eps.surprise_pct) == (0.44, None, None)


@pytest.mark.parametrize("reported_first", **_ORDERS)
def test_a_twins_revenue_consensus_keeps_a_missing_revenue_due(reported_first):
    """EPS alone is not complete while any row says a revenue is coming, shown or not."""
    pair = [_row("INTC", REPORT, eps_est=None), _row("INTC", REPORT, eps=0.44, eps_est=None,
                                                     rev_est=None)]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, pair[::-1] if reported_first else pair, now)
    snap = svc.snapshot("INTC", REPORT, now)
    assert snap.revenue.estimate is None and snap.status == els.STATUS_LIVE
    assert svc.pending_symbols(REPORT, ["INTC"]) == ["INTC"]


@pytest.mark.parametrize("listed_first", [False, True])
def test_the_newest_consensus_is_the_one_that_guards(listed_first):
    """Two pending twins disagree: FMP's latest touch decides, as it does for any tie on results."""
    figure = _row("AVGO", REPORT, eps=0.169, eps_est=None, rev_est=None)
    stale = {**_row("AVGO", REPORT, eps_est=0.17, rev_est=None), "lastUpdated": "2026-10-01"}
    fresh = {**_row("AVGO", REPORT, eps_est=1.65, rev_est=None), "lastUpdated": "2026-10-20"}
    twins = [fresh, stale] if listed_first else [stale, fresh]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [figure, *twins], now)
    assert svc.snapshot("AVGO", REPORT, now).eps.checking


def test_a_pending_row_after_the_figure_still_feeds_the_guard():
    """The same pair in two responses: the 30-min memo can hand the twin back later."""
    svc = _svc()
    svc.observe_rows(REPORT, [_row("AVGO", REPORT, eps=0.169, eps_est=None, rev_est=None)],
                     _et(REPORT, 17))
    svc.observe_rows(REPORT, [_row("AVGO", REPORT, eps_est=1.65, rev_est=None)], _et(REPORT, 17, 5))
    snap = svc.snapshot("AVGO", REPORT, _et(REPORT, 17, 5))
    assert snap.eps.checking and (snap.eps.actual, snap.eps.estimate) == (None, None)


def test_a_response_stating_no_consensus_keeps_the_guard_it_had():
    figure = _row("AVGO", REPORT, eps=0.169, eps_est=None, rev_est=None)
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [_row("AVGO", REPORT, eps_est=1.65, rev_est=None), figure], now)
    svc.observe_rows(REPORT, [figure], now + timedelta(minutes=5))
    assert svc.snapshot("AVGO", REPORT, now + timedelta(minutes=5)).eps.checking


def test_the_real_figure_after_a_held_one_lands_with_no_surprise_from_the_twin():
    twin = _row("AVGO", REPORT, eps_est=1.65, rev_est=None)
    svc = _svc()
    svc.observe_rows(REPORT, [twin, _row("AVGO", REPORT, eps=0.169, eps_est=None, rev_est=None)],
                     _et(REPORT, 16))
    svc.observe_rows(REPORT, [twin, _row("AVGO", REPORT, eps=1.69, eps_est=None, rev_est=None)],
                     _et(REPORT, 17))
    (snap,) = svc.landed_snapshots(_et(REPORT, 17))
    assert (snap.eps.actual, snap.eps.estimate, snap.eps.surprise_pct) == (1.69, None, None)
    assert snap.status == els.STATUS_REPORTED


def test_the_guard_only_consensus_also_judges_a_filing_figure():
    pair = [_row("AVGO", REPORT, eps_est=1.65, rev_est=None),
            _row("AVGO", REPORT, eps=0.169, eps_est=None, rev_est=None)]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, pair, now)
    svc.absorb_filing("AVGO", REPORT, efs.FilingFigures(
        accession="0001730168-26-000123", accepted_at=None, url=None, eps_adjusted=1.70), now)
    snap = svc.snapshot("AVGO", REPORT, now)
    assert (snap.eps.actual, snap.eps.source, snap.eps.estimate) == (1.70, els.SOURCE_FILING, None)
    other = els.EarningsLiveService()
    other.observe_rows(REPORT, pair, now)
    other.absorb_filing("AVGO", REPORT, efs.FilingFigures(
        accession="0001730168-26-000123", accepted_at=None, url=None, eps_adjusted=0.17), now)
    assert other.snapshot("AVGO", REPORT, now).eps.checking


def test_the_shown_consensus_beats_a_guard_only_one():
    """The figure's own row knows its consensus; a stale twin must not hold a real figure."""
    pair = [_row("TINY", REPORT, eps_est=1.65, rev_est=16.6e9),
            _row("TINY", REPORT, eps=0.17, eps_est=0.16, rev=1.71e9, rev_est=1.7e9)]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, pair, now)
    snap = svc.snapshot("TINY", REPORT, now)
    assert (snap.eps.actual, snap.eps.estimate) == (0.17, 0.16) and not snap.eps.checking
    assert (snap.revenue.actual, snap.revenue.estimate) == (1.71e9, 1.7e9)
    assert not snap.revenue.checking


@pytest.mark.parametrize("listed_first", [False, True])
def test_the_row_with_more_results_decides_a_conflict(listed_first):
    both = _row("INTC", REPORT, eps=0.44, rev=17.1e9)
    newer = {**_row("INTC", REPORT, eps=0.40, eps_est=0.35), "lastUpdated": "2026-10-30"}
    rows = [both, newer] if listed_first else [newer, both]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, rows, now)
    _assert_the_reported_rows_figures(svc.snapshot("INTC", REPORT, now))
    assert els.one_row_per_report(rows, REPORT)[("INTC", REPORT)] is both


@pytest.mark.parametrize("newest_first", [False, True])
@pytest.mark.parametrize("eps", [None, 0.44], ids=["pending", "reported"])
def test_the_newest_of_two_equal_rows_decides(eps, newest_first):
    old = {**_row("INTC", REPORT, eps=eps and 0.40, eps_est=0.35), "lastUpdated": "2026-10-01"}
    new = {**_row("INTC", REPORT, eps=eps, eps_est=0.41), "lastUpdated": "2026-10-20"}
    rows = [new, old] if newest_first else [old, new]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, rows, now)
    snap = svc.snapshot("INTC", REPORT, now)
    assert (snap.eps.actual, snap.eps.estimate) == (eps, 0.41)
    assert els.one_row_per_report(rows, REPORT)[("INTC", REPORT)] is new


@pytest.mark.parametrize("held_first", [False, True])
def test_a_pair_whose_one_good_figure_is_beside_a_held_one_is_not_a_landing(held_first):
    """The decision belongs to the group. Row by row, the held EPS would read as "seen
    pending" a moment before the revenue beside it read as the landing."""
    held = {**_row("INTC", REPORT, eps=0.169, eps_est=1.65),
            "lastUpdated": "2026-10-01" if held_first else "2026-10-20"}
    good = {**_row("INTC", REPORT, rev=17.1e9), "lastUpdated": "2026-10-10"}
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [held, good], now)
    snap = svc.snapshot("INTC", REPORT, now)
    assert snap.eps.checking and snap.revenue.actual == 17.1e9
    assert svc.landed_snapshots(now) == []


def test_a_figure_held_as_checking_still_counts_as_pending():
    """A dropped-digit EPS is held, so nothing has landed: the real figure after it does."""
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.169, eps_est=1.65, rev_est=None)],
                     _et(REPORT, 16))
    assert svc.landed_snapshots(_et(REPORT, 16)) == []
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=1.69, eps_est=1.65, rev_est=None)],
                     _et(REPORT, 17))
    assert [s.symbol for s in svc.landed_snapshots(_et(REPORT, 17))] == ["INTC"]


def test_a_new_batch_that_folds_several_symbols_decides_each_on_its_own():
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [_row("AAPL", REPORT)], _et(REPORT, 9))
    svc.observe_rows(REPORT, [*_pair(False), _row("AAPL", REPORT, eps=1.9, rev=1e11)], now)
    assert [s.symbol for s in svc.landed_snapshots(now)] == ["AAPL"]


def test_a_junk_row_inside_a_group_changes_nothing():
    pair = [None, "x", 7, {"symbol": "INTC"}, {"date": REPORT.isoformat()}, *_pair(True),
            {**_row("INTC", REPORT, eps=float("nan"), rev=float("inf"), eps_est="x", rev_est=True),
             "lastUpdated": {"bad": 1}}]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, pair, now)
    _assert_the_reported_rows_figures(svc.snapshot("INTC", REPORT, now))


# ── one row per report (the card and the sheet) ──────────────────────────────


def test_one_row_per_report_keeps_the_reported_row_and_the_reschedule_dates():
    later = REPORT + timedelta(days=3)
    picked = els.one_row_per_report(
        [*_pair(False), _row("INTC", later), _row("AAPL", REPORT)], REPORT, symbols={"INTC"})
    assert list(picked) == [("INTC", REPORT), ("INTC", later)], "first-seen order, watched only"
    assert picked[("INTC", REPORT)]["epsActual"] == 0.44


def test_a_timestamp_date_merges_with_its_plain_twin():
    stamped = {**_row("INTC", REPORT, eps=0.44), "date": f"{REPORT.isoformat()} 16:00:00"}
    picked = els.one_row_per_report([_row("INTC", REPORT), stamped], REPORT)
    assert picked == {("INTC", REPORT): stamped}


def test_a_future_dated_row_with_actuals_outranks_nothing():
    """Actuals on a future date are garbage (`earnings_window_service._absorb`), so only FMP's
    own touch can pick between it and a corrected pending row."""
    future = REPORT + timedelta(days=2)
    garbage = _row("INTC", future, eps=0.44)
    corrected = {**_row("INTC", future), "lastUpdated": REPORT.isoformat()}
    garbage["lastUpdated"] = PREV.isoformat()
    for rows in ([garbage, corrected], [corrected, garbage]):
        assert els.one_row_per_report(rows, REPORT)[("INTC", future)] is corrected


def test_a_tie_keeps_the_first_listed_row_on_the_card_and_in_the_record():
    first = _row("INTC", REPORT, eps_est=0.35)
    second = _row("INTC", REPORT, eps_est=0.41)
    assert els.one_row_per_report([first, second], REPORT)[("INTC", REPORT)] is first
    svc = _svc()
    svc.observe_rows(REPORT, [first, second], _et(REPORT, 9))
    assert svc.snapshot("INTC", REPORT, _et(REPORT, 9)).eps.estimate == 0.35


def test_one_row_per_report_skips_junk_and_never_raises():
    rows = [None, "garbage", 7, {}, {"symbol": None}, {"symbol": "  ", "date": "2026-10-29"},
            {"symbol": "INTC", "date": 20261029, "epsActual": "x", "revenueActual": {},
             "lastUpdated": 5},
            {"symbol": "INTC", "date": "soon", "epsActual": float("nan"),
             "lastUpdated": ["x"]}]
    assert set(els.one_row_per_report(rows, REPORT)) == {("INTC", None)}


# ── a zero revenue ───────────────────────────────────────────────────────────


def test_a_zero_revenue_against_a_material_consensus_is_not_a_result():
    svc = _svc()
    now = _et(REPORT, 16, 30)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44, rev=0)], now)
    snap = svc.snapshot("INTC", REPORT, now)
    assert snap.revenue.actual is None and snap.revenue.surprise_pct is None
    assert snap.status == els.STATUS_LIVE
    assert svc.pending_symbols(REPORT, ["INTC"]) == ["INTC"], "the hot refresh keeps asking"


@pytest.mark.parametrize("rev_est", [16.6e9, 30e6, None])
def test_a_later_zero_never_takes_a_revenue_back(rev_est):
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44, rev=17.1e9)], now)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44, rev=0, rev_est=rev_est)],
                     now + timedelta(minutes=5))
    snap = svc.snapshot("INTC", REPORT, now + timedelta(minutes=5))
    assert (snap.revenue.actual, snap.revenue.estimate) == (17.1e9, 16.6e9)


def test_a_zero_placeholder_is_not_a_landing():
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    svc.observe_rows(REPORT, [_row("INTC", REPORT, rev=0)], _et(REPORT, 16, 30))
    assert svc.landed_snapshots(_et(REPORT, 16, 31)) == []
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44, rev=17.1e9)], _et(REPORT, 17))
    assert [s.symbol for s in svc.landed_snapshots(_et(REPORT, 17))] == ["INTC"]


def test_a_zero_revenue_against_a_tiny_consensus_still_shows():
    """A pre-revenue name really can report nothing; hiding that hides a real result."""
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [_row("JOBY", REPORT, eps=-0.2, eps_est=-0.2, rev=0, rev_est=30e6)],
                     now)
    snap = svc.snapshot("JOBY", REPORT, now)
    assert snap.revenue.actual == 0.0 and snap.status == els.STATUS_REPORTED


@pytest.mark.parametrize("rev_est,stored", [(99.9e6, 0.0), (100e6, None)])
def test_the_zero_revenue_floor_is_the_plausibility_guards_own(rev_est, stored):
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.1, rev=0, rev_est=rev_est)], now)
    assert svc.snapshot("INTC", REPORT, now).revenue.actual == stored


def test_a_real_figure_replaces_a_held_zero():
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [_row("JOBY", REPORT, rev=0, rev_est=30e6)], now)
    svc.observe_rows(REPORT, [_row("JOBY", REPORT, rev=2e6, rev_est=31e6)], now)
    snap = svc.snapshot("JOBY", REPORT, now)
    assert (snap.revenue.actual, snap.revenue.estimate) == (2e6, 31e6)
    svc.observe_rows(REPORT, [_row("JOBY", REPORT, rev=0, rev_est=31e6)], now)
    assert svc.snapshot("JOBY", REPORT, now).revenue.actual == 2e6


def test_a_zero_revenue_with_no_consensus_to_judge_it_by_is_not_a_result():
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, rev=0, rev_est=None)], now)
    assert svc.snapshot("INTC", REPORT, now).revenue.actual is None
    assert svc.landed_snapshots(now) == []


def test_a_material_consensus_on_the_record_refuses_a_zero_on_a_row_with_none():
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    svc.observe_rows(REPORT, [_row("INTC", REPORT, rev=0, rev_est=None)], now)
    snap = svc.snapshot("INTC", REPORT, now)
    assert snap.revenue.actual is None and snap.revenue.estimate == 16.6e9


def test_a_tiny_consensus_on_the_record_lets_a_zero_stand_on_a_row_with_none():
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [_row("JOBY", REPORT, rev_est=30e6)], _et(REPORT, 9))
    svc.observe_rows(REPORT, [_row("JOBY", REPORT, eps=-0.2, rev=0, rev_est=None)], now)
    snap = svc.snapshot("JOBY", REPORT, now)
    assert (snap.revenue.actual, snap.revenue.estimate) == (0.0, 30e6)


@pytest.mark.parametrize("reported_first", **_ORDERS)
def test_a_zero_row_never_ranks_as_the_reported_row(reported_first):
    """FMP touching the zero row last must not put it ahead of a row with a real result, even
    one carrying a single metric, and its consensus must not get behind the real actual."""
    zero = {**_row("INTC", REPORT, rev=0, eps_est=0.35, rev_est=15.9e9), "lastUpdated": "2026-10-30"}
    reported = _row("INTC", REPORT, eps=0.44)
    rows = [reported, zero] if reported_first else [zero, reported]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, rows, now)
    snap = svc.snapshot("INTC", REPORT, now)
    assert (snap.eps.actual, snap.eps.estimate, snap.eps.surprise_pct) == (0.44, 0.41, 7.32)
    assert (snap.revenue.actual, snap.revenue.estimate) == (None, 16.6e9)
    assert els.one_row_per_report(rows, REPORT)[("INTC", REPORT)] is reported


@pytest.mark.parametrize("reported_first", **_ORDERS)
def test_a_zero_beside_a_pending_twin_is_still_pending(reported_first):
    """Neither row carries a result, so what comes next is a real landing."""
    rows = [_row("INTC", REPORT), _row("INTC", REPORT, rev=0)]
    svc = _svc()
    svc.observe_rows(REPORT, rows[::-1] if reported_first else rows, _et(REPORT, 9))
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44, rev=17.1e9)], _et(REPORT, 17))
    (snap,) = svc.landed_snapshots(_et(REPORT, 17))
    assert (snap.eps.actual, snap.revenue.actual) == (0.44, 17.1e9)


def _zero_and_twin(zero_newest: bool, twin_est: float = 30e6, zero_est=None) -> List[dict]:
    """A zero-revenue row with no consensus beside the pending row that has one."""
    newer, older = "2026-10-20", "2026-10-01"
    return [
        {**_row("JOBY", REPORT, rev=0, eps_est=None, rev_est=zero_est),
         "lastUpdated": newer if zero_newest else older},
        {**_row("JOBY", REPORT, eps_est=None, rev_est=twin_est),
         "lastUpdated": older if zero_newest else newer},
    ]


@pytest.mark.parametrize("zero_newest", [False, True])
@pytest.mark.parametrize("reported_first", **_ORDERS)
def test_a_zero_is_judged_against_the_whole_response_on_its_first_fold(zero_newest, reported_first):
    """A repeat must not decide differently: a refusal first and a store on the repeat reads
    as a landing, and FMP's touch date must not choose between the two."""
    rows = _zero_and_twin(zero_newest)
    rows = rows[::-1] if reported_first else rows
    svc = _svc()
    now = _et(REPORT, 15)
    for _ in range(3):
        svc.observe_rows(REPORT, rows, now)
        snap = svc.snapshot("JOBY", REPORT, now)
        assert (snap.revenue.actual, snap.revenue.surprise_pct) == (0.0, None)
        assert svc.landed_snapshots(now) == []


@pytest.mark.parametrize("zero_newest", [False, True])
def test_a_zero_beside_a_material_consensus_is_refused_on_every_fold(zero_newest):
    rows = _zero_and_twin(zero_newest, twin_est=16.6e9)
    svc = _svc()
    now = _et(REPORT, 15)
    for _ in range(3):
        svc.observe_rows(REPORT, rows, now)
        snap = svc.snapshot("JOBY", REPORT, now)
        assert (snap.revenue.actual, snap.revenue.estimate) == (None, 16.6e9)
    assert svc.landed_snapshots(now) == []


@pytest.mark.parametrize("zero_newest", [False, True])
def test_any_material_consensus_in_the_response_refuses_a_zero(zero_newest):
    """The zero's own tiny estimate does not outvote its twin, whichever FMP touched last."""
    svc = _svc()
    now = _et(REPORT, 15)
    svc.observe_rows(REPORT, [_row("JOBY", REPORT)], _et(REPORT, 9))
    svc.observe_rows(REPORT, _zero_and_twin(zero_newest, twin_est=16.6e9, zero_est=30e6), now)
    assert svc.snapshot("JOBY", REPORT, now).revenue.actual is None
    assert svc.landed_snapshots(now) == []


@pytest.mark.parametrize("zero_newest", [False, True])
def test_a_real_revenue_beats_a_zero_whichever_fmp_touched_last(zero_newest):
    newer, older = "2026-10-20", "2026-10-01"
    rows = [{**_row("JOBY", REPORT, eps=-0.2, rev=0, rev_est=30e6),
             "lastUpdated": newer if zero_newest else older},
            {**_row("JOBY", REPORT, rev=5e6, rev_est=None),
             "lastUpdated": older if zero_newest else newer}]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, rows, now)
    snap = svc.snapshot("JOBY", REPORT, now)
    assert (snap.revenue.actual, snap.revenue.estimate, snap.eps.actual) == (5e6, None, -0.2)


# A both-actuals-zero row: FMP's unfilled shape, or a true breakeven against a tiny consensus.


@pytest.mark.parametrize("rev", [0, -5e6], ids=["zero", "negative"])
def test_an_eps_of_zero_beside_a_refused_revenue_is_not_a_landing(rev):
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0, rev=rev)], _et(REPORT, 16, 30))
    snap = svc.snapshot("INTC", REPORT, _et(REPORT, 16, 30))
    assert (snap.eps.actual, snap.revenue.actual) == (None, None)
    assert (snap.eps.estimate, snap.revenue.estimate) == (0.41, 16.6e9)
    assert snap.status == els.STATUS_LIVE
    assert svc.landed_snapshots(_et(REPORT, 16, 31)) == []
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44, rev=17.1e9)], _et(REPORT, 17))
    (snap,) = svc.landed_snapshots(_et(REPORT, 17))
    assert (snap.eps.actual, snap.revenue.actual) == (0.44, 17.1e9)


@pytest.mark.parametrize("rev_est", [16.6e9, 30e6, None])
@pytest.mark.parametrize("held_revenue", [17.1e9, None])
def test_a_later_double_zero_never_takes_a_figure_back(rev_est, held_revenue):
    """Whatever the consensus says about a zero, it never replaces a real figure."""
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=1.69, eps_est=1.6, rev=held_revenue)], now)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0, eps_est=1.6, rev=0, rev_est=rev_est)],
                     now + timedelta(minutes=5))
    snap = svc.snapshot("INTC", REPORT, now + timedelta(minutes=5))
    assert (snap.eps.actual, snap.eps.estimate) == (1.69, 1.6)
    assert snap.revenue.actual == held_revenue


@pytest.mark.parametrize("newest_listed_first", [False, True])
@pytest.mark.parametrize("rev_est", [16.6e9, 30e6], ids=["material", "tiny"])
def test_a_double_zero_row_never_ranks_as_the_reported_row(newest_listed_first, rev_est):
    """Tied on results it would fold last and take a real EPS back, and the card would pick it."""
    real = {**_row("INTC", REPORT, eps=1.69, eps_est=1.6, rev_est=rev_est),
            "lastUpdated": "2026-10-01"}
    zero = {**_row("INTC", REPORT, eps=0, eps_est=1.6, rev=0, rev_est=rev_est),
            "lastUpdated": "2026-10-20"}
    rows = [zero, real] if newest_listed_first else [real, zero]
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, rows, now)
    assert svc.snapshot("INTC", REPORT, now).eps.actual == 1.69
    assert els.one_row_per_report(rows, REPORT)[("INTC", REPORT)] is real


@pytest.mark.parametrize("reported_first", **_ORDERS)
def test_a_double_zero_beside_a_pending_twin_is_still_pending(reported_first):
    rows = [_row("INTC", REPORT), _row("INTC", REPORT, eps=0, rev=0)]
    svc = _svc()
    now = _et(REPORT, 9)
    for _ in range(2):
        svc.observe_rows(REPORT, rows[::-1] if reported_first else rows, now)
    assert svc.snapshot("INTC", REPORT, now).eps.actual is None
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44, rev=17.1e9)], _et(REPORT, 17))
    assert [s.symbol for s in svc.landed_snapshots(_et(REPORT, 17))] == ["INTC"]


def test_a_breakeven_beside_a_real_revenue_stays_a_result():
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.0, eps_est=-0.02, rev=17.1e9)], now)
    snap = svc.snapshot("INTC", REPORT, now)
    assert (snap.eps.actual, snap.revenue.actual) == (0.0, 17.1e9)
    assert snap.status == els.STATUS_REPORTED


def test_a_breakeven_on_no_revenue_stands_against_a_tiny_consensus():
    """A pre-revenue name can report exactly nothing, on both lines."""
    svc = _svc()
    now = _et(REPORT, 17)
    svc.observe_rows(REPORT, [_row("JOBY", REPORT, eps=0.0, eps_est=-0.02, rev=0, rev_est=30e6)],
                     now)
    snap = svc.snapshot("JOBY", REPORT, now)
    assert (snap.eps.actual, snap.revenue.actual) == (0.0, 0.0)
    assert snap.status == els.STATUS_REPORTED


def test_a_refused_eps_is_logged_once_with_its_symbol_and_day(caplog):
    svc = _svc()
    now = _et(REPORT, 17)
    with caplog.at_level(logging.WARNING, logger=els.logger.name):
        for _ in range(3):
            svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0, rev=0)], now)
    (line,) = [r.getMessage() for r in caplog.records if "EPS" in r.getMessage()]
    assert "INTC" in line and REPORT.isoformat() in line


def test_a_refused_revenue_is_logged_once_with_its_symbol_and_day(caplog):
    """The fold runs every minute, and whether FMP ever sends this is exactly what a log
    has to answer."""
    svc = _svc()
    now = _et(REPORT, 17)
    with caplog.at_level(logging.WARNING, logger=els.logger.name):
        for _ in range(3):
            svc.observe_rows(REPORT, [_row("INTC", REPORT, rev=0)], now)
    (line,) = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert "INTC" in line and REPORT.isoformat() in line


def test_a_stored_zero_is_not_reported_as_refused(caplog):
    with caplog.at_level(logging.WARNING, logger=els.logger.name):
        _svc().observe_rows(REPORT, [_row("JOBY", REPORT, rev=0, rev_est=30e6)], _et(REPORT, 17))
    assert not [r for r in caplog.records if "refused" in r.getMessage()]


def test_a_negative_revenue_is_refused_and_does_not_rank():
    svc = _svc()
    now = _et(REPORT, 17)
    rows = [{**_row("INTC", REPORT, rev=-5e6, eps_est=0.35, rev_est=15.9e9),
             "lastUpdated": "2026-10-30"},
            _row("INTC", REPORT, eps=0.44)]
    svc.observe_rows(REPORT, rows, now)
    snap = svc.snapshot("INTC", REPORT, now)
    assert (snap.eps.estimate, snap.revenue.actual, snap.revenue.estimate) == (0.41, None, 16.6e9)
    assert els.one_row_per_report(rows, REPORT)[("INTC", REPORT)] is rows[1]
    svc.observe_rows(REPORT, [_row("AAPL", REPORT, rev=-5e6)], now)
    assert svc.snapshot("AAPL", REPORT, now).revenue.actual is None


# ── one response decides once ────────────────────────────────────────────────

_ROW_SHAPES = list(itertools.product(
    (None, 0, 0.44),                    # epsActual
    (None, 0, 17.1e9, 1.71e9),          # revenueActual: none, unfilled, real, dropped digit
    (None, 0.41),                       # epsEstimated
    (None, 30e6, 16.6e9),               # revenueEstimated: none, tiny, material
))


def _shape_row(shape: tuple, touched: str) -> dict:
    eps, rev, eps_est, rev_est = shape
    return {**_row("JOBY", REPORT, eps=eps, rev=rev, eps_est=eps_est, rev_est=rev_est),
            "lastUpdated": touched}


def _outcome(svc: els.EarningsLiveService, now: datetime) -> tuple:
    snap = svc.snapshot("JOBY", REPORT, now)
    return (snap.eps, snap.revenue, snap.status, svc.pending_symbols(REPORT, ["JOBY"]),
            [s.symbol for s in svc.landed_snapshots(now)])


@pytest.mark.parametrize("seen_pending", [False, True], ids=["first_sight", "after_pending"])
def test_every_pair_decides_on_its_first_fold_in_any_row_order(seen_pending):
    """Every feed build and every tick re-folds the same rows, so a second answer from one
    response is a landing nobody saw, or a card and a sheet that disagree on one memo."""
    now = _et(REPORT, 15)
    for first, second in itertools.product(_ROW_SHAPES, _ROW_SHAPES):
        rows = [_shape_row(first, "2026-10-01"), _shape_row(second, "2026-10-20")]
        outcomes = []
        for listed in (rows, rows[::-1]):
            svc = els.EarningsLiveService()
            if seen_pending:
                svc.observe_rows(REPORT, [_row("JOBY", REPORT)], _et(REPORT, 9))
            svc.observe_rows(REPORT, listed, now)
            once = _outcome(svc, now)
            svc.observe_rows(REPORT, listed, now)
            assert _outcome(svc, now) == once, (first, second)
            outcomes.append(once)
            if not seen_pending:
                assert once[-1] == [], (first, second)
        assert outcomes[0] == outcomes[1], (first, second)


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


_FILED_ADJUSTED = efs.FilingFigures(accession="0000050863-26-000123", accepted_at=None, url=None,
                                    revenue=17.1e9, eps_adjusted=0.44)
_FILED_GAAP = efs.FilingFigures(accession="0000050863-26-000124", accepted_at=None, url=None,
                                revenue=17.1e9, eps_gaap=0.38)


@pytest.mark.parametrize("figures", [_FILED_ADJUSTED, _FILED_GAAP], ids=["adjusted", "gaap_only"])
@pytest.mark.asyncio
async def test_a_filing_complete_report_still_gets_fmps_next_morning_confirmation(figures):
    svc = _svc()
    svc.observe_rows(PREV, [_row("INTC", PREV)], _et(PREV, 9), fetched=True)
    svc.absorb_filing("INTC", PREV, figures, _et(PREV, 16, 3))
    assert svc.snapshot("INTC", PREV, _et(REPORT, 8)).preliminary is True
    assert svc.pending_symbols(PREV, ["INTC"]) == [], "the SEC watch still stops"
    svc._fetched[PREV] = time.monotonic() - (els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1)
    assert PREV in svc.due_days(_et(REPORT, 8), ["INTC"]), "hot before noon"
    svc._fetched[PREV] = time.monotonic() - (els.EARNINGS_LIVE_COLD_TTL_SECONDS + 1)
    assert PREV in svc.due_days(_et(REPORT, 14), ["INTC"]), "cold after noon"
    fmp = _FMP({PREV: [_row("INTC", PREV, eps=0.45, rev=17.2e9)]})
    await svc.refresh(_et(REPORT, 8), fmp=fmp, symbols=["INTC"])
    snap = svc.snapshot("INTC", PREV, _et(REPORT, 8, 1))
    assert snap.preliminary is False and snap.eps.source == els.SOURCE_FMP
    assert snap.eps.basis == els.BASIS_ADJUSTED and snap.eps.surprise_pct is not None
    assert PREV not in svc.due_days(_et(REPORT, 14), ["INTC"]), "confirmed: nothing left to ask"


def test_a_filing_complete_report_stays_on_the_hot_fetch_on_its_own_day():
    svc = _svc()
    svc._fetched[PREV] = time.monotonic()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9), fetched=True)
    svc.absorb_filing("INTC", REPORT, _FILED_ADJUSTED, _et(REPORT, 16, 3))
    assert svc.pending_symbols(REPORT, ["INTC"]) == [], "the SEC watch stops"
    svc._fetched[REPORT] = time.monotonic() - (els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1)
    at = _et(REPORT, 16, 10)
    assert svc.due_days(at, ["INTC"]) == [REPORT]
    assert svc.due_days(at, ["intc"]) == [REPORT], "a watched ticker in lower case"
    assert svc.due_days(at, ["AAPL"]) == [], "not watched"
    assert svc.due_days(_et(REPORT, 22), ["INTC"]) == [], "the window has closed"
    svc._fetched[REPORT] = time.monotonic()
    assert svc.due_days(at, ["INTC"]) == [], "just asked"


def test_a_filing_figure_standing_in_for_a_held_fmp_figure_keeps_the_fetch_on():
    svc = _svc()
    svc.observe_rows(PREV, [_row("INTC", PREV, eps=0.041, rev=17.2e9)], _et(PREV, 17),
                     fetched=True)
    svc.absorb_filing("INTC", PREV, _FILED_ADJUSTED, _et(PREV, 17, 1))
    snap = svc.snapshot("INTC", PREV, _et(REPORT, 8))
    assert (snap.eps.source, snap.preliminary) == (els.SOURCE_FILING, True)
    assert svc.pending_symbols(PREV, ["INTC"]) == []
    svc._fetched[PREV] = time.monotonic() - (els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1)
    assert PREV in svc.due_days(_et(REPORT, 8), ["INTC"])


def test_a_finished_report_with_no_revenue_consensus_is_not_polled():
    svc = _svc()
    svc.observe_rows(PREV, [_row("INTC", PREV, eps=0.44, rev=None, rev_est=None)],
                     _et(PREV, 17), fetched=True)
    svc._fetched[PREV] = time.monotonic() - (els.EARNINGS_LIVE_COLD_TTL_SECONDS + 1)
    assert PREV not in svc.due_days(_et(REPORT, 8), ["INTC"])


def test_a_held_fmp_figure_is_still_re_read():
    svc = _svc()
    svc.observe_rows(PREV, [_row("INTC", PREV, eps=0.041, rev=17.2e9)],
                     _et(PREV, 17), fetched=True)
    assert svc.snapshot("INTC", PREV, _et(REPORT, 8)).eps.checking is True
    svc._fetched[PREV] = time.monotonic() - (els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1)
    assert PREV in svc.due_days(_et(REPORT, 8), ["INTC"])


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
async def test_a_record_stays_one_trading_day_past_the_live_days_for_the_sheet_alone():
    """A held push can wake on that day. Nothing else reads it: the sender's memory has moved
    on, so a report handed over from here would read as new on every round."""
    svc = _svc()
    svc.observe_rows(PREV, [_row("INTC", PREV)], _et(PREV, 9))
    svc.observe_rows(PREV, [_row("INTC", PREV, eps=0.3)], _et(PREV, 17))
    assert [s.day for s in svc.landed_snapshots(_et(PREV, 17))] == [PREV]

    at = _et(NEXT, 9)
    assert PREV not in els.live_days(NEXT) and PREV in els.sheet_days(NEXT)
    await svc.refresh(at, fmp=_FMP({REPORT: [], NEXT: []}), symbols=["INTC"])

    assert svc.snapshot("INTC", PREV, at).eps.actual == 0.3
    assert svc.landed_snapshots(at) == [], "out of the live days: nothing to tell any more"
    assert svc.recent_snapshots(["INTC"], at) == [], "and no card"


@pytest.mark.asyncio
async def test_records_outside_the_sheet_days_are_pruned_at_the_day_roll():
    svc = _svc()
    svc.observe_rows(PREV, [_row("INTC", PREV, eps=0.3)], _et(PREV, 17))
    after = NEXT + timedelta(days=4)    # Tuesday: back to Friday, so Wednesday's has gone
    await svc.refresh(_et(after, 9), fmp=_FMP({}), symbols=["INTC"])
    assert svc.snapshot("INTC", PREV, _et(after, 9)) is None


@pytest.mark.asyncio
async def test_a_day_that_rolls_out_of_the_live_days_is_never_fetched_again():
    """It kept its stamp, so a sheet asking for it is no first look, and a report on it that
    still wants FMP costs nothing: the sheet shows what it was left with."""
    svc = _svc()
    svc.observe_rows(PREV, [_row("INTC", PREV)], _et(PREV, 9))
    await svc.refresh(_et(REPORT, 8), fmp=_FMP({PREV: [_row("INTC", PREV)]}), symbols=["INTC"])
    assert PREV in svc._fetched
    fetched_at = svc._fetched[PREV]

    at = _et(NEXT, 13)
    svc.sheet_asked(PREV, NEXT)
    assert PREV not in svc.due_days(at, ["INTC"]), "not hot, not cold and not a first look"
    svc._prune(at)
    assert svc._fetched[PREV] == fetched_at
    svc._fetched[PREV] -= els.EARNINGS_LIVE_COLD_TTL_SECONDS * 2
    assert PREV not in svc.due_days(at, ["INTC"]) and not svc.awaits_first_load(PREV, NEXT)


@pytest.mark.asyncio
async def test_after_a_restart_only_a_sheet_asking_loads_the_day_past_the_live_days():
    """So a push that wakes after a restart in the night still opens its figures. Nothing about
    the day is held or told, and with nobody asking it costs nothing."""
    svc = _svc()
    at = _et(NEXT, 9)
    assert PREV not in svc.due_days(at, ["INTC"]) and not svc.awaits_first_load(PREV, NEXT)

    svc.sheet_asked(PREV, NEXT)
    assert PREV in svc.due_days(at, []), "whatever is watched: a sheet is waiting on it"
    assert svc.awaits_first_load(PREV, NEXT)

    fmp = _FMP({PREV: [_row("INTC", PREV, eps=0.3, rev=17e9)]})
    await svc.refresh(at, fmp=fmp, symbols=["INTC"])

    assert svc.snapshot("INTC", PREV, at).eps.actual == 0.3
    assert not svc.awaits_first_load(PREV, NEXT) and PREV not in svc.due_days(at, ["INTC"])
    assert svc.landed_snapshots(at) == [], "first seen reported, and out of the live days besides"
    svc.sheet_asked(PREV, NEXT)
    assert PREV not in svc.due_days(at, ["INTC"]), "one look"


@pytest.mark.parametrize("day,why", [
    (REPORT, "a live day has its own first look"),
    (NEXT, "today is the memo's"),
    (PREV - timedelta(days=1), "past the sheet days: the loop would never end the wait"),
    (NEXT + timedelta(days=3), "a later day is the memo's"),
])
def test_only_a_day_past_the_live_days_goes_on_the_round(day, why):
    svc = _svc()
    svc.sheet_asked(day, NEXT)
    assert svc._sheet_asked == set(), why


@pytest.mark.asyncio
async def test_a_blank_first_look_at_that_day_ends_like_any_other():
    """The wait must survive the prune every round runs, or the look would never end."""
    svc = _svc()
    at = _et(NEXT, 9)
    svc.sheet_asked(PREV, NEXT)
    fmp = _FMP()
    await svc.refresh(at, fmp=fmp, symbols=[])
    assert PREV in svc._blank_since and svc.awaits_first_load(PREV, NEXT)

    svc._blank_since[PREV] -= els.EARNINGS_LIVE_BLANK_LOAD_WAIT_SECONDS
    await svc.refresh(at, fmp=fmp, symbols=[])
    assert not svc.awaits_first_load(PREV, NEXT) and PREV not in svc.due_days(at, [])


@pytest.mark.asyncio
async def test_a_day_a_sheet_asked_for_leaves_with_the_sheet_days():
    svc = _svc()
    svc.sheet_asked(PREV, NEXT)
    later = NEXT + timedelta(days=4)
    assert PREV in svc._sheet_asked
    await svc.refresh(_et(later, 9), fmp=_FMP({}), symbols=[])
    assert svc._sheet_asked == set()


@pytest.mark.asyncio
async def test_the_stamps_stay_bounded_to_the_sheet_days():
    svc = _svc()
    await svc.refresh(_et(REPORT, 9), fmp=_FMP({PREV: [_row("INTC", PREV)]}), symbols=["INTC"])
    held = set(svc._fetched)
    svc._blank_since[PREV] = 1.0
    later = NEXT + timedelta(days=11)
    svc._prune(_et(later, 9))
    assert held and not (held & set(svc._fetched)) and not svc._blank_since


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


# ── one card per report, on the record's estimates ───────────────────────────


def _feed_service(monkeypatch, at: datetime, rows: List[dict]) -> ts.TrackingService:
    monkeypatch.setattr(ts, "_et_today", lambda: REPORT)
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(at))
    svc_obj = ts.TrackingService.__new__(ts.TrackingService)
    by_day: Dict[date, List[dict]] = {}
    for row in rows:
        by_day.setdefault(date.fromisoformat(row["date"][:10]), []).append(row)
    svc_obj.fmp = _FMP(by_day)
    return svc_obj


@pytest.mark.asyncio
@pytest.mark.parametrize("reported_first", **_ORDERS)
async def test_a_duplicate_pair_is_one_card_with_the_reported_rows_estimates(
        monkeypatch, reported_first):
    svc_obj = _feed_service(monkeypatch, _et(REPORT, 17), _pair(reported_first))
    (card,) = await svc_obj._get_earnings_alerts(["INTC"])
    assert (card.eps_estimate, card.revenue_estimate) == (0.41, 16.6e9)
    assert (card.eps_surprise_pct, card.revenue_surprise_pct) == (7.32, 3.01)
    assert card.earnings_status == "reported"


@pytest.mark.asyncio
async def test_a_reschedules_two_dates_stay_two_cards(monkeypatch):
    first, second = REPORT + timedelta(days=3), REPORT + timedelta(days=5)
    svc_obj = _feed_service(monkeypatch, _et(REPORT, 9),
                            [_row("INTC", first), _row("INTC", second)])
    cards = await svc_obj._get_earnings_alerts(["INTC"])
    assert [c.report_date for c in cards] == [first.isoformat(), second.isoformat()]


@pytest.mark.asyncio
@pytest.mark.parametrize("newest_first", [False, True])
async def test_a_future_pair_is_one_card_on_the_newest_row(monkeypatch, newest_first):
    day = REPORT + timedelta(days=4)
    stale = {**_row("INTC", day, eps_est=0.35, rev_est=15.9e9), "lastUpdated": "2026-10-01"}
    fresh = {**_row("INTC", day, eps_est=0.41, rev_est=16.6e9), "lastUpdated": "2026-10-20"}
    rows = [fresh, stale] if newest_first else [stale, fresh]
    (card,) = await _feed_service(monkeypatch, _et(REPORT, 9), rows)._get_earnings_alerts(["INTC"])
    assert (card.eps_estimate, card.revenue_estimate) == (0.41, 16.6e9)
    assert card.earnings_status == "upcoming"


@pytest.mark.asyncio
@pytest.mark.parametrize("reported_first", **_ORDERS)
async def test_with_live_off_a_pair_is_still_one_card_on_the_reported_row(
        monkeypatch, reported_first):
    from app.config import settings

    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", False)
    svc_obj = _feed_service(monkeypatch, _et(REPORT, 17), _pair(reported_first))
    (card,) = await svc_obj._get_earnings_alerts(["INTC"])
    assert (card.eps_estimate, card.revenue_estimate) == (0.41, 16.6e9)
    assert card.earnings_status is None, "no live fields without the live records"


@pytest.mark.asyncio
async def test_live_off_feed_cards_carry_no_report_date(monkeypatch):
    """The sheet tells the two feeds apart by this: every card has a date while live is on, and
    a card with none is what the live switch off looks like."""
    later = REPORT + timedelta(days=4)
    svc_obj = _feed_service(monkeypatch, _et(REPORT, 9), [_row("INTC", REPORT), _row("AAPL", later)])
    _svc().observe_rows(PREV, [_row("MSFT", PREV, eps=0.44)], _et(REPORT, 8))
    on = await svc_obj._get_earnings_alerts(["INTC", "AAPL"])
    recent = await svc_obj._get_recent_earnings_alerts(["MSFT"])
    assert [c.report_date for c in on + recent] == [
        REPORT.isoformat(), later.isoformat(), PREV.isoformat()]

    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", False)
    off = await svc_obj._get_earnings_alerts(["INTC", "AAPL"])
    assert [(c.ticker, c.report_date) for c in off] == [("INTC", None), ("AAPL", None)]
    assert await svc_obj._get_recent_earnings_alerts(["MSFT"]) == []


def _no_history(monkeypatch) -> None:
    async def _no_extras(self, symbol, report_day, *, loader=None):
        return None

    monkeypatch.setattr(els.EarningsLiveService, "history_extras", _no_extras)


@pytest.mark.asyncio
@pytest.mark.parametrize("reported_first", **_ORDERS)
async def test_a_guard_only_consensus_reaches_neither_the_card_nor_the_sheet(
        monkeypatch, reported_first):
    _no_history(monkeypatch)
    pair = [_row("INTC", REPORT, eps_est=0.35, rev_est=15.9e9),
            _row("INTC", REPORT, eps=0.44, rev=17.1e9, eps_est=None, rev_est=None)]
    svc_obj = _feed_service(monkeypatch, _et(REPORT, 17), pair[::-1] if reported_first else pair)
    (card,) = await svc_obj._get_earnings_alerts(["INTC"])
    assert (card.eps_estimate, card.revenue_estimate) == (None, None)
    assert (card.eps_surprise_pct, card.revenue_surprise_pct) == (None, None)
    sheet = await svc_obj.get_earnings_live("INTC")
    assert (sheet.eps.estimate, sheet.revenue.estimate) == (None, None)
    assert (sheet.eps.surprise_pct, sheet.revenue.surprise_pct) == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("zero_newest", [False, True])
async def test_the_card_and_the_sheet_agree_on_one_memo_holding_a_zero(monkeypatch, zero_newest):
    """Each folds the same memo again: a fold that changed its mind split them."""
    _no_history(monkeypatch)
    svc_obj = _feed_service(monkeypatch, _et(REPORT, 15), _zero_and_twin(zero_newest))
    (card,) = await svc_obj._get_earnings_alerts(["JOBY"])
    sheet = await svc_obj.get_earnings_live("JOBY")
    assert card.revenue_actual == sheet.revenue.actual == 0.0
    assert card.revenue_surprise_pct is None and sheet.revenue.surprise_pct is None
    assert card.earnings_status == sheet.status == els.STATUS_LIVE


async def _card_after_own_fetch(monkeypatch, memo: dict, own: Optional[dict]):
    """A card whose memo row is older than the live memo's own fetch."""
    svc_obj = _feed_service(monkeypatch, _et(REPORT, 16, 36), [memo])
    await ts._cached_earnings_calendar(svc_obj.fmp, REPORT)        # the memo is stored first
    if own is not None:
        _svc().observe_rows(REPORT, [own], _et(REPORT, 16, 35), fetched=True)
    (card,) = await svc_obj._get_earnings_alerts(["INTC"])
    return card


@pytest.mark.asyncio
async def test_a_memo_row_with_no_consensus_keeps_the_one_the_surprise_used(monkeypatch):
    card = await _card_after_own_fetch(
        monkeypatch, _row("INTC", REPORT, eps_est=None, rev_est=None),
        _row("INTC", REPORT, eps=1.06, eps_est=1.00, rev=17.1e9, rev_est=17.0e9))
    assert (card.eps_estimate, card.eps_surprise_pct) == (1.00, 6.0)
    assert (card.revenue_estimate, card.revenue_surprise_pct) == (17.0e9, 0.59)


@pytest.mark.asyncio
async def test_a_zero_revenue_estimate_in_the_memo_never_colours_the_card(monkeypatch):
    card = await _card_after_own_fetch(
        monkeypatch, _row("INTC", REPORT, eps=0.44, rev=17.1e9, rev_est=0), None)
    assert card.revenue_estimate is None and card.revenue_surprise_pct is None


@pytest.mark.asyncio
async def test_the_card_shows_the_estimate_its_surprise_was_computed_from(monkeypatch):
    card = await _card_after_own_fetch(
        monkeypatch, _row("INTC", REPORT, eps_est=1.00, rev_est=16.6e9),
        _row("INTC", REPORT, eps=1.06, eps_est=1.10, rev=17.1e9, rev_est=17.0e9))
    assert (card.eps_estimate, card.eps_surprise_pct) == (1.10, -3.64)
    assert (card.revenue_estimate, card.revenue_surprise_pct) == (17.0e9, 0.59)


@pytest.mark.asyncio
async def test_a_future_card_keeps_its_rows_consensus(monkeypatch):
    """No record exists ahead of the day, so the row is still the only source."""
    day = REPORT + timedelta(days=4)
    (card,) = await _feed_service(monkeypatch, _et(REPORT, 9),
                                  [_row("INTC", day)])._get_earnings_alerts(["INTC"])
    assert (card.eps_estimate, card.revenue_estimate) == (0.41, 16.6e9)


# ── the tick ─────────────────────────────────────────────────────────────────


class _Watcher:
    def __init__(self, figures=None):
        self.figures = figures or {}
        self.calls: List[tuple] = []
        self.kept: Optional[List[date]] = None

    def prune(self, days):
        self.kept = list(days)

    async def run_once(self, now, symbols, day, *, sec=None, gemini=None, on_figures=None):
        self.calls.append((list(symbols), day))
        found = {s: f for s, f in self.figures.items() if s in symbols}
        for symbol, figures in found.items():
            if on_figures is not None:
                on_figures(symbol, figures)
        return found


@pytest.mark.asyncio
async def test_the_sec_watch_reads_pending_watched_symbols_and_the_next_tick_pushes(monkeypatch):
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
    await els.earnings_live_tick(now, fmp=fmp, watched=["INTC", "AAPL"], dispatch=_dispatch)
    assert watcher.calls == [] and sent == [[]], "the FMP tick never runs the SEC watch"
    stats = await els.earnings_filing_tick(now, watched=["INTC", "AAPL"])
    assert watcher.calls == [(["INTC"], REPORT)], "AAPL is complete, TSLA is not watched"
    assert stats == {"watched": 2, "pending": 1, "filings": 1}
    assert sent == [[]], "the filing tick pushes nothing itself"
    # INTC was seen pending by the FMP tick that folded the rows, so the filing is a landing.
    await els.earnings_live_tick(now + timedelta(minutes=1), fmp=fmp, watched=["INTC", "AAPL"],
                                 dispatch=_dispatch)
    assert sent == [[], ["INTC"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("reported_first", **_ORDERS)
async def test_a_restart_over_a_previous_day_pair_dispatches_nothing(monkeypatch, reported_first):
    """Monday's first tick always fetches Friday. A fresh process must read Friday's pair as
    a report already in, in either row order, and leave the push to the daily pass."""
    monday, friday = date(2026, 11, 2), NEXT
    monkeypatch.setattr(ts, "_et_today", lambda: monday)
    fmp = _FMP({friday: _pair(reported_first, friday)})
    sent: List[List[str]] = []

    async def _dispatch(snaps, now):
        sent.append([s.symbol for s in snaps])

    await els.earnings_live_tick(_et(monday, 10), fmp=fmp, watched=["INTC"], dispatch=_dispatch)
    assert sent == [[]]
    assert _svc().snapshot("INTC", friday, _et(monday, 10)).status == els.STATUS_REPORTED


@pytest.mark.asyncio
@pytest.mark.parametrize("reported_first", **_ORDERS)
async def test_a_figure_held_at_first_sight_keeps_the_sec_watch_on_it(monkeypatch, reported_first):
    monkeypatch.setattr(ts, "_et_today", lambda: REPORT)
    pair = [_row("AVGO", REPORT, eps_est=1.65, rev_est=None),
            _row("AVGO", REPORT, eps=0.169, eps_est=None, rev_est=None)]
    fmp = _FMP({REPORT: pair[::-1] if reported_first else pair, PREV: []})
    watcher = _Watcher()
    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: watcher)
    sent: List[List[str]] = []

    async def _dispatch(snaps, now):
        sent.append([s.symbol for s in snaps])

    await els.earnings_live_tick(_et(REPORT, 16, 3), fmp=fmp, watched=["AVGO"], dispatch=_dispatch)
    await els.earnings_filing_tick(_et(REPORT, 16, 3), watched=["AVGO"])
    assert watcher.calls == [(["AVGO"], REPORT)]
    assert sent == [[]]


@pytest.mark.asyncio
@pytest.mark.parametrize("zero_newest", [False, True])
@pytest.mark.parametrize("twin_est", [30e6, 16.6e9], ids=["tiny", "material"])
async def test_unchanged_rows_with_a_zero_never_land_across_real_ticks(
        monkeypatch, zero_newest, twin_est):
    """The memo fold and the own fetch of one tick see one response: the second must not
    read the first as "seen pending"."""
    monkeypatch.setattr(ts, "_et_today", lambda: REPORT)
    fmp = _FMP({REPORT: _zero_and_twin(zero_newest, twin_est=twin_est), PREV: []})
    sent: List[List[str]] = []

    async def _dispatch(snaps, now):
        sent.append([s.symbol for s in snaps])

    for minute in (0, 1, 6):
        await els.earnings_live_tick(_et(REPORT, 15, minute), fmp=fmp, watched=["JOBY"],
                                     dispatch=_dispatch)
    assert sent == [[], [], []]


# ── the newest FMP answer wins ───────────────────────────────────────────────

_DOORS = ["tick", "card", "sheet"]


def _door_service(monkeypatch, rows: List[dict]) -> ts.TrackingService:
    """The real memo, tick, card and sheet over one fake FMP, with no history. The unwatched
    row keeps the previous day from being blank, so the first look reads it."""
    _no_history(monkeypatch)
    return _feed_service(monkeypatch, _et(REPORT, 16), [_row("ZZZZ", PREV), *rows])


def _avgo(eps: Optional[float], eps_est: float = 1.65) -> dict:
    """No revenue consensus, so EPS alone completes the report."""
    return _row("AVGO", REPORT, eps=eps, eps_est=eps_est, rev_est=None)


async def _tick(fmp: _FMP, at: datetime, symbol: str = "AVGO") -> Dict[str, int]:
    async def _dispatch(snaps, now):
        return None

    return await els.earnings_live_tick(at, fmp=fmp, watched=[symbol], dispatch=_dispatch)


async def _through(door: str, svc_obj: ts.TrackingService, at: datetime, monkeypatch) -> None:
    """One fold of the memo through one door."""
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(at))
    if door == "tick":
        await _tick(svc_obj.fmp, at)
    elif door == "card":
        await svc_obj._get_earnings_alerts(["AVGO"])
    else:
        await svc_obj.get_earnings_live("AVGO")


def _memo_days_expire(*days: date) -> None:
    """Push held days past the memo's TTL: the next read starts a round for them."""
    held = ts._earnings_calendar_memo[1]
    for day in days:
        at, rows = held[day]
        held[day] = (at - ts.EARNINGS_CALENDAR_TTL - 1, rows)


def _own_fetch_is_due() -> None:
    _svc()._fetched[REPORT] -= els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1


async def _own_fetch_corrects_the_memo(monkeypatch) -> ts.TrackingService:
    """16:20: the memo stores EPS 0.169, held as a dropped digit. FMP then corrects it to
    1.69, which only the 16:35 own fetch reads: the memo still holds the 16:20 row."""
    svc_obj = _door_service(monkeypatch, [_avgo(0.169)])
    await _tick(svc_obj.fmp, _et(REPORT, 16, 20))
    assert _svc().snapshot("AVGO", REPORT, _et(REPORT, 16, 20)).eps.checking
    svc_obj.fmp.by_day[REPORT] = [_avgo(1.69)]
    _own_fetch_is_due()
    assert (await _tick(svc_obj.fmp, _et(REPORT, 16, 35)))["fetched"] == 1
    return svc_obj


async def _assert_every_door_reads(svc_obj: ts.TrackingService, at: datetime, monkeypatch,
                                   eps: float, estimate: float = 1.65) -> None:
    """The tick's snapshot first: the other two doors fold again on their way to an answer."""
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(at))
    snap = _svc().snapshot("AVGO", REPORT, at)
    assert (snap.status, snap.eps.actual, snap.eps.estimate, snap.eps.checking) == (
        els.STATUS_REPORTED, eps, estimate, False)
    assert snap.checked_at == _et(REPORT, 16, 35), "a memo fold never moves `checked_at`"
    (card,) = await svc_obj._get_earnings_alerts(["AVGO"])
    assert (card.earnings_status, card.eps_actual, card.eps_estimate) == (
        els.STATUS_REPORTED, eps, estimate)
    sheet = await svc_obj.get_earnings_live("AVGO")
    assert (sheet.status, sheet.eps.actual, sheet.eps.estimate, sheet.eps.checking) == (
        els.STATUS_REPORTED, eps, estimate, False)
    assert sheet.poll_seconds is None
    assert _svc().pending_symbols(REPORT, ["AVGO"]) == []


@pytest.mark.asyncio
async def test_an_older_memo_row_never_overwrites_a_newer_own_fetch_on_any_door(monkeypatch):
    svc_obj = await _own_fetch_corrects_the_memo(monkeypatch)
    at = _et(REPORT, 16, 36)
    stats = await _tick(svc_obj.fmp, at)
    assert stats["fetched"] == 0, "complete: the memo is the only fold left in this tick"
    await _assert_every_door_reads(svc_obj, at, monkeypatch, eps=1.69)


@pytest.mark.asyncio
@pytest.mark.parametrize("door", _DOORS)
async def test_each_door_alone_skips_a_memo_older_than_the_own_fetch(monkeypatch, door):
    svc_obj = await _own_fetch_corrects_the_memo(monkeypatch)
    at = _et(REPORT, 16, 36)
    await _through(door, svc_obj, at, monkeypatch)
    assert _svc().snapshot("AVGO", REPORT, at).eps.actual == 1.69


@pytest.mark.asyncio
@pytest.mark.parametrize("door", _DOORS)
async def test_a_memo_refreshed_after_the_own_fetch_wins_once_the_loop_stops_fetching(
        monkeypatch, door):
    """A complete report is no longer fetched, so the memo is how a later correction arrives.
    Filling only what is still empty would freeze 1.69 / 1.65 here, and every door must hand
    the fold its rows' stamp or this answer reads as being of unknown age."""
    svc_obj = await _own_fetch_corrects_the_memo(monkeypatch)
    at = _et(REPORT, 16, 50)
    assert _svc().due_days(at, ["AVGO"]) == [], "the own fetch has stopped"
    svc_obj.fmp.by_day[REPORT] = [_avgo(1.72, eps_est=1.70)]
    _memo_days_expire(REPORT)
    await _through(door, svc_obj, at, monkeypatch)
    await _assert_every_door_reads(svc_obj, at, monkeypatch, eps=1.72, estimate=1.70)


def _a_day_goes_down(fmp: _FMP, day: date) -> None:
    """One failed day fails the memo's whole round (`fetch_calendar_days`)."""
    answer = fmp.get_earnings_calendar

    async def _down(from_date=None, to_date=None):
        if from_date == day.isoformat():
            raise RuntimeError("FMP 503")
        return await answer(from_date=from_date, to_date=to_date)

    fmp.get_earnings_calendar = _down


@pytest.mark.asyncio
async def test_held_memo_rows_after_a_failed_round_stay_older(monkeypatch):
    """A failed round serves the rows it already held, however old, with the age they had."""
    svc_obj = await _own_fetch_corrects_the_memo(monkeypatch)
    _a_day_goes_down(svc_obj.fmp, NEXT)
    _memo_days_expire(REPORT, NEXT)
    at = _et(REPORT, 16, 50)
    await _tick(svc_obj.fmp, at)
    assert ts._earnings_calendar_retry_after is not None, "the round must have failed"
    await _assert_every_door_reads(svc_obj, at, monkeypatch, eps=1.69)


@pytest.mark.asyncio
async def test_held_memo_rows_after_an_empty_round_stay_older(monkeypatch):
    """An empty window is never stored, so the rows served are the old ones too."""
    svc_obj = await _own_fetch_corrects_the_memo(monkeypatch)
    svc_obj.fmp.by_day[REPORT] = []
    _memo_days_expire(REPORT)
    at = _et(REPORT, 16, 50)
    await _tick(svc_obj.fmp, at)
    assert ts._earnings_calendar_retry_after is not None, "the empty round must not be stored"
    await _assert_every_door_reads(svc_obj, at, monkeypatch, eps=1.69)


@pytest.mark.asyncio
@pytest.mark.parametrize("round_fails,expected", [(False, 1.72), (True, 1.69)],
                         ids=["round_stored", "round_failed"])
async def test_a_leader_and_its_joiner_fold_under_the_stamp_of_their_own_rows(
        monkeypatch, round_fails, expected):
    svc_obj = await _own_fetch_corrects_the_memo(monkeypatch)
    fmp = svc_obj.fmp
    fmp.by_day[REPORT] = [_avgo(1.72, eps_est=1.70)]
    _memo_days_expire(REPORT)
    release = asyncio.Event()
    answer = fmp.get_earnings_calendar

    async def _held_open(from_date=None, to_date=None):
        await release.wait()
        if round_fails:
            raise RuntimeError("FMP 503")
        return await answer(from_date=from_date, to_date=to_date)

    fmp.get_earnings_calendar = _held_open
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_et(REPORT, 16, 50)))
    leader = asyncio.ensure_future(svc_obj._get_earnings_alerts(["AVGO"]))
    joiner = asyncio.ensure_future(svc_obj.get_earnings_live("AVGO"))
    for _ in range(20):
        await asyncio.sleep(0)
    assert ts._earnings_calendar_inflight, "the round must still be open"
    release.set()
    (card,), sheet = await asyncio.gather(leader, joiner)
    assert card.eps_actual == sheet.eps.actual == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("memo_eps", [None, 1.06], ids=["memo_pending", "memo_has_the_figure"])
async def test_a_sheet_poll_keeps_the_own_fetchs_estimate(monkeypatch, memo_eps):
    """FMP's estimate is there all day, so a revision alone can split two answers."""
    _no_history(monkeypatch)
    svc_obj = _feed_service(monkeypatch, _et(REPORT, 16, 36),
                            [_row("INTC", REPORT, eps=memo_eps, eps_est=1.00, rev_est=None)])
    await ts._cached_earnings_calendar(svc_obj.fmp, REPORT)        # the memo is stored first
    _svc().observe_rows(REPORT, [_row("INTC", REPORT, eps=1.06, eps_est=1.10, rev_est=None)],
                        _et(REPORT, 16, 35), fetched=True)
    sheet = await svc_obj.get_earnings_live("INTC")
    (card,) = await svc_obj._get_earnings_alerts(["INTC"])
    assert (sheet.eps.estimate, sheet.eps.surprise_pct) == (1.10, -3.64)
    assert (card.eps_estimate, card.eps_surprise_pct) == (1.10, -3.64)


@pytest.mark.asyncio
async def test_a_sheet_poll_between_two_ticks_cannot_change_what_the_push_reads(monkeypatch):
    """A filing's push waits for the next tick, and any reader can fold before it."""
    svc_obj = _door_service(monkeypatch, [_row("INTC", REPORT)])
    fmp = svc_obj.fmp
    await _tick(fmp, _et(REPORT, 16), "INTC")                      # seen pending
    fmp.by_day[REPORT] = [_row("INTC", REPORT, eps=0.60)]
    _memo_days_expire(REPORT)
    await ts._cached_earnings_calendar(fmp, REPORT)                # the memo stores 0.60
    fmp.by_day[REPORT] = [_row("INTC", REPORT, eps=0.44)]          # FMP corrects it
    _own_fetch_is_due()
    sent: List[els.LiveSnapshot] = []

    async def _dispatch(snaps, now):
        sent.extend(snaps)

    at = _et(REPORT, 16, 36)
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(at))
    await els.earnings_live_tick(at, fmp=fmp, watched=["INTC"], dispatch=_dispatch)
    await svc_obj.get_earnings_live("INTC")
    await els.earnings_live_tick(at + timedelta(minutes=1), fmp=fmp, watched=["INTC"],
                                 dispatch=_dispatch)
    assert [(s.eps.actual, s.eps.surprise_pct) for s in sent] == [(0.44, 7.32)] * 2


def _own_fetch_stamp(symbol: str = "INTC") -> float:
    return _svc()._records[(symbol, REPORT)].fmp_fetched_mono


@pytest.mark.parametrize("offset,folds", [(None, False), (-0.001, False), (0.0, True),
                                          (0.001, True)],
                         ids=["unknown_age", "older", "same_moment", "newer"])
def test_a_memo_row_folds_unless_it_is_older_than_the_records_own_fetch(offset, folds):
    svc = _svc()
    now = _et(REPORT, 16, 35)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44)], now, fetched=True)
    stamp = _own_fetch_stamp()
    stored_at = None if offset is None else stamp + offset
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.46)], now, stored_at=stored_at)
    assert svc.snapshot("INTC", REPORT, now).eps.actual == (0.46 if folds else 0.44)
    assert _own_fetch_stamp() == stamp, "only an own fetch moves the stamp"


def test_a_ticker_the_own_fetch_left_out_keeps_folding_older_memo_rows():
    """Stamped per record, not per day: a day-wide stamp would starve this ticker."""
    svc = _svc()
    now = _et(REPORT, 16, 35)
    svc.observe_rows(REPORT, [_row("AAPL", REPORT)], now, stored_at=time.monotonic())
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44)], now, fetched=True)
    memo = [_row("INTC", REPORT, eps=0.40), _row("AAPL", REPORT, eps=1.9)]
    svc.observe_rows(REPORT, memo, now, stored_at=_own_fetch_stamp() - 900)
    assert svc.snapshot("INTC", REPORT, now).eps.actual == 0.44
    assert svc.snapshot("AAPL", REPORT, now).eps.actual == 1.9


@pytest.mark.parametrize("stored_at", [None, 0.0], ids=["unknown_age", "very_old"])
def test_a_record_with_no_own_fetch_folds_every_memo_row(stored_at):
    """Before the hot gate opens the memo is the only source there is, however old its row.
    Only opening a record takes a row of a known, young age."""
    svc = _svc()
    now = _et(REPORT, 5)
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], now, stored_at=time.monotonic())
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44)], now, stored_at=stored_at)
    assert svc.snapshot("INTC", REPORT, now).eps.actual == 0.44


def test_a_skipped_row_moves_nothing_on_the_record():
    svc = _svc()
    now = _et(REPORT, 16, 35)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44, rev=17.1e9)], now, fetched=True)
    rec = svc._records[("INTC", REPORT)]
    before = dataclasses.replace(rec)
    older = dict(_row("INTC", REPORT, rev=0, eps_est=0.35, rev_est=15.9e9), companyName="Old")
    svc.observe_rows(REPORT, [older], now + timedelta(minutes=1),
                     stored_at=_own_fetch_stamp() - 1)
    assert rec == before


def test_every_own_fetch_moves_the_line_a_memo_row_must_clear():
    svc = _svc()
    now = _et(REPORT, 16, 35)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44)], now, fetched=True)
    first = _own_fetch_stamp()
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.46)], now, stored_at=first)
    assert svc.snapshot("INTC", REPORT, now).eps.actual == 0.46
    svc._records[("INTC", REPORT)].fmp_fetched_mono = first - 300     # five minutes ago
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.48)], now, fetched=True, stored_at=0.0)
    assert svc.snapshot("INTC", REPORT, now).eps.actual == 0.48, "an own fetch always folds"
    assert _own_fetch_stamp() >= first
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.46)], now,
                     stored_at=_own_fetch_stamp() - 0.001)
    assert svc.snapshot("INTC", REPORT, now).eps.actual == 0.48


# ── a filing already out at first sight ──────────────────────────────────────


def _morning_filing(**kw) -> efs.FilingFigures:
    base = dict(accession="0000050863-26-000123", accepted_at=_et(REPORT, 7), url=None,
                revenue=17.1e9, eps_adjusted=0.45)
    base.update(kw)
    return efs.FilingFigures(**base)


@pytest.mark.asyncio
async def test_a_morning_filing_found_by_a_fresh_process_is_not_a_landing(monkeypatch):
    """Fresh process at 15:00 ET, FMP still pending, 8-K accepted at 07:00 ET: a restart must
    not re-announce a morning's results. The figures still reach the card, and neither a
    later fold of the same pending row nor FMP's fill may re-open the landing."""
    monkeypatch.setattr(ts, "_et_today", lambda: REPORT)
    fmp = _FMP({REPORT: [_row("INTC", REPORT)], PREV: []})
    monkeypatch.setattr(efs, "get_earnings_filing_watcher",
                        lambda: _Watcher({"INTC": _morning_filing()}))
    sent: List[List[str]] = []

    async def _dispatch(snaps, now):
        sent.append([s.symbol for s in snaps])

    for minute in (0, 1):
        await els.earnings_live_tick(_et(REPORT, 15, minute), fmp=fmp, watched=["INTC"],
                                     dispatch=_dispatch)
        await els.earnings_filing_tick(_et(REPORT, 15, minute), watched=["INTC"])
    assert _svc().snapshot("INTC", REPORT, _et(REPORT, 15, 1)).eps.actual == 0.45
    # The filing completed the report, so the own fetch has stopped: FMP's fill comes by memo.
    fmp.by_day[REPORT] = [_row("INTC", REPORT, eps=0.46, rev=17.2e9)]
    _memo_days_expire(REPORT)
    await els.earnings_live_tick(_et(REPORT, 15, 31), fmp=fmp, watched=["INTC"],
                                 dispatch=_dispatch)
    assert sent == [[], [], []]
    assert _svc().snapshot("INTC", REPORT, _et(REPORT, 15, 31)).eps.actual == 0.46


def test_a_filing_out_at_first_sight_says_so_in_the_log(caplog):
    """No push is what this rule produces, so the log is the only trace of why."""
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 15))
    with caplog.at_level(logging.INFO, logger=els.logger.name):
        svc.absorb_filing("INTC", REPORT, _morning_filing(), _et(REPORT, 15, 1))
    (line,) = [r.getMessage() for r in caplog.records if "first sight" in r.getMessage()]
    assert "INTC" in line and REPORT.isoformat() in line


def test_a_filing_within_the_grace_of_first_sight_still_lands():
    """A deploy inside the release window must not lose a landing it can still announce."""
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 16, 10))
    svc.absorb_filing("INTC", REPORT, _morning_filing(accepted_at=_et(REPORT, 16, 2)),
                      _et(REPORT, 16, 11))
    assert [s.symbol for s in svc.landed_snapshots(_et(REPORT, 16, 12))] == ["INTC"]


@pytest.mark.parametrize("minutes_after,lands", [(15, True), (16, False)])
def test_the_grace_ends_exactly_fifteen_minutes_after_acceptance(minutes_after, lands):
    svc = _svc()
    accepted = _et(REPORT, 16, 2)
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], accepted + timedelta(minutes=minutes_after))
    svc.absorb_filing("INTC", REPORT, _morning_filing(accepted_at=accepted), _et(REPORT, 16, 30))
    landed = [s.symbol for s in svc.landed_snapshots(_et(REPORT, 16, 31))]
    assert landed == (["INTC"] if lands else [])


def test_a_filing_after_first_sight_lands_however_late_it_is_found():
    """A running process that saw the record before the release is the normal landing, even
    when SEC or Gemini kept the read back for hours."""
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 6, 30))
    svc.absorb_filing("INTC", REPORT, _morning_filing(), _et(REPORT, 15))
    assert [s.symbol for s in svc.landed_snapshots(_et(REPORT, 15, 1))] == ["INTC"]


def test_a_filing_with_no_acceptance_time_is_read_as_fresh():
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 15))
    svc.absorb_filing("INTC", REPORT, _morning_filing(accepted_at=None), _et(REPORT, 15, 1))
    assert [s.symbol for s in svc.landed_snapshots(_et(REPORT, 15, 2))] == ["INTC"]


def test_a_later_figure_keeps_a_landing_that_was_seen(caplog):
    """A landing already taken is never undone by an old filing found after it. First sight
    has to follow the filing past the grace, or the already-out rule never runs."""
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 15))
    svc.observe_rows(REPORT, [_row("INTC", REPORT, rev=17.1e9)], _et(REPORT, 15, 5))
    with caplog.at_level(logging.INFO, logger=els.logger.name):
        svc.absorb_filing("INTC", REPORT, _morning_filing(), _et(REPORT, 15, 6))
    assert [r for r in caplog.records if "first sight" in r.getMessage()], "the rule must run"
    assert [s.symbol for s in svc.landed_snapshots(_et(REPORT, 15, 7))] == ["INTC"]


def test_an_old_filing_with_no_usable_figure_leaves_the_next_figure_a_landing():
    """Nothing reached the card from it, so FMP's fill is still the first figure seen."""
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 15))
    svc.absorb_filing("INTC", REPORT, _morning_filing(revenue=None, eps_adjusted=None,
                                                      guidance="Q4 revenue of $18B."),
                      _et(REPORT, 15, 1))
    assert svc.landed_snapshots(_et(REPORT, 15, 1)) == []
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44, rev=17.1e9)], _et(REPORT, 15, 30))
    assert [s.symbol for s in svc.landed_snapshots(_et(REPORT, 15, 31))] == ["INTC"]


def test_a_record_born_from_an_old_filing_is_not_a_landing_either():
    svc = _svc()
    svc.absorb_filing("INTC", REPORT, _morning_filing(), _et(REPORT, 15))
    assert svc.landed_snapshots(_et(REPORT, 15, 1)) == []
    assert svc.snapshot("INTC", REPORT, _et(REPORT, 15, 1)).eps.actual == 0.45


@pytest.mark.asyncio
async def test_with_nothing_watched_the_tick_pays_for_first_looks_alone():
    """No memo round and nobody told: a sheet waiting on that first look is all it is for."""
    fmp = _FMP(_market(PREV))
    told = []

    async def _dispatch(snaps, now):
        told.append(snaps)

    stats = await els.earnings_live_tick(_et(REPORT, 9), fmp=fmp, watched=[], dispatch=_dispatch)
    assert stats == {"watched": 0, "fetched": 1, "landed": 0}
    assert fmp.calls == [PREV.isoformat()] and told == []
    assert ts._earnings_calendar_memo is None
    stats = await els.earnings_live_tick(_et(REPORT, 9, 1), fmp=fmp, watched=[],
                                         dispatch=_dispatch)
    assert stats["fetched"] == 0 and len(fmp.calls) == 1


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


# ── the live days ────────────────────────────────────────────────────────────

SATURDAY, SUNDAY, MONDAY, TUESDAY = (date(2026, 11, 7), date(2026, 11, 8), date(2026, 11, 9),
                                     date(2026, 11, 10))
FRIDAY = date(2026, 11, 6)


class _Pushes:
    """The dispatcher under the REAL sender: what it was asked to send, in order."""

    def __init__(self):
        self.sent: List[tuple] = []

    async def notify_watchers(self, **kw):
        self.sent.append((kw["kind"], kw["dedup_key"]))
        return 1


@pytest.fixture
def pushes(monkeypatch):
    rec = _Pushes()
    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: rec)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", True)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", True)
    sender.reset()
    yield rec
    sender.reset()


def _sent_once(day: date, symbol: str = "INTC") -> List[tuple]:
    """What a report is owed by the real sender: both kinds, under one key, once."""
    key = f"earnings_result:{symbol}:{day.isoformat()}"
    return [(KIND_EARNINGS_LIVE, key), (KIND_EARNINGS_RESULT, key)]


def _nothing_is_due(today: date) -> None:
    """Every day before `today` was already fetched, so the tick has no fetch to make: a prune
    that waited for one would never run."""
    svc, day = _svc(), previous_trading_day(today)
    while day < today:
        svc._fetched[day] = time.monotonic()
        day += timedelta(days=1)


async def _real_tick(monkeypatch, fmp: _FMP, at: datetime) -> None:
    """One round through the real sender, the memo's clock on the round's own day."""
    today = els.et_now(at).date()
    monkeypatch.setattr(ts, "_et_today", lambda: today)
    await els.earnings_live_tick(at, fmp=fmp, watched=["INTC"])


async def _a_landing(monkeypatch, day: date) -> _FMP:
    """INTC pending at 09:00 on `day` and filled at 17:00, seen by the real tick: a landing with
    a 46% surprise, which the sender owes both kinds."""
    pending = [_row("INTC", day)]
    fmp = _FMP({day: pending})

    async def _memo(_fmp, today, *, later_days_since=None):
        return pending if today == day else []

    monkeypatch.setattr(ts, "_cached_earnings_calendar", _memo)
    await _real_tick(monkeypatch, fmp, _et(day, 9))
    fmp.by_day[day] = [_row("INTC", day, eps=0.60, rev=17.1e9)]
    _svc()._fetched[day] -= els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1
    await _real_tick(monkeypatch, fmp, _et(day, 17))
    return fmp


_LATER_TICKS = {
    # The floor passes Tuesday at Thursday 00:00, and the next fetch is 06:00 at best.
    "weekday": (date(2026, 10, 27), [(date(2026, 10, 28), 12), (date(2026, 10, 29), 0),
                                     (date(2026, 10, 29), 3), (date(2026, 10, 29), 5)]),
    # Nothing reports over the weekend: the floor passes Thursday at Saturday 00:00.
    "weekend": (date(2026, 10, 29), [(date(2026, 10, 30), 12), (date(2026, 10, 31), 0),
                                     (date(2026, 11, 1), 10), (date(2026, 11, 2), 5)]),
    "thanksgiving": (date(2026, 11, 24), [(date(2026, 11, 25), 12), (date(2026, 11, 26), 0),
                                          (date(2026, 11, 26), 12), (date(2026, 11, 27), 23)]),
    # A report dated on a closed day stays in the window through Monday.
    "saturday_report": (SATURDAY, [(SUNDAY, 0), (SUNDAY, 12), (MONDAY, 9), (TUESDAY, 0),
                                   (TUESDAY, 9)]),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("day,later", _LATER_TICKS.values(), ids=_LATER_TICKS.keys())
async def test_a_landing_is_dispatched_once_when_no_fetch_is_due_after_the_day_roll(
        monkeypatch, pushes, day, later):
    fmp = await _a_landing(monkeypatch, day)
    assert pushes.sent == _sent_once(day)
    calls = len(fmp.calls)
    for tick_day, hour in later:
        _nothing_is_due(tick_day)
        await _real_tick(monkeypatch, fmp, _et(tick_day, hour))
    assert len(fmp.calls) == calls, "no round had a fetch to make"
    assert pushes.sent == _sent_once(day)


@pytest.mark.asyncio
async def test_a_failed_watcher_read_at_the_landing_tick_is_told_on_the_next_tick(
        monkeypatch, pushes):
    """Through the real tick: a landing is handed over every round, so the key coming back is
    all a retry needs."""
    send = pushes.notify_watchers
    failures = [pds.WatcherLookupFailed("INTC: RuntimeError: 520 from the edge")]

    async def _first_read_fails(**kw):
        if failures:
            raise failures.pop()
        return await send(**kw)

    monkeypatch.setattr(pushes, "notify_watchers", _first_read_fails)
    fmp = await _a_landing(monkeypatch, REPORT)
    assert pushes.sent == [] and failures == [], "the landing round's read failed"
    await _real_tick(monkeypatch, fmp, _et(REPORT, 17, 1))
    assert pushes.sent == _sent_once(REPORT)
    await _real_tick(monkeypatch, fmp, _et(REPORT, 17, 2))
    assert pushes.sent == _sent_once(REPORT)


@pytest.mark.asyncio
async def test_old_landings_cannot_fill_the_round_cap(pushes):
    """The sender sends oldest first and stops at its cap, so a report that left the window
    would otherwise hold back today's."""
    old = date(2026, 10, 26)
    svc = _svc()
    for i in range(sender.MAX_SENDS_PER_ROUND + 1):
        symbol = f"OLD{i:02d}"
        svc.observe_rows(old, [_row(symbol, old)], _et(old, 9))
        svc.observe_rows(old, [_row(symbol, old, eps=0.60, rev=17.1e9)], _et(old, 17))
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.60, rev=17.1e9)], _et(REPORT, 17))
    now = _et(REPORT, 17, 1)
    await sender.dispatch_landings(svc.landed_snapshots(now), now)
    assert pushes.sent == _sent_once(REPORT)


@pytest.mark.asyncio
async def test_a_closure_learned_late_cannot_bring_a_landing_back(monkeypatch, pushes):
    """A learned closure moves the window back a session, over a record that outlived its key.
    Handed over again, it is new to the sender and still tells nobody: a report past the live
    days is older than yesterday, whatever the calendar learns."""
    tue, wed, thu = date(2026, 10, 27), PREV, REPORT
    fmp = await _a_landing(monkeypatch, tue)
    _nothing_is_due(thu)
    await _real_tick(monkeypatch, fmp, _et(thu, 1))
    assert _svc().landed_snapshots(_et(thu, 1)) == [], "the key left with the live days"
    monkeypatch.setattr(mh, "_OBSERVED_CLOSURES", {(wed.year, wed.month, wed.day)})
    await _real_tick(monkeypatch, fmp, _et(thu, 10))
    assert [s.day for s in _svc().landed_snapshots(_et(thu, 10))] == [tue]
    assert _svc().snapshot("INTC", tue, _et(thu, 10)).status == els.STATUS_REPORTED
    assert pushes.sent == _sent_once(tue)


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting_out_a_failure", [False, True],
                         ids=["nothing_due", "failure_wait"])
async def test_refresh_prunes_with_nothing_due_and_during_the_failure_wait(waiting_out_a_failure):
    svc = _svc()
    tuesday = PREV - timedelta(days=1)
    svc.observe_rows(tuesday, [_row("INTC", tuesday, eps=0.30, rev=1.3e10)], _et(tuesday, 17))
    mono = time.monotonic()
    svc._fetched.update({tuesday: mono, PREV: mono, REPORT: mono})
    if waiting_out_a_failure:
        svc._failed_until = mono + 60
    now = _et(NEXT, 9)                  # Friday: Tuesday has left the sheet days too
    fmp = _FMP()
    await svc.refresh(now, fmp=fmp, symbols=["INTC"])
    assert fmp.calls == [], "nothing was fetched: the prune did not need a round"
    assert svc.snapshot("INTC", tuesday, now) is None
    assert set(svc._fetched) == {PREV, REPORT}


@pytest.mark.asyncio
async def test_a_report_dated_on_a_non_trading_day_stays_until_the_floor_passes_it():
    svc = _svc()
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY)], _et(SATURDAY, 9))
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY, eps=0.5, rev=17e9)], _et(SATURDAY, 11))
    fmp = _FMP()
    for day in (SUNDAY, MONDAY, TUESDAY):
        await svc.refresh(_et(day, 9), fmp=fmp, symbols=["INTC"])
        assert svc.snapshot("INTC", SATURDAY, _et(day, 9)).status == els.STATUS_REPORTED
    wednesday = TUESDAY + timedelta(days=1)
    await svc.refresh(_et(wednesday, 9), fmp=fmp, symbols=["INTC"])
    assert svc.snapshot("INTC", SATURDAY, _et(wednesday, 9)) is None


CLOSED_DAY_CASES = [
    (SATURDAY, SUNDAY),                             # a Saturday row, read on Sunday
    (SATURDAY, MONDAY),                             # and on Monday
    (date(2026, 11, 26), date(2026, 11, 27)),       # a Thanksgiving row, read on Friday
]


@pytest.mark.asyncio
@pytest.mark.parametrize("report,seen_on", CLOSED_DAY_CASES)
async def test_a_late_fill_on_a_closed_day_report_lands_and_keeps_its_card(
        report, seen_on, monkeypatch):
    monkeypatch.setattr(ts, "_et_today", lambda: seen_on)
    svc = _svc()
    svc.observe_rows(report, [_row("INTC", report)], _et(report, 20))
    svc._fetched[report] = time.monotonic() - (els.EARNINGS_LIVE_CLOSED_DAY_TTL_SECONDS + 1)
    fmp = _FMP({report: [_row("INTC", report, eps=0.5, rev=17e9)]})
    sent = []

    async def _dispatch(snaps, now):
        sent.extend((s.symbol, s.day) for s in snaps)

    now = _et(seen_on, 12)
    await els.earnings_live_tick(now, fmp=fmp, watched=["INTC"], dispatch=_dispatch)
    assert sent == [("INTC", report)]
    assert [(s.symbol, s.day, s.status) for s in svc.recent_snapshots(["INTC"], now)] == [
        ("INTC", report, els.STATUS_REPORTED)]


@pytest.mark.asyncio
@pytest.mark.parametrize("report,seen_on", CLOSED_DAY_CASES)
async def test_a_restart_on_a_closed_day_finds_its_report_again(report, seen_on, monkeypatch):
    monkeypatch.setattr(ts, "_et_today", lambda: seen_on)
    fmp = _FMP({report: [_row("INTC", report, eps=0.5, rev=17e9)]})
    sent = []

    async def _dispatch(snaps, now):
        sent.extend(snaps)

    now = _et(seen_on, 12)
    await els.earnings_live_tick(now, fmp=fmp, watched=["INTC"], dispatch=_dispatch)
    assert sent == [], "reported at first sight is not a landing"
    assert [(s.symbol, s.day) for s in _svc().recent_snapshots(["INTC"], now)] == [
        ("INTC", report)]


def test_a_closed_day_record_leaves_with_the_window():
    svc = _svc()
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY)], _et(SATURDAY, 9))
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY, eps=0.5, rev=17e9)], _et(SATURDAY, 11))
    assert [s.day for s in svc.landed_snapshots(_et(MONDAY, 9))] == [SATURDAY]
    assert svc.landed_snapshots(_et(TUESDAY, 9)) == []


def test_a_closed_day_is_asked_once_then_thinly_and_only_while_a_watched_report_is_pending():
    svc = _svc()
    now = _et(SUNDAY, 9)
    mono = time.monotonic()
    svc._fetched[FRIDAY] = mono
    assert svc.due_days(now, ["INTC"]) == [SATURDAY], "never asked: a restart's first look"
    svc._fetched[SATURDAY] = mono
    assert svc.due_days(now, ["INTC"]) == []
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY)], _et(SATURDAY, 20))
    assert svc.due_days(now, ["INTC"]) == [], "just asked"
    svc._fetched[SATURDAY] = mono - (els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1)
    assert svc.due_days(now, ["INTC"]) == [], "a closed day never goes hot"
    svc._fetched[SATURDAY] = mono - (els.EARNINGS_LIVE_COLD_TTL_SECONDS + 1)
    assert svc.due_days(now, ["INTC"]) == [], "nor keeps the trading-day pace"
    svc._fetched[SATURDAY] = mono - (els.EARNINGS_LIVE_CLOSED_DAY_TTL_SECONDS + 1)
    assert svc.due_days(now, ["INTC"]) == [SATURDAY]
    assert svc.due_days(now, ["AAPL"]) == [], "not watched"
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY, eps=0.5, rev=17e9)], now)
    assert svc.due_days(now, ["INTC"]) == [], "nothing left to ask"


def test_on_a_monday_morning_only_the_previous_trading_day_goes_hot():
    svc = _svc()
    now = _et(MONDAY, 9)
    for day in (FRIDAY, SATURDAY, SUNDAY):
        svc.observe_rows(day, [_row("INTC", day)], _et(day, 12))
        svc._fetched[day] = time.monotonic() - (els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1)
    assert svc.due_days(now, ["INTC"]) == [FRIDAY]


def test_a_friday_report_goes_hot_again_only_on_the_next_trading_morning():
    svc = _svc()
    svc.observe_rows(FRIDAY, [_row("INTC", FRIDAY)], _et(FRIDAY, 9), fetched=True)
    for at in (_et(SATURDAY, 8), _et(SUNDAY, 8)):
        svc._fetched[FRIDAY] = time.monotonic() - (els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1)
        assert FRIDAY not in svc.due_days(at, ["INTC"]), at
    assert FRIDAY in svc.due_days(_et(MONDAY, 8), ["INTC"])
    assert FRIDAY not in svc.due_days(_et(MONDAY, 12), ["INTC"]), "hot ends at noon"


@pytest.mark.parametrize("at", [_et(SATURDAY, 3), _et(SATURDAY, 8), _et(SATURDAY, 15),
                                _et(SUNDAY, 8), _et(SUNDAY, 23)])
@pytest.mark.parametrize("filed", [False, True], ids=["pending", "preliminary"])
def test_a_closed_day_asks_about_the_days_before_it_at_the_thin_pace(at, filed):
    svc = _svc()
    svc.observe_rows(FRIDAY, [_row("INTC", FRIDAY)], _et(FRIDAY, 9), fetched=True)
    svc._fetched[SATURDAY] = time.monotonic()
    if filed:
        svc.absorb_filing("INTC", FRIDAY, _FILED_ADJUSTED, _et(FRIDAY, 16, 3))
    svc._fetched[FRIDAY] = time.monotonic() - (els.EARNINGS_LIVE_COLD_TTL_SECONDS + 1)
    assert FRIDAY not in svc.due_days(at, ["INTC"])
    svc._fetched[FRIDAY] = time.monotonic() - (els.EARNINGS_LIVE_CLOSED_DAY_TTL_SECONDS + 1)
    assert FRIDAY in svc.due_days(at, ["INTC"])


def test_a_market_holiday_is_paced_like_a_weekend():
    wednesday, thanksgiving, friday = (date(2026, 11, 25), date(2026, 11, 26),
                                       date(2026, 11, 27))
    svc = _svc()
    svc.observe_rows(wednesday, [_row("INTC", wednesday)], _et(wednesday, 9), fetched=True)
    svc._fetched[wednesday] = time.monotonic() - (els.EARNINGS_LIVE_COLD_TTL_SECONDS + 1)
    assert wednesday not in svc.due_days(_et(thanksgiving, 8), ["INTC"])
    svc._fetched[thanksgiving] = time.monotonic()
    assert wednesday in svc.due_days(_et(friday, 8), ["INTC"]), "hot on the half day after it"


def test_a_closed_days_own_report_is_hot_on_its_day():
    """Berkshire reports on Saturdays: the fetch for TODAY keeps its pace on a closed day."""
    svc = _svc()
    svc._fetched[FRIDAY] = time.monotonic()
    svc.observe_rows(SATURDAY, [_row("BRK-B", SATURDAY)], _et(SATURDAY, 7), fetched=True)
    svc._fetched[SATURDAY] = time.monotonic() - (els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1)
    assert svc.due_days(_et(SATURDAY, 8), ["BRK-B"]) == [SATURDAY]


# ── the tick and the Tracking memo ───────────────────────────────────────────


def _market(first: date, days: int = 40) -> Dict[date, List[dict]]:
    """An unwatched row on every date, as a real calendar has: an empty window is never stored."""
    return {first + timedelta(days=i): [_row("ZZZZ", first + timedelta(days=i))]
            for i in range(days)}


async def _memo_filled_on(monkeypatch, fmp: _FMP, day: date) -> None:
    """The last tick of `day`'s live window, then every held day past its TTL: a later tick
    that reads the memo through its own door must pay for a round."""
    monkeypatch.setattr(ts, "_et_today", lambda: day)
    await _tick(fmp, _et(day, 21, 59), "INTC")
    _memo_days_expire(*ts._earnings_calendar_memo[1])
    fmp.calls.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("held_from,at", [
    (REPORT, _et(NEXT, 0)),
    (REPORT, _et(NEXT, 3)),
    (REPORT, _et(NEXT, 5, 59)),
    (FRIDAY, _et(SATURDAY, 10)),
    (FRIDAY, _et(SUNDAY, 10)),
    (date(2026, 11, 25), _et(date(2026, 11, 26), 10)),
], ids=["midnight", "overnight", "last_minute", "saturday", "sunday", "holiday"])
async def test_before_six_and_on_a_closed_day_the_tick_pays_for_no_memo_refresh(
        monkeypatch, held_from, at):
    fmp = _FMP(_market(held_from))
    await _memo_filled_on(monkeypatch, fmp, held_from)
    today = els.et_now(at).date()
    monkeypatch.setattr(ts, "_et_today", lambda: today)
    _nothing_is_due(today)
    await _tick(fmp, at, "INTC")
    assert fmp.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("at", [_et(NEXT, 6), _et(NEXT, 21, 59), _et(NEXT, 22), _et(NEXT, 23, 59)])
async def test_from_six_to_midnight_on_a_trading_day_the_tick_keeps_the_memo_fresh(monkeypatch, at):
    fmp = _FMP(_market(REPORT))
    await _memo_filled_on(monkeypatch, fmp, REPORT)
    monkeypatch.setattr(ts, "_et_today", lambda: NEXT)
    _nothing_is_due(NEXT)
    await _tick(fmp, at, "INTC")
    assert {NEXT.isoformat(), (NEXT + timedelta(days=1)).isoformat()} <= set(fmp.calls)


@pytest.mark.asyncio
async def test_a_fill_after_the_hot_fetch_stops_is_still_read_that_evening(monkeypatch):
    """The own fetch ends at 22:00, so until midnight the memo is what reads a late fill."""
    fmp = _FMP({**_market(PREV), REPORT: [_row("INTC", REPORT)]})
    monkeypatch.setattr(ts, "_et_today", lambda: REPORT)
    await _tick(fmp, _et(REPORT, 21, 55), "INTC")
    fmp.by_day[REPORT] = [_row("INTC", REPORT, eps=0.60, rev=17.1e9)]
    _memo_days_expire(REPORT)
    at = _et(REPORT, 22, 30)
    assert _svc().due_days(at, ["INTC"]) == [], "the hot window has closed"
    await _tick(fmp, at, "INTC")
    assert _svc().snapshot("INTC", REPORT, at).eps.actual == 0.60


@pytest.mark.asyncio
async def test_a_tick_before_six_still_folds_the_rows_the_memo_holds(monkeypatch):
    """So an overnight fill is still SEEN landing at the first read of the morning: the
    evening's "tomorrow" rows are what make it pending first."""
    fmp = _FMP({**_market(REPORT), NEXT: [_row("INTC", NEXT)]})
    await _memo_filled_on(monkeypatch, fmp, REPORT)
    monkeypatch.setattr(ts, "_et_today", lambda: NEXT)
    _nothing_is_due(NEXT)
    await _tick(fmp, _et(NEXT, 3), "INTC")
    assert fmp.calls == []
    assert _svc().pending_symbols(NEXT, ["INTC"]) == ["INTC"]
    fmp.by_day[NEXT] = [_row("INTC", NEXT, eps=0.60, rev=17.1e9)]
    await _tick(fmp, _et(NEXT, 6), "INTC")
    assert [(s.symbol, s.day) for s in _svc().landed_snapshots(_et(NEXT, 6))] == [("INTC", NEXT)]


@pytest.mark.asyncio
async def test_a_closed_days_own_report_reaches_the_hot_fetch_from_the_held_rows(monkeypatch):
    fmp = _FMP({**_market(FRIDAY), SATURDAY: [_row("BRK-B", SATURDAY)]})
    monkeypatch.setattr(ts, "_et_today", lambda: FRIDAY)
    await _tick(fmp, _et(FRIDAY, 21, 59), "BRK-B")
    _memo_days_expire(*ts._earnings_calendar_memo[1])
    fmp.calls.clear()
    monkeypatch.setattr(ts, "_et_today", lambda: SATURDAY)
    _nothing_is_due(SATURDAY)
    await _tick(fmp, _et(SATURDAY, 8), "BRK-B")
    assert fmp.calls == [SATURDAY.isoformat()], "its own fetch, and no memo round"


@pytest.mark.asyncio
@pytest.mark.parametrize("at", [_et(REPORT, 3), _et(SATURDAY, 10)],
                         ids=["before_six", "closed_day"])
async def test_a_restart_with_nothing_held_reads_todays_rows_once(monkeypatch, at):
    today = els.et_now(at).date()
    fmp = _FMP({**_market(today), today: [_row("INTC", today)]})
    monkeypatch.setattr(ts, "_et_today", lambda: today)
    _nothing_is_due(today)
    await _tick(fmp, at, "INTC")
    assert _svc().snapshot("INTC", today, at) is not None, "nothing held: the memo is read"
    made = len(fmp.calls)
    assert made >= ts._EARNINGS_ALERT_DAYS_AHEAD + 1
    await _tick(fmp, at + timedelta(minutes=1), "INTC")
    assert len(fmp.calls) == made, "held now, so the next ticks read it for free"


@pytest.mark.asyncio
async def test_a_failed_first_read_before_six_keeps_the_memos_own_wait(monkeypatch):
    at = _et(REPORT, 3)
    fmp = _FMP(_market(REPORT))
    _a_day_goes_down(fmp, NEXT)
    monkeypatch.setattr(ts, "_et_today", lambda: REPORT)
    _nothing_is_due(REPORT)
    await _tick(fmp, at, "INTC")
    assert ts._earnings_calendar_retry_after is not None, "the round must have failed"
    made = len(fmp.calls)
    await _tick(fmp, at + timedelta(minutes=1), "INTC")
    assert len(fmp.calls) == made, "one round per wait, not one per tick"


@pytest.mark.asyncio
async def test_a_feed_build_still_refreshes_the_memo_on_a_closed_day(monkeypatch):
    fmp = _FMP(_market(FRIDAY))
    await _memo_filled_on(monkeypatch, fmp, FRIDAY)
    await ts._cached_earnings_calendar(fmp, SATURDAY)
    assert SATURDAY.isoformat() in fmp.calls


# ── what it all costs ────────────────────────────────────────────────────────


class _Clock:
    """A `time` stand-in for the service and the memo alone: asyncio keeps its own clock."""

    def __init__(self):
        self.at = 1_000_000.0

    def monotonic(self) -> float:
        return self.at


#: What one FMP call takes on the stand-in clock. Small, and never zero: a fetch stamps itself when
#: it returns, and that moment past the tick's start is what a cadence has to survive.
_FMP_CALL_SECONDS = 0.1


async def _calls_per_day(monkeypatch, fmp: _FMP, start: datetime, end: datetime, watched: List[str],
                         on_tick: Optional[Callable[[datetime], None]] = None,
                         *, dispatch: Optional[Callable[..., Any]] = None,
                         after_tick: Optional[Callable[[datetime], Any]] = None,
                         interval: int = 60) -> Dict[date, int]:
    """Every FMP call the REAL tick makes at the loop's pace with nobody opening the app, under
    the ET day it was made on. As in `_forever`, the ticks START exactly `interval` seconds
    apart, however long their calls took. `dispatch` is the sender, a stand-in unless given;
    `after_tick` is awaited after each tick, for a reader to look at what it left."""
    clock = _Clock()
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(ts, "_time", SimpleNamespace(monotonic=clock.monotonic))
    answer = fmp.get_earnings_calendar

    async def _takes_a_moment(from_date=None, to_date=None):
        clock.at += _FMP_CALL_SECONDS
        return await answer(from_date=from_date, to_date=to_date)

    fmp.get_earnings_calendar = _takes_a_moment

    async def _dispatch(snaps, now):
        return None

    calls: Dict[date, int] = {}
    at = start
    while at < end:
        started = clock.at
        today = els.et_now(at).date()
        monkeypatch.setattr(ts, "_et_today", lambda day=today: day)
        if on_tick is not None:
            on_tick(at)
        made = len(fmp.calls)
        await els.earnings_live_tick(at, fmp=fmp, watched=watched, dispatch=dispatch or _dispatch)
        calls[today] = calls.get(today, 0) + len(fmp.calls) - made
        if after_tick is not None:
            await after_tick(at)
        at += timedelta(seconds=interval)
        clock.at = started + interval
    return calls


def _minutes_of_own_fetches(fetched_at: List[int], start: datetime) -> Callable[[datetime], None]:
    """An `on_tick` that notes the minute before each tick that fetched: `service.fetches` moves
    only on the module's own fetch, never on a memo round."""
    last = {"fetches": 0}

    def _note(at: datetime) -> None:
        now = _svc().fetches
        if now > last["fetches"]:
            fetched_at.append(int((at - start).total_seconds() // 60) - 1)
        last["fetches"] = now

    return _note


@pytest.mark.asyncio
async def test_a_hot_day_is_asked_on_every_fifth_tick_exactly(monkeypatch):
    """The stamp sits a moment past its tick's start, so the fifth tick after it reads a moment
    short of 5 min. An exact compare leaves the fetch for the sixth, and FMP's confirmation and
    its push a minute late on every round. The slack must not bring it to the fourth either."""
    fmp = _FMP({REPORT: [_row("INTC", REPORT)], PREV: [_row("ZZZZ", PREV)]})
    start = _et(REPORT, 9)
    fetched_at: List[int] = []
    await _calls_per_day(monkeypatch, fmp, start, start + timedelta(minutes=62), ["INTC"],
                         _minutes_of_own_fetches(fetched_at, start))
    assert fetched_at == list(range(0, 61, 5))


@pytest.mark.asyncio
@pytest.mark.parametrize("interval,gap", [
    (15, 300), (60, 300), (100, 300), (200, 400), (240, 480), (290, 580), (295, 590), (300, 300),
])
async def test_the_hot_read_waits_for_the_first_tick_at_least_295_s_after_the_last(
        monkeypatch, interval, gap):
    """FMP is read only on a tick, so the loop's cadence sets the real gap, and the upper half of
    the range the setting allows stretches it well past 5 min. The comment on
    `EARNINGS_LIVE_INTERVAL_SECONDS` states this, for whoever moves that number."""
    fmp = _FMP({REPORT: [_row("INTC", REPORT)], PREV: [_row("ZZZZ", PREV)]})
    start = _et(REPORT, 9)
    fetched_at: List[int] = []
    seen = {"fetches": 0}

    def _note(at: datetime) -> None:
        if _svc().fetches > seen["fetches"]:
            fetched_at.append(int((at - start).total_seconds()) - interval)
        seen["fetches"] = _svc().fetches

    await _calls_per_day(monkeypatch, fmp, start, start + timedelta(seconds=3 * gap + interval + 1),
                         ["INTC"], _note, interval=interval)
    assert fetched_at == [0, gap, 2 * gap, 3 * gap]


@pytest.mark.asyncio
@pytest.mark.parametrize("today,ttl_minutes", [(REPORT, 30), (SATURDAY, 120)],
                         ids=["trading_day", "closed_day"])
async def test_an_earlier_day_is_asked_again_on_the_tick_its_ttl_ends(monkeypatch, today,
                                                                       ttl_minutes):
    earlier = previous_trading_day(today)
    fmp = _FMP({earlier: [_row("INTC", earlier)], today: []})
    start = _et(today, 14)
    _svc().observe_rows(earlier, [_row("INTC", earlier)], start, fetched=True)
    fetched_at: List[int] = []
    end = start + timedelta(minutes=2 * ttl_minutes + 2)
    await _calls_per_day(monkeypatch, fmp, start, end, ["INTC"],
                         _minutes_of_own_fetches(fetched_at, start))
    assert fetched_at == [0, ttl_minutes, 2 * ttl_minutes]


@pytest.mark.parametrize("ttl,today,at", [
    (els.EARNINGS_LIVE_HOT_TTL_SECONDS, REPORT, _et(REPORT, 9)),
    (els.EARNINGS_LIVE_COLD_TTL_SECONDS, REPORT, _et(REPORT, 14)),
    (els.EARNINGS_LIVE_CLOSED_DAY_TTL_SECONDS, SATURDAY, _et(SATURDAY, 14)),
], ids=["hot", "cold", "closed_day"])
def test_a_day_comes_due_a_few_seconds_early_and_no_earlier(monkeypatch, ttl, today, at):
    clock = _Clock()
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    svc, earlier = _svc(), previous_trading_day(today)
    svc.observe_rows(earlier, [_row("INTC", earlier)], at)
    svc._fetched[today] = clock.at
    on_time = clock.at - ttl + els._DUE_SLACK_SECONDS
    svc._fetched[earlier] = on_time
    assert earlier in svc.due_days(at, ["INTC"])
    svc._fetched[earlier] = on_time + 0.001
    assert earlier not in svc.due_days(at, ["INTC"])


def test_today_comes_due_a_few_seconds_early_and_no_earlier(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    at, svc = _et(REPORT, 9), _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], at)
    svc._fetched[PREV] = clock.at
    on_time = clock.at - els.EARNINGS_LIVE_HOT_TTL_SECONDS + els._DUE_SLACK_SECONDS
    svc._fetched[REPORT] = on_time
    assert svc.due_days(at, ["INTC"]) == [REPORT]
    svc._fetched[REPORT] = on_time + 0.001
    assert svc.due_days(at, ["INTC"]) == []


@pytest.mark.asyncio
async def test_a_failed_fetch_is_asked_again_on_the_tick_its_wait_ends(monkeypatch):
    """Its wait counts from when it failed, a moment past the tick. "next try in 120s" in the log
    must not take 180."""
    clock = _Clock()
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    at, svc = _et(REPORT, 9), _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], at)
    svc._fetched[PREV] = clock.at
    asked: List[float] = []

    class _Down(_FMP):
        async def get_earnings_calendar(self, from_date=None, to_date=None):
            asked.append(clock.at)
            clock.at += _FMP_CALL_SECONDS
            raise RuntimeError("FMP 503")

    started, fmp = clock.at, _Down()
    for minute in range(4):
        clock.at = started + 60 * minute
        await svc.refresh(at, fmp=fmp, symbols=["INTC"])
    assert [t - started for t in asked] == [0, 120]


@pytest.mark.asyncio
@pytest.mark.parametrize("filed", [False, True], ids=["pending", "filing_complete"])
async def test_a_friday_report_fmp_never_fills_stays_on_the_weekend_budget(monkeypatch, filed):
    fmp = _FMP({**_market(FRIDAY), FRIDAY: [_row("INTC", FRIDAY)]})
    start = _et(FRIDAY, 21)

    def _file(at: datetime) -> None:
        if filed and at == start:
            _svc().absorb_filing("INTC", FRIDAY, _FILED_ADJUSTED, at)

    calls = await _calls_per_day(monkeypatch, fmp, start, _et(TUESDAY, 0), ["INTC"], _file)
    assert calls[SATURDAY] == 12
    assert calls[SUNDAY] == 13, "Saturday's own first look on top"
    assert calls[MONDAY] == 220, "108 for Friday, 111 for the memo, one first look at Sunday"


@pytest.mark.asyncio
async def test_a_quiet_weekend_costs_one_first_look_a_day_and_no_memo_call(monkeypatch):
    fmp = _FMP(_market(FRIDAY))
    calls = await _calls_per_day(monkeypatch, fmp, _et(FRIDAY, 21), _et(MONDAY, 0), ["INTC"])
    assert (calls[SATURDAY], calls[SUNDAY]) == (1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("filed", [False, True], ids=["pending", "filing_complete"])
async def test_a_busy_report_day_stays_on_budget(monkeypatch, filed):
    """Today's and the previous day's watched reports both still want FMP all day, the worst
    case: 192 for today, 108 for the previous day, 111 for the memo. A filing that completes
    today's report at 16:03 changes nothing: until FMP confirms it, it costs what a pending
    row costs."""
    fmp = _FMP({**_market(PREV), PREV: [_row("INTC", PREV)], REPORT: [_row("AAPL", REPORT)]})

    def _file(at: datetime) -> None:
        if filed and at == _et(REPORT, 16, 3):
            _svc().absorb_filing("AAPL", REPORT, _FILED_ADJUSTED, at)

    calls = await _calls_per_day(monkeypatch, fmp, _et(PREV, 0), _et(NEXT, 0), ["INTC", "AAPL"],
                                 _file)
    assert calls[REPORT] == 411




@pytest.mark.asyncio
async def test_a_restart_on_a_monday_asks_each_day_before_today_once():
    fmp = _FMP({FRIDAY: [_row("ZZZZ", FRIDAY)]})
    svc = _svc()
    await svc.refresh(_et(MONDAY, 1), fmp=fmp, symbols=["INTC"])
    await svc.refresh(_et(MONDAY, 1, 1), fmp=fmp, symbols=["INTC"])
    assert sorted(fmp.calls) == [d.isoformat() for d in (FRIDAY, SATURDAY, SUNDAY)]


# ── a row held too long to open a record ─────────────────────────────────────


_TWELVE_HOURS = 12 * 3600


def _memo_held_for(hours: float) -> None:
    """Every held day was stored `hours` ago, on the clock the tick reads."""
    held = ts._earnings_calendar_memo[1]
    for day, (at, rows) in held.items():
        held[day] = (at - hours * 3600, rows)


def _intc_on(day: date) -> List[dict]:
    """A calendar day with INTC on it. The unwatched row keeps the day from being empty."""
    return [_row("ZZZZ", day), _row("INTC", day)]


def _fmp_moves_intc(fmp: _FMP, old: date, new: date) -> None:
    fmp.by_day[old] = [_row("ZZZZ", old)]
    fmp.by_day[new] = _intc_on(new)


def _fmp_can_go_down(fmp: _FMP) -> Dict[str, bool]:
    """Set `["down"]` and every calendar call fails, counted like any other call."""
    answer = fmp.get_earnings_calendar
    state = {"down": False}

    async def _flaky(from_date=None, to_date=None):
        if state["down"]:
            fmp.calls.append(from_date)
            raise RuntimeError("FMP 503")
        return await answer(from_date=from_date, to_date=to_date)

    fmp.get_earnings_calendar = _flaky
    return state


def _stored_ago(monkeypatch, seconds: Optional[int]) -> Optional[float]:
    """A stamp exactly `seconds` old on a clock that stands still, or None for an unknown age."""
    clock = _Clock()
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    return None if seconds is None else clock.at - seconds


@pytest.mark.parametrize("held,opens", [(None, True), (_TWELVE_HOURS - 1, True),
                                        (_TWELVE_HOURS, False), (6 * _TWELVE_HOURS, False)],
                         ids=["unknown_age", "under_the_limit", "at_the_limit", "three_days"])
def test_a_memo_row_opens_a_record_only_under_twelve_hours_old(monkeypatch, held, opens):
    svc = _svc()
    now = _et(MONDAY, 0, 5)
    svc.observe_rows(MONDAY, [_row("INTC", MONDAY)], now, stored_at=_stored_ago(monkeypatch, held))
    assert (svc.snapshot("INTC", MONDAY, now) is not None) == opens


def test_an_own_fetch_opens_a_record_whatever_stamp_it_is_handed():
    svc = _svc()
    now = _et(MONDAY, 9)
    svc.observe_rows(MONDAY, [_row("INTC", MONDAY)], now, fetched=True,
                     stored_at=time.monotonic() - 72 * 3600)
    assert svc.pending_symbols(MONDAY, ["INTC"]) == ["INTC"]


def test_rows_too_old_to_open_a_record_say_so_once_per_fold(caplog):
    with caplog.at_level(logging.WARNING, logger=els.logger.name):
        _svc().observe_rows(MONDAY, [_row("INTC", MONDAY), _row("AAPL", MONDAY)], _et(MONDAY, 0),
                            stored_at=time.monotonic() - 72 * 3600)
    (record,) = caplog.records
    message = record.getMessage()
    assert record.levelno == logging.WARNING
    assert MONDAY.isoformat() in message and "72 h" in message
    assert "AAPL" in message and "INTC" in message


@pytest.mark.parametrize("rows,held,watched,expected", [
    ([_row("INTC", MONDAY)], _TWELVE_HOURS, ["INTC"], True),
    ([_row("INTC", MONDAY)], _TWELVE_HOURS, ["intc"], True),
    ([_row("ZZZZ", MONDAY), _row("INTC", MONDAY)], _TWELVE_HOURS, ["INTC"], True),
    ([_row("INTC", MONDAY)], _TWELVE_HOURS - 1, ["INTC"], False),
    ([_row("INTC", MONDAY)], None, ["INTC"], False),
    ([_row("ZZZZ", MONDAY)], _TWELVE_HOURS, ["INTC"], False),
    ([_row("INTC", TUESDAY)], _TWELVE_HOURS, ["INTC"], False),
    ([], _TWELVE_HOURS, ["INTC"], False),
    (["junk", None, {"symbol": "INTC", "date": "soon"}], _TWELVE_HOURS, ["INTC"], False),
], ids=["at_the_limit", "lower_case_watchlist", "beside_the_market", "young", "unknown_age",
        "unwatched", "another_day", "no_rows", "junk_rows"])
def test_old_rows_would_open_only_a_watched_report_on_that_day(
        monkeypatch, rows, held, watched, expected):
    stored_at = _stored_ago(monkeypatch, held)
    assert _svc().old_rows_would_open(MONDAY, rows, stored_at, watched) is expected


def test_old_rows_would_open_nothing_for_a_report_with_a_record(monkeypatch):
    svc = _svc()
    rows = [_row("INTC", MONDAY)]
    svc.observe_rows(MONDAY, rows, _et(MONDAY, 0), fetched=True)
    stored_at = _stored_ago(monkeypatch, _TWELVE_HOURS)
    assert svc.old_rows_would_open(MONDAY, rows, stored_at, ["INTC"]) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("at", [_et(MONDAY, 0), _et(SUNDAY, 10)],
                         ids=["before_six", "closed_day"])
async def test_old_rows_with_nothing_to_open_cost_no_round(monkeypatch, at):
    fmp = _FMP(_market(FRIDAY))
    await _memo_filled_on(monkeypatch, fmp, FRIDAY)
    _memo_held_for(72)
    today = els.et_now(at).date()
    monkeypatch.setattr(ts, "_et_today", lambda: today)
    _nothing_is_due(today)
    await _tick(fmp, at, "INTC")
    assert fmp.calls == []


@pytest.mark.asyncio
async def test_an_open_record_costs_no_round_however_old_the_held_rows(monkeypatch):
    fmp = _FMP({**_market(FRIDAY), SUNDAY: _intc_on(SUNDAY)})
    await _memo_filled_on(monkeypatch, fmp, FRIDAY)
    _memo_held_for(13)
    monkeypatch.setattr(ts, "_et_today", lambda: SUNDAY)
    _nothing_is_due(SUNDAY)
    at = _et(SUNDAY, 4)
    _svc().observe_rows(SUNDAY, [_row("INTC", SUNDAY)], at, fetched=True)
    await _tick(fmp, at, "INTC")
    assert fmp.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("last_trading_day,report,closed_day,new_date", [
    (FRIDAY, MONDAY, SUNDAY, TUESDAY),
    (date(2026, 11, 25), date(2026, 11, 27), date(2026, 11, 26), date(2026, 11, 30)),
], ids=["weekend", "thanksgiving"])
async def test_a_report_fmp_moved_across_a_closed_day_opens_nothing(
        monkeypatch, last_trading_day, report, closed_day, new_date):
    """Nobody opens the app after the move, so the tick is the only reader. The last trading
    day's rows must not make this a report day: LIVE, the hot fetch, an SEC slot, then a card."""
    fmp = _FMP({**_market(last_trading_day), report: _intc_on(report)})

    def _script(at: datetime) -> None:
        if at == _et(closed_day, 12):
            _fmp_moves_intc(fmp, report, new_date)

    end = _et(new_date, 9)
    calls = await _calls_per_day(monkeypatch, fmp, _et(last_trading_day, 18), end, ["INTC"],
                                 _script)
    assert _svc().snapshot("INTC", report, end) is None
    assert _svc().recent_snapshots(["INTC"], end) == []
    assert _svc().snapshot("INTC", new_date, end).status == els.STATUS_LIVE
    assert calls[report] == 127, "111 for the memo, 15 at midnight, one first look at the closed day"


@pytest.mark.asyncio
async def test_a_closed_day_report_fmp_moved_opens_nothing_on_its_day(monkeypatch):
    """Sunday's rows were stored on Friday, so a Sunday-dated report has Monday's gap a day
    earlier, on a day the tick otherwise never pays."""
    fmp = _FMP({**_market(FRIDAY), SUNDAY: _intc_on(SUNDAY)})

    def _script(at: datetime) -> None:
        if at == _et(SATURDAY, 12):
            _fmp_moves_intc(fmp, SUNDAY, MONDAY)

    end = _et(MONDAY, 0)
    calls = await _calls_per_day(monkeypatch, fmp, _et(FRIDAY, 18), end, ["INTC"], _script)
    assert _svc().snapshot("INTC", SUNDAY, end) is None
    assert calls[SUNDAY] == 16, "the round before the fold and Saturday's first look"


@pytest.mark.asyncio
async def test_a_closed_day_report_fmp_kept_pays_one_round_and_goes_hot(monkeypatch):
    fmp = _FMP({**_market(FRIDAY), SUNDAY: _intc_on(SUNDAY)})
    end = _et(MONDAY, 0)
    calls = await _calls_per_day(monkeypatch, fmp, _et(FRIDAY, 18), end, ["INTC"])
    assert _svc().snapshot("INTC", SUNDAY, end) is not None
    assert calls[SUNDAY] == 208, "192 hot, 15 for the one round, Saturday's first look"


@pytest.mark.asyncio
async def test_a_monday_with_a_watched_report_pays_one_round_at_midnight(monkeypatch):
    fmp = _FMP({**_market(FRIDAY), MONDAY: _intc_on(MONDAY)})
    calls = await _calls_per_day(monkeypatch, fmp, _et(FRIDAY, 18), _et(TUESDAY, 0), ["INTC"])
    assert calls[MONDAY] == 319, "192 hot, 111 for the memo, 15 at midnight, a first look at Sunday"


@pytest.mark.asyncio
async def test_a_report_fmp_kept_over_the_weekend_still_lands_on_its_overnight_fill(monkeypatch):
    """The round at midnight sees it pending, as the evening's rows do on any other night."""
    fmp = _FMP({**_market(FRIDAY), MONDAY: _intc_on(MONDAY)})

    def _script(at: datetime) -> None:
        if at == _et(MONDAY, 5):
            fmp.by_day[MONDAY] = [_row("ZZZZ", MONDAY), _row("INTC", MONDAY, eps=0.60, rev=17.1e9)]

    end = _et(MONDAY, 6, 1)
    await _calls_per_day(monkeypatch, fmp, _et(FRIDAY, 18), end, ["INTC"], _script)
    assert [(s.symbol, s.day) for s in _svc().landed_snapshots(end)] == [("INTC", MONDAY)]


@pytest.mark.asyncio
@pytest.mark.parametrize("moved", [True, False], ids=["moved", "kept"])
async def test_a_failed_midnight_round_opens_nothing_and_the_next_round_decides(monkeypatch, moved):
    """A failed round serves the rows it held, here Friday's, so the moment FMP blips must not
    turn them into a report day."""
    fmp = _FMP({**_market(FRIDAY), MONDAY: _intc_on(MONDAY)})
    outage = _fmp_can_go_down(fmp)
    during: List[Optional[els.LiveSnapshot]] = []

    def _script(at: datetime) -> None:
        if moved and at == _et(SUNDAY, 12):
            _fmp_moves_intc(fmp, MONDAY, TUESDAY)
        outage["down"] = _et(MONDAY, 0) <= at < _et(MONDAY, 0, 10)
        if at == _et(MONDAY, 0, 9):
            during.append(_svc().snapshot("INTC", MONDAY, at))

    end = _et(MONDAY, 0, 15)
    calls = await _calls_per_day(monkeypatch, fmp, _et(FRIDAY, 18), end, ["INTC"], _script)
    assert during == [None]
    assert (_svc().snapshot("INTC", MONDAY, end) is None) == moved
    assert calls[MONDAY] == 96, ("one round per 120 s wait, not one per tick: 5 of 15 failed and "
                                 "one good, with the first look at Sunday beside each")


@pytest.mark.asyncio
@pytest.mark.parametrize("door", _DOORS)
async def test_no_door_opens_a_record_on_rows_a_failed_round_left_old(monkeypatch, door):
    svc_obj = _door_service(monkeypatch, [_avgo(None)])
    await ts._cached_earnings_calendar(svc_obj.fmp, REPORT)
    _memo_held_for(13)
    _a_day_goes_down(svc_obj.fmp, NEXT)
    at = _et(REPORT, 9)
    await _through(door, svc_obj, at, monkeypatch)
    assert ts._earnings_calendar_retry_after is not None, "the round must have failed"
    assert _svc().snapshot("AVGO", REPORT, at) is None


@pytest.mark.asyncio
async def test_the_card_and_the_sheet_still_answer_when_old_rows_open_no_record(monkeypatch):
    """The row is still the best schedule there is, so it still draws a plain card. Only the
    LIVE state waits for an answer from FMP."""
    svc_obj = _door_service(monkeypatch, [_avgo(None)])
    await ts._cached_earnings_calendar(svc_obj.fmp, REPORT)
    _memo_held_for(13)
    _a_day_goes_down(svc_obj.fmp, NEXT)
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_et(REPORT, 9)))
    (card,) = await svc_obj._get_earnings_alerts(["AVGO"])
    assert (card.ticker, card.earnings_status) == ("AVGO", els.STATUS_UPCOMING)
    sheet = await svc_obj.get_earnings_live("AVGO")
    assert (sheet.status, sheet.poll_seconds) == (els.STATUS_UPCOMING, None)


@pytest.mark.asyncio
async def test_the_sec_watch_stays_on_todays_reports(monkeypatch):
    """A filing is matched to today's report, so a closed day's report is FMP's alone."""
    watcher = _Watcher()
    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: watcher)
    monkeypatch.setattr(ts, "_et_today", lambda: SUNDAY)
    _svc().observe_rows(SATURDAY, [_row("INTC", SATURDAY)], _et(SATURDAY, 20))
    fmp = _FMP({SATURDAY: [_row("INTC", SATURDAY)], SUNDAY: [_row("MSFT", SUNDAY)]})

    async def _dispatch(snaps, now):
        return None

    await els.earnings_live_tick(_et(SUNDAY, 9), fmp=fmp, watched=["INTC", "MSFT"],
                                 dispatch=_dispatch)
    await els.earnings_filing_tick(_et(SUNDAY, 9), watched=["INTC", "MSFT"])
    assert watcher.calls == [(["MSFT"], SUNDAY)]
    assert watcher.kept == els.live_days(SUNDAY)


# Cards: one per symbol, and an awaiting one drops once.


def _visible_at(svc: els.EarningsLiveService, *moments: tuple) -> List[bool]:
    return [bool(svc.recent_snapshots(["INTC"], _et(*at))) for at in moments]


def test_a_friday_awaiting_card_stays_up_through_the_weekend_and_drops_once():
    fri, sat, sun, mon = NEXT, date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)
    svc = _svc()
    svc.observe_rows(fri, [_row("INTC", fri)], _et(fri, 9))
    assert _visible_at(svc, (sat, 9), (sat, 15), (sun, 9), (sun, 15), (mon, 9), (mon, 15)) == [
        True, True, True, True, True, False]
    assert _visible_at(svc, (mon, 11, 59), (mon, 12)) == [True, False]


def test_a_pre_holiday_awaiting_card_drops_once():
    wed, thanksgiving, fri = date(2026, 11, 25), date(2026, 11, 26), date(2026, 11, 27)
    svc = _svc()
    svc.observe_rows(wed, [_row("INTC", wed)], _et(wed, 9))
    assert _visible_at(svc, (thanksgiving, 15), (fri, 9), (fri, 12)) == [True, True, False]


def test_a_saturday_awaiting_card_drops_once_on_monday():
    svc = _svc()
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY)], _et(SATURDAY, 9))
    assert _visible_at(svc, (SUNDAY, 9), (SUNDAY, 15), (MONDAY, 9), (MONDAY, 12)) == [
        True, True, True, False]


def test_two_reports_in_the_window_draw_one_card_the_newest():
    """iOS ids a card by ticker and date, so a Friday and a Saturday report would be two."""
    svc = _svc()
    svc.observe_rows(FRIDAY, [_row("INTC", FRIDAY, eps=0.44, rev=17.1e9)], _et(FRIDAY, 17))
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY, eps=0.45, rev=17.2e9)], _et(SATURDAY, 11))
    (snap,) = svc.recent_snapshots(["INTC"], _et(SUNDAY, 9))
    assert (snap.day, snap.eps.actual) == (SATURDAY, 0.45)


def test_a_report_still_awaiting_never_hides_an_older_reported_one():
    """A company does not report twice in a few days: the pending row is a leftover."""
    svc = _svc()
    svc.observe_rows(FRIDAY, [_row("INTC", FRIDAY, eps=0.44, rev=17.1e9)], _et(FRIDAY, 17))
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY)], _et(SATURDAY, 9))
    for at in ((SATURDAY, 22), (SUNDAY, 9), (MONDAY, 9), (MONDAY, 11, 59), (MONDAY, 12)):
        (snap,) = svc.recent_snapshots(["INTC"], _et(*at))
        assert (snap.day, snap.status, snap.eps.actual) == (FRIDAY, els.STATUS_REPORTED, 0.44), at


@pytest.mark.asyncio
@pytest.mark.parametrize("reported,leftover,moments", [
    (FRIDAY, SATURDAY, [(SUNDAY, 9), (MONDAY, 9), (MONDAY, 12)]),
    (date(2026, 11, 25), date(2026, 11, 26), [(date(2026, 11, 27), 9), (date(2026, 11, 27), 12)]),
], ids=["weekend", "thanksgiving"])
async def test_a_pending_leftover_on_a_closed_day_keeps_the_reported_card_through_the_tick(
        reported, leftover, moments, monkeypatch):
    fmp = _FMP({reported: [_row("INTC", reported, eps=0.44, rev=17.1e9)],
                leftover: [_row("INTC", leftover)]})

    async def _dispatch(snaps, now):
        pass

    for day, hour in moments:
        monkeypatch.setattr(ts, "_et_today", lambda d=day: d)
        now = _et(day, hour)
        await els.earnings_live_tick(now, fmp=fmp, watched=["INTC"], dispatch=_dispatch)
        (snap,) = _svc().recent_snapshots(["INTC"], now)
        assert (snap.day, snap.status, snap.eps.actual) == (
            reported, els.STATUS_REPORTED, 0.44), (day, hour)


def test_a_filing_figure_alone_makes_the_older_report_the_card():
    svc = _svc()
    svc.observe_rows(FRIDAY, [_row("INTC", FRIDAY)], _et(FRIDAY, 9))
    svc.absorb_filing("INTC", FRIDAY, efs.FilingFigures(
        accession="0000050863-26-000123", accepted_at=None, url=None, revenue=17.1e9),
        _et(FRIDAY, 16, 10))
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY)], _et(SATURDAY, 9))
    (snap,) = svc.recent_snapshots(["INTC"], _et(SUNDAY, 9))
    assert (snap.day, snap.status, snap.preliminary) == (FRIDAY, els.STATUS_REPORTED, True)


def test_a_held_figure_is_not_a_result_so_it_does_not_outrank_a_newer_awaiting_report():
    """A dropped-digit EPS shows nothing yet, so its report reads awaiting like the other."""
    svc = _svc()
    svc.observe_rows(FRIDAY, [_row("INTC", FRIDAY, eps=0.04, eps_est=0.41, rev_est=None)],
                     _et(FRIDAY, 17))
    assert svc.snapshot("INTC", FRIDAY, _et(SUNDAY, 9)).eps.checking
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY)], _et(SATURDAY, 9))
    (snap,) = svc.recent_snapshots(["INTC"], _et(SUNDAY, 9))
    assert (snap.day, snap.status) == (SATURDAY, els.STATUS_AWAITING)


def test_a_newer_reported_report_beats_an_older_one_still_awaiting():
    """Some companies do report on a Saturday, and the Friday row is then the leftover."""
    svc = _svc()
    svc.observe_rows(FRIDAY, [_row("INTC", FRIDAY)], _et(FRIDAY, 9))
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY, eps=0.45, rev=17.2e9)], _et(SATURDAY, 11))
    for at in ((SUNDAY, 9), (MONDAY, 9), (MONDAY, 12)):
        assert [s.day for s in svc.recent_snapshots(["INTC"], _et(*at))] == [SATURDAY], at


def test_two_reports_still_awaiting_draw_the_newest_and_drop_together():
    svc = _svc()
    svc.observe_rows(FRIDAY, [_row("INTC", FRIDAY)], _et(FRIDAY, 9))
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY)], _et(SATURDAY, 9))
    assert [s.day for s in svc.recent_snapshots(["INTC"], _et(MONDAY, 11, 59))] == [SATURDAY]
    assert svc.recent_snapshots(["INTC"], _et(MONDAY, 12)) == []


def test_each_symbol_picks_its_own_card():
    svc = _svc()
    svc.observe_rows(FRIDAY, [_row("INTC", FRIDAY, eps=0.44, rev=17.1e9)], _et(FRIDAY, 17))
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY), _row("MSFT", SATURDAY)], _et(SATURDAY, 9))
    assert [(s.symbol, s.day, s.status) for s in svc.recent_snapshots(
        ["msft", "INTC", "intc"], _et(SUNDAY, 9))] == [
            ("INTC", FRIDAY, els.STATUS_REPORTED), ("MSFT", SATURDAY, els.STATUS_AWAITING)]


def _report_on(day: date, status: str) -> els.LiveSnapshot:
    pending = els.LiveMetric(None)
    return els.LiveSnapshot("INTC", day, status, None, pending, pending, False, None, None)


@pytest.mark.parametrize("snaps,expected", [
    ([], None),
    ([(FRIDAY, els.STATUS_AWAITING)], FRIDAY),
    ([(FRIDAY, els.STATUS_REPORTED), (SATURDAY, els.STATUS_AWAITING)], FRIDAY),
    ([(SATURDAY, els.STATUS_AWAITING), (FRIDAY, els.STATUS_REPORTED)], FRIDAY),
    ([(FRIDAY, els.STATUS_AWAITING), (SATURDAY, els.STATUS_REPORTED)], SATURDAY),
    ([(FRIDAY, els.STATUS_REPORTED), (SATURDAY, els.STATUS_REPORTED)], SATURDAY),
    ([(SATURDAY, els.STATUS_REPORTED), (FRIDAY, els.STATUS_REPORTED)], SATURDAY),
    ([(FRIDAY, els.STATUS_AWAITING), (SATURDAY, els.STATUS_AWAITING)], SATURDAY),
], ids=["empty", "one", "reported_oldest", "reported_oldest_unsorted", "reported_newest",
        "two_reported", "two_reported_unsorted", "two_awaiting"])
def test_newest_report_prefers_a_reported_report_then_the_date_in_any_order(snaps, expected):
    picked = els.newest_report(_report_on(day, status) for day, status in snaps)
    assert (picked.day if picked else None) == expected


# The sheet.


def _calendar_of(*rows: dict):
    async def _calendar(fmp, today, *, later_days_since=None):
        return list(rows)

    return _calendar


def _sheet_on(monkeypatch, today: date) -> ts.TrackingService:
    _no_history(monkeypatch)
    monkeypatch.setattr(ts, "_et_today", lambda: today)
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_et(today, 12)))
    svc_obj = ts.TrackingService.__new__(ts.TrackingService)
    svc_obj.fmp = _FMP()
    return svc_obj


@pytest.mark.asyncio
@pytest.mark.parametrize("today", [SUNDAY, MONDAY])
@pytest.mark.parametrize("requested", [None, SATURDAY], ids=["no_date", "by_date"])
async def test_the_sheet_answers_a_closed_day_report_on_every_day_it_is_live(
        monkeypatch, today, requested):
    svc_obj = _sheet_on(monkeypatch, today)
    _svc().observe_rows(SATURDAY, [_row("INTC", SATURDAY, eps=0.5, rev=17e9)], _et(SATURDAY, 11))
    sheet = await svc_obj.get_earnings_live("INTC", requested)
    assert (sheet.report_date, sheet.status) == (SATURDAY.isoformat(), els.STATUS_REPORTED)


@pytest.mark.asyncio
async def test_without_a_date_the_sheet_takes_the_newest_report_before_today(monkeypatch):
    svc_obj = _sheet_on(monkeypatch, MONDAY)
    _svc().observe_rows(FRIDAY, [_row("INTC", FRIDAY, eps=0.44, rev=17.1e9)], _et(FRIDAY, 17))
    _svc().observe_rows(SATURDAY, [_row("INTC", SATURDAY, eps=0.45, rev=17.2e9)],
                        _et(SATURDAY, 11))
    assert (await svc_obj.get_earnings_live("INTC")).report_date == SATURDAY.isoformat()


REPORT_SHAPES = {
    "nothing": None,
    "pending": {},
    "reported": {"eps": 0.44, "rev": 17.1e9},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("friday", REPORT_SHAPES)
@pytest.mark.parametrize("saturday", REPORT_SHAPES)
@pytest.mark.parametrize("at", [(SUNDAY, 9), (MONDAY, 9), (MONDAY, 11, 59), (MONDAY, 12)],
                         ids=["sunday", "monday_morning", "before_noon", "noon"])
async def test_the_card_and_the_undated_sheet_always_name_the_same_report(
        monkeypatch, friday, saturday, at):
    """A wrong pick here reads "results aren't in yet" under a card that shows the figures."""
    svc_obj = _sheet_on(monkeypatch, at[0])
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_et(*at)))
    for day, shape in ((FRIDAY, friday), (SATURDAY, saturday)):
        if REPORT_SHAPES[shape] is not None:
            _svc().observe_rows(day, [_row("INTC", day, **REPORT_SHAPES[shape])], _et(day, 17))
    cards = _svc().recent_snapshots(["INTC"], _et(*at))
    sheet = await svc_obj.get_earnings_live("INTC")
    if cards:
        assert (sheet.report_date, sheet.status) == (cards[0].day.isoformat(), cards[0].status)


@pytest.mark.asyncio
@pytest.mark.parametrize("today", [SUNDAY, MONDAY])
async def test_a_report_still_awaiting_never_hides_an_older_reported_one_in_the_undated_sheet(
        monkeypatch, today):
    svc_obj = _sheet_on(monkeypatch, today)
    _svc().observe_rows(FRIDAY, [_row("INTC", FRIDAY, eps=0.44, rev=17.1e9)], _et(FRIDAY, 17))
    _svc().observe_rows(SATURDAY, [_row("INTC", SATURDAY)], _et(SATURDAY, 9))
    sheet = await svc_obj.get_earnings_live("INTC")
    assert (sheet.report_date, sheet.status, sheet.eps.actual) == (
        FRIDAY.isoformat(), els.STATUS_REPORTED, 0.44)


@pytest.mark.asyncio
async def test_a_dated_sheet_still_answers_the_report_it_asked_for(monkeypatch):
    """The card that was on screen before the older report filled still opens its own date."""
    svc_obj = _sheet_on(monkeypatch, SUNDAY)
    _svc().observe_rows(FRIDAY, [_row("INTC", FRIDAY, eps=0.44, rev=17.1e9)], _et(FRIDAY, 17))
    _svc().observe_rows(SATURDAY, [_row("INTC", SATURDAY)], _et(SATURDAY, 9))
    sheet = await svc_obj.get_earnings_live("INTC", SATURDAY)
    assert (sheet.report_date, sheet.status) == (SATURDAY.isoformat(), els.STATUS_AWAITING)


@pytest.mark.asyncio
async def test_the_undated_sheet_keeps_answering_a_report_whose_card_has_dropped(monkeypatch):
    svc_obj = _sheet_on(monkeypatch, MONDAY)
    _svc().observe_rows(SATURDAY, [_row("INTC", SATURDAY)], _et(SATURDAY, 9))
    assert _svc().recent_snapshots(["INTC"], _et(MONDAY, 12)) == []
    sheet = await svc_obj.get_earnings_live("INTC")
    assert (sheet.report_date, sheet.status) == (SATURDAY.isoformat(), els.STATUS_AWAITING)


@pytest.mark.asyncio
async def test_today_s_report_still_leads_the_undated_sheet(monkeypatch):
    svc_obj = _sheet_on(monkeypatch, MONDAY)
    monkeypatch.setattr(ts, "_cached_earnings_calendar", _calendar_of(_row("INTC", MONDAY)))
    _svc().observe_rows(FRIDAY, [_row("INTC", FRIDAY, eps=0.44, rev=17.1e9)], _et(FRIDAY, 17))
    assert (await svc_obj.get_earnings_live("INTC")).report_date == MONDAY.isoformat()


@pytest.mark.asyncio
async def test_the_sheet_refuses_a_date_before_the_window(monkeypatch):
    svc_obj = _sheet_on(monkeypatch, MONDAY)
    with pytest.raises(ValueError, match="outside 2026-11-05"):
        await svc_obj.get_earnings_live("INTC", FRIDAY - timedelta(days=2))


# ── a report FMP moved off its date ──────────────────────────────────────────


def _left(day: date = REPORT) -> _FMP:
    """FMP's answer once INTC has left `day`: the day still lists another report."""
    return _FMP({PREV: [_row("ZZZZ", PREV)], day: [_row("AAPL", day)]})


def _left_out_long_enough(day: date = REPORT) -> None:
    """The first own fetch that left each report out is the hold's length old, and the next own
    fetch is due: that fetch is the one that can end them."""
    svc = _svc()
    for (_, rec_day), rec in svc._records.items():
        if rec_day == day and rec.unlisted_since is not None:
            rec.unlisted_since -= els.EARNINGS_LIVE_UNLISTED_END_SECONDS
    svc._fetched[day] -= els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1


async def _left_out_for_good(fmp: _FMP, at: datetime, symbols: List[str],
                             day: date = REPORT) -> None:
    """Two own fetches, the hold apart, that leave a report out: the end of it."""
    await _svc().refresh(at, fmp=fmp, symbols=symbols)
    _left_out_long_enough(day)
    await _svc().refresh(at, fmp=fmp, symbols=symbols)


@pytest.mark.asyncio
async def test_a_row_gone_from_our_own_fetch_ends_its_figureless_record():
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT), _row("AAPL", REPORT)], _et(REPORT, 9))
    fmp = _FMP({REPORT: [_row("AAPL", REPORT)], PREV: []})
    await _left_out_for_good(fmp, _et(REPORT, 9, 5), ["INTC", "AAPL"])
    assert svc.snapshot("INTC", REPORT, _et(REPORT, 9, 5)) is None
    assert svc.pending_symbols(REPORT, ["INTC"]) == []
    assert svc.recent_snapshots(["INTC"], _et(NEXT, 8)) == []
    assert svc.snapshot("AAPL", REPORT, _et(REPORT, 9, 5)).status == els.STATUS_LIVE


@pytest.mark.asyncio
async def test_a_report_with_no_record_left_stops_asking_fmp():
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    await _left_out_for_good(_left(), _et(REPORT, 9, 5), ["INTC"])
    svc._fetched[REPORT] -= els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1
    assert svc.due_days(_et(REPORT, 9, 20), ["INTC"]) == []


@pytest.mark.asyncio
async def test_an_empty_own_fetch_drops_nothing_so_a_landing_survives():
    svc = _svc()
    svc.observe_rows(PREV, [_row("INTC", PREV)], _et(REPORT, 8), fetched=True)
    await svc.refresh(_et(REPORT, 8, 5), fmp=_FMP({}), symbols=["INTC"])
    svc.observe_rows(PREV, [_row("INTC", PREV, eps=0.44, rev=17.1e9)], _et(REPORT, 9), fetched=True)
    assert [s.symbol for s in svc.landed_snapshots(_et(REPORT, 9))] == ["INTC"]


@pytest.mark.asyncio
async def test_a_held_figure_survives_its_row_leaving():
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.041)], _et(REPORT, 17))
    fmp = _FMP({REPORT: [_row("AAPL", REPORT)], PREV: []})
    await svc.refresh(_et(REPORT, 17, 5), fmp=fmp, symbols=["INTC"])
    assert svc._records[("INTC", REPORT)].unlisted_since is None, "nothing to end, so no clock"
    assert svc.snapshot("INTC", REPORT, _et(REPORT, 17, 5)).eps.checking is True


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [
    _row("INTC", REPORT, eps=0.44),
    _row("INTC", REPORT, rev=17.1e9),
    _row("INTC", REPORT, eps=0.0),
    _row("INTC", REPORT, rev=0.0, rev_est=30e6),
], ids=["eps", "revenue", "breakeven_eps", "pre_revenue_zero"])
async def test_any_figure_from_fmp_keeps_its_record_when_the_row_leaves(row):
    """A company that reported has reported, whatever FMP lists afterwards. Zero is a figure."""
    svc = _svc()
    svc.observe_rows(REPORT, [row], _et(REPORT, 17))
    await svc.refresh(_et(REPORT, 17, 5), fmp=_left(), symbols=["INTC"])
    assert svc.snapshot("INTC", REPORT, _et(REPORT, 17, 5)) is not None


@pytest.mark.asyncio
async def test_a_filing_keeps_its_record_when_the_row_leaves():
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 16))
    svc.absorb_filing("INTC", REPORT, _FILED_GAAP, _et(REPORT, 16, 3))
    await svc.refresh(_et(REPORT, 16, 5), fmp=_left(), symbols=["INTC"])
    assert svc.snapshot("INTC", REPORT, _et(REPORT, 16, 5)).eps.actual == 0.38


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    [],
    [{"date": REPORT.isoformat()}],
    [{"symbol": "  ", "date": REPORT.isoformat()}],
    [_row("AAPL", NEXT)],
], ids=["empty", "no_symbol", "blank_symbol", "another_days_row"])
async def test_an_answer_that_lists_no_report_on_the_day_drops_nothing(answer):
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    await svc.refresh(_et(REPORT, 9, 5), fmp=_FMP({REPORT: answer}), symbols=["INTC"])
    assert svc.fetches == 2, "the fetch must have gone through"
    assert svc.pending_symbols(REPORT, ["INTC"]) == ["INTC"]


@pytest.mark.asyncio
async def test_an_answer_at_fmps_row_cap_drops_nothing():
    """FMP cuts an answer silently at its cap, so a report missing from one proves nothing."""
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    rows = [_row(f"S{i}", REPORT) for i in range(ews._TRUNCATION_ROWS)]
    await svc.refresh(_et(REPORT, 9, 5), fmp=_FMP({REPORT: rows}), symbols=["INTC"])
    assert svc.snapshot("S0", REPORT, _et(REPORT, 9, 5)) is not None, "the answer still folds"
    assert svc.pending_symbols(REPORT, ["INTC"]) == ["INTC"]


@pytest.mark.asyncio
async def test_a_failed_round_drops_nothing_even_where_one_day_answered():
    """`fetch_calendar_days` is all-or-nothing, so a day that answered is not read either."""

    class _PrevIsDown(_FMP):
        async def get_earnings_calendar(self, from_date=None, to_date=None):
            if from_date == PREV.isoformat():
                return EmptyAfterFailure("FMP 503")
            return await super().get_earnings_calendar(from_date=from_date, to_date=to_date)

    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    await svc.refresh(_et(REPORT, 9, 5), fmp=_PrevIsDown({REPORT: [_row("AAPL", REPORT)]}),
                      symbols=["INTC"])
    assert svc.pending_symbols(REPORT, ["INTC"]) == ["INTC"]


@pytest.mark.asyncio
@pytest.mark.parametrize("listed", [
    {"symbol": " intc ", "date": REPORT.isoformat()},
    {"symbol": "INTC", "date": f"{REPORT.isoformat()} 16:00:00"},
], ids=["lower_case_symbol", "timestamp_date"])
async def test_a_row_in_another_shape_still_lists_its_report(listed):
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    fmp = _FMP({PREV: [], REPORT: [_row("AAPL", REPORT), listed]})
    await svc.refresh(_et(REPORT, 9, 5), fmp=fmp, symbols=["INTC"])
    assert svc.pending_symbols(REPORT, ["INTC"]) == ["INTC"]


@pytest.mark.asyncio
async def test_only_the_day_fetched_loses_a_record():
    svc = _svc()
    svc.observe_rows(PREV, [_row("INTC", PREV)], _et(REPORT, 9), fetched=True)
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    svc._fetched[PREV] = time.monotonic()
    await _left_out_for_good(_left(), _et(REPORT, 9, 5), ["INTC"])
    assert svc.snapshot("INTC", REPORT, _et(REPORT, 9, 5)) is None
    assert svc.snapshot("INTC", PREV, _et(REPORT, 9, 5)) is not None


def test_a_fold_alone_never_ends_a_record():
    """A fold can be narrowed to one reader's tickers, and the memo can be half an hour old."""
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT), _row("AAPL", REPORT)], _et(REPORT, 9))
    svc.observe_rows(REPORT, [_row("AAPL", REPORT)], _et(REPORT, 9, 5), fetched=True)
    svc.observe_rows(REPORT, [_row("AAPL", REPORT)], _et(REPORT, 9, 6))
    assert svc.pending_symbols(REPORT, ["INTC"]) == ["INTC"]


@pytest.mark.asyncio
async def test_a_previous_day_report_that_left_its_answer_draws_no_morning_card():
    """A day before today has no memo row to bring it back, so its end is final."""
    svc = _svc()
    svc.observe_rows(PREV, [_row("INTC", PREV), _row("AAPL", PREV)], _et(PREV, 9), fetched=True)
    assert [s.symbol for s in svc.recent_snapshots(["INTC"], _et(REPORT, 8))] == ["INTC"]
    await _left_out_for_good(_left(PREV), _et(REPORT, 8), ["INTC"], PREV)
    assert svc.recent_snapshots(["INTC"], _et(REPORT, 8)) == []


@pytest.mark.asyncio
async def test_a_row_that_comes_back_opens_its_report_again_and_still_lands():
    """A day keeps being asked after this only for another report. If this one comes back filled
    rather than pending it reads as first seen reported, the price of having ended it."""
    svc = _svc()
    svc.observe_rows(PREV, [_row("INTC", PREV)], _et(PREV, 9), fetched=True)
    await _left_out_for_good(_left(PREV), _et(REPORT, 8), ["INTC"], PREV)
    assert svc.snapshot("INTC", PREV, _et(REPORT, 8)) is None
    svc.observe_rows(PREV, [_row("INTC", PREV)], _et(REPORT, 8, 5), fetched=True)
    svc.observe_rows(PREV, [_row("INTC", PREV, eps=0.44, rev=17.1e9)], _et(REPORT, 8, 10),
                     fetched=True)
    assert [s.symbol for s in svc.landed_snapshots(_et(REPORT, 8, 10))] == ["INTC"]


@pytest.mark.asyncio
async def test_a_filing_brings_an_ended_report_back_and_lands():
    """An 8-K read in the same minute outranks the calendar: the company has reported."""
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 16))
    await _left_out_for_good(_left(), _et(REPORT, 16, 1), ["INTC"])
    assert svc.snapshot("INTC", REPORT, _et(REPORT, 16, 1)) is None
    svc.absorb_filing("INTC", REPORT, _FILED_ADJUSTED, _et(REPORT, 16, 2))
    assert [s.symbol for s in svc.landed_snapshots(_et(REPORT, 16, 3))] == ["INTC"]


@pytest.mark.asyncio
async def test_an_ended_report_is_logged_with_its_symbol_and_day(caplog):
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT), _row("MSFT", REPORT)], _et(REPORT, 9))
    with caplog.at_level(logging.INFO, logger=els.logger.name):
        await _left_out_for_good(_left(), _et(REPORT, 9, 5), ["INTC"])
    (line,) = [r.getMessage() for r in caplog.records if "no longer list" in r.getMessage()]
    assert REPORT.isoformat() in line and "INTC" in line and "MSFT" in line


@pytest.mark.asyncio
async def test_no_reader_flickers_while_the_memo_still_holds_a_moved_row(monkeypatch):
    """The card comes from the memo's row, so until that row goes the record must answer: with
    none it would drop from LIVE to upcoming and back on every own fetch."""
    _no_history(monkeypatch)
    at = _et(REPORT, 9, 5)
    svc_obj = _feed_service(monkeypatch, at, [_row("ZZZZ", REPORT), _row("INTC", REPORT)])
    (card,) = await svc_obj._get_earnings_alerts(["INTC"])
    assert card.earnings_status == els.STATUS_LIVE
    svc_obj.fmp.by_day[REPORT] = [_row("ZZZZ", REPORT)]
    await _left_out_for_good(svc_obj.fmp, at, ["INTC"])
    assert _svc().snapshot("INTC", REPORT, at) is None, "the fetches closed it"
    (card,) = await svc_obj._get_earnings_alerts(["INTC"])
    assert card.earnings_status == els.STATUS_LIVE
    await _svc().refresh(at, fmp=svc_obj.fmp, symbols=["INTC"])
    sheet = await svc_obj.get_earnings_live("INTC")
    assert (sheet.status, sheet.report_date) == (els.STATUS_LIVE, REPORT.isoformat())


@pytest.mark.asyncio
async def test_a_report_moved_off_today_ends_its_report_day_once_the_memo_catches_up(monkeypatch):
    """Nobody opens the app, so the real tick is the only reader. The worst move comes just
    after a memo round: the row rides on for 30 min, then the own fetches close the record."""
    moved_at = _et(MONDAY, 9, 1)
    settled_by = moved_at + timedelta(minutes=45)
    end = _et(MONDAY, 13)
    fmp = _FMP({**_market(FRIDAY), MONDAY: _intc_on(MONDAY)})
    open_at: List[datetime] = []
    fetches_when_settled: List[int] = []

    def _script(at: datetime) -> None:
        if at == moved_at:
            _fmp_moves_intc(fmp, MONDAY, TUESDAY + timedelta(days=2))
        if _svc().pending_symbols(MONDAY, ["INTC"]):
            open_at.append(at)
        if at == settled_by:
            fetches_when_settled.append(_svc().fetches)

    await _calls_per_day(monkeypatch, fmp, _et(MONDAY, 6), end, ["INTC"], _script)
    assert moved_at in open_at, "still a report day when FMP moved it"
    assert max(open_at) < settled_by
    assert max(open_at) >= moved_at + timedelta(minutes=25), "the memo rows carry it until then"
    assert _svc().snapshot("INTC", MONDAY, end) is None
    assert _svc().recent_snapshots(["INTC"], _et(TUESDAY, 8)) == []
    assert _svc().fetches == fetches_when_settled[0], "FMP's day is no longer watched"


@pytest.mark.asyncio
async def test_a_closed_day_report_fmp_moved_ends_with_nobody_opening_the_app(monkeypatch):
    """A closed day pays for no memo round, so the row rides on until it is too old to open the
    record again. The tick then asks FMP once, and the own fetches end it for good."""
    moved_at = _et(SUNDAY, 9)
    end = _et(SUNDAY, 13)
    fmp = _FMP({**_market(FRIDAY), SUNDAY: _intc_on(SUNDAY)})
    open_at: List[datetime] = []

    def _script(at: datetime) -> None:
        if at == moved_at:
            _fmp_moves_intc(fmp, SUNDAY, MONDAY)
        if _svc().pending_symbols(SUNDAY, ["INTC"]):
            open_at.append(at)

    await _calls_per_day(monkeypatch, fmp, _et(FRIDAY, 18), end, ["INTC"], _script)
    assert max(open_at) < _et(SUNDAY, 12, 15), "12 h after the midnight round, then the hold"
    assert max(open_at) >= _et(SUNDAY, 11, 55), "a closed day has no memo round before that"
    assert _svc().snapshot("INTC", SUNDAY, end) is None


# ── a short answer from FMP ──────────────────────────────────────────────────


def _answers(fmp: _FMP, day: date, timeline: Dict[datetime, List[dict]]) -> Callable[[datetime], None]:
    """An `on_tick`: from each moment in `timeline`, FMP's answer for `day` is those rows."""

    def _script(at: datetime) -> None:
        if at in timeline:
            fmp.by_day[day] = timeline[at]

    return _script


def _without(day: date, *symbols: str) -> List[dict]:
    """A short answer: not empty, so it reads as a real one, with `symbols` listed and no INTC."""
    return [_row(symbol, day) for symbol in ("ZZZZ", *symbols)]


def _filled(day: date, symbol: str = "INTC") -> dict:
    """46% over the estimate, so the sender owes the report both kinds."""
    return _row(symbol, day, eps=0.60, rev=17.1e9)


async def _monday_into_tuesday(monkeypatch, fmp: _FMP, script: Callable[[datetime], None],
                               watched: List[str]) -> datetime:
    """Monday's watched reports seen pending on Monday night, then the real tick and the real
    sender through Tuesday morning, past the hot window. Answers with the last moment run."""
    end = _et(TUESDAY, 12)
    await _calls_per_day(monkeypatch, fmp, _et(MONDAY, 21, 50), end, watched, script,
                         dispatch=sender.dispatch_landings)
    return end - timedelta(minutes=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("minute", range(5), ids=lambda m: f"08:0{m}")
async def test_a_short_answer_loses_nothing_when_the_row_returns_with_its_figures(
        monkeypatch, pushes, minute):
    """Every phase of the 5-min cadence, so the two fetches a 6-min gap can hold are both in it:
    no memo row can reopen a day before today, so the record alone keeps that day asked."""
    gone_at = _et(TUESDAY, 8, minute)
    fmp = _FMP({**_market(FRIDAY), MONDAY: _intc_on(MONDAY)})
    script = _answers(fmp, MONDAY, {gone_at: _without(MONDAY),
                                    gone_at + timedelta(minutes=6): [_row("ZZZZ", MONDAY),
                                                                     _filled(MONDAY)]})
    last = await _monday_into_tuesday(monkeypatch, fmp, script, ["INTC"])
    assert _svc().snapshot("INTC", MONDAY, last).status == els.STATUS_REPORTED
    assert [s.symbol for s in _svc().recent_snapshots(["INTC"], last)] == ["INTC"]
    assert pushes.sent == _sent_once(MONDAY)


@pytest.mark.asyncio
async def test_a_row_back_pending_after_a_short_answer_still_lands_when_it_fills(monkeypatch, pushes):
    fmp = _FMP({**_market(FRIDAY), MONDAY: _intc_on(MONDAY)})
    script = _answers(fmp, MONDAY, {_et(TUESDAY, 8): _without(MONDAY),
                                    _et(TUESDAY, 8, 6): _intc_on(MONDAY),
                                    _et(TUESDAY, 8, 20): [_row("ZZZZ", MONDAY), _filled(MONDAY)]})
    await _monday_into_tuesday(monkeypatch, fmp, script, ["INTC"])
    assert pushes.sent == _sent_once(MONDAY)


@pytest.mark.asyncio
async def test_one_short_answer_takes_no_report_of_the_day_with_it(monkeypatch, pushes):
    """It hits every report with no figure on the day at once."""
    symbols = ["AAPL", "AMD", "INTC", "MSFT", "NVDA"]
    pending = [_row(symbol, MONDAY) for symbol in symbols]
    fmp = _FMP({**_market(FRIDAY), MONDAY: _without(MONDAY) + pending})
    script = _answers(fmp, MONDAY, {_et(TUESDAY, 8): _without(MONDAY),
                                    _et(TUESDAY, 8, 6): [_row("ZZZZ", MONDAY)] + [
                                        _filled(MONDAY, symbol) for symbol in symbols]})
    await _monday_into_tuesday(monkeypatch, fmp, script, symbols)
    assert sorted(pushes.sent) == sorted(sent for symbol in symbols
                                         for sent in _sent_once(MONDAY, symbol))


@pytest.mark.asyncio
async def test_a_report_left_out_beside_another_still_pending_keeps_its_landing(monkeypatch, pushes):
    """The other report keeps the day asked, so the row comes back whatever happens: what must
    survive is that this one was seen pending."""
    fmp = _FMP({**_market(FRIDAY), MONDAY: _intc_on(MONDAY) + [_row("AAPL", MONDAY)]})
    script = _answers(fmp, MONDAY, {_et(TUESDAY, 8): _without(MONDAY, "AAPL"),
                                    _et(TUESDAY, 8, 6): [_row("AAPL", MONDAY), _filled(MONDAY)]})
    await _monday_into_tuesday(monkeypatch, fmp, script, ["INTC", "AAPL"])
    assert pushes.sent == _sent_once(MONDAY)


@pytest.mark.asyncio
async def test_a_short_answer_at_the_closed_day_pace_loses_nothing(monkeypatch, pushes):
    """A Saturday asks about Friday every 2 h, so one answer has the day to itself until then."""
    fmp = _FMP({**_market(FRIDAY), FRIDAY: _intc_on(FRIDAY)})
    script = _answers(fmp, FRIDAY, {_et(SATURDAY, 7, 59): _without(FRIDAY),
                                    _et(SATURDAY, 8, 10): [_row("ZZZZ", FRIDAY), _filled(FRIDAY)]})
    end = _et(SATURDAY, 12)
    await _calls_per_day(monkeypatch, fmp, _et(FRIDAY, 21, 50), end, ["INTC"], script,
                         dispatch=sender.dispatch_landings)
    assert _svc().snapshot("INTC", FRIDAY, end).status == els.STATUS_REPORTED
    assert pushes.sent == _sent_once(FRIDAY)


@pytest.mark.asyncio
async def test_a_gap_across_a_memo_round_keeps_todays_landing(monkeypatch, pushes):
    """Every gap up to 10 min, from two minutes before the memo's 16:00 round to four after it,
    at every phase of the own fetch. A memo round inside the gap stores the short answer, so no
    row can reopen a record that ended."""
    lost = []
    for gap, offset, phase in itertools.product((3, 5, 6, 10), range(-2, 5), range(5)):
        _reset_all()
        sender.reset()
        pushes.sent.clear()
        fmp = _FMP({**_market(FRIDAY), MONDAY: _intc_on(MONDAY)})
        gone_at = _et(MONDAY, 16) + timedelta(minutes=offset)
        answers = _answers(fmp, MONDAY, {gone_at: _without(MONDAY),
                                         gone_at + timedelta(minutes=gap): [_row("ZZZZ", MONDAY),
                                                                            _filled(MONDAY)]})
        start = _et(MONDAY, 15, 30)

        def _script(at: datetime, begin=start, shift=phase, answer=answers) -> None:
            if at == begin + timedelta(minutes=1):
                # Moves this fetch `shift` minutes off the memo's rounds.
                _svc()._fetched[MONDAY] = els.time.monotonic() - shift * 60
            answer(at)

        await _calls_per_day(monkeypatch, fmp, start, _et(MONDAY, 17), ["INTC"], _script,
                             dispatch=sender.dispatch_landings)
        if pushes.sent != _sent_once(MONDAY):
            lost.append((gap, offset, phase))
    assert lost == []


@pytest.mark.asyncio
async def test_a_gap_past_the_hold_ends_todays_report_and_its_figures_still_come_back(
        monkeypatch, pushes):
    """A move has to end, so a gap this long is read as one. The memo brings the row back with
    the figures right, first seen reported: no landing, never a push for it."""
    fmp = _FMP({**_market(FRIDAY), MONDAY: _intc_on(MONDAY)})
    script = _answers(fmp, MONDAY, {_et(MONDAY, 15, 59): _without(MONDAY),
                                    _et(MONDAY, 16, 20): [_row("ZZZZ", MONDAY), _filled(MONDAY)]})
    gone: List[datetime] = []

    def _watching(at: datetime) -> None:
        script(at)
        if _svc().snapshot("INTC", MONDAY, at) is None:
            gone.append(at)

    end = _et(MONDAY, 17)
    await _calls_per_day(monkeypatch, fmp, _et(MONDAY, 15, 30), end, ["INTC"], _watching,
                         dispatch=sender.dispatch_landings)
    assert gone, "it ended"
    snap = _svc().snapshot("INTC", MONDAY, end)
    assert (snap.status, snap.eps.actual) == (els.STATUS_REPORTED, 0.60)
    assert pushes.sent == []


@pytest.mark.asyncio
async def test_a_gap_past_the_hold_ends_a_day_before_today_with_no_wrong_figure(monkeypatch, pushes):
    fmp = _FMP({**_market(FRIDAY), MONDAY: _intc_on(MONDAY)})
    script = _answers(fmp, MONDAY, {_et(TUESDAY, 8): _without(MONDAY),
                                    _et(TUESDAY, 8, 20): [_row("ZZZZ", MONDAY), _filled(MONDAY)]})
    last = await _monday_into_tuesday(monkeypatch, fmp, script, ["INTC"])
    assert _svc().snapshot("INTC", MONDAY, last) is None
    assert _svc().recent_snapshots(["INTC"], last) == []
    assert pushes.sent == []


# ── how long a moved report takes to end ─────────────────────────────────────


@pytest.mark.asyncio
async def test_a_report_moved_off_today_settles_inside_45_minutes_at_every_phase(monkeypatch):
    """The worst is a record the memo's last row opens again just before the memo's next round:
    10 min of own fetches then start after that round."""
    took = []
    for moved_minute, phase in itertools.product(range(0, 30, 3), range(5)):
        _reset_all()
        moved_at = _et(MONDAY, 9) + timedelta(minutes=moved_minute)
        start = _et(MONDAY, 8, 55)
        fmp = _FMP({**_market(MONDAY), MONDAY: _intc_on(MONDAY)})
        open_at: List[datetime] = []

        def _script(at: datetime, moved=moved_at, shift=phase, was=fmp, seen=open_at) -> None:
            if at == start + timedelta(minutes=1):
                _svc()._fetched[MONDAY] = els.time.monotonic() - shift * 60
            if at == moved:
                _fmp_moves_intc(was, MONDAY, MONDAY + timedelta(days=4))
            if _svc().pending_symbols(MONDAY, ["INTC"]):
                seen.append(at)

        await _calls_per_day(monkeypatch, fmp, start, _et(MONDAY, 11), ["INTC"], _script)
        took.append(max(open_at) - moved_at)
    assert max(took) <= timedelta(minutes=45)


@pytest.mark.asyncio
async def test_one_answer_never_ends_a_report_and_its_watches_stay_on():
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    await svc.refresh(_et(REPORT, 9, 5), fmp=_left(), symbols=["INTC"])
    assert svc.snapshot("INTC", REPORT, _et(REPORT, 9, 5)).status == els.STATUS_LIVE
    assert svc.pending_symbols(REPORT, ["INTC"]) == ["INTC"], "the SEC watch stays on"
    svc._fetched[REPORT] -= els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1
    assert svc.due_days(_et(REPORT, 9, 10), ["INTC"]) == [REPORT], "and FMP is asked again"


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds,ends", [(0, False), (300, False), (594, False), (595, True),
                                          (600, True), (1800, True)],
                         ids=["same_moment", "next_hot_fetch", "just_short", "with_the_slack",
                              "ten_minutes", "next_cold_fetch"])
async def test_a_report_ends_on_an_own_fetch_ten_minutes_after_the_first_that_left_it_out(
        monkeypatch, seconds, ends):
    """10 min, because a 6-min gap can hold two fetches 5 min apart. The fetch that ends it may
    come due a few seconds early, like every cadence here."""
    clock = _Clock()
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    await svc.refresh(_et(REPORT, 9, 5), fmp=_left(), symbols=["INTC"])
    clock.at += seconds
    svc._fetched[REPORT] -= els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1
    await svc.refresh(_et(REPORT, 9, 15), fmp=_left(), symbols=["INTC"])
    assert (svc.snapshot("INTC", REPORT, _et(REPORT, 9, 15)) is None) is ends


@pytest.mark.asyncio
async def test_a_listing_between_two_answers_starts_the_count_again():
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    await svc.refresh(_et(REPORT, 9, 5), fmp=_left(), symbols=["INTC"])
    _left_out_long_enough()
    await svc.refresh(_et(REPORT, 9, 10), fmp=_FMP({REPORT: [_row("INTC", REPORT)]}),
                      symbols=["INTC"])
    assert svc._records[("INTC", REPORT)].unlisted_since is None
    svc._fetched[REPORT] -= els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1
    await svc.refresh(_et(REPORT, 9, 15), fmp=_left(), symbols=["INTC"])
    assert svc.pending_symbols(REPORT, ["INTC"]) == ["INTC"], "a new gap, not the old one"


@pytest.mark.asyncio
async def test_a_listing_in_an_answer_at_the_row_cap_still_counts_as_a_listing():
    """Such an answer cannot show a report is gone, and can still show it is there."""
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    await svc.refresh(_et(REPORT, 9, 5), fmp=_left(), symbols=["INTC"])
    _left_out_long_enough()
    rows = [_row("INTC", REPORT)] + [_row(f"S{i}", REPORT) for i in range(ews._TRUNCATION_ROWS)]
    await svc.refresh(_et(REPORT, 9, 10), fmp=_FMP({REPORT: rows}), symbols=["INTC"])
    assert svc._records[("INTC", REPORT)].unlisted_since is None


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    [],
    [_row(f"S{i}", REPORT) for i in range(ews._TRUNCATION_ROWS)],
], ids=["empty", "at_the_row_cap"])
async def test_an_answer_that_cannot_show_a_report_gone_neither_starts_nor_breaks_a_gap(answer):
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    await svc.refresh(_et(REPORT, 9, 5), fmp=_FMP({REPORT: answer}), symbols=["INTC"])
    assert svc._records[("INTC", REPORT)].unlisted_since is None, "it starts nothing"
    svc._fetched[REPORT] -= els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1
    await svc.refresh(_et(REPORT, 9, 10), fmp=_left(), symbols=["INTC"])
    started = svc._records[("INTC", REPORT)].unlisted_since
    assert started is not None
    svc._fetched[REPORT] -= els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1
    await svc.refresh(_et(REPORT, 9, 15), fmp=_FMP({REPORT: answer}), symbols=["INTC"])
    assert svc._records[("INTC", REPORT)].unlisted_since == started, "and breaks nothing"


@pytest.mark.asyncio
async def test_a_report_left_out_once_then_filed_stays_whatever_fmp_lists_next():
    """A filing is a figure, so nothing can end the record from there on."""
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 16))
    await svc.refresh(_et(REPORT, 16, 1), fmp=_left(), symbols=["INTC"])
    svc.absorb_filing("INTC", REPORT, _FILED_ADJUSTED, _et(REPORT, 16, 2))
    _left_out_long_enough()
    await svc.refresh(_et(REPORT, 16, 11), fmp=_left(), symbols=["INTC"])
    assert svc.snapshot("INTC", REPORT, _et(REPORT, 16, 11)).eps.actual == 0.44


def test_a_fold_never_counts_as_an_own_fetch_listing_a_report():
    """The memo can be half an hour older than the fetch that left the report out."""
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    svc._records[("INTC", REPORT)].unlisted_since = 123.0
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9, 1))
    assert svc._records[("INTC", REPORT)].unlisted_since == 123.0


@pytest.mark.asyncio
async def test_a_report_left_out_once_is_logged_with_its_symbol_and_day(caplog):
    svc = _svc()
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 9))
    with caplog.at_level(logging.INFO, logger=els.logger.name):
        await svc.refresh(_et(REPORT, 9, 5), fmp=_left(), symbols=["INTC"])
    (line,) = [r.getMessage() for r in caplog.records if "left out" in r.getMessage()]
    assert REPORT.isoformat() in line and "INTC" in line


# ── the sheet of a report FMP moved ──────────────────────────────────────────


async def _pinned_sheet(monkeypatch, fmp: _FMP, start: datetime, end: datetime, pinned: date,
                        script: Callable[[datetime], None]) -> List[tuple]:
    """What a sheet pinned to `pinned` answers after every real tick, through the real memo:
    (moment, status, report date) each time the answer changes."""
    _no_history(monkeypatch)
    svc_obj = ts.TrackingService.__new__(ts.TrackingService)
    svc_obj.fmp = fmp
    answers: List[tuple] = []

    async def _read(at: datetime) -> None:
        monkeypatch.setattr(ts, "datetime", _FrozenDatetime(at))
        sheet = await svc_obj.get_earnings_live("INTC", pinned)
        if not answers or answers[-1][1:] != (sheet.status, sheet.report_date):
            answers.append((at, sheet.status, sheet.report_date))

    await _calls_per_day(monkeypatch, fmp, start, end, ["INTC"], script, after_tick=_read)
    return answers


def _moved_on(fmp: _FMP, at: datetime, old: date, new: date) -> Callable[[datetime], None]:
    def _script(now: datetime) -> None:
        if now == at:
            _fmp_moves_intc(fmp, old, new)

    return _script


@pytest.mark.asyncio
@pytest.mark.parametrize("days_out", [2, 3, 13])
@pytest.mark.parametrize("hour,minute", [(9, 1), (6, 5)], ids=["after_a_near_round", "after_a_far_round"])
async def test_a_pinned_sheet_goes_from_live_to_the_new_date_and_never_says_none(
        monkeypatch, days_out, hour, minute):
    new = MONDAY + timedelta(days=days_out)
    fmp = _FMP({**_market(MONDAY), MONDAY: _intc_on(MONDAY)})
    script = _moved_on(fmp, _et(MONDAY, hour, minute), MONDAY, new)
    answers = await _pinned_sheet(monkeypatch, fmp, _et(MONDAY, 6), _et(MONDAY, 13), MONDAY, script)
    assert [answer[1:] for answer in answers] == [(els.STATUS_LIVE, MONDAY.isoformat()),
                                                  (els.STATUS_UPCOMING, new.isoformat())]


@pytest.mark.asyncio
async def test_a_move_to_tomorrow_still_goes_straight_to_upcoming(monkeypatch):
    fmp = _FMP({**_market(MONDAY), MONDAY: _intc_on(MONDAY)})
    script = _moved_on(fmp, _et(MONDAY, 9, 1), MONDAY, TUESDAY)
    answers = await _pinned_sheet(monkeypatch, fmp, _et(MONDAY, 6), _et(MONDAY, 13), MONDAY, script)
    assert [answer[1:] for answer in answers] == [(els.STATUS_LIVE, MONDAY.isoformat()),
                                                  (els.STATUS_UPCOMING, TUESDAY.isoformat())]


@pytest.mark.asyncio
async def test_a_sheet_pinned_to_tomorrow_follows_a_report_moved_days_out(monkeypatch):
    """A date after today has no record and no own fetch, so its row is all the sheet has, and
    it leaves on a near round while its new date sits on a day held for hours."""
    new = MONDAY + timedelta(days=4)
    fmp = _FMP({**_market(MONDAY), TUESDAY: _intc_on(TUESDAY)})
    script = _moved_on(fmp, _et(MONDAY, 9, 1), TUESDAY, new)
    answers = await _pinned_sheet(monkeypatch, fmp, _et(MONDAY, 6), _et(MONDAY, 13), TUESDAY, script)
    assert [answer[1:] for answer in answers] == [(els.STATUS_UPCOMING, TUESDAY.isoformat()),
                                                  (els.STATUS_UPCOMING, new.isoformat())]


@pytest.mark.asyncio
@pytest.mark.parametrize("days_out", [1, 4], ids=["to_tomorrow", "days_out"])
async def test_a_sheet_pinned_to_the_day_before_follows_a_report_moved_after_every_memo_round(
        monkeypatch, days_out):
    """Nothing in the memo shows this move: all it holds was stored at 06:00, before it. Only
    the own fetch does, so that fetch is what every day after today must be as new as."""
    new = TUESDAY + timedelta(days=days_out)
    fmp = _FMP({**_market(FRIDAY), MONDAY: _intc_on(MONDAY)})
    script = _moved_on(fmp, _et(TUESDAY, 6, 5), MONDAY, new)
    answers = await _pinned_sheet(monkeypatch, fmp, _et(MONDAY, 21, 50), _et(TUESDAY, 7), MONDAY, script)
    assert all(status != els.STATUS_NONE for _, status, _ in answers), answers
    assert answers[-1][1:] == (els.STATUS_UPCOMING, new.isoformat())


@pytest.mark.asyncio
async def test_a_closed_day_report_moved_days_out_is_followed_with_no_tick_paying_for_the_memo(
        monkeypatch):
    """Only the reader pays on a closed day, so it pays for the near days and the later ones."""
    new = SUNDAY + timedelta(days=3)
    fmp = _FMP({**_market(FRIDAY), SUNDAY: _intc_on(SUNDAY)})
    script = _moved_on(fmp, _et(SUNDAY, 9), SUNDAY, new)
    answers = await _pinned_sheet(monkeypatch, fmp, _et(SUNDAY, 5), _et(SUNDAY, 13), SUNDAY, script)
    assert all(status != els.STATUS_NONE for _, status, _ in answers), answers
    assert answers[-1][1:] == (els.STATUS_UPCOMING, new.isoformat())


@pytest.mark.asyncio
async def test_a_sheet_opened_with_no_date_finds_a_report_on_a_day_the_memo_held_from_before(
        monkeypatch):
    """The same `none` with nothing pinned: no row in the memo's days, one in FMP's answer."""
    new = MONDAY + timedelta(days=5)
    svc_obj = _sheet_on(monkeypatch, MONDAY)
    svc_obj.fmp = _FMP(_market(MONDAY))
    clock = _Clock()
    monkeypatch.setattr(ts, "_time", SimpleNamespace(monotonic=clock.monotonic))
    await ts._cached_earnings_calendar(svc_obj.fmp, MONDAY)
    clock.at += ts.EARNINGS_CALENDAR_TTL
    svc_obj.fmp.by_day[new] = _intc_on(new)
    sheet = await svc_obj.get_earnings_live("INTC")
    assert (sheet.status, sheet.report_date) == (els.STATUS_UPCOMING, new.isoformat())


@pytest.mark.asyncio
async def test_a_pinned_date_on_a_later_day_asks_for_nothing_the_answer_did_not_already_bring(
        monkeypatch):
    """Those days are stored together, so one that left the report out speaks for them all."""
    day = MONDAY + timedelta(days=5)
    svc_obj = _sheet_on(monkeypatch, MONDAY)
    svc_obj.fmp = _FMP(_market(MONDAY))
    await svc_obj.get_earnings_live("INTC", day)
    svc_obj.fmp.calls.clear()
    assert (await svc_obj.get_earnings_live("INTC", day)).status == els.STATUS_NONE
    assert svc_obj.fmp.calls == []


@pytest.mark.asyncio
async def test_a_report_moved_past_the_window_answers_none_and_it_is_true(monkeypatch):
    new = MONDAY + timedelta(days=20)
    fmp = _FMP({**_market(MONDAY), MONDAY: _intc_on(MONDAY)})
    script = _moved_on(fmp, _et(MONDAY, 9, 1), MONDAY, new)
    answers = await _pinned_sheet(monkeypatch, fmp, _et(MONDAY, 6), _et(MONDAY, 13), MONDAY, script)
    assert [answer[1:] for answer in answers] == [(els.STATUS_LIVE, MONDAY.isoformat()),
                                                  (els.STATUS_NONE, MONDAY.isoformat())]


@pytest.mark.asyncio
async def test_a_sheet_answering_none_asks_for_the_later_days_once_per_answer_it_rests_on(
        monkeypatch):
    """A ticker with nothing listed is opened over and over: only a newer answer for its day
    makes the later days worth asking for again."""
    svc_obj = ts.TrackingService.__new__(ts.TrackingService)
    svc_obj.fmp = _FMP(_market(MONDAY))
    _no_history(monkeypatch)
    clock = _Clock()
    monkeypatch.setattr(ts, "_time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(ts, "_et_today", lambda: MONDAY)
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_et(MONDAY, 9)))
    await ts._cached_earnings_calendar(svc_obj.fmp, MONDAY)
    clock.at += ts.EARNINGS_CALENDAR_TTL
    calls = svc_obj.fmp.calls
    calls.clear()
    for _ in range(4):
        assert (await svc_obj.get_earnings_live("ZZZY", MONDAY)).status == els.STATUS_NONE
        clock.at += 60
    assert len(calls) == 15, "the near round, then the 13 later days, then nothing"
    assert len(set(calls)) == 15
    calls.clear()
    clock.at += ts.EARNINGS_CALENDAR_TTL
    await svc_obj.get_earnings_live("ZZZY", MONDAY)
    assert len(calls) == 15, "a newer answer for its day"


@pytest.mark.asyncio
async def test_a_sheet_that_finds_the_report_leaves_the_later_days_to_their_own_ttl(monkeypatch):
    """It polls every 30 s while LIVE, so only a `none` may pay for those days: here they are
    older than the near days, and the read asks for the near days alone."""
    clock = _Clock()
    monkeypatch.setattr(ts, "_time", SimpleNamespace(monotonic=clock.monotonic))
    svc_obj = _sheet_on(monkeypatch, MONDAY)
    svc_obj.fmp = _FMP({**_market(MONDAY), MONDAY: _intc_on(MONDAY)})
    await svc_obj.get_earnings_live("INTC", MONDAY)
    clock.at += ts.EARNINGS_CALENDAR_TTL
    svc_obj.fmp.calls.clear()
    await svc_obj.get_earnings_live("INTC", MONDAY)
    assert sorted(svc_obj.fmp.calls) == [MONDAY.isoformat(), TUESDAY.isoformat()]


@pytest.mark.asyncio
async def test_a_sheet_about_to_say_none_says_why_it_asks(monkeypatch, caplog):
    svc_obj = _sheet_on(monkeypatch, MONDAY)
    svc_obj.fmp = _FMP(_market(MONDAY))
    with caplog.at_level(logging.INFO, logger=ts.logger.name):
        await svc_obj.get_earnings_live("INTC", MONDAY)
    (line,) = [r.getMessage() for r in caplog.records if "nothing listed" in r.getMessage()]
    assert "INTC" in line and MONDAY.isoformat() in line


@pytest.mark.asyncio
async def test_a_failed_ask_for_the_later_days_answers_none_and_the_next_read_finds_the_report(
        monkeypatch):
    """The sheet still answers, and the memo's wait after a failed round is what the next read
    has to outlast. An outage is the one way left to read `none` beside a listed report."""
    clock = _Clock()
    monkeypatch.setattr(ts, "_time", SimpleNamespace(monotonic=clock.monotonic))
    new = MONDAY + timedelta(days=4)
    svc_obj = _sheet_on(monkeypatch, MONDAY)
    svc_obj.fmp = _FMP({**_market(MONDAY), TUESDAY: _intc_on(TUESDAY)})
    await svc_obj.get_earnings_live("INTC", TUESDAY)
    clock.at += ts.EARNINGS_CALENDAR_TTL
    _fmp_moves_intc(svc_obj.fmp, TUESDAY, new)
    await ts._cached_earnings_calendar(svc_obj.fmp, MONDAY)
    state = _fmp_can_go_down(svc_obj.fmp)
    state["down"] = True
    sheet = await svc_obj.get_earnings_live("INTC", TUESDAY)
    assert (sheet.status, sheet.report_date) == (els.STATUS_NONE, TUESDAY.isoformat())
    state["down"] = False
    sheet = await svc_obj.get_earnings_live("INTC", TUESDAY)
    assert sheet.status == els.STATUS_NONE, "inside the wait, no round starts"
    clock.at += ts._EARNINGS_CALENDAR_FAILURE_RETRY_SECONDS
    sheet = await svc_obj.get_earnings_live("INTC", TUESDAY)
    assert (sheet.status, sheet.report_date) == (els.STATUS_UPCOMING, new.isoformat())


@pytest.mark.asyncio
async def test_with_live_off_a_sheet_about_to_say_none_still_asks_the_memo_first(monkeypatch):
    """The ask is the memo's, not the live state's, so the switch leaves it: a report on a day
    the memo held from before is still found."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", False)
    new = MONDAY + timedelta(days=5)
    svc_obj = _sheet_on(monkeypatch, MONDAY)
    svc_obj.fmp = _FMP(_market(MONDAY))
    clock = _Clock()
    monkeypatch.setattr(ts, "_time", SimpleNamespace(monotonic=clock.monotonic))
    await ts._cached_earnings_calendar(svc_obj.fmp, MONDAY)
    clock.at += ts.EARNINGS_CALENDAR_TTL
    svc_obj.fmp.by_day[new] = _intc_on(new)
    sheet = await svc_obj.get_earnings_live("INTC")
    assert (sheet.status, sheet.report_date, sheet.poll_seconds) == (
        els.STATUS_UPCOMING, new.isoformat(), None)
    assert _svc()._records == {}


# ── the days the sheet waits on ──────────────────────────────────────────────


@pytest.mark.parametrize("today", [TUESDAY, MONDAY, SUNDAY, SATURDAY, date(2026, 11, 27)],
                         ids=["trading_day", "after_a_weekend", "sunday", "saturday",
                              "after_a_holiday"])
def test_every_day_the_sheet_waits_on_is_in_the_loops_next_round(today):
    """A wait the loop would not end is a poll with no end. Thanksgiving's Friday is the holiday
    case: its Thursday is a closed day inside the window."""
    svc = _svc()
    asked = [today + timedelta(days=offset) for offset in range(-8, 3)]
    assert [d for d in asked if svc.awaits_first_load(d, today)] == svc.due_days(_et(today, 9), [])


@pytest.mark.parametrize("today", [TUESDAY, MONDAY, SUNDAY, SATURDAY, date(2026, 11, 27)],
                         ids=["trading_day", "after_a_weekend", "sunday", "saturday",
                              "after_a_holiday"])
def test_a_day_a_sheet_asked_for_is_in_the_loops_next_round_too(today):
    svc = _svc()
    asked = [today + timedelta(days=offset) for offset in range(-8, 3)]
    for day in asked:
        svc.sheet_asked(day, today)
    assert svc._sheet_asked, "the sheet days reach past the live days on every one of these"
    assert [d for d in asked if svc.awaits_first_load(d, today)] == svc.due_days(_et(today, 9), [])


@pytest.mark.asyncio
@pytest.mark.parametrize("today", [TUESDAY, MONDAY, SUNDAY, SATURDAY, date(2026, 11, 27)],
                         ids=["trading_day", "after_a_weekend", "sunday", "saturday",
                              "after_a_holiday"])
async def test_the_real_tick_loads_every_day_a_sheet_waits_on_with_nothing_watched(today):
    """Only an empty database reaches this, and a poll with no end is what it would cost."""
    fmp = _FMP(_market(today - timedelta(days=10)))
    svc = _svc()
    asked = [today + timedelta(days=offset) for offset in range(-8, 3)]
    waited_on = [d for d in asked if svc.awaits_first_load(d, today)]
    assert waited_on

    async def _dispatch(snaps, now):
        return None

    stats = await els.earnings_live_tick(_et(today, 9), fmp=fmp, watched=[], dispatch=_dispatch)
    assert stats["fetched"] == len(waited_on)
    assert not any(svc.awaits_first_load(d, today) for d in waited_on)


@pytest.mark.asyncio
async def test_a_load_with_rows_ends_the_wait_and_a_closed_day_needs_none():
    svc = _svc()
    await svc.refresh(_et(MONDAY, 9), fmp=_FMP({FRIDAY: [_row("INTC", FRIDAY)]}), symbols=["INTC"])
    assert not any(svc.awaits_first_load(d, MONDAY) for d in (FRIDAY, SATURDAY, SUNDAY))


@pytest.mark.asyncio
async def test_a_blank_answer_settles_a_closed_day_and_leaves_a_trading_day_waiting():
    """A weekend has no reports, so its blank is an answer. A trading day's is the blip
    `earnings_window_service._hot_round` refuses too."""
    svc = _svc()
    await svc.refresh(_et(MONDAY, 9), fmp=_FMP(), symbols=["INTC"])
    assert [d for d in (FRIDAY, SATURDAY, SUNDAY) if svc.awaits_first_load(d, MONDAY)] == [FRIDAY]
    assert svc.fetched_at(FRIDAY) is None and svc.fetched_at(SATURDAY) is not None


@pytest.mark.asyncio
async def test_a_blank_first_load_is_asked_again_and_says_so(monkeypatch, caplog):
    clock = _Clock()
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    fmp = _FMP()
    svc = _svc()
    with caplog.at_level(logging.WARNING, logger=els.logger.name):
        await svc.refresh(_et(TUESDAY, 9), fmp=fmp, symbols=["INTC"])
    (line,) = [r.getMessage() for r in caplog.records if "blank" in r.getMessage()]
    assert MONDAY.isoformat() in line
    clock.at += 60
    fmp.by_day[MONDAY] = [_row("INTC", MONDAY, eps=0.44, rev=17.1e9)]
    await svc.refresh(_et(TUESDAY, 9, 1), fmp=fmp, symbols=["INTC"])
    assert fmp.calls == [MONDAY.isoformat()] * 2
    assert not svc.awaits_first_load(MONDAY, TUESDAY)
    assert svc.snapshot("INTC", MONDAY, _et(TUESDAY, 9, 2)).eps.actual == 0.44


@pytest.mark.asyncio
async def test_a_blank_first_load_never_holds_up_todays_hot_fetch():
    svc = _svc()
    fmp = _FMP({TUESDAY: [_row("INTC", TUESDAY)]})
    svc.observe_rows(TUESDAY, [_row("INTC", TUESDAY)], _et(TUESDAY, 9))
    await svc.refresh(_et(TUESDAY, 9), fmp=fmp, symbols=["INTC"])
    assert sorted(fmp.calls) == [MONDAY.isoformat(), TUESDAY.isoformat()]
    assert svc.fetched_at(TUESDAY) is not None
    svc._fetched[TUESDAY] -= els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1
    fmp.calls.clear()
    await svc.refresh(_et(TUESDAY, 9, 6), fmp=fmp, symbols=["INTC"])
    assert TUESDAY.isoformat() in fmp.calls


@pytest.mark.asyncio
async def test_a_trading_day_fmp_keeps_blank_settles_and_costs_the_slow_cadence(monkeypatch):
    """A day FMP really leaves empty must end, because a sheet polls until it does, and every
    tick is no cadence to keep up until the day leaves the window."""
    clock = _Clock()
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    fmp = _FMP()
    svc = _svc()
    start = _et(TUESDAY, 9)
    for minute in range(180):
        await svc.refresh(start + timedelta(minutes=minute), fmp=fmp, symbols=["INTC"])
        clock.at += 60
    assert not svc.awaits_first_load(MONDAY, TUESDAY)
    assert svc.fetched_at(MONDAY) is not None
    every_tick = els.EARNINGS_LIVE_BLANK_LOAD_WAIT_SECONDS // 60 + 1
    slow_cadence = 170 // (els.EARNINGS_LIVE_COLD_TTL_SECONDS // 60)
    assert len(fmp.calls) == every_tick + slow_cadence


@pytest.mark.asyncio
@pytest.mark.parametrize("today,minutes", [(TUESDAY, 30), (SATURDAY, 120)],
                         ids=["trading_day", "closed_day"])
async def test_a_settled_blank_day_keeps_being_asked_slowly_until_it_lists_something(
        monkeypatch, today, minutes):
    """A blip longer than the wait must not lose the day: with no record nothing else would ask
    again, so its reports would get no card and no push. Once it lists rows the asks stop."""
    clock = _Clock()
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    day = previous_trading_day(today)
    fmp = _FMP()
    svc = _svc()

    async def _ticks(start: datetime, count: int) -> None:
        for minute in range(count):
            await svc.refresh(start + timedelta(minutes=minute), fmp=fmp, symbols=["INTC"])
            clock.at += 60

    await _ticks(_et(today, 9), 11)
    assert not svc.awaits_first_load(day, today)
    fmp.calls.clear()
    await _ticks(_et(today, 9, 11), minutes)
    assert fmp.calls == [day.isoformat()], "once, on the slow cadence"
    fmp.by_day[day] = [_row("INTC", day, eps=0.44, rev=17.1e9)]
    await _ticks(_et(today, 11, 30), minutes)
    (card,) = svc.recent_snapshots(["INTC"], _et(today, 11, 30))
    assert (card.day, card.eps.actual) == (day, 0.44)
    fmp.calls.clear()
    await _ticks(_et(today, 15), 3 * minutes)
    assert fmp.calls == [], "the day is read, and INTC's report has what it wanted from FMP"


@pytest.mark.asyncio
async def test_a_blank_answer_on_a_day_already_read_keeps_its_cadence():
    """Only a first look can go unread: a read day has its record, and that keeps it asked."""
    svc = _svc()
    fmp = _FMP({MONDAY: [_row("INTC", MONDAY)]})
    await svc.refresh(_et(TUESDAY, 9), fmp=fmp, symbols=["INTC"])
    first = svc.fetched_at(MONDAY)
    fmp.by_day[MONDAY] = []
    fmp.calls.clear()
    svc._fetched[MONDAY] -= els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1
    await svc.refresh(_et(TUESDAY, 9, 5), fmp=fmp, symbols=["INTC"])
    assert fmp.calls == [MONDAY.isoformat()]
    assert svc.fetched_at(MONDAY) >= first and svc._blank_since == {}
    assert svc.due_days(_et(TUESDAY, 9, 6), ["INTC"]) == [], "stamped, so the cadence stands"
    assert svc.snapshot("INTC", MONDAY, _et(TUESDAY, 9, 6)) is not None, "and nothing ended"


@pytest.mark.asyncio
async def test_a_blank_own_fetch_of_today_is_stamped_like_any_other():
    """Today's first look is the memo's: a blank here must not make the hot fetch run hot."""
    svc = _svc()
    svc.observe_rows(TUESDAY, [_row("INTC", TUESDAY)], _et(TUESDAY, 9))
    await svc.refresh(_et(TUESDAY, 9), fmp=_FMP({MONDAY: [_row("ZZZZ", MONDAY)]}),
                      symbols=["INTC"])
    assert svc.fetched_at(TUESDAY) is not None and svc._blank_since == {}
    assert TUESDAY not in svc.due_days(_et(TUESDAY, 9, 1), ["INTC"])


@pytest.mark.asyncio
async def test_the_wait_on_a_blank_day_goes_when_the_day_does(monkeypatch):
    svc = _svc()
    await svc.refresh(_et(TUESDAY, 9), fmp=_FMP(), symbols=["INTC"])
    assert MONDAY in svc._blank_since
    await svc.refresh(_et(TUESDAY + timedelta(days=2), 9), fmp=_FMP(), symbols=["INTC"])
    assert MONDAY not in svc._blank_since


@pytest.mark.asyncio
async def test_an_outage_flagged_answer_is_a_failure_and_starts_no_blank_wait():
    class _Flagged(_FMP):
        async def get_earnings_calendar(self, from_date=None, to_date=None):
            self.calls.append(from_date)
            return EmptyAfterFailure("FMP 503")

    svc = _svc()
    await svc.refresh(_et(TUESDAY, 9), fmp=_Flagged(), symbols=["INTC"])
    assert svc._blank_since == {} and svc.awaits_first_load(MONDAY, TUESDAY)
    assert svc._failed_until > 0


@pytest.mark.asyncio
async def test_a_failed_load_keeps_the_wait():
    fmp = _FMP()
    _fmp_can_go_down(fmp)["down"] = True
    svc = _svc()
    await svc.refresh(_et(TUESDAY, 9), fmp=fmp, symbols=["INTC"])
    assert svc.awaits_first_load(MONDAY, TUESDAY)


@pytest.mark.parametrize("day", [FRIDAY, TUESDAY, TUESDAY + timedelta(days=1)],
                         ids=["older_than_the_window", "today", "later"])
def test_a_day_the_loop_does_not_load_first_is_never_waited_on(day):
    """Today and later are the memo's to read. An older day is never loaded at all."""
    assert not _svc().awaits_first_load(day, TUESDAY)


@pytest.mark.asyncio
async def test_a_deploy_minute_sheet_waits_then_answers_what_the_first_tick_loads(monkeypatch):
    """The real tick and the real memo, as a push tapped a minute after a deploy meets them."""
    svc_obj = _sheet_on(monkeypatch, TUESDAY)
    svc_obj.fmp = _FMP({MONDAY: [_row("INTC", MONDAY, eps=0.44, rev=17.1e9)],
                        TUESDAY: [_row("AAPL", TUESDAY)]})
    sheet = await svc_obj.get_earnings_live("INTC", MONDAY)
    assert (sheet.status, sheet.report_date, sheet.poll_seconds) == (
        els.STATUS_AWAITING, MONDAY.isoformat(), els.EARNINGS_LIVE_POLL_SECONDS)

    async def _nobody(snapshots, at):
        return None

    await els.earnings_live_tick(_et(TUESDAY, 12), fmp=svc_obj.fmp, watched=["INTC"],
                                 dispatch=_nobody)
    sheet = await svc_obj.get_earnings_live("INTC", MONDAY)
    assert (sheet.status, sheet.poll_seconds, sheet.eps.actual) == (
        els.STATUS_REPORTED, None, 0.44)


@pytest.mark.asyncio
async def test_a_deploy_minute_blank_load_waits_then_answers_what_the_next_round_loads(monkeypatch):
    """The real tick and the real memo. The day stays asked with no record, the card comes back
    with it, and a report that landed meanwhile is read as it was."""
    clock = _Clock()
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(ts, "_time", SimpleNamespace(monotonic=clock.monotonic))
    svc_obj = _sheet_on(monkeypatch, TUESDAY)
    svc_obj.fmp = _FMP(_market(TUESDAY))
    await _tick(svc_obj.fmp, _et(TUESDAY, 8), "AAPL")
    sheet = await svc_obj.get_earnings_live("INTC", MONDAY)
    assert (sheet.status, sheet.poll_seconds) == (els.STATUS_AWAITING, 30)
    svc_obj.fmp.by_day[MONDAY] = [_row("INTC", MONDAY, eps=0.44, rev=17.1e9)]
    clock.at += 60
    await _tick(svc_obj.fmp, _et(TUESDAY, 8, 1), "AAPL")
    sheet = await svc_obj.get_earnings_live("INTC", MONDAY)
    assert (sheet.status, sheet.poll_seconds, sheet.eps.actual) == (
        els.STATUS_REPORTED, None, 0.44)
    assert [s.symbol for s in _svc().recent_snapshots(["INTC"], _et(TUESDAY, 8, 2))] == ["INTC"]


@pytest.mark.asyncio
async def test_a_sheet_holding_a_record_never_pays_for_the_later_days(monkeypatch):
    """The loop answers that day every 5 min, each answer newer than the later days. Those days
    are worth asking for only before a `none`, and a record is no `none`: on the hour this runs
    a sheet asking anyway would spend 14 calls an answer."""
    clock = _Clock()
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(ts, "_time", SimpleNamespace(monotonic=clock.monotonic))
    svc_obj = _sheet_on(monkeypatch, TUESDAY)
    fmp = _FMP({**_market(TUESDAY), MONDAY: [_row("INTC", MONDAY, eps=0.44)]})
    svc_obj.fmp = fmp
    fetched = 0
    start = _et(TUESDAY, 9)
    for minute in range(60):
        stats = await _tick(fmp, start + timedelta(minutes=minute), "INTC")
        fetched += stats["fetched"]
        fmp.calls.clear()
        sheet = await svc_obj.get_earnings_live("INTC", MONDAY)
        assert (sheet.report_date, sheet.eps.actual) == (MONDAY.isoformat(), 0.44)
        assert fmp.calls == [], f"minute {minute}"
        clock.at += 60
    assert fetched >= 12, "the loop was answering that day every 5 min all along"


@pytest.mark.asyncio
async def test_an_unknown_answer_pays_for_no_ask_it_could_not_use(monkeypatch):
    """The later days are asked for before a `none`. A blank today answers no `none`, and its
    pinned sheet stays where it is whatever those days list."""
    clock = _Clock()
    monkeypatch.setattr(ts, "_time", SimpleNamespace(monotonic=clock.monotonic))
    svc_obj = _sheet_on(monkeypatch, MONDAY)
    fmp = _FMP(_market(MONDAY))
    svc_obj.fmp = fmp
    await ts._cached_earnings_calendar(fmp, MONDAY)
    clock.at += ts.EARNINGS_CALENDAR_TTL
    fmp.by_day[MONDAY] = []
    fmp.calls.clear()
    sheet = await svc_obj.get_earnings_live("INTC", MONDAY)
    assert (sheet.status, sheet.poll_seconds) == (els.STATUS_UPCOMING, 30)
    assert sorted(fmp.calls) == [MONDAY.isoformat(), TUESDAY.isoformat()], "the near round alone"


@pytest.mark.asyncio
async def test_a_poll_on_an_unknown_answer_pays_for_no_round_inside_the_memos_wait(monkeypatch):
    """It asks every 30 s for as long as the outage lasts, so it may only cost a memo read. The
    first read pays for the round the memo was due anyway."""
    clock = _Clock()
    monkeypatch.setattr(ts, "_time", SimpleNamespace(monotonic=clock.monotonic))
    svc_obj = _sheet_on(monkeypatch, MONDAY)
    fmp = _FMP(_market(MONDAY))
    svc_obj.fmp = fmp
    _fmp_can_go_down(fmp)["down"] = True
    sheet = await svc_obj.get_earnings_live("ZZZY", MONDAY)
    assert (sheet.status, sheet.poll_seconds) == (els.STATUS_UPCOMING, 30)
    assert len(fmp.calls) == 15
    fmp.calls.clear()
    for _ in range(3):
        clock.at += 30
        sheet = await svc_obj.get_earnings_live("ZZZY", MONDAY)
        assert (sheet.status, sheet.poll_seconds) == (els.STATUS_UPCOMING, 30)
    assert fmp.calls == []


@pytest.mark.asyncio
async def test_an_unknown_answer_turns_into_the_live_report_once_the_memo_reads_its_day(
        monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(ts, "_time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    svc_obj = _sheet_on(monkeypatch, MONDAY)
    fmp = _FMP({**_market(MONDAY), MONDAY: _intc_on(MONDAY)})
    svc_obj.fmp = fmp
    state = _fmp_can_go_down(fmp)
    state["down"] = True
    sheet = await svc_obj.get_earnings_live("INTC", MONDAY)
    assert (sheet.status, sheet.poll_seconds) == (els.STATUS_UPCOMING, 30)
    state["down"] = False
    clock.at += ts._EARNINGS_CALENDAR_FAILURE_RETRY_SECONDS
    sheet = await svc_obj.get_earnings_live("INTC", MONDAY)
    assert (sheet.status, sheet.poll_seconds) == (els.STATUS_LIVE, 30)


@pytest.mark.asyncio
async def test_an_answer_that_asks_again_says_so_with_its_symbol_and_day(monkeypatch, caplog):
    svc_obj = _sheet_on(monkeypatch, TUESDAY)
    svc_obj.fmp = _FMP(_market(TUESDAY))
    with caplog.at_level(logging.INFO, logger=ts.logger.name):
        await svc_obj.get_earnings_live("INTC", MONDAY)
    (line,) = [r.getMessage() for r in caplog.records if "asks again" in r.getMessage()]
    assert "INTC" in line and MONDAY.isoformat() in line


# ── the push: watched reports, inside the result window ──────────────────────

_MARKET = [f"A{i:03d}" for i in range(200)] + ["XOM", "ZM"]


async def _a_market_landing(monkeypatch, watched: List[str], dispatch=None,
                            market: List[str] = _MARKET) -> tuple:
    """`market` pending at 09:00 and filled with a beat by 16:30, through real ticks that know
    only `watched`. Returns the FMP fake and the last tick's stats."""
    pending = [_row(symbol, REPORT) for symbol in market]
    fmp = _FMP({REPORT: pending})

    async def _memo(_fmp, today, *, later_days_since=None):
        return pending if today == REPORT else []

    monkeypatch.setattr(ts, "_cached_earnings_calendar", _memo)
    monkeypatch.setattr(ts, "_et_today", lambda: REPORT)
    await els.earnings_live_tick(_et(REPORT, 9), fmp=fmp, watched=watched, dispatch=dispatch)
    fmp.by_day[REPORT] = [_row(symbol, REPORT, eps=0.60, rev=17.1e9) for symbol in market]
    _svc()._fetched[REPORT] -= els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1
    stats = await els.earnings_live_tick(_et(REPORT, 16, 30), fmp=fmp, watched=watched,
                                         dispatch=dispatch)
    return fmp, stats


@pytest.mark.asyncio
@pytest.mark.parametrize("watched", [["ZM"], ["zm"]], ids=["upper", "lower"])
async def test_the_tick_hands_the_sender_only_watched_landings(monkeypatch, watched):
    seen: List[List[str]] = []

    async def _dispatch(snaps, now):
        seen.append([s.symbol for s in snaps])

    _, stats = await _a_market_landing(monkeypatch, watched, _dispatch)
    assert seen[-1] == ["ZM"]
    assert stats["landed"] == 1
    assert _svc().snapshot("A000", REPORT, _et(REPORT, 16, 31)) is not None, \
        "the fold stays whole-market: cards and the sheet read these records"


@pytest.mark.asyncio
async def test_a_watched_report_is_not_queued_behind_the_market_in_one_round(monkeypatch, pushes):
    await _a_market_landing(monkeypatch, ["ZM"])
    assert pushes.sent == _sent_once(REPORT, "ZM")


@pytest.mark.asyncio
async def test_a_ticker_past_the_watched_clamp_keeps_its_card_but_gets_no_live_push(
        monkeypatch, pushes):
    await _a_market_landing(monkeypatch, ["ZM"], market=["XOM", "ZM"])
    assert pushes.sent == _sent_once(REPORT, "ZM")
    (card,) = _svc().recent_snapshots(["XOM"], _et(NEXT, 8))
    assert (card.symbol, card.day, card.status) == ("XOM", REPORT, els.STATUS_REPORTED)


@pytest.mark.asyncio
async def test_a_ticker_first_watched_after_it_landed_is_told_inside_the_window(monkeypatch, pushes):
    """An unwatched landing is not used up: a ticker added later is told while its report is
    inside the window, the reach the daily pass has too."""
    fmp, _ = await _a_market_landing(monkeypatch, ["ZM"], market=["XOM", "ZM"])
    await els.earnings_live_tick(_et(REPORT, 16, 40), fmp=fmp, watched=["XOM", "ZM"])
    assert pushes.sent == _sent_once(REPORT, "ZM") + _sent_once(REPORT, "XOM")


async def _a_late_fill(monkeypatch, report: date, filled_on: date) -> tuple:
    """INTC pending on `report`, and FMP's fill first seen by the real tick on `filled_on`."""
    pending = [_row("INTC", report)]
    fmp = _FMP({report: pending})

    async def _memo(_fmp, today, *, later_days_since=None):
        return pending if today == report else []

    monkeypatch.setattr(ts, "_cached_earnings_calendar", _memo)
    await _real_tick(monkeypatch, fmp, _et(report, 9))
    fmp.by_day[report] = [_row("INTC", report, eps=0.60, rev=17.1e9)]
    _svc()._fetched[report] -= els.EARNINGS_LIVE_CLOSED_DAY_TTL_SECONDS + 1
    now = _et(filled_on, 10)
    await _real_tick(monkeypatch, fmp, now)
    return fmp, now


@pytest.mark.asyncio
@pytest.mark.parametrize("report,filled_on,told", [
    (FRIDAY, SATURDAY, True),
    (FRIDAY, SUNDAY, False),
    (FRIDAY, MONDAY, False),
    (SATURDAY, SUNDAY, True),
    (SATURDAY, MONDAY, False),
    (date(2026, 11, 26), date(2026, 11, 27), True),     # dated Thanksgiving, filled Friday
    (date(2026, 11, 25), date(2026, 11, 27), False),    # the previous trading day, two days back
], ids=["fri_sat", "fri_sun", "fri_mon", "sat_sun", "sat_mon", "holiday_row", "before_holiday"])
async def test_a_late_fill_pushes_only_inside_the_result_window_and_always_keeps_its_card(
        monkeypatch, pushes, report, filled_on, told):
    fmp, now = await _a_late_fill(monkeypatch, report, filled_on)
    assert pushes.sent == (_sent_once(report) if told else [])
    (card,) = _svc().recent_snapshots(["INTC"], now)
    assert (card.day, card.status, card.eps.actual) == (report, els.STATUS_REPORTED, 0.60)
    sheet = await _sheet_on(monkeypatch, filled_on).get_earnings_live("INTC", report)
    assert (sheet.report_date, sheet.status, sheet.eps.actual) == (
        report.isoformat(), els.STATUS_REPORTED, 0.60)
    # Handled either way, so the next rounds have nothing left to decide.
    assert ("INTC", report) in sender._live_done and ("INTC", report) in sender._surprise_done
    await _real_tick(monkeypatch, fmp, now + timedelta(minutes=1))
    assert pushes.sent == (_sent_once(report) if told else [])


@pytest.mark.asyncio
async def test_fmps_next_morning_fill_confirms_a_filed_report_and_sends_the_result_push(
        monkeypatch, pushes):
    """The real tick, sender and sheet: the filing lands and is announced, the sheet is still
    polling the next morning, FMP's fill confirms it, and the result push goes out instead of
    waiting for the daily pass."""
    pending = [_row("INTC", PREV)]
    fmp = _FMP({PREV: pending})

    async def _memo(_fmp, today, *, later_days_since=None):
        return pending if today == PREV else []

    monkeypatch.setattr(ts, "_cached_earnings_calendar", _memo)
    await _real_tick(monkeypatch, fmp, _et(PREV, 9))
    _svc().absorb_filing("INTC", PREV, _FILED_ADJUSTED, _et(PREV, 16, 3))
    await _real_tick(monkeypatch, fmp, _et(PREV, 16, 4))
    key = f"earnings_result:INTC:{PREV.isoformat()}"
    assert pushes.sent == [(KIND_EARNINGS_LIVE, key)], "a filing figure never colours a push"
    sheet_service = _sheet_on(monkeypatch, REPORT)
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_et(REPORT, 8)))
    sheet = await sheet_service.get_earnings_live("INTC", PREV)
    assert (sheet.status, sheet.preliminary, sheet.poll_seconds) == (
        els.STATUS_REPORTED, True, 120)
    fmp.by_day[PREV] = [_row("INTC", PREV, eps=0.60, rev=17.2e9)]
    _svc()._fetched[PREV] -= els.EARNINGS_LIVE_HOT_TTL_SECONDS + 1
    await _real_tick(monkeypatch, fmp, _et(REPORT, 8))
    assert pushes.sent == _sent_once(PREV)
    sheet = await sheet_service.get_earnings_live("INTC", PREV)
    assert (sheet.status, sheet.preliminary, sheet.poll_seconds, sheet.eps.source) == (
        els.STATUS_REPORTED, False, None, els.SOURCE_FMP)


# ── reading the watched list ─────────────────────────────────────────────────

_SENDER_READ = "app.services.notification_senders.earnings_sender._watched_tickers_ranked"


def _boom():
    raise RuntimeError("rpc 520")


def _live_logs(caplog) -> List[logging.LogRecord]:
    return [r for r in caplog.records if r.name == els.__name__]


@pytest.mark.asyncio
async def test_a_failed_watched_read_serves_the_old_list_and_says_so(monkeypatch, caplog):
    # The callee imports inside the function, so patch the SOURCE module.
    monkeypatch.setattr(_SENDER_READ, _boom)
    memo = (time.monotonic() - 3600, ["AAPL"])
    monkeypatch.setattr(els, "_watched_memo", memo)  # `_reset_all` does not clear it
    with caplog.at_level(logging.WARNING, logger=els.__name__):
        assert await els._watched_ranked_cached() == ["AAPL"]
    rows = _live_logs(caplog)
    assert len(rows) == 1 and rows[0].levelno == logging.WARNING
    assert "RuntimeError" in rows[0].getMessage() and "rpc 520" in rows[0].getMessage()
    assert els._watched_memo is memo, "a failure must not refresh the list's age"


@pytest.mark.asyncio
async def test_a_failed_watched_read_with_no_memo_still_raises(monkeypatch, caplog):
    monkeypatch.setattr(_SENDER_READ, _boom)
    monkeypatch.setattr(els, "_watched_memo", None)
    with caplog.at_level(logging.WARNING, logger=els.__name__):
        with pytest.raises(RuntimeError):
            await els._watched_ranked_cached()
    assert not _live_logs(caplog), "the loop logs this one"


@pytest.mark.asyncio
async def test_a_fresh_watched_memo_is_served_without_a_read(monkeypatch, caplog):
    monkeypatch.setattr(_SENDER_READ, _boom)
    monkeypatch.setattr(els, "_watched_memo", (time.monotonic(), ["AAPL"]))
    with caplog.at_level(logging.WARNING, logger=els.__name__):
        assert await els._watched_ranked_cached() == ["AAPL"]
    assert not _live_logs(caplog)


@pytest.mark.asyncio
async def test_a_watched_read_that_recovers_replaces_the_list_quietly(monkeypatch, caplog):
    monkeypatch.setattr(_SENDER_READ, lambda: ["MSFT"])
    monkeypatch.setattr(els, "_watched_memo", (time.monotonic() - 3600, ["AAPL"]))
    with caplog.at_level(logging.WARNING, logger=els.__name__):
        assert await els._watched_ranked_cached() == ["MSFT"]
    assert els._watched_memo[1] == ["MSFT"]
    assert not _live_logs(caplog)


# ── the SEC watch beside the FMP tick ────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_stuck_filing_round_cannot_hold_the_fmp_tick_or_the_dispatch(monkeypatch):
    class _Stuck(_Watcher):
        async def run_once(self, *a, **k):
            await asyncio.Event().wait()

    monkeypatch.setattr(ts, "_et_today", lambda: REPORT)
    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: _Stuck())
    fmp = _FMP({REPORT: [_row("INTC", REPORT)], PREV: []})
    sent: List[datetime] = []

    async def _dispatch(snaps, now):
        sent.append(now)

    await asyncio.wait_for(
        els.earnings_live_tick(_et(REPORT, 16, 3), fmp=fmp, watched=["INTC"], dispatch=_dispatch),
        1.0)
    assert sent


@pytest.mark.asyncio
async def test_a_figure_is_pushed_while_the_round_that_read_it_still_runs(monkeypatch):
    """The record alone would still leave the push waiting for the round to end."""
    monkeypatch.setattr(ts, "_et_today", lambda: REPORT)
    at = _et(REPORT, 16, 3)
    figures = _morning_filing(accepted_at=_et(REPORT, 16, 2))
    delivered, release = asyncio.Event(), asyncio.Event()

    class _StillReading(_Watcher):
        async def run_once(self, now, symbols, day, *, sec=None, gemini=None, on_figures=None):
            on_figures("INTC", figures)
            delivered.set()
            await release.wait()
            return {"INTC": figures}

    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: _StillReading())
    fmp = _FMP({REPORT: [_row("INTC", REPORT)], PREV: []})
    sent: List[List[str]] = []

    async def _dispatch(snaps, now):
        sent.append([s.symbol for s in snaps])

    await asyncio.wait_for(
        els.earnings_live_tick(at, fmp=fmp, watched=["INTC"], dispatch=_dispatch), 1.0)
    watching = asyncio.create_task(els.earnings_filing_tick(at, watched=["INTC"]))
    await asyncio.wait_for(delivered.wait(), 1.0)
    await asyncio.wait_for(
        els.earnings_live_tick(at + timedelta(minutes=1), fmp=fmp, watched=["INTC"],
                               dispatch=_dispatch),
        1.0)
    assert sent == [[], ["INTC"]] and not watching.done()
    release.set()
    assert (await watching)["filings"] == 1


@pytest.mark.asyncio
async def test_a_filing_landing_inside_the_fmp_fetch_survives_the_fold_and_is_pushed(monkeypatch):
    """The two loops share the records, so the filing tick can run inside the FMP tick's await
    and the fold that follows must keep the figure and its landing."""
    monkeypatch.setattr(ts, "_et_today", lambda: REPORT)
    at = _et(REPORT, 16, 3)
    watcher = _Watcher({"INTC": _morning_filing(accepted_at=_et(REPORT, 16, 2))})
    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: watcher)

    class _FilingArrivesDuringTheFetch(_FMP):
        async def get_earnings_calendar(self, from_date=None, to_date=None):
            rows = await super().get_earnings_calendar(from_date=from_date, to_date=to_date)
            if from_date == REPORT.isoformat() and not watcher.calls:
                await els.earnings_filing_tick(at, watched=["INTC"])
            return rows

    fmp = _FilingArrivesDuringTheFetch({REPORT: [_row("INTC", REPORT)], PREV: []})
    sent: List[List[str]] = []

    async def _dispatch(snaps, now):
        sent.append([s.symbol for s in snaps])

    stats = await els.earnings_live_tick(at, fmp=fmp, watched=["INTC"], dispatch=_dispatch)
    assert watcher.calls == [(["INTC"], REPORT)], "the own fetch is what it ran inside"
    assert stats["fetched"] >= 1
    assert sent == [["INTC"]], "the pending row of that fetch must not undo the landing"
    assert _svc().snapshot("INTC", REPORT, at).eps.actual == 0.45


@pytest.mark.asyncio
async def test_a_filing_landing_is_pushed_once_by_the_next_fmp_tick(monkeypatch, pushes):
    """Through the real sender: the filing tick stores the figure and tells nobody."""
    fmp = _FMP({REPORT: [_row("INTC", REPORT)], PREV: []})
    monkeypatch.setattr(efs, "get_earnings_filing_watcher",
                        lambda: _Watcher({"INTC": _morning_filing(accepted_at=_et(REPORT, 16, 2))}))
    await _real_tick(monkeypatch, fmp, _et(REPORT, 16, 3))
    await els.earnings_filing_tick(_et(REPORT, 16, 3), watched=["INTC"])
    assert pushes.sent == []
    await _real_tick(monkeypatch, fmp, _et(REPORT, 16, 4))
    await _real_tick(monkeypatch, fmp, _et(REPORT, 16, 5))
    assert pushes.sent == [(KIND_EARNINGS_LIVE, "earnings_result:INTC:2026-10-29")]


@pytest.mark.asyncio
async def test_a_figure_is_stamped_with_the_moment_it_arrived_not_the_rounds_start(monkeypatch):
    """A figure read 90 s into a round says so, on the sheet's "updated" and on the landing."""
    clock = _Clock()
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))

    class _SlowRound(_Watcher):
        async def run_once(self, now, symbols, day, *, sec=None, gemini=None, on_figures=None):
            clock.at += 90
            return await super().run_once(now, symbols, day, on_figures=on_figures)

    figures = _morning_filing(accepted_at=_et(REPORT, 16, 2))
    monkeypatch.setattr(efs, "get_earnings_filing_watcher",
                        lambda: _SlowRound({"INTC": figures}))
    at = _et(REPORT, 16, 3)
    _svc().observe_rows(REPORT, [_row("INTC", REPORT)], at)
    await els.earnings_filing_tick(at, watched=["INTC"])
    rec = _svc()._records[("INTC", REPORT)]
    assert (rec.checked_at, rec.landed_at) == (at + timedelta(seconds=90),) * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("hour,reads", [(5, False), (6, True)], ids=["before_six", "at_six"])
async def test_the_sec_watch_starts_with_the_live_window(monkeypatch, hour, reads):
    watcher = _Watcher()
    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: watcher)
    _svc().observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 3))
    await els.earnings_filing_tick(_et(REPORT, hour), watched=["INTC"])
    assert watcher.calls == ([(["INTC"], REPORT)] if reads else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("watched", [[], ["INTC"]], ids=["nobody_watching", "nothing_pending"])
async def test_a_filing_tick_with_nothing_pending_never_wakes_the_watcher(monkeypatch, watched):
    watcher = _Watcher()
    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: watcher)
    stats = await els.earnings_filing_tick(_et(REPORT, 16, 3), watched=watched)
    assert stats == {"watched": len(watched), "pending": 0, "filings": 0}
    assert watcher.calls == [] and watcher.kept is None


@pytest.mark.asyncio
async def test_the_filing_tick_reads_the_watched_memo_the_fmp_tick_keeps(monkeypatch):
    monkeypatch.setattr(_SENDER_READ, _boom)
    monkeypatch.setattr(els, "_watched_memo", (time.monotonic(), ["INTC"]))
    stats = await els.earnings_filing_tick(_et(REPORT, 16, 3))
    assert stats["watched"] == 1


# ── the two loops ────────────────────────────────────────────────────────────

_TICKS = ["live", "filing"]


async def _pass_time(_seconds: float) -> None:
    await asyncio.sleep(0)


async def _run_loops(monkeypatch, *, live, filing, until: asyncio.Event, sleep=_pass_time,
                     enabled: bool = True) -> None:
    """The real loop over these two ticks until `until`, then the shutdown's cancel."""
    monkeypatch.setattr(els, "earnings_live_tick", live)
    monkeypatch.setattr(els, "earnings_filing_tick", filing)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", enabled)
    spawned = asyncio.create_task(els.run_earnings_live_loop(sleep=sleep))
    try:
        await asyncio.wait_for(until.wait(), 2.0)
    finally:
        spawned.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(spawned, 2.0)


def _counting(until: asyncio.Event, rounds: int = 3) -> tuple:
    """A tick that sets `until` on run number `rounds`, and the list of its runs."""
    seen: List[datetime] = []

    async def _tick(now):
        seen.append(now)
        if len(seen) >= rounds:
            until.set()

    return _tick, seen


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", _TICKS)
async def test_a_tick_that_keeps_failing_stops_neither_loop(monkeypatch, caplog, failing):
    until = asyncio.Event()
    healthy, seen = _counting(until)
    failures: List[datetime] = []

    async def _fails(now):
        failures.append(now)
        raise RuntimeError("boom")

    ticks = {"live": healthy, "filing": healthy, failing: _fails}
    with caplog.at_level(logging.ERROR, logger=els.__name__):
        await _run_loops(monkeypatch, **ticks, until=until)
    assert len(seen) >= 3 and len(failures) >= 2, "the failing loop keeps trying too"
    row = _live_logs(caplog)[0]
    assert row.levelno == logging.ERROR and row.exc_info is not None
    assert all(word in row.getMessage() for word in ("_fails", "RuntimeError", "boom"))


@pytest.mark.asyncio
@pytest.mark.parametrize("stuck", _TICKS)
async def test_a_stuck_tick_holds_up_neither_the_other_loop_nor_the_shutdown(monkeypatch, stuck):
    until = asyncio.Event()
    healthy, seen = _counting(until)
    reached: List[datetime] = []

    async def _stuck(now):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            reached.append(now)
            raise

    ticks = {"live": healthy, "filing": healthy, stuck: _stuck}
    await _run_loops(monkeypatch, **ticks, until=until)
    assert len(seen) >= 3
    assert len(reached) == 1, "cancelling the spawned task reaches the stuck child"


@pytest.mark.asyncio
@pytest.mark.parametrize("hit", _TICKS)
async def test_a_cancellation_nobody_asked_of_a_loop_is_a_failed_tick(monkeypatch, caplog, hit):
    """A tick can join a future someone else cancels. Taken for a shutdown, that ends its loop
    for good with nothing logged, while the other runs on alone."""
    until = asyncio.Event()
    healthy, seen = _counting(until)
    strays: List[datetime] = []

    async def _stray(now):
        strays.append(now)
        joined = asyncio.get_running_loop().create_future()
        joined.cancel()
        await joined

    ticks = {"live": healthy, "filing": healthy, hit: _stray}
    with caplog.at_level(logging.ERROR, logger=els.__name__):
        await _run_loops(monkeypatch, **ticks, until=until)
    assert len(seen) >= 3 and len(strays) >= 2
    row = _live_logs(caplog)[0]
    assert "_stray" in row.getMessage() and "cancelled" in row.getMessage()


@pytest.mark.asyncio
@pytest.mark.parametrize("slow", _TICKS)
async def test_the_spawned_task_ends_only_once_both_loops_have_unwound(monkeypatch, slow):
    """`main.py` closes the shared HTTP clients right after awaiting it, and `gather` left to
    itself ends at the first child cancelled."""
    until = asyncio.Event()
    healthy, _ = _counting(until)
    unwound: List[datetime] = []

    async def _slow_to_unwind(now):
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.05)
            unwound.append(now)

    ticks = {"live": healthy, "filing": healthy, slow: _slow_to_unwind}
    await _run_loops(monkeypatch, **ticks, until=until)
    assert len(unwound) == 1


@pytest.mark.asyncio
async def test_the_filing_loop_starts_behind_the_fmp_loop(monkeypatch):
    """Its first tick reads the records the FMP tick's first fold opens, so a restart inside a
    release window does not lose a minute, and one watched-list read serves both."""
    until = asyncio.Event()
    order: List[str] = []
    waits: List[float] = []

    async def _sleep(seconds):
        waits.append(seconds)
        await asyncio.sleep(0)

    async def _live(now):
        order.append("live")

    async def _filing(now):
        order.append("filing")
        until.set()

    await _run_loops(monkeypatch, live=_live, filing=_filing, until=until, sleep=_sleep)
    assert waits[0] == 45, "both still wait for the app to boot"
    assert order[0] == "live"
    assert els._FILING_LOOP_DELAY_SECONDS in waits and els._FILING_LOOP_DELAY_SECONDS > 0


@pytest.mark.asyncio
async def test_with_the_feature_off_neither_loop_ticks(monkeypatch):
    until = asyncio.Event()
    waits: List[float] = []
    ticked: List[datetime] = []

    async def _sleep(seconds):
        waits.append(seconds)
        if len(waits) > 6:
            until.set()
        await asyncio.sleep(0)

    async def _tick(now):
        ticked.append(now)

    await _run_loops(monkeypatch, live=_tick, filing=_tick, until=until, sleep=_sleep,
                     enabled=False)
    assert ticked == []


@pytest.mark.asyncio
async def test_both_loops_rest_for_the_configured_cadence(monkeypatch):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_INTERVAL_SECONDS", 120)
    until = asyncio.Event()
    ran = {"live": 0, "filing": 0}
    waits: List[float] = []

    async def _sleep(seconds):
        waits.append(seconds)
        await asyncio.sleep(0)

    def _tick(name):
        async def _run(now):
            ran[name] += 1
            if min(ran.values()) >= 2:
                until.set()
        return _run

    await _run_loops(monkeypatch, live=_tick("live"), filing=_tick("filing"), until=until,
                     sleep=_sleep)
    assert waits[0] == 45
    waits.remove(els._FILING_LOOP_DELAY_SECONDS)
    assert len(waits) > 2 and {round(wait) for wait in waits[1:]} == {120}


@pytest.mark.parametrize("name, default, low, high", [
    ("EARNINGS_LIVE_INTERVAL_SECONDS", 60, 15, 300),
    ("EARNINGS_LIVE_SEC_MAX_SYMBOLS", 25, 0, 50),
])
def test_a_bad_cadence_or_sec_budget_fails_the_deploy(monkeypatch, name, default, low, high):
    """`Field(ge, le)`: Railway keeps the old deployment and the error names the variable, where
    an unchecked 500 symbols is an 8-minute round on the SEC circuit that Learn and the stake
    finder share. From the example file, so no real `.env` is read and none can mask a value."""
    example = Path(__file__).resolve().parents[1] / ".env.example"
    assert Settings.model_fields[name].default == default and low <= default <= high
    for bad in (low - 1, high + 1, -1, 10 ** 30, "abc", "20.5", "true", ""):
        monkeypatch.setenv(name, str(bad))
        with pytest.raises(ValidationError) as e:
            Settings(_env_file=example)
        assert [err["loc"] for err in e.value.errors()] == [(name,)], bad
    for ok in (low, default, high):
        monkeypatch.setenv(name, str(ok))
        assert getattr(Settings(_env_file=example), name) == ok


class _StopLoop(BaseException):
    """Ends `_forever`, which outlives every `Exception`."""


@pytest.mark.asyncio
async def test_the_shutdowns_cancel_ends_a_loop_from_inside_its_tick(monkeypatch):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    entered = asyncio.Event()

    async def _blocks(now):
        entered.set()
        await asyncio.Event().wait()

    async def _sleep(_seconds):
        raise _StopLoop

    looping = asyncio.create_task(els._forever(_blocks, 60, sleep=_sleep))
    await asyncio.wait_for(entered.wait(), 1.0)
    looping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await looping


async def _forever_over(monkeypatch, tick, durations: List[float]) -> List[float]:
    """What `_forever` waits after ticks taking `durations` on a stand-in clock."""
    clock = _Clock()
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    waits: List[float] = []

    async def _timed(now):
        clock.at += durations.pop(0)
        await tick(now)

    async def _sleep(seconds):
        waits.append(seconds)
        if not durations:
            raise _StopLoop

    with pytest.raises(_StopLoop):
        await els._forever(_timed, 60, sleep=_sleep)
    return waits


@pytest.mark.asyncio
async def test_the_next_tick_starts_an_interval_after_the_last_one_started(monkeypatch):
    """A round that read 25 symbols in 30 s must not stretch "every minute" to 90 s, and one
    that overran still lets everything else run."""
    async def _ok(now):
        return None

    assert await _forever_over(monkeypatch, _ok, [30.0, 59.5, 60.0, 400.0, 0.0]) \
        == [30.0, 1.0, 1.0, 1.0, 60.0]


@pytest.mark.asyncio
async def test_a_failed_tick_keeps_the_same_clock(monkeypatch):
    async def _fails(now):
        raise RuntimeError("boom")

    assert await _forever_over(monkeypatch, _fails, [20.0, 90.0]) == [40.0, 1.0]
