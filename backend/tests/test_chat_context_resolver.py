"""
Unit tests for ChatContextResolver (backend context orchestration).

The resolver turns {context_type, reference_id} into a compact grounding block
by reading ALREADY-CACHED services. It must:
  * never raise — a miss / failure degrades to the client context (or None),
  * pass BOOK / unknown / NONE straight through to the client context,
  * defer STOCK to chat_service (returns None so stock_id enrichment runs),
  * extract the right fields on a hit.

No network / Supabase — the cached services are monkeypatched. Each branch does
a lazy import inside the resolver, so patching the module attribute before the
call takes effect.
"""

from types import SimpleNamespace

import pytest

from app.services.chat_context_resolver import (
    ChatContextResolver,
    get_chat_context_resolver,
)


@pytest.fixture
def resolver() -> ChatContextResolver:
    return ChatContextResolver()


# ── Pass-through / no-context branches ──────────────────────────────

@pytest.mark.asyncio
async def test_none_context_returns_client_context(resolver):
    assert await resolver.resolve(None, None, "cc") == "cc"
    assert await resolver.resolve("NONE", "x", "cc") == "cc"
    assert await resolver.resolve("", "x", "cc") == "cc"
    assert await resolver.resolve("general", "x", "cc") == "cc"


@pytest.mark.asyncio
async def test_book_passes_client_context_through(resolver):
    ctx = 'The user is reading the book "The Psychology of Money" by Morgan Housel.'
    assert await resolver.resolve("BOOK", "3", ctx) == ctx
    # Case-insensitive on the type.
    assert await resolver.resolve("book", "3", ctx) == ctx


@pytest.mark.asyncio
async def test_unknown_type_passes_through_and_never_raises(resolver):
    assert await resolver.resolve("WHAT_IS_THIS", "x", "cc") == "cc"
    assert await resolver.resolve("WHAT_IS_THIS", "x", None) is None


@pytest.mark.asyncio
async def test_stock_defers_to_none(resolver):
    # STOCK returns None so chat_service's stock_id enrichment runs; resolve()
    # then returns the client context (None here).
    assert await resolver.resolve("STOCK", "AAPL", None) is None
    assert await resolver.resolve("STOCK", "AAPL", "cc") == "cc"


# ── TICKER_REPORT ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ticker_report_hit_extracts_summary_score_thesis(resolver, monkeypatch):
    async def fake_get(ticker, persona):
        assert ticker == "AAPL" and persona == "warren_buffett"
        return {
            "company_name": "Apple Inc.",
            # quality_score is a 0-100 value (persona_scoring clamps to [0,100]).
            "quality_score": 72.0,
            "executive_summary_text": "Apple is a high-quality compounder with durable margins.",
            "core_thesis": {"bull_case": ["Durable ecosystem moat"], "bear_case": ["Valuation is rich"]},
        }

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    block = await resolver.resolve("TICKER_REPORT", "AAPL|warren_buffett", None)
    assert block is not None
    assert "Apple Inc." in block
    # Must be labeled /100 (the real scale) — a /10 label poisons the grounding.
    assert "72/100" in block
    assert "/10." not in block, f"score must not be rendered on a /10 scale: {block!r}"
    assert "high-quality compounder" in block
    assert "Durable ecosystem moat" in block
    assert "Valuation is rich" in block


@pytest.mark.asyncio
async def test_ticker_report_score_boundaries_render_on_100_scale(resolver, monkeypatch):
    """Outlier scores (0, 100, mid) all render on /100, never /10."""
    for score in (0.0, 50.5, 100.0):
        async def fake_get(ticker, persona, _s=score):
            return {"company_name": "X", "quality_score": _s, "executive_summary_text": "s"}

        import app.services.ticker_report_cache as trc
        monkeypatch.setattr(trc, "get_cached_report", fake_get)
        block = await resolver.resolve("TICKER_REPORT", "X|warren_buffett", None)
        assert f"{score:.0f}/100" in block
        assert "/10." not in block


