"""How the Insights card chooses and labels the sources it cites.

Two behaviours, one helper (`_corpus_sources`), both from TestFlight feedback on
the Market card: two rows attributed to "youtube.com", one of which was an
off-topic consumer feature.

1. PUBLISHER. The corpus row carries the outlet name in `source_name`; the card
   must pass it through so the client stops falling back to the URL host. News
   feeds link broadcast segments to their video upload, so the host reads
   "youtube.com" when the source is really CNBC Television or Bloomberg.

2. RANKING, NOT FILTERING. The corpus holds ~25 rows and only `_MAX_SOURCES` (8)
   are cited, so a pure recency slice spent those slots on whatever was newest.
   Material headlines sort first. Deliberately a re-ORDER: a lifestyle/off-topic
   regex DROP was measured over 800 live `news/general-latest` headlines and
   caught one true positive against three real market stories, so the drop
   version is net-negative and must not come back.

Pure transforms — no network, no DB.
"""

from app.services.news_insight_service import (
    _MAX_SOURCES,
    _corpus_sources,
    _sanitize_sources,
)


def _row(headline, url="", source_name=None):
    row = {"headline": headline, "article_url": url}
    if source_name is not None:
        row["source_name"] = source_name
    return row


# ───────────────────────────── publisher plumbing ─────────────────────────────

def test_publisher_comes_from_source_name():
    out = _corpus_sources([_row("Fed holds rates", "https://x/1", "CNBC Television")])
    assert out == [{
        "title": "Fed holds rates", "url": "https://x/1", "publisher": "CNBC Television",
    }]


def test_publisher_key_is_omitted_not_blank_when_unknown():
    """An absent key lets the client fall back to the host. An empty string is a
    value, and would render a blank subtitle instead."""
    for row in (_row("Fed holds rates", "https://x/1"),
                _row("Fed holds rates", "https://x/1", ""),
                _row("Fed holds rates", "https://x/1", "   ")):
        assert "publisher" not in _corpus_sources([row])[0]


def test_a_non_str_source_name_is_ignored_not_stringified():
    """A malformed cache row must not render "{'a': 1}" under a headline.
    `_sanitize_sources` rejects non-str, but it runs after this and would only
    ever see the coerced string — so the type check has to be here."""
    for bad in (123, {"a": 1}, ["x"], True, object()):
        row = _corpus_sources([_row("Fed holds rates", "https://x/1", bad)])[0]
        assert "publisher" not in row, f"{bad!r} leaked into the subtitle: {row}"


def test_publisher_survives_the_sanitize_round_trip():
    """`_sanitize_sources` runs on write AND on read-back, so it is the choke
    point: if it dropped the key the field would never reach a client."""
    out = _corpus_sources([_row("Fed holds rates", "https://x/1", "WSJ")])
    assert _sanitize_sources(out) == out
    assert _sanitize_sources(_sanitize_sources(out)) == out


def test_legacy_stored_rows_without_publisher_still_pass_through():
    """Cards stored before the field existed live up to their 96h hard TTL."""
    legacy = [{"title": "Old story", "url": "https://x/1"}]
    assert _sanitize_sources(legacy) == legacy


def test_publisher_is_clipped_and_non_str_is_ignored():
    long_name = "N" * 400
    got = _sanitize_sources([{"title": "t", "url": "", "publisher": long_name}])
    assert 0 < len(got[0]["publisher"]) <= 80
    assert "publisher" not in _sanitize_sources(
        [{"title": "t", "url": "", "publisher": {"nested": 1}}]
    )[0]
    assert "publisher" not in _sanitize_sources(
        [{"title": "t", "url": "", "publisher": None}]
    )[0]


# ──────────────────────────────── the ranking ────────────────────────────────

def test_material_headlines_are_cited_before_the_merely_recent():
    rows = [
        _row("A 158-year-old lawn company says it is a lifestyle brand now", "https://x/1"),
        _row("Markets brace for a possible rate hike", "https://x/2"),
    ]
    assert [s["title"] for s in _corpus_sources(rows)] == [
        "Markets brace for a possible rate hike",
        "A 158-year-old lawn company says it is a lifestyle brand now",
    ]


def test_ranking_is_stable_within_each_group():
    """Equal-materiality rows keep their newest-first corpus order — the ranking
    is a tiebreak on relevance, not a reshuffle."""
    rows = [_row(f"Fed decision take {i}", f"https://m/{i}") for i in range(4)]
    rows += [_row(f"Company profile {i}", f"https://o/{i}") for i in range(4)]
    got = [s["url"] for s in _corpus_sources(rows)]
    assert got == [f"https://m/{i}" for i in range(4)] + [f"https://o/{i}" for i in range(4)]


def test_ranking_drops_nothing():
    """The whole point of ranking over filtering: a corpus of entirely
    non-material rows still fills the list rather than emptying it."""
    rows = [_row(f"Quiet feature story {i}", f"https://x/{i}") for i in range(5)]
    out = _corpus_sources(rows)
    assert len(out) == 5
    assert {s["title"] for s in out} == {r["headline"] for r in rows}


def test_a_quiet_day_with_too_few_material_rows_still_fills_the_cap():
    rows = [_row("Fed holds rates", "https://m/0")]
    rows += [_row(f"Feature {i}", f"https://o/{i}") for i in range(_MAX_SOURCES + 4)]
    out = _corpus_sources(rows)
    assert len(out) == _MAX_SOURCES
    assert out[0]["title"] == "Fed holds rates"


def test_ranking_happens_before_the_cap_not_after():
    """A material story sitting past position 8 by recency must still be cited —
    ranking a list already truncated by recency would change nothing."""
    rows = [_row(f"Feature {i}", f"https://o/{i}") for i in range(_MAX_SOURCES + 2)]
    rows.append(_row("Fed announces a rate cut", "https://m/late"))
    out = _corpus_sources(rows)
    assert len(out) == _MAX_SOURCES
    assert out[0]["url"] == "https://m/late"


def test_dedup_still_wins_over_ranking():
    rows = [
        _row("Fed holds rates", "https://x/1", "WSJ"),
        _row("Fed holds rates again", "https://x/1", "Reuters"),  # same url
    ]
    out = _corpus_sources(rows)
    assert len(out) == 1 and out[0]["publisher"] == "WSJ"


def test_malformed_rows_do_not_break_ranking():
    rows = [None, 42, {}, _row("", "https://x/0"), _row("Fed holds rates", "https://x/1")]
    assert [s["title"] for s in _corpus_sources(rows)] == ["Fed holds rates"]
