"""The AI prose on the index and ETF detail screens must never be on the critical path.

Why this file exists, in numbers measured against production on 2026-08-26:

    ^GSPC  cold 5.63s | warm 0.14s | rebuild with the caches warm **0.36s**
    ^DJI   cold 11.42s        SCHD cold 5.89s
    SOL    cold 1.27s         SIUSD cold 1.34s

Crypto and commodity were already fast for one reason each: crypto fires its Gemini
snapshot generation into the background, and commodity has no Gemini at all. Index and
ETF `await`ed theirs inline — so ~5.3 of ^GSPC's 5.63 seconds was a user staring at a
shimmer while the server generated prose it ALREADY had a deterministic version of.

Three properties, and each has its own way of silently regressing:

1. **Nothing awaits Gemini on the request path.** A refactor that "simplifies" the
   background hand-off back into an await restores the whole bug with no other symptom.
2. **The text persists to Tier-2.** It used to live in the process dict alone, so every
   Railway redeploy and every hour made the next viewer pay for it again.
3. **One refresh per symbol.** The detail-level `_inflight` is keyed on the RANGE, so
   viewers on different range pills of one cold symbol would each spawn a Gemini call.

⚠️ Every fake below has a real suspension point (`await asyncio.sleep(0)`). Without one,
the first coroutine in an `asyncio.gather` runs to completion before the second starts and
every concurrency assertion here is vacuous — a trap this repo has already been caught by.
"""

import asyncio

import pytest

from app.schemas.index import MacroForecastItemResponse, SectorPerformanceEntryResponse


# ── Fakes ────────────────────────────────────────────────────────────


class _RecordingGemini:
    """Answers with a well-formed two-part story and records every call."""

    def __init__(self, text=None, delay=0.0):
        self.calls: list[dict] = []
        self._delay = delay
        # THREE parts, because with no cached macro the prompt asks for three. A
        # two-part answer is (correctly) rejected by the parser, which is how the first
        # draft of this file silently proved nothing.
        self._text = text if text is not None else (
            "The market trades at {PE_RATIO} against a {HISTORICAL_PERIOD} average of "
            "{HISTORICAL_AVG_PE}, which reads {VALUATION_LABEL}. Forward {FORWARD_PE}."
            "\n---\n"
            "{TOP_SECTOR} led at {TOP_SECTOR_CHANGE} while {BOTTOM_SECTOR} lagged at "
            "{BOTTOM_SECTOR_CHANGE}; {ADVANCING_COUNT} advanced and {DECLINING_COUNT} fell."
            "\n---\n"
            '[{"title": "GDP Growth", "description": "Steady expansion.", '
            '"signal": "positive"}, '
            '{"title": "Inflation", "description": "Cooling slowly.", "signal": "neutral"}, '
            '{"title": "Labor", "description": "Resilient.", "signal": "positive"}]'
        )

    async def generate_text(self, **kwargs):
        self.calls.append(kwargs)
        if self._delay:
            await asyncio.sleep(self._delay)
        else:
            await asyncio.sleep(0)
        return {"text": self._text}


