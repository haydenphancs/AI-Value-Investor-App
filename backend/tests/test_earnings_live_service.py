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
  * the live memo fetches TODAY only while a watched row is pending in 06:00–22:00 ET, and
    the previous trading day at 5 min while hot (until 12:00 ET), else every 30 min;
  * feed builds and the sheet never fetch — only the loop does;
  * the tick runs the SEC watch only for watched symbols still pending today;
  * one window, `live_days` (every calendar day from the previous trading day through today),
    holds the records, the push memory and every reader: a landing is sent once however the
    days roll, and a report dated on a closed day lands, keeps its card and answers the sheet
    like any other;
  * an awaiting card stays up until 12:00 ET on the next trading day and drops once.
Hermetic: inline fakes for FMP, SEC and the dispatcher; the clock is passed in.
"""
from __future__ import annotations

import asyncio
import dataclasses
import itertools
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

import app.services.earnings_filing_service as efs
import app.services.earnings_live_service as els
import app.services.notification_senders.earnings_live_sender as sender
import app.services.push_dispatch_service as pds
import app.services.tracking_service as ts
from app.config import settings
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
async def test_records_outside_the_live_days_are_pruned_at_the_day_roll():
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
    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: _Watcher())
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
    """The real memo, tick, card and sheet over one fake FMP, with no SEC watch or history."""
    _no_history(monkeypatch)
    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: _Watcher())
    return _feed_service(monkeypatch, _et(REPORT, 16), rows)


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
async def test_a_sheet_poll_during_the_sec_watch_cannot_change_what_the_push_reads(monkeypatch):
    """The push reads its snapshot after the SEC await, where any reader can fold."""
    svc_obj = _door_service(monkeypatch, [_row("INTC", REPORT)])
    fmp = svc_obj.fmp
    await _tick(fmp, _et(REPORT, 16), "INTC")                      # seen pending
    fmp.by_day[REPORT] = [_row("INTC", REPORT, eps=0.60)]
    _memo_days_expire(REPORT)
    await ts._cached_earnings_calendar(fmp, REPORT)                # the memo stores 0.60
    fmp.by_day[REPORT] = [_row("INTC", REPORT, eps=0.44)]          # FMP corrects it
    _own_fetch_is_due()

    class _SheetPollDuringTheWatch(_Watcher):
        async def run_once(self, now, symbols, day, *, sec=None, gemini=None):
            await svc_obj.get_earnings_live("INTC")
            return await super().run_once(now, symbols, day, sec=sec, gemini=gemini)

    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: _SheetPollDuringTheWatch())
    sent: List[els.LiveSnapshot] = []

    async def _dispatch(snaps, now):
        sent.extend(snaps)

    at = _et(REPORT, 16, 36)
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(at))
    await els.earnings_live_tick(at, fmp=fmp, watched=["INTC"], dispatch=_dispatch)
    (snap,) = sent
    assert (snap.eps.actual, snap.eps.surprise_pct) == (0.44, 7.32)


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
    svc.observe_rows(REPORT, [_row("AAPL", REPORT)], now, stored_at=0.0)
    svc.observe_rows(REPORT, [_row("INTC", REPORT, eps=0.44)], now, fetched=True)
    memo = [_row("INTC", REPORT, eps=0.40), _row("AAPL", REPORT, eps=1.9)]
    svc.observe_rows(REPORT, memo, now, stored_at=_own_fetch_stamp() - 900)
    assert svc.snapshot("INTC", REPORT, now).eps.actual == 0.44
    assert svc.snapshot("AAPL", REPORT, now).eps.actual == 1.9


@pytest.mark.parametrize("stored_at", [None, 0.0], ids=["unknown_age", "very_old"])
def test_a_record_with_no_own_fetch_folds_every_memo_row(stored_at):
    """Before the hot gate opens the memo is the only source there is."""
    svc = _svc()
    now = _et(REPORT, 5)
    svc.observe_rows(REPORT, [_row("INTC", REPORT)], now, stored_at=stored_at)
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
    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: _Watcher())
    sender.reset()
    yield rec
    sender.reset()


def _sent_once(day: date) -> List[tuple]:
    """What INTC's report is owed by the real sender: both kinds, under one key, once."""
    key = f"earnings_result:INTC:{day.isoformat()}"
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

    async def _memo(_fmp, today):
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
    """A learned closure moves the window back a session. The report's record and its key left
    together, so the re-read row is a report already in, not a landing."""
    tue, wed, thu = date(2026, 10, 27), PREV, REPORT
    fmp = await _a_landing(monkeypatch, tue)
    _nothing_is_due(thu)
    await _real_tick(monkeypatch, fmp, _et(thu, 1))
    assert _svc().snapshot("INTC", tue, _et(thu, 1)) is None
    monkeypatch.setattr(mh, "_OBSERVED_CLOSURES", {(wed.year, wed.month, wed.day)})
    await _real_tick(monkeypatch, fmp, _et(thu, 10))
    assert _svc().snapshot("INTC", tue, _et(thu, 10)).status == els.STATUS_REPORTED
    assert pushes.sent == _sent_once(tue)


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting_out_a_failure", [False, True],
                         ids=["nothing_due", "failure_wait"])
