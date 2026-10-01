"""P16 (2026-10-01): a lone reschedule leftover is no longer the next earnings date.

A company that reports EARLIER than first announced can leave the original date in FMP's
per-symbol feed as a pending row. The shared ``next_pending_earnings`` rule compares dates
only, so when that leftover was the ONLY pending row it came back as the next report: the
Financials card read "Expected <leftover>" (with the leftover's own timing), the AI chat
context quoted it, it became the ``earnings_cache`` invalidation key, and the projection
(next period end + this ticker's median lag) never ran.

The pairing already knows which fiscal period P each reported release belongs to, and it
assumes no quarter releases within ``_MIN_ANNOUNCE_LAG_DAYS`` (7) of its period end. A
pending row dated within ``STALE_RESCHEDULE_DAYS`` (21) after P's release and before the
NEXT period end + 7 cannot be any later quarter's release, so ``_reschedule_leftovers``
proves it a leftover and ``_find_next_earnings_date`` drops it (the unreported row only)
before the unchanged shared rule. Without period evidence nothing is dropped.

Every "→ the projection / None" assertion below fails on the round-3 code (it returned the
leftover, unconfirmed). Hermetic: FMP is faked; dates that depend on "today" are relative.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.integrations.fmp import FMPException
from app.services._earnings_common import within_reschedule_window
from app.services import earnings_service as es
from app.services.earnings_service import (
    EarningsService,
    _reschedule_leftover_evidence,
    _reschedule_leftovers,
)

_TODAY = datetime.now(timezone.utc).date()


def _d(days_from_today: int) -> str:
    return (_TODAY + timedelta(days=days_from_today)).strftime("%Y-%m-%d")


class _FakeFMP:
    def __init__(self, income, estimates=None, feed=None, feed_error=None):
        self.income, self.estimates, self.feed = income, estimates or [], feed or []
        self.feed_error = feed_error

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        return self.income

    async def get_analyst_estimates(self, ticker, period="quarter", limit=20):
        return self.estimates

    async def get_historical_prices(self, ticker, from_date=None, to_date=None):
        return []

    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        if self.feed_error is not None:
            raise self.feed_error
        return self.feed


def _svc(fake=None) -> EarningsService:
    svc = EarningsService.__new__(EarningsService)
    svc.fmp = fake
    svc.supabase = None
    return svc


def _inc(date_, period, fy=2026, eps=0.3, revenue=1e9):
    return {"date": date_, "period": period, "fiscalYear": fy, "epsDiluted": eps,
            "eps": eps, "revenue": revenue, "reportedCurrency": "USD"}


def _rel(date_, eps=1.1, est=1.0, time="amc"):
    """A REPORTED feed row."""
    return {"date": date_, "epsActual": eps, "epsEstimated": est,
            "revenueActual": 1.0e9, "revenueEstimated": 1.0e9, "time": time}


def _pending(date_, time="amc", **extra):
    return {"date": date_, "epsActual": None, "epsEstimated": 1.05,
            "revenueActual": None, "revenueEstimated": 1.05e9, "time": time, **extra}


def _est(date_):
    return {"date": date_, "epsAvg": 1.0, "revenueAvg": 1.0e9}


# ── The shape the audit reproduced ────────────────────────────────────────────
# Period end P = today-30, reported EARLY at today-3 (lag 27, like the two quarters
# before it). The originally announced date, today+4, is still listed as a pending row and
# is the ONLY pending row. The next period end (analyst estimate) is today+61, so the
# honest next date is the projection today+61+27 = today+88.

_P = -30
_P_NEXT = 61
_LAG = 27
_PROJECTION = _d(_P_NEXT + _LAG)

_INCOME_A = [_inc(_d(_P - 182), "Q1"), _inc(_d(_P - 91), "Q2"), _inc(_d(_P), "Q3")]
_INCOME_A2 = _INCOME_A[:2]          # FMP has no income row yet for the just-reported Q3
_ESTIMATES = [_est(_d(_P - 182)), _est(_d(_P - 91)), _est(_d(_P)),
              _est(_d(_P_NEXT)), _est(_d(_P_NEXT + 91))]
_RELEASES = [_rel(_d(_P - 182 + _LAG)), _rel(_d(_P - 91 + _LAG)), _rel(_d(_P + _LAG))]


def _feed(leftover_day=4, *extra):
    return [*_RELEASES, _pending(_d(leftover_day)), *extra]


def _assert_projection(nd):
    assert nd is not None
    assert nd.date == _PROJECTION
    assert nd.is_confirmed is False
    assert nd.timing == "Time Not Specified"


def _leftover_warnings(caplog, ticker):
    return [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "reschedule leftover" in r.getMessage()
        and ticker in r.getMessage()
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Through _build_earnings
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_phase_a_lone_leftover_is_replaced_by_the_projection(caplog):
    """Round 3: next_earnings_date = today+4, unconfirmed, 'After Market Close'."""
    caplog.set_level(logging.WARNING)
    resp = await _svc(_FakeFMP(_INCOME_A, _ESTIMATES, _feed()))._build_earnings("EARLY")

    _assert_projection(resp.next_earnings_date)
    assert resp.next_earnings_date.date != _d(4)
    assert resp.degraded == []
    warned = _leftover_warnings(caplog, "EARLY")
    assert warned, "a dropped leftover must be logged with the ticker"
    assert _d(4) in warned[0].getMessage() and _d(_P) in warned[0].getMessage()


@pytest.mark.asyncio
async def test_phase_a2_lone_leftover_is_replaced_by_the_projection(caplog):
    """The just-reported quarter has no income row yet (Phase A2 synthesizes it): its
    release still proves the leftover, so the same projection — never today+4."""
    caplog.set_level(logging.WARNING)
    resp = await _svc(_FakeFMP(_INCOME_A2, _ESTIMATES, _feed()))._build_earnings("EARLY2")

    # Phase A2 really did synthesize the just-reported quarter.
    assert any(q.fiscal_date == _d(_P) and q.actual_value is not None
               for q in resp.eps_quarters)
    _assert_projection(resp.next_earnings_date)
    assert resp.degraded == []
    assert _leftover_warnings(caplog, "EARLY2")


@pytest.mark.asyncio
async def test_a_leftover_dated_today_is_not_the_next_date():
    """Round 3: 'Expected <today>' — and today as the cache key rebuilt on every read."""
    resp = await _svc(_FakeFMP(_INCOME_A, _ESTIMATES, _feed(0)))._build_earnings("TODAY")
    _assert_projection(resp.next_earnings_date)
    assert resp.next_earnings_date.date != _d(0)


@pytest.mark.asyncio
async def test_a_same_date_pending_twin_of_todays_release_is_not_the_next_date():
    """FMP can list a pending twin beside the reported row (see ec_by_date). Reported TODAY
    with a pending twin today: round 3 showed 'Expected <today>'."""
    lag = 27
    p = -lag                                # period end; released today, lag 27
    income = [_inc(_d(p - 182), "Q1"), _inc(_d(p - 91), "Q2"), _inc(_d(p), "Q3")]
    estimates = [_est(_d(p - 182)), _est(_d(p - 91)), _est(_d(p)), _est(_d(p + 91))]
    feed = [_rel(_d(p - 182 + lag)), _rel(_d(p - 91 + lag)),
            _pending(_d(0)),                # the twin, listed BEFORE the real row
            _rel(_d(0))]
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("TWIN")

    nd = resp.next_earnings_date
    assert nd is not None and nd.date == _d(p + 91 + lag)
    assert nd.is_confirmed is False and nd.timing == "Time Not Specified"
    # The reported row still wins its quarter.
    assert _q_actual(resp.eps_quarters, _d(p)) == pytest.approx(1.1)


def _q_actual(quarters, fiscal_date):
    q = next((q for q in quarters if q.fiscal_date == fiscal_date), None)
    return None if q is None else q.actual_value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "estimates",
    [
        _ESTIMATES[:3],     # estimates only up to P: nothing after it to project from
        [],                 # no estimates at all (P_next falls back to P + 91)
    ],
)
async def test_a_leftover_with_no_quarter_to_project_from_gives_no_date(estimates):
    """Honest degradation: no card rather than 'Expected <leftover>'."""
    resp = await _svc(_FakeFMP(_INCOME_A, estimates, _feed()))._build_earnings("NOEST")
    assert resp.next_earnings_date is None
    assert resp.degraded == []


@pytest.mark.asyncio
async def test_a_leftover_plus_a_later_pending_row_shows_the_later_row_confirmed():
    """Unchanged behaviour: the real next report is listed, so it is shown Confirmed."""
    feed = _feed(4, _pending(_d(90), time="bmo"))
    resp = await _svc(_FakeFMP(_INCOME_A, _ESTIMATES, feed))._build_earnings("LATER")
    nd = resp.next_earnings_date
    assert nd.date == _d(90) and nd.is_confirmed is True
    assert nd.timing == "Before Market Open"


@pytest.mark.asyncio
async def test_without_period_evidence_the_lone_row_is_kept_unconfirmed(caplog):
    """No income at all → no release is paired with a fiscal period, so nothing is proven:
    the shared rule still shows the lone suspect row, never as Confirmed."""
    caplog.set_level(logging.WARNING)
    resp = await _svc(_FakeFMP([], _ESTIMATES, _feed()))._build_earnings("NOPERIOD")
    nd = resp.next_earnings_date
    assert nd is not None and nd.date == _d(4)
    assert nd.is_confirmed is False
    assert nd.timing == "After Market Close"
    assert not _leftover_warnings(caplog, "NOPERIOD")


# ── The boundary: L < P_next + 7 is a leftover, L at P_next + 7 is not ─────────
# P = today-85 (the newest income row), released LATE at today-2 (day 83, paired through
# _match_late_release); the next period end P_next = P + 91 = today+6. Both candidate
# leftovers sit inside the 21-day window after that release.

_BP = -85
_BP_NEXT = _BP + 91
_B_INCOME = [_inc(_d(_BP - 182), "Q1"), _inc(_d(_BP - 91), "Q2"), _inc(_d(_BP), "Q3")]
_B_ESTIMATES = [_est(_d(_BP - 182)), _est(_d(_BP - 91)), _est(_d(_BP)),
                _est(_d(_BP_NEXT)), _est(_d(_BP_NEXT + 91))]
_B_RELEASES = [_rel(_d(_BP - 182 + 30)), _rel(_d(_BP - 91 + 30)), _rel(_d(-2))]
_B_LAG = 30                                 # median of (30, 30, 83)


@pytest.mark.asyncio
async def test_boundary_six_days_past_the_next_period_end_is_a_leftover():
    feed = [*_B_RELEASES, _pending(_d(_BP_NEXT + 6))]
    resp = await _svc(_FakeFMP(_B_INCOME, _B_ESTIMATES, feed))._build_earnings("BND")
    # The late release really was paired with P.
    assert _q_actual(resp.eps_quarters, _d(_BP)) == pytest.approx(1.1)
    nd = resp.next_earnings_date
    assert nd.date == _d(_BP_NEXT + _B_LAG)
    assert nd.is_confirmed is False and nd.timing == "Time Not Specified"


@pytest.mark.asyncio
async def test_boundary_seven_days_past_the_next_period_end_keeps_todays_rule():
    """Day 7 after P_next could be P_next's own (fast) release: never dropped."""
    lone = [*_B_RELEASES, _pending(_d(_BP_NEXT + 7))]
    resp = await _svc(_FakeFMP(_B_INCOME, _B_ESTIMATES, lone))._build_earnings("BND7")
    nd = resp.next_earnings_date
    assert nd.date == _d(_BP_NEXT + 7)
    assert nd.is_confirmed is False          # inside the 21-day window: shown, unconfirmed

    with_later = [*lone, _pending(_d(100))]
    resp = await _svc(_FakeFMP(_B_INCOME, _B_ESTIMATES, with_later))._build_earnings("BND7")
    nd = resp.next_earnings_date
    assert nd.date == _d(100) and nd.is_confirmed is True


