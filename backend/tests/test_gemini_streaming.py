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

import pytest

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
