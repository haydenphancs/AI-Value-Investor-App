"""The web search's THREE tiers (owner decisions 2026-10-08, PLAN A7 + A8) at the service level.

* `decide_web_search` — ONE decision per turn: `report_explicit` (today's report gate, app ≥ 1.1),
  `explicit` (every chat, consent v3), `auto` (the automatic fallback, consent v3), or none — read by
  every gate helper; the master switch and the key close every tier.
* `X-AI-Consent-Version` — parsed STRICTLY (ASCII digits, ≤ 3), fails closed, captured per request.
* The budget — explicit tiers claim the global cap; an automatic search claims its account's bucket,
  the automatic global bucket, then the global cap, refunding every claimed unit when the search
  did not run; a capped bucket latches until ET midnight.
* Shadow mode — no declaration, no claim, no search call, one counts-only line.
* Market-data queries are refused on every tier; the automatic path goes through the sanitizer.

Hermetic: the budget RPC is a ledger fake and Brave a stub; every switch is set explicitly (the
module-level `Settings` reads backend/.env), and the request-scoped headers are reset per test.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from app.config import Settings
from app.core import client_app_version as cav
from app.integrations import brave_search as bs
from app.services import chat_market_tools as cmt
from app.services import chat_web_search_service as cws
from app.services.chat_budget_service import ChatBudgetUnavailable

UID = "user-tier-aaaa-0001"
UID2 = "user-tier-bbbb-0002"
UID3 = "user-tier-cccc-0003"
GLOBAL = cws._REPORT_WEB_SEARCH_BUCKET
AUTO = cws._AUTO_WEB_SEARCH_BUCKET


def ACCT(uid: str) -> str:
    return cws._auto_account_bucket(uid)


# ── fakes ─────────────────────────────────────────────────────────────────────


class _Ledger:
    def __init__(self):
        self.caps: Dict[str, int] = {}
        self.raise_on: set = set()
        self.counts: Dict[str, int] = {}
        self.claims: List[tuple] = []
        self.refunds: List[str] = []

    def try_claim_turn(self, bucket, limit=None):
        self.claims.append((bucket, limit))
        if bucket in self.raise_on:
            raise ChatBudgetUnavailable("db down")
        cap = self.caps.get(bucket, limit)
        if self.counts.get(bucket, 0) >= cap:
            return -1
        self.counts[bucket] = self.counts.get(bucket, 0) + 1
        return self.counts[bucket]

    def refund_turn(self, bucket):
        self.refunds.append(bucket)
        self.counts[bucket] = max(0, self.counts.get(bucket, 0) - 1)


class _Brave:
    def __init__(self, exc: Optional[BaseException] = None, delay: float = 0.0):
        self.exc = exc
        self.delay = delay
        self.calls: List[str] = []
        self.started = asyncio.Event()

    async def __call__(self, query, *, count=10, freshness=None, extra_snippets=False):
        self.calls.append(query)
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return {"results": [{"title": "Apple faces DOJ suit", "url": "https://www.reuters.com/legal/a/",
                             "description": "The case moved forward in court.",
                             "page_age": "2026-09-30T10:00:00"}]}


@pytest.fixture
def tiers(monkeypatch):
    """Every tier switched ON (the master, a key, every-chat search, automatic mode), consent 3
    and app 1.1 on the request, fresh caches / latch, a ledger and a Brave stub."""
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
    monkeypatch.setattr(s, "BRAVE_SEARCH_TIMEOUT_SECONDS", 4.0)
    monkeypatch.setattr(s, "BRAVE_SEARCH_EXTRA_SNIPPETS", False)
    monkeypatch.setattr(s, "GEMINI_TOOL_RESULT_MAX_CHARS", 8000)
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


def _consent(raw):
    cws.set_client_ai_consent_version(raw)


def _decide(st="NORMAL", ct="STOCK", msg="what is the moat?", uid=UID, **kw):
    return cws.decide_web_search(st, ct, msg, uid, **kw)


def _auto_turn(uid=UID, session="s1", msg="any lawsuits against Apple?"):
    t = cws.open_web_search_turn("NORMAL", "STOCK", msg, uid, "AAPL", session_id=session)
    assert t is not None and t.tier == cws.TIER_AUTO, t
    return t


def _explicit_turn(uid=UID, session="s1", msg="search the web for the Apple DOJ case"):
    t = cws.open_web_search_turn("NORMAL", "STOCK", msg, uid, "AAPL", session_id=session)
    assert t is not None and t.tier == cws.TIER_EXPLICIT, t
    return t


# ── settings ──────────────────────────────────────────────────────────────────


def test_every_new_switch_ships_closed():
    f = Settings.model_fields
    assert f["CHAT_WEB_SEARCH_ALL_CHATS_ENABLED"].default is False
    assert f["CHAT_AUTO_WEB_SEARCH_MODE"].default == "off"
    assert f["CHAT_AUTO_WEB_SEARCH_DAILY_CAP"].default == 100
    assert f["CHAT_AUTO_WEB_SEARCH_PER_ACCOUNT_DAILY"].default == 5
    assert f["CHAT_AUTO_WEB_SEARCH_ACCOUNT_ALLOWLIST"].default == ""
    assert f["CHAT_WEB_SEARCH_MIN_CONSENT_VERSION"].default == 3
    # The automatic share sits INSIDE the explicit cap.
    assert f["CHAT_AUTO_WEB_SEARCH_DAILY_CAP"].default <= f["CHAT_REPORT_WEB_SEARCH_DAILY_CAP"].default


@pytest.mark.parametrize("raw,expected", [
    ("off", "off"), ("on", "on"), ("shadow", "shadow"), (" ON ", "on"), ("Shadow", "shadow"),
    ("", "off"), ("enabled", "off"), ("true", "off"), ("1", "off"),
])
def test_an_unknown_auto_mode_reads_as_off(monkeypatch, raw, expected):
    monkeypatch.setenv("CHAT_AUTO_WEB_SEARCH_MODE", raw)
    assert Settings().CHAT_AUTO_WEB_SEARCH_MODE == expected


@pytest.mark.parametrize("name,bad", [
    ("CHAT_AUTO_WEB_SEARCH_DAILY_CAP", "181"), ("CHAT_AUTO_WEB_SEARCH_DAILY_CAP", "-1"),
    ("CHAT_AUTO_WEB_SEARCH_PER_ACCOUNT_DAILY", "51"), ("CHAT_WEB_SEARCH_MIN_CONSENT_VERSION", "2"),
])
def test_out_of_range_caps_and_a_lowered_consent_gate_fail_the_deploy(monkeypatch, name, bad):
    monkeypatch.setenv(name, bad)
    with pytest.raises(Exception):
        Settings()


@pytest.mark.parametrize("raw", ["ON!", 1, True, None, ["on"], "  "])
def test_the_service_re_reads_the_mode_and_fails_closed(monkeypatch, raw):
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_MODE", raw)
    assert cws._auto_mode() == "off"


@pytest.mark.parametrize("raw", [2, 0, -5, "4", None, True, 3.5])
def test_a_patched_consent_floor_never_drops_below_three(monkeypatch, raw):
    monkeypatch.setattr(cws.settings, "CHAT_WEB_SEARCH_MIN_CONSENT_VERSION", raw)
    assert cws._min_consent_version() == 3


# ── X-AI-Consent-Version ──────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("3", 3), ("2", 2), ("10", 10), ("999", 999), ("03", 3), ("0", 0),
    ("1000", None), ("", None), (" 3", None), ("3 ", None), ("3.0", None), ("+3", None),
    ("-3", None), ("v3", None), ("³", None), ("٣", None), ("３", None), ("0x3", None),
    ("3\n", None), (None, None), (3, None), (b"3", None), (3.0, None), (["3"], None),
])
def test_the_consent_header_is_parsed_strictly(raw, expected):
    assert cws.parse_ai_consent_version(raw) == expected


def test_an_over_long_header_is_dropped_and_nothing_leaks_between_requests():
    def run():
        cws.set_client_ai_consent_version("3" * 33)
        assert cws.client_ai_consent_version() is None
        cws.set_client_ai_consent_version("3")
        return cws.client_ai_consent_version()
    assert contextvars.copy_context().run(run) == 3
    assert contextvars.copy_context().run(cws.client_ai_consent_version) is None


def test_a_router_dependency_reaches_the_stream_its_tasks_and_threads():
    from fastapi import APIRouter, Depends, FastAPI
    from fastapi.responses import StreamingResponse
    from fastapi.testclient import TestClient

    router = APIRouter(dependencies=[Depends(cws.capture_client_ai_consent_version)])

    @router.get("/probe")
    async def probe():
        async def body():
            yield f"handler={cws.client_ai_consent_version()};"
            seen = await asyncio.create_task(asyncio.sleep(0, result=cws.client_ai_consent_version()))
            yield f"task={seen};"
            yield f"thread={await asyncio.to_thread(cws.client_ai_consent_version)}"
        return StreamingResponse(body(), media_type="text/plain")

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/probe", headers={"X-AI-Consent-Version": "3"}).text == "handler=3;task=3;thread=3"
    assert client.get("/probe", headers={"X-AI-Consent-Version": "3.0"}).text == "handler=None;task=None;thread=None"
    assert client.get("/probe").text == "handler=None;task=None;thread=None"


def _dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _dependency_calls(dep)


def test_every_chat_route_in_the_real_app_captures_the_consent():
    """Fail-closed by construction (no capture → no consent → no every-chat search), but a route
    that misses the capture would silently never open the tiers the copy promises."""
    from fastapi.routing import APIRoute
    from app.main import app

    chat_routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/v1/chat")]
    assert len(chat_routes) >= 5
    missing = sorted(f"{sorted(r.methods)} {r.path}" for r in chat_routes
                     if cws.capture_client_ai_consent_version not in set(_dependency_calls(r.dependant)))
    assert not missing, missing


def test_the_chat_router_declares_both_captures():
    src = (Path(__file__).resolve().parents[1] / "app/api/v1/endpoints/chat.py").read_text()
    code = "\n".join(re.sub(r"\s#.*$", "", l) for l in src.splitlines() if not l.strip().startswith("#"))
    assert re.search(r"router\s*=\s*APIRouter\(\s*dependencies=\[\s*Depends\(capture_client_app_version\),"
                     r"\s*Depends\(capture_client_ai_consent_version\)\s*\]\)", code)


# ── the decision matrix ───────────────────────────────────────────────────────

_ASK = "search the web for the Apple DOJ case"
_NEWS = "what's the latest news on Apple?"
_PLAIN = "what is Apple's moat?"
_LAWSUIT = "any lawsuits against Apple?"


@pytest.mark.parametrize("label,st,ct,msg,consent,app,uid,overrides,tier,flag", [
    # (a) report_explicit — exactly today's gate (no consent needed)
    ("report ask", "REPORT", "TICKER_REPORT", _ASK, None, "1.1", UID, {}, "report_explicit", None),
    ("report news", "REPORT", "TICKER_REPORT", _NEWS, None, "1.1", UID, {}, "report_explicit", None),
    ("report ask, no header", "REPORT", "TICKER_REPORT", _ASK, None, None, UID, {}, "report_explicit", None),
    ("report ask, app 1.0", "REPORT", "TICKER_REPORT", _ASK, None, "1.0", UID, {}, None, "unavailable"),
    ("report, no ask", "REPORT", "TICKER_REPORT", _PLAIN, None, "1.1", UID, {}, None, "on_request"),
    ("report ask, signed out", "REPORT", "TICKER_REPORT", _ASK, "3", "1.1", None, {}, None, "unavailable"),
    # (b) explicit — every chat, consent ≥ 3
    ("stock ask", "NORMAL", "STOCK", _ASK, "3", "1.1", UID, {}, "explicit", None),
    ("stock news", "NORMAL", "STOCK", _NEWS, "3", "1.1", UID, {}, "explicit", None),
    ("normal ask", "NORMAL", None, _ASK, "3", "1.1", UID, {}, "explicit", None),
    ("book ask (Learn included)", "BOOK", "BOOK", _ASK, "3", "1.1", UID, {}, "explicit", None),
    ("journey ask", "JOURNEY", "JOURNEY_LESSON", _ASK, "3", "1.1", UID, {}, "explicit", None),
    ("consent 10", "NORMAL", "STOCK", _ASK, "10", "1.1", UID, {}, "explicit", None),
    ("app 1.0 with consent 3", "REPORT", "TICKER_REPORT", _ASK, "3", "1.0", UID, {}, "explicit", None),
    ("stock ask, no consent", "NORMAL", "STOCK", _ASK, None, "1.1", UID, {}, None, "unavailable"),
    ("stock ask, consent 2", "NORMAL", "STOCK", _ASK, "2", "1.1", UID, {}, None, "unavailable"),
    ("stock ask, consent garbage", "NORMAL", "STOCK", _ASK, "garbage", "1.1", UID, {}, None, "unavailable"),
    ("stock ask, signed out", "NORMAL", "STOCK", _ASK, "3", "1.1", None, {}, None, "unavailable"),
    ("stock ask, blank uid", "NORMAL", "STOCK", _ASK, "3", "1.1", "   ", {}, None, "unavailable"),
    # (c) auto — no ask, every condition
    ("stock, no ask", "NORMAL", "STOCK", _LAWSUIT, "3", "1.1", UID, {}, "auto", None),
    ("normal, no ask", "NORMAL", None, _PLAIN, "3", "1.1", UID, {}, "auto", None),
    ("report, no ask, consent 3", "REPORT", "TICKER_REPORT", _PLAIN, "3", "1.1", UID, {}, "auto", None),
    ("updates, no ask", "NORMAL", "UPDATES_SCOPE", _LAWSUIT, "3", "1.1", UID, {}, "auto", None),
    ("market data", "NORMAL", "STOCK", "what's Apple's stock price?", "3", "1.1", UID, {}, None, "on_request"),
    ("fx", "NORMAL", None, "what's the euro to dollar exchange rate?", "3", "1.1", UID, {}, None, "on_request"),
    ("vix", "NORMAL", None, "where is the VIX?", "3", "1.1", UID, {}, None, "on_request"),
    ("dxy", "NORMAL", None, "DXY level?", "3", "1.1", UID, {}, None, "on_request"),
    ("book, no ask", "BOOK", "BOOK", _PLAIN, "3", "1.1", UID, {}, None, "on_request"),
    ("money moves, no ask", "NORMAL", "MONEY_MOVES_ARTICLE", _PLAIN, "3", "1.1", UID, {}, None, "on_request"),
    ("journey, no ask", "JOURNEY", "JOURNEY_LESSON", _PLAIN, "3", "1.1", UID, {}, None, "on_request"),
    ("concept session", "CONCEPT", None, _PLAIN, "3", "1.1", UID, {}, None, "on_request"),
    ("deep dive", "NORMAL", "ETF", _PLAIN, "3", "1.1", UID, {"is_deep_dive": True}, None, "on_request"),
    ("auto, no consent", "NORMAL", "STOCK", _LAWSUIT, None, "1.1", UID, {}, None, "none"),
    ("auto, consent 2", "NORMAL", "STOCK", _LAWSUIT, "2", "1.1", UID, {}, None, "none"),
    ("auto, signed out", "NORMAL", "STOCK", _LAWSUIT, "3", "1.1", None, {}, None, "none"),
])
def test_the_decision_matrix(tiers, label, st, ct, msg, consent, app, uid, overrides, tier, flag):
    _consent(consent)
    cav.set_client_app_version(app)
    d = cws.decide_web_search(st, ct, msg, uid, **overrides)
    assert d.tier == tier, (label, d)
    flags = {"unavailable": d.unavailable, "on_request": d.on_request, "none": d.none_line}
    if tier is not None:
        assert not any(flags.values()), (label, d)
    else:
        assert flags[flag] is True and sum(flags.values()) == 1, (label, d)
    # Every gate helper reads the same decision.
    assert cws.web_search_intent_unserved(st, ct, msg, user_id=uid) is (d.unavailable and tier is None)
    turn = cws.open_web_search_turn(st, ct, msg, uid, "AAPL", **overrides)
    assert (turn is not None) is (tier is not None)
    if turn is not None:
        assert turn.tier == tier and turn.ask_kind == d.ask_kind


@pytest.mark.parametrize("setting,value", [
    ("CHAT_REPORT_WEB_SEARCH_ENABLED", False), ("BRAVE_SEARCH_API_KEY", ""),
    ("BRAVE_SEARCH_API_KEY", None),
])
@pytest.mark.parametrize("st,ct,msg", [
    ("REPORT", "TICKER_REPORT", _ASK), ("NORMAL", "STOCK", _ASK), ("NORMAL", "STOCK", _NEWS),
    ("NORMAL", "STOCK", _LAWSUIT), ("BOOK", "BOOK", _ASK), ("REPORT", "TICKER_REPORT", _PLAIN),
])
def test_the_master_switch_or_a_missing_key_closes_every_tier(tiers, monkeypatch, setting, value, st, ct, msg):
    monkeypatch.setattr(cws.settings, setting, value)
    for mode in ("on", "shadow"):
        monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_MODE", mode)
        d = cws.decide_web_search(st, ct, msg, UID)
        assert d.tier is None and d.shadow is False and d.on_request is False, d
        assert cws.open_web_search_turn(st, ct, msg, UID) is None


def test_with_every_chat_search_off_an_ask_may_still_reach_the_automatic_tier(tiers, monkeypatch):
    """Precedence report_explicit > explicit > auto: an ask the explicit tier cannot serve is not a
    reason to deny the automatic one (still unforced, still budgeted)."""
    monkeypatch.setattr(cws.settings, "CHAT_WEB_SEARCH_ALL_CHATS_ENABLED", False)
    d = _decide(msg=_ASK)
    assert d.tier == cws.TIER_AUTO and d.ask_kind == "explicit"
    # …and with automatic search off too, the ask is "unavailable", never silently none.
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_MODE", "off")
    d = _decide(msg=_ASK)
    assert d.tier is None and d.unavailable and d.reason == "all_chats_off"


def test_automatic_but_not_this_turn_says_on_this_turn_not_in_this_chat(tiers, monkeypatch):
    monkeypatch.setattr(cws.settings, "CHAT_WEB_SEARCH_ALL_CHATS_ENABLED", False)
    d = _decide(msg="what's Apple's stock price?")
    assert d.tier is None and d.unavailable and not d.none_line and d.reason == "market_data"


@pytest.mark.parametrize("allowlist,uid,granted", [
    ("", UID, True), (UID, UID, True), (f"{UID2}, {UID}", UID, True), (f" {UID} ,", UID, True),
    (UID2, UID, False), (",,", UID, True), (f"{UID}x", UID, False),
])
def test_the_allowlist(tiers, monkeypatch, allowlist, uid, granted):
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_ACCOUNT_ALLOWLIST", allowlist)
    assert (_decide(msg=_LAWSUIT, uid=uid).tier == cws.TIER_AUTO) is granted


def test_a_non_string_allowlist_is_never_everyone(tiers, monkeypatch):
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_ACCOUNT_ALLOWLIST", [UID])
    assert _decide(msg=_LAWSUIT).tier is None


def test_the_decision_never_raises_and_fails_closed(tiers, monkeypatch):
    monkeypatch.setattr(cws, "is_market_data_question", lambda *_: 1 / 0)
    d = _decide(msg=_LAWSUIT)
    assert d.tier is None and d.unavailable and d.reason == "error"
    for junk in (None, 12, b"x", ["x"]):
        assert cws.decide_web_search(junk, junk, junk, junk).tier is None


# ── helpers read by the doors ─────────────────────────────────────────────────


def test_web_force_first(tiers):
    full = {"get_ticker_news", "explain_price_move", "web_search", "get_stock_chart_data",
            "get_market_snapshot"}
    explicit = cws.WebSearchTurn(user_id=UID, tier=cws.TIER_EXPLICIT, ask_kind="explicit")
    news = cws.WebSearchTurn(user_id=UID, tier=cws.TIER_REPORT_EXPLICIT, ask_kind="news",
                             ticker="AAPL")
    assert cws.web_force_first(explicit, full) == "web_search"
    assert cws.web_force_first(cws.WebSearchTurn(user_id=UID), full) == "web_search"
    assert cws.web_force_first(news, full) == ("get_ticker_news", "explain_price_move")
    assert cws.web_force_first(news, {"get_ticker_news": object(), "web_search": object()}) == ("get_ticker_news",)
    assert cws.web_force_first(news, {"web_search"}) == "web_search", "no licensed news at all: the web"
    assert cws.web_force_first(cws.WebSearchTurn(user_id=UID, tier=cws.TIER_AUTO), full) is None, \
        "an automatic turn that did not ask for news is never forced"
    assert cws.web_force_first(None, full) is None and cws.web_force_first("x", full) is None
    assert cws.web_force_first(news, None) == "web_search"
    assert cws.web_force_first(news, 5) is None, "junk never raises"


def test_a_news_ask_with_no_subject_company_is_never_forced_to_a_ticker_only_tool(tiers):
    """Review 2026-10-09: "Any news today?" in a general or a Learn chat (no screen ticker) was
    forced to `get_ticker_news` / `explain_price_move` — the model had to invent a ticker or send
    a non-symbol (a charged error). With no subject, the market-wide snapshot is offered (beside
    the headline tools, for a company the user names); without it, the web."""
    full = {"get_ticker_news", "explain_price_move", "get_market_snapshot", "web_search"}
    for tier in (cws.TIER_EXPLICIT, cws.TIER_REPORT_EXPLICIT):
        bare = cws.WebSearchTurn(user_id=UID, tier=tier, ask_kind="news", ticker=None)
        forced = cws.web_force_first(bare, full)
        assert isinstance(forced, tuple) and "get_market_snapshot" in forced
        assert set(forced) <= {"get_ticker_news", "explain_price_move", "get_market_snapshot"}
        assert cws.web_force_first(bare, {"get_ticker_news", "explain_price_move", "web_search"}) \
            == "web_search", "ticker-only tools are never forced alone on a turn with no ticker"
        assert cws.web_prompt_kind(bare, {"get_ticker_news", "web_search"}) == "explicit"
    # An INDEX screen (a ticker, no headline tool): the market-wide news, not the web.
    index = cws.WebSearchTurn(user_id=UID, tier=cws.TIER_EXPLICIT, ask_kind="news", ticker="^GSPC")
    assert cws.web_force_first(index, {"get_market_overview", "get_market_snapshot", "web_search"}) \
        == ("get_market_snapshot",)
    assert cws.web_force_first(index, {"get_market_overview", "web_search"}) == "web_search"


def test_the_automatic_tier_forces_licensed_news_for_a_news_ask(tiers):
    """Owner decision 2026-10-08: a "latest news" ask reads the licensed headlines first — on the
    automatic tier too (review 2026-10-09: it fell to an unforced turn there)."""
    full = {"get_ticker_news", "explain_price_move", "get_market_snapshot", "web_search"}
    auto_news = cws.WebSearchTurn(user_id=UID, tier=cws.TIER_AUTO, ask_kind="news", ticker="AAPL")
    assert cws.web_force_first(auto_news, full) == ("get_ticker_news", "explain_price_move")
    assert cws.web_force_first(auto_news, {"web_search"}) is None, "never the web on the automatic tier"
    auto_explicit = cws.WebSearchTurn(user_id=UID, tier=cws.TIER_AUTO, ask_kind="explicit", ticker="AAPL")
    assert cws.web_force_first(auto_explicit, full) is None
    assert cws.web_search_mode(auto_news, full) == "auto"


def test_a_market_data_ask_is_never_forced_to_the_web(tiers):
    """Review 2026-10-09: "search the web for the euro dollar rate" forced round 1 to the search.
    The tier stays granted (no false "unavailable"), round 1 is not forced, and the query
    refusal stays the backstop."""
    full = {"get_ticker_news", "explain_price_move", "get_market_snapshot", "web_search"}
    for st, ct, msg in (("REPORT", "TICKER_REPORT", "search the web for the euro dollar rate"),
                        ("NORMAL", "STOCK", "look up where bitcoin is at right now"),
                        ("NORMAL", "STOCK", "Can you search the web for Apple's stock price?")):
        d = _decide(st=st, ct=ct, msg=msg)
        assert d.granted and d.ask_kind == "explicit" and d.market_data is True, msg
        turn = cws.open_web_search_turn(st, ct, msg, UID, "AAPL", session_id="s1", decision=d)
        assert turn.market_data_ask is True
        assert cws.web_force_first(turn, full) is None, msg
        assert cws.web_prompt_kind(turn, full) == "explicit"
    plain = _decide(msg="search the web for the Apple DOJ case")
    assert plain.market_data is False


def test_web_extra_round_tools_and_mode(tiers):
    full = {"get_ticker_news", "explain_price_move", "get_market_snapshot", "web_search"}
    auto = cws.WebSearchTurn(user_id=UID, tier=cws.TIER_AUTO)
    assert cws.web_extra_round_tools(auto) == frozenset({"web_search"})
    for t in (cws.WebSearchTurn(user_id=UID), None, object()):
        assert cws.web_extra_round_tools(t) is None
    assert cws.web_search_mode(auto) == "auto"
    news = cws.WebSearchTurn(user_id=UID, ask_kind="news", ticker="AAPL")
    assert cws.web_search_mode(news, full) == "news"
    assert cws.web_search_mode(news, {"web_search"}) == "explicit", \
        "the news wording only when the licensed news really is fetched first"
    assert cws.web_search_mode(news) == "explicit", "no tools known: the conservative wording"
    assert cws.web_search_mode(cws.WebSearchTurn(user_id=UID, ask_kind="explicit"), full) == "explicit"
    assert cws.web_search_mode(None) is None
    assert cws.web_prompt_kind(None, full) is None and cws.web_prompt_kind(auto, full) is None


def test_decision_without_web(tiers):
    granted = cws.WebSearchDecision(tier=cws.TIER_AUTO, reason="auto", explicit_open=True,
                                    context="STOCK")
    d = cws.decision_without_web(granted)
    assert d.tier is None and not d.granted and d.on_request and not d.unavailable
    assert not d.none_line and not d.shadow and d.context == "STOCK" and d.reason == "dropped"
    closed = cws.decision_without_web(cws.WebSearchDecision(tier=cws.TIER_AUTO, explicit_open=False))
    assert closed.unavailable and not closed.on_request
    for junk in (None, 5, "x", object()):
        j = cws.decision_without_web(junk)
        assert not j.granted and j.unavailable


@pytest.mark.parametrize("st,ct,consent,uid,all_chats,dropped", [
    ("REPORT", "TICKER_REPORT", None, None, False, True),
    (None, "TICKER_REPORT", None, None, False, True),
    (" report ", None, None, None, False, True),
    ("NORMAL", "STOCK", "3", UID, True, True),
    ("NORMAL", "STOCK", "2", UID, True, False),
    ("NORMAL", "STOCK", None, UID, True, False),
    ("NORMAL", "STOCK", "3", None, True, False),
    ("NORMAL", "STOCK", "3", UID, False, False),
])
def test_web_chips_dropped(tiers, monkeypatch, st, ct, consent, uid, all_chats, dropped):
    monkeypatch.setattr(cws.settings, "CHAT_WEB_SEARCH_ALL_CHATS_ENABLED", all_chats)
    _consent(consent)
    assert cws.web_chips_dropped(st, ct, uid) is dropped


# ── the budget ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_automatic_search_claims_account_then_auto_global_then_global(tiers):
    led, brave = tiers
    out = await cws.run_web_search(_auto_turn(), "Apple DOJ lawsuit")
    assert out["status"] == "ok" and len(brave.calls) == 1
    assert led.claims == [(ACCT(UID), 5), (AUTO, 100), (GLOBAL, 500)]
    assert led.counts == {ACCT(UID): 1, AUTO: 1, GLOBAL: 1} and led.refunds == []


@pytest.mark.asyncio
async def test_an_explicit_search_claims_only_the_global_cap(tiers):
    led, _ = tiers
    out = await cws.run_web_search(_explicit_turn(), "Apple DOJ case")
    assert out["status"] == "ok" and led.claims == [(GLOBAL, 500)]


def test_the_buckets_are_distinct_derived_uuids():
    import uuid
    assert len({GLOBAL, AUTO, ACCT(UID), ACCT(UID2)}) == 4
    for b in (AUTO, ACCT(UID)):
        assert str(uuid.UUID(b)) == b
    assert ACCT(UID) != UID and ACCT(UID) == ACCT(UID)


@pytest.mark.asyncio
async def test_one_account_stops_at_five_automatic_searches_and_is_latched(tiers):
    led, brave = tiers
    topics = ["recall", "lawsuit", "antitrust", "layoffs", "outage", "patent"]
    outs = [await cws.run_web_search(_auto_turn(session=f"s{i}"), f"Apple {t}")
            for i, t in enumerate(topics)]
    assert [o["status"] for o in outs] == ["ok"] * 5 + ["daily_limit"]
    assert len(brave.calls) == 5 and led.counts[ACCT(UID)] == 5
    # The capped one claimed nothing else, and its note never announces a limit.
    assert "daily web-search limit" not in outs[-1]["note"] and "upstream" not in outs[-1]
    assert outs[-1]["note"] == cws._NOTE_AUTO_NOT_RUN
    # Latched for THIS account: the tool is no longer declared to it today …
    assert _decide(msg=_LAWSUIT).tier is None and _decide(msg=_LAWSUIT).reason == "auto_budget"
    # … while another account still gets it, and an explicit ask still searches.
    assert _decide(msg=_LAWSUIT, uid=UID2).tier == cws.TIER_AUTO
    assert (await cws.run_web_search(_explicit_turn(session="sx"), "Apple DOJ case"))["status"] == "ok"


@pytest.mark.asyncio
async def test_the_automatic_global_cap_refunds_the_account_unit_and_latches_everyone(tiers, monkeypatch):
    led, brave = tiers
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_DAILY_CAP", 2)
    a = await cws.run_web_search(_auto_turn(uid=UID, session="a"), "Apple recall")
    b = await cws.run_web_search(_auto_turn(uid=UID2, session="b"), "Apple lawsuit")
    c = await cws.run_web_search(_auto_turn(uid=UID3, session="c"), "Apple layoffs")
    assert [a["status"], b["status"], c["status"]] == ["ok", "ok", "daily_limit"]
    assert led.refunds == [ACCT(UID3)], "the third account's unit went back"
    assert led.counts[ACCT(UID3)] == 0 and led.counts[AUTO] == 2 and led.counts[GLOBAL] == 2
    for uid in (UID, UID2, UID3, "user-new-0009"):
        assert _decide(msg=_LAWSUIT, uid=uid).reason == "auto_budget"
    # Explicit asks may still use the rest of the global cap.
    assert (await cws.run_web_search(_explicit_turn(session="e"), "Apple DOJ"))["status"] == "ok"


@pytest.mark.asyncio
async def test_explicit_asks_keep_the_whole_global_cap_while_auto_stops_at_its_share(tiers, monkeypatch):
    led, _ = tiers
    monkeypatch.setattr(cws.settings, "CHAT_REPORT_WEB_SEARCH_DAILY_CAP", 4)
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_DAILY_CAP", 2)
    autos = [await cws.run_web_search(_auto_turn(uid=u, session=u), "Apple recall") for u in (UID, UID2, UID3)]
    assert [o["status"] for o in autos] == ["ok", "ok", "daily_limit"]
    explicit = [await cws.run_web_search(_explicit_turn(session=f"e{i}"), f"Apple DOJ {t}")
                for i, t in enumerate(["ruling", "appeal", "filing"])]
    assert [o["status"] for o in explicit] == ["ok", "ok", "daily_limit"], "180 (here 4) shared, auto ≤ its share"
    assert led.counts[GLOBAL] == 4 and led.counts[AUTO] == 2


@pytest.mark.asyncio
async def test_an_automatic_search_never_exceeds_the_global_cap(tiers, monkeypatch):
    led, brave = tiers
    monkeypatch.setattr(cws.settings, "CHAT_REPORT_WEB_SEARCH_DAILY_CAP", 1)
    assert (await cws.run_web_search(_explicit_turn(), "Apple DOJ"))["status"] == "ok"
    out = await cws.run_web_search(_auto_turn(session="a"), "Apple recall")
    assert out["status"] == "daily_limit" and len(brave.calls) == 1
    assert sorted(led.refunds) == sorted([ACCT(UID), AUTO]), "both automatic units went back"
    assert led.counts[ACCT(UID)] == 0 and led.counts[AUTO] == 0
    assert cws._latched(("global",))


@pytest.mark.asyncio
@pytest.mark.parametrize("cap_name", ["CHAT_AUTO_WEB_SEARCH_DAILY_CAP", "CHAT_AUTO_WEB_SEARCH_PER_ACCOUNT_DAILY",
                                      "CHAT_REPORT_WEB_SEARCH_DAILY_CAP"])
async def test_a_zero_cap_closes_the_automatic_tier_without_touching_the_ledger(tiers, monkeypatch, cap_name):
    led, brave = tiers
    monkeypatch.setattr(cws.settings, cap_name, 0)
    assert _decide(msg=_LAWSUIT).reason == "auto_budget"
    turn = cws.WebSearchTurn(user_id=UID, tier=cws.TIER_AUTO, session_id="s")
    out = await cws.run_web_search(turn, "Apple recall")
    assert out["status"] == "daily_limit" and brave.calls == []
    assert led.claims == [] and led.refunds == [], "refused before any claim"


@pytest.mark.asyncio
@pytest.mark.parametrize("down", ["acct", "auto", "global"])
async def test_a_budget_outage_at_any_step_fails_closed_and_holds_nothing(tiers, down):
    led, brave = tiers
    led.raise_on = {{"acct": ACCT(UID), "auto": AUTO, "global": GLOBAL}[down]}
    out = await cws.run_web_search(_auto_turn(), "Apple recall")
    assert out["status"] == "unavailable" and out["upstream"] is True and brave.calls == []
    assert sum(led.counts.values()) == 0, "every unit claimed before the outage went back"
    assert not cws._auto_latch, "an outage is transient: never latched"


@pytest.mark.asyncio
@pytest.mark.parametrize("exc,refunded", [
    (bs.BraveSearchNotConfiguredException("x", not_run=True), True),
    (bs.BraveSearchRateLimitException("x", retry_after=1.0), True),
    (bs.BraveSearchRequestException("x", not_run=True, status=422), True),
    (bs.BraveSearchUnavailableException("x", not_run=False), False),
])
async def test_every_claimed_unit_is_refunded_when_the_search_did_not_run(tiers, monkeypatch, exc, refunded):
    led, _ = tiers
    monkeypatch.setattr(cws.brave_search, "web_search", _Brave(exc=exc))
    turn = _auto_turn()
    out = await cws.run_web_search(turn, "Apple recall")
    assert out["status"] == "unavailable"
    if refunded:
        assert sorted(led.refunds) == sorted([ACCT(UID), AUTO, GLOBAL]) and sum(led.counts.values()) == 0
        assert turn.spent_a_unit() is False
    else:
        assert led.refunds == [] and turn.spent_a_unit() is True


@pytest.mark.asyncio
async def test_the_hard_bound_keeps_every_unit(tiers, monkeypatch):
    led, _ = tiers
    monkeypatch.setattr(cws.settings, "BRAVE_SEARCH_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(cws, "_HARD_BOUND_SLACK", 0.01)
    monkeypatch.setattr(cws.brave_search, "web_search", _Brave(delay=1.0))
    out = await cws.run_web_search(_auto_turn(), "Apple recall")
    assert out["status"] == "unavailable" and led.refunds == [] and led.counts[GLOBAL] == 1


@pytest.mark.asyncio
async def test_cancelling_an_automatic_search_refunds_all_three_units(tiers, monkeypatch):
    led, _ = tiers
    brave = _Brave(delay=5.0)
    monkeypatch.setattr(cws.brave_search, "web_search", brave)
    turn = _auto_turn()
    task = asyncio.ensure_future(cws.run_web_search(turn, "Apple recall"))
    await asyncio.wait_for(brave.started.wait(), 2)
    turn._task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn._task
    await asyncio.sleep(0.05)
    assert sorted(led.refunds) == sorted([ACCT(UID), AUTO, GLOBAL]) and sum(led.counts.values()) == 0
    out = await task
    assert out["status"] == "unavailable"


@pytest.mark.asyncio
async def test_a_twin_that_finished_during_the_claim_refunds_every_unit(tiers, monkeypatch):
    led, _ = tiers
    turn = _auto_turn(session="s1")
    winner = cws.WebSearchOutcome(status=cws.STATUS_OK, query="Apple recall",
                                  results=[{"n": 1, "title": "t"}], pills=[{}])
    orig = cmt._claim_bucket_status

    async def _claim_then_twin(bucket, cap, what):
        res = await orig(bucket, cap, what)
        if bucket == GLOBAL:      # the last claim: a twin finished meanwhile
            cws._cache[(UID, "apple recall", "")] = (cws._clock(), winner)
        return res
    monkeypatch.setattr(cmt, "_claim_bucket_status", _claim_then_twin)
    out = await cws.run_web_search(turn, "Apple recall")
    assert out["status"] == "ok" and sum(led.counts.values()) == 0
    assert sorted(led.refunds) == sorted([ACCT(UID), AUTO, GLOBAL])


def test_the_latch_resets_on_a_new_et_day(tiers, monkeypatch):
    day = {"v": "2026-10-09"}
    monkeypatch.setattr(cws, "budget_day", lambda: day["v"])
    cws._latch(("account", UID))
    cws._latch(("global",))
    assert cws._auto_budget_known_exhausted(UID) and _decide(msg=_LAWSUIT).reason == "auto_budget"
    day["v"] = "2026-10-10"
    assert not cws._auto_budget_known_exhausted(UID)
    assert _decide(msg=_LAWSUIT).tier == cws.TIER_AUTO
    assert cws._auto_latch == {}, "the stale entries were pruned"


def test_the_latch_is_bounded(tiers, monkeypatch):
    monkeypatch.setattr(cws, "_LATCH_MAX", 3)
    for i in range(10):
        cws._latch(("account", f"u{i}"))
    assert len(cws._auto_latch) <= 3 and cws._latched(("account", "u9"))


def test_the_latch_follows_the_budget_rows_clock():
    from app.services import chat_budget_service as cbs
    assert cws.budget_day is cbs.budget_day and cbs.budget_day() == cbs._budget_day()


# ── shadow mode ───────────────────────────────────────────────────────────────


def _shadow_lines(caplog):
    return [r.getMessage() for r in caplog.records if "AUTO_WEB_SHADOW" in r.getMessage()]


@pytest.mark.asyncio
async def test_shadow_declares_nothing_claims_nothing_calls_nothing_and_logs_one_line(tiers, monkeypatch, caplog):
    led, brave = tiers
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_MODE", "shadow")
    caplog.set_level(logging.INFO, logger=cws.logger.name)
    msg = "Any lawsuits against Apple, SECRETMARKER?"
    turn = cws.open_web_search_turn("NORMAL", "STOCK", msg, UID, "AAPL", session_id="s")
    assert turn is None
    assert led.claims == [] and brave.calls == []
    assert _shadow_lines(caplog) == ["AUTO_WEB_SHADOW category=lawsuit_regulatory context=STOCK"]
    assert "SECRETMARKER" not in caplog.text and "lawsuits against" not in caplog.text
    d = _decide(msg=msg)
    assert d.tier is None and d.shadow and d.shadow_topic == "lawsuit_regulatory"
    assert d.on_request and not d.none_line, "shadow changes no prompt line"


@pytest.mark.parametrize("msg,st,ct,kw", [
    ("what's Apple's stock price?", "NORMAL", "STOCK", {}),       # market data
    ("what is a moat?", "BOOK", "BOOK", {}),                       # Learn
    ("what is a moat?", "NORMAL", "ETF", {"is_deep_dive": True}),  # deep dive
])
def test_an_ineligible_turn_logs_no_shadow_line(tiers, monkeypatch, caplog, msg, st, ct, kw):
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_MODE", "shadow")
    caplog.set_level(logging.INFO, logger=cws.logger.name)
    assert cws.open_web_search_turn(st, ct, msg, UID, **kw) is None
    assert _shadow_lines(caplog) == []


@pytest.mark.parametrize("consent", ["3", "2", None, "garbage"])
def test_shadow_ignores_the_budget_latch_and_the_consent_version(tiers, monkeypatch, caplog, consent):
    """Final review 2026-10-09 (PLAN: "Shadow mode may run during Phase A"): shadow sends nothing
    to any provider and declares nothing — it logs a category and a context — so it measures every
    build's demand, before 1.01 sends a consent header too. A real automatic search (mode "on")
    still needs consent v3."""
    led, brave = tiers
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_MODE", "shadow")
    caplog.set_level(logging.INFO, logger=cws.logger.name)
    cws._latch(("global",))
    _consent(consent)
    d = _decide(msg=_LAWSUIT)
    assert d.shadow is True and d.tier is None, "shadow measures demand, not budget or consent"
    assert cws.open_web_search_turn("NORMAL", "STOCK", _LAWSUIT, UID, "AAPL") is None
    assert _shadow_lines(caplog), "the shadow line is logged"
    assert led.claims == [] and brave.calls == []


@pytest.mark.parametrize("consent", ["2", None])
def test_mode_on_still_needs_consent_three(tiers, consent):
    _consent(consent)
    d = _decide(msg=_LAWSUIT)
    assert d.tier is None and not d.shadow and d.reason == "consent" and d.none_line


def test_a_context_never_reaches_a_log_line_raw(tiers, monkeypatch, caplog):
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_MODE", "shadow")
    caplog.set_level(logging.INFO, logger=cws.logger.name)
    cws.open_web_search_turn("NORMAL", "EVIL\nINJECTED", _PLAIN, UID)
    assert _shadow_lines(caplog) == ["AUTO_WEB_SHADOW category=other context=other"]


# ── market-data queries, the sanitizer and the notes ──────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_auto_turn, _explicit_turn,
                                  lambda: cws.WebSearchTurn(user_id=UID, session_id="r")])
@pytest.mark.parametrize("query", ["Apple stock price", "AAPL price", "bitcoin price today",
                                   "Apple market cap", "EUR USD exchange rate", "VIX level",
                                   "DXY dollar index", "Apple price target",
                                   # the terse model-written quote queries (review 2026-10-09)
                                   "bitcoin today", "Apple stock today", "Nasdaq close today",
                                   "S&P level today", "euro dollar rate today", "gold today",
                                   "Tesla shares now", "oil right now", "treasury yields today"])
async def test_a_market_data_query_is_refused_on_every_tier(tiers, make, query):
    led, brave = tiers
    turn = make()
    out = await cws.run_web_search(turn, query)
    assert out["status"] == cws.STATUS_REFUSED and out["result_count"] == 0
    assert "error" not in out and "upstream" not in out, "a refusal is never a refundable failure"
    assert led.claims == [] and brave.calls == []
    assert not cws.web_results_delivered(out)
    # The turn's one search is not used up: a valid query still searches.
    ok = await cws.run_web_search(turn, "Apple DOJ lawsuit")
    assert ok["status"] == "ok" and len(brave.calls) == 1


@pytest.mark.parametrize("query", ["Vision Pro price", "Apple DOJ lawsuit 2025", "OpenAI revenue",
                                   "Nvidia guidance Q3", "Tesla recall", "Apple stock split today",
                                   "gold demand today", "Fed rate decision", "Dollar General lawsuit",
                                   "Apple shares buyback program", "Apple iPhone launch today",
                                   "index fund fees"])
def test_a_non_market_query_is_not_refused(query):
    assert cws._is_market_data_query(query) is False


@pytest.mark.asyncio
async def test_the_automatic_path_goes_through_the_sanitizer(tiers):
    led, brave = tiers
    out = await cws.run_web_search(
        _auto_turn(), "Apple lawsuit $391 billion 25% jane.doe@example.com https://evil.example/x 2025")
    assert out["status"] == "ok"
    (sent,) = brave.calls
    for gone in ("391", "billion", "25%", "@", "evil", "http"):
        assert gone not in sent, (gone, sent)
    assert sent.split()[:2] == ["Apple", "lawsuit"] and "2025" in sent


@pytest.mark.asyncio
async def test_an_invalid_automatic_query_claims_nothing(tiers):
    led, brave = tiers
    out = await cws.run_web_search(_auto_turn(), "$391 25%")
    assert out == cws._INVALID_QUERY and led.claims == [] and brave.calls == []


@pytest.mark.parametrize("status", [cws.STATUS_DAILY_LIMIT, cws.STATUS_DISABLED, cws.STATUS_UNAVAILABLE])
def test_the_automatic_tier_never_announces_a_search_that_did_not_run(status):
    out = cws.WebSearchOutcome(status=status, upstream_error=True).for_model(tier=cws.TIER_AUTO)
    assert out["note"] == cws._NOTE_AUTO_NOT_RUN
    assert "daily web-search limit" not in out["note"] and "has been reached" not in out["note"]
    explicit = cws.WebSearchOutcome(status=status, upstream_error=True).for_model(tier=cws.TIER_EXPLICIT)
    assert explicit["note"] != cws._NOTE_AUTO_NOT_RUN
    # The refund flag is unchanged by the tier.
    assert ("upstream" in out) == ("upstream" in explicit)


@pytest.mark.parametrize("note", [cws._NOTE_OK, cws._NOTE_NO_RESULTS, cws._NOTE_DAILY_LIMIT,
                                  cws._NOTE_UNAVAILABLE, cws._NOTE_DISABLED, cws._NOTE_AUTO_NOT_RUN,
                                  cws._NOTE_REFUSED, cws._NOTE_REPEAT, cws._NOTE_DEFERRED,
                                  cws._NOTE_OK_REPORT, cws._NOTE_NO_RESULTS_REPORT,
                                  cws._NOTE_DAILY_LIMIT_REPORT, cws._NOTE_UNAVAILABLE_REPORT,
                                  cws._NOTE_DISABLED_REPORT])
def test_every_note_names_no_vendor_and_no_url(note):
    low = note.lower()
    for word in ("brave", "google", "gemini", "bing", "http", "www."):
        assert word not in low, word
    assert "Caydex" in cws._NOTE_OK and "use Caydex's figure" in cws._NOTE_OK


def test_every_note_constant_is_in_the_vendor_scan():
    """A new `_NOTE_*` must join the scan above (it reaches the model)."""
    import inspect
    src = inspect.getsource(test_every_note_names_no_vendor_and_no_url)
    notes = {n for n in vars(cws) if n.startswith("_NOTE_")}
    params = inspect.getsource(inspect.getmodule(test_every_note_names_no_vendor_and_no_url))
    block = params[params.index('@pytest.mark.parametrize("note"'):params.index("def test_every_note_names_no_vendor_and_no_url")]
    assert notes == set(re.findall(r"cws\.(_NOTE_\w+)", block)), notes
    assert src


@pytest.mark.asyncio
async def test_a_switch_flipped_mid_turn_disables_that_tier(tiers, monkeypatch):
    led, brave = tiers
    auto = _auto_turn(session="a")
    explicit = _explicit_turn(session="b")
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_MODE", "shadow")
    monkeypatch.setattr(cws.settings, "CHAT_WEB_SEARCH_ALL_CHATS_ENABLED", False)
    a = await cws.run_web_search(auto, "Apple recall")
    b = await cws.run_web_search(explicit, "Apple DOJ")
    assert a["status"] == b["status"] == "disabled" and led.claims == [] and brave.calls == []
    assert a["note"] == cws._NOTE_AUTO_NOT_RUN and b["note"] == cws._NOTE_DISABLED


@pytest.mark.asyncio
async def test_the_log_line_names_the_tier_and_counts_only(tiers, caplog):
    caplog.set_level(logging.INFO, logger=cws.logger.name)
    await cws.run_web_search(_auto_turn(), "QUERYMARKER Apple lawsuit")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("REPORT_WEB_SEARCH ")]
    assert len(lines) == 1 and "tier=auto" in lines[0] and "units=3" in lines[0]
    assert "QUERYMARKER" not in caplog.text and "reuters" not in caplog.text.lower()


def _run_web_search_code() -> str:
    """`run_web_search`'s CODE only: the function's AST, docstring dropped, unparsed — never the
    module docstring or a comment, which named the guarantee while the code could drop it."""
    import ast
    tree = ast.parse(Path(cws.__file__).read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_web_search")
    if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(getattr(fn.body[0], "value", None), ast.Constant):
        fn.body = fn.body[1:]
    return ast.unparse(fn)


#: The synchronous election behind "one web search per question" (`test_asc_review_resubmit` ties
#: the review notes to it): the GUARD and the assignment together — dropping the guard makes every
#: call start a new search while the assignment alone would still match.
ONE_SEARCH_ELECTION = "if turn._task is None:\n        turn._task = asyncio.ensure_future(_search_once("


def test_one_search_per_turn_is_still_pinned_in_the_service():
    """Final review 2026-10-09: the old check read the module text, where the token sat only in the
    docstring — deleting the election left it green. Bound to `run_web_search`'s code."""
    assert ONE_SEARCH_ELECTION in _run_web_search_code()


