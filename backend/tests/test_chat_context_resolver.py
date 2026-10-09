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

@pytest.mark.parametrize("fair", [False, True])
def test_flatten_drops_noise_keys(fair):
    payload = {
        "name": "AAPL",
        "chart_data": [1, 2, 3],
        "price_action": {"narrative": "up on news", "prices": [1.0, 2.0, 3.0]},
        "readAlong": [{"t": 1}], "heroGradientColors": ["#fff"],
        "logo_url": "http://x.png", "embedding": [0.1] * 10,
    }
    out = _flatten_for_grounding(payload, 2000, fair=fair)
    assert "AAPL" in out and "up on news" in out          # semantic content kept
    assert "chart_data" not in out and "prices" not in out
    assert "readAlong" not in out and "heroGradientColors" not in out
    assert "http://x.png" not in out and "0.1" not in out  # url + embedding dropped


@pytest.mark.parametrize("fair", [False, True])
def test_flatten_nested_dicts_and_lists(fair):
    payload = {"a": {"b": "x"}, "items": [{"k": "v1"}, {"k": "v2"}], "tags": ["p", "q"]}
    out = _flatten_for_grounding(payload, 2000, fair=fair)
    assert "a.b: x" in out
    assert "v1" in out and "v2" in out           # list of dicts → per item
    assert "p, q" in out                         # pure-scalar list → inlined


@pytest.mark.parametrize("fair", [False, True])
def test_flatten_handles_mixed_list_with_scalars(fair):
    # A legacy string mixed with dicts (e.g. old keyHighlights) must not be dropped.
    payload = {"h": ["plain string", {"title": "T"}, None, {}]}
    out = _flatten_for_grounding(payload, 2000, fair=fair)
    assert "plain string" in out and "T" in out
    assert "None" not in out


@pytest.mark.parametrize("fair", [False, True])
def test_flatten_caps_total_and_truncates_strings(fair):
    payload = {"big": "z" * 5000, "many": {str(i): "y" * 100 for i in range(200)}}
    out = _flatten_for_grounding(payload, 500, str_cap=50, fair=fair)
    assert len(out) <= 800                       # bounded near the cap
    assert "z" * 51 not in out                   # a single field truncated to str_cap


@pytest.mark.parametrize("fair", [False, True])
def test_flatten_never_leaks_none_or_nan_or_raises(fair):
    payload = {"a": None, "b": "", "c": [], "d": {"e": None}, "f": float("nan"), "g": "keep"}
    out = _flatten_for_grounding(payload, 2000, fair=fair)
    assert "keep" in out
    assert "None" not in out and "nan" not in out.lower()
    # A bare scalar payload is fine; a bad structure must never raise.
    assert _flatten_for_grounding("just text", 100, fair=fair) == "just text"


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


@pytest.mark.parametrize("fair", [False, True])
def test_flatten_drops_infinite_floats(fair):
    out = _flatten_for_grounding({"pe": float("inf"), "peg": float("-inf"), "roe": float("nan"), "keep": "yes"}, 2000, fair=fair)
    assert "keep: yes" in out
    assert "inf" not in out.lower() and "nan" not in out.lower()


@pytest.mark.parametrize("fair", [False, True])
def test_flatten_inline_scalar_list_cannot_overshoot_cap(fair):
    """The bug: a 12-element list of long strings was joined into ONE _emit line (~4.8k chars),
    blowing past max_chars. The line is now bounded."""
    out = _flatten_for_grounding({"tags": ["z" * 400] * 12}, 500, str_cap=400, fair=fair)
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


# A published estimate: the section's insight reaches the chat only beside one
# (dcf_report_gate.wall_street_insight_is_for_this_card — the rule the app and PDF use).
_PUBLISHED = {"status": "ok", "fair_value": 100.0, "range_low": 90.0, "range_high": 110.0}


@pytest.mark.asyncio
async def test_ticker_report_dumps_every_module(resolver, monkeypatch):
    """Every visible module's text grounds the chat (values reach the block; labels are key-paths)."""
    from app.config import settings
    monkeypatch.setattr(settings, "DCF_ENABLED", True)

    async def fake_get(ticker, persona):
        return {
            "company_name": "X", "executive_summary_text": "s",
            "revenue_forecast": {"insight": "Growth reaccelerates on AI demand.", "beat_summary": "Beat 6 of 8"},
            "revenue_engine": {"analysis_note": "Cloud is now the largest segment."},
            "moat_competition": {"competitive_insight": "Switching costs anchor the moat."},
            "key_management": {"ownership_insight": "Founder-led with high insider ownership."},
            "wall_street_consensus": {"wall_street_insight": "Institutions kept adding.",
                                      "caydex_fair_value": dict(_PUBLISHED)},
            "macro_data": {"headline": "Rates are the swing factor.", "intelligence_brief": "Watch CPI."},
        }

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    block = await resolver.resolve("TICKER_REPORT", "X|warren_buffett", None)
    for phrase in ("Growth reaccelerates on AI demand.", "Beat 6 of 8", "Cloud is now the largest segment.",
                   "Switching costs anchor the moat.", "Founder-led with high insider ownership.",
                   "Institutions kept adding.", "Rates are the swing factor.", "Watch CPI."):
        assert phrase in block, phrase


@pytest.mark.asyncio
async def test_ticker_report_outliers_never_crash_or_leak_none(resolver, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DCF_ENABLED", True)

    async def fake_get(ticker, persona):
        return {
            "company_name": "X",
            "executive_summary_text": "base summary.",
            "price_action": "oops-not-a-dict",                            # malformed → no lead, skip_top'd
            "revenue_engine": {"analysis_note": None},                    # null field → skipped
            "moat_competition": {"competitive_insight": ""},              # empty → skipped
            "wall_street_consensus": {"wall_street_insight": "Real institutional view.",  # valid → dumped
                                      "caydex_fair_value": dict(_PUBLISHED)},
        }

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)

    block = await resolver.resolve("TICKER_REPORT", "X|warren_buffett", None)
    assert block is not None
    assert "base summary." in block
    assert "None" not in block
    assert "oops-not-a-dict" not in block            # malformed price_action skipped
    assert "Real institutional view." in block       # a valid module still dumped


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
    """Even a huge report — every lead field oversized, 50 competitor rows with 2 KB names and
    segments, 50 critical factors — is bounded by the lead caps + `_REPORT_DUMP_CAP`. The bound is
    DERIVED from the module's caps, so raising a cap moves it instead of silently passing."""
    import app.services.chat_context_resolver as ccr

    huge = "word " * 2000
    async def fake_get(ticker, persona):
        return {
            "company_name": "C" * 5000,
            "price_close_date": "2026-09-22 " * 500,
            "quality_score": 72,
            "price_action": {"narrative": huge, "change_pct": -3.2, "window_label": "w " * 500,
                             "tag": "t " * 500},
            "executive_summary_text": huge,
            "core_thesis": {"bull_case": [huge] * 20, "bear_case": [huge] * 20},
            "moat_competition": {
                "competitive_insight": "deep " * 400,
                "competitor_order": "direct", "competitor_source": "research",
                "competitors": [
                    {"name": "N " * 1000, "ticker": f"T{i}", "competitive_score": 5.0,
                     "threat_level": "high", "segment": "S " * 1000,
                     "score_basis": ("relative", "absolute")[i % 2]}
                    for i in range(50)
                ],
            },
            "critical_factors": [{"title": f"f{i}", "detail": "x" * 200} for i in range(50)],
            "macro_data": {"intelligence_brief": huge, "risk_factors": [{"d": huge}] * 40},
        }

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)
    block = await resolver.resolve("TICKER_REPORT", "X|warren_buffett", None)

    head, dump_and_tail = block.split("(an excerpt; long sections are shortened):\n", 1)
    dump = dump_and_tail.rsplit("\nAnswer grounded in THIS report", 1)[0]
    assert len(dump) <= ccr._REPORT_DUMP_CAP
    rows = ccr._COMPETITOR_LEAD_MAX_ROWS
    lead_bound = (
        120 + ccr._COMPETITOR_NAME_CAP                                 # "viewing … (X)."
        + 20 + ccr._REPORT_DATE_CAP                                    # "Report dated …"
        + 40                                                           # quality score
        + 60 + 2 * 61 + ccr._MAX_REPORT_MODULE                         # price movement
        + 30 + ccr._MAX_REPORT_SUMMARY                                 # executive summary
        + 2 * (20 + ccr._MAX_REPORT_THESIS)                            # bull / bear
        + 80 + rows * (60 + ccr._COMPETITOR_NAME_CAP + ccr._COMPETITOR_SEGMENT_CAP)
        + 2 * (300 + rows * 4) + 100 + 100                             # method, badge, source
        + ccr._REPORT_FIGURES_LEAD_CAP + 1                             # figures lead
        + 100                                                          # dump header
    )
    assert len(head) <= lead_bound, (len(head), lead_bound)
    assert len(block) <= lead_bound + ccr._REPORT_DUMP_CAP + 200
    assert len(block) < 10_000                 # and in absolute terms: never the full payload
    assert block.count("— competes in:") == rows


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

    state = {"cancelled": False, "done": False}

    class _SlowSvc:
        async def get_etf_detail(self, symbol):
            try:
                await _a.sleep(0.2)
                state["done"] = True
                return None
            except _a.CancelledError:
                state["cancelled"] = True
                raise

    monkeypatch.setattr(es, "get_etf_service", lambda: _SlowSvc())
    assert await resolver.resolve("ETF", "SPY", "fallback ctx") == "fallback ctx"
    # The ceiling abandons the chat's WAIT — it must not cancel the shared detail build
    # (the resolver is usually its `_inflight` leader; a cancelled leader failed every
    # joiner: the screen itself, the widget batch). 2026-09-17: shielded.
    await _a.sleep(0.3)
    assert state["done"] is True and state["cancelled"] is False, state


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


@pytest.mark.asyncio
async def test_crypto_profile_survives_a_stats_heavy_payload(resolver, monkeypatch):
    """E5 (TestFlight 2026-09-16): `crypto_profile` sits after the key statistics,
    performance periods and snapshots in schema order; a coin with a full set of those
    filled the 2800-char cap before the description was reached, so "Who maintains
    DOGE?" was answered from a grounding block that never mentioned the coin's origin.
    The profile is now emitted first. (Mutation: drop `priority_top=` → the description
    is absent and this fails.)"""
    stats = [_Obj(title=f"Group {g}", statistics=[
        _Obj(label=f"Statistic number {g}-{i}", value=f"{g * 100 + i:,} units of something")
        for i in range(12)
    ]) for g in range(6)]
    perf = [_Obj(period=f"P{i}", change_percent=float(i), label=f"Period label {i}") for i in range(12)]
    snaps = [_Obj(category=f"Category {i}", paragraphs=["x" * 380, "y" * 380]) for i in range(6)]
    detail = _Obj(
        name="Dogecoin", symbol="DOGE", current_price=0.09, price_change_percent=3.3,
        key_statistics_groups=stats, performance_periods=perf, snapshots=snaps,
        crypto_profile=_Obj(
            description="Dogecoin started as a joke in 2013; maintained by the Dogecoin Core developers.",
            consensus_mechanism="Proof of Work",
        ),
    )

    class _Svc:
        async def get_crypto_detail(self, s):
            return detail

    import app.services.crypto_service as cs
    monkeypatch.setattr(cs, "get_crypto_service", lambda: _Svc())
    block = await resolver.resolve("CRYPTO", "doge", None)
    assert block is not None
    assert "maintained by the Dogecoin Core developers" in block
    # …and it comes BEFORE the first statistic, not after the cap has been spent.
    assert block.index("Dogecoin Core developers") < block.index("Statistic number 0-0")
    # The numbers are not lost either — the cap still holds the leading stats.
    assert "Statistic number 0-0" in block


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
    from app.config import settings
    monkeypatch.setattr(settings, "DCF_ENABLED", True)
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
            "wall_street_consensus": {"wall_street_insight": "WALLMARK institutions split.",
                                      "caydex_fair_value": dict(_PUBLISHED)},
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


# ── JOURNEY_LESSON, reached from iOS for the first time (Phase 5) ────────────
#
# This branch shipped fully built and was NEVER CALLED: no iOS site sent the context
# type. Now that `InvestorJourneyView` does, these pin the two things that call site
# actually depends on.


def _journey_svc(monkeypatch, lessons):
    class _Svc:
        async def get_journey(self):
            return _Obj(lessons=lessons)

    import app.services.journey_content_service as jc
    monkeypatch.setattr(jc, "get_journey_content_service", lambda: _Svc())


@pytest.mark.asyncio
async def test_journey_resolves_by_TITLE_not_only_id(resolver, monkeypatch):
    """iOS sends the TITLE, and it has no choice: `Lesson.id` is a client-side `UUID()`
    regenerated every launch and unknown to the backend. If this branch only matched on
    id, every lesson chat would silently degrade to ungrounded."""
    _journey_svc(monkeypatch, [
        {"id": "srv-42", "title": "Margin of Safety", "description": "Buy below value."},
    ])
    block = await resolver.resolve("JOURNEY_LESSON", "Margin of Safety", None)
    assert block and "Margin of Safety" in block


@pytest.mark.asyncio
async def test_journey_title_match_is_exact(resolver, monkeypatch):
    """A near-miss must resolve to nothing rather than grounding on the wrong lesson —
    an almost-right lesson is worse than none, because the answer looks authoritative."""
    _journey_svc(monkeypatch, [{"id": "1", "title": "Margin of Safety", "description": "x"}])
    assert await resolver.resolve("JOURNEY_LESSON", "Margin of Safety ", None) is not None
    assert await resolver.resolve("JOURNEY_LESSON", "Margin", None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", [None, "", "   ", "Nonexistent Lesson"])
async def test_journey_unknown_reference_degrades_quietly(resolver, monkeypatch, ref):
    _journey_svc(monkeypatch, [{"id": "1", "title": "Risk 101", "description": "x"}])
    assert await resolver.resolve("JOURNEY_LESSON", ref, None) is None


@pytest.mark.asyncio
async def test_journey_service_failure_never_raises(resolver, monkeypatch):
    """Grounding is best-effort; a content-service outage must cost the grounding, not
    the answer."""
    class _Svc:
        async def get_journey(self):
            raise RuntimeError("journey content service down")

    import app.services.journey_content_service as jc
    monkeypatch.setattr(jc, "get_journey_content_service", lambda: _Svc())
    assert await resolver.resolve("JOURNEY_LESSON", "Risk 101", None) is None


# ── TICKER_REPORT: ground on the report the user is LOOKING AT ──────────────
#
# Reports are FROZEN point-in-time snapshots; `ticker_report_cache` is CLOSE-ALIGNED
# and returns None for anything written before the most recent weekday 18:00 ET. So
# grounding on that cache alone meant that, from the next close onward, opening a
# saved report and tapping "Chat with the report…" resolved to NO context — and
# chat_service then fell through to LIVE stock enrichment, answering about today's
# quote while the user read a three-week-old analysis. Before that boundary it could
# be worse: the cache may hold a NEWER regeneration of the same (ticker, persona), so
# the answer cited numbers that are not on screen.


def _stub_frozen_row(monkeypatch, *, expect_id=None, expect_user=None, data=None):
    """Stand in for the owner-scoped `research_reports` read."""
    calls = {}

    async def _fake(report_id, user_id):
        calls["report_id"] = report_id
        calls["user_id"] = user_id
        if expect_id is not None and report_id != expect_id:
            return None
        if expect_user is not None and user_id != expect_user:
            return None
        return data

    monkeypatch.setattr(
        ChatContextResolver, "_stored_report_for_user", staticmethod(_fake)
    )
    return calls


@pytest.mark.asyncio
async def test_report_chat_prefers_the_users_own_frozen_row(resolver, monkeypatch):
    """The stored row wins even when the shared cache holds a DIFFERENT report."""
    async def fake_cache(ticker, persona):
        return {"company_name": "Wrong Co.", "executive_summary_text": "the newer regeneration"}

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_cache)
    calls = _stub_frozen_row(
        monkeypatch,
        data={"company_name": "Oracle Corporation",
              "executive_summary_text": "the snapshot on screen"},
    )

    block = await resolver.resolve(
        "TICKER_REPORT", "ORCL|warren_buffett|rid-1", None, user_id="user-42",
    )
    assert "Oracle Corporation" in block and "the snapshot on screen" in block
    assert "Wrong Co." not in block and "newer regeneration" not in block
    assert calls == {"report_id": "rid-1", "user_id": "user-42"}


@pytest.mark.asyncio
async def test_report_chat_still_grounds_after_the_shared_cache_goes_stale(resolver, monkeypatch):
    """The close-cycle boundary must no longer silently un-ground a saved report."""
    async def empty_cache(ticker, persona):
        return None            # exactly what get_cached_report does past 18:00 ET

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", empty_cache)
    _stub_frozen_row(monkeypatch, data={"company_name": "Oracle Corporation",
                                        "executive_summary_text": "frozen text"})

    block = await resolver.resolve(
        "TICKER_REPORT", "ORCL|warren_buffett|rid-1", None, user_id="user-42",
    )
    assert block and "frozen text" in block


@pytest.mark.asyncio
async def test_report_chat_never_reads_a_row_without_an_identity(resolver, monkeypatch):
    """A report id is a bare UUID with no other access control. Without a user_id the
    stored-row read must not be attempted at all, or the chat becomes an oracle for
    anyone else's report."""
    async def empty_cache(ticker, persona):
        return None

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", empty_cache)
    calls = _stub_frozen_row(monkeypatch, data={"company_name": "Someone Else Inc."})

    block = await resolver.resolve("TICKER_REPORT", "ORCL|warren_buffett|rid-1", None)

    assert calls == {}, "the owner-scoped read fired with no identity to scope it to"
    assert block is None


@pytest.mark.asyncio
async def test_report_chat_falls_back_to_the_shared_cache_without_a_report_id(resolver, monkeypatch):
    """Ticker-browse has no research_reports row — the two-segment form must keep
    working exactly as before."""
    async def fake_cache(ticker, persona):
        assert (ticker, persona) == ("ORCL", "warren_buffett")
        return {"company_name": "Oracle Corporation", "executive_summary_text": "cache text"}

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_cache)
    calls = _stub_frozen_row(monkeypatch, data={"company_name": "Never Used"})

    block = await resolver.resolve(
        "TICKER_REPORT", "ORCL|warren_buffett", None, user_id="user-42",
    )
    assert "cache text" in block
    assert calls == {}


@pytest.mark.asyncio
async def test_report_chat_falls_back_when_the_row_is_not_the_callers(resolver, monkeypatch):
    """A mismatched owner yields None from the scoped read → shared cache, never a leak."""
    async def fake_cache(ticker, persona):
        return {"company_name": "Oracle Corporation", "executive_summary_text": "cache text"}

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_cache)
    _stub_frozen_row(monkeypatch, expect_user="somebody-else",
                     data={"company_name": "Not Yours Inc."})

    block = await resolver.resolve(
        "TICKER_REPORT", "ORCL|warren_buffett|rid-1", None, user_id="user-42",
    )
    assert "cache text" in block and "Not Yours" not in block


# ── S03-7: client-chosen grounding fields cannot forge or flood a log line ────

import logging as _logging


@pytest.mark.asyncio
async def test_a_newline_in_context_type_cannot_forge_a_second_log_record(resolver, caplog):
    forged = "TICKER\nERROR app.security: admin login from 1.2.3.4"
    with caplog.at_level(_logging.WARNING, logger="app.services.chat_context_resolver"):
        out = await resolver.resolve(forged, "AAPL", client_context="ctx")
    assert out == "ctx"
    records = [r for r in caplog.records if r.name == "app.services.chat_context_resolver"]
    assert len(records) == 1
    rendered = records[0].getMessage()
    assert "\n" not in rendered, rendered           # `%r` escapes it: '\\n' stays one line
    assert "\\n" in rendered