@pytest.mark.asyncio
async def test_ticker_report_agent_tag_maps_to_full_persona_key(resolver, monkeypatch):
    seen = {}

    async def fake_get(ticker, persona):
        seen["ticker"] = ticker
        seen["persona"] = persona
        return {"company_name": "Microsoft", "executive_summary_text": "solid."}

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    await resolver.resolve("TICKER_REPORT", "msft|buffett", None)
    assert seen["ticker"] == "MSFT"          # uppercased
    assert seen["persona"] == "warren_buffett"  # agent_tag → full key


@pytest.mark.asyncio
async def test_ticker_report_missing_persona_defaults(resolver, monkeypatch):
    seen = {}

    async def fake_get(ticker, persona):
        seen["persona"] = persona
        return {"company_name": "X", "executive_summary_text": "s"}

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    await resolver.resolve("TICKER_REPORT", "TSLA", None)  # no "|persona"
    assert seen["persona"] == "warren_buffett"


@pytest.mark.asyncio
async def test_ticker_report_cache_miss_returns_none(resolver, monkeypatch):
    async def fake_get(ticker, persona):
        return None

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    assert await resolver.resolve("TICKER_REPORT", "AAPL|warren_buffett", None) is None


@pytest.mark.asyncio
async def test_ticker_report_service_error_degrades_to_client_context(resolver, monkeypatch):
    async def boom(ticker, persona):
        raise RuntimeError("db down")

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", boom)

    assert await resolver.resolve("TICKER_REPORT", "AAPL|warren_buffett", "cc") == "cc"


@pytest.mark.asyncio
async def test_ticker_report_empty_ref_returns_none(resolver):
    assert await resolver.resolve("TICKER_REPORT", "", "cc") == "cc"  # no ticker → None → client ctx
    assert await resolver.resolve("TICKER_REPORT", "", None) is None


@pytest.mark.asyncio
async def test_ticker_report_summary_is_capped(resolver, monkeypatch):
    long_summary = "word " * 400  # ~2000 chars

    async def fake_get(ticker, persona):
        return {"company_name": "X", "executive_summary_text": long_summary}

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    block = await resolver.resolve("TICKER_REPORT", "X|warren_buffett", None)
    # The summary portion must be bounded (cap 800 + framing), not the full 2000.
    assert len(block) < 1400


@pytest.mark.asyncio
async def test_ticker_report_grounds_recent_price_movement(resolver, monkeypatch):
    """The reported grounding gap: the on-screen 'Recent Price Movement' insight
    (price_action.narrative) must reach the grounding so a 'why did the price move?' answer can cite
    the real reason (e.g. semiconductor oversupply) instead of restating raw price numbers."""
    async def fake_get(ticker, persona):
        return {
            "company_name": "SanDisk",
            "executive_summary_text": "A memory maker.",
            "price_action": {
                "narrative": "Fell on broader semiconductor oversupply fears following TSMC's earnings.",
                "change_pct": -24.1,
                "window_label": "Last 7 Days",
                "tag": "Semiconductor Sector Concerns",
            },
        }

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    block = await resolver.resolve("TICKER_REPORT", "SNDK|warren_buffett", None)
    assert "Recent price movement" in block
    assert "-24.1% over Last 7 Days" in block
    assert "Semiconductor Sector Concerns" in block
    assert "semiconductor oversupply fears following TSMC's earnings" in block


@pytest.mark.asyncio
async def test_ticker_report_grounds_module_insights(resolver, monkeypatch):
    """The other visible module insights ground the chat too (moat, revenue, ownership, Wall Street,
    forward outlook, earnings track record) — each on its own labeled line."""
    async def fake_get(ticker, persona):
        return {
            "company_name": "X",
            "executive_summary_text": "s",
            "revenue_forecast": {"insight": "Growth reaccelerates on AI demand.", "beat_summary": "Beat 6 of 8"},
            "revenue_engine": {"analysis_note": "Cloud is now the largest segment."},
            "moat_competition": {"competitive_insight": "Switching costs anchor the moat."},
            "key_management": {"ownership_insight": "Founder-led with high insider ownership."},
            "wall_street_consensus": {"wall_street_insight": "Analysts see modest upside to consensus."},
        }

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    block = await resolver.resolve("TICKER_REPORT", "X|warren_buffett", None)
    assert "Forward outlook: Growth reaccelerates on AI demand." in block
    assert "Earnings track record: Beat 6 of 8" in block
    assert "Revenue mix: Cloud is now the largest segment." in block
    assert "Moat: Switching costs anchor the moat." in block
    assert "Ownership: Founder-led with high insider ownership." in block
    assert "Wall Street view: Analysts see modest upside to consensus." in block


