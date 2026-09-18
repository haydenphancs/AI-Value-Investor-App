"""Guards for the market-awareness chat tools, and above all for the ONE paid path.

WHY THIS FILE EXISTS. Ask Cay AI could not answer "why". Asked why NAVN was down 22% it
restated the price and volume; asked why Basic Materials was lagging — a question the app
itself suggests — it replied that its tools only cover individual companies. Both were
accurate descriptions of a two-tool surface with no news and no sector data.

`chat_market_tools` closes that with an escalation ladder whose third tier is a grounded
Google Search at roughly $0.035 a call. Three things below are therefore load-bearing rather
than decorative:

  1. The `"today"` window label. It is a cache-identity component shared with the Updates
     sweeper AND the guard against answering a daily question with a 15-day narrative.
  2. The escalation gates. Every one of them is money.
  3. Cache-before-budget ordering, so a row someone already paid for is free to reuse.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import chat_market_tools as cmt
from app.services.daily_move_attribution import Attribution, CauseKind, MoveContext


def _explanation(*, tier: str, kind: CauseKind, change: float = -22.0, tag: str | None = None):
    return SimpleNamespace(
        ticker="NAVN", company_name="Navan, Inc.", change_percent=change, price=20.26,
        tier=tier, z=4.1, industry_name="Software", industry_change_percent=-0.4,
        market_change_percent=-0.2,
        attribution=Attribution(
            kind=kind, tag=tag, detail="detail",
            context=MoveContext(change_percent=change, z=4.1),
            considered=[],
        ),
    )


@pytest.fixture
def no_news(monkeypatch):
    """Tier 2 stubbed out. Every test here is about tier 1 and tier 3."""
    monkeypatch.setattr(
        cmt, "fetch_ticker_news",
        AsyncMock(return_value={"news_available": True, "articles": []}),
    )


# ── The window label ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_catalyst_is_always_asked_for_todays_window(monkeypatch):
    """`"today"` — never a multi-day label, and never a computed one.

    `daily_move_attribution`'s own header records the measured failure: the only cached ACHR
    row read `window "Last 15 Days" · +42.7%`, and rendering that under a red daily move is
    "a correct answer to a different question". The label is also a cache-key component
    (migration 095) that the Updates sweeper already writes as `"today"`, so drifting it
    both re-opens that bug AND stops chat sharing a cache someone already paid for.
    """
    seen = []

    async def _get_catalyst(ticker, change_pct, window_label, **kw):
        seen.append(window_label)
        return {"tag": "Guidance Cut", "reason": "Navan guided FY26 revenue below consensus.",
                "sources": [{"publisher": "reuters", "title": "Navan cuts guidance"}]}

    monkeypatch.setattr(
        cmt, "_claim_web_search", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.services.price_catalyst_service.get_price_catalyst_service",
        lambda: SimpleNamespace(get_catalyst=_get_catalyst),
    )
    exp = _explanation(tier="Extreme", kind=CauseKind.NONE)
    out = await cmt._maybe_web_catalyst("NAVN", exp, "none")

    assert out is not None and out["catalyst"] == "Guidance Cut"
    assert seen, "the catalyst was never called — this test would pass vacuously"
    assert set(seen) == {"today"}, f"window label drifted to {seen}"


@pytest.mark.asyncio
async def test_the_web_catalyst_forwards_the_listed_name_on_both_reads(monkeypatch):
    """LTC Properties (NYSE: LTC) on a σ-Extreme day with no dated cause: the paid search
    used to be "LTC moved -8.0% over today" — Litecoin's headlines, narrated as the REIT's
    cause with citations and cached for every reader. The explanation already knows the
    listed name; it must travel on the cache read AND the fresh search."""
    seen: list = []

    async def _get_catalyst(ticker, change_pct, window_label, *, cache_only=False,
                            company_name=None, **kw):
        seen.append((cache_only, company_name))
        if cache_only:
            return None
        return {"tag": "Dividend Cut", "reason": "LTC Properties cut its dividend.",
                "sources": [{"publisher": "reuters", "title": "LTC cuts dividend"}]}

    monkeypatch.setattr(cmt, "_claim_web_search", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.services.price_catalyst_service.get_price_catalyst_service",
        lambda: SimpleNamespace(get_catalyst=_get_catalyst),
    )
    exp = _explanation(tier="Extreme", kind=CauseKind.NONE)
    out = await cmt._maybe_web_catalyst("NAVN", exp, "none")
    assert out is not None and out["catalyst"] == "Dividend Cut"
    assert seen == [(True, "Navan, Inc."), (False, "Navan, Inc.")], seen


# ── The escalation gates. Each one is money. ─────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["earnings", "analyst", "company_news"])
async def test_no_paid_search_when_a_company_specific_cause_was_already_found(kind, monkeypatch):
    """Tier 1 is deterministic and dated. Paying to second-guess it is how a good answer
    gets talked over by a vaguer one."""
    called = AsyncMock()
    monkeypatch.setattr(
        "app.services.price_catalyst_service.get_price_catalyst_service",
        lambda: SimpleNamespace(get_catalyst=called),
    )
    exp = _explanation(tier="Extreme", kind=CauseKind(kind), tag="Q3 Earnings")
    assert await cmt._maybe_web_catalyst("NAVN", exp, kind) is None
    called.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["Typical", "Notable", "flat", "notable", "unknown", None])
async def test_no_paid_search_for_an_ordinary_day(tier, monkeypatch):
    """Only Unusual / Extreme / the fixed-band `extreme` fallback earn a search — byte-identical
    to the Updates sweeper's `_CATALYST_TIERS`. On an ordinary day there is usually no catalyst
    to find, and searching invites the model to manufacture significance."""
    called = AsyncMock()
    monkeypatch.setattr(
        "app.services.price_catalyst_service.get_price_catalyst_service",
        lambda: SimpleNamespace(get_catalyst=called),
    )
    exp = _explanation(tier=tier, kind=CauseKind.NONE)
    assert await cmt._maybe_web_catalyst("NAVN", exp, "none") is None
    called.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [0.0, None])
async def test_no_paid_search_for_a_phantom_flat_move(change, monkeypatch):
    """A live defect in the Updates sweeper before `_maybe_price_move` gained this check: an
    unusable quote plus a stale tier bought a web search for a +0.0% move that never happened,
    and then stored the answer."""
    called = AsyncMock()
    monkeypatch.setattr(
        "app.services.price_catalyst_service.get_price_catalyst_service",
        lambda: SimpleNamespace(get_catalyst=called),
    )
    exp = _explanation(tier="Extreme", kind=CauseKind.NONE, change=change)
    assert await cmt._maybe_web_catalyst("NAVN", exp, "none") is None
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_tier_gate_is_not_vacuous(monkeypatch):
    """Anti-vacuity for the two tests above: prove the SAME inputs minus the gate do search."""
    seen = []

    async def _get_catalyst(ticker, change_pct, window_label, *, cache_only=False, **kw):
        seen.append(cache_only)
        # A cache MISS, so the live tier is the one that answers — which is what makes this
        # a real check on the gate rather than on the cache.
        return None if cache_only else {"tag": "X", "reason": "because", "sources": []}

    monkeypatch.setattr(cmt, "_claim_web_search", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.services.price_catalyst_service.get_price_catalyst_service",
        lambda: SimpleNamespace(get_catalyst=_get_catalyst),
    )
    for tier in ("Unusual", "Extreme", "extreme"):
        seen.clear()
        exp = _explanation(tier=tier, kind=CauseKind.NONE)
        out = await cmt._maybe_web_catalyst("NAVN", exp, "none")
        assert out is not None and out["freshly_searched"] is True, tier
        assert seen == [True, False], f"{tier}: expected a cache probe then a live search"


# ── Cache before budget ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_cached_catalyst_costs_no_budget_unit(monkeypatch):
    """The cache is consulted BEFORE the budget, deliberately.

    The Updates sweeper already writes `"today"` rows for the tickers people watch. Charging a
    daily-cap unit for a row that was already paid for would let one popular ticker exhaust
    the ceiling while costing nothing — starving the long-tail names that genuinely need a
    search.
    """
    calls = []

    async def _get_catalyst(ticker, change_pct, window_label, *, cache_only=False, **kw):
        calls.append(cache_only)
        return {"tag": "Cached", "reason": "already known", "sources": []}

    claim = AsyncMock(return_value=True)
    monkeypatch.setattr(cmt, "_claim_web_search", claim)
    monkeypatch.setattr(
        "app.services.price_catalyst_service.get_price_catalyst_service",
        lambda: SimpleNamespace(get_catalyst=_get_catalyst),
    )
    exp = _explanation(tier="Extreme", kind=CauseKind.NONE)
    out = await cmt._maybe_web_catalyst("NAVN", exp, "none")

    assert out["catalyst"] == "Cached"
    assert out["freshly_searched"] is False
    assert calls == [True], "the first probe must be cache-only"
    claim.assert_not_awaited(), "a cache hit must not consume a paid-search unit"


@pytest.mark.asyncio
async def test_a_refused_budget_degrades_instead_of_erroring(monkeypatch):
    """The cap binding is not a failure. The turn keeps its deterministic answer."""
    async def _get_catalyst(ticker, change_pct, window_label, *, cache_only=False, **kw):
        assert cache_only, "a live search ran despite the budget refusing"
        return None

    monkeypatch.setattr(cmt, "_claim_web_search", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "app.services.price_catalyst_service.get_price_catalyst_service",
        lambda: SimpleNamespace(get_catalyst=_get_catalyst),
    )
    exp = _explanation(tier="Extreme", kind=CauseKind.NONE)
    assert await cmt._maybe_web_catalyst("NAVN", exp, "none") is None


@pytest.mark.asyncio
async def test_the_budget_fails_CLOSED(monkeypatch):
    """Opposite of `_claim_chat_turn_or_error`, and deliberate. That one fails OPEN so a DB
    blip cannot wall a user out of chat. This one guards SPEND: failing open would uncap the
    only paid path in the file, and refusing costs nothing but a slightly thinner answer."""
    from app.services.chat_budget_service import ChatBudgetUnavailable

    def _boom(*a, **kw):
        raise ChatBudgetUnavailable("supabase down")

    monkeypatch.setattr(
        cmt, "get_chat_budget_service",
        lambda: SimpleNamespace(try_claim_turn=_boom),
    )
    assert await cmt._claim_web_search() is False


@pytest.mark.asyncio
async def test_the_kill_switch_stops_every_paid_search(monkeypatch):
    monkeypatch.setattr(cmt.settings, "CHAT_WEB_SEARCH_ENABLED", False)
    assert await cmt._claim_web_search() is False


@pytest.mark.asyncio
async def test_the_daily_cap_refuses_at_the_ceiling(monkeypatch):
    """The RPC signals "cap reached" with -1 and no mutation — it does not raise."""
    monkeypatch.setattr(cmt.settings, "CHAT_WEB_SEARCH_ENABLED", True)
    monkeypatch.setattr(
        cmt, "get_chat_budget_service",
        lambda: SimpleNamespace(try_claim_turn=lambda *a, **kw: -1),
    )
    assert await cmt._claim_web_search() is False
    # Anti-vacuity: the same path admits when the RPC returns a real count.
    monkeypatch.setattr(
        cmt, "get_chat_budget_service",
        lambda: SimpleNamespace(try_claim_turn=lambda *a, **kw: 7),
    )
    assert await cmt._claim_web_search() is True


# ── Degradation: silence is never a finding ──────────────────────────────────

@pytest.mark.asyncio
async def test_an_unreadable_move_is_reported_as_unreadable_not_flat(monkeypatch, no_news):
    """`attribute_ticker_move` returns None ONLY for an unreadable quote. Describing that as
    an unchanged day is the `describe_no_cause` lie in a different costume — and this repo has
    shipped the "NaN reaching max() and winning" version of it more than once."""
    monkeypatch.setattr(
        "app.services.widget_movers_service.get_widget_movers_service",
        lambda: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=None)),
    )
    out = await cmt.explain_price_move("NAVN")
    assert out["move_readable"] is False
    assert "unchanged" not in out["note"].lower() or "rather than" in out["note"]
    assert "change_percent" not in out


@pytest.mark.asyncio
async def test_a_failed_news_read_says_so_rather_than_reporting_no_news(monkeypatch):
    """An empty list and a failed call are different claims. `_MarketContext` carries
    `*_available` flags for exactly this reason: asserting a negative nobody checked is a lie
    on a credit-charged turn."""
    class _Boom:
        async def get_ticker_news(self, *a, **kw):
            raise RuntimeError("supabase down")

    monkeypatch.setattr(
        "app.services.news_cache_service.get_news_cache_service", lambda: _Boom()
    )
    out = await cmt.fetch_ticker_news("NAVN")
    assert out["news_available"] is False
    assert "no news" in out["note"].lower()
    assert "articles" not in out


@pytest.mark.asyncio
async def test_market_snapshot_omits_a_failed_leg_rather_than_zeroing_it(monkeypatch):
    """Every leg degrades independently. A sector list of zeroes built out of an outage would
    let the model report a flat market that never happened."""
    class _Movers:
        async def get_sector_performance(self):
            raise RuntimeError("screener down")

        async def get_industry_performance(self):
            return [{"industry": "Steel", "sector": "Basic Materials",
                     "changesPercentage": -2.4}]

        async def get_scanner_inputs(self):
            raise RuntimeError("universe down")

    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service", lambda: _Movers()
    )
    monkeypatch.setattr(
        "app.services.news_insight_service.get_news_insight_service",
        lambda: SimpleNamespace(get_cards=AsyncMock(return_value={})),
    )
    out = await cmt.fetch_market_snapshot()
    assert "sectors" not in out, "a failed leg must be ABSENT, never an empty/zero list"
    assert out["leading_industries"][0]["industry"] == "Steel"
    assert "top_gainers" not in out


# ── The earnings direction conflict ──────────────────────────────────────────

def test_the_beat_and_miss_tags_match_the_attribution_module():
    """COUPLING GUARD. `_direction_conflict` matches on two tag strings owned by
    `daily_move_attribution`. A reword there would silently disable the note — the tool would
    keep working, keep returning a cause, and quietly go back to telling the model a stock
    fell because it beat estimates. Pin the strings so the reword fails here instead."""
    import inspect

    from app.services import daily_move_attribution as dma

    src = inspect.getsource(dma.detect_earnings)
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert f'tag = "{cmt._BEAT_TAG}"' in body
    assert f'tag = "{cmt._MISS_TAG}"' in body


@pytest.mark.parametrize(
    "tag,change,expect",
    [
        ("Earnings Beat", -21.7, "BEAT"),      # measured live: NAVN, 2026-09-10
        ("Earnings Miss", +8.4, "MISSED"),
        ("Earnings Beat", +8.4, None),          # agrees — no note
        ("Earnings Miss", -8.4, None),          # agrees — no note
        ("Earnings", -21.7, None),              # in-line print, neither beat nor miss
        (None, -21.7, None),
        ("Earnings Beat", None, None),          # unreadable move
    ],
)
def test_a_contradicting_earnings_reaction_is_flagged(tag, change, expect):
    note = cmt._direction_conflict(tag, change)
    if expect is None:
        assert note is None
    else:
        assert note is not None and expect in note
        assert "Do not say" in note


@pytest.mark.asyncio
async def test_the_conflict_note_reaches_the_tool_result(monkeypatch, no_news):
    """Wiring, not just the leaf. Testing a correct helper that nothing CALLS is the recurring
    failure mode in this repo — five mutations in one session survived by removing the call
    rather than the logic."""
    from app.services.daily_move_attribution import CauseKind

    exp = _explanation(tier="extreme", kind=CauseKind.EARNINGS, change=-21.7, tag="Earnings Beat")
    monkeypatch.setattr(
        "app.services.widget_movers_service.get_widget_movers_service",
        lambda: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=exp)),
    )
    out = await cmt.explain_price_move("NAVN")
    assert "BEAT" in out["important"]
    assert out["cause"] == "Earnings Beat"


@pytest.mark.asyncio
async def test_the_unusualness_note_never_claims_a_sigma_it_did_not_compute(monkeypatch, no_news):
    """σ is precomputed for the top-200 watchlist only, so a long-tail ticker legitimately has
    none and `classify_move` falls back to the fixed price band. Printing "4.1x its typical
    daily swing" from a band label would be a fabricated statistic."""
    from app.services.daily_move_attribution import CauseKind

    exp = _explanation(tier="extreme", kind=CauseKind.NONE)
    exp.z = None                                   # no σ row — the fixed-band path
    monkeypatch.setattr(
        "app.services.widget_movers_service.get_widget_movers_service",
        lambda: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=exp)),
    )
    # ⚠️ These fixture values — an extreme tier with NO company-specific cause — are exactly
    # the condition that unlocks TIER 3, the paid web catalyst. Unstubbed, `explain_price_move`
    # reached the real catalyst cache and the real daily budget (both Supabase) on every call
    # here; the hermeticity guard blocked them, the tool's own `except` swallowed the failure,
    # and this test stayed green on the degraded path. Stub both seams exactly as the tier-3
    # tests above do, so the assertions below are about `_unusualness_note` and nothing else.
    monkeypatch.setattr(cmt, "_claim_web_search", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "app.services.price_catalyst_service.get_price_catalyst_service",
        lambda: SimpleNamespace(get_catalyst=AsyncMock(return_value=None)),
    )
    out = await cmt.explain_price_move("NAVN")
    assert "judged on price alone" in out["how_unusual"]
    assert "x its typical" not in out["how_unusual"]

    # And the σ path DOES report the multiple — anti-vacuity for the assertion above.
    exp.z, exp.tier = 4.1, "Extreme"
    out = await cmt.explain_price_move("NAVN")
    assert "4.1x its typical daily swing" in out["how_unusual"]


