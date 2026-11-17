"""The report pipeline's per-call model / thinking-level switches (the 2026-12-01 reroute).

Google routes every gemini-2.5-flash request to gemini-3.5-flash-lite from 2026-12-01. Four settings
let the owner move the report's judgement calls without a deploy:

- `REPORT_STAGE_A_MODEL` → Stage A's `generate_json` (`generate_stage_a_shell`, both doors).
- `REPORT_SYNTHESIS_MODEL` + `REPORT_SYNTHESIS_THINKING_LEVEL` → the two post-assembly syntheses
  (`synthesize_core_thesis`, `synthesize_critical_factors`), shared by both doors.
- `REPORT_AGENTIC_FINDINGS_MEMO` → the deep door's `research_complete` `summary` description.

The contract pinned here: with every setting at its default the outgoing call is EXACTLY today's
(same kwargs, so the same request and the same response-cache key); a set value reaches the Gemini
call, and only the call it names. Fakes record kwargs; the end-to-end cases run the real
`GeminiClient` on a fake SDK transport. No network.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List

import pytest
from google.genai import types

from app.config import Settings, settings
from app.integrations.gemini import GeminiClient, _TTLCache
from app.services import ticker_report_service as trs
from app.services.agents import fmp_tools
from app.services.agents import narrative_prompts as np_
from app.services.agents import research_agent as ra
from app.services.agents.persona_config import get_persona_config
from app.services.agents.research_agent import ResearchAgent
from app.services.ticker_report_service import TickerReportService

PERSONA = get_persona_config("warren_buffett")
DOORS = ["direct", "deep"]
LITE = "gemini-3.5-flash-lite"

BRIEF = "Brief summary of key findings from your research"
MEMO = (
    "A detailed research memo of 300-600 words: every figure you fetched with its period, "
    "what changed, and what it means for the investment thesis"
)

GOOD_SHELL = '{"quality_score": 71, "core_thesis": {"bull_case": ["a"], "bear_case": ["b"]}}'
BROKEN_SHELL = '{"macro_data": {"risk_factors": [{"description": "the "Other Bets" unit"}]}}'

# Today's call shapes, kwarg for kwarg. A default-settings call must send exactly these.
STAGE_A_KWARGS = {"prompt", "system_instruction", "thinking_budget", "usage_tag", "cache"}
SYNTHESIS_KWARGS = {"prompt", "system_instruction", "usage_tag"}

FACTORS = [
    {"title": "Rates stay high", "severity": "high", "description": "d", "watch": "w"},
    {"title": "Margin compression", "severity": "medium", "description": "d", "watch": "w"},
]


@pytest.fixture
def defaults(monkeypatch):
    """Every switch at its declared default, whatever the local environment says."""
    for name in ("REPORT_STAGE_A_MODEL", "REPORT_SYNTHESIS_MODEL",
                 "REPORT_SYNTHESIS_THINKING_LEVEL"):
        monkeypatch.setattr(settings, name, None)
    monkeypatch.setattr(settings, "REPORT_AGENTIC_FINDINGS_MEMO", False)
    return settings


# ── fakes ────────────────────────────────────────────────────────────────────

class _Recorder:
    """Records every generate_json call's kwargs; answers from a script (last answer repeats)."""

    def __init__(self, *answers: str):
        self.answers = list(answers) or ["{}"]
        self.calls: List[Dict[str, Any]] = []

    async def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return {"text": answer, "finish_reason": "STOP"}


class _Out:
    ticker = "GOOGL"
    profile = {"companyName": "Alphabet Inc."}
    signal_of_confidence = None


async def _stage_a(door: str, gemini) -> dict:
    if door == "direct":
        svc = TickerReportService.__new__(TickerReportService)
        svc.gemini = gemini
        return await svc._generate_stage_a(_Out(), PERSONA, "EVIDENCE")
    agent = ResearchAgent.__new__(ResearchAgent)
    agent.gemini = gemini
    agent.persona = PERSONA
    return await agent._generate_stage_a(_Out(), "EVIDENCE", "FINDINGS")


def _thesis_report() -> Dict[str, Any]:
    # `synthesize_core_thesis` returns before any call when the module digest is empty; the
    # price-action line is what makes it non-empty (asserted by every caller's anti-vacuity check).
    return {"symbol": "AAPL", "company_name": "Apple Inc.",
            "price_action": {"change_pct": 4.2, "window_label": "5 days"}}


def _factors_report() -> Dict[str, Any]:
    return {**_thesis_report(), "critical_factors": [dict(f) for f in FACTORS],
            "core_thesis": {"bull_case": ["b"], "bear_case": ["r"]}}


