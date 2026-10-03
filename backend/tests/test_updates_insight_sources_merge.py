"""The Insights card's `sources` list: the FMP-news corpus, and nothing grounded.

A big-move "why it moved" catalyst's grounded web sources used to be MERGED into this list
(`_catalyst_web_sources` + `_merge_sources`, reserved slots at the tail). That catalyst was
retired on 2026-10-02 with Google Search grounding, and the merge went with it: a card's
sources are now exactly `_corpus_sources(articles)`. Cards stored before the retirement can
still hold grounding-redirect rows until migration 188 runs, so `_sanitize_sources` — the
write AND read-back choke point — drops them; the pipeline tests pin that drop.
"""

import json

import pytest

from app.services.news_insight_service import (
    _MAX_SOURCES,
    NewsInsightService,
    _corpus_sources,
    _sanitize_sources,
)

# A real Vertex AI Search redirect uri — the only form a grounded source ever took.
_VERTEX = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc123"
_VERTEX2 = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/def456"


def _corpus(n):
    return [{"title": f"FMP story {i}", "url": f"https://fmp.example/{i}"} for i in range(n)]


# ────────────────────────── a stored card's mixed list ────────────────────────

def test_pipeline_drops_grounded_links_at_sanitize_and_keeps_the_corpus():
    """What a card stored before the retirement holds — corpus rows, then the grounded rows
    the old merge appended — read back through the choke point: the grounding-redirect rows
    are dropped, every FMP corpus row (and its publisher) survives, in order."""
    corpus_rows = [
        {"headline": "CRM beats on revenue", "article_url": "https://fmp.example/a",
         "source_name": "CNBC Television"},
        {"headline": "Analysts lift CRM target", "article_url": "https://fmp.example/b"},
    ]
    stored = _corpus_sources(corpus_rows) + [
        {"title": "Reuters", "url": _VERTEX},
        {"title": "Salesforce guidance cut detailed", "url": _VERTEX2, "publisher": "Bloomberg"},
    ]
    sanitized = _sanitize_sources(stored)
    assert sanitized == [
        {"title": "CRM beats on revenue", "url": "https://fmp.example/a",
         "publisher": "CNBC Television"},
        {"title": "Analysts lift CRM target", "url": "https://fmp.example/b"},
    ]
    assert _sanitize_sources(sanitized) == sanitized          # idempotent on read-back


def test_a_dropped_grounded_row_never_spends_a_slot_of_the_cap():
    """The old merge reserved the TAIL for grounded rows; dropping them must not leave the
    list short when the stored corpus part already filled the cap."""
    stored = [{"title": "Grounded", "url": _VERTEX}] * 3 + _corpus(_MAX_SOURCES + 2)
    out = _sanitize_sources(stored)
    assert out == _corpus(_MAX_SOURCES)
    assert len(out) == _MAX_SOURCES == 8


def test_an_all_grounded_list_reads_back_as_no_sources():
    """None, not [] — the client then hides the tap affordance, as for a pre-092 card."""
    assert _sanitize_sources([{"title": "R", "url": _VERTEX}, {"title": "B", "url": _VERTEX2}]) is None


# ────────────────────────── the corpus rows themselves ─────────────────────────

def test_corpus_sources_carry_the_publisher_and_sanitize_keeps_it():
    """`_sanitize_sources` REBUILDS each row, so it drops any key it does not name — losing
    `publisher` here would send every card back to citing bare hosts."""
    rows = _sanitize_sources(_corpus_sources(
        [{"headline": "Fed holds rates", "article_url": "https://x/1", "source_name": "CNBC Television"},
         {"headline": "Oil climbs", "article_url": "https://x/2", "source_name": "   "},
         {"headline": "Gold slips", "article_url": "https://x/3", "source_name": {"a": 1}}]
    ))
    assert [r.get("publisher") for r in rows] == ["CNBC Television", None, None]
    assert all("publisher" not in r for r in rows[1:])        # absent, never blank


# ──────────────────────── generation writes the corpus only ───────────────────────

class _Gemini:
    async def generate_json(self, **kwargs):
        return {"text": json.dumps({
            "headline": "Salesforce beats on revenue",
            "points": ["Revenue beat estimates.", "Analysts lifted targets."],
            "sentiment": "bullish",
            "conclusion": "The beat and the target raises point the same way for Salesforce.",
        })}


class _Svc(NewsInsightService):
    def __init__(self):
        self.supabase = None
        self._cache = {}
        self._inflight = {}
        self.gemini = _Gemini()
        self.store_args = None

    def _store(self, *args, **kwargs):
        self.store_args = (args, kwargs)
        return True


@pytest.mark.asyncio
async def test_generate_and_store_writes_exactly_the_corpus_sources():
    articles = [
        {"headline": "Salesforce beats on revenue", "article_url": "https://fmp.example/a",
         "source_name": "Reuters", "summary": "Revenue beat estimates."},
        {"headline": "Analysts lift Salesforce targets", "article_url": "https://fmp.example/b",
         "summary": "Analysts lifted targets."},
    ]
    svc = _Svc()
    card = await svc.generate_and_store(
        scope="CRM", corpus=articles, inputset_id="iid", price_band="extreme",
        trigger_reason="t", quote=None, market_active=True,
    )
    assert card is not None
    args, kwargs = svc.store_args
    assert not kwargs
    assert args[-1] == _corpus_sources(articles)
    assert len(args) == 7          # scope, card, inputset, reason, count, market_active, sources
