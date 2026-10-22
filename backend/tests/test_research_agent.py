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
    signal_of_confidence = None


def _agent(gem: _Gem, monkeypatch, handlers: Dict[str, Any] | None = None) -> ResearchAgent:
    agent = ResearchAgent.__new__(ResearchAgent)
    agent.gemini = gem
    agent.fmp = object()
    agent.persona = get_persona_config("warren_buffett")
    monkeypatch.setattr(ra, "build_fmp_tool_declarations", lambda: object())
    monkeypatch.setattr(ra, "build_tool_handlers", lambda fmp, **_kw: handlers or {})
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


# ── C11: the reply to the LAST round's tool results is read, not discarded ───────────────
#
# With a tool call in every one of the MAX_AGENTIC_ROUNDS rounds, the final round still sends
# its tool results and receives a reply (MAX_AGENTIC_ROUNDS + 1 sends in all). That reply was
# never parsed: a `research_complete` or a written synthesis in it — the one turn that holds
# every fetched datum — was dropped, AGENTIC_ROUNDS_EXHAUSTED was logged falsely, and a sixth
# call produced a single-pass analysis that sees none of the tool data the 20 credits bought.

def _tool_rounds() -> List[_Resp]:
    return [fc("fetch_more_news", ticker="AAPL")] * MAX_AGENTIC_ROUNDS


async def _ok(args):
    return {"ok": 1}