async def test_refresh_prunes_with_nothing_due_and_during_the_failure_wait(waiting_out_a_failure):
    svc = _svc()
    svc.observe_rows(PREV, [_row("INTC", PREV, eps=0.30, rev=1.3e10)], _et(PREV, 17))
    mono = time.monotonic()
    svc._fetched.update({PREV: mono, REPORT: mono})
    if waiting_out_a_failure:
        svc._failed_until = mono + 60
    now = _et(NEXT, 9)                  # Friday: Wednesday has left the window
    fmp = _FMP()
    await svc.refresh(now, fmp=fmp, symbols=["INTC"])
    assert fmp.calls == [], "nothing was fetched: the prune did not need a round"
    assert svc.snapshot("INTC", PREV, now) is None
    assert set(svc._fetched) == {REPORT}


@pytest.mark.asyncio
async def test_a_report_dated_on_a_non_trading_day_stays_until_the_floor_passes_it():
    svc = _svc()
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY)], _et(SATURDAY, 9))
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY, eps=0.5, rev=17e9)], _et(SATURDAY, 11))
    fmp = _FMP()
    for day in (SUNDAY, MONDAY):
        await svc.refresh(_et(day, 9), fmp=fmp, symbols=["INTC"])
        assert svc.snapshot("INTC", SATURDAY, _et(day, 9)).status == els.STATUS_REPORTED
    await svc.refresh(_et(TUESDAY, 9), fmp=fmp, symbols=["INTC"])
    assert svc.snapshot("INTC", SATURDAY, _et(TUESDAY, 9)) is None


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
    svc._fetched[report] = time.monotonic() - (els.EARNINGS_LIVE_COLD_TTL_SECONDS + 1)
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


def test_a_closed_day_is_asked_once_then_cold_and_only_while_a_watched_report_is_pending():
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


@pytest.mark.asyncio
async def test_a_restart_on_a_monday_asks_each_day_before_today_once():
    fmp = _FMP()
    svc = _svc()
    await svc.refresh(_et(MONDAY, 1), fmp=fmp, symbols=["INTC"])
    await svc.refresh(_et(MONDAY, 1, 1), fmp=fmp, symbols=["INTC"])
    assert sorted(fmp.calls) == [d.isoformat() for d in (FRIDAY, SATURDAY, SUNDAY)]


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


def test_an_awaiting_card_that_dropped_gives_way_to_the_older_report():
    svc = _svc()
    svc.observe_rows(FRIDAY, [_row("INTC", FRIDAY, eps=0.44, rev=17.1e9)], _et(FRIDAY, 17))
    svc.observe_rows(SATURDAY, [_row("INTC", SATURDAY)], _et(SATURDAY, 9))
    assert [s.day for s in svc.recent_snapshots(["INTC"], _et(MONDAY, 9))] == [SATURDAY]
    assert [s.day for s in svc.recent_snapshots(["INTC"], _et(MONDAY, 12))] == [FRIDAY]


# The sheet.


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


@pytest.mark.asyncio
async def test_the_sheet_refuses_a_date_before_the_window(monkeypatch):
    svc_obj = _sheet_on(monkeypatch, MONDAY)
    with pytest.raises(ValueError, match="2026-11-05"):
        await svc_obj.get_earnings_live("INTC", FRIDAY - timedelta(days=1))
