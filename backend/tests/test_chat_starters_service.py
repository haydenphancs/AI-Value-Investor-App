"""Behaviour tests for `app/services/chat_starters_service`.

Hermetic: every upstream is stubbed, nothing touches Supabase, FMP, ApeWisdom or the
clock. Note WHERE the stubs are applied — this module imports `get_market_movers_service`,
`get_all_mentions` and `trading_date_et` INSIDE the functions that use them, so each name
resolves from its source module on every call and must be patched there. `get_supabase` is
a module-level import and is patched on this module instead. (`tests/test_patch_targets_exist.py`
fails the build if any of these names stops existing.)
"""

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from app.schemas.chat_starters import ChatStartersResponse
from app.services import chat_starters_service as mod
from app.services.chat_starters_service import ChatStartersService

DAY = "2026-09-10"


@pytest.fixture(autouse=True)
def _reset_caches():
    """Class-level caches are shared process-wide; a leak between tests is a false pass."""
    ChatStartersService._pool_cache = None
    ChatStartersService._pool_inflight = None
    ChatStartersService._response_cache = None
    ChatStartersService._response_inflight = None
    yield
    ChatStartersService._pool_cache = None
    ChatStartersService._pool_inflight = None
    ChatStartersService._response_cache = None
    ChatStartersService._response_inflight = None


def _profile(symbol: str, name: str, **over: Any) -> Dict[str, Any]:
    """A profile that CLEARS the quality gate unless a field is overridden."""
    base = {
        "symbol": symbol,
        "companyName": name,
        "marketCap": 50_000_000_000.0,
        "averageVolume": 20_000_000.0,
        "price": 100.0,
        "isEtf": False,
        "isFund": False,
        "sector": "Technology",
        "industry": "Semiconductors",
        "exchange": "NASDAQ",
    }
    base.update(over)
    return base


class _Movers:
    def __init__(self, universe, changes, sectors=None):
        self._u, self._c, self._s = universe, changes, sectors or []

    async def get_scanner_inputs(self):
        return self._u, self._c

    async def get_sector_performance(self):
        return self._s


def _install(monkeypatch, *, universe=None, changes=None, sectors=None,
             mentions=None, themes=None, pool_rows=None, day=DAY):
    """Wire every source. Anything omitted degrades to empty, not to an exception."""
    import app.integrations.apewisdom as apewisdom
    import app.services.market_movers_service as movers_mod
    import app.services.push_dispatch_service as push_mod

    movers = _Movers(universe or {}, changes or {}, sectors)
    monkeypatch.setattr(movers_mod, "get_market_movers_service", lambda: movers)

    async def _mentions():
        return mentions or {}

    monkeypatch.setattr(apewisdom, "get_all_mentions", _mentions)
    monkeypatch.setattr(push_mod, "trading_date_et", lambda: day)

    rows = pool_rows if pool_rows is not None else []
    theme_rows = themes or []

    class _Q:
        def __init__(self, table):
            self.table = table

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def execute(self):
            class R:
                pass

            r = R()
            r.data = theme_rows if self.table == "trending_themes" else rows
            return r

    class _SB:
        def table(self, name):
            return _Q(name)

    monkeypatch.setattr(mod, "get_supabase", lambda: _SB())
    return movers


def _texts(resp: ChatStartersResponse) -> List[str]:
    return [c.text for c in resp.global_starters]


# ── the shape of a healthy response ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_healthy_build_fills_every_slot(monkeypatch):
    _install(
        monkeypatch,
        universe={"NVDA": _profile("NVDA", "NVIDIA"), "KO": _profile("KO", "Coca-Cola")},
        changes={"NVDA": 6.4, "KO": -4.1},
        sectors=[{"sector": "Energy", "changesPercentage": 2.9},
                 {"sector": "Utilities", "changesPercentage": 0.1}],
        themes=[{"category": "AI & Semiconductors", "tickers": ["NVDA"]}],
    )
    resp = await mod.ChatStartersService().get_starters()

    assert resp.trading_date == DAY
    assert len(resp.global_starters) == mod._GLOBAL_SLOTS
    kinds = {c.kind for c in resp.global_starters}
    assert {"hot_ticker", "hot_sector", "hot_topic", "fixed"} <= kinds
    assert "What tickers are hot today?" in _texts(resp)
    assert "What topics are hot today?" in _texts(resp)
    assert any("NVDA" in t and "up" in t for t in _texts(resp))
    assert any("KO" in t and "down" in t for t in _texts(resp))
    for scope in ("ticker", "etf", "crypto", "commodity", "index"):
        picks = getattr(resp.detail_starters, scope)
        assert len(picks) == mod._DETAIL_SLOTS
        assert all("{symbol}" in p for p in picks), scope