# ── Malformed and failed feeds ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_junk_unsorted_feed_gives_the_same_answer_as_the_clean_feed():
    clean = await _svc(_FakeFMP(_INCOME_A, _ESTIMATES, _feed()))._build_earnings("JUNK")
    junk_feed = [
        _pending(_d(4), epsActual=float("nan"), revenueActual=float("inf")),
        "not a row", None, 42, ["list"],
        {"date": None, "epsActual": 9.9},
        {"date": "bad", "epsActual": 9.9},
        {"epsActual": 9.9},
        *reversed(_RELEASES),
    ]
    junk = await _svc(_FakeFMP(_INCOME_A, _ESTIMATES, junk_feed))._build_earnings("JUNK")

    _assert_projection(junk.next_earnings_date)
    assert junk.model_dump() == clean.model_dump()


@pytest.mark.asyncio
async def test_a_failed_calendar_is_degraded_and_shows_no_invented_date():
    """Exactly as before P16: no feed → no lag history → no projection, and the build is
    DEGRADED (never persisted)."""
    fake = _FakeFMP(_INCOME_A, _ESTIMATES, feed_error=FMPException("429 from upstream"))
    resp = await _svc(fake)._build_earnings("FAIL")
    assert resp.degraded == ["earnings_feed"]
    assert resp.next_earnings_date is None


