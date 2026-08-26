"""The subject filter's two PRODUCTION wiring defects, from TestFlight (2026-08-24).

    "I can see there are news today, but the Insight show updated 2 days ago.
     Improve this feature. Also, on Plug, it doesn't have Insight at all?"

Both symptoms came out of `news_insight_service.article_is_about`, which decides
whether an article belongs in a ticker's insight corpus. It was far stricter in
production than its own unit tests suggested, for two reasons that only appear at
the call sites:

1. **`company_name` was a dead parameter.** `filter_to_subject` and
   `select_recent_corpus` both accept it, `test_news_corpus_subject_filter`
   exercises it — and NO production caller supplied one. So the name branch never
   ran outside the suite, and the only title signal left was the literal SYMBOL.
   Headlines print "Oracle", never "ORCL", so ORCL's corpus collapsed to the
   handful of articles FMP happened to tag with exactly one ticker. An unchanged
   corpus is an unchanged fingerprint, and the materiality gate answers
   `fingerprint_unchanged` → the card froze while the timeline below it filled up
   with today's news. That is symptom one, exactly as reported.

2. **The endpoint gated show/hide on the SUBJECT corpus.** A ticker whose recent
   coverage is all peer/sector wraps filtered to empty and got NO card at all —
   neither AI nor fallback — above a timeline listing the very articles that had
   just been filtered out. That is symptom two.

These tests pin the wiring, not the predicate: the predicate was already covered
and already green while both defects shipped.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.api.v1.endpoints import updates as updates_endpoint
from app.services.news_insight_service import (
    _ROUNDUP_TICKER_COUNT,
    article_is_about,
    company_name_variants,
    select_recent_corpus,
)
from app.services.news_cache_service import MARKET_SCOPE
from app.services.updates_insight_sweeper import InsightSweeper

NOW = datetime(2026, 8, 24, 18, 0, tzinfo=timezone.utc)


def _row(headline, tickers, *, hours_ago=1.0):
    return {
        "headline": headline,
        "related_tickers": list(tickers),
        "published_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
    }


def _live_row(headline, tickers, *, hours_ago=1.0):
    """Dated against the REAL clock, for the endpoint tests only.

    `get_updates_feed` calls `datetime.now(timezone.utc)` itself, so a row dated
    from the frozen `NOW` above drifts through the 24h/48h bands as real time
    passes. That is not merely brittle — it silently VACATES the assertions: a
    fixture that lands in the 48h band makes `feed_window` and `subject_window`
    both 48, so a badge test cannot tell them apart and passes under a mutation
    that swaps one for the other. (Observed: the aged-out-badge test below passed
    with the guard removed until this helper existed.)
    """
    return {
        "headline": headline,
        "related_tickers": list(tickers),
        "published_at": (
            datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        ).isoformat(),
    }


# ── 1. The reported ORCL corpus starvation ───────────────────────────────────

# Verbatim from the user's screenshot: the card's own bullets cite the Metzler
# stake story, and the timeline beneath it leads with the Oracle/Amazon piece.
ORCL_TODAY = [
    _row("Oracle vs. Amazon: Which Is the Better AI Cloud Stock to Own for the "
         "Next 5 Years?", ["ORCL", "AMZN"]),
    _row("B. Metzler seel. Sohn & Co. AG Boosts Stake in Oracle Corporation",
         ["ORCL", "AMZN"], hours_ago=3),
    _row("Analysts Divided on Oracle's Path to Higher Stock Price",
         ["ORCL", "MSFT"], hours_ago=5),
]


@pytest.mark.parametrize("row", ORCL_TODAY)
def test_an_article_that_leads_with_the_company_is_in_its_own_corpus(row):
    """Every one of these was DROPPED before the fix: "orcl" is not in a headline
    that says "Oracle", no caller passed a name, and none was tagged with exactly
    one ticker."""
    assert article_is_about(row, "ORCL") is True


def test_the_orcl_corpus_is_not_starved_to_empty():
    kept, _ = select_recent_corpus(ORCL_TODAY, NOW, scope="ORCL")
    assert len(kept) == len(ORCL_TODAY), (
        "a starved corpus is an unchanged fingerprint, which is why the card "
        "froze at two days old while today's news showed below it"
    )


def test_enrichment_cannot_evict_an_article_from_its_own_corpus():
    """PATH DEPENDENCE, the nastiest half of defect 1.

    `_batch_enrich_articles` merges Gemini's extracted symbols into
    `related_tickers`, so a story that qualified at ingest as ["ORCL"] stopped
    qualifying once it became ["ORCL","MSFT"] — the corpus SHRANK as the pipeline
    did more work, and the card got staler the more the app enriched.
    """
    headline = "Oracle Stock Jumps On Cloud Backlog"
    at_ingest = _row(headline, ["ORCL"])
    after_enrichment = _row(headline, ["ORCL", "MSFT", "NVDA"])
    assert article_is_about(at_ingest, "ORCL") is True
    assert article_is_about(after_enrichment, "ORCL") is True


# ── anti-vacuity: the guard this filter exists for is still armed ────────────

ROUNDUP = (
    "FuelCell Energy Sinks 8%, Bloom Energy Falls 3%, Plug Power Drops 3%: "
    "What's Behind the Hydrogen Stock Selloff?"
)


def test_a_peer_wrap_is_still_rejected_for_a_trailing_member():
    """Widening the lead-tag rule must not re-open the incident that produced
    "Hydrogen Stocks Face Selloff" as PLUG's card on a day PLUG rose 4.16%."""
    assert article_is_about(_row(ROUNDUP, ["FCEL", "BE", "PLUG"]), "PLUG") is False


