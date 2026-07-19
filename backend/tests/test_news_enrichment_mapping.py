"""
Regression tests for the News-tab enrichment defects surfaced by the adversarial
review of news_cache_service.py:

  * Misattribution: enrichment results were keyed by Gemini's self-reported `index`
    (default 0). Duplicate/missing/1-based indices bound one article's bullets +
    sentiment to a DIFFERENT article. Fixed by POSITIONAL mapping (_map_enrichments),
    ignoring the model index and rejecting shape mismatches.
  * Cache poisoning: on a Gemini failure, _batch_enrich_articles returned a NON-EMPTY
    per-article "neutral" fallback dict, so the caller persisted ai_processed=True with
    empty bullets + forced-neutral sentiment into the shared 6h cache (never retried).
    Fixed by returning {} on any failure/mismatch so the caller degrades to
    unenriched-and-retryable.

Pure/async unit tests; no network, no Supabase (gemini is a local fake).
"""

from __future__ import annotations

import json

import pytest

from app.services.news_cache_service import NewsCacheService


# ── _map_enrichments: positional, index-agnostic, shape-guarded ──────────────

def test_map_enrichments_uses_position_not_model_index():
    # Every item lies about its index (all 0). Positional mapping must still bind
    # each article's analysis to its own slot — NOT collapse to one entry.
    parsed = [
        {"index": 0, "bullets": ["A1", "A2"], "sentiment": "bullish", "confidence": 90, "related_tickers": ["aapl"]},
        {"index": 0, "bullets": ["B1"], "sentiment": "bearish", "confidence": 80, "related_tickers": ["msft", " msft "]},
        {"index": 0, "bullets": ["C1"], "sentiment": "garbage", "confidence": 10, "related_tickers": []},
    ]
    out = NewsCacheService._map_enrichments(parsed, 3)
    assert set(out.keys()) == {0, 1, 2}
    assert out[0]["bullets"] == ["A1", "A2"]
    assert out[1]["bullets"] == ["B1"]
    assert out[0]["sentiment"] == "bullish"
    assert out[1]["sentiment"] == "bearish"
    assert out[2]["sentiment"] == "neutral"        # unknown → neutral
    assert out[0]["related_tickers"] == ["AAPL"]   # cleaned + uppercased
    assert out[1]["related_tickers"] == ["MSFT"]   # dedup + trim


def test_map_enrichments_rejects_count_mismatch():
    # Fewer results than articles → {} (caller returns unenriched+retryable),
    # never a partial map that could misattribute.
    parsed = [{"index": 0, "bullets": ["x"], "sentiment": "bullish", "confidence": 1}]
    assert NewsCacheService._map_enrichments(parsed, 3) == {}
    # More results than articles → {} too.
    assert NewsCacheService._map_enrichments(parsed * 4, 3) == {}


def test_map_enrichments_rejects_non_list():
    assert NewsCacheService._map_enrichments({"index": 0}, 1) == {}
    assert NewsCacheService._map_enrichments(None, 1) == {}


def test_map_enrichments_caps_bullets_and_tickers():
    parsed = [{
        "index": 0,
        "bullets": [f"b{i}" for i in range(9)],
        "sentiment": "neutral",
        "confidence": 0,
        "related_tickers": [f"T{i}" for i in range(20)],
    }]
    out = NewsCacheService._map_enrichments(parsed, 1)
    assert len(out[0]["bullets"]) == 5
    assert len(out[0]["related_tickers"]) == 8


def test_map_enrichments_tolerates_missing_optional_fields():
    parsed = [{"index": 0, "bullets": None, "sentiment": None, "confidence": None, "related_tickers": None}]
    out = NewsCacheService._map_enrichments(parsed, 1)
    assert out[0]["bullets"] == []
    assert out[0]["sentiment"] == "neutral"
    assert out[0]["related_tickers"] == []


# ── _batch_enrich_articles: failure → {} (no shared-cache poison) ────────────

class _RaisingGemini:
    async def generate_json(self, **kwargs):
        raise RuntimeError("429 quota exceeded")


class _FakeGemini:
    def __init__(self, text: str):
        self._text = text

    async def generate_json(self, **kwargs):
        return {"text": self._text}


def _svc_with_gemini(gemini):
    svc = object.__new__(NewsCacheService)  # bypass __init__ (no Supabase/FMP)
    svc.gemini = gemini
    return svc


