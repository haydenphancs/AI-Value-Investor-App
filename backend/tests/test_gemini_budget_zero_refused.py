"""thinking_budget=0 is never sent to a model that refuses it (stopgap 2026-11-15, corrected 2026-11-16).

Google deprecated gemini-2.5-flash and from 2026-12-01 routes every request that names it to
gemini-3.5-flash-lite. Measured live on 2026-11-15: gemini-3.5-flash-lite answers
`ThinkingConfig(thinking_budget=0)` with a bare 400 INVALID_ARGUMENT on EVERY request shape
(plain, JSON mode, response schema, tools, cached content, streaming with include_thoughts).
The app sends 0 ("thinking off") on the report agentic loop, Stage A, Stage B, the ETF hook,
index stories, the theme fit gate, the Learn writer/topic/cover subject and the marketing
writer, so all of them would fail on the cutover.

Two encodings, by the model the request NAMES:
* gemini-3.5-flash-lite: thinking_level "minimal", no budget. That is its real off (0 thought
  tokens on every prompt measured 2026-11-15/16). Budget 1 is NOT off there: on real prompts it
  thinks like level "low" (a Stage B narrative ~450-900 thought tokens per call).
* gemini-2.5-flash: budget 1. 2.5-flash refuses ANY thinking_level, and 1 is the smallest budget
  both sides of the reroute accept: 0 thought tokens on 2.5-flash itself, but "low"-like thinking
  once 3.5-flash-lite serves it. Only naming 3.5-flash-lite reaches the level path.
gemini-3.8-flash ACCEPTS 0 and REFUSES "minimal" (the judges rely on 0), and gemini-2.5-flash-lite
is not deprecated, so neither may change. A budget AND a level in one config is a 400 on every
model, so no config may carry both.

Three layers: the encoder's table (every edge of the model name and the budget), each client
method end to end through a recording fake SDK, and a source scan so a new call site cannot
skip the model.
"""

import ast
import logging
import sys
from pathlib import Path
from typing import Any, List

import pytest

from app.config import settings
from app.integrations import gemini as gem

BACKEND = Path(__file__).resolve().parents[1]
OFF = gem._BUDGET_OFF_ON_REFUSING_MODELS


# ── the encoder table ────────────────────────────────────────────────────────

def test_the_off_value_is_one():
    """1 is the smallest budget both sides of the reroute accept: 0 is the refused value itself,
    and a larger value lets 2.5-flash think (24 gave 21 thought tokens, 128 gave 98). It is 0
    thought tokens on gemini-2.5-flash, but NOT a guaranteed off once gemini-3.5-flash-lite serves
    it (re-measured 2026-11-16: ~450-900 thought tokens on a Stage B prompt), which is why a request
    naming 3.5-flash-lite gets thinking_level "minimal" instead (tests below)."""
    assert OFF == 1


_TWO_FIVE = ["gemini-2.5-flash", "models/gemini-2.5-flash", "  Gemini-2.5-Flash  ", "MODELS/GEMINI-2.5-FLASH"]
_LITE = ["gemini-3.5-flash-lite", "models/gemini-3.5-flash-lite",   # the context-cache spelling
         "  Gemini-3.5-Flash-Lite  ", "MODELS/GEMINI-3.5-FLASH-LITE"]  # case / whitespace


@pytest.mark.parametrize("model", _TWO_FIVE)
def test_zero_becomes_budget_one_on_a_request_naming_two_five_flash(model):
    assert gem._wire_budget(0, model) == OFF
    tc = gem._thinking_config(0, model=model)
    assert tc.thinking_budget == OFF and tc.thinking_level is None
    stream = gem._stream_thinking_config(0, model=model)
    assert stream.thinking_budget == OFF and stream.include_thoughts is True
    assert stream.thinking_level is None


