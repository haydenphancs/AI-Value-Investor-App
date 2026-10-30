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
  * each report is dispatched once per process (the 60 s loop re-hands every landing).
The dispatcher is a recorder — nothing here reaches APNs or Supabase.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
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
    LiveMetric,
    LiveSnapshot,
)
from app.services.notification_kinds import (
    KIND_EARNINGS_LIVE,
    KIND_EARNINGS_RESULT,
    NOTIFICATION_KINDS,
)

REPORT = date(2026, 10, 29)
NOW = datetime(2026, 10, 29, 20, 5, tzinfo=timezone.utc)


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
