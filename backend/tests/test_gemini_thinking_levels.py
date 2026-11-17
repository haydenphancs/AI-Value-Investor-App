"""Thinking LEVELS: the encoder's whole table, and every client method's wiring (2026-11-16).

Every GeminiClient generation method takes an optional `thinking_level` (minimal / low / medium /
high). The encoder (`gemini._thinking_fields`, behind `_thinking_config` and
`_stream_thinking_config`) puts a level on the wire ONLY for a model that accepts it, live-probed
2026-11-15: gemini-3.5-flash-lite takes all four, gemini-3.8-flash refuses "minimal", and
gemini-2.5-flash refuses every level. Elsewhere "minimal" means "off" (budget 0 rules) and the
other levels are ignored so the caller's budget applies. A budget AND a level in one config is a
400 on every model, so no config may carry both.

Three layers:
* the encoder over EVERY cell of (model spelling × budget × level), checked against a reference
  written here from the documented rules (never read from gemini.py), plus the invariants;
* each of the seven client methods through a recording fake SDK: a call with no level is the
  request it was before, and a level reaches the wire (including the forced and final rounds);
* the response cache: a level joins the key only when set, and no level keeps the old key.

Hermetic: the SDK is a fake, nothing leaves the process.
"""

import hashlib
import inspect
import logging
from typing import Any, Dict, List, Optional

import pytest
from google.genai import types

from app.config import GEMINI_THINKING_LEVELS, settings
from app.integrations import gemini as gem

_TWO_FIVE = "gemini-2.5-flash"
_LITE = "gemini-3.5-flash-lite"
_THREE_EIGHT = "gemini-3.8-flash"

# ── the reference, from the documented rules (not from gemini.py) ────────────

_LEVEL_NAMES = ("minimal", "low", "medium", "high")
#: What each model ACCEPTS (live probe 2026-11-15). Every other model accepts no level.
_ACCEPTS = {_LITE: frozenset(_LEVEL_NAMES), _THREE_EIGHT: frozenset({"low", "medium", "high"})}
#: Models that answer thinking_budget=0 with a 400.
_REFUSE_ZERO = frozenset({_TWO_FIVE, _LITE})


def _spec_key(model: Any) -> str:
    """The model as the probe tables name it: trimmed, lower case, ONE `models/` prefix dropped."""
    name = ("" if model is None else str(model)).strip().lower()
    return name[len("models/"):] if name.startswith("models/") else name


def _spec_level(level: Any) -> Optional[str]:
    """A level the caller really asked for, or None (no level / blank / junk / not text)."""
    if not isinstance(level, str):
        return None
    text = level.strip().lower()
    return text if text in _LEVEL_NAMES else None


def _pre_level(budget: Any, model: Any) -> Dict[str, Any]:
    """The budget-only encoding every caller got before levels existed: None → nothing; an int 0 →
    level "minimal" on a request naming gemini-3.5-flash-lite (its real off), budget 1 on one
    naming gemini-2.5-flash (the smallest budget both sides of the reroute accept), 0 elsewhere;
    every other value (bools included) passed through as sent."""
    if budget is None:
        return {}
    if budget == 0 and not isinstance(budget, bool):
        key = _spec_key(model)
        if key == _LITE:
            return {"thinking_level": "MINIMAL"}
        if key == _TWO_FIVE:
            return {"thinking_budget": 1}
    return {"thinking_budget": budget}


def _spec(budget: Any, level: Any, model: Any) -> Dict[str, Any]:
    """Rule 1: an accepted level goes alone. Rule 2: a refused "minimal" is budget 0. Rule 3:
    anything else (a refused low/medium/high, junk, None) leaves the budget-only encoding."""
    key, asked = _spec_key(model), _spec_level(level)
    if asked is not None and asked in _ACCEPTS.get(key, ()):
        return {"thinking_level": asked.upper()}
    if asked == "minimal":
        return _pre_level(0, model)
    return _pre_level(budget, model)


def _spec_wire(spec: Dict[str, Any]) -> Dict[str, Any]:
    """The reference as the SDK's pydantic model holds it: a bool budget is coerced to int."""
    return {k: (v if k == "thinking_level" else int(v)) for k, v in spec.items()}


def _exact(fields: Dict[str, Any], *, encoder: bool) -> Dict[str, Any]:
    """Fields with their TYPE, so `False` and `0` never compare equal by accident."""
    out = {}
    for k, v in fields.items():
        if k == "thinking_level":
            if encoder:
                assert isinstance(v, types.ThinkingLevel), f"level must be the SDK enum, got {v!r}"
                v = v.value
            out[k] = ("level", v)
        else:
            out[k] = (type(v).__name__, v)
    return out


