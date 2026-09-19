"""
Unit tests for GeminiClient.stream_text — native async streaming with REAL thinking
(unified google-genai SDK).

No network: `client._client.aio.models.generate_content_stream` is replaced with a fake that
returns an async iterator of fake chunks. Each chunk mirrors the SDK shape
(`chunk.candidates[0].content.parts`), and each part carries `.text` + a `.thought` flag. The
tests pin the behaviors the SSE endpoint depends on:
  * thought parts yield ("thought", text); answer parts yield ("answer", text), in order,
  * text-less parts (whose .text raises) are skipped,
  * an error raised mid-stream propagates to the async consumer,
  * an open quota circuit fails fast before touching the SDK.
"""

import asyncio

import pytest

from app.config import settings
from app.integrations import gemini as gem


class _FakePart:
    def __init__(self, text, thought=False):
        self._t = text
        self.thought = thought

    @property
    def text(self):
        # Mirror the SDK: .text raises when the part carries no text.
        if self._t is None:
            raise ValueError("no text in this part")
        return self._t


class _FakeContent:
    def __init__(self, parts):
        self.parts = parts


class _FakeCandidate:
    def __init__(self, parts):
        self.content = _FakeContent(parts)


class _FakeChunk:
    """One streamed chunk = one candidate with a list of parts."""
    def __init__(self, *parts):
        self.candidates = [_FakeCandidate(list(parts))]


class _FakeAioModels:
    def __init__(self, chunks, raise_at=None):
        self._chunks = chunks
        self._raise_at = raise_at

    async def generate_content_stream(self, *, model, contents, config):
        chunks, raise_at = self._chunks, self._raise_at
        # Real thinking must be requested.
        assert config.thinking_config is not None, "stream_text must enable thinking"

        async def _gen():
            for i, c in enumerate(chunks):
                if raise_at is not None and i == raise_at:
                    raise RuntimeError("boom mid-stream")
                yield c

        return _gen()


class _FakeAio:
    def __init__(self, models):
        self.models = models


class _FakeClient:
    def __init__(self, models):
        self.aio = _FakeAio(models)


def _client(models) -> gem.GeminiClient:
    """Build a GeminiClient WITHOUT genai.Client (no API key needed)."""
    c = gem.GeminiClient.__new__(gem.GeminiClient)
    c.model_name = "gemini-2.5-flash"
    c._temperature = 0.7
    c._max_tokens = 128
    c._client = _FakeClient(models)
    gem._quota_circuit.record_success()  # deterministic: start with the circuit closed
    return c


@pytest.mark.asyncio
async def test_stream_text_separates_thoughts_from_answer():
    c = _client(_FakeAioModels([
        _FakeChunk(_FakePart("Let me check margins. ", thought=True)),
        _FakeChunk(_FakePart("Apple ", thought=False)),
        _FakeChunk(_FakePart("is solid.", thought=False)),
    ]))
    out = [pair async for pair in c.stream_text("prompt")]
    assert out == [
        ("thought", "Let me check margins. "),
        ("answer", "Apple "),
        ("answer", "is solid."),
    ]


@pytest.mark.asyncio
async def test_stream_text_multiple_parts_in_one_chunk():
    # A single chunk can carry a thought part AND an answer part.
    c = _client(_FakeAioModels([
        _FakeChunk(_FakePart("thinking...", thought=True), _FakePart("answer.", thought=False)),
    ]))
    out = [pair async for pair in c.stream_text("prompt")]
    assert out == [("thought", "thinking..."), ("answer", "answer.")]


@pytest.mark.asyncio
async def test_stream_text_skips_textless_parts():
    c = _client(_FakeAioModels([
        _FakeChunk(_FakePart("a")),
        _FakeChunk(_FakePart(None)),   # finish-only part → .text raises → skipped
        _FakeChunk(_FakePart("b")),
    ]))
    out = [pair async for pair in c.stream_text("prompt")]
    assert out == [("answer", "a"), ("answer", "b")]


@pytest.mark.asyncio
async def test_stream_text_propagates_midstream_error():
    c = _client(_FakeAioModels([_FakeChunk(_FakePart("a")), _FakeChunk(_FakePart("b"))], raise_at=1))
    got = []
    with pytest.raises(RuntimeError):
        async for pair in c.stream_text("prompt"):
            got.append(pair)
    assert got == [("answer", "a")]