def _exhausted_logged(caplog) -> bool:
    return any("AGENTIC_ROUNDS_EXHAUSTED" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_research_complete_in_the_reply_to_the_last_round_is_used(monkeypatch, caplog):
    gem = _Gem(_tool_rounds() + [fc("research_complete", summary="REAL SYNTHESIS")],
               fallback_text="SINGLE-PASS FALLBACK (no tool data)")
    agent = _agent(gem, monkeypatch, {"fetch_more_news": _ok})
    with caplog.at_level("WARNING", logger=ra.__name__):
        out = await agent._agentic_research(_Out(), "EVIDENCE")
    assert out == "REAL SYNTHESIS"
    assert gem.fallback_calls == [], "no sixth call: the synthesis was already paid for"
    assert len(gem.chat.sent) == MAX_AGENTIC_ROUNDS + 1
    assert isinstance(gem.chat.sent[-1], list), "the last round's tool results were still sent"
    assert not _exhausted_logged(caplog), "the model finished — not an exhaustion"


@pytest.mark.asyncio
async def test_prose_in_the_reply_to_the_last_round_is_returned(monkeypatch, caplog):
    gem = _Gem(_tool_rounds() + [text("FINDINGS: margins expanding, net cash.")],
               fallback_text="FALLBACK")
    agent = _agent(gem, monkeypatch, {"fetch_more_news": _ok})
    with caplog.at_level("WARNING", logger=ra.__name__):
        out = await agent._agentic_research(_Out(), "EVIDENCE")
    assert out == "FINDINGS: margins expanding, net cash."
    assert gem.fallback_calls == []
    assert not _exhausted_logged(caplog)


@pytest.mark.asyncio
async def test_last_reply_research_complete_prefers_the_models_prose_over_the_summary(monkeypatch):
    last = _Resp([_Part(text="FULL PROSE"), _Part(fc=_FC("research_complete", {"summary": "short"}))])
    gem = _Gem(_tool_rounds() + [last])
    agent = _agent(gem, monkeypatch, {"fetch_more_news": _ok})
    assert await agent._agentic_research(_Out(), "EVIDENCE") == "FULL PROSE"
    assert gem.fallback_calls == []


@pytest.mark.asyncio
async def test_last_reply_research_complete_wins_over_a_pending_tool_call(monkeypatch):
    """Same rule as a round: `research_complete` ends the research whatever sits beside it."""
    last = _Resp([_Part(fc=_FC("fetch_more_news", {"ticker": "AAPL"})),
                  _Part(fc=_FC("research_complete", {"summary": "DONE"}))])
    gem = _Gem(_tool_rounds() + [last])
    agent = _agent(gem, monkeypatch, {"fetch_more_news": _ok})
    assert await agent._agentic_research(_Out(), "EVIDENCE") == "DONE"
    assert gem.fallback_calls == []


@pytest.mark.asyncio
async def test_an_empty_last_reply_falls_back_without_claiming_exhaustion(monkeypatch, caplog):
    gem = _Gem(_tool_rounds() + [_Resp([])], fallback_text="FALLBACK")
    agent = _agent(gem, monkeypatch, {"fetch_more_news": _ok})
    with caplog.at_level("WARNING", logger=ra.__name__):
        out = await agent._agentic_research(_Out(), "EVIDENCE")
    assert out == "FALLBACK" and len(gem.fallback_calls) == 1
    assert not _exhausted_logged(caplog)
    assert any("EMPTY" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_whitespace_only_last_reply_is_empty_not_findings(monkeypatch):
    gem = _Gem(_tool_rounds() + [text("  \n ")], fallback_text="FALLBACK")
    agent = _agent(gem, monkeypatch, {"fetch_more_news": _ok})
    assert await agent._agentic_research(_Out(), "EVIDENCE") == "FALLBACK"


@pytest.mark.asyncio
async def test_a_last_reply_research_complete_with_nothing_in_it_falls_back(monkeypatch):
    """No prose and no summary: shipping "Research complete." would make a placeholder the
    whole deep-research premium, which is what the exhaustion branch exists to prevent."""
    gem = _Gem(_tool_rounds() + [fc("research_complete", summary="  ")], fallback_text="FALLBACK")
    agent = _agent(gem, monkeypatch, {"fetch_more_news": _ok})
    out = await agent._agentic_research(_Out(), "EVIDENCE")
    assert out == "FALLBACK" and len(gem.fallback_calls) == 1


@pytest.mark.asyncio
async def test_prose_beside_a_pending_tool_call_is_a_preamble_not_findings(monkeypatch, caplog):
    """The model is still asking for data: the budget is genuinely exhausted, and its "let me
    check…" line must not become the report's findings."""
    last = _Resp([_Part(text="Let me check the cash flow statement next."),
                  _Part(fc=_FC("fetch_extended_financials", {"ticker": "AAPL"}))])
    gem = _Gem(_tool_rounds() + [last], fallback_text="SINGLE-PASS SYNTHESIS")
    agent = _agent(gem, monkeypatch, {"fetch_more_news": _ok})
    with caplog.at_level("WARNING", logger=ra.__name__):
        out = await agent._agentic_research(_Out(), "EVIDENCE")
    assert out == "SINGLE-PASS SYNTHESIS" and len(gem.fallback_calls) == 1
    assert _exhausted_logged(caplog)


@pytest.mark.asyncio
async def test_true_exhaustion_still_logs_the_marker_with_the_pending_tool(monkeypatch, caplog):
    """Negative control for the tests above: the marker still fires when it is true."""
    gem = _Gem(_tool_rounds() + [fc("fetch_sector_performance")], fallback_text="SYN")
    agent = _agent(gem, monkeypatch, {"fetch_more_news": _ok})
    with caplog.at_level("WARNING", logger=ra.__name__):
        assert await agent._agentic_research(_Out(), "EVIDENCE") == "SYN"
    msgs = [r.getMessage() for r in caplog.records if "AGENTIC_ROUNDS_EXHAUSTED" in r.getMessage()]
    assert len(msgs) == 1 and "pending=fetch_sector_performance" in msgs[0]
    assert len(gem.chat.sent) == MAX_AGENTIC_ROUNDS + 1, "no extra model call beyond the budget"


@pytest.mark.asyncio
async def test_an_in_loop_research_complete_with_no_summary_falls_back_not_a_placeholder(monkeypatch):
    gem = _Gem([_Resp([_Part(fc=_FC("research_complete", {}))])], fallback_text="FALLBACK")
    agent = _agent(gem, monkeypatch)
    out = await agent._agentic_research(_Out(), "EVIDENCE")
    assert out == "FALLBACK" and "Research complete." not in out
    assert len(gem.fallback_calls) == 1


@pytest.mark.asyncio
async def test_an_in_loop_research_complete_prefers_prose_over_summary(monkeypatch):
    first = _Resp([_Part(text="PROSE"), _Part(fc=_FC("research_complete", {"summary": "S"}))])
    gem = _Gem([first])
    agent = _agent(gem, monkeypatch)
    assert await agent._agentic_research(_Out(), "EVIDENCE") == "PROSE"
    assert gem.fallback_calls == []


# ── dividends and buybacks: the checked figures, never FMP's raw lines (2026-10-22) ──
#
# The deep door's cash-flow tools handed Gemini FMP's raw `commonStockRepurchased` /
# `commonDividendsPaid` / `netDividendsPaid` / `dividendsPaid`. On INTC those are RSU tax
# withholding (−$617M Q2 2026, −$181M Q2 2025: Intel bought back nothing) and a −$14.3B
# payment to a minority partner booked as a "dividend" — read by the model as cash returned
# to shareholders, in the free text that feeds Stage A. These drive the REAL loop and the
# REAL handlers: only the fetch and the model are fake.

import copy  # noqa: E402

from app.schemas.signal_of_confidence import (  # noqa: E402
    AnnualDividendSchema,
    DividendInfoSchema,
    SignalOfConfidenceDataPointSchema,
    SignalOfConfidenceResponse,
    SignalOfConfidenceSummarySchema,
)
from app.services.agents import fmp_tools  # noqa: E402

_RAW_RETURN_LINES = ("commonStockRepurchased", "commonDividendsPaid", "netDividendsPaid", "dividendsPaid")


def _cf_row(date, fy, period, *, buyback, dividend, cur="USD", **extra):
    return {
        "date": date, "symbol": "X", "reportedCurrency": cur, "fiscalYear": fy, "period": period,
        "operatingCashFlow": 2_345_000_000, "capitalExpenditure": -1_234_000_000,
        "freeCashFlow": 1_111_000_000, "commonStockRepurchased": buyback,
        "commonDividendsPaid": dividend, "netDividendsPaid": dividend, **extra,
    }


# FMP's rows as served: newest first, the two lines as FMP files them.
_INTC_ROWS = [
    _cf_row("2026-06-27", "2026", "Q2", buyback=-617_000_000, dividend=-14_300_000_000,
            dividendsPaid=-14_300_000_000),  # legacy spelling too: it must not leak either
    _cf_row("2026-03-28", "2026", "Q1", buyback=0, dividend=0),
    _cf_row("2025-06-28", "2025", "Q2", buyback=-181_000_000, dividend=0),
]
# Every magnitude those lines carry, in every spelling json.dumps can give a float/int.
_INTC_RAW_FIGURES = ("14300000000", "1.43e+10", "617000000", "6.17e+08", "181000000",
                     "1.81e+08", "14300.0", "617.0", "181.0")


def _soc(symbol, quarters, *, div_yield=0.0, bb_yield=0.0, bb_status="Low", div_status=None,
         dps=(), share_change=0.0, share_known=True, degraded=()):
    """A Signal of Confidence build as the service serves it (amounts in millions)."""
    return SignalOfConfidenceResponse(
        symbol=symbol,
        data_points=[
            SignalOfConfidenceDataPointSchema(
                period=p, dividend_amount=d, buyback_amount=b, cash_flow_reported=reported,
            )
            for p, d, b, reported in quarters
        ],
        summary=SignalOfConfidenceSummarySchema(
            dividend_yield=div_yield, buyback_yield=bb_yield, total_yield=div_yield + bb_yield,
            share_count_change=share_change, share_count_change_known=share_known,
            buyback_status=bb_status,
        ),
        dividend_info=(
            DividendInfoSchema(
                status=div_status,
                annual_dividends=[AnnualDividendSchema(year=y, per_share=v) for y, v in dps],
            )
            if div_status else None
        ),
        degraded=list(degraded),
    )


# INTC as the service builds it: the SEC check zeroed the withholding "buybacks" and the
# payer verdict (no common dividend since Q3 2024) refused the −14.3B line.
_INTC_SOC = _soc(
    "INTC",
    [("Q2 '25", 0.0, 0.0, True), ("Q3 '25", 0.0, 0.0, True), ("Q4 '25", 0.0, 0.0, True),
     ("Q1 '26", 0.0, 0.0, True), ("Q2 '26", 0.0, 0.0, True)],
    bb_status="Diluting (Mild)", div_status="Low", share_change=2.4,
    dps=[("2022", 1.4598), ("2023", 0.737), ("2024", 0.3736), ("2025", 0.0)],
)
_KO_ROWS = [_cf_row("2026-06-27", "2026", "Q2", buyback=-480_000_000, dividend=-2_196_000_000)]
_KO_SOC = _soc(
    "KO", [("Q1 '26", 2189.0, 410.0, True), ("Q2 '26", 2196.0, 480.0, True)],
    div_yield=2.95, bb_yield=0.41, bb_status="Low", div_status="Fair", share_change=-0.3,
    dps=[("2024", 1.94), ("2025", 2.0402)],
)
_AAPL_ROWS = [_cf_row("2026-06-27", "2026", "Q3", buyback=-25_100_000_000, dividend=-3_870_000_000)]
_AAPL_SOC = _soc(
    "AAPL", [("Q2 '26", 3810.0, 23_400.0, True), ("Q3 '26", 3870.0, 25_100.0, True)],
    div_yield=0.44, bb_yield=2.71, bb_status="High", div_status="Low", share_change=-2.6,
    dps=[("2024", 0.98), ("2025", 1.02)],
)


class _CashFlowFMP:
    """Serves FMP-shaped statements per ticker; records every fetch."""

    def __init__(self, rows: Dict[str, List[Dict[str, Any]]]):
        self.rows, self.calls = rows, []

    async def get_cash_flow_statement(self, ticker, period, limit):
        self.calls.append(("cash_flow", ticker, period, limit))
        return copy.deepcopy(self.rows.get(ticker, []))

    async def get_income_statement(self, ticker, period, limit):
        self.calls.append(("income", ticker, period, limit))
        return [{"date": "2026-06-27", "fiscalYear": "2026", "period": "Q2", "revenue": 1.0,
                 "commonStockRepurchased": -617_000_000}]


class _AgentOut:
    def __init__(self, ticker, soc, name="Co"):
        self.ticker, self.signal_of_confidence = ticker, soc
        self.profile = {"companyName": name}


def _real_agent(gem: _Gem, fmp, monkeypatch) -> ResearchAgent:
    """`_agent` without the handler stub: the REAL `build_tool_handlers`."""
    agent = ResearchAgent.__new__(ResearchAgent)
    agent.gemini, agent.fmp = gem, fmp
    agent.persona = get_persona_config("warren_buffett")
    monkeypatch.setattr(ra, "build_fmp_tool_declarations", lambda: object())
    return agent


def _fed_payloads(chat: _ScriptedChat, round_index: int = 1) -> List[str]:
    """The serialized tool results the model received in a follow-up — the exact text."""
    return [r["result"] for r in _fed_back(chat, round_index)]


def _assert_no_raw_lines(payload: str) -> None:
    for key in _RAW_RETURN_LINES:
        assert f'"{key}"' not in payload, f"FMP's raw `{key}` reached the model"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("fetch_quarterly_financials", {"ticker": "INTC", "statement_type": "cash_flow", "limit": 8}),
    ("fetch_extended_financials", {"ticker": "INTC", "statement_type": "cash_flow"}),
])
async def test_intc_shaped_rows_never_reach_the_model_as_a_dividend_or_buyback(
    monkeypatch, tool, args,
):
    fmp = _CashFlowFMP({"INTC": _INTC_ROWS})
    gem = _Gem([fc(tool, **args), text("INTC findings")])
    agent = _real_agent(gem, fmp, monkeypatch)

    assert await agent._agentic_research(_AgentOut("INTC", _INTC_SOC, "Intel"), "EV") == "INTC findings"
    (payload,) = _fed_payloads(gem.chat)
    _assert_no_raw_lines(payload)
    for figure in _INTC_RAW_FIGURES:
        assert figure not in payload, f"INTC's raw {figure} reached the model"

    result = json.loads(payload)
    assert [r["operatingCashFlow"] for r in result["data"]] == [2_345_000_000] * 3, (
        "the statement itself still reaches the model — only the two lines are gone"
    )
    returns = result["shareholder_returns"]
    assert returns["available"] is True and returns["amounts_in"] == "USD millions"
    newest = {q["period"]: q for q in returns["quarters"]}["Q2 '26"]
    assert newest == {"period": "Q2 '26", "dividends_paid": 0.0, "buybacks": 0.0}
    assert returns["trailing_12_months"]["buyback_status"] == "Diluting (Mild)"
    assert returns["annual_dividends_per_share"][-1] == {"fiscal_year": "2025", "per_share": 0.0}


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker,rows,soc,div_q,bb_q,yields", [
    ("KO", _KO_ROWS, _KO_SOC, 2196.0, 480.0, (2.95, 0.41, "Fair", "Low")),
    ("AAPL", _AAPL_ROWS, _AAPL_SOC, 3870.0, 25_100.0, (0.44, 2.71, "Low", "High")),
])
async def test_a_real_payer_and_repurchaser_keep_their_checked_figures(
    monkeypatch, ticker, rows, soc, div_q, bb_q, yields,
):
    fmp = _CashFlowFMP({ticker: rows})
    gem = _Gem([fc("fetch_quarterly_financials", ticker=ticker, statement_type="cash_flow"),
                text(f"{ticker} findings")])
    agent = _real_agent(gem, fmp, monkeypatch)

    assert await agent._agentic_research(_AgentOut(ticker, soc), "EV") == f"{ticker} findings"
    (payload,) = _fed_payloads(gem.chat)
    _assert_no_raw_lines(payload)
    returns = json.loads(payload)["shareholder_returns"]
    assert returns["available"] is True
    assert returns["quarters"][-1]["dividends_paid"] == div_q
    assert returns["quarters"][-1]["buybacks"] == bb_q
    t12 = returns["trailing_12_months"]
    div_yield, bb_yield, div_status, bb_status = yields
    assert (t12["dividend_yield_pct"], t12["buyback_yield_pct"]) == (div_yield, bb_yield)
    assert (t12["dividend_status"], t12["buyback_status"]) == (div_status, bb_status)
    assert t12["total_yield_pct"] == round(div_yield + bb_yield, 2)
    assert returns["annual_dividends_per_share"], "a payer's per-share record reaches the model"
    assert "never another cash-flow line" in returns["note"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["fetch_quarterly_financials", "fetch_extended_financials"])