# ── THE collision test ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_coin_ticker_that_is_also_a_real_company_never_becomes_a_trending_chip(
    monkeypatch,
):
    """ADA is Cardano on Reddit and Adams Resources & Energy on the NYSE.

    This is why the trending gate is not "is the symbol real". Membership in the movers
    universe PASSES for exactly this collision — that is the whole trap — so the chip
    would attach "everyone is talking about it" to an unrelated oil-logistics company
    whose shareholders are discussing nothing. The move test is what discriminates: the
    equity is flat, so no chip.
    """
    _install(
        monkeypatch,
        universe={"ADA": _profile("ADA", "Adams Resources & Energy",
                                  marketCap=1_200_000_000.0, sector="Energy")},
        changes={"ADA": 0.2},                    # the EQUITY is flat; the coin is not
        mentions={"ADA": {"rank": 1, "mentions": 9000}},
    )
    resp = await mod.ChatStartersService().get_starters()

    assert not any(c.kind == "trending" for c in resp.global_starters)
    assert not any("ADA" in t for t in _texts(resp))


@pytest.mark.asyncio
async def test_a_genuinely_moving_buzz_name_does_become_a_trending_chip(monkeypatch):
    """The control for the test above — without it, that one passes vacuously.

    If the trending slot were simply dead code, the ADA assertion would still be green.
    """
    _install(
        monkeypatch,
        universe={"GME": _profile("GME", "GameStop", sector="Consumer Cyclical")},
        changes={"GME": 14.0},
        mentions={"GME": {"rank": 1, "mentions": 9000}},
    )
    resp = await mod.ChatStartersService().get_starters()
    assert any(c.kind == "trending" and c.symbol == "GME" for c in resp.global_starters)


@pytest.mark.asyncio
async def test_a_buzz_name_that_fails_the_quality_gate_is_rejected(monkeypatch):
    """A moving penny stock is still not a company we will name in a chip."""
    _install(
        monkeypatch,
        universe={"PUMP": _profile("PUMP", "Pump Co", marketCap=4_000_000.0,
                                   averageVolume=1_000.0)},
        changes={"PUMP": 90.0},
        mentions={"PUMP": {"rank": 1, "mentions": 9000}},
    )
    resp = await mod.ChatStartersService().get_starters()
    assert not any("PUMP" in t for t in _texts(resp))


# ── honesty gates ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_barely_moving_ticker_is_not_called_hot(monkeypatch):
    _install(
        monkeypatch,
        universe={"KO": _profile("KO", "Coca-Cola")},
        changes={"KO": 0.4},
    )
    resp = await mod.ChatStartersService().get_starters()
    assert not any(c.kind == "hot_ticker" for c in resp.global_starters)


@pytest.mark.asyncio
async def test_a_flat_sector_does_not_get_a_leading_question(monkeypatch):
    _install(monkeypatch, sectors=[{"sector": "Utilities", "changesPercentage": 0.2}])
    resp = await mod.ChatStartersService().get_starters()
    assert not any(c.kind == "hot_sector" for c in resp.global_starters)


@pytest.mark.asyncio
async def test_a_flat_theme_basket_does_not_claim_something_is_driving_it(monkeypatch):
    """Migration 081's categories are static strings; only a live move earns "today"."""
    _install(
        monkeypatch,
        universe={"NVDA": _profile("NVDA", "NVIDIA")},
        changes={"NVDA": 0.1},
        themes=[{"category": "Rare Earth Mining", "tickers": ["NVDA"]}],
    )
    resp = await mod.ChatStartersService().get_starters()
    assert not any(c.kind == "hot_topic" for c in resp.global_starters)
    assert not any("Rare Earth Mining" in t for t in _texts(resp))


# ── degradation ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_live_source_failing_still_returns_a_full_row(monkeypatch):
    import app.integrations.apewisdom as apewisdom
    import app.services.market_movers_service as movers_mod
    import app.services.push_dispatch_service as push_mod

    class _Broken:
        async def get_scanner_inputs(self):
            raise RuntimeError("screener down")

        async def get_sector_performance(self):
            raise RuntimeError("screener down")

    async def _boom():
        raise RuntimeError("apewisdom down")

    def _no_db():
        raise RuntimeError("supabase down")

    monkeypatch.setattr(movers_mod, "get_market_movers_service", lambda: _Broken())
    monkeypatch.setattr(apewisdom, "get_all_mentions", _boom)
    monkeypatch.setattr(push_mod, "trading_date_et", lambda: DAY)
    monkeypatch.setattr(mod, "get_supabase", _no_db)

    resp = await mod.ChatStartersService().get_starters()
    assert len(resp.global_starters) == mod._GLOBAL_SLOTS
    assert "What tickers are hot today?" in _texts(resp)
    assert all(getattr(resp.detail_starters, s) for s in
               ("ticker", "etf", "crypto", "commodity", "index"))