def _wire(tc: Any) -> Dict[str, Any]:
    """The ThinkingConfig as it will be serialised (None = no config at all)."""
    return {} if tc is None else tc.model_dump(mode="json", exclude_none=True)


# ── the table: every model spelling × budget × level ─────────────────────────

_MODELS = [
    _TWO_FIVE, _LITE, _THREE_EIGHT, "gemini-2.5-flash-lite",
    "gemini-3.6-flash",                                  # an unprobed 3.x: no level, budget as sent
    "models/gemini-2.5-flash", "models/gemini-3.5-flash-lite", "models/gemini-3.8-flash",
    "GEMINI-2.5-FLASH", "Gemini-3.5-Flash-Lite", "GEMINI-3.8-FLASH", "Models/Gemini-3.5-Flash-Lite",
    "  gemini-2.5-flash  ", "\tgemini-3.5-flash-lite\n", " gemini-3.8-flash",
    "", None,
    # near misses: a table entry must never match by prefix or substring
    "gemini-3.5-flash-lite-preview", "gemini-3.8-flash-preview", "gemini-3.5-flash",
    "models/models/gemini-3.5-flash-lite",
]
_BUDGETS = [None, 0, 1, 512, 1024, -1, False, True]
_LEVELS = [None, "", "minimal", "low", "medium", "high", " Medium ", "MINIMAL", "turbo", 3, False]


def _false_budget_defect(budget: Any, level: Any, model: Any) -> bool:
    """The one known-bad cell family (reported; pinned by the strict xfail below): a `False`
    budget slips past both bool guards and pydantic coerces it to thinking_budget 0, the value
    the refusing models answer with a 400."""
    return (budget is False and _spec_key(model) in _REFUSE_ZERO
            and _spec(budget, level, model) == {"thinking_budget": False})


@pytest.mark.parametrize("level", _LEVELS, ids=repr)
@pytest.mark.parametrize("budget", _BUDGETS, ids=repr)
@pytest.mark.parametrize("model", _MODELS, ids=repr)
def test_every_cell_of_the_encoder_table(model, budget, level):
    key, asked = _spec_key(model), _spec_level(level)
    fields = gem._thinking_fields(budget, level, model)
    tc = gem._thinking_config(budget, model=model, level=level)
    stream = gem._stream_thinking_config(budget, model=model, level=level)
    spec = _spec(budget, level, model)

    # The whole reference, field for field and type for type, then as serialised.
    assert _exact(fields, encoder=True) == _exact(spec, encoder=False)
    assert (tc is None) == (not spec), "no config at all exactly when nothing is to be sent"
    assert _wire(tc) == _spec_wire(spec)
    assert _wire(stream) == {"include_thoughts": True, **_spec_wire(spec)}
    assert stream.include_thoughts is True

    for wire in (_wire(tc), _wire(stream)):
        assert not ("thinking_budget" in wire and "thinking_level" in wire), "budget AND level: a 400"
        if "thinking_level" in wire:
            assert key in (_LITE, _THREE_EIGHT), f"a level sent to {model!r}"
            if key == _THREE_EIGHT:
                assert wire["thinking_level"] != "MINIMAL", "gemini-3.8-flash refuses minimal"
        if key == _TWO_FIVE:
            assert "thinking_level" not in wire, "gemini-2.5-flash refuses every level"
        if key in _REFUSE_ZERO and not _false_budget_defect(budget, level, model):
            assert wire.get("thinking_budget") != 0, f"budget 0 sent to {model!r}"

    # "minimal" means off everywhere: identical to a plain budget 0, whatever budget came with it.
    if asked == "minimal":
        assert _exact(fields, encoder=True) == _exact(gem._thinking_fields(0, None, model), encoder=True)
        assert _wire(tc) == _wire(gem._thinking_config(0, model=model))

    # An ignored level (refused low/medium/high, blank, junk, not text) changes nothing at all.
    if asked is None or (asked != "minimal" and asked not in _ACCEPTS.get(key, ())):
        assert _exact(fields, encoder=True) == _exact(gem._thinking_fields(budget, None, model), encoder=True)
        assert _wire(tc) == _wire(gem._thinking_config(budget, model=model))
        assert _wire(stream) == _wire(gem._stream_thinking_config(budget, model=model))

    # No level reproduces the budget-only encoding exactly (the pre-level behaviour).
    if level is None:
        assert _exact(fields, encoder=True) == _exact(_pre_level(budget, model), encoder=False)


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT, reported 2026-11-16: budget=False passes the `isinstance(budget, bool)` guards in "
    "_wire_budget/_off_level unchanged, and ThinkingConfig coerces it to thinking_budget=0, the "
    "value gemini-2.5-flash / gemini-3.5-flash-lite refuse. Strict: once the encoder maps a bool "
    "to None or to the off encoding this XPASSes, so delete the marker then."))
