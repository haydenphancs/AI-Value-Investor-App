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
    were answered "not included here" — the figures sorted behind JSONB trivia one level down."""
    block = await _resolve_report(resolver, monkeypatch, _avgo_shaped_report())
    lines = _dump_of(block).splitlines()
    for figure in _HEADLINE_FIGURES:
        assert figure in lines, figure
    # The estimate leads its block; the trivia that used to beat it does not get ahead of it.
    dcf = [l for l in lines if l.startswith("wall_street_consensus.caydex_fair_value.")]
    assert dcf[0] == _HEADLINE_FIGURES[0], dcf
    # Every narrative still shows (the per-section share is unchanged).
    for marker in _NARRATIVE_MARKERS:
        assert marker in "\n".join(lines), marker
    # A pillar's per-metric drivers and confidence are on no screen: never in the dump.
    assert not any(".drivers" in l or ".confidence" in l for l in lines)
    assert len("\n".join(lines)) <= _ccr._REPORT_DUMP_CAP


@pytest.mark.asyncio
async def test_the_old_child_order_drops_the_headline_figures(resolver, monkeypatch):
    """Anti-vacuity: the same report under the old (direct-children-only) map loses both."""
    monkeypatch.setattr(_ccr, "_REPORT_CHILD_PRIORITY", _OLD_CHILD_PRIORITY)
    lines = _dump_of(await _resolve_report(resolver, monkeypatch, _avgo_shaped_report())).splitlines()
    assert _HEADLINE_FIGURES[0] not in lines
    assert not any(l.startswith("moat_competition.dimensions[0].score") for l in lines)
    assert "wall_street_consensus.caydex_fair_value.beta: 1.2" in lines   # the trivia that won


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
    """The resolver must not import the collector / the intel service (they pull the whole report
    pipeline into every chat turn), so it duplicates three constants. Pinned equal here."""
    from app.services import competitor_intel_service as cis
    from app.services.agents import ticker_report_data_collector as col
    assert _ccr._COMPETITOR_SEGMENT_CAP == cis.SEGMENT_MAX_CHARS
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
