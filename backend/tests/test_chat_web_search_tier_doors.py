"""The 2026-10-08 web tiers through BOTH chat doors and the stream→non-stream fallback.

* `generate_response` (the send door and the fallback): what is declared (the tier's description),
  what round 1 must call (`force_first_tool`: an explicit ask → the search; a news ask → Caydex's
  licensed headlines; the automatic tier → nothing), the send door's extra round for an automatic
  web call (`extra_round_tools`), the tier's prompt rule, and `web_search_automatic`.
* `prepare_stream_generation`: the same decision, plus the no-web instructions an automatic web turn
  needs when the endpoint routes it to a synthesis.
* A handed-in (fallback) turn keeps its tier; consent / master / Learn / deep-dive gates hold on
  both doors; shadow mode changes nothing a door sends.

Hermetic: a ledger fake, a Brave stub, the context resolver stubbed, every switch set explicitly.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from app.core import client_app_version as cav
from app.services import chat_market_tools as cmt
from app.services import chat_web_search_service as cws
from app.services.agents import chat_tools
from app.services.chat_service import ChatService

UID = "user-tier-door-0001"
ASK = "Can you search the web for the Apple DOJ case?"
NEWS = "What's the latest news on Apple?"
PLAIN = "Any lawsuits against Apple?"


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
    def __init__(self):
        self.calls: List[str] = []

    async def __call__(self, query, **kw):
        self.calls.append(query)
        return {"results": [{"title": "Apple faces DOJ case", "url": "https://www.reuters.com/legal/a/",
                             "description": "The case moved forward.", "page_age": "2026-09-30T00:00:00"}]}


@pytest.fixture
def env(monkeypatch):
    s = cws.settings
    monkeypatch.setattr(s, "BRAVE_SEARCH_API_KEY", "test-key")
    monkeypatch.setattr(s, "CHAT_REPORT_WEB_SEARCH_ENABLED", True)
    monkeypatch.setattr(s, "CHAT_WEB_SEARCH_ALL_CHATS_ENABLED", True)
    monkeypatch.setattr(s, "CHAT_AUTO_WEB_SEARCH_MODE", "on")
    monkeypatch.setattr(s, "CHAT_REPORT_WEB_SEARCH_DAILY_CAP", 500)
    monkeypatch.setattr(s, "CHAT_AUTO_WEB_SEARCH_DAILY_CAP", 100)
    monkeypatch.setattr(s, "CHAT_AUTO_WEB_SEARCH_PER_ACCOUNT_DAILY", 5)
    monkeypatch.setattr(s, "CHAT_AUTO_WEB_SEARCH_ACCOUNT_ALLOWLIST", "")
    monkeypatch.setattr(s, "CHAT_WEB_SEARCH_MIN_CONSENT_VERSION", 3)
    monkeypatch.setattr(s, "CHAT_REPORT_WEB_SEARCH_CACHE_TTL_SECONDS", 120)
    monkeypatch.setattr(cws, "_cache", {})
    monkeypatch.setattr(cws, "_inflight", {})
    monkeypatch.setattr(cws, "_auto_latch", {})
    led = _Ledger()
    monkeypatch.setattr(cmt, "get_chat_budget_service", lambda: led)
    brave = _Brave()
    monkeypatch.setattr(cws.brave_search, "web_search", brave)
    t1 = cws._client_ai_consent.set("3")
    t2 = cav._client_app_version.set("1.1")
    yield led, brave
    cws._client_ai_consent.reset(t1)
    cav._client_app_version.reset(t2)


def _svc(monkeypatch, asset_type="STOCK", deep_dive=False) -> ChatService:
    import app.services.chat_context_resolver as res
    monkeypatch.setattr(res, "get_chat_context_resolver",
                        lambda: SimpleNamespace(resolve=AsyncMock(return_value=None)))
    svc = ChatService.__new__(ChatService)
    svc.supabase = object()
    svc.fmp = object()
    svc._get_recent_messages = lambda *a, **k: []
    svc._retrieve_context = AsyncMock(return_value=([], []))
    svc._condense_history = AsyncMock(return_value="")
    svc._detect_asset_type = lambda *a, **k: asset_type
    svc._get_profit_summary = AsyncMock(return_value=None)
    svc._get_snapshot_summary = AsyncMock(return_value=None)
    svc._get_company_profile_summary = AsyncMock(return_value=None)
    svc._is_deep_dive_request = lambda *a, **k: deep_dive
    svc._check_deep_dive_cache = lambda *a, **k: None
    svc._deterministic_widget = AsyncMock(return_value=None)
    return svc


def _decls(tools) -> Dict[str, str]:
    return {fd.name: fd.description for t in (tools or []) for fd in (t.function_declarations or [])}


class _Gem:
    def __init__(self, *, call_web=False):
        self.kw: Dict[str, Any] = {}
        self.call_web = call_web

    async def generate_with_tools(self, **kw):
        self.kw = kw
        results = []
        if self.call_web:
            res = await kw["tool_handlers"]["web_search"]({"query": "Apple DOJ lawsuit"})
            results.append(res)
        return {"text": "Reuters, Sep 30, 2026: the case advanced.", "tokens_used": 40,
                "tool_results": results, "tool_errors": [], "finish_reason": "STOP"}

    async def generate_text(self, **kw):
        return {"text": "plain", "tokens_used": 1, "finish_reason": "STOP"}


async def _gen(svc, msg, session_type="NORMAL", context_type="STOCK", **kw):
    return await svc.generate_response(
        "sess-1", msg, session_type=session_type, stock_id="AAPL", context_type=context_type,
        reference_id="AAPL", user_id=kw.pop("user_id", UID), **kw)


# ── the send door ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_explicit_ask_in_a_stock_chat_forces_the_search(env, monkeypatch):
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    await _gen(svc, ASK)
    kw = svc.gemini.kw
    assert _decls(kw["tools"])["web_search"] == chat_tools.TOOL_DESCRIPTIONS["web_search"]
    assert kw["force_first_tool"] == "web_search" and kw["extra_round_tools"] is None
    assert ChatService._WEB_RESULTS_RULE_GENERAL in kw["system_instruction"]
    assert chat_tools.WEB_SEARCH_TIER_CAPABILITIES["explicit"] in kw["system_instruction"]


@pytest.mark.asyncio
@pytest.mark.parametrize("st,ct", [("REPORT", "TICKER_REPORT"), ("NORMAL", "STOCK")])
async def test_a_news_ask_forces_licensed_headlines_first(env, monkeypatch, st, ct):
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    await _gen(svc, NEWS, session_type=st, context_type=ct)
    kw = svc.gemini.kw
    assert kw["force_first_tool"] == ("get_ticker_news", "explain_price_move")
    assert _decls(kw["tools"])["web_search"] == chat_tools.WEB_SEARCH_TIER_DESCRIPTIONS["news"]
    report = st == "REPORT"
    news_rule = ChatService._WEB_NEWS_RULE if report else ChatService._WEB_NEWS_RULE_GENERAL
    results_rule = ChatService._WEB_RESULTS_RULE if report else ChatService._WEB_RESULTS_RULE_GENERAL
    assert news_rule in kw["system_instruction"]
    assert results_rule not in kw["system_instruction"]


@pytest.mark.asyncio
async def test_a_news_ask_in_an_index_chat_reads_the_market_wide_news_first(env, monkeypatch):
    """An INDEX chat has no headline tool, but it has the market snapshot (the Updates market card
    and its catalyst): that is the licensed news a news ask reads first — never the web."""
    svc = _svc(monkeypatch, asset_type="INDEX")
    svc.gemini = _Gem()
    await svc.generate_response("sess-1", "What's the latest news on the S&P 500?",
                                session_type="NORMAL", stock_id="^GSPC", context_type="INDEX",
                                reference_id="^GSPC", user_id=UID)
    kw = svc.gemini.kw
    assert kw["force_first_tool"] == ("get_market_snapshot",)
    assert _decls(kw["tools"])["web_search"] == chat_tools.WEB_SEARCH_TIER_DESCRIPTIONS["news"]
    assert ChatService._WEB_NEWS_RULE_GENERAL in kw["system_instruction"]


@pytest.mark.asyncio
async def test_a_news_ask_forced_to_the_web_is_described_and_ruled_as_an_explicit_search(env, monkeypatch):
    """Review 2026-10-09: when no licensed news tool is granted, round 1 IS the web search — and
    the description, capability line and rule must not say "the licensed headlines were fetched
    first" (none were). Both doors."""
    monkeypatch.setitem(chat_tools._TOOLS_BY_ASSET_TYPE, "INDEX", frozenset({"get_market_overview"}))
    svc = _svc(monkeypatch, asset_type="INDEX")
    svc.gemini = _Gem()
    msg = "What's the latest news on the S&P 500?"
    await svc.generate_response("sess-1", msg, session_type="NORMAL", stock_id="^GSPC",
                                context_type="INDEX", reference_id="^GSPC", user_id=UID)
    kw = svc.gemini.kw
    assert kw["force_first_tool"] == "web_search"
    assert _decls(kw["tools"])["web_search"] == chat_tools.TOOL_DESCRIPTIONS["web_search"]
    instr = kw["system_instruction"]
    assert ChatService._WEB_RESULTS_RULE_GENERAL in instr and ChatService._WEB_NEWS_RULE_GENERAL not in instr
    assert chat_tools.WEB_SEARCH_TIER_CAPABILITIES["explicit"] in instr
    assert chat_tools.WEB_SEARCH_TIER_CAPABILITIES["news"] not in instr
    prep = await svc.prepare_stream_generation(
        "sess-1", msg, session_type="NORMAL", stock_id="^GSPC", context_type="INDEX",
        reference_id="^GSPC", user_id=UID)
    assert prep["web_force_first"] == "web_search" and prep["web_search_mode"] == "explicit"
    assert ChatService._WEB_NEWS_RULE_GENERAL not in prep["system_instruction"]


