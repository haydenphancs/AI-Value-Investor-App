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
    # Gemini's four keys, plus the tokens by kind (the marketing cost line prices each at its own rate)
    assert result == {"text": '{"a": 1}', "model": "claude-sonnet-5-5", "tokens_used": 155,
                      "finish_reason": "STOP",
                      "usage": {"input": 100, "output": 20, "cache_read": 30, "cache_write": 5}}
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
    # …by kind, with the model that billed them: the router hands both on, so a failed Claude attempt is
    # still priced as Claude's in the marketing cost line
    assert info.value.usage == {"input": 100, "output": 20, "cache_read": 0, "cache_write": 0}
    assert info.value.model == "claude-sonnet-5-5"


@pytest.mark.asyncio
async def test_a_call_that_never_answered_carries_no_usage(monkeypatch):
    _install(monkeypatch, [httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error",
                                                                                  "message": "Overloaded"}})])
    with pytest.raises(ac.ClaudeUnavailableException) as info:
        await _call()
    assert (info.value.tokens_used, info.value.usage, info.value.model) == (0, None, None)


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


# ── vision + a caller's deadline (the marketing per-post art, owner decision 2026-11-15) ──────────
# "Google draws, Claude checks": the art brief and check reach this client through
# llm_provider.ClaudeOnly with `images` (the drawn picture) and a `deadline_seconds` that fits the
# art step's budget. Everything else keeps today's request, byte for byte.

_JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + bytes(range(256)) * 4
_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 2


@pytest.mark.asyncio
async def test_without_images_the_request_body_is_byte_identical_to_todays(monkeypatch):
    seen = _install(monkeypatch, [httpx2.Response(200, json=_message()) for _ in range(4)])
    await _call()                                   # today's call: no new keyword at all
    await _call(images=())
    await _call(images=[], deadline_seconds=None)
    await _call(images=None)
    assert len(seen) == 4
    assert seen[1].content == seen[0].content
    assert seen[2].content == seen[0].content
    assert seen[3].content == seen[0].content
    assert json.loads(seen[0].content)["messages"] == [{"role": "user", "content": "PROMPT"}]


