"""
News Insight service — the N-articles → 1-card Gemini roll-up.

The load-bearing property under test is NEGATIVE: **a degraded model response
must never be persisted.** This repo has a documented incident where a
"neutral + empty bullets" fallback was written with ai_processed=True and
poisoned a shared 6-hour cache for every user with no retry path
(news_cache_service._batch_enrich_articles). Every case below asserts that a
bad response produces no card AND no write.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services.news_insight_service import (
    MAX_BULLETS,
    MAX_HEADLINE_CHARS,
    MIN_BULLETS,
    NewsInsightService,
    _clip,
    _iso,
    normalize_card_sentiment,
)


class _StubService(NewsInsightService):
    """NewsInsightService with the Supabase/Gemini clients stubbed out.

    Bypasses __init__ so no network client is constructed — this is a math and
    validation test, not an integration test (see .claude/rules/testing.md).
    """

    def __init__(self):
        self.supabase = None
        self.gemini = None
        self._cache = {}
        self._inflight = {}
        self.writes = []

    def _store(self, scope, card, inputset_id, trigger_reason, article_count, market_active):
        self.writes.append(
            {"scope": scope, "card": card, "articles": article_count}
        )
        return True


@pytest.fixture
def svc():
    return _StubService()


# ── sentiment normalization ───────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("bullish", "Bullish"), ("BULLISH", "Bullish"), (" Bullish ", "Bullish"),
        ("positive", "Bullish"),          # legacy ticker_news_cache spelling
        ("bearish", "Bearish"), ("negative", "Bearish"), ("NEGATIVE", "Bearish"),
        ("neutral", "Neutral"), ("Neutral", "Neutral"),
        # Unknown/absent must ABSTAIN (None), not silently become Neutral —
        # otherwise un-analysed rows outvote the ones that have an opinion.
        (None, None), ("", None), ("mixed", None), ("sideways", None), (42, None),
    ],
)
def test_normalize_card_sentiment(raw, expected):
    assert normalize_card_sentiment(raw) == expected


# ── _validate(): every degraded shape is rejected ─────────────────────

def test_validate_accepts_a_good_card(svc):
    card = svc._validate("AAPL", {
        "headline": "Apple beats on services revenue",
        "bullets": ["Services hit a record.", "Analysts raised targets."],
        "sentiment": "bullish",
    })
    assert card == {
        "headline": "Apple beats on services revenue",
        "bullets": ["Services hit a record.", "Analysts raised targets."],
        "sentiment": "Bullish",
    }


@pytest.mark.parametrize("parsed", [
    None,
    [],
    ["not", "an", "object"],
    "a string",
    42,
    {},
    {"headline": "", "bullets": ["a", "b"], "sentiment": "bullish"},
    {"headline": "   ", "bullets": ["a", "b"], "sentiment": "bullish"},
    {"headline": None, "bullets": ["a", "b"], "sentiment": "bullish"},
    {"headline": "H", "bullets": None, "sentiment": "bullish"},
    {"headline": "H", "bullets": "not a list", "sentiment": "bullish"},
    {"headline": "H", "bullets": [], "sentiment": "bullish"},
    {"headline": "H", "bullets": ["only one"], "sentiment": "bullish"},
    {"headline": "H", "bullets": [1, 2, 3], "sentiment": "bullish"},
    {"headline": "H", "bullets": ["", "  "], "sentiment": "bullish"},
    {"headline": "H", "bullets": ["a", "b"], "sentiment": "sideways"},
    {"headline": "H", "bullets": ["a", "b"], "sentiment": None},
    {"headline": "H", "bullets": ["a", "b"]},
])
def test_validate_rejects_every_degraded_response(svc, parsed):
    assert svc._validate("AAPL", parsed) is None


def test_validate_deduplicates_repeated_bullets(svc):
    # SwiftUI renders bullets with ForEach(id: \.self); duplicates collapse and
    # read as a rendering bug.
    card = svc._validate("AAPL", {
        "headline": "H",
        "bullets": ["same", "same", "different"],
        "sentiment": "neutral",
    })
    assert card["bullets"] == ["same", "different"]


def test_validate_rejects_when_dedup_drops_below_the_minimum(svc):
    assert svc._validate("AAPL", {
        "headline": "H", "bullets": ["same", "same"], "sentiment": "neutral",
    }) is None


def test_validate_caps_bullets_at_the_schema_maximum(svc):
    card = svc._validate("AAPL", {
        "headline": "H",
        "bullets": [f"bullet {i}" for i in range(12)],
        "sentiment": "neutral",
    })
    assert len(card["bullets"]) == MAX_BULLETS


def test_validate_clips_an_overlong_headline_within_the_db_limit(svc):
    card = svc._validate("AAPL", {
        "headline": "word " * 200,
        "bullets": ["a", "b"],
        "sentiment": "neutral",
    })
    # A length overrun is verbosity, not a degraded card — clip, don't discard.
    assert card is not None
    assert len(card["headline"]) <= MAX_HEADLINE_CHARS


def test_validate_collapses_whitespace(svc):
    card = svc._validate("AAPL", {
        "headline": "Apple\n\n  beats   estimates",
        "bullets": ["a  b", "c\nd"],
        "sentiment": "neutral",
    })
    assert card["headline"] == "Apple beats estimates"
    assert card["bullets"] == ["a b", "c d"]


# ── _clip(): the off-by-one that becomes a failed DB write ────────────

@pytest.mark.parametrize("limit", [1, 2, 5, 40, 160, 240])
@pytest.mark.parametrize("text", [
    "x" * 500,
    "short",
    "a sentence with several words in it that runs on for a while",
    "",
])
def test_clip_never_exceeds_its_limit(limit, text):
    # Appending "…" AFTER slicing to `limit` yields limit+1 characters — exactly
    # the off-by-one that turns a length CHECK into a failed write and no card.
    assert len(_clip(text, limit)) <= limit


def test_clip_leaves_short_text_untouched():
    assert _clip("hello", 40) == "hello"


def test_clip_handles_a_zero_limit():
    assert _clip("anything", 0) == ""


# ── deterministic fallback card ───────────────────────────────────────

def test_fallback_returns_none_for_an_empty_corpus(svc):
    # Silence beats a fabricated card.
    assert svc.build_fallback_card("AAPL", []) is None
    assert svc.build_fallback_card("AAPL", [{"headline": "   "}]) is None
    assert svc.build_fallback_card("AAPL", [None, "junk", {}]) is None


def test_fallback_bullets_are_the_real_headlines(svc):
    rows = [
        {"headline": "First story", "sentiment": "bullish"},
        {"headline": "Second story", "sentiment": "bullish"},
        {"headline": "Third story", "sentiment": None},
    ]
    card = svc.build_fallback_card("AAPL", rows)
    assert card["bullets"] == ["First story", "Second story", "Third story"]
    # It must never claim AI authorship for text no model wrote.
    assert card["ai_generated"] is False
    assert card["refreshing"] is True


def test_fallback_pads_a_single_article_to_meet_the_minimum(svc):
    card = svc.build_fallback_card("AAPL", [{"headline": "Only one"}])
    assert len(card["bullets"]) >= MIN_BULLETS
    assert card["bullets"][0] == "Only one"
    # The padding must be honest provenance, not invented commentary.
    assert "AI summary" in card["bullets"][1]


def test_fallback_sentiment_abstains_when_nothing_is_enriched(svc):
    # NULL sentiment is an ABSTENTION. Counting it as Neutral would let
    # un-analysed rows outvote the enriched ones.
    rows = [{"headline": "a"}, {"headline": "b"}, {"headline": "c"}]
    assert svc.build_fallback_card("X", rows)["sentiment"] == "Neutral"


def test_fallback_sentiment_is_a_majority_of_enriched_rows_only(svc):
    rows = [
        {"headline": "a", "sentiment": "bearish"},
        {"headline": "b", "sentiment": "bearish"},
        {"headline": "c", "sentiment": None},      # abstains
        {"headline": "d", "sentiment": None},      # abstains
        {"headline": "e", "sentiment": "bullish"},
    ]
    assert svc.build_fallback_card("X", rows)["sentiment"] == "Bearish"


def test_fallback_folds_legacy_positive_negative_spellings(svc):
    rows = [
        {"headline": "a", "sentiment": "Positive"},
        {"headline": "b", "sentiment": "Positive"},
        {"headline": "c", "sentiment": "bearish"},
    ]
    assert svc.build_fallback_card("X", rows)["sentiment"] == "Bullish"


def test_fallback_ties_resolve_to_neutral(svc):
    rows = [
        {"headline": "a", "sentiment": "bullish"},
        {"headline": "b", "sentiment": "bearish"},
    ]
    assert svc.build_fallback_card("X", rows)["sentiment"] == "Neutral"


def test_fallback_market_scope_is_labelled_market_not_the_raw_key(svc):
    card = svc.build_fallback_card("__MARKET__", [{"headline": "a"}, {"headline": "b"}])
    assert "__MARKET__" not in card["headline"]
    assert "Market" in card["headline"]


# ── _row_to_card(): a malformed DB row is a cache MISS, not a bad card ─

def _row(**over):
    now = datetime.now(timezone.utc)
    row = {
        "scope": "AAPL",
        "headline": "Something happened",
        "bullets": ["one", "two"],
        "sentiment": "Bullish",
        "article_count": 12,
        "generated_at": now.isoformat(),
        "soft_expires_at": (now + timedelta(minutes=15)).isoformat(),
        "trigger_reason": "new_articles",
    }
    row.update(over)
    return row


def test_row_to_card_happy_path(svc):
    card = svc._row_to_card(_row())
    assert card["scope"] == "AAPL"
    assert card["ai_generated"] is True
    assert card["is_stale"] is False
    assert card["bullets"] == ["one", "two"]


def test_row_to_card_parses_bullets_stored_as_json_text(svc):
    card = svc._row_to_card(_row(bullets=json.dumps(["one", "two"])))
    assert card["bullets"] == ["one", "two"]


@pytest.mark.parametrize("bad", [
    {"bullets": None},
    {"bullets": "not json"},
    {"bullets": []},
    {"bullets": ["only one"]},
    {"bullets": [f"b{i}" for i in range(9)]},
    {"headline": ""},
    {"headline": None},
])
def test_row_to_card_discards_malformed_rows(svc, bad):
    # A half-written card in a finance app is worse than no card, so a bad row
    # is treated as a cache MISS and rebuilt.
    assert svc._row_to_card(_row(**bad)) is None


def test_row_to_card_flags_a_soft_expired_card_as_stale(svc):
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    card = svc._row_to_card(_row(soft_expires_at=past.isoformat()))
    assert card["is_stale"] is True


def test_row_to_card_defaults_unknown_sentiment_rather_than_dropping_the_card(svc):
    # Sentiment is one field of many; losing the whole card over it would be
    # a worse trade than showing a neutral badge.
    card = svc._row_to_card(_row(sentiment="who knows"))
    assert card is not None
    assert card["sentiment"] == "Neutral"


# ── timestamp formatting (iOS .iso8601 rejects fractional seconds) ────

def test_iso_strips_fractional_seconds():
    out = _iso("2026-07-20T17:26:41.123456+00:00")
    assert out == "2026-07-20T17:26:41Z"
    assert "." not in out


def test_iso_falls_back_to_now_for_garbage():
    out = _iso("not a timestamp")
    assert out.endswith("Z") and len(out) == 20


# ── prompt construction ───────────────────────────────────────────────

def test_prompt_embeds_the_inputset_id(svc):
    # GeminiClient.generate_json caches on prompt+system+model. Without the
    # fingerprint in the prompt text, two regenerations with byte-identical
    # prompts silently return the CACHED body under a fresh generated_at,
    # making the timestamp on a finance card a lie.
    prompt = svc._build_prompt(
        "AAPL", [{"headline": "A", "summary": "s"}], "FINGERPRINT123", "notable", None
    )
    assert "FINGERPRINT123" in prompt


def test_prompt_includes_price_context_only_when_the_quote_is_usable(svc):
    rows = [{"headline": "A", "summary": "s"}]
    with_quote = svc._build_prompt("AAPL", rows, "x", "notable", {"changePercentage": -3.2})
    assert "3.20%" in with_quote and "down" in with_quote

    for bad in (None, {}, {"changePercentage": None}, {"changePercentage": float("nan")}):
        assert "Price context" not in svc._build_prompt("AAPL", rows, "x", "notable", bad)


def test_prompt_forbids_invention(svc):
    prompt = svc._build_prompt("AAPL", [{"headline": "A"}], "x", None, None)
    assert "Never state a fact" in prompt


def test_market_scope_prompt_describes_the_market_not_the_key(svc):
    prompt = svc._build_prompt("__MARKET__", [{"headline": "A"}], "x", None, None)
    assert "__MARKET__" not in prompt
    assert "US stock market" in prompt


# ── regressions found by the adversarial review ───────────────────────

def test_fallback_card_never_carries_the_ai_summary_badge(svc):
    """The non-AI fallback must not be badged as an AI summary.

    It previously omitted `badge`, so the Pydantic default ("24h · AI Summary")
    filled it in and three verbatim headlines shipped under an AI label.
    """
    card = svc.build_fallback_card("AAPL", [{"headline": "a"}, {"headline": "b"}])
    assert card["ai_generated"] is False
    assert "AI" not in card["badge"]

    # And the field must survive the wire model rather than being defaulted.
    from app.schemas.updates import AIInsightCardResponse
    assert "AI" not in AIInsightCardResponse(**card).badge


def test_fallback_bullets_are_deduplicated(svc):
    # Two publishers syndicating one story pass URL-based dedup but produce
    # identical headlines, which collapse under SwiftUI's ForEach(id: \\.self).
    rows = [
        {"headline": "Same wire story"},
        {"headline": "Same wire story"},
        {"headline": "A different story"},
    ]
    bullets = svc.build_fallback_card("X", rows)["bullets"]
    assert len(bullets) == len(set(bullets))


def test_hard_ttl_spans_a_long_weekend():
    """The sweeper only runs while the market is active.

    With a 12h hard TTL, a card written Friday evening expired Saturday morning
    and every scope served the non-AI fallback all weekend. The TTL must cover
    the longest real gap between sweeps (Thu close → Mon open ≈ 92h).
    """
    from app.services.news_insight_service import (
        _HARD_TTL_ACTIVE_SECONDS, _HARD_TTL_CLOSED_SECONDS,
    )
    longest_gap_hours = 92
    assert _HARD_TTL_ACTIVE_SECONDS >= longest_gap_hours * 3600
    assert _HARD_TTL_CLOSED_SECONDS >= longest_gap_hours * 3600
