"""Live earnings pushes: two kinds, one dedup key per report, live kind first.

The owner's rule (2026-10-29): a new "Live Results" toggle, "Results & Surprises" stays and can
be sent live too, and NOBODY GETS TWO PUSHES FOR ONE REPORT. That holds because
`push_dispatch_service` claims a key only for users who pass their toggle and cap, and both
kinds — and the daily 16:00 ET sender — claim the SAME key. What is pinned here:
  * both live kinds and the daily sender use one key (`result_dedup_key`);
  * `earnings_live` goes FIRST, sequentially, then `earnings_result`;
  * `earnings_result` is sent on FMP's adjusted EPS only, never on a filing figure (it
    reaches 1.02 users), and only past `MIN_SURPRISE`;
  * `EARNINGS_LIVE_PUSH_ENABLED` holds only the new kind and `EARNINGS_LIVE_RESULT_PUSH_ENABLED`
    only the live sending of the existing one, and both ship off (on the second, the owner's
    "Keep it off at first", 2026-11-05);
  * the daily 16:00 ET pass reads neither switch, and neither does the wake of a row it held: as
    shipped it is what tells a result, in the words it always used, from 16:00 ET and not before;
  * what off costs beyond the wait: the kinds of report nobody is told, each with its sheet,
    each named on a line of its own, never counted or placed;
  * a lock-screen banner prints an absolute figure only from the company's own release;
  * each report is dispatched once per process (the 60 s loop re-hands every landing);
  * a failed watcher read is the one exception: it told nobody, so the key goes back, the round
    ends there (one failed read a round, however many reports wait) and a later one retries,
    live kind first, with that ticker behind every other report, so one whose read keeps failing
    holds back nobody;
  * neither kind goes out for a report older than yesterday, the daily pass's own window, and
    such a report is handled once, for good;
  * the daily pass keeps the EPS the live path keeps, so a row the fold reads as unfilled never
    goes out at 16:00 ET either;
  * a row of zeros shown to be unfilled, stored first or refused at once, sends no false miss,
    whatever FMP fills next or sends again, and a report left with nothing on show sends nothing
    at all;
  * a digit off a consensus straddling the guard's floor is never announced as a -90% miss;
  * a round sends oldest first, so the cap cannot run a report out of that window;
  * the round cap's warning counts the reports still owed a push, nothing else;
  * one report on two dated rows is told once to each user, whichever source fills which row
    in which order, across a restart, across a closed day and past the daily pass: the real loop
    runs those over the real dispatcher and a fake ledger, and the keys it holds are what they
    assert;
  * after a restart the leftover row reads as its report from the filing `earnings_filing_cache`
    kept, as it did before the restart; with no table (not applied yet, or a failed read) it
    reads as its own, with no figure, and the ledger alone keeps the report to one push;
  * who was told is the ledger's answer, so a report nobody heard about holds nothing back;
  * a cent print with the consensus inside its half cent has no verdict, so neither kind and no
    pass states one, and the Financials tab's raw compare is pinned beside it.
  * a result landing in a default-settings user's night waits for 07:00 on their clock, the row
    claimed at once, one buzz at the wake, across both kinds and the daily pass, through a
    restart and past the window above;
  * what that wake says and opens is the report as it stands then: the text comes from the
    record, a surprise the record no longer holds is never buzzed, nor is what a wake wrote while
    it waited for FMP, a restart included, and the sheet still takes the report's date on any day
    such a row can wake;
  * the record it reads is never older than the row: the daily pass folds what it read first;
  * a report FMP lists twice is told by the pass as its record reads it, so every phone and the
    sheet say one thing (the owner's "Follow the record", 2026-11-11), and so is a report whose
    EPS is 0, which the zero rules weigh against what its record already holds; each from the row
    the record would read when the pass could not fold into it;
  * a figure still being confirmed is no answer, so the row asks again, a bounded number of
    times, instead of ending, and each ask has the report read just before the next look, so a
    closed day's 2 h cadence cannot hide FMP's recovery from every look: timed from the instant
    the row is held to, however far into a hand-out the row was, one read shared by the rows of
    a hand-out and by looks under a tick apart, none once the report has answered, and none for
    a wake that sends or ends its row;
  * a held live row the user's settings turn off at its wake goes out as the result they take,
    while the result push is on and the record holds a surprise it would start now, and is
    otherwise deleted, so the key is free for the round or the pass that can still tell it.
The dispatcher is a recorder, or the real one over a fake table where a failed read or the
ledger is the point, and the real loop keeps its filings in an in-memory `earnings_filing_cache`
(`_filing_cache_fake.FilingCache`) — nothing here reaches APNs or Supabase.
"""
from __future__ import annotations

import contextlib
import json
import ast
import asyncio
import logging
import re
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pytest

import app.services.earnings_filing_service as efs
import app.services.earnings_live_service as els
import app.services.learn_weekly.signals as signals
import app.services.notification_senders.earnings_live_sender as sender
import app.services.notification_senders.earnings_sender as daily
import app.services.push_dispatch_service as pds
import app.services.tracking_service as ts
from app.config import Settings, settings
from app.services import quiet_hours as qh
from app.services.earnings_filing_service import FilingFigures
from app.services.earnings_live_service import (
    BASIS_ADJUSTED,
    BASIS_GAAP,
    EARNINGS_LIVE_UNLISTED_END_SECONDS,
    SOURCE_FILING,
    SOURCE_FMP,
    STATUS_AWAITING,
    STATUS_LIVE,
    STATUS_REPORTED,
    EarningsLiveService,
    LiveMetric,
    LiveSnapshot,
    get_earnings_live_service,
    sheet_days,
)
from app.services.notification_inbox_service import NotificationInboxService
from app.services.notification_kinds import (
    KIND_EARNINGS_LIVE,
    KIND_EARNINGS_RESULT,
    NOTIFICATION_KINDS,
)
from app.services.push_service import PushOutcome
from app.utils.market_hours import ET, previous_trading_day

from _filing_cache_fake import FilingCache

REPORT = date(2026, 10, 29)
NOW = datetime(2026, 10, 29, 20, 5, tzinfo=timezone.utc)
FRIDAY, SATURDAY, SUNDAY, MONDAY, TUESDAY = (date(2026, 11, day) for day in (6, 7, 8, 9, 10))


def _snap(symbol="INTC", *, eps=None, eps_source=SOURCE_FMP, eps_basis=BASIS_ADJUSTED,
          eps_est=0.41, eps_pct=None, rev=None, rev_source=SOURCE_FMP, rev_est=16.6e9,
          rev_pct=None, preliminary=False, status=STATUS_REPORTED) -> LiveSnapshot:
    return LiveSnapshot(
        symbol=symbol, day=REPORT, status=status, company_name="Intel",
        eps=LiveMetric(eps_est, eps, eps_pct, eps_source if eps is not None else None,
                       eps_basis if eps is not None else None),
        revenue=LiveMetric(rev_est, rev, rev_pct, rev_source if rev is not None else None),
        preliminary=preliminary, checked_at=NOW, filing=None,
    )


def _methods_left_on(service: Optional[EarningsLiveService]) -> List[str]:
    """The class methods `service` holds on the instance itself, each hiding the class's."""
    if service is None:
        return []
    return sorted(name for name, value in vars(service).items()
                  if callable(value) and hasattr(type(service), name))


@pytest.fixture(autouse=True)
def _a_test_leaves_no_method_on_the_process_record():
    """monkeypatch undoes a patch on an INSTANCE by writing the original back onto it, so a method
    patched onto the process's live record outlives the test and hides every later patch of the
    class, in this file and the ones after it. Patch a test's own record (`live`), or the class.
    With no fixture of its own it is set up first and torn down last, after monkeypatch's undo."""
    yield
    left = _methods_left_on(els._service)
    assert not left, f"this test left methods on the process's live record: {left}"


class _Dispatch:
    def __init__(self):
        self.sent: List[Dict[str, Any]] = []

    async def notify_watchers(self, **kw):
        self.sent.append(kw)
        return 1


@pytest.fixture
def dispatch(monkeypatch):
    rec = _Dispatch()
    import app.services.push_dispatch_service as pds

    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: rec)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", True)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", True)
    sender.reset()
    yield rec
    sender.reset()


def test_the_new_kind_is_registered_beside_its_siblings():
    live = NOTIFICATION_KINDS[KIND_EARNINGS_LIVE]
    result = NOTIFICATION_KINDS[KIND_EARNINGS_RESULT]
    assert live.preference_key == "notify_earnings_live" and live.default_on is True
    assert live.master_preference_key == result.master_preference_key == "notify_earnings_alerts"
    assert (live.category, live.thread_id) == (result.category, result.thread_id)
    assert live.respects_quiet_hours is True
    assert live.expiration_hours == 4


def test_both_live_push_switches_ship_off():
    """The declared defaults, not this machine's environment. Live Results waits for the release
    with its toggle; on the result push, the owner's "Keep it off at first" (2026-11-05)."""
    assert Settings.model_fields["EARNINGS_LIVE_PUSH_ENABLED"].default is False
    assert Settings.model_fields["EARNINGS_LIVE_RESULT_PUSH_ENABLED"].default is False


def test_one_key_for_both_live_kinds_and_the_daily_sender():
    assert daily.result_dedup_key("intc", "2026-10-29") == "earnings_result:INTC:2026-10-29"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [KIND_EARNINGS_RESULT, daily.KIND_EARNINGS_UPCOMING])
@pytest.mark.parametrize("enabled,report_date", [(True, "2026-10-29"), (False, None)],
                         ids=["live_on", "live_off"])
async def test_the_daily_sender_names_the_report_only_while_live_results_exist(
        monkeypatch, kind, enabled, report_date):
    """A route with no date offers no live results, and with the switch off none exist: the
    sheet would answer estimates under a push announcing the figures."""
    rec = _Dispatch()
    monkeypatch.setattr(daily, "get_push_dispatch_service", lambda: rec)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", enabled)
    await daily._dispatch(kind=kind, symbol="INTC", when="2026-10-29", title="t", body="b")
    (sent,) = rec.sent
    assert sent["data"].get("report_date") == report_date
    assert sent["data"]["ticker"] == "INTC" and "2026-10-29" in sent["dedup_key"]


@pytest.mark.asyncio
async def test_the_live_kind_goes_first_then_the_surprise_under_the_same_key(dispatch):
    await sender.dispatch_landings([_snap(eps=0.60, eps_pct=46.34, rev=17.1e9, rev_pct=3.01)], NOW)
    kinds = [s["kind"] for s in dispatch.sent]
    keys = {s["dedup_key"] for s in dispatch.sent}
    assert kinds == [KIND_EARNINGS_LIVE, KIND_EARNINGS_RESULT]
    assert keys == {"earnings_result:INTC:2026-10-29"}
    assert all(s["data"]["report_date"] == "2026-10-29" for s in dispatch.sent)


@pytest.mark.asyncio
async def test_a_small_surprise_gets_only_the_live_kind(dispatch):
    await sender.dispatch_landings([_snap(eps=0.42, eps_pct=2.44, rev=17.1e9)], NOW)
    assert [s["kind"] for s in dispatch.sent] == [KIND_EARNINGS_LIVE]


@pytest.mark.asyncio
async def test_the_surprise_kind_never_uses_a_filing_figure(dispatch):
    snap = _snap(eps=0.60, eps_source=SOURCE_FILING, eps_pct=46.34, rev=17.1e9,
                 rev_source=SOURCE_FILING, preliminary=True, status=STATUS_LIVE)
    await sender.dispatch_landings([snap], NOW)
    assert [s["kind"] for s in dispatch.sent] == [KIND_EARNINGS_LIVE]
    # FMP's figure lands a round later: NOW the surprise is decided — once.
    await sender.dispatch_landings([_snap(eps=0.60, eps_pct=46.34, rev=17.1e9)], NOW)
    await sender.dispatch_landings([_snap(eps=0.60, eps_pct=46.34, rev=17.1e9)], NOW)
    assert [s["kind"] for s in dispatch.sent] == [KIND_EARNINGS_LIVE, KIND_EARNINGS_RESULT]


@pytest.mark.asyncio
async def test_with_the_push_flag_off_only_the_existing_kind_is_sent_live(dispatch, monkeypatch):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    await sender.dispatch_landings([_snap(eps=0.60, eps_pct=46.34, rev=17.1e9)], NOW)
    assert [s["kind"] for s in dispatch.sent] == [KIND_EARNINGS_RESULT]


@pytest.mark.asyncio
async def test_with_both_flags_off_nothing_is_sent(dispatch, monkeypatch):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", False)
    await sender.dispatch_landings([_snap(eps=0.60, eps_pct=46.34)], NOW)
    assert dispatch.sent == []


@pytest.mark.asyncio
async def test_a_report_is_dispatched_once_per_process(dispatch):
    snap = _snap(eps=0.42, eps_pct=2.44, rev=17.1e9)
    for _ in range(3):
        await sender.dispatch_landings([snap], NOW)
    assert len(dispatch.sent) == 1


@pytest.mark.asyncio
async def test_the_once_per_report_memory_is_bounded_to_the_live_days(dispatch):
    await sender.dispatch_landings([_snap(eps=0.42, eps_pct=2.44, rev=17.1e9)], NOW)
    assert ("INTC", REPORT) in sender._live_done
    three_days_later = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    await sender.dispatch_landings([], three_days_later)
    assert ("INTC", REPORT) not in sender._live_done


@pytest.mark.asyncio
async def test_the_memory_keeps_a_closed_day_report_as_long_as_it_is_live(dispatch):
    """A Saturday report is handed over through Monday. A key forgotten any sooner would be
    sent again on the next round."""
    saturday = date(2026, 11, 7)
    snap = replace(_snap(eps=0.42, eps_pct=2.44, rev=17.1e9), day=saturday)
    for now in (datetime(2026, 11, 7, 18, tzinfo=timezone.utc),
                datetime(2026, 11, 8, 18, tzinfo=timezone.utc),
                datetime(2026, 11, 9, 18, tzinfo=timezone.utc)):
        await sender.dispatch_landings([snap], now)
    assert len(dispatch.sent) == 1
    await sender.dispatch_landings([], datetime(2026, 11, 10, 18, tzinfo=timezone.utc))
    assert ("INTC", saturday) not in sender._live_done


@pytest.mark.asyncio
async def test_a_dropped_digit_eps_never_reaches_a_result_push(dispatch):
    """A held figure arrives as `actual=None, checking=True` — not decidable, not sent."""
    held = LiveSnapshot(
        symbol="INTC", day=REPORT, status=STATUS_LIVE, company_name=None,
        eps=LiveMetric(1.65, None, None, None, None, checking=True),
        revenue=LiveMetric(16.6e9, 17.1e9, 3.01, SOURCE_FMP),
        preliminary=False, checked_at=NOW, filing=None,
    )
    await sender.dispatch_landings([held], NOW)
    assert [s["kind"] for s in dispatch.sent] == [KIND_EARNINGS_LIVE]
    assert "EPS being confirmed" in dispatch.sent[0]["body"]


@pytest.mark.asyncio
async def test_a_row_with_both_actuals_at_zero_pushes_nothing(dispatch):
    """The record refuses FMP's unfilled shape, so the live sender sees no landing and sends no
    "EPS missed consensus by 100%"."""
    def _row(**kw):
        return {"symbol": "INTC", "date": REPORT.isoformat(), "epsEstimated": 0.41,
                "revenueEstimated": 16.6e9, **kw}

    live = EarningsLiveService()
    live.observe_rows(REPORT, [_row()], datetime(2026, 10, 29, 13, 0, tzinfo=timezone.utc))
    live.observe_rows(REPORT, [_row(epsActual=0, revenueActual=0)], NOW)
    await sender.dispatch_landings(live.landed_snapshots(NOW), NOW)
    assert dispatch.sent == []


def _revenue_phrase(rec: els._Record) -> str:
    snap = els.snapshot_of(rec, REPORT, datetime(2026, 10, 29, 17, 0, tzinfo=ET))
    return sender.live_copy(snap)[1].split(" · ")[0]


def _filed_revenue(value: float) -> FilingFigures:
    return FilingFigures(accession="0000050863-26-000123", accepted_at=None, url=None,
                         revenue=value)


def test_a_figure_shown_on_agreement_is_announced_with_no_percentage():
    """+918% against a dropped-digit consensus is the worst place to print it."""
    rec = els._Record("INTC", REPORT, eps_estimate=0.41, revenue_estimate=1.66e9, fmp_eps=0.44,
                      fmp_revenue=16.9e9, filing=_filed_revenue(16.9e9))
    assert _revenue_phrase(rec) == "Revenue reported"


def test_a_held_fmp_revenue_announces_the_companys_own_figure():
    rec = els._Record("INTC", REPORT, eps_estimate=0.41, revenue_estimate=16.6e9, fmp_eps=0.44,
                      fmp_revenue=1.69e9, filing=_filed_revenue(16.9e9))
    assert _revenue_phrase(rec) == "Revenue $16.9B (beat consensus by 2%)"


def test_a_zero_revenue_past_a_material_consensus_is_never_announced_as_a_miss():
    rec = els._Record("JOBY", REPORT, eps_estimate=-0.2, revenue_estimate=30e6,
                      revenue_guard_estimate=16.6e9, fmp_eps=-0.2, fmp_revenue=0.0)
    assert _revenue_phrase(rec) == "Revenue being confirmed"


def test_a_figure_far_off_a_consensus_that_lost_a_digit_is_announced_with_no_percentage():
    """A x10 slip stays under the 1,000% the copy hides, so it used to read "beat by 911%"."""
    rec = els._Record("TINY", REPORT, eps_estimate=0.41, revenue_estimate=95e6, fmp_eps=0.44,
                      fmp_revenue=961e6, filing=_filed_revenue(960e6))
    assert _revenue_phrase(rec) == "Revenue reported"
    rec = els._Record("TINY", REPORT, eps_estimate=0.41, revenue_estimate=95e6, fmp_eps=0.44,
                      filing=_filed_revenue(960e6))
    assert "beat" in _revenue_phrase(rec), "one source alone keeps its percentage"


def test_a_filing_past_fmps_dropped_digit_is_announced_as_the_companys_figure():
    rec = els._Record("TINY", REPORT, eps_estimate=0.41, revenue_estimate=50e6, fmp_eps=0.44,
                      fmp_revenue=5.2e6, filing=_filed_revenue(52e6))
    assert _revenue_phrase(rec) == "Revenue $52.0M (beat consensus by 4%)"


@pytest.mark.asyncio
@pytest.mark.parametrize("zeros_first", [False, True], ids=["the_filing_first", "the_row_first"])
async def test_a_row_of_zeros_beside_a_filing_sends_no_false_miss(dispatch, monkeypatch,
                                                                  zeros_first):
    """With the default switches this was the only push the report sent: "EPS missed consensus
    by 100%", over the $0.44 the company printed."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)

    def _row(**kw):
        return {"symbol": "JOBY", "date": REPORT.isoformat(), "epsEstimated": 0.41,
                "revenueEstimated": 30e6, **kw}

    filing = FilingFigures(accession="0001628280-26-000128", accepted_at=None, url=None,
                           revenue=33e6, eps_adjusted=0.44)
    zeros = _row(epsActual=0, revenueActual=0)
    live = EarningsLiveService()
    live.observe_rows(REPORT, [_row()], datetime(2026, 10, 29, 13, 0, tzinfo=timezone.utc))
    if zeros_first:
        live.observe_rows(REPORT, [zeros], NOW)
    live.absorb_filing("JOBY", REPORT, filing, NOW)
    live.observe_rows(REPORT, [zeros], NOW)
    (snap,) = live.landed_snapshots(NOW)
    assert (snap.eps.actual, snap.eps.source) == (0.44, SOURCE_FILING)
    await sender.dispatch_landings([snap], NOW)
    assert dispatch.sent == [], "the result push waits for FMP's own figure"


@pytest.mark.asyncio
async def test_a_stored_zero_taken_off_leaves_no_push_with_nothing_to_report(dispatch):
    def _row(**kw):
        return {"symbol": "JOBY", "date": REPORT.isoformat(), **kw}

    live = EarningsLiveService()
    live.observe_rows(REPORT, [_row(revenueEstimated=30e6)],
                      datetime(2026, 10, 29, 13, 0, tzinfo=timezone.utc))
    live.observe_rows(REPORT, [_row(revenueEstimated=30e6, revenueActual=0)], NOW)
    live.observe_rows(REPORT, [_row(revenueEstimated=16.6e9)], NOW)
    await sender.dispatch_landings(live.landed_snapshots(NOW), NOW)
    assert dispatch.sent == []


def _joby(**kw) -> Dict[str, Any]:
    return {"symbol": "JOBY", "date": REPORT.isoformat(), "epsEstimated": 0.41,
            "revenueEstimated": 30e6, **kw}


_MORNING = datetime(2026, 10, 29, 19, 0, tzinfo=timezone.utc)
_ZEROS_AT = datetime(2026, 10, 29, 20, 4, 30, tzinfo=timezone.utc)
_FILED_AT = datetime(2026, 10, 29, 20, 4, 40, tzinfo=timezone.utc)
_FILLED_AT = datetime(2026, 10, 29, 20, 40, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("zeros_first", [False, True], ids=["the_filing_first", "the_row_first"])
async def test_a_revenue_fill_after_a_row_of_zeros_sends_no_false_miss(dispatch, monkeypatch,
                                                                       zeros_first):
    """Default switches, the real folds and the real send: "EPS missed consensus by 100%" went
    out at 16:40 over the $0.44 on show since 16:05, once FMP filled the revenue alone."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    filing = FilingFigures(accession="0001628280-26-000130", accepted_at=None, url=None,
                           revenue=33e6, eps_adjusted=0.44)
    live = EarningsLiveService()
    live.observe_rows(REPORT, [_joby()], _MORNING)
    if zeros_first:
        live.observe_rows(REPORT, [_joby(epsActual=0, revenueActual=0)], _ZEROS_AT)
    live.absorb_filing("JOBY", REPORT, filing, _FILED_AT)
    if not zeros_first:
        live.observe_rows(REPORT, [_joby(epsActual=0, revenueActual=0)], _FILED_AT)
    live.observe_rows(REPORT, [_joby(revenueActual=33.2e6)], _FILLED_AT)
    (snap,) = live.landed_snapshots(_FILLED_AT)
    assert (snap.eps.actual, snap.eps.source) == (0.44, SOURCE_FILING)
    await sender.dispatch_landings([snap], _FILLED_AT)
    assert dispatch.sent == []


@pytest.mark.asyncio
async def test_a_material_consensus_then_a_revenue_fill_sends_no_false_miss(dispatch, monkeypatch):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    live = EarningsLiveService()
    live.observe_rows(REPORT, [_joby()], _MORNING)
    live.observe_rows(REPORT, [_joby(epsActual=0, revenueActual=0)], _ZEROS_AT)
    live.observe_rows(REPORT, [_joby(revenueEstimated=16.6e9)], _FILED_AT)
    live.observe_rows(REPORT, [_joby(revenueActual=16.9e9, revenueEstimated=16.6e9)], _FILLED_AT)
    (snap,) = live.landed_snapshots(_FILLED_AT)
    assert snap.eps.actual is None
    await sender.dispatch_landings([snap], _FILLED_AT)
    assert dispatch.sent == []


@pytest.mark.asyncio
async def test_a_fill_with_no_other_evidence_sends_no_false_miss_either(dispatch, monkeypatch):
    """The fill alone proves it: a report with a real revenue had no real $0 row."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    live = EarningsLiveService()
    live.observe_rows(REPORT, [_joby()], _MORNING)
    live.observe_rows(REPORT, [_joby(epsActual=0, revenueActual=0)], _ZEROS_AT)
    live.observe_rows(REPORT, [_joby(revenueActual=33.2e6)], _FILLED_AT)
    await sender.dispatch_landings(live.landed_snapshots(_FILLED_AT), _FILLED_AT)
    assert dispatch.sent == []


@pytest.mark.asyncio
async def test_a_digit_off_a_consensus_on_the_floor_is_announced_as_being_confirmed(dispatch):
    """The real folds and the real send: "Revenue missed consensus by 90%" went out here."""
    def _row(**kw):
        return {"symbol": "ABCD", "date": REPORT.isoformat(), "epsEstimated": 0.41, **kw}

    live = EarningsLiveService()
    live.observe_rows(REPORT, [_row(revenueEstimated=99e6)], _MORNING)
    live.observe_rows(REPORT, [_row(epsActual=0.44, revenueActual=9.9e6, revenueEstimated=99e6),
                               _row(revenueEstimated=101e6)], NOW)
    await sender.dispatch_landings(live.landed_snapshots(NOW), NOW)
    (live_push,) = [s for s in dispatch.sent if s["kind"] == KIND_EARNINGS_LIVE]
    assert live_push["body"] == "Revenue being confirmed · EPS beat consensus by 7%."


# ── the daily pass reads an EPS of zero as the live path does ────────────────


@pytest.mark.parametrize("eps,rev,rev_est", [
    (0, 0, 16.6e9), (0, -5e6, 16.6e9), (0, 0, None), (0, 0, 0), (0, 0, 100e6),
    (0, 0, 99.9e6), (0, 0, 30e6), (0, 17.1e9, 16.6e9), (0, None, 16.6e9), (0.60, 0, 16.6e9),
])
def test_the_daily_pass_and_the_live_path_keep_the_same_eps(eps, rev, rev_est):
    """One rule, not a copy of it: what the fold refuses never reaches the 16:00 ET banner, and
    what it keeps is still told."""
    row = {"symbol": "INTC", "date": REPORT.isoformat(), "epsEstimated": 0.41, "epsActual": eps,
           "revenueActual": rev, "revenueEstimated": rev_est}
    live = EarningsLiveService()
    live.observe_rows(REPORT, [row], NOW)
    kept = live.snapshot("INTC", REPORT, NOW).eps.actual
    assert bool(daily.select_results([row], REPORT)) == (kept is not None)


# ── the round cap ────────────────────────────────────────────────────────────


def _seed_done(snaps, *, surprise=True):
    for s in snaps:
        sender._live_done.add((s.symbol, s.day))
        if surprise:
            sender._surprise_done.add((s.symbol, s.day))


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def _owed(line: str) -> int:
    """The count a cap warning states, read whole: "361 report(s)" also contains "1 report(s)"."""
    found = re.search(r"reached, (\d+) report", line)
    assert found, line
    return int(found.group(1))


@pytest.mark.asyncio
async def test_the_cap_warning_counts_only_the_reports_still_owed(dispatch, monkeypatch, caplog):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    fresh = [_snap(f"A{i:03d}", eps=0.60, eps_pct=46.34) for i in range(61)]
    done = [_snap(f"Z{i:03d}", eps=0.42, eps_pct=2.44) for i in range(300)]
    _seed_done(done)
    with caplog.at_level(logging.WARNING, logger=sender.__name__):
        await sender.dispatch_landings(fresh + done, NOW)
    assert len(dispatch.sent) == 60
    lines = _warnings(caplog)
    assert len(lines) == 1 and _owed(lines[0]) == 1


@pytest.mark.asyncio
async def test_no_cap_warning_when_nothing_is_left(dispatch, monkeypatch, caplog):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    fresh = [_snap(f"A{i:03d}", eps=0.60, eps_pct=46.34) for i in range(60)]
    done = [_snap(f"Z{i:03d}", eps=0.42, eps_pct=2.44) for i in range(300)]
    _seed_done(done)
    with caplog.at_level(logging.WARNING, logger=sender.__name__):
        await sender.dispatch_landings(fresh + done, NOW)
    assert len(dispatch.sent) == 60
    assert _warnings(caplog) == []


@pytest.mark.asyncio
async def test_no_cap_warning_when_only_reports_waiting_on_fmp_are_left(
        dispatch, monkeypatch, caplog):
    """Still open, nothing to send yet: the round stops on them and owes nobody a line."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    fresh = [_snap(f"A{i:03d}", eps=0.60, eps_pct=46.34) for i in range(60)]
    waiting = [_snap(f"Z{i:03d}", eps=0.60, eps_source=SOURCE_FILING) for i in range(5)]
    _seed_done(waiting, surprise=False)
    with caplog.at_level(logging.WARNING, logger=sender.__name__):
        await sender.dispatch_landings(fresh + waiting, NOW)
    assert len(dispatch.sent) == 60
    assert _warnings(caplog) == []


@pytest.mark.asyncio
async def test_the_cap_warning_skips_reports_waiting_on_fmp(dispatch, monkeypatch, caplog):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    fresh = [_snap(f"A{i:03d}", eps=0.60, eps_pct=46.34) for i in range(61)]
    waiting = [_snap(f"Z{i:03d}", eps=0.60, eps_source=SOURCE_FILING) for i in range(300)]
    _seed_done(waiting, surprise=False)
    with caplog.at_level(logging.WARNING, logger=sender.__name__):
        await sender.dispatch_landings(fresh + waiting, NOW)
    lines = _warnings(caplog)
    assert len(lines) == 1 and _owed(lines[0]) == 1


@pytest.mark.asyncio
async def test_the_cap_warning_counts_a_seen_report_whose_fmp_beat_just_arrived(
        dispatch, monkeypatch, caplog):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    fresh = [_snap(f"A{i:03d}", eps=0.60, eps_pct=46.34) for i in range(60)]
    seen = [_snap(f"Z{i:03d}", eps=0.60, eps_pct=46.34) for i in range(5)]
    _seed_done(seen, surprise=False)
    with caplog.at_level(logging.WARNING, logger=sender.__name__):
        await sender.dispatch_landings(fresh + seen, NOW)
    lines = _warnings(caplog)
    assert len(lines) == 1 and _owed(lines[0]) == 5


@pytest.mark.asyncio
async def test_the_cap_warning_with_the_result_flag_off(dispatch, monkeypatch, caplog):
    """Beats, so only the flag stands between a report told live and a second send."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", False)
    fresh = [_snap(f"A{i:03d}", eps=0.60, eps_pct=46.34, rev=17.1e9) for i in range(61)]
    done = [_snap(f"Z{i:03d}", eps=0.60, eps_pct=46.34, rev=17.1e9) for i in range(300)]
    _seed_done(done, surprise=False)
    with caplog.at_level(logging.WARNING, logger=sender.__name__):
        await sender.dispatch_landings(fresh + done, NOW)
    lines = _warnings(caplog)
    assert len(lines) == 1 and _owed(lines[0]) == 1


@pytest.mark.asyncio
async def test_the_cap_warning_counts_reports_not_sends(dispatch, caplog):
    """With both flags on a beat costs two sends, and the line still counts one report."""
    fresh = [_snap(f"A{i:03d}", eps=0.60, eps_pct=46.34) for i in range(31)]
    with caplog.at_level(logging.WARNING, logger=sender.__name__):
        await sender.dispatch_landings(fresh, NOW)
    assert len(dispatch.sent) == 60
    lines = _warnings(caplog)
    assert len(lines) == 1 and _owed(lines[0]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("eps,estimate", [(0.42, 0.41), (0.42, 0.005)],
                         ids=["in_line", "near_zero_estimate"])
async def test_no_cap_warning_for_new_reports_with_nothing_to_send(
        dispatch, monkeypatch, caplog, eps, estimate):
    """The result kind alone, on reports this process has not decided yet: the round stops on
    them and the next one sends nothing for them. The near-zero estimate has no honest surprise."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    fresh = [_snap(f"A{i:03d}", eps=0.60, eps_pct=46.34) for i in range(60)]
    quiet = [_snap(f"Z{i:03d}", eps=eps, eps_est=estimate) for i in range(5)]
    with caplog.at_level(logging.WARNING, logger=sender.__name__):
        await sender.dispatch_landings(fresh + quiet, NOW)
    assert len(dispatch.sent) == 60
    assert _warnings(caplog) == []


_BEHIND_THE_CAP = {
    "beat": {"eps": 0.60, "eps_pct": 46.34},
    "miss": {"eps": 0.30, "eps_pct": -26.83},
    "exactly_at_the_bar": {"eps": 21.0, "eps_est": 20.0, "eps_pct": 5.0},
    "in_line": {"eps": 0.42, "eps_pct": 2.44},
    "near_zero_estimate": {"eps": 0.42, "eps_est": 0.005},
    "no_estimate": {"eps": 0.42, "eps_est": None},
    "filing_figure": {"eps": 0.60, "eps_source": SOURCE_FILING, "eps_pct": 46.34},
    "no_eps_yet": {"rev": 17.1e9, "rev_pct": 3.01},
    "cent_print_inside_its_band": {"eps": 0.01, "eps_est": 0.0148},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", list(_BEHIND_THE_CAP.values()), ids=list(_BEHIND_THE_CAP))
@pytest.mark.parametrize("live,result", [(False, True), (True, False), (True, True)],
                         ids=["live_off", "result_off", "both_on"])
@pytest.mark.parametrize("told_live", [False, True], ids=["new", "told_live"])
async def test_the_cap_warning_says_what_the_next_round_sends(
        dispatch, monkeypatch, caplog, shape, live, result, told_live):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", live)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", result)
    sends_per_beat = int(live) + int(result)
    fresh = [_snap(f"A{i:03d}", eps=0.60, eps_pct=46.34)
             for i in range(sender.MAX_SENDS_PER_ROUND // sends_per_beat)]
    behind = [_snap(f"Z{i:03d}", **shape) for i in range(3)]
    if told_live:
        _seed_done(behind, surprise=False)
    with caplog.at_level(logging.WARNING, logger=sender.__name__):
        await sender.dispatch_landings(fresh + behind, NOW)
    assert len(dispatch.sent) == sender.MAX_SENDS_PER_ROUND
    lines = _warnings(caplog)
    assert len(lines) <= 1
    await sender.dispatch_landings(fresh + behind, NOW)
    next_round = {s["ticker"] for s in dispatch.sent[sender.MAX_SENDS_PER_ROUND:]}
    assert (_owed(lines[0]) if lines else 0) == len(next_round)


# ── the result window ────────────────────────────────────────────────────────


def _beat(day: date, symbol: str = "INTC") -> LiveSnapshot:
    return replace(_snap(symbol, eps=0.60, eps_pct=46.34, rev=17.1e9), day=day)


def _noon(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12, tzinfo=ET)


@pytest.mark.asyncio
@pytest.mark.parametrize("report,today,told", [
    (FRIDAY, FRIDAY, True),
    (FRIDAY, SATURDAY, True),
    (FRIDAY, SUNDAY, False),
    (FRIDAY, MONDAY, False),
    (SATURDAY, SUNDAY, True),
    (SATURDAY, MONDAY, False),
    (SUNDAY, MONDAY, True),
], ids=["same_day", "fri_sat", "fri_sun", "fri_mon", "sat_sun", "sat_mon", "sun_mon"])
async def test_a_result_push_needs_a_report_from_today_or_calendar_yesterday(
        dispatch, report, today, told):
    await sender.dispatch_landings([_beat(report)], _noon(today))
    expected = [KIND_EARNINGS_LIVE, KIND_EARNINGS_RESULT] if told else []
    assert [s["kind"] for s in dispatch.sent] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("days_old", [0, 1, 2, 3, 6])
async def test_the_live_sender_reads_the_daily_passs_window(dispatch, days_old):
    """One rule, not a copy of it: the two senders agree on every age."""
    assert sender.in_result_window is daily.in_result_window
    today = date(2026, 11, 11)
    report = today - timedelta(days=days_old)
    row = {"symbol": "INTC", "date": report.isoformat(), "epsEstimated": 0.41, "epsActual": 0.60}
    await sender.dispatch_landings([_beat(report)], _noon(today))
    assert bool(dispatch.sent) == bool(daily.select_results([row], today))


@pytest.mark.asyncio
@pytest.mark.parametrize("now,told", [
    (datetime(2026, 11, 8, 4, 59, tzinfo=timezone.utc), True),    # Saturday 23:59 ET
    (datetime(2026, 11, 8, 5, 0, tzinfo=timezone.utc), False),    # Sunday 00:00 ET
], ids=["saturday_night_et", "sunday_midnight_et"])
async def test_the_window_turns_over_at_midnight_et_not_utc(dispatch, now, told):
    await sender.dispatch_landings([_beat(FRIDAY)], now)
    assert bool(dispatch.sent) is told


@pytest.mark.asyncio
async def test_a_report_outside_the_window_is_handled_once_and_for_good(dispatch, caplog):
    snap = _beat(FRIDAY)
    with caplog.at_level(logging.INFO, logger=sender.__name__):
        for minute in range(3):
            await sender.dispatch_landings([snap], _noon(SUNDAY) + timedelta(minutes=minute))
    assert dispatch.sent == []
    assert ("INTC", FRIDAY) in sender._live_done and ("INTC", FRIDAY) in sender._surprise_done
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1 and "INTC" in lines[0] and FRIDAY.isoformat() in lines[0]


@pytest.mark.asyncio
async def test_a_report_that_leaves_the_window_gets_no_second_kind(dispatch):
    """The filing's figure on Saturday told the live kind. FMP's arrives on Sunday: too old for
    the result kind, whichever kind it would have been."""
    filed = replace(_snap(eps=0.60, eps_source=SOURCE_FILING, eps_pct=46.34, preliminary=True),
                    day=FRIDAY)
    await sender.dispatch_landings([filed], _noon(SATURDAY))
    assert [s["kind"] for s in dispatch.sent] == [KIND_EARNINGS_LIVE]
    await sender.dispatch_landings([_beat(FRIDAY)], _noon(SUNDAY))
    assert [s["kind"] for s in dispatch.sent] == [KIND_EARNINGS_LIVE]
    assert ("INTC", FRIDAY) in sender._surprise_done


@pytest.mark.asyncio
async def test_the_window_line_does_not_deny_a_live_push_the_report_already_got(dispatch, caplog):
    filed = replace(_snap(eps=0.60, eps_source=SOURCE_FILING, eps_pct=46.34, preliminary=True),
                    day=FRIDAY)
    await sender.dispatch_landings([filed], _noon(SATURDAY))
    with caplog.at_level(logging.INFO, logger=sender.__name__):
        await sender.dispatch_landings([_beat(FRIDAY)], _noon(SUNDAY))
    assert [s["kind"] for s in dispatch.sent] == [KIND_EARNINGS_LIVE]
    (line,) = [r.getMessage() for r in caplog.records if "older than yesterday" in r.getMessage()]
    assert "no push" not in line
    assert "nothing further will be sent" in line and "its card and sheet stay" in line


@pytest.mark.asyncio
async def test_a_report_held_back_by_the_cap_past_midnight_is_not_told(dispatch):
    """The cap defers to the next round, and that round can be the next day."""
    fresh = [_beat(FRIDAY, f"A{i:03d}") for i in range(31)]
    late = _noon(SATURDAY).replace(hour=23, minute=59)
    await sender.dispatch_landings(fresh, late)
    assert len(dispatch.sent) == 60
    await sender.dispatch_landings(fresh, _noon(SUNDAY))
    assert len(dispatch.sent) == 60, "the last report has left the window"


@pytest.mark.asyncio
async def test_the_oldest_report_goes_first_so_the_cap_cannot_run_it_out_of_the_window(
        dispatch, monkeypatch):
    """Saturday 23:59 ET is a Friday report's last round. A Saturday report the cap holds back
    still has all of Sunday, so every report is told. Within a day, symbol order decides, not
    the order they were handed over."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    saturday = [_beat(SATURDAY, f"A{i:03d}") for i in reversed(range(sender.MAX_SENDS_PER_ROUND))]
    reports = saturday + [_beat(FRIDAY, "ZZZ")]
    await sender.dispatch_landings(reports, _noon(SATURDAY).replace(hour=23, minute=59))
    assert len(dispatch.sent) == sender.MAX_SENDS_PER_ROUND
    told = {s["ticker"] for s in dispatch.sent}
    assert "ZZZ" in told
    assert saturday[0].symbol not in told, "the last symbol of the newest day is the one to wait"
    await sender.dispatch_landings(reports, _noon(SUNDAY).replace(hour=0))
    assert {s["ticker"] for s in dispatch.sent} == {s.symbol for s in reports}


@pytest.mark.asyncio
async def test_reports_outside_the_window_use_none_of_the_round_cap(dispatch, caplog):
    old = [_beat(FRIDAY, f"A{i:03d}") for i in range(sender.MAX_SENDS_PER_ROUND + 1)]
    with caplog.at_level(logging.WARNING, logger=sender.__name__):
        await sender.dispatch_landings(old + [_beat(MONDAY, "ZM")], _noon(MONDAY))
    assert {s["ticker"] for s in dispatch.sent} == {"ZM"}
    assert _warnings(caplog) == []


@pytest.mark.asyncio
async def test_the_memory_of_a_report_outside_the_window_stays_bounded(dispatch):
    await sender.dispatch_landings([_beat(FRIDAY)], _noon(MONDAY))
    await sender.dispatch_landings([], _noon(TUESDAY))
    assert not sender._live_done and not sender._surprise_done


# ── a failed watcher read ────────────────────────────────────────────────────


class _NobodyTold:
    """`notification_events`, read for who holds a neighbouring key: nobody here."""

    def select(self, *_):
        return self

    def in_(self, *_):
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class _Watchlist:
    """`watchlist_items`. The page reads numbered in `failing` raise, as an edge 520 does."""

    def __init__(self, failing=(), users=("u1",)):
        self.failing, self.users, self.reads = set(failing), users, 0

    def table(self, name):
        return self if name == "watchlist_items" else _NobodyTold()

    def select(self, *_):
        return self

    def eq(self, *_):
        return self

    def order(self, *_, **__):
        return self

    def range(self, *_):
        return self

    def execute(self):
        self.reads += 1
        if self.reads in self.failing:
            raise RuntimeError("520 from the edge")
        return SimpleNamespace(data=[{"user_id": u} for u in self.users])


def _real_dispatcher(monkeypatch, failing=(), *, users=("u1",)):
    """The real notify_watchers, watchers_of and fetch_all_rows: only the table and the fan-out
    are fake, so nothing here can pass on a stub's idea of what a failed read does."""
    svc = object.__new__(pds.PushDispatchService)
    svc.supabase = _Watchlist(failing, users)
    svc.sent = []

    async def notify_users(users, *, kind, **_kw):
        svc.sent.append(kind)
        return len(users)

    svc.notify_users = notify_users
    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: svc)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", True)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", True)
    sender.reset()
    return svc


