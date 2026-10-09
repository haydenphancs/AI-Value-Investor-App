"""The chat system instruction carries today's date (ET) — once, in a fixed place (2026-10-08).

No chat instruction carried a date, so "the latest quarter", "this year" and the age of a filing
or headline were judged against the model's training cut-off. `chat_service._today_line()` puts
ONE server-clock line on every build — tool-less, fallback and continuation included — after
ADVICE_BOUNDARY and the reader lens, before the persona, the subject line and every fence. It is
left OUT of the two builds whose answer is stored and replayed to other users: the starter warm
(`include_today_line=False`) and a deep dive whose brief may enter the shared 24 h cache.

No network: the builder is pure string assembly and the doors are stubbed at their seams.
"""

from __future__ import annotations


import inspect
import io
import itertools
import logging
import re
import tokenize
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.chat_service as cs
from app.config import settings
from app.services.agents.chat_tools import TOOL_DESCRIPTIONS
from app.services.agents.persona_config import ADVICE_BOUNDARY, IDENTITY_RULE
from app.services.chat_service import ChatService

_FIXED = datetime(2026, 10, 8, 18, 5, tzinfo=timezone.utc)     # 14:05 ET (EDT)
_LINE = "Today is Thursday, Oct 8, 2026 (US Eastern time)."
_MARK = "Today is "
_ASSET_TYPES = ["STOCK", "NORMAL", "ETF", "CRYPTO", "INDEX", "COMMODITY"]
_SYMBOL = {"NORMAL": None, "INDEX": "^GSPC", "COMMODITY": "GCUSD", "CRYPTO": "BTCUSD",
           "ETF": "SPY", "STOCK": "AAPL"}
_LENS = "\n\nUSER PREFERENCES (how this reader likes to learn): beginner.\n"
_REPORT_BLOCK = "The user is viewing the in-depth Cay research report for Apple Inc. (AAPL)."


@pytest.fixture(autouse=True)
def _pinned_clock(monkeypatch):
    monkeypatch.setattr(cs, "_now_et", lambda: _FIXED)
    monkeypatch.setattr(settings, "CHAT_REPORT_VOICE_ENABLED", True)


def _svc() -> ChatService:
    return ChatService.__new__(ChatService)


def _instr(asset_type="STOCK", session_type="NORMAL", **kw):
    return _svc()._build_system_instruction(session_type, _SYMBOL[asset_type],
                                            asset_type=asset_type, **kw)


# ── the line itself ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("now, expected", [
    (_FIXED, "Thursday, Oct 8, 2026"),
    # 03:59 UTC on the 9th is still the 8th in New York.
    (datetime(2026, 10, 9, 3, 59, tzinfo=timezone.utc), "Thursday, Oct 8, 2026"),
    (datetime(2026, 10, 9, 4, 0, tzinfo=timezone.utc), "Friday, Oct 9, 2026"),
    # Winter (EST, UTC-5) and the day DST ends.
    (datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc), "Monday, Jan 5, 2026"),
    (datetime(2026, 1, 6, 4, 59, tzinfo=timezone.utc), "Monday, Jan 5, 2026"),
    (datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), "Sunday, Nov 1, 2026"),
    # A naive instant is taken as UTC.
    (datetime(2026, 7, 1, 14, 5), "Wednesday, Jul 1, 2026"),
])
def test_the_line_is_the_et_date(now, expected):
    assert cs._today_line(now).startswith(f"\nToday is {expected} (US Eastern time).")


def test_the_line_carries_no_time_of_day():
    """The minute stamp changed the instruction every minute ahead of its largest spans (the
    persona, the enrichment, the report rule, the screen fence) and cost report-chat follow-ups
    the provider's implicit prefix-cache discount (fix round, 2026-10-08)."""
    line = cs._today_line(_FIXED)
    assert not re.search(r"\b[0-2]?[0-9]:[0-5][0-9]\b", line), line
    assert " ET." not in line and "14" not in line and "05" not in line


def test_the_instruction_is_byte_identical_all_et_day_and_changes_at_et_midnight(monkeypatch):
    def at(*args):
        monkeypatch.setattr(cs, "_now_et", lambda: datetime(*args, tzinfo=timezone.utc))
        return _instr(reader_lens=_LENS, client_context=_REPORT_BLOCK, report_grounded=True)
    # 00:00 ET (04:00 UTC) → 23:59 ET (03:59 UTC next day): one instruction, byte for byte.
    first = at(2026, 10, 8, 4, 0)
    for moment in ((2026, 10, 8, 13, 31), (2026, 10, 8, 18, 5), (2026, 10, 8, 20, 59),
                   (2026, 10, 9, 3, 59)):
        assert at(*moment) == first, moment
    nxt = at(2026, 10, 9, 4, 0)
    assert nxt != first and "Friday, Oct 9, 2026" in nxt and "Thursday" not in nxt