@pytest.mark.asyncio
@pytest.mark.parametrize("st,ct", [("NORMAL", None), ("NORMAL", "GENERAL"), ("BOOK", "BOOK"),
                                   ("CONCEPT", "MONEY_MOVES_ARTICLE"), ("JOURNEY", "JOURNEY_LESSON")])
async def test_a_news_ask_with_no_subject_company_is_offered_the_market_wide_news(env, monkeypatch, st, ct):
    """Review 2026-10-09: "Any news today?" in a general or a Learn chat (no stock id) was forced
    to the two TICKER-only headline tools. The market snapshot is offered too, so a forced call
    never has to invent a ticker. Both doors."""
    svc = _svc(monkeypatch, asset_type="NORMAL")
    svc.gemini = _Gem()
    await svc.generate_response("sess-1", "Any news today?", session_type=st, stock_id=None,
                                context_type=ct, reference_id="1", user_id=UID)
    forced = svc.gemini.kw["force_first_tool"]
    assert isinstance(forced, tuple) and "get_market_snapshot" in forced
    assert ChatService._WEB_NEWS_RULE_GENERAL in svc.gemini.kw["system_instruction"]
    prep = await svc.prepare_stream_generation(
        "sess-1", "Any news today?", session_type=st, stock_id=None, context_type=ct,
        reference_id="1", user_id=UID)
    assert prep["web_turn"].ticker is None and "get_market_snapshot" in prep["web_force_first"]