@pytest.mark.parametrize("model", _LITE)
def test_zero_becomes_level_minimal_on_a_request_naming_three_five_lite(model):
    """Its real off switch, and nothing else on the config: a budget beside a level is a 400."""
    tc = gem._thinking_config(0, model=model)
    assert tc.thinking_level == gem.types.ThinkingLevel.MINIMAL
    assert tc.thinking_budget is None and tc.include_thoughts is None
    stream = gem._stream_thinking_config(0, model=model)
    assert stream.thinking_level == gem.types.ThinkingLevel.MINIMAL
    assert stream.thinking_budget is None and stream.include_thoughts is True


@pytest.mark.parametrize("model", _LITE)
def test_the_budget_encoder_alone_still_never_sends_zero_to_three_five_lite(model):
    """`_wire_budget` is the safety net for a direct caller: it never returns 0 for 3.5-flash-lite."""
    assert gem._wire_budget(0, model) == OFF


@pytest.mark.parametrize("budget", [1, 24, 512, 1024, 24576, -1, -500])
@pytest.mark.parametrize("model", _LITE)
def test_a_non_zero_budget_on_three_five_lite_stays_a_budget(model, budget):
    """Only a 0 becomes the level. A real ceiling (chat's 1024, theme insights' 512) or -1 must stay
    a budget on BOTH encoders — a level there would silently change how much the model thinks."""
    tc = gem._thinking_config(budget, model=model)
    assert tc.thinking_budget == budget and tc.thinking_level is None
    stream = gem._stream_thinking_config(budget, model=model)
    assert stream.thinking_budget == budget and stream.thinking_level is None
    assert stream.include_thoughts is True


def test_the_level_set_is_exactly_three_five_lite():
    """2.5-flash refuses ANY thinking_level and 3.8-flash refuses "minimal" (both 400, live-probed
    2026-11-15): adding either would break every request that names it."""
    assert gem._LEVEL_OFF_MODELS == frozenset({"gemini-3.5-flash-lite"})
    assert gem._LEVEL_OFF == gem.types.ThinkingLevel.MINIMAL
    assert gem._LEVEL_OFF_MODELS <= gem._BUDGET_ZERO_REFUSED_MODELS


_MATRIX_MODELS = ["gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash",
                  "gemini-2.5-flash-lite", "models/gemini-3.5-flash-lite", "", None]


@pytest.mark.parametrize("budget", [None, 0, 1, 512, 1024, -1, False])
@pytest.mark.parametrize("model", _MATRIX_MODELS)
def test_no_config_ever_carries_both_a_budget_and_a_level(model, budget):
    for tc in (gem._thinking_config(budget, model=model), gem._stream_thinking_config(budget, model=model)):
        if tc is not None:
            assert tc.thinking_budget is None or tc.thinking_level is None


@pytest.mark.parametrize("budget", [None, 0, 1, 512, 1024, -1])
@pytest.mark.parametrize("model", ["gemini-2.5-flash", "models/gemini-2.5-flash", "gemini-3.8-flash",
                                   "gemini-2.5-flash-lite", "gemini-3.5-flash-lite-preview", "", None])
def test_only_three_five_lite_ever_gets_a_level(model, budget):
    for tc in (gem._thinking_config(budget, model=model), gem._stream_thinking_config(budget, model=model)):
        if tc is not None:
            assert tc.thinking_level is None


@pytest.mark.parametrize("model", [
    "gemini-3.8-flash",            # the marketing + Learn judges: budget 0 accepted, keep it
    "gemini-2.5-flash-lite",       # not deprecated; chat_answer_coverage sends 0 to it
    "gemini-2.5-flash-lite-preview",
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-flashy",           # a near-miss name must not match by prefix
    "my-gemini-2.5-flash",
    "", None,                      # unknown model: leave the value exactly as the caller sent it
])
def test_zero_is_unchanged_on_every_other_model(model):
    assert gem._wire_budget(0, model) == 0
    assert gem._thinking_config(0, model=model).thinking_budget == 0
    assert gem._stream_thinking_config(0, model=model).thinking_budget == 0


@pytest.mark.parametrize("budget", [1, 24, 512, 1024, 24576, -1, -500])
@pytest.mark.parametrize("model", ["gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"])
def test_every_non_zero_budget_passes_through(model, budget):
    """Only 0 is refused. A positive ceiling or Gemini's -1 (dynamic) reaches the wire as sent."""
    assert gem._wire_budget(budget, model) == budget
    assert gem._thinking_config(budget, model=model).thinking_budget == budget