@pytest.fixture
def clean_sender():
    sender.reset()
    yield
    sender.reset()


def _big(symbol: str = "INTC") -> LiveSnapshot:
    return _snap(symbol, eps=0.60, eps_pct=46.34, rev=17.1e9, rev_pct=3.01)


BOTH = [KIND_EARNINGS_LIVE, KIND_EARNINGS_RESULT]


@pytest.mark.asyncio
async def test_a_failed_watcher_read_does_not_use_up_the_reports_push(monkeypatch, clean_sender):
    svc = _real_dispatcher(monkeypatch, failing=[1])
    await sender.dispatch_landings([_big()], NOW)
    assert svc.sent == [], "the result push must not run ahead of a live push still owed"
    await sender.dispatch_landings([_big()], NOW)
    assert svc.sent == BOTH
    await sender.dispatch_landings([_big()], NOW)
    assert len(svc.sent) == 2, "once delivered, once per process again"


@pytest.mark.asyncio
@pytest.mark.parametrize("live_on,kinds", [(False, [KIND_EARNINGS_RESULT]), (True, BOTH)],
                         ids=["result_kind_alone", "both_kinds"])
async def test_a_failed_watcher_read_ends_the_round_and_the_next_round_retries(
        monkeypatch, clean_sender, live_on, kinds):
    svc = _real_dispatcher(monkeypatch, failing=[1])
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", live_on)
    await sender.dispatch_landings([_big("AAA"), _big("BBB")], NOW)
    assert svc.sent == [] and svc.supabase.reads == 1, "one failed read, not one per report"
    for _ in range(3):
        await sender.dispatch_landings([_big("AAA")], NOW)
    assert svc.sent == kinds


@pytest.mark.asyncio
async def test_an_outage_costs_one_read_a_round_and_everyone_is_told_once_after_it(
        monkeypatch, clean_sender):
    svc = _real_dispatcher(monkeypatch, failing=range(1, 6))
    reports = [_big("AAA"), _big("BBB")]
    for round_number in range(1, 6):
        await sender.dispatch_landings(reports, NOW)
        assert (svc.sent, svc.supabase.reads) == ([], round_number)
    for _ in range(3):
        await sender.dispatch_landings(reports, NOW)
    assert svc.sent == BOTH + BOTH


@pytest.mark.asyncio
async def test_a_failed_read_behind_a_sent_live_push_retries_only_the_result(
        monkeypatch, clean_sender):
    svc = _real_dispatcher(monkeypatch, failing=[2])
    stats = await sender.dispatch_landings([_big()], NOW)
    assert svc.sent == [KIND_EARNINGS_LIVE] and stats == {"live": 1, "result": 0}
    stats = await sender.dispatch_landings([_big()], NOW)
    assert svc.sent == BOTH and stats == {"live": 0, "result": 1}
    await sender.dispatch_landings([_big()], NOW)
    assert svc.sent == BOTH and svc.supabase.reads == 3


@pytest.mark.asyncio
async def test_the_reports_ahead_of_a_failed_read_keep_what_they_got(monkeypatch, clean_sender):
    svc = _real_dispatcher(monkeypatch, failing=[3])
    reports = [_big("AAA"), _big("BBB"), _big("CCC")]
    await sender.dispatch_landings(reports, NOW)
    assert svc.sent == BOTH and svc.supabase.reads == 3, "BBB failed, CCC was never read"
    await sender.dispatch_landings(reports, NOW)
    assert svc.sent == BOTH * 3


@pytest.mark.asyncio
async def test_a_read_that_fails_on_a_later_page_tells_nobody_until_it_is_whole(
        monkeypatch, clean_sender):
    svc = _real_dispatcher(monkeypatch, failing=[2], users=[f"u{i}" for i in range(1000)])
    await sender.dispatch_landings([_big()], NOW)
    assert svc.sent == [] and svc.supabase.reads == 2
    await sender.dispatch_landings([_big()], NOW)
    assert svc.sent == BOTH


@pytest.mark.asyncio
async def test_a_failed_read_says_which_report_went_back_and_what_is_still_owed(
        monkeypatch, clean_sender, caplog):
    _real_dispatcher(monkeypatch, failing=[1])
    with caplog.at_level(logging.WARNING, logger=sender.logger.name):
        await sender.dispatch_landings([_big("AAA"), _big("BBB"), _big("CCC")], NOW)
    (line,) = _warnings(caplog)
    assert "AAA" in line and REPORT.isoformat() in line and "520 from the edge" in line
    assert "RuntimeError" in line and "3 report(s) still owed" in line


@pytest.mark.asyncio
async def test_nobody_watching_is_not_a_failed_read(monkeypatch, clean_sender):
    svc = _real_dispatcher(monkeypatch, users=())
    for _ in range(3):
        await sender.dispatch_landings([_big()], NOW)
    assert svc.sent == [] and svc.supabase.reads == 2, "one read per kind, and the report is done"


@pytest.mark.asyncio
async def test_any_other_failure_keeps_the_key_so_a_bug_cannot_retry_every_minute(
        monkeypatch, clean_sender):
    calls = []

    class _Broken:
        async def notify_watchers(self, **kw):
            calls.append(kw["kind"])
            raise ValueError("a bug, not an outage")

    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: _Broken())
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", True)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", True)
    for _ in range(3):
        await sender.dispatch_landings([_big()], NOW)
    assert calls == BOTH, "tried once, and the round went on past it"


@pytest.mark.asyncio
async def test_a_report_still_owed_when_its_window_closes_is_let_go(monkeypatch, clean_sender):
    svc = _real_dispatcher(monkeypatch, failing=[1])
    await sender.dispatch_landings([_beat(FRIDAY)], _noon(FRIDAY))
    await sender.dispatch_landings([_beat(FRIDAY)], _noon(MONDAY))
    assert svc.sent == [] and svc.supabase.reads == 1


class _TickerDown(_Watchlist):
    """`watchlist_items`, with every read of the tickers in `down` failing as an edge 520 does."""

    def __init__(self, *down):
        super().__init__()
        self.down, self.ticker, self.failed = set(down), None, []

    def eq(self, _column, ticker):
        self.ticker = ticker
        return self

    def execute(self):
        if self.ticker in self.down:
            self.failed.append(self.ticker)
            raise RuntimeError("520 from the edge")
        return super().execute()


def _with_tickers_down(monkeypatch, *down):
    """The real dispatcher, with what each send was keyed on, in order, in `svc.keys`."""
    svc = _real_dispatcher(monkeypatch)
    svc.supabase = _TickerDown(*down)
    svc.keys = []

    async def notify_users(users, *, kind, dedup_key, **_kw):
        svc.sent.append(kind)
        svc.keys.append(dedup_key)
        return len(users)

    svc.notify_users = notify_users
    return svc


def _told(svc) -> List[str]:
    """The tickers told, in the order each was first told."""
    return list(dict.fromkeys(key.split(":")[1] for key in svc.keys))


@pytest.mark.asyncio
async def test_a_ticker_whose_read_always_fails_holds_back_no_report_behind_it(
        monkeypatch, clean_sender):
    svc = _with_tickers_down(monkeypatch, "AAA")
    reports = [_big("AAA"), _big("BBB"), _big("CCC")]
    await sender.dispatch_landings(reports, NOW)
    assert svc.keys == [] and svc.supabase.failed == ["AAA"]
    await sender.dispatch_landings(reports, NOW)
    assert _told(svc) == ["BBB", "CCC"] and svc.sent == BOTH * 2
    assert svc.supabase.failed == ["AAA", "AAA"], "one failed read a round, behind the rest"


@pytest.mark.asyncio
async def test_a_ticker_down_costs_one_failed_read_a_round_for_as_long_as_it_lasts(
        monkeypatch, clean_sender):
    svc = _with_tickers_down(monkeypatch, "AAA")
    reports = [_big("AAA"), _big("BBB")]
    for round_number in range(1, 8):
        await sender.dispatch_landings(reports, NOW)
        assert len(svc.supabase.failed) == round_number
    assert _told(svc) == ["BBB"]
    svc.supabase.down.clear()
    await sender.dispatch_landings(reports, NOW)
    assert _told(svc) == ["BBB", "AAA"] and svc.sent == BOTH * 2
    await sender.dispatch_landings(reports, NOW)
    assert svc.sent == BOTH * 2, "once delivered, once per process again"


@pytest.mark.asyncio
async def test_two_tickers_down_take_turns_and_neither_starves_the_other(
        monkeypatch, clean_sender):
    svc = _with_tickers_down(monkeypatch, "AAA", "BBB")
    reports = [_big("AAA"), _big("BBB"), _big("CCC")]
    for _ in range(5):
        await sender.dispatch_landings(reports, NOW)
    assert svc.supabase.failed == ["AAA", "BBB", "AAA", "BBB", "AAA"]
    assert _told(svc) == ["CCC"]
    svc.supabase.down.discard("BBB")
    await sender.dispatch_landings(reports, NOW)
    assert _told(svc) == ["CCC", "BBB"], "the one that went back longest ago reads first"
    assert svc.supabase.failed[-1] == "AAA" and len(svc.supabase.failed) == 6


@pytest.mark.asyncio
async def test_a_failed_read_of_the_result_kind_sends_the_report_back_too(
        monkeypatch, clean_sender):
    svc = _real_dispatcher(monkeypatch, failing=[2])
    reports = [_big("AAA"), _big("BBB")]
    await sender.dispatch_landings(reports, NOW)
    assert svc.sent == [KIND_EARNINGS_LIVE]
    await sender.dispatch_landings(reports, NOW)
    assert svc.sent == [KIND_EARNINGS_LIVE, KIND_EARNINGS_LIVE, KIND_EARNINGS_RESULT,
                        KIND_EARNINGS_RESULT], "BBB goes whole, then AAA's result"


@pytest.mark.asyncio
async def test_a_report_dated_twice_keeps_its_first_date_through_a_failed_read(
        monkeypatch, clean_sender):
    """The read is the ticker's, so both rows go back together. Sent back alone, the first row
    would land behind the leftover row, and the one push would carry the later date."""
    svc = _ledger_dispatcher(monkeypatch)
    svc.supabase.failing_reads = 1
    reports = [replace(_big(), day=REPORT - timedelta(days=1)), _big(), _big("ZZZ")]
    await sender.dispatch_landings(reports, NOW)
    assert svc.supabase.rows == []
    await sender.dispatch_landings(reports, NOW)
    assert svc.supabase.told == {("u1", "earnings_result:INTC:2026-10-28"),
                                 ("u1", "earnings_result:ZZZ:2026-10-29")}


@pytest.mark.asyncio
async def test_a_read_that_works_puts_the_ticker_back_in_its_place(monkeypatch, clean_sender):
    """Here the live kind heals and the result kind is still to come, so the report stays open."""
    svc = _with_tickers_down(monkeypatch, "AAA")
    filed = _snap("AAA", rev=17.1e9, rev_source=SOURCE_FILING, preliminary=True)
    await sender.dispatch_landings([filed, _big("BBB")], NOW)
    svc.supabase.down.clear()
    await sender.dispatch_landings([filed, _big("BBB")], NOW)
    assert _told(svc) == ["BBB", "AAA"]
    svc.keys.clear()
    await sender.dispatch_landings([_big("AAA"), _big("CCC")], NOW)
    assert _told(svc) == ["AAA", "CCC"], "date order again, then the symbol"


@pytest.mark.asyncio
async def test_a_ticker_down_on_the_older_date_holds_back_no_report_dated_after_it(
        monkeypatch, clean_sender):
    """Yesterday's after-close report beside today's is the likeliest shape of this. Date order
    alone would read that ticker first on every round and tell nobody else."""
    svc = _with_tickers_down(monkeypatch, "AAA")
    reports = [replace(_big("AAA"), day=REPORT - timedelta(days=1)), _big("BBB"), _big("CCC")]
    await sender.dispatch_landings(reports, NOW)
    assert svc.keys == [] and svc.supabase.failed == ["AAA"]
    await sender.dispatch_landings(reports, NOW)
    assert _told(svc) == ["BBB", "CCC"] and svc.sent == BOTH * 2
    assert svc.supabase.failed == ["AAA", "AAA"], "one failed read a round, behind the rest"


def _waiting_on_fmp(symbol: str) -> LiveSnapshot:
    """A filing's revenue alone: the report stays open for FMP's EPS once its live kind is done,
    so only a read that works can clear its ticker from the order."""
    return _snap(symbol, rev=17.1e9, rev_source=SOURCE_FILING, preliminary=True)


@pytest.mark.asyncio
async def test_a_read_that_finds_nobody_watching_still_puts_the_ticker_back(
        monkeypatch, clean_sender):
    svc = _with_tickers_down(monkeypatch, "AAA")
    await sender.dispatch_landings([_waiting_on_fmp("AAA")], NOW)
    assert list(sender._went_back) == ["AAA"]
    svc.supabase.down.clear()
    svc.supabase.users = ()
    await sender.dispatch_landings([_waiting_on_fmp("AAA")], NOW)
    assert svc.keys == [] and sender._went_back == {}


@pytest.mark.asyncio
async def test_a_result_read_that_works_puts_the_ticker_back_too(monkeypatch, clean_sender):
    """The result kind alone, as shipped, with the ticker's second dated row still open."""
    svc = _with_tickers_down(monkeypatch, "AAA")
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    reports = [replace(_big("AAA"), day=REPORT - timedelta(days=1)), _waiting_on_fmp("AAA")]
    await sender.dispatch_landings(reports, NOW)
    assert list(sender._went_back) == ["AAA"]
    svc.supabase.down.clear()
    await sender.dispatch_landings(reports, NOW)
    assert svc.sent == [KIND_EARNINGS_RESULT] and sender._went_back == {}


@pytest.mark.asyncio
async def test_a_report_sent_back_still_goes_out_behind_a_full_cap(monkeypatch, clean_sender):
    monkeypatch.setattr(sender, "MAX_SENDS_PER_ROUND", 2)
    svc = _with_tickers_down(monkeypatch, "AAA")
    reports = [_big(symbol) for symbol in ("AAA", "BBB", "CCC", "DDD")]
    await sender.dispatch_landings(reports, NOW)
    svc.supabase.down.clear()
    for _ in range(4):
        await sender.dispatch_landings(reports, NOW)
    assert _told(svc) == ["BBB", "CCC", "DDD", "AAA"]


@pytest.mark.asyncio
async def test_a_cap_that_stops_the_round_leaves_the_back_of_the_queue_unread(
        monkeypatch, clean_sender):
    monkeypatch.setattr(sender, "MAX_SENDS_PER_ROUND", 2)
    svc = _with_tickers_down(monkeypatch, "AAA")
    reports = [_big("AAA"), _big("BBB"), _big("CCC")]
    await sender.dispatch_landings(reports, NOW)
    await sender.dispatch_landings(reports, NOW)
    assert _told(svc) == ["BBB"] and svc.supabase.failed == ["AAA"]


@pytest.mark.asyncio
async def test_the_order_forgets_a_ticker_done_while_another_report_stays_open(
        monkeypatch, clean_sender):
    _with_tickers_down(monkeypatch, "AAA")
    waiting = _waiting_on_fmp("BBB")
    await sender.dispatch_landings([_big("AAA"), waiting], NOW)
    assert list(sender._went_back) == ["AAA"]
    await sender.dispatch_landings([waiting], NOW)
    assert sender._went_back == {}


@pytest.mark.asyncio
async def test_the_order_forgets_a_ticker_with_nothing_left_to_decide(monkeypatch, clean_sender):
    _with_tickers_down(monkeypatch, "AAA")
    await sender.dispatch_landings([_big("AAA")], NOW)
    assert list(sender._went_back) == ["AAA"]
    await sender.dispatch_landings([], NOW)
    assert sender._went_back == {}


@pytest.mark.asyncio
async def test_the_order_forgets_a_report_that_leaves_the_window(monkeypatch, clean_sender):
    _with_tickers_down(monkeypatch, "INTC")
    await sender.dispatch_landings([_beat(FRIDAY)], _noon(FRIDAY))
    assert list(sender._went_back) == ["INTC"]
    await sender.dispatch_landings([_beat(FRIDAY)], _noon(MONDAY))
    assert sender._went_back == {}


def test_reset_forgets_the_order_too():
    sender._went_back["AAA"] = 1
    sender.reset()
    assert sender._went_back == {}


@pytest.mark.asyncio
async def test_notify_watchers_raises_only_in_strict_mode(monkeypatch, clean_sender):
    svc = _real_dispatcher(monkeypatch, failing=[1, 2])
    kw = dict(ticker="INTC", title="t", body="b", dedup_key="k", kind=KIND_EARNINGS_LIVE)
    assert await svc.notify_watchers(**kw) == 0
    with pytest.raises(pds.WatcherLookupFailed) as failed:
        await svc.notify_watchers(**kw, strict=True)
    assert "INTC" in str(failed.value) and "RuntimeError" in str(failed.value)
    assert isinstance(failed.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_a_strict_read_that_works_answers_like_the_default(monkeypatch, clean_sender):
    svc = _real_dispatcher(monkeypatch, users=("u1", "u2", "u1"))
    kw = dict(ticker="INTC", title="t", body="b", dedup_key="k", kind=KIND_EARNINGS_LIVE)
    assert await svc.notify_watchers(**kw, strict=True) == await svc.notify_watchers(**kw) == 2


@pytest.mark.asyncio
async def test_the_default_path_still_reads_through_a_one_argument_watchers_of(
        monkeypatch, clean_sender):
    svc = _real_dispatcher(monkeypatch)
    svc.watchers_of = lambda ticker: ["u1", "u2"]
    kw = dict(ticker="INTC", title="t", body="b", dedup_key="k", kind=KIND_EARNINGS_LIVE)
    assert await svc.notify_watchers(**kw) == 2
    assert svc.supabase.reads == 0


def test_watchers_of_still_answers_a_failed_read_with_nobody(monkeypatch, caplog, clean_sender):
    svc = _real_dispatcher(monkeypatch, failing=[1])
    with caplog.at_level(logging.WARNING, logger=pds.logger.name):
        assert svc.watchers_of("INTC") == []
    assert "no alerts this cycle" in caplog.text and "INTC" in caplog.text
    assert svc.watchers_of("INTC") == ["u1"]


# ── copy ─────────────────────────────────────────────────────────────────────


# ── the night hold, end to end ───────────────────────────────────────────────
#
# Owner decision 2026-11-05, "Hold until 7 AM". The hold itself is `decide`'s, pinned in
# test_notification_dispatch.py. What is pinned here is that the rest of the path carries it:
# the real sender, notify_watchers, claim, flush and daily pass, on a fake ledger with the
# table's UNIQUE and the claim RPC's WHERE, and a clock the test moves.

FRI_0230 = datetime(2026, 10, 30, 2, 30, tzinfo=ET)     # INTC reported Thursday, after the close
FRI_0700 = datetime(2026, 10, 30, 7, 0, tzinfo=ET)
FRI_NOON = datetime(2026, 10, 30, 12, 0, tzinfo=ET)
FRI_1600 = datetime(2026, 10, 30, 16, 0, tzinfo=ET)
FRI_2330 = datetime(2026, 10, 30, 23, 30, tzinfo=ET)
SAT_0700 = datetime(2026, 10, 31, 7, 0, tzinfo=ET)
IN_NEW_YORK = {qh.PREF_TIMEZONE: "America/New_York"}


class _Duplicate(Exception):
    code = "23505"


class _Update:
    """An UPDATE, or with no patch a DELETE, over the rows its filters match."""

    def __init__(self, rows, patch=None):
        self.rows, self.patch, self.filters = rows, patch, {}
        self.nulls, self.within, self.unlike = set(), {}, {}

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def neq(self, column, value):
        self.unlike[column] = value
        return self

    def is_(self, column, value):
        """Only `is.null`, as the inbox's `mark_read` sends it."""
        assert value == "null", value
        self.nulls.add(column)
        return self

    def in_(self, column, values):
        self.within[column] = set(values)
        return self

    def execute(self):
        hit = [row for row in self.rows
               if all(row.get(c) == v for c, v in self.filters.items())
               and all(row.get(c) != v for c, v in self.unlike.items())
               and all(row.get(c) is None for c in self.nulls)
               and all(row.get(c) in v for c, v in self.within.items())]
        if self.patch is None:
            self.rows[:] = [row for row in self.rows if not any(row is h for h in hit)]
        else:
            for row in hit:
                row.update(self.patch)
        return SimpleNamespace(data=[dict(row) for row in hit])


class _HoldersRead:
    """`select("user_id").in_(...).in_(...)` on `notification_events`, every state counted."""

    def __init__(self, rows):
        self.rows, self.wanted = rows, {}

    def in_(self, column, values):
        self.wanted[column] = set(values)
        return self

    def eq(self, column, value):
        self.wanted[column] = {value}
        return self

    def execute(self):
        return SimpleNamespace(data=[
            {"user_id": r["user_id"]} for r in self.rows
            if all(r.get(c) in v for c, v in self.wanted.items())
        ])


class _Ledger:
    """`notification_events`: UNIQUE (user_id, dedup_key) and `claim_due_notifications`
    (migration 119), so the real claim, stamp and flush run against them."""

    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    def table(self, name):
        assert name == pds.TABLE
        return self

    def insert(self, row):
        def execute():
            if any((r["user_id"], r["dedup_key"]) == (row["user_id"], row["dedup_key"])
                   for r in self.rows):
                raise _Duplicate("duplicate key value violates unique constraint")
            self.rows.append({"attempts": 0, **row})
        return SimpleNamespace(execute=execute)

    def update(self, patch):
        return _Update(self.rows, patch)

    def delete(self):
        return _Update(self.rows)

    def select(self, *_):
        """Who holds a key (`PushDispatchService._holders_of`): the unique index, read as such."""
        return _HoldersRead(self.rows)

    def rpc(self, name, params):
        assert name == "claim_due_notifications"
        due = [r for r in self.rows
               if r["push_state"] == "deferred" and r.get("deliver_after")
               and _stamp(r["deliver_after"]) <= _stamp(params["p_now"])]
        due = sorted(due, key=lambda r: _stamp(r["deliver_after"]))[:max(params["p_limit"], 0)]
        for row in due:
            row.update(push_state="pending", attempts=row["attempts"] + 1)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[dict(r) for r in due]))

    def sent_counts(self, ids, category, cutoffs):
        return {uid: sum(1 for r in self.rows
                         if r["user_id"] == uid and r["category"] == category
                         and r.get("sent_at") and _stamp(r["sent_at"]) >= cutoffs[uid])
                for uid in ids}

    def of(self, user):
        return [r for r in self.rows if r["user_id"] == user]


def _stamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


class _Phones:
    enabled = True

    def __init__(self):
        self.buzzes: List[Dict[str, Any]] = []

    async def send_to_user(self, user_id, **kw):
        self.buzzes.append({"user_id": user_id, **kw})
        return PushOutcome(attempted=1, accepted=1)

    def of(self, user):
        return [b for b in self.buzzes if b["user_id"] == user]


def _clock_at(monkeypatch, instant: datetime) -> datetime:
    """Move the dispatcher's clock. `notify_watchers` and the flush read the time themselves, so
    without this these tests would pass or fail by the hour they run."""
    class _Moved(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz or timezone.utc)

    monkeypatch.setattr(pds, "datetime", _Moved)
    return instant


def _dispatcher_over(monkeypatch, ledger, phones, *, prefs=None, users=("u1",)):
    """The real dispatcher. Only the reads that are not the point here are fixed: the preferences
    blob of each user (never set means `{}`), one device each, and an unread count of nothing."""
    blobs = prefs if prefs is not None else {u: dict(IN_NEW_YORK) for u in users}
    svc = object.__new__(pds.PushDispatchService)
    svc.supabase, svc._push = ledger, phones
    svc._read_watchers = lambda ticker: list(users)
    svc._preferences_bulk = lambda ids: {u: blobs.get(u, {}) for u in ids}
    svc._devices_bulk = lambda ids: {u: [{"token": f"t-{u}", "environment": "sandbox"}] for u in ids}
    svc.unread_counts_bulk = lambda ids: {u: 0 for u in ids}
    svc._category_counts_bulk = ledger.sent_counts
    svc.resolve_route_asset_type = lambda ticker, current: None
    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: svc)
    monkeypatch.setattr(daily, "get_push_dispatch_service", lambda: svc)
    monkeypatch.setattr(settings, "PUSH_DRY_RUN", False)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", True)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", True)
    sender.reset()
    # A fresh process holds no record, and the wake reads the record: no other test's may leak in.
    get_earnings_live_service().reset()
    return svc


async def _daily_pass(monkeypatch, now, *, day=REPORT, symbol="INTC", row=None, rows=None):
    """The real 16:00 ET pass over one calendar row, by default a 46% beat, or over `rows` and
    the tickers they name."""
    @contextlib.asynccontextmanager
    async def claimed(_job):
        yield SimpleNamespace(success=False, notified=0)

    listed = rows or [row or {"symbol": symbol, "date": day.isoformat(), "time": "amc",
                              "epsEstimated": 0.41, "epsActual": 0.60}]
    watched = {listed_row["symbol"] for listed_row in rows} if rows else {symbol}

    async def calendar(from_date=None, to_date=None):
        return list(listed) if from_date == day.isoformat() else []

    monkeypatch.setattr(daily, "claimed_job", claimed)
    monkeypatch.setattr(daily, "get_fmp_client",
                        lambda: SimpleNamespace(get_earnings_calendar=calendar))
    monkeypatch.setattr(daily, "_watched_tickers", lambda: watched)
    return await daily.run_earnings_notifications(now=now)


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True], ids=["same_process", "restart_in_the_night"])
async def test_a_night_landing_buzzes_once_at_7_and_nobody_is_told_twice(
        monkeypatch, clean_sender, restart):
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)

    night = _clock_at(monkeypatch, FRI_0230)
    await sender.dispatch_landings([_big()], night)

    assert phones.buzzes == [], "a default-settings watcher must not be buzzed at 02:30"
    (row,) = ledger.rows
    assert (row["kind"], row["push_state"]) == (KIND_EARNINGS_LIVE, "deferred"), (
        "the live kind claims the key at once, so the inbox has the row and the second kind "
        "finds it taken"
    )
    assert _stamp(row["deliver_after"]) == FRI_0700
    assert row["category"] == "earnings", "the wake's cap read counts this category alone"
    assert (row["route"]["ticker"], row["route"]["report_date"]) == ("INTC", "2026-10-29")

    if restart:
        svc = _dispatcher_over(monkeypatch, ledger, phones)

    _clock_at(monkeypatch, FRI_0700 - timedelta(seconds=1))
    assert (await svc.flush_deferred())["claimed"] == 0
    assert phones.buzzes == []

    _clock_at(monkeypatch, FRI_0700)
    stats = await svc.flush_deferred()
    assert (stats["sent"], stats["failed"], stats["stale"]) == (1, 0, 0)
    (buzz,) = phones.buzzes
    assert buzz["user_id"] == "u1" and buzz["data"]["kind"] == KIND_EARNINGS_LIVE
    assert buzz["title"] == "INTC results are out"
    assert row["push_state"] == "sent" and row["sent_at"]
    assert buzz["data"]["dedup_key"] == row["dedup_key"]
    for key in ("ticker", "tab", "report_date"):
        assert buzz["data"][key] == row["route"][key], "a tap needs the row's own route"

    assert (await svc.flush_deferred())["claimed"] == 0, "a sent row must not be claimed again"
    _clock_at(monkeypatch, FRI_1600)
    await _daily_pass(monkeypatch, FRI_1600)
    await sender.dispatch_landings([_big()], FRI_1600)
    assert len(phones.buzzes) == 1 and len(ledger.rows) == 1


@pytest.mark.asyncio
async def test_with_live_results_switched_off_the_surprise_kind_is_what_waits(
        monkeypatch, clean_sender):
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)

    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, FRI_0230))
    assert phones.buzzes == [] and [r["kind"] for r in ledger.rows] == [KIND_EARNINGS_RESULT]

    _clock_at(monkeypatch, FRI_0700)
    await svc.flush_deferred()
    (buzz,) = phones.buzzes
    assert buzz["data"]["kind"] == KIND_EARNINGS_RESULT
    assert buzz["body"].startswith("EPS beat consensus by 46%")


@pytest.mark.asyncio
async def test_a_result_after_22_00_for_a_report_already_told_stays_one_push(
        monkeypatch, clean_sender):
    """The filing's figure went out at 21:59. FMP's adjusted EPS arrives at 22:01, inside the
    hold: the parked claim hits the same key and leaves nothing to wake."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)

    thursday_2159 = _clock_at(monkeypatch, datetime(2026, 10, 29, 21, 59, tzinfo=ET))
    await sender.dispatch_landings([_snap(rev=17.1e9, rev_source=SOURCE_FILING)], thursday_2159)
    assert [b["data"]["kind"] for b in phones.buzzes] == [KIND_EARNINGS_LIVE]

    thursday_2201 = _clock_at(monkeypatch, datetime(2026, 10, 29, 22, 1, tzinfo=ET))
    await sender.dispatch_landings([_big()], thursday_2201)
    assert len(ledger.rows) == 1 and ledger.rows[0]["push_state"] == "sent"

    _clock_at(monkeypatch, FRI_0700)
    assert (await svc.flush_deferred())["claimed"] == 0
    assert len(phones.buzzes) == 1


@pytest.mark.asyncio
async def test_the_night_is_each_watchers_own_and_a_stated_off_is_kept(monkeypatch, clean_sender):
    ledger, phones = _Ledger(), _Phones()
    prefs = {
        "held": dict(IN_NEW_YORK),
        "said_off": {**IN_NEW_YORK, qh.PREF_QUIET_ENABLED: False},
        "tokyo": {qh.PREF_TIMEZONE: "Asia/Tokyo"},      # 15:30 there
        "unknown_zone": {},                             # falls back to ET
    }
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=prefs, users=tuple(prefs))

    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, FRI_0230))

    assert sorted(b["user_id"] for b in phones.buzzes) == ["said_off", "tokyo"]
    assert {u: ledger.of(u)[0]["push_state"] for u in prefs} == {
        "held": "deferred", "said_off": "sent", "tokyo": "sent", "unknown_zone": "deferred",
    }
    assert all(len(ledger.of(u)) == 1 for u in prefs)

    _clock_at(monkeypatch, FRI_0700)
    await svc.flush_deferred()
    assert sorted(b["user_id"] for b in phones.buzzes) == sorted(prefs)


@pytest.mark.asyncio
async def test_a_row_already_held_outlives_the_older_than_yesterday_rule(
        monkeypatch, clean_sender, live):
    """Thursday's report lands in Friday's night, inside the window. At Saturday's 07:00 wake it
    is two days old: too old to START a push, and nothing may take back one already owed, or
    the screen it opens."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)

    landed = _lands(live, FRI_2330)
    await sender.dispatch_landings(landed, _clock_at(monkeypatch, FRI_2330))
    (row,) = ledger.rows
    assert row["push_state"] == "deferred" and _stamp(row["deliver_after"]) == SAT_0700

    _clock_at(monkeypatch, SAT_0700)
    assert daily.in_result_window(REPORT, SAT_0700.date()) is False, "the case is not the case"
    # The tick hands the report over again after a restart, and the window closes it for good.
    sender.reset()
    await sender.dispatch_landings([_big()], SAT_0700)
    assert row["push_state"] == "deferred" and phones.buzzes == []

    stats = await svc.flush_deferred()
    assert (stats["sent"], stats["suppressed"], stats["stale"]) == (1, 0, 0)
    assert len(phones.buzzes) == 1 and len(ledger.rows) == 1
    await live.refresh(SAT_0700, fmp=_Calendar(), symbols=["INTC"])
    answer = await _tap(monkeypatch, phones.buzzes[0]["data"], SAT_0700)
    assert (answer.status, answer.eps.actual, answer.eps.surprise_pct) == (
        "reported", 0.60, 46.34)


@pytest.mark.asyncio
async def test_the_16_00_et_pass_tells_each_watcher_on_their_own_clock(monkeypatch, clean_sender):
    """The pass runs at one instant: the afternoon in New York, 05:00 the next day in Tokyo and
    22:00 in Helsinki. It tells the first now and holds the other two for their own 07:00."""
    ledger, phones = _Ledger(), _Phones()
    prefs = {
        "ny": dict(IN_NEW_YORK),
        "tokyo": {qh.PREF_TIMEZONE: "Asia/Tokyo"},
        "helsinki": {qh.PREF_TIMEZONE: "Europe/Helsinki"},
        "tokyo_said_off": {qh.PREF_TIMEZONE: "Asia/Tokyo", qh.PREF_QUIET_ENABLED: False},
    }
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=prefs, users=tuple(prefs))
    _flags_as_shipped(monkeypatch)

    pass_time = _clock_at(monkeypatch, datetime(2026, 10, 29, 16, 0, tzinfo=ET))
    stats = await _daily_pass(monkeypatch, pass_time)

    assert stats["results"] == 2, "the pass counts the buzzes it sent, never a parked row"
    assert sorted(b["user_id"] for b in phones.buzzes) == ["ny", "tokyo_said_off"]
    tokyo, helsinki = ledger.of("tokyo")[0], ledger.of("helsinki")[0]
    assert (tokyo["kind"], tokyo["push_state"]) == (KIND_EARNINGS_RESULT, "deferred")
    tokyo_wake = _stamp(tokyo["deliver_after"]).astimezone(ZoneInfo("Asia/Tokyo"))
    helsinki_wake = _stamp(helsinki["deliver_after"]).astimezone(ZoneInfo("Europe/Helsinki"))
    assert (tokyo_wake.date(), tokyo_wake.hour) == (date(2026, 10, 30), 7)
    assert (helsinki_wake.date(), helsinki_wake.hour) == (date(2026, 10, 30), 7)

    _clock_at(monkeypatch, _stamp(tokyo["deliver_after"]))
    await svc.flush_deferred()
    assert sorted(b["user_id"] for b in phones.buzzes) == ["ny", "tokyo", "tokyo_said_off"]

    _clock_at(monkeypatch, _stamp(helsinki["deliver_after"]))
    await svc.flush_deferred()
    assert len(phones.of("helsinki")) == 1 and len(phones.buzzes) == 4