async def test_another_tickers_cash_flow_carries_no_figure_at_all(tool):
    """A competitor the model looks up has no checked build here: its raw lines are left
    out and it says so — never an empty list that reads as "pays nothing"."""
    handlers = fmp_tools.build_tool_handlers(
        _CashFlowFMP({"AMD": _INTC_ROWS}), subject_ticker="INTC", signal_of_confidence=_INTC_SOC,
    )
    out = await handlers[tool]({"ticker": "AMD", "statement_type": "cash_flow"})
    _assert_no_raw_lines(json.dumps(out))
    assert out["shareholder_returns"] == {
        "available": False, "note": fmp_tools._RETURNS_OTHER_TICKER_NOTE,
    }
    assert "Make no claim" in out["shareholder_returns"]["note"]
    assert len(out["data"]) == 3


@pytest.mark.asyncio
async def test_no_subject_at_all_strips_every_ticker():
    """The default (no subject) is the fail-closed one: nobody gets figures."""
    handlers = fmp_tools.build_tool_handlers(_CashFlowFMP({"INTC": _INTC_ROWS}))
    out = await handlers["fetch_quarterly_financials"]({"ticker": "INTC", "statement_type": "cash_flow"})
    _assert_no_raw_lines(json.dumps(out))
    assert out["shareholder_returns"]["available"] is False


