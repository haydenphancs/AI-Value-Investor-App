"""Unit tests for the web-search-grounded price-catalyst service.

No live Gemini / Supabase: a fake Gemini client is injected, and Supabase I/O
is monkeypatched to fail gracefully (the service degrades to its in-memory
tier, which we exercise directly — per the testing rules, don't mock the cache,
test it).
"""
from __future__ import annotations

import pytest

from app.services import price_catalyst_service as pcs


def _fence(tag: str, reason: str) -> str:
    return (
        "Here is the analysis:\n```json\n"
        f'{{"catalyst_tag": "{tag}", "reason": "{reason}"}}\n```'
    )


class _FakeGemini:
    """Returns a scripted grounded response; counts calls; can simulate 503s."""

    def __init__(self, text="", sources=None, raise_times=0):
        self.text = text
        self.sources = sources if sources is not None else []
        self.calls = 0
        self._raise_times = raise_times

    async def generate_grounded_research(
        self, prompt, model_name=None, max_output_tokens=8192,
    ):
        self.calls += 1
        if self._raise_times > 0:
            self._raise_times -= 1
            raise RuntimeError("503 Service Unavailable")
        return {
            "text": self.text,
            "grounding_sources": self.sources,
            "search_queries": ["why did the stock move"],
            "tokens_used": 123,
            "model": model_name or "gemini-2.5-flash",
        }


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # No real Supabase — force every DB call down the graceful except path.
    def _no_db():
        raise RuntimeError("no supabase in tests")

    monkeypatch.setattr(pcs, "get_supabase", _no_db)
    monkeypatch.setattr(pcs, "_RETRY_BASE_SECONDS", 0)  # no real backoff sleeps
    pcs._mem_cache.clear()
    pcs._inflight.clear()
    yield
    pcs._mem_cache.clear()
    pcs._inflight.clear()


def _svc(fake: _FakeGemini) -> pcs.PriceCatalystService:
    s = pcs.PriceCatalystService()
    s._gemini = fake  # inject fake, bypass get_gemini_client()
    return s


@pytest.mark.asyncio
async def test_applied_returns_tag_and_sources():
    fake = _FakeGemini(
        text=_fence("Q1 Earnings Beat", "Beat Q1 estimates and raised guidance."),
        sources=[{"title": "Reuters", "uri": "http://r", "publisher": "reuters.com"}],
    )
    res = await _svc(fake).get_catalyst("AAPL", 12.4, "Since May 1")
    assert res is not None
    assert res["tag"] == "Q1 Earnings Beat"
    assert res["sources"] and res["sources"][0]["publisher"] == "reuters.com"


@pytest.mark.asyncio
async def test_no_sources_demotes_to_no_catalyst():
    # Hallucination guard: a specific tag with NO grounding sources is not trusted.
    fake = _FakeGemini(text=_fence("Acquisition", "Rumored buyout."), sources=[])
    res = await _svc(fake).get_catalyst("AAPL", 9.0, "Since May 1")
    assert res is not None
    assert res["tag"] is None        # demoted — never fabricate an uncited catalyst
    assert res["sources"] == []


@pytest.mark.asyncio
async def test_no_clear_catalyst_label():
    fake = _FakeGemini(
        text=_fence("No Clear Catalyst", "Broad sector selloff; no company news."),
        sources=[{"title": "X", "uri": "http://x", "publisher": "x.com"}],
    )
    res = await _svc(fake).get_catalyst("AAPL", -8.0, "Last 7 Days")
    assert res is not None and res["tag"] is None


@pytest.mark.asyncio
async def test_unparseable_returns_none():
    # No ```json fence → gemini_error → None → caller keeps the FMP fallback.
    fake = _FakeGemini(text="I could not determine the reason.", sources=[])
    res = await _svc(fake).get_catalyst("AAPL", 11.0, "Since May 1")
    assert res is None


@pytest.mark.asyncio
async def test_mem_cache_dedups_repeat_calls():
    fake = _FakeGemini(
        text=_fence("FDA Approval", "FDA approved the lead drug."),
        sources=[{"title": "FDA", "uri": "http://f", "publisher": "fda.gov"}],
    )
    svc = _svc(fake)
    first = await svc.get_catalyst("NVDA", 20.0, "Since May 1")
    second = await svc.get_catalyst("NVDA", 20.0, "Since May 1")
    assert first == second
    assert fake.calls == 1  # second served from the in-memory tier


