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

import asyncio
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


# ── review fixes (2026-09-27 deep-check) ─────────────────────────────────────


def test_rolling_back_to_gemini_with_a_leftover_model_uses_flash_lite(monkeypatch, caplog):
    """OWNER_TASKS' rollback used to be only NEWS_LLM_PROVIDER=gemini, which kept
    NEWS_LLM_MODEL=deepseek-flash and sent it to Gemini — a 404 on every news call."""
    monkeypatch.setattr(settings, "NEWS_LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "NEWS_LLM_MODEL", "deepseek-flash")
    monkeypatch.setattr(news_llm, "_warned_bad_gemini_model", False)
    with caplog.at_level("ERROR"):
        cfg = news_llm.news_llm_config()
        news_llm.news_llm_config()
    assert cfg.provider == "gemini" and cfg.model == "gemini-2.5-flash-lite"
    assert news_llm.news_model_name() == "gemini-2.5-flash-lite", "the audit column tells the truth"
    assert caplog.text.count("is not a Gemini model") == 1, "logged once"


@pytest.mark.parametrize("model", ["gemini-2.5-flash-lite", "", "  "])
def test_openai_compat_without_its_own_model_falls_back_to_gemini(monkeypatch, caplog, model):
    monkeypatch.setattr(settings, "NEWS_LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(settings, "NEWS_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "NEWS_LLM_API_KEY", "k")
    monkeypatch.setattr(settings, "NEWS_LLM_MODEL", model)
    monkeypatch.setattr(news_llm, "_warned_bad_provider", False)
    with caplog.at_level("ERROR"):
        cfg = news_llm.news_llm_config()
    assert cfg.provider == "gemini" and cfg.model == "gemini-2.5-flash-lite"
    assert "must name the openai_compat model" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens"])
async def test_the_output_cap_is_sent_in_the_configured_field(monkeypatch, field):
    _openai_settings(monkeypatch)
    monkeypatch.setattr(settings, "NEWS_LLM_MAX_TOKENS", 8192)
    monkeypatch.setattr(settings, "NEWS_LLM_MAX_TOKENS_FIELD", field)
    captured = _install_transport(monkeypatch, lambda r: _chat_response('{"a": 1}'))
    await news_llm.generate_news_json(prompt="p", system_instruction=None,
                                      response_schema={"type": "OBJECT", "properties": {}})
    body = json.loads(captured[0].content)
    assert body[field] == 8192
    other = {"max_tokens", "max_completion_tokens"} - {field}
    assert not other & set(body), "only one cap field (reasoning models 400 on max_tokens)"


@pytest.mark.asyncio
async def test_extra_body_cannot_replace_the_fields_the_client_owns(monkeypatch, caplog):
    with caplog.at_level("ERROR"):
        _openai_settings(monkeypatch, extra_body=json.dumps({
            "model": "deepseek-reasoner", "temperature": 0.7, "stream": True,
            "thinking": {"type": "disabled"},
        }))
        captured = _install_transport(monkeypatch, lambda r: _chat_response('{"a": 1}'))
        await news_llm.generate_news_json(prompt="p", system_instruction=None, temperature=0.0,
                                          response_schema={"type": "OBJECT", "properties": {}})
    body = json.loads(captured[0].content)
    assert body["model"] == "deepseek-flash" and body["temperature"] == 0.0
    assert "stream" not in body and body["thinking"] == {"type": "disabled"}
    assert "may not set model, stream, temperature" in caplog.text
    assert oc.parse_extra_body('{"model": "x", "top_p": 1}') == {"top_p": 1}
    # The client refuses them too, whoever builds it.
    client = oc.OpenAICompatClient(base_url="https://x", api_key="k", extra_body={"model": "y", "top_p": 1})
    assert client._extra_body == {"top_p": 1}


@pytest.mark.asyncio
async def test_an_exhausted_account_is_a_failure_not_a_wait(monkeypatch):
    _openai_settings(monkeypatch)
    _install_transport(monkeypatch, lambda r: httpx.Response(
        429, json={"error": {"code": "insufficient_quota", "message": "You exceeded your current quota"}}))
    with pytest.raises(oc.OpenAICompatError) as e:
        await news_llm.generate_news_json(prompt="p", system_instruction=None,
                                          response_schema={"type": "OBJECT", "properties": {}})
    assert not news_llm.is_transient_news_llm_error(e.value), "callers log it as a failure (ERROR)"
    assert "insufficient_quota" in str(e.value)
    assert oc.quota_breaker.tripped
    oc.quota_breaker.reset()


@pytest.mark.asyncio
async def test_a_run_of_429s_logs_one_error_until_a_success(monkeypatch, caplog):
    _openai_settings(monkeypatch)
    status = {"code": 429}
    _install_transport(monkeypatch, lambda r: httpx.Response(status["code"]) if status["code"] == 429
                       else _chat_response('{"a": 1}'))

    async def _call():
        oc.quota_breaker._open_until = 0.0      # let each call through to the wire
        return await news_llm.generate_news_json(prompt="p", system_instruction=None,
                                                 response_schema={"type": "OBJECT", "properties": {}})

    with caplog.at_level("ERROR", logger="app.integrations.openai_compat"):
        for _ in range(5):
            with pytest.raises(oc.OpenAICompatQuotaError):
                await _call()
    assert caplog.text.count("quota refusals in a row") == 1, "one alert, latched"
    status["code"] = 200
    await _call()
    assert oc.quota_breaker._trips == 0 and not oc.quota_breaker._alerted, "a success re-arms it"
    oc.quota_breaker.reset()


@pytest.mark.parametrize("base,reason", [
    ("api.deepseek.com", "must start with https://"),       # no scheme
    ("   ", "missing"),
])
def test_a_schemeless_or_blank_base_url_falls_back_loudly(monkeypatch, caplog, base, reason):
    monkeypatch.setattr(settings, "NEWS_LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(settings, "NEWS_LLM_BASE_URL", base)
    monkeypatch.setattr(settings, "NEWS_LLM_API_KEY", "k")
    monkeypatch.setattr(settings, "NEWS_LLM_MODEL", "deepseek-flash")
    monkeypatch.setattr(news_llm, "_warned_bad_provider", False)
    with caplog.at_level("ERROR"):
        cfg = news_llm.news_llm_config()
    assert cfg.provider == "gemini"
    assert reason in caplog.text


def test_a_key_pasted_with_whitespace_is_sent_stripped(monkeypatch):
    _openai_settings(monkeypatch)
    monkeypatch.setattr(settings, "NEWS_LLM_API_KEY", "  sk-test\n")
    monkeypatch.setattr(settings, "NEWS_LLM_BASE_URL", " https://api.example.test/v1 \n")
    cfg = news_llm.news_llm_config()
    assert cfg.provider == "openai_compat"
    assert cfg.api_key == "sk-test" and cfg.base_url == "https://api.example.test/v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    httpx.UnsupportedProtocol("Request URL is missing an 'http://' or 'https://' protocol."),
    httpx.LocalProtocolError("Illegal header value b'Bearer sk-SECRETKEY99\\n'"),
    httpx.ProxyError("proxy refused"),
])
async def test_a_setup_error_is_a_failure_not_a_transient_blip(monkeypatch, exc):
    _openai_settings(monkeypatch)
    monkeypatch.setattr(settings, "NEWS_LLM_API_KEY", "sk-SECRETKEY99")

    def _raise(request):
        raise exc

    _install_transport(monkeypatch, _raise)
    with pytest.raises(oc.OpenAICompatError) as e:
        await news_llm.generate_news_json(prompt="p", system_instruction=None,
                                          response_schema={"type": "OBJECT", "properties": {}})
    assert not news_llm.is_transient_news_llm_error(e.value)
    assert "SECRETKEY99" not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__, "no key via the chained error"


