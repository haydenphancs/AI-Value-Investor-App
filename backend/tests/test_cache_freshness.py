"""Tests for close-aligned cache freshness + last-close price selection.

Reports pin to the LAST COMPLETED market close, so caches must refresh on the
trading-close cycle (a weekday 6pm ET boundary) rather than a rolling TTL — the
first viewer after a new close regenerates, instead of being served "the last
close as of first generation." These tests pin that boundary logic and the
historical-close picker. Pure / offline.

Times are chosen to land on the same side of the boundary under BOTH EDT
(UTC-4, summer) and the EST (UTC-5) fallback, so they don't depend on whether
tzdata is installed in the test environment.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.services.ticker_report_cache import (
    CACHE_SCHEMA_FLOOR,
    current_close_cycle_start,
    is_cache_fresh,
)
from app.services.agents.ticker_report_data_collector import _latest_completed_close

# 2026-06-22 is a Monday; 06-19 Friday; 06-20 Saturday. All after CACHE_SCHEMA_FLOOR.
_MON_PRE_CLOSE = datetime(2026, 6, 22, 19, 30, tzinfo=timezone.utc)   # ~3:30pm ET Mon
_MON_POST_CLOSE = datetime(2026, 6, 23, 0, 30, tzinfo=timezone.utc)   # ~8:30pm ET Mon
_MON_AFTERNOON = datetime(2026, 6, 22, 18, 0, tzinfo=timezone.utc)    # ~2pm ET Mon


def test_cycle_start_is_at_or_before_now_and_a_weekday():
    cycle = current_close_cycle_start(_MON_POST_CLOSE)
    assert cycle <= _MON_POST_CLOSE
    assert cycle.weekday() < 5  # never a Sat/Sun boundary


def test_is_cache_fresh_boundary_invariants():
    # Built off the function's OWN boundary → independent of EDT vs EST offset.
    now = _MON_POST_CLOSE
    cycle = current_close_cycle_start(now)
    assert not is_cache_fresh(cycle - timedelta(seconds=1), now=now)
    assert is_cache_fresh(cycle + timedelta(seconds=1), now=now)


def test_user_scenario_pre_close_report_goes_stale_after_new_close():
    # The exact case the user raised: a report cached Monday afternoon (before
    # Monday's close) is fresh while viewed pre-close, but stale once a viewer
    # comes after Monday's close settles — so they regenerate with Monday's close.
    assert is_cache_fresh(_MON_AFTERNOON, now=_MON_PRE_CLOSE)
    assert not is_cache_fresh(_MON_AFTERNOON, now=_MON_POST_CLOSE)


def test_weekend_holds_fridays_close():
    # Saturday: the boundary is Friday's close (no Sat/Sun boundary), so a
    # Friday-evening report stays fresh through the weekend. Dates must sit
    # AFTER CACHE_SCHEMA_FLOOR (else the floor, not the cycle, marks it stale).
    sat = datetime(2026, 6, 27, 18, 0, tzinfo=timezone.utc)  # ~2pm ET Sat
    cycle = current_close_cycle_start(sat)
    assert cycle.weekday() == 4  # Friday
    fri_eve = datetime(2026, 6, 26, 23, 0, tzinfo=timezone.utc)  # ~7pm ET Fri
    assert is_cache_fresh(fri_eve, now=sat)


def test_pre_schema_floor_is_stale():
    # Older than CACHE_SCHEMA_FLOOR → always stale regardless of the cycle.
    old = CACHE_SCHEMA_FLOOR - timedelta(days=1)
    assert not is_cache_fresh(old, now=_MON_POST_CLOSE)


def test_regen_after_boundary_is_fresh_no_loop():
    # After a boundary passes, a freshly-written row (cached_at = now) is fresh —
    # so a holiday/late-FMP regen re-caches and does NOT loop on every request.
    now = _MON_POST_CLOSE
    assert is_cache_fresh(now, now=now)


# ── Last-completed-close picker ─────────────────────────────────────


def test_latest_completed_close_picks_newest_bar():
    hist = [  # FMP returns newest-first
        {"date": "2026-06-16", "close": 192.64},
        {"date": "2026-06-15", "close": 190.10},
    ]
    d, px = _latest_completed_close(hist)
    assert d == date(2026, 6, 16)
    assert px == 192.64


def test_latest_completed_close_dict_shape_and_empty():
    # FMP can return {"historical": [...]} on some plan tiers.
    d, px = _latest_completed_close({"historical": [{"date": "2026-06-16", "close": 5.0}]})
    assert d == date(2026, 6, 16) and px == 5.0
    assert _latest_completed_close([]) == (None, None)
    assert _latest_completed_close({"historical": []}) == (None, None)


def test_latest_completed_close_skips_malformed_bars():
    hist = [
        {"date": "bad-date", "close": 1.0},
        {"date": "2026-06-15", "close": None},
        {"date": "2026-06-12", "close": 188.0},
    ]
    d, px = _latest_completed_close(hist)
    assert d == date(2026, 6, 12) and px == 188.0
