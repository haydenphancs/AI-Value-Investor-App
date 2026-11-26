"""
Anthropic Claude Messages API — a thin JSON-generation wrapper.

Used ONLY through `app/services/llm_provider.py`, and only for marketing and Learn weekly work:
  * its provider router, for four roles — the marketing writer and judge, the Learn weekly
    writer and judge. Gemini stays the default and the fallback for every one of them;
  * its no-fallback `ClaudeOnly` path: the measurement scripts (judge calibration, the writer
    preview) and — owner decision 2026-11-15, "Google draws, Claude checks" — the marketing
    per-post ART: Claude writes the picture's brief and then inspects the drawn picture (vision,
    `images=`). Gemini draws it (`integrations/gemini.py::generate_image`); there is no Gemini
    fallback for the brief or the check, and art is optional (any failure = no art that day).
Nothing else in the app calls Claude (owner: Claude is for marketing only, never app features).

Integration-layer rules (.claude/rules/integrations.md): HTTP in, dict out; typed exceptions; a
lazy module-level client closed in the app lifespan; no caching, no business decisions. Returns
the SAME dict shape as `GeminiClient.generate_json` — ``{"text", "model", "tokens_used",
"finish_reason"}`` — so callers parse one shape, plus ``usage`` (the call's input / output /
cache_read / cache_write tokens, which the marketing cost line prices apart, per model).

Deliberate choices:
  * ONE attempt per call (`max_retries=0`) under a TOTAL deadline. The router falls back to
    Gemini on any failure, so a retry here would only double the worst case the marketing lease
    is sized from (script_service.worst_case_model_call_seconds). A caller with its own step
    budget (the art) passes a SHORTER `deadline_seconds`, so the call ends in the typed
    ClaudeTimeoutException here rather than being cancelled from outside, where nothing would
    record that it may have billed (`ClaudeException.billing_unknown`).
  * Every abnormal ending RAISES rather than returning a finish reason: a refusal or a truncated
    answer has a usable Gemini answer one call away, which a content failure would not get.
  * A request this wrapper can tell the API would refuse — an image that is not a JPEG or PNG of
    its declared type, an oversized image, a non-positive deadline — raises
    ClaudeRequestInvalidException BEFORE any I/O (nothing sent, nothing billed).
  * No temperature/top_p/top_k: API version 2026-10-21 (sent by anthropic 2.x) refuses them on
    every model.
  * The key lives only in the SDK client; no message built here contains it, the URL, the
    prompt or any image byte.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

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

#: The media types an image block may declare (2026-11-16, the marketing art check: it sends the JPEG
#: it will publish; PNG for a picture kept as drawn). Anything else is refused before any I/O.
IMAGE_MEDIA_TYPES = ("image/jpeg", "image/png")
#: The leading bytes each declared type must start with: the API refuses an image whose bytes are not
#: its declared type, so the mismatch is caught here first (no request, nothing billed).
_IMAGE_MAGIC = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
#: The API refuses an image whose base64 data is over 5 MB (5,242,880 characters) — about 3.9 MB of
#: raw bytes. Refused here first.
MAX_IMAGE_BASE64_CHARS = 5 * 1024 * 1024
#: Images in one call. The art check sends one; the cap only bounds a caller's mistake.
MAX_IMAGES = 20


class ClaudeException(Exception):
    """Any Claude failure. `tokens_used` is what the failed call billed (0 when unknown); `usage` is
    the same tokens by kind (`USAGE_KINDS`) and `model` the model that billed them — None when the
    call never answered (a timeout, a transport or HTTP error).

    `billing_unknown` is True when the request may have reached the API, and been billed, without
    an answer being read — a timeout, a dropped connection. Its tokens are then unknown (0 / None
    above), so a caller holding a spend cap must count that call at its worst case."""

    #: Overridden per class (a timeout) or per instance (a connection failure, `_translate`).
    billing_unknown: bool = False

    def __init__(self, message: str, tokens_used: int = 0, *,
                 usage: Optional[Dict[str, int]] = None, model: Optional[str] = None):
        super().__init__(message)
        self.tokens_used = tokens_used
        self.usage = usage
        self.model = model


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
    """The call ran past its deadline: CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS, or the caller's
    shorter `deadline_seconds`. It may have billed (`billing_unknown`)."""

    billing_unknown = True


class ClaudeBreakerOpenException(ClaudeUnavailableException):
    """Never raised by this client: `llm_provider.ClaudeOnly` raises it under its opt-in breaker
    policy when the shared Claude breaker is open — no call was made, nothing billed."""


class ClaudeRequestInvalidException(ClaudeException):
    """Refused HERE, before any I/O: an image the API would refuse (a media type other than
    IMAGE_MEDIA_TYPES, empty or oversized bytes, bytes that are not the declared type, too many
    images) or a deadline that is not a positive, finite number of seconds. Nothing was sent and
    nothing billed. A caller's bug, never a Claude outage: it opens no breaker."""


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
#: The narrower set for an OPTIONAL call (the marketing art brief and check, llm_provider's
#: BREAKER_ACCOUNT policy): only a failure of the ACCOUNT itself — used-up credits, a revoked or
#: wrong key — opens the shared breaker. A 429, an overload or a timeout on an optional picture
#: only means no art this run; it must not push the marketing judge and the Learn weekly roles to
#: Gemini for an hour (review 2026-11-15).
ACCOUNT_BREAKER_EXCEPTIONS = (ClaudeCreditsExhaustedException, ClaudeAuthException)


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


