"""The sweeper's gated "why did it move" catalyst fetch (P4).

The grounded web-search catalyst is expensive, so it is fetched ONLY for a
per-ticker Unusual/Extreme move, is kill-switchable, and is bounded by a per-ET-day
cap. A None result (hard failure / no clear catalyst path) yields no price_move and
must never block the news card. No network — get_catalyst is stubbed.
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
from app.services.updates_insight_sweeper import (
    InsightSweeper,
    MARKET_SCOPE,
    _CATALYST_DAILY_CAP,
)
from app.services.updates_materiality import (
    ACTION_GENERATE,
    ACTION_SKIP,
    BAND_EXTREME,
    BAND_NOTABLE,
    TIER_EXTREME,
    TIER_NOTABLE,
    TIER_TYPICAL,
    TIER_UNUSUAL,
    Decision,
)

NOW = datetime(2026, 7, 21, 18, 0, tzinfo=timezone.utc)


def _sweeper() -> InsightSweeper:
    s = object.__new__(InsightSweeper)   # bypass __init__ (no DB/FMP clients)
    s._catalyst_day = None
    s._catalyst_count = 0
    s._catalyst_scopes = set()
    return s


def _dec(tier: str) -> Decision:
    return Decision(action=ACTION_GENERATE, reason="x", price_band=tier)


class _StubCatalyst:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def get_catalyst(self, ticker, change_pct, window_label, *, company_name=None):
        self.calls += 1
        self.company_name = company_name
        return self.result


@pytest.fixture
def stub(monkeypatch):
    st = _StubCatalyst(
        {"tag": "Analyst Downgrade", "reason": "Cut to Underweight.", "sources": [{"uri": "x"}]}
    )
    monkeypatch.setattr(mod, "get_price_catalyst_service", lambda: st)
    monkeypatch.setattr(mod.settings, "PRICE_CATALYST_AI_ENABLED", True)
    return st


@pytest.mark.asyncio
async def test_catalyst_only_for_big_per_ticker_moves(stub):
    s = _sweeper()
    q = {"changePercentage": -8.2}

    pm = await s._maybe_price_move("AAPL", _dec(TIER_EXTREME), NOW, q)
    assert pm == {
        "tier": "Extreme", "change_pct": -8.2,
        "catalyst_tag": "Analyst Downgrade", "reason": "Cut to Underweight.",
        # The grounded web sources are carried through raw for the caller to merge
        # into the card's `sources`; `_sanitize_price_move` strips this key before
        # the `price_move` block is stored.
        "web_sources": [{"uri": "x"}],
    }
    assert await s._maybe_price_move("AAPL", _dec(TIER_UNUSUAL), NOW, q) is not None
    # Not big enough → no paid search.
    assert await s._maybe_price_move("AAPL", _dec(TIER_NOTABLE), NOW, q) is None
    assert await s._maybe_price_move("AAPL", _dec(TIER_TYPICAL), NOW, q) is None
    # Market scope keeps its news summary — no per-ticker catalyst.
    assert await s._maybe_price_move(MARKET_SCOPE, _dec(TIER_EXTREME), NOW, q) is None


@pytest.mark.asyncio
async def test_kill_switch_skips_the_search(stub, monkeypatch):
    monkeypatch.setattr(mod.settings, "PRICE_CATALYST_AI_ENABLED", False)
    s = _sweeper()
    assert await s._maybe_price_move("AAPL", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.0}) is None
    assert stub.calls == 0


@pytest.mark.asyncio
async def test_none_result_yields_no_price_move(monkeypatch):
    st = _StubCatalyst(None)   # hard failure / no result
    monkeypatch.setattr(mod, "get_price_catalyst_service", lambda: st)
    monkeypatch.setattr(mod.settings, "PRICE_CATALYST_AI_ENABLED", True)
    s = _sweeper()
    assert await s._maybe_price_move("AAPL", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.0}) is None


@pytest.mark.asyncio
async def test_day_cap_bounds_calls_and_resets_next_day(stub):
    s = _sweeper()
    for i in range(_CATALYST_DAILY_CAP):
        assert await s._maybe_price_move(f"T{i}", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.0}) is not None
    # One past the cap → skipped, and no further search fired.
    calls_at_cap = stub.calls
    assert await s._maybe_price_move("OVER", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.0}) is None
    assert stub.calls == calls_at_cap
    # A new ET trading day resets the budget.
    next_day = NOW + timedelta(days=1)
    assert await s._maybe_price_move("NEWDAY", _dec(TIER_EXTREME), next_day, {"changePercentage": -8.0}) is not None


@pytest.mark.asyncio
async def test_day_cap_counts_distinct_movers_not_attempts(stub):
    # A single churning ticker re-tripping many times must consume exactly ONE
    # unit — otherwise it starves genuinely-new movers of their catalyst.
    s = _sweeper()
    for _ in range(5):
        assert await s._maybe_price_move("AAPL", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.0}) is not None
    assert s._catalyst_count == 1
    assert s._catalyst_scopes == {"AAPL"}


@pytest.mark.asyncio
async def test_no_catalyst_for_a_phantom_zero_move(stub):
    # When the current quote is unusable the gate can carry a STALE σ-tier; we must
    # NOT fetch a paid catalyst for a "+0.0%" move nor store a self-contradictory
    # {tier:Unusual, change_percent:0.0} block.
    s = _sweeper()
    for bad in (None, float("nan"), float("inf"), {}, {"changePercentage": 0.004}):
        q = bad if isinstance(bad, dict) else {"changePercentage": bad}
        assert await s._maybe_price_move("AAPL", _dec(TIER_EXTREME), NOW, q) is None
    assert stub.calls == 0          # no paid search fired
    assert s._catalyst_count == 0   # no budget consumed on a phantom move


@pytest.mark.asyncio
async def test_fixed_band_extreme_still_gets_a_catalyst(stub):
    # A thin-history / newly-listed name (σ unavailable → fallback band 'extreme')
    # is the population most prone to violent moves; it must get the catalyst too,
    # with the tier normalised to the capitalized vocabulary.
    s = _sweeper()
    pm = await s._maybe_price_move("NEWCO", _dec(BAND_EXTREME), NOW, {"changePercentage": -15.0})
    assert pm is not None
    assert pm["tier"] == "Extreme"          # normalised from fixed-band 'extreme'
    assert pm["change_pct"] == -15.0
    # The softer fixed-band 'notable' is NOT enough (matches the σ-path gate).
    assert await s._maybe_price_move("NEWCO2", _dec(BAND_NOTABLE), NOW, {"changePercentage": -6.0}) is None


# ── _store: preserve vs clear the price_move column ───────────────────────

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


def test_store_preserves_existing_block_when_move_still_big_but_no_new_catalyst():
    # preserve=True + no new block → OMIT the column so an existing, still-valid
    # "why it moved" is kept on conflict rather than wiped to NULL.
    svc = _insight_service()
    assert svc._store("AAPL", _CARD, "iid", "reason", 3, True, None, True)
    row = svc.supabase.rows[-1]
    assert "price_move" not in row


def test_store_clears_the_block_when_the_move_is_no_longer_big():
    # preserve=False + no new block → explicitly write NULL (clear a now-stale block).
    svc = _insight_service()
    assert svc._store("AAPL", _CARD, "iid", "reason", 3, True, None, False)
    row = svc.supabase.rows[-1]
    assert "price_move" in row and row["price_move"] is None


def test_store_writes_a_new_block_even_when_preserve_is_set():
    svc = _insight_service()
    pm = {"tier": "Extreme", "change_pct": -8.2, "catalyst_tag": "Guidance Cut", "reason": "Cut FY guide."}
    assert svc._store("AAPL", _CARD, "iid", "reason", 3, True, pm, True)
    row = svc.supabase.rows[-1]
    assert row["price_move"] == {
        "tier": "Extreme", "change_percent": -8.2,
        "catalyst_tag": "Guidance Cut", "reason": "Cut FY guide.",
    }


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
    assert svc._store("AAPL", _CARD, "iid", "reason", 3, True, None, False, sources)
    row = svc.supabase.rows[-1]
    assert row["sources"] == [{"title": "Fed holds rates", "url": "https://x/1"}]


def test_store_writes_null_sources_when_none():
    svc = _insight_service()
    assert svc._store("AAPL", _CARD, "iid", "reason", 3, True, None, False, None)
    assert svc.supabase.rows[-1]["sources"] is None


@pytest.mark.asyncio
async def test_the_sweeper_hands_the_listed_name_to_the_grounded_search(stub):
    """A starred LTC Properties (REIT) on an Extreme day: the paid search must be aimed at
    the security, not at the coin that shares its ticker. `price_service._shape` carries
    the name on the quote row; a row without one still searches (bare ticker)."""
    s = _sweeper()
    pm = await s._maybe_price_move(
        "LTC", _dec(TIER_EXTREME), NOW,
        {"changePercentage": -8.2, "name": "LTC Properties, Inc."},
    )
    assert pm is not None and stub.company_name == "LTC Properties, Inc."
    await s._maybe_price_move("LTC", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.2})
    assert stub.company_name is None


# ── F04-8: a search the breaker refused must not consume a distinct-mover slot ──
#
# On an open-breaker day every grounded attempt fails fast inside the process, and
# each one still burned one of the 30 daily slots — so the afternoon's movers, when
# the quota was back, got nothing.

from app.integrations import gemini as _gem
from app.services.price_catalyst_service import CatalystNotAttempted


class _RefusingCatalyst(_StubCatalyst):
    async def get_catalyst(self, ticker, change_pct, window_label, *, company_name=None):
        self.calls += 1
        raise CatalystNotAttempted("quota circuit open")


@pytest.fixture
def closed_breaker():
    _gem._quota_circuit.reset()
    yield
    _gem._quota_circuit.reset()


@pytest.mark.asyncio
async def test_a_refused_search_releases_its_day_cap_unit(monkeypatch, closed_breaker):
    st = _RefusingCatalyst(None)
    monkeypatch.setattr(mod, "get_price_catalyst_service", lambda: st)
    monkeypatch.setattr(mod.settings, "PRICE_CATALYST_AI_ENABLED", True)
    s = _sweeper()
    assert await s._maybe_price_move("AAPL", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.0}) is None
    assert st.calls == 1
    assert s._catalyst_count == 0, "nothing ran, nothing billed — the slot goes back"
    assert "AAPL" not in s._catalyst_scopes, "...and the scope may try again later today"


@pytest.mark.asyncio
async def test_an_attempted_but_unusable_search_keeps_its_unit(monkeypatch, closed_breaker):
    """A None return means the search RAN and answered unusably — Google billed it."""
    st = _StubCatalyst(None)
    monkeypatch.setattr(mod, "get_price_catalyst_service", lambda: st)
    monkeypatch.setattr(mod.settings, "PRICE_CATALYST_AI_ENABLED", True)
    s = _sweeper()
    assert await s._maybe_price_move("AAPL", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.0}) is None
    assert s._catalyst_count == 1 and "AAPL" in s._catalyst_scopes


@pytest.mark.asyncio
async def test_an_open_breaker_claims_no_unit_and_makes_no_call(monkeypatch, stub):
    monkeypatch.setattr(_gem.settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", 2)
    monkeypatch.setattr(_gem.settings, "GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS", 3600)
    _gem._quota_circuit.reset()
    try:
        _gem._quota_circuit.record_quota_error(); _gem._quota_circuit.record_quota_error()
        assert _gem._quota_circuit.tripped
        s = _sweeper()
        assert await s._maybe_price_move("AAPL", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.0}) is None
        assert stub.calls == 0
        assert s._catalyst_count == 0
    finally:
        _gem._quota_circuit.reset()


@pytest.mark.asyncio
async def test_a_refusal_for_a_scope_counted_earlier_today_keeps_that_unit(monkeypatch, closed_breaker):
    """The earlier search for this scope RAN (and was billed, gemini_error → not cached); a
    later refusal for the same scope must not hand back a unit it did not take."""
    ran = _StubCatalyst(None)
    monkeypatch.setattr(mod, "get_price_catalyst_service", lambda: ran)
    monkeypatch.setattr(mod.settings, "PRICE_CATALYST_AI_ENABLED", True)
    s = _sweeper()
    await s._maybe_price_move("AAPL", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.0})
    assert s._catalyst_count == 1
    refused = _RefusingCatalyst(None)
    monkeypatch.setattr(mod, "get_price_catalyst_service", lambda: refused)
    await s._maybe_price_move("AAPL", _dec(TIER_EXTREME), NOW, {"changePercentage": -8.0})
    assert s._catalyst_count == 1 and "AAPL" in s._catalyst_scopes


def test_release_ignores_a_day_rollover_between_claim_and_release():
    s = _sweeper()
    assert s._claim_catalyst_budget(NOW, "AAPL")
    next_day = NOW + timedelta(days=1)
    assert s._claim_catalyst_budget(next_day, "MSFT")        # the ledger rolled
    s._release_catalyst_budget(NOW, "AAPL")                  # yesterday's unit
    assert s._catalyst_count == 1 and s._catalyst_scopes == {"MSFT"}


# ── a prior session's move never buys a "today" search (TestFlight, 2026-09-15) ──
#
# Pre-market, before a ticker's first print, the batch row still carries YESTERDAY's
# whole-session change (stamped `changeSession` = yesterday). A "today" search for it
# would cache yesterday's cause under TODAY's key and serve it for a genuine
# same-direction move later in the day — and it would spend a day-cap unit on nothing.

@pytest.mark.asyncio
async def test_no_search_and_no_unit_for_a_prior_session_move(stub):
    s = _sweeper()
    stale = {"changePercentage": -13.3, "changeSession": "2026-07-20", "price": 371.47}
    assert await s._maybe_price_move("TER", _dec(TIER_EXTREME), NOW, stale) is None
    assert stub.calls == 0, "spent a grounded search on yesterday's move"
    assert "TER" not in s._catalyst_scopes and s._catalyst_count == 0


@pytest.mark.asyncio
async def test_a_current_session_move_still_searches(stub):
    s = _sweeper()
    live = {"changePercentage": -6.1, "changeSession": "2026-07-21", "price": 348.8}
    pm = await s._maybe_price_move("TER", _dec(TIER_EXTREME), NOW, live)
    assert pm is not None and pm["change_pct"] == -6.1
    assert stub.calls == 1


@pytest.mark.asyncio
async def test_an_unstamped_row_still_searches(stub):
    """Crypto and older shapes carry no stamp: fail open, as before."""
    s = _sweeper()
    assert await s._maybe_price_move("BTCUSD", _dec(TIER_EXTREME), NOW, {"changePercentage": 5.0}) is not None
    assert stub.calls == 1


# ── …and the SWEEP blanks such a row before the gate ever sees it ────────────────

@pytest.mark.asyncio
async def test_the_sweep_neutralises_prior_session_rows_at_the_choke_point(monkeypatch):
    """`decide()` (the materiality gate), the card prompt's price line, the catalyst and
    the alert all read the SAME `quotes_by_symbol`. The sweep blanks a prior-session
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
            self._catalyst_day = self._enrich_day = None
            self._catalyst_count = self._enrich_count = 0
            self._catalyst_scopes = set()

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