# ── Outliers: a NaN must never reach the model ───────────────────────────────

def test_num_refuses_every_non_finite_value():
    """`x or 0.0` DOES NOT catch NaN — NaN is truthy, so `nan or 0.0` is `nan`. It then
    reaches `json.dumps`, which emits the bare token `NaN` (invalid JSON) into a tool result
    the model has to parse."""
    for bad in (float("nan"), float("inf"), float("-inf"), None, "", "abc", {}, []):
        assert cmt._num(bad) is None, bad
    # Anti-vacuity: real numbers survive, including a signed zero and a negative.
    assert cmt._num(-2.5349) == -2.53
    assert cmt._num(0.0) == 0.0
    assert cmt._num("3.5") == 3.5


@pytest.mark.asyncio
async def test_a_nan_sector_is_dropped_not_zeroed(monkeypatch):
    """`market_movers_service._group_performance` gates on `change is not None`, NOT on
    isfinite — so ONE malformed universe row makes a whole sector's mean NaN. Reporting that
    sector as 0.00% would be a fabricated flat day; the honest move is to omit it."""
    class _Movers:
        async def get_sector_performance(self):
            return [
                {"sector": "Technology", "changesPercentage": float("nan"), "constituents": 618},
                {"sector": "Energy", "changesPercentage": -0.33, "constituents": 237},
            ]

        async def get_industry_performance(self):
            return [{"industry": "Steel", "sector": "Basic Materials",
                     "changesPercentage": float("inf")}]

        async def get_scanner_inputs(self):
            return ({}, {})

    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service", lambda: _Movers()
    )
    monkeypatch.setattr(
        "app.services.news_insight_service.get_news_insight_service",
        lambda: SimpleNamespace(get_cards=AsyncMock(return_value={})),
    )
    out = await cmt.fetch_market_snapshot()
    assert [s["sector"] for s in out["sectors"]] == ["Energy"]
    assert "leading_industries" not in out, "an infinite industry change must not be emitted"

    # The whole payload must be valid JSON with NO NaN token — this is what actually reaches
    # Gemini, via `json.dumps(result)[:8000]` in `stream_agentic`.
    import json

    encoded = json.dumps(out, allow_nan=False)
    assert "NaN" not in encoded and "Infinity" not in encoded