@pytest.mark.asyncio
async def test_images_are_base64_blocks_sent_before_the_text_in_order(monkeypatch):
    import base64

    seen = _install(monkeypatch, [httpx2.Response(200, json=_message())])
    result = await _call(images=[(_JPEG, "image/jpeg"), (bytearray(_PNG), "image/png")],
                         usage_tag="marketing_art_check")
    content = json.loads(seen[0].content)["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "image", "text"], "image blocks first, the text last"
    assert content[0]["source"] == {"type": "base64", "media_type": "image/jpeg",
                                    "data": base64.standard_b64encode(_JPEG).decode("ascii")}
    assert content[1]["source"]["media_type"] == "image/png"
    assert base64.standard_b64decode(content[1]["source"]["data"]) == _PNG
    assert content[2] == {"type": "text", "text": "PROMPT"}
    body = json.loads(seen[0].content)
    assert body["system"] == "SYS" and body["output_config"]["effort"] == "low"
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in body
    assert result["text"] == '{"a": 1}' and result["usage"]["input"] == 100


def _bad_images():
    gif = b"GIF89a" + b"\x00" * 64
    return {
        "gif": [(gif, "image/gif")],
        "webp": [(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32, "image/webp")],
        "upper_case_type": [(_JPEG, "IMAGE/JPEG")],
        "png_declared_jpeg": [(_PNG, "image/jpeg")],
        "jpeg_declared_png": [(_JPEG, "image/png")],
        "empty": [(b"", "image/jpeg")],
        "str_data": [("not bytes", "image/jpeg")],
        "type_not_str": [(_JPEG, None)],
        "not_a_pair": [(_JPEG,)],
        "triple": [(_JPEG, "image/jpeg", "extra")],
        "bare_bytes": _JPEG,
        "bare_str": "image/jpeg",
        "generator": ((d, t) for d, t in [(_JPEG, "image/jpeg")]),
        "dict": {"data": _JPEG},
        "too_many": [(_JPEG, "image/jpeg")] * (ac.MAX_IMAGES + 1),
    }


@pytest.mark.parametrize("name", list(_bad_images()))
@pytest.mark.asyncio
async def test_a_bad_image_is_refused_typed_before_any_io(monkeypatch, name):
    seen = _install(monkeypatch, [httpx2.Response(200, json=_message())])
    with pytest.raises(ac.ClaudeRequestInvalidException) as info:
        await _call(images=_bad_images()[name])
    assert seen == [], "nothing was sent"
    text = str(info.value)
    assert "JFIF" not in text and "PNG" not in text.replace("image/png", ""), "never a byte of the image"
    assert "PROMPT" not in text and _KEY not in text
    assert info.value.tokens_used == 0 and info.value.usage is None
    assert info.value.billing_unknown is False, "nothing was sent, so nothing can have billed"


@pytest.mark.asyncio
async def test_the_request_checks_run_before_the_key_is_even_needed(monkeypatch):
    """No key AND a bad image → the image refusal, not ClaudeConfigException: the checks are pure and
    come first (a refused request never constructs the client)."""
    monkeypatch.setattr(settings, "CAYDEX_ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(ac, "_client", None)
    with pytest.raises(ac.ClaudeRequestInvalidException):
        await _call(images=[(_JPEG, "image/gif")])
    with pytest.raises(ac.ClaudeRequestInvalidException):
        await _call(deadline_seconds=0)
    assert ac._client is None
    with pytest.raises(ac.ClaudeConfigException):
        await _call(images=[(_JPEG, "image/jpeg")])


@pytest.mark.asyncio
async def test_an_image_over_the_apis_base64_limit_is_refused_and_one_at_it_is_sent(monkeypatch):
    # base64 length = 4 × ceil(n / 3): 3,932,160 raw bytes is exactly 5 MiB of base64, one more is over
    at_limit = b"\xff\xd8\xff" + b"\x00" * (3 * ac.MAX_IMAGE_BASE64_CHARS // 4 - 3)
    over = at_limit + b"\x00"
    assert len(ac.image_blocks([(at_limit, "image/jpeg")])[0]["source"]["data"]) == ac.MAX_IMAGE_BASE64_CHARS
    seen = _install(monkeypatch, [httpx2.Response(200, json=_message())])
    with pytest.raises(ac.ClaudeRequestInvalidException) as info:
        await _call(images=[(over, "image/jpeg")])
    assert seen == [] and "base64" in str(info.value)
    await _call(images=[(at_limit, "image/jpeg")])
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_the_usage_line_counts_images_only_on_a_call_that_sent_them(monkeypatch, caplog):
    _install(monkeypatch, [httpx2.Response(200, json=_message(i=1500, o=40)),
                           httpx2.Response(200, json=_message(i=100, o=20))])
    with caplog.at_level("INFO", logger="app.integrations.anthropic_client"):
        await _call(images=[(_JPEG, "image/jpeg")], usage_tag="marketing_art_check")
        await _call(usage_tag="marketing_writer")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("CLAUDE_USAGE")]
    assert lines == [
        "CLAUDE_USAGE tag=marketing_art_check model=claude-sonnet-5-5 input=1500 output=40 cache_read=0 "
        "cache_write=0 total=1540 stop=end_turn images=1",
        "CLAUDE_USAGE tag=marketing_writer model=claude-sonnet-5-5 input=100 output=20 cache_read=0 "
        "cache_write=0 total=120 stop=end_turn",
    ], "an image-free call's line is exactly today's"


@pytest.mark.asyncio
async def test_a_shorter_deadline_ends_in_the_typed_timeout_here_not_an_outer_cancel(monkeypatch):
    _install(monkeypatch, [])
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS", 120.0)

    async def slow(**_kw: Any) -> Any:
        await asyncio.sleep(5)

    monkeypatch.setattr(ac._client.messages, "create", slow)
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(ac.ClaudeTimeoutException) as info:
        await _call(deadline_seconds=0.05, images=[(_JPEG, "image/jpeg")])
    assert loop.time() - started < 2.0, "the caller's deadline, not the configured 120 s"
    assert "0.05s deadline" in str(info.value) and "images=1" in str(info.value)
    assert info.value.billing_unknown is True, "it may have billed: a spend cap counts it at its worst"
    assert (info.value.tokens_used, info.value.usage) == (0, None)


@pytest.mark.parametrize("value,expected", [(None, 120.0), (30, 30.0), (37.5, 37.5), (500, 120.0),
                                            (120.0, 120.0)])
def test_the_call_deadline_is_the_shorter_of_the_callers_and_the_configured(monkeypatch, value, expected):
    monkeypatch.setattr(settings, "CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS", 120.0)
    assert ac.call_deadline(value) == expected


@pytest.mark.parametrize("value", [0, 0.0, -1, float("nan"), float("inf"), True, "30", [30]])
@pytest.mark.asyncio
async def test_a_deadline_that_is_not_a_positive_finite_number_is_refused_before_any_io(monkeypatch, value):
    seen = _install(monkeypatch, [httpx2.Response(200, json=_message())])
    with pytest.raises(ac.ClaudeRequestInvalidException):
        await _call(deadline_seconds=value)
    assert seen == []


@pytest.mark.asyncio
async def test_billing_unknown_marks_only_the_failures_that_may_have_billed(monkeypatch):
    # a dropped connection: the request may have reached the API
    def handler(request):
        raise httpx2.ReadError("connection reset by peer")

    monkeypatch.setattr(settings, "CAYDEX_ANTHROPIC_API_KEY", _KEY)
    monkeypatch.setattr(ac, "_client", anthropic.AsyncAnthropic(
        api_key=_KEY, max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))))
    with pytest.raises(ac.ClaudeUnavailableException) as dropped:
        await _call()
    assert dropped.value.billing_unknown is True
    # an HTTP answer (an overload) or an abnormal answer is known: nothing unknown about its bill
    _install(monkeypatch, [httpx2.Response(529, json={"type": "error", "error": {
        "type": "overloaded_error", "message": "Overloaded"}}), httpx2.Response(200, json=_message(stop="refusal"))])
    with pytest.raises(ac.ClaudeUnavailableException) as overloaded:
        await _call()
    with pytest.raises(ac.ClaudeRefusalException) as refused:
        await _call()
    assert overloaded.value.billing_unknown is False and refused.value.billing_unknown is False
    assert ac.ClaudeException("x").billing_unknown is False
    assert ac.ClaudeTimeoutException("x").billing_unknown is True
    assert ac.ClaudeBreakerOpenException("x").billing_unknown is False


