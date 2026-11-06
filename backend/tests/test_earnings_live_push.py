"""Live earnings pushes: two kinds, one dedup key per report, live kind first.

The owner's rule (2026-10-29): a new "Live Results" toggle, "Results & Surprises" stays and is
now sent live too, and NOBODY GETS TWO PUSHES FOR ONE REPORT. That holds because
`push_dispatch_service` claims a key only for users who pass their toggle and cap, and both
kinds — and the daily 16:00 ET sender — claim the SAME key. What is pinned here:
  * both live kinds and the daily sender use one key (`result_dedup_key`);
  * `earnings_live` goes FIRST, sequentially, then `earnings_result`;
  * `earnings_result` is sent on FMP's adjusted EPS only, never on a filing figure (it
    reaches 1.02 users), and only past `MIN_SURPRISE`;
  * `EARNINGS_LIVE_PUSH_ENABLED` (ships off) holds only the new kind;
  * a lock-screen banner prints an absolute figure only from the company's own release;
  * each report is dispatched once per process (the 60 s loop re-hands every landing);
  * a failed watcher read is the one exception: it told nobody, so the key goes back, the round
    ends there (one failed read a round, however many reports wait) and the next one retries,
    live kind first;
  * neither kind goes out for a report older than yesterday, the daily pass's own window, and
    such a report is handled once, for good;
  * a round sends oldest first, so the cap cannot run a report out of that window;
  * the round cap's warning counts the reports still owed a push, nothing else.
The dispatcher is a recorder, or the real one over a fake table where a failed read is the
point — nothing here reaches APNs or Supabase.
"""
from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import app.services.earnings_live_service as els
import app.services.notification_senders.earnings_live_sender as sender
import app.services.notification_senders.earnings_sender as daily
import app.services.push_dispatch_service as pds
from app.config import settings
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
)
from app.services.notification_kinds import (
    KIND_EARNINGS_LIVE,
    KIND_EARNINGS_RESULT,
    NOTIFICATION_KINDS,
)
from app.utils.market_hours import ET

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


def test_the_push_flag_ships_off_and_the_result_flag_on():
    from app.config import Settings

    assert Settings.model_fields["EARNINGS_LIVE_PUSH_ENABLED"].default is False
    assert Settings.model_fields["EARNINGS_LIVE_RESULT_PUSH_ENABLED"].default is True


def test_one_key_for_both_live_kinds_and_the_daily_sender():
    assert daily.result_dedup_key("intc", "2026-10-29") == "earnings_result:INTC:2026-10-29"


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
    """FMP's unfilled shape must not reach a banner as "EPS missed consensus by 100%"."""
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
    """The shipped flags, on reports this process has not decided yet: the round stops on them
    and the next one sends nothing for them. The near-zero estimate has no honest surprise."""
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
                         ids=["shipped", "result_off", "both_on"])
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


def test_the_copy_is_informational_never_directive():
    for snap in (_snap(eps=0.44, eps_pct=7.3, rev=17.1e9, rev_pct=3.0),
                 _snap(eps=0.30, eps_pct=-26.8, rev=15.0e9, rev_pct=-9.6)):
        title, body = sender.live_copy(snap)
        text = f"{title} {body}".lower()
        for word in ("buy", "sell", "don't miss", "act now", "position yourself"):
            assert word not in text