@pytest.mark.parametrize("model", ["gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"])
def test_none_still_means_no_thinking_config(model):
    """None stays the byte-identical rollback value: no ThinkingConfig at all (non-streaming),
    thoughts on with no budget (streaming)."""
    assert gem._wire_budget(None, model) is None
    assert gem._thinking_config(None, model=model) is None
    stream = gem._stream_thinking_config(None, model=model)
    assert stream.include_thoughts is True and stream.thinking_budget is None


def test_a_bool_is_not_read_as_zero():
    """False == 0 in Python; neither remap may fire on a wrong-typed value (it is passed through
    exactly as before the stopgap, so the remap adds no new behaviour for it)."""
    assert gem._wire_budget(False, "gemini-2.5-flash") is False
    assert gem._off_level(False, "gemini-3.5-flash-lite") is None
    assert gem._thinking_config(False, model="gemini-3.5-flash-lite").thinking_level is None


def test_the_model_argument_is_required_and_keyword_only():
    """A new call site that forgets the model must fail at once, not silently send 0."""
    with pytest.raises(TypeError):
        gem._thinking_config(0)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        gem._stream_thinking_config(0)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        gem._thinking_config(0, "gemini-2.5-flash")  # type: ignore[misc]


def test_the_refusing_set_is_exactly_the_two_measured_models():
    """Adding a model needs a live probe first (see the constant's comment): gemini-3.8-flash
    accepts 0 and REJECTS thinking_level 'minimal', so a wider rule breaks the judges."""
    assert gem._BUDGET_ZERO_REFUSED_MODELS == frozenset({"gemini-2.5-flash", "gemini-3.5-flash-lite"})


def test_the_remap_logs_once_per_model(caplog, monkeypatch):
    monkeypatch.setattr(gem, "_budget_zero_remap_logged", set())
    with caplog.at_level(logging.INFO, logger=gem.logger.name):
        for _ in range(3):
            gem._wire_budget(0, "gemini-2.5-flash")
        gem._wire_budget(0, "models/gemini-2.5-flash")   # same model, other spelling
        gem._wire_budget(0, "gemini-3.5-flash-lite")
        gem._wire_budget(0, "gemini-3.8-flash")          # not remapped: no line
    lines = [r.getMessage() for r in caplog.records if "thinking_budget 0 sent as" in r.getMessage()]
    assert len(lines) == 2
    assert any("model=gemini-2.5-flash " in m for m in lines)
    assert any("model=gemini-3.5-flash-lite " in m for m in lines)
    # The line no longer claims budget 1 is "no thinking" (it is not, once 3.5-flash-lite serves it).
    assert not any("no thinking" in m for m in lines)


def test_the_level_remap_logs_once_per_model(caplog, monkeypatch):
    monkeypatch.setattr(gem, "_level_off_logged", set())
    with caplog.at_level(logging.INFO, logger=gem.logger.name):
        for _ in range(3):
            gem._thinking_config(0, model="gemini-3.5-flash-lite")
        gem._stream_thinking_config(0, model="models/gemini-3.5-flash-lite")   # other spelling
        gem._thinking_config(0, model="gemini-2.5-flash")                     # budget path: no level line
    lines = [r.getMessage() for r in caplog.records if "thinking_level=minimal" in r.getMessage()]
    assert lines == ["Gemini thinking_budget 0 sent as thinking_level=minimal for model=gemini-3.5-flash-lite "
                     "(its off switch)"]


# ── each client method, end to end through a recording fake SDK ──────────────

class _Part:
    thought = False
    function_call = None

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text='{"ok": true}'):
        self.candidates = [type("C", (), {"content": type("Ct", (), {"parts": [_Part(text)]})(),
                                          "finish_reason": "STOP"})()]
        self.usage_metadata = None
        self.text = text


class _Chunk:
    def __init__(self, text):
        self.candidates = [type("C", (), {"content": type("Ct", (), {"parts": [_Part(text)]})(),
                                          "finish_reason": None})()]
        self.usage_metadata = None


