"""Topic-match alerts: the matcher and the copy (Phase 6).

Pure tests — the matcher takes the signals payload and a sector map as arguments, so it
needs no network, no Supabase and no LLM. That testability is the point of the design:
a generated match could not be pinned like this.

The assertions that matter most:
  * the shared `signals_v3` object is never mutated (it is served to every user);
  * a style topic like "dividends" NEVER produces a match from sector data alone;
  * the copy stays informational — no suitability phrasing, checked against the very
    patterns `chat_guardrails` uses on the chat surface.
"""

import copy

import pytest

from app.services.agents.chat_guardrails import scan_answer
from app.services.notification_senders.profile_match_sender import _body, _title
from app.services.profile_match import (
    SIGNAL_FAMILY_TO_FOLLOW,
    Match,
    match_profile,
    sectors_from_rows,
    topic_for_sector,
    topics_for_signal,
)

SECTORS = {"XOM": "Energy", "NVDA": "Technology", "AAPL": "Technology", "PFE": "Healthcare"}


def _signals():
    return {
        "congress": {"as_of_date": "2026-08-10", "entries": [
            {"rank": 1, "symbol": "XOM", "name": "Exxon", "value": 4},
            {"rank": 2, "symbol": "AAPL", "name": "Apple", "value": 3},
        ]},
        "whale": {"as_of_date": "2026-08-01", "entries": [
            {"rank": 1, "symbol": "NVDA", "name": "NVIDIA", "value": 7},
        ]},
        "earnings": {"as_of_date": "2026-08-12", "entries": [
            {"rank": 1, "symbol": "PFE", "name": "Pfizer", "value": -12.5},
        ]},
    }


# ── sector → topic ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("sector,expected", [
    ("Energy", "energy"), ("energy", "energy"), ("  ENERGY  ", "energy"),
    ("Technology", "technology"), ("Communication Services", "technology"),
    ("Healthcare", "healthcare"), ("Health Care", "healthcare"),
    ("Utilities", "energy"),
])
def test_known_sectors_map(sector, expected):
    assert topic_for_sector(sector) == expected


@pytest.mark.parametrize("sector", [None, "", "   ", "Financials", "Widgets", 5, [], {}])
def test_unknown_or_junk_sector_yields_no_topic(sector):
    """A WRONG topic is worse than none — it would put a ticker in a category the
    reader never asked about, in a push notification."""
    assert topic_for_sector(sector) is None


def test_topics_for_signal_handles_a_missing_ticker():
    assert topics_for_signal("ZZZZ", SECTORS) == set()
    assert topics_for_signal("", SECTORS) == set()
    assert topics_for_signal(None, SECTORS) == set()


# ── matching ────────────────────────────────────────────────────────────────

def test_no_stated_interests_never_matches():
    assert match_profile({"topics": [], "follow_signals": []}, _signals(), SECTORS) == []


@pytest.mark.parametrize("bad", [None, "x", 5, [], object()])
def test_a_malformed_profile_never_matches(bad):
    assert match_profile(bad, _signals(), SECTORS) == []


def test_following_a_family_matches_its_rows():
    out = match_profile({"follow_signals": ["congress"], "topics": []}, _signals(), SECTORS)
    assert [m.symbol for m in out] == ["XOM", "AAPL"]
    assert all(m.family == "congress" for m in out)
    assert all(m.topic is None for m in out), "family match should not claim a topic"


def test_a_sector_topic_matches_across_families():
    out = match_profile({"follow_signals": [], "topics": ["technology"]}, _signals(), SECTORS)
    assert {m.symbol for m in out} == {"AAPL", "NVDA"}
    assert all(m.topic == "technology" for m in out)


def test_matching_on_both_reasons_outranks_matching_on_one():
    out = match_profile(
        {"follow_signals": ["congress"], "topics": ["technology"]}, _signals(), SECTORS,
    )
    assert out[0].symbol == "AAPL", "congress + technology should lead"
    assert out[0].topic == "technology"


@pytest.mark.parametrize("topic", ["dividends", "value", "growth", "crypto", "macro", "small_cap", "etf_index"])
def test_style_topics_never_match_from_sector_data(topic):
    """THE honesty guard. "You like dividends" must not become a dividend claim about a
    ticker whose yield we never looked at — sector data cannot support it."""
    assert match_profile({"follow_signals": [], "topics": [topic]}, _signals(), SECTORS) == []


