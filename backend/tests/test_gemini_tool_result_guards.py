"""Tool-result handling in the Gemini client: structural truncation, the guarded handler
runner, and the thinking budget on the tool-chat factory (2026-09-11).

WHY
---
Three call sites fed tool results back to the model with three different hard cuts —
`json.dumps(result)[:8000]` (stream_agentic), `[:5000]` (the research loop) and no cut at
all (generate_with_tools) — so an oversized result arrived as syntactically broken JSON
with no marker. And `generate_with_tools` ran the tool handlers INSIDE its `@async_retry`
body: an `FMPRateLimitException` ("rate limit" in its message) was classified as a Gemini
QUOTA error, retried on the quota ladder, and counted toward the process-wide circuit
breaker. `create_tool_chat` — the deep-research loop's factory — had no thinking knob at
all while every other report stage was capped.

No network: fakes for the SDK and the handlers.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.config import settings
from app.integrations import gemini as gem
from app.integrations.gemini import (
    GeminiClient, _TTLCache, _run_tool_handler, truncate_tool_result,
)


# ── truncate_tool_result ──────────────────────────────────────────────────────


def _size(v) -> int:
    return len(json.dumps(v, default=str))


def test_small_results_pass_through_untouched():
    r = {"ticker": "AAPL", "price": 1.0, "rows": [1, 2, 3]}
    assert truncate_tool_result(r, budget=500) is r


def test_oversized_result_is_pruned_structurally_and_marked():
    big = {"ticker": "AAPL", "headlines": [{"title": "x" * 300, "body": "y" * 800} for _ in range(60)]}
    out = truncate_tool_result(big, budget=4000)
    assert _size(out) <= 4000
    assert out["_truncated"] is True and out["_dropped"] > 0
    assert out["ticker"] == "AAPL", "scalar fields survive pruning"
    assert 0 < len(out["headlines"]) < 60, "list tails are dropped, not the whole list"
    json.loads(json.dumps(out))  # still valid JSON — the point of pruning over slicing


def test_a_single_huge_scalar_is_wrapped_not_cut():
    out = truncate_tool_result({"text": "z" * 50_000}, budget=1000)
    assert _size(out) <= 1000
    assert out["_truncated"] is True
    json.loads(json.dumps(out))


def test_non_dict_shapes_never_raise():
    assert truncate_tool_result(["a"] * 10_000, budget=200)["_truncated"] is True
    assert truncate_tool_result("s" * 10_000, budget=200)["_truncated"] is True
    assert truncate_tool_result(None, budget=10) is None


def test_the_budget_defaults_to_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_TOOL_RESULT_MAX_CHARS", 300)
    out = truncate_tool_result({"rows": list(range(500))})
    assert _size(out) <= 300 and out["_truncated"] is True


# ── _run_tool_handler ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_raising_handler_becomes_an_error_result_not_an_exception():
    from app.integrations.fmp import FMPRateLimitException

    async def boom(args):
        raise FMPRateLimitException("FMP rate limit hit on /quote")

    out = await _run_tool_handler("get_stock_chart_data", boom, {"ticker": "AAPL"})
    assert out["error"].startswith("FMP rate limit") and out["tool"] == "get_stock_chart_data"


@pytest.mark.asyncio
async def test_a_slow_handler_times_out_with_an_explicit_marker(monkeypatch):
    """A tool with no per-tool ceiling runs under the default `CHAT_TOOL_TIMEOUT_SECONDS`."""
    monkeypatch.setattr(settings, "CHAT_TOOL_TIMEOUT_SECONDS", 0.05)

    async def slow(args):
        await asyncio.sleep(1.0)
        return {"never": True}

    out = await _run_tool_handler("get_ticker_news", slow, {"ticker": "AAPL"})
    assert out == {"error": "timed_out", "tool": "get_ticker_news", "timeout_seconds": 0.05}


@pytest.mark.asyncio
async def test_long_running_market_tools_are_not_cut_at_the_default_ceiling(monkeypatch):
    """`explain_price_move` may escalate to a grounded web search (a model chain with
    retries) and `get_market_snapshot` sweeps several services. Cancelling those at the 8 s
    default wasted the paid search AND stranded its claimed daily unit — so they carry their
    own ceilings, and the default setting must not reach them."""
    from app.integrations.gemini import _TOOL_TIMEOUTS

    monkeypatch.setattr(settings, "CHAT_TOOL_TIMEOUT_SECONDS", 0.01)
    assert _TOOL_TIMEOUTS["explain_price_move"] >= 60.0
    assert _TOOL_TIMEOUTS["get_market_snapshot"] >= 30.0
    assert _TOOL_TIMEOUTS["explain_price_move"] > _TOOL_TIMEOUTS["get_market_snapshot"]

    async def slower_than_default(args):
        await asyncio.sleep(0.05)      # past the 0.01 s default, far under its own ceiling
        return {"paid": True}

    out = await _run_tool_handler("explain_price_move", slower_than_default, {"ticker": "AAPL"})
    assert out == {"paid": True}


@pytest.mark.asyncio
async def test_an_unknown_tool_is_an_error_result():
    out = await _run_tool_handler("ghost", None, {})
    assert out == {"error": "unknown tool: ghost"}


# ── generate_with_tools: a tool failure must not trip the quota breaker ───────


class _Part:
    def __init__(self, fc=None, text=None):
        self.function_call = fc
        self.text = text
        self.thought = False


class _FC:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _Cand:
    def __init__(self, parts):
        self.content = _Content(parts)
        self.finish_reason = None


class _Resp:
    def __init__(self, parts):
        self.candidates = [_Cand(parts)]
        self.usage_metadata = None


def _client(responses):
    calls: list = []

    class _Models:
        async def generate_content(self, **kwargs):
            calls.append(kwargs)
            await asyncio.sleep(0)
            return responses.pop(0)

    client = GeminiClient.__new__(GeminiClient)
    client.model_name = "gemini-2.5-flash"
    client._temperature = 0.7
    client._max_tokens = 8192
    client._response_cache = _TTLCache(max_size=8, ttl_seconds=60)
    client._embedding_cache = _TTLCache(max_size=8, ttl_seconds=60)

    class _Aio:
        models = _Models()

    class _C:
        aio = _Aio()

    client._client = _C()
    return client, calls


@pytest.mark.asyncio
async def test_a_tool_that_raises_a_rate_limit_is_not_retried_and_does_not_touch_the_breaker():
    from app.integrations.fmp import FMPRateLimitException

    gem._quota_circuit.reset()
    first = _Resp([_Part(fc=_FC("get_stock_chart_data", {"ticker": "AAPL"}))])
    follow = _Resp([_Part(text="Here is the answer.")])
    client, calls = _client([first, follow])

    async def boom(args):
        raise FMPRateLimitException("FMP rate limit hit on /quote")

    out = await client.generate_with_tools(
        prompt="p", tools=[], tool_handlers={"get_stock_chart_data": boom},
    )
    assert out["text"] == "Here is the answer."
    assert len(calls) == 2, "one model call + one follow-up — NO quota-ladder retry"
    assert gem._quota_circuit._consecutive == 0, "an FMP failure is not a Gemini quota error"
    # The model still received one function_response per call, carrying the error.
    sent = calls[1]["contents"][-1].parts[0].function_response.response["result"]
    assert sent["error"].startswith("FMP rate limit")
    assert out["tool_results"] == [], "an error result is not a widget payload"


# ── create_tool_chat: the deep-research loop's thinking budget ────────────────


def test_create_tool_chat_forwards_the_thinking_budget():
    seen: list = []

    class _Chats:
        def create(self, *, model, config):
            seen.append(config)
            return object()

    client = GeminiClient.__new__(GeminiClient)
    client.model_name = "gemini-2.5-flash"

    class _Aio:
        chats = _Chats()

    class _C:
        aio = _Aio()

    client._client = _C()
    client.create_tool_chat("sys", [], thinking_budget=0)
    client.create_tool_chat("sys", [], thinking_budget=None)
    assert seen[0].thinking_config is not None and seen[0].thinking_config.thinking_budget == 0
    assert seen[1].thinking_config is None, "None must attach NO thinking_config (byte-identical to pre-cap)"


@pytest.mark.asyncio
async def test_the_research_loop_passes_its_own_budget(monkeypatch):
    """The loop is up to five calls per deep report and had no budget at all."""
    from app.services.agents.narrative_prompts import agentic_thinking_budget
    from app.services.agents.persona_config import get_persona_config
    from app.services.agents.research_agent import ResearchAgent

    monkeypatch.setattr(settings, "REPORT_AGENTIC_THINKING_BUDGET", 0)
    seen: dict = {}

    class _Chat:
        async def send_message(self, msg):
            return _Resp([_Part(text="findings")])

    class _Gem:
        model_name = "gemini-2.5-flash"

        def create_tool_chat(self, **kwargs):
            seen.update(kwargs)
            return _Chat()

    agent = ResearchAgent.__new__(ResearchAgent)
    agent.gemini = _Gem()
    agent.fmp = object()
    agent.persona = get_persona_config("warren_buffett")

    class _Out:
        ticker = "AAPL"
        profile = {"companyName": "Apple Inc."}

    text = await agent._agentic_research(_Out(), "EVIDENCE")
    assert text == "findings"
    assert seen["thinking_budget"] == agentic_thinking_budget() == 0

    monkeypatch.setattr(settings, "REPORT_AGENTIC_THINKING_BUDGET", -1)
    assert agentic_thinking_budget() is None, "negative restores the model default"