@pytest.mark.asyncio
async def test_ticker_report_module_outliers_never_crash_or_leak_none(resolver, monkeypatch):
    """Absent / non-dict / null-field / empty modules are skipped silently — the base block still
    returns, a single malformed module never drops the others, and 'None' never leaks into the text."""
    async def fake_get(ticker, persona):
        return {
            "company_name": "X",
            "executive_summary_text": "base summary.",
            "price_action": "oops-not-a-dict",                            # malformed → skipped
            "revenue_engine": {"analysis_note": None},                    # null field → skipped
            "moat_competition": {"competitive_insight": ""},              # empty → skipped
            "wall_street_consensus": {"wall_street_insight": "Real analyst view."},  # valid → survives
        }

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    block = await resolver.resolve("TICKER_REPORT", "X|warren_buffett", None)
    assert block is not None
    assert "base summary." in block
    assert "None" not in block                              # no null leaked into grounding
    assert "Recent price movement" not in block             # malformed price_action skipped
    assert "Wall Street view: Real analyst view." in block  # a valid module still survived the bad one


@pytest.mark.asyncio
async def test_ticker_report_price_action_nan_change_still_grounds_narrative(resolver, monkeypatch):
    """A NaN change_pct must be dropped from the framing (not rendered), while the narrative + tag
    still ground the answer."""
    async def fake_get(ticker, persona):
        return {
            "company_name": "X",
            "price_action": {"narrative": "Moved on news.", "change_pct": float("nan"), "tag": "Catalyst"},
        }

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    block = await resolver.resolve("TICKER_REPORT", "X|warren_buffett", None)
    assert "Recent price movement (Catalyst):" in block
    assert "Moved on news." in block
    assert "nan" not in block.lower()   # NaN change never rendered


# ── ETF ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_etf_hit_extracts_price_and_stats(resolver, monkeypatch):
    class _Item:
        def __init__(self, label, value):
            self.label, self.value = label, value

    class _Prof:
        index_tracked = "S&P 500 Index"

    class _Detail:
        name = "SPDR S&P 500 ETF"
        symbol = "SPY"
        current_price = 500.12
        price_change_percent = 0.53
        key_statistics = [_Item("Expense Ratio", "0.09%"), _Item("AUM", "$500B")]
        etf_profile = _Prof()

    class _Svc:
        async def get_etf_detail(self, symbol):
            assert symbol == "SPY"
            return _Detail()

    import app.services.etf_service as es
    monkeypatch.setattr(es, "get_etf_service", lambda: _Svc())

    block = await resolver.resolve("ETF", "spy", None)
    assert block is not None
    assert "SPDR S&P 500 ETF" in block
    assert "0.09%" in block
    assert "S&P 500 Index" in block


@pytest.mark.asyncio
async def test_etf_empty_symbol_returns_none(resolver):
    assert await resolver.resolve("ETF", "", None) is None


@pytest.mark.asyncio
async def test_resolve_times_out_on_slow_recompute_and_degrades(resolver, monkeypatch):
    """A cold ETF/crypto/index detail recompute must NOT stall the first token:
    the resolve timeout bounds it and degrades to the client context."""
    import asyncio as _a
    import app.services.chat_context_resolver as ccr
    import app.services.etf_service as es

    monkeypatch.setattr(ccr, "_RESOLVE_TIMEOUT_SECONDS", 0.05)

    class _SlowSvc:
        async def get_etf_detail(self, symbol):
            await _a.sleep(1.0)  # far exceeds the 0.05s bound
            raise AssertionError("should have been cancelled by the timeout")

    monkeypatch.setattr(es, "get_etf_service", lambda: _SlowSvc())

    block = await resolver.resolve("ETF", "SPY", "fallback ctx")
    assert block == "fallback ctx"