def test_the_line_is_server_text_with_no_tool_vendor_or_injection_word():
    line = cs._today_line(_FIXED)
    low = line.lower()
    for name in TOOL_DESCRIPTIONS:
        assert not re.search(rf"\b{re.escape(name)}\b", line), name
    for word in ("gemini", "google", "openai", "fmp", "brave", "language model", "disregard",
                 "ignore previous", "new system prompt", "<<<", "http"):
        assert word not in low, word
    assert "mention the date only when it matters" in line


def test_a_clock_failure_drops_the_line_not_the_turn(monkeypatch, caplog):
    def _boom():
        raise RuntimeError("clock")
    monkeypatch.setattr(cs, "_now_et", _boom)
    with caplog.at_level(logging.WARNING, logger=cs.__name__):
        instr = _instr()
    assert _MARK not in instr and instr.startswith(IDENTITY_RULE) and ADVICE_BOUNDARY in instr
    assert any("date line unavailable" in r.getMessage() for r in caplog.records)


# ── once, on every build shape ───────────────────────────────────────────────

_SHAPES = list(itertools.product(_ASSET_TYPES, [True, False], [True, False], [True, False],
                                 [{}, {"web_search_granted": True},
                                  {"web_search_unavailable": True},
                                  {"web_search_on_request": True}]))


@pytest.mark.parametrize("asset_type, tools_granted, is_deep_dive, grounded, web", _SHAPES)
def test_exactly_one_date_line_on_every_build_shape(asset_type, tools_granted, is_deep_dive,
                                                    grounded, web):
    instr = _instr(asset_type, tools_granted=tools_granted, is_deep_dive=is_deep_dive,
                   client_context=_REPORT_BLOCK if grounded else None,
                   report_grounded=grounded, **web)
    assert instr.count(_MARK) == 1 and instr.count(_LINE) == 1


@pytest.mark.parametrize("session_type, ref", [("BOOK", "2"), ("REPORT", "AAPL|lynch"),
                                               ("CONCEPT", None), ("JOURNEY", None)])
def test_the_line_is_present_for_learn_and_report_sessions(session_type, ref):
    instr = _svc()._build_system_instruction(session_type, None if session_type != "REPORT"
                                             else "AAPL", reference_id=ref, reader_lens=_LENS)
    assert instr.count(_LINE) == 1


def test_the_chip_generator_build_carries_it_too():
    instr = _svc()._build_system_instruction("NORMAL", None, asset_type="CRYPTO",
                                             tools_granted=False)
    assert instr.count(_LINE) == 1


# ── placement ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("asset_type", _ASSET_TYPES)
def test_the_line_follows_the_guards_and_the_lens_and_precedes_everything_turn_specific(asset_type):
    instr = _instr(asset_type, reader_lens=_LENS, client_context=_REPORT_BLOCK,
                   report_grounded=True, web_search_granted=True,
                   company_profile_summary="Company Profile for AAPL: | CEO: Tim Cook")
    pos = instr.index(_LINE)
    assert instr.startswith(IDENTITY_RULE)
    assert instr.index(ADVICE_BOUNDARY) < pos
    assert instr.index("WHAT YOU KNOW:") < pos
    assert instr.index(_LENS) < pos
    fence = instr.index("<<<CLIENT_CONTEXT>>>")
    assert pos < fence
    assert pos < instr.index("THE REPORT ON SCREEN:") and pos < instr.index("WEB RESULTS:")
    if _SYMBOL[asset_type]:
        assert pos < instr.index("You are currently helping analyze")
    if asset_type in ChatService._ASSET_PERSONAS:
        assert pos < instr.index(ChatService._ASSET_PERSONAS[asset_type])
    fenced = instr[fence:instr.index("<<<END_CLIENT_CONTEXT>>>")]
    assert _MARK not in fenced


def test_the_line_follows_the_learn_sentence_and_precedes_the_book_voice():
    instr = _svc()._build_system_instruction("BOOK", None, reference_id="2", reader_lens=_LENS,
                                             client_context="the guide outline")
    pos = instr.index(_LINE)
    assert instr.index("Since this is a learning topic") < pos < instr.index("BOOK GUIDE VOICE")


def test_the_line_precedes_the_report_voice():
    instr = _svc()._build_system_instruction("REPORT", "AAPL", reference_id="AAPL|lynch",
                                             reader_lens=_LENS)
    assert instr.index(_LENS) < instr.index(_LINE) < instr.index("REPORT CHAT MODE — ")