@pytest.mark.asyncio
async def test_model_fallback_on_503():
    # First model 503s through all its retries; the next model in the chain succeeds.
    fake = _FakeGemini(
        text=_fence("Raised Guidance", "Raised full-year guidance."),
        sources=[{"title": "BBG", "uri": "http://b", "publisher": "bloomberg.com"}],
        raise_times=pcs._RETRIES_PER_MODEL,
    )
    res = await _svc(fake).get_catalyst("MSFT", 7.0, "Since May 1")
    assert res is not None and res["tag"] == "Raised Guidance"
    assert fake.calls == pcs._RETRIES_PER_MODEL + 1  # N fails, then success


# ── B2: cache identity is (ticker, window, direction), NOT ticker alone ──────

def test_ctx_matches_requires_window_and_direction():
    m = pcs._ctx_matches
    assert m("today", -7.0, "today", -5.0) is True    # same window + same (down) dir
    assert m("30d", 22.0, "30d", 3.0) is True          # same window + same (up) dir
    assert m("today", 22.0, "today", -7.0) is False    # OPPOSITE direction → no match
    assert m("30d", -7.0, "today", -7.0) is False      # different window → no match
    assert m("Today", -1.0, "today", -2.0) is True     # window is case/space-insensitive
    # Legacy row (pre-migration NULLs) → no match → regenerate (immediate correctness).
    assert m(None, None, "today", -7.0) is False
    assert m("today", None, "today", -7.0) is False
    assert m("today", "not-a-number", "today", -7.0) is False   # defensive


def test_ctx_key_separates_window_and_direction():
    k = pcs._ctx_key
    assert k("TSLA", "today", -7.0) != k("TSLA", "today", 7.0)   # direction splits
    assert k("TSLA", "today", -7.0) != k("TSLA", "30d", -7.0)    # window splits
    assert k("TSLA", "TODAY", -7.0) == k("TSLA", "today", -3.0)  # same bucket (down)


@pytest.mark.asyncio
async def test_opposite_direction_move_does_not_reuse_cached_reason():
    # The core bug: a +22% rally reason cached for TSLA must NOT be served for a
    # -7% drop. With ticker-only keying the second call returned the rally reason;
    # with (window,direction) keying it misses the mem tier and re-grounds.
    fake = _FakeGemini(
        text=_fence("Rally", "Multi-day rally on a delivery beat."),
        sources=[{"title": "R", "uri": "http://r", "publisher": "reuters.com"}],
    )
    svc = _svc(fake)
    up = await svc.get_catalyst("TSLA", 22.0, "30d")
    assert up is not None and fake.calls == 1
    down = await svc.get_catalyst("TSLA", -7.0, "today")
    assert fake.calls == 2   # a fresh grounded call, NOT the cached rally reason


@pytest.mark.asyncio
async def test_same_context_still_served_from_mem_without_a_second_call():
    # Same window + same direction is the SAME move context → cache hit (no churn).
    fake = _FakeGemini(
        text=_fence("Beat", "Earnings beat."),
        sources=[{"title": "R", "uri": "http://r", "publisher": "reuters.com"}],
    )
    svc = _svc(fake)
    a = await svc.get_catalyst("AAPL", 8.0, "today")
    b = await svc.get_catalyst("AAPL", 5.0, "today")   # same ctx bucket (today, up)
    assert fake.calls == 1     # second served from mem
    assert a == b


# ── 2026-09-16: a refused search is "not attempted", and the ladder's cost is pinned ──

class _QuotaGemini(_FakeGemini):
    async def generate_grounded_research(self, prompt, model_name=None, max_output_tokens=8192):
        from app.integrations.gemini import GeminiQuotaError
        self.calls += 1
        raise GeminiQuotaError("quota circuit open (resource_exhausted)")


