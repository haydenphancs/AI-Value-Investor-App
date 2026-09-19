"""Chat thinking ceiling (E1 of the 2026-09-16 TestFlight brief).

Gemini's `max_output_tokens` bounds THOUGHTS + ANSWER together. Chat thinking was
unbounded, so the prod turn behind the "answer cut off mid-sentence" report thought for
1150 of its 1200 tokens and streamed 40 (`GEMINI_USAGE call_site=stream_agentic …
output_tok=40 thoughts_tok=1150`, 2026-09-16 03:23 UTC). `CHAT_THINKING_BUDGET` bounds
the pass; this file pins that every chat generation call actually carries it and that
the encoder keeps `include_thoughts=True` (the thinking card) on the streaming pair.

Two halves, like `test_chat_output_cap.py`: the plumbing (the client forwards the
budget into the config) and the call sites (an AST walk — a substring scan would
match the parameter definition and pass vacuously).
"""

import ast
from pathlib import Path
from typing import Any, List

import pytest

from app.config import settings
from app.integrations import gemini as gem

BACKEND = Path(__file__).resolve().parents[1]
CHAT_CALLERS = (
    BACKEND / "app" / "services" / "chat_service.py",
    BACKEND / "app" / "api" / "v1" / "endpoints" / "chat.py",
)
ANSWER_METHODS = {"stream_text", "stream_agentic", "generate_with_tools"}


# ── the encoder ──────────────────────────────────────────────────────────────

def test_stream_encoder_keeps_thoughts_on_and_forwards_the_ceiling():
    """The streaming pair renders thought summaries as the thinking card; `_thinking_config`
    (no `include_thoughts`) would blank it, hence a dedicated encoder."""
    none = gem._stream_thinking_config(None)
    assert none.include_thoughts is True and none.thinking_budget is None
    off = gem._stream_thinking_config(0)
    assert off.include_thoughts is True and off.thinking_budget == 0
    capped = gem._stream_thinking_config(1024)
    assert capped.include_thoughts is True and capped.thinking_budget == 1024


def test_the_report_encoder_is_untouched():
    """`test_report_thinking_budget.py` pins `_thinking_config`; the chat encoder is a
    sibling, not a change to it."""
    assert gem._thinking_config(None) is None
    assert gem._thinking_config(0).thinking_budget == 0


# ── the resolver ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [(-1, None), (-500, None), (0, 0), (1024, 1024), (7, 7)])
def test_chat_thinking_budget_resolver(monkeypatch, value, expected):
    from app.services.chat_service import _chat_thinking_budget
    monkeypatch.setattr(settings, "CHAT_THINKING_BUDGET", value)
    assert _chat_thinking_budget() == expected


def test_shipped_budget_leaves_the_answer_at_least_half_the_ceiling():
    assert 0 < settings.CHAT_THINKING_BUDGET <= settings.CHAT_MAX_OUTPUT_TOKENS // 2


# ── plumbing: the client forwards the budget ─────────────────────────────────

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
        self.candidates = [
            type("C", (), {"content": type("Ct", (), {"parts": [_Part(text)]})(),
                           "finish_reason": None})()
        ]


class _Models:
    def __init__(self):
        self.config = None

    async def generate_content_stream(self, *, model, contents, config):
        self.config = config

        async def _gen():
            yield _Chunk("hi")

        return _gen()


class _Chat:
    async def send_message_stream(self, message):
        async def _gen():
            yield _Chunk("hi")

        return _gen()


def _client(models=None, chats_holder=None):
    c = gem.GeminiClient.__new__(gem.GeminiClient)
    c.model_name = "gemini-2.5-flash"
    c._temperature, c._max_tokens = 0.7, settings.GEMINI_MAX_TOKENS
    c._client = type("Cl", (), {"aio": type("Aio", (), {
        "models": models, "chats": chats_holder,
    })()})()
    gem._quota_circuit.record_success()
    return c


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [None, 0, 1024])
async def test_stream_text_forwards_the_budget_with_thoughts_on(budget):
    models = _Models()
    c = _client(models=models)
    async for _ in c.stream_text("p", max_output_tokens=777, thinking_budget=budget):
        pass
    tc = models.config.thinking_config
    assert tc.include_thoughts is True
    assert tc.thinking_budget == budget


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [None, 0, 1024])
async def test_stream_agentic_forwards_the_budget_with_thoughts_on(budget):
    captured = {}

    class _Chats:
        @staticmethod
        def create(**kw):
            captured["config"] = kw.get("config")
            return _Chat()

    c = _client(chats_holder=_Chats())
    async for _ in c.stream_agentic("p", tools=[], tool_handlers={}, max_output_tokens=555,
                                    thinking_budget=budget):
        pass
    tc = captured["config"].thinking_config
    assert tc.include_thoughts is True
    assert tc.thinking_budget == budget


class _FC:
    def __init__(self, name, args=None):
        self.name, self.args = name, args or {}


class _FCPart(_Part):
    def __init__(self, name):
        super().__init__("")
        self.function_call = _FC(name)


class _Resp:
    def __init__(self, parts, finish="STOP"):
        self.candidates = [
            type("C", (), {"content": type("Ct", (), {"parts": parts})(),
                           "finish_reason": finish})()
        ]
        self.usage_metadata = None

    @property
    def text(self):
        texts = [p.text for p in self.candidates[0].content.parts if p.text]
        if not texts:
            raise ValueError("no text part")
        return "".join(texts)


