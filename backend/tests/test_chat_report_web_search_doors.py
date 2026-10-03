"""Report chat's web search through BOTH chat doors.

* `generate_response` (the send door and the stream→non-stream fallback): the tool is declared and
  handled only on a gated turn; `web_search_used` is True only when web results reached a
  SUCCESSFUL tool round; a fallback that is handed the stream's `WebSearchTurn` replays its one
  search; a web answer never enters the shared deep-dive cache.
* `prepare_stream_generation`: one gate decision, exposed as `prep["web_turn"]` /
  `prep["web_search_granted"]`, and the capability block follows it.
* `stream_synthesis`: two specialists that both call the tool share ONE search.
* The stream door (source-scan of `event_gen`, AST-bounded, comments stripped by `tokenize`):
  single mode decided BEFORE the `routing` frame, the turn threaded to the handlers and to the
  fallback, no auto-continuation on a web turn, no deep-dive write from a web answer.

Hermetic: Brave is a stub, the budget is a ledger fake; the key AND the switch are set explicitly
in every gate test (`Settings` reads `backend/.env`).
"""

from __future__ import annotations

import ast
import asyncio
import io
import re
import tokenize
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from app.services import chat_market_tools as cmt
from app.services import chat_web_search_service as cws
from app.services.agents import chat_tools
from app.services.chat_service import ChatService

UID = "user-door-0001"
WEB_MSG = "Can you verify the DOJ case against Apple?"


class _Ledger:
    def __init__(self):
        self.counts: Dict[str, int] = {}
        self.claims: List[str] = []
        self.refunds: List[str] = []

    def try_claim_turn(self, bucket, limit=None):
        self.claims.append(bucket)
        self.counts[bucket] = self.counts.get(bucket, 0) + 1
        return self.counts[bucket]

    def refund_turn(self, bucket):
        self.refunds.append(bucket)


class _Brave:
    def __init__(self, rows=None):
        self.calls: List[str] = []
        self.rows = rows if rows is not None else [{
            "title": "Apple DOJ case advances", "url": "https://www.reuters.com/legal/apple/",
            "description": "The case moved forward.", "page_age": "2026-09-30T00:00:00",
        }]

    async def __call__(self, query, **kw):
        self.calls.append(query)
        return {"results": list(self.rows)}


@pytest.fixture
def web_env(monkeypatch):
    s = cws.settings
    monkeypatch.setattr(s, "BRAVE_SEARCH_API_KEY", "test-key")
    monkeypatch.setattr(s, "CHAT_REPORT_WEB_SEARCH_ENABLED", True)
    monkeypatch.setattr(s, "CHAT_REPORT_WEB_SEARCH_USER_DAILY_CAP", 10)
    monkeypatch.setattr(s, "CHAT_REPORT_WEB_SEARCH_DAILY_CAP", 500)
    monkeypatch.setattr(s, "CHAT_REPORT_WEB_SEARCH_CACHE_TTL_SECONDS", 120)
    monkeypatch.setattr(cws, "_cache", {})
    monkeypatch.setattr(cws, "_inflight", {})
    led = _Ledger()
    monkeypatch.setattr(cmt, "get_chat_budget_service", lambda: led)
    brave = _Brave()
    monkeypatch.setattr(cws.brave_search, "web_search", brave)
    return led, brave


def _svc(monkeypatch) -> ChatService:
    import app.services.chat_context_resolver as res
    monkeypatch.setattr(res, "get_chat_context_resolver",
                        lambda: SimpleNamespace(resolve=AsyncMock(return_value=None)))
    svc = ChatService.__new__(ChatService)
    svc.supabase = object()
    svc.fmp = object()
    svc._get_recent_messages = lambda *a, **k: []
    svc._retrieve_context = AsyncMock(return_value=([], []))
    svc._condense_history = AsyncMock(return_value="")
    svc._detect_asset_type = lambda *a, **k: "STOCK"
    svc._get_profit_summary = AsyncMock(return_value=None)
    svc._get_snapshot_summary = AsyncMock(return_value=None)
    svc._get_company_profile_summary = AsyncMock(return_value=None)
    svc._is_deep_dive_request = lambda *a, **k: False
    svc._deterministic_widget = AsyncMock(return_value=None)
    return svc


