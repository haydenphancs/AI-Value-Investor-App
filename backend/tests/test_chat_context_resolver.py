"""
Unit tests for ChatContextResolver (backend context orchestration) + its prune-then-dump serializer.

The resolver turns {context_type, reference_id} into a grounding block by reading ALREADY-CACHED
services, then a short lead + `_flatten_for_grounding` dumps the whole payload MINUS heavy noise keys.
It must:
  * never raise — a miss / failure degrades to the client context (or None),
  * pass BOOK / unknown / NONE straight through to the client context,
  * defer STOCK to chat_service (returns None so stock_id enrichment runs),
  * ground the answer on the on-screen VALUES (we assert the values reach the block, since the labels
    are now generic key-paths), and drop the heavy non-semantic arrays (chart/price series, read-along
    timings, urls, embeddings).

No network / Supabase — the cached services are monkeypatched. Each branch does a lazy import inside
the resolver, so patching the module attribute before the call takes effect.
"""

import pytest

from app.services.chat_context_resolver import (
    ChatContextResolver,
    get_chat_context_resolver,
    _flatten_for_grounding,
    _num,
    _price,
)


class _Obj:
    """A mock detail object: attribute access (for the resolver's lead, e.g. `detail.name`) AND a
    recursive `model_dump()` (for the prune-then-dump flatten). Mirrors a Pydantic model closely
    enough for the resolver, which reads a few attrs for the lead and dumps the rest."""
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        def conv(v):
            if isinstance(v, _Obj):
                return v.model_dump()
            if isinstance(v, list):
                return [conv(x) for x in v]
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            return v
        return {k: conv(v) for k, v in self.__dict__.items()}


@pytest.fixture
def resolver() -> ChatContextResolver:
    return ChatContextResolver()


# ── _flatten_for_grounding (the generic serializer) ─────────────────

def test_flatten_drops_noise_keys():
    payload = {
        "name": "AAPL",
        "chart_data": [1, 2, 3],
        "price_action": {"narrative": "up on news", "prices": [1.0, 2.0, 3.0]},
        "readAlong": [{"t": 1}], "heroGradientColors": ["#fff"],
        "logo_url": "http://x.png", "embedding": [0.1] * 10,
    }
    out = _flatten_for_grounding(payload, 2000)
    assert "AAPL" in out and "up on news" in out          # semantic content kept
    assert "chart_data" not in out and "prices" not in out
    assert "readAlong" not in out and "heroGradientColors" not in out
    assert "http://x.png" not in out and "0.1" not in out  # url + embedding dropped


def test_flatten_nested_dicts_and_lists():
    payload = {"a": {"b": "x"}, "items": [{"k": "v1"}, {"k": "v2"}], "tags": ["p", "q"]}
    out = _flatten_for_grounding(payload, 2000)
    assert "a.b: x" in out
    assert "v1" in out and "v2" in out           # list of dicts → per item
    assert "p, q" in out                         # pure-scalar list → inlined


def test_flatten_handles_mixed_list_with_scalars():
    # A legacy string mixed with dicts (e.g. old keyHighlights) must not be dropped.
    payload = {"h": ["plain string", {"title": "T"}, None, {}]}
    out = _flatten_for_grounding(payload, 2000)
    assert "plain string" in out and "T" in out
    assert "None" not in out


def test_flatten_caps_total_and_truncates_strings():
    payload = {"big": "z" * 5000, "many": {str(i): "y" * 100 for i in range(200)}}
    out = _flatten_for_grounding(payload, 500, str_cap=50)
    assert len(out) <= 800                       # bounded near the cap
    assert "z" * 51 not in out                   # a single field truncated to str_cap


def test_flatten_never_leaks_none_or_nan_or_raises():
    payload = {"a": None, "b": "", "c": [], "d": {"e": None}, "f": float("nan"), "g": "keep"}
    out = _flatten_for_grounding(payload, 2000)
    assert "keep" in out
    assert "None" not in out and "nan" not in out.lower()
    # A bare scalar payload is fine; a bad structure must never raise.
    assert _flatten_for_grounding("just text", 100) == "just text"


# ── _num / _price number formatting (adversarial-review fixes) ──────