def test_the_account_breaker_set_is_exactly_used_up_credits_and_auth_inside_the_roles_set():
    assert set(ac.ACCOUNT_BREAKER_EXCEPTIONS) == {ac.ClaudeCreditsExhaustedException, ac.ClaudeAuthException}
    assert set(ac.ACCOUNT_BREAKER_EXCEPTIONS) < set(ac.BREAKER_EXCEPTIONS), "the roles keep the wider set"
    for cls in (ac.ClaudeRateLimitException, ac.ClaudeUnavailableException, ac.ClaudeTimeoutException,
                ac.ClaudeRequestInvalidException, ac.ClaudeBreakerOpenException):
        assert not issubclass(cls, ac.ACCOUNT_BREAKER_EXCEPTIONS), cls.__name__
    for cls in (ac.ClaudeRequestInvalidException, ac.ClaudeBreakerOpenException):
        assert issubclass(cls, ac.ClaudeException)
        assert not issubclass(cls, ac.BREAKER_EXCEPTIONS), "neither ever opens the router's breaker"


def test_the_new_exceptions_classify_as_the_ai_engine_too():
    from app.api.error_response import ErrorCode, classify_exception

    for cls in (ac.ClaudeRequestInvalidException, ac.ClaudeBreakerOpenException):
        code, _status = classify_exception(cls("status=429 timed out"))
        assert code == ErrorCode.GEMINI_UNAVAILABLE, cls.__name__


