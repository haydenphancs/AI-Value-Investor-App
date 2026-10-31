"""
`app/integrations/anthropic_client.py` — the thin Claude JSON client behind llm_provider.

Hermetic: the real SDK talks to an `httpx2.MockTransport`, so these tests exercise the SDK's own
request building and error classes without a socket. They pin the wire request (no temperature —
API version 2026-10-21 refuses it; structured outputs with a closed, constraint-free schema; one
attempt), the Gemini-shaped result dict, and the typed exception for every failure the router
falls back on.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import anthropic
import httpx2
import pytest

from app.config import settings
from app.integrations import anthropic_client as ac

_KEY = "sk-ant-test-not-a-real-key"


def _message(text: str = '{"a": 1}', stop: str = "end_turn", **usage: int) -> Dict[str, Any]:
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": text}] if text is not None else [],
        "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": usage.get("i", 100), "output_tokens": usage.get("o", 20),
                  "cache_read_input_tokens": usage.get("cr", 0),
                  "cache_creation_input_tokens": usage.get("cw", 0)},
    }


def _install(monkeypatch, responses: List[httpx2.Response]) -> List[httpx2.Request]:
    """A real SDK client over a mock transport; returns the list of requests it sent."""
    seen: List[httpx2.Request] = []
    queue = list(responses)

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return queue.pop(0)

    monkeypatch.setattr(settings, "CAYDEX_ANTHROPIC_API_KEY", _KEY)
    client = anthropic.AsyncAnthropic(
        api_key=_KEY, max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(ac, "_client", client)
    return seen


async def _call(**kw: Any) -> Dict[str, Any]:
    args = dict(json_schema={"type": "object", "properties": {"a": {"type": "integer"}}},
                model_name="claude-sonnet-5-5", system_instruction="SYS", effort="low",
                usage_tag="marketing_writer")
    args.update(kw)
    return await ac.ClaudeClient().generate_json("PROMPT", **args)


@pytest.mark.asyncio
async def test_a_clean_answer_comes_back_in_geminis_dict_shape(monkeypatch):
    seen = _install(monkeypatch, [httpx2.Response(200, json=_message(i=100, o=20, cr=30, cw=5))])
    result = await _call()
    assert result == {"text": '{"a": 1}', "model": "claude-sonnet-5-5", "tokens_used": 155,
                      "finish_reason": "STOP"}
    assert len(seen) == 1
    body = json.loads(seen[0].content)
    assert body["model"] == "claude-sonnet-5-5"
    assert body["system"] == "SYS"
    assert body["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["max_tokens"] == settings.CAYDEX_CLAUDE_MAX_TOKENS
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in body
    assert seen[0].headers.get("anthropic-version") == "2026-10-21"


@pytest.mark.asyncio
async def test_thinking_is_sent_only_when_asked(monkeypatch):
    seen = _install(monkeypatch, [httpx2.Response(200, json=_message()),
                                  httpx2.Response(200, json=_message())])
    await _call()
    await _call(thinking={"type": "between_tools"})
    assert "thinking" not in json.loads(seen[0].content)
    assert json.loads(seen[1].content)["thinking"] == {"type": "between_tools"}


def test_the_wire_schema_drops_unsupported_keywords_and_closes_every_object():
    schema = {
        "type": "object",
        "properties": {
            "lines": {"type": "array", "minItems": 6, "maxItems": 6,
                      "items": {"type": "string", "maxLength": 90}},
            "n": {"type": "integer", "minimum": 0, "maximum": 9},
            "card": {"type": "object", "properties": {"t": {"type": ["string", "null"]}}},
        },
        "required": ["lines"],
    }
    out = ac.claude_wire_schema(schema)
    assert out["additionalProperties"] is False
    assert out["properties"]["card"]["additionalProperties"] is False
    assert out["properties"]["lines"] == {"type": "array", "items": {"type": "string"}}
    assert out["properties"]["n"] == {"type": "integer"}
    assert out["required"] == ["lines"]
    # The input is not mutated (callers reuse module-level schemas).
    assert schema["properties"]["lines"]["minItems"] == 6


@pytest.mark.asyncio
async def test_a_top_level_array_is_wrapped_on_the_wire_and_unwrapped_for_the_caller(monkeypatch):
    seen = _install(monkeypatch, [httpx2.Response(200, json=_message('{"items": [{"x": 1}]}'))])
    result = await _call(json_schema={"type": "array", "items": {"type": "object",
                                                                 "properties": {"x": {"type": "integer"}}}})
    sent = json.loads(seen[0].content)["output_config"]["format"]["schema"]
    assert sent["type"] == "object" and sent["required"] == ["items"]
    assert json.loads(result["text"]) == [{"x": 1}]


@pytest.mark.parametrize("status,body,exc", [
    (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
     ac.ClaudeRateLimitException),
    (400, {"type": "error", "error": {"type": "invalid_request_error",
                                      "message": "Your credit balance is too low to access the Anthropic API."}},
     ac.ClaudeCreditsExhaustedException),
    (401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}},
     ac.ClaudeAuthException),
    (403, {"type": "error", "error": {"type": "permission_error", "message": "no access"}},
     ac.ClaudeAuthException),
    (500, {"type": "error", "error": {"type": "api_error", "message": "boom"}},
     ac.ClaudeUnavailableException),
    (529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
     ac.ClaudeUnavailableException),
    (400, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad schema"}},
     ac.ClaudeException),
])
@pytest.mark.asyncio
async def test_every_http_failure_raises_its_typed_exception_after_ONE_attempt(monkeypatch, status, body, exc):
    seen = _install(monkeypatch, [httpx2.Response(status, json=body)] * 3)
    with pytest.raises(exc) as info:
        await _call()
    assert len(seen) == 1, "never retried on Claude — the router falls back to Gemini instead"
    assert _KEY not in str(info.value)
    assert "PROMPT" not in str(info.value)


@pytest.mark.parametrize("message,exc", [
    (_message(stop="refusal"), ac.ClaudeRefusalException),
    (_message(stop="max_tokens"), ac.ClaudeTruncatedException),
    (_message(stop="tool_use"), ac.ClaudeBadAnswerException),
    (_message(text="   "), ac.ClaudeBadAnswerException),
    (_message(text=None), ac.ClaudeBadAnswerException),
])
@pytest.mark.asyncio
async def test_an_abnormal_answer_raises_and_carries_its_billed_tokens(monkeypatch, message, exc):
    _install(monkeypatch, [httpx2.Response(200, json=message)])
    with pytest.raises(exc) as info:
        await _call()
    assert info.value.tokens_used == 120


@pytest.mark.asyncio
async def test_the_total_deadline_raises_the_timeout_exception(monkeypatch):
    _install(monkeypatch, [])
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS", 0.05)

    async def slow(**_kw: Any) -> Any:
        await asyncio.sleep(5)

    monkeypatch.setattr(ac._client.messages, "create", slow)
    with pytest.raises(ac.ClaudeTimeoutException):
        await _call()
    assert issubclass(ac.ClaudeTimeoutException, ac.ClaudeUnavailableException)


@pytest.mark.asyncio
async def test_no_key_raises_before_any_io(monkeypatch):
    monkeypatch.setattr(settings, "CAYDEX_ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(ac, "_client", None)
    assert ac.is_configured() is False
    with pytest.raises(ac.ClaudeConfigException):
        await _call()


@pytest.mark.asyncio
async def test_close_releases_the_client(monkeypatch):
    _install(monkeypatch, [])
    await ac.close_claude_client()
    assert ac._client is None
    await ac.close_claude_client()   # idempotent


def test_every_breaker_exception_is_a_claude_exception():
    for cls in ac.BREAKER_EXCEPTIONS:
        assert issubclass(cls, ac.ClaudeException)


def test_classify_exception_names_the_ai_engine_never_the_market_data_provider():
    from app.api.error_response import ErrorCode, classify_exception

    for cls in (ac.ClaudeException, ac.ClaudeRateLimitException, ac.ClaudeTimeoutException,
                ac.ClaudeCreditsExhaustedException, ac.ClaudeRefusalException):
        code, _status = classify_exception(cls("status=429 timed out"))
        assert code == ErrorCode.GEMINI_UNAVAILABLE, cls.__name__


def _walk(node, path="$"):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def _real_schemas():
    from app.services.learn_weekly import article_schema as a
    from app.services.learn_weekly import judge as lj
    from app.services.marketing import judge as mj
    from app.services.marketing import writer_prompts as wp

    return {
        "marketing_writer": wp.RESPONSE_SCHEMA,
        "marketing_judge": mj.response_schema(["hook", "captions.x", "image_post.title"]),
        "learn_outline": a.outline_response_schema(["f1", "f2"]),
        "learn_section": a.SECTION_RESPONSE_SCHEMA,
        "learn_judge": lj.response_schema(["title", "sections[0]"]),
        "learn_support": lj.support_schema(),
    }


@pytest.mark.parametrize("name", list(_real_schemas()))
def test_every_real_role_schema_translates_to_one_structured_outputs_accepts(name):
    """The four roles' real Gemini schemas, through the router's translation and the wire filter:
    no keyword structured outputs refuse, every object closed, every type lowercase JSON Schema."""
    from app.services.llm_schema import gemini_schema_to_json_schema

    wire = ac.claude_wire_schema(gemini_schema_to_json_schema(_real_schemas()[name]))
    allowed_types = {"object", "array", "string", "integer", "number", "boolean", "null"}
    for path, node in _walk(wire):
        if path.endswith(".properties") or path.endswith(".$defs"):
            continue   # a map of names, not a schema node
        assert not (set(node) & ac._UNSUPPORTED_SCHEMA_KEYS), (name, path, set(node))
        t = node.get("type")
        for one in (t if isinstance(t, list) else [t] if t is not None else []):
            assert one in allowed_types, (name, path, one)
        if t == "object" or (isinstance(t, list) and "object" in t):
            assert node.get("additionalProperties") is False, (name, path)
            for req in node.get("required", []):
                assert req in node.get("properties", {}), (name, path, req)


# ── review round (2026-10-30) ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status,body", [
    (402, {"type": "error", "error": {"type": "billing_error", "message": "Payment required"}}),
    (400, {"type": "error", "error": {"type": "invalid_request_error",
                                      "message": "You have reached your specified API usage limits."}}),
])
@pytest.mark.asyncio
async def test_a_billing_stop_or_spend_limit_is_used_up_credits_and_opens_the_breaker(monkeypatch, status, body):
    _install(monkeypatch, [httpx2.Response(status, json=body)])
    with pytest.raises(ac.ClaudeCreditsExhaustedException):
        await _call()
    assert issubclass(ac.ClaudeCreditsExhaustedException, ac.BREAKER_EXCEPTIONS)


@pytest.mark.asyncio
async def test_a_status_error_names_the_api_type_request_id_and_the_apis_own_message(monkeypatch):
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": "model: not found x"}}
    _install(monkeypatch, [httpx2.Response(400, json=body, headers={"request-id": "req_abc123"})])
    with pytest.raises(ac.ClaudeException) as info:
        await _call()
    text = str(info.value)
    assert "type=invalid_request_error" in text and "request_id=req_abc123" in text
    assert "model: not found x" in text


@pytest.mark.asyncio
async def test_a_connection_failure_names_its_root_cause(monkeypatch):
    def handler(request):
        raise httpx2.ConnectError("certificate verify failed: unable to get local issuer certificate")

    monkeypatch.setattr(settings, "CAYDEX_ANTHROPIC_API_KEY", _KEY)
    client = anthropic.AsyncAnthropic(
        api_key=_KEY, max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(ac, "_client", client)
    with pytest.raises(ac.ClaudeUnavailableException) as info:
        await _call()
    assert "cause=ConnectError: certificate verify failed" in str(info.value)


def test_the_production_client_is_one_attempt_pinned_to_the_api_and_ignores_session_variables(monkeypatch):
    """`_get_client` itself, not a test-built client: max_retries 0 (the router, not the SDK, owns
    what happens after a failure), the configured timeout, and neither ANTHROPIC_BASE_URL nor
    ANTHROPIC_AUTH_TOKEN from a Claude Code session can redirect or re-key it."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.invalid")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "session-token-should-not-be-used")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-session-key-should-not-be-used")
    monkeypatch.setattr(settings, "CAYDEX_ANTHROPIC_API_KEY", _KEY)
    monkeypatch.setattr(ac, "_client", None)
    client = ac._get_client()
    try:
        assert client.max_retries == 0
        assert client.timeout == settings.CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS
        assert str(client.base_url).rstrip("/") == ac.API_BASE_URL
        assert client.api_key == _KEY and client.auth_token is None
    finally:
        asyncio.run(ac.close_claude_client())