# ── CRYPTO ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crypto_hit_extracts_stats_from_groups(resolver, monkeypatch):
    class _Item:
        def __init__(self, label, value):
            self.label, self.value = label, value

    class _Group:
        def __init__(self, stats):
            self.statistics = stats

    class _Prof:
        blockchain = "Bitcoin"

    class _Detail:
        name = "Bitcoin"
        symbol = "BTC"
        current_price = 65000.0
        price_change_percent = -1.2
        key_statistics_groups = [_Group([_Item("Market Cap", "$1.2T"), _Item("Volume 24h", "$30B")])]
        crypto_profile = _Prof()

    class _Svc:
        async def get_crypto_detail(self, symbol):
            return _Detail()

    import app.services.crypto_service as cs
    monkeypatch.setattr(cs, "get_crypto_service", lambda: _Svc())

    block = await resolver.resolve("CRYPTO", "btc", None)
    assert block is not None
    assert "Bitcoin" in block
    assert "$1.2T" in block


# ── MONEY_MOVES_ARTICLE ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_money_move_hit_extracts_title_author_highlights(resolver, monkeypatch):
    # keyHighlights are {icon,title,description} dicts (the real served shape) —
    # the resolver must extract human text, NOT str() the dict.
    class _Resp:
        articles = [
            {
                "slug": "compound-interest",
                "title": "The Power of Compounding",
                "subtitle": "Small amounts, big results",
                "author": {"name": "Jane Doe"},
                "keyHighlights": [
                    {"icon": "clock.fill", "title": "Start Early", "description": "Time is the biggest lever"},
                    {"icon": "arrow.up", "title": "Stay Consistent", "description": "Automate contributions"},
                ],
            },
            {"slug": "other", "title": "Other Article"},
        ]

    class _Svc:
        async def get_money_moves(self):
            return _Resp()

    import app.services.money_moves_content_service as mm
    monkeypatch.setattr(mm, "get_money_moves_content_service", lambda: _Svc())

    block = await resolver.resolve("MONEY_MOVES_ARTICLE", "compound-interest", None)
    assert block is not None
    assert "The Power of Compounding" in block
    assert "Jane Doe" in block
    # Human title + description extracted; NO raw dict/SF-symbol leakage.
    assert "Start Early" in block
    assert "Time is the biggest lever" in block
    assert "clock.fill" not in block
    assert "'icon'" not in block and "{" not in block


@pytest.mark.asyncio
async def test_money_move_highlights_tolerate_string_and_malformed(resolver, monkeypatch):
    """Outlier: string highlights (legacy) and empty/None entries degrade cleanly."""
    class _Resp:
        articles = [{
            "slug": "s", "title": "T", "author": "",
            "keyHighlights": ["plain string", {"title": "OnlyTitle"}, {"description": "OnlyDesc"}, {}, None],
        }]

    class _Svc:
        async def get_money_moves(self):
            return _Resp()

    import app.services.money_moves_content_service as mm
    monkeypatch.setattr(mm, "get_money_moves_content_service", lambda: _Svc())

    block = await resolver.resolve("MONEY_MOVES_ARTICLE", "s", None)
    assert block is not None
    assert "plain string" in block and "OnlyTitle" in block and "OnlyDesc" in block
    assert "{" not in block and "None" not in block


@pytest.mark.asyncio
async def test_money_move_unknown_slug_returns_none(resolver, monkeypatch):
    class _Resp:
        articles = [{"slug": "a", "title": "A"}]

    class _Svc:
        async def get_money_moves(self):
            return _Resp()

    import app.services.money_moves_content_service as mm
    monkeypatch.setattr(mm, "get_money_moves_content_service", lambda: _Svc())

    assert await resolver.resolve("MONEY_MOVES_ARTICLE", "missing", None) is None


# ── Enriched grounding: asset details (ETF / CRYPTO / INDEX) ────────