@pytest.mark.asyncio
async def test_a_huge_reference_id_is_bounded_in_the_log(resolver, caplog):
    with caplog.at_level(_logging.WARNING, logger="app.services.chat_context_resolver"):
        await resolver.resolve("NOT_A_TYPE", "x" * 100_000, client_context=None)
    rendered = [r.getMessage() for r in caplog.records
                if r.name == "app.services.chat_context_resolver"][0]
    assert len(rendered) < 400, len(rendered)
    assert "…" in rendered


@pytest.mark.asyncio
async def test_the_timeout_and_failure_arms_are_bounded_too(resolver, caplog, monkeypatch):
    import asyncio

    async def _slow(self, ref, ctx):
        await asyncio.sleep(3600)

    async def _boom(self, ref, ctx):
        raise RuntimeError("upstream")

    from app.services import chat_context_resolver as mod
    monkeypatch.setattr(mod, "_RESOLVE_TIMEOUT_SECONDS", 0.01)
    huge = "y\n" * 50_000
    for handler in (_slow, _boom):
        monkeypatch.setattr(ChatContextResolver, "_dispatch", classmethod(lambda cls, h=handler: {"TICKER": h}))
        caplog.clear()
        with caplog.at_level(_logging.WARNING, logger="app.services.chat_context_resolver"):
            assert await resolver.resolve("TICKER", huge, client_context="ctx") == "ctx"
        rendered = [r.getMessage() for r in caplog.records
                    if r.name == "app.services.chat_context_resolver"]
        assert len(rendered) == 1
        assert "\n" not in rendered[0] and len(rendered[0]) < 500


# ── E1 review (2026-09-19): unmeasured guidance never reaches Cay AI as visible data ──

def test_unmeasured_guidance_is_stripped_from_the_report_dump():
    from app.services.chat_context_resolver import _without_unmeasured_guidance
    report = {"symbol": "TER", "revenue_forecast": {
        "cagr": 12.0, "management_guidance": "unknown", "guidance_quote": None,
        "guidance_speaker": None, "guidance_period": None, "projections": [],
    }, "core_thesis": {"bull_case": ["x"]}}
    out = _without_unmeasured_guidance(report)
    rf = out["revenue_forecast"]
    for key in ("management_guidance", "guidance_quote", "guidance_speaker", "guidance_period"):
        assert key not in rf
    assert rf["cagr"] == 12.0 and out["core_thesis"] == report["core_thesis"]
    # The caller's dict is untouched (the report is frozen data).
    assert report["revenue_forecast"]["management_guidance"] == "unknown"


@pytest.mark.parametrize("stance", ["raised", "maintained", "lowered"])
def test_a_read_stance_stays_in_the_report_dump(stance):
    from app.services.chat_context_resolver import _without_unmeasured_guidance
    report = {"revenue_forecast": {"management_guidance": stance, "guidance_quote": "We raise."}}
    assert _without_unmeasured_guidance(report) is report


@pytest.mark.parametrize("report", [None, "x", {}, {"revenue_forecast": None}, {"revenue_forecast": "x"}])
def test_guidance_strip_tolerates_garbage(report):
    from app.services.chat_context_resolver import _without_unmeasured_guidance
    assert _without_unmeasured_guidance(report) == report


# ── 2026-09-26: "Wall Street Consensus" → "Valuation & Institutions" ──
#
# The analyst half of the section (rating, price targets, rating distribution, momentum) is
# unlicensed FMP data the user no longer sees, and valuation_status / discount_percent /
# dcf_measured are verdict-shaped FMP-DCF leftovers. The dump is labelled "data the user can
# see", so none of it may reach the model — but ONLY inside that section: the flattener serves
# every screen and `rating` / `target_price` are generic names.

_ANALYST_ERA_WS = {
    "rating": "strong_buy", "current_price": 172.4, "target_price": 205.5,
    "low_target": 150.5, "high_target": 260.5, "valuation_status": "deep_undervalued",
    "discount_percent": 15.9, "dcf_measured": True, "dcf_source": "caydex",
    "momentum_upgrades": 6, "momentum_downgrades": 2, "momentum_maintains": 5,
    "analyst_strong_buy": 8, "analyst_buy": 22, "analyst_hold": 12, "analyst_sell": 2,
    "analyst_strong_sell": 1,
    "caydex_fair_value": {"symbol": "ORCL", "status": "ok", "fair_value": 180.25,
                          "range_low": 150.75, "range_high": 210.75, "analyst_years": 3},
    "hedge_fund_smart_money": {"net_flow_label": "INSTFLOWMARK"},
    "wall_street_insight": "WSINSIGHTMARK institutions added.",
}

_WS_DROPPED = ("rating", "target_price", "low_target", "high_target", "valuation_status",
               "discount_percent", "dcf_measured")


def _ws_keys(dump: str) -> list:
    """The key-paths of every grounding line that sits directly under wall_street_consensus."""
    prefix = "wall_street_consensus."
    return [line.split(":", 1)[0][len(prefix):] for line in dump.splitlines()
            if line.startswith(prefix)]


@pytest.mark.parametrize("fair", [False, True])
def test_flatten_drops_the_analyst_half_of_the_valuation_section_only(fair):
    payload = {
        "rating": "A+",                                          # top-level: another meaning
        "moat_competition": {"rating": "wide", "target_price": 99.5, "momentum_score": 7,
                             "analyst_note": "MOATNOTE", "valuation_status": "moatval"},
        "wall_street_consensus": dict(_ANALYST_ERA_WS),
    }
    out = _flatten_for_grounding(payload, 4000, fair=fair)
    keys = _ws_keys(out)
    for k in keys:
        assert k.split(".", 1)[0] not in _WS_DROPPED, k
        assert not k.startswith(("analyst_", "momentum_")), k
    for leaked in ("strong_buy", "205.5", "150.5", "260.5", "deep_undervalued", "15.9"):
        assert leaked not in out, leaked
    # Kept: the price, the published estimate (incl. its nested `analyst_years` — direct
    # children only), the 13F flow and the insight.
    assert "wall_street_consensus.current_price: 172.4" in out
    assert "wall_street_consensus.caydex_fair_value.fair_value: 180.25" in out
    assert "wall_street_consensus.caydex_fair_value.range_low: 150.75" in out
    assert "wall_street_consensus.caydex_fair_value.analyst_years: 3" in out
    assert "INSTFLOWMARK" in out and "WSINSIGHTMARK" in out
    # Anti-vacuity: the same names OUTSIDE the section are untouched.
    assert "rating: A+" in out.splitlines()
    assert "moat_competition.rating: wide" in out
    assert "moat_competition.target_price: 99.5" in out
    assert "moat_competition.momentum_score: 7" in out
    assert "moat_competition.analyst_note: MOATNOTE" in out
    assert "moat_competition.valuation_status: moatval" in out


@pytest.mark.parametrize("fair", [False, True])
def test_flatten_section_drop_is_case_insensitive_and_tolerates_garbage(fair):
    out = _flatten_for_grounding({"Wall_Street_Consensus": {"Rating": "buy", "Target_Price": 9.5,
                                                            "Momentum_Upgrades": 3, "keep": "yes"}}, 2000,
                                 fair=fair)
    assert out == "Wall_Street_Consensus.keep: yes"
    # A non-dict section (list of scalars / dicts) must not raise and is not filtered further.
    assert _flatten_for_grounding({"wall_street_consensus": ["a", "b"]}, 2000, fair=fair) == \
        "wall_street_consensus: a, b"
    assert "x: 1" in _flatten_for_grounding({"wall_street_consensus": [{"rating": "buy", "x": 1}]}, 2000,
                                            fair=fair)


@pytest.mark.asyncio
async def test_ticker_report_chat_never_sees_the_analyst_half(resolver, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DCF_ENABLED", True)

    async def fake_get(ticker, persona):
        return {"company_name": "Oracle", "executive_summary_text": "s", "rating": "TOPRATING",
                "wall_street_consensus": dict(_ANALYST_ERA_WS)}

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)
    block = await resolver.resolve("TICKER_REPORT", "ORCL|warren_buffett", None)
    for leaked in ("strong_buy", "205.5", "260.5", "deep_undervalued", "momentum_", "analyst_buy"):
        assert leaked not in block, leaked
    assert "180.25" in block and "TOPRATING" in block
    # The analyst-era insight was written for the analyst card ("Buy-rated with a $190 target…"):
    # the app and the PDF hide it, so the model must not see it either.
    assert "WSINSIGHTMARK" not in block


@pytest.mark.asyncio
async def test_ticker_report_chat_keeps_the_insight_written_beside_the_estimate(resolver, monkeypatch):
    """Anti-vacuity twin: with no analyst coverage and a published estimate, the insight IS
    the one on the user's screen and must ground the chat — so "drop every insight" fails."""
    from app.config import settings
    monkeypatch.setattr(settings, "DCF_ENABLED", True)
    ws = {k: v for k, v in _ANALYST_ERA_WS.items()
          if not k.startswith(("analyst_", "momentum_")) and k not in ("target_price", "low_target", "high_target")}

    async def fake_get(ticker, persona):
        return {"company_name": "Oracle", "executive_summary_text": "s", "wall_street_consensus": ws,
                "_scoring_inputs": {"wall_street": {"price_target": 777.25},
                                    "valuation": {"status": "SCORINGVERDICT"}},
                "key_vitals": {"valuation": {"status": "LEGACYVERDICT"}}}

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)
    block = await resolver.resolve("TICKER_REPORT", "ORCL|warren_buffett", None)
    assert "WSINSIGHTMARK" in block and "180.25" in block
    # Internal scoring inputs are not "data the user can see" (they re-label the estimate a
    # "price_target" and carry a valuation verdict), and the vendor stamp is not either.
    for leaked in ("777.25", "SCORINGVERDICT", "LEGACYVERDICT", "_scoring_inputs", "dcf_source"):
        assert leaked not in block, leaked


@pytest.mark.asyncio
async def test_ticker_report_chat_drops_a_withdrawn_estimates_insight(resolver, monkeypatch):
    """Kill switch through the chat door: a caydex-built report loses its estimate AND the
    insight that quotes it. An FMP-built one loses its insight too — since 2026-09-26 an insight
    is shown (app, PDF, chat) only beside the estimate it was written with."""
    from app.config import settings
    monkeypatch.setattr(settings, "DCF_ENABLED", False)
    reports = {
        "CAYX": {"company_name": "C", "executive_summary_text": "s",
                 "wall_street_consensus": dict(_ANALYST_ERA_WS)},
        "FMPX": {"company_name": "F", "executive_summary_text": "s",
                 "wall_street_consensus": {**_ANALYST_ERA_WS, "dcf_source": "fmp"}},
    }

    async def fake_get(ticker, persona):
        return reports[ticker]

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)
    caydex = await resolver.resolve("TICKER_REPORT", "CAYX|warren_buffett", None)
    assert "180.25" not in caydex and "WSINSIGHTMARK" not in caydex
    fmp = await resolver.resolve("TICKER_REPORT", "FMPX|warren_buffett", None)
    assert "180.25" not in fmp and "WSINSIGHTMARK" not in fmp


# ── TestFlight #57 (2026-09-26): the report chat could not see the report ────────────
#
# AVGO "Chat with the report": the report listed NVIDIA first; Cay AI said NVIDIA is not the
# main competitor, then that the report does not mention it. The dump split keys yes/no on
# `priority_top` and KEPT THE PAYLOAD'S ORDER — and a report read back from JSONB stores its keys
# shortest-first, so `macro_data` led and spent the budget before the moat, revenue and Wall
# Street sections. The competitor rows never reached the model at all. These tests build the
# report in JSONB key order, as production reads it.

import json as _json

import app.services.chat_context_resolver as _ccr


def _jsonb_order(obj):
    """Re-key every object the way Postgres JSONB stores it: by key LENGTH, then bytewise."""
    if isinstance(obj, dict):
        return {k: _jsonb_order(obj[k])
                for k in sorted(obj, key=lambda k: (len(k.encode()), k.encode()))}
    if isinstance(obj, list):
        return [_jsonb_order(x) for x in obj]
    return obj


_OLD_AVGO_ROWS = [   # the tester's report: score-ordered, no order marker, no segment/basis
    {"name": "NVIDIA Corporation", "ticker": "NVDA", "threat_level": "high",
     "competitive_score": 9.0, "market_share_percent": 0.0},
    {"name": "Marvell Technology, Inc.", "ticker": "MRVL", "threat_level": "high",
     "competitive_score": 7.1, "market_share_percent": 0.0},
    {"name": "Microsoft Corporation", "ticker": "MSFT", "threat_level": "moderate",
     "competitive_score": 6.2, "market_share_percent": 0.0},
    {"name": "Intel Corp.", "ticker": "INTC", "threat_level": "moderate",
     "competitive_score": 4.4, "market_share_percent": 0.0},
    {"name": "Alphabet Inc.", "ticker": "GOOGL", "threat_level": "moderate",
     "competitive_score": 3.3, "market_share_percent": 0.0},
]


# A published estimate with EVERY `DcfFairValueResponse` field, as the report stores it — the
# headline (fair_value 180.25) sorts 9th of the 24 set fields in JSONB order (key length
# first), behind beta / as_of / method / status / symbol / currency / range_low.
_AVGO_DCF = {
    "symbol": "AVGO", "status": "ok", "model_version": "dcf-v1",
    "refusal_code": None, "refusal_reason": None,
    "fair_value": 180.25, "range_low": 150.75, "range_high": 210.75,
    "alternative_value": 171.5, "currency": "USD", "method": "earnings",
    "discount_rate_pct": 9.26, "terminal_growth_pct": 2.5, "risk_free_pct": 4.18,
    "equity_risk_premium_pct": 4.23, "beta": 1.2, "analyst_years": 3, "analysts_min": 5,
    "terminal_share_pct": 61.4, "cash_conversion": 0.92, "fcf_margin_pct": 38.1,
    "sbc_status": "deducted", "shares_diluted": 4_912_000_000.0,
    "last_reported_fiscal_year_end": "2025-11-02", "as_of": "2026-09-22",
    "notes": ["Stock-based compensation is deducted from free cash flow."],
}


def test_the_avgo_dcf_fixture_is_a_full_published_estimate():
    """The fixture stays realistic: it validates, and carries every field the schema has."""
    from app.schemas.dcf_fair_value import DcfFairValueResponse
    DcfFairValueResponse.model_validate(_AVGO_DCF)
    assert set(DcfFairValueResponse.model_fields) == set(_AVGO_DCF)


def _avgo_shaped_report(competitors=None, **moat_extra):
    """A synthetic report with the SHAPE and section sizes of the stored AVGO reports (2026-09-22):
    ~2.7k chars of flattened macro, ~6k insider, ~8k signals / Wall Street, ~6.5k moat."""
    words = lambda tag, n: f"{tag} " + " ".join(f"w{i}" for i in range(n))   # noqa: E731
    return _jsonb_order({
        "agent": "bill_ackman", "symbol": "AVGO", "exchange": "NASDAQ",
        "logo_url": "https://example.com/avgo.png",
        "live_date": "As of Sep 22, 2026 close", "price_close_date": "2026-09-22",
        "company_name": "Broadcom Inc.", "quality_score": 62,
        "disclaimer_text": "Educational only.",
        "executive_summary_text": words("SUMMARYMARK", 60),
        "price_action": {"narrative": words("MOVEMARK", 30), "change_pct": -14.8,
                         "window_label": "Last 45 Days", "tag": "Typical"},
        "core_thesis": {"bull_case": [words("BULLMARK", 18), words("bull2", 18)],
                        "bear_case": [words("BEARMARK", 18), words("bear2", 18), words("bear3", 18)]},
        "overall_assessment": {"text": words("ASSESSMARK", 70), "average_rating": 3.4,
                               "strong_count": 4, "weak_count": 1},
        "macro_data": {
            "headline": "High macro risk — led by Elevated Inflation (6 active factors).",
            "last_updated": "2026-09-22",
            "overall_threat_level": "high",
            "intelligence_brief": words("BRIEFMARK", 60),
            "risk_factors": [{"category": "inflation", "title": f"Risk {i}", "impact": 7.5,
                              "trend": "worsening", "severity": "high",
                              "description": words(f"riskdesc{i}", 40)} for i in range(6)],
        },
        "insider_data": {
            "sentiment": "negative", "timeframe": "Last 12 Months",
            "transactions": [{"type": f"T{i}", "count": i * 7, "value": f"${i}.5M",
                              "shares": f"{i}K"} for i in range(12)],
            "recent_activity": [{"name": f"Insider {i}", "title": "Director", "shares": 1000 + i,
                                 "value": 123456.75 + i, "date": "2026-08-01"} for i in range(12)],
        },
        "key_management": {
            "ownership_insight": words("OWNMARK", 60),
            "officers": [{"name": f"Officer {i}", "title": "VP", "ownership": 1000.5 + i,
                          "percent_owned": 0.01, "ownership_value": 1234567.0 + i} for i in range(10)],
        },
        "revenue_engine": {
            "period": "FY 2026", "revenue_unit": "Billions", "total_revenue": 63887,
            "analysis_note": words("ENGINEMARK", 20),
            "segments": [{"name": f"Segment {i}", "total_revenue": 1000.25 * i,
                          "current_revenue": 900.5 * i, "previous_revenue": 800.75 * i}
                         for i in range(6)],
        },
        "revenue_forecast": {"cagr": 57.8, "insight": words("FORECASTMARK", 60), "eps_growth": 59.5,
                             "beat_summary": "Beat 6 of 8", "management_guidance": "unknown",
                             "projections": [{"period": "2027", "revenue": 1.0}] * 8},
        "critical_factors": [{"title": f"Factor {i}", "watch": words(f"watch{i}", 20),
                              "severity": "high", "description": words(f"factor{i}", 22)}
                             for i in range(4)],
        "moat_competition": {
            "dimensions": [{"name": f"Dim {d}", "score": 7.5, "source": "deterministic",
                            "confidence": "high", "peer_score": 5.0,
                            "drivers": [{"metric": f"m{j}", "focal": 0.3988917933225852 + j,
                                         "sub_score": 9.89, "sector_median": 0.0707,
                                         "period_used": "2026", "sample_size": 81}
                                        for j in range(3)]} for d in range(5)],
            "competitors": competitors if competitors is not None else _json.loads(_json.dumps(_OLD_AVGO_ROWS)),
            "durability_note": words("DURABLEMARK", 60),
            "market_dynamics": {"cagr_5yr": 6.34, "industry": "Semiconductors", "tam_scope": "global",
                                "future_tam": 954.76, "current_tam": 702.44, "future_year": "2030",
                                "current_year": "2025", "source_grain": "industry",
                                "concentration": "oligopoly", "lifecycle_phase": "mature",
                                "tam_source_label": "VENDORLABEL Intelligence / Vendor Two",
                                "tam_source_quote": "VENDORQUOTE the market will reach…"},
            "competitive_insight": words("INSIGHTMARK", 15),
            **moat_extra,
        },
        "hidden_market_signals": {
            "insight": words("SIGNALMARK", 55),
            "congress": {"period": "Last 12 Months", "num_buyers": 10, "num_sellers": 7,
                         "trades": [{"date": "2026-08-01", "type": "buy", "amount": "$1K-$15K",
                                     "party": "?"} for _ in range(12)]},
            "short_interest": {"percent_float": 1.1, "trend": [1.0 + i / 10 for i in range(12)]},
        },
        "wall_street_consensus": {
            "current_price": 364.54,
            "caydex_fair_value": _json.loads(_json.dumps(_AVGO_DCF)),
            "wall_street_insight": words("WSMARK", 30),
            "hedge_fund_smart_money": {"tab": "Institutions", "summary": {
                "total_buy": 1263.38, "total_sell": 1105.76, "is_positive": True,
                "period_description": "Last 8 quarters"},
                "holders": [{"name": f"Fund {i}", "shares": 1000 * i, "change": 12.5 * i}
                            for i in range(12)]},
        },
        "fundamental_metrics": [{"title": f"Card{c}", "metrics": [
            {"name": f"Metric{c}{m}", "value": "45%", "sector_value": "12%"} for m in range(6)]}
            for c in range(6)],
        "_scoring_inputs": {"wall_street": {"price_target": 777.25}},
    })