@pytest.mark.asyncio
async def test_a_night_of_reports_still_stops_at_the_daily_cap(monkeypatch, clean_sender):
    """Parked rows have used none of the 4/day budget, so the wake is where the cap bites: four
    buzzes, and the rest stay readable in the inbox with the reason on the row."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    reports = [_big(f"T{i}") for i in range(6)]

    await sender.dispatch_landings(reports, _clock_at(monkeypatch, FRI_0230))
    assert len(ledger.rows) == 6 and phones.buzzes == []

    _clock_at(monkeypatch, FRI_0700)
    stats = await svc.flush_deferred()

    assert (stats["sent"], stats["suppressed"]) == (4, 2)
    assert len(phones.buzzes) == 4
    capped = [r for r in ledger.rows if r["push_state"] == "failed"]
    assert len(capped) == 2
    assert all("cap_reached:earnings:4" in r["last_error"] for r in capped)


TUE_1605 = datetime(2026, 11, 10, 16, 5, tzinfo=ET)     # 22:05 in Berlin, 06:05 Wednesday in Tokyo
WED_0700_BERLIN = datetime(2026, 11, 11, 7, 0, tzinfo=ZoneInfo("Europe/Berlin"))
IN_BERLIN = {"u1": {qh.PREF_TIMEZONE: "Europe/Berlin"}}


def _spent_today(ledger, at, *, user="u1", n=4):
    """The `n` earnings buzzes `user` had in the hours before `at`, the same day."""
    for i in range(n):
        ledger.rows.append({
            "user_id": user, "dedup_key": f"earlier:{i}", "kind": "earnings_upcoming",
            "category": "earnings", "push_state": "sent", "attempts": 0,
            "sent_at": (at - timedelta(hours=i + 1)).isoformat(),
        })


@pytest.mark.asyncio
async def test_a_result_held_past_midnight_meets_the_cap_of_the_morning_it_buzzes_on(
        monkeypatch, clean_sender):
    """Berlin's Tuesday already holds four earnings alerts when the 16:00 ET pass reaches it at
    22:05. The result buzzes at 07:00 on Wednesday, so Wednesday's budget is the one it spends: the
    row is written at once and buzzes then. Judged on Tuesday's, it got no row at all."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=IN_BERLIN)
    _flags_as_shipped(monkeypatch)
    _spent_today(ledger, TUE_1605)

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, TUE_1605), day=TUESDAY)
    (row,) = [r for r in ledger.rows if r["kind"] == KIND_EARNINGS_RESULT]
    assert (row["push_state"], _stamp(row["deliver_after"])) == ("deferred", WED_0700_BERLIN)
    assert phones.buzzes == []

    _clock_at(monkeypatch, WED_0700_BERLIN)
    assert (await svc.flush_deferred())["sent"] == 1
    assert [b["data"]["kind"] for b in phones.buzzes] == [KIND_EARNINGS_RESULT]
    assert row["push_state"] == "sent"


@pytest.mark.asyncio
async def test_a_live_landing_at_23_30_behind_a_full_day_still_buzzes_at_7(
        monkeypatch, clean_sender):
    """No daily pass stands behind the live kind: an in-line report dropped here was never told."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _spent_today(ledger, FRI_2330)

    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, FRI_2330))
    (row,) = [r for r in ledger.rows if r["kind"] == KIND_EARNINGS_LIVE]
    assert (row["push_state"], _stamp(row["deliver_after"])) == ("deferred", SAT_0700)

    _clock_at(monkeypatch, SAT_0700)
    assert (await svc.flush_deferred())["sent"] == 1
    assert len(phones.buzzes) == 1 and row["push_state"] == "sent"


@pytest.mark.asyncio
async def test_a_tokyo_evening_behind_a_full_day_buzzes_on_tokyos_next_morning(
        monkeypatch, clean_sender):
    """09:30 in New York is 22:30 in Tokyo, and the 07:00 wake there is still Friday in New York:
    the day it buzzes on is Tokyo's Saturday, with its four untouched."""
    tokyo = ZoneInfo("Asia/Tokyo")
    landing = datetime(2026, 10, 30, 9, 30, tzinfo=ET)
    wake = datetime(2026, 10, 31, 7, 0, tzinfo=tokyo)
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones,
                           prefs={"u1": {qh.PREF_TIMEZONE: "Asia/Tokyo"}})
    _spent_today(ledger, landing)

    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, landing))
    (row,) = [r for r in ledger.rows if r["kind"] == KIND_EARNINGS_LIVE]
    assert (row["push_state"], _stamp(row["deliver_after"])) == ("deferred", wake)

    _clock_at(monkeypatch, wake)
    assert (await svc.flush_deferred())["sent"] == 1
    assert len(phones.buzzes) == 1 and row["push_state"] == "sent"


@pytest.mark.asyncio
async def test_a_user_who_changed_timezone_is_held_once_more_and_then_told(
        monkeypatch, clean_sender):
    """The row was due at New York's 07:00. Its owner has since landed in Honolulu, where that is
    01:00, so it waits for their own morning. Once: asking again at every wake could park it for
    good, so the second wake delivers whatever the clock says there."""
    ledger, phones = _Ledger(), _Phones()
    prefs = {"u1": dict(IN_NEW_YORK)}
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=prefs)

    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, FRI_0230))
    prefs["u1"] = {qh.PREF_TIMEZONE: "Pacific/Honolulu"}

    _clock_at(monkeypatch, FRI_0700)
    stats = await svc.flush_deferred()
    (row,) = ledger.rows
    assert (stats["sent"], stats["requeued"]) == (0, 1) and phones.buzzes == []
    assert row["push_state"] == "deferred"
    wake = _stamp(row["deliver_after"])
    assert wake.astimezone(ZoneInfo("Pacific/Honolulu")).hour == 7

    # Off again: 07:00 in Honolulu is 02:00 in Tokyo's night, and that is one hold too many.
    prefs["u1"] = {qh.PREF_TIMEZONE: "Asia/Tokyo"}
    _clock_at(monkeypatch, wake)
    assert (await svc.flush_deferred())["sent"] == 1
    assert len(phones.buzzes) == 1 and row["push_state"] == "sent"


@pytest.mark.asyncio
async def test_a_daytime_landing_goes_out_at_once_as_before(monkeypatch, clean_sender):
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)

    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, FRI_NOON))
    (buzz,) = phones.buzzes
    assert buzz["data"]["kind"] == KIND_EARNINGS_LIVE
    assert [r["push_state"] for r in ledger.rows] == ["sent"]


# ── as shipped: the 16:00 ET pass is what tells a result ─────────────────────
#
# With both push switches off nothing is sent live, so the daily pass is what tells a result. It
# must not notice: it reads neither switch. `_PASS_SENDS` is what the pass sent for
# `_PASS_CALENDAR` before the result switch went off, word for word, and it stays the answer
# whichever way the switches stand.

THU_1700 = datetime(2026, 10, 29, 17, 0, tzinfo=ET)


def _stable_row(symbol: str, day: str, eps_estimate: float, revenue_estimate: float,
                eps: Any = None, revenue: Any = None) -> Dict[str, Any]:
    """A calendar row with the fields `/stable` returns and no others: there is no time of day
    (`earnings_window_service`), so the words the pass sends for this row are the words it can
    send in production."""
    return {"symbol": symbol, "date": day, "epsEstimated": eps_estimate, "epsActual": eps,
            "revenueEstimated": revenue_estimate, "revenueActual": revenue, "lastUpdated": day}


#: A Friday's calendar: tomorrow's heads-up, today's miss, yesterday's beat, a surprise under the
#: bar, and a beat nobody watches.
_PASS_CALENDAR = {
    "2026-10-31": [_stable_row("NVDA", "2026-10-31", 1.0, 60e9)],
    "2026-10-30": [_stable_row("MSFT", "2026-10-30", 2.0, 70e9, eps=1.5, revenue=68e9),
                   _stable_row("AAPL", "2026-10-30", 1.0, 90e9, eps=1.02, revenue=91e9)],
    "2026-10-29": [_stable_row("INTC", "2026-10-29", 0.41, 16.6e9, eps=0.60, revenue=17.1e9),
                   _stable_row("ZZZZ", "2026-10-29", 1.0, 1e9, eps=2.0, revenue=1.5e9)],
}


def _pass_send(symbol: str, day: str, kind: str, title: str, body: str) -> Dict[str, Any]:
    """A result names the keys of the same report under a neighbouring date, so one report is
    one push whichever row FMP dated it by (`neighbouring_result_keys`). Every send asks to hear
    about a failed read of who to tell (`strict`), so the pass can leave the day open for it."""
    held = daily.neighbouring_result_keys(symbol, day) if kind == "earnings_result" else []
    return {"ticker": symbol, "title": title, "body": body, "kind": kind,
            "dedup_key": f"{kind}:{symbol}:{day}", "unless_claimed": held, "strict": True,
            "data": {"route": "ticker", "ticker": symbol, "asset_type": "stock",
                     "tab": "financials", "report_date": day}}


_PASS_SENDS = [
    _pass_send("NVDA", "2026-10-31", "earnings_upcoming", "NVDA reports earnings",
               "NVDA is scheduled to report tomorrow."),
    _pass_send("MSFT", "2026-10-30", "earnings_result", "MSFT earnings are out",
               "EPS missed consensus by 25%. Tap for the full picture."),
    _pass_send("INTC", "2026-10-29", "earnings_result", "INTC earnings are out",
               "EPS beat consensus by 46%. Tap for the full picture."),
]


async def _pass_over_the_calendar(monkeypatch):
    """The real pass at Friday 16:00 ET over `_PASS_CALENDAR`: its counts and its claim."""
    run = SimpleNamespace(success=False, notified=0)

    @contextlib.asynccontextmanager
    async def claimed(_job):
        yield run

    async def calendar(from_date=None, to_date=None):
        return [dict(row) for row in _PASS_CALENDAR.get(from_date, [])]

    monkeypatch.setattr(daily, "claimed_job", claimed)
    monkeypatch.setattr(daily, "get_fmp_client",
                        lambda: SimpleNamespace(get_earnings_calendar=calendar))
    monkeypatch.setattr(daily, "_watched_tickers", lambda: {"NVDA", "MSFT", "AAPL", "INTC"})
    return await daily.run_earnings_notifications(now=FRI_1600), run


@pytest.mark.asyncio
@pytest.mark.parametrize("live_push", [False, True], ids=["live_off", "live_on"])
@pytest.mark.parametrize("result_push", [False, True], ids=["result_off", "result_on"])
async def test_the_16_00_pass_says_the_same_whichever_way_the_switches_stand(
        monkeypatch, clean_sender, live, live_push, result_push):
    """Every word, key and route it sends, and nothing else, and the same counts on the job's
    claim. Each send is `strict`: a failed read of who to tell ends that ticker's send, never the
    pass (`test_a_failed_read_of_who_to_tell_leaves_the_day_open_for_the_next_wake`)."""
    rec = _Dispatch()
    monkeypatch.setattr(daily, "get_push_dispatch_service", lambda: rec)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", live_push)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", result_push)

    stats, run = await _pass_over_the_calendar(monkeypatch)

    assert rec.sent == _PASS_SENDS
    assert stats == {"upcoming": 1, "results": 2, "symbols": 3}
    assert (run.success, run.notified) == (True, 3)


async def _senders_on_a_wake_at(monkeypatch, wake: datetime) -> List[str]:
    """One hourly wake of the REAL scheduler at `wake`, at the declared hours: which senders ran.
    Each stand-in is set before the clock moves, because that imports its module, which must not
    keep the moved clock."""
    import datetime as datetime_module

    import app.main as main_mod

    class _WokenOnce(BaseException):
        """Ends a scheduler that outlives every `Exception`."""

    class _Moved(datetime):
        @classmethod
        def now(cls, tz=None):
            return wake.astimezone(tz)

    async def _rest(seconds):
        if seconds == 3600:
            raise _WokenOnce

    ran: List[str] = []

    def _noted(name: str):
        async def _run():
            ran.append(name)
        return _run

    with monkeypatch.context() as m:
        for name in ("earnings", "smart_money", "profile_match"):
            m.setattr(f"app.services.notification_senders.{name}_sender.run_{name}_notifications",
                      _noted(name))
            hour = f"{name.upper()}_NOTIFY_HOUR_ET"
            m.setattr(settings, hour, Settings.model_fields[hour].default)
        m.setattr(main_mod, "asyncio",
                  SimpleNamespace(sleep=_rest, CancelledError=asyncio.CancelledError))
        m.setattr(datetime_module, "datetime", _Moved)
        with pytest.raises(_WokenOnce):
            await main_mod._run_scheduled_notification_senders()
    return ran


@pytest.mark.asyncio
@pytest.mark.parametrize("wake,ran", [
    (datetime(2026, 10, 30, 15, 59, tzinfo=ET), []),
    (datetime(2026, 10, 30, 16, 0, tzinfo=ET), ["earnings"]),
    (datetime(2026, 10, 30, 17, 59, tzinfo=ET), ["earnings"]),
], ids=["a_minute_before", "on_the_hour", "the_retry_wakes_after_it"])
async def test_the_pass_runs_from_16_00_et_and_not_before(monkeypatch, wake, ran):
    """The wake itself, not the setting's default: a scheduler reading another hour, or an
    off-by-one compare, would move the pass with every test of the pass still green."""
    assert await _senders_on_a_wake_at(monkeypatch, wake) == ran


@pytest.mark.asyncio
async def test_as_shipped_a_landing_claims_nothing_and_the_16_00_pass_tells_it(
        monkeypatch, clean_sender, live):
    """Thursday's result lands at 17:00 and no row is claimed: the key has to stay free, or the
    pass would find it taken and nobody would hear. Friday's pass tells it, once, and neither the
    sender's later rounds nor a pass run again add anything."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    _flags_as_shipped(monkeypatch)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)

    landed = _lands(live, _clock_at(monkeypatch, THU_1700))
    assert [s.symbol for s in landed] == ["INTC"], "the sender must be handed a real landing"
    stats = await sender.dispatch_landings(landed, THU_1700)
    assert stats == {"live": 0, "result": 0}
    assert ledger.rows == [] and phones.buzzes == []

    assert (await _daily_pass(monkeypatch, _clock_at(monkeypatch, FRI_1600)))["results"] == 1
    (row,) = ledger.rows
    (buzz,) = phones.buzzes
    assert (row["kind"], row["dedup_key"], row["push_state"]) == (
        KIND_EARNINGS_RESULT, "earnings_result:INTC:2026-10-29", "sent")
    assert (buzz["title"], buzz["body"]) == (
        "INTC earnings are out", "EPS beat consensus by 46%. Tap for the full picture.")
    assert buzz["data"]["report_date"] == "2026-10-29"

    await sender.dispatch_landings(live.landed_snapshots(FRI_1600), FRI_1600)
    await _daily_pass(monkeypatch, FRI_1600)
    assert len(ledger.rows) == len(phones.buzzes) == 1


@pytest.mark.asyncio
async def test_a_result_push_switched_on_later_finds_what_the_pass_told(
        monkeypatch, clean_sender, live):
    """The switch goes on with a redeploy: a fresh process, a report the pass already told, and
    the one key between them."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    _flags_as_shipped(monkeypatch)
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, FRI_1600))
    assert len(phones.buzzes) == 1

    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", True)
    sender.reset()
    await sender.dispatch_landings([_big()], FRI_1600)
    assert len(ledger.rows) == len(phones.buzzes) == 1


@pytest.mark.asyncio
async def test_as_shipped_a_busy_round_sends_nothing_warns_of_nothing_and_still_forgets(
        dispatch, monkeypatch, caplog):
    """Nothing is ever decided with both off, so every report stays open for its whole window. It
    must stay quiet there: past the round cap there is nothing owed, and the memory still ends
    with the live days."""
    _flags_as_shipped(monkeypatch)
    many = [_snap(f"A{i:03d}", eps=0.60, eps_pct=46.34)
            for i in range(sender.MAX_SENDS_PER_ROUND + 5)]
    with caplog.at_level(logging.WARNING, logger=sender.__name__):
        await sender.dispatch_landings(many, NOW)
        await sender.dispatch_landings(many, NOW)
    assert dispatch.sent == [] and _warnings(caplog) == []
    assert len(sender._live_done) == len(many)

    await sender.dispatch_landings([], datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc))
    assert not sender._live_done and not sender._surprise_done


# ── what a held result says and opens ────────────────────────────────────────
#
# The row is written at the landing and its buzz goes out hours later, so two things can have
# moved: the day, past the date the sheet takes, and the figure, under the text. These run the
# real record, sender, flush and sheet read side by side, so the push, its inbox row and the
# screen it opens can be read against each other.

WED_1550 = datetime(2026, 11, 11, 15, 50, tzinfo=ET)
WED_1600 = datetime(2026, 11, 11, 16, 0, tzinfo=ET)
THU_2210 = datetime(2026, 10, 29, 22, 10, tzinfo=ET)
THU_2340 = datetime(2026, 10, 29, 23, 40, tzinfo=ET)


class _Calendar:
    """FMP with nothing listed, so what a sheet shows can only come from the record."""

    async def get_earnings_calendar(self, from_date=None, to_date=None):
        return []


@pytest.fixture
def live(monkeypatch):
    """The real record the sender, the flush and the sheet all read, and a sheet with no extras.

    A record of this test's own, put where `get_earnings_live_service` finds it, never the
    process's: a method a test patches onto it goes with it. Patched onto the process's record,
    monkeypatch's undo writes the original back onto the instance, where it hides every later
    patch of the class for the rest of the run."""
    async def _no_extras(self, symbol, report_day, *, loader=None):
        return None

    monkeypatch.setattr(EarningsLiveService, "history_extras", _no_extras)
    service = EarningsLiveService()
    monkeypatch.setattr(els, "_service", service)
    ts.reset_earnings_calendar_cache()
    yield service
    ts.reset_earnings_calendar_cache()


def _fmp_row(day=REPORT, *, eps=None, revenue=None):
    return {"symbol": "INTC", "date": day.isoformat(), "companyName": "Intel Corporation",
            "epsEstimated": 0.41, "revenueEstimated": 16.6e9,
            "epsActual": eps, "revenueActual": revenue}


def _fmp_says(live, at, *, eps=0.60, revenue=17.1e9, day=REPORT):
    """FMP's answer at `at` reaches the record. Returns what the tick would hand the sender."""
    live.observe_rows(day, [_fmp_row(day, eps=eps, revenue=revenue)], at, fetched=True)
    return live.landed_snapshots(at)


def _lands(live, at, *, eps=0.60, revenue=17.1e9, day=REPORT):
    """The loop saw the report pending, and FMP fills it at `at`."""
    live.observe_rows(day, [_fmp_row(day)], at - timedelta(hours=8), fetched=True)
    return _fmp_says(live, at, eps=eps, revenue=revenue, day=day)


async def _tap(monkeypatch, route, at):
    """"See live results" on a delivered route, through the real sheet read at `at`."""
    class _Moved(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz or timezone.utc)

    monkeypatch.setattr(ts, "datetime", _Moved)
    monkeypatch.setattr(ts, "_et_today", lambda: at.astimezone(ET).date())
    sheet = ts.TrackingService.__new__(ts.TrackingService)
    sheet.fmp = _Calendar()
    return await sheet.get_earnings_live(route["ticker"],
                                         date.fromisoformat(route["report_date"]))


def _result_kind_alone(monkeypatch):
    """`earnings_result` sent live on its own, `earnings_live` held for the release with its
    toggle. Over `_dispatcher_over`, which turns both on."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)


def _flags_as_shipped(monkeypatch):
    """Both push switches at their declared defaults, over whatever a fixture set."""
    for name in ("EARNINGS_LIVE_PUSH_ENABLED", "EARNINGS_LIVE_RESULT_PUSH_ENABLED"):
        monkeypatch.setattr(settings, name, Settings.model_fields[name].default)


async def _flush_until_settled(svc, ledger, monkeypatch):
    """Run the flush at every new wake the row is given. Returns the wakes it was given."""
    wakes = []
    (row,) = ledger.rows
    for _ in range(pds.PushDispatchService.MAX_FLUSH_ATTEMPTS + 2):
        if row["push_state"] != "deferred":
            break
        at = _stamp(row["deliver_after"])
        wakes.append(at)
        _clock_at(monkeypatch, at)
        await svc.flush_deferred()
    return wakes


@pytest.mark.asyncio
@pytest.mark.parametrize("landing,wake", [
    (FRI_2330, SAT_0700),
    (datetime(2026, 11, 10, 22, 30, tzinfo=ET), datetime(2026, 11, 11, 7, 0, tzinfo=ET)),
], ids=["over_a_weekend", "plain_weeknight"])
async def test_the_screen_a_held_push_opens_answers_its_figures(
        monkeypatch, clean_sender, live, landing, wake):
    """A report dated the day before the landing, filled late that night. By the wake the
    report has left the live days, and the buzz's first destination must still open."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    day = previous_trading_day(landing.date())

    await sender.dispatch_landings(_lands(live, landing, day=day),
                                   _clock_at(monkeypatch, landing))
    assert _stamp(ledger.rows[0]["deliver_after"]) == wake

    await live.refresh(wake, fmp=_Calendar(), symbols=["INTC"])
    _clock_at(monkeypatch, wake)
    assert (await svc.flush_deferred())["sent"] == 1

    answer = await _tap(monkeypatch, phones.buzzes[0]["data"], wake)
    assert (answer.status, answer.report_date) == ("reported", day.isoformat())
    assert (answer.eps.actual, answer.eps.surprise_pct, answer.eps.source) == (0.60, 46.34, "fmp")
    assert answer.revenue.actual == 17.1e9
    assert answer.poll_seconds is None, "nothing further is coming for it"


@pytest.mark.asyncio
@pytest.mark.parametrize("zone", ["Europe/Paris", "Europe/Berlin", "Europe/Helsinki"])
async def test_a_daily_pass_held_for_europe_opens_at_its_wake(
        monkeypatch, clean_sender, live, zone):
    """The pass reads yesterday's report at 16:00 ET, which is night there, and their 07:00 is
    already tomorrow in New York, when that report has left the live days."""
    ledger, phones = _Ledger(), _Phones()
    prefs = {"u1": {qh.PREF_TIMEZONE: zone}}
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=prefs)
    _flags_as_shipped(monkeypatch)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    tuesday = date(2026, 11, 10)

    _lands(live, WED_1550, day=tuesday)
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=tuesday)
    (row,) = ledger.rows
    wake = _stamp(row["deliver_after"])
    assert (row["push_state"], wake.astimezone(ET).date()) == ("deferred", date(2026, 11, 12))

    await live.refresh(wake, fmp=_Calendar(), symbols=["INTC"])
    _clock_at(monkeypatch, wake)
    assert (await svc.flush_deferred())["sent"] == 1
    answer = await _tap(monkeypatch, phones.buzzes[0]["data"], wake)
    assert (answer.status, answer.eps.actual) == ("reported", 0.60)


def test_every_wake_a_held_result_can_reach_is_a_day_the_sheet_takes():
    """Over two calendars, holidays included, for a report any push may still start for
    (`in_result_window`), landing at any hour in anybody's night. A failed read at the landing
    parks the row on ET first, the wake then holds it once more on the user's clock, and a
    figure still being confirmed takes the rest of its hand-outs on top."""
    svc = object.__new__(pds.PushDispatchService)
    kind = NOTIFICATION_KINDS[KIND_EARNINGS_RESULT]
    zones = ("Pacific/Honolulu", "America/Los_Angeles", "America/New_York", "Europe/London",
             "Europe/Helsinki", "Asia/Tokyo", "Pacific/Kiritimati")
    def _wake(prefs, at):
        return svc.decide(pds._Recipient("u1", preferences=prefs), kind, at).deliver_after

    held = 0
    report = date(2026, 1, 1)
    while report.year < 2028:
        for landing_day in (report, report + timedelta(days=1)):
            for hour in range(0, 24, 2):
                at = datetime(landing_day.year, landing_day.month, landing_day.day, hour, 59,
                              tzinfo=ET).astimezone(timezone.utc)
                for zone in zones:
                    prefs = {qh.PREF_TIMEZONE: zone}
                    wake = _wake(prefs, at) or _wake({}, at)
                    if wake is None:
                        continue
                    held += 1
                    wake = _wake(prefs, wake) or wake
                    # The last look a row with no answer yet can have.
                    wake += sender.WAKE_ASK_AGAIN * (pds.PushDispatchService.MAX_FLUSH_ATTEMPTS - 1)
                    assert report in sheet_days(wake.astimezone(ET).date()), (report, at, zone)
        report += timedelta(days=1)
    assert held > 10_000, "the sweep must reach the hold"


@pytest.mark.asyncio
async def test_a_buzz_at_the_wake_says_what_the_record_says_then(
        monkeypatch, clean_sender, live):
    """FMP's 0.60 is parked as a 46% beat, then FMP corrects it. The buzz, its inbox row and the
    sheet all say the corrected figure, with no number left over from the landing."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)

    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    (row,) = ledger.rows
    assert row["body"].startswith("EPS beat consensus by 46%")

    _fmp_says(live, THU_2340, eps=0.30)
    _clock_at(monkeypatch, FRI_0700)
    assert (await svc.flush_deferred())["sent"] == 1

    (buzz,) = phones.buzzes
    assert buzz["body"] == "EPS missed consensus by 27%. Tap for the full picture."
    assert row["body"] == buzz["body"] and row["title"] == buzz["title"]
    answer = await _tap(monkeypatch, buzz["data"], FRI_0700)
    assert (answer.eps.actual, answer.eps.surprise_pct) == (0.30, -26.83)


@pytest.mark.asyncio
@pytest.mark.parametrize("eps,wakes,why", [
    (0.40, 1, "a 2% miss starts no push, and that is a final answer"),
    (0.42, 1, "nor a 2% beat"),
    (0.041, pds.PushDispatchService.MAX_FLUSH_ATTEMPTS,
     "a dropped digit is held as checking, never a -90% miss, and it is asked about again"),
], ids=["small_miss", "small_beat", "dropped_digit"])
async def test_a_surprise_the_record_no_longer_holds_is_never_buzzed(
        monkeypatch, clean_sender, live, eps, wakes, why):
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)

    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    _fmp_says(live, THU_2340, eps=eps)

    assert len(await _flush_until_settled(svc, ledger, monkeypatch)) == wakes
    assert phones.buzzes == [], why
    (row,) = ledger.rows
    assert row["push_state"] == "failed" and "suppressed on flush" in row["last_error"]
    assert "46%" not in row["body"], "the inbox keeps the row, never its reversed number"
    assert row["title"] == "INTC results are out"

    # The key stays taken: the daily pass reads the same report and tells nobody twice.
    _clock_at(monkeypatch, FRI_1600)
    await _daily_pass(monkeypatch, FRI_1600)
    assert phones.buzzes == [] and len(ledger.rows) == 1


@pytest.mark.asyncio
async def test_a_small_surprise_reads_as_what_it_is_in_the_inbox(monkeypatch, clean_sender, live):
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)

    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    _fmp_says(live, THU_2340, eps=0.41)
    _clock_at(monkeypatch, FRI_0700)
    await svc.flush_deferred()

    assert ledger.rows[0]["body"] == "Revenue beat consensus by 3% · EPS in line with consensus."


@pytest.mark.asyncio
async def test_a_live_row_says_what_has_landed_by_the_wake(monkeypatch, clean_sender, live):
    """`earnings_live`. Revenue landed at night, FMP's EPS came later still."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)

    await sender.dispatch_landings(_lands(live, THU_2210, eps=None),
                                   _clock_at(monkeypatch, THU_2210))
    (row,) = ledger.rows
    assert row["kind"] == KIND_EARNINGS_LIVE and "EPS not out yet" in row["body"]

    _fmp_says(live, THU_2340, eps=0.60)
    _clock_at(monkeypatch, FRI_0700)
    await svc.flush_deferred()

    (buzz,) = phones.buzzes
    assert buzz["body"] == "Revenue beat consensus by 3% · EPS beat consensus by 46%."
    assert row["body"] == buzz["body"]


@pytest.mark.asyncio
async def test_a_live_row_whose_figures_are_all_held_is_not_buzzed(
        monkeypatch, clean_sender, live):
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)

    await sender.dispatch_landings(_lands(live, THU_2210, revenue=None),
                                   _clock_at(monkeypatch, THU_2210))
    _fmp_says(live, THU_2340, eps=0.041, revenue=None)

    wakes = await _flush_until_settled(svc, ledger, monkeypatch)

    assert len(wakes) == pds.PushDispatchService.MAX_FLUSH_ATTEMPTS
    assert phones.buzzes == [] and ledger.rows[0]["push_state"] == "failed"
    assert "EPS being confirmed" in ledger.rows[0]["body"]


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["restart", "pending_again"])
async def test_a_record_that_holds_nothing_leaves_the_rows_own_text(
        monkeypatch, clean_sender, live, how):
    """A restart forgets the record, and a record is sticky only while it lives: it can come back
    with no figure, from an answer that has lost one or a release only the old process read.
    Neither says anything against the row, which a figure did land to write."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)

    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    live.reset()
    if how == "pending_again":
        live.observe_rows(REPORT, [_fmp_row()], THU_2340, fetched=True)

    _clock_at(monkeypatch, FRI_0700)
    assert (await svc.flush_deferred())["sent"] == 1
    assert phones.buzzes[0]["body"].startswith("EPS beat consensus by 46%")


@pytest.mark.parametrize("route", [
    {}, {"ticker": "INTC"}, {"report_date": "2026-10-29"},
    {"ticker": "INTC", "report_date": "soon"}, {"ticker": "INTC", "report_date": 5},
    {"ticker": "", "report_date": "2026-10-29"}, {"ticker": None, "report_date": None},
], ids=["empty", "no_date", "no_ticker", "bad_date", "number_date", "blank_ticker", "nulls"])
def test_a_route_that_names_no_report_keeps_the_rows_own_text(live, route):
    _lands(live, THU_2210)
    assert sender.copy_at_wake(KIND_EARNINGS_RESULT, route, FRI_0700) is None


def test_a_kind_with_no_copy_of_its_own_keeps_the_rows_own_text(live):
    _lands(live, THU_2210)
    route = {"ticker": "INTC", "report_date": "2026-10-29"}
    assert sender.copy_at_wake("ticker_move", route, FRI_0700) is None


@pytest.mark.asyncio
async def test_a_broken_re_read_costs_the_new_text_never_the_buzz(
        monkeypatch, clean_sender, caplog):
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("record exploded")

    monkeypatch.setattr(sender, "copy_at_wake", _boom)
    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, FRI_0230))
    _clock_at(monkeypatch, FRI_0700)
    with caplog.at_level(logging.WARNING):
        stats = await svc.flush_deferred()

    assert (stats["sent"], stats["requeued"]) == (1, 0)
    assert phones.buzzes[0]["body"].startswith("EPS beat consensus by 46%")
    (record,) = [r for r in caplog.records if "could not re-read" in r.getMessage()]
    assert "RuntimeError" in record.getMessage() and "INTC" in record.getMessage()
    # Only a bug reaches that except, and its line is the one trace it leaves: the stack goes too.
    assert record.levelno == logging.WARNING
    assert record.exc_info is not None and record.exc_info[0] is RuntimeError


@pytest.mark.asyncio
async def test_a_failed_text_write_still_sends_the_new_text(
        monkeypatch, clean_sender, live, caplog):
    """The inbox falls behind the phone for one row, which the log says. The phone never gets
    the number the record has left."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    _fmp_says(live, THU_2340, eps=0.30)

    real_update = ledger.update

    def _fails_on_text(patch):
        if "body" in patch:
            raise RuntimeError("write refused")
        return real_update(patch)

    monkeypatch.setattr(ledger, "update", _fails_on_text)
    _clock_at(monkeypatch, FRI_0700)
    with caplog.at_level(logging.WARNING):
        assert (await svc.flush_deferred())["sent"] == 1

    assert phones.buzzes[0]["body"].startswith("EPS missed consensus by 27%")
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_row_the_cap_stops_at_the_wake_still_loses_its_stale_number(
        monkeypatch, clean_sender, live):
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    _fmp_says(live, THU_2340, eps=0.30)

    svc._category_counts_bulk = lambda ids, category, cutoffs: {u: 4 for u in ids}
    _clock_at(monkeypatch, FRI_0700)
    stats = await svc.flush_deferred()

    (row,) = ledger.rows
    assert stats["suppressed"] == 1 and phones.buzzes == []
    assert "cap_reached" in row["last_error"]
    assert row["body"].startswith("EPS missed consensus by 27%")


def test_a_held_row_reads_back_from_the_inbox_as_held():
    """Every night result of a default-settings user now takes this path, so the row the inbox
    draws at 02:30 is this one, not a sent one."""
    ledger = _Ledger()
    svc = object.__new__(pds.PushDispatchService)
    svc.supabase = ledger
    svc.claim_send(
        "u1", "earnings_result:INTC:2026-10-29", kind=KIND_EARNINGS_RESULT, category="earnings",
        title="INTC earnings are out", body="EPS beat consensus by 46%.",
        route={"ticker": "INTC", "report_date": "2026-10-29", "kind": KIND_EARNINGS_RESULT},
        push_state=pds.STATE_DEFERRED, deliver_after=FRI_0700,
    )
    row = {"id": "1", "claimed_at": FRI_0230.isoformat(), "read_at": None, **ledger.rows[0]}

    dto = object.__new__(NotificationInboxService)._to_response(row)

    assert dto is not None
    assert (dto.delivery_state, dto.kind, dto.category) == (
        "deferred", KIND_EARNINGS_RESULT, "earnings")
    assert dto.route["report_date"] == "2026-10-29" and dto.sent_at is None


async def _two_results_held_in_berlin(monkeypatch, ledger, phones) -> Dict[str, Dict[str, Any]]:
    """The 16:05 ET pass (22:05 in Berlin) holds INTC's and CSCO's beats for 07:00. By ticker."""
    calendar = [{**_beat_on(TUESDAY), "symbol": symbol} for symbol in ("INTC", "CSCO")]
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, TUE_1605), day=TUESDAY, rows=calendar)
    held = {row["route"]["ticker"]: row for row in ledger.rows}
    assert {row["push_state"] for row in held.values()} == {"deferred"} and phones.buzzes == []
    return held


def _inbox_over(ledger) -> NotificationInboxService:
    inbox = object.__new__(NotificationInboxService)
    inbox.supabase = ledger
    return inbox


@pytest.mark.asyncio
@pytest.mark.parametrize("landing_read", ["good", "failed"])
async def test_a_held_row_opened_in_the_app_is_not_buzzed_at_its_wake(
        monkeypatch, clean_sender, landing_read):
    """Every evening result of a default-settings user in Europe is held and listed at once, so it
    is often opened before its wake. At 07:00 the opened row ends unbuzzed, and the unread one
    beside it is the only buzz, its badge counting itself alone. After a failed read at the pass,
    07:00 is the rows' second hand-out: the first, a minute on, held them for Berlin's morning."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=IN_BERLIN)
    _flags_as_shipped(monkeypatch)
    if landing_read == "failed":
        answers = iter([{"u1": None}, {"u1": None}])      # one read per ticker the pass tells
        svc._preferences_bulk = lambda ids: next(answers, IN_BERLIN)
        calendar = [{**_beat_on(TUESDAY), "symbol": symbol} for symbol in ("INTC", "CSCO")]
        await _daily_pass(monkeypatch, _clock_at(monkeypatch, TUE_1605), day=TUESDAY,
                          rows=calendar)
        _clock_at(monkeypatch, TUE_1605 + timedelta(minutes=1))
        assert (await svc.flush_deferred())["requeued"] == 2
        held = {row["route"]["ticker"]: row for row in ledger.rows}
        assert {_stamp(row["deliver_after"]) for row in held.values()} == {WED_0700_BERLIN}
    else:
        held = await _two_results_held_in_berlin(monkeypatch, ledger, phones)

    held["INTC"]["id"] = "row-intc"      # the table's own id, which the inbox hands the app
    assert _inbox_over(ledger).mark_read("u1", ids=["row-intc"]) == 1

    _clock_at(monkeypatch, WED_0700_BERLIN)
    await svc.flush_deferred()

    assert [(buzz["data"]["ticker"], buzz["badge"]) for buzz in phones.buzzes] == [("CSCO", 1)]
    intc = held["INTC"]
    assert (intc["push_state"], intc.get("sent_at")) == ("failed", None)
    assert intc["last_error"] == "suppressed on flush: read in the app before its wake"


@pytest.mark.asyncio
async def test_opening_alerts_reads_no_held_row_so_its_wake_still_buzzes(monkeypatch, clean_sender):
    """The Alerts screen marks everything read on sight, over rows a filter chip or the fold keeps
    off the screen; a price move delivered at 22:20 is what sets it off here. That proves nothing
    about a held row, so it stays unread and buzzes at 07:00, each badge counting it."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=IN_BERLIN)
    _flags_as_shipped(monkeypatch)
    held = await _two_results_held_in_berlin(monkeypatch, ledger, phones)
    ledger.rows.append({
        "id": "row-move", "user_id": "u1", "dedup_key": "ticker_move:AAPL:2026-11-10",
        "kind": "ticker_move", "category": "watchlist", "push_state": "sent", "attempts": 0,
        "sent_at": (TUE_1605 + timedelta(minutes=15)).isoformat(), "read_at": None,
    })

    assert _inbox_over(ledger).mark_read("u1", mark_all=True) == 1
    assert [row.get("read_at") for row in held.values()] == [None, None]

    _clock_at(monkeypatch, WED_0700_BERLIN)
    assert (await svc.flush_deferred())["sent"] == 2
    assert sorted(buzz["data"]["ticker"] for buzz in phones.buzzes) == ["CSCO", "INTC"]
    assert [buzz["badge"] for buzz in phones.buzzes] == [1, 2]