def test_the_one_search_guard_is_not_vacuous():
    code = _run_web_search_code()
    unguarded = code.replace("if turn._task is None:\n        turn._task", "turn._task", 1)
    assert ONE_SEARCH_ELECTION not in unguarded
    assert "ONE SEARCH PER TURN" not in code, "the docstring token is not what this reads"


# ── review 2026-10-09: Caydex's tools first (the deferral) and the latch race ──


@pytest.mark.asyncio
async def test_an_automatic_search_beside_caydex_tools_is_deferred_and_claims_nothing(tiers, caplog):
    led, brave = tiers
    turn = _auto_turn()
    turn.note_tool_round(("check_company_financials", "web_search"))
    with caplog.at_level(logging.INFO, logger=cws.logger.name):
        out = await cws.run_web_search(turn, "Apple DOJ lawsuit")
    assert out["status"] == cws.STATUS_DEFERRED and out["deferred"] is True
    assert "upstream" not in out and "error" not in out and out["results"] == []
    assert led.claims == [] and brave.calls == [] and turn._task is None
    assert not turn.spent_a_unit()
    line = [r.getMessage() for r in caplog.records if "REPORT_WEB_SEARCH_DEFERRED" in r.getMessage()]
    assert line == ["REPORT_WEB_SEARCH_DEFERRED tier=auto reason=caydex_tools_in_round"]
    # The next round, after Caydex's results: the search runs, and the turn's one search is fresh.
    turn.note_tool_round(("web_search",))
    out = await cws.run_web_search(turn, "Apple DOJ lawsuit")
    assert out["status"] == "ok" and "repeat_note" not in out
    assert len(led.claims) == 3 and len(brave.calls) == 1