def test_a_two_tag_peer_story_is_still_rejected():
    """The widening is LEAD-tag, not ANY-tag. A story about Bloom that merely
    tags us stays out."""
    row = _row("Bloom Energy lands data-centre deal", ["BE", "PLUG"])
    assert article_is_about(row, "PLUG") is False


def test_a_short_ticker_is_not_a_substring_wildcard():
    """`symbol.lower() in haystack` made every short ticker match on a letter:
    "BE" (Bloom Energy) matched "Beyond Meat", "F" (Ford) matched the f inside
    any word. Whole-token matching, with a real hit as the anti-vacuity control."""
    assert article_is_about(_row("Beyond Meat Slumps On Guidance", ["APRN"]), "BE") is False
    assert article_is_about(_row("Ford Recalls 200,000 Trucks", ["GM"]), "F") is False
    assert article_is_about(_row("F Q3 Deliveries Beat", ["GM", "F"]), "F") is True


def test_the_widening_is_confined_to_two_tag_articles():
    """THE BLAST-RADIUS FENCE.

    Extending the lead-tag rule from "round-ups only" to "any tag count" reads like
    it could neuter the filter, so the exact reach is pinned here. It cannot:

      * 1 tag  — the old code already admitted `tags == [symbol]`, which for a lone
                 tag is the same predicate.
      * >=3    — the old code already ran the lead-tag check first.

    So ONLY the two-tag case moves, which is the gap that dropped "Oracle vs.
    Amazon" from Oracle's own corpus. Measured against 608 live production articles
    across 14 tickers: 32 verdicts changed, all of them two-tag, none at 1 or >=3.
    The Hydrogen-selloff shape is a THREE-tag wrap and is bit-for-bit unaffected.
    """
    def old_predicate(tags, title, symbol):
        """The rule exactly as it shipped, minus the never-supplied name."""
        hay = title.lower()
        if len(tags) >= _ROUNDUP_TICKER_COUNT:
            if tags and tags[0] == symbol:
                return True
            hay = re.split(r"[,:—–-]", hay, maxsplit=1)[0]
        if symbol.lower() in hay:
            return True
        return tags == [symbol]

    titles = [
        "Acme Corp Beats Estimates",
        "PLUG Jumps 9% After Order",
        "Rival Sinks 8%, Acme Falls 3%: Sector Selloff",
        "Nothing Relevant Here",
    ]
    tagsets = [
        [], ["PLUG"], ["BE"],
        ["PLUG", "BE"], ["BE", "PLUG"],
        ["PLUG", "BE", "FCEL"], ["BE", "FCEL", "PLUG"], ["BE", "PLUG", "FCEL", "ACME"],
    ]
    changed_at = set()
    for title in titles:
        for tags in tagsets:
            row = {"headline": title, "related_tickers": tags}
            if article_is_about(row, "PLUG") != old_predicate(tags, title, "PLUG"):
                changed_at.add(len(tags))

    assert changed_at <= {2}, (
        f"the lead-tag widening leaked outside the two-tag case: tag counts {sorted(changed_at)}"
    )
    assert changed_at == {2}, "anti-vacuity: the two-tag case must actually have moved"