# ═══════════════════════════════════════════════════════════════════════════
# _find_next_earnings_date — the keyword-only opt-in
# ═══════════════════════════════════════════════════════════════════════════


def _next(ec, release_periods=None, estimates=(), used=(), reported_through=None, lag=None):
    return _svc()._find_next_earnings_date(
        list(estimates), list(ec), set(used), _d(0),
        reported_through=reported_through, announce_lag_days=lag,
        release_periods=release_periods, ticker="UNIT",
    )


def test_old_call_shape_drops_nothing():
    """No release_periods (every pre-P16 caller): the lone suspect row is kept."""
    rows = [_rel(_d(-3)), _pending(_d(4))]
    nd = _svc()._find_next_earnings_date([], rows, set(), _d(0))
    assert nd.date == _d(4) and nd.is_confirmed is False


def test_a_reported_twin_on_the_leftovers_date_is_never_removed():
    """P = today-85, released LATE today (reported) with a pending twin today; X sits at
    P_next + 7 (not provable). Only the unreported twin is dropped: the reported row stays,
    so X is still inside its 21-day window and is shown UNCONFIRMED. Dropping by date
    alone would remove the release too and show X as 'Confirmed'."""
    p, p_next = _d(-85), _d(6)
    rows = [_rel(_d(0)), _pending(_d(0)), _pending(_d(13))]
    nd = _next(rows, release_periods={_d(0): p}, estimates=[{"date": p_next}],
               used=[p], reported_through=p, lag=30)
    assert nd.date == _d(13)
    assert nd.is_confirmed is False