def test_the_prefix_before_the_line_is_identical_at_any_time(monkeypatch):
    """Prompt caching: the stable prefix (identity → boundary → lens) must not move with
    the clock; only the line itself does."""
    a = _instr(reader_lens=_LENS)
    monkeypatch.setattr(cs, "_now_et", lambda: datetime(2027, 3, 1, 12, tzinfo=timezone.utc))
    b = _instr(reader_lens=_LENS)
    assert a != b
    assert a[:a.index(_MARK)] == b[:b.index(_MARK)]


# ── the opt-out is exactly the line ──────────────────────────────────────────

@pytest.mark.parametrize("asset_type", _ASSET_TYPES)
@pytest.mark.parametrize("tools_granted", [True, False])
def test_include_today_line_false_removes_the_line_and_nothing_else(asset_type, tools_granted):
    kw = dict(tools_granted=tools_granted, reader_lens=_LENS, client_context=_REPORT_BLOCK,
              report_grounded=True)
    with_line = _instr(asset_type, **kw)
    without = _instr(asset_type, include_today_line=False, **kw)
    assert _MARK not in without
    assert with_line.replace(cs._today_line(_FIXED), "", 1) == without


# ── the doors: the starter warm and a cacheable deep dive leave it out ────────

def _strip_comments(src: str) -> str:
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        out.append(tok)
    return tokenize.untokenize(out)


def test_the_starter_warm_generates_without_the_line():
    from app.services import chat_starter_warm_service as warm
    body = _strip_comments(inspect.getsource(warm._warm_one))
    call = body[body.index("svc.generate_response("):]
    call = call[:call.index(")\n")]
    assert "include_today_line=False" in call, call


async def _prep(monkeypatch, *, message, stock_id, context_type, reference_id, resolved,
                asset_type, history=None, include_today_line=True):
    import app.services.chat_context_resolver as res
    monkeypatch.setattr(res, "get_chat_context_resolver",
                        lambda: SimpleNamespace(resolve=AsyncMock(return_value=resolved)))
    svc = _svc()
    svc.supabase = object()
    svc.fmp = object()
    svc._get_recent_messages = lambda *a, **k: list(history or [])
    svc._retrieve_context = AsyncMock(return_value=([], []))
    svc._condense_history = AsyncMock(return_value="")
    svc._detect_asset_type = lambda *a, **k: asset_type
    svc._get_profit_summary = AsyncMock(return_value=None)
    svc._get_snapshot_summary = AsyncMock(return_value=None)
    svc._get_company_profile_summary = AsyncMock(return_value=None)
    svc._check_deep_dive_cache = lambda *a, **k: None
    svc._deterministic_widget = AsyncMock(return_value=None)
    return await svc.prepare_stream_generation(
        "sess", message, stock_id=stock_id, context_type=context_type,
        reference_id=reference_id, include_today_line=include_today_line,
    )


_ETF_BLOCK = "The user is viewing the ETF detail screen for SPDR S&P 500 (SPY). Price $510.20."


@pytest.mark.asyncio
async def test_a_cacheable_deep_dive_is_built_without_the_line(monkeypatch):
    prep = await _prep(monkeypatch, message="Give me a comprehensive deep dive", stock_id="SPY",
                       context_type="ETF", reference_id="SPY", resolved=_ETF_BLOCK,
                       asset_type="ETF")
    assert prep["is_deep_dive"] is True and prep["deep_dive_context"] == _ETF_BLOCK
    assert _MARK not in prep["system_instruction"]
    assert _MARK not in prep["system_instruction_no_tools"]


@pytest.mark.asyncio
async def test_a_deep_dive_that_will_not_be_cached_keeps_the_line(monkeypatch):
    prep = await _prep(monkeypatch, message="Give me a comprehensive deep dive", stock_id="SPY",
                       context_type="ETF", reference_id="SPY", resolved=_ETF_BLOCK,
                       asset_type="ETF", history=[{"role": "user", "content": "hi"}])
    assert prep["is_deep_dive"] is True and prep["deep_dive_context"] is None
    assert prep["system_instruction"].count(_LINE) == 1


@pytest.mark.asyncio
async def test_an_ordinary_turn_carries_the_line_in_both_instructions(monkeypatch):
    prep = await _prep(monkeypatch, message="how is SPY doing", stock_id="SPY",
                       context_type="ETF", reference_id="SPY", resolved=_ETF_BLOCK,
                       asset_type="ETF")
    assert prep["system_instruction"].count(_LINE) == 1
    assert prep["system_instruction_no_tools"].count(_LINE) == 1


@pytest.mark.asyncio
async def test_the_caller_flag_wins_on_the_stream_door(monkeypatch):
    prep = await _prep(monkeypatch, message="how is SPY doing", stock_id="SPY",
                       context_type="ETF", reference_id="SPY", resolved=_ETF_BLOCK,
                       asset_type="ETF", include_today_line=False)
    assert _MARK not in prep["system_instruction"]