@pytest.mark.parametrize("model,level", [
    (model, level)
    for model in (_TWO_FIVE, _LITE, "models/gemini-3.5-flash-lite")
    for level in (None, "low", "turbo")
    if _false_budget_defect(False, level, model)      # an accepted level replaces the budget
])
def test_a_false_budget_never_reaches_a_refusing_model_as_zero(model, level):
    for tc in (gem._thinking_config(False, model=model, level=level),
               gem._stream_thinking_config(False, model=model, level=level)):
        assert _wire(tc).get("thinking_budget") != 0


def test_the_encoder_tables_match_the_live_probe():
    """A model joins the level table only after a live probe: 2.5-flash refuses every level and
    3.8-flash refuses minimal (both 400). The keys must be spelled as `_model_key` normalises."""
    assert gem._LEVELS_BY_MODEL == _ACCEPTS
    for name, levels in gem._LEVELS_BY_MODEL.items():
        assert name == _spec_key(name)
        assert levels <= frozenset(GEMINI_THINKING_LEVELS)
    assert _TWO_FIVE not in gem._LEVELS_BY_MODEL and "gemini-2.5-flash-lite" not in gem._LEVELS_BY_MODEL
    assert tuple(GEMINI_THINKING_LEVELS) == _LEVEL_NAMES
    assert {k: v.value for k, v in gem._LEVEL_ENUM.items()} == {n: n.upper() for n in _LEVEL_NAMES}
    assert gem._LEVEL_OFF_MODELS == frozenset({_LITE})


@pytest.mark.parametrize("enum_level,expected", [
    (types.ThinkingLevel.LOW, {"thinking_level": "LOW"}),
    (types.ThinkingLevel.MINIMAL, {"thinking_level": "MINIMAL"}),
    (types.ThinkingLevel.THINKING_LEVEL_UNSPECIFIED, {"thinking_budget": 512}),   # ignored
])
def test_an_sdk_enum_level_reads_like_its_name(enum_level, expected):
    """A caller passing the SDK's own enum (a str subclass) gets what its name asks for."""
    assert _wire(gem._thinking_config(512, model=_LITE, level=enum_level)) == expected


def test_an_ignored_level_warns_once_per_model_and_level(caplog, monkeypatch):
    monkeypatch.setattr(gem, "_level_ignored_logged", set())
    with caplog.at_level(logging.WARNING, logger=gem.logger.name):
        for _ in range(3):
            gem._thinking_fields(0, "low", _TWO_FIVE)
        gem._thinking_fields(0, " LOW ", "models/gemini-2.5-flash")   # same pair, other spelling
        gem._thinking_fields(0, "high", _TWO_FIVE)                    # another level
        gem._thinking_fields(0, "low", "gemini-3.6-flash")            # another model
        gem._thinking_fields(0, "low", _LITE)                         # accepted: no line
        gem._thinking_fields(0, "minimal", _THREE_EIGHT)              # off, not ignored: no line
    lines = [r.getMessage() for r in caplog.records if "is not accepted by model=" in r.getMessage()]
    assert len(lines) == 3, lines
    assert all(r.levelno == logging.WARNING for r in caplog.records if "is not accepted" in r.getMessage())