@pytest.mark.asyncio
async def test_a_lone_automatic_search_runs_and_explicit_tiers_are_never_deferred(tiers):
    led, brave = tiers
    lone = _auto_turn(session="s1")
    lone.note_tool_round(("web_search", "web_search"))
    assert (await cws.run_web_search(lone, "Apple recall"))["status"] == "ok"
    explicit = _explicit_turn(session="s2", msg="search the web for the Apple antitrust case")
    explicit.note_tool_round(("get_ticker_news", "web_search"))
    assert (await cws.run_web_search(explicit, "Apple antitrust case"))["status"] == "ok"
    # A turn whose search already ran replays it, whatever else the round calls.
    lone.note_tool_round(("get_ticker_news", "web_search"))
    replay = await cws.run_web_search(lone, "Apple something else")
    assert replay["status"] == "ok" and replay["repeat_note"]
    assert len(brave.calls) == 2


@pytest.mark.parametrize("junk", [None, 5, "web_search", [1, None, "x"], object()])
def test_note_tool_round_never_raises(tiers, junk):
    turn = _auto_turn()
    turn.note_tool_round(junk)
    assert isinstance(turn._round_names, frozenset)


@pytest.mark.asyncio
async def test_a_refund_after_an_account_race_reopens_the_automatic_tier(tiers, monkeypatch):
    """Turn A holds the account's last unit while turn B's account claim comes back capped and
    latches; A's search then provably did not run and hands every unit back — the bucket has a
    unit free, so the latch must not keep the tier closed until ET midnight."""
    led, _ = tiers
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_PER_ACCOUNT_DAILY", 1)
    gate = asyncio.Event()

    class _Held(_Brave):
        async def __call__(self, query, **kw):
            self.calls.append(query)
            self.started.set()
            await gate.wait()
            raise bs.BraveSearchNotConfiguredException("x", not_run=True)
    held = _Held()
    monkeypatch.setattr(cws.brave_search, "web_search", held)
    a = _auto_turn(session="sa", msg="any lawsuits against Apple?")
    task = asyncio.ensure_future(cws.run_web_search(a, "Apple DOJ lawsuit"))
    await held.started.wait()
    b = _auto_turn(session="sb", msg="any recalls at Apple?")
    out_b = await cws.run_web_search(b, "Apple recall")
    assert out_b["status"] == "daily_limit" and cws._latched(("account", UID))
    gate.set()
    assert (await task)["status"] == "unavailable"
    assert sum(led.counts.values()) == 0
    assert not cws._latched(("account", UID)) and not cws._latched(("global",))
    assert not cws._auto_budget_known_exhausted(UID)
    assert _decide(msg=_LAWSUIT).tier == cws.TIER_AUTO