# ── a row from the daily pass, and the record that reads it at the wake ───────
#
# A landing's row is written from the record, so the record can only be newer. The pass reads FMP
# by itself, which can be newer than the record: a complete report is not asked about again. So
# what it read goes into the record before it tells anyone, and the wake has nothing older to
# overrule the row with. New York's buzz, Berlin's held buzz and the sheet read the same answer.

TUE_2005 = datetime(2026, 11, 10, 20, 5, tzinfo=ET)
TUE_2030 = datetime(2026, 11, 10, 20, 30, tzinfo=ET)
TO_BERLIN = {"ny": dict(IN_NEW_YORK), "berlin": {qh.PREF_TIMEZONE: "Europe/Berlin"}}


def _beat_on(day, *, eps=0.60):
    return {**_fmp_row(day, eps=eps, revenue=17.1e9), "time": "amc"}


async def _pass_then_wake(monkeypatch, svc, phones, ledger, pass_time, *, day, user="berlin"):
    """The pass at `pass_time`, then the flush at the wake of `user`'s row. Returns its stats."""
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, pass_time), day=day, row=_beat_on(day))
    (row,) = ledger.of(user)
    assert row["push_state"] == "deferred" and phones.of(user) == []
    _clock_at(monkeypatch, _stamp(row["deliver_after"]))
    return await svc.flush_deferred()


@pytest.mark.asyncio
async def test_a_pass_row_is_not_dropped_by_a_record_older_than_the_pass(
        monkeypatch, clean_sender, live):
    """Tuesday's 0.40 misses by 2%, so no push and a free key, and the record is complete, so
    nothing reads that day again. FMP then says 0.60, which the pass reads on Wednesday."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=TO_BERLIN, users=tuple(TO_BERLIN))
    _result_kind_alone(monkeypatch)
    await sender.dispatch_landings(_lands(live, TUE_2005, eps=0.40, day=TUESDAY),
                                   _clock_at(monkeypatch, TUE_2005))
    assert ledger.rows == []

    stats = await _pass_then_wake(monkeypatch, svc, phones, ledger, WED_1600, day=TUESDAY)

    assert (stats["sent"], stats["suppressed"]) == (1, 0)
    (ny,), (berlin,) = phones.of("ny"), phones.of("berlin")
    assert ny["body"].startswith("EPS beat consensus by 46%")
    assert berlin["body"] == ny["body"] == ledger.of("berlin")[0]["body"]
    assert ledger.of("berlin")[0]["push_state"] == "sent"


@pytest.mark.asyncio
async def test_the_sheet_a_pass_buzz_opens_says_what_the_buzz_said(
        monkeypatch, clean_sender, live):
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    _flags_as_shipped(monkeypatch)
    _lands(live, TUE_2005, eps=0.40, day=TUESDAY)

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                      row=_beat_on(TUESDAY))

    (buzz,) = phones.buzzes
    answer = await _tap(monkeypatch, buzz["data"], WED_1600)
    assert (answer.eps.actual, answer.eps.surprise_pct, answer.eps.source) == (0.60, 46.34, "fmp")


@pytest.mark.asyncio
async def test_two_watchers_of_one_pass_never_get_opposite_directions(
        monkeypatch, clean_sender, live):
    """The process starts on FMP's 0.30, first seen reported: no landing and no key. By the pass
    FMP says 0.60. One row, one text for both, never a beat in New York and a miss in Berlin."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=TO_BERLIN, users=tuple(TO_BERLIN))
    _flags_as_shipped(monkeypatch)
    assert _fmp_says(live, TUE_2030, eps=0.30, day=TUESDAY) == []

    await _pass_then_wake(monkeypatch, svc, phones, ledger, WED_1600, day=TUESDAY)

    assert phones.of("berlin")[0]["body"] == phones.of("ny")[0]["body"]
    assert phones.of("berlin")[0]["body"].startswith("EPS beat consensus by 46%")


@pytest.mark.asyncio
@pytest.mark.parametrize("corrected,buzzes,body,surprise", [
    (0.30, 1, "EPS missed consensus by 27%. Tap for the full picture.", -26.83),
    (0.40, 0, "Revenue beat consensus by 3% · EPS missed consensus by 2%.", -2.44),
], ids=["reversed", "under_the_bar"])
async def test_as_shipped_a_held_pass_row_wakes_on_the_record_as_it_stands(
        monkeypatch, clean_sender, live, corrected, buzzes, body, surprise):
    """The pass tells New York, holds Berlin's row, and FMP corrects the figure in between. The
    switches stop at the live sender, so the wake is the same as shipped: the row says what the
    record says then, and a surprise the record no longer holds goes to the inbox alone."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=TO_BERLIN, users=tuple(TO_BERLIN))
    _flags_as_shipped(monkeypatch)
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                      row=_beat_on(TUESDAY))
    (row,) = ledger.of("berlin")
    assert row["push_state"] == "deferred" and row["body"].startswith("EPS beat consensus by 46%")

    _fmp_says(live, WED_1600 + timedelta(hours=2), eps=corrected, day=TUESDAY)
    wake = _clock_at(monkeypatch, _stamp(row["deliver_after"]))
    await svc.flush_deferred()

    assert row["body"] == body
    assert [buzz["body"] for buzz in phones.of("berlin")] == [body] * buzzes
    assert row["push_state"] == ("sent" if buzzes else "failed")
    answer = await _tap(monkeypatch, row["route"], wake)
    assert (answer.eps.actual, answer.eps.surprise_pct) == (corrected, surprise)


def test_only_the_live_sender_reads_the_two_push_switches():
    """`earnings_sender` says the switches stop here. A read in the pass or the wake would change
    what they do with the switch off, and no test over one calendar has to notice. By AST, over
    all of `app/`, because comments and docstrings name these switches freely."""
    switches = {"EARNINGS_LIVE_PUSH_ENABLED", "EARNINGS_LIVE_RESULT_PUSH_ENABLED"}
    app_root = Path(__file__).resolve().parents[1] / "app"
    readers = set()
    for path in app_root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            named = (node.attr if isinstance(node, ast.Attribute)
                     else node.value if isinstance(node, ast.Constant) else None)
            if isinstance(named, str) and named in switches:
                readers.add(path.relative_to(app_root).as_posix())
    assert readers == {"services/notification_senders/earnings_live_sender.py"}


# ── what off costs ───────────────────────────────────────────────────────────
#
# For most reports the result push being off is a wait for the next pass. These are the kinds it
# costs the buzz outright, as `earnings_live_sender`'s docstring lists them: the pass has its own
# rules, which the live sender does not share, and it knows only the rows it read. The sheet keeps
# every one of them.

THU_1600 = datetime(2026, 11, 12, 16, 0, tzinfo=ET)


def _set_result_push(monkeypatch, on: bool) -> None:
    _flags_as_shipped(monkeypatch)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", on)


@pytest.mark.asyncio
@pytest.mark.parametrize("result_push", [False, True], ids=["as_shipped", "result_push_on"])
async def test_off_a_report_filled_after_the_last_pass_that_can_tell_it_is_told_by_nobody(
        monkeypatch, clean_sender, live, result_push):
    """Tuesday's report, filled on Wednesday at 17:05. Wednesday's pass read it pending and was
    the last whose window holds Tuesday, and Thursday's does not ask about that day at all."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    _set_result_push(monkeypatch, result_push)
    fill = datetime(2026, 11, 11, 17, 5, tzinfo=ET)

    stats = await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                              row=_fmp_row(TUESDAY))
    assert stats["results"] == 0
    landed = _fmp_says(live, fill, day=TUESDAY)
    assert [s.symbol for s in landed] == ["INTC"], "the sender must be handed a real landing"
    await sender.dispatch_landings(landed, _clock_at(monkeypatch, fill))
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, THU_1600), day=TUESDAY,
                      row=_beat_on(TUESDAY))

    told = ["EPS beat consensus by 46%. Tap for the full picture."] if result_push else []
    assert [buzz["body"] for buzz in phones.buzzes] == told and len(ledger.rows) == len(told)
    answer = await _tap(monkeypatch, {"ticker": "INTC", "report_date": TUESDAY.isoformat()},
                        THU_1600)
    assert (answer.status, answer.eps.actual, answer.eps.surprise_pct) == (
        STATUS_REPORTED, 0.60, 46.34)


@pytest.mark.asyncio
@pytest.mark.parametrize("listed", ["once", "twice"])
@pytest.mark.parametrize("result_push", [False, True], ids=["as_shipped", "result_push_on"])
@pytest.mark.parametrize("estimate,eps,gaap,body,surprise", [
    (0.05, 0.46, 0.39, "EPS beat consensus by 820%. Tap for the full picture.", 820.0),
    (0.11, 0.01, -0.02, "EPS missed consensus by 91%. Tap for the full picture.", -90.91),
], ids=["ten_times_the_estimate", "a_tenth_of_it"])
async def test_off_a_surprise_only_the_filing_can_clear_is_told_by_nobody(
        monkeypatch, clean_sender, live, result_push, estimate, eps, gaap, body, surprise,
        listed):
    """FMP's figure has the dropped-digit signature and the filing's GAAP EPS says it is real, so
    the record shows it. The pass judges that signature without the GAAP EPS and refuses it, a
    report FMP lists twice included, which the pass reads as its record does. Kept that way by
    the owner's choice of 2026-11-11, "Keep it quiet": the guard is not handed the filing's GAAP
    EPS, so as shipped nobody is told."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    _set_result_push(monkeypatch, result_push)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    pending = {**_fmp_row(TUESDAY), "epsEstimated": estimate}
    filled = {**_fmp_row(TUESDAY, eps=eps, revenue=17.1e9), "epsEstimated": estimate}
    read = [filled] if listed == "once" else [
        {**filled, "lastUpdated": f"{TUESDAY.isoformat()} 13:40:00"},
        {**pending, "lastUpdated": f"{TUESDAY.isoformat()} 13:50:00"}]

    live.observe_rows(TUESDAY, [pending], TUE_2005 - timedelta(hours=8), fetched=True)
    live.absorb_filing("INTC", TUESDAY, FilingFigures(
        accession="a", accepted_at=None, url=None, eps_gaap=gaap), TUE_2005 - timedelta(hours=1))
    live.observe_rows(TUESDAY, [filled], TUE_2005, fetched=True)
    await sender.dispatch_landings(live.landed_snapshots(TUE_2005),
                                   _clock_at(monkeypatch, TUE_2005))
    stats = await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                              rows=read)

    assert stats["results"] == 0
    told = [body] if result_push else []
    assert [buzz["body"] for buzz in phones.buzzes] == told and len(ledger.rows) == len(told)
    answer = await _tap(monkeypatch, {"ticker": "INTC", "report_date": TUESDAY.isoformat()},
                        WED_1600)
    assert (answer.status, answer.eps.actual, answer.eps.surprise_pct, answer.eps.source) == (
        STATUS_REPORTED, eps, surprise, SOURCE_FMP)


@pytest.mark.asyncio
@pytest.mark.parametrize("result_push", [False, True], ids=["as_shipped", "result_push_on"])
async def test_off_a_report_past_the_passs_cap_is_told_by_nobody(
        monkeypatch, clean_sender, live, result_push):
    """The pass cuts its list at `MAX_SYMBOLS_PER_PASS`, and the next day's pass reads the same
    rows in the same order, so the tail stays cut. The live sender's cap only moves a report to
    its next round."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    _set_result_push(monkeypatch, result_push)
    monkeypatch.setattr(daily, "MAX_SYMBOLS_PER_PASS", 1)
    monkeypatch.setattr(sender, "MAX_SENDS_PER_ROUND", 1)
    noon = datetime(2026, 11, 10, 12, 0, tzinfo=ET)
    pending = [{**_fmp_row(TUESDAY), "symbol": symbol} for symbol in ("AAAA", "BBBB")]
    filled = [{**_beat_on(TUESDAY), "symbol": symbol} for symbol in ("AAAA", "BBBB")]

    live.observe_rows(TUESDAY, pending, noon - timedelta(hours=4), fetched=True)
    live.observe_rows(TUESDAY, filled, noon, fetched=True)
    for _round in range(2):
        await sender.dispatch_landings(live.landed_snapshots(noon), _clock_at(monkeypatch, noon))
    for pass_time in (noon + timedelta(hours=4), WED_1600):
        await _daily_pass(monkeypatch, _clock_at(monkeypatch, pass_time), day=TUESDAY, rows=filled)

    told = ["AAAA", "BBBB"] if result_push else ["AAAA"]
    assert [buzz["title"] for buzz in phones.buzzes] == [f"{s} earnings are out" for s in told]
    answer = await _tap(monkeypatch, {"ticker": "BBBB", "report_date": TUESDAY.isoformat()},
                        WED_1600)
    assert (answer.status, answer.eps.surprise_pct) == (STATUS_REPORTED, 46.34)


def _filled_without_an_estimate(blank: Dict[str, Any]) -> Dict[str, Any]:
    filled = _fmp_row(TUESDAY, eps=0.60, revenue=17.1e9)
    del filled["epsEstimated"]
    return {**filled, **blank}


@pytest.mark.asyncio
@pytest.mark.parametrize("result_push", [False, True], ids=["as_shipped", "result_push_on"])
@pytest.mark.parametrize("blank", [{"epsEstimated": None}, {"epsEstimated": float("nan")}, {}],
                         ids=["null", "nan", "missing"])
async def test_off_a_filled_row_with_no_estimate_of_its_own_is_told_by_nobody(
        monkeypatch, clean_sender, live, result_push, blank):
    """The row was pending with a consensus of 0.41. It fills with none, so the record keeps the
    0.41 it held and the sheet has a surprise, while the pass has nothing in the row to divide
    by."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    _set_result_push(monkeypatch, result_push)
    filled = _filled_without_an_estimate(blank)

    live.observe_rows(TUESDAY, [_fmp_row(TUESDAY)], TUE_2005 - timedelta(hours=8), fetched=True)
    live.observe_rows(TUESDAY, [filled], TUE_2005, fetched=True)
    await sender.dispatch_landings(live.landed_snapshots(TUE_2005),
                                   _clock_at(monkeypatch, TUE_2005))
    stats = await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                              row=filled)

    assert stats["results"] == 0
    told = ["EPS beat consensus by 46%. Tap for the full picture."] if result_push else []
    assert [buzz["body"] for buzz in phones.buzzes] == told and len(ledger.rows) == len(told)
    answer = await _tap(monkeypatch, {"ticker": "INTC", "report_date": TUESDAY.isoformat()},
                        WED_1600)
    assert (answer.status, answer.eps.estimate, answer.eps.actual, answer.eps.surprise_pct) == (
        STATUS_REPORTED, 0.41, 0.60, 46.34)


async def _off_the_calendar(live, day, at) -> List[Dict[str, Any]]:
    """Two own fetches, the hold apart, over a calendar that lists one other report on `day`. It
    is pending, so the day is still asked about, and `BBBB`, left out with no figure, shows that
    these fetches end a report: what outlives them has outlived something. Returns the rows."""
    listed = {**_fmp_row(day), "symbol": "AAAA"}
    live.observe_rows(day, [listed, {**_fmp_row(day), "symbol": "BBBB"}], at, fetched=True)

    async def calendar(from_date=None, to_date=None):
        return [listed] if from_date == day.isoformat() else []

    for _fetch in range(2):
        live._fetched.pop(day, None)
        await live.refresh(at, fmp=SimpleNamespace(get_earnings_calendar=calendar),
                           symbols=["AAAA", "BBBB", "INTC"])
        for record in live._records.values():
            if record.unlisted_since is not None:
                record.unlisted_since -= EARNINGS_LIVE_UNLISTED_END_SECONDS
    assert live.snapshot("BBBB", day, at) is None
    return [listed]


@pytest.mark.asyncio
@pytest.mark.parametrize("result_push", [False, True], ids=["as_shipped", "result_push_on"])
@pytest.mark.parametrize("taken_back", ["blank_again", "zeros_in_its_place", "off_the_calendar"])
async def test_off_a_figure_fmp_takes_back_before_the_pass_is_told_by_nobody(
        monkeypatch, clean_sender, live, result_push, taken_back):
    """Filled on Tuesday evening, and by Wednesday's pass blank again, a row of zeros under a tiny
    consensus, or off the calendar. The record keeps its figure through each, beside which the
    zeros are FMP's unfilled row, so the sheet has it, while the pass tells only an EPS its own
    answer gave the record: never the zeros as a 100% miss."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    _set_result_push(monkeypatch, result_push)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)

    _lands(live, TUE_2005, day=TUESDAY)
    await sender.dispatch_landings(live.landed_snapshots(TUE_2005),
                                   _clock_at(monkeypatch, TUE_2005))
    zeros = {**_fmp_row(TUESDAY, eps=0, revenue=0), "revenueEstimated": 30e6}
    rows = ([_fmp_row(TUESDAY)] if taken_back == "blank_again"
            else [zeros] if taken_back == "zeros_in_its_place"
            else await _off_the_calendar(live, TUESDAY, TUE_2030))
    stats = await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                              rows=rows)

    assert stats["results"] == 0
    told = ["EPS beat consensus by 46%. Tap for the full picture."] if result_push else []
    assert [buzz["body"] for buzz in phones.buzzes] == told and len(ledger.rows) == len(told)
    answer = await _tap(monkeypatch, {"ticker": "INTC", "report_date": TUESDAY.isoformat()},
                        WED_1600)
    assert (answer.status, answer.eps.actual, answer.eps.surprise_pct) == (
        STATUS_REPORTED, 0.60, 46.34)


#: Each kind pinned above, by its test: the words its own line in `earnings_live_sender`'s list of
#: what off costs must hold, and whether no captured calendar shows FMP doing it, which the list
#: says by naming it after the lines.
_WHAT_OFF_COSTS = {
    "test_off_a_report_filled_after_the_last_pass_that_can_tell_it_is_told_by_nobody":
        ("FMP fills after the pass on the day after its date", False),
    "test_off_a_surprise_only_the_filing_can_clear_is_told_by_nobody":
        ("the dropped-digit signature that the filing's GAAP EPS clears", False),
    "test_off_a_report_past_the_passs_cap_is_told_by_nobody":
        ("past `MAX_SYMBOLS_PER_PASS` on a pass's list", False),
    "test_off_a_filled_row_with_no_estimate_of_its_own_is_told_by_nobody":
        ("filled row with no estimate of its own", True),
    "test_off_a_figure_fmp_takes_back_before_the_pass_is_told_by_nobody":
        ("figure FMP takes back before the pass reads it", True),
}


def _what_off_costs() -> tuple:
    """(the whole passage; its lines, each on one line; what follows them), from the sender's
    docstring."""
    doc = sender.__doc__
    costs = doc[doc.index("With both off"):doc.index("`push_dispatch_service` claims the key")]
    lines: List[str] = []
    after: List[str] = []
    for line in costs.splitlines():
        if line.startswith("  * "):
            lines.append(line[4:].strip())
        elif line.startswith("    ") and lines and not after:
            lines[-1] += " " + line.strip()
        elif lines and line.strip():
            after.append(line.strip())
    return costs, lines, " ".join(after)


#: Words that place a line in the list instead of naming it ("the last two", "the final pair
#: above"): a line added or moved later leaves them pointing at the wrong ones.
_PLACED = re.compile(
    r"\b(?:former|latter|above|below|preceding|pair)\b"
    r"|\b(?:first|last|final|second|third|fourth|fifth|next|previous)\s+"
    r"(?:ones?|kinds?|lines?|bullets?|items?|entries|of\s+(?:them|these|those))\b",
    re.IGNORECASE)


def test_the_senders_list_of_what_off_costs_has_a_line_for_every_kind_pinned_here():
    """An owner reads that list before leaving the switch off, so a kind pinned above and missing
    there is a cost nobody was told about. Each kind is its own line, found by its words in that
    line and no other, and the kinds no captured calendar shows are named, never counted or placed
    ("the last two"): a kind found later must leave no wrong number and no wrong pair behind. A
    number in digits is a count too; a date, a clock time or an EPS of 0 is not one."""
    costs, lines, after = _what_off_costs()
    pinned = {name for name in globals() if name.startswith("test_off_")}
    assert pinned == set(_WHAT_OFF_COSTS), "a kind pinned with no words for its line, or the reverse"
    assert len(lines) == len(_WHAT_OFF_COSTS), lines
    for line in lines:
        held = [name for name, (words, _) in _WHAT_OFF_COSTS.items() if words in line]
        assert len(held) == 1, (line, held)
    for name, (words, unseen) in _WHAT_OFF_COSTS.items():
        assert sum(words in line for line in lines) == 1, (name, lines)
        assert (words in after) is unseen, (name, after)
    counted = re.findall(r"\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",
                         costs, re.IGNORECASE)
    counted += re.findall(r"\d+", re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}\b|\b0(?:\.\d+)?\b",
                                         "", costs))
    assert counted == [], counted
    assert _PLACED.findall(costs) == [], _PLACED.findall(costs)


@pytest.mark.asyncio
async def test_a_pass_row_outlives_a_record_that_held_only_the_filing(
        monkeypatch, clean_sender, live):
    """A Friday report with the filing's 0.60, and FMP's own 0.60 from 15:40 on Saturday. The
    closed day's 2 h cadence has not read it by Tokyo's wake, and both sources say the same."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones,
                           prefs={"tokyo": {qh.PREF_TIMEZONE: "Asia/Tokyo"}}, users=("tokyo",))
    _flags_as_shipped(monkeypatch)
    friday_noon = datetime(2026, 11, 6, 12, 0, tzinfo=ET)
    live.observe_rows(FRIDAY, [_fmp_row(FRIDAY)], friday_noon, fetched=True)
    live.absorb_filing("INTC", FRIDAY, FilingFigures(
        accession="a", accepted_at=None, url=None, eps_adjusted=0.60), friday_noon)

    saturday_1600 = datetime(2026, 11, 7, 16, 0, tzinfo=ET)
    stats = await _pass_then_wake(monkeypatch, svc, phones, ledger, saturday_1600, day=FRIDAY,
                                  user="tokyo")

    assert (stats["sent"], stats["suppressed"]) == (1, 0)
    assert phones.buzzes[0]["body"].startswith("EPS beat consensus by 46%")


@pytest.mark.asyncio
async def test_rows_read_before_the_pass_cannot_put_the_older_figure_back(
        monkeypatch, clean_sender, live):
    """The Tracking memo can be 30 min behind the pass. What it holds for that report is older
    than the pass, and the newest FMP answer wins, as it does over an own fetch."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    _flags_as_shipped(monkeypatch)
    wednesday = WED_1600.date()
    _lands(live, WED_1550, eps=0.40, day=wednesday)
    # Stored after the loop's last fetch, so that fetch alone would let this row in.
    held_rows_stored_at = time.monotonic()

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=wednesday,
                      row=_beat_on(wednesday))
    later = WED_1600 + timedelta(minutes=1)
    live.observe_rows(wednesday, [_fmp_row(wednesday, eps=0.40, revenue=17.1e9)], later,
                      stored_at=held_rows_stored_at)

    assert live.snapshot("INTC", wednesday, later).eps.actual == 0.60


@pytest.mark.asyncio
async def test_a_report_the_pass_finds_filled_lands_and_is_still_one_push(
        monkeypatch, clean_sender, live):
    """Seen pending, then filled in the pass's own read. That is a landing like any other, so the
    next tick hands it over, and the key the pass took keeps it to one buzz."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    live.observe_rows(TUESDAY, [_fmp_row(TUESDAY)], TUE_2005, fetched=True)

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                      row=_beat_on(TUESDAY))

    assert len(phones.buzzes) == 1
    landed = live.landed_snapshots(WED_1600)
    assert [(s.symbol, s.eps.actual) for s in landed] == [("INTC", 0.60)]
    await sender.dispatch_landings(landed, WED_1600)
    assert len(phones.buzzes) == 1 and len(ledger.rows) == 1


@pytest.mark.asyncio
async def test_a_report_first_seen_filled_by_the_pass_is_no_landing(
        monkeypatch, clean_sender, live):
    """The same rule as a first fetch after the fact: a restart must not read as news."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                      row=_beat_on(TUESDAY))

    assert live.snapshot("INTC", TUESDAY, WED_1600).eps.actual == 0.60
    assert live.landed_snapshots(WED_1600) == []


@pytest.mark.asyncio
async def test_a_pass_says_when_the_sources_were_last_checked(monkeypatch, clean_sender, live):
    """It is a real answer from FMP, so the sheet's "updated" moves to it, as for an own fetch."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    _lands(live, TUE_2005, eps=0.40, day=TUESDAY)

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                      row=_beat_on(TUESDAY))

    assert live.snapshot("INTC", TUESDAY, WED_1600).checked_at == WED_1600


@pytest.mark.asyncio
async def test_the_pass_reads_no_figure_from_tomorrows_rows(monkeypatch, clean_sender, live):
    """Tomorrow is read for the upcoming pass only. Actuals on a date not reached are garbage."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    tomorrow = WED_1600.date() + timedelta(days=1)

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=tomorrow,
                      row=_beat_on(tomorrow))

    assert live.snapshot("INTC", tomorrow, WED_1600) is None


@pytest.mark.asyncio
async def test_a_pass_with_live_results_off_writes_no_record(monkeypatch, clean_sender, live):
    """No loop runs with the switch off, so nothing would ever prune it, and the sheet reads the
    memo alone. The pass tells people exactly as it did before there were records."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", False)

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                      row=_beat_on(TUESDAY))

    assert len(phones.buzzes) == 1
    assert live.snapshot("INTC", TUESDAY, WED_1600) is None


@pytest.mark.asyncio
async def test_the_record_is_already_new_when_the_first_buzz_goes_out(
        monkeypatch, clean_sender, live):
    """A tap on the banner lands inside the fan-out, so the record cannot come after it."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _lands(live, TUE_2005, eps=0.40, day=TUESDAY)
    seen = []
    real_notify = svc.notify_watchers

    async def _notify(**kw):
        seen.append(live.snapshot("INTC", TUESDAY, WED_1600).eps.actual)
        return await real_notify(**kw)

    svc.notify_watchers = _notify
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                      row=_beat_on(TUESDAY))

    assert seen == [0.60]


@pytest.mark.asyncio
async def test_the_scheduled_pass_with_no_clock_handed_in_folds_too(
        monkeypatch, clean_sender, live):
    """The lifespan loop calls the pass with no `now`, unlike every other test here, so it runs
    on the real clock. The day is read off that clock for the same reason."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    today = daily._et_today()

    await _daily_pass(monkeypatch, None, day=today, row=_beat_on(today))

    held = live.snapshot("INTC", today, datetime.now(timezone.utc))
    assert held is not None and held.eps.actual == 0.60
    assert held.checked_at is not None and held.checked_at.tzinfo is not None


@pytest.mark.asyncio
async def test_a_record_that_will_not_fold_costs_the_pass_nothing(
        monkeypatch, clean_sender, live, caplog):
    """The buzz is the pass's job, and the row keeps its own text when no record says otherwise.
    A bug in the fold is logged with the day, and tells nobody less."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)

    def _boom(*a, **k):
        raise RuntimeError("fold exploded")

    monkeypatch.setattr(live, "observe_rows", _boom)
    with caplog.at_level(logging.WARNING):
        stats = await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                                  row=_beat_on(TUESDAY))

    assert stats["results"] == 1 and len(phones.buzzes) == 1
    assert any("RuntimeError" in r.getMessage() and TUESDAY.isoformat() in r.getMessage()
               for r in caplog.records)


# ── a report FMP lists twice ─────────────────────────────────────────────────
#
# The owner's choice, 2026-11-11: "Follow the record". FMP can list one report twice, one actual
# beside two consensus figures. The record reads the row FMP touched last, and the 16:00 ET pass
# tells the report as that record reads it once it has folded what it read, so New York's banner,
# Berlin's held row and the sheet both open say one thing, whichever row FMP lists first and
# however the push switches stand. The record can hold what neither row says: the consensus it
# read in the morning, or a consensus the zero rules weigh a row of zeros against. That last holds
# for a report listed once too, so one whose EPS is 0 is told as its record reads it as well.

def _twins(older: tuple, newer: tuple, *, newer_first: bool) -> List[Dict[str, Any]]:
    """INTC's Tuesday report listed twice, each row (estimate, actual) beside the same revenue,
    the second touched by FMP ten minutes after the first."""
    rows = [{**_fmp_row(TUESDAY, eps=actual, revenue=17.1e9), "epsEstimated": estimate,
             "lastUpdated": f"{TUESDAY.isoformat()} {touched}"}
            for (estimate, actual), touched in ((older, "13:40:00"), (newer, "13:50:00"))]
    return rows[::-1] if newer_first else rows


async def _flush_every_wake(svc, ledger, monkeypatch) -> None:
    """The flush at each held row's wake, the earliest first, until no row is held."""
    for _ in range(len(ledger.rows) * (pds.PushDispatchService.MAX_FLUSH_ATTEMPTS + 2)):
        held = [row for row in ledger.rows if row["push_state"] == "deferred"]
        if not held:
            return
        _clock_at(monkeypatch, min(_stamp(row["deliver_after"]) for row in held))
        await svc.flush_deferred()
    assert not [row for row in ledger.rows if row["push_state"] == "deferred"], "a row stays held"


@pytest.mark.asyncio
@pytest.mark.parametrize("result_push", [False, True], ids=["as_shipped", "result_push_on"])
@pytest.mark.parametrize("newer_first", [False, True],
                         ids=["older_listed_first", "newer_listed_first"])
@pytest.mark.parametrize("older,newer,told,surprise", [
    ((0.41, 0.60), (0.70, 0.60), "EPS missed consensus by 14%. Tap for the full picture.", -14.29),
    ((0.70, 0.60), (0.41, 0.60), "EPS beat consensus by 46%. Tap for the full picture.", 46.34),
    ((0.41, 0.30), (0.41, 0.60), "EPS beat consensus by 46%. Tap for the full picture.", 46.34),
    ((0.41, 0.60), (0.41, 0.40), None, -2.44),
    ((0.41, 0.60), (None, 0.60), "EPS beat consensus by 46%. Tap for the full picture.", 46.34),
    ((0.41, 0.60), (None, 0.44), "EPS beat consensus by 7%. Tap for the full picture.", 7.32),
], ids=["the_newer_consensus_says_missed", "the_newer_consensus_says_beat",
        "a_stale_miss_beside_the_newer_beat", "a_stale_beat_beside_a_newer_figure_under_the_bar",
        "the_newer_row_has_no_estimate_of_its_own",
        "a_newer_figure_with_no_estimate_of_its_own"])
async def test_a_report_fmp_lists_twice_reads_one_way_on_every_phone_and_on_the_sheet(
        monkeypatch, clean_sender, live, result_push, newer_first, older, newer, told, surprise):
    """Seen pending, then both rows at 20:05 on Tuesday, then Wednesday's 16:00 ET pass over the
    same two. New York is told at once, Berlin at 07:00 on its own clock, by whichever sender
    reaches the report first; a figure under the bar is told to nobody. A newer row with no
    estimate of its own leaves the record on the 0.41 it read while the report was pending, and
    the pass tells what the record says."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=TO_BERLIN, users=tuple(TO_BERLIN))
    _set_result_push(monkeypatch, result_push)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    twins = _twins(older, newer, newer_first=newer_first)

    live.observe_rows(TUESDAY, [_fmp_row(TUESDAY)], TUE_2005 - timedelta(hours=8), fetched=True)
    live.observe_rows(TUESDAY, twins, TUE_2005, fetched=True)
    await sender.dispatch_landings(live.landed_snapshots(TUE_2005),
                                   _clock_at(monkeypatch, TUE_2005))
    await _flush_every_wake(svc, ledger, monkeypatch)
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY, rows=twins)
    await _flush_every_wake(svc, ledger, monkeypatch)

    expected = [told] if told else []
    assert [buzz["body"] for buzz in phones.of("ny")] == expected
    assert [buzz["body"] for buzz in phones.of("berlin")] == expected
    assert sorted((row["user_id"], row["body"]) for row in ledger.rows) == (
        [("berlin", told), ("ny", told)] if told else [])
    answer = await _tap(monkeypatch, {"ticker": "INTC", "report_date": TUESDAY.isoformat()},
                        WED_1600)
    assert (answer.eps.actual, answer.eps.surprise_pct) == (newer[1], surprise)


@pytest.mark.asyncio
@pytest.mark.parametrize("revenue_first", [False, True],
                         ids=["eps_row_listed_first", "revenue_row_listed_first"])
async def test_an_eps_row_beside_a_newer_revenue_only_twin_reads_one_way_everywhere(
        monkeypatch, clean_sender, live, revenue_first):
    """The card's one row is the newer, revenue-only twin, but the EPS comes from the other row:
    its 7% beat is what every phone is told and what the sheet shows."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=TO_BERLIN, users=tuple(TO_BERLIN))
    _flags_as_shipped(monkeypatch)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    eps_row = {**_fmp_row(TUESDAY, eps=0.44), "lastUpdated": f"{TUESDAY.isoformat()} 13:40:00"}
    revenue_row = {**_fmp_row(TUESDAY, revenue=17.1e9),
                   "lastUpdated": f"{TUESDAY.isoformat()} 13:50:00"}
    rows = [revenue_row, eps_row] if revenue_first else [eps_row, revenue_row]

    live.observe_rows(TUESDAY, [_fmp_row(TUESDAY)], TUE_2005 - timedelta(hours=8), fetched=True)
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY, rows=rows)
    await _flush_every_wake(svc, ledger, monkeypatch)

    told = ["EPS beat consensus by 7%. Tap for the full picture."]
    assert [buzz["body"] for buzz in phones.of("ny")] == told
    assert [buzz["body"] for buzz in phones.of("berlin")] == told
    answer = await _tap(monkeypatch, {"ticker": "INTC", "report_date": TUESDAY.isoformat()},
                        WED_1600)
    assert (answer.eps.actual, answer.eps.surprise_pct, answer.revenue.actual) == (
        0.44, 7.32, 17.1e9)


TUE_0900 = datetime(2026, 11, 10, 9, 0, tzinfo=ET)


def _smol(eps, revenue, revenue_estimate) -> Dict[str, Any]:
    """SMOL's Tuesday report: a small name expected to lose 5 cents a share. FMP's own
    `lastUpdated` is a date, so two rows of one day tie on it."""
    return {"symbol": "SMOL", "date": TUESDAY.isoformat(), "time": "amc",
            "companyName": "Smol Co", "epsEstimated": -0.05, "epsActual": eps,
            "revenueEstimated": revenue_estimate, "revenueActual": revenue,
            "lastUpdated": TUESDAY.isoformat()}


@pytest.mark.asyncio
@pytest.mark.parametrize("zero_first", [True, False],
                         ids=["zeros_listed_first", "zeros_listed_second"])
@pytest.mark.parametrize("morning,told,status,eps,surprise", [
    (105e6, None, STATUS_AWAITING, None, None),
    (None, "EPS beat consensus by 100%. Tap for the full picture.", STATUS_REPORTED, 0.0, 100.0),
], ids=["a_morning_consensus_over_the_floor", "no_earlier_answer"])
async def test_a_row_of_zeros_listed_twice_is_told_only_when_its_record_shows_the_zero(
        monkeypatch, clean_sender, live, zero_first, morning, told, status, eps, surprise):
    """FMP's row of zeros, with no revenue consensus of its own, beside a twin stating $95M, under
    the $100M floor. The record judges the zero against the consensus it has held too: after a
    morning read of $105M it is FMP's unfilled row and the sheet shows no EPS, so nobody is told a
    100% beat. With no earlier answer the $95M stands behind it, a small name's real zero."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=TO_BERLIN, users=tuple(TO_BERLIN))
    _flags_as_shipped(monkeypatch)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    if morning is not None:
        live.observe_rows(TUESDAY, [{**_smol(None, None, morning), "lastUpdated": "2026-11-09"}],
                          TUE_0900, fetched=True)
    zeros, twin = _smol(0.0, 0, None), _smol(None, None, 95e6)

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                      rows=[zeros, twin] if zero_first else [twin, zeros])
    await _flush_every_wake(svc, ledger, monkeypatch)

    expected = [told] if told else []
    assert [buzz["body"] for buzz in phones.of("ny")] == expected
    assert [buzz["body"] for buzz in phones.of("berlin")] == expected
    answer = await _tap(monkeypatch, {"ticker": "SMOL", "report_date": TUESDAY.isoformat()},
                        WED_1600)
    assert (answer.status, answer.eps.actual, answer.eps.surprise_pct) == (status, eps, surprise)


@pytest.mark.asyncio
@pytest.mark.parametrize("listed", ["beside_a_pending_twin", "the_pending_twin_first", "twice",
                                    "once"])
async def test_a_row_of_zeros_its_record_keeps_on_an_earlier_consensus_is_told_as_it_is_kept(
        monkeypatch, clean_sender, live, listed):
    """No row of Wednesday's answer states a revenue consensus, so read alone the zeros are FMP's
    unfilled row. The record still holds the $50M it read on Tuesday morning, under the $100M
    floor, so it keeps them as a small name's real report and the sheet shows a 100% beat. Every
    phone is told that, however FMP lists the zeros."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=TO_BERLIN, users=tuple(TO_BERLIN))
    _flags_as_shipped(monkeypatch)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    live.observe_rows(TUESDAY, [{**_smol(None, None, 50e6), "lastUpdated": "2026-11-09"}],
                      TUE_0900, fetched=True)
    zeros, pending = _smol(0.0, 0, None), _smol(None, None, None)
    rows = {"beside_a_pending_twin": [zeros, pending], "the_pending_twin_first": [pending, zeros],
            "twice": [zeros, dict(zeros)], "once": [zeros]}[listed]

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY, rows=rows)
    await _flush_every_wake(svc, ledger, monkeypatch)

    told = ["EPS beat consensus by 100%. Tap for the full picture."]
    assert [buzz["body"] for buzz in phones.of("ny")] == told
    assert [buzz["body"] for buzz in phones.of("berlin")] == told
    answer = await _tap(monkeypatch, {"ticker": "SMOL", "report_date": TUESDAY.isoformat()},
                        WED_1600)
    assert (answer.status, answer.eps.actual, answer.eps.surprise_pct) == (
        STATUS_REPORTED, 0.0, 100.0)


