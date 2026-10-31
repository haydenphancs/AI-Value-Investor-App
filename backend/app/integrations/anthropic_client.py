"""
Anthropic Claude Messages API — a thin JSON-generation wrapper.

Used ONLY by the provider router (`app/services/llm_provider.py`) for four roles: the marketing
writer and judge, the Learn weekly writer and judge. Gemini stays the default and the fallback
for every one of them; nothing else in the app calls Claude.

Integration-layer rules (.claude/rules/integrations.md): HTTP in, dict out; typed exceptions; a
lazy module-level client closed in the app lifespan; no caching, no business decisions. Returns
the SAME dict shape as `GeminiClient.generate_json` — ``{"text", "model", "tokens_used",
"finish_reason"}`` — so callers parse one shape.

Deliberate choices:
  * ONE attempt per call (`max_retries=0`) under a TOTAL deadline. The router falls back to
    Gemini on any failure, so a retry here would only double the worst case the marketing lease
    is sized from (script_service.worst_case_model_call_seconds).
  * Every abnormal ending RAISES rather than returning a finish reason: a refusal or a truncated
    answer has a usable Gemini answer one call away, which a content failure would not get.
  * No temperature/top_p/top_k: API version 2026-10-21 (sent by anthropic 2.x) refuses them on
    every model.
  * The key lives only in the SDK client; no message built here contains it, the URL or the
    prompt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional[anthropic.AsyncAnthropic] = None

#: JSON Schema keywords Claude's structured outputs refuse. Dropped from the WIRE schema only —
#: every caller validates counts and lengths itself, so the answer is still checked.
_UNSUPPORTED_SCHEMA_KEYS = frozenset({
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems",
    "minProperties", "maxProperties",
})
#: Top-level arrays are wrapped under this key (structured outputs want an object at the root)
#: and unwrapped again before the caller sees the text.
WRAP_KEY = "items"
#: Phrases of a used-up balance or a spend limit. A 402 / `billing_error` counts whatever it says.
_CREDIT_PHRASES = ("credit balance", "insufficient credit", "usage limit", "spend limit",
                   "spending limit")
_MESSAGE_CAP = 200
#: Pinned, never read from the environment: the SDK would otherwise follow ANTHROPIC_BASE_URL, which
#: Claude Code sessions export (a local preview must not send the owner's key through a session's
#: gateway).
API_BASE_URL = "https://api.anthropic.com"


class ClaudeException(Exception):
    """Any Claude failure. `tokens_used` is what the failed call billed (0 when unknown)."""

    def __init__(self, message: str, tokens_used: int = 0):
        super().__init__(message)
        self.tokens_used = tokens_used


class ClaudeConfigException(ClaudeException):
    """No CAYDEX_ANTHROPIC_API_KEY — raised before any I/O."""


class ClaudeRateLimitException(ClaudeException):
    """HTTP 429."""


class ClaudeCreditsExhaustedException(ClaudeException):
    """The organisation's credit balance is used up ("credit balance is too low"). Included
    plan credits come back only at the next billing cycle."""


class ClaudeAuthException(ClaudeException):
    """401/403: a revoked or wrong key, or an organisation without access."""


class ClaudeUnavailableException(ClaudeException):
    """5xx, overload, connection failure — transient."""


class ClaudeTimeoutException(ClaudeUnavailableException):
    """The call ran past CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS."""


class ClaudeRefusalException(ClaudeException):
    """`stop_reason == "refusal"` — the model declined this prompt."""


class ClaudeTruncatedException(ClaudeException):
    """`stop_reason == "max_tokens"` — the JSON is incomplete."""


class ClaudeBadAnswerException(ClaudeException):
    """No text block, or a stop reason this wrapper does not expect."""


#: Failures that mean "Claude is unusable for a while", not "this one prompt": the router opens
#: its breaker on these.
BREAKER_EXCEPTIONS = (ClaudeRateLimitException, ClaudeCreditsExhaustedException,
                      ClaudeAuthException, ClaudeConfigException)


def is_configured() -> bool:
    return bool((settings.CAYDEX_ANTHROPIC_API_KEY or "").strip())


def _drop_session_headers(client: anthropic.AsyncAnthropic) -> None:
    """Remove the headers ANTHROPIC_CUSTOM_HEADERS (a Claude Code / gateway session variable) added.
    The SDK merges them into `_custom_headers`, which outranks its own auth and version headers, so
    they could replace our Authorization (a 401 that opens the breaker), pin another API version, or
    carry a gateway token to api.anthropic.com. Dropping them leaves the SDK's own values standing.
    `tests/test_anthropic_client.py::test_session_custom_headers_never_reach_the_api` pins it against
    the real SDK, so a change in how the SDK stores them fails a test rather than silently."""
    raw = os.environ.get("ANTHROPIC_CUSTOM_HEADERS")
    if not raw:
        return
    names = {line.split(":", 1)[0].strip().lower() for line in raw.split("\n") if ":" in line}
    names.discard("")
    custom = getattr(client, "_custom_headers", None)
    if not isinstance(custom, dict):
        logger.warning("anthropic_client: ANTHROPIC_CUSTOM_HEADERS is set but this SDK version keeps no "
                       "_custom_headers dict — its headers may be sent")
        return
    dropped = [k for k in list(custom) if str(k).lower() in names]
    for k in dropped:
        custom.pop(k, None)
    if dropped:
        logger.warning("anthropic_client: ignoring %d header(s) from ANTHROPIC_CUSTOM_HEADERS (%s) — the "
                       "Claude client sends only its own", len(dropped), ", ".join(str(k)[:40] for k in dropped[:5]))


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        if not is_configured():
            raise ClaudeConfigException("CAYDEX_ANTHROPIC_API_KEY is not set")
        # An explicit api_key makes the SDK skip every credential variable (ANTHROPIC_API_KEY,
        # ANTHROPIC_AUTH_TOKEN, profiles) — the CAYDEX_ name keeps a Claude Code session's own
        # credentials out of a local run.
        _client = anthropic.AsyncAnthropic(
            api_key=settings.CAYDEX_ANTHROPIC_API_KEY,
            base_url=API_BASE_URL,
            max_retries=0,
            timeout=float(settings.CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS),
        )
        _drop_session_headers(_client)
    return _client


async def close_claude_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def claude_wire_schema(json_schema: Any) -> Any:
    """A standard JSON Schema → what structured outputs accept: unsupported keywords dropped,
    every object closed (`additionalProperties: false`, which the API requires). Pure."""
    if isinstance(json_schema, list):
        return [claude_wire_schema(s) for s in json_schema]
    if not isinstance(json_schema, dict):
        return json_schema
    out: Dict[str, Any] = {}
    for key, value in json_schema.items():
        if key in _UNSUPPORTED_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: claude_wire_schema(v) for k, v in value.items()}
        elif key in ("items", "anyOf", "allOf", "$defs", "definitions"):
            if isinstance(value, dict) and key in ("$defs", "definitions"):
                out[key] = {k: claude_wire_schema(v) for k, v in value.items()}
            else:
                out[key] = claude_wire_schema(value)
        else:
            out[key] = value
    types = out.get("type")
    if types == "object" or (isinstance(types, list) and "object" in types):
        out["additionalProperties"] = False
    return out


def _is_array(json_schema: Any) -> bool:
    t = json_schema.get("type") if isinstance(json_schema, dict) else None
    return t == "array" or (isinstance(t, list) and "array" in t)


def _unwrap(text: str) -> str:
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text
    if isinstance(parsed, dict) and isinstance(parsed.get(WRAP_KEY), list):
        return json.dumps(parsed[WRAP_KEY])
    return text


def _short(text: Any) -> str:
    s = " ".join(str(text or "").split())
    return s[:_MESSAGE_CAP]


def _usage_tokens(response: Any) -> Dict[str, int]:
    usage = getattr(response, "usage", None)

    def _n(name: str) -> int:
        v = getattr(usage, name, None)
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0

    out = {
        "input": _n("input_tokens"),
        "output": _n("output_tokens"),
        "cache_read": _n("cache_read_input_tokens"),
        "cache_write": _n("cache_creation_input_tokens"),
    }
    out["total"] = sum(out.values())
    return out


def _api_message(e: Exception) -> str:
    """The API's own error message (`error.message` of the body) when there is one, else the SDK's
    text — which prefixes boilerplate and the raw body."""
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"]
    return str(getattr(e, "message", "") or e)


def _translate(e: Exception) -> ClaudeException:
    """An SDK error → our typed exception. Message: class, status, the API error type, request id
    and a capped message — or, for a connection failure, its ROOT cause (DNS, TLS, reset: the SDK
    says only "Connection error."). Never the key, the URL or the prompt (each part scrubbed)."""
    from app.log_redaction import redact_secrets

    name = type(e).__name__
    status = getattr(e, "status_code", None)
    kind = getattr(e, "type", None)
    request_id = getattr(e, "request_id", None)
    msg = _short(redact_secrets(_api_message(e)))
    parts = [f"{name}: status={status}"]
    if kind:
        parts.append(f"type={_short(kind)}")
    if request_id:
        parts.append(f"request_id={_short(request_id)}")
    parts.append(msg)
    if isinstance(e, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        root = e.__cause__ or e.__context__
        if root is not None:
            parts.append(f"cause={type(root).__name__}: {_short(redact_secrets(str(root)))}")
    text = " ".join(p for p in parts if p).strip()
    if isinstance(e, anthropic.RateLimitError):
        return ClaudeRateLimitException(text)
    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ClaudeAuthException(text)
    if isinstance(e, anthropic.APITimeoutError):
        return ClaudeTimeoutException(text)
    if isinstance(e, anthropic.APIConnectionError):
        return ClaudeUnavailableException(text)
    if isinstance(e, anthropic.APIStatusError):
        if status == 402 or kind == "billing_error" or any(p in msg.lower() for p in _CREDIT_PHRASES):
            return ClaudeCreditsExhaustedException(text)
        if isinstance(status, int) and (status >= 500 or status == 529):
            return ClaudeUnavailableException(text)
        return ClaudeException(text)
    return ClaudeException(text)


class ClaudeClient:
    """`generate_json` only — the router's one call shape."""

    async def generate_json(
        self,
        prompt: str,
        *,
        json_schema: Optional[Dict[str, Any]],
        model_name: str,
        system_instruction: Optional[str] = None,
        thinking: Optional[Dict[str, Any]] = None,
        effort: Optional[str] = None,
        max_tokens: Optional[int] = None,
        usage_tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        client = _get_client()
        wrapped = _is_array(json_schema)
        kwargs: Dict[str, Any] = {
            "model": model_name,
            "max_tokens": int(max_tokens or settings.CAYDEX_CLAUDE_MAX_TOKENS),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_instruction:
            kwargs["system"] = system_instruction
        if thinking is not None:
            kwargs["thinking"] = thinking
        output_config: Dict[str, Any] = {}
        if effort:
            output_config["effort"] = effort
        if json_schema is not None:
            schema = claude_wire_schema(json_schema)
            if wrapped:
                schema = {"type": "object", "properties": {WRAP_KEY: schema},
                          "required": [WRAP_KEY], "additionalProperties": False}
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if output_config:
            kwargs["output_config"] = output_config

        deadline = float(settings.CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS)
        try:
            response = await asyncio.wait_for(client.messages.create(**kwargs), timeout=deadline)
        except asyncio.TimeoutError:
            raise ClaudeTimeoutException(
                f"Claude call ran past its {deadline:.0f}s deadline (model={model_name}, "
                f"tag={usage_tag})") from None
        except anthropic.AnthropicError as e:
            raise _translate(e) from e

        usage = _usage_tokens(response)
        stop = getattr(response, "stop_reason", None)
        served = getattr(response, "model", None) or model_name
        logger.info(
            "CLAUDE_USAGE tag=%s model=%s input=%d output=%d cache_read=%d cache_write=%d "
            "total=%d stop=%s", usage_tag or "-", served, usage["input"], usage["output"],
            usage["cache_read"], usage["cache_write"], usage["total"], stop,
        )
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            raise ClaudeRefusalException(
                f"Claude declined (category={category}, model={served}, tag={usage_tag})",
                tokens_used=usage["total"])
        if stop == "max_tokens":
            raise ClaudeTruncatedException(
                f"Claude answer cut at max_tokens (model={served}, tag={usage_tag})",
                tokens_used=usage["total"])
        if stop not in ("end_turn", "stop_sequence"):
            raise ClaudeBadAnswerException(
                f"unexpected stop_reason={stop} (model={served}, tag={usage_tag})",
                tokens_used=usage["total"])
        texts = [b.text for b in (getattr(response, "content", None) or [])
                 if getattr(b, "type", None) == "text" and isinstance(getattr(b, "text", None), str)]
        text = "".join(texts)
        if not text.strip():
            raise ClaudeBadAnswerException(
                f"no text in the answer (model={served}, tag={usage_tag})",
                tokens_used=usage["total"])
        return {
            "text": _unwrap(text) if wrapped else text,
            "model": served,
            "tokens_used": usage["total"],
            "finish_reason": "STOP",
        }


_instance: Optional[ClaudeClient] = None


def get_claude_client() -> ClaudeClient:
    global _instance
    if _instance is None:
        _instance = ClaudeClient()
    return _instance