@pytest.mark.asyncio
async def test_a_refund_of_the_automatic_global_bucket_clears_the_global_latch(tiers, monkeypatch):
    led, _ = tiers
    monkeypatch.setattr(cws.settings, "CHAT_AUTO_WEB_SEARCH_DAILY_CAP", 1)
    gate = asyncio.Event()

    class _Held(_Brave):
        async def __call__(self, query, **kw):
            self.calls.append(query)
            self.started.set()
            await gate.wait()
            raise bs.BraveSearchNotConfiguredException("x", not_run=True)
    held = _Held()
    monkeypatch.setattr(cws.brave_search, "web_search", held)
    task = asyncio.ensure_future(cws.run_web_search(_auto_turn(uid=UID), "Apple DOJ lawsuit"))
    await held.started.wait()
    out = await cws.run_web_search(_auto_turn(uid=UID2, session="s2"), "Apple recall")
    assert out["status"] == "daily_limit" and cws._latched(("global",))
    assert led.counts.get(ACCT(UID2), 0) == 0, "the capped step refunded B's account unit"
    gate.set()
    await task
    assert not cws._latched(("global",))
    assert _decide(msg=_LAWSUIT, uid=UID3).tier == cws.TIER_AUTO


@pytest.mark.asyncio
async def test_a_cancelled_frame_refund_also_clears_the_latch(tiers):
    cws._latch(("account", UID))
    cws._latch(("global",))
    cws._release_detached([(ACCT(UID), "auto account", ("account", UID)),
                           (cws._AUTO_WEB_SEARCH_BUCKET, "auto global", ("global",))])
    await asyncio.sleep(0.02)
    assert not cws._latched(("account", UID)) and not cws._latched(("global",))
    # A legacy two-field entry for a cross-account bucket maps to the global key.
    assert cws._latch_key_of((cws._REPORT_WEB_SEARCH_BUCKET, "report global")) == ("global",)
    assert cws._latch_key_of((ACCT(UID), "auto account")) is None


