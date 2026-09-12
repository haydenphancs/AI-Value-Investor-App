"""Behavioural tests for `ResearchAgent._agentic_research` — the deep-research tool loop.

WHY THIS FILE EXISTS
--------------------
This ~150-line multi-round loop is the ENTIRE premium of the 20-credit deep door over the
free direct door (both then run the same Stage A / Stage B), and until 2026-09-11 no test
ran it: the one test that named it monkeypatched it out. Its chat-side twin
(`stream_agentic`) has tests/test_chat_agentic_stream.py; this is that file's mirror.

Shape: a fake `create_tool_chat` returning a scripted chat whose `send_message` pops
pre-built responses (a function_call part, a text part, or nothing), fake tool handlers,
and the real loop driving them. No network, no Gemini, no FMP.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from app.config import settings
from app.services.agents import research_agent as ra
from app.services.agents.persona_config import get_persona_config
from app.services.agents.research_agent import MAX_AGENTIC_ROUNDS, ResearchAgent


# ── fakes ────────────────────────────────────────────────────────────────────

class _FC:
    def __init__(self, name: str, args: Dict[str, Any] | None = None):
        self.name = name
        self.args = args or {}


class _Part:
    def __init__(self, fc: _FC | None = None, text: str | None = None):
        self.function_call = fc
        self.text = text
        self.thought = False


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _Cand:
    def __init__(self, parts):
        self.content = _Content(parts)
        self.finish_reason = None


class _Resp:
    def __init__(self, parts: List[_Part]):
        self.candidates = [_Cand(parts)] if parts is not None else []
        self.usage_metadata = None
        # `_safe_response_text` may read `.text`; mirror the SDK: only text parts contribute.
        texts = [p.text for p in (parts or []) if p.text]
        self.text = "".join(texts) if texts else None


def fc(name, **args) -> _Resp:
    return _Resp([_Part(fc=_FC(name, args))])


def text(s: str) -> _Resp:
    return _Resp([_Part(text=s)])


class _ScriptedChat:
    """Pops one response per send_message; records what was sent."""

    def __init__(self, responses: List[Any]):
        self._responses = list(responses)
        self.sent: List[Any] = []

    async def send_message(self, msg):
        self.sent.append(msg)
        if not self._responses:
            raise AssertionError("send_message called more times than scripted")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class _Gem:
    def __init__(self, responses: List[Any], fallback_text: str = "FALLBACK ANALYSIS"):
        self.model_name = "gemini-2.5-flash"
        self.chat = _ScriptedChat(responses)
        self.create_kwargs: Dict[str, Any] = {}
        self.fallback_calls: List[Dict[str, Any]] = []
        self._fallback_text = fallback_text

    def create_tool_chat(self, **kwargs):
        self.create_kwargs = kwargs
        return self.chat

    async def generate_text(self, **kwargs):
        self.fallback_calls.append(kwargs)
        if isinstance(self._fallback_text, Exception):
            raise self._fallback_text
        return {"text": self._fallback_text}


class _Out:
    ticker = "AAPL"
    profile = {"companyName": "Apple Inc."}


def _agent(gem: _Gem, monkeypatch, handlers: Dict[str, Any] | None = None) -> ResearchAgent:
    agent = ResearchAgent.__new__(ResearchAgent)
    agent.gemini = gem
    agent.fmp = object()
    agent.persona = get_persona_config("warren_buffett")
    monkeypatch.setattr(ra, "build_fmp_tool_declarations", lambda: object())
    monkeypatch.setattr(ra, "build_tool_handlers", lambda fmp: handlers or {})
    return agent


def _fed_back(chat: _ScriptedChat, round_index: int) -> List[Dict[str, Any]]:
    """The function_response payloads the loop sent back in a given follow-up."""
    sent = chat.sent[round_index]
    assert isinstance(sent, list), "a tool follow-up sends a list of Parts"
    return [p.function_response.response for p in sent]


# ── the happy path: tool → answer ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_tool_call_is_executed_and_its_result_fed_back(monkeypatch):
    seen: Dict[str, Any] = {}

    async def more_news(args):
        seen.update(args)
        return {"headlines": ["Apple beats"]}

    gem = _Gem([fc("fetch_more_news", ticker="AAPL", limit=5), text("Findings: strong quarter.")])
    agent = _agent(gem, monkeypatch, {"fetch_more_news": more_news})

    out = await agent._agentic_research(_Out(), "EVIDENCE")

    assert out == "Findings: strong quarter."
    assert seen == {"ticker": "AAPL", "limit": 5}
    fed = _fed_back(gem.chat, 1)
    assert json.loads(fed[0]["result"]) == {"headlines": ["Apple beats"]}
    assert gem.fallback_calls == [], "a successful loop never runs the single-pass fallback"


@pytest.mark.asyncio
async def test_research_complete_returns_its_summary_when_there_is_no_text(monkeypatch):
    gem = _Gem([fc("research_complete", summary="Buy the dip, quality compounder.")])
    agent = _agent(gem, monkeypatch)
    out = await agent._agentic_research(_Out(), "EVIDENCE")
    assert out == "Buy the dip, quality compounder."
    assert len(gem.chat.sent) == 1, "research_complete ends the loop immediately"


@pytest.mark.asyncio
async def test_the_persona_prompt_and_budget_reach_the_tool_chat(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_AGENTIC_THINKING_BUDGET", 0)
    gem = _Gem([text("ok")])
    agent = _agent(gem, monkeypatch)
    await agent._agentic_research(_Out(), "EVIDENCE")
    assert agent.persona.system_prompt in gem.create_kwargs["system_instruction"]
    assert "Cay AI" in gem.create_kwargs["system_instruction"], "the identity rule rides in the persona prompt"
    assert gem.create_kwargs["thinking_budget"] == 0
    assert gem.create_kwargs["max_output_tokens"] == settings.GEMINI_MAX_TOKENS


# ── degradation inside the loop ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unknown_tool_gets_an_error_result_and_the_loop_continues(monkeypatch):
    gem = _Gem([fc("fetch_unicorns"), text("answer anyway")])
    agent = _agent(gem, monkeypatch, {})
    out = await agent._agentic_research(_Out(), "EVIDENCE")
    assert out == "answer anyway"
    fed = json.loads(_fed_back(gem.chat, 1)[0]["result"])
    assert fed == {"error": "Unknown tool: fetch_unicorns"}


@pytest.mark.asyncio
async def test_a_raising_handler_becomes_an_error_result_not_a_crash(monkeypatch):
    async def boom(args):
        raise RuntimeError("FMP 503")

    gem = _Gem([fc("fetch_more_news", ticker="AAPL"), text("still answered")])
    agent = _agent(gem, monkeypatch, {"fetch_more_news": boom})
    out = await agent._agentic_research(_Out(), "EVIDENCE")
    assert out == "still answered"
    assert json.loads(_fed_back(gem.chat, 1)[0]["result"]) == {"error": "FMP 503"}
    assert gem.fallback_calls == []


@pytest.mark.asyncio
async def test_an_oversized_tool_result_is_pruned_structurally_and_marked(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_TOOL_RESULT_MAX_CHARS", 2000)

    async def huge(args):
        return {"rows": [{"period": f"Q{i}", "note": "x" * 400} for i in range(80)]}

    gem = _Gem([fc("fetch_quarterly_financials", ticker="AAPL"), text("done")])
    agent = _agent(gem, monkeypatch, {"fetch_quarterly_financials": huge})
    await agent._agentic_research(_Out(), "EVIDENCE")
    raw = _fed_back(gem.chat, 1)[0]["result"]
    assert len(raw) <= 2000
    fed = json.loads(raw)  # valid JSON — the old `[:5000]` slice handed the model a broken cut
    assert fed["_truncated"] is True and fed["_dropped"] > 0
    assert 0 < len(fed["rows"]) < 80


@pytest.mark.asyncio
async def test_parallel_function_calls_in_one_round_all_get_a_response(monkeypatch):
    calls: List[str] = []

    async def h(args):
        calls.append(args.get("k"))
        return {"k": args.get("k")}

    resp = _Resp([_Part(fc=_FC("fetch_more_news", {"k": "a"})), _Part(fc=_FC("fetch_more_news", {"k": "b"}))])
    gem = _Gem([resp, text("merged")])
    agent = _agent(gem, monkeypatch, {"fetch_more_news": h})
    out = await agent._agentic_research(_Out(), "EVIDENCE")
    assert out == "merged" and calls == ["a", "b"]
    assert len(_fed_back(gem.chat, 1)) == 2, "one function_response per call, or the API 400s"


# ── the three fallback entry points ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_round_exhaustion_runs_the_single_pass_synthesis(monkeypatch):
    """Every round a tool call and never `research_complete` → there is no synthesis. This
    used to return the literal "Research analysis complete." — a placeholder that became
    the whole deep-research premium on a report charged 20 credits."""
    gem = _Gem([fc("fetch_more_news", ticker="AAPL")] * (MAX_AGENTIC_ROUNDS + 1),
               fallback_text="SINGLE-PASS SYNTHESIS")

    async def h(args):
        return {"ok": 1}

    agent = _agent(gem, monkeypatch, {"fetch_more_news": h})
    out = await agent._agentic_research(_Out(), "EVIDENCE")
    assert out == "SINGLE-PASS SYNTHESIS"
    assert len(gem.fallback_calls) == 1
    assert "Research analysis complete." not in out
    # The synthesis is persona-voiced, like the loop.
    assert gem.fallback_calls[0]["system_instruction"] == agent.persona.system_prompt


@pytest.mark.asyncio
async def test_an_empty_model_response_falls_back(monkeypatch):
    gem = _Gem([_Resp([])], fallback_text="FALLBACK")
    agent = _agent(gem, monkeypatch)
    assert await agent._agentic_research(_Out(), "EVIDENCE") == "FALLBACK"


@pytest.mark.asyncio
async def test_a_transport_error_falls_back(monkeypatch):
    gem = _Gem([RuntimeError("socket closed")], fallback_text="FALLBACK")
    agent = _agent(gem, monkeypatch)
    assert await agent._agentic_research(_Out(), "EVIDENCE") == "FALLBACK"


@pytest.mark.asyncio
async def test_a_failing_fallback_yields_the_honest_sentinel(monkeypatch):
    gem = _Gem([RuntimeError("socket closed")], fallback_text=RuntimeError("quota"))
    agent = _agent(gem, monkeypatch)
    out = await agent._agentic_research(_Out(), "EVIDENCE")
    assert out == "Analysis for AAPL could not be completed."