async def _resolve_report(resolver, monkeypatch, report, ref="AVGO|bill_ackman"):
    from app.config import settings
    monkeypatch.setattr(settings, "DCF_ENABLED", True)

    async def fake_get(ticker, persona):
        return report

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)
    return await resolver.resolve("TICKER_REPORT", ref, None)


def _dump_of(block: str) -> str:
    return block.split("(an excerpt; long sections are shortened):\n", 1)[1].rsplit(
        "\nAnswer grounded in THIS report", 1)[0]


_NARRATIVE_MARKERS = (
    "moat_competition.durability_note: DURABLEMARK",
    "revenue_engine.analysis_note: ENGINEMARK",
    "wall_street_consensus.wall_street_insight: WSMARK",
    "key_management.ownership_insight: OWNMARK",
    "macro_data.intelligence_brief: BRIEFMARK",
)


@pytest.mark.asyncio
async def test_every_report_narrative_survives_jsonb_key_order(resolver, monkeypatch):
    block = await _resolve_report(resolver, monkeypatch, _avgo_shaped_report())
    dump = _dump_of(block)
    for marker in _NARRATIVE_MARKERS:
        assert marker in dump, marker
    for marker in ("ASSESSMARK", "FORECASTMARK", "SIGNALMARK", "macro_data.headline"):
        assert marker in dump, marker
    # The thesis rides in the lead (both halves), the summary and the date too.
    assert "Bull case: BULLMARK" in block and "Bear case: BEARMARK" in block
    assert "SUMMARYMARK" in block and "Report dated 2026-09-22." in block
    assert len(dump) <= _ccr._REPORT_DUMP_CAP


def test_the_avgo_fixture_reproduces_the_starvation_bug():
    """Anti-vacuity: the fixture starves the old dump (2800, yes/no priority, first-come)."""
    old_priority = ("core_thesis", "overall_assessment", "revenue_forecast", "revenue_engine",
                    "moat_competition", "macro_data", "wall_street_consensus", "insider_data",
                    "key_management", "hidden_market_signals", "critical_factors")
    report = _avgo_shaped_report()
    old_order = {k: report[k] for k in report}   # JSONB order, as the old sort kept it
    old_order = dict(sorted(old_order.items(),
                            key=lambda kv: 0 if kv[0] in old_priority else 1))
    old = _flatten_for_grounding(old_order, 2800)   # the old call: no rank, no fairness
    assert sum(m not in old for m in _NARRATIVE_MARKERS) >= 3
    assert old.splitlines()[0].startswith("macro_data.")


@pytest.mark.asyncio
async def test_report_sections_follow_the_priority_order(resolver, monkeypatch):
    """ORDERED priority: macro (shortest key, first in JSONB) is emitted LAST. Reverting the
    rank sort to the old yes/no split (payload order among priority keys) fails here."""
    dump = _dump_of(await _resolve_report(resolver, monkeypatch, _avgo_shaped_report()))
    firsts = []
    for line in dump.splitlines():
        top = line.split(":", 1)[0].split(".", 1)[0].split("[", 1)[0]
        if top not in firsts:
            firsts.append(top)
    ranked = [s for s in _ccr._REPORT_PRIORITY if s in firsts]
    assert firsts[:len(ranked)] == ranked, firsts
    assert ranked[-1] == "macro_data"


# The headline figures one level down: Cay's fair value and the moat pillar scores. Pass 2
# hands a full report's section only ~2 lines, so they must lead their own dicts.
_HEADLINE_FIGURES = (
    "wall_street_consensus.caydex_fair_value.fair_value: 180.25",
    "wall_street_consensus.caydex_fair_value.range_low: 150.75",
    "wall_street_consensus.caydex_fair_value.range_high: 210.75",
    "moat_competition.dimensions[0].name: Dim 0",
    "moat_competition.dimensions[0].score: 7.5",
)

# The map before 2026-10-01: a section's DIRECT children only, market_dynamics second.
_OLD_CHILD_PRIORITY = {
    "moat_competition": ("durability_note", "market_dynamics", "competitive_insight", "dimensions"),
    "macro_data": ("headline", "overall_threat_level", "intelligence_brief", "risk_factors",
                   "last_updated"),
}


@pytest.mark.asyncio
async def test_the_fair_value_and_pillar_scores_survive_a_full_report(resolver, monkeypatch):
    """F1 (2026-10-01 review): "What is Cay's fair value?" / "How strong is each moat pillar?"
    were answered "not included here" — the figures sorted behind JSONB trivia one level down.
    Since 2026-10-08 they ride in the figures lead, labelled, and leave the dump (said once)."""
    block = await _resolve_report(resolver, monkeypatch, _avgo_shaped_report())
    head = block.split("Report data the user can see", 1)[0]
    lines = _dump_of(block).splitlines()
    assert ("Caydex fair value: 180.25 USD per share (Caydex model estimate, not a price "
            "target); range 150.75–210.75; as of 2026-09-22") in head
    for d in range(5):
        assert f"Dim {d} 7.5 vs peers 5" in head, d
    assert not any("caydex_fair_value" in l or ".dimensions" in l for l in lines)
    # Every narrative still shows (the per-section share is unchanged).
    for marker in _NARRATIVE_MARKERS:
        assert marker in "\n".join(lines), marker
    # A pillar's per-metric drivers and confidence are on no screen: never in the block.
    for hidden in (".drivers", ".confidence", "sub_score", "0.3988", "sector_median"):
        assert hidden not in block, hidden
    assert len("\n".join(lines)) <= _ccr._REPORT_DUMP_CAP


def _direct_report_flatten(report, child_priority):
    """The resolver's dump call WITHOUT the figures prune, at the pre-2026-10-08 cap: what the
    child-priority map does to a block the figures lead did not take."""
    return _flatten_for_grounding(report, 3600, skip_top=_ccr._REPORT_SKIP_TOP,
                                  priority_top=_ccr._REPORT_PRIORITY, fair=True,
                                  child_priority=child_priority).splitlines()


def test_the_child_priority_still_leads_each_block_with_its_headline_figure():
    """The map's own contract, kept for a direct flatten of a report: the estimate and the
    pillar scores lead their dicts — and the old (direct-children-only) map loses both."""
    lines = _direct_report_flatten(_avgo_shaped_report(), _ccr._REPORT_CHILD_PRIORITY)
    for figure in _HEADLINE_FIGURES:
        assert figure in lines, figure
    dcf = [l for l in lines if l.startswith("wall_street_consensus.caydex_fair_value.")]
    assert dcf[0] == _HEADLINE_FIGURES[0], dcf
    old = _direct_report_flatten(_avgo_shaped_report(), _OLD_CHILD_PRIORITY)
    assert _HEADLINE_FIGURES[0] not in old
    assert not any(l.startswith("moat_competition.dimensions[0].score") for l in old)
    assert "wall_street_consensus.caydex_fair_value.beta: 1.2" in old   # the trivia that won


@pytest.mark.asyncio
async def test_the_figures_reach_the_model_only_through_the_lead(resolver, monkeypatch):
    """Anti-vacuity for the prune: with the figures lead switched off, none of the figures it
    carries is anywhere in the block — the dump no longer holds a copy."""
    monkeypatch.setattr(_ccr, "_report_figures_lead", lambda report: [])
    block = await _resolve_report(resolver, monkeypatch, _avgo_shaped_report())
    for figure in ("180.25", "150.75", "Dim 0", "Segment 5", "4,502.5", "Officer 9", "Metric00",
                   "57.8", "59.5", "Beat 6 of 8", "63,887", "FY 2026"):
        assert figure not in block, figure
    for marker in _NARRATIVE_MARKERS:   # the narratives are untouched
        assert marker in block, marker


def test_child_priority_matches_dotted_paths_at_any_depth():
    payload = {"Sec": {"zz": "first-by-payload", "Inner": {"b": 2, "a": 1, "deep": {"y": 1, "x": 2}},
                       "rows": [{"t": "T0", "s": 0}, "bare", None, {"s": 1, "t": "T1"}]}}
    prio = {"sec": ("inner", "rows"), "SEC.inner": ("a",), "sec.inner.deep": ("x",),
            "sec.rows[*]": ("s",), "sec.nowhere[*]": ("q",)}
    out = _flatten_for_grounding(payload, 4000, priority_top=("sec",), fair=True,
                                 child_priority=prio).splitlines()
    assert out == [
        "Sec.Inner.a: 1", "Sec.Inner.b: 2", "Sec.Inner.deep.x: 2", "Sec.Inner.deep.y: 1",
        "Sec.rows[0].s: 0", "Sec.rows[0].t: T0", "Sec.rows[1]: bare",
        "Sec.rows[3].s: 1", "Sec.rows[3].t: T1", "Sec.zz: first-by-payload",
    ]
    # A path whose node is not a dict, and a list at a dict's path, are walked as before.
    odd = {"sec": {"inner": ["a", "b"], "rows": "text"}}
    assert _flatten_for_grounding(odd, 4000, priority_top=("sec",), fair=True,
                                  child_priority=prio).splitlines() == \
        ["sec.inner: a, b", "sec.rows: text"]
    # Only fair mode reorders: the default flattener keeps payload order, map or not.
    assert _flatten_for_grounding(payload, 4000, child_priority=prio).splitlines()[0] == \
        "Sec.zz: first-by-payload"


@pytest.mark.parametrize("dcf, first", [
    # A refusal carries no figures, so its reason leads.
    ({"symbol": "AVGO", "status": "refused", "refusal_code": "negative_earnings",
      "refusal_reason": "REFUSALMARK earnings are negative.", "as_of": "2026-09-22"},
     "wall_street_consensus.caydex_fair_value.refusal_reason: REFUSALMARK earnings are negative."),
    # A NaN headline is no figure: the range leads, never "nan".
    ({**_AVGO_DCF, "fair_value": float("nan")},
     "wall_street_consensus.caydex_fair_value.range_low: 150.75"),
    # Malformed blocks never raise.
    ("not-a-dict", "wall_street_consensus.caydex_fair_value: not-a-dict"),
    ([{"fair_value": 1.5}], "wall_street_consensus.caydex_fair_value[0].fair_value: 1.5"),
])
def test_the_estimate_block_leads_with_what_it_has(dcf, first):
    payload = _jsonb_order({"wall_street_consensus": {"current_price": 364.54,
                                                      "caydex_fair_value": dcf}})
    out = _flatten_for_grounding(payload, 4000, priority_top=_ccr._REPORT_PRIORITY, fair=True,
                                 child_priority=_ccr._REPORT_CHILD_PRIORITY).splitlines()
    assert out[0] == first, out
    assert not any(l.lower().endswith(": nan") for l in out)
    assert out[-1] == "wall_street_consensus.current_price: 364.54"


@pytest.mark.parametrize("dims", [
    [None, "x", 5, [], {"score": float("inf"), "name": "Pillar"}, {"name": "Real", "score": 6.5}],
    {"name": "not-a-list", "score": 4.0},
    "garbage",
])
def test_malformed_moat_pillars_never_raise(dims):
    payload = {"moat_competition": {"durability_note": "DURABLE", "dimensions": dims}}
    out = _flatten_for_grounding(payload, 4000, priority_top=_ccr._REPORT_PRIORITY, fair=True,
                                 child_priority=_ccr._REPORT_CHILD_PRIORITY)
    assert out.splitlines()[0] == "moat_competition.durability_note: DURABLE"
    assert not any(l.lower().endswith((": inf", ": nan")) for l in out.splitlines())


@pytest.mark.asyncio
async def test_report_dump_hides_competitor_rows_vendor_and_market_share(resolver, monkeypatch):
    block = await _resolve_report(resolver, monkeypatch, _avgo_shaped_report())
    dump = _dump_of(block)
    assert "market_share_percent" not in block
    assert "moat_competition.competitors" not in dump
    for leaked in ("VENDORLABEL", "VENDORQUOTE", "tam_source", "777.25", "_scoring_inputs"):
        assert leaked not in block, leaked
    # …while the rows themselves ARE in the block, through the lead.
    assert "1. NVIDIA Corporation (NVDA) — threat High, 9.0" in block


@pytest.mark.parametrize("fair", [False, True])
def test_the_moat_drops_are_scoped_to_the_moat_section(fair):
    """Anti-vacuity: the same names elsewhere are not dropped."""
    payload = {
        "moat_competition": {"competitors": [{"ticker": "AAA"}], "keep": "MOATKEEP",
                             "market_dynamics": {"tam_source_label": "VENDOR", "cagr_5yr": 6.5},
                             "dimensions": [{"drivers": [{"tam_source_quote": "QUOTEVENDOR"}]}]},
        "peers": {"competitors": [{"ticker": "BBB"}], "tam_source_label": "ELSEWHERE"},
    }
    out = _flatten_for_grounding(payload, 4000, fair=fair)
    assert "AAA" not in out and "VENDOR" not in out.replace("ELSEWHERE", "") \
        and "QUOTEVENDOR" not in out
    assert "MOATKEEP" in out and "moat_competition.market_dynamics.cagr_5yr: 6.5" in out
    assert "peers.competitors[0].ticker: BBB" in out and "peers.tam_source_label: ELSEWHERE" in out


@pytest.mark.parametrize("fair", [False, True])
def test_a_pillars_hidden_drivers_and_confidence_drop_inside_the_moat_only(fair):
    """iOS decodes a pillar's name / score / peer_score / source only; `drivers` and
    `confidence` are on no screen. Generic names, so the drop is moat-scoped."""
    payload = {
        "moat_competition": {"dimensions": [
            {"name": "Pricing Power", "score": 8.25, "peer_score": 5.5, "source": "deterministic",
             "confidence": "high", "drivers": [{"metric": "DRIVERMARK", "sub_score": 9.89}]}]},
        "elsewhere": {"confidence": "KEPTCONF", "drivers": ["KEPTDRIVER"]},
    }
    out = _flatten_for_grounding(payload, 4000, fair=fair)
    assert "DRIVERMARK" not in out and "9.89" not in out and "confidence: high" not in out
    for kept in ("moat_competition.dimensions[0].name: Pricing Power",
                 "moat_competition.dimensions[0].score: 8.25",
                 "moat_competition.dimensions[0].peer_score: 5.5",
                 "moat_competition.dimensions[0].source: deterministic",
                 "elsewhere.confidence: KEPTCONF", "elsewhere.drivers: KEPTDRIVER"):
        assert kept in out, kept


# ── the competitor lead ──────────────────────────────────────────────────────

_DIRECT_ROWS = [
    {"name": "Marvell Technology, Inc.", "ticker": "MRVL", "competitive_score": 6.8,
     "threat_level": "moderate", "market_share_percent": 0.0,
     "segment": "Custom AI accelerators & AI data-center networks", "score_basis": "relative"},
    {"name": "QUALCOMM Incorporated", "ticker": "QCOM", "competitive_score": 5.9,
     "threat_level": "moderate", "market_share_percent": 0.0,
     "segment": "Wi-Fi, Bluetooth & RF chips", "score_basis": "relative"},
    {"name": "NVIDIA Corporation", "ticker": "NVDA", "competitive_score": 9.0,
     "threat_level": "high", "market_share_percent": 0.0,
     "segment": "AI data-center networking", "score_basis": "relative"},
]


def test_the_direct_lead_verbatim():
    assert len(_DIRECT_ROWS[0]["segment"]) == 48          # exactly the cap: never cut
    report = {"moat_competition": {"competitor_order": "direct", "competitor_source": "research",
                                   "competitors": _DIRECT_ROWS}}
    assert _ccr._competitor_lead(report) == [
        "Competitors on the report, most direct first:",
        "1. Marvell Technology, Inc. (MRVL) — competes in: Custom AI accelerators & "
        "AI data-center networks — threat Moderate, 6.8",
        "2. QUALCOMM Incorporated (QCOM) — competes in: Wi-Fi, Bluetooth & RF chips — "
        "threat Moderate, 5.9",
        "3. NVIDIA Corporation (NVDA) — competes in: AI data-center networking — threat High, 9.0",
        "Threat score (0-10): blends how directly the rival competes (Cay's research order) with "
        "its return on invested capital vs the company's, scaled by moat; 5 is a neutral midpoint.",
        "Threat badge: High at 7.0 or above, Low at 3.0 or below, Moderate in between.",
        "How the list was built: Cay's web research into filings and public coverage.",
    ]


@pytest.mark.asyncio
async def test_the_direct_lead_reaches_the_block_before_the_dump(resolver, monkeypatch):
    report = _avgo_shaped_report(competitors=_DIRECT_ROWS, competitor_order="direct",
                                 competitor_source="research")
    block = await _resolve_report(resolver, monkeypatch, report)
    assert "Competitors on the report, most direct first:" in block
    assert block.index("most direct first") < block.index("(an excerpt;")
    assert "competes in: Wi-Fi, Bluetooth & RF chips" in block


@pytest.mark.parametrize("marker", [None, "threat", "Direct", " direct", "DIRECT", "most_direct",
                                   1, True, ["direct"], {"order": "direct"}])
def test_a_report_without_the_exact_direct_marker_never_says_most_direct(marker):
    """Every report stored before the marker existed (the tester's included) is SCORE-ordered."""
    mc = {"competitors": _json.loads(_json.dumps(_OLD_AVGO_ROWS))}
    if marker is not None:
        mc["competitor_order"] = marker
    lead = _ccr._competitor_lead({"moat_competition": mc})
    text = "\n".join(lead)
    assert "most direct" not in text.lower()
    assert lead[0] == "Competitors on the report, in the order shown, highest threat score first:"
    # Old rows carry no score_basis / source → only the either/or sentences that are true of
    # both scoring paths and both sources; never a badge legend (pre-2026-05-28 thresholds).
    assert _ccr._SCORE_UNKNOWN_BASIS_TEXT in lead
    assert _ccr._COMPETITOR_SOURCE_UNKNOWN_TEXT in lead
    assert not any(l.startswith("Threat badge") for l in lead)


@pytest.mark.asyncio
async def test_the_prod_shaped_old_report_reads_threat_first(resolver, monkeypatch):
    block = await _resolve_report(resolver, monkeypatch, _avgo_shaped_report())
    assert "Competitors on the report, in the order shown, highest threat score first:" in block
    assert "most direct" not in block.lower()


@pytest.mark.asyncio
async def test_an_old_report_still_explains_how_the_list_and_score_were_built(resolver, monkeypatch):
    """TestFlight #57 asked "how did we get the competitors?" on a report stored before the
    rows carried `score_basis` / the list carried `competitor_source`. The lead must still
    answer it, with sentences true of BOTH scoring paths and BOTH sources — and with no
    badge legend (thresholds before 2026-05-28 differed)."""
    block = await _resolve_report(resolver, monkeypatch, _avgo_shaped_report())
    assert _ccr._SCORE_UNKNOWN_BASIS_TEXT in block
    assert _ccr._COMPETITOR_SOURCE_UNKNOWN_TEXT in block
    assert "Threat badge:" not in block
    assert "most direct" not in block.lower()


def test_the_either_or_sentences_appear_only_when_the_report_does_not_say():
    rows = [{"name": "Rival", "ticker": "RVL", "competitive_score": 7.5, "threat_level": "high",
             "score_basis": "relative"}]
    new = _ccr._competitor_lead({"moat_competition": {
        "competitors": rows, "competitor_order": "direct", "competitor_source": "research"}})
    assert _ccr._SCORE_UNKNOWN_BASIS_TEXT not in new
    assert _ccr._COMPETITOR_SOURCE_UNKNOWN_TEXT not in new
    assert any(line.startswith("Threat badge:") for line in new)
    # A "direct" list whose rows lost their basis keeps quiet rather than guess.
    bare = [{k: v for k, v in rows[0].items() if k != "score_basis"}]
    direct = _ccr._competitor_lead({"moat_competition": {
        "competitors": bare, "competitor_order": "direct", "competitor_source": "research"}})
    assert _ccr._SCORE_UNKNOWN_BASIS_TEXT not in direct
    # A non-string source is "unknown", not a key lookup crash.
    odd = _ccr._competitor_lead({"moat_competition": {"competitors": bare, "competitor_source": 5}})
    assert _ccr._COMPETITOR_SOURCE_UNKNOWN_TEXT in odd
    for text in (_ccr._SCORE_UNKNOWN_BASIS_TEXT, _ccr._COMPETITOR_SOURCE_UNKNOWN_TEXT):
        assert "most direct" not in text.lower()
        for banned in ("gemini", "google", "openai", "fmp", "financial modeling prep"):
            assert banned not in text.lower()


