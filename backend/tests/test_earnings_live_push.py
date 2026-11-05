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
  * neither kind goes out for a report older than yesterday, the daily pass's own window, and
    such a report is handled once, for good;
  * the round cap's warning counts the reports still owed a push, nothing else.
The dispatcher is a recorder — nothing here reaches APNs or Supabase.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

import app.services.notification_senders.earnings_live_sender as sender
import app.services.notification_senders.earnings_sender as daily
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
    assert len(lines) == 1 and "1 report(s)" in lines[0]


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
    assert len(lines) == 1 and "1 report(s)" in lines[0]


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
    assert len(lines) == 1 and "5 report(s)" in lines[0]


@pytest.mark.asyncio
async def test_the_cap_warning_with_the_result_flag_off(dispatch, monkeypatch, caplog):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_RESULT_PUSH_ENABLED", False)
    fresh = [_snap(f"A{i:03d}", eps=0.42, eps_pct=2.44, rev=17.1e9) for i in range(61)]
    done = [_snap(f"Z{i:03d}", eps=0.42, eps_pct=2.44, rev=17.1e9) for i in range(300)]
    _seed_done(done, surprise=False)
    with caplog.at_level(logging.WARNING, logger=sender.__name__):
        await sender.dispatch_landings(fresh + done, NOW)
    lines = _warnings(caplog)
    assert len(lines) == 1 and "1 report(s)" in lines[0]


@pytest.mark.asyncio
async def test_the_cap_warning_counts_reports_not_sends(dispatch, caplog):
    """With both flags on a beat costs two sends, and the line still counts one report."""
    fresh = [_snap(f"A{i:03d}", eps=0.60, eps_pct=46.34) for i in range(31)]
    with caplog.at_level(logging.WARNING, logger=sender.__name__):
        await sender.dispatch_landings(fresh, NOW)
    assert len(dispatch.sent) == 60
    lines = _warnings(caplog)
    assert len(lines) == 1 and "1 report(s)" in lines[0]


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
async def test_a_report_held_back_by_the_cap_past_midnight_is_not_told(dispatch):
    """The cap defers to the next round, and that round can be the next day."""
    fresh = [_beat(FRIDAY, f"A{i:03d}") for i in range(31)]
    late = _noon(SATURDAY).replace(hour=23, minute=59)
    await sender.dispatch_landings(fresh, late)
    assert len(dispatch.sent) == 60
    await sender.dispatch_landings(fresh, _noon(SUNDAY))
    assert len(dispatch.sent) == 60, "the last report has left the window"


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


def test_the_copy_is_informational_never_directive():
    for snap in (_snap(eps=0.44, eps_pct=7.3, rev=17.1e9, rev_pct=3.0),
                 _snap(eps=0.30, eps_pct=-26.8, rev=15.0e9, rev_pct=-9.6)):
        title, body = sender.live_copy(snap)
        text = f"{title} {body}".lower()
        for word in ("buy", "sell", "don't miss", "act now", "position yourself"):
            assert word not in text