def _client(model="gemini-2.5-flash"):
    seen: List[dict] = []

    class _Models:
        async def generate_content(self, **kw):
            seen.append(kw)
            return _Resp()

        async def generate_content_stream(self, **kw):
            seen.append(kw)

            async def _gen():
                yield _Chunk("hi")
            return _gen()

    class _Chat:
        async def send_message_stream(self, message):
            async def _gen():
                yield _Chunk("hi")
            return _gen()

    class _Chats:
        def create(self, **kw):
            seen.append(kw)
            return _Chat()

    c = gem.GeminiClient.__new__(gem.GeminiClient)
    c.model_name = model
    c._temperature, c._max_tokens = 0.7, settings.GEMINI_MAX_TOKENS
    c._response_cache = gem._TTLCache(max_size=16, ttl_seconds=3600)
    c._embedding_cache = gem._TTLCache(max_size=16, ttl_seconds=3600)
    c._client = type("Cl", (), {"aio": type("Aio", (), {"models": _Models(), "chats": _Chats()})()})()
    gem._quota_circuit.record_success()
    return c, seen


#: The wire value expected for a caller's 0 on a request naming gemini-3.5-flash-lite.
LEVEL = "level:minimal"


def _budget(kw: dict) -> Any:
    """The thinking setting on the wire: the budget, or LEVEL for thinking_level "minimal"
    (asserting the config never carries both)."""
    tc = kw["config"].thinking_config
    if tc is None:
        return None
    assert tc.thinking_budget is None or tc.thinking_level is None, "budget AND level is a 400"
    if tc.thinking_level is not None:
        assert tc.thinking_level == gem.types.ThinkingLevel.MINIMAL
        return LEVEL
    return tc.thinking_budget


# (client model, model_name override, expected wire value for a caller's 0)
_ROUTES = [
    ("gemini-2.5-flash", None, OFF),                      # GEMINI_MODEL default: reports, Stage A/B
    ("gemini-2.5-flash", "gemini-2.5-flash", OFF),        # hardcoded ETF / index / marketing writer
    ("gemini-2.5-flash", "gemini-3.5-flash-lite", LEVEL), # an explicit switch to the new model
    ("gemini-3.5-flash-lite", None, LEVEL),               # GEMINI_MODEL switched to the new model
    ("gemini-2.5-flash", "gemini-3.8-flash", 0),          # the judges: unchanged
    ("gemini-2.5-flash", "gemini-2.5-flash-lite", 0),     # chat_answer_coverage: unchanged
    ("gemini-3.8-flash", None, 0),                        # a client whose default accepts 0
]


@pytest.mark.asyncio
@pytest.mark.parametrize("client_model,override,expected", _ROUTES)
async def test_generate_text_and_generate_json_use_the_model_they_send_to(client_model, override, expected):
    c, seen = _client(client_model)
    await c.generate_text(prompt="p", model_name=override, thinking_budget=0)
    await c.generate_json(prompt="p", model_name=override, thinking_budget=0, cache=False)
    for kw in seen:
        assert kw["model"] == (override or client_model)
        assert _budget(kw) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("client_model,override,expected", _ROUTES)
async def test_the_two_streams_use_the_model_they_send_to(client_model, override, expected):
    c, seen = _client(client_model)
    async for _ in c.stream_text("p", model_name=override, thinking_budget=0):
        pass
    async for _ in c.stream_agentic("p", tools=[], tool_handlers={}, model_name=override, thinking_budget=0):
        pass
    assert len(seen) == 2
    for kw in seen:
        assert kw["model"] == (override or client_model)
        assert kw["config"].thinking_config.include_thoughts is True
        assert _budget(kw) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("client_model,expected", [("gemini-2.5-flash", OFF), ("gemini-3.5-flash-lite", LEVEL),
                                                   ("gemini-3.8-flash", 0)])