TUE_1205 = datetime(2026, 11, 10, 12, 5, tzinfo=ET)


@pytest.mark.asyncio
async def test_a_zero_eps_its_record_reads_as_unfilled_is_told_to_nobody(
        monkeypatch, clean_sender, live):
    """SMOL filled on Tuesday at 20:05: a loss of 25 cents a share against 30 expected, and a
    revenue of 0 under a $30M consensus. Wednesday's pass reads one row, an EPS of 0 with no
    revenue under a $105M consensus. Read alone that is a 100% beat. Beside the 0 revenue the
    record holds, against a consensus now material, it is FMP's unfilled row, so the sheet shows
    no figure, and nobody is told one."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=TO_BERLIN, users=tuple(TO_BERLIN))
    _flags_as_shipped(monkeypatch)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)

    def _row(eps, revenue, revenue_estimate, touched):
        return {**_smol(eps, revenue, revenue_estimate), "epsEstimated": -0.30,
                "lastUpdated": touched}

    live.observe_rows(TUESDAY, [_row(None, None, 30e6, "2026-11-09")], TUE_1205, fetched=True)
    live.observe_rows(TUESDAY, [_row(-0.25, 0, 30e6, "2026-11-10")], TUE_2005, fetched=True)
    stats = await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                              rows=[_row(0.0, None, 105e6, "2026-11-11")])
    await _flush_every_wake(svc, ledger, monkeypatch)

    assert stats["results"] == 0
    assert phones.buzzes == [] and ledger.rows == []
    answer = await _tap(monkeypatch, {"ticker": "SMOL", "report_date": TUESDAY.isoformat()},
                        WED_1600)
    assert (answer.status, answer.eps.actual, answer.eps.surprise_pct) == (
        STATUS_AWAITING, None, None)


def _joby_on_tuesday(eps=None, revenue=None, *, consensus=30e6) -> Dict[str, Any]:
    """JOBY's Tuesday report, a small name under a $30M consensus unless told otherwise."""
    return {"symbol": "JOBY", "date": TUESDAY.isoformat(), "time": "amc", "companyName": "Joby",
            "epsEstimated": 0.41, "epsActual": eps, "revenueEstimated": consensus,
            "revenueActual": revenue, "lastUpdated": TUESDAY.isoformat()}


TUE_2003 = datetime(2026, 11, 10, 20, 3, tzinfo=ET)
_THE_RELEASE = FilingFigures(accession="a", accepted_at=None, url=None, revenue=33e6,
                             eps_adjusted=0.44)

#: How FMP's row of zeros reaches the record: (the consensus, when the release is read, if at
#: all, and the EPS the sheet then shows as (figure, surprise, source)).
_ZEROS_THAT_TURN_OUT_UNFILLED = {
    "stored_until_the_release_reads_after_them": (30e6, TUE_2005, (0.44, 7.32, "filing")),
    "refused_beside_the_release_read_before_them": (30e6, TUE_2003, (0.44, 7.32, "filing")),
    "refused_beside_a_material_consensus": (16.6e9, None, (None, None, None)),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("result_push", [False, True], ids=["as_shipped", "result_push_on"])
@pytest.mark.parametrize("consensus,release_at,sheet", _ZEROS_THAT_TURN_OUT_UNFILLED.values(),
                         ids=_ZEROS_THAT_TURN_OUT_UNFILLED.keys())
async def test_a_zero_fmp_sends_again_after_its_row_of_zeros_gave_way_is_never_told_as_a_miss(
        monkeypatch, clean_sender, live, result_push, consensus, release_at, sheet):
    """FMP's row of zeros at 20:05 is its unfilled row: kept as a small name's real report until
    the release, $33M and $0.44, shows it, or refused at once, beside a release already read or a
    material consensus. FMP then sends an EPS of 0 with no revenue: the same unfilled field, so
    nobody is told "missed consensus by 100%", by the live sender or by the 16:00 ET pass, and the
    sheet shows the release's $0.44, or no EPS at all."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=TO_BERLIN, users=tuple(TO_BERLIN))
    _set_result_push(monkeypatch, result_push)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    live.observe_rows(TUESDAY, [_joby_on_tuesday(consensus=consensus)], TUE_1205, fetched=True)
    if release_at == TUE_2003:
        live.absorb_filing("JOBY", TUESDAY, _THE_RELEASE, TUE_2003)
    live.observe_rows(TUESDAY, [_joby_on_tuesday(0.0, 0.0, consensus=consensus)], TUE_2005,
                      fetched=True)
    if release_at == TUE_2005:
        live.absorb_filing("JOBY", TUESDAY, _THE_RELEASE, TUE_2005)
    zero_again = _joby_on_tuesday(0.0, consensus=consensus)
    live.observe_rows(TUESDAY, [zero_again], TUE_2030, fetched=True)
    await sender.dispatch_landings(live.landed_snapshots(TUE_2030),
                                   _clock_at(monkeypatch, TUE_2030))
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                      rows=[zero_again])
    await _flush_every_wake(svc, ledger, monkeypatch)

    assert not [row for row in ledger.rows if "missed" in row["body"]], ledger.rows
    assert not [buzz for buzz in phones.buzzes if "missed" in buzz["body"]], phones.buzzes
    answer = await _tap(monkeypatch, {"ticker": "JOBY", "report_date": TUESDAY.isoformat()},
                        WED_1600)
    assert (answer.eps.actual, answer.eps.surprise_pct, answer.eps.source) == sheet


#: A field FMP sends alone as 0 before its row of zeros, and the other field it sends alone as 0
#: after them: (first, other).
_A_LONE_ZERO_BEFORE_ITS_ZEROS = {
    "its_eps": (dict(eps=0.0), dict(revenue=0.0)),
    "its_revenue": (dict(revenue=0.0), dict(eps=0.0)),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("result_push", [False, True], ids=["as_shipped", "result_push_on"])
@pytest.mark.parametrize("first,other", _A_LONE_ZERO_BEFORE_ITS_ZEROS.values(),
                         ids=_A_LONE_ZERO_BEFORE_ITS_ZEROS.keys())
async def test_a_zero_fmp_sent_alone_before_its_row_of_zeros_is_never_told_at_a_wake(
        monkeypatch, clean_sender, live, result_push, first, other):
    """JOBY's release, an EPS of $0.44 and no revenue, is read at 20:03. At 20:05 FMP sends one
    field as 0 with nothing beside it, which nothing yet says is unfilled, so the record takes it:
    an EPS of 0 lands as a breakeven, which New York, awake, is told at once with the result push
    on, the residual of a lone zero no row of zeros has come before. At 20:10 FMP's row of zeros
    is refused beside the release, so the zero it sent alone is that unfilled field too, and goes,
    and the other field's zero at 20:30 is refused as well. Berlin's row, held for its morning, is
    never told a miss its record no longer holds, nobody is told one at 16:00 ET, and the sheet
    shows the release's $0.44 with no revenue: no false -100% stays behind on either field."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=TO_BERLIN, users=tuple(TO_BERLIN))
    _set_result_push(monkeypatch, result_push)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)

    async def _fmp_answers(at, row):
        live.observe_rows(TUESDAY, [row], at, fetched=True)
        await sender.dispatch_landings(live.landed_snapshots(at), _clock_at(monkeypatch, at))

    await _fmp_answers(TUE_1205, _joby_on_tuesday())
    live.absorb_filing("JOBY", TUESDAY, FilingFigures(accession="a", accepted_at=None, url=None,
                                                      eps_adjusted=0.44), TUE_2003)
    await sender.dispatch_landings(live.landed_snapshots(TUE_2003),
                                   _clock_at(monkeypatch, TUE_2003))
    await _fmp_answers(TUE_2005, _joby_on_tuesday(**first))
    await _fmp_answers(TUE_2005 + timedelta(minutes=5), _joby_on_tuesday(0.0, 0.0))
    last = _joby_on_tuesday(**other)
    await _fmp_answers(TUE_2030, last)
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY, rows=[last])
    await _flush_every_wake(svc, ledger, monkeypatch)

    berlin = [row for row in ledger.rows if row["user_id"] == "berlin"]
    held_at_the_landing = result_push and "eps" in first
    assert len(berlin) == int(held_at_the_landing), "the row the 20:05 landing held for Berlin"
    assert not [row for row in berlin if row["push_state"] == "sent" or "missed" in row["body"]], \
        berlin
    assert phones.of("berlin") == []
    answer = await _tap(monkeypatch, {"ticker": "JOBY", "report_date": TUESDAY.isoformat()},
                        WED_1600)
    assert (answer.eps.actual, answer.eps.surprise_pct, answer.eps.source) == (
        0.44, 7.32, "filing")
    assert (answer.revenue.actual, answer.revenue.surprise_pct) == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("consensus", [16.6e9, None], ids=["material_consensus", "no_consensus"])
@pytest.mark.parametrize("live_push", [False, True], ids=["result_push", "both_pushes"])
async def test_a_zero_taken_off_with_no_release_is_never_buzzed_at_a_wake(
        monkeypatch, clean_sender, live, consensus, live_push):
    """The no-release twin of the test above. No 8-K is read. At 20:05 FMP sends JOBY's EPS as 0
    with nothing beside it, which lands as a -100% miss: New York, awake, is told at once (the
    residual of a lone zero no row of zeros has come before), and Berlin's row is held for its
    morning. At 20:10 FMP's row of zeros shows the field unfilled, so the record takes the zero
    off and holds no figure. At Berlin's wake the row is not buzzed in its landing's words, a miss
    the sheet it opens no longer shows, and its inbox text states no figure either."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=TO_BERLIN, users=tuple(TO_BERLIN))
    _set_result_push(monkeypatch, True)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", live_push)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)

    async def _fmp_answers(at, row):
        live.observe_rows(TUESDAY, [row], at, fetched=True)
        await sender.dispatch_landings(live.landed_snapshots(at), _clock_at(monkeypatch, at))

    await _fmp_answers(TUE_1205, _joby_on_tuesday(consensus=consensus))
    await _fmp_answers(TUE_2005, _joby_on_tuesday(0.0, consensus=consensus))
    await _fmp_answers(TUE_2005 + timedelta(minutes=5),
                       _joby_on_tuesday(0.0, 0.0, consensus=consensus))
    assert live.snapshot("JOBY", TUESDAY, TUE_2030).eps.actual is None, "the zero is taken off"
    last = _joby_on_tuesday(None, 0.0, consensus=consensus)
    await _fmp_answers(TUE_2030, last)
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY, rows=[last])
    await _flush_every_wake(svc, ledger, monkeypatch)

    berlin = [row for row in ledger.rows if row["user_id"] == "berlin"]
    assert len(berlin) == 1, "the row the 20:05 landing held for Berlin"
    assert berlin[0]["push_state"] != "sent" and "missed" not in berlin[0]["body"], berlin
    assert phones.of("berlin") == []
    # It waited at every look, not just the first, and ended at the bound in words that state
    # no figure.
    assert (berlin[0]["push_state"], berlin[0]["attempts"], (berlin[0]["title"],
            berlin[0]["body"]), berlin[0].get("last_error")) == (
        "failed", pds.PushDispatchService.MAX_FLUSH_ATTEMPTS, _NO_FIGURE,
        "suppressed on flush: a figure is still being confirmed")
    answer = await _tap(monkeypatch, {"ticker": "JOBY", "report_date": TUESDAY.isoformat()},
                        WED_1600)
    assert (answer.eps.actual, answer.eps.surprise_pct) == (None, None)


#: What a held JOBY row says once its record has taken off the zero it stated.
_NO_FIGURE = ("JOBY results are out", "Revenue not out yet · EPS not out yet.")
_JOBY_ROUTE = {"ticker": "JOBY", "report_date": TUESDAY.isoformat()}
_MATERIAL = 16.6e9
WED_0100 = datetime(2026, 11, 11, 1, 0, tzinfo=ET)


def _joby_zero_taken_off(live) -> None:
    """JOBY pending at noon, its EPS of 0 sent alone at 20:05 (it lands), and FMP's row of zeros
    at 20:10, which shows that zero unfilled: the record takes it off and holds no figure."""
    for at, row in ((TUE_1205, _joby_on_tuesday(consensus=_MATERIAL)),
                    (TUE_2005, _joby_on_tuesday(0.0, consensus=_MATERIAL)),
                    (TUE_2005 + timedelta(minutes=5),
                     _joby_on_tuesday(0.0, 0.0, consensus=_MATERIAL))):
        live.observe_rows(TUESDAY, [row], at, fetched=True)


async def _joby_zero_taken_off_and_told(monkeypatch, live) -> None:
    """`_joby_zero_taken_off`, each answer handed to the sender as the tick would."""
    for at, row in ((TUE_1205, _joby_on_tuesday(consensus=_MATERIAL)),
                    (TUE_2005, _joby_on_tuesday(0.0, consensus=_MATERIAL)),
                    (TUE_2005 + timedelta(minutes=5),
                     _joby_on_tuesday(0.0, 0.0, consensus=_MATERIAL))):
        live.observe_rows(TUESDAY, [row], at, fetched=True)
        await sender.dispatch_landings(live.landed_snapshots(at), _clock_at(monkeypatch, at))


def _fmp_moves_joby_off_tuesday(live) -> None:
    """Two own fetches of Tuesday, the end's hold apart, that list another company and leave JOBY
    out (A REPORT FMP MOVES): with no figure left to keep it, JOBY's record ends."""
    other = {**_joby_on_tuesday(), "symbol": "ACME", "companyName": "Acme"}
    live._end_unlisted(TUESDAY, [other])
    live._records[("JOBY", TUESDAY)].unlisted_since -= EARNINGS_LIVE_UNLISTED_END_SECONDS
    live._end_unlisted(TUESDAY, [other])
    assert ("JOBY", TUESDAY) not in live._records, "the record ends"


def _berlin_held(monkeypatch, *, live_push: bool):
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=TO_BERLIN, users=tuple(TO_BERLIN))
    _set_result_push(monkeypatch, True)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", live_push)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    return ledger, phones, svc


async def _berlins_first_wake(monkeypatch, svc, ledger) -> datetime:
    (berlin,) = ledger.of("berlin")
    wake = _stamp(berlin["deliver_after"])
    _clock_at(monkeypatch, wake)
    await svc.flush_deferred()
    return wake


_KINDS = pytest.mark.parametrize("live_push", [False, True], ids=["result_row", "live_row"])


@pytest.mark.parametrize("ended", [False, True], ids=["record_holds_nothing", "record_ended"])
@pytest.mark.parametrize("kind", [KIND_EARNINGS_RESULT, KIND_EARNINGS_LIVE])
def test_a_wake_on_a_figure_taken_off_asks_again_and_books_its_read(clean_sender, live, kind,
                                                                     ended):
    """The record took off the zero a held row states, and holds nothing, or an own fetch has
    ended it since. Either way the wake keeps the row off the phone in words that state no
    figure, asks again an hour on, and books the loop's read of the report just before that
    look. A record that never held a figure says nothing against the row, as before."""
    _joby_zero_taken_off(live)
    if ended:
        _fmp_moves_joby_off_tuesday(live)
    assert live.ended_after_a_figure("JOBY", TUESDAY) is ended
    wake = sender.copy_at_wake(kind, _JOBY_ROUTE, WED_0100)
    assert wake == sender.WakeCopy(*_NO_FIGURE, send=False, ask_again_in=sender.WAKE_ASK_AGAIN)
    assert live._looks == {("JOBY", TUESDAY): {WED_0100 + sender.WAKE_ASK_AGAIN}}

    live.reset()
    live.observe_rows(TUESDAY, [_joby_on_tuesday(consensus=_MATERIAL)], TUE_1205, fetched=True)
    assert sender.copy_at_wake(kind, _JOBY_ROUTE, WED_0100) is None, "a record that held none"


@pytest.mark.asyncio
@_KINDS
@pytest.mark.parametrize("moved", ["before_the_wake", "between_looks"])
async def test_a_report_fmp_moves_after_its_zero_is_taken_off_is_never_buzzed(
        monkeypatch, clean_sender, live, live_push, moved):
    """The zero is taken off and FMP then moves JOBY off Tuesday, so own fetches end its record,
    which holds no figure to keep it: before Berlin's wake, or between two of its looks. The
    record is gone, but the service remembers it ended after a figure, so the zero is never
    buzzed and the row ends at the bound in words that state none."""
    ledger, phones, svc = _berlin_held(monkeypatch, live_push=live_push)
    await _joby_zero_taken_off_and_told(monkeypatch, live)
    if moved == "before_the_wake":
        _fmp_moves_joby_off_tuesday(live)
    else:
        await _berlins_first_wake(monkeypatch, svc, ledger)
        _fmp_moves_joby_off_tuesday(live)
    await _flush_every_wake(svc, ledger, monkeypatch)

    assert phones.of("berlin") == []
    (berlin,) = ledger.of("berlin")
    assert (berlin["push_state"], (berlin["title"], berlin["body"])) == ("failed", _NO_FIGURE)


@pytest.mark.asyncio
@_KINDS
@pytest.mark.parametrize("after", ["record_gone", "record_rebuilt"])
async def test_a_row_held_for_a_figure_taken_off_waits_through_a_restart(
        monkeypatch, clean_sender, live, live_push, after):
    """Berlin's first look holds the row in words that state no figure. A restart then forgets
    the record, or rebuilds it from FMP's next answer, which holds no figure either. The row the
    wake held waits again at every later look (`_left_waiting`: an earnings_live row by its
    figure-less words, an earnings_result row by its title), and is never buzzed."""
    ledger, phones, svc = _berlin_held(monkeypatch, live_push=live_push)
    await _joby_zero_taken_off_and_told(monkeypatch, live)
    wake = await _berlins_first_wake(monkeypatch, svc, ledger)
    (berlin,) = ledger.of("berlin")
    assert (berlin["push_state"], (berlin["title"], berlin["body"])) == ("deferred", _NO_FIGURE)
    live.reset()
    sender.reset()
    if after == "record_rebuilt":
        live.observe_rows(TUESDAY, [_joby_on_tuesday(None, 0.0, consensus=_MATERIAL)],
                          wake + timedelta(minutes=30), fetched=True)
    await _flush_every_wake(svc, ledger, monkeypatch)

    assert phones.of("berlin") == []
    (berlin,) = ledger.of("berlin")
    assert berlin["push_state"] == "failed" and "missed" not in berlin["body"], berlin


@pytest.mark.asyncio
async def test_a_live_row_a_wake_held_in_doubt_waits_through_a_restart(monkeypatch, clean_sender,
                                                                       live):
    """A live row whose EPS FMP then puts in doubt (a dropped digit), revenue never in, is held
    at its 07:00 wake as 'being confirmed'. A restart forgets the record: the row waits again,
    never buzzed in that wake's words, which state no figure."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    await sender.dispatch_landings(_lands(live, THU_2210, revenue=None),
                                   _clock_at(monkeypatch, THU_2210))
    _fmp_says(live, THU_2340, eps=0.041, revenue=None)
    _clock_at(monkeypatch, FRI_0700)
    await svc.flush_deferred()
    held = [row for row in ledger.rows if row["push_state"] == "deferred"]
    assert held and all(sender._states_no_figure(row["body"]) for row in held), ledger.rows
    live.reset()
    sender.reset()
    await _flush_every_wake(svc, ledger, monkeypatch)

    assert phones.buzzes == [], phones.buzzes
    assert all(row["push_state"] == "failed" for row in ledger.rows), ledger.rows


@pytest.mark.asyncio
@_KINDS
async def test_a_figure_back_after_its_zero_was_taken_off_is_told_at_the_next_look(
        monkeypatch, clean_sender, live, live_push):
    """FMP fills JOBY's real EPS, $0.45 against $0.41, after Berlin's first look held the row:
    the next look tells it, once, in the words the sheet shows."""
    ledger, phones, svc = _berlin_held(monkeypatch, live_push=live_push)
    await _joby_zero_taken_off_and_told(monkeypatch, live)
    wake = await _berlins_first_wake(monkeypatch, svc, ledger)
    assert phones.of("berlin") == []
    live.observe_rows(TUESDAY, [_joby_on_tuesday(0.45, consensus=_MATERIAL)],
                      wake + timedelta(minutes=30), fetched=True)
    await _flush_every_wake(svc, ledger, monkeypatch)

    (buzz,) = phones.of("berlin")
    assert "EPS beat consensus by 10%" in buzz["body"], buzz
    (berlin,) = ledger.of("berlin")
    assert berlin["push_state"] == "sent"


@pytest.mark.asyncio
@_KINDS
async def test_a_zero_a_new_process_first_sees_then_takes_off_is_never_buzzed(
        monkeypatch, clean_sender, live, live_push):
    """The zero lands and Berlin's row is held. A restart comes before FMP's row of zeros, and
    the new process first sees the zero already reported (no landing of its own) before the
    zeros take it off. It held that figure all the same, so the wake holds the row."""
    ledger, phones, svc = _berlin_held(monkeypatch, live_push=live_push)
    for at, row in ((TUE_1205, _joby_on_tuesday(consensus=_MATERIAL)),
                    (TUE_2005, _joby_on_tuesday(0.0, consensus=_MATERIAL))):
        live.observe_rows(TUESDAY, [row], at, fetched=True)
        await sender.dispatch_landings(live.landed_snapshots(at), _clock_at(monkeypatch, at))
    assert len(ledger.of("berlin")) == 1
    live.reset()
    sender.reset()
    for minutes, row in ((10, _joby_on_tuesday(0.0, consensus=_MATERIAL)),
                         (15, _joby_on_tuesday(0.0, 0.0, consensus=_MATERIAL))):
        at = TUE_2005 + timedelta(minutes=minutes)
        live.observe_rows(TUESDAY, [row], at, fetched=True)
        await sender.dispatch_landings(live.landed_snapshots(at), _clock_at(monkeypatch, at))
    assert live.snapshot("JOBY", TUESDAY, WED_0100).eps.actual is None
    await _flush_every_wake(svc, ledger, monkeypatch)

    assert phones.of("berlin") == []
    (berlin,) = ledger.of("berlin")
    assert (berlin["push_state"], (berlin["title"], berlin["body"])) == ("failed", _NO_FIGURE)


@pytest.mark.parametrize("body,waits", [
    ("Revenue not out yet · EPS not out yet.", True),
    ("Revenue not out yet · EPS being confirmed.", True),
    ("Revenue being confirmed · GAAP EPS not out yet.", True),
    ("Revenue not out yet · EPS missed consensus by 100%.", False),
    ("Revenue $18.5B (beat consensus by 2%) · EPS not out yet. From the company's release.", False),
    ("Revenue being confirmed · EPS reported.", False),
    ("Revenue not out yet · EPS in line with consensus.", False),
], ids=["nothing", "eps_in_doubt", "revenue_in_doubt_gaap", "a_zero_stated", "a_release_figure",
        "eps_reported", "in_line"])
def test_only_a_live_row_whose_words_state_no_figure_reads_as_left_waiting(body, waits):
    assert sender._left_waiting(KIND_EARNINGS_LIVE, "JOBY", ("JOBY results are out", body)) is waits
    assert sender._left_waiting(KIND_EARNINGS_LIVE, "JOBY", None) is False


@pytest.mark.asyncio
async def test_a_report_listed_twice_is_told_from_its_row_when_its_record_will_not_fold(
        monkeypatch, clean_sender, live):
    """The record still holds Tuesday's single row, 0.60 against 0.41, which the pass could not
    replace with what it read, though Wednesday's fold went through. A record older than the
    pass is no record to follow: the pass tells from the row the record would have read, FMP's
    newer consensus."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    _flags_as_shipped(monkeypatch)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    live.observe_rows(TUESDAY, [_fmp_row(TUESDAY)], TUE_2005 - timedelta(hours=8), fetched=True)
    live.observe_rows(TUESDAY, [_beat_on(TUESDAY)], TUE_2005, fetched=True)
    fold = live.observe_rows

    def _tuesday_explodes(day, rows, *a, **k):
        if day == TUESDAY:
            raise RuntimeError("fold exploded")
        return fold(day, rows, *a, **k)

    monkeypatch.setattr(live, "observe_rows", _tuesday_explodes)
    stats = await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                              rows=_twins((0.41, 0.60), (0.70, 0.60), newer_first=False))

    assert stats["results"] == 1
    assert [buzz["body"] for buzz in phones.buzzes] == [
        "EPS missed consensus by 14%. Tap for the full picture."]


@pytest.mark.asyncio
async def test_a_live_record_that_cannot_be_read_leaves_a_report_listed_twice_its_row(
        monkeypatch, clean_sender, live, caplog):
    """A failed read of the record is logged with the report, and the pass tells from the row
    the record would read: nobody is told less because of it."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    _flags_as_shipped(monkeypatch)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)

    def _boom(*a, **k):
        raise RuntimeError("snapshot exploded")

    monkeypatch.setattr(live, "snapshot", _boom)
    with caplog.at_level(logging.WARNING, logger=daily.__name__):
        stats = await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                                  rows=_twins((0.41, 0.60), (0.70, 0.60), newer_first=False))

    assert stats["results"] == 1
    assert [buzz["body"] for buzz in phones.buzzes] == [
        "EPS missed consensus by 14%. Tap for the full picture."]
    assert any("RuntimeError" in r.getMessage() and "INTC" in r.getMessage()
               and TUESDAY.isoformat() in r.getMessage() for r in caplog.records)


# ── a figure still being confirmed at the wake ───────────────────────────────
#
# "checking" is no answer: the guard holds a figure until FMP says what it is. So it cannot end
# the row the way a figure that moved does. The row asks again, a bounded number of times.

@pytest.mark.asyncio
async def test_a_figure_being_confirmed_at_the_wake_is_asked_about_again(
        monkeypatch, clean_sender, live):
    """0.60 is parked, FMP drops a digit overnight and recovers just after the wake. One buzz,
    the true one, and the inbox, the sheet and the daily pass agree with it."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    _fmp_says(live, THU_2340, eps=0.041)

    _clock_at(monkeypatch, FRI_0700)
    stats = await svc.flush_deferred()

    (row,) = ledger.rows
    assert (stats["sent"], stats["suppressed"], stats["requeued"]) == (0, 0, 1)
    assert phones.buzzes == [] and row["push_state"] == "deferred"
    assert _stamp(row["deliver_after"]) == FRI_0700 + timedelta(hours=1)
    assert row["body"] == "Revenue beat consensus by 3% · EPS being confirmed."

    _fmp_says(live, FRI_0700 + timedelta(minutes=30), eps=0.60)
    assert (await svc.flush_deferred())["claimed"] == 0, "it waits for its new time"
    _clock_at(monkeypatch, FRI_0700 + timedelta(hours=1))
    stats = await svc.flush_deferred()

    assert (stats["sent"], stats["suppressed"]) == (1, 0)
    (buzz,) = phones.buzzes
    assert buzz["body"] == "EPS beat consensus by 46%. Tap for the full picture."
    assert row["body"] == buzz["body"] and row["push_state"] == "sent"
    answer = await _tap(monkeypatch, buzz["data"], FRI_0700 + timedelta(hours=1))
    assert (answer.status, answer.eps.actual, answer.eps.surprise_pct) == ("reported", 0.60, 46.34)

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, FRI_1600))
    assert len(phones.buzzes) == 1 and len(ledger.rows) == 1


@pytest.mark.asyncio
async def test_a_figure_that_settles_on_a_small_surprise_ends_the_row_then(
        monkeypatch, clean_sender, live):
    """Asking again is for an answer, and this is one: no buzz, and the row says so."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    _fmp_says(live, THU_2340, eps=0.041)
    _clock_at(monkeypatch, FRI_0700)
    await svc.flush_deferred()

    _fmp_says(live, FRI_0700 + timedelta(minutes=30), eps=0.42)
    _clock_at(monkeypatch, FRI_0700 + timedelta(hours=1))
    stats = await svc.flush_deferred()

    assert (stats["sent"], stats["suppressed"], stats["requeued"]) == (0, 1, 0)
    (row,) = ledger.rows
    assert row["push_state"] == "failed" and phones.buzzes == []
    assert "46%" not in row["body"] and "being confirmed" not in row["body"]


@pytest.mark.asyncio
async def test_a_figure_never_confirmed_ends_the_row_without_a_buzz(
        monkeypatch, clean_sender, live):
    """The hold has an end. The row keeps saying what is known, and never the doubted number."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    _fmp_says(live, THU_2340, eps=0.041)

    wakes = await _flush_until_settled(svc, ledger, monkeypatch)

    (row,) = ledger.rows
    assert len(wakes) == row["attempts"] == pds.PushDispatchService.MAX_FLUSH_ATTEMPTS
    assert phones.buzzes == [] and row["push_state"] == "failed"
    assert row["body"] == "Revenue beat consensus by 3% · EPS being confirmed."
    assert row["last_error"] == "suppressed on flush: a figure is still being confirmed"


FRI_2220 = datetime(2026, 11, 6, 22, 20, tzinfo=ET)
FRI_2240 = datetime(2026, 11, 6, 22, 40, tzinfo=ET)
SAT_0815 = datetime(2026, 11, 7, 8, 15, tzinfo=ET)
#: After the loop's first read of Friday on Saturday: done with Friday at 22:00 ET, it reads
#: the report again at the closed day's 2 h pace, at midnight.
SAT_0030 = datetime(2026, 11, 7, 0, 30, tzinfo=ET)


class _OvernightMarket:
    """FMP over INTC's Friday report: its EPS filled at 22:20 with the revenue still to come, a
    dropped digit from 00:30 on Saturday, its own figure again from `recovers`. The loop asks
    nothing after 22:00, so it reads the fill at its first Saturday read, at midnight, and the
    result lands in the night. The revenue FMP still owes keeps the loop asking at the closed
    day's 2 h pace (a report FMP has confirmed whole is asked no more), so its 02:00, 04:00 and
    06:00 reads find the dropped digit. Every day lists a pending row of another report, as a
    real calendar does, so no first look comes back blank."""

    def __init__(self, recovers: datetime):
        self.recovers = recovers
        self.at = FRI_2220
        self.reads: List[tuple] = []        # (the walk's minute, the day asked)

    def _intc(self) -> Dict[str, Any]:
        eps = (None if self.at < FRI_2220 else 0.60 if self.at < SAT_0030
               else 0.041 if self.at < self.recovers else 0.60)
        return {**_fmp_row(FRIDAY, eps=eps, revenue=None),
                "lastUpdated": FRIDAY.isoformat()}

    async def get_earnings_calendar(self, from_date=None, to_date=None):
        self.reads.append((self.at, date.fromisoformat(from_date)))
        rows = [{**_fmp_row(date.fromisoformat(from_date)), "symbol": "ZZZZ"}]
        return rows + [self._intc()] if from_date == FRIDAY.isoformat() else rows


async def _walk_the_night(monkeypatch, svc, market: _OvernightMarket, start: datetime,
                          end: datetime) -> List[datetime]:
    """The real FMP tick and the real flush, minute by minute, as the loops run them, on a clock
    the walk moves for the live records and the memo too. Returns the minutes a phone buzzed."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_FILINGS_ENABLED", False)
    clock = SimpleNamespace(at=1_000_000.0)
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=lambda: clock.at))
    monkeypatch.setattr(ts, "_time", SimpleNamespace(monotonic=lambda: clock.at))
    phones = svc._push
    buzzed: List[datetime] = []
    at = start
    while at < end:
        clock.at += 60
        market.at = at
        monkeypatch.setattr(ts, "_et_today", lambda day=at.date(): day)
        _clock_at(monkeypatch, at)
        before = len(phones.buzzes)
        await els.earnings_live_tick(at, fmp=market, watched=["INTC"])
        await svc.flush_deferred()
        buzzed += [at] * (len(phones.buzzes) - before)
        at += timedelta(minutes=1)
    return buzzed


_SATURDAY_LOOKS = [datetime(2026, 11, 7, hour, 0, tzinfo=ET) for hour in (7, 8, 9)]


@pytest.mark.asyncio
@pytest.mark.parametrize("recovers,heard_by", [
    (SAT_0815, _SATURDAY_LOOKS[2]),
    (datetime(2026, 11, 7, 7, 30, tzinfo=ET), _SATURDAY_LOOKS[1]),
    (datetime(2026, 11, 7, 6, 59, tzinfo=ET), _SATURDAY_LOOKS[1]),
], ids=["between_the_second_and_last_look", "between_the_first_two_looks", "before_the_wake"])
async def test_a_closed_day_hold_hears_fmps_recovery_before_its_next_look(
        monkeypatch, clean_sender, live, recovers, heard_by):
    """A closed day reads Friday's report only every 2 h, so the looks at 07:00, 08:00 and 09:00
    all read one dropped-digit answer and the row ended unbuzzed with FMP back at 0.60 since
    08:15. Each look that asks again now has the report read just before the next one, so a
    recovery is heard by the first look whose read comes after it, at the latest."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    market = _OvernightMarket(recovers)

    buzzed = await _walk_the_night(monkeypatch, svc, market, datetime(2026, 11, 6, 21, 30, tzinfo=ET),
                                   datetime(2026, 11, 7, 10, 30, tzinfo=ET))

    (row,) = ledger.rows
    assert len(buzzed) == 1, (buzzed, row.get("last_error"))
    assert buzzed[0] in _SATURDAY_LOOKS and buzzed[0] <= heard_by, buzzed
    assert phones.buzzes[0]["body"] == "EPS beat consensus by 46%. Tap for the full picture."


async def _walk_by_the_second(monkeypatch, svc, market: _OvernightMarket, start: datetime,
                              end: datetime, *, interval: int, tick_at: int, flush_at: int,
                              lag: int) -> List[datetime]:
    """The real FMP tick every `interval` seconds from `tick_at` and the real flush every minute
    from `flush_at`, each on its own clock as the two loops run, to the second. The flush reads
    its clock as it starts and reaches the held row `lag` seconds later, as it does a row far into
    a hand-out of many. Returns the instants a phone buzzed."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_FILINGS_ENABLED", False)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_INTERVAL_SECONDS", interval)
    base = 1_000_000.0
    clock = SimpleNamespace(at=base)
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=lambda: clock.at))
    monkeypatch.setattr(ts, "_time", SimpleNamespace(monotonic=lambda: clock.at))
    phones = svc._push
    buzzed: List[datetime] = []
    total = int((end - start).total_seconds())
    ticks, flushes = range(tick_at, total, interval), range(flush_at, total, 60)
    for second in sorted(set(ticks) | set(flushes)):
        at = start + timedelta(seconds=second)
        _clock_at(monkeypatch, at)
        if second in ticks:
            clock.at = base + second
            market.at = at
            monkeypatch.setattr(ts, "_et_today", lambda day=at.date(): day)
            await els.earnings_live_tick(at, fmp=market, watched=["INTC"])
        if second in flushes:
            clock.at = base + second + lag
            before = len(phones.buzzes)
            await svc.flush_deferred()
            buzzed += [at] * (len(phones.buzzes) - before)
    return buzzed


@pytest.mark.asyncio
@pytest.mark.parametrize("interval,tick_at,flush_at,lag", [
    (15, 2, 31, 20), (60, 10, 0, 115), (60, 59, 0, 150), (300, 7, 45, 90),
], ids=["the_fastest_loop", "the_usual_loop", "the_usual_loop_a_tick_later",
        "the_slowest_loop"])
async def test_a_row_reached_late_in_its_hand_out_has_its_report_read_before_its_next_look(
        monkeypatch, clean_sender, live, interval, tick_at, flush_at, lag):
    """The flush holds a row to the clock it read as it started, plus an hour. A row reached
    `lag` seconds into a long hand-out timed its read from that later moment instead, so the read
    landed just after the look it was for, every hour, and the row ended unbuzzed although FMP was
    back at 0.60 from 08:15. Timed from the hold itself, the 09:00 look reads the recovery."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    market = _OvernightMarket(SAT_0815)

    buzzed = await _walk_by_the_second(
        monkeypatch, svc, market, datetime(2026, 11, 6, 21, 30, tzinfo=ET),
        datetime(2026, 11, 7, 10, 30, tzinfo=ET), interval=interval, tick_at=tick_at,
        flush_at=flush_at, lag=lag)

    (row,) = ledger.rows
    assert len(buzzed) == 1, (buzzed, row.get("last_error"))
    assert _SATURDAY_LOOKS[2] <= buzzed[0] < _SATURDAY_LOOKS[2] + timedelta(minutes=1), buzzed
    assert phones.buzzes[0]["body"] == "EPS beat consensus by 46%. Tap for the full picture."


SAT_0700_ET = datetime(2026, 11, 7, 7, 0, tzinfo=ET)
#: The live days on Saturday: Friday, the previous trading day, and Saturday itself.
_LIVE_DAYS_ON_SATURDAY = (FRIDAY, SATURDAY)