def _stub_send(svc, monkeypatch, gem):
    import app.services.chat_context_resolver as res
    monkeypatch.setattr(res, "get_chat_context_resolver",
                        lambda: SimpleNamespace(resolve=AsyncMock(return_value=None)))
    svc.supabase = object()
    svc.fmp = object()
    svc.gemini = gem
    svc._get_recent_messages = lambda *a, **k: []
    svc._retrieve_context = AsyncMock(return_value=([], []))
    svc._condense_history = AsyncMock(return_value="")
    svc._detect_asset_type = lambda *a, **k: "STOCK"
    svc._get_profit_summary = AsyncMock(return_value=None)
    svc._get_snapshot_summary = AsyncMock(return_value=None)
    svc._get_company_profile_summary = AsyncMock(return_value=None)
    svc._is_deep_dive_request = lambda *a, **k: False
    svc._deterministic_widget = AsyncMock(return_value=None)


@pytest.mark.asyncio
async def test_the_send_door_and_its_tool_less_fallback_both_carry_the_line(monkeypatch):
    seen = {}

    class _Gem:
        async def generate_with_tools(self, **kw):
            seen["tools"] = kw["system_instruction"]
            raise RuntimeError("function calling exploded")

        async def generate_text(self, **kw):
            seen["fallback"] = kw["system_instruction"]
            return {"text": "plain answer", "tokens_used": 3}

    svc = _svc()
    _stub_send(svc, monkeypatch, _Gem())
    out = await svc.generate_response("sess", "how is AAPL doing?", stock_id="AAPL")
    assert out["degraded"] == "no_tools"
    assert seen["tools"].count(_LINE) == 1 and seen["fallback"].count(_LINE) == 1


@pytest.mark.asyncio
async def test_the_send_door_flag_removes_it_from_both_builds(monkeypatch):
    seen = {}

    class _Gem:
        async def generate_with_tools(self, **kw):
            seen["tools"] = kw["system_instruction"]
            raise RuntimeError("function calling exploded")

        async def generate_text(self, **kw):
            seen["fallback"] = kw["system_instruction"]
            return {"text": "plain answer", "tokens_used": 3}

    svc = _svc()
    _stub_send(svc, monkeypatch, _Gem())
    await svc.generate_response("sess", "how is AAPL doing?", stock_id="AAPL",
                                include_today_line=False)
    assert _MARK not in seen["tools"] and _MARK not in seen["fallback"]


def test_the_quiet_cacheability_check_logs_nothing(caplog):
    svc = _svc()
    bad = dict(cache_safe=True, history=[{"role": "user"}], reader_lens=None, stock_id="SPY",
               asset_type="ETF", context_type="CRYPTO", reference_id="QQQ")
    with caplog.at_level(logging.INFO, logger=cs.__name__):
        assert svc._deep_dive_cacheable(**bad, log=False) is False
        assert svc._deep_dive_cacheable(**{**bad, "history": []}, log=False) is False
        assert svc._deep_dive_cacheable(**{**bad, "history": [], "context_type": "ETF"},
                                        log=False) is False
    assert not [r for r in caplog.records if "deep dive: not cacheable" in r.getMessage()]
    with caplog.at_level(logging.INFO, logger=cs.__name__):
        svc._deep_dive_cacheable(**bad)
    assert [r for r in caplog.records if "deep dive: not cacheable" in r.getMessage()]


def test_a_failing_cacheability_check_keeps_the_line(caplog):
    svc = _svc()

    def _boom(**k):
        raise RuntimeError("bad")
    svc._deep_dive_cacheable = _boom
    with caplog.at_level(logging.WARNING, logger=cs.__name__):
        assert svc._today_line_allowed(
            True, is_deep_dive=True, context="x", stock_id="SPY", cache_safe=True, history=[],
            reader_lens=None, asset_type="ETF", context_type="ETF", reference_id="SPY",
        ) is True
    assert any("keeping the line" in r.getMessage() for r in caplog.records)


def test_both_doors_route_through_the_one_decision():
    """Source scan, comments stripped and def-bound: each door decides the flag through
    `_today_line_allowed` inside its ONE instr_kwargs dict, so the tool round, the tool-less
    fallback and the merge/continuation instruction cannot disagree."""
    for fn in (ChatService.generate_response, ChatService.prepare_stream_generation):
        body = _strip_comments(inspect.getsource(fn))
        kwargs = body[body.index("instr_kwargs = dict("):]
        kwargs = kwargs[:kwargs.index("system_instruction = self._build_system_instruction(")]
        assert "include_today_line=self._today_line_allowed(" in kwargs, fn.__name__
        # …and the decision is made from the CALLER's flag, not a literal.
        decision = kwargs[kwargs.index("self._today_line_allowed("):]
        assert decision.split("(", 1)[1].lstrip().startswith("include_today_line,"), decision