class _ExplodingGemini:
    def __init__(self):
        self.calls: list[dict] = []

    async def generate_text(self, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(0)
        raise RuntimeError("gemini is down")


_SECTORS = [
    SectorPerformanceEntryResponse(sector="Technology", change_percent=1.2),
    SectorPerformanceEntryResponse(sector="Energy", change_percent=0.9),
    SectorPerformanceEntryResponse(sector="Utilities", change_percent=-0.3),
]

_STORY_ARGS = dict(
    symbol="^GSPC", index_name="S&P 500", pe=26.4, forward_pe=22.4,
    earnings_yield=3.79, val_label="Expensive", historical_avg_pe=21,
    historical_period="10-year", sectors=_SECTORS,
)


def _index_service(monkeypatch, gemini, tier2=None, macro=None):
    import app.services.index_service as mod
    mod._cache.clear()
    mod._ai_refresh_inflight.clear()
    mod._background_tasks.clear()
    svc = mod.IndexService.__new__(mod.IndexService)
    svc.fmp = None
    svc.supabase = None
    svc._tier2_get = staticmethod(lambda symbol, category: tier2)      # type: ignore
    puts: list = []
    svc._tier2_put = staticmethod(                                     # type: ignore
        lambda symbol, category, payload: puts.append((symbol, category, payload)))
    svc._check_macro_cache = staticmethod(lambda symbol: macro)        # type: ignore
    svc._upsert_macro_cache = staticmethod(lambda *a, **k: None)       # type: ignore
    monkeypatch.setattr(mod, "get_gemini_client", lambda: gemini)
    return mod, svc, puts


async def _drain():
    """Let the spawned background task run to completion."""
    import app.services.index_service as mod
    for _ in range(50):
        if not mod._background_tasks:
            break
        await asyncio.gather(*list(mod._background_tasks), return_exceptions=True)
        await asyncio.sleep(0)


# ── 1. Gemini is never awaited on the request path ───────────────────


@pytest.mark.asyncio
async def test_index_stories_return_templates_without_awaiting_gemini(monkeypatch):
    gem = _RecordingGemini(delay=0.05)
    mod, svc, _ = _index_service(monkeypatch, gem)

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    valuation, sector, macro, indicators = await svc._generate_ai_stories(**_STORY_ARGS)
    elapsed = loop.time() - t0

    # Returned BEFORE Gemini could possibly have answered.
    assert elapsed < 0.05, f"the request path waited {elapsed:.3f}s on Gemini"
    assert gem.calls == [], "Gemini must not have been called on the request path yet"
    # …and what came back is the deterministic template, fully formed.
    assert "{PE_RATIO}" in valuation and "{VALUATION_LABEL}" in valuation
    assert "{TOP_SECTOR}" in sector and "{ADVANCING_COUNT}" in sector
    assert macro and len(indicators) == 4
    await _drain()


@pytest.mark.asyncio
async def test_a_gemini_stall_cannot_hold_the_response(monkeypatch):
    """GEMINI_REQUEST_TIMEOUT_SECONDS is 90. Before this change a stall held the whole
    index screen for up to that long, waiting for prose that was already computed."""
    class _Hanging(_RecordingGemini):
        async def generate_text(self, **kwargs):
            self.calls.append(kwargs)
            await asyncio.sleep(3600)

    gem = _Hanging()
    mod, svc, _ = _index_service(monkeypatch, gem)
    result = await asyncio.wait_for(svc._generate_ai_stories(**_STORY_ARGS), timeout=1.0)
    assert result[0]
    for task in list(mod._background_tasks):
        task.cancel()
    await _drain()


@pytest.mark.asyncio
async def test_index_stories_never_await_gemini_even_when_it_fails(monkeypatch):
    gem = _ExplodingGemini()
    mod, svc, _ = _index_service(monkeypatch, gem)
    valuation, _, _, _ = await svc._generate_ai_stories(**_STORY_ARGS)
    assert "{PE_RATIO}" in valuation
    await _drain()
    # The failure happened in the background and left the templates in place.
    assert len(gem.calls) == 1


# ── 2. The refresh writes BOTH tiers ─────────────────────────────────


@pytest.mark.asyncio
async def test_the_background_refresh_persists_to_tier2(monkeypatch):
    gem = _RecordingGemini()
    mod, svc, puts = _index_service(monkeypatch, gem)
    await svc._generate_ai_stories(**_STORY_ARGS)
    await _drain()

    assert len(gem.calls) == 1
    assert [c[1] for c in puts] == ["ai_stories"], (
        "the AI stories must land in the EXISTING index_cache tier-2 under a new "
        "category — this is the whole reason a redeploy stops costing a Gemini call"
    )
    payload = puts[0][2]
    assert set(payload) == {"valuation", "sector", "macro", "indicators"}
    assert isinstance(payload["indicators"], list) and payload["indicators"]
    # Tier-1 now holds the AI version, not the template.
    cached = mod._cache_get("ai_stories_^GSPC")
    assert cached is not None and "{TOP_SECTOR}" in cached[1]
    assert "led at" in cached[1], "the Gemini story should have replaced the template"


@pytest.mark.asyncio
async def test_a_tier2_hit_serves_without_calling_gemini(monkeypatch):
    gem = _RecordingGemini()
    stored = {
        "valuation": "{PE_RATIO} vs {VALUATION_LABEL} — persisted.",
        "sector": "{TOP_SECTOR} led, {ADVANCING_COUNT} advanced — persisted.",
        "macro": "Macro, persisted.",
        "indicators": [
            {"title": "GDP", "description": "Steady.", "signal": "positive"},
        ],
    }
    mod, svc, _ = _index_service(monkeypatch, gem, tier2=stored)
    valuation, sector, macro, indicators = await svc._generate_ai_stories(**_STORY_ARGS)
    await _drain()

    assert valuation == stored["valuation"]
    assert sector == stored["sector"]
    assert macro == stored["macro"]
    assert [i.title for i in indicators] == ["GDP"]
    assert isinstance(indicators[0], MacroForecastItemResponse)
    assert gem.calls == [], "a warm tier-2 must not spawn a refresh"
    assert not mod._background_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    "not-a-dict",
    {},                                                     # no keys
    {"valuation": "v", "sector": "s"},                      # missing macro
    {"valuation": "", "sector": "s", "macro": "m", "indicators": [{"title": "t"}]},
    {"valuation": 1, "sector": "s", "macro": "m", "indicators": [{"title": "t"}]},
    {"valuation": "v", "sector": "s", "macro": "m", "indicators": []},
    {"valuation": "v", "sector": "s", "macro": "m", "indicators": "nope"},
    {"valuation": "v", "sector": "s", "macro": "m",
     "indicators": [{"nope": "wrong shape"}]},
])
async def test_an_unusable_tier2_row_rebuilds_instead_of_raising(monkeypatch, bad):
    """A tier-2 row is JSON a PREVIOUS deploy wrote. `Model(**row)` raising out of the
    middle of a build 500s the screen for the life of the row — drop it and rebuild."""
    gem = _RecordingGemini()
    mod, svc, _ = _index_service(monkeypatch, gem, tier2=bad)
    valuation, _, _, indicators = await svc._generate_ai_stories(**_STORY_ARGS)
    assert "{PE_RATIO}" in valuation      # fell back to the template
    assert len(indicators) == 4
    await _drain()