# ── final review 2026-10-09 ───────────────────────────────────────────────────


def test_would_defer_is_the_one_predicate(tiers):
    turn = _auto_turn()
    assert turn.would_defer() is False, "no round noted"
    turn.note_tool_round(("web_search",))
    assert turn.would_defer() is False, "a lone search"
    turn.note_tool_round(("check_company_financials", "web_search"))
    assert turn.would_defer() is True
    explicit = _explicit_turn(session="s9")
    explicit.note_tool_round(("check_company_financials", "web_search"))
    assert explicit.would_defer() is False, "explicit tiers are never deferred"
    turn._task = object()          # elected (any non-None task)
    assert turn.would_defer() is False, "a turn whose search was elected replays it"


def test_would_defer_never_raises(tiers):
    turn = _auto_turn()
    turn._round_names = None      # a corrupted state
    assert turn.would_defer() is False


@pytest.mark.parametrize("tier,report", [(cws.TIER_REPORT_EXPLICIT, True), (cws.TIER_EXPLICIT, False),
                                         (cws.TIER_AUTO, False), (None, False)])
@pytest.mark.parametrize("status", [cws.STATUS_OK, cws.STATUS_NO_RESULTS, cws.STATUS_DAILY_LIMIT,
                                    cws.STATUS_DISABLED, cws.STATUS_UNAVAILABLE])