@pytest.mark.asyncio
async def test_the_move_payload_is_json_safe(monkeypatch, no_news):
    """Same guarantee on the other tool. A NaN industry delta arriving from a degraded
    universe must not turn the whole tool result into invalid JSON."""
    import json

    from app.services.daily_move_attribution import CauseKind

    exp = _explanation(tier="Extreme", kind=CauseKind.NONE)
    exp.industry_change_percent = float("nan")
    exp.market_change_percent = float("inf")
    monkeypatch.setattr(
        "app.services.widget_movers_service.get_widget_movers_service",
        lambda: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=exp)),
    )
    monkeypatch.setattr(cmt, "_maybe_web_catalyst", AsyncMock(return_value=None))
    out = await cmt.explain_price_move("NAVN")

    assert "industry_change_percent" not in out
    assert "market_change_percent" not in out
    assert out["industry"] == "Software", "the industry NAME is still useful without its %"
    json.dumps(out, allow_nan=False)


# ── Never a dead end ─────────────────────────────────────────────────────────
#
# From the user, after a follow-up chip Cay AI had PROPOSED came back "I don't have specific
# information on what caused copper to drop today": *"i need all answer should be a reason for
# it. or at least, if there is no reason, then say #ticker move normally in a range like a
# normal up and down. or no clear catalyst news."*

