"""
Unit tests for GeminiClient.stream_text — native async streaming (unified google-genai SDK).

No network: `client._client.aio.models.generate_content_stream` is replaced with a fake that
returns an async iterator of fake chunks. The tests pin the behaviors the SSE endpoint depends on:
  * text chunks are yielded in order,
  * text-less chunks (safety/finish-only, whose .text raises) are skipped,
  * an error raised mid-stream propagates to the async consumer,
  * an open quota circuit fails fast before touching the SDK.
"""

import pytest

from app.integrations import gemini as gem


class _FakeChunk:
    def __init__(self, text):
        self._t = text

    @property
    def text(self):
        # Mirror the SDK: .text raises when the chunk carries no text part.
        if self._t is None:
            raise ValueError("no text part in this chunk")
        return self._t


class _FakeAioModels:
    def __init__(self, chunks, raise_at=None):
        self._chunks = chunks
        self._raise_at = raise_at

    async def generate_content_stream(self, *, model, contents, config):
        chunks, raise_at = self._chunks, self._raise_at

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
async def test_stream_text_yields_chunks_in_order():
    c = _client(_FakeAioModels([_FakeChunk("Apple "), _FakeChunk("is "), _FakeChunk("solid.")]))
    out = [d async for d in c.stream_text("prompt")]
    assert out == ["Apple ", "is ", "solid."]


@pytest.mark.asyncio
async def test_stream_text_skips_textless_chunks():
    c = _client(_FakeAioModels([_FakeChunk("a"), _FakeChunk(None), _FakeChunk("b")]))
    out = [d async for d in c.stream_text("prompt")]
    assert out == ["a", "b"]


@pytest.mark.asyncio
async def test_stream_text_propagates_midstream_error():
    # Yields index 0 ("a"), then raises before index 1.
    c = _client(_FakeAioModels([_FakeChunk("a"), _FakeChunk("b")], raise_at=1))
    got = []
    with pytest.raises(RuntimeError):
        async for d in c.stream_text("prompt"):
            got.append(d)
    assert got == ["a"]


@pytest.mark.asyncio
async def test_stream_text_fails_fast_when_circuit_open(monkeypatch):
    c = _client(_FakeAioModels([_FakeChunk("x")]))
    monkeypatch.setattr(gem._quota_circuit, "is_open", lambda: True)
    with pytest.raises(gem.GeminiQuotaError):
        async for _ in c.stream_text("prompt"):
            pass