def test_the_shared_signals_payload_is_never_mutated():
    """`signals_v3` is one process-wide object served to every user. The same hazard
    `redact_signals` carries a warning about."""
    signals = _signals()
    before = copy.deepcopy(signals)
    match_profile({"follow_signals": ["congress", "whales"], "topics": ["technology"]},
                  signals, SECTORS)
    assert signals == before


def test_limit_is_respected():
    out = match_profile(
        {"follow_signals": ["congress", "whales", "earnings"], "topics": []},
        _signals(), SECTORS, limit=2,
    )
    assert len(out) == 2


def test_limit_zero_returns_nothing():
    out = match_profile({"follow_signals": ["congress"], "topics": []},
                        _signals(), SECTORS, limit=0)
    assert out == []


def test_ordering_does_not_depend_on_incidental_row_order():
    """The ranking must be a TOTAL order, not merely deterministic.

    Re-running with identical input proves nothing — Python's sort is stable, so even a
    partial key gives the same answer every time. The real hazard is the upstream payload
    reordering equally-ranked rows (a cache rebuild, a changed query), which would flip
    the lead ticker — and the lead drives the notification's deep link and its whole
    first sentence. So this permutes the input instead.
    """
    profile = {"follow_signals": ["congress", "whales", "earnings"], "topics": ["technology"]}
    baseline = [m.symbol for m in match_profile(profile, _signals(), SECTORS)]

    for family in ("congress", "whale", "earnings"):
        shuffled = _signals()
        shuffled[family]["entries"] = list(reversed(shuffled[family]["entries"]))
        assert [m.symbol for m in match_profile(profile, shuffled, SECTORS)] == baseline, (
            f"reordering the {family} rows changed the ranking — the sort key is not a "
            f"total order"
        )


def test_equal_ranked_rows_break_ties_by_symbol_not_by_family_order():
    """The tie-break, tested where it can actually be observed.

    XOM (congress, rank 1) and PFE (earnings, rank 1) match on family alone, so they tie
    on BOTH strength and rank. Without the symbol in the sort key the stable sort would
    simply preserve insertion order — i.e. whichever family the loop happens to visit
    first — making the ranking an artefact of a hardcoded tuple rather than of the data.
    Asserting symbol-ascending pins the documented behaviour and is what fails if the
    tie-break is dropped.
    """
    profile = {"follow_signals": ["congress", "earnings"], "topics": []}
    symbols = [m.symbol for m in match_profile(profile, _signals(), SECTORS, limit=10)]
    assert symbols.index("PFE") < symbols.index("XOM"), (
        f"expected symbol-ascending on a (strength, rank) tie, got {symbols}"
    )


def test_missing_or_empty_groups_are_skipped():
    signals = {"congress": None, "whale": {"entries": []}}
    assert match_profile({"follow_signals": ["congress", "whales"], "topics": []},
                         signals, SECTORS) == []


def test_malformed_rows_are_skipped_not_crashed():
    signals = {"congress": {"entries": [
        {"rank": 1, "symbol": None, "name": "x", "value": 1},
        {"rank": "bad", "symbol": "  ", "value": None},
        {"symbol": "XOM", "name": None, "value": "not-a-number"},
    ]}}
    out = match_profile({"follow_signals": ["congress"], "topics": []}, signals, SECTORS)
    assert [m.symbol for m in out] == ["XOM"]
    assert out[0].value == 0.0, "unparseable value degrades to 0, not a crash"
    assert out[0].company_name == "XOM", "missing name falls back to the symbol"


def test_sectors_from_rows_reads_both_cache_shapes():
    out = sectors_from_rows([
        {"ticker": "xom", "sector": "Energy"},                      # watchlist_items
        {"ticker": "NVDA", "profile_json": {"sector": "Technology"}},  # company_profile_cache
        {"ticker": "BAD"},                                          # neither → absent
        {"ticker": None, "sector": "Energy"},                       # junk
        "not-a-row",
    ])
    assert out == {"XOM": "Energy", "NVDA": "Technology"}


# ── copy ────────────────────────────────────────────────────────────────────

def _all_copy():
    cases = [
        [Match("congress", "XOM", "Exxon", 4.0, 1, "energy", None)],
        [Match("whale", "NVDA", "NVIDIA", 7.0, 1, None, None)],
        [Match("earnings", "PFE", "Pfizer", -12.5, 1, "healthcare", None),
         Match("whale", "NVDA", "NVIDIA", 7.0, 1, None, None)],
    ]
    return [f"{_title(c[0])} {_body(c)}" for c in cases]


def test_copy_is_never_suitability_flavoured():
    """Checked against the SAME patterns the chat surface is judged by. §11.7 requires
    push copy to be informational, never directive — and a notification has no room for
    the qualification that would make a suitability claim accurate."""
    for text in _all_copy():
        assert "suitability_claim" not in scan_answer(text), text
        assert "advice_directive" not in scan_answer(text), text


