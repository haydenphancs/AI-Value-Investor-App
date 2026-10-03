"""The Updates card's `price_move` block, its `sources`, and the price-move alert.

The block used to carry the grounded web-search "why it moved" catalyst. That search was
retired on 2026-10-02 with Google Search grounding (its terms forbid caching a grounded
answer and serving it to every watcher), so nothing produces a block any more: `_store`
always writes the column NULL (clearing a block a row still carries), the feed never serves
it, and the alert body is deterministic. No network.
"""

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture(autouse=True)
def _fresh_earnings_singleton():
    """`run_sweep` reaches the REAL earnings-window service through a stub client
    that lacks `get_earnings_calendar`; it degrades correctly but stamps a 15-min
    negative TTL on the process singleton with the real clock. Reset so no other
    test inherits that clock."""
    from app.services.earnings_window_service import get_earnings_window_service

    get_earnings_window_service().reset()
    yield
    get_earnings_window_service().reset()

from app.services import updates_insight_sweeper as mod
from app.services.updates_insight_sweeper import InsightSweeper
from app.services.updates_materiality import ACTION_SKIP, Decision


# ── _store: the price_move column is always written NULL ─────────────────

class _CaptureSupabase:
    """Records the exact row handed to upsert so we can assert on the payload."""
    def __init__(self):
        self.rows = []

    def table(self, _name):
        return self

    def upsert(self, row, on_conflict=None):
        self.rows.append(row)
        return self

    def execute(self):
        class _R:
            data = []
        return _R()


def _insight_service():
    from app.services.news_insight_service import NewsInsightService
    svc = object.__new__(NewsInsightService)
    svc.supabase = _CaptureSupabase()
    svc._cache = {}
    svc._inflight = {}
    return svc


_CARD = {"headline": "H", "bullets": ["a", "b"], "sentiment": "Neutral"}


def test_store_always_writes_a_null_price_move():
    """The column stays in the upsert payload as NULL — a PostgREST upsert only SETs the
    columns it is given, so OMITTING it would keep a grounded block a row still holds."""
    svc = _insight_service()
    assert svc._store("AAPL", _CARD, "iid", "reason", 3, True)
    row = svc.supabase.rows[-1]
    assert "price_move" in row and row["price_move"] is None


def test_nothing_can_hand_the_card_a_price_move_or_catalyst_links_any_more():
    """The dormant parameters are gone, so a caller that still passed one fails loudly
    (TypeError) instead of having a grounded block or link quietly stored."""
    import inspect

    from app.services import news_insight_service as nis

    dead = {"price_move", "preserve_price_move", "catalyst_sources", "max_points"}
    for fn in (nis.NewsInsightService.generate_and_store, nis.NewsInsightService._store,
               nis.NewsInsightService._generate_card, nis.NewsInsightService._build_prompt,
               nis.NewsInsightService._repair_prompt):
        assert not dead & set(inspect.signature(fn).parameters), fn.__name__
    for name in ("catalyst_display_line", "_catalyst_block", "_sanitize_price_move",
                 "_catalyst_web_sources", "_merge_sources", "_MAX_CATALYST_SOURCES",
                 "_max_points_for", "MAX_POINTS_WITH_CATALYST"):
        assert not hasattr(nis, name), name


# ── Insights "sources" (migration 092) ────────────────────────────────

def test_corpus_sources_selects_title_url_dedups_and_caps():
    from app.services.news_insight_service import _corpus_sources
    corpus = [
        {"headline": "Fed holds rates", "article_url": "https://x/1"},
        {"headline": "Fed holds rates", "article_url": "https://x/1"},  # dup url → dropped
        {"headline": "Oil climbs", "url": "https://x/2"},               # `url` fallback key
        {"headline": ""},                                               # no title → dropped
        {"nope": 1},                                                    # not an article → dropped
        {"headline": "No link story"},                                  # kept, empty url
    ]
    out = _corpus_sources(corpus)
    assert out == [
        {"title": "Fed holds rates", "url": "https://x/1"},
        {"title": "Oil climbs", "url": "https://x/2"},
        {"title": "No link story", "url": ""},
    ]
    # Cap is respected.
    many = [{"headline": f"S{i}", "article_url": f"https://x/{i}"} for i in range(20)]
    assert len(_corpus_sources(many, cap=8)) == 8