def test_an_anthropic_key_is_redacted_in_logs_and_its_lookalikes_are_not():
    from app.log_redaction import redact_secrets

    assert redact_secrets(f"oops {_KEY}x9 here") == "oops sk-ant-*** here"
    for keep in ("the sk-ant- prefix", "task-ant-colony", "Anthropic sells Claude"):
        assert redact_secrets(keep) == keep


def _code(src: str) -> str:
    """Python source with `#` comments dropped (string literals are kept)."""
    return "\n".join(line.split("#", 1)[0] for line in src.splitlines())


def _call_block(src: str, opener: str) -> str:
    start = src.index(opener)
    depth = 0
    for i in range(start + len(opener) - 1, len(src)):
        depth += {"(": 1, ")": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
    raise AssertionError(f"unbalanced {opener}")


def test_the_generic_teardown_guard_covers_the_claude_client():
    """The lifespan teardown of `close_claude_client` is checked by the generic guard
    (tests/test_integration_client_teardown.py), which must therefore SEE this module: it is
    derived from the constructor, never hand-listed (review 2026-10-30)."""
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parent / "test_integration_client_teardown.py"
    spec = importlib.util.spec_from_file_location("_teardown_guard", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
        needs = mod._needs_teardown()
    finally:
        sys.modules.pop(spec.name, None)
    assert needs.get("anthropic_client") == ["close_claude_client"]


def test_sentry_never_auto_instruments_the_anthropic_sdk():
    """A Claude error the router handles must not become an unhandled Sentry event with the prompt
    in its frame vars (review 2026-10-30): the integration is disabled inside the init call itself."""
    from pathlib import Path

    src = _code((Path(__file__).resolve().parents[1] / "app/main.py").read_text(encoding="utf-8"))
    init = _call_block(src, "sentry_sdk.init(")
    assert "disabled_integrations=[AnthropicIntegration()]" in init
    assert "auto_enabling_integrations=False" not in init


def test_session_custom_headers_never_reach_the_api(monkeypatch):
    """ANTHROPIC_CUSTOM_HEADERS (a session/gateway variable the SDK merges into every request) could
    replace our Authorization header or pin another API version: each named header is omitted."""
    seen: List[httpx2.Request] = []

    def handler(request):
        seen.append(request)
        return httpx2.Response(200, json=_message())

    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS",
                       "Authorization: Bearer gateway-token\nanthropic-version: 2023-06-01\nX-Gw: 1")
    monkeypatch.setattr(settings, "CAYDEX_ANTHROPIC_API_KEY", _KEY)
    monkeypatch.setattr(ac, "_client", None)
    real = anthropic.AsyncAnthropic

    def with_transport(**kw):
        return real(**kw, http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))

    monkeypatch.setattr(anthropic, "AsyncAnthropic", with_transport)
    try:
        asyncio.run(_call())
    finally:
        asyncio.run(ac.close_claude_client())
    headers = seen[0].headers
    assert "gateway-token" not in headers.get("authorization", "")
    assert _KEY in headers.get("authorization", "") or headers.get("x-api-key") == _KEY
    assert headers.get("anthropic-version") == "2026-10-21"
    assert "x-gw" not in headers