def _declared(tools) -> set:
    return {fd.name for t in (tools or []) for fd in (t.function_declarations or [])}


class _Gem:
    """`generate_with_tools` that records what it was given and optionally calls the web tool
    (the way the real one runs a handler) before answering or raising."""

    def __init__(self, *, call_web: bool = False, raise_after: bool = False, query: str = "Apple DOJ"):
        self.kw: Dict[str, Any] = {}
        self.call_web = call_web
        self.raise_after = raise_after
        self.query = query
        self.text_calls: List[Dict[str, Any]] = []
        self.web_results: List[Dict[str, Any]] = []

    async def generate_with_tools(self, **kw):
        self.kw = kw
        results, errors = [], []
        if self.call_web:
            res = await kw["tool_handlers"]["web_search"]({"query": self.query})
            self.web_results.append(res)
            (errors if isinstance(res, dict) and res.get("error") else results).append(
                res if not (isinstance(res, dict) and res.get("error"))
                else {"name": "web_search", "error": res["error"], "upstream": bool(res.get("upstream"))})
        if self.raise_after:
            raise RuntimeError("function calling exploded after the tool ran")
        return {"text": "Reuters, Sep 30, 2026: the case advanced.", "tokens_used": 40,
                "tool_results": results, "tool_errors": errors, "finish_reason": "STOP"}

    async def generate_text(self, **kw):
        self.text_calls.append(kw)
        return {"text": "plain answer from the report", "tokens_used": 12, "finish_reason": "STOP"}


async def _gen(svc, msg=WEB_MSG, session_type="REPORT", context_type="TICKER_REPORT", user_id=UID, **kw):
    return await svc.generate_response(
        "sess-1", msg, session_type=session_type, stock_id="AAPL", context_type=context_type,
        reference_id="AAPL", user_id=user_id, **kw)