def test_the_module_docstring_names_the_art_use_the_jump_lookup_and_no_other():
    """Reworded with the jump search's fallback lookup (2026-11-27): the one app feature that reaches
    Claude, through `ClaudeOnly.cited_web_search` only — the boundary test pins who may call it."""
    doc = ac.__doc__ or ""
    assert "ClaudeOnly" in doc and "per-post ART" in doc
    assert "four roles" in doc and "Nothing else in the app calls Claude" in doc
    assert "cited_web_search" in doc and "jump_claude_lookup_service" in doc
    assert "test_claude_web_search_boundary.py" in doc


# ── cited_web_search: the jump search's fallback lookup (2026-11-27) ─────────────────────────────
# Hand-written pages only (never a real card or page). The real SDK builds and parses every message.

_URL_A = "https://news.example.org/acme-order"
_URL_B = "https://wire.example.net/acme-plant"
_URL_C = "https://blog.example.com/uncited"


def _search_message(stop: str = "end_turn", *, searches: Any = 2, content: Any = None,
                    usage_extra: Any = None) -> Dict[str, Any]:
    usage: Dict[str, Any] = {"input_tokens": 9000, "output_tokens": 300, "cache_read_input_tokens": 0,
                             "cache_creation_input_tokens": 0}
    if searches is not None:
        usage["server_tool_use"] = {"web_search_requests": searches, "web_fetch_requests": 0}
    if usage_extra:
        usage.update(usage_extra)
    if content is None:
        content = [
            {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
             "input": {"query": "acme widgets news"}},
            {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
                {"type": "web_search_result", "url": _URL_A, "title": "Acme Widgets lands a utility order",
                 "page_age": "November 27, 2026", "encrypted_content": "ENC-SECRET-A"},
                {"type": "web_search_result", "url": _URL_B, "title": "Acme opens a second plant",
                 "page_age": None, "encrypted_content": "ENC-SECRET-B"},
                {"type": "web_search_result", "url": _URL_C, "title": "An unrelated post",
                 "encrypted_content": "ENC-SECRET-C"},
            ]},
            {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_2",
             "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}},
            {"type": "text", "text": "Acme rose after news of an order.", "citations": [
                {"type": "web_search_result_location", "url": _URL_A, "title": "Acme Widgets lands a utility order",
                 "cited_text": "Acme Widgets said a utility placed a multi-year order.",
                 "encrypted_index": "IDX-SECRET-A"},
            ]},
            {"type": "text", "text": " It also opened a plant.", "citations": [
                {"type": "web_search_result_location", "url": _URL_B, "title": None,
                 "cited_text": "The company opened its second plant this week.", "encrypted_index": "IDX-SECRET-B"},
            ]},
        ]
    return {"id": "msg_search", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": content, "stop_reason": stop, "stop_sequence": None, "usage": usage}


_HOSTS = ["fb.com", "markets.ft.com", "reddit.com", "t.co"]
_LOCATION = {"type": "approximate", "country": "US", "timezone": "America/New_York"}


async def _search(**kw: Any) -> Dict[str, Any]:
    args = dict(model_name="claude-sonnet-5-5", system_instruction="SYS", max_uses=2, max_tokens=3000,
                effort="low", thinking={"type": "between_tools"}, blocked_domains=_HOSTS,
                user_location=_LOCATION, usage_tag="updates_jump_lookup", deadline_seconds=90.0)
    prompt = kw.pop("prompt", "PROMPT")
    args.update(kw)
    return await ac.ClaudeClient().cited_web_search(prompt, **args)


@pytest.mark.asyncio
async def test_cited_web_search_request_and_mapping(monkeypatch):
    seen = _install(monkeypatch, [httpx2.Response(200, json=_search_message())])
    result = await _search()
    body = json.loads(seen[0].content)
    assert body["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 2,
                              "blocked_domains": _HOSTS, "user_location": _LOCATION}]
    assert body["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert body["system"] == "SYS" and body["max_tokens"] == 3000
    assert body["thinking"] == {"type": "between_tools"}
    assert body["output_config"] == {"effort": "low"}, "no structured output: citations need plain text"
    for banned in ("temperature", "top_p", "top_k", "tool_choice", "fallbacks", "cache_control"):
        assert banned not in body, banned
    assert seen[0].headers.get("anthropic-version") == "2026-10-21"
    assert len(seen) == 1, "one attempt"
    # the answer: results and web citations only, never an encrypted field
    assert result["results"] == [
        {"url": _URL_A, "title": "Acme Widgets lands a utility order", "page_age": "November 27, 2026"},
        {"url": _URL_B, "title": "Acme opens a second plant", "page_age": None},
        {"url": _URL_C, "title": "An unrelated post", "page_age": None},
    ]
    assert result["citations"] == [
        {"url": _URL_A, "title": "Acme Widgets lands a utility order",
         "cited_text": "Acme Widgets said a utility placed a multi-year order."},
        {"url": _URL_B, "title": None, "cited_text": "The company opened its second plant this week."},
    ]
    assert result["search_errors"] == {"max_uses_exceeded": 1}, "an error object is counted, not raised"
    assert result["web_search_requests"] == 2
    assert result["text"] == "Acme rose after news of an order. It also opened a plant."
    assert result["stop_reason"] == "end_turn" and result["model"] == "claude-sonnet-5-5"
    assert result["usage"] == {"input": 9000, "output": 300, "cache_read": 0, "cache_write": 0}
    assert result["tokens_used"] == 9300
    assert "SECRET" not in json.dumps(result), "encrypted_content / encrypted_index are never returned"


@pytest.mark.asyncio
async def test_an_empty_domain_list_and_no_location_send_neither_key(monkeypatch):
    seen = _install(monkeypatch, [httpx2.Response(200, json=_search_message())])
    await _search(blocked_domains=(), user_location=None, thinking=None, effort=None, system_instruction=None)
    body = json.loads(seen[0].content)
    assert body["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 2}]
    for absent in ("thinking", "output_config", "system"):
        assert absent not in body, absent


@pytest.mark.parametrize("stop,exc", [
    ("pause_turn", ac.ClaudeBadAnswerException),
    ("tool_use", ac.ClaudeBadAnswerException),
    ("model_context_window_exceeded", ac.ClaudeBadAnswerException),
    ("refusal", ac.ClaudeRefusalException),
    ("max_tokens", ac.ClaudeTruncatedException),
])
@pytest.mark.asyncio
async def test_cited_web_search_abnormal_stops_are_billed_typed_and_never_resumed(monkeypatch, stop, exc):
    seen = _install(monkeypatch, [httpx2.Response(200, json=_search_message(stop))] * 2)
    with pytest.raises(exc) as info:
        await _search()
    assert len(seen) == 1, "a paused turn is never resumed: the cost stays inside the caller's reserve"
    assert info.value.tokens_used == 9300 and info.value.model == "claude-sonnet-5-5"
    assert info.value.usage == {"input": 9000, "output": 300, "cache_read": 0, "cache_write": 0}
    assert info.value.web_searches == 2, "the searches it billed ride on the exception"
    assert "PROMPT" not in str(info.value) and "acme" not in str(info.value).lower()


@pytest.mark.asyncio
async def test_an_empty_answer_is_returned_not_raised(monkeypatch):
    _install(monkeypatch, [httpx2.Response(200, json=_search_message(content=[], searches=0))])
    result = await _search()
    assert (result["text"], result["results"], result["citations"], result["web_search_requests"]) == ("", [], [], 0)


@pytest.mark.asyncio
async def test_unreadable_search_usage_is_none_only_when_results_exist(monkeypatch):
    _install(monkeypatch, [httpx2.Response(200, json=_search_message(searches=None)),
                           httpx2.Response(200, json=_search_message(searches=None, content=[]))])
    assert (await _search())["web_search_requests"] is None, "searched, count unknown → priced at its worst"
    assert (await _search())["web_search_requests"] == 0, "no result block, no search"


@pytest.mark.asyncio
async def test_odd_blocks_are_skipped_never_raised(monkeypatch):
    content = [
        {"type": "web_search_tool_result", "tool_use_id": "s1", "content": [
            {"type": "web_search_result", "url": 5, "title": "x", "encrypted_content": "e"},
            {"type": "web_search_result", "url": _URL_A, "title": None, "encrypted_content": "e"},
            {"type": "something_else", "url": _URL_A},
        ]},
        {"type": "web_search_tool_result", "tool_use_id": "s2", "content": {"type": "web_search_tool_result_error",
                                                                          "error_code": "Bad Code!"}},
        {"type": "text", "text": "x", "citations": [
            {"type": "char_location", "cited_text": "not a web citation", "document_index": 0,
             "document_title": None, "start_char_index": 0, "end_char_index": 1},
            {"type": "web_search_result_location", "url": _URL_A, "cited_text": None, "encrypted_index": "i"},
        ]},
    ]
    _install(monkeypatch, [httpx2.Response(200, json=_search_message(content=content))])
    result = await _search()
    assert result["results"] == [] and result["citations"] == []
    assert result["search_errors"] == {"unknown": 1}


@pytest.mark.asyncio
async def test_the_usage_line_appends_web_searches_only_on_the_search_call(monkeypatch, caplog):
    _install(monkeypatch, [httpx2.Response(200, json=_search_message()),
                           httpx2.Response(200, json=_message(i=100, o=20)),
                           httpx2.Response(200, json=_search_message(searches=None))])
    with caplog.at_level("INFO", logger="app.integrations.anthropic_client"):
        await _search()
        await _call(usage_tag="marketing_writer")
        await _search()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("CLAUDE_USAGE")]
    assert lines == [
        "CLAUDE_USAGE tag=updates_jump_lookup model=claude-sonnet-5-5 input=9000 output=300 cache_read=0 "
        "cache_write=0 total=9300 stop=end_turn web_searches=2",
        "CLAUDE_USAGE tag=marketing_writer model=claude-sonnet-5-5 input=100 output=20 cache_read=0 "
        "cache_write=0 total=120 stop=end_turn",
        "CLAUDE_USAGE tag=updates_jump_lookup model=claude-sonnet-5-5 input=9000 output=300 cache_read=0 "
        "cache_write=0 total=9300 stop=end_turn web_searches=?",
    ], "every other call's line is exactly today's"
    joined = " ".join(lines)
    assert _URL_A not in joined and "Acme" not in joined and "PROMPT" not in joined


@pytest.mark.parametrize("status,body,exc,code", [
    (400, {"type": "error", "error": {"type": "invalid_request_error",
                                      "message": "Your credit balance is too low to access the Anthropic API."}},
     ac.ClaudeCreditsExhaustedException, 400),
    (401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}},
     ac.ClaudeAuthException, 401),
    (403, {"type": "error", "error": {"type": "permission_error", "message": "web search is not enabled"}},
     ac.ClaudeAuthException, 403),
    (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
     ac.ClaudeRateLimitException, 429),
    (529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
     ac.ClaudeUnavailableException, 529),
    (400, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad tool"}},
     ac.ClaudeException, 400),
])
@pytest.mark.asyncio
async def test_cited_web_search_failures_are_typed_and_carry_their_http_status(monkeypatch, status, body, exc, code):
    seen = _install(monkeypatch, [httpx2.Response(status, json=body)] * 2)
    with pytest.raises(exc) as info:
        await _search()
    assert type(info.value) is exc and info.value.status_code == code
    assert len(seen) == 1 and info.value.web_searches == 0, "an HTTP error answered nothing: no search billed"
    assert _KEY not in str(info.value) and "PROMPT" not in str(info.value)