def test_num_sub_cent_keeps_significant_figures():
    """The bug: f"{v:,.4f}" rounds sub-5e-5 floats to "0" — a SHIB-class crypto price told the LLM
    the coin costs $0. Small values must keep significant figures."""
    assert _num(0.00001234) == "0.00001234"        # was "0"
    assert _num(7.5e-6) == "0.0000075"
    assert _num(0.0000005) == "0.0000005"
    assert _num(0.03) == "0.03"                     # normal values unchanged
    assert _num(3012.5) == "3,012.5"
    assert _num(1_200_000_000) == "1,200,000,000"
    assert _num(0) == "0" and _num(0.0) == "0"


def test_num_non_finite_returns_none():
    """NaN AND ±inf must both be dropped (was: only NaN; inf leaked the literal 'inf')."""
    assert _num(float("nan")) is None
    assert _num(float("inf")) is None
    assert _num(float("-inf")) is None


def test_price_helper_sub_cent_and_non_finite():
    assert _price(0.00001234) == "0.00001234"       # NOT "0.00"
    assert _price(3000.0) == "3,000.00"
    assert _price(500.12) == "500.12"
    assert _price(float("nan")) is None
    assert _price(float("inf")) is None
    assert _price(None) is None
    assert _price("x") is None


def test_flatten_drops_infinite_floats():
    out = _flatten_for_grounding({"pe": float("inf"), "peg": float("-inf"), "roe": float("nan"), "keep": "yes"}, 2000)
    assert "keep: yes" in out
    assert "inf" not in out.lower() and "nan" not in out.lower()


def test_flatten_inline_scalar_list_cannot_overshoot_cap():
    """The bug: a 12-element list of long strings was joined into ONE _emit line (~4.8k chars),
    blowing past max_chars. The line is now bounded."""
    out = _flatten_for_grounding({"tags": ["z" * 400] * 12}, 500, str_cap=400)
    assert len(out) < 900                            # one bounded line, not ~4800


# ── Pass-through / no-context branches ──────────────────────────────

@pytest.mark.asyncio
async def test_none_context_returns_client_context(resolver):
    assert await resolver.resolve(None, None, "cc") == "cc"
    assert await resolver.resolve("NONE", "x", "cc") == "cc"
    assert await resolver.resolve("", "x", "cc") == "cc"
    assert await resolver.resolve("general", "x", "cc") == "cc"


@pytest.mark.asyncio
async def test_book_passes_client_context_through(resolver):
    ctx = 'The user is reading "The Psychology of Money" by Morgan Housel. The passage: …'
    assert await resolver.resolve("BOOK", "3", ctx) == ctx
    assert await resolver.resolve("book", "3", ctx) == ctx   # case-insensitive


@pytest.mark.asyncio
async def test_unknown_type_passes_through_and_never_raises(resolver):
    assert await resolver.resolve("WHAT_IS_THIS", "x", "cc") == "cc"
    assert await resolver.resolve("WHAT_IS_THIS", "x", None) is None


@pytest.mark.asyncio
async def test_stock_defers_to_none(resolver):
    assert await resolver.resolve("STOCK", "AAPL", None) is None
    assert await resolver.resolve("STOCK", "AAPL", "cc") == "cc"


# ── TICKER_REPORT ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ticker_report_lead_and_dump(resolver, monkeypatch):
    async def fake_get(ticker, persona):
        assert ticker == "AAPL" and persona == "warren_buffett"
        return {
            "company_name": "Apple Inc.",
            "quality_score": 72.0,
            "executive_summary_text": "Apple is a high-quality compounder with durable margins.",
            "core_thesis": {"bull_case": ["Durable ecosystem moat"], "bear_case": ["Valuation is rich"]},
            "price_action": {"prices": [1.0, 2.0, 3.0]},   # heavy array → must be dropped
        }

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    block = await resolver.resolve("TICKER_REPORT", "AAPL|warren_buffett", None)
    assert "Apple Inc." in block
    assert "72/100" in block and "/10." not in block            # /100 scale, not /10
    assert "high-quality compounder" in block                   # lead summary
    assert "Durable ecosystem moat" in block                    # dumped thesis
    assert "Valuation is rich" in block
    assert "1.0, 2.0, 3.0" not in block and "prices" not in block  # price series dropped


