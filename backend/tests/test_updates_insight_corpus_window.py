"""The corpus time-window that makes the Insights badge honest.

The window is DYNAMIC: prefer the last 24h (``PRIMARY_WINDOW_HOURS``) and fall
back to 48h (``CORPUS_WINDOW_HOURS``) only when the scope has no news in 24h. The
sweeper bounds each scope's corpus to that window before BOTH the materiality
fingerprint and generation, and the Updates endpoint uses the SAME selector to
decide whether to show a card at all AND which badge to render. This pins the
boundary behaviour of the shared pure filter (what is kept/dropped, how malformed
timestamps degrade) and the 24h→48h fallback selection.
"""

from datetime import datetime, timedelta, timezone

from app.services.news_insight_service import (
    CORPUS_WINDOW_HOURS,
    PRIMARY_WINDOW_HOURS,
    articles_within_window,
    select_recent_corpus,
)

NOW = datetime(2026, 7, 21, 18, 0, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(hours=CORPUS_WINDOW_HOURS)  # 2026-07-19 18:00Z at 48h


def _row(published_at, ident="x"):
    return {"id": ident, "external_id": ident, "published_at": published_at}


def _hours_before_now(h):
    return (NOW - timedelta(hours=h)).isoformat()


def test_keeps_rows_inside_the_window_drops_older_ones():
    rows = [
        _row(_hours_before_now(1), "fresh_1h"),      # inside
        _row(_hours_before_now(40), "fresh_40h"),    # inside (< 48h)
        _row(_hours_before_now(50), "stale_50h"),    # outside (> 48h)
        _row(_hours_before_now(200), "ancient"),     # outside
    ]
    kept = {r["id"] for r in articles_within_window(rows, CUTOFF)}
    assert kept == {"fresh_1h", "fresh_40h"}


def test_boundary_row_exactly_at_cutoff_is_kept():
    # >= cutoff, so an article published exactly at the window edge is inside.
    assert articles_within_window([_row(CUTOFF.isoformat())], CUTOFF)


def test_one_minute_past_the_cutoff_is_dropped():
    just_outside = (CUTOFF - timedelta(minutes=1)).isoformat()
    assert articles_within_window([_row(just_outside)], CUTOFF) == []


def test_drops_rows_with_missing_or_empty_or_unparseable_dates():
    rows = [
        _row(None, "null"),
        _row("", "empty"),
        _row("not-a-date", "garbage"),
        _row(_hours_before_now(1), "good"),
    ]
    kept = {r["id"] for r in articles_within_window(rows, CUTOFF)}
    # An undated article cannot be asserted to fall inside the window, so it is
    # dropped rather than kept — keeping it would re-introduce the over-claim.
    assert kept == {"good"}


def test_accepts_fmp_space_form_and_z_suffix_and_naive_as_utc():
    rows = [
        _row("2026-07-21 17:30:00", "space"),        # FMP space form (UTC)
        _row("2026-07-21T17:30:00Z", "zsuffix"),     # trailing Z
        _row("2026-07-21T17:30:00", "naive"),        # naive → treated as UTC
    ]
    kept = {r["id"] for r in articles_within_window(rows, CUTOFF)}
    assert kept == {"space", "zsuffix", "naive"}


def test_naive_timestamp_is_read_as_utc_not_local():
    # CUTOFF is 2026-07-19 18:00 UTC. "2026-07-19 17:30" naive is 30 min BEFORE
    # the cutoff when read as UTC (correct → dropped). Read as US-eastern (-4 in
    # July) it would be 21:30 UTC, INSIDE the window (wrong → kept). So this pins
    # that the parser treats a naive stamp as UTC, not device/host-local.
    assert articles_within_window([_row("2026-07-19 17:30:00")], CUTOFF) == []
    # ...and one hour later (inside the window under either reading of the date,
    # but unambiguously inside as UTC) is kept.
    assert articles_within_window([_row("2026-07-19 18:30:00")], CUTOFF)


def test_non_dict_rows_are_skipped_not_crashed():
    rows = [None, "junk", 42, _row(_hours_before_now(1), "good")]
    kept = [r["id"] for r in articles_within_window(rows, CUTOFF)]
    assert kept == ["good"]


def test_empty_input_returns_empty():
    assert articles_within_window([], CUTOFF) == []


# ── select_recent_corpus(): 24h preferred, 48h fallback ───────────────────

def test_select_recent_prefers_24h_and_excludes_older_when_fresh_news_exists():
    rows = [
        _row(_hours_before_now(2), "fresh_2h"),     # inside 24h
        _row(_hours_before_now(30), "old_30h"),     # 24–48h → excluded when 24h has news
    ]
    kept, hours = select_recent_corpus(rows, NOW)
    assert hours == PRIMARY_WINDOW_HOURS            # 24 → badge "24h"
    assert {r["id"] for r in kept} == {"fresh_2h"}


def test_select_recent_falls_back_to_48h_when_no_24h_news():
    rows = [
        _row(_hours_before_now(30), "old_30h"),     # only 24–48h news
        _row(_hours_before_now(60), "ancient_60h"),
    ]
    kept, hours = select_recent_corpus(rows, NOW)
    assert hours == CORPUS_WINDOW_HOURS             # 48 → badge "48h"
    assert {r["id"] for r in kept} == {"old_30h"}


def test_select_recent_is_empty_when_nothing_within_48h():
    rows = [_row(_hours_before_now(60), "a"), _row(_hours_before_now(200), "b")]
    kept, hours = select_recent_corpus(rows, NOW)
    assert kept == []                               # → endpoint shows NO card
    assert hours == CORPUS_WINDOW_HOURS


def test_select_recent_boundary_at_24h_counts_as_fresh():
    # An article exactly 24h old is inside the 24h window (>= cutoff).
    rows = [_row(_hours_before_now(24), "edge_24h"), _row(_hours_before_now(40), "old")]
    kept, hours = select_recent_corpus(rows, NOW)
    assert hours == PRIMARY_WINDOW_HOURS
    assert {r["id"] for r in kept} == {"edge_24h"}