# ── 3. One refresh per symbol ────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_cold_viewers_spawn_exactly_one_gemini_call(monkeypatch):
    """The detail-level `_inflight` is keyed on the RANGE, so ten viewers on ten
    different range pills of one cold index would each reach here."""
    gem = _RecordingGemini(delay=0.05)
    mod, svc, _ = _index_service(monkeypatch, gem)

    await asyncio.gather(*(svc._generate_ai_stories(**_STORY_ARGS) for _ in range(10)))
    await _drain()

    assert len(gem.calls) == 1, f"expected 1 Gemini call, got {len(gem.calls)}"


@pytest.mark.asyncio
async def test_the_inflight_marker_clears_so_a_later_refresh_can_run(monkeypatch):
    """A latch that never releases would pin the templates forever after one failure."""
    gem = _ExplodingGemini()
    mod, svc, _ = _index_service(monkeypatch, gem)
    await svc._generate_ai_stories(**_STORY_ARGS)
    await _drain()
    assert "^GSPC" not in mod._ai_refresh_inflight

    mod._cache.clear()
    await svc._generate_ai_stories(**_STORY_ARGS)
    await _drain()
    assert len(gem.calls) == 2, "a second attempt must be possible after a failure"


@pytest.mark.asyncio
async def test_the_background_task_is_owned_and_reports_its_death(monkeypatch, caplog):
    """`asyncio.create_task` keeps only a WEAK reference — an unowned task can be
    collected mid-flight, and an exception nobody retrieves is silent until GC."""
    import logging
    gem = _ExplodingGemini()
    mod, svc, _ = _index_service(monkeypatch, gem)
    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        await svc._generate_ai_stories(**_STORY_ARGS)
        assert mod._background_tasks, "the task handle must be held while it runs"
        await _drain()
    assert not mod._background_tasks, "a finished task must be released"
    assert any("refresh failed" in r.message or "gemini is down" in str(r.msg)
               or "refresh failed" in str(r.msg) for r in caplog.records), \
        "a background failure must leave a marker — silent degradation is the worst case"


