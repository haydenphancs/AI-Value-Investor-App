"""The news features' switchable model (app/services/news_llm.py + integrations/openai_compat.py).

Two promises are pinned here:
  1. On the default provider (Gemini) the per-article enrichment call is UNCHANGED — same
     prompt, system instruction, model and schema, byte for byte — against a golden captured
     from the code before the switch existed (tests/data/news_enrichment_prompt_golden.json).
  2. On an OpenAI-compatible provider (DeepSeek, OpenAI, Qwen, …) the answer is mapped back to
     the shape every caller already parses, and a malformed answer leaves articles unenriched
     (retryable) — never labelled "neutral".
Hermetic: httpx is replaced by a fake transport; nothing leaves the process.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import app.integrations.openai_compat as oc
import app.services.news_llm as news_llm
from app.config import settings
from app.services.news_cache_service import (
    MARKET_SCOPE,
    SENTIMENT_RUBRIC,
    NewsCacheService,
    article_external_id,
)

_GOLDEN = json.loads((Path(__file__).parent / "data" / "news_enrichment_prompt_golden.json").read_text())


class _CapGemini:
    def __init__(self):
        self.calls = []

    async def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        return {"text": "[]", "model": "fake", "tokens_used": 0, "finish_reason": "STOP"}


# ── 1. the default path is byte-identical ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("case,ticker", [("ticker", "ORCL"), ("market", MARKET_SCOPE), ("blank", "")])
async def test_the_gemini_enrichment_call_is_unchanged(monkeypatch, case, ticker):
    monkeypatch.setattr(settings, "NEWS_LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "NEWS_LLM_MODEL", "gemini-2.5-flash-lite")
    cap = _CapGemini()
    svc = object.__new__(NewsCacheService)
    svc.gemini = cap
    await svc._batch_enrich_articles(_GOLDEN["inputs"], ticker=ticker)
    [kw] = cap.calls
    want = _GOLDEN["cases"][case]
    assert sorted(kw) == want["keys"], "no new argument may reach the Gemini call"
    assert kw["prompt"] == want["prompt"]
    assert kw["system_instruction"] == want["system_instruction"]
    assert kw["model_name"] == want["model_name"]
    assert json.loads(json.dumps(kw["response_schema"])) == want["response_schema"]


def test_the_rubric_is_the_one_in_the_live_prompt():
    assert SENTIMENT_RUBRIC in _GOLDEN["cases"]["ticker"]["prompt"]


@pytest.mark.parametrize("raw,i,expected", [
    ({"url": "https://x/1", "title": "T"}, 0, "https://x/1"),
    ({"url": "", "title": "Title only"}, 3, "Title only"),
    ({"url": None, "title": None}, 7, "unknown_7"),
    ({"url": "u" * 600}, 0, "u" * 500),
])
def test_article_external_id(raw, i, expected):
    assert article_external_id(raw, i) == expected


# ── 2. configuration ──────────────────────────────────────────────────────────


def test_default_provider_is_gemini():
    cfg = news_llm.news_llm_config()
    assert cfg.provider == "gemini" and cfg.model == "gemini-2.5-flash-lite"


@pytest.mark.parametrize("provider,base,key", [
    ("deepseek", "https://api.deepseek.com", "k"),     # unknown provider name
    ("openai_compat", None, "k"),                      # incomplete setup
    ("openai_compat", "https://api.deepseek.com", None),
])
def test_a_bad_setup_falls_back_to_gemini_loudly(monkeypatch, caplog, provider, base, key):
    monkeypatch.setattr(settings, "NEWS_LLM_PROVIDER", provider)
    monkeypatch.setattr(settings, "NEWS_LLM_BASE_URL", base)
    monkeypatch.setattr(settings, "NEWS_LLM_API_KEY", key)
    monkeypatch.setattr(news_llm, "_warned_bad_provider", False)
    with caplog.at_level("ERROR"):
        cfg = news_llm.news_llm_config()
    assert cfg.provider == "gemini" and cfg.model == "gemini-2.5-flash-lite"
    assert "falling back to Gemini" in caplog.text


def test_the_api_key_never_appears_in_a_repr(monkeypatch):
    monkeypatch.setattr(settings, "NEWS_LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(settings, "NEWS_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "NEWS_LLM_API_KEY", "sk-SECRET123")
    monkeypatch.setattr(settings, "NEWS_LLM_MODEL", "deepseek-flash")
    cfg = news_llm.news_llm_config()
    assert "sk-SECRET123" not in repr(cfg)
    client = oc.OpenAICompatClient(base_url=cfg.base_url, api_key=cfg.api_key)
    assert "sk-SECRET123" not in repr(client)


# ── 3. schema translation ─────────────────────────────────────────────────────


def test_the_enrichment_schema_translates_to_json_schema():
    out = news_llm.gemini_schema_to_json_schema(NewsCacheService._ENRICHMENT_SCHEMA)
    assert out["type"] == "array"
    item = out["items"]
    assert item["type"] == "object" and item["additionalProperties"] is False
    assert item["properties"]["sentiment"] == {"type": "string", "enum": ["bullish", "bearish", "neutral"]}
    assert item["properties"]["bullets"] == {"type": "array", "items": {"type": "string"}}
    assert item["required"] == ["index", "bullets", "sentiment", "confidence"]


def test_property_ordering_becomes_key_order_and_is_dropped():
    schema = {"type": "OBJECT", "properties": {"b": {"type": "STRING"}, "a": {"type": "INTEGER"}},
              "propertyOrdering": ["a", "b"], "required": ["a"]}
    out = news_llm.gemini_schema_to_json_schema(schema)
    assert list(out["properties"]) == ["a", "b"]
    assert "propertyOrdering" not in out


def test_nullable_and_bounds_carry_over():
    schema = {"type": "ARRAY", "items": {"type": "STRING", "nullable": True}, "minItems": 1, "maxItems": 4}
    out = news_llm.gemini_schema_to_json_schema(schema)
    assert out == {"type": "array", "items": {"type": ["string", "null"]}, "minItems": 1, "maxItems": 4}


# ── 4. the OpenAI-compatible path ─────────────────────────────────────────────


def _openai_settings(monkeypatch, **extra):
    monkeypatch.setattr(settings, "NEWS_LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(settings, "NEWS_LLM_BASE_URL", "https://api.example.test/v1/")
    monkeypatch.setattr(settings, "NEWS_LLM_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "NEWS_LLM_MODEL", "deepseek-flash")
    monkeypatch.setattr(settings, "NEWS_LLM_JSON_MODE", extra.get("json_mode", "json_object"))
    monkeypatch.setattr(settings, "NEWS_LLM_EXTRA_BODY", extra.get("extra_body"))
    oc.quota_breaker.reset()


def _install_transport(monkeypatch, handler):
    captured = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_wrapped))
    monkeypatch.setattr(oc, "_client", client)
    monkeypatch.setattr(oc, "_RETRY_DELAY_SECONDS", 0.0)
    return captured


def _chat_response(content, finish="stop", usage=None):
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150,
                           "prompt_cache_hit_tokens": 64},
    })


@pytest.mark.asyncio
async def test_an_array_answer_is_wrapped_on_the_wire_and_unwrapped_for_the_caller(monkeypatch, caplog):
    _openai_settings(monkeypatch, extra_body='{"thinking": {"type": "disabled"}}')
    captured = _install_transport(monkeypatch, lambda r: _chat_response(json.dumps({"items": [
        {"index": 0, "bullets": ["a", "b"], "sentiment": "bearish", "confidence": 70, "related_tickers": []},
    ]})))
    with caplog.at_level("INFO"):
        out = await news_llm.generate_news_json(
            prompt="Label this.", system_instruction="SYS",
            response_schema=NewsCacheService._ENRICHMENT_SCHEMA, usage_tag="t",
        )
    assert json.loads(out["text"])[0]["sentiment"] == "bearish", "the caller gets the bare array back"
    assert out["finish_reason"] == "STOP" and out["tokens_used"] == 150 and out["model"] == "deepseek-flash"
    [req] = captured
    assert str(req.url) == "https://api.example.test/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-test"
    body = json.loads(req.content)
    assert body["model"] == "deepseek-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}, "provider switches come from settings"
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    user = body["messages"][1]["content"]
    assert "JSON" in user and '"items"' in user, "json_object mode needs the shape in the prompt"
    assert "LLM_USAGE provider=openai_compat model=deepseek-flash" in caplog.text
    assert "cached=64" in caplog.text
    assert "sk-test" not in caplog.text


@pytest.mark.asyncio
async def test_json_schema_mode_sends_the_schema(monkeypatch):
    _openai_settings(monkeypatch, json_mode="json_schema")
    captured = _install_transport(monkeypatch, lambda r: _chat_response('{"conclusion": "x"}'))
    await news_llm.generate_news_json(
        prompt="p", system_instruction=None,
        response_schema={"type": "OBJECT", "properties": {"conclusion": {"type": "STRING"}}},
    )
    body = json.loads(captured[0].content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["properties"]["conclusion"] == {"type": "string"}
    assert body["messages"][0]["role"] == "user", "no system message when none is given"


@pytest.mark.asyncio
@pytest.mark.parametrize("finish,expected", [("length", "MAX_TOKENS"), ("content_filter", "SAFETY"), ("stop", "STOP")])
async def test_finish_reasons_map_to_the_gemini_vocabulary(monkeypatch, finish, expected):
    _openai_settings(monkeypatch)
    _install_transport(monkeypatch, lambda r: _chat_response('{"a": 1}', finish=finish))
    out = await news_llm.generate_news_json(prompt="p", system_instruction=None,
                                           response_schema={"type": "OBJECT", "properties": {}})
    assert out["finish_reason"] == expected


@pytest.mark.asyncio
async def test_a_429_is_a_quota_error_and_trips_only_this_breaker(monkeypatch):
    from app.integrations.gemini import _quota_circuit

    _openai_settings(monkeypatch)
    _install_transport(monkeypatch, lambda r: httpx.Response(429, headers={"retry-after": "30"}))
    gemini_was = _quota_circuit.tripped
    with pytest.raises(oc.OpenAICompatQuotaError):
        await news_llm.generate_news_json(prompt="p", system_instruction=None,
                                          response_schema={"type": "OBJECT", "properties": {}})
    assert oc.quota_breaker.tripped and news_llm.quota_tripped()
    assert _quota_circuit.tripped == gemini_was, "Gemini (chat, reports) is never tripped by it"
    assert news_llm.is_transient_news_llm_error(oc.OpenAICompatQuotaError("x"))
    with pytest.raises(oc.OpenAICompatQuotaError, match="breaker is open"):
        await news_llm.generate_news_json(prompt="p", system_instruction=None,
                                          response_schema={"type": "OBJECT", "properties": {}})
    oc.quota_breaker.reset()


@pytest.mark.asyncio
async def test_5xx_is_retried_once_then_transient(monkeypatch):
    _openai_settings(monkeypatch)
    captured = _install_transport(monkeypatch, lambda r: httpx.Response(503))
    with pytest.raises(oc.OpenAICompatTransientError):
        await news_llm.generate_news_json(prompt="p", system_instruction=None,
                                          response_schema={"type": "OBJECT", "properties": {}})
    assert len(captured) == 2
    assert news_llm.is_transient_news_llm_error(oc.OpenAICompatTransientError("x"))


@pytest.mark.asyncio
async def test_a_4xx_is_not_transient(monkeypatch):
    _openai_settings(monkeypatch)
    _install_transport(monkeypatch, lambda r: httpx.Response(401, text="bad key"))
    with pytest.raises(oc.OpenAICompatError) as e:
        await news_llm.generate_news_json(prompt="p", system_instruction=None,
                                          response_schema={"type": "OBJECT", "properties": {}})
    assert not news_llm.is_transient_news_llm_error(e.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    "not json",
    json.dumps({"items": [{"index": 0, "bullets": ["a", "b"], "sentiment": "bullish", "confidence": 1}]}),  # 1 of 2
    json.dumps({"wrong_key": []}),
    "",
])
async def test_a_bad_answer_leaves_articles_unenriched_never_neutral(monkeypatch, content):
    _openai_settings(monkeypatch)
    _install_transport(monkeypatch, lambda r: _chat_response(content))
    svc = object.__new__(NewsCacheService)
    svc.gemini = _CapGemini()
    out = await svc._batch_enrich_articles(
        [{"title": "A", "text": "x"}, {"title": "B", "text": "y"}], ticker="ORCL",
    )
    assert out == {}, "wrong count / malformed → retryable, never a default label"
    assert svc.gemini.calls == [], "the Gemini client is not used on the other provider"


@pytest.mark.asyncio
async def test_a_good_answer_enriches_through_the_other_provider(monkeypatch):
    _openai_settings(monkeypatch)
    _install_transport(monkeypatch, lambda r: _chat_response(json.dumps({"items": [
        {"index": 0, "bullets": ["a", "b"], "sentiment": "bullish", "confidence": 80, "related_tickers": ["orcl"]},
        {"index": 1, "bullets": ["c", "d"], "sentiment": "Negative", "confidence": 60, "related_tickers": []},
    ]})))
    svc = object.__new__(NewsCacheService)
    svc.gemini = _CapGemini()
    out = await svc._batch_enrich_articles(
        [{"title": "A", "text": "x"}, {"title": "B", "text": "y"}], ticker="ORCL",
    )
    assert out[0]["sentiment"] == "bullish" and out[1]["sentiment"] == "bearish"
    assert out[0]["related_tickers"] == ["ORCL"]
    assert news_llm.news_model_name() == "deepseek-flash"


def test_extra_body_must_be_a_json_object(caplog):
    with caplog.at_level("ERROR"):
        assert oc.parse_extra_body("not json") == {}
        assert oc.parse_extra_body("[1, 2]") == {}
    assert oc.parse_extra_body('{"reasoning_effort": "minimal"}') == {"reasoning_effort": "minimal"}
    assert oc.parse_extra_body(None) == {}
    assert "NEWS_LLM_EXTRA_BODY" in caplog.text


def test_chat_and_reports_never_import_the_news_model():
    """Scope guard: only the news features route through news_llm."""
    import re

    root = Path(__file__).resolve().parents[1] / "app"
    importer = re.compile(r"^\s*(from app\.services\.news_llm import|import app\.services\.news_llm)", re.M)
    users = sorted(
        str(p.relative_to(root)) for p in root.rglob("*.py")
        if importer.search(p.read_text()) and p.name != "news_llm.py"
    )
    assert "services/news_cache_service.py" in users
    assert set(users) <= {
        "services/news_cache_service.py",
        "services/news_sentiment_backfill_service.py",
    }, f"only the news features may use the switchable model: {users}"