# ── generate_response: the gate ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_report_web_ask_declares_and_handles_the_tool(web_env, monkeypatch):
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    out = await _gen(svc)
    assert chat_tools.WEB_SEARCH_TOOL in _declared(svc.gemini.kw["tools"])
    assert chat_tools.WEB_SEARCH_TOOL in svc.gemini.kw["tool_handlers"]
    assert re.search(r"\bweb_search\b", svc.gemini.kw["system_instruction"])
    assert out["web_search_used"] is False and out["web_sources"] == [], "declared but not called"


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    dict(msg="what is the moat?"), dict(session_type="NORMAL"), dict(user_id=None),
    dict(context_type="ETF"), dict(context_type="STOCK"),
])
async def test_no_gate_no_tool(web_env, monkeypatch, override):
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    out = await _gen(svc, **override)
    assert chat_tools.WEB_SEARCH_TOOL not in _declared(svc.gemini.kw["tools"])
    assert chat_tools.WEB_SEARCH_TOOL not in svc.gemini.kw["tool_handlers"]
    assert "web_search" not in svc.gemini.kw["system_instruction"]
    assert out["web_search_used"] is False and out["web_sources"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("setting,value", [("BRAVE_SEARCH_API_KEY", ""),
                                           ("CHAT_REPORT_WEB_SEARCH_ENABLED", False)])
async def test_no_key_or_switch_off_means_no_tool(web_env, monkeypatch, setting, value):
    monkeypatch.setattr(cws.settings, setting, value)
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    await _gen(svc)
    assert chat_tools.WEB_SEARCH_TOOL not in svc.gemini.kw["tool_handlers"]


# ── generate_response: web_search_used ────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_search_used_only_when_results_reached_the_answer(web_env, monkeypatch):
    led, brave = web_env
    svc = _svc(monkeypatch)
    svc.gemini = _Gem(call_web=True)
    out = await _gen(svc)
    assert out["web_search_used"] is True and len(brave.calls) == 1
    assert [p["detail"] for p in out["web_sources"]] == ["Reuters"]
    assert out["web_sources"][0]["url"].startswith("https://")
    assert "degraded" not in out


@pytest.mark.asyncio
async def test_a_failed_tool_round_is_not_web_used_even_though_the_search_ran(web_env, monkeypatch):
    _, brave = web_env
    svc = _svc(monkeypatch)
    svc.gemini = _Gem(call_web=True, raise_after=True)
    out = await _gen(svc)
    assert len(brave.calls) == 1, "the search ran"
    assert out["web_search_used"] is False and out["web_sources"] == []
    assert out["degraded"] == "no_tools"
    # The tool-less fallback claims no tool, web_search included.
    assert "web_search" not in svc.gemini.text_calls[0]["system_instruction"]


@pytest.mark.asyncio
async def test_a_capped_search_is_charged_and_not_web_used(web_env, monkeypatch):
    led, brave = web_env
    monkeypatch.setattr(cws.settings, "CHAT_REPORT_WEB_SEARCH_USER_DAILY_CAP", 0)
    svc = _svc(monkeypatch)
    svc.gemini = _Gem(call_web=True)
    out = await _gen(svc)
    assert out["web_search_used"] is False and brave.calls == []
    assert "degraded" not in out, "a capped search stays charged — one credit buys one answer"


@pytest.mark.asyncio
async def test_a_search_outage_as_the_only_tool_settles_degraded(web_env, monkeypatch):
    led, _ = web_env

    def _down(*a, **k):
        raise cmt.ChatBudgetUnavailable("db down")
    monkeypatch.setattr(cmt, "get_chat_budget_service", lambda: SimpleNamespace(try_claim_turn=_down))
    svc = _svc(monkeypatch)
    svc.gemini = _Gem(call_web=True)
    out = await _gen(svc)
    assert out["web_search_used"] is False and out["degraded"] == "no_tools"


@pytest.mark.asyncio
async def test_the_fallback_reuses_the_streams_search(web_env, monkeypatch):
    led, brave = web_env
    turn = cws.open_web_search_turn("REPORT", "TICKER_REPORT", WEB_MSG, UID, "AAPL", session_id="sess-1")
    first = await cws.run_web_search(turn, "Apple DOJ case")
    assert first["status"] == "ok" and len(brave.calls) == 1
    claims_before = list(led.claims)

    svc = _svc(monkeypatch)
    svc.gemini = _Gem(call_web=True, query="a different query entirely")
    out = await _gen(svc, web_turn=turn)
    assert len(brave.calls) == 1 and led.claims == claims_before, "no second claim, no second call"
    assert out["web_search_used"] is True
    # A new generation: the fallback's first call is not told it already searched…
    assert svc.gemini.web_results[0]["status"] == "ok"
    assert "repeat_note" not in svc.gemini.web_results[0]
    # …but its OWN second call is a repeat.
    res = await svc.gemini.kw["tool_handlers"][chat_tools.WEB_SEARCH_TOOL]({"query": "x y"})
    assert "repeat_note" in res and len(brave.calls) == 1


@pytest.mark.asyncio
async def test_a_handed_in_turn_wins_over_the_door_gate(web_env, monkeypatch):
    """The fallback's message is the same, but even a closed gate cannot drop the stream's turn."""
    turn = cws.WebSearchTurn(user_id=UID)
    monkeypatch.setattr(cws.settings, "CHAT_REPORT_WEB_SEARCH_ENABLED", False)
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    await _gen(svc, web_turn=turn)
    assert chat_tools.WEB_SEARCH_TOOL in svc.gemini.kw["tool_handlers"]


@pytest.mark.asyncio
async def test_a_web_answer_never_enters_the_deep_dive_cache(web_env, monkeypatch):
    """Forces `is_deep_dive` and a cacheable context — otherwise the gate is vacuous for a report
    chat, which runs as STOCK (review 2026-10-02 #18)."""
    async def _resolve(*a, **k):
        return "SERVER BLOCK", True, False, True, None

    for call_web, expected_writes in ((True, 0), (False, 1)):
        svc = _svc(monkeypatch)
        svc._resolve_grounding = _resolve
        svc._is_deep_dive_request = lambda *a, **k: True
        svc._check_deep_dive_cache = lambda *a, **k: None
        svc._deep_dive_cacheable = lambda **k: True
        writes: list = []
        svc._upsert_deep_dive_cache = lambda *a, **k: writes.append(a)
        gem = _Gem(call_web=call_web)
        gem_text = "x" * 200

        async def _gwt(_g=gem, **kw):
            out = await _Gem.generate_with_tools(_g, **kw)
            out["text"] = gem_text
            return out
        gem.generate_with_tools = _gwt
        svc.gemini = gem
        cws._cache.clear()
        await _gen(svc, msg=f"{WEB_MSG} {call_web}")
        assert len(writes) == expected_writes, (call_web, writes)


@pytest.mark.asyncio
async def test_a_deep_dive_cache_hit_reports_no_web_use(web_env, monkeypatch):
    async def _resolve(*a, **k):
        return "SERVER BLOCK", True, False, True, None
    svc = _svc(monkeypatch)
    svc._resolve_grounding = _resolve
    svc._is_deep_dive_request = lambda *a, **k: True
    svc._check_deep_dive_cache = lambda *a, **k: "cached brief"
    svc.gemini = _Gem()
    out = await _gen(svc)
    assert out["content"] == "cached brief"
    assert out["web_search_used"] is False and out["web_sources"] == []


# ── prepare_stream_generation ─────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("msg,granted", [(WEB_MSG, True), ("what is the moat?", False)])
async def test_prep_exposes_one_gate_decision(web_env, monkeypatch, msg, granted):
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    prep = await svc.prepare_stream_generation(
        "sess-1", msg, session_type="REPORT", stock_id="AAPL", context_type="TICKER_REPORT",
        reference_id="AAPL", user_id=UID)
    assert prep["web_search_granted"] is granted
    assert (prep["web_turn"] is not None) is granted
    if granted:
        assert isinstance(prep["web_turn"], cws.WebSearchTurn) and prep["web_turn"].user_id == UID
    assert bool(re.search(r"\bweb_search\b", prep["system_instruction"])) is granted
    assert "web_search" not in prep["system_instruction_no_tools"]


# ── stream_synthesis: two specialists, one search ─────────────────────────────


@pytest.mark.asyncio
async def test_two_specialists_share_one_search(web_env, monkeypatch):
    led, brave = web_env

    _queries = iter(["Apple DOJ case", "Apple antitrust lawsuit", "spare", "spare2"])

    class _SynthGem:
        def stream_agentic(self, prompt, tools=None, tool_handlers=None, **kw):
            async def _g():
                yield "tool_start", {"name": "web_search"}      # consumers must ignore it
                res = await tool_handlers["web_search"]({"query": next(_queries)})
                yield "tool", {"name": "web_search", "args": {}, "result": res}
                yield "answer", "a specialist answer"
            return _g()

        def stream_text(self, prompt, **kw):
            async def _g():
                yield "answer", "merged"
            return _g()

    svc = ChatService.__new__(ChatService)
    svc.gemini = _SynthGem()
    turn = cws.open_web_search_turn("REPORT", "TICKER_REPORT", WEB_MSG, UID)
    handlers = chat_tools.build_chat_tool_handlers(svc, user_id=UID, web_turn=turn)
    route = {"specialists": ["valuation", "moat"], "mode": "synthesize", "labels": ["Valuation", "Moat"]}
    prep = {"prompt": "p", "system_instruction": "s", "system_instruction_no_tools": "s"}
    events = [ev async for ev in svc.stream_synthesis(prep, WEB_MSG, route, [], handlers)]
    assert len(brave.calls) == 1
    assert sorted(led.claims) == sorted([cws._user_report_web_search_bucket(UID), cws._REPORT_WEB_SEARCH_BUCKET])
    assert [k for k, _ in events].count("tool") == 2
    assert "tool_start" not in [k for k, _ in events], "synthesis consumes the specialists' events"


# ── the stream door (source-scan) ─────────────────────────────────────────────


def _event_gen_source() -> str:
    path = Path(__file__).resolve().parents[1] / "app/api/v1/endpoints/chat.py"
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    outer = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.AsyncFunctionDef) and n.name == "stream_chat_message")
    inner = next(n for n in ast.walk(outer)
                 if isinstance(n, ast.AsyncFunctionDef) and n.name == "event_gen")
    return _strip_comments(ast.get_source_segment(src, inner))


