"""
Fail-safe tests for the Gemini context-cache methods in
``app.integrations.gemini.GeminiClient`` (unified google-genai SDK):

    * create_narrative_cache
    * generate_text_cached
    * delete_cache

These methods exist to make the N parallel Stage-B narrative calls share one
CachedContent prefix (≈1x write + N×25% reads instead of N×100%). The ironclad
invariant is that caching must NEVER degrade or hang a report: any
below-min-token / quota / hung-SDK condition has to degrade to the inline path
(create_* returns None) rather than raising or parking the agent run.

We use a REAL ``GeminiClient`` instance and monkeypatch only the SDK boundary
(``client._client.aio.caches.create`` / ``.delete``) so NO network is touched.

What's covered:
  1. Empty evidence → None, with ZERO SDK calls.
  2. create raises → create_narrative_cache returns None (never raises).
  3. REGRESSION (timeout guard): a HUNG create (sleeps > GEMINI_REQUEST_TIMEOUT_SECONDS)
     must NOT hang — returns None within the tiny monkeypatched timeout. delete_cache
     likewise swallows a hung/raising delete and a None handle.
  4. The model name passed to caches.create is normalized to a "models/" prefix, and
     the handle is the opaque ``{"cache"}`` dict (no cache-bound model in the new SDK).

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
    """Build a real GeminiClient. ``genai.Client(api_key=...)`` only stashes config
    (no network); we pin a deterministic bare model name for the prefix assertion."""
    client = GeminiClient()
    client.model_name = "gemini-2.5-flash"  # no "models/" prefix → must be added
    return client


def _reset_circuit() -> None:
    gemini._quota_circuit._consecutive = 0
    gemini._quota_circuit._opened_at = 0.0


def _patch_caches_create(monkeypatch, client, fn):
    monkeypatch.setattr(client._client.aio.caches, "create", fn)


def _patch_caches_delete(monkeypatch, client, fn):
    monkeypatch.setattr(client._client.aio.caches, "delete", fn)


# ── 1. Empty evidence short-circuits with no SDK call ───────────────────────


@pytest.mark.asyncio
async def test_create_narrative_cache_empty_evidence_returns_none_no_sdk_call(monkeypatch):
    _reset_circuit()
    client = _make_client()

    called = {"n": 0}

    async def _spy(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("caches.create must NOT be called for empty evidence")

    _patch_caches_create(monkeypatch, client, _spy)

    assert await client.create_narrative_cache("sys prompt", "") is None
    assert await client.create_narrative_cache("sys prompt", None) is None  # type: ignore[arg-type]
    assert called["n"] == 0


# ── 2. create raises → None (never propagates) ──────────────────────────────


@pytest.mark.asyncio
async def test_create_narrative_cache_swallows_create_error_returns_none(monkeypatch):
    _reset_circuit()
    client = _make_client()

    async def _boom(*args, **kwargs):
        raise ValueError("Cached content is too small (min 1024 tokens)")

    _patch_caches_create(monkeypatch, client, _boom)

    handle = await client.create_narrative_cache("sys prompt", "lots of evidence here")
    assert handle is None  # degraded to inline, no exception bubbled up


# ── 3. REGRESSION: timeout guard — hung create / delete must not hang ────────


@pytest.mark.asyncio
async def test_create_narrative_cache_times_out_on_hung_create(monkeypatch):
    _reset_circuit()
    client = _make_client()
    monkeypatch.setattr(settings, "GEMINI_REQUEST_TIMEOUT_SECONDS", 0.05)

    async def _hung_create(*args, **kwargs):
        await asyncio.sleep(1.0)  # >> the 0.05s timeout
        return MagicMock(name="cache")

    _patch_caches_create(monkeypatch, client, _hung_create)

    handle = await asyncio.wait_for(
        client.create_narrative_cache("sys prompt", "lots of evidence here"),
        timeout=0.8,  # generous vs. the 0.05s internal timeout, well under 1.0s
    )
    assert handle is None  # timed out internally → inline fallback, no hang


@pytest.mark.asyncio
async def test_delete_cache_none_handle_is_noop():
    _reset_circuit()
    client = _make_client()
    assert await client.delete_cache(None) is None


@pytest.mark.asyncio
async def test_delete_cache_swallows_hung_delete(monkeypatch):
    _reset_circuit()
    client = _make_client()
    monkeypatch.setattr(settings, "GEMINI_REQUEST_TIMEOUT_SECONDS", 0.05)

    async def _hung_delete(*args, **kwargs):
        await asyncio.sleep(1.0)  # >> the 0.05s timeout

    _patch_caches_delete(monkeypatch, client, _hung_delete)
    fake_cache = MagicMock()
    fake_cache.name = "cachedContents/abc"
    handle = {"cache": fake_cache}

    result = await asyncio.wait_for(client.delete_cache(handle), timeout=0.8)
    assert result is None  # timeout-guarded, swallowed, no raise


@pytest.mark.asyncio
async def test_delete_cache_swallows_raising_delete(monkeypatch):
    _reset_circuit()
    client = _make_client()

    async def _raise_delete(*args, **kwargs):
        raise RuntimeError("delete blew up")

    _patch_caches_delete(monkeypatch, client, _raise_delete)
    fake_cache = MagicMock()
    fake_cache.name = "cachedContents/abc"
    handle = {"cache": fake_cache}

    assert await client.delete_cache(handle) is None  # no exception propagates


# ── 4. Model name normalized to "models/" prefix; handle is {"cache"} ────────


@pytest.mark.asyncio
async def test_create_narrative_cache_normalizes_model_prefix(monkeypatch):
    _reset_circuit()
    client = _make_client()
    client.model_name = "gemini-2.5-flash"  # explicitly bare

    captured: dict = {}

    async def _capture_create(*, model, config):
        captured["model"] = model
        captured["system_instruction"] = config.system_instruction
        captured["contents"] = config.contents
        return MagicMock(name="cache")

    _patch_caches_create(monkeypatch, client, _capture_create)

    handle = await client.create_narrative_cache("sys prompt", "lots of evidence here")

    # Success path returns the opaque handle dict (no cache-bound model in the new SDK).
    assert handle is not None
    assert set(handle.keys()) == {"cache"}

    # The load-bearing assertion: model name carries the "models/" prefix.
    assert captured["model"] == "models/gemini-2.5-flash"
    # Sanity: the evidence is embedded under the documented label.
    assert any("FINANCIAL EVIDENCE:" in c for c in captured["contents"])


@pytest.mark.asyncio
async def test_create_narrative_cache_preserves_existing_models_prefix(monkeypatch):
    _reset_circuit()
    client = _make_client()
    client.model_name = "models/gemini-2.5-flash"  # already prefixed

    captured: dict = {}

    async def _capture_create(*, model, config):
        captured["model"] = model
        return MagicMock(name="cache")

    _patch_caches_create(monkeypatch, client, _capture_create)

    handle = await client.create_narrative_cache("sys prompt", "lots of evidence here")
    assert handle is not None
    assert captured["model"] == "models/gemini-2.5-flash"  # not "models/models/..."