@pytest.mark.asyncio
async def test_a_quota_refusal_raises_not_attempted_and_stops_the_chain():
    """The breaker's fail-fast used to be swallowed per attempt, the whole model ladder
    slept through, and `get_catalyst` returned None — indistinguishable from "searched,
    nothing found", so the caller kept the paid web-search unit it had claimed."""
    fake = _QuotaGemini()
    with pytest.raises(pcs.CatalystNotAttempted):
        await _svc(fake).get_catalyst("AAPL", 9.0, "today")
    assert fake.calls == 1, "the quota is shared across the chain — no second model, no retries"
    assert pcs._inflight == {}


@pytest.mark.asyncio
async def test_a_joiner_of_a_refused_search_gets_the_same_signal():
    import asyncio as _aio
    fake = _QuotaGemini()
    svc = _svc(fake)
    results = await _aio.gather(
        svc.get_catalyst("AAPL", 9.0, "today"), svc.get_catalyst("AAPL", 9.0, "today"),
        return_exceptions=True,
    )
    assert all(isinstance(r, pcs.CatalystNotAttempted) for r in results), results


@pytest.mark.asyncio
async def test_the_kill_switch_is_not_attempted_either(monkeypatch):
    monkeypatch.setattr(pcs.settings, "PRICE_CATALYST_AI_ENABLED", False)
    fake = _FakeGemini(text=_fence("x", "y"))
    with pytest.raises(pcs.CatalystNotAttempted):
        await _svc(fake).get_catalyst("AAPL", 9.0, "today")
    assert fake.calls == 0


@pytest.mark.asyncio
async def test_a_503_storm_still_walks_the_ladder_with_real_backoff(monkeypatch):
    """Pins the ladder's COST (F7-9): N retries per model with exponential sleeps, and the
    exhausted case is a plain None (searched, nothing usable — the unit stays spent)."""
    sleeps = []

    async def _sleep(secs):
        sleeps.append(secs)
    monkeypatch.setattr(pcs.asyncio, "sleep", _sleep)
    monkeypatch.setattr(pcs, "_RETRY_BASE_SECONDS", 1.0)
    fake = _FakeGemini(raise_times=10_000)
    res = await _svc(fake).get_catalyst("MSFT", 7.0, "Since May 1")
    assert res is None
    assert fake.calls == len(pcs._MODEL_CHAIN) * pcs._RETRIES_PER_MODEL
    per_model = [1.0 * (2 ** a) for a in range(pcs._RETRIES_PER_MODEL - 1)]
    assert sleeps == per_model * len(pcs._MODEL_CHAIN)


# ── 2026-09-16: a "today" reason is specific to ONE session ──────────────────────

def test_a_today_key_carries_the_et_trading_date(monkeypatch):
    monkeypatch.setattr(pcs, "_et_today_iso", lambda: "2026-09-14")
    assert pcs._ctx_key("AAPL", "today", -3.0) == "AAPL|today|2026-09-14|d"
    monkeypatch.setattr(pcs, "_et_today_iso", lambda: "2026-09-15")
    assert pcs._ctx_key("AAPL", "today", -3.0) == "AAPL|today|2026-09-15|d"
    # Multi-day windows are unchanged.
    assert pcs._ctx_key("AAPL", "Since May 1", 3.0) == "AAPL|since may 1|u"


def test_a_today_row_never_expires_past_the_et_day():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    now = datetime(2026, 9, 14, 20, 30, tzinfo=timezone.utc)      # 16:30 ET Monday
    exp = pcs._today_window_expiry(now)
    assert exp.astimezone(ZoneInfo("America/New_York")).date().isoformat() == "2026-09-14"
    assert exp > now
    # …and the write uses the tighter of TTL vs end-of-day for "today" only.
    captured = {}

    class _T:
        def upsert(self, row): captured.update(row); return self
        def execute(self): return None

    class _SB:
        def table(self, name): return _T()
    import unittest.mock as um
    with um.patch.object(pcs, "get_supabase", lambda: _SB()), \
         um.patch.object(pcs, "datetime", um.MagicMock(now=lambda tz=None: now, fromisoformat=datetime.fromisoformat)):
        pcs.PriceCatalystService()._write_cache("AAPL", {"tag": "x", "reason": "y"}, "m", "today", -3.0)
    assert captured["expires_at"] == exp.isoformat()