async def _run_thesis(gem) -> None:
    await np_.synthesize_core_thesis(_thesis_report(), PERSONA, gem, "EVIDENCE")


async def _run_factors(gem) -> None:
    await np_.synthesize_critical_factors(_factors_report(), PERSONA, gem, "EVIDENCE")


SYNTHESES = {
    "thesis": (_run_thesis, '{"bull_case": ["x"], "bear_case": ["y"]}'),
    "factors": (_run_factors, json.dumps({"critical_factors": FACTORS})),
}


# ── a real GeminiClient on a recording fake transport ───────────────────────

class _FakeModels:
    def __init__(self, calls, text):
        self._calls = calls
        self._text = text

    async def generate_content(self, **kwargs):
        self._calls.append(kwargs)
        await asyncio.sleep(0)
        text = self._text

        class _R:
            candidates: list = []
            usage_metadata = None

        _R.text = text
        return _R()


def _real_client(text: str = '{"ok": true}'):
    calls: list = []
    client = GeminiClient.__new__(GeminiClient)
    client.model_name = "gemini-2.5-flash"
    client._temperature = 0.7
    client._max_tokens = 8192
    client._response_cache = _TTLCache(max_size=16, ttl_seconds=3600)
    client._embedding_cache = _TTLCache(max_size=16, ttl_seconds=3600)

    class _Aio:
        models = _FakeModels(calls, text)

    class _Client:
        aio = _Aio()

    client._client = _Client()
    return client, calls


def _summary_description(tool: types.Tool) -> str:
    decl = next(d for d in tool.function_declarations if d.name == "research_complete")
    return decl.parameters.properties["summary"].description


# ── the declared defaults ────────────────────────────────────────────────────

def test_the_declared_defaults_are_todays_behaviour():
    fields = Settings.model_fields
    assert fields["REPORT_STAGE_A_MODEL"].default is None
    assert fields["REPORT_SYNTHESIS_MODEL"].default is None
    assert fields["REPORT_SYNTHESIS_THINKING_LEVEL"].default is None
    assert fields["REPORT_AGENTIC_FINDINGS_MEMO"].default is False