async def _reads_after_a_look(monkeypatch, live, route_day: date, *,
                              then=None, interval: int = 60) -> List[tuple]:
    """A look at 07:00 on Saturday at a held result for INTC's report dated `route_day`, then the
    loop's refresh every `interval` seconds to the next look, an hour on, on a clock the test
    moves. `then` runs after the look. Returns (seconds after the look, day) for every day read."""
    return await _reads_after_looks(monkeypatch, live, route_day, then=then, interval=interval)


async def _reads_after_looks(monkeypatch, live, route_day: date, *,
                             hand_outs=((SAT_0700_ET, 0.0),), then=None, interval: int = 60,
                             symbols=("INTC",), start: float = 1_000_000.0,
                             friday_read_at: Optional[float] = None) -> List[tuple]:
    """Looks at held results for INTC's report dated `route_day`, one per hand-out: the flush's
    own clock for it, and how many seconds after reading that clock the flush reaches the row, on
    a monotonic clock at `start` at 07:00 on Saturday. Every day of the live days was read at
    `start`, and Friday at `start + friday_read_at` when that is given. Then the loop's refresh
    every `interval` seconds, from 07:00 to past the last look, an hour after its hand-out. `then`
    runs after the looks. Returns (seconds after 07:00, day) for every day read."""
    clock = SimpleNamespace(at=start)
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=lambda: clock.at))
    monkeypatch.setattr(settings, "EARNINGS_LIVE_INTERVAL_SECONDS", interval)
    for day in _LIVE_DAYS_ON_SATURDAY:
        live._fetched[day] = start
    if friday_read_at is not None:
        live._fetched[FRIDAY] = start + friday_read_at
    route = {"ticker": "INTC", "report_date": route_day.isoformat()}
    for handed_out, reached in hand_outs:
        clock.at = start + (handed_out - SAT_0700_ET).total_seconds() + reached
        wake = sender.copy_at_wake(KIND_EARNINGS_RESULT, route, handed_out)
        assert wake is not None and wake.ask_again_in == sender.WAKE_ASK_AGAIN
    if then is not None:
        then()
    reads: List[tuple] = []
    elapsed = 0

    async def _calendar(from_date=None, to_date=None):
        reads.append((elapsed, date.fromisoformat(from_date)))
        return [{**_fmp_row(date.fromisoformat(from_date)), "symbol": "ZZZZ"}]

    last_look = 3600 + max((handed_out - SAT_0700_ET).total_seconds()
                           for handed_out, _ in hand_outs)
    for elapsed in range(0, int(last_look) + interval, interval):
        clock.at = start + elapsed
        await live.refresh(SAT_0700_ET + timedelta(seconds=elapsed),
                           fmp=SimpleNamespace(get_earnings_calendar=_calendar),
                           symbols=list(symbols))
    return reads


def _just_before_the_next_look(second: int, interval: int = 60) -> bool:
    """Inside the two ticks and a fetch's deadline before the look an hour on, the slack every
    cadence comes due early by included."""
    return 3600 - (2 * interval + 20 + els._DUE_SLACK_SECONDS) <= second < 3600


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [15, 60, 300],
                         ids=["the_fastest_loop", "the_usual_loop", "the_slowest_loop"])
async def test_a_look_that_asks_again_has_its_report_read_once_just_before_the_next(
        monkeypatch, clean_sender, live, interval):
    """Friday's report, its EPS a dropped digit at Saturday's 07:00 look, and the closed day's own
    reads two hours apart: one read lands just before 08:00, and no other. A read is made only on
    a tick, and `EARNINGS_LIVE_INTERVAL_SECONDS` may be 15 s to 300 s, so a fixed lead shorter
    than a tick would leave no tick to make it."""
    _lands(live, FRI_2220, day=FRIDAY)
    _fmp_says(live, FRI_2240, eps=0.041, day=FRIDAY)
    reads = await _reads_after_a_look(monkeypatch, live, FRIDAY, interval=interval)
    (second,) = [second for second, day in reads if day == FRIDAY]
    assert _just_before_the_next_look(second, interval), reads


@pytest.mark.asyncio
async def test_a_look_at_a_leftover_row_has_the_report_it_reads_as_read(
        monkeypatch, clean_sender, live):
    """A Saturday row with no figures of its own reads as Friday's report, so Friday's answer is
    the one the next look reads."""
    _lands(live, FRI_2220, day=FRIDAY)
    _fmp_says(live, FRI_2240, eps=0.041, day=FRIDAY)
    live.observe_rows(SATURDAY, [_fmp_row(SATURDAY)], SAT_0700_ET, fetched=True)
    assert live.snapshot("INTC", SATURDAY, SAT_0700_ET).reads_as == FRIDAY
    reads = await _reads_after_a_look(monkeypatch, live, SATURDAY)
    (second,) = [second for second, day in reads if day == FRIDAY]
    assert _just_before_the_next_look(second), reads


@pytest.mark.asyncio
async def test_a_look_whose_report_has_answered_by_then_costs_no_read(
        monkeypatch, clean_sender, live):
    """FMP's own figure came back in the hour, in another answer (the daily pass's): the next look
    has its answer, so the read it asked for never goes out."""
    _lands(live, FRI_2220, day=FRIDAY)
    _fmp_says(live, FRI_2240, eps=0.041, day=FRIDAY)
    reads = await _reads_after_a_look(
        monkeypatch, live, FRIDAY,
        then=lambda: _fmp_says(live, SAT_0700_ET + timedelta(minutes=10), day=FRIDAY))
    assert [day for _, day in reads if day == FRIDAY] == [], reads


@pytest.mark.asyncio
async def test_a_look_whose_report_has_answered_costs_no_read_while_another_on_its_day_waits(
        monkeypatch, clean_sender, live):
    """AMD's Friday report still waits on FMP, which the day's own cadence asks about. INTC's
    answered in the hour, so the read its look booked is nobody's: the look asks about its own
    report, never about its day."""
    _lands(live, FRI_2220, day=FRIDAY)
    _fmp_says(live, FRI_2240, eps=0.041, day=FRIDAY)
    live.observe_rows(FRIDAY, [{**_fmp_row(FRIDAY), "symbol": "AMD"}], FRI_2240, fetched=True)
    reads = await _reads_after_looks(
        monkeypatch, live, FRIDAY, symbols=("INTC", "AMD"),
        then=lambda: _fmp_says(live, SAT_0700_ET + timedelta(minutes=10), day=FRIDAY))
    assert [day for _, day in reads if day == FRIDAY] == [], reads


@pytest.mark.asyncio
async def test_the_rows_of_one_hand_out_share_one_look_and_one_read(
        monkeypatch, clean_sender, live):
    """The flush reads its clock once per hand-out and holds every row of it to that clock plus
    an hour. Two rows of one report reached 0.2 s apart, either side of half a minute, used to
    look at two different minutes and cost two reads of the day."""
    _lands(live, FRI_2220, day=FRIDAY)
    _fmp_says(live, FRI_2240, eps=0.041, day=FRIDAY)
    reads = await _reads_after_looks(monkeypatch, live, FRIDAY, start=60.0 * 20000 + 29.9,
                                     hand_outs=((SAT_0700_ET, 0.0), (SAT_0700_ET, 0.2)))
    (second,) = [second for second, day in reads if day == FRIDAY]
    assert _just_before_the_next_look(second), reads


@pytest.mark.asyncio
@pytest.mark.parametrize("apart", [1, 40, 59], ids=lambda s: f"{s}s_apart")
async def test_looks_less_than_a_tick_apart_share_one_read(monkeypatch, clean_sender, live,
                                                           apart):
    """Two hand-outs `apart` seconds apart hold their rows to two instants under a tick apart:
    the read the first one's look gets lands before the second's too, so it is the second's."""
    _lands(live, FRI_2220, day=FRIDAY)
    _fmp_says(live, FRI_2240, eps=0.041, day=FRIDAY)
    reads = await _reads_after_looks(
        monkeypatch, live, FRIDAY, start=60.0 * 20000 + 25,
        hand_outs=((SAT_0700_ET, 0.0), (SAT_0700_ET + timedelta(seconds=apart), 0.0)))
    (second,) = [second for second, day in reads if day == FRIDAY]
    assert _just_before_the_next_look(second), reads


@pytest.mark.asyncio
async def test_a_look_and_its_days_own_cadence_due_on_one_tick_read_the_day_once(
        monkeypatch, clean_sender, live):
    """Friday's 2 h cadence on a closed day comes due on the tick that opens the look's window:
    one call for the day, never one for each reason to read it."""
    _lands(live, FRI_2220, day=FRIDAY)
    _fmp_says(live, FRI_2240, eps=0.041, day=FRIDAY)
    first_in_the_window = 3480
    cold = els.EARNINGS_LIVE_CLOSED_DAY_TTL_SECONDS - els._DUE_SLACK_SECONDS
    assert _just_before_the_next_look(first_in_the_window)
    assert not _just_before_the_next_look(first_in_the_window - 60)
    reads = await _reads_after_looks(monkeypatch, live, FRIDAY,
                                     friday_read_at=first_in_the_window - cold)
    assert [read for read in reads if read[1] == FRIDAY] == [(first_in_the_window, FRIDAY)], reads


@pytest.mark.parametrize("kind,eps,revenue,asks_again", [
    (KIND_EARNINGS_RESULT, 0.60, 17.1e9, False),
    (KIND_EARNINGS_RESULT, 0.42, 17.1e9, False),
    (KIND_EARNINGS_RESULT, 0.041, 17.1e9, True),
    (KIND_EARNINGS_LIVE, 0.60, 17.1e9, False),
    (KIND_EARNINGS_LIVE, 0.041, 17.1e9, False),
    (KIND_EARNINGS_LIVE, 0.041, None, True),
], ids=["result_sends", "result_ends_under_the_bar", "result_asks_again", "live_sends",
        "live_sends_a_revenue_beside_an_eps_in_doubt", "live_asks_again"])
def test_only_a_wake_that_asks_again_books_a_read_before_its_next_look(
        clean_sender, live, kind, eps, revenue, asks_again):
    """A row its wake sends or ends has no next look, so a read an hour on would serve nobody. A
    row that asks again books one, for its own report, at the instant its row is held to."""
    _lands(live, FRI_2220, eps=eps, revenue=revenue, day=FRIDAY)
    route = {"ticker": "INTC", "report_date": FRIDAY.isoformat()}
    wake = sender.copy_at_wake(kind, route, SAT_0700_ET)
    assert wake is not None and (wake.ask_again_in is not None) is asks_again, wake
    booked = {("INTC", FRIDAY): {SAT_0700_ET + sender.WAKE_ASK_AGAIN}} if asks_again else {}
    assert live._looks == booked


@pytest.mark.asyncio
async def test_a_row_that_spent_a_hand_out_on_the_clock_still_gets_its_look(
        monkeypatch, clean_sender, live):
    """Parked on ET after a failed read, then held once for the user's own morning: the figure in
    doubt at that wake has had no second look yet."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    _read_fails_once(svc, {qh.PREF_TIMEZONE: "America/Los_Angeles"})
    await sender.dispatch_landings(_lands(live, FRI_0230 - timedelta(hours=4)),
                                   _clock_at(monkeypatch, FRI_0230 - timedelta(hours=4)))
    (row,) = ledger.rows
    _fmp_says(live, FRI_0230, eps=0.041)

    _clock_at(monkeypatch, FRI_0700)
    assert (await svc.flush_deferred())["requeued"] == 1
    wake = _clock_at(monkeypatch, _stamp(row["deliver_after"]))
    assert wake.astimezone(ZoneInfo("America/Los_Angeles")).hour == 7
    stats = await svc.flush_deferred()

    assert (stats["sent"], stats["suppressed"], stats["requeued"]) == (0, 0, 1)
    _fmp_says(live, wake + timedelta(minutes=30), eps=0.60)
    _clock_at(monkeypatch, _stamp(row["deliver_after"]))
    assert (await svc.flush_deferred())["sent"] == 1
    assert phones.buzzes[0]["body"].startswith("EPS beat consensus by 46%")


THU_2130 = datetime(2026, 10, 29, 21, 30, tzinfo=ET)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefs,morning", [
    (IN_NEW_YORK, FRI_0700),
    ({**IN_NEW_YORK, qh.PREF_QUIET_ENABLED: True, qh.PREF_QUIET_START: "22:15",
      qh.PREF_QUIET_END: "06:30"}, FRI_0700 - timedelta(minutes=30)),
], ids=["default_night", "own_window"])
async def test_a_second_look_never_lands_in_the_users_night(
        monkeypatch, clean_sender, live, prefs, morning):
    """Parked on a failed read at 21:30, the row's first hand-out is a minute on, on a good read:
    New York's evening, so it is not held. Its figure is being confirmed, so it asks again in an
    hour, and 22:31 is the user's night, where nothing would hold it: only a first hand-out, or one
    after a hand-out that judged nothing, is held again. So the next look waits for the night's
    end, and the one buzz is the morning's."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    _read_fails_once(svc, prefs)
    await sender.dispatch_landings(_lands(live, THU_2130), _clock_at(monkeypatch, THU_2130))
    (row,) = ledger.rows
    assert _stamp(row["deliver_after"]) == THU_2130
    _fmp_says(live, THU_2130 + timedelta(seconds=30), eps=0.041)

    _clock_at(monkeypatch, THU_2130 + timedelta(minutes=1))
    stats = await svc.flush_deferred()

    assert (stats["sent"], stats["requeued"]) == (0, 1) and phones.buzzes == []
    assert _stamp(row["deliver_after"]) == morning
    _fmp_says(live, THU_2340, eps=0.60)
    _clock_at(monkeypatch, morning)
    assert (await svc.flush_deferred())["sent"] == 1
    assert phones.buzzes[0]["body"].startswith("EPS beat consensus by 46%")


@pytest.mark.asyncio
async def test_a_second_look_that_misses_the_night_keeps_its_hour(monkeypatch, clean_sender, live):
    """A look an hour on at 20:31 is still the evening: it is not moved."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    _read_fails_once(svc, IN_NEW_YORK)
    landing = THU_2130 - timedelta(hours=1)
    await sender.dispatch_landings(_lands(live, landing), _clock_at(monkeypatch, landing))
    _fmp_says(live, landing + timedelta(seconds=30), eps=0.041)

    _clock_at(monkeypatch, landing + timedelta(minutes=1))
    assert (await svc.flush_deferred())["requeued"] == 1
    assert _stamp(ledger.rows[0]["deliver_after"]) == landing + timedelta(minutes=1, hours=1)


@pytest.mark.asyncio
async def test_a_second_look_after_a_failed_read_keeps_its_hour(monkeypatch, clean_sender, live):
    """With no blob, nothing says where the user is, so ET's night would be a guess: the flush
    holds nothing on one, and moves no look on one either."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    svc._preferences_bulk = lambda ids: {u: None for u in ids}
    await sender.dispatch_landings(_lands(live, THU_2130), _clock_at(monkeypatch, THU_2130))
    _fmp_says(live, THU_2130 + timedelta(seconds=30), eps=0.041)

    _clock_at(monkeypatch, THU_2130 + timedelta(minutes=1))
    assert (await svc.flush_deferred())["requeued"] == 1
    assert _stamp(ledger.rows[0]["deliver_after"]) == THU_2130 + timedelta(minutes=1, hours=1)


@pytest.mark.asyncio
async def test_a_look_asked_for_on_an_unread_clock_is_held_for_the_night_it_lands_in(
        monkeypatch, clean_sender, live):
    """Parked on a failed read at 21:30, its first hand-out at 21:31 reads nothing either, so its
    look an hour on could not be checked against the user's clock: 22:31 is New York's night. That
    hand-out judged nothing, so it spends no night hold: the look's good read holds the row for
    the morning, where it used to buzz at 22:31 as a second hand-out."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    answers = iter([{"u1": None}, {"u1": None}])     # the landing's read and the first flush's
    svc._preferences_bulk = lambda ids: next(answers, {"u1": IN_NEW_YORK})
    await sender.dispatch_landings(_lands(live, THU_2130), _clock_at(monkeypatch, THU_2130))
    (row,) = ledger.rows
    _fmp_says(live, THU_2130 + timedelta(seconds=30), eps=0.041)

    _clock_at(monkeypatch, THU_2130 + timedelta(minutes=1))
    assert (await svc.flush_deferred())["requeued"] == 1
    look = _stamp(row["deliver_after"])
    assert look == THU_2130 + timedelta(minutes=1, hours=1), "nothing said where the user is"
    assert row["last_error"] == pds.UNREAD_CLOCK_ASK, "the row says its look judged nothing"

    _fmp_says(live, look - timedelta(minutes=10), eps=0.60)
    _clock_at(monkeypatch, look)
    stats = await svc.flush_deferred()
    assert (stats["sent"], stats["requeued"]) == (0, 1) and phones.buzzes == []
    assert _stamp(row["deliver_after"]) == FRI_0700

    _clock_at(monkeypatch, FRI_0700)
    assert (await svc.flush_deferred())["sent"] == 1
    assert phones.buzzes[0]["body"].startswith("EPS beat consensus by 46%")


@pytest.mark.asyncio
async def test_a_look_asked_for_on_a_good_read_spends_the_night_hold(monkeypatch, clean_sender,
                                                                     live):
    """The bound stays: a look judged on the user's own clock never lands in their night, so a
    later hand-out found there anyway, a clock the user changed since, is not held again."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    _read_fails_once(svc, IN_NEW_YORK)
    landing = THU_2130 - timedelta(hours=1)
    await sender.dispatch_landings(_lands(live, landing), _clock_at(monkeypatch, landing))
    (row,) = ledger.rows
    _fmp_says(live, landing + timedelta(seconds=30), eps=0.041)
    _clock_at(monkeypatch, landing + timedelta(minutes=1))
    assert (await svc.flush_deferred())["requeued"] == 1
    assert row.get("last_error") is None, "a look judged on a good read carries no note"

    svc._preferences_bulk = lambda ids: {"u1": {qh.PREF_TIMEZONE: "Europe/Berlin"}}
    _fmp_says(live, landing + timedelta(minutes=30), eps=0.60)
    _clock_at(monkeypatch, _stamp(row["deliver_after"]))
    assert (await svc.flush_deferred())["sent"] == 1


@pytest.mark.asyncio
async def test_a_hand_out_a_redeploy_cut_off_spends_neither_an_attempt_nor_the_night_hold(
        monkeypatch, clean_sender):
    """A Berlin row parked by a failed read at 17:05 ET is handed out at 17:06, and a redeploy cuts
    that flush off before it judged anything. Back to `deferred` with the claim's attempt given
    back, as `_return_to_deferred` promises, so the new process's good read at 22:07 in Berlin
    holds it for Berlin's morning instead of buzzing it there."""
    ledger, phones = _Ledger(), _Phones()
    berlin = {qh.PREF_TIMEZONE: "Europe/Berlin"}
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs={"u1": berlin})
    _read_fails_once(svc, berlin)
    landing = datetime(2026, 10, 29, 17, 5, tzinfo=ET)
    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, landing))
    (row,) = ledger.rows
    assert (row["push_state"], row["attempts"]) == ("deferred", 0)

    _clock_at(monkeypatch, landing + timedelta(minutes=1))
    claimed = svc._claim_due(100)
    assert row["attempts"] == 1
    svc._return_to_deferred(claimed, reason=pds.FLUSH_CANCELLED_MID_BATCH)
    assert (row["push_state"], row["attempts"]) == ("deferred", 0)

    wake = _clock_at(monkeypatch, landing + timedelta(minutes=2))
    stats = await svc.flush_deferred()
    assert (stats["sent"], stats["requeued"]) == (0, 1) and phones.buzzes == []
    held = _stamp(row["deliver_after"]).astimezone(ZoneInfo("Europe/Berlin"))
    assert (held.hour, held.date()) == (7, (wake + timedelta(days=1)).date())


@pytest.mark.asyncio
async def test_a_hand_out_cut_off_after_a_look_on_an_unread_clock_still_spends_no_night_hold(
        monkeypatch, clean_sender, live):
    """The cut-off flush's note replaces the look's on the row, and says the same: nothing judged
    the user's clock, so the next good read in their night still holds the row."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    answers = iter([{"u1": None}, {"u1": None}])
    svc._preferences_bulk = lambda ids: next(answers, {"u1": IN_NEW_YORK})
    await sender.dispatch_landings(_lands(live, THU_2130), _clock_at(monkeypatch, THU_2130))
    (row,) = ledger.rows
    _fmp_says(live, THU_2130 + timedelta(seconds=30), eps=0.041)
    _clock_at(monkeypatch, THU_2130 + timedelta(minutes=1))
    assert (await svc.flush_deferred())["requeued"] == 1

    look = _clock_at(monkeypatch, _stamp(row["deliver_after"]))
    svc._return_to_deferred(svc._claim_due(100), reason=pds.FLUSH_CANCELLED_MID_BATCH)
    _fmp_says(live, look, eps=0.60)
    _clock_at(monkeypatch, look + timedelta(minutes=1))
    stats = await svc.flush_deferred()
    assert (stats["sent"], stats["requeued"]) == (0, 1) and phones.buzzes == []
    assert _stamp(row["deliver_after"]) == FRI_0700


async def _held_on_its_third_hand_out(monkeypatch, live):
    """Parked on a failed read at 20:30, with the figure in doubt and the settings read failing
    at the 20:31 and 21:31 flushes too: two looks asked for an hour on, each on a clock nobody
    read. FMP answers before the third, at 22:31, whose good read finds New York's night."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    answers = iter([{"u1": None}, {"u1": None}, {"u1": None}])
    svc._preferences_bulk = lambda ids: next(answers, {"u1": IN_NEW_YORK})
    landing = THU_2130 - timedelta(hours=1)
    await sender.dispatch_landings(_lands(live, landing), _clock_at(monkeypatch, landing))
    (row,) = ledger.rows
    _fmp_says(live, landing + timedelta(seconds=30), eps=0.041)
    for hand_out in (1, 2):
        _clock_at(monkeypatch, landing + timedelta(minutes=1, hours=hand_out - 1))
        assert (await svc.flush_deferred())["requeued"] == 1
        assert (row["attempts"], row["last_error"]) == (hand_out, pds.UNREAD_CLOCK_ASK)
    look = _stamp(row["deliver_after"])
    assert look == landing + timedelta(minutes=1, hours=2), "22:31: New York's night"
    _fmp_says(live, look - timedelta(minutes=10), eps=0.60)

    _clock_at(monkeypatch, look)
    stats = await svc.flush_deferred()
    return svc, ledger, phones, row, stats


@pytest.mark.asyncio
async def test_two_looks_on_an_unread_clock_leave_the_row_its_night_hold(
        monkeypatch, clean_sender, live):
    """Two hand-outs that judged nothing spend no hold, even when the second is the last one the
    attempts ceiling counts: the third, the first on a clock it read, holds the row for the
    morning. It used to buzz at 22:31, in New York's night as in ET's."""
    svc, ledger, phones, row, stats = await _held_on_its_third_hand_out(monkeypatch, live)
    assert (stats["sent"], stats["requeued"]) == (0, 1) and phones.buzzes == []
    assert (row["attempts"], _stamp(row["deliver_after"])) == (
        pds.PushDispatchService.MAX_FLUSH_ATTEMPTS, FRI_0700)

    _clock_at(monkeypatch, FRI_0700)
    assert (await svc.flush_deferred())["sent"] == 1
    assert phones.buzzes[0]["body"].startswith("EPS beat consensus by 46%")
    assert (row["push_state"], row["attempts"]) == (
        "sent", pds.PushDispatchService.MAX_FLUSH_ATTEMPTS + 1)


@pytest.mark.asyncio
async def test_a_row_held_on_its_last_counted_hand_out_comes_back_once_and_is_held_no_more(
        monkeypatch, clean_sender, live):
    """What ends the holds: the attempts ceiling. The row held at 22:31 comes back at 07:00 for one
    hand-out past it and goes out whatever the clock says then, here a user who has flown to
    Auckland, where it is midnight. Its note still reads as a hand-out that judged nothing, so
    without the ceiling it would be held again, and again for every clock that moves."""
    svc, ledger, phones, row, stats = await _held_on_its_third_hand_out(monkeypatch, live)
    assert stats["requeued"] == 1 and row["last_error"] == pds.UNREAD_CLOCK_ASK
    svc._preferences_bulk = lambda ids: {"u1": {qh.PREF_TIMEZONE: "Pacific/Auckland"}}

    wake = _clock_at(monkeypatch, FRI_0700)
    assert wake.astimezone(ZoneInfo("Pacific/Auckland")).hour == 0
    stats = await svc.flush_deferred()
    assert (stats["sent"], stats["requeued"]) == (1, 0)
    assert (row["push_state"], row["attempts"]) == (
        "sent", pds.PushDispatchService.MAX_FLUSH_ATTEMPTS + 1)


@pytest.mark.asyncio
async def test_fmps_figure_in_doubt_with_the_filing_on_show_is_no_answer_either(
        monkeypatch, clean_sender, live):
    """The guard skips FMP's 0.041 for the filing's 0.60, so nothing reads as checking. This kind
    still goes out on FMP's own figure alone, which is the one in doubt."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    live.absorb_filing("INTC", REPORT, FilingFigures(
        accession="a", accepted_at=None, url=None, eps_adjusted=0.60), THU_2340)
    _fmp_says(live, THU_2340, eps=0.041)

    _clock_at(monkeypatch, FRI_0700)
    stats = await svc.flush_deferred()
    assert (stats["sent"], stats["suppressed"], stats["requeued"]) == (0, 0, 1)
    assert "From the company's release." in ledger.rows[0]["body"]

    _fmp_says(live, FRI_0700 + timedelta(minutes=30), eps=0.60)
    _clock_at(monkeypatch, FRI_0700 + timedelta(hours=1))
    assert (await svc.flush_deferred())["sent"] == 1
    assert phones.buzzes[0]["body"].startswith("EPS beat consensus by 46%")


@pytest.mark.asyncio
async def test_a_live_row_with_a_figure_to_state_does_not_wait_for_the_other(
        monkeypatch, clean_sender, live):
    """Revenue stands and EPS is held. That is a true sentence, so it goes out at the wake."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    assert ledger.rows[0]["kind"] == KIND_EARNINGS_LIVE
    _fmp_says(live, THU_2340, eps=0.041)

    _clock_at(monkeypatch, FRI_0700)
    assert (await svc.flush_deferred())["sent"] == 1
    assert phones.buzzes[0]["body"] == "Revenue beat consensus by 3% · EPS being confirmed."


@pytest.mark.asyncio
async def test_a_revenue_in_doubt_does_not_touch_an_eps_surprise(monkeypatch, clean_sender, live):
    """The surprise is an EPS sentence. A revenue the guard holds has no say in it."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    _fmp_says(live, THU_2340, revenue=1.71e9)
    assert live.snapshot("INTC", REPORT, THU_2340).revenue.checking is True

    _clock_at(monkeypatch, FRI_0700)
    assert (await svc.flush_deferred())["sent"] == 1
    assert phones.buzzes[0]["body"].startswith("EPS beat consensus by 46%")


@pytest.mark.asyncio
async def test_a_row_the_cap_stops_is_not_held_for_a_figure(monkeypatch, clean_sender, live):
    """It would not go out whatever the figure says, so no hour of waiting can change anything."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    _fmp_says(live, THU_2340, eps=0.041)

    svc._category_counts_bulk = lambda ids, category, cutoffs: {u: 4 for u in ids}
    _clock_at(monkeypatch, FRI_0700)
    stats = await svc.flush_deferred()

    (row,) = ledger.rows
    assert (stats["suppressed"], stats["requeued"]) == (1, 0)
    assert row["push_state"] == "failed" and "cap_reached" in row["last_error"]


@pytest.mark.asyncio
async def test_a_hold_for_a_figure_that_cannot_be_written_ends_the_row_instead_of_stranding_it(
        monkeypatch, clean_sender, live, caplog):
    """Nothing re-reads a row left at `pending`. It ends here, with no buzz, and the log says so."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    _fmp_says(live, THU_2340, eps=0.041)

    real_update = ledger.update

    def _fails_on_the_new_wake(patch):
        if "deliver_after" in patch:
            raise RuntimeError("write refused")
        return real_update(patch)

    monkeypatch.setattr(ledger, "update", _fails_on_the_new_wake)
    _clock_at(monkeypatch, FRI_0700)
    with caplog.at_level(logging.WARNING):
        stats = await svc.flush_deferred()

    assert (stats["suppressed"], stats["requeued"]) == (1, 0)
    assert ledger.rows[0]["push_state"] == "failed" and phones.buzzes == []
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("kept,meanwhile", [
    (False, "Revenue beat consensus by 3% · EPS being confirmed."),
    (True, "Revenue $17.1B · EPS $0.44. From the company's release."),
], ids=["the_record_gone", "the_filing_given_back_alone"])
@pytest.mark.parametrize("fmp_then,buzzed", [
    (0.60, ["EPS beat consensus by 46%. Tap for the full picture."]),
    (0.041, []),
], ids=["fmp_confirms_the_beat", "fmp_still_in_doubt"])
async def test_a_restart_inside_the_hold_never_buzzes_what_the_wake_wrote_while_waiting(
        monkeypatch, clean_sender, live, kept, meanwhile, fmp_then, buzzed):
    """The 46% is parked, then FMP puts a dropped digit on the EPS: the 07:00 wake holds the row
    for FMP's answer and leaves on it what is known meanwhile, which this kind never sends. A
    deploy at 07:30 forgets the record, or the table gives back the filing alone, and neither is
    that answer: the 08:00 wake asks again, as the old process would have, and the row goes out
    only on what FMP says next. The doubted number is never buzzed."""
    from _filing_cache_fake import FilingCache

    table, watcher = FilingCache(), efs.EarningsFilingWatcher()
    monkeypatch.setattr("app.database.get_supabase", lambda: table)
    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: watcher)
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    if kept:
        live.absorb_filing("INTC", REPORT, FilingFigures(
            accession="0000050863-26-000123", accepted_at=THU_2210, url="https://www.sec.gov/x",
            revenue=17.1e9, eps_adjusted=0.44), THU_2210)
        await live.keep_filing("INTC", REPORT, THU_2210)
    _fmp_says(live, THU_2340, eps=0.041)

    _clock_at(monkeypatch, FRI_0700)
    assert (await svc.flush_deferred())["requeued"] == 1
    (row,) = ledger.rows
    assert row["title"] == "INTC results are out" and phones.buzzes == []
    assert ("$0.44" in row["body"]) is kept, "the release's EPS is on the row, never on a phone"

    live.reset()
    await live.restore_filings(FRI_0700 + timedelta(minutes=30))
    second = _clock_at(monkeypatch, FRI_0700 + timedelta(hours=1))
    stats = await svc.flush_deferred()
    assert (stats["sent"], stats["requeued"]) == (0, 1) and phones.buzzes == []
    assert (row["push_state"], row["title"], row["body"]) == ("deferred", "INTC results are out",
                                                              meanwhile)

    _fmp_says(live, second + timedelta(minutes=5), eps=fmp_then)
    _clock_at(monkeypatch, second + timedelta(hours=1))
    await svc.flush_deferred()
    assert [b["body"] for b in phones.buzzes] == buzzed
    assert row["push_state"] == ("sent" if buzzed else "failed")
    assert row["attempts"] == pds.PushDispatchService.MAX_FLUSH_ATTEMPTS


@pytest.mark.parametrize("settled", [
    {"eps": 0.60}, {"eps": 0.42}, {"eps": 0.30}, {"eps": 0.60, "revenue": None},
], ids=["big_beat", "small_beat", "big_miss", "no_revenue"])
def test_only_a_report_with_no_answer_yet_asks_for_another_look(live, settled):
    """The rule the flush acts on, read off the sender: FMP's own figure on show is an answer."""
    _lands(live, THU_2210, **settled)
    route = {"ticker": "INTC", "report_date": REPORT.isoformat()}
    assert sender.copy_at_wake(KIND_EARNINGS_RESULT, route, FRI_0700).ask_again_in is None


@pytest.mark.asyncio
async def test_a_row_of_any_other_kind_goes_out_as_it_always_did(monkeypatch, clean_sender, live):
    """A deferred price move whose route names the very report in doubt. Nothing here reads the
    report for it: its own text, its first wake, and no second look."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _lands(live, THU_2210)
    _fmp_says(live, THU_2340, eps=0.041)
    svc.claim_send(
        "u1", "ticker_move:INTC:2026-10-30", kind="ticker_move", category="watchlist",
        title="INTC moved", body="INTC is up 9% today.",
        route={"ticker": "INTC", "report_date": REPORT.isoformat(), "kind": "ticker_move"},
        push_state=pds.STATE_DEFERRED, deliver_after=FRI_0700,
    )

    _clock_at(monkeypatch, FRI_0700)
    stats = await svc.flush_deferred()

    assert (stats["sent"], stats["requeued"], stats["suppressed"]) == (1, 0, 0)
    assert phones.buzzes[0]["body"] == "INTC is up 9% today."
    assert ledger.rows[0]["attempts"] == 1 and ledger.rows[0]["push_state"] == "sent"


# ── a failed read at the landing ─────────────────────────────────────────────
#
# Nothing said where the user lives, so the row was parked on ET, and ET's morning can be the
# small hours further west. The flush reads the blob again, and a good read is the user's clock.

def _read_fails_once(svc, blob):
    answers = iter([{"u1": None}])
    svc._preferences_bulk = lambda ids: next(answers, {"u1": blob})


@pytest.mark.asyncio
@pytest.mark.parametrize("zone,hour_there", [
    ("America/Los_Angeles", 4), ("Pacific/Honolulu", 1),
])
async def test_a_failed_read_at_the_landing_still_waits_for_the_users_own_morning(
        monkeypatch, clean_sender, zone, hour_there):
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _read_fails_once(svc, {qh.PREF_TIMEZONE: zone})

    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, FRI_0230))
    (row,) = ledger.rows
    assert _stamp(row["deliver_after"]) == FRI_0700

    assert FRI_0700.astimezone(ZoneInfo(zone)).hour == hour_there
    _clock_at(monkeypatch, FRI_0700)
    assert (await svc.flush_deferred())["sent"] == 0
    assert phones.buzzes == [] and row["push_state"] == "deferred"
    wake = _stamp(row["deliver_after"])
    assert wake.astimezone(ZoneInfo(zone)).hour == 7

    _clock_at(monkeypatch, wake - timedelta(seconds=1))
    assert (await svc.flush_deferred())["claimed"] == 0
    _clock_at(monkeypatch, wake)
    assert (await svc.flush_deferred())["sent"] == 1
    assert len(phones.buzzes) == 1


@pytest.mark.asyncio
async def test_a_failed_read_east_of_et_is_late_and_never_held_twice(monkeypatch, clean_sender):
    """07:00 in New York is the evening in Tokyo: late, and awake."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _read_fails_once(svc, {qh.PREF_TIMEZONE: "Asia/Tokyo"})

    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, FRI_0230))
    _clock_at(monkeypatch, FRI_0700)
    assert (await svc.flush_deferred())["sent"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("zone,wake", [
    ("America/New_York", None),
    ("Europe/Berlin", WED_0700_BERLIN),
    ("Asia/Tokyo", datetime(2026, 11, 11, 7, 0, tzinfo=ZoneInfo("Asia/Tokyo"))),
], ids=["new_york", "berlin", "tokyo"])
async def test_a_failed_read_at_the_16_00_pass_is_judged_on_the_users_own_clock(
        monkeypatch, clean_sender, zone, wake):
    """New York's afternoon is the night from Berlin to Tokyo, and a failed read says nothing of
    which this user is in. So the row waits for the next flush, a minute on, which reads the
    settings again: New York's goes out then, the others at 07:00 on their own clocks. Never a
    buzz at 22:05 in Berlin."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _flags_as_shipped(monkeypatch)
    _read_fails_once(svc, {qh.PREF_TIMEZONE: zone})

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, TUE_1605), day=TUESDAY)
    (row,) = ledger.rows
    assert phones.buzzes == [] and row["push_state"] == "deferred"
    assert _stamp(row["deliver_after"]) == TUE_1605

    _clock_at(monkeypatch, TUE_1605 + timedelta(minutes=1))
    stats = await svc.flush_deferred()
    if wake is not None:
        assert (stats["sent"], stats["requeued"]) == (0, 1) and phones.buzzes == []
        assert _stamp(row["deliver_after"]) == wake
        _clock_at(monkeypatch, wake)
        await svc.flush_deferred()
    assert len(phones.buzzes) == 1 and row["push_state"] == "sent"


@pytest.mark.asyncio
async def test_a_read_that_fails_again_at_the_flush_goes_out_then_and_once(monkeypatch, clean_sender):
    """Nothing better can be known, so ET's word stands: a minute late, and never held twice."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=IN_BERLIN)
    _flags_as_shipped(monkeypatch)
    svc._preferences_bulk = lambda ids: {u: None for u in ids}

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, TUE_1605), day=TUESDAY)
    assert phones.buzzes == [] and ledger.rows[0]["push_state"] == "deferred"
    _clock_at(monkeypatch, TUE_1605 + timedelta(minutes=1))
    assert (await svc.flush_deferred())["sent"] == 1
    _clock_at(monkeypatch, TUE_1605 + timedelta(minutes=2))
    assert (await svc.flush_deferred())["claimed"] == 0
    assert len(phones.buzzes) == 1 and len(ledger.rows) == 1


RESULTS_ONLY = {**IN_NEW_YORK, "notify_earnings_live": False, "notify_earnings_surprises": True}
FRI_0630 = datetime(2026, 10, 30, 6, 30, tzinfo=ET)