def test_dropped_leftover_with_no_lag_history_is_none():
    rows = [_rel(_d(-3)), _pending(_d(4))]
    assert _next(rows, release_periods={_d(-3): _d(-30)},
                 estimates=[{"date": _d(61)}], used=[_d(-30)],
                 reported_through=_d(-30), lag=None) is None


# ═══════════════════════════════════════════════════════════════════════════
# _reschedule_leftovers — the pure rule (fixed dates, explicit today)
# ═══════════════════════════════════════════════════════════════════════════

_U_P = "2026-06-30"
_U_TODAY = "2026-09-21"
_U_RELEASE = "2026-09-20"                   # P's late release (day 82)


@pytest.mark.parametrize("release_periods", [None, {}, [], "junk", 42])
def test_no_period_evidence_proves_nothing(release_periods):
    rows = [_rel(_U_RELEASE), _pending("2026-09-25")]
    assert _reschedule_leftovers(rows, release_periods, ["2026-09-30"], _U_TODAY) == set()


@pytest.mark.parametrize("ec", [None, "junk", {"date": "2026-09-25"}, 42])
def test_a_non_list_feed_proves_nothing(ec):
    assert _reschedule_leftovers(ec, {_U_RELEASE: _U_P}, ["2026-09-30"], _U_TODAY) == set()


def test_an_unparseable_today_proves_nothing():
    rows = [_rel(_U_RELEASE), _pending("2026-09-25")]
    assert _reschedule_leftovers(rows, {_U_RELEASE: _U_P}, [], "not-a-day") == set()


def test_garbage_period_keys_are_ignored():
    rows = [_rel(_U_RELEASE), _pending("2026-10-06"), _pending("2026-10-07")]
    keys = [None, "bad", 42, "2026/09/30", "", {"x": 1}, "2026-09-30"]
    # P_next = 2026-09-30 (the one valid key): 10-06 is P_next+6, 10-07 is P_next+7.
    assert _reschedule_leftovers(rows, {_U_RELEASE: _U_P}, keys, _U_TODAY) == {"2026-10-06"}
    # Non-list keys never raise; they fall back to P + 91 (2026-09-29), so 10-05
    # (P+91+6) is a leftover and 10-06 (P+91+7) is not.
    rows_fb = [_rel(_U_RELEASE), _pending("2026-10-05"), _pending("2026-10-06")]
    for bad_keys in ("junk", None, 42):
        assert _reschedule_leftovers(
            rows_fb, {_U_RELEASE: _U_P}, bad_keys, _U_TODAY) == {"2026-10-05"}