@pytest.mark.asyncio
@pytest.mark.parametrize("st,ct,msg", [
    ("REPORT", "TICKER_REPORT", "search the web for the euro dollar rate"),
    ("NORMAL", "STOCK", "look up where bitcoin is at right now"),
])
async def test_a_market_data_ask_keeps_the_tool_but_is_never_forced_to_the_web(env, monkeypatch, st, ct, msg):
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    await _gen(svc, msg, session_type=st, context_type=ct)
    kw = svc.gemini.kw
    assert "web_search" in kw["tool_handlers"] and kw["force_first_tool"] is None
    prep = await svc.prepare_stream_generation(
        "sess-1", msg, session_type=st, stock_id="AAPL", context_type=ct, reference_id="AAPL",
        user_id=UID)
    assert prep["web_turn"] is not None and prep["web_force_first"] is None


@pytest.mark.asyncio
async def test_a_report_question_is_not_a_news_ask(env, monkeypatch):
    """Review 2026-10-09 (with only the master switch on — production's state): "What's new in
    this report?" opened the report tier, collapsed the lenses and forced the headlines."""
    monkeypatch.setattr(cws.settings, "CHAT_WEB_SEARCH_ALL_CHATS_ENABLED", False)
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_MODE", "off")
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    for msg in ("What's new in this report?", "What is new in the latest 10-K?",
                "What's new with the moat score since last quarter?"):
        await _gen(svc, msg, session_type="REPORT", context_type="TICKER_REPORT")
        kw = svc.gemini.kw
        assert "web_search" not in kw["tool_handlers"] and kw["force_first_tool"] is None, msg
        assert ChatService._WEB_ON_REQUEST_RULE in kw["system_instruction"]


