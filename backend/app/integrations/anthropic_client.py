"""
Anthropic Claude Messages API — a thin JSON-generation wrapper.

Used ONLY through `app/services/llm_provider.py`, for marketing and Learn weekly work plus ONE
app feature's web lookup:
  * its provider router, for four roles — the marketing writer and judge, the Learn weekly
    writer and judge. Gemini stays the default and the fallback for every one of them;
  * its no-fallback `ClaudeOnly` path: the measurement scripts (judge calibration, the writer
    preview) and — owner decision 2026-11-15, "Google draws, Claude checks" — the marketing
    per-post ART: Claude writes the picture's brief and then inspects the drawn picture (vision,
    `images=`). Gemini draws it (`integrations/gemini.py::generate_image`); there is no Gemini
    fallback for the brief or the check, and art is optional (any failure = no art that day);
  * `ClaudeOnly.cited_web_search` → `ClaudeClient.cited_web_search`, the server-side web search
    tool, for the Updates Insights jump search's FALLBACK lookup only (owner 2026-11-27: "use
    Claude API (200 credits as fallback)", `services/jump_claude_lookup_service.py`, which ships
    OFF). It FINDS news pages; Claude's own prose is discarded and never reaches a card.
Nothing else in the app calls Claude (owner: Claude is for marketing, never app features — the
jump lookup above is the one, page-finding exception; `tests/test_claude_web_search_boundary.py`).

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
import re
from collections import Counter
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

#: The server-side web search tool `cited_web_search` sends (2026-11-27): the dynamic-filtering
#: version, which Sonnet 5.5 and Opus 5.5 take. Its built-in code execution is free with it.
WEB_SEARCH_TOOL_TYPE = "web_search_20260209"
WEB_SEARCH_TOOL_NAME = "web_search"
#: Searches one call may make (the API's `max_uses`). The caller's own cap is smaller; this only
#: bounds a caller's mistake.
MAX_WEB_SEARCH_USES = 10
#: The API takes 1-64 plain hostnames per domain list; more is refused here, before any I/O.
MAX_BLOCKED_DOMAINS = 64
#: A plain lowercase hostname with at least one dot: no scheme, port, path, wildcard or IP-only form.
_HOSTNAME_RE = re.compile(
    r"(?=[a-z0-9.-]{4,253}\Z)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
    r"\.[a-z][a-z0-9-]{0,61}[a-z0-9]")
#: The keys an approximate user location may carry (the SDK's UserLocationParam).
_USER_LOCATION_KEYS = frozenset({"type", "city", "country", "region", "timezone"})
#: A web search tool error code as it may be counted: short, lowercase, underscored.
_ERROR_CODE_RE = re.compile(r"[a-z_]{1,40}")


class ClaudeException(Exception):
    """Any Claude failure. `tokens_used` is what the failed call billed (0 when unknown); `usage` is
    the same tokens by kind (`USAGE_KINDS`) and `model` the model that billed them — None when the
    call never answered (a timeout, a transport or HTTP error).

    `billing_unknown` is True when the request may have reached the API, and been billed, without
    an answer being read — a timeout, a dropped connection. Its tokens are then unknown (0 / None
    above), so a caller holding a spend cap must count that call at its worst case."""

    #: Overridden per class (a timeout) or per instance (a connection failure, `_translate`).
    billing_unknown: bool = False
    #: The HTTP status of an API error (`_translate`), None for anything else — a caller that must
    #: tell a 401 (the key) from a 403 (a refused tool) reads it instead of parsing the message.
    status_code: Optional[int] = None
    #: Server-side web searches the call billed (`cited_web_search` sets it on the exceptions it
    #: raises after an answer: $10 per 1,000 on top of the tokens); None when the answer's usage
    #: could not be read. 0 for every other call.
    web_searches: Optional[int] = 0

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


def _log_usage(usage_tag: Optional[str], served: Any, usage: Dict[str, int], stop: Any, suffix: str) -> None:
    """The one CLAUDE_USAGE line per answered call. `suffix` rides at the END (" images=N" on a call
    that sent images, " web_searches=N" on `cited_web_search`), so every other line is unchanged."""
    logger.info(
        "CLAUDE_USAGE tag=%s model=%s input=%d output=%d cache_read=%d cache_write=%d "
        "total=%d stop=%s%s", usage_tag or "-", served, usage["input"], usage["output"],
        usage["cache_read"], usage["cache_write"], usage["total"], stop, suffix,
    )


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
    """An SDK error → our typed exception (`_translate_typed`), carrying the HTTP status of an API
    error as `status_code` (None for a timeout or a connection failure)."""
    out = _translate_typed(e)
    status = getattr(e, "status_code", None)
    out.status_code = status if isinstance(status, int) and not isinstance(status, bool) else None
    return out


def _translate_typed(e: Exception) -> ClaudeException:
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


def _value_shown(value: Any) -> str:
    """A refused argument as it may appear in a message: a number or a short printable string as
    is, anything else by its type."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    if isinstance(value, str) and len(value) <= 64 and value.isprintable():
        return repr(value)
    return f"<{type(value).__name__}>"