def test_the_unknown_basis_sentence_never_both_asserts_and_denies_directness():
    """F3 (2026-10-01 review): it said the score "blends how directly the rival competes" and
    then that it "does not measure which rival's business is closest" — the model could tell
    the user either. On these reports the rank input is the row's place in its source list,
    not a measured directness, so the sentence claims none; and it holds whatever the row
    order (the head line says whether the scores descend)."""
    text = _ccr._SCORE_UNKNOWN_BASIS_TEXT
    low = text.lower()
    for banned in ("most direct", "how directly", "closest", "the first row", "most-direct"):
        assert banned not in low, banned
    assert text.startswith("Threat score (0-10): a threat measure")
    assert "the rival's place in the list it came from" in text
    assert "operating margin, ROE and revenue growth vs its own sector median" in text
    assert low.endswith("a higher score means a bigger threat, not necessarily a closer rival.")


@pytest.mark.parametrize("rows, says", [
    # Before 2026-05-27 the rows carried `moat_score` (absolute path only, ±1.5 badge): the
    # lead shows no score, so there is no score to explain.
    ([{"name": "NVIDIA Corporation", "ticker": "NVDA", "moat_score": 8.5, "threat_level": "high",
       "market_share_percent": 0.0},
      {"name": "Intel Corp.", "ticker": "INTC", "moat_score": 4.0, "threat_level": "low",
       "market_share_percent": 0.0}], False),
    # A score that is not a real 0-10 number is no score either.
    ([{"ticker": "AAA", "competitive_score": float("nan"), "threat_level": "high"},
      {"ticker": "BBB", "competitive_score": True, "threat_level": "low"},
      {"ticker": "CCC", "competitive_score": 11.0, "threat_level": "low"},
      {"ticker": "DDD", "competitive_score": "7.5", "threat_level": "low"}], False),
    # One row with a real score is enough: that score is on screen.
    ([{"ticker": "AAA", "moat_score": 8.0, "threat_level": "high"},
      {"ticker": "BBB", "competitive_score": 6.0, "threat_level": "moderate"}], True),
    ([{"ticker": "AAA", "competitive_score": 0, "threat_level": "low"}], True),
])
def test_the_unknown_basis_sentence_needs_a_real_score_on_the_rows(rows, says):
    lead = _ccr._competitor_lead({"moat_competition": {"competitors": rows}})
    assert (_ccr._SCORE_UNKNOWN_BASIS_TEXT in lead) is says
    # The list still says how it was built, and never claims an order it cannot know.
    assert _ccr._COMPETITOR_SOURCE_UNKNOWN_TEXT in lead
    assert "most direct" not in "\n".join(lead).lower()
    assert not any(line.startswith("Threat badge") for line in lead)


def test_unsorted_scores_without_a_marker_claim_no_ordering():
    rows = list(reversed(_json.loads(_json.dumps(_OLD_AVGO_ROWS))))
    assert _ccr._competitor_lead({"moat_competition": {"competitors": rows}})[0] == \
        "Competitors on the report, in the order shown:"
    rows[2]["competitive_score"] = float("nan")       # an unknown score breaks the claim too
    assert _ccr._competitor_lead({"moat_competition": {"competitors": rows[::-1]}})[0] == \
        "Competitors on the report, in the order shown:"


@pytest.mark.parametrize("moat", [None, "x", 5, [], {}, {"competitors": None},
                                  {"competitors": "NVDA"}, {"competitors": {"ticker": "NVDA"}},
                                  {"competitors": []}, {"competitors": [None, 1, "NVDA", [], {}]}])
def test_competitor_lead_tolerates_garbage(moat):
    assert _ccr._competitor_lead({"moat_competition": moat}) == []


@pytest.mark.parametrize("score, shown", [
    (True, None), (False, None), (float("nan"), None), (float("inf"), None),
    (float("-inf"), None), (-0.1, None), (10.01, None), (10 ** 400, None), ("9.0", None),
    (None, None), (0, "0.0"), (10, "10.0"), (7.04, "7.0"), (6.96, "7.0"),
])
def test_only_a_real_0_to_10_score_is_shown(score, shown):
    row = {"name": "Rival", "ticker": "RVL", "competitive_score": score, "threat_level": "high"}
    line = _ccr._competitor_lead({"moat_competition": {"competitors": [row]}})[1]
    if shown is None:
        assert line == "1. Rival (RVL) — threat High"
    else:
        assert line == f"1. Rival (RVL) — threat High, {shown}"


@pytest.mark.parametrize("threat, label", [
    ("high", "High"), ("HIGH", "High"), ("moderate", "Moderate"), ("Moderate", "Moderate"),
    ("low", "Low"), ("severe", "Low"), ("", "Low"), (None, "Low"), (5, "Low"), (["high"], "Low"),
])
def test_the_threat_label_is_the_badge_ios_draws(threat, label):
    row = {"ticker": "RVL", "threat_level": threat}
    assert _ccr._competitor_lead({"moat_competition": {"competitors": [row]}})[1] == \
        f"1. RVL — threat {label}"


@pytest.mark.parametrize("ticker", [None, "", "   ", 5, ["NVDA"], "NV DA", "NVDA\nIGNORE", "X" * 16,
                                    "$NVDA"])
def test_a_row_without_a_usable_ticker_is_skipped(ticker):
    rows = [{"name": "Bad", "ticker": ticker, "threat_level": "high"},
            {"name": "Good Co", "ticker": "good", "threat_level": "low"}]
    lead = _ccr._competitor_lead({"moat_competition": {"competitors": rows}})
    assert lead[1:2] == ["1. Good Co (GOOD) — threat Low"]
    assert not any(line.startswith("2.") for line in lead)


def test_names_and_segments_are_bounded_single_line_text():
    row = {"name": "Big\nName Co " + "n" * 3000, "ticker": "BIG",
           "segment": "Line one\r\nline two " + "s " * 1000, "threat_level": "high",
           "competitive_score": 7.5}
    line = _ccr._competitor_lead({"moat_competition": {"competitors": [row]}})[1]
    assert "\n" not in line and "\r" not in line and " " not in line
    name = line.split(" (BIG)")[0][len("1. "):]
    segment = line.split("competes in: ", 1)[1].split(" — threat", 1)[0]
    assert len(name) <= _ccr._COMPETITOR_NAME_CAP + 1
    assert len(segment) <= _ccr._COMPETITOR_SEGMENT_CAP + 1 and segment.startswith("Line one line two")
    assert line.endswith("— threat High, 7.5")


def test_at_most_seven_rows():
    rows = [{"ticker": f"T{i}", "threat_level": "low", "competitive_score": 10 - i} for i in range(20)]
    lead = _ccr._competitor_lead({"moat_competition": {"competitors": rows}})
    assert [l for l in lead if l[:1].isdigit()][-1].startswith("7. T6")
    assert len([l for l in lead if l[:1].isdigit()]) == _ccr._COMPETITOR_LEAD_MAX_ROWS


def test_a_mixed_basis_names_the_rows_each_sentence_covers():
    rows = [
        {"ticker": "AAA", "threat_level": "high", "competitive_score": 8.0, "score_basis": "relative"},
        {"ticker": "BBB", "threat_level": "moderate", "competitive_score": 5.0, "score_basis": "absolute"},
        {"ticker": "CCC", "threat_level": "low", "competitive_score": 2.0, "score_basis": "relative"},
        {"ticker": "DDD", "threat_level": "low", "competitive_score": 1.0},              # no basis
        {"ticker": "EEE", "threat_level": "low", "competitive_score": 0.5, "score_basis": "bogus"},
    ]
    lead = _ccr._competitor_lead({"moat_competition": {
        "competitors": rows, "competitor_order": "threat", "competitor_source": "industry_peers"}})
    assert lead[0] == "Competitors on the report, in the order shown, highest threat score first:"
    assert ("Threat score for AAA, CCC (0-10): blends the rival's place in the industry peer list "
            "with its return on invested capital vs the company's, scaled by moat; 5 is a neutral "
            "midpoint.") in lead
    assert ("Threat score for BBB (0-10): operating margin, ROE and revenue growth vs the rival's "
            "own sector median (5 = median).") in lead
    assert "How the list was built: same-industry peers." in lead
    assert "Cay's research order" not in "\n".join(lead)   # the heuristic list is not research


def test_the_lead_names_no_model_or_vendor():
    for source in ("research", "industry_peers", None):
        for basis in ("relative", "absolute"):
            lead = _ccr._competitor_lead({"moat_competition": {
                "competitor_order": "direct", "competitor_source": source,
                "competitors": [dict(_DIRECT_ROWS[0], score_basis=basis)]}})
            low = "\n".join(lead).lower()
            for word in ("gemini", "google", "openai", "llm", "model", "fmp",
                         "financial modeling prep", "grounded"):
                assert word not in low, (source, basis, word)


def test_duplicated_constants_match_their_sources():
    """The resolver must not import the collector (it pulls the whole report pipeline into
    every chat turn), so it duplicates two constants. Pinned equal here.

    The segment cap has no live source any more: the collector stopped writing "competes
    in" segments when the grounded research list was retired (2026-10-02), and 48 was the
    cap it wrote them under — so a stored segment is never cut by the resolver."""
    from app.services.agents import ticker_report_data_collector as col
    assert _ccr._COMPETITOR_SEGMENT_CAP == 48
    assert _ccr._THREAT_HIGH_AT == col._THREAT_HIGH_THRESHOLD
    assert _ccr._THREAT_LOW_AT == col._THREAT_LOW_THRESHOLD


def test_the_resolver_never_imports_the_report_pipeline():
    import ast as _ast
    import inspect
    tree = _ast.parse(inspect.getsource(_ccr))
    names = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, _ast.ImportFrom):
            names.add(node.module or "")
            names.update(f"{node.module}.{a.name}" for a in node.names)
    for banned in ("competitor_intel_service", "ticker_report_data_collector"):
        assert not any(banned in n for n in names), banned


def test_the_ios_badge_mapping_is_still_high_moderate_else_low():
    """`_threat_label` mirrors iOS `mapCompetitorThreat`. Brace-bound, comments stripped."""
    import pathlib
    import re as _re
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "frontend/ios/ios/Models/TickerReportResponse.swift").read_text()
    src = _re.sub(r"//[^\n]*", "", src)
    start = src.index("func mapCompetitorThreat(")
    body = src[src.index("{", start):]
    depth, end = 0, 0
    for i, ch in enumerate(body):
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            end = i
            break
    body = " ".join(body[:end + 1].split())
    assert 'switch s.lowercased()' in body
    assert 'case "high": return .high' in body
    assert 'case "moderate": return .moderate' in body
    assert 'default: return .low' in body
    assert body.count("case ") == 2


# ── Report date + thesis lead ────────────────────────────────────────────────

@pytest.mark.parametrize("fields, expected", [
    ({"price_close_date": "2026-09-22", "live_date": "As of Sep 22, 2026 close"}, "2026-09-22"),
    ({"live_date": "As of Sep 22, 2026 close"}, "Sep 22, 2026 close"),
    ({"price_close_date": "  ", "live_date": "as of  Sep 1"}, "Sep 1"),
    ({"price_close_date": 20260922, "live_date": None}, None),
    ({"live_date": "As of "}, None),
    ({}, None),
])
def test_report_date(fields, expected):
    assert _ccr._report_date(fields) == expected


@pytest.mark.parametrize("thesis", [None, "x", [], {"bull_case": None}, {"bull_case": [None, 5, ""]}])
def test_thesis_lead_tolerates_garbage(thesis):
    assert _ccr._thesis_lead({"core_thesis": thesis}) == []


def test_thesis_lead_is_bounded():
    lead = _ccr._thesis_lead({"core_thesis": {"bull_case": ["b " * 900] * 30, "bear_case": "single"}})
    assert lead[0].startswith("Bull case: b b") and len(lead[0]) <= len("Bull case: ") + _ccr._MAX_REPORT_THESIS + 1
    assert lead[1] == "Bear case: single"


@pytest.mark.asyncio
async def test_lead_numbers_never_leak_nan_inf_or_bool(resolver, monkeypatch):
    for score in (float("nan"), float("inf"), True, "72", 10 ** 400):
        report = {"company_name": "X", "quality_score": score,
                  "price_action": {"narrative": "Moved.", "change_pct": score}}
        block = await _resolve_report(resolver, monkeypatch, report, ref="X|warren_buffett")
        assert "Overall quality score" not in block, score
        assert "nan" not in block.lower() and "inf" not in block.lower(), score


@pytest.mark.asyncio
async def test_the_fenced_text_no_longer_invites_the_report_lacks_it(resolver, monkeypatch):
    block = await _resolve_report(resolver, monkeypatch, _avgo_shaped_report())
    assert "Report data the user can see (an excerpt; long sections are shortened):" in block
    assert block.endswith("If something is not in this excerpt, say it was not included here, "
                          "never that the report lacks it.")
    assert "if asked about something it doesn't cover, say so" not in block
    assert "Full report data the user can see" not in block


# ── fair mode: the budget itself ─────────────────────────────────────────────

def test_fair_mode_gives_a_later_section_its_share():
    payload = {"early": {"text": "x " * 3000, "text2": "y " * 3000},
               "late": {"note": "LATEMARK the narrative that used to be starved"}}
    fair = _flatten_for_grounding(payload, 600, priority_top=("early", "late"), fair=True)
    assert "late.note: LATEMARK" in fair and len(fair) <= 600
    # Anti-vacuity: first-come (the default) spends it all on the early section.
    assert "LATEMARK" not in _flatten_for_grounding(payload, 600, priority_top=("early", "late"))


def test_fair_mode_orders_children_narrative_first_and_by_child_priority():
    payload = _jsonb_order({"moat_competition": {
        "dimensions": [{"score": 1.5}] * 3, "market_dynamics": {"cagr_5yr": 6.34},
        "durability_note": "DURABLE", "competitive_insight": "INSIGHT"}})
    out = _flatten_for_grounding(payload, 4000, priority_top=("moat_competition",), fair=True,
                                 child_priority=_ccr._REPORT_CHILD_PRIORITY).splitlines()
    assert out == [
        "moat_competition.durability_note: DURABLE",
        "moat_competition.dimensions[0].score: 1.5",
        "moat_competition.dimensions[1].score: 1.5",
        "moat_competition.dimensions[2].score: 1.5",
        "moat_competition.competitive_insight: INSIGHT",
        "moat_competition.market_dynamics.cagr_5yr: 6.34",
    ]
    # Without a child-priority map: scalars before containers, at every depth.
    plain = _flatten_for_grounding({"s": {"arr": [{"n": 1, "t": "T"}], "note": "N"}}, 4000,
                                   priority_top=("s",), fair=True).splitlines()
    assert plain == ["s.note: N", "s.arr[0].n: 1", "s.arr[0].t: T"]


def test_fair_mode_never_cuts_a_number():
    nums = {f"n{i}": v for i, v in enumerate((180.25, 123456.789, 7, -0.0042, 1_000_000, 3.5))}
    payload = {"a": {"note": "alpha " * 200, **nums}, "b": {"note": "beta " * 200, **nums},
               "c": {"vals": [180.25, 99.125, 7.5] * 4, **nums}}
    full = {f"{sec}.{k}": _ccr._num(v) for sec in ("a", "b") for k, v in nums.items()}
    full.update({f"c.{k}": _ccr._num(v) for k, v in nums.items()})
    for max_chars in range(60, 1400, 13):
        out = _flatten_for_grounding(payload, max_chars, priority_top=("a", "b", "c"), fair=True)
        assert len(out) <= max_chars, max_chars
        for line in out.splitlines():
            key, _, value = line.partition(": ")
            if key in full:
                assert value == full[key], (max_chars, line)
            if key == "c.vals":   # a list is cut only between elements
                kept = value.removesuffix(", …").split(", ")
                assert all(v in ("180.25", "99.125", "7.5") for v in kept), (max_chars, line)


_LONG_NUMS = (10 ** 40, -(10 ** 45) + 7, 2.5e55)   # an int, a negative int, an integral float


def test_fair_mode_never_cuts_a_number_longer_than_the_cut_floor():
    """The test above only uses numbers under `_MIN_CUT_CHARS`, which the floor alone keeps
    whole — so it stays green without the `_LINE_NUM` guard (F2, 2026-10-01 review). These
    numbers are longer than the floor, and the sweep hits every room between the floor and
    their full length, in pass 1 (a lone number) and pass 2 (a number behind a long text)."""
    for big in _LONG_NUMS:
        full = _ccr._num(big)
        assert len(full) > _ccr._MIN_CUT_CHARS + 5, full          # the setup is not vacuous
        payload = {"a": {"n": big, "t": "x " * 200}, "b": {"t": "word " * 120, "n": big}}
        windows = 0
        for max_chars in range(20, 900):
            out = _flatten_for_grounding(payload, max_chars, priority_top=("a", "b"), fair=True)
            assert len(out) <= max_chars, max_chars
            values = {k: v for k, _, v in (l.partition(": ") for l in out.splitlines())}
            for key in ("a.n", "b.n"):
                if key in values:
                    assert values[key] == full, (big, max_chars, values[key])
            # A room where a cut would have been allowed: lone number, floor ≤ avail < length.
            avail = max_chars + 1 - 1 - len("a.n: ")
            windows += _ccr._MIN_CUT_CHARS <= avail < len(full)
        assert windows >= 5, (big, windows)


def test_shrink_line_refuses_a_number_the_floor_would_allow():
    """The guard itself: the same over-long value is cut as TEXT and refused as a NUMBER."""
    full = _ccr._num(10 ** 40)
    room = len("a.n: ") + _ccr._MIN_CUT_CHARS + 6                  # avail = floor + 5 < len
    assert _ccr._MIN_CUT_CHARS <= room - 1 - len("a.n: ") < len(full)
    assert _ccr._shrink_line("a.n", full, _ccr._LINE_NUM, (), room) is None
    assert _ccr._shrink_line("a.n", full, _ccr._LINE_TEXT, (), room) is not None   # anti-vacuity


def test_fair_mode_cuts_text_only_on_a_word_boundary():
    text = " ".join(f"word{i}" for i in range(300))
    for max_chars in range(80, 900, 17):
        out = _flatten_for_grounding({"a": {"t": text}, "b": {"t": text}}, max_chars,
                                     priority_top=("a", "b"), fair=True)
        for line in out.splitlines():
            value = line.partition(": ")[2]
            if value.endswith("…"):
                stem = value[:-1]
                assert text.startswith(stem) and text[len(stem)] == " ", (max_chars, line)


@pytest.mark.parametrize("payload, expected", [
    ("just text", "just text"), (5, "5"), (None, ""), ([], ""), ({}, ""),
    ([{"a": 1}, "x"], "[0].a: 1\n[1]: x"),
])
def test_fair_mode_non_dict_payloads(payload, expected):
    assert _flatten_for_grounding(payload, 500, priority_top=("a",), fair=True) == expected


def test_fair_mode_list_valued_section_skip_top_and_an_absent_priority_key():
    payload = {"wall_street_consensus": [{"rating": "buy", "x": 1}, "y"],
               "secret": {"note": "SKIPPED"}, "other": "rest"}
    out = _flatten_for_grounding(payload, 2000, skip_top=("secret",), fair=True,
                                 priority_top=("missing", "secret", "wall_street_consensus"))
    assert out.splitlines() == ["wall_street_consensus[0].rating: buy", "wall_street_consensus[0].x: 1",
                                "wall_street_consensus[1]: y", "other: rest"]


def test_fair_mode_tolerates_a_raising_node():
    class _Boom(dict):
        def items(self):
            raise RuntimeError("bad node")

    payload = {"a": {"bad": _Boom(x=1)}, "b": {"note": "BMARK"}}
    out = _flatten_for_grounding(payload, 2000, priority_top=("a", "b"), fair=True)
    assert "b.note: BMARK" in out