@pytest.mark.parametrize("banned", [
    "for you", "buy", "sell", "should", "pick", "opportunity", "recommend", "best",
])
def test_copy_avoids_directive_words(banned):
    """WHOLE WORDS, not substrings.

    "institutional buying" describes what institutions did and is exactly the kind of
    factual statement this copy is supposed to make — a substring scan flags it for
    containing "buy", which is the same false-positive class `_boundary_regex` exists to
    stop (`as an ai` matching inside `as an aid`). The banned thing is a directive aimed
    at the READER, not a verb describing a third party.
    """
    import re

    pattern = re.compile(rf"(?<!\w){re.escape(banned)}(?!\w)")
    for text in _all_copy():
        assert not pattern.search(text.lower()), f"{banned!r} in {text!r}"


def test_copy_names_the_ticker_and_the_reason():
    text = f"{_title(Match('congress', 'XOM', 'Exxon', 4.0, 1, 'energy', None))} " \
           f"{_body([Match('congress', 'XOM', 'Exxon', 4.0, 1, 'energy', None)])}"
    assert "XOM" in text and "Energy" in text and "congressional filings" in text


def test_copy_mentions_additional_symbols_when_present():
    body = _body([
        Match("congress", "XOM", "Exxon", 4.0, 1, "energy", None),
        Match("whale", "NVDA", "NVIDIA", 7.0, 1, None, None),
    ])
    assert "NVDA" in body


def test_a_ticker_matching_two_families_appears_only_once():
    """Found by running the sender against live data: GOOGL matched Congress AND whales,
    and the notification read "GOOGL is seeing congressional filings … Also active: TSM,
    GOOGL" — the same ticker twice in one sentence. `limit` must mean N DISTINCT tickers.
    """
    signals = {
        "congress": {"entries": [{"rank": 1, "symbol": "GOOGL", "name": "Alphabet", "value": 3}]},
        "whale": {"entries": [
            {"rank": 1, "symbol": "GOOGL", "name": "Alphabet", "value": 9},
            {"rank": 2, "symbol": "TSM", "name": "TSMC", "value": 5},
        ]},
    }
    sectors = {"GOOGL": "Communication Services", "TSM": "Technology"}
    out = match_profile(
        {"follow_signals": ["congress", "whales"], "topics": ["technology"]},
        signals, sectors, limit=3,
    )
    symbols = [m.symbol for m in out]
    assert len(symbols) == len(set(symbols)), f"duplicate ticker in {symbols}"
    assert set(symbols) == {"GOOGL", "TSM"}


def test_dedupe_keeps_the_strongest_reason():
    """The survivor must be the higher-ranked match, not whichever family was visited
    first — the kept row drives both the deep link and the first sentence."""
    signals = {
        # congress: family-follow only (strength 1). whale: family + topic (strength 2).
        "congress": {"entries": [{"rank": 1, "symbol": "NVDA", "name": "NVIDIA", "value": 2}]},
        "whale": {"entries": [{"rank": 1, "symbol": "NVDA", "name": "NVIDIA", "value": 8}]},
    }
    out = match_profile(
        {"follow_signals": ["whales"], "topics": ["technology"]},
        signals, {"NVDA": "Technology"}, limit=3,
    )
    assert len(out) == 1
    assert out[0].family == "whale" and out[0].topic == "technology"


def test_limit_counts_distinct_tickers_not_rows():
    signals = {
        "congress": {"entries": [{"rank": 1, "symbol": "AAPL", "name": "Apple", "value": 1}]},
        "whale": {"entries": [{"rank": 1, "symbol": "AAPL", "name": "Apple", "value": 1}]},
        "earnings": {"entries": [{"rank": 1, "symbol": "PFE", "name": "Pfizer", "value": -5}]},
    }
    out = match_profile(
        {"follow_signals": ["congress", "whales", "earnings"], "topics": []},
        signals, SECTORS, limit=2,
    )
    assert [m.symbol for m in out] == ["AAPL", "PFE"]


# ── Consent is a gate on the PUSH path too ───────────────────────────────────
#
# `_load_profiles`' docstring said "readers who opted in" while the query selected only
# `user_id, topics, follow_signals` — consent was never fetched. A profile captured during
# first-run onboarding (which never grants consent: OnboardingViewModel leaves
# `accepted_personalization_terms` nil) therefore drove a personalized push saying
# "…a topic you follow" to somebody who had not accepted the personalization terms.
#
# Every other consumer of this table already refuses such a row. Push is the surface where
# getting it wrong is least recoverable: the notification has already been delivered.