def test_a_symbol_with_punctuation_still_matches():
    """Lookarounds rather than `\\b`: a symbol starting or ending with a non-word
    character (`^GSPC`, `BRK.B`) asserts `\\b` against the wrong side and would
    silently never match."""
    assert article_is_about(_row("BRK.B Tops Estimates", ["BRK.B"]), "BRK.B") is True


# ── 2. company_name is actually supplied now ─────────────────────────────────

def test_company_name_variants_reach_a_bare_surname_headline():
    """The old matcher tried the full name then its first TWO words, so its own
    documented example did not work: "Archer Aviation Inc." yielded "archer
    aviation", which is absent from a headline reading "Archer Jumps 9%"."""
    assert "archer" in company_name_variants("Archer Aviation Inc.")
    assert "oracle" in company_name_variants("Oracle Corporation")
    assert "kroger" in company_name_variants("The Kroger Co.")
    assert company_name_variants(None) == []
    assert company_name_variants("   ") == []


def test_the_name_admits_an_article_the_tag_order_does_not():
    """The name path's whole job: FMP led with the peer, but the headline is ours."""
    row = _row("Amazon and Oracle sign multi-year cloud pact", ["AMZN", "ORCL"])
    assert article_is_about(row, "ORCL") is False
    assert article_is_about(row, "ORCL", "Oracle Corporation") is True


class _Table:
    def __init__(self, rows):
        self._rows = rows
        self.selected = None
        self.filtered = None

    def select(self, cols):
        self.selected = cols
        return self

    def in_(self, col, values):
        self.filtered = (col, list(values))
        return self

    def execute(self):
        class _R:
            data = self._rows
        return _R()


class _Supabase:
    def __init__(self, rows):
        self.rows = rows
        self.table_calls = []
        self.last_table = None

    def table(self, name):
        self.table_calls.append(name)
        self.last_table = _Table(self.rows)
        return self.last_table


class _StubSweeper(InsightSweeper):
    """InsightSweeper with no network clients (see .claude/rules/testing.md)."""

    def __init__(self, supabase):
        self.supabase = supabase
        self.fmp = None
        self.news = None
        self.insights = None
        self.vol = None


def test_the_sweeper_resolves_company_names_for_the_universe():
    """THE WIRING TEST. `company_name` was accepted, documented and unit-tested
    while no caller passed one; only a test at this level can catch that."""
    supabase = _Supabase([
        {"ticker": "orcl", "company_name": "Oracle Corporation"},
        {"ticker": "ORCL", "company_name": "Oracle Corporation"},   # another watcher
        {"ticker": "PLUG", "company_name": "Plug Power Inc."},
        {"ticker": "NONAME", "company_name": None},
    ])
    names = _StubSweeper(supabase)._company_names([MARKET_SCOPE, "ORCL", "PLUG", "NONAME"])

    assert names == {"ORCL": "Oracle Corporation", "PLUG": "Plug Power Inc."}
    assert supabase.table_calls == ["watchlist_items"]
    # MARKET_SCOPE has no company and must never reach the IN () clause.
    assert supabase.last_table.filtered == ("ticker", ["ORCL", "PLUG", "NONAME"])


def test_company_name_lookup_failure_degrades_instead_of_failing_the_sweep():
    class _Boom:
        def table(self, name):
            raise RuntimeError("supabase down")

    assert _StubSweeper(_Boom())._company_names(["ORCL"]) == {}


def test_market_scope_alone_skips_the_query_entirely():
    supabase = _Supabase([])
    assert _StubSweeper(supabase)._company_names([MARKET_SCOPE]) == {}
    assert supabase.table_calls == []