@pytest.mark.asyncio
async def test_an_automatic_news_ask_reads_the_licensed_headlines_first(env, monkeypatch):
    """With every-chat search off and the automatic tier on, a news ask falls to the automatic
    tier — and still reads the licensed headlines first (owner decision 2026-10-08)."""
    monkeypatch.setattr(cws.settings, "CHAT_WEB_SEARCH_ALL_CHATS_ENABLED", False)
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    await _gen(svc, NEWS)
    kw = svc.gemini.kw
    assert kw["force_first_tool"] == ("get_ticker_news", "explain_price_move")
    assert _decls(kw["tools"])["web_search"] == chat_tools.WEB_SEARCH_TIER_DESCRIPTIONS["auto"]


@pytest.mark.asyncio
async def test_an_asked_search_on_the_automatic_tier_is_not_captioned_automatic(env, monkeypatch):
    """Review 2026-10-09: with every-chat search off, an explicit ask reaches the automatic tier —
    and its answer was stored with "Cay AI searched the web because Caydex's data did not cover
    this", although the USER asked for the search."""
    monkeypatch.setattr(cws.settings, "CHAT_WEB_SEARCH_ALL_CHATS_ENABLED", False)
    svc = _svc(monkeypatch)
    svc.gemini = _Gem(call_web=True)
    out = await _gen(svc, ASK)
    assert out["web_search_used"] is True and out["web_search_automatic"] is False


@pytest.mark.asyncio
async def test_the_automatic_tier_is_unforced_with_its_own_description_rule_and_extra_round(env, monkeypatch):
    led, brave = env
    svc = _svc(monkeypatch)
    svc.gemini = _Gem(call_web=True)
    out = await _gen(svc, PLAIN)
    kw = svc.gemini.kw
    assert kw["force_first_tool"] is None
    assert kw["extra_round_tools"] == frozenset({"web_search"})
    assert _decls(kw["tools"])["web_search"] == chat_tools.WEB_SEARCH_TIER_DESCRIPTIONS["auto"]
    instr = kw["system_instruction"]
    assert ChatService._AUTO_WEB_RULE_GENERAL in instr and ChatService._KNOWLEDGE_AUTO_WEB_CLAUSE in instr
    assert chat_tools.WEB_SEARCH_TIER_CAPABILITIES["auto"] in instr
    assert out["web_search_used"] is True and out["web_search_automatic"] is True
    assert len(led.claims) == 3 and len(brave.calls) == 1