@pytest.mark.asyncio
@pytest.mark.parametrize("result_push", [True, False], ids=["result_push_on", "result_push_off"])
@pytest.mark.parametrize("landing", [FRI_NOON, FRI_0630], ids=["et_day", "et_night"])
async def test_a_live_row_parked_on_a_failed_read_gives_its_key_back_to_a_results_only_user(
        monkeypatch, clean_sender, result_push, landing):
    """The read failed, so Live Results was parked for a user who has it off and Results &
    Surprises on, under the one key both kinds and the daily pass share; the result kind behind it
    found that key taken. Ended at the flush, the row kept it, and the surprise this user did ask
    for was told by nobody. It gives the key back instead, so the 16:00 pass tells them. Here the
    record holds nothing at the wake (the report reached the sender straight, as after a restart),
    so the row has no result to go out as; with one, it does (the section below)."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", result_push)
    _read_fails_once(svc, RESULTS_ONLY)

    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, landing))
    (row,) = ledger.rows
    assert (row["kind"], row["push_state"]) == (KIND_EARNINGS_LIVE, "deferred")

    _clock_at(monkeypatch, max(landing + timedelta(minutes=1), _stamp(row["deliver_after"])))
    stats = await svc.flush_deferred()
    assert (stats["claimed"], stats["sent"]) == (1, 0) and phones.buzzes == []
    assert ledger.rows == []

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, FRI_1600))
    (told,) = phones.buzzes
    assert told["data"]["kind"] == KIND_EARNINGS_RESULT
    assert told["body"].startswith("EPS beat consensus by 46%")
    assert [r["kind"] for r in ledger.rows] == [KIND_EARNINGS_RESULT]


@pytest.mark.asyncio
async def test_a_released_key_tells_a_user_who_wants_neither_kind_nothing(
        monkeypatch, clean_sender):
    """Given back, the key only lets the daily pass ask this user's settings itself: both kinds
    off, it claims nothing and buzzes nobody."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _read_fails_once(svc, {**RESULTS_ONLY, "notify_earnings_surprises": False})

    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, FRI_NOON))
    _clock_at(monkeypatch, FRI_NOON + timedelta(minutes=1))
    await svc.flush_deferred()
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, FRI_1600))

    assert phones.buzzes == [] and ledger.rows == []


# ── a live row turned off at its wake, with the result push on ───────────────
#
# The result kind behind a parked live row found the key taken and is done with the report in this
# process, and the daily pass keeps to its own window: a Thursday report landing on Friday after
# 16:00 ET is in no later pass. So with the result push on, a live row the user's settings turn off
# goes on as the result they do take, from the record as it stands, as a landing that had read
# those settings would have sent it. Whatever would start no result push now (no clear surprise,
# FMP's EPS not in yet, the user's toggles, the cap, the switch off) deletes the row as before, so
# the key is free for the round or the pass that can still tell it.

FRI_1605 = datetime(2026, 10, 30, 16, 5, tzinfo=ET)
FRI_2145 = datetime(2026, 10, 30, 21, 45, tzinfo=ET)
SAT_1600 = datetime(2026, 10, 31, 16, 0, tzinfo=ET)
REPORTED_FRIDAY = date(2026, 10, 30)
BEAT_46 = ("INTC earnings are out", "EPS beat consensus by 46%. Tap for the full picture.")


async def _parked_then_woken(monkeypatch, svc, ledger, live, landing, blob, **figures):
    """A landing whose settings read fails, which parks the live row on a guess; then the flush
    at that row's wake, which reads `blob`. Returns the flush's stats."""
    _read_fails_once(svc, blob)
    await sender.dispatch_landings(_lands(live, landing, **figures),
                                   _clock_at(monkeypatch, landing))
    (row,) = [r for r in ledger.rows if r["kind"] == KIND_EARNINGS_LIVE]
    assert row["push_state"] == "deferred"
    _clock_at(monkeypatch, max(landing + timedelta(minutes=1), _stamp(row["deliver_after"])))
    return await svc.flush_deferred()


@pytest.mark.asyncio
@pytest.mark.parametrize("landing,wake", [
    (FRI_NOON, FRI_NOON + timedelta(minutes=1)),
    (FRI_1605, FRI_1605 + timedelta(minutes=1)),
    (FRI_2145, FRI_2145 + timedelta(minutes=1)),
    (FRI_0630, FRI_0700),
    (FRI_2330, SAT_0700),
], ids=["before_the_pass", "after_the_pass", "late_evening", "et_night", "out_of_the_window"])
async def test_a_live_row_turned_off_at_its_wake_goes_out_as_the_result_the_user_takes(
        monkeypatch, clean_sender, live, landing, wake):
    """Thursday's 46% beat lands on Friday, and a Results-only user's settings read fails. A good
    read would have sent them the result at once (or at 07:00 in ET's night); it now does, at the
    flush that reads them, and once: neither a later round nor a pass tells it again. Saturday 07:00
    is past the window, but the row was claimed inside it, as a held result row is."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)

    stats = await _parked_then_woken(monkeypatch, svc, ledger, live, landing, RESULTS_ONLY)

    assert (stats["sent"], stats["released"]) == (1, 0)
    (buzz,) = phones.buzzes
    assert (buzz["title"], buzz["body"]) == BEAT_46
    assert buzz["data"]["kind"] == KIND_EARNINGS_RESULT
    assert buzz["data"]["dedup_key"] == "earnings_result:INTC:2026-10-29"
    assert (buzz["data"]["ticker"], buzz["data"]["report_date"]) == ("INTC", "2026-10-29")
    (row,) = ledger.rows
    assert (row["kind"], row["category"], row["push_state"]) == (
        KIND_EARNINGS_RESULT, "earnings", "sent")
    assert (row["title"], row["body"]) == BEAT_46, "the inbox says what the phone said"
    assert row["route"]["kind"] == KIND_EARNINGS_RESULT and row["sent_at"]

    await sender.dispatch_landings(live.landed_snapshots(wake), wake)
    next_pass = FRI_1600 if wake < FRI_1600 else FRI_1600 + timedelta(days=1)
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, next_pass))
    assert len(phones.buzzes) == 1 and len(ledger.rows) == 1


@pytest.mark.asyncio
async def test_a_live_row_turned_off_in_the_users_night_waits_for_their_morning_as_the_result(
        monkeypatch, clean_sender, live):
    """16:05 ET is 22:05 in Berlin, on a day that holds four alerts by the flush a minute later.
    The result it goes on as is held for Berlin's 07:00, which spends Wednesday's budget, and
    buzzes once then."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _read_fails_once(svc, {**RESULTS_ONLY, qh.PREF_TIMEZONE: "Europe/Berlin"})

    await sender.dispatch_landings(_lands(live, TUE_1605, day=TUESDAY),
                                   _clock_at(monkeypatch, TUE_1605))
    assert [r["kind"] for r in ledger.rows] == [KIND_EARNINGS_LIVE]
    _spent_today(ledger, TUE_1605 + timedelta(minutes=1))
    _clock_at(monkeypatch, TUE_1605 + timedelta(minutes=1))
    stats = await svc.flush_deferred()

    assert (stats["sent"], stats["requeued"]) == (0, 1) and phones.buzzes == []
    (row,) = [r for r in ledger.rows if r["dedup_key"] == "earnings_result:INTC:2026-11-10"]
    assert (row["kind"], row["push_state"]) == (KIND_EARNINGS_RESULT, "deferred")
    assert _stamp(row["deliver_after"]) == WED_0700_BERLIN

    _clock_at(monkeypatch, WED_0700_BERLIN)
    assert (await svc.flush_deferred())["sent"] == 1
    (buzz,) = phones.buzzes
    assert buzz["data"]["kind"] == KIND_EARNINGS_RESULT
    assert buzz["body"] == BEAT_46[1] and row["push_state"] == "sent"


@pytest.mark.asyncio
@pytest.mark.parametrize("eps,body", [
    (0.60, BEAT_46[1]),
    (0.30, "EPS missed consensus by 27%. Tap for the full picture."),
], ids=["as_landed", "corrected_overnight"])
async def test_live_results_turned_off_in_the_night_still_tells_the_result_at_7(
        monkeypatch, clean_sender, live, eps, body):
    """A good read at 22:10 held Live Results for 07:00, and the result kind found its key taken.
    The user turns Live Results off before 07:00 and keeps Results & Surprises: at 07:00 they are
    told the result, as the record has it then."""
    ledger, phones = _Ledger(), _Phones()
    prefs = {"u1": dict(IN_NEW_YORK)}
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs=prefs)

    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    (row,) = ledger.rows
    assert (row["kind"], _stamp(row["deliver_after"])) == (KIND_EARNINGS_LIVE, FRI_0700)

    _fmp_says(live, THU_2340, eps=eps)
    prefs["u1"] = dict(RESULTS_ONLY)
    _clock_at(monkeypatch, FRI_0700)
    assert (await svc.flush_deferred())["sent"] == 1

    (buzz,) = phones.buzzes
    assert (buzz["data"]["kind"], buzz["body"]) == (KIND_EARNINGS_RESULT, body)
    assert (row["kind"], row["body"]) == (KIND_EARNINGS_RESULT, body)


@pytest.mark.asyncio
@pytest.mark.parametrize("eps", [None, 0.041], ids=["not_out_yet", "being_confirmed"])
async def test_a_live_row_turned_off_before_fmps_eps_is_in_leaves_the_key_to_the_result_round(
        monkeypatch, clean_sender, live, eps):
    """Revenue landed with no EPS that stands (none yet, or a dropped digit held as checking), so
    the result kind has not decided yet: it decides once FMP's EPS stands. The row is deleted, and
    that round tells the user. A result row holding the key would have asked again twice and then
    ended, with nobody left to tell a later EPS."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)

    stats = await _parked_then_woken(monkeypatch, svc, ledger, live, FRI_NOON, RESULTS_ONLY,
                                     eps=eps)
    assert (stats["released"], stats["sent"]) == (1, 0)
    assert ledger.rows == [] and phones.buzzes == []

    filled = FRI_NOON + timedelta(minutes=40)
    await sender.dispatch_landings(_fmp_says(live, filled, eps=0.60),
                                   _clock_at(monkeypatch, filled))
    (buzz,) = phones.buzzes
    assert (buzz["data"]["kind"], buzz["body"]) == (KIND_EARNINGS_RESULT, BEAT_46[1])
    assert [r["kind"] for r in ledger.rows] == [KIND_EARNINGS_RESULT]


@pytest.mark.asyncio
@pytest.mark.parametrize("eps", [0.42, 0.40], ids=["small_beat", "small_miss"])
async def test_a_live_row_turned_off_with_no_surprise_to_tell_is_deleted(
        monkeypatch, clean_sender, live, eps):
    """A 2% surprise starts no result push, at the landing or at 16:00 ET: nobody is told."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)

    stats = await _parked_then_woken(monkeypatch, svc, ledger, live, FRI_NOON, RESULTS_ONLY,
                                     eps=eps)
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, FRI_1600),
                      row=_fmp_row(eps=eps, revenue=17.1e9))

    assert stats["released"] == 1
    assert phones.buzzes == [] and ledger.rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize("off", [
    {"notify_earnings_surprises": False}, {"notify_earnings_alerts": False},
], ids=["results_off", "earnings_master_off"])
async def test_a_live_row_turned_off_for_a_user_who_takes_no_result_either_is_deleted(
        monkeypatch, clean_sender, live, off):
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)

    stats = await _parked_then_woken(monkeypatch, svc, ledger, live, FRI_NOON,
                                     {**RESULTS_ONLY, **off})
    await _daily_pass(monkeypatch, _clock_at(monkeypatch, FRI_1600))

    assert stats["released"] == 1
    assert phones.buzzes == [] and ledger.rows == []


@pytest.mark.asyncio
async def test_a_live_row_turned_off_behind_a_full_day_is_deleted_as_a_capped_landing_would_be(
        monkeypatch, clean_sender, live):
    """The day fills between the landing and the flush. A capped alert takes no row, so it costs
    no later pass its key: Saturday's pass reads Friday's report as yesterday's, with a new day's
    budget."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _read_fails_once(svc, RESULTS_ONLY)
    await sender.dispatch_landings(_lands(live, FRI_NOON, day=REPORTED_FRIDAY),
                                   _clock_at(monkeypatch, FRI_NOON))
    _spent_today(ledger, FRI_NOON + timedelta(minutes=1))

    _clock_at(monkeypatch, FRI_NOON + timedelta(minutes=1))
    stats = await svc.flush_deferred()
    assert (stats["released"], stats["sent"]) == (1, 0) and phones.buzzes == []
    assert [r for r in ledger.rows if r["dedup_key"].startswith("earnings_result:")] == []

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, SAT_1600), day=REPORTED_FRIDAY)
    (buzz,) = phones.buzzes
    assert (buzz["data"]["kind"], buzz["body"]) == (KIND_EARNINGS_RESULT, BEAT_46[1])


@pytest.mark.asyncio
async def test_with_the_result_push_off_a_live_row_turned_off_is_deleted_whatever_the_record_says(
        monkeypatch, clean_sender, live):
    """The owner's switch: off, no result goes out live, so a good read at the landing would have
    left this user to the 16:00 ET pass, and so does the delete."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", False)

    stats = await _parked_then_woken(monkeypatch, svc, ledger, live, FRI_NOON, RESULTS_ONLY)
    assert (stats["released"], stats["sent"]) == (1, 0)
    assert ledger.rows == [] and phones.buzzes == []

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, FRI_1600))
    (buzz,) = phones.buzzes
    assert (buzz["data"]["kind"], buzz["body"]) == (KIND_EARNINGS_RESULT, BEAT_46[1])


@pytest.mark.asyncio
async def test_a_result_that_cannot_be_written_onto_the_row_leaves_it_deleted_as_before(
        monkeypatch, clean_sender, live, caplog):
    """The row goes on as a result only once the ledger says so, or the inbox and a later wake
    would read a live row the phone was told as a result. A failed write deletes it as before,
    which frees the key for the 16:00 ET pass, and the log names the row."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    real_update = ledger.update

    def _refuses_a_new_kind(patch):
        if "kind" in patch:
            raise RuntimeError("write refused")
        return real_update(patch)

    monkeypatch.setattr(ledger, "update", _refuses_a_new_kind)
    with caplog.at_level(logging.WARNING):
        stats = await _parked_then_woken(monkeypatch, svc, ledger, live, FRI_NOON, RESULTS_ONLY)

    assert (stats["released"], stats["sent"]) == (1, 0)
    assert ledger.rows == [] and phones.buzzes == []
    (line,) = [r.getMessage() for r in caplog.records if "write refused" in r.getMessage()]
    assert "user=u1" in line and "earnings_result:INTC:2026-10-29" in line

    await _daily_pass(monkeypatch, _clock_at(monkeypatch, FRI_1600))
    assert [buzz["body"] for buzz in phones.buzzes] == [BEAT_46[1]]


@pytest.mark.asyncio
async def test_a_broken_answer_to_what_a_row_goes_out_as_deletes_it_and_keeps_the_stack(
        monkeypatch, clean_sender, live, caplog):
    """Only a bug reaches that except, and its line is the one trace: a WARNING with the stack,
    and the row deleted as with nothing to go out as."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)

    def _boom(*a, **k):
        raise RuntimeError("record exploded")

    monkeypatch.setattr(sender, "instead_at_wake", _boom)
    with caplog.at_level(logging.WARNING):
        stats = await _parked_then_woken(monkeypatch, svc, ledger, live, FRI_NOON, RESULTS_ONLY)

    assert (stats["released"], stats["sent"]) == (1, 0)
    assert ledger.rows == [] and phones.buzzes == []
    (record,) = [r for r in caplog.records if "goes out as instead" in r.getMessage()]
    assert "RuntimeError" in record.getMessage() and "INTC" in record.getMessage()
    assert record.levelno == logging.WARNING
    assert record.exc_info is not None and record.exc_info[0] is RuntimeError


@pytest.mark.parametrize("state", ["sent", "deferred", "failed"])
def test_a_new_kind_is_only_ever_written_onto_a_row_still_claimed(state):
    """A row some other path has finished, or put back, keeps its kind and its text."""
    ledger = _Ledger()
    svc = object.__new__(pds.PushDispatchService)
    svc.supabase = ledger
    ledger.rows.append({"user_id": "u1", "dedup_key": "k", "push_state": state,
                        "kind": KIND_EARNINGS_LIVE, "title": "t", "body": "b", "route": {}})
    result = NOTIFICATION_KINDS[KIND_EARNINGS_RESULT]

    assert svc._rekind("u1", "k", result, *BEAT_46, {"kind": KIND_EARNINGS_RESULT}) is False
    assert (ledger.rows[0]["kind"], ledger.rows[0]["body"]) == (KIND_EARNINGS_LIVE, "b")


@pytest.mark.asyncio
async def test_a_wake_with_no_good_read_holds_nothing_again(monkeypatch, clean_sender):
    """The row came due inside ET's night on the user's own earlier window, and this read failed:
    an empty blob would read as ET's night, which says nothing about where they are."""
    ledger, phones = _Ledger(), _Phones()
    window = {**IN_NEW_YORK, qh.PREF_QUIET_ENABLED: True,
              qh.PREF_QUIET_START: "21:00", qh.PREF_QUIET_END: "22:30"}
    svc = _dispatcher_over(monkeypatch, ledger, phones, prefs={"u1": window})

    at_night = datetime(2026, 10, 29, 21, 30, tzinfo=ET)
    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, at_night))
    wake = _clock_at(monkeypatch, datetime(2026, 10, 29, 22, 30, tzinfo=ET))
    assert _stamp(ledger.rows[0]["deliver_after"]) == wake

    svc._preferences_bulk = lambda ids: {"u1": None}
    assert (await svc.flush_deferred())["sent"] == 1


@pytest.mark.asyncio
async def test_a_second_hold_that_cannot_be_written_delivers_instead_of_stranding_the_row(
        monkeypatch, clean_sender, caplog):
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _read_fails_once(svc, {qh.PREF_TIMEZONE: "America/Los_Angeles"})
    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, FRI_0230))

    real_update = ledger.update

    def _fails_on_the_new_wake(patch):
        if "deliver_after" in patch:
            raise RuntimeError("write refused")
        return real_update(patch)

    monkeypatch.setattr(ledger, "update", _fails_on_the_new_wake)
    _clock_at(monkeypatch, FRI_0700)
    with caplog.at_level(logging.WARNING):
        assert (await svc.flush_deferred())["sent"] == 1

    assert ledger.rows[0]["push_state"] == "sent"
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


def test_a_second_hold_only_ever_touches_a_row_still_claimed():
    """A row some other path has finished stays finished, however late this write lands."""
    ledger = _Ledger()
    svc = object.__new__(pds.PushDispatchService)
    svc.supabase = ledger
    ledger.rows.append({"user_id": "u1", "dedup_key": "k", "push_state": "sent",
                        "deliver_after": FRI_0230.isoformat()})
    assert svc._hold_again("u1", "k", FRI_0700) is True
    assert ledger.rows[0]["push_state"] == "sent"
    assert ledger.rows[0]["deliver_after"] == FRI_0230.isoformat()


@pytest.mark.asyncio
async def test_a_row_put_back_by_a_failed_try_is_not_held_again(monkeypatch, clean_sender):
    """Its one hand-out went on the failed try. Better a late buzz than a second clock to reason
    about on a row already retrying."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _read_fails_once(svc, {qh.PREF_TIMEZONE: "America/Los_Angeles"})
    await sender.dispatch_landings([_big()], _clock_at(monkeypatch, FRI_0230))
    ledger.rows[0]["attempts"] = 1

    _clock_at(monkeypatch, FRI_0700)
    assert (await svc.flush_deferred())["sent"] == 1


def test_fmp_figures_are_stated_as_beat_or_miss_never_as_numbers():
    title, body = sender.live_copy(_snap(eps=0.44, eps_pct=7.32, rev=17.1e9, rev_pct=3.01))
    assert title == "INTC results are out"
    assert body == "Revenue beat consensus by 3% · EPS beat consensus by 7%."
    assert "$" not in body, "FMP data on a lock screen is outside the licensed app"


def test_a_filing_figure_is_printed_and_attributed():
    snap = _snap(eps=0.45, eps_source=SOURCE_FILING, eps_pct=9.76, rev=17.1e9,
                 rev_source=SOURCE_FILING, rev_pct=3.01, preliminary=True)
    _, body = sender.live_copy(snap)
    assert body == ("Revenue $17.1B (beat consensus by 3%) · EPS $0.45 (beat consensus by 10%). "
                    "From the company's release.")


def test_whatever_has_not_landed_says_so():
    _, body = sender.live_copy(_snap(rev=17.1e9, rev_pct=3.01, status=STATUS_LIVE))
    assert body == "Revenue beat consensus by 3% · EPS not out yet."


def test_a_gaap_only_eps_is_labelled_and_never_called_a_beat():
    snap = _snap(eps=-0.12, eps_source=SOURCE_FILING, eps_basis=BASIS_GAAP, eps_pct=None,
                 rev=17.1e9, rev_source=SOURCE_FILING, rev_pct=3.01, preliminary=True)
    _, body = sender.live_copy(snap)
    assert "GAAP EPS -$0.12" in body and "GAAP EPS -$0.12 (" not in body


def test_an_exploding_percentage_is_not_printed():
    _, body = sender.live_copy(_snap(eps=0.23, eps_pct=2200.0, eps_est=0.01, rev=17.1e9,
                                     rev_pct=3.01))
    assert "EPS beat consensus." in body


def test_a_surprise_that_rounds_to_zero_keeps_the_verdict_the_card_shows():
    """`EarningsMetricOutcome.result` compares the figures once the server gave a surprise, so a
    surprise that rounds to 0.00 on a figure off the consensus is a beat or a miss on the card and
    on the sheet. "In line" is for a figure equal to the consensus, there and here."""
    estimate = 17_100_000_000.0
    for actual, verdict in ((17_100_500_000.0, "beat consensus"),
                            (17_099_500_000.0, "missed consensus"),
                            (estimate, "in line with consensus")):
        pct = els.revenue_surprise_pct(actual, estimate)
        assert pct == 0.0
        _, body = sender.live_copy(_snap(rev=actual, rev_est=estimate, rev_pct=pct))
        assert body.startswith(f"Revenue {verdict} ·"), body
    for actual, verdict in ((1000.01, "beat consensus"), (999.99, "missed consensus"),
                            (1000.0, "in line with consensus")):
        pct = els.eps_surprise_pct(actual, 1000.0)
        assert pct == 0.0
        _, body = sender.live_copy(_snap(eps=actual, eps_est=1000.0, eps_pct=pct))
        assert body.endswith(f"EPS {verdict}."), body
    # A loss wider than a negative consensus is a miss, as the card compares it.
    pct = els.eps_surprise_pct(-1000.01, -1000.0)
    assert pct == 0.0
    _, body = sender.live_copy(_snap(eps=-1000.01, eps_est=-1000.0, eps_pct=pct))
    assert body.endswith("EPS missed consensus."), body


def test_a_filing_figure_keeps_its_print_beside_a_verdict_that_rounds_to_zero():
    estimate = 17_100_000_000.0
    pct = els.revenue_surprise_pct(17_100_500_000.0, estimate)
    snap = _snap(rev=17_100_500_000.0, rev_source=SOURCE_FILING, rev_est=estimate, rev_pct=pct,
                 preliminary=True)
    _, body = sender.live_copy(snap)
    assert body.startswith("Revenue $17.1B (beat consensus) ·"), body


@pytest.mark.parametrize("eps,revenue,body", [
    (2.01, 10.05e9, "Revenue beat consensus · EPS beat consensus."),
    (1.99, 9.95e9, "Revenue missed consensus · EPS missed consensus."),
    (2.03, 10.15e9, "Revenue beat consensus by 2% · EPS beat consensus by 2%."),
], ids=["half_a_percent_beat", "half_a_percent_miss", "one_and_a_half_percent"])
def test_a_percentage_that_would_print_as_zero_is_left_out(eps, revenue, body):
    """A 0.50% surprise prints "0%" (Python rounds half to even), and "beat consensus by 0%" on
    a lock screen contradicts the "Beat +0.5%" of the sheet it opens. The percentage goes
    whenever it would print as 0; the daily pass rounds the same way above its 5% floor."""
    snap = _snap(eps=eps, eps_est=2.00, eps_pct=els.eps_surprise_pct(eps, 2.00),
                 rev=revenue, rev_est=10.0e9, rev_pct=els.revenue_surprise_pct(revenue, 10.0e9))
    assert sender.live_copy(snap)[1] == body


@pytest.mark.parametrize("pct,phrase", [
    (0.49, "beat consensus"), (0.5, "beat consensus"), (0.51, "beat consensus by 1%"),
    (-0.5, "missed consensus"), (-0.51, "missed consensus by 1%"),
    (999.4, "beat consensus by 999%"), (1000.0, "beat consensus"),
])
def test_the_percentage_is_named_only_between_its_two_bounds(pct, phrase):
    m = LiveMetric(2.0, 2.0 * (1 + pct / 100), pct, SOURCE_FMP)
    assert sender._vs_consensus(m) == phrase


def test_the_copy_is_informational_never_directive():
    for snap in (_snap(eps=0.44, eps_pct=7.3, rev=17.1e9, rev_pct=3.0),
                 _snap(eps=0.30, eps_pct=-26.8, rev=15.0e9, rev_pct=-9.6)):
        title, body = sender.live_copy(snap)
        text = f"{title} {body}".lower()
        for word in ("buy", "sell", "don't miss", "act now", "position yourself"):
            assert word not in text


# ── one report, two dated rows ───────────────────────────────────────────────

OCT_28, OCT_29 = date(2026, 10, 28), date(2026, 10, 29)
_INTEL_CIK = "0000050863"
_INTEL_ACC = "0000050863-26-000123"
_FIRST_PUSH = {"earnings_result:INTC:2026-10-28"}
_INDEX_PAGE = """<table class="tableFile">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>8-K</td><td><a href="/Archives/edgar/data/50863/a/intc.htm">intc.htm</a></td>
<td>8-K</td><td>30 KB</td></tr>
<tr><td>2</td><td>PRESS RELEASE</td><td><a href="/Archives/edgar/data/50863/a/q3.htm">q3.htm</a>
</td><td>EX-99.1</td><td>90 KB</td></tr>
</table>"""
_REVENUE_LINE = "Third-quarter revenue was $17.1 billion, up 25% year-over-year."
_EPS_LINE = "Non-GAAP earnings per share attributable to Intel was $0.44."
_RELEASE_TEXT = f"Intel Corporation today reported results. {_REVENUE_LINE} {_EPS_LINE}"
_READ_FIGURES = json.dumps({
    "is_quarterly_results": True, "currency": "USD",
    "revenue": {"value": 17.1, "unit": "billion", "quote": _REVENUE_LINE},
    "eps_adjusted_diluted": {"value": 0.44, "quote": _EPS_LINE},
})


def _et_time(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET).astimezone(timezone.utc)


def _calendar_row(day: date, *, eps=None, revenue=None) -> Dict[str, Any]:
    return {"symbol": "INTC", "date": day.isoformat(), "epsActual": eps, "epsEstimated": 0.41,
            "revenueActual": revenue, "revenueEstimated": 16.6e9, "lastUpdated": day.isoformat()}


def _results_8k(*, filed: date, accepted: str) -> Dict[str, Any]:
    """`accepted` as SEC stamps it, in UTC: these dates are EDT, so 16:02 ET is 20:02Z."""
    return {"accessionNumber": _INTEL_ACC, "filingDate": filed.isoformat(), "form": "8-K",
            "items": "2.02,9.01", "primaryDocument": "intc.htm", "acceptanceDateTime": accepted}


_EVENING_8K = _results_8k(filed=OCT_29, accepted="2026-10-28T22:05:00.000Z")   # 18:05 ET
_MORNING_8K = _results_8k(filed=OCT_29, accepted="2026-10-29T12:00:00.000Z")   # 08:00 ET


class _Market:
    """FMP's calendar, SEC's filings and the model at one simulated moment: the one network
    fake behind the real loop, so a test moves the world and runs a minute."""

    def __init__(self, days=(OCT_28, OCT_29)):
        self.rows = {day: [_calendar_row(day)] for day in days}
        self.filings: List[Dict[str, Any]] = []
        self.model_calls = 0

    def fills(self, day: date, *, eps=0.60, revenue=17.1e9) -> None:
        self.rows[day] = [_calendar_row(day, eps=eps, revenue=revenue)]

    async def get_earnings_calendar(self, from_date=None, to_date=None):
        return [dict(row) for row in self.rows.get(date.fromisoformat(from_date), [])]

    async def get_submissions(self, cik):
        columns: Dict[str, List[Any]] = {
            key: [] for key in ("accessionNumber", "filingDate", "form", "items",
                                "primaryDocument", "acceptanceDateTime")}
        for filing in self.filings:
            for key, column in columns.items():
                column.append(filing[key])
        return {"filings": {"recent": columns}}

    async def get_filing_document(self, cik, accession, document):
        if document.endswith("-index.htm"):
            return _INDEX_PAGE
        return f"<html><body><p>{_RELEASE_TEXT}</p></body></html>"

    async def generate_json(self, prompt, **kwargs):
        self.model_calls += 1
        return {"text": _READ_FIGURES, "model": "m", "tokens_used": 10}


class _UniqueViolation(Exception):
    code = "23505"


class _LedgerQuery:
    """One read or write of `_EventLedger`."""

    def __init__(self, ledger: "_EventLedger", table: str):
        self.ledger, self.table_name = ledger, table
        self.wanted: Dict[str, Any] = {}
        self.row = None

    def select(self, *_):
        return self

    def eq(self, column, value):
        self.wanted[column] = {value}
        return self

    def order(self, *_, **__):
        return self

    def range(self, *_):
        return self

    def in_(self, column, values):
        self.wanted[column] = set(values)
        return self

    def insert(self, row):
        self.row = row
        return self

    def execute(self):
        return self.ledger.answer(self)


class _EventLedger:
    """`watchlist_items` and `notification_events`: one row per (user, key), a second one refused
    as Postgres refuses it, and `failing_reads` reads of who holds a key raising as an edge 520
    does."""

    def __init__(self, users=("u1",)):
        self.users, self.rows, self.failing_reads, self.reads = users, [], 0, []
        # What a failing read raises: by default an error no retry takes for a blip.
        self.read_error = lambda: RuntimeError("520 from the edge")

    def table(self, name):
        return _LedgerQuery(self, name)

    def answer(self, query: _LedgerQuery):
        if query.table_name == "watchlist_items":
            return SimpleNamespace(data=[{"user_id": u} for u in self.users])
        if query.row is not None:
            if any(r["user_id"] == query.row["user_id"]
                   and r["dedup_key"] == query.row["dedup_key"] for r in self.rows):
                raise _UniqueViolation("duplicate key")
            self.rows.append(query.row)
            return SimpleNamespace(data=[query.row])
        self.reads.append(dict(query.wanted))
        if self.failing_reads:
            self.failing_reads -= 1
            raise self.read_error()
        return SimpleNamespace(data=[
            {"user_id": row["user_id"]} for row in self.rows
            if all(row.get(column) in values for column, values in query.wanted.items())
        ])

    @property
    def told(self) -> set:
        return {(row["user_id"], row["dedup_key"]) for row in self.rows}


def _ledger_dispatcher(monkeypatch, *, users=("u1",), prefs=None) -> pds.PushDispatchService:
    """The real notify_watchers, fan-out, preference check and claim over a `_EventLedger`: only the
    table, the bulk reads behind a recipient and APNs are fake."""
    svc = object.__new__(pds.PushDispatchService)
    svc.supabase = _EventLedger(users)
    svc._push = SimpleNamespace(enabled=True)
    svc.resolve_route_asset_type = lambda *_: None
    svc.resolve_recipients = lambda ids, kind, now: {
        uid: pds._Recipient(user_id=uid, preferences=(prefs or {}).get(uid, {})) for uid in ids
    }

    async def _deliver(recipient, kind, **_kw):
        return True

    svc._deliver = _deliver
    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: svc)
    monkeypatch.setattr(daily, "get_push_dispatch_service", lambda: svc)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", True)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", True)
    sender.reset()
    return svc


@pytest.fixture
def ledger(monkeypatch):
    yield _ledger_dispatcher(monkeypatch).supabase
    sender.reset()


@pytest.fixture
def live_loop(ledger, monkeypatch):
    """Run minutes of the real FMP tick, filing tick and `dispatch_landings` over a `_Market`,
    with the real dispatcher over a `_EventLedger`. Each minute starts the Tracking memo afresh unless
    it keeps it, as a minute of the 30 it lives for does.

    Both ticks keep and restore filings through `earnings_filing_cache`, here ONE in-memory table
    for the whole test (`table`): it outlives `restart()`, as the real one outlives a deploy, and it
    keeps the loop off the network. A test of the table down sets its errors (`_table_down`)."""
    service = EarningsLiveService()
    watcher = efs.EarningsFilingWatcher()
    table = FilingCache()
    monkeypatch.setattr("app.database.get_supabase", lambda: table)
    monkeypatch.setattr(els, "get_earnings_live_service", lambda: service)
    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: watcher)
    monkeypatch.setattr(efs, "_CALL_SPACING_SECONDS", 0)
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "Caydex test@example.com")
    monkeypatch.setattr(settings, "EARNINGS_LIVE_FILINGS_ENABLED", True)

    async def _ciks(*, sec=None):
        return {"INTC": _INTEL_CIK}

    # `run_once` imports this inside the function, so it is patched at its source.
    monkeypatch.setattr(signals, "ticker_cik_map", _ciks)

    async def _minute(market: _Market, at: datetime, *, keep_memo: bool = False) -> None:
        monkeypatch.setattr(ts, "_et_today", lambda: at.astimezone(ET).date())
        # All three run on real time, and these minutes are not real ones.
        service._fetched.clear()
        if not keep_memo:
            ts.reset_earnings_calendar_cache()
        for watch in watcher._watch.values():
            watch.checked_mono = 0.0
        await els.earnings_live_tick(at, fmp=market, watched=["INTC"])
        await els.earnings_filing_tick(at, watched=["INTC"], sec=market, gemini=market)
        await els.earnings_live_tick(at, fmp=market, watched=["INTC"])

    def _restart() -> None:
        # The process goes, with its records, its watch and its memory of who was sent what. The
        # table stays.
        service.reset()
        watcher.reset()
        sender.reset()

    async def _daily_pass(market: _Market, at: datetime) -> None:
        @contextlib.asynccontextmanager
        async def _claim(_job):
            yield SimpleNamespace(success=False, notified=0)

        monkeypatch.setattr(daily, "claimed_job", _claim)
        monkeypatch.setattr(daily, "get_fmp_client", lambda: market)
        monkeypatch.setattr(daily, "_watched_tickers", lambda: {"INTC"})
        await daily.run_earnings_notifications(at)

    ts.reset_earnings_calendar_cache()
    yield SimpleNamespace(minute=_minute, restart=_restart, daily_pass=_daily_pass,
                          service=service, watcher=watcher, table=table)
    ts.reset_earnings_calendar_cache()


def _table_down(loop) -> None:
    """`earnings_filing_cache` not there yet, as before the owner applies migration 209: every
    read and write fails, so a restart is what it was before the table."""
    loop.table.read_error = RuntimeError("PGRST205: could not find the table")
    loop.table.write_error = RuntimeError("PGRST205: could not find the table")


def _keys(ledger: _EventLedger) -> set:
    """The result keys held. The daily pass also says "reports tomorrow", under its own key."""
    return {row["dedup_key"] for row in ledger.rows
            if row["dedup_key"].startswith(KIND_EARNINGS_RESULT)}


async def _evening_8k_on_its_day(loop, market: _Market) -> None:
    await loop.minute(market, _et_time(OCT_28, 16))
    market.filings.append(_EVENING_8K)
    await loop.minute(market, _et_time(OCT_28, 18, 6))


async def _same_process(loop, market: _Market) -> None:
    await _evening_8k_on_its_day(loop, market)
    await loop.minute(market, _et_time(OCT_29, 8))


async def _restart_overnight(loop, market: _Market) -> None:
    await _evening_8k_on_its_day(loop, market)
    loop.restart()
    await loop.minute(market, _et_time(OCT_29, 8))


async def _restart_then_fmp_fills_the_leftover_row(loop, market: _Market) -> None:
    await _restart_overnight(loop, market)
    market.fills(OCT_29)
    await loop.minute(market, _et_time(OCT_29, 8, 10))


async def _restart_then_fmp_fills_both_rows_the_later_first(loop, market: _Market) -> None:
    await _restart_then_fmp_fills_the_leftover_row(loop, market)
    market.fills(OCT_28)
    await loop.minute(market, _et_time(OCT_29, 9))


async def _restart_at_nine_then_fmp_fills_the_leftover_row(loop, market: _Market) -> None:
    await _same_process(loop, market)
    loop.restart()
    await loop.minute(market, _et_time(OCT_29, 9))
    market.fills(OCT_29)
    await loop.minute(market, _et_time(OCT_29, 9, 10))


async def _fmp_lands_at_five(loop, market: _Market) -> None:
    await loop.minute(market, _et_time(OCT_28, 16))
    market.fills(OCT_28)
    await loop.minute(market, _et_time(OCT_28, 17))


async def _fmp_completes_the_first_row_first(loop, market: _Market) -> None:
    await _fmp_lands_at_five(loop, market)
    market.filings.append(_EVENING_8K)
    await loop.minute(market, _et_time(OCT_28, 18, 6))
    await loop.minute(market, _et_time(OCT_29, 8))


async def _an_8k_the_morning_after_fmp_landed(loop, market: _Market) -> None:
    await _fmp_lands_at_five(loop, market)
    market.filings.append(_MORNING_8K)
    await loop.minute(market, _et_time(OCT_29, 8, 1))


async def _fmp_fills_the_leftover_row(loop, market: _Market) -> None:
    await _fmp_lands_at_five(loop, market)
    await loop.minute(market, _et_time(OCT_29, 8))
    market.fills(OCT_29)
    await loop.minute(market, _et_time(OCT_29, 8, 10))


async def _a_restart_before_fmp_fills_the_leftover_row(loop, market: _Market) -> None:
    await _fmp_lands_at_five(loop, market)
    loop.restart()
    await loop.minute(market, _et_time(OCT_29, 8))
    market.fills(OCT_29)
    await loop.minute(market, _et_time(OCT_29, 8, 10))


async def _the_daily_pass_finds_the_leftover_row_filled(loop, market: _Market) -> None:
    await _fmp_fills_the_leftover_row(loop, market)
    await loop.daily_pass(market, _et_time(OCT_29, 16))


async def _the_daily_pass_tells_the_first_row_and_fmp_fills_the_second(loop, market: _Market):
    await loop.minute(market, _et_time(OCT_28, 15))
    market.fills(OCT_28)
    loop.restart()                                     # so the live path never saw it land
    await loop.minute(market, _et_time(OCT_28, 15, 30))
    await loop.daily_pass(market, _et_time(OCT_28, 16))
    await loop.minute(market, _et_time(OCT_29, 8))
    market.fills(OCT_29)
    await loop.minute(market, _et_time(OCT_29, 8, 10))


async def _both_rows_fill_between_two_fetches(loop, market: _Market) -> None:
    await loop.minute(market, _et_time(OCT_28, 16))
    await loop.minute(market, _et_time(OCT_29, 7))
    market.fills(OCT_28)
    market.fills(OCT_29)
    await loop.minute(market, _et_time(OCT_29, 7, 5), keep_memo=True)


async def _an_8k_with_no_acceptance_time(loop, market: _Market) -> None:
    await loop.minute(market, _et_time(OCT_28, 16))
    market.filings.append(dict(_EVENING_8K, acceptanceDateTime=""))
    await loop.minute(market, _et_time(OCT_28, 18, 6))
    await loop.minute(market, _et_time(OCT_29, 8))


_ONE_REPORT = {
    "same_process": _same_process,
    "restart_overnight": _restart_overnight,
    "restart_then_fmp_fills_the_leftover_row": _restart_then_fmp_fills_the_leftover_row,
    "restart_then_fmp_fills_both_rows_the_later_first":
        _restart_then_fmp_fills_both_rows_the_later_first,
    "restart_at_nine_then_fmp_fills_the_leftover_row":
        _restart_at_nine_then_fmp_fills_the_leftover_row,
    "fmp_completes_the_first_row_first": _fmp_completes_the_first_row_first,
    "an_8k_the_morning_after_fmp_landed": _an_8k_the_morning_after_fmp_landed,
    "fmp_fills_the_leftover_row": _fmp_fills_the_leftover_row,
    "a_restart_before_fmp_fills_the_leftover_row": _a_restart_before_fmp_fills_the_leftover_row,
    "the_daily_pass_finds_the_leftover_row_filled": _the_daily_pass_finds_the_leftover_row_filled,
    "the_daily_pass_tells_the_first_row_and_fmp_fills_the_second":
        _the_daily_pass_tells_the_first_row_and_fmp_fills_the_second,
    "both_rows_fill_between_two_fetches": _both_rows_fill_between_two_fetches,
    "an_8k_with_no_acceptance_time": _an_8k_with_no_acceptance_time,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["kept", "down"])
@pytest.mark.parametrize("leg", _ONE_REPORT.values(), ids=_ONE_REPORT.keys())
async def test_one_report_on_two_dated_rows_is_told_once_under_its_first_date(
        live_loop, ledger, leg, table):
    """FMP's reschedule leftover puts a second, later row beside a report, and the dedup key
    carries the row's date. The key is what must be asserted, never a count of one: a first
    date that never landed would leave one push, keyed on the wrong report. Whether a restart
    gets the filing back from the table or not."""
    if table == "down":
        _table_down(live_loop)
    await leg(live_loop, _Market())
    assert _keys(ledger) == _FIRST_PUSH


THANKSGIVING_EVE, THANKSGIVING, BLACK_FRIDAY = (date(2026, 11, day) for day in (25, 26, 27))


async def _first_row_lands_then_fmp_fills_the_next(loop, market: _Market, first: date,
                                                   second: date) -> None:
    await loop.minute(market, _et_time(first, 8))
    market.fills(first)
    await loop.minute(market, _et_time(first, 9))
    await loop.minute(market, _et_time(second, 7))
    market.fills(second)
    await loop.minute(market, _et_time(second, 8))


async def _second_row_lands_then_fmp_fills_the_first(loop, market: _Market, first: date,
                                                     second: date) -> None:
    await loop.minute(market, _et_time(first, 14))
    await loop.minute(market, _et_time(second, 8))
    market.fills(second)
    await loop.minute(market, _et_time(second, 8, 10))
    market.fills(first)
    await loop.minute(market, _et_time(second, 11))


@pytest.mark.asyncio
@pytest.mark.parametrize("live_kind", [False, True], ids=["shipped_flags", "live_kind_on"])
@pytest.mark.parametrize("first,second,told", [
    (SATURDAY, MONDAY, SATURDAY),
    (SUNDAY, MONDAY, SUNDAY),
    (SATURDAY, SUNDAY, SATURDAY),
    (FRIDAY, SATURDAY, FRIDAY),
    (FRIDAY, SUNDAY, FRIDAY),
    (THANKSGIVING_EVE, THANKSGIVING, THANKSGIVING_EVE),
    (THANKSGIVING, BLACK_FRIDAY, THANKSGIVING),
], ids=["sat_mon", "sun_mon", "sat_sun", "fri_sat", "fri_sun", "wed_holiday", "holiday_fri"])
async def test_a_report_dated_on_a_closed_day_is_told_once_with_its_first_row_landing_first(
        live_loop, ledger, monkeypatch, live_kind, first, second, told):
    """`previous_trading_day` steps over a closed day, so a Monday leftover beside a Saturday
    report named no key of it. The key is asserted, as in the table above."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", live_kind)
    await _first_row_lands_then_fmp_fills_the_next(
        live_loop, _Market(days=(first, second)), first, second)
    assert _keys(ledger) == {f"earnings_result:INTC:{told.isoformat()}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("live_kind", [False, True], ids=["shipped_flags", "live_kind_on"])
@pytest.mark.parametrize("first,second", [
    (FRIDAY, SATURDAY), (SATURDAY, SUNDAY), (THANKSGIVING_EVE, THANKSGIVING),
], ids=["fri_sat", "sat_sun", "wed_holiday"])
async def test_a_report_dated_on_a_closed_day_is_told_once_with_its_later_row_landing_first(
        live_loop, ledger, monkeypatch, live_kind, first, second):
    """FMP can fill the rows in either order, so the neighbour must be found from both sides. The
    row FMP filled first is the one told, whichever date it carries."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", live_kind)
    await _second_row_lands_then_fmp_fills_the_first(
        live_loop, _Market(days=(first, second)), first, second)
    assert _keys(ledger) == {f"earnings_result:INTC:{second.isoformat()}"}


@pytest.mark.asyncio
async def test_a_monday_leftover_filled_ahead_of_its_saturday_row_is_the_one_push(
        live_loop, ledger):
    """By Monday a Saturday landing is outside the result window, so it tells nobody."""
    await _second_row_lands_then_fmp_fills_the_first(
        live_loop, _Market(days=(SATURDAY, MONDAY)), SATURDAY, MONDAY)
    assert _keys(ledger) == {f"earnings_result:INTC:{MONDAY.isoformat()}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("report", [SATURDAY, SUNDAY])
async def test_the_monday_leftover_of_a_weekend_report_reads_as_it_and_never_as_live(
        live_loop, ledger, report):
    market = _Market(days=(report, MONDAY))
    await live_loop.minute(market, _et_time(report, 8))
    market.fills(report)
    await live_loop.minute(market, _et_time(report, 9))
    await live_loop.minute(market, _et_time(MONDAY, 7))
    card = live_loop.service.snapshot("INTC", MONDAY, _et_time(MONDAY, 7))
    assert (card.status, card.reads_as) == (STATUS_REPORTED, report)
    assert (card.eps.actual, card.revenue.actual) == (0.60, 17.1e9)


@pytest.mark.asyncio
async def test_with_the_shipped_flags_the_leftover_row_sends_no_second_result_push(
        live_loop, ledger, monkeypatch):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    await _fmp_fills_the_leftover_row(live_loop, _Market())
    assert _keys(ledger) == _FIRST_PUSH
    assert {row["kind"] for row in ledger.rows} == {KIND_EARNINGS_RESULT}


@pytest.mark.asyncio
async def test_with_the_shipped_flags_the_rows_filling_later_first_are_one_push(
        live_loop, ledger, monkeypatch):
    """The first row's 8-K told nobody, so the row FMP filled is the one told, whichever date
    it carries, and the row FMP fills after it finds that user already told."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    await _restart_then_fmp_fills_both_rows_the_later_first(live_loop, _Market())
    assert _keys(ledger) == {"earnings_result:INTC:2026-10-29"}


@pytest.mark.asyncio
async def test_a_report_nobody_heard_about_holds_nothing_back(live_loop, ledger, monkeypatch):
    """A filing alone tells nobody with the live kind off, so FMP's figure on the later row is
    the report's only push, and it goes out at that minute, not at the 16:00 pass."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    market = _Market()
    await live_loop.minute(market, _et_time(OCT_28, 16))
    market.filings.append(_results_8k(filed=OCT_28, accepted="2026-10-28T20:02:00.000Z"))
    await live_loop.minute(market, _et_time(OCT_28, 16, 3))
    await live_loop.minute(market, _et_time(OCT_29, 8))
    assert ledger.rows == []
    market.fills(OCT_29)
    await live_loop.minute(market, _et_time(OCT_29, 8, 10))
    assert _keys(ledger) == {"earnings_result:INTC:2026-10-29"}


@pytest.mark.asyncio
@pytest.mark.parametrize("live_kind,eps", [(True, 0.42), (True, 0.60), (False, 0.60)],
                         ids=["live_kind_alone", "live_kind_and_a_surprise", "shipped_flags"])
async def test_a_stale_friday_row_filled_on_monday_never_costs_mondays_push(
        live_loop, ledger, monkeypatch, live_kind, eps):
    """Friday's landing is older than yesterday, so it told nobody, and the true report on
    Monday is the push. It goes out when the rows land, with no daily pass behind the live kind."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", live_kind)
    friday, monday = date(2026, 10, 30), date(2026, 11, 2)
    market = _Market(days=(friday, monday))
    await live_loop.minute(market, _et_time(friday, 16))
    await live_loop.minute(market, _et_time(monday, 8))
    market.fills(friday, eps=eps)
    market.fills(monday, eps=eps)
    await live_loop.minute(market, _et_time(monday, 9, 5), keep_memo=True)
    assert _keys(ledger) == {"earnings_result:INTC:2026-11-02"}