@pytest.mark.asyncio
async def test_stream_text_fails_fast_when_circuit_open(monkeypatch):
    c = _client(_FakeAioModels([_FakeChunk(_FakePart("x"))]))
    monkeypatch.setattr(gem._quota_circuit, "is_open", lambda: True)
    with pytest.raises(gem.GeminiQuotaError):
        async for _ in c.stream_text("prompt"):
            pass


# ── F04-3: the half-open trial's verdict is the FIRST chunk ─────────────────
#
# A chat stream admitted as the trial used to hold the slot until its last
# chunk (and, if the user stopped it before then, for a whole extra cooldown):
# every other Gemini call in the process failed fast for that long on a quota
# that had already recovered.


def _half_open(monkeypatch, clock_start=1000.0):
    """Trip the breaker and advance past the cooldown; returns the fake clock."""
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", 2)
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS", 30.0)
    now = {"t": clock_start}
    monkeypatch.setattr(gem.time, "time", lambda: now["t"])
    gem._quota_circuit.reset()
    gem._quota_circuit.record_quota_error(); gem._quota_circuit.record_quota_error()
    now["t"] += 31.0
    return now


@pytest.mark.asyncio
async def test_stream_text_trial_closes_the_breaker_on_the_first_chunk(monkeypatch):
    c = _client(_FakeAioModels([_FakeChunk(_FakePart("a")), _FakeChunk(_FakePart("b"))]))
    _half_open(monkeypatch)
    cb = gem._quota_circuit

    gen = c.stream_text("prompt")
    assert await gen.__anext__() == ("answer", "a")
    assert cb.tripped is False and cb.half_open is False, (
        "the first chunk IS the verdict — the breaker must not wait for the last one"
    )
    assert cb.is_open() is False, "other callers are admitted while this stream is still going"
    await gen.aclose()


@pytest.mark.asyncio
async def test_stream_text_trial_dropped_before_the_first_chunk_releases_the_slot(monkeypatch):
    """The user stops the turn (GeneratorExit) before anything streamed: no verdict, so
    the slot goes straight to the next caller instead of wedging for a cooldown."""
    c = _client(_FakeAioModels([_FakeChunk(_FakePart("a"))]))
    _half_open(monkeypatch)
    cb = gem._quota_circuit

    gen = c.stream_text("prompt")
    # Enter the generator far enough to be admitted (the gate runs before the first await).
    monkeypatch.setattr(c._client.aio.models, "generate_content_stream", _never_yields)
    task = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)
    assert cb.half_open is True, "admitted as the trial"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await gen.aclose()
    assert cb.half_open is False, "released — no verdict was reached"
    assert cb.is_open() is False, "the next caller is the trial immediately"


@pytest.mark.asyncio
async def test_stream_text_trial_that_fails_on_quota_keeps_the_breaker_open(monkeypatch):
    class _Exhausted:
        async def generate_content_stream(self, *, model, contents, config):
            raise gem.GeminiQuotaError("429 RESOURCE_EXHAUSTED")
    c = _client(_Exhausted())
    now = _half_open(monkeypatch)
    cb = gem._quota_circuit

    with pytest.raises(gem.GeminiQuotaError):
        async for _ in c.stream_text("prompt"):
            pass
    assert cb.is_open() is True, "the failed trial re-opened it and the release did not undo that"
    now["t"] += 29.0
    assert cb.is_open() is True


@pytest.mark.asyncio
async def test_stream_text_trial_that_raises_a_non_quota_error_releases_the_slot(monkeypatch):
    class _Broken:
        async def generate_content_stream(self, *, model, contents, config):
            raise RuntimeError("connection reset")
    c = _client(_Broken())
    _half_open(monkeypatch)
    cb = gem._quota_circuit

    with pytest.raises(RuntimeError):
        async for _ in c.stream_text("prompt"):
            pass
    assert cb.half_open is False
    assert cb.is_open() is False and cb.half_open is True, "next caller becomes the trial"


async def _never_yields(*, model, contents, config):
    await asyncio.sleep(3600)