@pytest.mark.asyncio
async def test_a_missing_build_is_unavailable_not_zero():
    handlers = fmp_tools.build_tool_handlers(
        _CashFlowFMP({"INTC": _INTC_ROWS}), subject_ticker="INTC", signal_of_confidence=None,
    )
    out = await handlers["fetch_quarterly_financials"]({"ticker": "INTC", "statement_type": "cash_flow"})
    _assert_no_raw_lines(json.dumps(out))
    returns = out["shareholder_returns"]
    assert returns == {"available": False, "note": fmp_tools._RETURNS_UNAVAILABLE_NOTE}
    assert "Unavailable is not zero" in returns["note"]


@pytest.mark.asyncio
@pytest.mark.parametrize("degraded,available", [
    (["buyback_check"], False),          # SEC unreachable: FMP's UNCHECKED buybacks
    (["income", "cash_flow_row"], False),
    (["cash_flow_row"], True),           # a lagging newest quarter: the service trimmed it
    ([], True),
])
async def test_a_degraded_build_is_withheld_and_logged(caplog, degraded, available):
    soc = _soc("INTC", [("Q2 '26", 0.0, 617.0, True)], degraded=degraded)
    handlers = fmp_tools.build_tool_handlers(
        _CashFlowFMP({"INTC": _INTC_ROWS}), subject_ticker="INTC", signal_of_confidence=soc,
    )
    with caplog.at_level("WARNING", logger=fmp_tools.__name__):
        out = await handlers["fetch_quarterly_financials"](
            {"ticker": "INTC", "statement_type": "cash_flow"}
        )
    assert out["shareholder_returns"]["available"] is available
    refused = [r.getMessage() for r in caplog.records if "research-tool-returns-refused" in r.getMessage()]
    if available:
        assert not refused
    else:
        assert "617" not in json.dumps(out), "an unchecked build's buyback reached the model"
        assert len(refused) == 1 and "INTC" in refused[0]
        assert all(r in refused[0] for r in degraded if r != "cash_flow_row"), refused[0]