# ── Money Moves / ETF / INDEX: the short, high-value blocks lead ─────────────

@pytest.mark.asyncio
async def test_money_move_highlights_and_statistics_survive_a_long_body(resolver, monkeypatch):
    article = _jsonb_order({   # DB order: sections (8) < statistics (10) < keyHighlights (13)
        "slug": "s", "title": "T", "author": {"name": "A"},
        "sections": [{"title": f"Part {i}", "content": [{"type": "paragraph", "text": "body " * 70}]}
                     for i in range(12)],
        "statistics": [{"value": "8%", "label": "STATMARK annual return"}],
        "keyHighlights": [{"title": "HIGHMARK start early", "description": "time is the lever"}],
    })

    class _Svc:
        async def get_money_moves(self):
            return _Obj(articles=[article])

    import app.services.money_moves_content_service as mm
    monkeypatch.setattr(mm, "get_money_moves_content_service", lambda: _Svc())
    block = await resolver.resolve("MONEY_MOVES_ARTICLE", "s", None)
    assert "HIGHMARK" in block and "STATMARK" in block and "8%" in block
    assert block.index("HIGHMARK") < block.index("STATMARK") < block.index("Part 0")
    # Anti-vacuity: in DB order without the priority, the body eats both.
    plain = _flatten_for_grounding(article, _ccr._DUMP_CAP)
    assert "HIGHMARK" not in plain and "STATMARK" not in plain


@pytest.mark.asyncio
async def test_etf_profile_and_strategy_survive_heavy_statistics(resolver, monkeypatch):
    stats = [_Obj(label=f"Statistic {i}", value=f"{i * 1000:,} units of something") for i in range(12)]
    detail = _Obj(
        name="Fund", symbol="FND", current_price=10.0, price_change_percent=0.1,
        key_statistics=stats,
        key_statistics_groups=[_Obj(title=f"G{g}", statistics=stats) for g in range(6)],
        performance_periods=[_Obj(label=f"P{i}", change_percent=float(i)) for i in range(12)],
        strategy=_Obj(hook="STRATMARK broad passive exposure"),
        holdings_risk=_Obj(top_holdings=[_Obj(symbol=f"H{i}", name="Holding " * 5, weight=1.5)
                                         for i in range(12)]),
        etf_profile=_Obj(description="PROFILEMARK tracks an index of large caps"),
    )

    class _Svc:
        async def get_etf_detail(self, s):
            return detail

    import app.services.etf_service as es
    monkeypatch.setattr(es, "get_etf_service", lambda: _Svc())
    block = await resolver.resolve("ETF", "fnd", None)
    assert "PROFILEMARK" in block and "STRATMARK" in block
    assert "PROFILEMARK" not in _flatten_for_grounding(detail.model_dump(), _ccr._DUMP_CAP)


@pytest.mark.asyncio
async def test_index_profile_survives_heavy_snapshots(resolver, monkeypatch):
    stats = [_Obj(label=f"Statistic {i}", value=f"{i * 1000:,} units of something") for i in range(12)]
    detail = _Obj(
        index_name="Idx", current_price=5000.0, price_change_percent=0.2,
        key_statistics_groups=[_Obj(title=f"G{g}", statistics=stats) for g in range(6)],
        performance_periods=[_Obj(label=f"P{i}", change_percent=float(i)) for i in range(12)],
        snapshots_data=_Obj(sector_performance=_Obj(sectors=[_Obj(sector=f"Sector {i}", change_percent=0.5)
                                                             for i in range(12)])),
        index_profile=_Obj(description="IDXPROFILEMARK 500 large-cap stocks"),
    )

    class _Svc:
        async def get_index_detail(self, s):
            return detail

    import app.services.index_service as ixs
    monkeypatch.setattr(ixs, "get_index_service", lambda: _Svc())
    block = await resolver.resolve("INDEX", "^IDX", None)
    assert "IDXPROFILEMARK" in block
    assert "IDXPROFILEMARK" not in _flatten_for_grounding(detail.model_dump(), _ccr._DUMP_CAP)


@pytest.mark.asyncio
async def test_non_string_lead_fields_still_ground_the_rest(resolver, monkeypatch):
    """A malformed narrative / summary must cost that line, not the whole block."""
    report = {"company_name": ["not", "a", "name"], "executive_summary_text": 42,
              "price_action": {"narrative": {"x": 1}, "change_pct": 1.0},
              "moat_competition": {"durability_note": "DURABLEMARK",
                                   "competitors": _json.loads(_json.dumps(_OLD_AVGO_ROWS))}}
    block = await _resolve_report(resolver, monkeypatch, report, ref="X|warren_buffett")
    assert block.startswith("The user is viewing the in-depth Cay research report for X (X).")
    assert "DURABLEMARK" in block and "1. NVIDIA Corporation (NVDA)" in block
    assert "Executive summary" not in block and "Recent price movement" not in block


# ── The grounded report's persona (2026-10-02): shared tag map + the `meta` out-param ──
#
# The report chat's mode voice follows the report ACTUALLY grounded (its stored `agent` tag),
# reported through `resolve(..., meta=)`. The cache lookup now goes through the shared
# `persona_config.AGENT_TAG_TO_KEY` — CURRENT tags only, so a legacy `dalio` reference misses
# the cache instead of grounding an old chat on today's Activist report.

@pytest.mark.asyncio
@pytest.mark.parametrize("ref, expected", [
    ("AAPL|buffett", "warren_buffett"),
    ("AAPL|lynch", "peter_lynch"),
    ("AAPL|LYNCH", "peter_lynch"),
    ("AAPL|peter_lynch", "peter_lynch"),
    ("AAPL|burry|", "michael_burry"),
    ("AAPL|", "warren_buffett"),          # empty segment → the documented default
    ("AAPL|soros", "soros"),              # unknown passes through and simply misses
    ("AAPL|dalio", "dalio"),              # LEGACY: never today's bill_ackman row
])
async def test_ticker_report_persona_segment_resolves_through_the_shared_table(resolver, monkeypatch, ref, expected):
    seen = {}

    async def fake_get(ticker, persona):
        seen["persona"] = persona
        return None

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)
    await resolver.resolve("TICKER_REPORT", ref, None)
    assert seen["persona"] == expected