# ── 4. Thinking is capped on these calls ─────────────────────────────


@pytest.mark.asyncio
async def test_the_story_call_disables_thinking(monkeypatch):
    """Measured on this exact prompt: 3.91s/4.26s and ~700 thought tokens (billed at the
    OUTPUT rate) with the default dynamic budget, versus 1.21s/1.47s and zero at 0. The
    output is two templates whose every number is a placeholder token — there is nothing
    to reason about. See SYSTEM_DESIGN_GUIDELINES §9b.7."""
    gem = _RecordingGemini()
    mod, svc, _ = _index_service(monkeypatch, gem)
    await svc._generate_ai_stories(**_STORY_ARGS)
    await _drain()
    assert gem.calls[0]["thinking_budget"] == 0


@pytest.mark.asyncio
async def test_thinking_budget_is_part_of_the_gemini_cache_key(monkeypatch):
    """Same reason `max_output_tokens` is in the key: a no-thinking answer and a reasoned
    one to the SAME prompt are different answers, and the cheap one must not be served to
    the caller that asked for the reasoned one.

    Behavioural, not a source grep — the key is built from a multi-line call and a regex
    over it proved nothing the first time.
    """
    from app.integrations.gemini import GeminiClient

    calls: list = []

    class _FakeModels:
        async def generate_content(self, **kwargs):
            calls.append(kwargs)
            await asyncio.sleep(0)
            class _R:
                candidates = []
                usage_metadata = None
            return _R()

    client = GeminiClient.__new__(GeminiClient)
    client.model_name = "gemini-2.5-flash"
    client._temperature = 0.7
    client._max_tokens = 8192
    from app.integrations.gemini import _TTLCache
    client._response_cache = _TTLCache(max_size=16, ttl_seconds=3600)
    client._embedding_cache = _TTLCache(max_size=16, ttl_seconds=3600)

    class _Aio:
        models = _FakeModels()

    class _Client:
        aio = _Aio()

    client._client = _Client()

    await client.generate_text(prompt="same prompt", thinking_budget=0)
    await client.generate_text(prompt="same prompt")          # default budget
    assert len(calls) == 2, (
        "a capped and an uncapped call for one prompt collided in the response cache"
    )

    # …and the SAME budget still caches, so the key did not become a cache-buster.
    await client.generate_text(prompt="same prompt", thinking_budget=0)
    assert len(calls) == 2, "an identical call must still hit the cache"

    # The budget actually reaches the SDK as a ThinkingConfig, and is absent by default.
    assert calls[0]["config"].thinking_config is not None
    assert calls[0]["config"].thinking_config.thinking_budget == 0
    assert calls[1]["config"].thinking_config is None


# ── 5. ETF: the same three properties ────────────────────────────────


def _etf_service(monkeypatch, gemini, tier2=None):
    import app.services.etf_service as mod
    mod._cache.clear()
    mod._ai_refresh_inflight.clear()
    mod._background_tasks.clear()
    svc = mod.ETFService.__new__(mod.ETFService)
    svc.fmp = None
    svc.supabase = None
    svc._tier2_get = staticmethod(lambda symbol, category: tier2)   # type: ignore
    puts: list = []
    svc._tier2_put = staticmethod(                                  # type: ignore
        lambda symbol, category, payload: puts.append((symbol, category, payload)))
    monkeypatch.setattr(mod, "get_gemini_client", lambda: gemini)
    return mod, svc, puts