def _strip_comments(src: str) -> str:
    """String-aware: `tokenize` drops only COMMENT tokens, never a `#` inside a string."""
    lines = src.splitlines(keepends=True)
    out = list(lines)
    toks = tokenize.generate_tokens(io.StringIO(src).readline)
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            (row, col), _ = tok.start, tok.end
            line = out[row - 1]
            out[row - 1] = line[:col] + line[col + len(tok.string):]
    return "".join(out)


def _call_args(src: str, callee: str) -> List[str]:
    """The argument text of every `callee(` call, bounded by its balanced parentheses."""
    found = []
    for m in re.finditer(re.escape(callee) + r"\(", src):
        depth, i = 1, m.end()
        while i < len(src) and depth:
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            i += 1
        found.append(src[m.end(): i - 1])
    return found


def test_the_comment_stripper_is_string_aware_and_strips_comments():
    s = _strip_comments('x = "a # not a comment"  # a comment\n# whole line\ny = 1\n')
    assert '"a # not a comment"' in s and "a comment\n" not in s and "whole line" not in s


def test_single_mode_is_decided_before_the_routing_frame():
    body = _event_gen_source()
    collapse = body.index("route = single_lens_route(route)")
    assert collapse < body.index('_sse("routing"'), "a web turn must never promise lenses"
    assert collapse < body.index('_sse("sources"')
    assert re.search(r"web_turn\s*=\s*prep\.get\(\"web_turn\"\)\s*\n\s*if web_turn is not None:\s*\n"
                     r"\s*route = single_lens_route\(route\)", body)