def test_a_quarter_with_no_cash_flow_row_reads_null_not_zero():
    """`cash_flow_reported=False` points carry 0.0 PLACEHOLDERS (non-Optional on shipped
    iOS); handed to the model as 0.0 they would read as "paid nothing that quarter"."""
    soc = _soc("KO", [("Q1 '26", 0.0, 0.0, False), ("Q2 '26", 2196.0, 480.0, True)],
               div_status="Fair")
    returns = fmp_tools.shareholder_returns_block(soc, "USD")
    assert returns["quarters"][0] == {"period": "Q1 '26", "dividends_paid": None, "buybacks": None}
    assert returns["quarters"][1]["dividends_paid"] == 2196.0


def test_an_unknown_share_count_change_is_null_not_flat():
    soc = _soc("KO", [("Q2 '26", 1.0, 1.0, True)], share_change=0.0, share_known=False)
    assert fmp_tools.shareholder_returns_block(soc, "USD")["trailing_12_months"][
        "share_count_change_pct"] is None


@pytest.mark.parametrize("rows,label,cur", [
    ([_cf_row("2026-06-30", "2026", "Q2", buyback=0, dividend=-9e10, cur="TWD")],
     "TWD millions", "TWD"),
    ([{**_cf_row("2026-06-30", "2026", "Q2", buyback=0, dividend=0), "reportedCurrency": None}],
     "millions, in the statement's reporting currency", "an unknown reporting currency"),
    ([_cf_row("2026-06-30", "2026", "Q2", buyback=0, dividend=0, cur="USD"),
      _cf_row("2026-03-31", "2026", "Q1", buyback=0, dividend=0, cur="TWD")],
     "millions, in the statement's reporting currency", "an unknown reporting currency"),
    ([], "millions, in the statement's reporting currency", "an unknown reporting currency"),
])
def test_yields_ride_only_on_a_usd_statement(rows, label, cur):
    """The service divides reporting-currency amounts by a US-dollar market cap: on TSM's
    TWD statements every yield (and the verdicts built on it) is ~30x off."""
    returns = fmp_tools.shareholder_returns_block(_KO_SOC, fmp_tools._statement_currency(rows))
    assert returns["available"] is True and returns["amounts_in"] == label
    t12 = returns["trailing_12_months"]
    assert set(t12) == {"share_count_change_pct"}, "a yield or verdict escaped the USD gate"
    assert cur in returns["yields_note"]
    assert returns["quarters"][-1]["dividends_paid"] == 2196.0, "the amounts themselves stay"