from app.services.notification_senders import profile_match_sender as pms


class _FakeTable:
    def __init__(self, rows, sink):
        self._rows, self._sink = rows, sink

    def select(self, cols):
        self._sink["columns"] = cols
        return self

    def limit(self, n):
        return self

    def order(self, *a, **kw):
        self._sink["ordered"] = True
        return self

    @property
    def not_(self):
        return self

    def is_(self, col, val):
        self._sink.setdefault("sql_filters", []).append((col, val))
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


def _install_profile_rows(monkeypatch, rows):
    sink: dict = {}
    monkeypatch.setattr(
        pms, "get_supabase", lambda: type("S", (), {"table": lambda _s, _n: _FakeTable(rows, sink)})()
    )
    return sink


_CONSENT = "2026-08-13T00:00:00+00:00"


def test_an_unconsented_profile_is_never_loaded(monkeypatch):
    """THE regression: preferences stated, terms never accepted."""
    _install_profile_rows(monkeypatch, [
        {"user_id": "u1", "topics": ["technology"], "follow_signals": [], "consented_at": None},
    ])
    assert pms._load_profiles() == [], "loaded a reader who never accepted personalization"


def test_a_consented_profile_is_loaded(monkeypatch):
    _install_profile_rows(monkeypatch, [
        {"user_id": "u1", "topics": ["technology"], "follow_signals": [], "consented_at": _CONSENT},
    ])
    assert [r["user_id"] for r in pms._load_profiles()] == ["u1"]


def test_consent_is_filtered_in_sql_not_only_in_python(monkeypatch):
    """The row cap and the tier read below must be spent on candidates that can be sent to."""
    sink = _install_profile_rows(monkeypatch, [])
    pms._load_profiles()
    assert "consented_at" in sink.get("columns", ""), "consent column is not even selected"
    assert ("consented_at", "null") in sink.get("sql_filters", []), "no SQL consent filter"


def test_the_scan_is_ordered_so_the_cap_does_not_starve_the_same_readers(monkeypatch):
    """An unordered LIMIT returns an arbitrary but STABLE subset, so once the table exceeds
    the cap the same readers were excluded on every daily run, permanently — while the log
    implied a transient miss."""
    sink = _install_profile_rows(monkeypatch, [])
    pms._load_profiles()
    assert sink.get("ordered"), "the profile scan has no ORDER BY"


def test_a_consented_but_silent_reader_is_still_excluded(monkeypatch):
    """Consent alone is not something to match on."""
    _install_profile_rows(monkeypatch, [
        {"user_id": "u1", "topics": [], "follow_signals": [], "consented_at": _CONSENT},
    ])
    assert pms._load_profiles() == []


def test_the_follow_vocabulary_matches_the_profile_column():
    """`SIGNAL_FAMILY_TO_FOLLOW` maps a signal FAMILY to a `follow_signals` VALUE, and the
    two vocabularies are different words for the same idea ("whale" → "whales").

    Several tests in this file used to pass the family name as a follow preference, so the
    whale family was never actually followed and those cases matched only via the topic arm
    — green, but testing something other than what they claimed. A value that is not in the
    column's vocabulary can never match, and nothing else would notice.
    """
    from app.services.user_investor_profile_service import ARRAY_FIELDS

    vocab = set(ARRAY_FIELDS["follow_signals"])
    unknown = {f: v for f, v in SIGNAL_FAMILY_TO_FOLLOW.items() if v not in vocab}
    assert not unknown, (
        f"{unknown} map to follow_signals values that do not exist in the column vocabulary "
        f"{sorted(vocab)} — those families can never be followed."
    )


def test_no_test_in_this_file_uses_an_invalid_follow_signal():
    """Anti-recurrence: the bug above was in the TESTS, so guard the tests."""
    import ast
    from pathlib import Path

    from app.services.user_investor_profile_service import ARRAY_FIELDS

    vocab = set(ARRAY_FIELDS["follow_signals"])
    src = Path(__file__).read_text(encoding="utf-8")
    bad = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if not (isinstance(k, ast.Constant) and k.value == "follow_signals"):
                continue
            if not isinstance(v, (ast.List, ast.Tuple)):
                continue
            for item in v.elts:
                if isinstance(item, ast.Constant) and item.value not in vocab:
                    bad.append((item.value, item.lineno))
    assert not bad, f"invalid follow_signals values in this file: {bad} (vocab {sorted(vocab)})"