@pytest.mark.asyncio
async def test_an_explicit_search_that_reached_the_answer_is_not_automatic(env, monkeypatch):
    svc = _svc(monkeypatch)
    svc.gemini = _Gem(call_web=True)
    out = await _gen(svc, ASK)
    assert out["web_search_used"] is True and out["web_search_automatic"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("consent", [None, "2", "garbage", "3.0"])
async def test_without_consent_v3_a_stock_chat_gets_no_tool_and_the_honest_line(env, monkeypatch, consent):
    cws.set_client_ai_consent_version(consent)
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    for msg, line in ((ASK, ChatService._WEB_UNAVAILABLE_RULE), (PLAIN, ChatService._WEB_NONE_RULE)):
        await _gen(svc, msg)
        kw = svc.gemini.kw
        assert "web_search" not in kw["tool_handlers"] and kw["force_first_tool"] is None
        assert kw["system_instruction"].count(line) == 1, msg
        assert "web_search" not in kw["system_instruction"]


@pytest.mark.asyncio
async def test_the_master_switch_off_closes_both_doors_with_every_tier_on(env, monkeypatch):
    monkeypatch.setattr(cws.settings, "CHAT_REPORT_WEB_SEARCH_ENABLED", False)
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    for msg in (ASK, NEWS, PLAIN):
        await _gen(svc, msg)
        assert "web_search" not in svc.gemini.kw["tool_handlers"]
        prep = await svc.prepare_stream_generation(
            "sess-1", msg, session_type="NORMAL", stock_id="AAPL", context_type="STOCK",
            reference_id="AAPL", user_id=UID)
        assert prep["web_turn"] is None and prep["web_force_first"] is None
        assert "web_search" not in prep["system_instruction"]


@pytest.mark.asyncio
async def test_a_learn_chat_gets_explicit_search_but_never_the_automatic_tier(env, monkeypatch):
    svc = _svc(monkeypatch, asset_type="NORMAL")
    svc.gemini = _Gem()
    await svc.generate_response("sess-1", PLAIN, session_type="BOOK", context_type="BOOK",
                                reference_id="1", user_id=UID)
    assert "web_search" not in svc.gemini.kw["tool_handlers"]
    assert ChatService._WEB_ON_REQUEST_RULE in svc.gemini.kw["system_instruction"]
    await svc.generate_response("sess-1", ASK, session_type="BOOK", context_type="BOOK",
                                reference_id="1", user_id=UID)
    assert svc.gemini.kw["force_first_tool"] == "web_search"


@pytest.mark.asyncio
async def test_a_deep_dive_never_gets_the_automatic_tier(env, monkeypatch):
    svc = _svc(monkeypatch, asset_type="ETF", deep_dive=True)
    svc.gemini = _Gem()
    await _gen(svc, PLAIN, context_type="ETF")
    assert "web_search" not in svc.gemini.kw["tool_handlers"]


@pytest.mark.asyncio
async def test_a_market_data_question_gets_no_automatic_search(env, monkeypatch):
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    await _gen(svc, "What's Apple's stock price today?")
    assert "web_search" not in svc.gemini.kw["tool_handlers"]
    assert ChatService._WEB_ON_REQUEST_RULE in svc.gemini.kw["system_instruction"]


@pytest.mark.asyncio
async def test_the_fallback_keeps_the_streams_tier(env, monkeypatch):
    led, brave = env
    auto = cws.open_web_search_turn("NORMAL", "STOCK", PLAIN, UID, "AAPL", session_id="sess-1")
    assert auto.tier == cws.TIER_AUTO
    await cws.run_web_search(auto, "Apple DOJ lawsuit")
    claims = list(led.claims)
    # Even with a message that would open a different tier on its own, the handed-in turn rules.
    svc = _svc(monkeypatch)
    svc.gemini = _Gem(call_web=True)
    out = await _gen(svc, ASK, web_turn=auto)
    kw = svc.gemini.kw
    assert kw["force_first_tool"] is None and kw["extra_round_tools"] == frozenset({"web_search"})
    assert ChatService._AUTO_WEB_RULE_GENERAL in kw["system_instruction"]
    assert led.claims == claims and len(brave.calls) == 1, "one search per turn"
    assert out["web_search_automatic"] is True


@pytest.mark.asyncio
async def test_a_fallback_handed_a_dropped_decision_never_re_decides(env, monkeypatch):
    """Review 2026-10-09: an automatic turn routed to a synthesis drops its search; when its stream
    then fails, the fallback was handed `web_turn=None`, re-decided, re-granted the automatic tier
    and could claim units on a turn already logged AUTO_WEB_DROPPED. Handed the dropped decision,
    it declares no tool, claims nothing and logs nothing."""
    led, brave = env
    svc = _svc(monkeypatch)
    svc.gemini = _Gem(call_web=False)
    prep = await _prep(svc, PLAIN)
    assert prep["web_turn"].tier == "auto" and isinstance(prep["web_decision"], cws.WebSearchDecision)
    dropped = cws.decision_without_web(prep["web_decision"])
    calls = []
    real = cws.decide_web_search
    monkeypatch.setattr("app.services.chat_service.decide_web_search",
                        lambda *a, **k: calls.append(a) or real(*a, **k))
    await _gen(svc, PLAIN, web_decision=dropped)
    kw = svc.gemini.kw
    assert calls == [], "the fallback must not decide the turn a second time"
    assert "web_search" not in kw["tool_handlers"] and kw["force_first_tool"] is None
    instr = kw["system_instruction"]
    assert ChatService._AUTO_WEB_RULE_GENERAL not in instr and instr.count("WEB SEARCH:") == 1
    assert ChatService._WEB_ON_REQUEST_RULE in instr       # every-chat search is open here
    assert led.claims == [] and brave.calls == []
    # A GRANTED decision with no turn to carry it is closed the same way (never a search).
    await _gen(svc, PLAIN, web_decision=prep["web_decision"])
    assert "web_search" not in svc.gemini.kw["tool_handlers"] and calls == []


@pytest.mark.asyncio
async def test_a_fallback_logs_no_second_shadow_line(env, monkeypatch, caplog):
    import logging
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_MODE", "shadow")
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    with caplog.at_level(logging.INFO, logger=cws.logger.name):
        prep = await _prep(svc, PLAIN)
        await _gen(svc, PLAIN, web_decision=prep["web_decision"])
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("AUTO_WEB_SHADOW")]
    assert len(lines) == 1, lines