def test_only_the_report_tier_notes_name_the_report(tier, report, status):
    """A chat with no report must not be told to "answer from Caydex's data and the report"."""
    note = cws.WebSearchOutcome(status=status, results=[{"n": 1}] if status == cws.STATUS_OK else [],
                                upstream_error=True).for_model(tier=tier)["note"]
    assert ("report" in note.lower()) is report, (tier, status, note)
    if tier != cws.TIER_AUTO or status in (cws.STATUS_OK, cws.STATUS_NO_RESULTS):
        assert "Caydex's data" in note


@pytest.mark.asyncio
async def test_a_non_report_turn_reads_the_non_report_note(tiers):
    out = await cws.run_web_search(_explicit_turn(), "Apple DOJ lawsuit")
    assert out["status"] == "ok" and out["note"] == cws._NOTE_OK
    rep = await cws.run_web_search(cws.WebSearchTurn(user_id=UID, session_id="r"), "Apple antitrust case")
    assert rep["note"] == cws._NOTE_OK_REPORT


# ── the market-data refusal reads the RAW query too; coins, market value, FX ──


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_auto_turn, _explicit_turn,
                                  lambda: cws.WebSearchTurn(user_id=UID, session_id="r")])
@pytest.mark.parametrize("query", [
    # digit-led markers the sanitizer would have stripped before the check
    "AAPL 52-week high", "Nvidia 52 week low", "Tesla stock 52-week range", "10-year treasury yield",
    "bitcoin above 100k", "Apple shares below $200",
    # coins by name, a market value, multi-word indices, a bare FX pair, closes and returns
    "ethereum price", "Ethereum price USD", "current ethereum price", "price of solana",
    "Solana price", "Dogecoin today", "Cardano price", "Nvidia market value", "Dow Jones today",
    "Nasdaq composite today", "USD JPY", "eur usd", "TSLA close yesterday", "AAPL close October 2026",
    "NVDA YTD return 2026", "Apple stock performance 2026", "stock market today", "Wall Street today",
    "bitcoin weekly gain", "yen trading level", "Tesla shares down today",
])
async def test_the_reviewers_market_queries_are_refused_on_every_tier(tiers, make, query):
    led, brave = tiers
    out = await cws.run_web_search(make(), query)
    assert out["status"] == cws.STATUS_REFUSED, (query, out)
    assert led.claims == [] and brave.calls == []
    assert "52" not in out["query"] and "100k" not in out["query"], "the raw text is never echoed"