@pytest.mark.asyncio
async def test_an_ordinary_move_still_gets_an_answer(monkeypatch):
    """A calm day is not an absence of an answer — it IS the answer, and it must be stated."""
    from app.services.daily_move_attribution import CauseKind

    exp = _explanation(tier="Typical", kind=CauseKind.NONE, change=0.32)
    monkeypatch.setattr(
        "app.services.widget_movers_service.get_widget_movers_service",
        lambda: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=exp)),
    )
    monkeypatch.setattr(
        cmt, "fetch_ticker_news",
        AsyncMock(return_value={"news_available": True, "articles": [{"headline": "x"}]}),
    )
    out = await cmt.explain_price_move("KO")
    assert out["no_single_catalyst"] is True
    line = out["bottom_line"]
    assert line and "0.3% today" in line
    assert "No single catalyst" in line
    assert "inside its normal daily range" in out["how_unusual"]


@pytest.mark.asyncio
async def test_no_news_at_all_is_said_differently_from_a_failed_read(monkeypatch):
    """Three distinct states, three distinct sentences. Collapsing "we checked and there was
    nothing" into "we could not check" (or the reverse) is how a confident negative gets
    asserted out of an outage."""
    from app.services.daily_move_attribution import CauseKind

    exp = _explanation(tier="Typical", kind=CauseKind.NONE, change=0.32)
    monkeypatch.setattr(
        "app.services.widget_movers_service.get_widget_movers_service",
        lambda: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=exp)),
    )

    monkeypatch.setattr(
        cmt, "fetch_ticker_news",
        AsyncMock(return_value={"news_available": True, "articles": []}),
    )
    assert "No company news was published today" in (await cmt.explain_price_move("KO"))["bottom_line"]

    monkeypatch.setattr(
        cmt, "fetch_ticker_news", AsyncMock(return_value={"news_available": False}),
    )
    line = (await cmt.explain_price_move("KO"))["bottom_line"]
    assert "could not be checked" in line
    assert "do not say there was none" in line