@pytest.mark.asyncio
@pytest.mark.parametrize("subject,asked", [("BRK-B", "brk.b"), ("BRK.B", "BRK-B"), ("ko", " KO ")])
async def test_the_subject_matches_across_share_class_spellings(subject, asked):
    handlers = fmp_tools.build_tool_handlers(
        _CashFlowFMP({asked.strip().upper(): _KO_ROWS}),
        subject_ticker=subject, signal_of_confidence=_KO_SOC,
    )
    out = await handlers["fetch_quarterly_financials"]({"ticker": asked, "statement_type": "cash_flow"})
    assert out["shareholder_returns"]["available"] is True


@pytest.mark.asyncio
async def test_income_and_balance_results_are_unchanged():
    handlers = fmp_tools.build_tool_handlers(
        _CashFlowFMP({}), subject_ticker="INTC", signal_of_confidence=_INTC_SOC,
    )
    out = await handlers["fetch_quarterly_financials"]({"ticker": "INTC", "statement_type": "income"})
    assert "shareholder_returns" not in out
    assert "commonStockRepurchased" not in out["data"][0], "the income select never took it"


@pytest.mark.asyncio
async def test_a_malformed_build_costs_the_figures_not_the_tool(caplog):
    class _Broken:
        symbol, degraded, data_points = "INTC", [], [object()]   # no `period` / amounts

    handlers = fmp_tools.build_tool_handlers(
        _CashFlowFMP({"INTC": _INTC_ROWS}), subject_ticker="INTC", signal_of_confidence=_Broken(),
    )
    with caplog.at_level("WARNING", logger=fmp_tools.__name__):
        out = await handlers["fetch_quarterly_financials"](
            {"ticker": "INTC", "statement_type": "cash_flow"}
        )
    assert "error" not in out and len(out["data"]) == 3, "the statement still reached the model"
    assert out["shareholder_returns"] == {"available": False, "note": fmp_tools._RETURNS_UNAVAILABLE_NOTE}
    assert any("research-tool-returns-malformed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "617", None])
def test_a_non_numeric_amount_is_null(value):
    assert fmp_tools._rounded(value) is None