@pytest.mark.asyncio
async def test_shadow_mode_changes_nothing_a_door_sends(env, monkeypatch):
    led, brave = env
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_MODE", "shadow")
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    await _gen(svc, PLAIN)
    kw = svc.gemini.kw
    assert "web_search" not in kw["tool_handlers"] and kw["force_first_tool"] is None
    assert ChatService._AUTO_WEB_RULE_GENERAL not in kw["system_instruction"]
    assert led.claims == [] and brave.calls == []


# ── the stream door's prep ────────────────────────────────────────────────────


async def _prep(svc, msg, st="NORMAL", ct="STOCK"):
    return await svc.prepare_stream_generation(
        "sess-1", msg, session_type=st, stock_id="AAPL", context_type=ct, reference_id="AAPL",
        user_id=UID)


@pytest.mark.asyncio
@pytest.mark.parametrize("msg,tier,force,mode", [
    (ASK, "explicit", "web_search", "explicit"),
    (NEWS, "explicit", ("get_ticker_news", "explain_price_move"), "news"),
    (PLAIN, "auto", None, "auto"),
])
async def test_prep_exposes_the_tier_what_round_one_calls_and_the_mode(env, monkeypatch, msg, tier, force, mode):
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    prep = await _prep(svc, msg)
    assert prep["web_turn"].tier == tier and prep["web_force_first"] == force
    assert prep["web_search_mode"] == mode
    assert (prep["system_instruction_no_web"] is not None) is (tier == "auto")


@pytest.mark.asyncio
async def test_an_automatic_turns_no_web_instructions_claim_no_search(env, monkeypatch):
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    prep = await _prep(svc, PLAIN)
    for key in ("system_instruction_no_web", "system_instruction_no_tools_no_web"):
        text = prep[key]
        assert not re.search(r"\bweb_search\b", text) and "WEB RESULTS:" not in text
        assert ChatService._KNOWLEDGE_AUTO_WEB_CLAUSE not in text
        # every-chat search is open for this caller: the on-request line, once
        assert text.count(ChatService._WEB_ON_REQUEST_RULE) == 1 and text.count("WEB SEARCH:") == 1
    monkeypatch.setattr(cws.settings, "CHAT_WEB_SEARCH_ALL_CHATS_ENABLED", False)
    prep = await _prep(svc, PLAIN)
    assert prep["system_instruction_no_web"].count(ChatService._WEB_UNAVAILABLE_RULE) == 1