@pytest.mark.asyncio
async def test_a_keepalive_trickle_hits_the_total_deadline_once(monkeypatch):
    """httpx's timeout bounds each READ; a provider sending a blank line every few seconds
    never trips it. The total deadline does — once, without a billed retry."""
    _openai_settings(monkeypatch)
    monkeypatch.setattr(settings, "NEWS_LLM_REQUEST_TIMEOUT_SECONDS", 0.3)
    calls = []

    async def _trickle():
        for _ in range(100):
            yield b"\n"
            await asyncio.sleep(0.05)

    def _handler(request):
        calls.append(request)
        return httpx.Response(200, content=_trickle())

    _install_transport(monkeypatch, _handler)
    with pytest.raises(oc.OpenAICompatTimeoutError):
        await news_llm.generate_news_json(prompt="p", system_instruction=None,
                                          response_schema={"type": "OBJECT", "properties": {}})
    assert len(calls) == 1, "a deadline is not retried (it would bill the generation twice)"
    assert news_llm.is_transient_news_llm_error(oc.OpenAICompatTimeoutError("x"))


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"error": {"code": "data_inspection_failed", "message": "Input data may contain inappropriate content."}},
    {"error": {"code": "invalid_request_error", "message": "Content Exists Risk"}},
])
async def test_a_moderation_400_is_a_content_refusal(monkeypatch, body):
    _openai_settings(monkeypatch)
    _install_transport(monkeypatch, lambda r: httpx.Response(400, json=body))
    with pytest.raises(oc.OpenAICompatContentRejected) as e:
        await news_llm.generate_news_json(prompt="p", system_instruction=None,
                                          response_schema={"type": "OBJECT", "properties": {}})
    assert news_llm.is_content_refusal(e.value)