_HOOK_ARGS = dict(
    symbol="SCHD", name="Schwab U.S. Dividend Equity ETF", description="Dividend fund.",
    asset_class="Equity", index_tracked="Dow Jones U.S. Dividend 100",
    holdings_count=103, top_holdings=[], top_sectors=[],
    fallback="Tracks a dividend index of 103 U.S. companies.",
)


async def _drain_etf():
    import app.services.etf_service as mod
    for _ in range(50):
        if not mod._background_tasks:
            break
        await asyncio.gather(*list(mod._background_tasks), return_exceptions=True)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_etf_hook_returns_the_fallback_without_awaiting_gemini(monkeypatch):
    gem = _RecordingGemini(text="Owns 103 dividend payers, weighted by quality.", delay=0.05)
    mod, svc, _ = _etf_service(monkeypatch, gem)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    hook = await svc._generate_hook_text(**_HOOK_ARGS)
    assert loop.time() - t0 < 0.05
    assert hook == _HOOK_ARGS["fallback"]
    assert gem.calls == []
    await _drain_etf()


@pytest.mark.asyncio
async def test_etf_hook_refresh_persists_and_caps_thinking(monkeypatch):
    gem = _RecordingGemini(text="Owns 103 dividend payers, weighted by quality.")
    mod, svc, puts = _etf_service(monkeypatch, gem)
    await svc._generate_hook_text(**_HOOK_ARGS)
    await _drain_etf()
    assert [c[1] for c in puts] == ["ai_hook"]
    assert puts[0][2] == {"hook": "Owns 103 dividend payers, weighted by quality."}
    assert gem.calls[0]["thinking_budget"] == 0
    assert mod._cache_get("etf_hook_SCHD") == "Owns 103 dividend payers, weighted by quality."


@pytest.mark.asyncio
async def test_etf_hook_serves_a_warm_tier2_without_gemini(monkeypatch):
    gem = _RecordingGemini()
    mod, svc, _ = _etf_service(monkeypatch, gem, tier2={"hook": "Persisted hook."})
    assert await svc._generate_hook_text(**_HOOK_ARGS) == "Persisted hook."
    assert gem.calls == []
    assert not mod._background_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["not-a-dict", {}, {"hook": ""}, {"hook": 7}, None])
async def test_etf_hook_rebuilds_on_an_unusable_tier2_row(monkeypatch, bad):
    gem = _RecordingGemini(text="Fresh hook.")
    mod, svc, _ = _etf_service(monkeypatch, gem, tier2=bad)
    assert await svc._generate_hook_text(**_HOOK_ARGS) == _HOOK_ARGS["fallback"]
    await _drain_etf()
    assert len(gem.calls) == 1


@pytest.mark.asyncio
async def test_etf_concurrent_cold_viewers_spawn_one_gemini_call(monkeypatch):
    gem = _RecordingGemini(text="Owns 103 dividend payers.", delay=0.05)
    mod, svc, _ = _etf_service(monkeypatch, gem)
    await asyncio.gather(*(svc._generate_hook_text(**_HOOK_ARGS) for _ in range(10)))
    await _drain_etf()
    assert len(gem.calls) == 1


@pytest.mark.asyncio
async def test_an_overlong_gemini_hook_is_rejected_not_persisted(monkeypatch):
    gem = _RecordingGemini(text="x" * 400)
    mod, svc, puts = _etf_service(monkeypatch, gem)
    await svc._generate_hook_text(**_HOOK_ARGS)
    await _drain_etf()
    assert puts == [], "a rejected hook must not reach tier-2"


# ── 6. Anti-vacuity ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_fakes_actually_suspend():
    """If a fake had no suspension point, the first coroutine in every `gather` above
    would run to completion before the second started and the dedup assertions would
    pass with the dedup deleted."""
    gem = _RecordingGemini(delay=0.01)
    order: list[str] = []

    async def call(tag):
        order.append(f"{tag}-start")
        await gem.generate_text(prompt=tag)
        order.append(f"{tag}-end")

    await asyncio.gather(call("a"), call("b"))
    assert order[:2] == ["a-start", "b-start"], (
        "both coroutines must be in flight before either finishes, or every "
        "concurrency test in this file is vacuous"
    )