@pytest.mark.parametrize("query", [
    "FTC closes probe Apple", "DOJ close case", "fair market value of options",
    "Apple stock performance awards", "smartphone market today", "Apple deal closed on Friday",
    "Trump today tariffs", "Apple One price", "Ethereum upgrade", "gold demand",
    "stocks under pressure tariffs", "CAD software Autodesk", "Apple stock movement plan",
])
def test_the_must_keep_twins_are_not_refused(query):
    assert cws._is_market_data_query(query) is False
    assert cws._is_market_data_query(cws.sanitize_web_query(query)) is False


@pytest.mark.asyncio
async def test_an_explicit_crypto_price_ask_is_never_forced_and_its_query_is_refused(tiers):
    """The reviewer's end-to-end: a CRYPTO chat, "search the web for the ethereum price" — the
    question is market data (round 1 is not forced to the web) and the model's query is refused."""
    led, brave = tiers
    turn = cws.open_web_search_turn("NORMAL", "CRYPTO", "search the web for the ethereum price",
                                    UID, "ETH", session_id="c1")
    assert turn is not None and turn.tier == cws.TIER_EXPLICIT and turn.market_data_ask is True
    assert cws.web_force_first(turn, {"web_search", "get_stock_chart_data"}) is None
    out = await cws.run_web_search(turn, "ethereum price")
    assert out["status"] == cws.STATUS_REFUSED and brave.calls == [] and led.claims == []


