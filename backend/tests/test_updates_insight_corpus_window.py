"""The corpus time-window that makes the Insights badge honest.

The window is DYNAMIC: the last 24h (``PRIMARY_WINDOW_HOURS``) when it holds at
least ``MIN_CORPUS_ARTICLES`` stories about the scope; otherwise it widens to 48h
(``CORPUS_WINDOW_HOURS``) — and, only across a closed market, to 72/96h — but each
step is taken only when it ADDS an article, so a lone fresh story keeps the
narrow, literally-true "24h". The sweeper bounds each scope's corpus to that
window before BOTH the materiality fingerprint and generation, and the Updates
endpoint uses the SAME selector to decide whether to show a card at all AND which
badge to render. This pins the boundary behaviour of the shared pure filter (what
is kept/dropped, how malformed timestamps degrade) and the tier selection.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.news_insight_service import (
    CORPUS_WINDOW_HOURS,
    MIN_CORPUS_ARTICLES,
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


# ── select_recent_corpus(): 24h when well covered, else widen ─────────────

def _fresh(n, prefix="fresh"):
    """``n`` distinct rows inside the 24h window (1h, 2h, ... apart)."""
    return [_row(_hours_before_now(1 + i), f"{prefix}_{i}") for i in range(n)]


def test_select_recent_prefers_24h_and_excludes_older_when_fresh_news_exists():
    rows = _fresh(MIN_CORPUS_ARTICLES) + [
        _row(_hours_before_now(30), "old_30h"),     # 24–48h → excluded: 24h is well covered
    ]
    kept, hours = select_recent_corpus(rows, NOW)
    assert hours == PRIMARY_WINDOW_HOURS            # 24 → badge "24h"
    assert {r["id"] for r in kept} == {r["id"] for r in _fresh(MIN_CORPUS_ARTICLES)}


def test_a_thin_24h_corpus_widens_to_48h_and_takes_both_rows():
    """The TestFlight case (PLUG): one story today, one yesterday morning."""
    rows = [
        _row(_hours_before_now(2), "fresh_2h"),
        _row(_hours_before_now(30), "old_30h"),
    ]
    kept, hours = select_recent_corpus(rows, NOW)
    assert hours == CORPUS_WINDOW_HOURS
    assert {r["id"] for r in kept} == {"fresh_2h", "old_30h"}


def test_exactly_the_minimum_keeps_24h_and_one_fewer_widens():
    old = _row(_hours_before_now(30), "old_30h")
    kept, hours = select_recent_corpus(_fresh(MIN_CORPUS_ARTICLES) + [old], NOW)
    assert hours == PRIMARY_WINDOW_HOURS and len(kept) == MIN_CORPUS_ARTICLES
    kept, hours = select_recent_corpus(_fresh(MIN_CORPUS_ARTICLES - 1) + [old], NOW)
    assert hours == CORPUS_WINDOW_HOURS and len(kept) == MIN_CORPUS_ARTICLES


@pytest.mark.parametrize("n", [1, MIN_CORPUS_ARTICLES - 1])
def test_widening_that_adds_nothing_keeps_the_narrow_badge(n):
    """A thin day with NOTHING older is still a 24h scope — the badge must not
    claim a lookback that bought no article."""
    kept, hours = select_recent_corpus(_fresh(n), NOW)
    assert hours == PRIMARY_WINDOW_HOURS
    assert len(kept) == n


def test_a_thin_48h_corpus_is_still_served_at_48h():
    """Below the minimum at 48h too, midweek: serve what there is, badged 48h."""
    kept, hours = select_recent_corpus([_row(_hours_before_now(30), "old_30h")], NOW)
    assert hours == CORPUS_WINDOW_HOURS
    assert [r["id"] for r in kept] == ["old_30h"]


def test_widening_to_48h_that_adds_but_stays_thin_still_reports_48h():
    rows = [_row(_hours_before_now(2), "fresh"), _row(_hours_before_now(40), "old")]
    assert MIN_CORPUS_ARTICLES > 2, "fixture assumes two rows are still thin"
    kept, hours = select_recent_corpus(rows, NOW)
    assert hours == CORPUS_WINDOW_HOURS and len(kept) == 2


def test_the_threshold_is_applied_after_the_subject_filter():
    """Five fresh rows of which only two are about PLUG is a THIN 24h window."""
    def _tagged(h, ident, tags, title):
        r = _row(_hours_before_now(h), ident)
        r["related_tickers"] = tags
        r["headline"] = title
        return r
    rows = [
        _tagged(1, "plug_a", ["PLUG"], "Plug Power lands a 5MW order"),
        _tagged(2, "plug_b", ["PLUG"], "Plug Power stock forms a risky pattern"),
        _tagged(3, "peer_1", ["FCEL", "BE", "PLUG"], "FuelCell Energy sinks 8%, Bloom falls, Plug drops"),
        _tagged(4, "peer_2", ["BE", "PLUG"], "Bloom Energy wins a data-center deal"),
        _tagged(5, "peer_3", ["FCEL"], "FuelCell Energy reports a wider loss"),
        _tagged(30, "plug_c", ["PLUG"], "Plug Power hydrogen plant reaches nameplate"),
    ]
    kept, hours = select_recent_corpus(rows, NOW, scope="PLUG", company_name="Plug Power Inc.")
    assert hours == CORPUS_WINDOW_HOURS
    assert {r["id"] for r in kept} == {"plug_a", "plug_b", "plug_c"}


def test_future_rows_never_count_toward_the_threshold():
    rows = _fresh(MIN_CORPUS_ARTICLES - 1) + [
        _row((NOW + timedelta(hours=5)).isoformat(), "future"),
    ]
    kept, hours = select_recent_corpus(rows, NOW)
    assert hours == PRIMARY_WINDOW_HOURS
    assert "future" not in {r["id"] for r in kept}
    assert len(kept) == MIN_CORPUS_ARTICLES - 1


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
    # An article exactly 24h old is inside the 24h window (>= cutoff), so it
    # counts toward the threshold ...
    rows = (
        _fresh(MIN_CORPUS_ARTICLES - 1)
        + [_row(_hours_before_now(24), "edge_24h"), _row(_hours_before_now(40), "old")]
    )
    kept, hours = select_recent_corpus(rows, NOW)
    assert hours == PRIMARY_WINDOW_HOURS
    assert "edge_24h" in {r["id"] for r in kept} and "old" not in {r["id"] for r in kept}
    # ... and alone with an older row it is a THIN 24h window that widens.
    kept, hours = select_recent_corpus(rows[-2:], NOW)
    assert hours == CORPUS_WINDOW_HOURS
    assert {r["id"] for r in kept} == {"edge_24h", "old"}


# ── Future-dated rows must not fake a fresh card (upper bound) ─────────────

def test_future_dated_row_beyond_skew_does_not_fake_a_24h_card():
    # A parseable but FUTURE published_at (embargoed PR / FMP TZ glitch) would
    # otherwise satisfy `ts >= cutoff` and return ([future], "24h") for a scope
    # whose only real news is >48h old. With the upper bound it's excluded, so the
    # result is empty → the endpoint shows NO card (honest).
    rows = [
        _row((NOW + timedelta(hours=5)).isoformat(), "future"),
        _row(_hours_before_now(60), "ancient"),
    ]
    kept, hours = select_recent_corpus(rows, NOW)
    assert kept == []
    assert hours == CORPUS_WINDOW_HOURS


def test_future_row_does_not_flip_a_48h_scope_to_a_24h_badge():
    # Real news at 30h (→ "48h" window) plus a stray future row. Without the upper
    # bound the future row would pull the result into the 24h window and mis-badge
    # a 30h-old card as "24h". With it, the badge stays honest.
    rows = [
        _row(_hours_before_now(30), "real_30h"),
        _row((NOW + timedelta(hours=6)).isoformat(), "future"),
    ]
    kept, hours = select_recent_corpus(rows, NOW)
    assert {r["id"] for r in kept} == {"real_30h"}
    assert hours == CORPUS_WINDOW_HOURS


def test_just_published_within_skew_is_kept():
    # A tiny clock skew / same-minute stamp just past `now` is tolerated so a
    # legitimately just-published article isn't dropped as "future".
    rows = [_row((NOW + timedelta(minutes=30)).isoformat(), "just_now")]
    kept, hours = select_recent_corpus(rows, NOW)
    assert {r["id"] for r in kept} == {"just_now"}
    assert hours == PRIMARY_WINDOW_HOURS


def test_articles_within_window_upper_bound_drops_future_rows_directly():
    upper = NOW + timedelta(hours=2)
    rows = [
        _row(_hours_before_now(1), "recent"),
        _row((NOW + timedelta(hours=5)).isoformat(), "future"),
    ]
    kept = {r["id"] for r in articles_within_window(rows, CUTOFF, upper)}
    assert kept == {"recent"}
    # With no upper bound (legacy call), the future row is kept — the default is
    # backward-compatible; the future filter only applies via select_recent_corpus.
    kept_no_upper = {r["id"] for r in articles_within_window(rows, CUTOFF)}
    assert kept_no_upper == {"recent", "future"}


# ── the third tier: stretch ONLY across a market that was actually shut ───────
#
# Two tiers meant the card VANISHED on a Monday morning: a ticker whose last
# story was Friday has an empty 24h AND 48h window, the endpoint's `if
# feed_recent:` gate then renders no card at all, and it does so even though a
# perfectly good card is sitting unexpired in the cache (the 96h hard TTL exists
# for exactly this weekend, and the gate overrode it).
#
# The rule anchors on the last completed SESSION CLOSE, not on counting weekend
# days. The intuitive weekend-counting version fires on an ordinary Tuesday,
# because 48h back from a Tuesday afternoon lands on a Sunday — it would stretch
# the window on days the market never closed, which is how a merely quiet ticker
# starts presenting 4-day-old news as current.

from zoneinfo import ZoneInfo  # noqa: E402

from app.services.news_insight_service import (  # noqa: E402
    MAX_WINDOW_HOURS,
    _closed_market_window_hours,
)

_ET = ZoneInfo("America/New_York")


def _at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=_ET)


@pytest.mark.parametrize(
    "label,now,expected",
    [
        # No stretch — the tape closed since the cutoff, so an empty window means
        # the ticker is genuinely quiet and the honest answer is still "no card".
        ("Wed 10:00, normal week", _at(2026, 8, 26, 10), CORPUS_WINDOW_HOURS),
        ("Tue 14:00, normal week", _at(2026, 7, 21, 14), CORPUS_WINDOW_HOURS),
        ("Sat 10:00 (Fri close is 18h back)", _at(2026, 8, 22, 10), CORPUS_WINDOW_HOURS),
        ("Sun 10:00 (Fri close is 42h back)", _at(2026, 8, 23, 10), CORPUS_WINDOW_HOURS),
        # Stretch — no session has finished since the 48h cutoff.
        ("Sun 20:00", _at(2026, 8, 23, 20), 72),
        ("Mon 10:00", _at(2026, 8, 24, 10), 72),
        ("Mon 08:00 premarket", _at(2026, 8, 24, 8), 72),
        ("Tue 10:00 after Labor Day", _at(2026, 9, 8, 10), 96),
    ],
)
def test_the_window_stretches_only_across_a_closed_market(label, now, expected):
    assert _closed_market_window_hours(now) == expected, label


def test_the_stretch_is_capped():
    """A malformed calendar must not walk the window back indefinitely."""
    # Christmas 2026 falls on a Friday; the following Monday is the longest real
    # gap this calendar produces. Assert the cap holds for a whole week of them.
    for day in range(26, 32):
        got = _closed_market_window_hours(_at(2026, 12, day, 10))
        assert CORPUS_WINDOW_HOURS <= got <= MAX_WINDOW_HOURS, day


def test_a_naive_now_is_read_as_utc_not_local():
    """The suite runs under TZ=UTC and TZ=America/Denver; both must agree."""
    naive = datetime(2026, 8, 24, 14, 0)
    aware = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)
    assert _closed_market_window_hours(naive) == _closed_market_window_hours(aware)


def test_monday_recovers_a_card_that_two_tiers_would_have_hidden():
    """The user-visible point of the whole change."""
    now = _at(2026, 8, 24, 10)                      # Monday morning
    friday = _at(2026, 8, 21, 15)                   # 67h back — outside 48h
    rows = [_row(friday.isoformat())]

    kept, hours = select_recent_corpus(rows, now)
    assert kept, "the Monday card is still hidden — the third tier is not being reached"
    assert hours == 72, "the badge must state the window it actually used"


def test_a_quiet_ticker_in_a_full_trading_week_is_still_hidden():
    """The other half. Stretching here would age-launder 3-day-old news."""
    now = _at(2026, 8, 26, 10)                      # Wednesday
    sunday = _at(2026, 8, 23, 15)                   # 67h back, but Mon+Tue traded
    kept, hours = select_recent_corpus([_row(sunday.isoformat())], now)
    assert kept == []
    assert hours == CORPUS_WINDOW_HOURS


def test_fresh_news_is_untouched_by_the_third_tier():
    now = _at(2026, 8, 24, 10)                      # a Monday, where the tier IS armed
    kept, hours = select_recent_corpus([_row((now - timedelta(hours=3)).isoformat())], now)
    assert len(kept) == 1
    assert hours == PRIMARY_WINDOW_HOURS, (
        "a lone fresh story is thin, but the stretch added nothing — the badge stays 24h"
    )


def test_a_thin_corpus_stretches_across_a_closed_market_only_when_it_adds_an_article():
    monday = _at(2026, 8, 24, 10)
    fresh = _row((monday - timedelta(hours=3)).isoformat(), "fresh")
    friday = _row(_at(2026, 8, 21, 15).isoformat(), "friday")   # 67h back
    # Thin at 24h and 48h, the tape was shut, and 72h turns up Friday's story.
    kept, hours = select_recent_corpus([fresh, friday], monday)
    assert hours == 72
    assert {r["id"] for r in kept} == {"fresh", "friday"}
    # Same rows on a Wednesday: Mon+Tue traded, so the stretch is not armed and
    # the 3-day-old story must not be age-laundered in.
    wednesday = _at(2026, 8, 26, 10)
    fresh_w = _row((wednesday - timedelta(hours=3)).isoformat(), "fresh")
    sunday = _row(_at(2026, 8, 23, 15).isoformat(), "sunday")   # 67h back
    kept, hours = select_recent_corpus([fresh_w, sunday], wednesday)
    assert hours == PRIMARY_WINDOW_HOURS
    assert [r["id"] for r in kept] == ["fresh"]


def test_naive_and_aware_now_agree_on_a_thin_monday():
    """The suite runs under TZ=UTC and TZ=America/Denver; the tier arming must
    not depend on the host clock."""
    aware = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)     # Monday 10:00 ET
    naive = datetime(2026, 8, 24, 14, 0)
    rows = [
        _row((aware - timedelta(hours=3)).isoformat(), "fresh"),
        _row((aware - timedelta(hours=67)).isoformat(), "friday"),
    ]
    assert select_recent_corpus(rows, naive) == select_recent_corpus(rows, aware)
    assert select_recent_corpus(rows, aware)[1] == 72


def test_an_empty_result_reports_the_widest_window_tried():
    """The int is not evidence of freshness — it used to be a flat 48 either way."""
    assert select_recent_corpus([], _at(2026, 8, 26, 10))[1] == CORPUS_WINDOW_HOURS
    assert select_recent_corpus([], _at(2026, 8, 24, 10))[1] == 72


# ── the badge floor from what the card CITES ─────────────────────────────────
#
# The card row stores no window; the endpoint re-derives the badge from the CURRENT
# feed. Since a thin day now widens the corpus, that recompute can under-claim once
# the day fills in: a brief written over 48h (yesterday's story cited) would read
# "24h" the moment three fresh articles landed while the sweeper was capped. The
# card's own `sources` are the floor.

from app.services.news_insight_service import (  # noqa: E402
    MAX_WINDOW_HOURS as _MAX,
    cited_window_floor,
)


def _feed_row(url, hours_ago, ident="r"):
    return {"id": ident, "article_url": url, "published_at": _hours_before_now(hours_ago)}


def _src(url):
    return {"title": "t", "url": url}


def test_a_card_citing_yesterday_floors_the_badge_at_48h():
    rows = [_feed_row("u1", 2), _feed_row("u2", 30), _feed_row("u3", 1), _feed_row("u4", 3)]
    assert cited_window_floor([_src("u1"), _src("u2")], rows, NOW) == CORPUS_WINDOW_HOURS
    # ...and the recompute alone would have said 24h now that three fresh rows exist.
    assert select_recent_corpus(rows, NOW)[1] == PRIMARY_WINDOW_HOURS


def test_a_card_citing_only_fresh_rows_keeps_24h():
    rows = [_feed_row("u1", 2), _feed_row("u2", 30)]
    assert cited_window_floor([_src("u1")], rows, NOW) == PRIMARY_WINDOW_HOURS


@pytest.mark.parametrize("hours,expected", [(23.9, 24), (24, 24), (24.1, 48), (48, 48), (60, 72), (72, 72), (90, 96), (96, 96), (200, 96)])
def test_the_floor_snaps_to_the_badge_vocabulary(hours, expected):
    rows = [_feed_row("u", hours)]
    assert cited_window_floor([_src("u")], rows, NOW) == expected
    assert expected <= _MAX


@pytest.mark.parametrize("sources", [None, [], "u1", 42, [None, "u1", 7], [{"title": "no url"}], [{"url": ""}], [{"url": "   "}]])
def test_unusable_sources_yield_no_floor(sources):
    assert cited_window_floor(sources, [_feed_row("u1", 30)], NOW) is None


def test_uncited_or_undated_rows_yield_no_floor():
    assert cited_window_floor([_src("u9")], [_feed_row("u1", 30)], NOW) is None      # not in the feed
    assert cited_window_floor([_src("u1")], [{"article_url": "u1", "published_at": None}], NOW) is None
    assert cited_window_floor([_src("u1")], [], NOW) is None
    assert cited_window_floor([_src("u1")], [None, "junk", 3], NOW) is None


def test_the_floor_matches_the_legacy_url_key_and_trims_whitespace():
    rows = [{"url": " u1 ", "published_at": _hours_before_now(30)}]
    assert cited_window_floor([{"url": "u1"}], rows, NOW) == CORPUS_WINDOW_HOURS


def test_a_naive_now_is_read_as_utc_by_the_floor():
    rows = [_feed_row("u1", 30)]
    assert cited_window_floor([_src("u1")], rows, NOW.replace(tzinfo=None)) == CORPUS_WINDOW_HOURS


def test_a_future_dated_cited_row_does_not_widen_the_floor():
    rows = [_feed_row("u1", -5), _feed_row("u2", 2)]     # u1 is 5h in the future
    assert cited_window_floor([_src("u1"), _src("u2")], rows, NOW) == PRIMARY_WINDOW_HOURS