def web_search_tool(max_uses: Any, blocked_domains: Any = (), user_location: Any = None, *,
                    usage_tag: Optional[str] = None) -> Dict[str, Any]:
    """The `tools` entry of `cited_web_search`: the server-side web search tool. Pure: every check
    runs before any I/O and raises ClaudeRequestInvalidException — `max_uses` not a whole number
    from 1 to MAX_WEB_SEARCH_USES (a bool included); `blocked_domains` not a list or tuple (a bare
    str, a set — no order — a generator), over MAX_BLOCKED_DOMAINS, a repeat, or an entry that is not
    a plain lowercase hostname; a `user_location` that is not an approximate location of short
    strings. An empty domain list sends no `blocked_domains` key."""
    if isinstance(max_uses, bool) or not isinstance(max_uses, int) or not 1 <= max_uses <= MAX_WEB_SEARCH_USES:
        raise ClaudeRequestInvalidException(
            f"max_uses={_value_shown(max_uses)} is not a whole number from 1 to {MAX_WEB_SEARCH_USES} "
            f"(tag={usage_tag})")
    tool: Dict[str, Any] = {"type": WEB_SEARCH_TOOL_TYPE, "name": WEB_SEARCH_TOOL_NAME, "max_uses": max_uses}
    if blocked_domains is None:
        domains: List[str] = []
    elif isinstance(blocked_domains, (list, tuple)):
        domains = list(blocked_domains)
    else:
        raise ClaudeRequestInvalidException(
            f"blocked_domains must be a list or tuple of hostnames, not {type(blocked_domains).__name__} "
            f"(tag={usage_tag})")
    if len(domains) > MAX_BLOCKED_DOMAINS:
        raise ClaudeRequestInvalidException(
            f"{len(domains)} blocked domains — the API takes at most {MAX_BLOCKED_DOMAINS} (tag={usage_tag})")
    for i, domain in enumerate(domains):
        if not isinstance(domain, str) or not _HOSTNAME_RE.fullmatch(domain):
            raise ClaudeRequestInvalidException(
                f"blocked domain {i} ({_value_shown(domain)}) is not a plain lowercase hostname (tag={usage_tag})")
    if len(set(domains)) != len(domains):
        raise ClaudeRequestInvalidException(f"blocked_domains repeats a hostname (tag={usage_tag})")
    if domains:
        tool["blocked_domains"] = domains
    if user_location is not None:
        if (not isinstance(user_location, dict) or user_location.get("type") != "approximate"
                or set(user_location) - _USER_LOCATION_KEYS
                or any(not isinstance(v, str) or not v.strip() or len(v) > 64 for v in user_location.values())):
            raise ClaudeRequestInvalidException(
                f"user_location must be an approximate location of short strings ({sorted(_USER_LOCATION_KEYS)}) "
                f"(tag={usage_tag})")
        tool["user_location"] = dict(user_location)
    return tool


