"""Thinking budgets on the 20-credit report path (SYSTEM_DESIGN_GUIDELINES 9b.7).

gemini-2.5-flash reasons by default and those thought tokens bill at the OUTPUT
rate ($2.50/1M) while producing nothing the user reads. Nothing on the report
path ever set a budget, which is what pulled worst-case report COGS from a
documented $0.05-0.06 toward $0.09-0.15.

This file covers the CLIENT half — that a budget reaches the SDK as a
ThinkingConfig on the two helpers that lacked one, that `generate_json`'s
response cache cannot serve a cheap answer to a caller who asked for a reasoned
one, and that Stage A passes its (separate) budget on BOTH report doors.
The Stage-B runner half lives in `test_narrative_context_cache.py`.

No network: a fake `aio.models` records the kwargs the SDK would have received.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.integrations.gemini import GeminiClient, _TTLCache, _thinking_config


# ── A GeminiClient wired to a recording fake SDK ──────────────────────────────

class _FakeModels:
    def __init__(self, calls):
        self._calls = calls

    async def generate_content(self, **kwargs):
        self._calls.append(kwargs)
        await asyncio.sleep(0)

        class _R:
            candidates = []
            usage_metadata = None

        return _R()


def _client():
    """A real GeminiClient with a fake transport — the production code paths run."""
    calls: list = []
    client = GeminiClient.__new__(GeminiClient)
    client.model_name = "gemini-2.5-flash"
    client._temperature = 0.7
    client._max_tokens = 8192
    client._response_cache = _TTLCache(max_size=16, ttl_seconds=3600)
    client._embedding_cache = _TTLCache(max_size=16, ttl_seconds=3600)

    class _Aio:
        models = _FakeModels(calls)

    class _Client:
        aio = _Aio()

    client._client = _Client()
    return client, calls


_HANDLE_NAME = "cachedContents/abc123"


class _FakeCache:
    name = _HANDLE_NAME


# ── generate_text_cached: the Stage-B hot path ────────────────────────────────

@pytest.mark.asyncio
async def test_generate_text_cached_forwards_the_thinking_budget():
    client, calls = _client()

    await client.generate_text_cached("job prompt", {"cache": _FakeCache()},
                                      thinking_budget=0)

    cfg = calls[0]["config"]
    assert cfg.thinking_config is not None
    assert cfg.thinking_config.thinking_budget == 0
    # The OTHER cost optimisation on this exact line. Losing the cached prefix
    # while gaining the thinking cap would be a net loss, and silent.
    assert cfg.cached_content == _HANDLE_NAME


@pytest.mark.asyncio
async def test_generate_text_cached_sends_no_thinking_config_by_default():
    client, calls = _client()

    await client.generate_text_cached("job prompt", {"cache": _FakeCache()})

    assert calls[0]["config"].thinking_config is None
    assert calls[0]["config"].cached_content == _HANDLE_NAME


# ── generate_json: Stage A, and the cache key ─────────────────────────────────

@pytest.mark.asyncio
async def test_generate_json_forwards_the_thinking_budget():
    client, calls = _client()

    await client.generate_json(prompt="shell", thinking_budget=0)

    cfg = calls[0]["config"]
    assert cfg.thinking_config is not None
    assert cfg.thinking_config.thinking_budget == 0
    assert cfg.response_mime_type == "application/json"


@pytest.mark.asyncio
async def test_generate_json_thinking_budget_is_part_of_the_cache_key():
    """Same reason it is in `generate_text`'s key: a no-thinking answer and a
    reasoned one to the same prompt are different answers, and the cheap one
    must not be served to the caller that asked for the reasoned one."""
    client, calls = _client()

    await client.generate_json(prompt="same prompt", thinking_budget=0)
    await client.generate_json(prompt="same prompt")            # model default
    assert len(calls) == 2, "a capped and an uncapped call collided in the cache"

    # …and an identical call still caches, so the key did not become a buster.
    await client.generate_json(prompt="same prompt", thinking_budget=0)
    assert len(calls) == 2, "an identical call must still hit the cache"

    assert calls[0]["config"].thinking_config.thinking_budget == 0
    assert calls[1]["config"].thinking_config is None


@pytest.mark.asyncio
async def test_generate_json_response_schema_is_part_of_the_cache_key():
    """Two callers issuing the same prompt under different schemas would have
    collided, and the first one's SHAPE served to the second. No such pair
    exists today; the key is fixed rather than the hazard documented."""
    client, calls = _client()

    await client.generate_json(prompt="p", response_schema={"type": "OBJECT"})
    await client.generate_json(prompt="p", response_schema={"type": "ARRAY"})
    assert len(calls) == 2, "two schemas for one prompt collided in the cache"


# ── the budget -> ThinkingConfig encoder ──────────────────────────────────────

def test_none_sends_no_thinking_config_at_all():
    """This is what makes a negative setting a safe rollback: no ThinkingConfig
    is byte-identical on the wire to a pre-cap request. Passing Gemini's own
    `-1` ("dynamic thinking") instead would send a field today's requests do not,
    and would depend on AUTOMATIC and the model default being the same thing."""
    assert _thinking_config(None) is None
    assert _thinking_config(0).thinking_budget == 0
    assert _thinking_config(512).thinking_budget == 512


# ── Stage A passes its budget on BOTH report doors ────────────────────────────

class _StageAGemini:
    """Records the kwargs Stage A hands to generate_json.

    `thinking_budget` is declared EXPLICITLY, never swallowed by **kwargs — a
    permissive fake cannot notice a call site that stopped passing it.
    """

    def __init__(self, text='{"ok": 1}'):
        self.calls: list = []
        self._text = text

    async def generate_json(self, prompt=None, system_instruction=None,
                            model_name=None, response_schema=None,
                            thinking_budget=None):
        self.calls.append({"thinking_budget": thinking_budget})
        return {"text": self._text}


@pytest.mark.asyncio
async def test_ticker_report_stage_a_caps_thinking(monkeypatch):
    from app.services.agents.narrative_prompts import stage_a_thinking_budget
    from app.services.ticker_report_service import TickerReportService
    from app.services.agents.persona_config import get_persona_config

    svc = TickerReportService.__new__(TickerReportService)
    gem = _StageAGemini()
    svc.gemini = gem

    class _Out:
        ticker = "AAPL"
        profile = {"companyName": "Apple Inc."}

    await svc._generate_stage_a(_Out(), get_persona_config("warren_buffett"), "EVIDENCE")

    assert gem.calls and gem.calls[0]["thinking_budget"] == stage_a_thinking_budget()


@pytest.mark.asyncio
async def test_research_agent_stage_a_caps_thinking(monkeypatch):
    """The deep path costs the same as the direct one on a cache miss, so a cap
    on only one door is half a fix."""
    from app.services.agents.narrative_prompts import stage_a_thinking_budget
    from app.services.agents.research_agent import ResearchAgent
    from app.services.agents.persona_config import get_persona_config

    agent = ResearchAgent.__new__(ResearchAgent)
    gem = _StageAGemini()
    agent.gemini = gem
    agent.persona = get_persona_config("warren_buffett")

    class _Out:
        ticker = "AAPL"
        profile = {"companyName": "Apple Inc."}

    await agent._generate_stage_a(_Out(), "EVIDENCE", "")

    assert gem.calls and gem.calls[0]["thinking_budget"] == stage_a_thinking_budget()


@pytest.mark.asyncio
async def test_a_negative_stage_a_setting_restores_the_model_default(monkeypatch):
    from app.services.ticker_report_service import TickerReportService
    from app.services.agents.persona_config import get_persona_config

    monkeypatch.setattr(settings, "REPORT_STAGE_A_THINKING_BUDGET", -1)
    svc = TickerReportService.__new__(TickerReportService)
    gem = _StageAGemini()
    svc.gemini = gem

    class _Out:
        ticker = "AAPL"
        profile = {"companyName": "Apple Inc."}

    await svc._generate_stage_a(_Out(), get_persona_config("warren_buffett"), "EVIDENCE")

    assert gem.calls[0]["thinking_budget"] is None


# ── the documented boundary: what is deliberately NOT capped ──────────────────

@pytest.mark.asyncio
async def test_the_post_assembly_syntheses_are_deliberately_uncapped():
    """`synthesize_core_thesis` / `synthesize_critical_factors` write the bull and
    bear thesis and the risk factors — judgement, not template fill-in — so they
    keep full thinking. Pinned so a later blanket edit is a conscious act rather
    than a sweep, and so the carve-out in the config comment stays true.
    """
    from app.services.agents.narrative_prompts import synthesize_core_thesis
    from app.services.agents.persona_config import get_persona_config

    seen: list = []

    class _StrictGemini:
        async def generate_json(self, prompt=None, system_instruction=None,
                                model_name=None, response_schema=None,
                                thinking_budget=None):
            seen.append(thinking_budget)
            return {"text": "{}"}

    # `synthesize_core_thesis` returns EARLY when `build_module_digest` is empty,
    # so a bare report makes this test assert nothing (`all([])` is True). The
    # price-action digest needs `change_pct` to emit a line — verified by the
    # anti-vacuity assertion below, which is the whole reason it is here.
    report = {
        "symbol": "AAPL",
        "company_name": "Apple Inc.",
        "price_action": {"change_pct": 4.2, "window_label": "5 days"},
    }
    await synthesize_core_thesis(report, get_persona_config("warren_buffett"),
                                 _StrictGemini(), "EVIDENCE")

    assert seen, (
        "VACUOUS: synthesize_core_thesis never reached generate_json, so this "
        "test asserted nothing. Give the report a digest-producing section."
    )
    assert all(budget is None for budget in seen), (
        "the post-assembly syntheses are deliberately uncapped — "
        "see REPORT_STAGE_A_THINKING_BUDGET's comment in config.py"
    )