@pytest.mark.asyncio
async def test_etf_grounds_holdings_dividend_sectors_performance(resolver, monkeypatch):
    detail = SimpleNamespace(
        name="Vanguard S&P 500", symbol="VOO", current_price=500.0, price_change_percent=0.5,
        key_statistics=[SimpleNamespace(label="AUM", value="$400B")],
        etf_profile=SimpleNamespace(description="Tracks the S&P 500 index of US large caps.", index_tracked="S&P 500"),
        net_yield=SimpleNamespace(
            dividend_yield=1.30, expense_ratio=0.03, pay_frequency="Quarterly",
            last_dividend_payment=SimpleNamespace(dividend_per_share="$1.77", pay_date="Jan 31, 2026"),
        ),
        holdings_risk=SimpleNamespace(
            top_holdings=[SimpleNamespace(symbol="AAPL", name="Apple", weight=7.1),
                          SimpleNamespace(symbol="MSFT", name="Microsoft", weight=6.5)],
            top_sectors=[SimpleNamespace(name="Technology", weight=30.2)],
            asset_allocation=SimpleNamespace(equities=99.0, bonds=0.0, commodities=0.0, cash=1.0, total_assets="$400B"),
            concentration=SimpleNamespace(top_n=10, weight=32.0, insight="Top 10 holdings are ~a third of the fund."),
        ),
        performance_periods=[SimpleNamespace(label="1Y", change_percent=12.3)],
        benchmark_summary=SimpleNamespace(avg_annual_return=13.1, sp_benchmark=13.1),
        identity_rating=SimpleNamespace(volatility_label="Moderate Volatility"),
        strategy=SimpleNamespace(hook="Cheap, broad, passive exposure to US large caps.", tags=["Passive", "Index"]),
    )

    class _Svc:
        async def get_etf_detail(self, s):
            return detail

    import app.services.etf_service as es
    monkeypatch.setattr(es, "get_etf_service", lambda: _Svc())

    block = await resolver.resolve("ETF", "voo", None)
    assert "dividend yield 1.30% (paid quarterly)" in block
    assert "expense ratio 0.03%" in block
    assert "AAPL 7.1%" in block and "MSFT 6.5%" in block
    assert "Technology 30.2%" in block
    assert "1Y +12.3%" in block
    assert "Tracks the S&P 500 index" in block
    # The secondary fields fold in too.
    assert "equities 99%" in block and "AUM $400B" in block
    assert "Top 10 holdings are ~a third" in block
    assert "Last dividend $1.77 paid Jan 31, 2026" in block
    assert "avg annual return 13.1% vs S&P 13.1%" in block
    assert "Volatility: Moderate Volatility" in block
    assert "Strategy: Cheap, broad, passive" in block


@pytest.mark.asyncio
async def test_crypto_grounds_snapshots_and_description(resolver, monkeypatch):
    detail = SimpleNamespace(
        name="Ethereum", symbol="ETH", current_price=3000.0, price_change_percent=-1.2,
        crypto_profile=SimpleNamespace(description="A programmable blockchain for smart contracts.",
                                       blockchain="Ethereum", consensus_mechanism="Proof of Stake",
                                       launch_date="Jul 2015"),
        snapshots=[
            SimpleNamespace(category="Tokenomics", paragraphs=["ETH has no fixed max supply; issuance is offset by EIP-1559 burns."]),
            SimpleNamespace(category="Risks", paragraphs=["Regulatory classification and L2 competition are key risks."]),
        ],
        key_statistics_groups=[SimpleNamespace(statistics=[SimpleNamespace(label="Market Cap", value="$360B")])],
        performance_periods=[SimpleNamespace(label="1Y", change_percent=42.0, benchmark_label="BTC")],
    )

    class _Svc:
        async def get_crypto_detail(self, s):
            return detail

    import app.services.crypto_service as cs
    monkeypatch.setattr(cs, "get_crypto_service", lambda: _Svc())

    block = await resolver.resolve("CRYPTO", "eth", None)
    assert "programmable blockchain for smart contracts" in block
    assert "Tokenomics:" in block and "EIP-1559 burns" in block
    assert "Risks:" in block and "L2 competition" in block
    assert "Market Cap: $360B" in block
    assert "consensus Proof of Stake" in block
    assert "launched Jul 2015" in block
    assert "Performance (vs BTC) — 1Y +42.0%" in block