@pytest.mark.asyncio
async def test_the_tool_less_build_of_an_automatic_turn_claims_no_search(env, monkeypatch):
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    prep = await _prep(svc, PLAIN)
    no_tools = prep["system_instruction_no_tools"]
    assert no_tools.count(ChatService._WEB_UNAVAILABLE_RULE) == 1 and "WEB RESULTS:" not in no_tools


# ── chips ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("consent,dropped", [("3", True), (None, False), ("2", False)])
async def test_news_chips_are_dropped_where_every_chat_search_is_open_for_the_caller(env, consent, dropped):
    import json
    cws.set_client_ai_consent_version(consent)
    svc = ChatService.__new__(ChatService)

    class _G:
        async def generate_json(self, prompt, system_instruction=None, model_name=None):
            return {"text": json.dumps({"suggestions": [
                "Any recent news on Apple?", "Search the web for Apple's DOJ case",
                "What drives the moat?", "How does it compare to peers?"]})}

    svc.gemini = _G()
    out = await svc.generate_followup_suggestions(
        "q", "a", context_type="STOCK", reference_id="AAPL", session_type="NORMAL", user_id=UID)
    if dropped:
        assert out == ["What drives the moat?", "How does it compare to peers?"]
    else:
        assert out == ["Any recent news on Apple?", "What drives the moat?"], \
            "the search-the-web chip is a dead end there; the news chip is answerable"


# ── final review 2026-10-09: a deferred search is NEUTRAL for the refund gate ──

_DEFERRED = {"web_search": True, "status": "deferred", "query": "Apple lawsuit", "result_count": 0,
             "results": [], "note": "No web search ran yet.", "deferred": True}
_FAILED = {"name": "check_company_financials", "error": "timed_out", "upstream": True}


class _GemResults(_Gem):
    def __init__(self, tool_results, tool_errors):
        super().__init__()
        self._results, self._errors = tool_results, tool_errors

    async def generate_with_tools(self, **kw):
        self.kw = kw
        return {"text": "Caydex's figures could not be loaded right now.", "tokens_used": 20,
                "tool_results": list(self._results), "tool_errors": list(self._errors),
                "finish_reason": "STOP"}


@pytest.mark.asyncio
async def test_send_door_a_failed_caydex_tool_beside_a_deferred_search_is_degraded(env, monkeypatch):
    svc = _svc(monkeypatch)
    svc.gemini = _GemResults([dict(_DEFERRED)], [dict(_FAILED)])
    out = await _gen(svc, PLAIN)
    assert out["degraded"] == "no_tools", "the deferred search ran nothing: refunded like no_tools"


@pytest.mark.asyncio
async def test_send_door_a_delivered_search_after_a_failed_tool_stays_charged(env, monkeypatch):
    delivered = {"web_search": True, "status": "ok", "result_count": 1,
                 "results": [{"title": "DOJ case advances", "publisher": "Reuters"}]}
    svc = _svc(monkeypatch)
    svc.gemini = _GemResults([dict(_DEFERRED), delivered], [dict(_FAILED)])
    out = await _gen(svc, PLAIN)
    assert not out.get("degraded")


@pytest.mark.asyncio
async def test_send_door_the_deadline_reaches_generate_with_tools(env, monkeypatch):
    svc = _svc(monkeypatch)
    svc.gemini = _Gem()
    await _gen(svc, PLAIN, deadline=12345.0)
    assert svc.gemini.kw["deadline"] == 12345.0
    await _gen(svc, PLAIN)
    assert svc.gemini.kw["deadline"] is None, "no door deadline: the elapsed-time gate"