@pytest.mark.asyncio
async def test_a_real_cause_is_never_talked_over_by_the_fallback(monkeypatch, no_news):
    """The bottom line is the answer of LAST resort. Emitting it beside a found cause would
    let the model close a correct earnings explanation with "no catalyst is visible"."""
    from app.services.daily_move_attribution import CauseKind

    for kind in (CauseKind.EARNINGS, CauseKind.COMPANY_NEWS, CauseKind.SECTOR, CauseKind.MARKET):
        exp = _explanation(tier="Extreme", kind=kind, tag="Q3 Earnings")
        monkeypatch.setattr(
            "app.services.widget_movers_service.get_widget_movers_service",
            lambda exp=exp: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=exp)),
        )
        monkeypatch.setattr(cmt, "_maybe_web_catalyst", AsyncMock(return_value=None))
        out = await cmt.explain_price_move("NAVN")
        assert "bottom_line" not in out, kind
        assert "no_single_catalyst" not in out, kind


@pytest.mark.asyncio
async def test_a_web_catalyst_also_suppresses_the_fallback(monkeypatch, no_news):
    from app.services.daily_move_attribution import CauseKind

    exp = _explanation(tier="Extreme", kind=CauseKind.NONE)
    monkeypatch.setattr(
        "app.services.widget_movers_service.get_widget_movers_service",
        lambda: SimpleNamespace(attribute_ticker_move=AsyncMock(return_value=exp)),
    )
    monkeypatch.setattr(
        cmt, "_maybe_web_catalyst",
        AsyncMock(return_value={"reason": "Guidance cut", "from_web_search": True}),
    )
    out = await cmt.explain_price_move("NAVN")
    assert "bottom_line" not in out
    assert out["web_research"]["reason"] == "Guidance cut"


@pytest.mark.asyncio
async def test_an_industry_outside_the_top_and_bottom_five_is_still_visible(monkeypatch):
    """The copper case, pinned. Five of ~150 industries told the day's story but left every
    other named industry unanswerable — the tool knew the number and could not show it."""
    # Copper MID-PACK, deliberately: with it at either extreme this test passes through
    # `lagging_industries` and proves nothing about the extra list. (The first version of
    # this fixture did exactly that and survived a mutation deleting the feature.)
    rows = [{"industry": f"Up{i}", "sector": "S", "changesPercentage": 10.0 - i * 0.4}
            for i in range(20)]
    rows.append({"industry": "Copper", "sector": "Basic Materials", "changesPercentage": -6.4})
    rows += [{"industry": f"Down{i}", "sector": "S", "changesPercentage": -7.0 - i * 0.4}
             for i in range(20)]
    rows.sort(key=lambda r: -r["changesPercentage"])

    class _Movers:
        async def get_sector_performance(self):
            return [{"sector": "Basic Materials", "changesPercentage": -2.5, "constituents": 280}]

        async def get_industry_performance(self):
            return rows

        async def get_scanner_inputs(self):
            return ({}, {})

    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service", lambda: _Movers()
    )
    monkeypatch.setattr(
        "app.services.news_insight_service.get_news_insight_service",
        lambda: SimpleNamespace(get_cards=AsyncMock(return_value={})),
    )
    out = await cmt.fetch_market_snapshot()
    edges = {r["industry"] for r in
             out.get("leading_industries", []) + out.get("lagging_industries", [])}
    assert "Copper" not in edges, "fixture is wrong — Copper must be mid-pack to test anything"
    extra = {r["industry"] for r in out.get("other_industries_that_moved", [])}
    assert "Copper" in extra


@pytest.mark.asyncio
async def test_a_flat_industry_is_not_padded_into_the_list(monkeypatch):
    """Anti-vacuity, and a cap guard: an industry that did not move needs no row — its honest
    answer is "it moved normally" — and 150 of them would blow the tool-result cap."""
    rows = [{"industry": f"Flat{i}", "sector": "S", "changesPercentage": 0.01} for i in range(60)]
    rows.append({"industry": "Copper", "sector": "Basic Materials", "changesPercentage": -6.4})

    class _Movers:
        async def get_sector_performance(self):
            return []

        async def get_industry_performance(self):
            return rows

        async def get_scanner_inputs(self):
            return ({}, {})

    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service", lambda: _Movers()
    )
    monkeypatch.setattr(
        "app.services.news_insight_service.get_news_insight_service",
        lambda: SimpleNamespace(get_cards=AsyncMock(return_value={})),
    )
    out = await cmt.fetch_market_snapshot()
    assert not any(r["industry"].startswith("Flat")
                   for r in out.get("other_industries_that_moved", []))
    import json
    assert len(json.dumps(out)) < 8000, "the tool result must fit inside stream_agentic's cap"