@pytest.mark.asyncio
async def test_index_grounds_name_price_sectors_macro(resolver, monkeypatch):
    snap = SimpleNamespace(
        valuation=SimpleNamespace(pe_ratio=21.0, forward_pe=18.5, earnings_yield=4.7,
                                  historical_avg_pe=17.5, historical_period="10Y"),
        sector_performance=SimpleNamespace(sectors=[SimpleNamespace(sector="Technology", change_percent=0.8),
                                                    SimpleNamespace(sector="Energy", change_percent=-0.4)]),
        macro_forecast=SimpleNamespace(indicators=[SimpleNamespace(title="Inflation", description="cooling", signal="neutral")]),
    )
    detail = SimpleNamespace(index_name="S&P 500", current_price=5200.0, price_change_percent=0.3,
                             index_profile=SimpleNamespace(description="500 large-cap US stocks.",
                                                           number_of_constituents=500,
                                                           weighting_methodology="Market-cap",
                                                           index_provider="S&P Dow Jones"),
                             performance_periods=[SimpleNamespace(label="1Y", change_percent=11.0)],
                             snapshots_data=snap)

    class _Svc:
        async def get_index_detail(self, s):
            return detail

    import app.services.index_service as ixs
    monkeypatch.setattr(ixs, "get_index_service", lambda: _Svc())

    block = await resolver.resolve("INDEX", "^GSPC", None)
    assert "S&P 500" in block
    assert "Level 5,200.00 (+0.30%)" in block
    assert "P/E 21.0 (fwd 18.5)" in block
    assert "10Y avg P/E 17.5" in block
    assert "Technology +0.8%" in block and "Energy -0.4%" in block
    assert "Inflation: neutral" in block
    assert "500 large-cap US stocks" in block
    assert "500 constituents" in block and "Market-cap-weighted" in block
    assert "Performance — 1Y +11.0%" in block


@pytest.mark.asyncio
async def test_asset_detail_missing_enrichment_fields_no_crash(resolver, monkeypatch):
    """A bare detail (only price/stats, none of the new fields) still grounds the basics without a crash."""
    detail = SimpleNamespace(name="X", symbol="X", current_price=1.0, price_change_percent=0.0,
                             key_statistics=[SimpleNamespace(label="AUM", value="$1B")],
                             etf_profile=SimpleNamespace(index_tracked="Idx"))

    class _Svc:
        async def get_etf_detail(self, s):
            return detail

    import app.services.etf_service as es
    monkeypatch.setattr(es, "get_etf_service", lambda: _Svc())

    block = await resolver.resolve("ETF", "x", None)
    assert block is not None and "AUM: $1B" in block and "Tracks: Idx" in block
    assert "None" not in block


# ── Enriched grounding: COMMODITY (bundled profile appended to iOS context) ──

@pytest.mark.asyncio
async def test_commodity_appends_bundled_profile_to_client_context(resolver, monkeypatch):
    import app.services.commodity_service as cms
    monkeypatch.setattr(cms, "_get_meta", lambda s: {
        "description": "Gold is a safe-haven precious metal.",
        "major_producers": "China, Australia, Russia",
        "major_consumers": "China, India, USA",
        "category": "metals", "exchange": "COMEX",
        "trading_hours": "Sun–Fri 6PM–5PM ET", "contract_size": "100 troy ounces",
    })
    client = "COMMODITY CONTEXT: Symbol GCUSD, Price $2000."
    block = await resolver.resolve("COMMODITY", "GCUSD", client)
    assert client in block                                  # the iOS context is preserved, not replaced
    assert "Commodity profile —" in block
    assert "safe-haven precious metal" in block
    assert "Major producers: China, Australia, Russia." in block
    assert "Major consumers: China, India, USA." in block
    assert "trades on COMEX" in block and "contract 100 troy ounces" in block


@pytest.mark.asyncio
async def test_commodity_unknown_symbol_degrades_to_client_context(resolver, monkeypatch):
    import app.services.commodity_service as cms
    monkeypatch.setattr(cms, "_get_meta", lambda s: {})     # unknown symbol → empty meta
    assert await resolver.resolve("COMMODITY", "ZZUSD", "cc") == "cc"
    assert await resolver.resolve("COMMODITY", "ZZUSD", None) is None