# ── Stage A: REPORT_STAGE_A_MODEL ────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_stage_a_default_call_is_unchanged(door, defaults):
    gem = _Recorder(GOOD_SHELL)
    shell = await _stage_a(door, gem)
    assert shell["quality_score"] == 71
    assert len(gem.calls) == 1
    assert set(gem.calls[0]) == STAGE_A_KWARGS, (
        "a default-settings Stage A call must carry exactly today's kwargs (no model_name, "
        "no thinking_level)"
    )
    assert gem.calls[0]["cache"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_stage_a_model_reaches_the_call_on_both_doors(door, defaults, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_STAGE_A_MODEL", LITE)
    gem = _Recorder(GOOD_SHELL)
    await _stage_a(door, gem)
    assert gem.calls and gem.calls[0]["model_name"] == LITE
    assert set(gem.calls[0]) == STAGE_A_KWARGS | {"model_name"}
    assert "thinking_level" not in gem.calls[0], "Stage A has no level setting"
    assert gem.calls[0]["thinking_budget"] == np_.stage_a_thinking_budget(), (
        "the model switch must not drop Stage A's thinking budget"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_stage_a_model_also_rides_the_re_ask(door, defaults, monkeypatch):
    """The one re-ask after a bad answer goes to the same model, not back to the default."""
    monkeypatch.setattr(settings, "REPORT_STAGE_A_MODEL", LITE)
    gem = _Recorder(BROKEN_SHELL, GOOD_SHELL)
    await _stage_a(door, gem)
    assert len(gem.calls) == 2
    assert [c.get("model_name") for c in gem.calls] == [LITE, LITE]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
async def test_a_blank_stage_a_model_sends_no_model(value, defaults, monkeypatch):
    """Config turns blank into None; a hand-set blank must not put model_name='' on the wire."""
    monkeypatch.setattr(settings, "REPORT_STAGE_A_MODEL", value)
    gem = _Recorder(GOOD_SHELL)
    await _stage_a("direct", gem)
    assert set(gem.calls[0]) == STAGE_A_KWARGS


@pytest.mark.asyncio
async def test_a_padded_stage_a_model_is_trimmed(defaults, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_STAGE_A_MODEL", f"  {LITE}\n")
    gem = _Recorder(GOOD_SHELL)
    await _stage_a("deep", gem)
    assert gem.calls[0]["model_name"] == LITE


@pytest.mark.asyncio
async def test_a_non_text_stage_a_model_is_ignored_loudly(defaults, monkeypatch, caplog):
    monkeypatch.setattr(settings, "REPORT_STAGE_A_MODEL", 35)
    gem = _Recorder(GOOD_SHELL)
    with caplog.at_level(logging.WARNING):
        shell = await _stage_a("direct", gem)
    assert shell["quality_score"] == 71, "an odd setting must never degrade the report"
    assert set(gem.calls[0]) == STAGE_A_KWARGS
    assert "[report-model-setting] REPORT_STAGE_A_MODEL" in caplog.text


@pytest.mark.asyncio
async def test_the_stage_a_model_is_read_at_call_time(defaults, monkeypatch):
    gem = _Recorder(GOOD_SHELL)
    await _stage_a("direct", gem)
    monkeypatch.setattr(settings, "REPORT_STAGE_A_MODEL", LITE)
    await _stage_a("direct", gem)
    assert [c.get("model_name") for c in gem.calls] == [None, LITE]


@pytest.mark.asyncio
async def test_synthesis_settings_never_reach_stage_a(defaults, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_SYNTHESIS_MODEL", LITE)
    monkeypatch.setattr(settings, "REPORT_SYNTHESIS_THINKING_LEVEL", "high")
    gem = _Recorder(GOOD_SHELL)
    await _stage_a("deep", gem)
    assert set(gem.calls[0]) == STAGE_A_KWARGS


# ── the syntheses: REPORT_SYNTHESIS_MODEL / REPORT_SYNTHESIS_THINKING_LEVEL ──

def test_both_doors_share_the_one_pair_of_syntheses():
    """The settings live in narrative_prompts; both doors must call THESE functions, or one door
    would keep the old model after the switch."""
    assert ra.synthesize_core_thesis is np_.synthesize_core_thesis
    assert trs.synthesize_core_thesis is np_.synthesize_core_thesis
    assert ra.synthesize_critical_factors is np_.synthesize_critical_factors
    assert trs.synthesize_critical_factors is np_.synthesize_critical_factors
    assert ra.generate_stage_a_shell is np_.generate_stage_a_shell
    assert trs.generate_stage_a_shell is np_.generate_stage_a_shell


@pytest.mark.asyncio
@pytest.mark.parametrize("which", list(SYNTHESES))
async def test_synthesis_default_call_is_unchanged(which, defaults):
    run, answer = SYNTHESES[which]
    gem = _Recorder(answer)
    await run(gem)
    assert gem.calls, f"VACUOUS: the {which} synthesis never reached generate_json"
    assert set(gem.calls[0]) == SYNTHESIS_KWARGS, (
        "a default-settings synthesis must carry exactly today's kwargs (same request, same "
        "response-cache key)"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("which", list(SYNTHESES))
async def test_synthesis_model_alone(which, defaults, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_SYNTHESIS_MODEL", LITE)
    run, answer = SYNTHESES[which]
    gem = _Recorder(answer)
    await run(gem)
    assert gem.calls, f"VACUOUS: the {which} synthesis never reached generate_json"
    assert gem.calls[0]["model_name"] == LITE
    assert set(gem.calls[0]) == SYNTHESIS_KWARGS | {"model_name"}


@pytest.mark.asyncio
@pytest.mark.parametrize("which", list(SYNTHESES))
async def test_synthesis_level_alone(which, defaults, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_SYNTHESIS_THINKING_LEVEL", "low")
    run, answer = SYNTHESES[which]
    gem = _Recorder(answer)
    await run(gem)
    assert gem.calls, f"VACUOUS: the {which} synthesis never reached generate_json"
    assert gem.calls[0]["thinking_level"] == "low"
    assert set(gem.calls[0]) == SYNTHESIS_KWARGS | {"thinking_level"}


@pytest.mark.asyncio
@pytest.mark.parametrize("which", list(SYNTHESES))
async def test_synthesis_model_and_level_together(which, defaults, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_SYNTHESIS_MODEL", LITE)
    monkeypatch.setattr(settings, "REPORT_SYNTHESIS_THINKING_LEVEL", "medium")
    run, answer = SYNTHESES[which]
    gem = _Recorder(answer)
    await run(gem)
    assert gem.calls, f"VACUOUS: the {which} synthesis never reached generate_json"
    call = gem.calls[0]
    assert (call["model_name"], call["thinking_level"]) == (LITE, "medium")
    assert "thinking_budget" not in call, "the syntheses stay uncapped: a level, never a budget"


@pytest.mark.asyncio
@pytest.mark.parametrize("which", list(SYNTHESES))
async def test_the_stage_a_model_never_reaches_the_syntheses(which, defaults, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_STAGE_A_MODEL", LITE)
    run, answer = SYNTHESES[which]
    gem = _Recorder(answer)
    await run(gem)
    assert gem.calls and set(gem.calls[0]) == SYNTHESIS_KWARGS


@pytest.mark.asyncio
@pytest.mark.parametrize("which", list(SYNTHESES))
async def test_blank_synthesis_settings_send_nothing(which, defaults, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_SYNTHESIS_MODEL", "  ")
    monkeypatch.setattr(settings, "REPORT_SYNTHESIS_THINKING_LEVEL", "")
    run, answer = SYNTHESES[which]
    gem = _Recorder(answer)
    await run(gem)
    assert gem.calls and set(gem.calls[0]) == SYNTHESIS_KWARGS


@pytest.mark.asyncio
async def test_the_synthesis_result_is_still_applied_with_the_switch_on(defaults, monkeypatch):
    """The override changes where the call goes, not what is done with the answer."""
    monkeypatch.setattr(settings, "REPORT_SYNTHESIS_MODEL", LITE)
    monkeypatch.setattr(settings, "REPORT_SYNTHESIS_THINKING_LEVEL", "low")
    report = _thesis_report()
    gem = _Recorder('{"bull_case": ["Durable services revenue"], "bear_case": ["China exposure"]}')
    await np_.synthesize_core_thesis(report, PERSONA, gem, "EVIDENCE")
    assert report["core_thesis"] == {"bull_case": ["Durable services revenue"],
                                     "bear_case": ["China exposure"]}


# ── end to end through the real GeminiClient (fake transport) ────────────────

@pytest.mark.asyncio
async def test_e2e_default_synthesis_request_and_cache_key_are_todays(defaults):
    """Default settings: the request goes to the client default with no thinking config, and its
    response-cache entry is the one today's call shape (prompt + system instruction only) hits."""
    client, calls = _real_client()
    await _run_thesis(client)
    assert len(calls) == 1, "VACUOUS: the synthesis never reached the transport"
    assert calls[0]["model"] == "gemini-2.5-flash"
    assert calls[0]["config"].thinking_config is None

    # Today's exact call shape must be a cache HIT on the entry the synthesis wrote.
    again = await client.generate_json(prompt=calls[0]["contents"],
                                       system_instruction=PERSONA.system_prompt)
    assert len(calls) == 1, "the default synthesis wrote under a different cache key than today's"
    assert again["text"] == '{"ok": true}'


@pytest.mark.asyncio
@pytest.mark.parametrize("which", list(SYNTHESES))
async def test_e2e_synthesis_model_and_level_reach_the_wire(which, defaults, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_SYNTHESIS_MODEL", LITE)
    monkeypatch.setattr(settings, "REPORT_SYNTHESIS_THINKING_LEVEL", "low")
    run, _ = SYNTHESES[which]
    client, calls = _real_client()
    await run(client)
    assert len(calls) == 1, f"VACUOUS: the {which} synthesis never reached the transport"
    assert calls[0]["model"] == LITE
    thinking = calls[0]["config"].thinking_config
    assert thinking is not None and thinking.thinking_level == types.ThinkingLevel.LOW
    assert thinking.thinking_budget is None, "a level and a budget together are a 400"


@pytest.mark.asyncio
async def test_e2e_a_level_without_a_model_switch_leaves_todays_wire(defaults, monkeypatch):
    """gemini-2.5-flash (the client default) refuses every level: setting only the level must not
    put one on the wire (the encoder drops it; the syntheses send no budget)."""
    monkeypatch.setattr(settings, "REPORT_SYNTHESIS_THINKING_LEVEL", "high")
    client, calls = _real_client(json.dumps({"critical_factors": FACTORS}))
    await _run_factors(client)
    assert len(calls) == 1
    assert calls[0]["model"] == "gemini-2.5-flash"
    assert calls[0]["config"].thinking_config is None


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_e2e_stage_a_on_the_lite_model_gets_its_real_off(door, defaults, monkeypatch):
    """Stage A's budget 0 on a request NAMING gemini-3.5-flash-lite goes out as level minimal
    (0 thought tokens), not the budget 1 a gemini-2.5-flash request gets."""
    monkeypatch.setattr(settings, "REPORT_STAGE_A_THINKING_BUDGET", 0)
    monkeypatch.setattr(settings, "REPORT_STAGE_A_MODEL", LITE)
    client, calls = _real_client(GOOD_SHELL)
    await _stage_a(door, client)
    assert calls, "VACUOUS: Stage A never reached the transport"
    assert calls[0]["model"] == LITE
    thinking = calls[0]["config"].thinking_config
    assert thinking.thinking_level == types.ThinkingLevel.MINIMAL
    assert thinking.thinking_budget is None


@pytest.mark.asyncio
async def test_e2e_stage_a_default_model_is_the_client_default(defaults, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_STAGE_A_THINKING_BUDGET", 0)
    client, calls = _real_client(GOOD_SHELL)
    await _stage_a("direct", client)
    assert calls and calls[0]["model"] == "gemini-2.5-flash"
    thinking = calls[0]["config"].thinking_config
    assert thinking.thinking_level is None and thinking.thinking_budget is not None


# ── research_complete: REPORT_AGENTIC_FINDINGS_MEMO ─────────────────────────

def test_the_default_summary_ask_is_byte_identical(defaults):
    assert fmp_tools.RESEARCH_COMPLETE_SUMMARY_BRIEF == BRIEF
    assert _summary_description(fmp_tools.build_fmp_tool_declarations()) == BRIEF


def test_the_memo_switch_asks_for_the_memo(defaults, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_AGENTIC_FINDINGS_MEMO", True)
    assert _summary_description(fmp_tools.build_fmp_tool_declarations()) == MEMO


def test_the_memo_switch_changes_nothing_else_in_the_declarations(defaults, monkeypatch):
    def shape(tool):
        out = []
        for d in tool.function_declarations:
            dumped = d.model_dump(exclude_none=True)
            if d.name == "research_complete":
                dumped["parameters"]["properties"]["summary"].pop("description")
            out.append(dumped)
        return out

    brief = fmp_tools.build_fmp_tool_declarations()
    monkeypatch.setattr(settings, "REPORT_AGENTIC_FINDINGS_MEMO", True)
    memo = fmp_tools.build_fmp_tool_declarations()
    assert shape(brief) == shape(memo)
    decl = next(d for d in memo.function_declarations if d.name == "research_complete")
    assert decl.parameters.required == ["summary"]


def test_the_memo_switch_is_read_when_the_declarations_are_built(defaults, monkeypatch):
    before = fmp_tools.build_fmp_tool_declarations()
    monkeypatch.setattr(settings, "REPORT_AGENTIC_FINDINGS_MEMO", True)
    after = fmp_tools.build_fmp_tool_declarations()
    assert _summary_description(before) == BRIEF, "a built declaration must not change under it"
    assert _summary_description(after) == MEMO


@pytest.mark.parametrize("value", ["false", "true", 1, "1", None])
def test_only_a_real_true_asks_for_the_memo(value, defaults, monkeypatch):
    """Pydantic parses the Railway variable into a bool; anything else that slips in (a hand-set
    string) fails closed to today's brief ask, never the more expensive memo."""
    monkeypatch.setattr(settings, "REPORT_AGENTIC_FINDINGS_MEMO", value)
    assert _summary_description(fmp_tools.build_fmp_tool_declarations()) == BRIEF


class _ToolChatGem:
    model_name = "gemini-2.5-flash"

    def __init__(self):
        self.create_kwargs: Dict[str, Any] = {}

    def create_tool_chat(self, **kwargs):
        self.create_kwargs = kwargs

        class _Chat:
            async def send_message(self, msg):
                class _R:
                    candidates: list = []
                    usage_metadata = None
                    text = "Findings: strong quarter."
                return _R()

        return _Chat()


@pytest.mark.asyncio
@pytest.mark.parametrize("memo, expected", [(False, BRIEF), (True, MEMO)])
async def test_the_deep_door_hands_the_model_the_switched_description(
    memo, expected, defaults, monkeypatch,
):
    """The real loop builds the declarations per run, so the switch reaches the tool chat."""
    monkeypatch.setattr(settings, "REPORT_AGENTIC_FINDINGS_MEMO", memo)
    monkeypatch.setattr(ra, "build_tool_handlers", lambda fmp, **_kw: {})
    agent = ResearchAgent.__new__(ResearchAgent)
    gem = _ToolChatGem()
    agent.gemini = gem
    agent.fmp = object()
    agent.persona = PERSONA
    findings = await agent._agentic_research(_Out(), "EVIDENCE")
    assert findings == "Findings: strong quarter."
    tools = gem.create_kwargs["tools"]
    assert _summary_description(tools[0]) == expected