async def test_the_cached_stage_b_call_and_the_tool_chat_use_the_client_model(client_model, expected):
    """Both always send `self.model_name` (the cache is created under it), so that is the model
    the encoder must see."""
    c, seen = _client(client_model)

    class _Cache:
        name = "cachedContents/x"

    await c.generate_text_cached("job", {"cache": _Cache()}, thinking_budget=0)
    c.create_tool_chat(system_instruction="sys", tools=[], thinking_budget=0)
    assert [kw["model"] for kw in seen] == [client_model, client_model]
    assert [_budget(kw) for kw in seen] == [expected, expected]


@pytest.mark.asyncio
@pytest.mark.parametrize("override,expected", [(None, OFF), ("gemini-3.8-flash", 0),
                                               ("gemini-3.5-flash-lite", LEVEL)])
async def test_generate_with_tools_uses_the_model_it_sends_to(override, expected):
    c, _ = _client("gemini-2.5-flash")
    configs: List[Any] = []

    async def _fake(*, model, contents, config, what):
        configs.append((model, config))
        return _Resp("fine")

    c._generate_content_retried = _fake  # type: ignore[assignment]
    await c.generate_with_tools("p", tools=[], tool_handlers={}, model_name=override, thinking_budget=0)
    assert configs and all(m == (override or "gemini-2.5-flash") for m, _ in configs)
    assert all(_budget({"config": cfg}) == expected for _, cfg in configs)


@pytest.mark.asyncio
async def test_generate_with_tools_final_answer_round_also_remaps():
    """The tool-less final round (reached after a SECOND function call) builds its own config;
    it must see the same model as the first request, or a 0 budget reaches 2.5-flash there."""
    c, _ = _client("gemini-2.5-flash")
    configs: List[Any] = []

    class _FC:
        def __init__(self, name):
            self.name, self.args = name, {}

    class _FCPart(_Part):
        def __init__(self, name):
            super().__init__("")
            self.function_call = _FC(name)

    class _ToolResp:
        """Like the SDK: `.text` raises when the candidate holds only function calls."""
        usage_metadata = None

        def __init__(self, parts):
            self.candidates = [type("C", (), {"content": type("Ct", (), {"parts": parts})(),
                                              "finish_reason": "STOP"})()]

        @property
        def text(self):
            texts = [p.text for p in self.candidates[0].content.parts if p.text]
            if not texts:
                raise ValueError("no text part")
            return "".join(texts)

    def _resp(parts):
        return _ToolResp(parts)

    script = [_resp([_FCPart("get_stock_chart_data")]),     # round 1: a tool call
              _resp([_FCPart("get_ticker_news")]),          # follow-up: ANOTHER call
              _resp([_Part("Apple is doing fine.")])]       # final: the answer, tools=None

    async def _fake(*, model, contents, config, what):
        configs.append((model, config))
        return script.pop(0)

    async def _handler(args):
        return {"ok": True}

    c._generate_content_retried = _fake  # type: ignore[assignment]
    out = await c.generate_with_tools("p", tools=[], tool_handlers={"get_stock_chart_data": _handler,
                                                                     "get_ticker_news": _handler},
                                      thinking_budget=0)
    assert out["text"] == "Apple is doing fine."
    assert len(configs) == 3
    assert configs[2][1].tools is None, "the third request must be the tool-less final round"
    for model, cfg in configs:
        assert model == "gemini-2.5-flash"
        assert cfg.thinking_config.thinking_budget == OFF


@pytest.mark.asyncio
async def test_the_same_prompt_on_two_models_is_two_cache_entries_with_two_wire_values():
    """The response cache keys on the caller's budget and the model override, so a 0 sent to
    3.8-flash (wire 0) and to 2.5-flash (wire 1) can never be served to each other."""
    c, seen = _client("gemini-2.5-flash")
    for override in ("gemini-3.8-flash", "gemini-2.5-flash", "gemini-3.8-flash", "gemini-2.5-flash"):
        await c.generate_text(prompt="same", model_name=override, thinking_budget=0)
    assert [(kw["model"], _budget(kw)) for kw in seen] == [("gemini-3.8-flash", 0), ("gemini-2.5-flash", OFF)]


# ── source scan: nothing can bypass the encoders (AST, so comments and docstrings never count) ──

