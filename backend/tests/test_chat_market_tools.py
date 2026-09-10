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
