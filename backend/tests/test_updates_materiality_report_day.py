"""Report-day behaviour of the Insights materiality gate.

TestFlight, ORCL, Thu 2026-09-10: the card was written that morning (accurate
then) and froze on `daily_cap` before the 16:10 ET release, so the evening read
"Oracle is set to report" over a completed report. These tests pin the three
levers that stop that — all no-ops at their defaults:

* ``report_day`` ADDS `AFTER_CLOSE_EARNINGS_RESERVE` to the cap (never carves it
  out of the ordinary allowance) and slows the after-hours cooldown to 30 min;
* ``earnings_pending_today`` holds that extra allowance back through the
  regular session only;
* ``earnings_reported_at`` regenerates a card written before the results landed
  exactly once — through the fingerprint and both reserves, never through the
  daily cap, the attempt cap or the cooldown.

Plus the failure cooldown, which keeps a repeatable rejection from spending a
scope's whole failure allowance within the hour.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.updates_materiality import (
    ACTION_GENERATE,
    ACTION_SKIP,
    ACTION_TOUCH,
    AFTER_CLOSE_EARNINGS_RESERVE,
    COOLDOWN_SESSION_SECONDS,
    PER_SCOPE_DAILY_CAP,
    PER_SCOPE_DAILY_CAP_MARKET,
    PER_SCOPE_FAILURE_ALLOWANCE,
    PROMPT_VERSION,
    REPORT_DAY_AFTERHOURS_COOLDOWN_SECONDS,
    attempt_cap_for,
    compute_inputset_id,
    corpus_article_ids,
    daily_cap_for,
    decide,
    earnings_reserve_applies,
    premarket_cap_for,
)
from app.utils.market_hours import (
    SESSION_AFTERHOURS,
    SESSION_CLOSED,
    SESSION_PREMARKET,
    SESSION_REGULAR,
)

_ET = ZoneInfo("America/New_York")
MODEL = "gemini-2.5-flash-lite"
CORPUS = [
    {"external_id": "https://a.example/1", "headline": "A"},
    {"external_id": "https://a.example/2", "headline": "B"},
]


def _et(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=_ET)


# Thu 2026-09-10 — ORCL's report day.
REGULAR = _et(2026, 9, 10, 13, 0)
PREMARKET = _et(2026, 9, 10, 7, 0)
AFTERHOURS = _et(2026, 9, 10, 17, 0)
DAY = "2026-09-10"


def _decide(now, phase, state=None, **over):
    kwargs = dict(
        scope="ORCL",
        corpus=CORPUS,
        quote={"changePercentage": 0.4, "marketCap": 5e11},
        state=state,
        market_change_percent=0.1,
        close_cycle_start=now - timedelta(hours=20),
        now=now,
        model=MODEL,
        market_active=phase != SESSION_CLOSED,
        is_market_scope=False,
        session_phase=phase,
    )
    kwargs.update(over)
    return decide(**kwargs)


def _spent(n, *, attempts=None, last_generated_at=None, **extra):
    state = {
        "regen_day": DAY,
        "regen_count_today": n,
        "attempts_today": n if attempts is None else attempts,
    }
    if last_generated_at is not None:
        state["last_generated_at"] = last_generated_at.isoformat()
    state.update(extra)
    return state


def _unchanged(now, *, last_generated_at):
    """State whose fingerprint equals the corpus the gate will compute."""
    inputset = compute_inputset_id(
        corpus_article_ids(CORPUS), "flat", MODEL, PROMPT_VERSION,
    )
    return {
        "last_inputset_id": inputset,
        "last_price_band": "flat",
        "last_generated_at": last_generated_at.isoformat(),
        "close_cycle": now.isoformat(),
    }


# ── caps ────────────────────────────────────────────────────────────────

def test_report_day_adds_the_after_close_allowance_to_the_boosted_cap():
    assert AFTER_CLOSE_EARNINGS_RESERVE == 6
    assert daily_cap_for(False, earnings_window=True, report_day=True) == (
        PER_SCOPE_DAILY_CAP_MARKET + AFTER_CLOSE_EARNINGS_RESERVE
    )


def test_report_day_without_the_boost_never_drops_below_the_ordinary_cap():
    # The boost set and the statuses come from the same snapshot in production,
    # but a missing boost must never SHRINK a reporting ticker's allowance.
    assert daily_cap_for(False, report_day=True) == (
        PER_SCOPE_DAILY_CAP + AFTER_CLOSE_EARNINGS_RESERVE
    )
    assert daily_cap_for(False, report_day=True) > daily_cap_for(False)


def test_the_market_scope_ignores_report_day():
    assert daily_cap_for(True, report_day=True) == PER_SCOPE_DAILY_CAP_MARKET
    assert daily_cap_for(True, earnings_window=True, report_day=True) == (
        PER_SCOPE_DAILY_CAP_MARKET
    )


@pytest.mark.parametrize("is_market", [True, False])
@pytest.mark.parametrize("boost", [True, False])
@pytest.mark.parametrize("report_day", [True, False])
def test_attempt_cap_stays_above_the_daily_cap(is_market, boost, report_day):
    daily = daily_cap_for(is_market, earnings_window=boost, report_day=report_day)
    attempts = attempt_cap_for(is_market, earnings_window=boost, report_day=report_day)
    assert attempts == daily + PER_SCOPE_FAILURE_ALLOWANCE


def test_premarket_cap_does_not_grow_on_report_day():
    # The extra allowance is for after the results, never for the overnight wire.
    assert premarket_cap_for(False, earnings_window=True) == (
        PER_SCOPE_DAILY_CAP_MARKET // 3
    )


def test_defaults_are_no_ops():
    state = _spent(PER_SCOPE_DAILY_CAP)
    assert _decide(REGULAR, SESSION_REGULAR, state).reason == "daily_cap"


# ── earnings_reserve_applies ─────────────────────────────────────────────

@pytest.mark.parametrize("phase,pending,expected", [
    (SESSION_REGULAR, True, True),
    (SESSION_REGULAR, False, False),
    (SESSION_PREMARKET, True, False),
    (SESSION_AFTERHOURS, True, False),
    (SESSION_CLOSED, True, False),
])
def test_reserve_applies_only_in_the_regular_session(phase, pending, expected):
    assert earnings_reserve_applies(REGULAR, phase, pending) is expected


def test_reserve_never_applies_on_a_half_day():
    # 2026-11-27 closes at 13:00 ET: the equity sweep stops, so an allowance
    # held "until the close" could never be spent.
    half_day = _et(2026, 11, 27, 11, 0)
    assert earnings_reserve_applies(half_day, SESSION_REGULAR, True) is False


def test_reserve_reads_a_naive_now_as_utc():
    naive = datetime(2026, 9, 10, 17, 0)   # 13:00 ET
    assert earnings_reserve_applies(naive, SESSION_REGULAR, True) is True


# ── the reserve in the gate ──────────────────────────────────────────────

def _report_day(**over):
    kw = dict(earnings_window=True, report_day=True, earnings_pending_today=True)
    kw.update(over)
    return kw


def test_regular_session_is_held_at_the_ordinary_boosted_cap_while_pending():
    d = _decide(REGULAR, SESSION_REGULAR, _spent(16), **_report_day())
    assert d.action == ACTION_SKIP
    assert d.reason == "earnings_reserved"


def test_regular_session_below_the_hold_still_generates():
    d = _decide(REGULAR, SESSION_REGULAR, _spent(15), **_report_day())
    assert d.action == ACTION_GENERATE


def test_after_the_close_the_reserve_is_spendable():
    d = _decide(AFTERHOURS, SESSION_AFTERHOURS, _spent(16), **_report_day())
    assert d.action == ACTION_GENERATE


def test_once_reported_the_reserve_lifts_in_the_session():
    d = _decide(
        REGULAR, SESSION_REGULAR, _spent(16),
        **_report_day(earnings_pending_today=False),
    )
    assert d.action == ACTION_GENERATE


def test_the_extended_cap_still_binds():
    d = _decide(AFTERHOURS, SESSION_AFTERHOURS, _spent(22), **_report_day())
    assert d.reason == "daily_cap"


def test_premarket_reserve_binds_before_the_earnings_reserve():
    d = _decide(PREMARKET, SESSION_PREMARKET, _spent(5), **_report_day())
    assert d.reason == "premarket_reserved"


def test_the_attempt_cap_outranks_the_earnings_reserve():
    # The reason that lasts all day is the one reported.
    d = _decide(
        REGULAR, SESSION_REGULAR, _spent(16, attempts=26), **_report_day(),
    )
    assert d.reason == "attempt_cap"


def test_the_market_scope_is_never_earnings_reserved():
    d = _decide(
        REGULAR, SESSION_REGULAR, _spent(15), scope="__MARKET__",
        is_market_scope=True, **_report_day(),
    )
    assert d.action == ACTION_GENERATE


def test_half_day_report_is_not_reserved():
    half = _et(2026, 11, 27, 11, 0)
    state = _spent(16)
    state["regen_day"] = "2026-11-27"
    d = _decide(half, SESSION_REGULAR, state, **_report_day())
    assert d.action == ACTION_GENERATE


# ── cooldowns ─────────────────────────────────────────────────────────────

def test_report_day_after_hours_cooldown_is_thirty_minutes():
    assert REPORT_DAY_AFTERHOURS_COOLDOWN_SECONDS == 1800
    recent = AFTERHOURS - timedelta(minutes=20)
    state = _spent(17, last_generated_at=recent)
    assert _decide(
        AFTERHOURS, SESSION_AFTERHOURS, state, **_report_day(),
    ).reason == "cooldown"
    # An ordinary ticker at the same instant is past its 15-minute cooldown.
    assert _decide(
        AFTERHOURS, SESSION_AFTERHOURS, _spent(3, last_generated_at=recent),
    ).action == ACTION_GENERATE


def test_report_day_regular_session_keeps_the_ordinary_cooldown():
    recent = REGULAR - timedelta(seconds=COOLDOWN_SESSION_SECONDS + 30)
    d = _decide(
        REGULAR, SESSION_REGULAR, _spent(3, last_generated_at=recent),
        **_report_day(),
    )
    assert d.action == ACTION_GENERATE


def test_a_recent_failure_waits_one_cooldown():
    state = _spent(
        2, attempts=3,
        last_generated_at=REGULAR - timedelta(hours=2),
        last_failure_at=(REGULAR - timedelta(minutes=5)).isoformat(),
    )
    d = _decide(REGULAR, SESSION_REGULAR, state)
    assert d.action == ACTION_SKIP
    assert d.reason == "failure_cooldown"


def test_an_old_failure_does_not_block():
    state = _spent(
        2, attempts=3,
        last_generated_at=REGULAR - timedelta(hours=2),
        last_failure_at=(REGULAR - timedelta(minutes=20)).isoformat(),
    )
    assert _decide(REGULAR, SESSION_REGULAR, state).action == ACTION_GENERATE


def test_a_failure_older_than_the_last_success_is_ignored():
    state = _spent(
        2, attempts=3,
        last_generated_at=REGULAR - timedelta(minutes=16),
        last_failure_at=(REGULAR - timedelta(minutes=18)).isoformat(),
    )
    assert _decide(REGULAR, SESSION_REGULAR, state).action == ACTION_GENERATE


def test_a_failure_on_a_cold_scope_still_waits():
    state = {"last_failure_at": (REGULAR - timedelta(minutes=2)).isoformat()}
    assert _decide(REGULAR, SESSION_REGULAR, state).reason == "failure_cooldown"


def test_a_malformed_failure_stamp_is_ignored():
    state = {"last_failure_at": "not-a-date"}
    assert _decide(REGULAR, SESSION_REGULAR, state).action == ACTION_GENERATE


# ── the one-shot "results just landed" trigger ─────────────────────────────

def test_results_landing_after_the_card_regenerates_an_unchanged_corpus():
    written = AFTERHOURS - timedelta(hours=2)
    state = _unchanged(AFTERHOURS, last_generated_at=written)
    # Without the trigger, the corpus is unchanged → skip.
    assert _decide(AFTERHOURS, SESSION_AFTERHOURS, state).reason == (
        "fingerprint_unchanged"
    )
    d = _decide(
        AFTERHOURS, SESSION_AFTERHOURS, state,
        report_day=True, earnings_reported_at=AFTERHOURS - timedelta(minutes=10),
    )
    assert d.action == ACTION_GENERATE
    assert "earnings_reported" in d.reason


def test_the_trigger_fires_once():
    # The card was generated AT (or after) the moment the results were seen.
    seen = AFTERHOURS - timedelta(minutes=40)
    state = _unchanged(AFTERHOURS, last_generated_at=seen)
    d = _decide(
        AFTERHOURS, SESSION_AFTERHOURS, state,
        report_day=True, earnings_reported_at=seen,
    )
    assert d.action in (ACTION_SKIP, ACTION_TOUCH)
    assert d.reason in ("fingerprint_unchanged", "cycle_touch")


def test_the_trigger_bypasses_the_premarket_reserve():
    # A before-the-open report that lands at 07:30 is worth one card now.
    written = PREMARKET - timedelta(hours=2)
    state = _spent(5, last_generated_at=written)
    blocked = _decide(PREMARKET, SESSION_PREMARKET, state, **_report_day())
    assert blocked.reason == "premarket_reserved"
    d = _decide(
        PREMARKET, SESSION_PREMARKET, state, earnings_window=True,
        report_day=True, earnings_reported_at=PREMARKET - timedelta(minutes=5),
    )
    assert d.action == ACTION_GENERATE


def test_the_trigger_never_passes_the_daily_cap():
    state = _spent(22, last_generated_at=AFTERHOURS - timedelta(hours=1))
    d = _decide(
        AFTERHOURS, SESSION_AFTERHOURS, state, earnings_window=True,
        report_day=True, earnings_reported_at=AFTERHOURS - timedelta(minutes=5),
    )
    assert d.reason == "daily_cap"


def test_the_trigger_respects_the_cooldown():
    state = _spent(4, last_generated_at=AFTERHOURS - timedelta(minutes=10))
    d = _decide(
        AFTERHOURS, SESSION_AFTERHOURS, state, report_day=True,
        earnings_reported_at=AFTERHOURS - timedelta(minutes=5),
    )
    assert d.reason == "cooldown"


def test_the_trigger_accepts_a_naive_timestamp_as_utc():
    written = AFTERHOURS - timedelta(hours=2)
    state = _unchanged(AFTERHOURS, last_generated_at=written)
    naive_seen = (AFTERHOURS - timedelta(minutes=10)).astimezone(timezone.utc).replace(
        tzinfo=None
    )
    d = _decide(
        AFTERHOURS, SESSION_AFTERHOURS, state, report_day=True,
        earnings_reported_at=naive_seen,
    )
    assert d.action == ACTION_GENERATE


def test_the_market_scope_ignores_the_trigger():
    written = AFTERHOURS - timedelta(minutes=30)
    state = _unchanged(AFTERHOURS, last_generated_at=written)
    d = _decide(
        AFTERHOURS, SESSION_AFTERHOURS, state, scope="__MARKET__",
        is_market_scope=True, earnings_reported_at=AFTERHOURS,
    )
    assert d.reason != "earnings_reported"
    assert "earnings_reported" not in d.reason