@pytest.mark.asyncio
async def test_batch_enrich_gemini_exception_returns_empty():
    # The old code returned {i: neutral-fallback} here, which the caller persisted as
    # ai_processed=True (6h cache poison). It must now return {} → unenriched/retryable.
    svc = _svc_with_gemini(_RaisingGemini())
    out = await svc._batch_enrich_articles(
        [{"title": "A", "text": "x"}, {"title": "B", "text": "y"}], ticker="AAPL"
    )
    assert out == {}


@pytest.mark.asyncio
async def test_batch_enrich_truncated_json_returns_empty():
    svc = _svc_with_gemini(_FakeGemini(text="[{"))  # json.loads raises
    out = await svc._batch_enrich_articles([{"title": "A", "text": "x"}], ticker="X")
    assert out == {}


@pytest.mark.asyncio
async def test_batch_enrich_count_mismatch_returns_empty():
    # Valid JSON but 1 result for 2 articles → {} (no partial misattribution).
    svc = _svc_with_gemini(_FakeGemini(
        text=json.dumps([{"index": 0, "bullets": ["b"], "sentiment": "bullish", "confidence": 50}])
    ))
    out = await svc._batch_enrich_articles([{"title": "A"}, {"title": "B"}], ticker="X")
    assert out == {}


# ── Log level: expected LLM degradations are WARNING, not ERROR (no Sentry page) ─

class _QuotaGemini:
    def __init__(self, exc):
        self._exc = exc

    async def generate_json(self, **kwargs):
        raise self._exc


@pytest.mark.asyncio
async def test_batch_enrich_malformed_json_logs_warning_not_error(caplog):
    # A truncated / non-JSON LLM response is an EXPECTED degradation (returns {} →
    # retryable). It must log at WARNING so it does not page as a Sentry ERROR issue.
    import logging
    from app.services import news_cache_service as ncs

    svc = _svc_with_gemini(_FakeGemini(text="[{"))  # json.loads raises JSONDecodeError
    with caplog.at_level(logging.WARNING, logger=ncs.logger.name):
        out = await svc._batch_enrich_articles([{"title": "A", "text": "x"}], ticker="X")
    assert out == {}
    recs = [r for r in caplog.records if "malformed JSON" in r.getMessage()]
    assert recs and all(r.levelno == logging.WARNING for r in recs)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_batch_enrich_quota_error_logs_warning_not_error(caplog):
    # Quota / 429 is a known transient capacity condition (typed GeminiQuotaError in
    # prod, or an untyped 429 message) → WARNING, never ERROR.
    import logging
    from app.services import news_cache_service as ncs
    from app.integrations.gemini import GeminiQuotaError

    for exc in (GeminiQuotaError("resource_exhausted"), RuntimeError("429 quota exceeded")):
        caplog.clear()
        svc = _svc_with_gemini(_QuotaGemini(exc))
        with caplog.at_level(logging.WARNING, logger=ncs.logger.name):
            out = await svc._batch_enrich_articles([{"title": "A"}], ticker="X")
        assert out == {}
        assert [r for r in caplog.records if "quota-limited" in r.getMessage()]
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_batch_enrich_unexpected_error_still_logs_error(caplog):
    # A genuinely unexpected failure (not JSON / not quota) MUST still surface at ERROR
    # so it pages — the downgrade is scoped to the known degradations only.
    import logging
    from app.services import news_cache_service as ncs

    svc = _svc_with_gemini(_QuotaGemini(TypeError("unexpected boom in enrichment")))
    with caplog.at_level(logging.WARNING, logger=ncs.logger.name):
        out = await svc._batch_enrich_articles([{"title": "A"}], ticker="X")
    assert out == {}
    assert [r for r in caplog.records if r.levelno == logging.ERROR and "failed" in r.getMessage()]


@pytest.mark.asyncio
async def test_batch_enrich_happy_path_maps_positionally():
    svc = _svc_with_gemini(_FakeGemini(text=json.dumps([
        {"index": 5, "bullets": ["a"], "sentiment": "bullish", "confidence": 70, "related_tickers": ["AAPL"]},
        {"index": 5, "bullets": ["b"], "sentiment": "bearish", "confidence": 60, "related_tickers": []},
    ])))
    out = await svc._batch_enrich_articles([{"title": "A"}, {"title": "B"}], ticker="X")
    assert set(out.keys()) == {0, 1}                 # positional, ignores index=5
    assert out[0]["sentiment"] == "bullish"
    assert out[1]["sentiment"] == "bearish"