def test_the_resolver_keeps_no_private_copy_of_the_tag_map():
    """A renamed private copy would pass a name check, so look for the PAIRS — in the AST, so
    neither a comment nor a quote style can satisfy or dodge it (testing.md §3)."""
    import ast
    import inspect
    import app.services.chat_context_resolver as mod

    tree = ast.parse(inspect.getsource(mod))
    pairs = {("buffett", "warren_buffett"), ("lynch", "peter_lynch"), ("burry", "michael_burry"),
             ("ackman", "bill_ackman"), ("wood", "cathie_wood"), ("dalio", "bill_ackman")}

    def _const(node):
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    for node in ast.walk(tree):
        found = set()
        if isinstance(node, ast.Dict):
            found = {(_const(k), _const(v)) for k, v in zip(node.keys, node.values) if k is not None}
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "dict"):
            found = {(kw.arg, _const(kw.value)) for kw in node.keywords}
        assert not (found & pairs), f"a private tag map is back at line {node.lineno}: {found & pairs}"

    # Nothing in the module may rebind the shared name (a local copy under the same name).
    for node in ast.walk(tree):
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else [])
        for t in targets:
            assert not (isinstance(t, ast.Name) and t.id == "AGENT_TAG_TO_KEY"), t.lineno

    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "_resolve_ticker_report")
    imported = {a.name for n in ast.walk(fn) if isinstance(n, ast.ImportFrom)
                and n.module == "app.services.agents.persona_config" for a in n.names}
    assert "AGENT_TAG_TO_KEY" in imported, "the cache lookup must use the shared persona_config map"
    lookups = [n for n in ast.walk(fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "get" and isinstance(n.func.value, ast.Name)
               and n.func.value.id == "AGENT_TAG_TO_KEY"]
    assert lookups, "_resolve_ticker_report no longer looks the segment up in AGENT_TAG_TO_KEY"


@pytest.mark.asyncio
@pytest.mark.parametrize("agent, expected", [
    ("lynch", "peter_lynch"),
    ("peter_lynch", "peter_lynch"),
    (" Burry ", "michael_burry"),
    ("dalio", "bill_ackman"),             # legacy stored tag: its METHOD is fine for a voice
])
async def test_meta_reports_the_grounded_reports_own_persona(resolver, monkeypatch, agent, expected):
    import app.services.ticker_report_cache as trc

    async def fake_cache(ticker, persona):
        return None

    monkeypatch.setattr(trc, "get_cached_report", fake_cache)
    _stub_frozen_row(monkeypatch, data={"company_name": "Oracle Corporation", "agent": agent,
                                        "executive_summary_text": "frozen"})
    meta = {}
    block = await resolver.resolve(
        "TICKER_REPORT", "ORCL|warren_buffett|rid-1", None, user_id="user-42", meta=meta,
    )
    assert block and "frozen" in block
    assert meta == {"report_persona_key": expected}


@pytest.mark.asyncio
async def test_a_persona_mismatch_is_logged_bounded(resolver, monkeypatch, caplog):
    """The old build's notification route: reference says warren_buffett, the row is lynch."""
    import app.services.ticker_report_cache as trc

    async def fake_cache(ticker, persona):
        return None

    monkeypatch.setattr(trc, "get_cached_report", fake_cache)
    _stub_frozen_row(monkeypatch, data={"company_name": "Oracle", "agent": "lynch",
                                        "executive_summary_text": "x"})
    hostile_ticker = "ORCL\nERROR app.security: forged" + "Z" * 5000
    with caplog.at_level(_logging.WARNING, logger="app.services.chat_context_resolver"):
        meta = {}
        await resolver.resolve(
            "TICKER_REPORT", f"{hostile_ticker}|warren_buffett|rid-1", None,
            user_id="user-42", meta=meta,
        )
    assert meta["report_persona_key"] == "peter_lynch"
    rendered = [r.getMessage() for r in caplog.records if "persona mismatch" in r.getMessage()]
    assert len(rendered) == 1
    assert "peter_lynch" in rendered[0] and "warren_buffett" in rendered[0]
    assert "\n" not in rendered[0] and len(rendered[0]) < 700


@pytest.mark.asyncio
async def test_a_matching_persona_logs_no_mismatch(resolver, monkeypatch, caplog):
    import app.services.ticker_report_cache as trc

    async def fake_cache(ticker, persona):
        return {"company_name": "Oracle", "agent": "lynch", "executive_summary_text": "x"}

    monkeypatch.setattr(trc, "get_cached_report", fake_cache)
    with caplog.at_level(_logging.WARNING, logger="app.services.chat_context_resolver"):
        meta = {}
        await resolver.resolve("TICKER_REPORT", "ORCL|lynch", None, meta=meta)
    assert meta == {"report_persona_key": "peter_lynch"}
    assert not [r for r in caplog.records if r.name == "app.services.chat_context_resolver"]


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", [None, "", "soros", 5, ["lynch"], "x" * 500])
async def test_an_unknown_stored_tag_vouches_for_nothing(resolver, monkeypatch, agent):
    """Unknown or malformed `agent` → no meta key (the voice falls back to the reference),
    the block still builds, and nothing of the stored value is echoed into meta."""
    import app.services.ticker_report_cache as trc

    report = {"company_name": "Oracle", "executive_summary_text": "x"}
    if agent is not None:
        report["agent"] = agent

    async def fake_cache(ticker, persona):
        return report

    monkeypatch.setattr(trc, "get_cached_report", fake_cache)
    meta = {}
    block = await resolver.resolve("TICKER_REPORT", "ORCL|lynch", None, meta=meta)
    assert block and "Oracle" in block
    assert meta == {}


@pytest.mark.asyncio
async def test_meta_is_untouched_when_nothing_resolves(resolver, monkeypatch):
    import app.services.ticker_report_cache as trc

    async def fake_cache(ticker, persona):
        return None

    monkeypatch.setattr(trc, "get_cached_report", fake_cache)
    meta = {}
    assert await resolver.resolve("TICKER_REPORT", "ORCL|lynch", "cc", meta=meta) == "cc"
    assert meta == {}


@pytest.mark.asyncio
async def test_meta_is_untouched_when_the_resolve_times_out(resolver, monkeypatch):
    """A shielded handler that finishes AFTER the ceiling must not change a turn already built."""
    import asyncio
    from app.services import chat_context_resolver as mod

    finished = asyncio.Event()

    async def slow_ticker_report(self, ref, ctx, user_id=None, meta=None):
        await asyncio.sleep(0.05)
        if meta is not None:
            meta["report_persona_key"] = "peter_lynch"
        finished.set()
        return "late block"

    monkeypatch.setattr(mod, "_RESOLVE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(ChatContextResolver, "_resolve_ticker_report", slow_ticker_report)
    meta = {}
    assert await resolver.resolve("TICKER_REPORT", "ORCL|lynch", "cc", meta=meta) == "cc"
    await asyncio.wait_for(finished.wait(), timeout=1.0)
    assert meta == {}, "the late handler wrote into the caller's meta"


@pytest.mark.asyncio
async def test_other_context_types_never_write_meta(resolver, monkeypatch):
    async def fake_get(ticker, persona):
        raise AssertionError("not a report")

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)
    meta = {}
    await resolver.resolve("BOOK", "2", "guide text", meta=meta)
    await resolver.resolve("STOCK", "AAPL", None, meta=meta)
    await resolver.resolve(None, "AAPL|lynch", None, meta=meta)
    assert meta == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", ["ORCL", "ORCL|", "ORCL|soros"])
async def test_a_reference_naming_no_persona_is_not_a_mismatch(resolver, monkeypatch, caplog, ref):
    """The two-segment-less form looks up the default row; it claims no persona, so the
    stored one simply drives the voice — no mismatch warning per turn."""
    import app.services.ticker_report_cache as trc

    async def fake_cache(ticker, persona):
        return {"company_name": "Oracle", "agent": "buffett", "executive_summary_text": "x"}

    monkeypatch.setattr(trc, "get_cached_report", fake_cache)
    with caplog.at_level(_logging.WARNING, logger="app.services.chat_context_resolver"):
        meta = {}
        await resolver.resolve("TICKER_REPORT", ref, None, meta=meta)
    assert meta == {"report_persona_key": "warren_buffett"}
    assert not [r for r in caplog.records if "mismatch" in r.getMessage()]


# ── The report's as-of date in `meta` (report chat's web search, 2026-10-02) ───
#
# The code-authored web-results caveat names the report date ("Your report reflects data as of
# Sep 22, 2026."). It comes from the SAME `_report_date` the "Report dated …" lead line uses, and
# reaches the caller's meta only when the handler finished inside the ceiling.

@pytest.mark.asyncio
@pytest.mark.parametrize("report_dates, expected", [
    ({"price_close_date": "2026-09-22"}, "2026-09-22"),
    ({"live_date": "As of Sep 22, 2026 close"}, "Sep 22, 2026 close"),
    ({"price_close_date": "2026-09-22", "live_date": "As of Sep 23, 2026 close"}, "2026-09-22"),
])
async def test_meta_carries_the_report_date_the_lead_line_shows(resolver, monkeypatch, report_dates, expected):
    import app.services.ticker_report_cache as trc

    async def fake_cache(ticker, persona):
        return {"company_name": "Oracle", "agent": "lynch", "executive_summary_text": "x",
                **report_dates}

    monkeypatch.setattr(trc, "get_cached_report", fake_cache)
    meta = {}
    block = await resolver.resolve("TICKER_REPORT", "ORCL|lynch", None, meta=meta)
    assert f"Report dated {expected}." in block
    assert meta == {"report_persona_key": "peter_lynch", "report_as_of": expected}


@pytest.mark.asyncio
@pytest.mark.parametrize("report_dates", [
    {}, {"price_close_date": None}, {"price_close_date": ""}, {"price_close_date": 20260922},
    {"live_date": "As of"}, {"price_close_date": ["2026-09-22"]},
])
async def test_an_undated_report_writes_no_report_date(resolver, monkeypatch, report_dates):
    import app.services.ticker_report_cache as trc

    async def fake_cache(ticker, persona):
        return {"company_name": "Oracle", "agent": "lynch", "executive_summary_text": "x",
                **report_dates}

    monkeypatch.setattr(trc, "get_cached_report", fake_cache)
    meta = {}
    block = await resolver.resolve("TICKER_REPORT", "ORCL|lynch", None, meta=meta)
    assert block and "Report dated" not in block
    assert "report_as_of" not in meta


@pytest.mark.asyncio
async def test_a_late_handler_never_writes_the_report_date(resolver, monkeypatch):
    import asyncio
    from app.services import chat_context_resolver as mod

    finished = asyncio.Event()

    async def slow_ticker_report(self, ref, ctx, user_id=None, meta=None):
        await asyncio.sleep(0.05)
        if meta is not None:
            meta["report_as_of"] = "2026-09-22"
        finished.set()
        return "late block"

    monkeypatch.setattr(mod, "_RESOLVE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(ChatContextResolver, "_resolve_ticker_report", slow_ticker_report)
    meta = {}
    assert await resolver.resolve("TICKER_REPORT", "ORCL|lynch", "cc", meta=meta) == "cc"
    await asyncio.wait_for(finished.wait(), timeout=1.0)
    assert meta == {}


# ── The report figures lead (2026-10-08, plan A5) ────────────────────────────────────
#
# Report chat saw 1 of 5 moat pillars, 1 of 6 segments and no fundamentals line: the fair dump
# handed a full report's section ~2 lines. The figures now LEAD (`_report_figures_lead`), one
# labelled line per group, ≤ `_REPORT_FIGURES_LEAD_CAP`, and leave the dump (said once). The
# forecast's growth is recomputed from the projections — the collector stores 0.0 for "unknown".

def _figures_block_of(block: str) -> str:
    """The block's lead (everything before the dump header)."""
    return block.split("Report data the user can see", 1)[0]


def _full_figures_report(**overrides):
    """The AVGO-shaped fixture with REALISTIC figure sections (the base fixture's projections
    are eight copies of one placeholder row, and it carries no track record or top holders)."""
    report = _json.loads(_json.dumps(_avgo_shaped_report()))
    rf = report["revenue_forecast"]
    rf["projections"] = [
        {"period": str(y), "revenue": r, "revenue_label": f"${r:.1f}B", "eps": e,
         "eps_label": f"${e:.2f}", "is_forecast": True, "eps_analyst_count": 30}
        for y, r, e in ((2026, 70.1, 6.80), (2027, 80.2, 8.10), (2028, 90.3, 9.40), (2029, 99.9, 10.70))
    ]
    rf["forecast_analyst_count"] = 41
    # The stored rates match their projections, as the collector's always do (it computes them
    # from the unrounded estimates over the same window); the base fixture's 57.8 / 59.5 do not.
    rf["cagr"], rf["eps_growth"] = 12.5, 16.3
    rf["earnings_track_record"] = [
        {"period": f"Q{q} '{yy}", "surprise_percent": s, "beat": res == "beat", "result": res}
        for yy, q, s, res in ((25, 1, 9.9, "beat"), (25, 2, 8.8, "beat"), (25, 3, 7.7, "beat"),
                              (25, 4, 6.6, "beat"), (26, 1, 3.1, "beat"), (26, 2, 2.04, "beat"),
                              (26, 3, 0.0, "met"), (26, 4, -1.2, "miss"))
    ]
    report["key_management"]["top_holders"] = [
        {"name": "Holder A", "title": "10% Owner", "ownership": "460M", "ownership_value": "—",
         "percent_ownership": 9.8},
        {"name": "Holder B", "title": "10% Owner", "ownership": "380M", "ownership_value": "—",
         "percent_ownership": None, "percent_owned": 8.1},
    ]
    report["fundamental_metrics"] = [
        {"title": title, "star_rating": stars, "quality_label": verdict, "metrics": [
            {"label": label, "value": value, "annual_history": [{"period": "2020", "value": 1.0}] * 10}
            for label, value in metrics]}
        for title, stars, verdict, metrics in (
            ("Profitability", 4, "Margins well above industry peers",
             (("Gross Margin*", "77.3%"), ("Operating Margin*", "31.8%"), ("ROE*", "11.4%"))),
            ("Growth", 5, "Growth well above industry peers",
             (("Revenue Growth*", "44.0%"), ("EPS Growth*", "38.1%"))),
            ("Valuation", 2, "Valuation rich vs industry", (("P/E (TTM)*", "68.4x"),)),
            ("Health", 3, "Balance sheet in line with peers", (("Debt/Equity*", "0.9x"),)),
        )
    ]
    for key, value in overrides.items():
        report[key] = value
    return _jsonb_order(report)


def _wire_metric(label, value, peer_level="industry"):
    return {"label": label, "value": value, "trend": None, "peer_level": peer_level,
            "history_key": "k", "annual_history": [{"period": "2020", "value": 1.0}] * 10}


def _wire_realistic_report(**overrides):
    """A full report built from the REAL wire shapes, not iOS display labels: the snapshot
    services' metric labels with their peer suffixes (profitability "(1.20x sector avg 64.3%)",
    valuation "(1.30x sector avg 22.40)" / "(sector avg 22.40)", health "(vs sector 0.95)"),
    `peer_level` "industry", the card verdict vocabulary, the DCF service's method label, the
    collector's 5 officers / 3 holders with real-length filing titles, and stored growth rates
    that match their projections (the collector computes them over the same window)."""
    report = {
        "symbol": "MSFT", "company_name": "Microsoft Corporation", "executive_summary_text": "s",
        "wall_street_consensus": {"caydex_fair_value": {
            "status": "ok", "fair_value": 412.37, "currency": "USD", "range_low": 351.12,
            "range_high": 478.94, "as_of": "2026-10-07", "method": "2-stage free cash flow to equity",
            "discount_rate_pct": 9.12, "terminal_growth_pct": 2.5, "alternative_value": 389.5}},
        "moat_competition": {"dimensions": [
            {"name": n, "score": s, "peer_score": p, "source": src} for n, s, p, src in (
                ("Switching Costs", 8.5, 6.1, "measured"), ("Network Effects", 7.5, 5.4, "grounded"),
                ("Intangible Assets", 8.0, 6.3, "measured"), ("Cost Advantage", 6.5, 5.9, "measured"),
                ("Efficient Scale", 7.0, 5.0, "measured"))]},
        "revenue_engine": {
            "period": "FY 2025", "revenue_unit": "Billions", "total_revenue": 281.72,
            "intersegment_eliminations": 0.0,
            "segments": [{"name": n, "current_revenue": c, "previous_revenue": p} for n, c, p in (
                ("Server Products and Tools", 98.44, 79.97), ("Office Products and Cloud Services", 95.36, 84.94),
                ("Windows and Devices", 26.04, 25.0), ("Gaming", 23.46, 21.5),
                ("LinkedIn Corporation", 17.81, 16.37), ("Search and News Advertising", 13.88, 12.58))]},
        "revenue_forecast": {
            "cagr": 13.7, "eps_growth": 15.2, "forecast_analyst_count": 52,
            "beat_summary": "Beat 8 of 8",
            "projections": [
                {"period": str(y), "revenue": r, "revenue_label": f"${r:.1f}B", "eps": e,
                 "eps_label": f"${e:.2f}", "is_forecast": True}
                for y, r, e in ((2026, 325.12, 15.71), (2027, 371.94, 18.28), (2028, 421.6, 20.97),
                                (2029, 478.04, 24.04))],
            "earnings_track_record": [
                {"period": f"Q{q} '{yy}", "surprise_percent": s, "beat": True, "result": "beat"}
                for yy, q, s in ((25, 1, 4.1), (25, 2, 3.6), (25, 3, 6.2), (25, 4, 5.0), (26, 1, 2.9),
                                 (26, 2, 7.4), (26, 3, 3.3), (26, 4, 4.8))]},
        "key_management": {
            "officers": [
                {"name": n, "title": t, "ownership": o, "ownership_value": "$1.0B", "percent_owned": p,
                 "percent_ownership": None} for n, t, o, p in (
                    ("Satya Nadella", "director, Chairman and Chief Executive Officer", "1.2M", 0.016123),
                    ("Amy E. Hood", "EVP, Chief Financial Officer", "520K", 0.006977),
                    ("Bradford L. Smith", "Vice Chair and President", "660K", 0.008855),
                    ("Judson Althoff", "EVP & Chief Commercial Officer", "140K", 0.001878),
                    ("Takeshi Numoto", "EVP, Chief Marketing Officer", "50K", 0.000671))],
            "top_holders": [
                {"name": n, "title": "10% Owner", "ownership": o, "ownership_value": "—",
                 "percent_ownership": b, "percent_owned": d} for n, o, b, d in (
                    ("Vanguard Group Inc", "680.1M", 9.1, 9.148213),
                    ("BlackRock Inc.", "540.3M", 7.3, 7.267701),
                    ("State Street Corp", "300.2M", 4.0, 4.038197))]},
        "fundamental_metrics": [
            {"title": "Profitability", "star_rating": 5, "peer_group_level": "industry",
             "quality_label": "Fat Margins vs Industry, Weak Returns on Equity", "metrics": [
                 _wire_metric("Gross Margin (1.20x sector avg 57.4%)", "68.82%"),
                 _wire_metric("Operating Margin (1.58x sector avg 28.3%)", "44.69%"),
                 _wire_metric("Net Margin (1.65x sector avg 21.8%)", "35.97%"),
                 _wire_metric("Return on Equity (ROE) (0.93x sector avg 31.6%)", "29.65%"),
                 _wire_metric("Return on Assets (ROA) (1.42x sector avg 12.3%)", "17.43%")]},
            {"title": "Growth", "star_rating": 4, "peer_group_level": "industry",
             "quality_label": "Rapid Sales Growth, Cash Flow Surging", "metrics": [
                 _wire_metric("Revenue Growth (YoY)", "+14.9%", None),
                 _wire_metric("EPS Growth", "+15.5%", None),
                 _wire_metric("Free Cash Flow Growth (YoY)", "-2.6%", None),
                 _wire_metric("Operating Income Growth", "+17.4%", None)]},
            {"title": "Valuation", "star_rating": 2, "peer_group_level": "industry",
             "quality_label": "Pricey vs Industry, Cheap on Cash Flow", "metrics": [
                 _wire_metric("P/E (1.37x sector avg 26.10)", "35.84"),
                 _wire_metric("P/B (1.21x sector avg 8.69)", "10.48"),
                 _wire_metric("P/S (1.62x sector avg 7.92)", "12.86"),
                 _wire_metric("P/FCF (sector avg 37.20)", "Neg."),
                 _wire_metric("EV/EBITDA (1.11x sector avg 21.40)", "23.79"),
                 _wire_metric("Earnings Yield (0.73x sector avg 3.83%)", "2.79%")]},
            {"title": "Health", "star_rating": 4, "peer_group_level": "industry",
             "quality_label": "Rock-Solid Balance Sheet, Strained Interest Cover", "metrics": [
                 _wire_metric("Altman Z-Score", "9.42", None),
                 _wire_metric("Debt-to-Equity (vs sector 0.95)", "0.90"),
                 _wire_metric("Current Ratio (vs sector 1.53)", "1.35"),
                 _wire_metric("Interest Coverage (vs sector 18.40)", "41.20"),
                 _wire_metric("Quick Ratio (vs sector 1.32)", "1.21")]},
        ],
    }
    for key, value in overrides.items():
        report[key] = value
    return _jsonb_order(report)


def _fig_lines(report) -> list:
    return _ccr._report_figures_lead(report)


@pytest.mark.asyncio
async def test_the_full_avgo_report_leads_with_every_figure(resolver, monkeypatch):
    report = _avgo_shaped_report()
    block = await _resolve_report(resolver, monkeypatch, report)
    head = _figures_block_of(block)
    lead = _fig_lines(report)
    assert "\n".join(lead) in head                      # the block carries exactly the lead
    # All five pillars, each with its peer score.
    pillars = next(l for l in lead if l.startswith("Moat pillars (score out of 10): "))
    for d in range(5):
        assert f"Dim {d} 7.5 vs peers 5" in pillars, d
    assert "…" not in pillars
    # All six segments, largest first, with share, period and unit.
    segments = next(l for l in lead if l.startswith("Revenue segments ("))
    assert segments.startswith("Revenue segments (FY 2026; Billions of reporting currency; "
                               "% = share of total revenue 63,887), largest first: ")
    names = [f"Segment {i} " for i in (5, 4, 3, 2, 1, 0)]
    positions = [segments.index(n) for n in names]
    assert positions == sorted(positions)
    assert "Segment 5 4,502.5 (7.0%, prior yr 4,003.8)" in segments
    # The collector stores at most five officers; this synthetic report holds ten, so the line
    # shows the first five in the stored (role-rank) order and says how many it shows.
    officers = next(l for l in lead if l.startswith("Officers, in role order"))
    assert officers.startswith("Officers, in role order (first 5 of 10; ")
    order = [officers.index(f"Officer {i} (VP)") for i in range(5)]
    assert order == sorted(order) and "…" not in officers
    assert "Officer 5 (VP)" not in officers
    assert "Officer 0 (VP): 1,000.5 shares, 0.01%" in officers
    # Every fundamentals card.
    for c in range(6):
        assert f"Card{c} card: Metric{c}0 45%" in head, c
    assert "Earnings vs analyst EPS estimates: Beat 6 of 8." in lead
    # Sizes: the lead and the (narrative-only) dump are both bounded.
    assert len("\n".join(lead)) <= _ccr._REPORT_FIGURES_LEAD_CAP
    dump = _dump_of(block)
    assert len(dump) <= _ccr._REPORT_DUMP_CAP == 3000
    for marker in _NARRATIVE_MARKERS:
        assert marker in dump, marker


@pytest.mark.asyncio
async def test_a_figure_is_said_once_never_in_the_dump_too(resolver, monkeypatch):
    block = await _resolve_report(resolver, monkeypatch, _full_figures_report())
    dump = _dump_of(block)
    for moved in ("moat_competition.dimensions", "revenue_engine.segments", "revenue_engine.period",
                  "revenue_engine.total_revenue", "revenue_engine.revenue_unit",
                  "key_management.officers", "key_management.top_holders",
                  "revenue_forecast.cagr", "revenue_forecast.eps_growth",
                  "revenue_forecast.beat_summary", "revenue_forecast.forecast_analyst_count",
                  "caydex_fair_value", "fundamental_metrics", "annual_history"):
        assert moved not in dump, moved
    assert block.count("Officer 3 (VP)") == 1 and block.count("180.25") == 1
    assert block.count("Margins well above industry peers") == 1


@pytest.mark.asyncio
async def test_a_small_report_with_dump_room_still_says_each_figure_once(resolver, monkeypatch):
    """The AVGO dump is full, so its budget alone could hide a duplicate: a small report leaves
    the dump room for every key it is allowed to walk."""
    report = {
        "company_name": "Small", "executive_summary_text": "s",
        "fundamental_metrics": [{"title": "Growth", "star_rating": 4, "quality_label": "CARDVERDICT",
                                 "metrics": [{"label": "Revenue Growth", "value": "METRICVALUE"}]}],
        "moat_competition": {"durability_note": "DURABLEMARK",
                             "dimensions": [{"name": "PILLARNAME", "score": 6.5, "peer_score": 5.5}]},
        "revenue_engine": {"analysis_note": "ENGINEMARK", "period": "FY 2025", "revenue_unit": "Millions",
                           "total_revenue": 1000.0,
                           "segments": [{"name": "SEGNAME", "current_revenue": 600.0}]},
        "revenue_forecast": {"insight": "FORECASTMARK", "cagr": 0.0, "eps_growth": 0.0,
                             "beat_summary": "Beat 3 of 4", "forecast_analyst_count": 17},
        "key_management": {"ownership_insight": "OWNMARK",
                           "officers": [{"name": "OFFICERNAME", "title": "CEO", "ownership": "1.0M"}],
                           "top_holders": [{"name": "HOLDERNAME", "title": "10% Owner"}]},
        "wall_street_consensus": {"current_price": 50.5,
                                  "caydex_fair_value": {"status": "ok", "fair_value": 77.25}},
    }
    block = await _resolve_report(resolver, monkeypatch, report, ref="SMLL|warren_buffett")
    dump = _dump_of(block)
    for once in ("CARDVERDICT", "METRICVALUE", "PILLARNAME", "SEGNAME", "OFFICERNAME", "HOLDERNAME",
                 "77.25", "Beat 3 of 4", "FY 2025", "Millions"):
        assert block.count(once) == 1, once
        assert once not in dump, once
    for marker in ("DURABLEMARK", "ENGINEMARK", "FORECASTMARK", "OWNMARK", "50.5"):
        assert marker in dump, marker
    assert "cagr" not in block and "eps_growth" not in block and "17 analysts" not in block


@pytest.mark.asyncio
async def test_a_realistic_full_report_fits_every_group(resolver, monkeypatch):
    report = _full_figures_report()
    lead = _fig_lines(report)
    text = "\n".join(lead)
    assert len(text) <= _ccr._REPORT_FIGURES_LEAD_CAP
    assert [l.split(" ", 1)[0] for l in lead] == [
        "Caydex", "Moat", "Revenue", "Forward", "Earnings",
        "Profitability", "Growth", "Valuation", "Health", "Officers,", "Top"]
    assert ("Earnings vs analyst EPS estimates (Beat 6 of 8; last reported quarters, oldest "
            "first): Q1 '26 beat +3.1%; Q2 '26 beat +2.0%; Q3 '26 met +0.0%; Q4 '26 miss -1.2%") in lead
    # The collector's "10% Owner" title is the head's own words: said once, not per holder.
    assert ("Top holders, 10%+ owners (each one's latest 13D/G filing, may predate the report): Holder A: 460M shares, "
            "9.8% beneficial; Holder B: 380M shares, 8.1% of shares") in lead
    assert ("Profitability card (4/5 stars; Margins well above industry peers): Gross Margin* 77.3%; "
            "Operating Margin* 31.8%; ROE* 11.4%") in lead
    block = await _resolve_report(resolver, monkeypatch, report)
    assert text in _figures_block_of(block)


# Every metric of `_wire_realistic_report`, as the lead states it: the wire label peer-worded
# ("industry", `peer_level`) and its suffix restated as "<value> (industry avg <median>)".
_WIRE_CARD_LINES = (
    "Profitability card (5/5 stars; Fat Margins vs Industry, Weak Returns on Equity): "
    "Gross Margin 68.82% (industry avg 57.4%); Operating Margin 44.69% (industry avg 28.3%); "
    "Net Margin 35.97% (industry avg 21.8%); Return on Equity (ROE) 29.65% (industry avg 31.6%); "
    "Return on Assets (ROA) 17.43% (industry avg 12.3%)",
    "Growth card (4/5 stars; Rapid Sales Growth, Cash Flow Surging): Revenue Growth (YoY) +14.9%; "
    "EPS Growth +15.5%; Free Cash Flow Growth (YoY) -2.6%; Operating Income Growth +17.4%",
    "Valuation card (2/5 stars; Pricey vs Industry, Cheap on Cash Flow): P/E 35.84 (industry avg "
    "26.10); P/B 10.48 (industry avg 8.69); P/S 12.86 (industry avg 7.92); P/FCF Neg. (industry avg "
    "37.20); EV/EBITDA 23.79 (industry avg 21.40); Earnings Yield 2.79% (industry avg 3.83%)",
    "Health card (4/5 stars; Rock-Solid Balance Sheet, Strained Interest Cover): Altman Z-Score 9.42; "
    "Debt-to-Equity 0.90 (vs industry 0.95); Current Ratio 1.35 (vs industry 1.53); Interest "
    "Coverage 41.20 (vs industry 18.40); Quick Ratio 1.21 (vs industry 1.32)",
)


@pytest.mark.asyncio
async def test_a_report_with_the_real_wire_labels_states_every_card_and_metric(
        resolver, monkeypatch, caplog):
    """The sizing is pinned on the REAL wire shapes, not on short display labels: every card and
    every metric (with its peer median), the five officers and three holders, all whole — and
    nothing logged as cut. The old caps (220 per card, 720 for all, a 2,400 lead with the cards
    after the officers) cut ROE and left out the Valuation and Health cards on this report."""
    report = _wire_realistic_report()
    with caplog.at_level(_logging.WARNING, logger="app.services.chat_context_resolver"):
        lead = _fig_lines(report)
    text = "\n".join(lead)
    assert len(text) <= _ccr._REPORT_FIGURES_LEAD_CAP
    # ~10% headroom for longer names, verdicts and titles than this fixture's.
    assert len(text) <= 0.95 * _ccr._REPORT_FIGURES_LEAD_CAP
    assert not [r for r in caplog.records if "cut for space" in r.getMessage()]
    assert "…" not in text and "sector" not in text
    for card in _WIRE_CARD_LINES:
        assert card in lead, card
    officers = next(l for l in lead if l.startswith("Officers, in role order ("))
    for name in ("Satya Nadella (director, Chairman and Chief Executive Officer): 1.2M shares, "
                 "0.0161%", "Amy E. Hood", "Bradford L. Smith", "Judson Althoff", "Takeshi Numoto"):
        assert name in officers, name
    assert lead[-1] == ("Top holders, 10%+ owners (each one's latest 13D/G filing, may predate the report): Vanguard Group Inc: "
                        "680.1M shares, 9.1% beneficial; BlackRock Inc.: 540.3M shares, 7.3% "
                        "beneficial; State Street Corp: 300.2M shares, 4% beneficial")
    assert "revenue CAGR 2026–2029 13.7%/yr; EPS CAGR 2026–2029 15.2%/yr" in text
    block = await _resolve_report(resolver, monkeypatch, report, ref="MSFT|warren_buffett")
    assert text in _figures_block_of(block)
    assert "sector avg" not in block and "vs sector" not in block


def test_long_filing_titles_still_fit_every_officer():
    """Five officers whose filing titles run to the 48-character cut still fit the line whole."""
    report = _wire_realistic_report()
    for o in report["key_management"]["officers"]:
        o["title"] = "director, President, Chief Executive Officer and Chairman of the Board"
    lead = _fig_lines(report)
    officers = next(l for l in lead if l.startswith("Officers, in role order ("))
    assert not officers.endswith("; …")
    assert officers.count("director, President, Chief Executive Officer…") == 5
    assert len("\n".join(lead)) <= _ccr._REPORT_FIGURES_LEAD_CAP
    assert lead[-1].startswith("Top holders")


# ── the forecast: the stored growth rate, or one recomputed where it is 0.0 ("unknown") ──

def test_the_forecast_states_the_stored_cagr_with_its_window():
    """The stored rate is the one the card and the PDF print (the collector computed it from the
    unrounded estimates over this same window), labelled with the window's first and last year."""
    report = _full_figures_report()
    line = next(l for l in _fig_lines(report) if l.startswith("Forward forecasts"))
    assert line == (
        "Forward forecasts (analyst estimate): "
        "revenue CAGR 2026–2029 12.5%/yr; EPS CAGR 2026–2029 16.3%/yr; "
        "41 analysts on the nearest year; 2026 revenue $70.1B, EPS $6.80; 2027 revenue $80.2B, "
        "EPS $8.10; 2028 revenue $90.3B, EPS $9.40; 2029 revenue $99.9B, EPS $10.70")


def _forecast_line(projections, **rf):
    report = {"revenue_forecast": {"cagr": 0.0, "eps_growth": 0.0, "projections": projections, **rf}}
    lines = [l for l in _fig_lines(report) if l.startswith("Forward forecasts")]
    return lines[0] if lines else None


def _row(period, revenue=None, eps=None, **extra):
    row = {"period": period, "revenue": revenue, "eps": eps, "is_forecast": True}
    if isinstance(revenue, (int, float)) and not isinstance(revenue, bool):
        row["revenue_label"] = f"${revenue}B"
    if isinstance(eps, (int, float)) and not isinstance(eps, bool):
        row["eps_label"] = f"${eps}"
    row.update(extra)
    return row


def _eps_rows(values, yoy=None):
    return [_row(str(2026 + i), None, v, **({"eps_yoy_pct": yoy[i]} if yoy else {}))
            for i, v in enumerate(values)]


def test_a_stored_rate_beats_one_recomputed_from_rounded_projections():
    """Raw EPS 0.1249 → 0.2951 is stored as 33.2 (the card: "+33% CAGR"); the payload's rounded
    0.12 → 0.30 would compound to 35.7. The lead says what the card says."""
    line = _forecast_line(_eps_rows([0.12, 0.17, 0.23, 0.30]), eps_growth=33.2)
    assert "EPS CAGR 2026–2029 33.2%/yr" in line
    assert "35.7" not in line and "revenue CAGR" not in line


@pytest.mark.parametrize("stored", [float("nan"), float("inf"), -float("inf"), True, "12.5",
                                    1e300, 5e6, -1e5, None, 0, 0.0, [12.5]])
def test_a_stored_rate_that_is_not_a_number_is_never_stated(stored):
    """Garbage and the 0.0 "unknown" fall through to the recompute — here refused, because
    rounded projections this small cannot carry a rate."""
    line = _forecast_line(_eps_rows([0.12, 0.17, 0.23, 0.30]), eps_growth=stored)
    assert "CAGR" not in line
    assert "nan" not in line.lower() and "inf" not in line.lower() and "12.5" not in line


@pytest.mark.parametrize("projections", [
    # A repeated period: the window is not the one the stored rate was computed over.
    [_row("2026", None, 10.0), _row("2026", None, 99.0), _row("2027", None, 11.0)],
    # An endpoint without a positive estimate: the stored rate contradicts the payload.
    [_row("2026", None, 0.0), _row("2027", None, 11.0), _row("2028", None, 12.1)],
    [_row("2026", None, 10.0), _row("2027", None, -1.0)],
    # A row the lead cannot read.
    [_row("2026", None, 10.0), "garbage", _row("2027", None, 11.0)],
])
def test_a_stored_rate_is_stated_only_over_an_intact_window(projections):
    line = _forecast_line(projections, eps_growth=77.7) or ""
    assert "77.7" not in line


def test_the_sentinel_rate_is_recomputed_by_chaining_the_unrounded_yoy():
    """A turnaround: the first year is a loss, so the collector stored 0.0. The positive years'
    YoY changes (computed from the unrounded estimates) chain into the rate the rounded 0.30
    base could not carry."""
    line = _forecast_line(_eps_rows([-0.5, 0.30, 0.80, 1.40], yoy=[None, None, 166.7, 75.0]))
    rate = ((1 + 1.667) * (1 + 0.75)) ** 0.5 * 100 - 100
    assert f"EPS CAGR 2027–2029 {rate:.1f}%/yr" in line
    # Without the YoY the rounded base is refused (0.295-0.305 → 1.395-1.405: 114-118%/yr).
    assert "CAGR" not in _forecast_line(_eps_rows([-0.5, 0.30, 0.80, 1.40]))


@pytest.mark.parametrize("between", ["garbage", None, {"period": None, "eps": 0.5},
                                     {"period": "2026", "eps": 0.5, "is_forecast": True}])
def test_the_yoy_chain_needs_consecutive_payload_rows(between):
    """A YoY is measured against the PAYLOAD's previous row: past a row the lead dropped (junk,
    no period, a repeated period) it is not a change from the row the lead kept, even when its
    number happens to agree — and the rounded 0.30 base alone cannot carry the rate."""
    rows = [_row("2026", None, 0.30), between, _row("2027", None, 0.80, eps_yoy_pct=166.7)]
    line = _forecast_line(rows) or ""
    assert "CAGR" not in line
    # Anti-vacuity: the same rows without the dropped one chain into a rate.
    assert "EPS CAGR 2026–2027 166.7%/yr" in _forecast_line([rows[0], rows[2]])


def test_an_implausible_recomputed_rate_is_never_stated():
    """10 → 20,000 in a year is 199,900%/yr: a corrupt row, from the projections or the chain."""
    assert "CAGR" not in _forecast_line(_eps_rows([10.0, 20000.0]))
    assert "CAGR" not in _forecast_line(_eps_rows([10.0, 20000.0], yoy=[None, 199900.0]))
    # A large but plausible rate is stated (from the unrounded YoY: the rounded 10 → 100 alone
    # spans 899.4-900.6%, wider than a one-decimal rate may claim).
    assert "CAGR 2026–2027 900.0%/yr" in _forecast_line(_eps_rows([10.0, 100.0], yoy=[None, 900.0]))
    assert "CAGR" not in _forecast_line(_eps_rows([10.0, 100.0]))


@pytest.mark.parametrize("values, yoy, expected", [
    # A YoY the rounded projections contradict is never compounded: the rate comes from the
    # projections themselves when they can carry it, else there is none.
    ([10.0, 11.0, 12.1], [None, 500.0, 10.0], "EPS CAGR 2026–2028 10.0%/yr"),
    ([0.12, 0.17, 0.30], [None, 41.7, 500.0], None),
    # A missing link (a NaN or bool YoY) falls back the same way.
    ([10.0, 11.0, 12.1], [None, float("nan"), 10.0], "EPS CAGR 2026–2028 10.0%/yr"),
    ([10.0, 11.0, 12.1], [None, True, 10.0], "EPS CAGR 2026–2028 10.0%/yr"),
    # A gap (no estimate) breaks the chain; 0.12 alone cannot carry the rate.
    ([0.12, 0.0, 0.30], [None, None, None], None),
])
def test_the_yoy_chain_degrades_to_the_projections(values, yoy, expected):
    line = _forecast_line(_eps_rows(values, yoy=yoy)) or ""
    if expected is None:
        assert "CAGR" not in line
    else:
        assert expected in line and "500" not in line


@pytest.mark.parametrize("projections", [
    [],                                                        # no estimates at all
    [_row("2027", 1.0)],                                       # one positive projection
    [_row("2027", 1.0)] * 8,                                   # the base fixture: one period, repeated
    [_row("2026", 0.0), _row("2027", 5.0)],                    # the collector's 0.0 "no estimate"
    [_row("2026", -3.0), _row("2027", 5.0)],                   # a non-positive base
    [_row("2026", float("nan")), _row("2027", 5.0)],
    [_row("2026", float("inf")), _row("2027", 5.0)],
    [_row("2026", True), _row("2027", 5.0)],                   # a bool is not a revenue
    [_row("2026", "70.1"), _row("2027", 80.2)],                # a string is not either
    [_row("2029", 9.0), _row("2026", 5.0)],                    # unsorted years
    [_row("2026", 0.05), _row("2027", 5.0)],                   # a base the rounding cannot carry
    [_row("2026", 1e300), _row("2027", 5.0)],                  # a corrupt magnitude
])
def test_no_cagr_line_from_fewer_than_two_usable_projections(projections):
    line = _forecast_line(projections) or ""
    assert "CAGR" not in line
    assert "0.0%" not in line and "nan" not in line.lower() and "inf" not in line.lower()


def test_the_zero_sentinels_never_reach_the_block_at_all():
    report = {"revenue_forecast": {"cagr": 0.0, "eps_growth": 0.0, "projections": [_row("2027", 1.0)],
                                   "insight": "FORECASTMARK"}}
    pruned = _ccr._without_lead_figures(report)
    assert "cagr" not in pruned["revenue_forecast"] and "eps_growth" not in pruned["revenue_forecast"]
    assert pruned["revenue_forecast"]["insight"] == "FORECASTMARK"
    assert "cagr" in report["revenue_forecast"]          # the input is never mutated


@pytest.mark.asyncio
async def test_a_zero_sentinel_report_states_no_growth_rate(resolver, monkeypatch):
    report = {"company_name": "X", "executive_summary_text": "s",
              "revenue_forecast": {"cagr": 0.0, "eps_growth": 0.0, "insight": "FORECASTMARK",
                                   "projections": [_row("2027", 4.2, 1.5)]}}
    block = await _resolve_report(resolver, monkeypatch, report, ref="X|warren_buffett")
    assert "CAGR" not in block and "cagr" not in block and "eps_growth" not in block
    assert "Forward forecasts (analyst estimate): 2027 revenue $4.2B, EPS $1.5" in block
    assert "FORECASTMARK" in block


@pytest.mark.parametrize("projections, expected", [
    # Non-year labels: the span is the row distance (the collector's own rule).
    ([_row("FY0", 10.0), _row("FY1", 11.0), _row("FY2", 12.1)], "revenue CAGR FY0–FY2 10.0%/yr"),
    # "FY2026"-style labels are years.
    ([_row("FY2026", 10.0), _row("FY2028", 12.1)], "revenue CAGR FY2026–FY2028 10.0%/yr"),
    # A gap row with no estimate keeps the year span honest.
    ([_row("2026", 10.0), _row("2027", 0.0), _row("2028", 12.1)], "revenue CAGR 2026–2028 10.0%/yr"),
    # A shrinking forecast keeps its sign.
    ([_row("2026", 10.0), _row("2027", 9.0)], "revenue CAGR 2026–2027 -10.0%/yr"),
    # A loss year is left out of the EPS rate; the endpoints that remain are positive (and
    # large enough for their 2-decimal rounding to carry the rate).
    ([_row("2026", 10.0, 20.0), _row("2027", 11.0, -10.0), _row("2028", 12.1, 24.2)],
     "EPS CAGR 2026–2028 10.0%/yr"),
    # The same loss year with a small EPS: 1.995-2.005 → 2.415-2.425 is 9.7-10.3%/yr, a guess.
    ([_row("2026", None, 2.0), _row("2027", None, -1.0), _row("2028", None, 2.42)], None),
])
def test_the_recomputed_cagr(projections, expected):
    if expected is None:
        assert "CAGR" not in (_forecast_line(projections) or "")
    else:
        assert expected in _forecast_line(projections)


def test_a_repeated_period_is_stated_once_first_row_wins():
    line = _forecast_line([_row("2026", 100.0), _row("2026", 990.0), _row("2027", 110.0)])
    assert line.count("2026 revenue") == 1 and "$990.0B" not in line
    assert "revenue CAGR 2026–2027 10.0%/yr" in line


def test_forecast_rows_degrade_by_omission():
    line = _forecast_line([
        _row("2026", 0.0, 0.0),                    # the collector's "$0" placeholders → nothing
        _row("2027", 5.0, -1.25, eps_label="$-1.25"),   # a loss keeps its sign
        _row("2028", 6.0, is_forecast=False),      # an actual is not an analyst estimate
        "garbage", None, {"period": None, "revenue": 3.0},
        _row("2029\n2030", 7.0),                   # a label cannot start a new line
    ], forecast_analyst_count=True)                # a bool is not a count
    assert line.startswith("Forward forecasts (analyst estimate): ")
    assert "$0" not in line and "2026" not in line and "2028" not in line
    assert "2027 revenue $5.0B, EPS $-1.25" in line
    assert "\n" not in line and "analysts" not in line
    assert _forecast_line("not-a-list") is None


# ── the fair value: labelled, absent when the switch is off ──

@pytest.mark.asyncio
async def test_the_fair_value_leads_labelled_when_dcf_is_on(resolver, monkeypatch):
    block = await _resolve_report(resolver, monkeypatch, _avgo_shaped_report())
    line = next(l for l in block.splitlines() if l.startswith("Caydex fair value"))
    assert line == ("Caydex fair value: 180.25 USD per share (Caydex model estimate, not a price "
                    "target); range 150.75–210.75; as of 2026-09-22; method: earnings; discount "
                    "rate 9.26%; terminal growth 2.5%; revenue-based cross-check 171.5")


@pytest.mark.asyncio
async def test_the_fair_value_is_absent_when_dcf_is_off(resolver, monkeypatch):
    from app.config import settings
    report = _avgo_shaped_report()

    async def fake_get(ticker, persona):
        return report

    import app.services.ticker_report_cache as trc
    monkeypatch.setattr(trc, "get_cached_report", fake_get)
    monkeypatch.setattr(settings, "DCF_ENABLED", False)
    block = await resolver.resolve("TICKER_REPORT", "AVGO|bill_ackman", None)
    for leaked in ("Caydex fair value", "180.25", "150.75", "210.75", "171.5", "price target",
                   "caydex_fair_value", "9.26"):
        assert leaked not in block, leaked
    assert "Moat pillars (score out of 10): Dim 0 7.5" in block   # the rest still leads
    # Anti-vacuity: the stored report still holds the estimate the switch withdrew.
    assert report["wall_street_consensus"]["caydex_fair_value"]["fair_value"] == 180.25


@pytest.mark.parametrize("dcf, expected", [
    ({"status": "refused", "refusal_reason": "Earnings are negative."},
     "Caydex fair value: none published for this report — Earnings are negative."),
    ({"status": "refused"}, "Caydex fair value: none published for this report."),
    ({"status": "ok", "fair_value": 42.5}, "Caydex fair value: 42.5 per share (Caydex model "
                                           "estimate, not a price target)."),
    # The shared currency rule (`app.utils.currency.currency_code`, 2026-10-09) reads "usd" as
    # USD, as the report and the Overview do; the inverted range is still dropped.
    ({"status": "ok", "fair_value": 42.5, "currency": "usd", "range_low": 50.0, "range_high": 40.0},
     "Caydex fair value: 42.5 USD per share (Caydex model estimate, not a price target)."),
    ({"status": "ok", "fair_value": 42.5, "currency": " twd "},
     "Caydex fair value: 42.5 TWD per share (Caydex model estimate, not a price target)."),
    ({"status": "ok", "fair_value": 42.5, "currency": "ßU"},
     "Caydex fair value: 42.5 per share (Caydex model estimate, not a price target)."),
    ({"status": "ok", "fair_value": 42.5, "currency": "US$"},
     "Caydex fair value: 42.5 per share (Caydex model estimate, not a price target)."),
    ({"status": "ok", "fair_value": float("nan"), "range_low": 1.0, "range_high": 2.0}, None),
    ({"status": "ok", "fair_value": -5.0}, None),
    ({"status": "ok", "fair_value": True}, None),
    ({"fair_value": 42.5}, None),                       # no status: not a published estimate
    ({"status": "pending", "fair_value": 42.5}, None),
    ("not-a-dict", None),
    (None, None),
])
def test_the_fair_value_line_degrades_by_omission(dcf, expected):
    lines = _fig_lines({"wall_street_consensus": {"caydex_fair_value": dcf}})
    got = [l for l in lines if l.startswith("Caydex fair value")]
    assert got == ([expected] if expected else [])


def test_the_fair_value_never_relabels_an_analyst_target():
    """The analyst rating and targets are unlicensed and stay dropped; the lead's only
    "price target" words are the disclaimer that the estimate is not one."""
    report = {"wall_street_consensus": dict(_ANALYST_ERA_WS)}
    text = "\n".join(_fig_lines(report))
    for leaked in ("205.5", "150.5", "260.5", "strong_buy", "deep_undervalued", "15.9", "analyst_"):
        assert leaked not in text, leaked
    assert text.lower().count("target") == 1 and "not a price target" in text


def _resolve_ticker_report_code() -> str:
    import ast as _ast
    import inspect
    import textwrap
    src = textwrap.dedent(inspect.getsource(_ccr.ChatContextResolver._resolve_ticker_report))
    return _ast.unparse(_ast.parse(src))   # this def only; comments gone, calls normalised


def test_the_lead_reads_the_report_after_the_kill_switch():
    """Def-bound and comment-free (`ast.unparse`): the figures lead is built from the report the
    kill switch already stripped, so a withdrawn estimate can never lead."""
    code = _resolve_ticker_report_code()
    assert code.index("report = strip_caydex_if_disabled(report)") \
        < code.index("lead.extend(_report_figures_lead(report))")
    assert "_without_lead_figures(_without_unmeasured_guidance(report))" in code
    assert "skip_top=_REPORT_SKIP_TOP" in code and "fundamental_metrics" in _ccr._REPORT_SKIP_TOP


# ── the other groups: outliers degrade by omission ──

def test_moat_pillars_tolerate_garbage_and_follow_the_pdf_peer_rule():
    dims = [None, "x", 5, [], {"name": "NaN", "score": float("nan")}, {"name": "Inf", "score": float("inf")},
            {"name": "Bool", "score": True}, {"name": "High", "score": 11.0}, {"score": 4.0},
            {"name": "Real", "score": 6.5, "peer_score": 0.0, "source": "grounded"},
            {"name": "real", "score": 9.0},                            # duplicate name
            {"name": "Legacy", "score": 3.0, "peer_score": 0, "source": "ai_legacy"}]
    line = _fig_lines({"moat_competition": {"dimensions": dims}})[0]
    assert line == "Moat pillars (score out of 10): Real 6.5 (researched); Legacy 3 (qualitative)"
    # One real peer score switches the peer line on for every valid one.
    line = _fig_lines({"moat_competition": {"dimensions": [
        {"name": "A", "score": 6.5, "peer_score": 0.0}, {"name": "B", "score": 7.0, "peer_score": 5.5},
        {"name": "C", "score": 7.0, "peer_score": float("nan")}]}})[0]
    assert line == "Moat pillars (score out of 10): A 6.5 vs peers 0; B 7 vs peers 5.5; C 7"
    assert _fig_lines({"moat_competition": {"dimensions": "garbage"}}) == []
    assert _fig_lines({"moat_competition": {"dimensions": [None, {}]}}) == []


def test_segments_sort_bound_and_label_their_currency():
    eng = {"period": "FY 2025", "revenue_unit": "Millions", "total_revenue": 1000.0,
           "reporting_currency": "TWD", "intersegment_eliminations": 50.0,
           "segments": [{"name": "Small", "current_revenue": 100.0, "previous_revenue": 90.0},
                        {"name": "Corporate", "current_revenue": -20.0},          # negative: no share
                        {"name": "Big", "current_revenue": 700.0, "previous_revenue": float("nan")},
                        {"name": "Corrupt", "current_revenue": 5000.0},           # above the total
                        {"name": "Nan", "current_revenue": float("nan")},
                        {"name": "big", "current_revenue": 1.0},                  # duplicate name
                        {"name": None, "current_revenue": 9.0}, "garbage", None,
                        {"name": "Huge", "current_revenue": 1e300}]}
    line = _fig_lines({"revenue_engine": eng})[0]
    assert line == (
        "Revenue segments (FY 2025; Millions of TWD; % = share of total revenue 1,000; "
        "intersegment sales of 50 removed in consolidation), largest first: Corrupt 5,000; "
        "Big 700 (70.0%); Small 100 (10.0%, prior yr 90); Corporate -20")
    # No total, no unit, an invalid currency: no shares, and no invented currency.
    line = _fig_lines({"revenue_engine": {"total_revenue": 0, "currency": "dollars", "segments": [
        {"name": f"S{i}", "current_revenue": float(i)} for i in range(9)]}})[0]
    assert line == ("Revenue segments (amounts in reporting currency), largest first: "
                    "S8 8; S7 7; S6 6; S5 5; S4 4; S3 3")
    assert _fig_lines({"revenue_engine": {"segments": []}}) == []
    assert _fig_lines({"revenue_engine": "garbage"}) == []
    # One currency rule everywhere (`app.utils.currency.currency_code`): a stored report whose
    # code is not canonical still names it; a case-folding trap never becomes a code.
    seg = [{"name": "A", "current_revenue": 1.0}]
    assert "Millions of TWD" in _fig_lines({"revenue_engine": {
        "revenue_unit": "Millions", "reporting_currency": " twd ", "segments": seg}})[0]
    assert "amounts in reporting currency" in _fig_lines({"revenue_engine": {
        "reporting_currency": "ßU", "segments": seg}})[0]


@pytest.mark.parametrize("rows, expected", [
    # Legacy rows (no `result`): only a negative surprise may be called a miss.
    ([{"period": "Q1", "surprise_percent": 0.0, "beat": False},
      {"period": "Q2", "surprise_percent": -0.04, "beat": False},
      {"period": "Q3", "surprise_percent": 2.0, "beat": True}],
     "Q1 +0.0%; Q2 +0.0%; Q3 beat +2.0%"),
    ([{"period": "Q1", "surprise_percent": -3.0, "beat": False}], "Q1 miss -3.0%"),
    # Malformed rows are skipped; only the last four usable rows are shown.
    ([None, "x", {"period": "Q0", "surprise_percent": float("nan")}, {"surprise_percent": 1.0},
      {"period": "Q1", "surprise_percent": True}]
     + [{"period": f"R{i}", "surprise_percent": float(i), "result": "beat"} for i in range(6)],
     "R2 beat +2.0%; R3 beat +3.0%; R4 beat +4.0%; R5 beat +5.0%"),
    ([{"period": "Q1", "surprise_percent": 1.0, "result": "bogus", "beat": "yes"}], "Q1 +1.0%"),
])
def test_the_track_record_rows(rows, expected):
    line = _fig_lines({"revenue_forecast": {"earnings_track_record": rows}})[0]
    assert line == f"Earnings vs analyst EPS estimates (last reported quarters, oldest first): {expected}"
    assert "-0.0" not in line


def test_management_skips_placeholders_and_dedupes():
    km = {"officers": [
        {"name": "Data unavailable", "title": "Officer", "ownership": "—"},       # placeholder
        {"name": "Jane Roe", "title": "CEO", "ownership": "—", "percent_owned": None},
        {"name": "jane roe", "title": "CEO", "ownership": "2M"},                   # duplicate
        {"name": "John Doe", "title": None, "ownership": 0, "percent_owned": float("nan")},
        {"name": "Ann Lee", "title": "CFO", "ownership": "0", "percent_owned": 250.0},   # % > 100
        {"name": "Al Kim", "title": "COO", "ownership": 1e300, "percent_owned": True},
        {"name": "Bo Li", "title": "CTO", "ownership": "1.2M", "percent_owned": 0.0123},
        None, "garbage", {"title": "No name"}],
        "top_holders": "garbage"}
    lines = _fig_lines({"key_management": km})
    assert lines == ["Officers, in role order (direct holdings after each one's latest Form 4, not as of the report date; % of shares "
                     "outstanding): Jane Roe (CEO); John Doe; Ann Lee (CFO); Al Kim (COO); "
                     "Bo Li (CTO): 1.2M shares, 0.0123%"]


def test_fundamentals_cards_skip_empty_values():
    cards = [
        {"title": "Profitability", "star_rating": 0, "quality_label": "Data unavailable", "metrics": []},
        {"title": "Growth", "star_rating": 6, "quality_label": None, "metrics": [
            {"label": "Revenue Growth", "value": "—"}, {"label": "EPS Growth", "value": "N/A"},
            {"name": "FCF Growth", "value": "12%"}, {"label": "Score", "value": 7.25},
            {"label": "Bad", "value": float("nan")}, {"label": None, "value": "1%"}, "garbage"]},
        {"title": "Valuation", "star_rating": True, "quality_label": "Rich", "metrics": "garbage"},
        {"title": None, "metrics": [{"label": "X", "value": "1"}]}, None,
    ]
    assert _fig_lines({"fundamental_metrics": cards}) == [
        "Growth card: FCF Growth 12%; Score 7.25",
        "Valuation card (Rich).",
    ]


def _one_metric_line(label, value="77.30%", **metric):
    cards = [{"title": "Profitability", "metrics": [{"label": label, "value": value, **metric}]}]
    lines = _fig_lines({"fundamental_metrics": cards})
    return lines[0] if lines else None


@pytest.mark.parametrize("label, peer_level, expected", [
    # An INDUSTRY median is named the industry's, as on the 1.1 card (`peer_wording`).
    ("Gross Margin (1.20x sector avg 64.3%)", "industry", "Gross Margin 77.30% (industry avg 64.3%)"),
    ("Debt-to-Equity (vs sector 0.95)", "industry", "Debt-to-Equity 77.30% (vs industry 0.95)"),
    ("P/E (sector average 22.40)", "industry", "P/E 77.30% (industry average 22.40)"),
    # A sector median, an unset level (older reports) or a junk level keeps the wire's word.
    ("Gross Margin (1.20x sector avg 64.3%)", "sector", "Gross Margin 77.30% (sector avg 64.3%)"),
    ("Gross Margin (1.20x sector avg 64.3%)", None, "Gross Margin 77.30% (sector avg 64.3%)"),
    ("Gross Margin (1.20x sector avg 64.3%)", "INDUSTRY", "Gross Margin 77.30% (sector avg 64.3%)"),
    ("Gross Margin (1.20x sector avg 64.3%)", 5, "Gross Margin 77.30% (sector avg 64.3%)"),
    # A loss-maker's label prints the median without a multiple.
    ("Net Margin (sector avg 12.3%)", "industry", "Net Margin 77.30% (industry avg 12.3%)"),
    # A label with no peer suffix is stated as it is.
    ("Revenue Growth (YoY)", None, "Revenue Growth (YoY) 77.30%"),
    ("Altman Z-Score", "industry", "Altman Z-Score 77.30%"),
    # A median too long to state whole is dropped with its words, never cut.
    ("Gross Margin (1.20x sector avg 123456789012345678901234567%)", "industry", "Gross Margin 77.30%"),
])
def test_a_card_metric_is_peer_worded_and_restated_compactly(label, peer_level, expected):
    line = _one_metric_line(label, peer_level=peer_level)
    assert line == f"Profitability card: {expected}"


def test_a_card_metric_label_too_long_to_state_whole_is_dropped():
    assert _one_metric_line("Gross Margin " + "x" * 200) is None
    assert _one_metric_line("Gross Margin\n(1.20x sector avg 64.3%)", peer_level="industry") == (
        "Profitability card: Gross Margin 77.30% (industry avg 64.3%)")   # controls → spaces
    assert _one_metric_line(" (1.20x sector avg 64.3%)") == "Profitability card: (1.20x sector avg 64.3%) 77.30%"


@pytest.mark.asyncio
async def test_the_block_names_an_industry_median_the_industrys(resolver, monkeypatch):
    """End to end: the resolved block says "industry avg" for an industry median, so report
    chat cannot call it a "sector average" beside a 1.1 card that says "industry"."""
    report = _wire_realistic_report()
    block = await _resolve_report(resolver, monkeypatch, report, ref="MSFT|warren_buffett")
    assert "Gross Margin 68.82% (industry avg 57.4%)" in block
    assert "sector avg" not in block
    # Anti-vacuity: the stored wire labels still say "sector".
    assert report["fundamental_metrics"][0]["metrics"][0]["label"] == "Gross Margin (1.20x sector avg 57.4%)"


@pytest.mark.parametrize("previous", [0.0, 0, -12.5, float("nan"), True, "80.0", None])
def test_a_segment_without_a_real_prior_year_states_none(previous):
    """0.0 is the collector's "no prior-year figure" (no FY-1 record; always for "Unallocated"),
    and iOS shows a prior only when it is > 0 — never "prior yr 0"."""
    line = _fig_lines({"revenue_engine": {"total_revenue": 100.0, "segments": [
        {"name": "Unallocated", "current_revenue": 20.0, "previous_revenue": previous},
        {"name": "Core", "current_revenue": 80.0, "previous_revenue": 70.0}]}})[0]
    assert line.endswith("largest first: Core 80 (80.0%, prior yr 70); Unallocated 20 (20.0%)")
    assert line.count("prior yr") == 1


def test_officers_beyond_the_limit_say_how_many_are_shown():
    rows = [{"name": f"Person {i}", "title": "VP"} for i in range(7)]
    rows.insert(3, {"name": "person 1", "title": "Dup"})          # a duplicate is not counted
    line = _fig_lines({"key_management": {"officers": rows}})[0]
    assert line == ("Officers, in role order (first 5 of 7; direct holdings after each one's latest Form 4, not as of the report date; % of "
                    "shares outstanding): Person 0 (VP); Person 1 (VP); Person 2 (VP); Person 3 (VP); "
                    "Person 4 (VP)")
    exactly = _fig_lines({"key_management": {"officers": rows[:4] + rows[5:6]}})[0]
    assert "first" not in exactly and "Person 4" in exactly


def test_a_holders_own_title_is_kept_and_the_collectors_label_is_not_repeated():
    line = _fig_lines({"key_management": {"top_holders": [
        {"name": "Fund A", "title": "10% Owner", "percent_ownership": 9.5},
        {"name": "Fund B", "title": "10 PERCENT OWNER", "percent_ownership": 8.0},
        {"name": "Jane Roe", "title": "director, 10 percent owner", "percent_ownership": 12.0}]}})[0]
    assert line == ("Top holders, 10%+ owners (each one's latest 13D/G filing, may predate the report): Fund A: 9.5% beneficial; "
                    "Fund B: 8% beneficial; Jane Roe (director, 10 percent owner): 12% beneficial")
    # An officer keeps any title, that one included.
    officer = _fig_lines({"key_management": {"officers": [{"name": "X Y", "title": "10% Owner"}]}})[0]
    assert officer.endswith(": X Y (10% Owner)")


def test_a_line_cut_for_space_is_logged_once(monkeypatch, caplog):
    """A figure the room drops reads "not included here" to the model, so every cut is logged —
    one bounded WARNING per lead naming each cut line, and none when everything fits."""
    monkeypatch.setattr(_ccr, "_REPORT_FIGURES_LEAD_CAP", 1900)
    with caplog.at_level(_logging.WARNING, logger="app.services.chat_context_resolver"):
        lead = _fig_lines(_wire_realistic_report())
    assert len("\n".join(lead)) <= 1900
    msgs = [r.getMessage() for r in caplog.records if "cut for space" in r.getMessage()]
    assert len(msgs) == 1, msgs
    assert "'MSFT'" in msgs[0] and "\n" not in msgs[0]
    assert "Officers, in role order left out" in msgs[0] and "Top holders, 10%+ owners left out" in msgs[0]
    cut_card = next(l for l in lead if l.endswith("; …"))
    name = cut_card.split(" (", 1)[0]
    assert f"{name} " in msgs[0] and "items" in msgs[0]
    monkeypatch.undo()                                      # the shipped cap: everything fits
    caplog.clear()
    with caplog.at_level(_logging.WARNING, logger="app.services.chat_context_resolver"):
        _fig_lines(_wire_realistic_report())
    assert not [r for r in caplog.records if "cut for space" in r.getMessage()]


def test_the_cut_log_is_bounded_and_cannot_forge_a_line(monkeypatch, caplog):
    monkeypatch.setattr(_ccr, "_REPORT_FIGURES_LEAD_CAP", 300)
    with caplog.at_level(_logging.WARNING, logger="app.services.chat_context_resolver"):
        _fig_lines(_monster_report() | {"symbol": "X\nERROR forged" * 50})
    msgs = [r.getMessage() for r in caplog.records if "cut for space" in r.getMessage()]
    assert len(msgs) == 1 and "\n" not in msgs[0] and len(msgs[0]) < 700


@pytest.mark.parametrize("report", [
    {}, {"moat_competition": None}, {"revenue_engine": []}, {"revenue_forecast": "x"},
    {"key_management": {"officers": None, "top_holders": None}}, {"fundamental_metrics": {}},
    {"wall_street_consensus": {"caydex_fair_value": {}}},
])
def test_a_legacy_report_without_the_figures_degrades_to_no_lines(report):
    assert _fig_lines(report) == []
    assert _ccr._report_figures_lead("not-a-dict") == []


@pytest.mark.asyncio
async def test_a_legacy_report_still_resolves_with_no_figures(resolver, monkeypatch):
    report = {"company_name": "Legacy Co", "executive_summary_text": "LEGACYMARK summary.",
              "moat_competition": {"durability_note": "DURABLEMARK"},
              "revenue_engine": {"analysis_note": "ENGINEMARK"},
              "key_management": {"ownership_insight": "OWNMARK"}}
    block = await _resolve_report(resolver, monkeypatch, report, ref="LGCY|warren_buffett")
    for marker in ("LEGACYMARK", "DURABLEMARK", "ENGINEMARK", "OWNMARK"):
        assert marker in block, marker
    for absent in ("Moat pillars", "Revenue segments", "Officers", "Forward forecasts",
                   "Caydex fair value", "Earnings vs", "None", "nan"):
        assert absent not in block, absent


# ── bounds, failure isolation, vendor names ──

def _monster_report():
    big = "N" * 500
    return {
        "wall_street_consensus": {"caydex_fair_value": {
            "status": "ok", "fair_value": 123456789.25, "range_low": 1.5, "range_high": 2e14,
            "as_of": "d" * 500, "method": "m " * 500, "discount_rate_pct": 9.5,
            "terminal_growth_pct": 2.5, "alternative_value": 3e14}},
        "moat_competition": {"dimensions": [{"name": f"{big}{i}", "score": 5.0, "peer_score": 5.0}
                                            for i in range(200)]},
        "revenue_engine": {"period": "p " * 200, "revenue_unit": "u " * 200, "total_revenue": 9e14,
                           "segments": [{"name": f"{big}{i}", "current_revenue": 1e14 - i,
                                         "previous_revenue": 9e13} for i in range(200)]},
        "revenue_forecast": {
            "projections": [_row(f"{1900 + i}", 1e14, 1e10) for i in range(200)],
            "forecast_analyst_count": 9999,
            "earnings_track_record": [{"period": big, "surprise_percent": 1e14, "result": "beat"}] * 200,
            "beat_summary": big},
        "key_management": {
            "officers": [{"name": f"{big}{i}", "title": big, "ownership": big} for i in range(200)],
            "top_holders": [{"name": f"{big}{i}", "title": big, "ownership": "9" * 500}
                            for i in range(200)]},
        "fundamental_metrics": [{"title": big, "star_rating": 5, "quality_label": big, "metrics": [
            {"label": big, "value": "9" * 500} for _ in range(200)]} for _ in range(200)],
    }


def test_the_lead_is_bounded_even_for_a_monster_report():
    import re as _re
    lead = _fig_lines(_monster_report())
    text = "\n".join(lead)
    assert 0 < len(text) <= _ccr._REPORT_FIGURES_LEAD_CAP
    # Priority order: the earlier groups win the room, the fundamentals cards squeeze first.
    assert lead[0].startswith("Caydex fair value: 123,456,789.25 per share (Caydex model estimate")
    assert lead[1].startswith("Moat pillars") and lead[2].startswith("Revenue segments")
    # Never a cut figure: a long value / period / holding is dropped whole (names may be cut
    # on a word boundary — text, not a number). The 500-digit strings never appear at all.
    assert not _re.search(r"\d…", text)
    assert "9" * 25 not in text and "d" * 25 not in text
    for line in lead:
        assert "\n" not in line


def test_a_figure_string_too_long_to_state_whole_is_dropped_not_cut():
    lines = _fig_lines({
        "fundamental_metrics": [{"title": "Growth", "metrics": [
            {"label": "Revenue Growth", "value": "12.3456789012345678901234%"},   # 25 chars
            {"label": "EPS Growth", "value": "38.1%"}]}],
        "key_management": {"officers": [{"name": "A B", "title": "CEO", "ownership": "12345678901234567"}]},
        "revenue_forecast": {"earnings_track_record": [
            {"period": "Q1 '26 (fiscal, restated)", "surprise_percent": 1.0, "result": "beat"}],
            "projections": [_row("2026", 1.0, 1.0, revenue_label="$1.0B but a very long label")]},
    })
    assert lines == ["Forward forecasts (analyst estimate): 2026 EPS $1.0",
                     "Growth card: EPS Growth 38.1%",
                     "Officers, in role order (direct holdings after each one's latest Form 4, not as of the report date; % of shares "
                     "outstanding): A B (CEO)"]


@pytest.mark.parametrize("cap", [0, -5, 3])
def test_fit_items_never_cuts_an_item(cap):
    assert _ccr._fit_items("Head: ", ["4,502.5", "3,602"], cap) is None
    assert _ccr._fit_items("", [], cap) is None


def test_fit_items_keeps_whole_items_and_marks_a_cut():
    f = _ccr._fit_items
    assert f("H: ", ["aa", "bb", "cc"], 100) == "H: aa; bb; cc"
    assert f("H: ", ["aa", "bb", "cc"], len("H: aa; bb; cc")) == "H: aa; bb; cc"   # exact fit
    assert f("H: ", ["aa", "bb", "cc"], len("H: aa; bb; cc") - 1) == "H: aa; bb; …"
    assert f("H: ", ["aa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"], 12) == "H: aa; …"
    assert f("Whole line.", [], 11) == "Whole line." and f("Whole line.", [], 10) is None
    # The cut marker is budgeted too: a line never exceeds its cap, at any cap.
    assert f("H: ", ["aa", "bb", "cc"], len("H: aa; bb")) == "H: aa; …"
    for items in (["aa", "bb", "cc"], ["a" * 7, "b", "c" * 4, "d"], ["x"]):
        for cap in range(0, 40):
            got = f("H: ", items, cap)
            assert got is None or len(got) <= cap, (items, cap, got)
            if got is not None:   # whole items only, in order
                kept = got[len("H: "):].removesuffix("; …").split("; ")
                assert kept == items[:len(kept)], (items, cap, got)


def test_a_raising_group_costs_only_itself(monkeypatch, caplog):
    def boom(report):
        raise RuntimeError("malformed\nforged")

    groups = list(_ccr._FIGURE_GROUPS)
    groups.insert(1, (boom, 300))
    monkeypatch.setattr(_ccr, "_FIGURE_GROUPS", tuple(groups))
    with caplog.at_level(_logging.WARNING, logger="app.services.chat_context_resolver"):
        lead = _fig_lines(_full_figures_report(symbol="AVGO\nERROR forged"))
    assert lead[0].startswith("Caydex fair value") and lead[1].startswith("Moat pillars")
    msgs = [r.getMessage() for r in caplog.records if "report figures group" in r.getMessage()]
    assert len(msgs) == 1 and "boom" in msgs[0] and "RuntimeError" in msgs[0]
    assert "'AVGO\\nERROR forged'" in msgs[0]            # the symbol is %r-rendered, one record
    assert "\n" not in msgs[0]                            # and so is the exception text
    # A raise in a pure builder is a bug: the record carries the stack that locates it.
    record = next(r for r in caplog.records if "report figures group" in r.getMessage())
    assert record.exc_info is not None and record.exc_info[0] is RuntimeError


def test_the_figures_lead_names_no_model_or_vendor():
    import re as _re
    for report in (_full_figures_report(), _avgo_shaped_report(), _monster_report()):
        text = "\n".join(_fig_lines(report)).lower()
        for word in ("gemini", "google", "openai", "llm", "fmp", "financial modeling prep",
                     "grounded"):
            assert word not in text, word
        assert not _re.search(r"\b(nan|none|true|false|inf|-inf)\b", text), text


def test_without_lead_figures_is_scoped_and_never_mutates():
    report = {"Moat_Competition": {"dimensions": [1]},                    # not the section name
              "moat_competition": {"Dimensions": [1], "durability_note": "D"},
              "key_management": "garbage",
              "elsewhere": {"officers": ["kept"], "cagr": 1.0}}
    out = _ccr._without_lead_figures(report)
    assert out["moat_competition"] == {"durability_note": "D"}        # child keys: any case
    assert out["Moat_Competition"] == {"dimensions": [1]}
    assert out["key_management"] == "garbage"
    assert out["elsewhere"] == {"officers": ["kept"], "cagr": 1.0}
    assert report["moat_competition"] == {"Dimensions": [1], "durability_note": "D"}
    assert _ccr._without_lead_figures("x") == "x"


# ── final review 2026-10-09: Key Management names its real, filing-dated basis ──


def test_key_management_never_dates_a_holding_to_the_report():
    """An officer's figure is the direct balance after that person's latest Form 4 (it can be a
    year before the report) and a holder's is its latest 13D/G (it can be years old): the lead
    used to call both "as of the report date"."""
    report = _wire_realistic_report()
    text = "\n".join(_fig_lines(report))
    assert "holdings as of the report date" not in text
    assert "(as of the report date)" not in text
    officers = next(l for l in text.splitlines() if l.startswith("Officers, in role order ("))
    assert "direct holdings after each one's latest Form 4, not as of the report date" in officers
    holders = next(l for l in text.splitlines() if l.startswith("Top holders, 10%+ owners ("))
    assert "each one's latest 13D/G filing, may predate the report" in holders