def _field(obj: Any, name: str) -> Any:
    """`obj.name` for an SDK model, `obj[name]` for a dict (the SDK builds an unknown shape as one)."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _web_search_answer(response: Any) -> Dict[str, Any]:
    """The parts of a web-search answer a caller may keep — Claude's text; each search RESULT as
    {url, title, page_age} (never its `encrypted_content`); each web CITATION as {url, title,
    cited_text} (never its `encrypted_index`); the search errors by code (an error object in place of
    the result list is counted, never raised); and `web_search_requests`, the billed searches from
    `usage.server_tool_use`, or None when that cannot be read on an answer that holds search results.
    Anything of another shape is skipped. Pure; holds web results, so `log_redaction` drops this
    frame's locals."""
    texts: List[str] = []
    results: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    errors: Counter = Counter()
    result_blocks = 0
    for block in (getattr(response, "content", None) or []):
        kind = _field(block, "type")
        if kind == "text":
            text = _field(block, "text")
            if isinstance(text, str):
                texts.append(text)
            cited = _field(block, "citations")
            for c in (cited if isinstance(cited, (list, tuple)) else ()):
                if _field(c, "type") != "web_search_result_location":
                    continue
                url, cited_text, title = _field(c, "url"), _field(c, "cited_text"), _field(c, "title")
                if isinstance(url, str) and isinstance(cited_text, str):
                    citations.append({"url": url, "title": title if isinstance(title, str) else None,
                                      "cited_text": cited_text})
        elif kind == "web_search_tool_result":
            result_blocks += 1
            content = _field(block, "content")
            if isinstance(content, (list, tuple)):
                for item in content:
                    if _field(item, "type") != "web_search_result":
                        continue
                    url, title, age = _field(item, "url"), _field(item, "title"), _field(item, "page_age")
                    if isinstance(url, str) and isinstance(title, str):
                        results.append({"url": url, "title": title,
                                        "page_age": age if isinstance(age, str) else None})
            else:
                code = _field(content, "error_code")
                errors[code if isinstance(code, str) and _ERROR_CODE_RE.fullmatch(code) else "unknown"] += 1
    server = _field(getattr(response, "usage", None), "server_tool_use")
    searches = _field(server, "web_search_requests")
    if isinstance(searches, bool) or not isinstance(searches, int) or searches < 0:
        searches = None if result_blocks else 0
    return {"text": "".join(texts), "results": results, "citations": citations,
            "search_errors": dict(errors), "web_search_requests": searches}


def _search_failure(cls: type, message: str, usage: Dict[str, int], served: str,
                    searches: Optional[int]) -> ClaudeException:
    """An abnormal web-search answer's exception: billed (tokens by kind, the model) and carrying its
    searches, so a spend cap prices the failed call in full."""
    exc = cls(message, **_billed(usage, served))
    exc.web_searches = searches
    return exc