def test_sanitize_sources_drops_junk_and_is_idempotent():
    from app.services.news_insight_service import _sanitize_sources
    assert _sanitize_sources(None) is None
    assert _sanitize_sources("nope") is None
    assert _sanitize_sources([]) is None                    # empty → None, not []
    assert _sanitize_sources([{"title": ""}, {"nope": 1}, 42]) is None
    clean = _sanitize_sources([{"title": "T", "url": "u"}, {"title": "T2"}])
    assert clean == [{"title": "T", "url": "u"}, {"title": "T2", "url": ""}]
    # Read-back of an already-sanitized list is idempotent.
    assert _sanitize_sources(clean) == clean


def test_store_writes_sanitized_sources():
    svc = _insight_service()
    sources = [
        {"title": "Fed holds rates", "url": "https://x/1"},
        {"title": "", "url": "https://x/bad"},   # dropped (no title)
    ]
    assert svc._store("AAPL", _CARD, "iid", "reason", 3, True, sources)
    row = svc.supabase.rows[-1]
    assert row["sources"] == [{"title": "Fed holds rates", "url": "https://x/1"}]


def test_store_writes_null_sources_when_none():
    svc = _insight_service()
    assert svc._store("AAPL", _CARD, "iid", "reason", 3, True, None)
    assert svc.supabase.rows[-1]["sources"] is None


# ── …and the SWEEP blanks such a row before the gate ever sees it ────────────────

@pytest.mark.asyncio
async def test_the_sweep_neutralises_prior_session_rows_at_the_choke_point(monkeypatch):
    """`decide()` (the materiality gate), the card prompt's price line and the alert all
    read the SAME `quotes_by_symbol`. The sweep blanks a prior-session
    row's change fields once, there, so a pre-market pass cannot regenerate on
    yesterday's band, describe yesterday's move as "the current session", or alert on it.
    Dates are built against the real clock because `run_sweep` reads it."""
    from app.services.news_cache_service import MARKET_SCOPE as MKT
    from app.utils.market_hours import previous_trading_day, session_trading_date
    from _price_fakes import PriceFromFMPFake

    current = session_trading_date()
    prior = previous_trading_day(current)

    class _Stub(InsightSweeper):
        def __init__(self):
            self.supabase = None
            self.fmp = self
            self.price = PriceFromFMPFake(self.fmp)
            self.vol = self
            self.news = self
            self.insights = self
            self._enrich_day = None
            self._enrich_count = 0

        async def _universe(self):
            return [MKT, "TER", "AAPL"]

        def _company_names(self, scopes):
            return {}

        def _load_state(self, scopes):
            return {}

        def _record_skips(self, skips, now):
            pass

        async def get_batch_quotes_bulk(self, symbols):
            return [
                # Pre-market TER: price == yesterday's close, change = yesterday's session.
                {"symbol": "TER", "price": 371.47, "change": -57.0, "changePercentage": -13.3,
                 "changesPercentage": -13.3, "changeSession": prior.isoformat()},
                # AAPL has printed: a live, current-session move.
                {"symbol": "AAPL", "price": 230.0, "change": 2.3, "changePercentage": 1.0,
                 "changesPercentage": 1.0, "changeSession": current.isoformat()},
                # The index leg, current.
                {"symbol": mod.MARKET_INDEX_SYMBOL, "price": 500.0, "change": -5.0,
                 "changePercentage": -1.0, "changesPercentage": -1.0,
                 "changeSession": current.isoformat()},
            ]

        async def get_sigmas_bulk(self, symbols):
            return {}

        def get_cached_bulk(self, scopes, limit):
            return {s: [] for s in scopes}

        async def mark_verified_current(self, scopes, market_active):
            return None

    seen = {}

    def _recording_decide(**kwargs):
        seen[kwargs["scope"]] = (kwargs.get("quote"), kwargs.get("market_change_percent"))
        return Decision(action=ACTION_SKIP, reason="no_corpus")

    monkeypatch.setattr(mod, "decide", _recording_decide)
    monkeypatch.setattr(mod, "is_market_active", lambda: True)

    await _Stub().run_sweep(refresh_news=False)

    ter_quote, _ = seen["TER"]
    assert ter_quote["changePercentage"] is None and ter_quote["change"] is None \
        and ter_quote["changesPercentage"] is None, "yesterday's move reached the gate as today's"
    assert ter_quote["price"] == 371.47 and ter_quote["changeSession"] == prior.isoformat()
    aapl_quote, mkt = seen["AAPL"]
    assert aapl_quote["changePercentage"] == 1.0, "a current-session row must pass through untouched"
    assert mkt == -1.0, "the index leg (MWCB guard) must pass through untouched"