# ── 2b. …and the SWEEP actually passes them to the filter ────────────────────

class _SweepStub(InsightSweeper):
    """Enough of a sweeper to reach the corpus-windowing call and stop.

    Every corpus is empty, so the materiality gate answers `no_corpus` for all
    scopes and nothing is claimed, generated or billed.
    """

    def __init__(self):
        self.supabase = None
        self.fmp = self
        self.vol = self
        self.news = self
        self.insights = self
        self._catalyst_day = self._enrich_day = None
        self._catalyst_count = self._enrich_count = 0
        self._catalyst_scopes = set()

    async def _universe(self):
        return [MARKET_SCOPE, "ORCL"]

    def _company_names(self, scopes):
        return {"ORCL": "Oracle Corporation"}

    def _load_state(self, scopes):
        return {}

    def _record_skips(self, skips, now):
        pass

    async def get_batch_quotes_bulk(self, symbols):
        return []

    async def get_sigmas_bulk(self, symbols):
        return {}

    def get_cached_bulk(self, scopes, limit):
        return {"__MARKET__": [], "ORCL": []}

    async def mark_verified_current(self, scopes, market_active):
        return None


def test_the_sweep_passes_the_resolved_company_name_into_the_subject_filter(monkeypatch):
    """THE CALL-SITE TEST, and the reason it exists.

    `test_the_sweeper_resolves_company_names_for_the_universe` above proves the
    LOOKUP works — and a mutation that deletes `company_name=` from the sweep's
    `select_recent_corpus(...)` call leaves it green, because the lookup still
    runs and its result is simply dropped on the floor. That is bit-for-bit the
    original defect: a parameter that was accepted, documented and unit-tested
    while no caller supplied it. Only an assertion on the ARGUMENTS the sweep
    actually passes can catch it.
    """
    import app.services.updates_insight_sweeper as sweeper_mod

    calls = []
    real = sweeper_mod.select_recent_corpus

    def _recording(rows, now, **kwargs):
        calls.append(kwargs)
        return real(rows, now, **kwargs)

    monkeypatch.setattr(sweeper_mod, "select_recent_corpus", _recording)
    monkeypatch.setattr(sweeper_mod, "is_market_active", lambda: True)

    asyncio.run(_SweepStub().run_sweep(refresh_news=False))

    by_scope = {c.get("scope"): c for c in calls}
    assert by_scope["ORCL"]["company_name"] == "Oracle Corporation", (
        "the sweep resolved the name and then failed to pass it to the filter"
    )
    # MARKET opts out of the subject filter entirely and has no company.
    assert by_scope[None]["company_name"] is None


# ── 3. The endpoint shows a card whenever the FEED has news ──────────────────

class _NewsStub:
    def __init__(self, articles):
        self._articles = articles

    async def get_ticker_news(self, scope, **kwargs):
        return {"articles": self._articles, "cached": True}

    async def get_market_news(self, **kwargs):
        return {"articles": self._articles, "cached": True}


class _InsightsStub:
    """No stored AI card — the cold-scope / all-peer-coverage case."""

    def __init__(self):
        self.fallback_corpus = None

    async def get_cards(self, scopes):
        return {}

    def build_fallback_card(self, scope, corpus):
        self.fallback_corpus = corpus
        return {
            "scope": scope,
            "headline": f"Latest {scope} headlines",
            "bullets": [r["headline"] for r in corpus][:3] or ["a", "b"],
            "sentiment": "Neutral",
            "badge": "Latest headlines",
            "article_count": len(corpus),
            "generated_at": "2026-08-24T18:00:00Z",
            "is_stale": False,
            "refreshing": False,
            "ai_generated": False,
            "trigger_reason": None,
            "sources": [],
        }


def _feed(monkeypatch, articles):
    insights = _InsightsStub()
    monkeypatch.setattr(
        updates_endpoint, "get_news_cache_service", lambda: _NewsStub(articles)
    )
    monkeypatch.setattr(
        updates_endpoint, "get_news_insight_service", lambda: insights
    )
    resp = asyncio.run(updates_endpoint.get_updates_feed(scope="PLUG", limit=50, offset=0))
    return resp, insights


