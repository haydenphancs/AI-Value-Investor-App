"""
Unit tests for GeminiClient.stream_text — the sync-SDK → async-generator bridge
that powers SSE chat streaming.

No network: genai.GenerativeModel is monkeypatched with a fake that returns a
sync iterator of fake chunks (mirroring `generate_content(stream=True)`). The
tests pin the behaviors the SSE endpoint depends on:
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


class _FakeModel:
    def __init__(self, chunks, raise_at=None):
        self._chunks = chunks
        self._raise_at = raise_at

    def generate_content(self, prompt, stream=False):
        assert stream is True, "stream_text must request stream=True"

        def _iter():
            for i, c in enumerate(self._chunks):
                if self._raise_at is not None and i == self._raise_at:
                    raise RuntimeError("boom mid-stream")
                yield c

        return _iter()


def _client() -> gem.GeminiClient:
    """Build a GeminiClient WITHOUT genai.configure (no API key needed)."""
    client = gem.GeminiClient.__new__(gem.GeminiClient)
    client.model_name = "gemini-2.5-flash"
    client.generation_config = {"temperature": 0.7, "max_output_tokens": 128}
    return client


def _install_model(monkeypatch, model):
    monkeypatch.setattr(gem.genai, "GenerativeModel", lambda *a, **k: model)
    # Ensure the shared circuit starts closed for a deterministic test.
    gem._quota_circuit.record_success()


@pytest.mark.asyncio
async def test_stream_text_yields_chunks_in_order(monkeypatch):
    _install_model(monkeypatch, _FakeModel([_FakeChunk("Apple "), _FakeChunk("is "), _FakeChunk("solid.")]))
    out = [d async for d in _client().stream_text("prompt")]
    assert out == ["Apple ", "is ", "solid."]


@pytest.mark.asyncio
async def test_stream_text_skips_textless_chunks(monkeypatch):
    _install_model(monkeypatch, _FakeModel([_FakeChunk("a"), _FakeChunk(None), _FakeChunk("b")]))
    out = [d async for d in _client().stream_text("prompt")]
    assert out == ["a", "b"]


@pytest.mark.asyncio
async def test_stream_text_propagates_midstream_error(monkeypatch):
    # Yields index 0 ("a"), then raises before index 1.
    _install_model(monkeypatch, _FakeModel([_FakeChunk("a"), _FakeChunk("b")], raise_at=1))
    got = []
    with pytest.raises(RuntimeError):
        async for d in _client().stream_text("prompt"):
            got.append(d)
    assert got == ["a"]


@pytest.mark.asyncio
async def test_stream_text_fails_fast_when_circuit_open(monkeypatch):
    monkeypatch.setattr(gem._quota_circuit, "is_open", lambda: True)
    with pytest.raises(gem.GeminiQuotaError):
        async for _ in _client().stream_text("prompt"):
            pass
