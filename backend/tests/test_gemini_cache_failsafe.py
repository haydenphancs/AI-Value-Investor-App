"""
Fail-safe tests for the Gemini context-cache methods in
``app.integrations.gemini.GeminiClient``:

    * create_narrative_cache
    * generate_text_cached
    * delete_cache

These methods exist to make the N parallel Stage-B narrative calls share one
CachedContent prefix (≈1x write + N×25% reads instead of N×100%). The ironclad
invariant is that caching must NEVER degrade or hang a report: any
missing-SDK / below-min-token / quota / hung-SDK condition has to degrade to
the inline path (create_* returns None) rather than raising or parking the
agent run.

We use a REAL ``GeminiClient`` instance and monkeypatch only the SDK boundary
(``google.generativeai.caching.CachedContent.create`` and
``genai.GenerativeModel.from_cached_content``) so NO network is ever touched.

What's covered:
  1. Empty evidence → None, with ZERO SDK calls.
  2. CachedContent.create raises → create_narrative_cache returns None (never
     raises) so the caller falls back to inline.
  3. REGRESSION (timeout guard): a HUNG create that sleeps longer than
     GEMINI_REQUEST_TIMEOUT_SECONDS must NOT hang — create_narrative_cache
     returns None within the (tiny, monkeypatched) timeout. delete_cache must
     likewise swallow a hung/raising delete and a None handle.
  4. The model name passed to CachedContent.create is normalized to a
     "models/" prefix.

Run with:
    cd backend && ./venv/bin/python -m pytest tests/test_gemini_cache_failsafe.py -q
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.integrations import gemini
from app.integrations.gemini import GeminiClient


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_client() -> GeminiClient:
    """Build a real GeminiClient without touching the network.

    ``GeminiClient.__init__`` calls ``genai.configure(api_key=...)`` which only
    stashes the key (no I/O) and sets a few config dicts. That's safe to run as
    is, but we keep the constructed object minimal and pin a deterministic
    model name so the "models/" normalization assertion is unambiguous.
    """
    client = GeminiClient()
    client.model_name = "gemini-2.5-flash"  # no "models/" prefix → must be added
    return client


def _reset_circuit() -> None:
    """The quota circuit breaker is a module-level singleton keyed on
    time.time(); reset it so cross-test state never leaks."""
    gemini._quota_circuit._consecutive = 0
    gemini._quota_circuit._opened_at = 0.0


# ── 1. Empty evidence short-circuits with no SDK call ───────────────────────


@pytest.mark.asyncio
async def test_create_narrative_cache_empty_evidence_returns_none_no_sdk_call(
    monkeypatch,
):
    _reset_circuit()
    client = _make_client()

    # If the SDK boundary is touched at all for empty evidence, fail loudly.
    create_spy = MagicMock(side_effect=AssertionError("CachedContent.create must NOT be called for empty evidence"))
    from google.generativeai import caching

    monkeypatch.setattr(caching.CachedContent, "create", staticmethod(create_spy))

    # Both falsy forms of "no evidence".
    assert await client.create_narrative_cache("sys prompt", "") is None
    assert await client.create_narrative_cache("sys prompt", None) is None  # type: ignore[arg-type]

    create_spy.assert_not_called()


# ── 2. create raises → None (never propagates) ──────────────────────────────


@pytest.mark.asyncio
async def test_create_narrative_cache_swallows_create_error_returns_none(
    monkeypatch,
):
    """Simulate the below-min-token / generic SDK error path: the SDK raises,
    and the caller must transparently fall back to inline (None, no raise)."""
    _reset_circuit()
    client = _make_client()

    from google.generativeai import caching

    def _boom(*args, **kwargs):
        # Mirrors a real below-min-size / SDK rejection.
        raise ValueError("Cached content is too small (min 1024 tokens)")

    monkeypatch.setattr(caching.CachedContent, "create", staticmethod(_boom))
    # from_cached_content should never be reached, but stub it so an accidental
    # call doesn't hit the network.
    monkeypatch.setattr(
        gemini.genai.GenerativeModel,
        "from_cached_content",
        staticmethod(lambda **kw: MagicMock(name="model")),
    )

    handle = await client.create_narrative_cache("sys prompt", "lots of evidence here")
    assert handle is None  # degraded to inline, no exception bubbled up


# ── 3. REGRESSION: timeout guard — hung create / delete must not hang ────────


@pytest.mark.asyncio
async def test_create_narrative_cache_times_out_on_hung_create(monkeypatch):
    """A hung SDK create (sleeps longer than GEMINI_REQUEST_TIMEOUT_SECONDS)
    must NOT park the agent run. With the timeout monkeypatched to a tiny
    value, create_narrative_cache returns None well within a sane bound.

    This pins the timeout-guard fix: create_narrative_cache routes through
    _call_with_timeout, so asyncio.wait_for fires a TimeoutError that the
    method swallows → None → inline path.
    """
    _reset_circuit()
    client = _make_client()

    # Tiny timeout so the test is fast and deterministic.
    monkeypatch.setattr(settings, "GEMINI_REQUEST_TIMEOUT_SECONDS", 0.05)

    from google.generativeai import caching

    def _hung_create(*args, **kwargs):
        # _call_with_timeout runs this in a worker thread via asyncio.to_thread;
        # a blocking sleep here models a hung network read. The wait_for on the
        # event loop side trips first, so this thread's overrun is harmless.
        import time as _t

        _t.sleep(1.0)  # >> the 0.05s timeout
        return MagicMock(name="cache")

    monkeypatch.setattr(caching.CachedContent, "create", staticmethod(_hung_create))
    monkeypatch.setattr(
        gemini.genai.GenerativeModel,
        "from_cached_content",
        staticmethod(lambda **kw: MagicMock(name="model")),
    )

    # The whole call must resolve to None far faster than the hung 1.0s sleep.
    handle = await asyncio.wait_for(
        client.create_narrative_cache("sys prompt", "lots of evidence here"),
        timeout=0.8,  # generous vs. the 0.05s internal timeout, well under 1.0s
    )
    assert handle is None  # timed out internally → inline fallback, no hang


@pytest.mark.asyncio
async def test_delete_cache_none_handle_is_noop(monkeypatch):
    """delete_cache(None) must return without touching the SDK or raising."""
    _reset_circuit()
    client = _make_client()
    # No SDK stubs needed: with a None handle the method short-circuits.
    assert await client.delete_cache(None) is None


@pytest.mark.asyncio
async def test_delete_cache_swallows_hung_delete(monkeypatch):
    """A hung cache.delete() must be swallowed (cache simply expires via TTL),
    never raised, and never parks the caller."""
    _reset_circuit()
    client = _make_client()

    monkeypatch.setattr(settings, "GEMINI_REQUEST_TIMEOUT_SECONDS", 0.05)

    def _hung_delete():
        import time as _t

        _t.sleep(1.0)  # >> the 0.05s timeout

    fake_cache = MagicMock()
    fake_cache.delete = _hung_delete
    handle = {"cache": fake_cache, "model": MagicMock()}

    # Must resolve (to None) well before the 1.0s hung sleep.
    result = await asyncio.wait_for(client.delete_cache(handle), timeout=0.8)
    assert result is None  # timeout-guarded, swallowed, no raise


@pytest.mark.asyncio
async def test_delete_cache_swallows_raising_delete(monkeypatch):
    """A delete that raises synchronously is swallowed too (best-effort)."""
    _reset_circuit()
    client = _make_client()

    fake_cache = MagicMock()
    fake_cache.delete = MagicMock(side_effect=RuntimeError("delete blew up"))
    handle = {"cache": fake_cache, "model": MagicMock()}

    assert await client.delete_cache(handle) is None  # no exception propagates


# ── 4. Model name normalized to "models/" prefix ────────────────────────────


@pytest.mark.asyncio
async def test_create_narrative_cache_normalizes_model_prefix(monkeypatch):
    """When self.model_name lacks the "models/" prefix, the value passed to
    CachedContent.create(model=...) must be prefixed."""
    _reset_circuit()
    client = _make_client()
    client.model_name = "gemini-2.5-flash"  # explicitly bare

    from google.generativeai import caching

    captured: dict = {}

    def _capture_create(*args, **kwargs):
        captured["model"] = kwargs.get("model")
        captured["system_instruction"] = kwargs.get("system_instruction")
        captured["contents"] = kwargs.get("contents")
        return MagicMock(name="cache")

    monkeypatch.setattr(caching.CachedContent, "create", staticmethod(_capture_create))
    monkeypatch.setattr(
        gemini.genai.GenerativeModel,
        "from_cached_content",
        staticmethod(lambda **kw: MagicMock(name="model")),
    )

    handle = await client.create_narrative_cache("sys prompt", "lots of evidence here")

    # Success path returns the opaque handle dict.
    assert handle is not None
    assert set(handle.keys()) == {"cache", "model"}

    # The load-bearing assertion: model name carries the "models/" prefix.
    assert captured["model"] is not None
    assert captured["model"].startswith("models/")
    assert captured["model"] == "models/gemini-2.5-flash"
    # Sanity: the evidence is embedded under the documented label.
    assert any("FINANCIAL EVIDENCE:" in c for c in captured["contents"])


@pytest.mark.asyncio
async def test_create_narrative_cache_preserves_existing_models_prefix(monkeypatch):
    """When self.model_name ALREADY has the "models/" prefix, it must not be
    double-prefixed."""
    _reset_circuit()
    client = _make_client()
    client.model_name = "models/gemini-2.5-flash"  # already prefixed

    from google.generativeai import caching

    captured: dict = {}

    def _capture_create(*args, **kwargs):
        captured["model"] = kwargs.get("model")
        return MagicMock(name="cache")

    monkeypatch.setattr(caching.CachedContent, "create", staticmethod(_capture_create))
    monkeypatch.setattr(
        gemini.genai.GenerativeModel,
        "from_cached_content",
        staticmethod(lambda **kw: MagicMock(name="model")),
    )

    handle = await client.create_narrative_cache("sys prompt", "lots of evidence here")
    assert handle is not None
    assert captured["model"] == "models/gemini-2.5-flash"  # not "models/models/..."