@pytest.mark.parametrize("junk", ["turbo", 3, False, types.ThinkingLevel.THINKING_LEVEL_UNSPECIFIED])
def test_a_junk_level_is_logged_never_silent(caplog, junk):
    """Settings validate at start-up; any other caller's junk degrades to "no level" WITH a line."""
    with caplog.at_level(logging.WARNING, logger=gem.logger.name):
        assert gem._thinking_fields(None, junk, _LITE) == {}
    assert any("ignored" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


def test_a_blank_or_absent_level_is_quiet(caplog):
    with caplog.at_level(logging.DEBUG, logger=gem.logger.name):
        for level in (None, "", "   "):
            assert gem._thinking_fields(512, level, _LITE) == {"thinking_budget": 512}
    assert not [r for r in caplog.records if "thinking_level" in r.getMessage()]


# ── the client: a recording fake SDK ─────────────────────────────────────────

class _FC:
    def __init__(self, name):
        self.name, self.args = name, {}


class _Part:
    thought = False
    function_call = None

    def __init__(self, text):
        self.text = text


class _FCPart(_Part):
    def __init__(self, name):
        super().__init__("")
        self.function_call = _FC(name)


class _Resp:
    usage_metadata = None

    def __init__(self, parts=None, text='{"ok": true}'):
        self._parts = parts if parts is not None else [_Part(text)]
        self.candidates = [type("C", (), {"content": type("Ct", (), {"parts": self._parts})(),
                                          "finish_reason": "STOP"})()]

    @property
    def text(self):
        texts = [p.text for p in self._parts if p.text]
        if not texts:
            raise ValueError("no text part")
        return "".join(texts)


class _Chunk:
    usage_metadata = None

    def __init__(self, parts, finish=None):
        self.candidates = [type("C", (), {"content": type("Ct", (), {"parts": parts})(),
                                          "finish_reason": finish})()]


class _CachedContent:
    name = "cachedContents/x"


def _client(model: str, *, chat_rounds: Optional[List[List[Any]]] = None):
    """A GeminiClient on `model` whose SDK records every request: `seen` holds each
    generate_content / generate_content_stream / chats.create kwargs; `sent` holds the
    per-message config of each chat send (None = the chat's own config)."""
    seen: List[dict] = []
    sent: List[Any] = []
    rounds = list(chat_rounds or [])

    class _Models:
        async def generate_content(self, **kw):
            seen.append(kw)
            return _Resp()

        async def generate_content_stream(self, **kw):
            seen.append(kw)

            async def _gen():
                yield _Chunk([_Part("hi")], finish="STOP")
            return _gen()

    class _Chat:
        async def send_message_stream(self, message, config=None):
            sent.append(config)
            parts = rounds.pop(0) if rounds else [_Part("hi")]

            async def _gen():
                yield _Chunk(parts, finish="STOP")
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
    return c, seen, sent


_METHODS = ["generate_text", "generate_json", "generate_text_cached", "stream_text",
            "stream_agentic", "generate_with_tools", "create_tool_chat"]
_STREAMS = {"stream_text", "stream_agentic"}


async def _drive(c, method: str, *, budget: Any, model_name: Optional[str] = None, **level_kw):
    """Call `method` once the way its callers do. `level_kw` is either empty (the caller passes
    no level, as every caller did before) or {"thinking_level": x}."""
    if method == "generate_text":
        await c.generate_text(prompt="p", model_name=model_name, thinking_budget=budget, **level_kw)
    elif method == "generate_json":
        await c.generate_json(prompt="p", model_name=model_name, thinking_budget=budget, cache=False,
                              **level_kw)
    elif method == "generate_text_cached":
        assert model_name is None, "the cached call always sends the client's model"
        await c.generate_text_cached("job", {"cache": _CachedContent()}, thinking_budget=budget, **level_kw)
    elif method == "stream_text":
        async for _ in c.stream_text("p", model_name=model_name, thinking_budget=budget, **level_kw):
            pass
    elif method == "stream_agentic":
        async for _ in c.stream_agentic("p", tools=[], tool_handlers={}, model_name=model_name,
                                        thinking_budget=budget, **level_kw):
            pass
    elif method == "generate_with_tools":
        await c.generate_with_tools("p", tools=[], tool_handlers={}, model_name=model_name,
                                    thinking_budget=budget, **level_kw)
    elif method == "create_tool_chat":
        assert model_name is None, "the tool chat always sends the client's model"
        c.create_tool_chat(system_instruction="sys", tools=[], thinking_budget=budget, **level_kw)
    else:  # pragma: no cover
        raise AssertionError(method)


def _config_wire(kw: dict) -> Dict[str, Any]:
    return _wire(getattr(kw["config"], "thinking_config", None))


@pytest.mark.parametrize("method", _METHODS)
def test_every_method_accepts_thinking_level_defaulting_to_none(method):
    """`inspect.signature` follows @async_retry's `functools.wraps`, so this reads the real method."""
    param = inspect.signature(getattr(gem.GeminiClient, method)).parameters.get("thinking_level")
    assert param is not None, f"{method} takes no thinking_level"
    assert param.default is None
    assert param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [None, 0, 512, -1])