@pytest.mark.asyncio
async def test_any_other_400_stays_a_plain_failure(monkeypatch):
    _openai_settings(monkeypatch)
    _install_transport(monkeypatch, lambda r: httpx.Response(
        400, json={"error": {"code": "invalid_request_error", "message": "Unknown model deepseek-x"}}))
    with pytest.raises(oc.OpenAICompatError) as e:
        await news_llm.generate_news_json(prompt="p", system_instruction=None,
                                          response_schema={"type": "OBJECT", "properties": {}})
    assert not news_llm.is_content_refusal(e.value), "a bad model must never become an accepted gap"


@pytest.mark.asyncio
async def test_a_read_timeout_is_not_retried(monkeypatch):
    _openai_settings(monkeypatch)
    calls = []

    def _handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("slow generation")

    _install_transport(monkeypatch, _handler)
    with pytest.raises(oc.OpenAICompatTimeoutError):
        await news_llm.generate_news_json(prompt="p", system_instruction=None,
                                          response_schema={"type": "OBJECT", "properties": {}})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_one_burst_of_concurrent_429s_is_not_an_outage(monkeypatch, caplog):
    """Calls already on the wire when the first 429 opened the breaker each got a 429 too:
    that is ONE rate-limit burst, not three separate refusals."""
    _openai_settings(monkeypatch)
    _install_transport(monkeypatch, lambda r: httpx.Response(429, headers={"retry-after": "2"}))
    breaker = oc.quota_breaker
    with caplog.at_level("ERROR", logger="app.integrations.openai_compat"):
        for _ in range(4):                    # four responses of the same burst
            breaker.trip(2.0, reason="rate_limit_exceeded")
    assert breaker._trips == 1 and "quota refusals in a row" not in caplog.text
    oc.quota_breaker.reset()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [
    (402, {"error": {"code": "invalid_request_error", "message": "Insufficient Balance"}}),  # DeepSeek
    (400, {"code": "Arrearage", "error": {"code": "Arrearage", "message": "Access denied"}}), # DashScope
])
async def test_an_exhausted_account_outside_a_429_trips_the_breaker(monkeypatch, status, body):
    _openai_settings(monkeypatch)
    calls = []

    def _handler(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    _install_transport(monkeypatch, _handler)
    for _ in range(3):
        with pytest.raises(oc.OpenAICompatError):
            await news_llm.generate_news_json(prompt="p", system_instruction=None,
                                              response_schema={"type": "OBJECT", "properties": {}})
    assert len(calls) == 1, "the breaker holds later calls off the wire"
    oc.quota_breaker.reset()


@pytest.mark.asyncio
async def test_a_reasoning_model_can_be_sent_no_temperature(monkeypatch):
    _openai_settings(monkeypatch)
    monkeypatch.setattr(settings, "NEWS_LLM_SEND_TEMPERATURE", False)
    captured = _install_transport(monkeypatch, lambda r: _chat_response('{"a": 1}'))
    await news_llm.generate_news_json(prompt="p", system_instruction=None, temperature=0.0,
                                      response_schema={"type": "OBJECT", "properties": {}})
    assert "temperature" not in json.loads(captured[0].content)
    monkeypatch.setattr(settings, "NEWS_LLM_SEND_TEMPERATURE", True)
    captured2 = _install_transport(monkeypatch, lambda r: _chat_response('{"a": 1}'))
    await news_llm.generate_news_json(prompt="p", system_instruction=None, temperature=0.0,
                                      response_schema={"type": "OBJECT", "properties": {}})
    assert json.loads(captured2[0].content)["temperature"] == 0.0, "default keeps temperature 0"


@pytest.mark.asyncio
async def test_one_refused_article_no_longer_stalls_its_live_batch(monkeypatch, caplog):
    """A moderation 400 for ONE article used to leave all 25 of its batch unsummarised on
    every sweep. Split once: the clean half is enriched at its own positions."""
    import app.services.news_cache_service as ncs
    from app.integrations.openai_compat import OpenAICompatContentRejected

    monkeypatch.setattr(ncs, "_REFUSED_BATCHES", set())
    articles = [{"title": f"A{i}" + (" FORBIDDEN" if i == 3 else ""), "text": "t"} for i in range(4)]
    calls = []

    async def _gen(**kw):
        calls.append(kw["prompt"])
        if "FORBIDDEN" in kw["prompt"]:
            raise OpenAICompatContentRejected("400 data_inspection_failed")
        n = sum(1 for i in range(10) if f"<<<END_ARTICLE {i}>>>" in kw["prompt"])
        return {"text": json.dumps([{"index": i, "bullets": [f"b{i}", "c"], "sentiment": "bearish",
                                     "confidence": 70} for i in range(n)])}

    monkeypatch.setattr(ncs, "generate_news_json", _gen)
    svc = object.__new__(NewsCacheService)
    with caplog.at_level("WARNING"):
        out = await svc._batch_enrich_articles(articles, ticker="TSM")
    assert sorted(out) == [0, 1], "the clean half is enriched, at its batch positions"
    assert out[0]["bullets"] == ["b0", "c"] and out[1]["bullets"] == ["b1", "c"]
    assert len(calls) == 3, "one call, then the two halves — no deeper recursion"
    assert "refused by the provider's moderation" in caplog.text
    caplog.clear()
    with caplog.at_level("ERROR"):
        await svc._batch_enrich_articles(articles, ticker="TSM")
    assert "refused by the provider's moderation" not in caplog.text, "ERROR once per process"


@pytest.mark.asyncio
async def test_the_right_half_keys_shift_back_to_batch_positions(monkeypatch):
    import app.services.news_cache_service as ncs
    from app.integrations.openai_compat import OpenAICompatContentRejected

    monkeypatch.setattr(ncs, "_REFUSED_BATCHES", set())
    articles = [{"title": f"A{i}" + (" FORBIDDEN" if i == 0 else ""), "text": "t"} for i in range(4)]

    async def _gen(**kw):
        if "FORBIDDEN" in kw["prompt"]:
            raise OpenAICompatContentRejected("400 content_filter")
        n = sum(1 for i in range(10) if f"<<<END_ARTICLE {i}>>>" in kw["prompt"])
        titles = [line.split("Title: ", 1)[1] for line in kw["prompt"].splitlines() if line.startswith("Title: ")]
        return {"text": json.dumps([{"index": i, "bullets": [titles[i], "x"], "sentiment": "bullish",
                                     "confidence": 50} for i in range(n)])}

    monkeypatch.setattr(ncs, "generate_news_json", _gen)
    out = await object.__new__(NewsCacheService)._batch_enrich_articles(articles, ticker="TSM")
    assert {k: v["bullets"][0] for k, v in out.items()} == {2: "A2", 3: "A3"}