def test_a_scope_whose_recent_news_is_all_peer_coverage_still_gets_a_card(monkeypatch):
    """THE REPORTED PLUG BUG. Every article here is filtered out of PLUG's SUBJECT
    corpus — and that is correct, none of them is about PLUG. But they are real,
    in-window articles that the timeline renders, so answering "no Insights card
    at all" left a visible hole above a populated feed."""
    articles = [_live_row(ROUNDUP, ["FCEL", "BE", "PLUG"]),
                _live_row("Bloom Energy lands data-centre deal", ["BE", "PLUG"], hours_ago=4)]
    assert select_recent_corpus(articles, datetime.now(timezone.utc), scope="PLUG")[0] == [], (
        "precondition: the subject corpus really is empty"
    )

    resp, insights = _feed(monkeypatch, articles)

    assert resp.insight is not None, "a populated feed must not show an empty header"
    assert resp.insight.ai_generated is False
    assert resp.insight.badge == "Latest headlines"
    # The fallback is built from the FEED window, not the (empty) subject window.
    assert len(insights.fallback_corpus) == 2


def test_an_ai_card_whose_own_corpus_aged_out_does_not_claim_a_24h_window(monkeypatch):
    """The badge is a claim about the brief's SPAN, not about the feed's.

    Once show/hide moved to the feed window, an AI card can outlive the articles it
    was written from: peer coverage keeps the feed populated while nothing about the
    ticker has landed in 48h. Badging that card from the FEED window would print
    "24h" over a brief written days ago, citing articles that were never in it.
    """
    articles = [_live_row(ROUNDUP, ["FCEL", "BE", "PLUG"])]     # 1h old, not about PLUG
    assert select_recent_corpus(articles, datetime.now(timezone.utc), scope="PLUG")[0] == []
    assert select_recent_corpus(articles, datetime.now(timezone.utc))[1] == 24, (
        "anti-vacuity: the FEED window must be 24h here, so borrowing it would "
        "produce a visibly different badge from the 48h default"
    )

    class _HasStaleCard(_InsightsStub):
        async def get_cards(self, scopes):
            return {"PLUG": {
                "scope": "PLUG", "headline": "Plug Power margin progress",
                "bullets": ["a", "b"], "sentiment": "Neutral",
                "article_count": 4, "generated_at": "2026-08-21T12:00:00Z",
                "is_stale": False, "refreshing": False, "ai_generated": True,
                "trigger_reason": None, "sources": [],
            }}

    monkeypatch.setattr(
        updates_endpoint, "get_news_cache_service", lambda: _NewsStub(articles)
    )
    monkeypatch.setattr(updates_endpoint, "get_news_insight_service", _HasStaleCard)
    resp = asyncio.run(updates_endpoint.get_updates_feed(scope="PLUG", limit=50, offset=0))

    assert resp.insight is not None and resp.insight.ai_generated is True
    assert resp.insight.badge == "48h", (
        "an aged-out card must fall back to the widest span, not borrow the feed's"
    )


def test_a_scope_with_no_news_in_the_window_still_shows_no_card(monkeypatch):
    """Anti-vacuity for the change above: the show/hide gate must still be a gate.
    Nothing in 48h means nothing to summarise, and an empty card is worse than none."""
    articles = [_live_row("Plug Power wins hydrogen contract", ["PLUG"], hours_ago=200)]
    resp, _ = _feed(monkeypatch, articles)
    assert resp.insight is None


def test_page_two_never_carries_an_insight_card(monkeypatch):
    """Unchanged contract: a page-2 fallback would summarise yesterday's news and
    replace today's."""
    monkeypatch.setattr(
        updates_endpoint, "get_news_cache_service",
        lambda: _NewsStub([_live_row("Plug Power wins hydrogen contract", ["PLUG"])]),
    )
    monkeypatch.setattr(
        updates_endpoint, "get_news_insight_service", lambda: _InsightsStub()
    )
    resp = asyncio.run(
        updates_endpoint.get_updates_feed(scope="PLUG", limit=50, offset=50)
    )
    assert resp.insight is None