# ── 2026-09-16: the web-search unit is released only when the search provably never ran ──

@pytest.mark.asyncio
async def test_a_search_that_never_ran_releases_its_unit(monkeypatch):
    """Before `CatalystNotAttempted`, every refusal came back as None and the unit was kept —
    the `except Exception → release` branch was unreachable."""
    from app.services.price_catalyst_service import CatalystNotAttempted
    released = []

    async def _release():
        released.append(1)
    calls = {"cache_only": 0, "fresh": 0}

    async def _get_catalyst(ticker, change_pct, window_label, cache_only=False, **kw):
        if cache_only:
            calls["cache_only"] += 1
            return None
        calls["fresh"] += 1
        raise CatalystNotAttempted("quota circuit open")

    monkeypatch.setattr(cmt, "_claim_web_search", AsyncMock(return_value=True))
    monkeypatch.setattr(cmt, "_release_web_search", _release)
    monkeypatch.setattr("app.services.price_catalyst_service.get_price_catalyst_service",
                        lambda: SimpleNamespace(get_catalyst=_get_catalyst))
    out = await cmt._maybe_web_catalyst("NAVN", _explanation(tier="Extreme", kind=CauseKind.NONE), "none")
    assert out is None
    assert calls == {"cache_only": 1, "fresh": 1}
    assert released == [1], "a refused search kept its unit"


@pytest.mark.asyncio
async def test_a_search_that_ran_and_found_nothing_keeps_its_unit(monkeypatch):
    """Google billed that search; a spend gate refunds only what provably was not spent."""
    released = []

    async def _release():
        released.append(1)

    async def _get_catalyst(ticker, change_pct, window_label, cache_only=False, **kw):
        return None
    monkeypatch.setattr(cmt, "_claim_web_search", AsyncMock(return_value=True))
    monkeypatch.setattr(cmt, "_release_web_search", _release)
    monkeypatch.setattr("app.services.price_catalyst_service.get_price_catalyst_service",
                        lambda: SimpleNamespace(get_catalyst=_get_catalyst))
    out = await cmt._maybe_web_catalyst("NAVN", _explanation(tier="Extreme", kind=CauseKind.NONE), "none")
    assert out is None and released == []


# ── 2026-09-16: the session word travels; crashes are counted; no paid search for Friday ──

@pytest.mark.asyncio
async def test_no_paid_search_for_a_prior_sessions_move(monkeypatch):
    """Pre-market Monday `attribute_ticker_move` labels the change "on Fri". A paid "today"
    search would cache under `X|today|…` and answer Monday's question with Friday's cause."""
    claimed = []
    monkeypatch.setattr(cmt, "_claim_web_search", AsyncMock(side_effect=lambda: claimed.append(1) or True))
    exp = _explanation(tier="Extreme", kind=CauseKind.NONE)
    exp.session_word = "on Fri"
    assert await cmt._maybe_web_catalyst("NAVN", exp, "none") is None
    assert claimed == []


@pytest.mark.asyncio
async def test_explain_price_move_carries_the_session_and_words_the_bottom_line_with_it(monkeypatch):
    exp = _explanation(tier="Typical", kind=CauseKind.NONE, change=-1.2)
    exp.session_word = "on Fri"
    exp.session_date = "2026-09-11"

    class _WM:
        async def attribute_ticker_move(self, sym):
            return exp
    monkeypatch.setattr("app.services.widget_movers_service.get_widget_movers_service", lambda: _WM())
    monkeypatch.setattr(cmt, "fetch_ticker_news", AsyncMock(return_value={"news_available": True, "articles": []}))
    monkeypatch.setattr(cmt, "_maybe_web_catalyst", AsyncMock(return_value=None))
    out = await cmt.explain_price_move("NAVN")
    assert out["session"] == "on Fri" and out["session_date"] == "2026-09-11"
    assert "on Fri" in out["bottom_line"] and "today" not in out["bottom_line"].split("—")[0]


@pytest.mark.asyncio
async def test_an_attribution_crash_is_an_error_result_not_an_unreadable_quote(monkeypatch):
    """The stream door counts a tool as failed only when its result carries `error`; a crash
    upstream used to come back in the same shape as a genuinely unreadable move, so the turn
    was charged while the identical outage on `get_market_snapshot` was refunded."""
    class _WM:
        async def attribute_ticker_move(self, sym):
            raise RuntimeError("supabase 520")
    monkeypatch.setattr("app.services.widget_movers_service.get_widget_movers_service", lambda: _WM())
    out = await cmt.explain_price_move("NAVN")
    assert out["move_readable"] is False and out["error"].startswith("RuntimeError")


@pytest.mark.asyncio
async def test_an_unreadable_move_without_a_crash_carries_no_error(monkeypatch):
    class _WM:
        async def attribute_ticker_move(self, sym):
            return None
    monkeypatch.setattr("app.services.widget_movers_service.get_widget_movers_service", lambda: _WM())
    out = await cmt.explain_price_move("NAVN")
    assert out["move_readable"] is False and "error" not in out


