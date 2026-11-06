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
  * what off costs beyond the wait: three kinds of report nobody is told, each with its sheet;
  * a lock-screen banner prints an absolute figure only from the company's own release;
  * each report is dispatched once per process (the 60 s loop re-hands every landing);
  * a failed watcher read is the one exception: it told nobody, so the key goes back, the round
    ends there (one failed read a round, however many reports wait) and the next one retries,
    live kind first;
  * neither kind goes out for a report older than yesterday, the daily pass's own window, and
    such a report is handled once, for good;
  * a round sends oldest first, so the cap cannot run a report out of that window;
  * the round cap's warning counts the reports still owed a push, nothing else;
  * a result landing in a default-settings user's night waits for 07:00 on their clock, the row
    claimed at once, one buzz at the wake, across both kinds and the daily pass, through a
    restart and past the window above;
  * what that wake says and opens is the report as it stands then: the text comes from the
    record, a surprise the record no longer holds is never buzzed, and the sheet still takes the
    report's date on any day such a row can wake;
  * the record it reads is never older than the row: the daily pass folds what it read first;
  * a figure still being confirmed is no answer, so the row asks again, a bounded number of
    times, instead of ending.
The dispatcher is a recorder, or the real one over a fake table where a failed read or the
ledger is the point — nothing here reaches APNs or Supabase.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import logging
import re
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import pytest

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
    SOURCE_FILING,
    SOURCE_FMP,
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
    """The record refuses FMP's unfilled shape, so the live sender sees no landing and no banner
    says "EPS missed consensus by 100%"."""
    def _row(**kw):
        return {"symbol": "INTC", "date": REPORT.isoformat(), "epsEstimated": 0.41,
                "revenueEstimated": 16.6e9, **kw}

    live = EarningsLiveService()
    live.observe_rows(REPORT, [_row()], datetime(2026, 10, 29, 13, 0, tzinfo=timezone.utc))
    live.observe_rows(REPORT, [_row(epsActual=0, revenueActual=0)], NOW)
    await sender.dispatch_landings(live.landed_snapshots(NOW), NOW)
    assert dispatch.sent == []


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


class _Watchlist:
    """`watchlist_items`. The page reads numbered in `failing` raise, as an edge 520 does."""

    def __init__(self, failing=(), users=("u1",)):
        self.failing, self.users, self.reads = set(failing), users, 0

    def table(self, _name):
        return self

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
    def __init__(self, rows, patch):
        self.rows, self.patch, self.filters = rows, patch, {}

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def execute(self):
        for row in self.rows:
            if all(row.get(c) == v for c, v in self.filters.items()):
                row.update(self.patch)


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
    return {"ticker": symbol, "title": title, "body": body, "kind": kind,
            "dedup_key": f"{kind}:{symbol}:{day}",
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
    """Every word, key and route it sends, and nothing else: no `strict`, which would end the
    whole pass on one failed watcher read, and the same counts on the job's claim."""
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
    """The real record the sender, the flush and the sheet all read, and a sheet with no extras."""
    async def _no_extras(self, symbol, report_day, *, loader=None):
        return None

    monkeypatch.setattr(EarningsLiveService, "history_extras", _no_extras)
    service = get_earnings_live_service()
    service.reset()
    ts.reset_earnings_calendar_cache()
    yield service
    service.reset()
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
    assert any("RuntimeError" in r.getMessage() and "INTC" in r.getMessage()
               for r in caplog.records)


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
# For most reports the result push being off is a wait for the next pass. These are the three it
# costs the buzz outright, as `earnings_live_sender`'s docstring says: the pass has its own rules,
# which the live sender does not share. The sheet keeps every one of them.

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
@pytest.mark.parametrize("result_push", [False, True], ids=["as_shipped", "result_push_on"])
@pytest.mark.parametrize("estimate,eps,gaap,body,surprise", [
    (0.05, 0.46, 0.39, "EPS beat consensus by 820%. Tap for the full picture.", 820.0),
    (0.11, 0.01, -0.02, "EPS missed consensus by 91%. Tap for the full picture.", -90.91),
], ids=["ten_times_the_estimate", "a_tenth_of_it"])
async def test_off_a_surprise_only_the_filing_can_clear_is_told_by_nobody(
        monkeypatch, clean_sender, live, result_push, estimate, eps, gaap, body, surprise):
    """FMP's figure has the dropped-digit signature and the filing's GAAP EPS says it is real, so
    the record shows it. The pass reads rows, with no GAAP beside them, and refuses it."""
    ledger, phones = _Ledger(), _Phones()
    _dispatcher_over(monkeypatch, ledger, phones)
    _set_result_push(monkeypatch, result_push)
    pending = {**_fmp_row(TUESDAY), "epsEstimated": estimate}
    filled = {**_fmp_row(TUESDAY, eps=eps, revenue=17.1e9), "epsEstimated": estimate}

    live.observe_rows(TUESDAY, [pending], TUE_2005 - timedelta(hours=8), fetched=True)
    live.absorb_filing("INTC", TUESDAY, FilingFigures(
        accession="a", accepted_at=None, url=None, eps_gaap=gaap), TUE_2005 - timedelta(hours=1))
    live.observe_rows(TUESDAY, [filled], TUE_2005, fetched=True)
    await sender.dispatch_landings(live.landed_snapshots(TUE_2005),
                                   _clock_at(monkeypatch, TUE_2005))
    stats = await _daily_pass(monkeypatch, _clock_at(monkeypatch, WED_1600), day=TUESDAY,
                              row=filled)

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
async def test_a_restart_inside_the_hold_never_buzzes_the_doubted_number(
        monkeypatch, clean_sender, live):
    """The record is gone, so the row's own text goes out. It no longer holds the 46%."""
    ledger, phones = _Ledger(), _Phones()
    svc = _dispatcher_over(monkeypatch, ledger, phones)
    _result_kind_alone(monkeypatch)
    await sender.dispatch_landings(_lands(live, THU_2210), _clock_at(monkeypatch, THU_2210))
    _fmp_says(live, THU_2340, eps=0.041)
    _clock_at(monkeypatch, FRI_0700)
    await svc.flush_deferred()

    live.reset()
    _clock_at(monkeypatch, FRI_0700 + timedelta(hours=1))
    assert (await svc.flush_deferred())["sent"] == 1

    assert phones.buzzes[0]["body"] == "Revenue beat consensus by 3% · EPS being confirmed."


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


def test_the_copy_is_informational_never_directive():
    for snap in (_snap(eps=0.44, eps_pct=7.3, rev=17.1e9, rev_pct=3.0),
                 _snap(eps=0.30, eps_pct=-26.8, rev=15.0e9, rev_pct=-9.6)):
        title, body = sender.live_copy(snap)
        text = f"{title} {body}".lower()
        for word in ("buy", "sell", "don't miss", "act now", "position yourself"):
            assert word not in text
