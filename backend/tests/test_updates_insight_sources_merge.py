"""Merging the "why it moved" catalyst web sources into a card's source list.

The Insights card `sources` list is the FMP-news corpus (`_corpus_sources`). When a
big-move catalyst runs a grounded web search, its outside sources are folded in via
`_catalyst_web_sources` (shape-map + quality cap) and `_merge_sources` (reserved
slots, dedup, overall cap). Both are pure transforms — no network, no DB. These
tests pin the shape/openability/quality guarantees the user asked for:
outside sources keep a title and an openable url, and only the best few are kept.
"""

from app.services.news_insight_service import (
    _MAX_CATALYST_SOURCES,
    _MAX_SOURCES,
    _catalyst_web_sources,
    _corpus_sources,
    _merge_sources,
    _sanitize_sources,
)

# A real Vertex AI Search redirect uri — opens and redirects to the publisher.
_VERTEX = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc123"
_VERTEX2 = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/def456"


# ─────────────────────────── _catalyst_web_sources ───────────────────────────

def test_web_source_real_headline_kept():
    out = _catalyst_web_sources(
        [{"title": "Salesforce cuts guidance on soft demand", "uri": _VERTEX, "publisher": "reuters"}]
    )
    assert out == [{"title": "Salesforce cuts guidance on soft demand", "url": _VERTEX}]


def test_web_source_bare_domain_title_falls_back_to_publisher():
    # Grounding often returns title as a bare host — use the publisher name instead.
    out = _catalyst_web_sources([{"title": "reuters.com", "uri": _VERTEX, "publisher": "reuters"}])
    assert out == [{"title": "Reuters", "url": _VERTEX}]


def test_web_source_bare_domain_no_publisher_strips_tld():
    out = _catalyst_web_sources([{"title": "www.infosys.com", "uri": _VERTEX, "publisher": ""}])
    assert out == [{"title": "Infosys", "url": _VERTEX}]


def test_web_source_empty_title_uses_publisher():
    out = _catalyst_web_sources([{"title": "", "uri": _VERTEX, "publisher": "bloomberg"}])
    assert out == [{"title": "Bloomberg", "url": _VERTEX}]


def test_web_source_no_name_and_no_url_skipped():
    # url present but nothing nameable → skipped; no url → skipped.
    assert _catalyst_web_sources([{"title": "", "uri": _VERTEX, "publisher": ""}]) == []
    assert _catalyst_web_sources([{"title": "Reuters cuts", "uri": "", "publisher": "reuters"}]) == []


def test_web_source_dedup_by_publisher():
    out = _catalyst_web_sources([
        {"title": "reuters.com", "uri": _VERTEX, "publisher": "reuters"},
        {"title": "Another Reuters take", "uri": _VERTEX2, "publisher": "reuters"},
    ])
    assert len(out) == 1  # one link per publisher


def test_web_source_dedup_by_url():
    out = _catalyst_web_sources([
        {"title": "Reuters A", "uri": _VERTEX, "publisher": "reuters"},
        {"title": "CNBC B", "uri": _VERTEX, "publisher": "cnbc"},
    ])
    assert out == [{"title": "Reuters A", "url": _VERTEX}]


def test_web_source_caps_at_max_and_keeps_head_order():
    raw = [
        {"title": f"Headline {i}", "uri": f"https://vertexaisearch.cloud.google.com/r/{i}",
         "publisher": f"pub{i}"}
        for i in range(6)
    ]
    out = _catalyst_web_sources(raw)
    assert len(out) == _MAX_CATALYST_SOURCES == 3
    assert [s["title"] for s in out] == ["Headline 0", "Headline 1", "Headline 2"]


def test_web_source_malformed_input_yields_empty():
    assert _catalyst_web_sources(None) == []
    assert _catalyst_web_sources("nope") == []
    assert _catalyst_web_sources({}) == []
    assert _catalyst_web_sources([None, 3, "x", {"nope": 1}]) == []


# ─────────────────────────────── _merge_sources ──────────────────────────────

def _corpus(n):
    return [{"title": f"FMP story {i}", "url": f"https://fmp.example/{i}"} for i in range(n)]


def test_merge_no_web_is_corpus_unchanged():
    corpus = _corpus(5)
    assert _merge_sources(corpus, []) == corpus
    assert _merge_sources(corpus, None) == corpus


def test_merge_reserves_slots_so_web_always_shows():
    # 8 corpus + 2 web, cap 8 → web must not be crowded out.
    corpus = _corpus(8)
    web = [{"title": "Reuters", "url": _VERTEX}, {"title": "CNBC", "url": _VERTEX2}]
    merged = _merge_sources(corpus, web)
    assert len(merged) == _MAX_SOURCES == 8
    assert merged[-2:] == web            # web reserved at the tail
    assert merged[:6] == corpus[:6]      # corpus fills the rest, in order


def test_merge_dedup_by_url_across_lists():
    corpus = [{"title": "Shared", "url": "https://dup.example/x"}]
    web = [{"title": "Shared web", "url": "https://dup.example/x"}]
    merged = _merge_sources(corpus, web)
    assert len(merged) == 1


def test_merge_caps_web_at_max_catalyst():
    web = [{"title": f"W{i}", "url": f"https://vertexaisearch.cloud.google.com/r/{i}"} for i in range(5)]
    merged = _merge_sources([], web)
    assert len(merged) == _MAX_CATALYST_SOURCES == 3


def test_merge_empty_inputs():
    assert _merge_sources([], []) == []
    assert _merge_sources(None, None) == []


def test_merge_skips_titleless_rows():
    merged = _merge_sources([{"title": "", "url": "https://x"}], [{"title": "Ok", "url": _VERTEX}])
    assert merged == [{"title": "Ok", "url": _VERTEX}]


# ────────────────────────── end-to-end pipeline shape ────────────────────────

def test_pipeline_corpus_plus_catalyst_survives_sanitize():
    """The exact production path: corpus + mapped web → merge → sanitize (write)."""
    corpus_rows = [
        {"headline": "CRM beats on revenue", "article_url": "https://fmp.example/a"},
        {"headline": "Analysts lift CRM target", "article_url": "https://fmp.example/b"},
    ]
    grounding = [
        {"title": "reuters.com", "uri": _VERTEX, "publisher": "reuters"},
        {"title": "Salesforce guidance cut detailed", "uri": _VERTEX2, "publisher": "bloomberg"},
    ]
    merged = _merge_sources(_corpus_sources(corpus_rows), _catalyst_web_sources(grounding))
    sanitized = _sanitize_sources(merged)
    assert sanitized is not None
    titles = [s["title"] for s in sanitized]
    # Both FMP headlines AND the openable web sources are present, each nameable.
    assert "CRM beats on revenue" in titles
    assert "Reuters" in titles
    assert "Salesforce guidance cut detailed" in titles
    assert all(s["title"] for s in sanitized)          # every row has a title
    assert any(s["url"] == _VERTEX for s in sanitized)  # web url is openable/kept