@pytest.mark.asyncio
async def test_ticker_report_score_boundaries_render_on_100_scale(resolver, monkeypatch):
    for score in (0.0, 50.5, 100.0):
        async def fake_get(ticker, persona, _s=score):
            return {"company_name": "X", "quality_score": _s, "executive_summary_text": "s"}

        import app.services.ticker_report_cache as trc
        monkeypatch.setattr(trc, "get_cached_report", fake_get)
        block = await resolver.resolve("TICKER_REPORT", "X|warren_buffett", None)
        assert f"{score:.0f}/100" in block
        assert "/10." not in block


@pytest.mark.asyncio
async def test_ticker_report_grounds_recent_price_movement(resolver, monkeypatch):
    """The reported gap: the on-screen 'Recent Price Movement' insight must lead the grounding so a
    'why did it move?' answer cites the real reason instead of restating raw price numbers."""
    async def fake_get(ticker, persona):
        return {
            "company_name": "SanDisk",
            "executive_summary_text": "A memory maker.",
            "price_action": {
                "narrative": "Fell on broader semiconductor oversupply fears following TSMC's earnings.",
                "change_pct": -24.1, "window_label": "Last 7 Days", "tag": "Semiconductor Sector Concerns",
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
async def test_ticker_report_dumps_every_module(resolver, monkeypatch):
    """Every visible module's text grounds the chat (values reach the block; labels are key-paths)."""
    async def fake_get(ticker, persona):
        return {
            "company_name": "X", "executive_summary_text": "s",
            "revenue_forecast": {"insight": "Growth reaccelerates on AI demand.", "beat_summary": "Beat 6 of 8"},
            "revenue_engine": {"analysis_note": "Cloud is now the largest segment."},
            "moat_competition": {"competitive_insight": "Switching costs anchor the moat."},
            "key_management": {"ownership_insight": "Founder-led with high insider ownership."},
            "wall_street_consensus": {"wall_street_insight": "Analysts see modest upside."},
            "macro_data": {"headline": "Rates are the swing factor.", "intelligence_brief": "Watch CPI."},
        }

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    block = await resolver.resolve("TICKER_REPORT", "X|warren_buffett", None)
    for phrase in ("Growth reaccelerates on AI demand.", "Beat 6 of 8", "Cloud is now the largest segment.",
                   "Switching costs anchor the moat.", "Founder-led with high insider ownership.",
                   "Analysts see modest upside.", "Rates are the swing factor.", "Watch CPI."):
        assert phrase in block, phrase


@pytest.mark.asyncio
async def test_ticker_report_outliers_never_crash_or_leak_none(resolver, monkeypatch):
    async def fake_get(ticker, persona):
        return {
            "company_name": "X",
            "executive_summary_text": "base summary.",
            "price_action": "oops-not-a-dict",                            # malformed → no lead, skip_top'd
            "revenue_engine": {"analysis_note": None},                    # null field → skipped
            "moat_competition": {"competitive_insight": ""},              # empty → skipped
            "wall_street_consensus": {"wall_street_insight": "Real analyst view."},  # valid → dumped
        }

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    block = await resolver.resolve("TICKER_REPORT", "X|warren_buffett", None)
    assert block is not None
    assert "base summary." in block
    assert "None" not in block
    assert "oops-not-a-dict" not in block            # malformed price_action skipped
    assert "Real analyst view." in block             # a valid module still dumped


@pytest.mark.asyncio
async def test_ticker_report_price_action_nan_change(resolver, monkeypatch):
    async def fake_get(ticker, persona):
        return {"company_name": "X",
                "price_action": {"narrative": "Moved on news.", "change_pct": float("nan"), "tag": "Catalyst"}}

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    block = await resolver.resolve("TICKER_REPORT", "X|warren_buffett", None)
    assert "Recent price movement (Catalyst):" in block
    assert "Moved on news." in block
    assert "nan" not in block.lower()


@pytest.mark.asyncio
async def test_ticker_report_agent_tag_maps_to_full_persona_key(resolver, monkeypatch):
    seen = {}

    async def fake_get(ticker, persona):
        seen["ticker"], seen["persona"] = ticker, persona
        return {"company_name": "Microsoft", "executive_summary_text": "solid."}

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    await resolver.resolve("TICKER_REPORT", "msft|buffett", None)
    assert seen["ticker"] == "MSFT"
    assert seen["persona"] == "warren_buffett"


@pytest.mark.asyncio
async def test_ticker_report_missing_persona_defaults(resolver, monkeypatch):
    seen = {}

    async def fake_get(ticker, persona):
        seen["persona"] = persona
        return {"company_name": "X", "executive_summary_text": "s"}

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    await resolver.resolve("TICKER_REPORT", "TSLA", None)
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
    assert await resolver.resolve("TICKER_REPORT", "", "cc") == "cc"
    assert await resolver.resolve("TICKER_REPORT", "", None) is None


@pytest.mark.asyncio
async def test_ticker_report_block_is_bounded(resolver, monkeypatch):
    """Even a huge report is bounded by the lead caps + _DUMP_CAP (~lead + 2800 chars)."""
    async def fake_get(ticker, persona):
        return {
            "company_name": "X",
            "executive_summary_text": "word " * 400,                       # ~2000 chars → capped in lead
            "moat_competition": {"competitive_insight": "deep " * 400},     # big → dump-capped
            "critical_factors": [{"title": f"f{i}", "detail": "x" * 200} for i in range(50)],
        }

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)
    block = await resolver.resolve("TICKER_REPORT", "X|warren_buffett", None)
    assert len(block) < 4600     # lead (~1k) + dump (2800) + framing — never the full payload


# ── ETF ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_etf_hit_grounds_price_and_stats(resolver, monkeypatch):
    detail = _Obj(name="SPDR S&P 500 ETF", symbol="SPY", current_price=500.12, price_change_percent=0.53,
                  key_statistics=[_Obj(label="Expense Ratio", value="0.09%"), _Obj(label="AUM", value="$500B")],
                  etf_profile=_Obj(index_tracked="S&P 500 Index", website="http://spy.com"))

    class _Svc:
        async def get_etf_detail(self, symbol):
            assert symbol == "SPY"
            return detail

    import app.services.etf_service as es
    monkeypatch.setattr(es, "get_etf_service", lambda: _Svc())

    block = await resolver.resolve("ETF", "spy", None)
    assert "SPDR S&P 500 ETF" in block and "0.09%" in block and "S&P 500 Index" in block
    assert "http://spy.com" not in block          # website dropped


@pytest.mark.asyncio
async def test_etf_dumps_holdings_dividend_sectors(resolver, monkeypatch):
    detail = _Obj(
        name="Vanguard S&P 500", symbol="VOO", current_price=500.0, price_change_percent=0.5,
        chart_data=[{"t": 1, "p": 499.0}],   # heavy noise → dropped
        etf_profile=_Obj(description="Tracks the S&P 500 index of US large caps.", index_tracked="S&P 500"),
        net_yield=_Obj(dividend_yield=1.30, expense_ratio=0.03, pay_frequency="Quarterly",
                       last_dividend_payment=_Obj(dividend_per_share="$1.77", pay_date="Jan 31 2026")),
        holdings_risk=_Obj(top_holdings=[_Obj(symbol="AAPL", name="Apple", weight=7.1),
                                         _Obj(symbol="MSFT", name="Microsoft", weight=6.5)],
                           top_sectors=[_Obj(name="Technology", weight=30.2)]),
        performance_periods=[_Obj(label="1Y", change_percent=12.3)],
        strategy=_Obj(hook="Cheap, broad, passive exposure to US large caps."),
    )

    class _Svc:
        async def get_etf_detail(self, s):
            return detail

    import app.services.etf_service as es
    monkeypatch.setattr(es, "get_etf_service", lambda: _Svc())

    block = await resolver.resolve("ETF", "voo", None)
    assert "Vanguard S&P 500 (VOO)" in block
    for v in ("1.3", "0.03", "Quarterly", "AAPL", "7.1", "MSFT", "Technology", "30.2",
              "$1.77", "Jan 31 2026", "12.3", "Tracks the S&P 500 index", "Cheap, broad, passive"):
        assert v in block, v
    assert "chart_data" not in block             # heavy series dropped


@pytest.mark.asyncio
async def test_etf_bare_detail_still_grounds(resolver, monkeypatch):
    detail = _Obj(name="X", symbol="X", current_price=1.0, price_change_percent=0.0,
                  key_statistics=[_Obj(label="AUM", value="$1B")], etf_profile=_Obj(index_tracked="Idx"))

    class _Svc:
        async def get_etf_detail(self, s):
            return detail

    import app.services.etf_service as es
    monkeypatch.setattr(es, "get_etf_service", lambda: _Svc())

    block = await resolver.resolve("ETF", "x", None)
    assert "X (X)" in block and "$1B" in block and "Idx" in block
    assert "None" not in block


@pytest.mark.asyncio
async def test_etf_empty_symbol_returns_none(resolver):
    assert await resolver.resolve("ETF", "", None) is None


@pytest.mark.asyncio
async def test_resolve_times_out_on_slow_recompute_and_degrades(resolver, monkeypatch):
    import asyncio as _a
    import app.services.chat_context_resolver as ccr
    import app.services.etf_service as es

    monkeypatch.setattr(ccr, "_RESOLVE_TIMEOUT_SECONDS", 0.05)

    class _SlowSvc:
        async def get_etf_detail(self, symbol):
            await _a.sleep(1.0)
            raise AssertionError("should have been cancelled by the timeout")

    monkeypatch.setattr(es, "get_etf_service", lambda: _SlowSvc())
    assert await resolver.resolve("ETF", "SPY", "fallback ctx") == "fallback ctx"


# ── CRYPTO ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crypto_dumps_snapshots_and_profile(resolver, monkeypatch):
    detail = _Obj(
        name="Ethereum", symbol="ETH", current_price=3000.0, price_change_percent=-1.2,
        crypto_profile=_Obj(description="A programmable blockchain for smart contracts.",
                            blockchain="Ethereum", consensus_mechanism="Proof of Stake"),
        snapshots=[_Obj(category="Tokenomics", paragraphs=["No fixed max supply; EIP-1559 burns."]),
                   _Obj(category="Risks", paragraphs=["Regulatory and L2 competition risks."])],
        key_statistics_groups=[_Obj(statistics=[_Obj(label="Market Cap", value="$360B")])],
    )

    class _Svc:
        async def get_crypto_detail(self, s):
            return detail

    import app.services.crypto_service as cs
    monkeypatch.setattr(cs, "get_crypto_service", lambda: _Svc())

    block = await resolver.resolve("CRYPTO", "eth", None)
    for v in ("Ethereum (ETH)", "programmable blockchain for smart contracts", "Tokenomics",
              "EIP-1559 burns", "Risks", "L2 competition", "Market Cap", "$360B", "Proof of Stake"):
        assert v in block, v


# ── INDEX ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_index_dumps_name_price_sectors_macro(resolver, monkeypatch):
    detail = _Obj(
        index_name="S&P 500", current_price=5200.0, price_change_percent=0.3,
        index_profile=_Obj(description="500 large-cap US stocks.", number_of_constituents=500,
                           weighting_methodology="Market-cap"),
        snapshots_data=_Obj(
            valuation=_Obj(pe_ratio=21.0, forward_pe=18.5, earnings_yield=4.7),
            sector_performance=_Obj(sectors=[_Obj(sector="Technology", change_percent=0.8),
                                             _Obj(sector="Energy", change_percent=-0.4)]),
            macro_forecast=_Obj(indicators=[_Obj(title="Inflation", description="cooling", signal="neutral")])),
        chart_data=[{"m": "Jan", "p": 5000}],
    )

    class _Svc:
        async def get_index_detail(self, s):
            return detail

    import app.services.index_service as ixs
    monkeypatch.setattr(ixs, "get_index_service", lambda: _Svc())

    block = await resolver.resolve("INDEX", "^GSPC", None)
    assert "S&P 500" in block and "Level 5,200.00 (+0.30%)" in block
    for v in ("18.5", "Technology", "Energy", "Inflation", "cooling", "neutral",
              "500 large-cap US stocks", "Market-cap"):
        assert v in block, v
    assert "chart_data" not in block


# ── COMMODITY ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_commodity_appends_bundled_profile(resolver, monkeypatch):
    import app.services.commodity_service as cms
    monkeypatch.setattr(cms, "_get_meta", lambda s: {
        "description": "Gold is a safe-haven precious metal.",
        "major_producers": "China, Australia, Russia", "major_consumers": "China, India, USA",
        "category": "metals", "exchange": "COMEX", "fmp_symbol": "GCUSD",
    })
    client = "COMMODITY CONTEXT: Symbol GCUSD, Price $2000."
    block = await resolver.resolve("COMMODITY", "GCUSD", client)
    assert client in block                       # iOS context preserved, not replaced
    assert "Commodity profile" in block
    for v in ("safe-haven precious metal", "China, Australia, Russia", "China, India, USA", "COMEX"):
        assert v in block, v


@pytest.mark.asyncio
async def test_commodity_unknown_symbol_degrades_to_client_context(resolver, monkeypatch):
    import app.services.commodity_service as cms
    monkeypatch.setattr(cms, "_get_meta", lambda s: {})
    assert await resolver.resolve("COMMODITY", "ZZUSD", "cc") == "cc"
    assert await resolver.resolve("COMMODITY", "ZZUSD", None) is None


# ── MONEY_MOVES_ARTICLE ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_money_move_dumps_body_and_drops_noise(resolver, monkeypatch):
    resp = _Obj(articles=[{
        "slug": "compounding", "title": "The Magic of Compounding", "subtitle": "Small sums, big time.",
        "author": {"name": "Jane Doe"},
        "keyHighlights": [{"title": "Start early", "description": "time is the lever", "icon": "clock"}],
        "statistics": [{"value": "8%", "label": "Avg annual return"}],
        "viewCount": 1234, "audioUrl": "http://a.m4a", "heroGradientColors": ["#fff", "#000"],
        "sections": [{"title": "Why it works", "content": [
            {"type": "paragraph", "text": "Compounding reinvests returns so growth accelerates."},
            {"type": "bulletList", "items": ["Reinvest dividends", "Avoid interrupting the curve"]},
            {"type": "quote", "text": "Compound interest is the eighth wonder.", "attribution": "Einstein"}]}],
    }])

    class _Svc:
        async def get_money_moves(self):
            return resp

    import app.services.money_moves_content_service as mm
    monkeypatch.setattr(mm, "get_money_moves_content_service", lambda: _Svc())

    block = await resolver.resolve("MONEY_MOVES_ARTICLE", "compounding", None)
    assert "The Magic of Compounding" in block and "Jane Doe" in block
    for v in ("Compounding reinvests returns", "Reinvest dividends", "eighth wonder", "Einstein",
              "Start early", "time is the lever", "Avg annual return", "8%"):
        assert v in block, v
    # Noise dropped
    assert "1234" not in block and "http://a.m4a" not in block and "#fff" not in block and "clock" not in block


@pytest.mark.asyncio
async def test_money_move_malformed_sections_no_crash(resolver, monkeypatch):
    resp = _Obj(articles=[{"slug": "x", "title": "T", "sections": "not-a-list"}])

    class _Svc:
        async def get_money_moves(self):
            return resp

    import app.services.money_moves_content_service as mm
    monkeypatch.setattr(mm, "get_money_moves_content_service", lambda: _Svc())

    block = await resolver.resolve("MONEY_MOVES_ARTICLE", "x", None)
    assert block is not None and "None" not in block


@pytest.mark.asyncio
async def test_money_move_unknown_slug_returns_none(resolver, monkeypatch):
    resp = _Obj(articles=[{"slug": "a", "title": "A"}])

    class _Svc:
        async def get_money_moves(self):
            return resp

    import app.services.money_moves_content_service as mm
    monkeypatch.setattr(mm, "get_money_moves_content_service", lambda: _Svc())
    assert await resolver.resolve("MONEY_MOVES_ARTICLE", "missing", None) is None


# ── JOURNEY_LESSON ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_journey_dumps_lesson_body_and_drops_timings(resolver, monkeypatch):
    lesson = {"id": "1", "title": "Risk 101", "description": "Intro to risk.",
              "story_content": {"cards": [
                  {"type": "title", "headline": "Risk 101", "text": "Risk is the chance of loss."},
                  {"type": "content", "text": "Diversification reduces unsystematic risk.",
                   "readAlongWords": [{"w": "Diversification", "t0": 0.0, "t1": 0.4}]}]}}
    resp = _Obj(lessons=[lesson])

    class _Svc:
        async def get_journey(self):
            return resp

    import app.services.journey_content_service as jc
    monkeypatch.setattr(jc, "get_journey_content_service", lambda: _Svc())

    block = await resolver.resolve("JOURNEY_LESSON", "1", None)
    assert "Risk 101" in block
    assert "Risk is the chance of loss." in block
    assert "Diversification reduces unsystematic risk." in block
    assert "readAlongWords" not in block and "t0" not in block   # per-word timing arrays dropped


@pytest.mark.asyncio
async def test_journey_null_story_content_no_crash(resolver, monkeypatch):
    lesson = {"id": "2", "title": "L2", "description": "d", "story_content": None}
    resp = _Obj(lessons=[lesson])

    class _Svc:
        async def get_journey(self):
            return resp

    import app.services.journey_content_service as jc
    monkeypatch.setattr(jc, "get_journey_content_service", lambda: _Svc())

    block = await resolver.resolve("JOURNEY_LESSON", "2", None)
    assert block is not None
    assert "Lesson content" not in block and "None" not in block


# ── Adversarial-review regressions (sub-cent lead + report starvation) ──

@pytest.mark.asyncio
async def test_crypto_lead_sub_cent_price_not_zeroed(resolver, monkeypatch):
    """The confirmed bug: the crypto lead f"${detail.current_price:,.4f}" rendered a SHIB-class coin
    as 'Price $0.0000', telling the LLM it costs $0. The lead now keeps significant figures."""
    detail = _Obj(name="Shiba Inu", symbol="SHIB", current_price=0.00001234, price_change_percent=2.5,
                  crypto_profile=_Obj(description="A meme coin."))

    class _Svc:
        async def get_crypto_detail(self, s):
            return detail

    import app.services.crypto_service as cs
    monkeypatch.setattr(cs, "get_crypto_service", lambda: _Svc())

    block = await resolver.resolve("CRYPTO", "shib", None)
    assert "Shiba Inu (SHIB)" in block
    assert "0.00001234" in block               # the real sub-cent price
    assert "$0.0000 " not in block             # NOT the rounded-to-zero bug


@pytest.mark.asyncio
async def test_ticker_report_history_arrays_dropped_narratives_survive(resolver, monkeypatch):
    """The HIGH bug: the frozen per-metric history arrays (annual/quarterly/sector history) sit early
    (fundamental_metrics) and ate the whole dump budget, starving the moat/Wall-Street/macro insights
    OUT of the block. They're now dropped AND the narratives are emitted first."""
    big_history = [{"period": f"20{i:02d}", "value": i * 1.1} for i in range(40)]
    async def fake_get(ticker, persona):
        return {
            "company_name": "X", "executive_summary_text": "s",
            "fundamental_metrics": [{"title": f"Card{c}", "metrics": [
                {"name": f"ROE{c}{m}", "value": "45%",
                 "annual_history": big_history, "quarterly_history": big_history,
                 "sector_annual_history": big_history, "sector_quarterly_history": big_history}
                for m in range(6)]} for c in range(6)],
            "moat_competition": {"competitive_insight": "MOATMARK switching costs anchor it."},
            "wall_street_consensus": {"wall_street_insight": "WALLMARK analysts split."},
            "macro_data": {"headline": "MACROMARK rates swing it."},
        }

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    block = await resolver.resolve("TICKER_REPORT", "X|warren_buffett", None)
    # The history arrays are dropped (their key-paths never appear, and a 40-point series can't dominate).
    assert "annual_history" not in block and "sector_quarterly_history" not in block
    # The metric name/value still survive (useful, small).
    assert "ROE00" in block and "45%" in block
    # The later narrative modules are NOT starved out — the whole point of the grounding.
    assert "MOATMARK" in block
    assert "WALLMARK" in block
    assert "MACROMARK" in block


# ── Singleton ───────────────────────────────────────────────────────

def test_singleton_is_stable():
    assert get_chat_context_resolver() is get_chat_context_resolver()