@pytest.mark.asyncio
async def test_status_code_is_none_for_a_timeout_and_a_dropped_connection(monkeypatch):
    _install(monkeypatch, [])

    async def slow(**_kw: Any) -> Any:
        await asyncio.sleep(5)

    monkeypatch.setattr(ac._client.messages, "create", slow)
    with pytest.raises(ac.ClaudeTimeoutException) as info:
        await _search(deadline_seconds=0.05)
    assert info.value.status_code is None and info.value.billing_unknown is True
    assert "0.05s deadline" in str(info.value)


def _bad_search_args():
    return {
        "max_uses_zero": dict(max_uses=0),
        "max_uses_bool": dict(max_uses=True),
        "max_uses_float": dict(max_uses=2.0),
        "max_uses_too_many": dict(max_uses=ac.MAX_WEB_SEARCH_USES + 1),
        "domains_str": dict(blocked_domains="reddit.com"),
        "domains_set": dict(blocked_domains={"reddit.com"}),
        "domains_generator": dict(blocked_domains=(d for d in ["reddit.com"])),
        "domains_scheme": dict(blocked_domains=["https://reddit.com"]),
        "domains_path": dict(blocked_domains=["reddit.com/r/x"]),
        "domains_upper": dict(blocked_domains=["Reddit.com"]),
        "domains_single_label": dict(blocked_domains=["localhost"]),
        "domains_ip": dict(blocked_domains=["10.0.0.1"]),
        "domains_not_str": dict(blocked_domains=[7]),
        "domains_repeat": dict(blocked_domains=["reddit.com", "reddit.com"]),
        "domains_too_many": dict(blocked_domains=[f"h{i}.example.com" for i in range(ac.MAX_BLOCKED_DOMAINS + 1)]),
        "location_not_dict": dict(user_location="US"),
        "location_type": dict(user_location={"type": "exact", "country": "US"}),
        "location_key": dict(user_location={"type": "approximate", "street": "1 Main"}),
        "location_value": dict(user_location={"type": "approximate", "country": 1}),
        "prompt_empty": dict(prompt="   "),
        "max_tokens_zero": dict(max_tokens=0),
        "max_tokens_bool": dict(max_tokens=True),
        "max_tokens_huge": dict(max_tokens=64000),
        "deadline_zero": dict(deadline_seconds=0),
    }