@pytest.mark.asyncio
async def test_generate_with_tools_forwards_the_budget_into_both_configs():
    """The first config (tools declared) AND the tool-less final config — the round that
    answers after a second function call — both carry the ceiling; a budget on one and
    not the other would let the final answer round think the answer's share away."""
    c = _client()
    configs: List[Any] = []
    responses = [
        _Resp([_FCPart("get_stock_chart_data")]),                 # round 1: a tool call
        _Resp([_FCPart("get_ticker_news")]),                      # follow-up: ANOTHER call
        _Resp([_Part("Apple is doing fine.")]),                   # final: the answer
    ]

    async def _fake(*, model, contents, config, what):
        configs.append(config)
        return responses.pop(0)

    c._generate_content_retried = _fake  # type: ignore[assignment]

    async def _handler(args):
        return {"widget_type": "stock_chart", "ticker": "AAPL"}

    out = await c.generate_with_tools(
        "p", tools=[], tool_handlers={"get_stock_chart_data": _handler},
        max_output_tokens=999, thinking_budget=256,
    )
    assert out["text"] == "Apple is doing fine."
    assert out["finish_reason"] == "STOP"
    assert len(configs) == 3
    for cfg in (configs[0], configs[2]):
        assert cfg.thinking_config is not None and cfg.thinking_config.thinking_budget == 256
        assert cfg.max_output_tokens == 999


@pytest.mark.asyncio
async def test_generate_with_tools_without_a_budget_attaches_no_thinking_config():
    """`None` keeps the request byte-identical to before — the rollback value."""
    c = _client()
    configs: List[Any] = []

    async def _fake(*, model, contents, config, what):
        configs.append(config)
        return _Resp([_Part("fine")])

    c._generate_content_retried = _fake  # type: ignore[assignment]
    await c.generate_with_tools("p", tools=[], tool_handlers={}, max_output_tokens=999)
    assert configs[0].thinking_config is None


@pytest.mark.asyncio
async def test_a_stream_that_raises_before_the_first_chunk_still_logs_usage(caplog):
    """`finish` is read in the `finally` logger; it used to be bound INSIDE the try, so a
    request that raised before the first chunk would have NameError'd in the logger."""
    class _Boom:
        async def generate_content_stream(self, *, model, contents, config):
            raise RuntimeError("503 overloaded")

    c = _client(models=_Boom())
    import logging
    with caplog.at_level(logging.INFO, logger="app.integrations.gemini"):
        with pytest.raises(RuntimeError):
            async for _ in c.stream_text("p", max_output_tokens=10, thinking_budget=5):
                pass
    lines = [r.getMessage() for r in caplog.records if "GEMINI_USAGE" in r.getMessage()]
    assert lines and "call_site=stream_text" in lines[-1] and "finish=-" in lines[-1]


@pytest.mark.asyncio
async def test_the_usage_line_carries_the_finish_reason(caplog):
    """The prod diagnosis paired `thoughts_tok` with a finish reason that had to be
    inferred; the line now says it outright."""
    class _CutChunk(_Chunk):
        def __init__(self, text):
            super().__init__(text)
            self.candidates[0].finish_reason = type("FR", (), {"name": "MAX_TOKENS"})()

    class _CutModels(_Models):
        async def generate_content_stream(self, *, model, contents, config):
            async def _gen():
                yield _CutChunk("half an answer")
            return _gen()

    import logging
    c = _client(models=_CutModels())
    with caplog.at_level(logging.INFO, logger="app.integrations.gemini"):
        events = [ev async for ev in c.stream_text("p", max_output_tokens=10)]
    assert ("finish", "MAX_TOKENS") in events
    usage = [r.getMessage() for r in caplog.records if "GEMINI_USAGE" in r.getMessage()]
    assert usage and "finish=MAX_TOKENS" in usage[-1]


# ── call sites: every chat answer call passes the budget ─────────────────────

def _calls(path: Path, methods):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in methods:
                yield node


def test_the_scan_finds_the_call_sites_it_claims_to_guard():
    total = sum(len(list(_calls(p, ANSWER_METHODS))) for p in CHAT_CALLERS)
    assert total >= 5, f"expected the known chat answer call sites, found {total}"


@pytest.mark.parametrize("path", CHAT_CALLERS, ids=lambda p: p.name)
def test_every_chat_answer_call_passes_the_thinking_budget(path):
    missing = [
        f"{path.name}:{node.lineno} {node.func.attr}"
        for node in _calls(path, ANSWER_METHODS)
        if not any(kw.arg == "thinking_budget" for kw in node.keywords)
    ]
    assert not missing, "chat answer call(s) missing thinking_budget=: " + ", ".join(missing)


def test_the_non_stream_plain_text_fallback_passes_the_thinking_budget():
    """`generate_text` is also used for query rewriting and history condensing on the cheap
    model (where a budget would TURN THINKING ON), so only the ANSWER fallback inside
    `generate_response` is required to carry it."""
    tree = ast.parse(CHAT_CALLERS[0].read_text())
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "generate_response"
    )
    calls = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "generate_text"
    ]
    assert calls, "generate_response's plain-text fallback call not found"
    for call in calls:
        assert any(kw.arg == "thinking_budget" for kw in call.keywords), (
            f"generate_response's generate_text at line {call.lineno} lacks thinking_budget="
        )
