"""Chat output-token ceiling (Phase 1d).

Output is the majority of a chat turn's cost, and the chat system prompt asks for
"SHORT, direct… AT MOST 2-3 brief supporting bullet points" — real answers land
around 150 tokens. Chat inherited `GEMINI_MAX_TOKENS = 8192`, a ceiling that can
only ever bind when something has gone wrong. `CHAT_MAX_OUTPUT_TOKENS` bounds the
blast radius WITHOUT touching report generation, which legitimately needs 8192.

Two halves, and both are needed:
  * the plumbing — the stream methods forward the cap into GenerateContentConfig;
  * the call sites — every chat entry point actually passes it. That half is an AST
    walk rather than a substring scan: a regex over the file would also match the
    parameter's own definition and a comment, and would pass vacuously.
"""

import ast
from pathlib import Path

import pytest

from app.config import settings
from app.integrations import gemini as gem

BACKEND = Path(__file__).resolve().parents[1]
CHAT_STREAM_CALLERS = (
    BACKEND / "app" / "services" / "chat_service.py",
    BACKEND / "app" / "api" / "v1" / "endpoints" / "chat.py",
)
# Every generation method a CHAT turn can reach — not just the streaming pair. The
# original set was `{"stream_text", "stream_agentic"}` while the docstring above claimed
# to check "every chat entry point", so the non-streaming /chat/send path
# (`generate_with_tools`, falling back to `generate_text`) sat outside the scan and
# inherited GEMINI_MAX_TOKENS. The file was green with the hole open.
STREAM_METHODS = {"stream_text", "stream_agentic", "generate_with_tools", "generate_text"}


# ── the cap is actually a cap ───────────────────────────────────────────────

def test_chat_cap_is_below_the_global_ceiling():
    """A 'cap' at or above GEMINI_MAX_TOKENS would be decorative."""
    assert 0 < settings.CHAT_MAX_OUTPUT_TOKENS < settings.GEMINI_MAX_TOKENS


def test_chat_cap_leaves_room_for_a_real_answer():
    """Measured answers are ~150 tokens; the cap must bound runaways, not truncate
    ordinary replies."""
    assert settings.CHAT_MAX_OUTPUT_TOKENS >= 600


# ── plumbing: the stream methods forward the cap ────────────────────────────

class _Part:
    thought = False

    def __init__(self, text):
        self._t = text

    @property
    def text(self):
        return self._t


class _Chunk:
    def __init__(self, text):
        self.candidates = [
            type("C", (), {"content": type("Ct", (), {"parts": [_Part(text)]})()})()
        ]


class _Models:
    """Captures the GenerateContentConfig the client was handed."""

    def __init__(self):
        self.config = None

    async def generate_content_stream(self, *, model, contents, config):
        self.config = config

        async def _gen():
            yield _Chunk("hi")

        return _gen()


class _Chat:
    def __init__(self):
        self.config = None

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
async def test_stream_text_forwards_the_cap():
    models = _Models()
    c = _client(models=models)
    async for _ in c.stream_text("p", max_output_tokens=777):
        pass
    assert models.config.max_output_tokens == 777


@pytest.mark.asyncio
async def test_stream_text_without_a_cap_keeps_the_global_default():
    """Non-chat callers must be unaffected — report generation needs 8192."""
    models = _Models()
    c = _client(models=models)
    async for _ in c.stream_text("p"):
        pass
    assert models.config.max_output_tokens == settings.GEMINI_MAX_TOKENS


@pytest.mark.asyncio
async def test_stream_agentic_forwards_the_cap():
    captured = {}

    class _Chats:
        @staticmethod
        def create(**kw):
            captured["config"] = kw.get("config")
            return _Chat()

    c = _client(chats_holder=_Chats())
    async for _ in c.stream_agentic("p", tools=[], tool_handlers={}, max_output_tokens=555):
        pass
    assert captured["config"].max_output_tokens == 555


@pytest.mark.asyncio
async def test_stream_agentic_without_a_cap_keeps_the_global_default():
    captured = {}

    class _Chats:
        @staticmethod
        def create(**kw):
            captured["config"] = kw.get("config")
            return _Chat()

    c = _client(chats_holder=_Chats())
    async for _ in c.stream_agentic("p", tools=[], tool_handlers={}):
        pass
    assert captured["config"].max_output_tokens == settings.GEMINI_MAX_TOKENS


# ── call sites: every chat stream passes the cap ────────────────────────────

def _stream_calls(path: Path):
    """Every Call node invoking stream_text / stream_agentic, via AST.

    AST, not a substring scan: a regex for 'max_output_tokens' in the file would
    also match the parameter definition and this module's own prose, and would keep
    passing after a new call site forgot it.
    """
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in STREAM_METHODS:
                yield node


def test_the_scan_finds_the_call_sites_it_claims_to_guard():
    """Guard against the guard: if a refactor renames the methods this scan would
    silently inspect nothing and pass."""
    total = sum(len(list(_stream_calls(p))) for p in CHAT_STREAM_CALLERS)
    assert total >= 4, f"expected the known chat stream call sites, found {total}"


@pytest.mark.parametrize("path", CHAT_STREAM_CALLERS, ids=lambda p: p.name)
def test_every_chat_stream_call_passes_the_output_cap(path):
    missing = [
        f"{path.name}:{node.lineno} {node.func.attr}"
        for node in _stream_calls(path)
        if not any(kw.arg == "max_output_tokens" for kw in node.keywords)
    ]
    assert not missing, (
        "chat stream call(s) missing max_output_tokens=settings.CHAT_MAX_OUTPUT_TOKENS: "
        + ", ".join(missing)
    )