@pytest.mark.parametrize("name", list(_bad_search_args()))
@pytest.mark.asyncio
async def test_a_bad_search_request_is_refused_typed_before_any_io(monkeypatch, name):
    seen = _install(monkeypatch, [httpx2.Response(200, json=_search_message())])
    with pytest.raises(ac.ClaudeRequestInvalidException) as info:
        await _search(**_bad_search_args()[name])
    assert seen == [], "nothing was sent"
    assert info.value.tokens_used == 0 and info.value.billing_unknown is False


def test_exactly_64_plain_hostnames_are_accepted_and_the_jump_deny_list_fits():
    hosts = [f"h{i}.example.com" for i in range(ac.MAX_BLOCKED_DOMAINS)]
    assert ac.web_search_tool(2, hosts)["blocked_domains"] == hosts
    from app.services import web_result_hygiene as hygiene

    deny = sorted(getattr(hygiene, "DENIED_HOSTS", None) or hygiene._DENIED_HOSTS)
    assert ac.web_search_tool(2, deny)["blocked_domains"] == deny, "every deny-list host is a plain hostname"
    assert len(deny) <= ac.MAX_BLOCKED_DOMAINS


def test_every_new_exception_attribute_defaults_to_nothing_billed():
    e = ac.ClaudeException("x")
    assert (e.status_code, e.web_searches) == (None, 0)
    assert ac.WEB_SEARCH_TOOL_TYPE == "web_search_20260209" and ac.WEB_SEARCH_TOOL_NAME == "web_search"


@pytest.mark.asyncio
async def test_generate_json_is_untouched_by_the_web_search_call(monkeypatch):
    """The JSON call never sends a tool and keeps its exact body (no `tools` key)."""
    seen = _install(monkeypatch, [httpx2.Response(200, json=_message())])
    await _call()
    assert "tools" not in json.loads(seen[0].content)