def test_with_no_period_in_range_the_p_plus_91_fallback_is_used():
    """Keys only 5 days after P (that quarter itself) and 274 days after it (too far):
    P_next = P + 91 = 2026-09-29, so 10-05 (P_next+6) is a leftover and 10-06 is not."""
    rows = [_rel(_U_RELEASE), _pending("2026-10-05"), _pending("2026-10-06")]
    keys = ["2026-07-05", "2027-03-31"]
    assert _reschedule_leftovers(rows, {_U_RELEASE: _U_P}, keys, _U_TODAY) == {"2026-10-05"}
    ev = _reschedule_leftover_evidence(rows, {_U_RELEASE: _U_P}, keys, _U_TODAY)
    assert ev == {"2026-10-05": (_U_RELEASE, _U_P, "2026-09-29")}


def test_a_period_within_fifteen_days_of_p_is_not_p_next():
    rows = [_rel(_U_RELEASE), _pending("2026-10-06"), _pending("2026-10-07")]
    keys = ["2026-07-10", "2026-07-15", "2026-09-30"]   # 10 and 15 days after P: P itself
    ev = _reschedule_leftover_evidence(rows, {_U_RELEASE: _U_P}, keys, _U_TODAY)
    assert ev == {"2026-10-06": (_U_RELEASE, _U_P, "2026-09-30")}


def test_the_earliest_period_in_range_is_p_next():
    rows = [_rel(_U_RELEASE), _pending("2026-10-06")]
    keys = ["2026-12-31", "2026-09-30", "2026-07-16"]   # unsorted; 07-16 is day 16
    ev = _reschedule_leftover_evidence(rows, {_U_RELEASE: _U_P}, keys, _U_TODAY)
    # P_next = 07-16 (16 days after P is in range): 10-06 is far past it → not a leftover.
    assert ev == {}


def test_the_window_after_the_release_is_21_days():
    """A row more than 21 days after the release is not tested at all (another rule's
    business); a row before the release is not either."""
    p, rel = "2026-03-31", "2026-04-02"
    rows = [_rel(rel), _pending("2026-04-23"), _pending("2026-04-24")]
    assert _reschedule_leftovers(rows, {rel: p}, ["2026-06-30"], "2026-04-02") == {"2026-04-23"}


def test_rows_before_today_and_reported_rows_are_never_listed():
    rows = [_rel("2026-09-10"), _pending("2026-09-12"), _rel("2026-09-25"),
            _pending("2026-09-28")]
    periods = {"2026-09-10": _U_P, "2026-09-25": "2026-07-01"}
    out = _reschedule_leftovers(rows, periods, ["2026-09-30"], "2026-09-20")
    assert out == {"2026-09-28"}          # 09-12 is past; 09-25 is a reported row


def test_a_release_with_no_known_period_proves_nothing_even_beside_one_that_does():
    rows = [_rel("2026-09-18"), _pending("2026-09-25")]
    # The only period evidence is for a DIFFERENT release date.
    assert _reschedule_leftovers(rows, {"2026-06-01": _U_P}, ["2026-09-30"], _U_TODAY) == set()
    # An unparseable period is no evidence either.
    assert _reschedule_leftovers(rows, {"2026-09-18": "junk"}, ["2026-09-30"], _U_TODAY) == set()


def test_input_order_and_junk_rows_do_not_matter():
    rows = [_rel(_U_RELEASE), _pending("2026-10-06"), _pending("2026-10-07")]
    junk = [None, "x", {"date": None}, {"date": "bad"}, *reversed(rows),
            _pending("2026-10-06", epsActual=float("nan"))]
    keys = ["2026-09-30"]
    assert (_reschedule_leftovers(junk, {_U_RELEASE: _U_P}, keys, _U_TODAY)
            == _reschedule_leftovers(rows, {_U_RELEASE: _U_P}, keys, _U_TODAY)
            == {"2026-10-06"})


def test_the_payload_version_was_bumped_for_p16():
    assert es._EARNINGS_PAYLOAD_VERSION == 4


