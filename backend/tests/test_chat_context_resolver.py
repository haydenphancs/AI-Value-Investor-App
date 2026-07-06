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
            "quality_score": 8.2,
            "executive_summary_text": "Apple is a high-quality compounder with durable margins.",
            "core_thesis": {"bull_case": ["Durable ecosystem moat"], "bear_case": ["Valuation is rich"]},
        }

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    block = await resolver.resolve("TICKER_REPORT", "AAPL|warren_buffett", None)
    assert block is not None
    assert "Apple Inc." in block
    assert "8.2/10" in block
    assert "high-quality compounder" in block
    assert "Durable ecosystem moat" in block
    assert "Valuation is rich" in block


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
    class _Resp:
        articles = [
            {
                "slug": "compound-interest",
                "title": "The Power of Compounding",
                "subtitle": "Small amounts, big results",
                "author": {"name": "Jane Doe"},
                "keyHighlights": ["Start early", "Stay consistent", "Reinvest"],
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
    assert "Start early" in block


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


# ── Singleton ───────────────────────────────────────────────────────

def test_singleton_is_stable():
    assert get_chat_context_resolver() is get_chat_context_resolver()