#: The token kinds of `usage` (each billed at its own rate) — every key but "total".
USAGE_KINDS = ("input", "output", "cache_read", "cache_write")


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


def _billed(usage: Dict[str, int], model: str) -> Dict[str, Any]:
    """The billing keywords of an exception raised AFTER Claude answered: its total, its tokens by
    kind and the model that billed them."""
    return {"tokens_used": usage["total"], "usage": {k: usage[k] for k in USAGE_KINDS}, "model": model}


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
        # A refused DNS lookup never sent anything; a connection reset after the request did, and
        # may have billed. The SDK does not say which, so a spend cap counts it at its worst case.
        unavailable = ClaudeUnavailableException(text)
        unavailable.billing_unknown = True
        return unavailable
    if isinstance(e, anthropic.APIStatusError):
        if status == 402 or kind == "billing_error" or any(p in msg.lower() for p in _CREDIT_PHRASES):
            return ClaudeCreditsExhaustedException(text)
        if isinstance(status, int) and (status >= 500 or status == 529):
            return ClaudeUnavailableException(text)
        return ClaudeException(text)
    return ClaudeException(text)


def _media_type_shown(media_type: Any) -> str:
    """A refused media type as it may appear in a message: short and printable, else its type."""
    if isinstance(media_type, str) and len(media_type) <= 40 and media_type.isprintable():
        return repr(media_type)
    return f"<{type(media_type).__name__}>"


def image_blocks(images: Sequence[Tuple[bytes, str]], *, usage_tag: Optional[str] = None) -> List[Dict[str, Any]]:
    """`(bytes, media type)` pairs → the Messages API's base64 image blocks, in order. Pure: every
    check runs before any I/O and raises ClaudeRequestInvalidException, naming the image's position,
    its size and its declared type — never a byte of it. None or an empty list is no image. Refused:
    anything but a list or tuple of pairs (a bare bytes, a str, a one-shot generator a retry would
    find empty), more than MAX_IMAGES, an item that is not a (bytes, str) pair, empty bytes, a media
    type outside IMAGE_MEDIA_TYPES, bytes that do not start like their declared type, and base64
    data over MAX_IMAGE_BASE64_CHARS."""
    if images is None:
        return []
    if not isinstance(images, (list, tuple)):
        raise ClaudeRequestInvalidException(
            f"images must be a list or tuple of (bytes, media type) pairs, not {type(images).__name__} "
            f"(tag={usage_tag})")
    items = list(images)
    if len(items) > MAX_IMAGES:
        raise ClaudeRequestInvalidException(
            f"{len(items)} images in one call — at most {MAX_IMAGES} (tag={usage_tag})")
    blocks: List[Dict[str, Any]] = []
    for i, item in enumerate(items):
        if not (isinstance(item, (tuple, list)) and len(item) == 2):
            raise ClaudeRequestInvalidException(
                f"image {i} is not a (bytes, media type) pair: {type(item).__name__} (tag={usage_tag})")
        data, media_type = item
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise ClaudeRequestInvalidException(
                f"image {i}: its data is {type(data).__name__}, not bytes (tag={usage_tag})")
        raw = bytes(data)
        if not raw:
            raise ClaudeRequestInvalidException(f"image {i} is empty (tag={usage_tag})")
        if media_type not in IMAGE_MEDIA_TYPES:
            raise ClaudeRequestInvalidException(
                f"image {i}: media type {_media_type_shown(media_type)} is not one of "
                f"{', '.join(IMAGE_MEDIA_TYPES)} (tag={usage_tag})")
        if not raw.startswith(_IMAGE_MAGIC[media_type]):
            raise ClaudeRequestInvalidException(
                f"image {i}: {len(raw)} bytes that do not start like {media_type} (tag={usage_tag})")
        encoded = base64.standard_b64encode(raw).decode("ascii")
        if len(encoded) > MAX_IMAGE_BASE64_CHARS:
            raise ClaudeRequestInvalidException(
                f"image {i}: {len(raw)} bytes ({len(encoded)} base64 characters) is over the API's "
                f"{MAX_IMAGE_BASE64_CHARS}-character limit (tag={usage_tag})")
        blocks.append({"type": "image",
                       "source": {"type": "base64", "media_type": media_type, "data": encoded}})
    return blocks