# ═══════════════════════════════════════════════════════════════════════════
# The other surfaces that name the next report (fix-pass finding, 2026-10-01)
# ═══════════════════════════════════════════════════════════════════════════
# Before P16 the lone leftover was the card's date too, so every surface agreed on it.
# Now the card drops it, but the Updates earnings alert (`tracking_service.
# _get_earnings_alerts`) and the eve-of push (`earnings_sender.select_upcoming`) read
# FMP's MARKET-WIDE calendar and filter nothing, so they can still announce it. Their
# fix belongs to those modules: per alerted symbol, read the per-symbol feed and drop a
# row for which `within_reschedule_window(row, feed)` holds, keeping the alert (with a
# logged warning) when that read fails. Never widen the market-wide window instead: FMP
# caps it at 4,000 rows and keeps the NEWEST, so a wider window loses exactly the past
# reported rows that recognise a leftover.
#
# That filter is only safe if every leftover the card drops is inside the shared window.
# The first test pins that: if `_reschedule_leftover_evidence` ever proves a row the
# shared predicate does not flag, the alert would announce a date the card dropped.


def _sweep_rows(rd):
    fmt = lambda d: d.strftime("%Y-%m-%d")  # noqa: E731
    pend = [_pending(fmt(rd + timedelta(days=k))) for k in range(-3, 40)]
    rows = [
        _rel(fmt(rd - timedelta(days=91))), _rel(fmt(rd)), *pend,
        None, "x", {"date": None}, {"date": "bad"},
        _pending(fmt(rd + timedelta(days=5)), epsActual=float("nan")),
    ]
    return pend, rows


def test_every_proven_leftover_is_inside_the_shared_reschedule_window():
    """Sweep: release lags 20-95 days after P, today 0-10 days after the release, five
    shapes of next-period evidence, pending rows from 3 days before to 39 after the
    release, junk rows mixed in. Every row P16 proves a leftover must also be flagged by
    `within_reschedule_window` over the same feed."""
    fmt = lambda d: d.strftime("%Y-%m-%d")  # noqa: E731
    p = datetime(2026, 6, 30)
    proven = 0
    for lag in (20, 40, 60, 82, 95):
        rd = p + timedelta(days=lag)
        pend, rows = _sweep_rows(rd)
        by_day = {r["date"]: r for r in pend}
        for after_release in (0, 3, 10):
            today = fmt(rd + timedelta(days=after_release))
            for offsets in ((), (91,), (80,), (100,), (16,)):
                keys = [fmt(p + timedelta(days=o)) for o in offsets]
                left = _reschedule_leftovers(rows, {fmt(rd): fmt(p)}, keys, today)
                for day in left:
                    assert within_reschedule_window(by_day[day], rows), (
                        f"lag={lag} today={today} keys={keys}: the card drops {day} but "
                        f"the shared window (used by the alert filter) does not flag it"
                    )
                proven += len(left)
    # Non-vacuous: the sweep proves many leftovers, including ones more than a week
    # after the release (the early-release lags).
    assert proven >= 150, proven


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "outside earnings_service: tracking_service._get_earnings_alerts does not yet "
        "drop a reschedule leftover. Remove this marker in the change that adds that "
        "filter (strict: an XPASS fails the run)."
    ),
)
@pytest.mark.asyncio
async def test_the_updates_alert_does_not_name_a_leftover_the_card_dropped():
    from app.services.tracking_service import TrackingService

    feed = _feed()
    card = await _svc(_FakeFMP(_INCOME_A, _ESTIMATES, feed))._build_earnings("EARLY")
    # Preconditions use pytest.fail (not AssertionError), so a broken fixture is a real
    # failure rather than the expected one.
    if card.next_earnings_date is None or card.next_earnings_date.date != _PROJECTION:
        pytest.fail(f"precondition: the card should show the projection, got "
                    f"{card.next_earnings_date!r}")

    leftover = {**_pending(_d(4)), "symbol": "EARLY", "companyName": "Early Corp"}
    real = {**_pending(_d(9)), "symbol": "CONF", "companyName": "Confirmed Corp"}
    feeds = {"EARLY": feed, "CONF": [_rel(_d(-80)), _pending(_d(9))]}

    class _AlertFMP:
        async def get_earnings_calendar(self, from_date=None, to_date=None):
            return [dict(leftover), dict(real)]

        async def get_earning_calendar_full(self, ticker, raise_errors=False):
            return list(feeds.get(str(ticker).upper(), []))

    svc = TrackingService.__new__(TrackingService)
    svc.fmp = _AlertFMP()
    alerts = await svc._get_earnings_alerts(["EARLY", "CONF"])
    tickers = [a.ticker for a in alerts]
    if "CONF" not in tickers:
        pytest.fail(f"control: a real report must still alert, got {tickers}")

    assert "EARLY" not in tickers, (
        f"the Updates alert announces EARLY on the leftover {_d(4)}; the card shows "
        f"{card.next_earnings_date.date}"
    )