@pytest.mark.asyncio
async def test_a_news_feed_crash_is_an_error_result(monkeypatch):
    class _NC:
        async def get_ticker_news(self, *a, **k):
            raise RuntimeError("fmp 503")
    monkeypatch.setattr("app.services.news_cache_service.get_news_cache_service", lambda: _NC())
    out = await cmt.fetch_ticker_news("AAPL")
    assert out["news_available"] is False and out["error"].startswith("RuntimeError")


# ── Which session the snapshot describes (F7-4) ───────────────────────────────
#
# At 07:00 ET on a Monday the screener still reports Friday's close, so every sector,
# industry and mover percentage is FRIDAY's move. The universe stamps that on every row
# and the widget refuses to say "today" about it; this tool used to drop the stamp and
# the model said "Technology is up 0.8% today" about a session that ended three days
# earlier.


def _snapshot_movers(*, sector_date=None, industry_date=None, universe=None):
    class _Movers:
        async def get_sector_performance(self):
            row = {"sector": "Technology", "changesPercentage": 0.8, "constituents": 300}
            if sector_date:
                row["date"] = sector_date
            return [row]

        async def get_industry_performance(self):
            row = {"industry": "Semiconductors", "sector": "Technology",
                   "changesPercentage": 1.9}
            if industry_date:
                row["date"] = industry_date
            return [row]

        async def get_scanner_inputs(self):
            if universe is None:
                return ({}, {})
            change_map = {s: r["changePercentage"] for s, r in universe.items()}
            return (universe, change_map)
    return _Movers()


def _quality_row(symbol, change, session=None):
    row = {"symbol": symbol, "companyName": symbol, "price": 100.0, "marketCap": 5e10,
           "volume": 1e7, "averageVolume": 1e7, "changePercentage": change,
           "exchange": "NASDAQ", "isEtf": False, "isFund": False,
           "sector": "Technology", "industry": "Semiconductors"}
    if session:
        row["changeSession"] = session
    return row


def _stub_cards(monkeypatch):
    monkeypatch.setattr(
        "app.services.news_insight_service.get_news_insight_service",
        lambda: SimpleNamespace(get_cards=AsyncMock(return_value={})),
    )


@pytest.mark.asyncio
async def test_a_prior_session_snapshot_is_worded_on_fri_not_today(monkeypatch):
    from datetime import date
    universe = {"NVDA": _quality_row("NVDA", 4.2, "2026-09-11"),
                "INTC": _quality_row("INTC", -3.1, "2026-09-11")}
    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service",
        lambda: _snapshot_movers(sector_date="2026-09-11", industry_date="2026-09-11",
                                 universe=universe),
    )
    _stub_cards(monkeypatch)
    # Monday pre-market: the live session is the 14th, the numbers are Friday the 11th's.
    monkeypatch.setattr("app.utils.market_hours.session_trading_date",
                        lambda now=None: date(2026, 9, 14))
    out = await cmt.fetch_market_snapshot()
    assert out["as_of_session"] == {
        "date": "2026-09-11", "word": "on Fri",
        "note": out["as_of_session"]["note"],
    }
    assert "today" not in out["as_of_session"]["word"]
    assert '"today"' in out["as_of_session"]["note"], "the note must forbid the word"
    assert out["sectors"][0]["session_date"] == "2026-09-11"
    assert out["leading_industries"][0]["session_date"] == "2026-09-11"
    assert out["top_gainers"][0]["session_date"] == "2026-09-11"
    assert out["top_losers"][0]["session_date"] == "2026-09-11"


@pytest.mark.asyncio
async def test_a_current_session_snapshot_says_today(monkeypatch):
    from datetime import date
    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service",
        lambda: _snapshot_movers(sector_date="2026-09-14", industry_date="2026-09-14"),
    )
    _stub_cards(monkeypatch)
    monkeypatch.setattr("app.utils.market_hours.session_trading_date",
                        lambda now=None: date(2026, 9, 14))
    monkeypatch.setattr("app.services.widget_movers_service._et_calendar_day",
                        lambda: date(2026, 9, 14))
    out = await cmt.fetch_market_snapshot()
    assert out["as_of_session"]["word"] == "today"
    assert out["as_of_session"]["date"] == "2026-09-14"


@pytest.mark.asyncio
async def test_a_saturday_snapshot_of_fridays_session_is_worded_on_fri(monkeypatch):
    from datetime import date
    """On a weekend the LIVE session is Friday and every stamp is Friday — but it is not
    today. The model said "Technology is up 0.8% today" all weekend."""
    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service",
        lambda: _snapshot_movers(sector_date="2026-09-11", industry_date="2026-09-11"),
    )
    _stub_cards(monkeypatch)
    monkeypatch.setattr("app.utils.market_hours.session_trading_date",
                        lambda now=None: date(2026, 9, 11))          # Friday
    monkeypatch.setattr("app.services.widget_movers_service._et_calendar_day",
                        lambda: date(2026, 9, 12))                   # Saturday
    out = await cmt.fetch_market_snapshot()
    assert out["as_of_session"] == {"date": "2026-09-11", "word": "on Fri",
                                    "note": out["as_of_session"]["note"]}


@pytest.mark.asyncio
async def test_an_unstamped_snapshot_carries_no_session_claim(monkeypatch):
    """Older-shape rows (no `date`, no `changeSession`) must not INVENT a session: the
    key is absent, and no row carries a `session_date`."""
    universe = {"NVDA": _quality_row("NVDA", 4.2)}
    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service",
        lambda: _snapshot_movers(universe=universe),
    )
    _stub_cards(monkeypatch)
    out = await cmt.fetch_market_snapshot()
    assert "as_of_session" not in out
    assert "session_date" not in out["sectors"][0]
    assert "session_date" not in out["top_gainers"][0]