class ClaudeClient:
    """`generate_json` — the router's one call shape (plus, for `ClaudeOnly`, images and a shorter
    deadline) — and `cited_web_search`, the jump search's fallback lookup (2026-11-27)."""

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
        _log_usage(usage_tag, served, usage, stop, f" images={len(blocks)}" if blocks else "")
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

    async def cited_web_search(
        self,
        prompt: str,
        *,
        model_name: str,
        system_instruction: Optional[str],
        max_uses: int,
        max_tokens: int,
        effort: Optional[str],
        thinking: Optional[Dict[str, Any]],
        blocked_domains: Sequence[str] = (),
        user_location: Optional[Dict[str, str]] = None,
        usage_tag: Optional[str],
        deadline_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """ONE call with the server-side web search tool (WEB_SEARCH_TOOL_TYPE), answered in plain
        text with citations (2026-11-27, the Updates jump search's fallback lookup).

        Sends: the prompt, the system instruction, `thinking` and `effort` as given, and ONE tool —
        `max_uses`, `blocked_domains` (omitted when empty) and `user_location` (omitted when None).
        Never temperature/top_p/top_k (API version 2026-10-21), no `tool_choice` (a forced tool is a
        400 on Sonnet 5.5 and Opus 5.5), no `output_config.format` (citations and structured outputs
        do not mix), no `fallbacks`, no cache. One attempt under the total deadline (`call_deadline`).

        Returns {"text", "model", "tokens_used", "usage", "web_search_requests", "results",
        "citations", "search_errors", "stop_reason"} (`_web_search_answer`; `web_search_requests` is
        None when the answer's usage could not be read). A refusal, a cut answer (max_tokens), a
        paused turn (pause_turn — never resumed, so the cost stays bounded) or any other stop reason
        raises its typed exception, billed and carrying `web_searches`. An empty text is returned,
        not raised: the caller keeps only cited pages, and none means none."""
        tool = web_search_tool(max_uses, blocked_domains, user_location, usage_tag=usage_tag)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ClaudeRequestInvalidException(f"the web search prompt is empty (tag={usage_tag})")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 32000:
            raise ClaudeRequestInvalidException(
                f"max_tokens={_value_shown(max_tokens)} is not a whole number from 1 to 32000 (tag={usage_tag})")
        deadline = call_deadline(deadline_seconds, usage_tag=usage_tag)
        client = _get_client()
        kwargs: Dict[str, Any] = {
            "model": model_name,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [tool],
        }
        if system_instruction:
            kwargs["system"] = system_instruction
        if thinking is not None:
            kwargs["thinking"] = thinking
        if effort:
            kwargs["output_config"] = {"effort": effort}

        try:
            response = await asyncio.wait_for(client.messages.create(**kwargs), timeout=deadline)
        except asyncio.TimeoutError:
            raise ClaudeTimeoutException(
                f"Claude web search call ran past its {deadline:g}s deadline (model={model_name}, "
                f"tag={usage_tag})") from None
        except anthropic.AnthropicError as e:
            raise _translate(e) from e

        usage = _usage_tokens(response)
        stop = getattr(response, "stop_reason", None)
        served = getattr(response, "model", None) or model_name
        answer = _web_search_answer(response)
        searches = answer["web_search_requests"]
        # The search count rides at the END and only on this call: every other line is unchanged.
        _log_usage(usage_tag, served, usage, stop,
                   f" web_searches={searches if searches is not None else '?'}")
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            raise _search_failure(
                ClaudeRefusalException,
                f"Claude declined the web search (category={getattr(details, 'category', None)}, "
                f"model={served}, tag={usage_tag})", usage, served, searches)
        if stop == "max_tokens":
            raise _search_failure(
                ClaudeTruncatedException,
                f"Claude web search answer cut at max_tokens (model={served}, tag={usage_tag})",
                usage, served, searches)
        if stop not in ("end_turn", "stop_sequence"):
            # pause_turn included: resuming the server's loop would spend more searches and tokens
            # than the caller's pre-charged worst case.
            raise _search_failure(
                ClaudeBadAnswerException,
                f"unexpected stop_reason={stop} on a web search (model={served}, tag={usage_tag})",
                usage, served, searches)
        return {
            "text": answer["text"],
            "model": served,
            "tokens_used": usage["total"],
            "usage": {k: usage[k] for k in USAGE_KINDS},
            "web_search_requests": searches,
            "results": answer["results"],
            "citations": answer["citations"],
            "search_errors": answer["search_errors"],
            "stop_reason": stop,
        }


_instance: Optional[ClaudeClient] = None


def get_claude_client() -> ClaudeClient:
    global _instance
    if _instance is None:
        _instance = ClaudeClient()
    return _instance