# ── No "why it moved" any more: a deterministic alert, no block, no grounded links ──

@pytest.mark.parametrize("cp, body", [
    (-8.2, "Down 8.2% in today's session. Open AAPL for the latest coverage."),
    (12.04, "Up 12.0% in today's session. Open AAPL for the latest coverage."),
])
def test_the_alert_body_is_the_move_and_a_pointer_never_a_cause(cp, body):
    assert InsightSweeper._alert_body("AAPL", cp) == body


def test_the_sweep_passes_no_price_move_and_no_catalyst_links():
    """Comment-free source scan of `run_sweep`: the card is generated and the watchers are
    notified with no `price_move` and no `catalyst_sources` — and the fetch is gone."""
    import inspect

    src = "\n".join(l for l in inspect.getsource(InsightSweeper.run_sweep).splitlines()
                    if not l.strip().startswith("#"))
    for token in ("price_move", "catalyst_sources", "_maybe_price_move", "get_catalyst"):
        assert token not in src, token
    assert not hasattr(InsightSweeper, "_maybe_price_move")
    assert "price_move" not in inspect.signature(InsightSweeper._notify_watchers).parameters


def test_the_feed_never_serves_a_stored_price_move_block():
    """A card stored before the retirement still holds its grounded block until migration 188
    runs; the read path drops it regardless."""
    import inspect
    from app.services import news_insight_service as nis

    src = inspect.getsource(nis.NewsInsightService._row_to_card)
    assert '"price_move": None,' in src
    assert "row.get(\"price_move\")" not in src

    svc = _insight_service()
    card = svc._row_to_card({
        "scope": "AAPL", "headline": "H", "bullets": ["a", "b"], "sentiment": "Bearish",
        "generated_at": "2026-10-01T15:00:00+00:00", "prompt_version": 7,
        "price_move": {"tier": "Extreme", "change_percent": -8.2,
                       "catalyst_tag": "Guidance Cut", "reason": "Cut FY guide."},
        "sources": [
            {"title": "Grounded", "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/z"},
            {"title": "FMP article", "url": "https://reuters.com/x", "publisher": "Reuters"},
        ],
    }, market_active=False)
    assert card is not None and "price_move" in card and card["price_move"] is None
    assert card["sources"] == [
        {"title": "FMP article", "url": "https://reuters.com/x", "publisher": "Reuters"},
    ]


@pytest.mark.parametrize("url, kept", [
    ("https://vertexaisearch.cloud.google.com/grounding-api-redirect/AbC123", False),
    ("https://VERTEXAISEARCH.cloud.google.com/grounding-api-redirect/x", False),
    ("https://eu.vertexaisearch.cloud.google.com/grounding-api-redirect/x", False),  # subdomain
    (" https://vertexaisearch.cloud.google.com/r/x ", False),   # padded: stripped first
    ("https://reuters.com/markets/a", True),
    ("https://vertexaisearch.cloud.google.com.evil.example/x", True),  # look-alike host
    ("https://notvertexaisearch.cloud.google.com/x", True),     # suffix without the dot
    ("http://[::1", True),                                      # urlparse ValueError → kept
    ("https://example.com/?u=vertexaisearch.cloud.google.com", True),   # host, not substring
    ("not a url", True),
    ("", True),
])
def test_sanitize_sources_drops_grounding_redirect_links_on_write_and_read(url, kept):
    from app.services.news_insight_service import _sanitize_sources
    out = _sanitize_sources([{"title": "Reuters", "url": url}]) or []
    assert (len(out) == 1) is kept
    # Idempotent on read-back: a stored mixed list keeps only the article link.
    mixed = _sanitize_sources([
        {"title": "Grounded", "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/z"},
        {"title": "FMP article", "url": "https://reuters.com/x"},
    ])
    assert mixed == [{"title": "FMP article", "url": "https://reuters.com/x"}]