_ENCODERS = {"_thinking_config", "_stream_thinking_config"}


def _callee_name(call: ast.Call) -> str:
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _enclosing_functions(tree: ast.AST) -> dict:
    """Map every node id to the name of its innermost (sync OR async) function, '' at module level."""
    owner: dict = {}

    def visit(node, fn_name):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn_name
            owner[id(child)] = name
            visit(child, name)
    visit(tree, "")
    return owner


def _is_dict_value(node: ast.AST) -> bool:
    return isinstance(node, ast.Dict) or (isinstance(node, ast.Call) and _callee_name(node) == "dict")


def _bypasses(path: Path) -> list:
    """Every way a module could put a thinking setting on the wire without the encoders."""
    tree = ast.parse(path.read_text())
    owner = _enclosing_functions(tree)
    rel = str(path.relative_to(BACKEND))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and _callee_name(n) == "ThinkingConfig":
            if not (rel == "app/integrations/gemini.py" and owner.get(id(n)) in _ENCODERS):
                out.append(f"{rel}:{n.lineno} ThinkingConfig( in {owner.get(id(n)) or '<module>'}")
        if isinstance(n, ast.keyword) and n.arg == "thinking_config" and _is_dict_value(n.value):
            out.append(f"{rel}:{n.value.lineno} thinking_config=<dict>")
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value in ("thinking_config", "thinkingConfig") and _is_dict_value(v):
                    out.append(f"{rel}:{k.lineno} '{k.value}': <dict>")
    return out


def test_every_encoder_call_in_the_client_passes_a_real_model():
    """AST walk (a substring scan would match the definitions). Python would also raise a
    TypeError, but only when that path runs; this fails the build before it ships. `model=None`
    is refused too: it would pass the keyword check and silently skip the remap."""
    tree = ast.parse((BACKEND / "app" / "integrations" / "gemini.py").read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _callee_name(n) in _ENCODERS]
    assert len(calls) >= 8, f"expected the 8 generation paths, found {len(calls)}"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "model" in kw, f"line {call.lineno}: {_callee_name(call)} without model="
        assert not (isinstance(kw["model"], ast.Constant) and kw["model"].value is None), \
            f"line {call.lineno}: model=None skips the remap"


def test_no_thinking_setting_reaches_the_wire_outside_the_encoders():
    """A ThinkingConfig built anywhere but the two encoders (sync or async function, module level,
    bare name or attribute), or a thinking_config passed as a dict, would bypass the remap."""
    offenders = [o for path in sorted((BACKEND / "app").rglob("*.py")) for o in _bypasses(path)]
    assert offenders == [], f"thinking settings built outside the encoders: {offenders}"


@pytest.mark.parametrize("snippet", [
    "async def gen(self):\n    return types.ThinkingConfig(thinking_budget=0)\n",
    "from google.genai.types import ThinkingConfig\nX = ThinkingConfig(thinking_budget=0)\n",
    "def f():\n    tag = '#x'; return dict(t=types.ThinkingConfig(thinking_budget=0))\n",
    "def f():\n    return types.GenerateContentConfig(thinking_config={'thinking_budget': 0})\n",
    "def f():\n    return dict(thinking_config=dict(thinking_budget=0))\n",
    "CFG = {'thinkingConfig': {'thinkingBudget': 0}}\n",
])
def test_the_scan_catches_each_bypass_shape(tmp_path, monkeypatch, snippet):
    """Mutation proof for the guard above: every shape the review found must be flagged."""
    monkeypatch.setattr(sys.modules[__name__], "BACKEND", tmp_path)
    p = tmp_path / "app" / "services" / "x.py"
    p.parent.mkdir(parents=True)
    p.write_text(snippet)
    assert _bypasses(p), snippet


def test_the_scan_ignores_docstrings_and_comments(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "BACKEND", tmp_path)
    p = tmp_path / "app" / "services" / "y.py"
    p.parent.mkdir(parents=True)
    p.write_text('"""Build a ThinkingConfig(include_thoughts=True) here."""\n# ThinkingConfig(thinking_budget=0)\nX = 1\n')
    assert _bypasses(p) == []
