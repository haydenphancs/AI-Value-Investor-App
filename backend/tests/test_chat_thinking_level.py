"""Chat's Gemini `thinking_level` (2026-11-16, before the 2026-12-01 gemini-2.5-flash reroute).

`CHAT_THINKING_LEVEL` (default None) lets the owner pick a level for chat's flagship route with a
Railway variable. `chat_service._chat_thinking_level(model)` is the twin of
`_chat_thinking_budget(model)`: the CHEAP route never gets one, every other call reads the
setting at CALL time. Every call site that passes `thinking_budget=_chat_thinking_budget(<m>)`
also splats `**_chat_thinking_level_kwargs(<m>)` with the SAME model argument, which is `{}`
while the setting is unset.

What this file pins:
  * NEUTRALITY: with the setting unset, no chat call carries a `thinking_level` keyword at all
    (so the request, the response-cache key and every strict-signature test fake are unchanged),
    and the wire config is today's (budget, thoughts on, no level);
  * the level reaches every chat answer call when set, on both doors (service + endpoint, the
    stream door's single-mode call and its cut-answer continuation), and the cheap route never;
  * an AST guard that every budget call site carries the level splat with the same model
    argument, so a new site cannot forget it and an old one cannot drift;
  * through the REAL GeminiClient encoder, what goes on the wire per model.

No network: every Gemini/Supabase seam is a fake.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest
from google.genai import types

from app.config import GEMINI_THINKING_LEVELS, Settings, settings
from app.integrations import gemini as gem
from app.services.chat_service import (
    ChatService,
    _chat_thinking_budget,
    _chat_thinking_level,
    _chat_thinking_level_kwargs,
)
# The one real-request harness for the stream door (fixture + its fakes). Imported, not copied:
# a second copy of a 300-line seam map would drift from the first.
from tests.test_chat_stream_endpoint import (  # noqa: F401  (`harness` is a pytest fixture)
    _CUT_EVENTS,
    _FakeChatService,
    _FakeGemini,
    _post,
    harness,
)

BACKEND = Path(__file__).resolve().parents[1]
CHAT_SERVICE = BACKEND / "app" / "services" / "chat_service.py"
CHAT_ENDPOINT = BACKEND / "app" / "api" / "v1" / "endpoints" / "chat.py"

FLAGSHIP = "gemini-2.5-flash"
LITE = "gemini-3.5-flash-lite"


def _set(monkeypatch, level: Optional[str], budget: int = 1024) -> None:
    monkeypatch.setattr(settings, "CHAT_THINKING_LEVEL", level)
    monkeypatch.setattr(settings, "CHAT_THINKING_BUDGET", budget)


# ── the resolver ─────────────────────────────────────────────────────────────

def test_the_setting_defaults_to_no_level():
    """The shipped default is "no level" — the field's own default, not whatever the local
    environment happens to hold."""
    assert Settings.model_fields["CHAT_THINKING_LEVEL"].default is None


@pytest.mark.parametrize("model", [None, "", FLAGSHIP, LITE, "gemini-3.8-flash", "cheap"])
def test_unset_resolves_to_no_level_and_no_keyword_on_every_model(monkeypatch, model):
    _set(monkeypatch, None)
    if model == "cheap":
        model = settings.CHAT_CHEAP_MODEL
    assert _chat_thinking_level(model) is None
    assert _chat_thinking_level_kwargs(model) == {}


def test_a_blank_setting_sends_no_level(monkeypatch):
    """The validator turns a blank variable into None; a value set around it (a test, a
    hot-patch) must still send nothing rather than an empty level."""
    _set(monkeypatch, "")
    assert _chat_thinking_level() is None
    assert _chat_thinking_level_kwargs(FLAGSHIP) == {}


@pytest.mark.parametrize("level", GEMINI_THINKING_LEVELS)
def test_a_set_level_resolves_on_the_flagship_and_never_on_the_cheap_route(monkeypatch, level):
    _set(monkeypatch, level)
    for model in (None, "", FLAGSHIP, LITE):
        assert _chat_thinking_level(model) == level
        assert _chat_thinking_level_kwargs(model) == {"thinking_level": level}
    assert _chat_thinking_level(settings.CHAT_CHEAP_MODEL) is None
    assert _chat_thinking_level_kwargs(settings.CHAT_CHEAP_MODEL) == {}


def test_the_level_is_read_at_call_time(monkeypatch):
    _set(monkeypatch, "low")
    assert _chat_thinking_level() == "low"
    monkeypatch.setattr(settings, "CHAT_THINKING_LEVEL", "high")
    assert _chat_thinking_level() == "high"
    monkeypatch.setattr(settings, "CHAT_THINKING_LEVEL", None)
    assert _chat_thinking_level_kwargs() == {}


@pytest.mark.parametrize("model", [
    None, "", FLAGSHIP, LITE, "cheap", "models/cheap", "CHEAP", " cheap", "cheap ",
])
def test_level_and_budget_agree_on_which_calls_are_the_cheap_route(monkeypatch, model):
    """The two helpers are twins: a model the budget treats as the cheap route (no budget) gets
    no level either, and the other way round — whatever spelling reaches them."""
    _set(monkeypatch, "low", budget=1024)
    cheap = settings.CHAT_CHEAP_MODEL
    if model is not None:
        model = model.replace("cheap", cheap).replace("CHEAP", cheap.upper())
    assert (_chat_thinking_budget(model) is None) == (_chat_thinking_level(model) is None)


# ── the call sites (AST: a substring scan would match the definitions) ───────

def _calls(path: Path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call):
            yield node


def _named_call(node: Any, name: str) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == name)


def _arg_dump(call: ast.Call) -> str:
    return ast.dump(ast.Tuple(elts=list(call.args), ctx=ast.Load())) + "|" + "|".join(
        f"{kw.arg}={ast.dump(kw.value)}" for kw in call.keywords
    )


@pytest.mark.parametrize("path,minimum", [(CHAT_SERVICE, 5), (CHAT_ENDPOINT, 2)],
                         ids=["chat_service.py", "chat.py"])
def test_every_budget_call_site_splats_the_level_with_the_same_model(path, minimum):
    sites = 0
    problems: List[str] = []
    for call in _calls(path):
        budget = next((kw.value for kw in call.keywords
                       if kw.arg == "thinking_budget"
                       and _named_call(kw.value, "_chat_thinking_budget")), None)
        if budget is None:
            continue
        sites += 1
        splats = [kw.value for kw in call.keywords
                  if kw.arg is None and _named_call(kw.value, "_chat_thinking_level_kwargs")]
        if len(splats) != 1:
            problems.append(f"line {call.lineno}: {len(splats)} level splat(s), expected 1")
        elif _arg_dump(splats[0]) != _arg_dump(budget):
            problems.append(f"line {call.lineno}: the level is resolved for a different model "
                            "than the budget")
    assert sites >= minimum, f"expected >= {minimum} chat budget call sites, found {sites}"
    assert not problems, f"{path.name}: " + "; ".join(problems)


@pytest.mark.parametrize("path", [CHAT_SERVICE, CHAT_ENDPOINT], ids=lambda p: p.name)
def test_the_level_never_travels_without_its_budget_or_as_a_bare_keyword(path):
    """A level splat with no budget beside it, or a literal `thinking_level=` (which would send
    a keyword even while the setting is unset), breaks the neutrality the helper guarantees."""
    problems: List[str] = []
    for call in _calls(path):
        if any(kw.arg == "thinking_level" for kw in call.keywords):
            problems.append(f"line {call.lineno}: a bare thinking_level= keyword")
        has_splat = any(kw.arg is None and _named_call(kw.value, "_chat_thinking_level_kwargs")
                        for kw in call.keywords)
        has_budget = any(kw.arg == "thinking_budget"
                         and _named_call(kw.value, "_chat_thinking_budget")
                         for kw in call.keywords)
        if has_splat and not has_budget:
            problems.append(f"line {call.lineno}: a level splat with no chat budget")
    assert not problems, f"{path.name}: " + "; ".join(problems)


# ── the service door: stream_synthesis ──────────────────────────────────────

class _RecordingGemini:
    """Records every chat call's keywords. `specialists_answer=False` makes every specialist
    come back empty, so stream_synthesis takes its general-fallback stream_agentic."""

    def __init__(self, specialists_answer: bool = True):
        self.agentic: List[Dict[str, Any]] = []
        self.text: List[Dict[str, Any]] = []
        self._answer = specialists_answer

    async def stream_agentic(self, prompt, **kw):
        self.agentic.append(kw)
        if self._answer or len(self.agentic) > 2:
            yield ("answer", f"view {len(self.agentic)}")
        else:
            yield ("thought", "nothing to say")

    async def stream_text(self, prompt, **kw):
        self.text.append(kw)
        yield ("answer", "merged")


class _StrictGemini:
    """The PRE-CHANGE signatures (no thinking_level, no **kwargs), recording each call that
    got through. stream_synthesis swallows a specialist's exception, so a TypeError from an
    unexpected keyword would hide; the recorded count is what proves the calls went out."""

    def __init__(self):
        self.calls: List[str] = []

    async def stream_agentic(self, prompt, tools=None, tool_handlers=None,
                             system_instruction=None, max_rounds=4, model_name=None,
                             usage_tag=None, max_output_tokens=None, thinking_budget=None):
        self.calls.append("agentic")
        yield ("answer", "a view")

    async def stream_text(self, prompt, system_instruction=None, model_name=None,
                          usage_tag=None, max_output_tokens=None, thinking_budget=None):
        self.calls.append("text")
        yield ("answer", "merged")


_ROUTE = {"specialists": ["valuation", "fundamentals"], "labels": ["Valuation", "Fundamentals"],
          "mode": "synthesize"}
_PREP = {"system_instruction": "sys", "prompt": "p"}


def _svc(gemini) -> ChatService:
    svc = object.__new__(ChatService)
    svc.gemini = gemini
    return svc


async def _drain(svc: ChatService) -> List[Any]:
    return [ev async for ev in svc.stream_synthesis(_PREP, "q", _ROUTE, tools=[], tool_handlers={})]


@pytest.mark.asyncio
async def test_unset_synthesis_works_against_the_pre_change_signatures(monkeypatch):
    _set(monkeypatch, None)
    g = _StrictGemini()
    events = await _drain(_svc(g))
    assert g.calls.count("agentic") == 2 and g.calls.count("text") == 1
    assert "".join(t for k, t in events if k == "answer") == "merged"


@pytest.mark.asyncio
@pytest.mark.parametrize("specialists_answer", [True, False], ids=["merge", "general-fallback"])
async def test_unset_synthesis_sends_no_level_on_any_call(monkeypatch, specialists_answer):
    _set(monkeypatch, None)
    g = _RecordingGemini(specialists_answer)
    await _drain(_svc(g))
    calls = g.agentic + g.text
    assert len(g.agentic) == (2 if specialists_answer else 3)
    assert len(g.text) == (1 if specialists_answer else 0)
    for kw in calls:
        assert "thinking_level" not in kw
        assert kw["thinking_budget"] == 1024


@pytest.mark.asyncio
@pytest.mark.parametrize("specialists_answer", [True, False], ids=["merge", "general-fallback"])
async def test_a_set_level_reaches_every_synthesis_call(monkeypatch, specialists_answer):
    _set(monkeypatch, "high")
    g = _RecordingGemini(specialists_answer)
    await _drain(_svc(g))
    calls = g.agentic + g.text
    assert calls
    for kw in calls:
        assert kw["thinking_level"] == "high"
        assert kw["thinking_budget"] == 1024   # the budget still rides for a model with no levels


# ── the service door: generate_response (non-stream) ────────────────────────

def _stub_collaborators(svc: ChatService, monkeypatch) -> None:
    import app.services.chat_context_resolver as res
    monkeypatch.setattr(res, "get_chat_context_resolver",
                        lambda: SimpleNamespace(resolve=AsyncMock(return_value=None)))
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


class _SendGemini:
    def __init__(self, tools_fail: bool):
        self.tools_fail = tools_fail
        self.with_tools: List[Dict[str, Any]] = []
        self.text: List[Dict[str, Any]] = []

    async def generate_with_tools(self, **kw):
        self.with_tools.append(kw)
        if self.tools_fail:
            raise RuntimeError("function calling exploded")
        return {"text": "tooled answer", "tokens_used": 40, "tool_results": []}

    async def generate_text(self, **kw):
        self.text.append(kw)
        return {"text": "plain answer", "tokens_used": 12}


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [None, "low"])
@pytest.mark.parametrize("tools_fail", [False, True], ids=["tools", "plain-text-fallback"])
async def test_generate_response_carries_the_level_only_when_set(monkeypatch, level, tools_fail):
    _set(monkeypatch, level)
    svc = _svc(_SendGemini(tools_fail))
    _stub_collaborators(svc, monkeypatch)
    out = await svc.generate_response("sess", "how is AAPL doing?", stock_id="AAPL")
    g = svc.gemini
    assert out["content"] == ("plain answer" if tools_fail else "tooled answer")
    assert len(g.with_tools) == 1 and len(g.text) == (1 if tools_fail else 0)
    for kw in g.with_tools + g.text:
        assert kw["thinking_budget"] == 1024
        if level is None:
            assert "thinking_level" not in kw
        else:
            assert kw["thinking_level"] == level


# ── the endpoint: the cut-answer continuation ───────────────────────────────

class _ContinueGemini:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def stream_text(self, prompt, **kw):
        self.calls.append(kw)
        yield ("answer", " and that finishes the answer as one clean sentence here.")


async def _continue(gemini, model: Optional[str]) -> Dict[str, Any]:
    from app.api.v1.endpoints import chat as chat_ep

    signals: dict = {}
    _ = [t async for _k, t in chat_ep._continue_cut_answer(
        SimpleNamespace(gemini=gemini), {"prompt": "USER MESSAGE: q", "system_instruction": "S"},
        "The answer so far", model_name=model, max_output_tokens=256, usage_tag="t",
        signals=signals)]
    return signals


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [None, *GEMINI_THINKING_LEVELS])
async def test_the_continuation_carries_the_level_for_its_model(monkeypatch, level):
    _set(monkeypatch, level)
    g = _ContinueGemini()
    signals = await _continue(g, FLAGSHIP)
    assert signals["continuation"] in ("clean", "cut")
    (kw,) = g.calls
    assert kw["model_name"] == FLAGSHIP and kw["thinking_budget"] == 1024
    if level is None:
        assert "thinking_level" not in kw
    else:
        assert kw["thinking_level"] == level


@pytest.mark.asyncio
async def test_the_continuation_of_a_cheap_route_answer_never_thinks(monkeypatch):
    _set(monkeypatch, "high")
    g = _ContinueGemini()
    await _continue(g, settings.CHAT_CHEAP_MODEL)
    (kw,) = g.calls
    assert "thinking_level" not in kw and kw["thinking_budget"] is None


# ── the endpoint: one real stream request (single mode + continuation) ──────

def test_unset_stream_turn_sends_no_level(harness, monkeypatch):
    client, _db, _quota, _ = harness
    _set(monkeypatch, None)
    r = _post(client)
    assert r.status_code == 200, r.text
    sent = _FakeChatService.instances[-1].gemini.stream_calls[0]
    assert "thinking_level" not in sent
    assert sent["thinking_budget"] == _chat_thinking_budget(sent["model_name"])


@pytest.mark.parametrize("level", GEMINI_THINKING_LEVELS)
def test_a_set_level_reaches_the_stream_doors_answer_call(harness, monkeypatch, level):
    client, _db, _quota, _ = harness
    _set(monkeypatch, level)
    r = _post(client)
    assert r.status_code == 200, r.text
    sent = _FakeChatService.instances[-1].gemini.stream_calls[0]
    assert sent["model_name"] != settings.CHAT_CHEAP_MODEL   # precondition: the flagship route
    assert sent["thinking_level"] == level
    assert sent["thinking_budget"] == 1024


def test_the_stream_doors_cheap_route_gets_no_level(harness, monkeypatch):
    client, _db, _quota, _ = harness
    _set(monkeypatch, "high")
    import app.services.agents.chat_router as router
    monkeypatch.setattr(router, "select_model", lambda route, **k: settings.CHAT_CHEAP_MODEL)
    r = _post(client)
    assert r.status_code == 200, r.text
    sent = _FakeChatService.instances[-1].gemini.stream_calls[0]
    assert sent["model_name"] == settings.CHAT_CHEAP_MODEL
    assert "thinking_level" not in sent and sent["thinking_budget"] is None


@pytest.mark.parametrize("level", [None, "medium"])
def test_a_cut_stream_answer_is_continued_with_the_same_level(harness, monkeypatch, level):
    client, _db, _quota, _ = harness
    _set(monkeypatch, level)
    _FakeChatService.events = list(_CUT_EVENTS)
    _FakeGemini.continue_events = [("answer", " supply is uncapped, so it is mildly inflationary.")]
    r = _post(client, message="Is MATIC inflationary?")
    assert r.status_code == 200, r.text
    answer_call = _FakeChatService.instances[-1].gemini.stream_calls[0]
    (cont,) = _FakeGemini.continue_calls
    assert cont["model_name"] == answer_call["model_name"]
    if level is None:
        assert "thinking_level" not in cont and "thinking_level" not in answer_call
    else:
        assert cont["thinking_level"] == level == answer_call["thinking_level"]


# ── through the REAL client encoder: what reaches the wire ──────────────────

class _Part:
    thought = False
    function_call = None

    def __init__(self, text):
        self._t = text

    @property
    def text(self):
        return self._t


class _Chunk:
    def __init__(self, text):
        self.candidates = [SimpleNamespace(content=SimpleNamespace(parts=[_Part(text)]),
                                           finish_reason=None)]


class _WireModels:
    def __init__(self):
        self.configs: List[Any] = []

    async def generate_content_stream(self, *, model, contents, config):
        self.configs.append((model, config))

        async def _gen():
            yield _Chunk("the rest of the answer, finished here as one sentence.")
        return _gen()


class _WireChat:
    async def send_message_stream(self, message, config=None):
        async def _gen():
            yield _Chunk("a specialist view")
        return _gen()


class _WireChats:
    def __init__(self):
        self.configs: List[Any] = []

    def create(self, *, model, config):
        self.configs.append((model, config))
        return _WireChat()


def _real_client(model: str):
    models, chats = _WireModels(), _WireChats()
    c = gem.GeminiClient.__new__(gem.GeminiClient)
    c.model_name = model
    c._temperature, c._max_tokens = 0.7, settings.GEMINI_MAX_TOKENS
    c._client = SimpleNamespace(aio=SimpleNamespace(models=models, chats=chats))
    gem._quota_circuit.record_success()
    return c, models, chats


@pytest.mark.asyncio
@pytest.mark.parametrize("model,level,want_level,want_budget", [
    # Unset: today's request on every model (a positive budget is sent as is).
    (LITE, None, None, 1024),
    (FLAGSHIP, None, None, 1024),
    # A model that takes the level gets the level alone (never both: a 400 everywhere).
    (LITE, "low", types.ThinkingLevel.LOW, None),
    ("gemini-3.8-flash", "high", types.ThinkingLevel.HIGH, None),
    # gemini-2.5-flash refuses every level: it is ignored and the budget rules.
    (FLAGSHIP, "low", None, 1024),
    # "minimal" on a model that refuses it means off: budget 0, sent as 1 where 0 is refused.
    (FLAGSHIP, "minimal", None, gem._BUDGET_OFF_ON_REFUSING_MODELS),
    ("gemini-3.8-flash", "minimal", None, 0),
])
async def test_the_continuation_wire_config(monkeypatch, model, level, want_level, want_budget):
    _set(monkeypatch, level)
    client, models, _chats = _real_client(FLAGSHIP)
    await _continue(client, model)
    ((sent_model, config),) = models.configs
    assert sent_model == model
    tc = config.thinking_config
    assert tc.include_thoughts is True   # the thinking card stays on
    assert tc.thinking_level == want_level
    assert tc.thinking_budget == want_budget


@pytest.mark.asyncio
async def test_synthesis_on_the_lite_model_sends_the_level_on_every_wire_config(monkeypatch):
    _set(monkeypatch, "medium")
    client, models, chats = _real_client(LITE)
    events = await _drain(_svc(client))
    assert "".join(t for k, t in events if k == "answer")
    configs = [c for _m, c in chats.configs] + [c for _m, c in models.configs]
    assert len(chats.configs) == 2 and len(models.configs) == 1
    for config in configs:
        tc = config.thinking_config
        assert tc.include_thoughts is True
        assert tc.thinking_level == types.ThinkingLevel.MEDIUM
        assert tc.thinking_budget is None


@pytest.mark.asyncio
async def test_unset_synthesis_on_the_lite_model_keeps_todays_wire_config(monkeypatch):
    _set(monkeypatch, None)
    client, models, chats = _real_client(LITE)
    await _drain(_svc(client))
    configs = [c for _m, c in chats.configs] + [c for _m, c in models.configs]
    assert len(configs) == 3
    for config in configs:
        tc = config.thinking_config
        assert tc.include_thoughts is True
        assert tc.thinking_level is None and tc.thinking_budget == 1024