# ── the latch is cleared AFTER the refund lands, never latched across one ─────


async def _held_refund(monkeypatch):
    """`_refund_bucket` blocked on a gate; returns (gate, started)."""
    gate, started = asyncio.Event(), asyncio.Event()

    async def refund(bucket, what):
        started.set()
        await gate.wait()
    monkeypatch.setattr(cmt, "_refund_bucket", refund)
    return gate, started


@pytest.mark.asyncio
async def test_a_claim_capped_while_a_refund_is_in_flight_never_latches(tiers, monkeypatch):
    """The reviewer's race: A refunds its account unit; while that refund's RPC is in flight, B's
    claim on the same bucket comes back capped. The refund then frees the unit — so B must not
    latch the bucket until ET midnight."""
    gate, started = await _held_refund(monkeypatch)

    async def capped(bucket, cap, what):
        return "capped"
    monkeypatch.setattr(cmt, "_claim_bucket_status", capped)
    key = ("account", UID)
    a = asyncio.ensure_future(cws._release_claims([(ACCT(UID), "auto account", key)]))
    await started.wait()
    claimed: list = []
    assert await cws._claim_for_turn(_auto_turn(), claimed) == "capped"
    assert not cws._latched(key), "a refund overlapped the claim: no latch"
    gate.set()
    await a
    assert not cws._latched(key) and not cws._refunds_inflight.get(key)


@pytest.mark.asyncio
async def test_a_capped_answer_that_returns_after_a_refund_landed_never_latches(tiers, monkeypatch):
    """The skew case: B's claim is sent, A's refund starts AND lands, then B's capped answer
    returns (its row may predate the refund) — still no latch."""
    claim_gate, claim_started = asyncio.Event(), asyncio.Event()

    async def slow_capped(bucket, cap, what):
        claim_started.set()
        await claim_gate.wait()
        return "capped"

    async def refund(bucket, what):
        return None
    monkeypatch.setattr(cmt, "_claim_bucket_status", slow_capped)
    monkeypatch.setattr(cmt, "_refund_bucket", refund)
    key = ("account", UID)
    b = asyncio.ensure_future(cws._claim_for_turn(_auto_turn(), []))
    await claim_started.wait()
    await cws._release_claims([(ACCT(UID), "auto account", key)])
    claim_gate.set()
    assert await b == "capped"
    assert not cws._latched(key)


@pytest.mark.asyncio
async def test_a_capped_claim_with_no_overlapping_refund_still_latches(tiers, monkeypatch):
    """The control (the guard is not vacuous): no refund overlaps → the latch is set."""
    async def capped(bucket, cap, what):
        return "capped"
    monkeypatch.setattr(cmt, "_claim_bucket_status", capped)
    assert await cws._claim_for_turn(_auto_turn(), []) == "capped"
    assert cws._latched(("account", UID))


@pytest.mark.asyncio
async def test_a_turns_own_refund_does_not_block_its_global_latch(tiers, monkeypatch):
    """The auto-global unit and the global cap share the ("global",) key: a turn whose global-cap
    step comes back capped refunds its own earlier units — that must not cancel the correct latch
    (the decision is taken before its own refunds)."""
    async def status(bucket, cap, what):
        return "capped" if bucket == GLOBAL else "ok"

    async def refund(bucket, what):
        return None
    monkeypatch.setattr(cmt, "_claim_bucket_status", status)
    monkeypatch.setattr(cmt, "_refund_bucket", refund)
    assert await cws._claim_for_turn(_auto_turn(), []) == "capped"
    assert cws._latched(("global",))


@pytest.mark.asyncio
async def test_a_failing_refund_still_unlatches_and_balances_the_counter(tiers, monkeypatch):
    async def boom(bucket, what):
        raise RuntimeError("rpc down")
    monkeypatch.setattr(cmt, "_refund_bucket", boom)
    key = ("account", UID)
    cws._latch(key)
    with pytest.raises(RuntimeError):
        await cws._release_claims([(ACCT(UID), "auto account", key)])
    assert not cws._latched(key) and not cws._refunds_inflight.get(key)


def test_the_uncalled_turnless_claim_helpers_are_gone():
    assert not hasattr(cws, "_claim_report_web_search")
    assert not hasattr(cws, "_release_report_web_search")