@pytest.mark.parametrize("model", [_TWO_FIVE, _LITE, _THREE_EIGHT, "gemini-2.5-flash-lite"])
@pytest.mark.parametrize("method", _METHODS)
async def test_default_settings_send_todays_request(method, model, budget):
    """BEHAVIOUR-NEUTRAL: a caller that passes no level (every caller under default settings) sends
    the budget-only encoding, and passing thinking_level=None sends the very same request."""
    c, seen, _ = _client(model)
    await _drive(c, method, budget=budget)
    keys = set(c._response_cache._store)
    c._response_cache = gem._TTLCache(max_size=16, ttl_seconds=3600)   # else generate_text hits it
    await _drive(c, method, budget=budget, thinking_level=None)
    assert set(c._response_cache._store) == keys, "thinking_level=None must keep the cache key"
    assert len(seen) == 2
    omitted, explicit_none = seen
    expected = _spec_wire(_pre_level(budget, model))
    if method in _STREAMS:
        expected = {"include_thoughts": True, **expected}
    assert _config_wire(omitted) == expected
    assert omitted["model"] == explicit_none["model"] == model
    assert (omitted["config"].model_dump(exclude_none=True)
            == explicit_none["config"].model_dump(exclude_none=True))


# (client model, budget, level, expected thinking fields on the wire, without include_thoughts)
_FORWARD = [
    (_LITE, None, "low", {"thinking_level": "LOW"}),
    (_LITE, 512, "high", {"thinking_level": "HIGH"}),               # an accepted level drops the budget
    (_LITE, None, "minimal", {"thinking_level": "MINIMAL"}),
    ("models/gemini-3.5-flash-lite", 0, " Medium ", {"thinking_level": "MEDIUM"}),
    (_THREE_EIGHT, 0, "medium", {"thinking_level": "MEDIUM"}),
    (_THREE_EIGHT, None, "minimal", {"thinking_budget": 0}),         # refused minimal → off
    (_TWO_FIVE, 0, "low", {"thinking_budget": 1}),                   # ignored: the budget applies
    (_TWO_FIVE, None, "minimal", {"thinking_budget": 1}),            # off, as budget 0 on 2.5-flash
    (_TWO_FIVE, None, "high", {}),                                   # ignored: nothing at all
    ("gemini-2.5-flash-lite", 512, "low", {"thinking_budget": 512}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("model,budget,level,expected", _FORWARD)
@pytest.mark.parametrize("method", _METHODS)
async def test_every_method_forwards_the_level(method, model, budget, level, expected):
    c, seen, _ = _client(model)
    await _drive(c, method, budget=budget, thinking_level=level)
    assert len(seen) == 1 and seen[0]["model"] == model
    if method in _STREAMS:
        expected = {"include_thoughts": True, **expected}
    assert _config_wire(seen[0]) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["generate_text", "generate_json", "stream_text", "stream_agentic",
                                    "generate_with_tools"])
@pytest.mark.parametrize("client_model,override,expected", [
    (_TWO_FIVE, _LITE, {"thinking_level": "LOW"}),     # the override accepts it
    (_LITE, _TWO_FIVE, {}),                            # the override refuses it: ignored
    (_LITE, _THREE_EIGHT, {"thinking_level": "LOW"}),
])
async def test_the_level_follows_the_model_the_request_names(method, client_model, override, expected):
    c, seen, _ = _client(client_model)
    await _drive(c, method, budget=None, model_name=override, thinking_level="low")
    assert seen[0]["model"] == override
    if method in _STREAMS:
        expected = {"include_thoughts": True, **expected}
    assert _config_wire(seen[0]) == expected


@pytest.mark.asyncio
async def test_generate_with_tools_sends_the_level_on_every_round_including_the_final_one():
    """The tool-less final round builds its OWN config; it must carry the level too."""
    c, _, _ = _client(_LITE)
    configs: List[Any] = []
    script = [_Resp([_FCPart("get_stock_chart_data")]),   # round 1: a tool call
              _Resp([_FCPart("get_ticker_news")]),        # follow-up: ANOTHER call
              _Resp([_Part("Apple is doing fine.")])]     # final: the answer, tools=None

    async def _fake(*, model, contents, config, what):
        configs.append((model, config))
        return script.pop(0)

    async def _handler(args):
        return {"ok": True}

    c._generate_content_retried = _fake  # type: ignore[assignment]
    out = await c.generate_with_tools(
        "p", tools=[], tool_handlers={"get_stock_chart_data": _handler, "get_ticker_news": _handler},
        thinking_budget=512, thinking_level="high", force_first_tool="get_stock_chart_data")
    assert out["text"] == "Apple is doing fine."
    assert len(configs) == 3 and configs[2][1].tools is None
    assert configs[0][1].tool_config is not None, "round 1 must be the forced copy"
    for model, cfg in configs:
        assert model == _LITE
        assert _wire(cfg.thinking_config) == {"thinking_level": "HIGH"}


@pytest.mark.asyncio
async def test_stream_agentic_sends_the_level_on_the_forced_and_the_final_round():
    """A per-message config REPLACES the chat's (google-genai), so the forced first round and the
    tool-less final round are full copies: each must still carry the level and no budget."""
    c, seen, sent = _client(_LITE, chat_rounds=[[_FCPart("t")], [_Part("done")]])

    async def _handler(args):
        return {"ok": True}

    out = [ev async for ev in c.stream_agentic("p", tools=[], tool_handlers={"t": _handler},
                                               max_rounds=1, thinking_budget=0, thinking_level="low",
                                               force_first_tool="t")]
    assert ("answer", "done") in out
    assert len(seen) == 1 and len(sent) == 2
    chat_cfg, (forced_cfg, final_cfg) = seen[0]["config"], sent
    assert forced_cfg is not None and forced_cfg.tool_config is not None
    assert final_cfg is not None and final_cfg.tools is None
    for cfg in (chat_cfg, forced_cfg, final_cfg):
        assert _wire(cfg.thinking_config) == {"include_thoughts": True, "thinking_level": "LOW"}


# ── the response cache ──────────────────────────────────────────────────────

def _old_key(*parts: str) -> str:
    """The cache key exactly as it was computed before levels (independent of gem._cache_key)."""
    return hashlib.sha256("|".join(str(p) for p in parts if p).encode()).hexdigest()[:32]


@pytest.mark.asyncio
async def test_generate_text_keys_a_level_apart_and_keeps_the_old_key_without_one():
    c, seen, _ = _client(_LITE)
    for level_kw in ({}, {"thinking_level": "low"}, {"thinking_level": None}, {"thinking_level": "low"}):
        await c.generate_text(prompt="same", system_instruction="sys", max_output_tokens=900,
                              thinking_budget=0, **level_kw)
    # Two upstream calls (no level, then "low"); the repeats were served from the cache.
    assert [_config_wire(kw) for kw in seen] == [{"thinking_level": "MINIMAL"}, {"thinking_level": "LOW"}]
    old = _old_key("same", "sys", "", "900", "tb=0")
    assert set(c._response_cache._store) == {old, _old_key("same", "sys", "", "900", "tb=0", "tl=low")}


@pytest.mark.asyncio
async def test_generate_text_with_no_budget_and_no_level_keeps_the_old_key():
    c, _, _ = _client(_TWO_FIVE)
    await c.generate_text(prompt="q", model_name=_LITE)
    assert set(c._response_cache._store) == {_old_key("q", "", _LITE, "", "")}


@pytest.mark.asyncio
async def test_generate_json_keys_a_level_apart_and_keeps_the_old_key_without_one():
    c, seen, _ = _client(_LITE)
    for level_kw in ({}, {"thinking_level": "low"}, {}, {"thinking_level": "low"}):
        await c.generate_json(prompt="same", model_name=_THREE_EIGHT, thinking_budget=0,
                              temperature=0.0, **level_kw)
    assert [_config_wire(kw) for kw in seen] == [{"thinking_budget": 0}, {"thinking_level": "LOW"}]
    old_parts = ["json", "same", "", _THREE_EIGHT, "", "tb=0"]
    assert set(c._response_cache._store) == {
        _old_key(*old_parts, "temp=0.0"),                  # byte-identical to the pre-level key
        _old_key(*old_parts, "tl=low", "temp=0.0"),
    }


@pytest.mark.asyncio
async def test_two_levels_on_the_same_prompt_are_never_served_to_each_other():
    c, seen, _ = _client(_LITE)
    for level in ("low", "high", "low", "high", "minimal"):
        await c.generate_json(prompt="same", thinking_level=level)
    assert [_config_wire(kw)["thinking_level"] for kw in seen] == ["LOW", "HIGH", "MINIMAL"]
    assert c._response_cache.size == 3