@pytest.mark.asyncio
async def test_the_snapshot_session_is_the_mode_not_the_newest_stamp(monkeypatch):
    """One early premarket print that has ticked into Monday must not relabel Friday's
    whole snapshot as today's — and the odd row keeps its own, differing, date."""
    from datetime import date
    universe = {"NVDA": _quality_row("NVDA", 4.2, "2026-09-11"),
                "AMD": _quality_row("AMD", 3.0, "2026-09-11"),
                "EARLY": _quality_row("EARLY", 2.0, "2026-09-14")}
    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service",
        lambda: _snapshot_movers(sector_date="2026-09-11", industry_date="2026-09-11",
                                 universe=universe),
    )
    _stub_cards(monkeypatch)
    monkeypatch.setattr("app.utils.market_hours.session_trading_date",
                        lambda now=None: date(2026, 9, 14))
    out = await cmt.fetch_market_snapshot()
    assert out["as_of_session"]["date"] == "2026-09-11"
    assert out["as_of_session"]["word"] == "on Fri"
    by_sym = {r["symbol"]: r for r in out["top_gainers"]}
    assert by_sym["EARLY"]["session_date"] == "2026-09-14"
    assert by_sym["NVDA"]["session_date"] == "2026-09-11"


@pytest.mark.asyncio
async def test_a_garbage_session_stamp_never_crashes_the_snapshot(monkeypatch):
    monkeypatch.setattr(
        "app.services.market_movers_service.get_market_movers_service",
        lambda: _snapshot_movers(sector_date="not-a-date", industry_date="not-a-date"),
    )
    _stub_cards(monkeypatch)
    out = await cmt.fetch_market_snapshot()
    assert "as_of_session" not in out
    assert out["sectors"][0]["change_percent"] == 0.8


def test_as_of_session_words_a_prior_session_by_weekday(monkeypatch):
    from collections import Counter
    from datetime import date
    monkeypatch.setattr("app.utils.market_hours.session_trading_date",
                        lambda now=None: date(2026, 9, 16))
    monkeypatch.setattr("app.services.widget_movers_service._et_calendar_day",
                        lambda: date(2026, 9, 16))
    assert cmt._as_of_session(Counter({"2026-09-15": 3}))["word"] == "on Tue"
    assert cmt._as_of_session(Counter({"2026-09-16": 3}))["word"] == "today"
    # A stamp AHEAD of the live session (clock skew, a stale `session_trading_date`
    # patch) is still "today", never a future weekday.
    assert cmt._as_of_session(Counter({"2026-09-17": 3}))["word"] == "today"
    assert cmt._as_of_session(Counter()) is None


# ── A quote-source OUTAGE is an upstream failure; an unquotable symbol is not (2026-09-17) ──
#
# `price_service.get_quotes` folds a universe failure into `{}`, so `attribute_ticker_move`
# saw the same `ranked == []` for "FMP is down" and "this symbol has no usable row" — and
# `explain_price_move` answered both as `move_readable: False` with no `error`, so an
# outage on the only tool was charged in full while the identical outage on
# `get_ticker_news` was refunded. The discriminator is the index band that rides on every
# batch: no SPY/QQQ/DIA row at all means the SOURCE failed.


def _movers_with_quotes(monkeypatch, quotes_result):
    from app.services import widget_movers_service as wm
    svc = wm.WidgetMoversService()
    if isinstance(quotes_result, BaseException):
        async def _quotes(self, symbols):
            raise quotes_result
    else:
        async def _quotes(self, symbols):
            return dict(quotes_result)
    monkeypatch.setattr(wm.WidgetMoversService, "_quotes", _quotes)
    monkeypatch.setattr("app.services.volatility_cache_service.get_volatility_cache_service",
                        lambda: SimpleNamespace(get_sigmas_bulk=AsyncMock(return_value={})))
    monkeypatch.setattr("app.services.news_insight_service.get_news_insight_service",
                        lambda: SimpleNamespace(get_cards=AsyncMock(return_value={})))
    monkeypatch.setattr(cmt, "_maybe_web_catalyst", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.widget_movers_service.get_widget_movers_service", lambda: svc)
    return svc


@pytest.mark.asyncio
async def test_a_quote_source_that_raises_is_an_upstream_error(monkeypatch):
    _movers_with_quotes(monkeypatch, RuntimeError("fmp 503"))
    out = await cmt.explain_price_move("NVDA")
    assert out["move_readable"] is False
    assert out["upstream"] is True and "QuoteSourceUnavailable" in out["error"]


@pytest.mark.asyncio
async def test_an_empty_batch_with_no_index_band_is_an_outage(monkeypatch):
    """`get_quotes` swallowed the failure into `{}`: the band is the tell."""
    _movers_with_quotes(monkeypatch, {})
    out = await cmt.explain_price_move("NVDA")
    assert out["move_readable"] is False
    assert out.get("upstream") is True and "index band empty" in out["error"]


@pytest.mark.asyncio
async def test_a_symbol_missing_from_a_healthy_batch_is_unreadable_but_not_an_error(monkeypatch):
    """The band came back, the symbol did not: the model's miss (or a delisted name) —
    answered as unreadable and CHARGED, exactly as before."""
    from app.services.widget_movers_service import _INDEX_SYMBOLS
    band = {s: {"symbol": s, "price": 500.0, "changePercentage": 0.4, "changeSession": "2026-09-16"}
            for s, _ in _INDEX_SYMBOLS}
    _movers_with_quotes(monkeypatch, band)
    out = await cmt.explain_price_move("ZZZZ")
    assert out["move_readable"] is False
    assert "error" not in out and "upstream" not in out