@pytest.mark.asyncio
@pytest.mark.parametrize("figure", [{"eps": 0.60, "revenue": None}, {"eps": None, "revenue": 17e9}],
                         ids=["eps_alone", "revenue_alone"])
async def test_a_report_told_on_one_figure_was_told(live_loop, ledger, figure):
    market = _Market()
    await live_loop.minute(market, _et_time(OCT_28, 16))
    market.fills(OCT_28, **figure)
    await live_loop.minute(market, _et_time(OCT_28, 17))
    await live_loop.minute(market, _et_time(OCT_29, 8))
    market.fills(OCT_29)
    await live_loop.minute(market, _et_time(OCT_29, 8, 10))
    assert _keys(ledger) == _FIRST_PUSH


@pytest.mark.asyncio
async def test_each_user_hears_of_the_report_once_on_whichever_row_reached_them(
        monkeypatch, live_loop):
    """Who was told is per user. u2 takes Results & Surprises alone, so the 8-K told only u1, and
    FMP's figure on the leftover row is u2's one push."""
    svc = _ledger_dispatcher(
        monkeypatch, users=("u1", "u2"), prefs={"u2": {"notify_earnings_live": False}})
    await _restart_then_fmp_fills_the_leftover_row(live_loop, _Market())
    assert svc.supabase.told == {("u1", "earnings_result:INTC:2026-10-28"),
                                 ("u2", "earnings_result:INTC:2026-10-29")}


@pytest.mark.asyncio
async def test_a_failed_read_of_who_was_told_goes_back_for_the_next_round(monkeypatch, caplog):
    svc = _ledger_dispatcher(monkeypatch)
    svc.supabase.rows.append({"user_id": "u1", "dedup_key": "earnings_result:INTC:2026-10-28",
                              "kind": KIND_EARNINGS_RESULT})
    svc.supabase.failing_reads = 1
    with caplog.at_level(logging.WARNING, logger=sender.logger.name):
        await sender.dispatch_landings([_big()], NOW)
    assert any("INTC" in line and "520 from the edge" in line for line in _warnings(caplog))
    assert ("INTC", REPORT) not in sender._live_done, "the key went back"
    reads = len(svc.supabase.reads)
    await sender.dispatch_landings([_big()], NOW)
    assert len(svc.supabase.reads) > reads
    assert svc.supabase.told == {("u1", "earnings_result:INTC:2026-10-28")}


@pytest.mark.asyncio
async def test_a_report_nobody_heard_about_goes_out_after_a_failed_read(monkeypatch):
    svc = _ledger_dispatcher(monkeypatch)
    svc.supabase.failing_reads = 1
    await sender.dispatch_landings([_big()], NOW)
    assert svc.supabase.rows == []
    await sender.dispatch_landings([_big()], NOW)
    assert svc.supabase.told == {("u1", "earnings_result:INTC:2026-10-29")}


@pytest.mark.asyncio
async def test_the_daily_pass_tells_nobody_on_a_failed_read_of_who_was_told(monkeypatch):
    """The read told nobody and claimed nothing, and it says so to the pass, which can try again:
    a 0 would read as a ticker nobody watches."""
    svc = _ledger_dispatcher(monkeypatch)
    svc.supabase.failing_reads = 1
    with pytest.raises(pds.WatcherLookupFailed, match="520 from the edge"):
        await daily._dispatch(kind=KIND_EARNINGS_RESULT, symbol="INTC", when="2026-10-29",
                              title="t", body="b")
    assert svc.supabase.rows == []


class _FailingWatcherRead(_Dispatch):
    """A dispatcher whose read of who to tell fails for `failing` tickers, `times` times each, as
    the real one says it when asked (`strict`)."""

    def __init__(self, failing, times=1):
        super().__init__()
        self.left = {symbol: times for symbol in failing}

    async def notify_watchers(self, **kw):
        if self.left.get(kw["ticker"]):
            assert kw.get("strict") is True, "only a strict send hears about a failed read"
            self.left[kw["ticker"]] -= 1
            raise pds.WatcherLookupFailed(f"{kw['ticker']}: RuntimeError: 520 from the edge")
        return await super().notify_watchers(**kw)


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", [{"INTC"}, {"NVDA"}, {"MSFT", "INTC"}],
                         ids=["a_result", "a_reports_tomorrow", "two_results"])
async def test_a_failed_read_of_who_to_tell_leaves_the_day_open_for_the_next_wake(
        monkeypatch, clean_sender, live, caplog, failing):
    """One failed read used to cost that ticker's push for everyone watching it: the pass sent
    nothing for it, stamped the day done, and Thursday's after-close report was out of the window
    by Saturday. The other tickers are still told, the pass raises so the day stays open, and the
    next hourly wake tells the rest."""
    rec = _FailingWatcherRead(failing)
    monkeypatch.setattr(daily, "get_push_dispatch_service", lambda: rec)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    with caplog.at_level(logging.WARNING, logger=daily.__name__):
        with pytest.raises(pds.WatcherLookupFailed) as raised:
            await _pass_over_the_calendar(monkeypatch)
    told = {send["ticker"] for send in rec.sent}
    assert told == {"NVDA", "MSFT", "INTC"} - failing, "the other tickers are still told"
    for symbol in failing:
        assert symbol in str(raised.value)
        assert any(symbol in line and "520 from the edge" in line for line in _warnings(caplog))

    stats, run = await _pass_over_the_calendar(monkeypatch)
    assert [send["ticker"] for send in rec.sent].count("MSFT") == 1 + ("MSFT" not in failing)
    assert {send["ticker"] for send in rec.sent} == {"NVDA", "MSFT", "INTC"}
    assert (run.success, stats["symbols"]) == (True, 3)


@pytest.mark.asyncio
async def test_the_next_wake_tells_each_watcher_of_the_failed_ticker_once(
        monkeypatch, clean_sender, live):
    """The real dispatcher over the ledger: the wake after the failed read tells INTC's watchers,
    and the ledger keeps everyone the first wake told from hearing twice."""
    svc = _ledger_dispatcher(monkeypatch, users=("u1", "u2"))
    _clock_at(monkeypatch, FRI_1600)
    _flags_as_shipped(monkeypatch)
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    svc.supabase.failing_reads = 1      # MSFT's read of who holds a neighbouring key: it goes first
    with pytest.raises(pds.WatcherLookupFailed, match="MSFT"):
        await _pass_over_the_calendar(monkeypatch)
    assert {key for _, key in svc.supabase.told} == {
        "earnings_upcoming:NVDA:2026-10-31", "earnings_result:INTC:2026-10-29"}

    stats, run = await _pass_over_the_calendar(monkeypatch)
    assert run.success is True
    assert sorted(svc.supabase.told) == sorted(
        (user, key) for user in ("u1", "u2") for key in (
            "earnings_upcoming:NVDA:2026-10-31", "earnings_result:MSFT:2026-10-30",
            "earnings_result:INTC:2026-10-29"))
    assert len(svc.supabase.rows) == 6, "nobody the first wake told is told again"


@pytest.mark.asyncio
async def test_a_user_told_on_the_neighbouring_date_is_skipped_by_the_daily_pass(monkeypatch):
    svc = _ledger_dispatcher(monkeypatch, users=("u1", "u2"))
    svc.supabase.rows.append({"user_id": "u1", "dedup_key": "earnings_result:INTC:2026-10-28",
                              "kind": KIND_EARNINGS_RESULT})
    await daily._dispatch(kind=KIND_EARNINGS_RESULT, symbol="INTC", when="2026-10-29",
                          title="t", body="b")
    assert svc.supabase.told == {("u1", "earnings_result:INTC:2026-10-28"),
                                 ("u2", "earnings_result:INTC:2026-10-29")}


@pytest.mark.asyncio
async def test_a_reports_tomorrow_push_asks_nothing_of_the_ledger(monkeypatch):
    svc = _ledger_dispatcher(monkeypatch)
    await daily._dispatch(kind=daily.KIND_EARNINGS_UPCOMING, symbol="INTC", when="2026-10-29",
                          title="t", body="b")
    assert svc.supabase.reads == []
    assert len(svc.supabase.rows) == 1


@pytest.mark.parametrize("when,around", [
    ("2026-10-28", ["2026-10-27", "2026-10-29"]),                            # an ordinary Wednesday
    ("2026-10-30", ["2026-10-29", "2026-10-31", "2026-11-01", "2026-11-02"]),  # Friday
    ("2026-11-02", ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-03"]),  # Monday
    ("2026-11-25", ["2026-11-24", "2026-11-26", "2026-11-27"]),              # over Thanksgiving
    ("2026-11-26", ["2026-11-25", "2026-11-27"]),                            # a row dated on it
    ("2026-10-31", ["2026-10-30", "2026-11-01", "2026-11-02"]),              # a Saturday row
    ("2026-11-01", ["2026-10-30", "2026-10-31", "2026-11-02"]),              # a Sunday row
])
def test_the_neighbouring_keys_are_every_day_out_to_the_trading_day_either_side(when, around):
    assert daily.neighbouring_result_keys("intc", when) == [
        f"earnings_result:INTC:{day}" for day in around]


def test_the_neighbours_run_both_ways_across_every_closed_day_of_the_calendar():
    """Either side, because FMP can fill the two rows in either order: a row that names another
    must be named by it, or the second row to land finds nobody told."""
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(730)]
    near = {day: set(daily.neighbouring_result_keys("INTC", day.isoformat())) for day in days}
    key = {day: daily.result_dedup_key("INTC", day.isoformat()) for day in days}
    for day in days:
        assert key[day] not in near[day], "a report never holds itself back"
        for other in (day + timedelta(days=i) for i in range(1, 9)):
            if other in near:
                assert (key[other] in near[day]) == (key[day] in near[other]), (day, other)


def test_the_neighbours_of_a_row_stop_at_the_trading_day_either_side():
    """A company does report again the week after: nothing past those days may hold it back."""
    keys = daily.neighbouring_result_keys("INTC", "2026-10-28")
    assert "earnings_result:INTC:2026-10-26" not in keys
    assert "earnings_result:INTC:2026-10-30" not in keys
    keys = daily.neighbouring_result_keys("INTC", "2026-11-09")
    assert "earnings_result:INTC:2026-11-05" not in keys
    assert "earnings_result:INTC:2026-11-11" not in keys


@pytest.mark.asyncio
async def test_a_user_told_on_a_saturday_is_skipped_by_the_daily_pass_on_monday(monkeypatch):
    svc = _ledger_dispatcher(monkeypatch, users=("u1", "u2"))
    svc.supabase.rows.append({"user_id": "u1", "dedup_key": "earnings_result:INTC:2026-11-07",
                              "kind": KIND_EARNINGS_RESULT})
    await daily._dispatch(kind=KIND_EARNINGS_RESULT, symbol="INTC", when="2026-11-09",
                          title="t", body="b")
    assert svc.supabase.told == {("u1", "earnings_result:INTC:2026-11-07"),
                                 ("u2", "earnings_result:INTC:2026-11-09")}


# ── a cent print inside its half cent ────────────────────────────────────────


def _cent_print_rows(**kw) -> Dict[str, Any]:
    return {"symbol": "INTC", "date": REPORT.isoformat(), "epsEstimated": 0.0148,
            "revenueEstimated": 16.6e9, **kw}


async def _cent_print_lands(*, estimate=0.0148) -> None:
    """$0.01 lands, seen pending first, through the real fold and the real sender."""
    live = EarningsLiveService()
    live.observe_rows(REPORT, [_cent_print_rows(epsEstimated=estimate)],
                      datetime(2026, 10, 29, 13, tzinfo=timezone.utc))
    live.observe_rows(REPORT, [_cent_print_rows(epsEstimated=estimate, epsActual=0.01,
                                                revenueActual=16.6e9)], NOW)
    await sender.dispatch_landings(live.landed_snapshots(NOW), NOW)


@pytest.mark.asyncio
async def test_a_cent_print_inside_its_half_cent_never_sends_the_result_kind(dispatch, monkeypatch):
    """$0.01 against 0.0148 is a 32% miss on paper, and the true figure can sit on either side of
    that consensus. The sender works the surprise out for itself, so it must read the band too."""
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    await _cent_print_lands()
    assert dispatch.sent == []
    assert ("INTC", REPORT) in sender._surprise_done, "decided, not waiting on another figure"


@pytest.mark.asyncio
async def test_a_cent_print_inside_its_half_cent_is_stated_with_no_verdict(dispatch):
    await _cent_print_lands()
    assert [s["kind"] for s in dispatch.sent] == [KIND_EARNINGS_LIVE]
    assert "EPS reported." in dispatch.sent[0]["body"]
    assert "missed" not in dispatch.sent[0]["body"].split("·")[1]


@pytest.mark.asyncio
async def test_a_cent_print_just_outside_its_half_cent_still_sends_the_miss(dispatch, monkeypatch):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_PUSH_ENABLED", False)
    await _cent_print_lands(estimate=0.0151)
    assert [s["kind"] for s in dispatch.sent] == [KIND_EARNINGS_RESULT]
    assert "missed consensus by 34%" in dispatch.sent[0]["body"]


def test_the_daily_pass_reads_the_same_band_or_it_would_send_what_the_live_sender_held():
    """The key stays unclaimed, so the 16:00 pass would otherwise state the verdict itself."""
    inside = _cent_print_rows(epsActual=0.01)
    outside = _cent_print_rows(epsActual=0.01, epsEstimated=0.0151)
    assert daily.surprise_pct(inside) is None
    assert daily.select_results([inside], REPORT) == []
    assert daily.select_results([outside], REPORT) == [
        ("INTC", REPORT.isoformat(), pytest.approx(-0.3377, abs=0.0001))]


def test_the_read_of_who_was_told_is_batched_and_names_both_columns(monkeypatch):
    svc = _ledger_dispatcher(monkeypatch)
    svc.supabase.rows.append({"user_id": "u450", "dedup_key": "k1"})
    users = [f"u{i}" for i in range(451)]
    assert svc._holders_of(users, ["k1", "k2"]) == {"u450"}
    assert [len(read["user_id"]) for read in svc.supabase.reads] == [200, 200, 51]
    assert all(read["dedup_key"] == {"k1", "k2"} for read in svc.supabase.reads)


class _EdgeBlip(Exception):
    """What postgrest raises when Cloudflare answers for the origin: an int status for a code."""

    code = 520


def test_an_edge_blip_on_the_read_of_who_was_told_is_read_again_in_place(monkeypatch, caplog):
    """One of ceil(N/200) reads hitting a 520 used to cost every watcher of the ticker: the live
    sender's round, or the whole 16:00 ET pass, waits for the next try. It is a read, so it is
    read again where it failed, and only that batch."""
    import app.utils.supabase_errors as supabase_errors

    monkeypatch.setattr(supabase_errors.time, "sleep", lambda _seconds: None)
    svc = _ledger_dispatcher(monkeypatch)
    svc.supabase.rows.append({"user_id": "u450", "dedup_key": "k1"})
    svc.supabase.read_error = lambda: _EdgeBlip("JSON could not be generated")
    svc.supabase.failing_reads = 1
    users = [f"u{i}" for i in range(451)]
    with caplog.at_level(logging.WARNING):
        assert svc._holders_of(users, ["k1", "k2"]) == {"u450"}
    assert [len(read["user_id"]) for read in svc.supabase.reads] == [200, 200, 200, 51]
    assert any("retrying" in line for line in _warnings(caplog))


@pytest.mark.asyncio
async def test_a_send_goes_out_through_one_edge_blip_and_stops_on_a_failure_that_stays(
        monkeypatch):
    import app.utils.supabase_errors as supabase_errors

    monkeypatch.setattr(supabase_errors.time, "sleep", lambda _seconds: None)
    svc = _ledger_dispatcher(monkeypatch)
    svc.supabase.read_error = lambda: _EdgeBlip("JSON could not be generated")
    svc.supabase.failing_reads = 1
    assert await _send_unless_told(svc, strict=True) == 1

    svc = _ledger_dispatcher(monkeypatch)
    svc.supabase.read_error = lambda: _EdgeBlip("JSON could not be generated")
    svc.supabase.failing_reads = 3
    with pytest.raises(pds.WatcherLookupFailed, match="who holds"):
        await _send_unless_told(svc, strict=True)
    assert svc.supabase.rows == [] and len(svc.supabase.reads) == 3


async def _send_unless_told(svc: pds.PushDispatchService, **kw) -> int:
    """One `earnings_result` send of key "k" unless "k0" is held, at the file's NOW (16:05 ET).
    Handed no `now`, the dispatcher reads the wall clock, and from 22:00 to 07:00 ET (the night
    of a user who never set quiet hours) it holds every send for the morning: a count would pass
    or fail by the hour the file ran."""
    return await svc.notify_watchers(
        ticker="INTC", title="t", body="b", dedup_key="k", kind=KIND_EARNINGS_RESULT,
        data={"ticker": "INTC"}, unless_claimed=["k0"], now=NOW, **kw)


@pytest.mark.asyncio
async def test_with_nothing_held_a_send_reaches_everyone_it_would_have(monkeypatch):
    """At the file's NOW (16:05 ET): these users never set Quiet Hours, so from 22:00 to 07:00 ET
    the night hold would park both rows and the count would read 0 whatever `unless_claimed` did."""
    svc = _ledger_dispatcher(monkeypatch, users=("u1", "u2"))
    assert await _send_unless_told(svc) == 2
    assert svc.supabase.told == {("u1", "k"), ("u2", "k")}


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["deferred", "pending", "sent", "no_device", "failed"])
async def test_a_row_in_any_state_is_a_user_told(monkeypatch, state):
    """A push waiting out quiet hours still goes out, and a row is what the inbox shows. Told
    means no row of their own either: a send held for the night still claims one."""
    svc = _ledger_dispatcher(monkeypatch)
    svc.supabase.rows.append({"user_id": "u1", "dedup_key": "k0", "push_state": state})
    assert await _send_unless_told(svc) == 0
    assert svc.supabase.told == {("u1", "k0")}


@pytest.mark.asyncio
async def test_a_send_with_everyone_told_says_so_and_ends(monkeypatch, caplog):
    svc = _ledger_dispatcher(monkeypatch)
    svc.supabase.rows.append({"user_id": "u1", "dedup_key": "k0"})
    with caplog.at_level(logging.INFO, logger=pds.logger.name):
        sent = await _send_unless_told(svc)
    assert sent == 0 and svc.supabase.told == {("u1", "k0")}
    (line,) = [r.getMessage() for r in caplog.records if "already told" in r.getMessage()]
    assert "INTC" in line and "k0" in line


@pytest.mark.asyncio
async def test_a_strict_send_raises_on_a_failed_read_of_who_was_told(monkeypatch):
    svc = _ledger_dispatcher(monkeypatch)
    svc.supabase.failing_reads = 1
    with pytest.raises(pds.WatcherLookupFailed, match="520 from the edge"):
        await _send_unless_told(svc, strict=True)
    assert svc.supabase.rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize("leg,model_calls", [
    (_same_process, 1), (_restart_overnight, 1), (_fmp_completes_the_first_row_first, 0),
], ids=["same_process", "restart_overnight", "fmp_completes_the_first_row_first"])
async def test_an_evening_8k_stays_off_the_next_days_record(live_loop, ledger, leg, model_calls):
    """The filing belongs to the day it was accepted: that row's record must not hold it, or pay
    to read it again."""
    market = _Market()
    await leg(live_loop, market)
    leftover = live_loop.service._records[("INTC", OCT_29)]
    assert leftover.filing is None and leftover.fmp_eps is None
    assert market.model_calls == model_calls


@pytest.mark.asyncio
async def test_the_leftover_row_beside_an_evening_8k_reads_as_that_report(live_loop, ledger):
    market = _Market()
    await _same_process(live_loop, market)
    card = live_loop.service.snapshot("INTC", OCT_29, _et_time(OCT_29, 8, 5))
    assert card.status == els.STATUS_REPORTED and card.preliminary is True
    assert (card.eps.actual, card.revenue.actual) == (0.44, 17.1e9)


@pytest.mark.asyncio
async def test_after_a_restart_the_leftover_row_reads_as_the_report_the_table_kept(live_loop,
                                                                                   ledger):
    """The filing outlives the process in `earnings_filing_cache`, kept under the day it was
    accepted, so after a restart the leftover row reads as that report just as its same-process
    twin above does, from the company's own figures, and the release is not read again."""
    market = _Market()
    await _restart_overnight(live_loop, market)
    assert set(live_loop.table.rows) == {("INTC", OCT_28.isoformat())}
    assert OCT_28.isoformat() in live_loop.table.reads[-1], "the new process asked for its day"
    card = live_loop.service.snapshot("INTC", OCT_29, _et_time(OCT_29, 8, 5))
    assert card.status == els.STATUS_REPORTED and card.preliminary is True
    assert card.reads_as == OCT_28
    assert (card.eps.actual, card.revenue.actual) == (0.44, 17.1e9)
    assert (card.eps.surprise_pct, card.revenue.surprise_pct) == (7.32, 3.01)
    assert (card.eps.source, card.revenue.source) == (SOURCE_FILING, SOURCE_FILING)
    assert card.filing is not None and card.filing.accession == _INTEL_ACC
    assert market.model_calls == 1


@pytest.mark.asyncio
async def test_after_a_restart_with_no_table_the_leftover_row_has_no_report_to_read_as(
        live_loop, ledger):
    """Until the table answers (not applied yet, or a failed read), a restart is what it was
    before the table: the filing went with the process, and the row says what it knows, nothing
    yet, never a figure it no longer holds."""
    _table_down(live_loop)
    market = _Market()
    await _restart_overnight(live_loop, market)
    assert live_loop.table.rows == {}
    assert len(live_loop.table.reads) == 2, "each process tried the table once"
    card = live_loop.service.snapshot("INTC", OCT_29, _et_time(OCT_29, 8, 5))
    assert card.status == els.STATUS_LIVE and card.reads_as is None
    assert (card.eps.actual, card.revenue.actual) == (None, None)
    assert market.model_calls == 1, "the previous day's filing is not read again"


_RESTARTS_AFTER_A_FILING = ("restart_overnight", "restart_then_fmp_fills_the_leftover_row",
                            "restart_then_fmp_fills_both_rows_the_later_first",
                            "restart_at_nine_then_fmp_fills_the_leftover_row")


@pytest.mark.asyncio
@pytest.mark.parametrize("leg", [_ONE_REPORT[name] for name in _RESTARTS_AFTER_A_FILING],
                         ids=_RESTARTS_AFTER_A_FILING)
async def test_with_no_table_a_restart_still_tells_the_report_once_under_its_first_date(
        live_loop, ledger, leg):
    """The legs of `test_one_report_on_two_dated_rows_is_told_once_under_its_first_date` that
    restart after a filing was read, with nothing kept and nothing restored: a failed table never
    stops a tick, and the ledger, not the table, keeps the report to one push."""
    _table_down(live_loop)
    await leg(live_loop, _Market())
    assert live_loop.table.rows == {}
    assert len(live_loop.table.reads) == 2, "each process tried the table once"
    assert _keys(ledger) == _FIRST_PUSH


@pytest.mark.asyncio
async def test_after_a_restart_the_leftover_row_reads_as_the_report_the_table_gives_back(
        live_loop, ledger):
    """The table outlives the deploy, so the evening's filing comes back to its record, and the
    leftover row reads as that report the next morning, with no second read of the release."""
    market = _Market()
    await _restart_overnight(live_loop, market)
    card = live_loop.service.snapshot("INTC", OCT_29, _et_time(OCT_29, 8, 5))
    assert (card.status, card.reads_as, card.preliminary) == (els.STATUS_REPORTED, OCT_28, True)
    assert (card.eps.actual, card.revenue.actual) == (0.44, 17.1e9)
    assert market.model_calls == 1
    assert [(symbol, day) for symbol, day in live_loop.table.rows] == [
        ("INTC", OCT_28.isoformat())]


@pytest.mark.asyncio
async def test_a_lone_row_with_an_evening_8k_is_told_on_its_own_date(live_loop, ledger):
    market = _Market(days=(OCT_28,))
    await _same_process(live_loop, market)
    assert _keys(ledger) == _FIRST_PUSH
    assert market.model_calls == 1


@pytest.mark.asyncio
async def test_a_pair_with_an_8k_during_the_day_is_told_on_its_first_date(live_loop, ledger):
    market = _Market()
    await live_loop.minute(market, _et_time(OCT_28, 16))
    market.filings.append(_results_8k(filed=OCT_28, accepted="2026-10-28T20:02:00.000Z"))
    await live_loop.minute(market, _et_time(OCT_28, 16, 3))
    await live_loop.minute(market, _et_time(OCT_29, 8))
    assert _keys(ledger) == _FIRST_PUSH


@pytest.mark.asyncio
async def test_a_stale_earlier_row_never_stops_the_true_report_the_next_day(live_loop, ledger):
    market = _Market()
    await live_loop.minute(market, _et_time(OCT_28, 16))
    await live_loop.minute(market, _et_time(OCT_29, 15))
    market.filings.append(_results_8k(filed=OCT_29, accepted="2026-10-29T20:02:00.000Z"))
    await live_loop.minute(market, _et_time(OCT_29, 16, 3))
    assert _keys(ledger) == {"earnings_result:INTC:2026-10-29"}


@pytest.mark.asyncio
async def test_a_row_fmp_dates_after_an_evening_8k_waits_for_fmps_figure(live_loop, ledger):
    """The cost of giving an 8-K to the day it was accepted: this one has no fast path, and its
    one push comes with FMP's figure."""
    market = _Market(days=(OCT_29,))
    market.filings.append(_EVENING_8K)
    await live_loop.minute(market, _et_time(OCT_29, 8))
    assert ledger.rows == [] and market.model_calls == 0
    market.fills(OCT_29)
    await live_loop.minute(market, _et_time(OCT_29, 8, 10))
    assert _keys(ledger) == {"earnings_result:INTC:2026-10-29"}


@pytest.mark.asyncio
async def test_a_friday_evening_8k_is_told_on_friday_and_never_again_on_monday(live_loop, ledger):
    friday, monday = date(2026, 10, 30), date(2026, 11, 2)
    market = _Market(days=(friday, monday))
    await live_loop.minute(market, _et_time(friday, 16))
    market.filings.append(_results_8k(filed=monday, accepted="2026-10-30T22:05:00.000Z"))
    await live_loop.minute(market, _et_time(friday, 18, 6))
    assert _keys(ledger) == {"earnings_result:INTC:2026-10-30"}
    await live_loop.minute(market, _et_time(monday, 8))
    market.fills(monday)
    await live_loop.minute(market, _et_time(monday, 8, 10))
    assert _keys(ledger) == {"earnings_result:INTC:2026-10-30"}
    assert market.model_calls == 1


@pytest.mark.asyncio
async def test_a_release_already_out_when_the_process_starts_is_not_pushed(live_loop, ledger):
    """SEC stamps the 8-K in UTC: 20:02Z is 16:02 ET. A process that first sees the report at
    19:00 ET finds a release three hours old, which is no landing. Read as 20:02 ET, it would
    look newer than the process and go out at 19:00 as news, three hours late."""
    market = _Market(days=(OCT_28,))
    market.filings.append(_results_8k(filed=OCT_28, accepted="2026-10-28T20:02:00.000Z"))
    at = _et_time(OCT_28, 19)
    await live_loop.minute(market, at)
    assert market.model_calls == 1
    assert live_loop.service.snapshot("INTC", OCT_28, at).eps.actual == 0.44, "on the card"
    assert ledger.rows == []