def call_deadline(deadline_seconds: Optional[float], *, usage_tag: Optional[str] = None) -> float:
    """The TOTAL deadline of one call: CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS, or the caller's
    `deadline_seconds` when it is SHORTER (never longer: the SDK client's own timeout is the
    configured one). A value that is not a positive, finite number (a bool, NaN, inf, 0, a
    negative, a string) raises ClaudeRequestInvalidException before any I/O."""
    configured = float(settings.CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS)
    if deadline_seconds is None:
        return configured
    if (isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(deadline_seconds) or deadline_seconds <= 0):
        shown = deadline_seconds if isinstance(deadline_seconds, (int, float)) else type(deadline_seconds).__name__
        raise ClaudeRequestInvalidException(
            f"deadline_seconds={shown!r} is not a positive, finite number of seconds (tag={usage_tag})")
    return min(float(deadline_seconds), configured)


class ClaudeClient:
    """`generate_json` only — the router's one call shape (plus, for `ClaudeOnly`, images and a
    shorter deadline)."""

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
        images: Sequence[Tuple[bytes, str]] = (),
        deadline_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        # Every request check first (pure): a refused image or deadline costs nothing, needs no key.
        blocks = image_blocks(images, usage_tag=usage_tag)
        deadline = call_deadline(deadline_seconds, usage_tag=usage_tag)
        client = _get_client()
        wrapped = _is_array(json_schema)
        # With no image the message is today's plain string, byte for byte; with images, the image
        # blocks come first and the text last (the order the vision docs use).
        content: Any = prompt if not blocks else [*blocks, {"type": "text", "text": prompt}]
        kwargs: Dict[str, Any] = {
            "model": model_name,
            "max_tokens": int(max_tokens or settings.CAYDEX_CLAUDE_MAX_TOKENS),
            "messages": [{"role": "user", "content": content}],
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

        try:
            response = await asyncio.wait_for(client.messages.create(**kwargs), timeout=deadline)
        except asyncio.TimeoutError:
            raise ClaudeTimeoutException(
                f"Claude call ran past its {deadline:g}s deadline (model={model_name}, "
                f"tag={usage_tag}, images={len(blocks)})") from None
        except anthropic.AnthropicError as e:
            raise _translate(e) from e

        usage = _usage_tokens(response)
        stop = getattr(response, "stop_reason", None)
        served = getattr(response, "model", None) or model_name
        # The image count rides at the END and only on a call that sent images, so every other
        # call's line is exactly today's.
        logger.info(
            "CLAUDE_USAGE tag=%s model=%s input=%d output=%d cache_read=%d cache_write=%d "
            "total=%d stop=%s%s", usage_tag or "-", served, usage["input"], usage["output"],
            usage["cache_read"], usage["cache_write"], usage["total"], stop,
            f" images={len(blocks)}" if blocks else "",
        )
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            raise ClaudeRefusalException(
                f"Claude declined (category={category}, model={served}, tag={usage_tag})",
                **_billed(usage, served))
        if stop == "max_tokens":
            raise ClaudeTruncatedException(
                f"Claude answer cut at max_tokens (model={served}, tag={usage_tag})",
                **_billed(usage, served))
        if stop not in ("end_turn", "stop_sequence"):
            raise ClaudeBadAnswerException(
                f"unexpected stop_reason={stop} (model={served}, tag={usage_tag})",
                **_billed(usage, served))
        texts = [b.text for b in (getattr(response, "content", None) or [])
                 if getattr(b, "type", None) == "text" and isinstance(getattr(b, "text", None), str)]
        text = "".join(texts)
        if not text.strip():
            raise ClaudeBadAnswerException(
                f"no text in the answer (model={served}, tag={usage_tag})",
                **_billed(usage, served))
        return {
            "text": _unwrap(text) if wrapped else text,
            "model": served,
            "tokens_used": usage["total"],
            "finish_reason": "STOP",
            "usage": {k: usage[k] for k in USAGE_KINDS},
        }


_instance: Optional[ClaudeClient] = None


def get_claude_client() -> ClaudeClient:
    global _instance
    if _instance is None:
        _instance = ClaudeClient()
    return _instance