# ── Enriched grounding: Learn content bodies (Money Moves + Journey) ──

@pytest.mark.asyncio
async def test_money_move_grounds_article_body(resolver, monkeypatch):
    resp = SimpleNamespace(articles=[{
        "slug": "compounding", "title": "The Magic of Compounding", "subtitle": "Small sums, big time.",
        "author": {"name": "Jane"}, "keyHighlights": [{"title": "Start early", "description": "time is the lever"}],
        "statistics": [{"value": "8%", "label": "Avg annual return", "trend": "up", "trendValue": "x"}],
        "sections": [
            {"title": "Why it works", "content": [
                {"type": "paragraph", "text": "Compounding reinvests returns so growth accelerates."},
                {"type": "bulletList", "items": ["Reinvest dividends", "Avoid interrupting the curve"]},
                {"type": "quote", "text": "Compound interest is the eighth wonder.", "attribution": "Einstein"},
            ]},
        ],
    }])

    class _Svc:
        async def get_money_moves(self):
            return resp

    import app.services.money_moves_content_service as mm
    monkeypatch.setattr(mm, "get_money_moves_content_service", lambda: _Svc())

    block = await resolver.resolve("MONEY_MOVES_ARTICLE", "compounding", None)
    assert "Article content:" in block
    assert "Compounding reinvests returns" in block
    assert "Reinvest dividends" in block                    # bulletList items
    assert "eighth wonder" in block and "Einstein" in block  # quote + attribution
    assert "Key figures: Avg annual return: 8%" in block     # stat callouts


@pytest.mark.asyncio
async def test_money_move_malformed_sections_no_crash(resolver, monkeypatch):
    resp = SimpleNamespace(articles=[{"slug": "x", "title": "T", "sections": "not-a-list"}])

    class _Svc:
        async def get_money_moves(self):
            return resp

    import app.services.money_moves_content_service as mm
    monkeypatch.setattr(mm, "get_money_moves_content_service", lambda: _Svc())

    block = await resolver.resolve("MONEY_MOVES_ARTICLE", "x", None)
    assert block is not None                                 # base block still returns
    assert "Article content" not in block                   # malformed sections skipped
    assert "None" not in block


@pytest.mark.asyncio
async def test_journey_grounds_lesson_body_and_strips_markup(resolver, monkeypatch):
    lesson = SimpleNamespace(id="1", title="Risk 101", description="Intro to risk.",
                             story_content={"cards": [
                                 {"type": "title", "headline": "Risk 101", "text": "Risk is the chance of loss."},
                                 {"type": "content", "headline": None, "text": "Diversification **reduces** unsystematic risk."},
                             ]})
    resp = SimpleNamespace(lessons=[lesson])

    class _Svc:
        async def get_journey(self):
            return resp

    import app.services.journey_content_service as jc
    monkeypatch.setattr(jc, "get_journey_content_service", lambda: _Svc())

    block = await resolver.resolve("JOURNEY_LESSON", "1", None)
    assert "Lesson content:" in block
    assert "Risk is the chance of loss." in block
    assert "Diversification reduces unsystematic risk." in block   # ** markup stripped
    assert "**" not in block


@pytest.mark.asyncio
async def test_journey_null_story_content_no_crash(resolver, monkeypatch):
    lesson = SimpleNamespace(id="2", title="L2", description="d", story_content=None)  # Optional → None
    resp = SimpleNamespace(lessons=[lesson])

    class _Svc:
        async def get_journey(self):
            return resp

    import app.services.journey_content_service as jc
    monkeypatch.setattr(jc, "get_journey_content_service", lambda: _Svc())

    block = await resolver.resolve("JOURNEY_LESSON", "2", None)
    assert block is not None
    assert "Lesson content" not in block
    assert "None" not in block


# ── Singleton ───────────────────────────────────────────────────────

def test_singleton_is_stable():
    assert get_chat_context_resolver() is get_chat_context_resolver()