def test_the_turn_reaches_the_handlers_the_declarations_and_the_fallback():
    body = _event_gen_source()
    (handlers,) = _call_args(body, "build_chat_tool_handlers")
    assert re.search(r"\bweb_turn=web_turn\b", handlers) and 'user_id=user["id"]' in handlers
    (decls,) = _call_args(body, "build_chat_tool_declarations")
    assert "web_search=web_turn is not None" in decls
    assert any("web_search=web_turn is not None" in a for a in _call_args(body, "tools_for_asset_type"))
    fallback = [a for a in _call_args(body, "chat_service.generate_response")]
    assert len(fallback) == 1 and re.search(r"\bweb_turn=web_turn\b", fallback[0])
    assert re.search(r"web_search_used = bool\(ai_result\.get\(\"web_search_used\"\)\)", body)


def test_no_auto_continuation_and_no_deep_dive_write_on_a_web_turn():
    body = _event_gen_source()
    cont = body.index("is_length_cut(")
    cont_cond = body[cont: body.index("):", cont)]
    # Keyed on results REACHING the model, never on the gate opening (review 2026-10-02 MED:
    # `web_turn is None` made every cut web-intent answer settle free as truncated).
    assert "not web_search_used" in cont_cond and "web_turn" not in cont_cond
    write = body.index("chat_service._upsert_deep_dive_cache")
    write_cond = body[body.rindex("if (", 0, write): write]
    assert "not web_search_used" in write_cond


def test_web_search_used_comes_from_the_tool_event():
    body = _event_gen_source()
    assert re.search(r'payload\.get\("name"\) == WEB_SEARCH_TOOL and web_results_delivered\(_res\)',
                     body)
    # Bound before the try, so the fallback and the persist block can read it.
    assert body.index("web_search_used = False") < body.index("try:")
    assert body.index("web_turn = None") < body.index("try:")


def test_tool_start_is_consumed_before_the_stream_counts_as_started():
    body = _event_gen_source()
    start = body.index('if kind == "tool_start":')
    assert body.index("continue", start) < body.index("streamed_any = True", start)