@pytest.mark.asyncio
async def test_an_empty_pool_read_never_replaces_a_good_pool(monkeypatch):
    """The reseed window: rows briefly vanish, and every client must not fall back."""
    _install(monkeypatch, pool_rows=[{"text": "Real DB question?", "scope": "global"}])
    svc = mod.ChatStartersService()
    first = await svc._get_pools()
    assert "Real DB question?" in first["global"]

    ChatStartersService._pool_cache = (0.0, first)     # force expiry, keep the value
    _install(monkeypatch, pool_rows=[])                 # table momentarily empty
    second = await svc._get_pools()
    assert second["global"] == first["global"]


@pytest.mark.asyncio
async def test_a_stale_response_is_not_served_across_an_et_day_rollover(monkeypatch):
    """Yesterday's top gainer must not keep being announced as "hot today".

    Serving stale on failure is right WITHIN a day and wrong across one: the claim in
    the text is time-bound, so past midnight the body drops to evergreen-only rather
    than repeating a statement that is now false.
    """
    _install(
        monkeypatch,
        universe={"NVDA": _profile("NVDA", "NVIDIA")},
        changes={"NVDA": 9.0},
        day="2026-09-10",
    )
    warm = await mod.ChatStartersService().get_starters()
    assert any("NVDA" in t for t in _texts(warm))

    import app.services.market_movers_service as movers_mod
    import app.services.push_dispatch_service as push_mod

    class _Broken:
        async def get_scanner_inputs(self):
            raise RuntimeError("down")

        async def get_sector_performance(self):
            raise RuntimeError("down")

    monkeypatch.setattr(movers_mod, "get_market_movers_service", lambda: _Broken())
    monkeypatch.setattr(push_mod, "trading_date_et", lambda: "2026-09-11")

    fresh = await mod.ChatStartersService().get_starters()
    assert fresh.trading_date == "2026-09-11"
    assert not any("NVDA" in t for t in _texts(fresh))
    assert len(fresh.global_starters) == mod._GLOBAL_SLOTS


# ── determinism, dedupe, concurrency ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_same_day_composes_the_same_evergreen_questions(monkeypatch):
    _install(monkeypatch)
    first = await mod.ChatStartersService().get_starters()
    ChatStartersService._response_cache = None
    second = await mod.ChatStartersService().get_starters()
    assert _texts(first) == _texts(second)


@pytest.mark.asyncio
async def test_consecutive_days_do_not_repeat_the_evergreen_questions(monkeypatch):
    _install(monkeypatch, day="2026-09-10")
    first = set(_texts(await mod.ChatStartersService().get_starters()))
    ChatStartersService._response_cache = None
    _install(monkeypatch, day="2026-09-11")
    second = set(_texts(await mod.ChatStartersService().get_starters()))
    evergreen_first = first - {"What tickers are hot today?", "What topics are hot today?"}
    assert not (evergreen_first & second)


@pytest.mark.asyncio
async def test_a_duplicate_between_a_live_slot_and_the_pool_is_collapsed(monkeypatch):
    """iOS keys the chip row by string, so a duplicate silently shortens the row."""
    _install(
        monkeypatch,
        pool_rows=[{"text": "What tickers are hot today?", "scope": "global"}]
        + [{"text": f"Filler question {i}?", "scope": "global"} for i in range(30)],
    )
    resp = await mod.ChatStartersService().get_starters()
    texts = _texts(resp)
    assert len(texts) == len(set(texts))
    assert len(texts) == mod._GLOBAL_SLOTS


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_build(monkeypatch):
    calls = {"n": 0}
    _install(monkeypatch)
    svc = mod.ChatStartersService()
    original = svc._collect_sources

    async def counting():
        calls["n"] += 1
        await asyncio.sleep(0.02)
        return await original()

    monkeypatch.setattr(svc, "_collect_sources", counting)
    results = await asyncio.gather(*(svc.get_starters() for _ in range(8)))
    assert calls["n"] == 1
    assert all(_texts(r) == _texts(results[0]) for r in results)


@pytest.mark.asyncio
async def test_a_waiter_giving_up_does_not_poison_the_leader(monkeypatch):
    """The shield. A joined caller's cancellation must not kill the shared build."""
    _install(monkeypatch)
    svc = mod.ChatStartersService()
    original = svc._collect_sources

    async def slow():
        await asyncio.sleep(0.05)
        return await original()

    monkeypatch.setattr(svc, "_collect_sources", slow)
    leader = asyncio.create_task(svc.get_starters())
    await asyncio.sleep(0.01)
    waiter = asyncio.create_task(svc.get_starters())
    await asyncio.sleep(0.005)
    waiter.cancel()
    resp = await leader
    assert len(resp.global_starters) == mod._GLOBAL_SLOTS


@pytest.mark.asyncio
async def test_detail_scopes_do_not_all_rotate_in_lockstep(monkeypatch):
    """Without the per-scope salt every bar sits at the same phase of the walk."""
    _install(monkeypatch)
    resp = await mod.ChatStartersService().get_starters()
    shapes = {
        scope: tuple(getattr(resp.detail_starters, scope))
        for scope in ("ticker", "etf", "crypto", "commodity", "index")
    }
    assert len(set(shapes.values())) == len(shapes)
