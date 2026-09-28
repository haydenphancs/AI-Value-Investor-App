"""
OpenAI-compatible chat-completions client — a thin JSON-generation wrapper.

Used ONLY by the news features' provider switch (`app/services/news_llm.py`): per-article
sentiment + summary bullets and the sentiment backfill. It speaks the de-facto standard
`POST {base_url}/chat/completions` that DeepSeek, OpenAI, Qwen (DashScope compatible mode),
Groq, Together and OpenRouter all accept, so moving the news features between them is a
settings change (`NEWS_LLM_*`), not code.

Integration-layer rules (.claude/rules/integrations.md): HTTP in, dict out; typed exceptions;
a lazy module-level `httpx.AsyncClient` closed in the app lifespan; no caching, no business
decisions. Returns the SAME dict shape as `GeminiClient.generate_json` —
``{"text", "model", "tokens_used", "finish_reason"}`` — so callers parse one shape.

Deliberately separate from Gemini's failure handling: it has its OWN quota breaker, so a
429 from this provider can never fail-fast chat or reports (which stay on Gemini), and a
Gemini outage never stops this provider.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_MAX_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 2.0
#: After a 429 the breaker refuses calls for this long (or the provider's Retry-After).
_QUOTA_COOLDOWN_SECONDS = 60.0
#: An exhausted account (OpenAI `insufficient_quota`, a billing limit) does not clear in a
#: minute: back off longer, and treat it as a failure to fix, not a blip to wait out.
_BILLING_COOLDOWN_SECONDS = 900.0
#: This many 429 trips in a row with no success between them is an outage, not load: ERROR
#: once (a Sentry event), latched until the next success.
_ALERT_AFTER_TRIPS = 3
#: A total deadline per call. httpx's timeout bounds each READ, not the request, and a busy
#: DeepSeek keeps a non-streaming request open with keep-alive blank lines for up to 30 min.
_DEFAULT_DEADLINE_SECONDS = 120.0
_MODERATION_CODES = ("content_filter", "data_inspection_failed", "content_policy_violation")
_MODERATION_PHRASES = ("content exists risk", "content_policy_violation", "flagged by the content")
_BILLING_CODES = ("insufficient_quota", "billing_hard_limit_reached", "billing_not_active",
                  "insufficient_balance", "arrearage")
#: Request fields the client owns. NEWS_LLM_EXTRA_BODY may not replace them: a "model" there
#: would make the audit column lie, "temperature" would undo the backfill's 0, "stream" would
#: turn every answer into SSE.
RESERVED_BODY_KEYS = frozenset({
    "model", "messages", "response_format", "stream", "n", "temperature",
    "max_tokens", "max_completion_tokens",
})

# OpenAI-style finish reasons → the Gemini vocabulary the callers already understand.
_FINISH = {
    "stop": "STOP",
    "length": "MAX_TOKENS",
    "content_filter": "SAFETY",
    "tool_calls": "STOP",
    "function_call": "STOP",
}


class OpenAICompatError(Exception):
    """A non-retryable failure (bad request, auth, malformed answer)."""


class OpenAICompatQuotaError(OpenAICompatError):
    """HTTP 429, or the breaker is open after one."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class OpenAICompatTransientError(OpenAICompatError):
    """5xx, timeout or connection failure — worth retrying later."""


class OpenAICompatTimeoutError(OpenAICompatTransientError):
    """The call ran past its total deadline, or a read/write timed out. Transient, but NOT
    retried in the same call: the provider was generating, and a retry bills it twice
    (Gemini's timeout budget is 0 retries for the same reason)."""


class OpenAICompatContentRejected(OpenAICompatError):
    """The provider's content moderation refused the prompt (HTTP 400 with a moderation
    code). Deterministic for that input, so callers treat it like Gemini's blocked prompt —
    an unusable answer for THAT batch — not as an outage and not as a failed run."""


def is_transient_openai_compat_error(exc: BaseException) -> bool:
    return isinstance(exc, (OpenAICompatQuotaError, OpenAICompatTransientError))


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _client


async def close_openai_compat_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class _QuotaBreaker:
    """Process-local: open for a cooldown after a 429. Never shared with Gemini's breaker.

    Each 429 is expected degradation (WARNING at the caller). A run of them with no success
    in between is an outage, so the breaker logs one ERROR — latched until a call succeeds —
    which is what reaches Sentry; without it the summaries could stop app-wide in silence.
    """

    def __init__(self) -> None:
        self._open_until = 0.0
        self._trips = 0
        self._alerted = False

    @property
    def tripped(self) -> bool:
        return time.monotonic() < self._open_until

    def trip(self, seconds: float, *, reason: str = "HTTP 429") -> None:
        # A 429 that lands while the breaker is already open is a SIBLING of the same burst
        # (a call already on the wire): it may extend the window, never count as a new trip —
        # else one rate-limit burst of 3-4 concurrent calls paged as an "outage".
        was_open = self.tripped
        self._open_until = max(self._open_until, time.monotonic() + max(1.0, seconds))
        if was_open:
            return
        self._trips += 1
        if self._trips >= _ALERT_AFTER_TRIPS and not self._alerted:
            self._alerted = True
            logger.error("openai_compat: %d quota refusals in a row (%s) — news summaries and "
                         "sentiment labels are paused; check the provider account", self._trips, reason)

    def record_success(self) -> None:
        self._trips = 0
        self._alerted = False

    def reset(self) -> None:
        self._open_until = 0.0
        self._trips = 0
        self._alerted = False


quota_breaker = _QuotaBreaker()


def _retry_after(response: httpx.Response) -> Optional[float]:
    raw = response.headers.get("retry-after")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _deadline_seconds() -> float:
    try:
        from app.config import settings

        value = float(getattr(settings, "NEWS_LLM_REQUEST_TIMEOUT_SECONDS", _DEFAULT_DEADLINE_SECONDS))
    except Exception:  # noqa: BLE001 — a bad setting must not remove the deadline
        return _DEFAULT_DEADLINE_SECONDS
    return value if value > 0 else _DEFAULT_DEADLINE_SECONDS


def _is_moderation_refusal(response: httpx.Response) -> bool:
    """HTTP 400 whose error names content moderation. Narrow on purpose: treating any 400 as
    "unusable answer" would turn a bad key, a wrong model or an unsupported option into
    silent, accepted gaps across every ticker."""
    if _error_code(response) in _MODERATION_CODES:
        return True
    try:
        text = response.text[:2000].lower()
    except Exception:  # noqa: BLE001
        return False
    return any(p in text for p in _MODERATION_PHRASES)


def _error_code(response: httpx.Response) -> str:
    """`error.code` (or `error.type`) of an OpenAI-style error body, lower-case; "" when absent.
    Bounded and never echoed whole: the body can repeat the request."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    err = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(err, dict):
        return ""
    for key in ("code", "type"):
        value = err.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()[:60]
    return ""


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class OpenAICompatClient:
    """One provider endpoint. Cheap to construct; the HTTP pool is module-level."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        extra_body: Optional[Dict[str, Any]] = None,
        json_mode: str = "json_object",
        max_tokens_field: str = "max_tokens",
        send_temperature: bool = True,
    ) -> None:
        if not base_url or not api_key:
            raise OpenAICompatError("openai_compat: base_url and api_key are required")
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._extra_body = {k: v for k, v in (extra_body or {}).items() if k not in RESERVED_BODY_KEYS}
        self._json_mode = json_mode if json_mode in ("json_object", "json_schema") else "json_object"
        self._max_tokens_field = (
            max_tokens_field if max_tokens_field in ("max_tokens", "max_completion_tokens") else "max_tokens"
        )
        # OpenAI's reasoning models (gpt-5, o-series) refuse any temperature but their own:
        # NEWS_LLM_SEND_TEMPERATURE=false omits it (the key stays reserved in EXTRA_BODY).
        self._send_temperature = bool(send_temperature)

    def __repr__(self) -> str:  # never print the key
        return f"OpenAICompatClient(url={self._url!r}, json_mode={self._json_mode!r})"

    def _scrub(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text

    async def generate_json(
        self,
        *,
        prompt: str,
        model: str,
        system_instruction: Optional[str] = None,
        json_schema: Optional[Dict[str, Any]] = None,
        temperature: Optional[float] = None,
        usage_tag: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """One JSON answer. Raises the typed errors above; never returns a partial dict."""
        if quota_breaker.tripped:
            raise OpenAICompatQuotaError("openai_compat quota breaker is open")

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})
        # Provider-specific switches (e.g. DeepSeek {"thinking": {"type": "disabled"}}, OpenAI
        # {"reasoning_effort": "minimal"}) come from settings, never from code — FIRST, so the
        # fields this client owns are always the ones sent.
        body: Dict[str, Any] = dict(self._extra_body)
        body["model"] = model
        body["messages"] = messages
        if self._json_mode == "json_schema" and json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "news_output", "schema": json_schema, "strict": False},
            }
        else:
            # json_object mode enforces "valid JSON" only; the SHAPE is described in the
            # prompt by the caller and validated by the caller. Some providers (DeepSeek)
            # also require the literal word "json" in the prompt — news_llm adds it.
            body["response_format"] = {"type": "json_object"}
        if temperature is not None and self._send_temperature:
            body["temperature"] = float(temperature)
        if max_tokens is not None:
            body[self._max_tokens_field] = int(max_tokens)

        deadline = _deadline_seconds()
        last: Optional[BaseException] = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return await asyncio.wait_for(
                    self._post(body, model=model, usage_tag=usage_tag), timeout=deadline,
                )
            except asyncio.TimeoutError as e:
                raise OpenAICompatTimeoutError(
                    f"openai_compat call exceeded its {deadline:.0f}s deadline"
                ) from e
            except (OpenAICompatQuotaError, OpenAICompatTimeoutError):
                raise
            except OpenAICompatTransientError as e:
                last = e
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS * attempt)
        assert last is not None
        raise last

    async def _post(self, body: Dict[str, Any], *, model: str, usage_tag: Optional[str]) -> Dict[str, Any]:
        try:
            response = await _get_client().post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json=body,
            )
        except (httpx.UnsupportedProtocol, httpx.LocalProtocolError, httpx.ProxyError, httpx.InvalidURL) as e:
            # A setup mistake (no scheme on NEWS_LLM_BASE_URL, a key with a stray newline),
            # not a blip — these subclass TransportError, and as "transient" they retried
            # forever at WARNING. `from None` and the type name ONLY: h11's message for a bad
            # header repeats the whole Authorization value, i.e. the API key.
            raise OpenAICompatError(
                f"openai_compat: the request could not be sent ({type(e).__name__}) — check "
                "NEWS_LLM_BASE_URL / NEWS_LLM_API_KEY"
            ) from None
        except (httpx.ReadTimeout, httpx.WriteTimeout) as e:
            raise OpenAICompatTimeoutError(f"openai_compat {type(e).__name__}") from e
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise OpenAICompatTransientError(f"{type(e).__name__}: {self._scrub(str(e))[:200]}") from e

        if response.status_code == 429:
            code = _error_code(response)
            if code in _BILLING_CODES:
                # The account is out of credit: not transient, so each caller logs it as a
                # failure (ERROR) instead of waiting it out at WARNING forever.
                quota_breaker.trip(_BILLING_COOLDOWN_SECONDS, reason=code)
                raise OpenAICompatError(f"openai_compat HTTP 429 {code} — the provider account "
                                        "has no quota left")
            retry_after = _retry_after(response)
            quota_breaker.trip(retry_after or _QUOTA_COOLDOWN_SECONDS, reason=code or "HTTP 429")
            raise OpenAICompatQuotaError(f"openai_compat HTTP 429{f' {code}' if code else ''}",
                                         retry_after=retry_after)
        if response.status_code >= 500:
            raise OpenAICompatTransientError(f"openai_compat HTTP {response.status_code}")
        if response.status_code == 402 or (
            400 <= response.status_code < 500 and _error_code(response) in _BILLING_CODES
        ):
            # An exhausted account outside a 429: DeepSeek answers 402 "Insufficient Balance",
            # DashScope a 400 "Arrearage". Same handling as the 429 billing case.
            code = _error_code(response) or f"HTTP {response.status_code}"
            quota_breaker.trip(_BILLING_COOLDOWN_SECONDS, reason=code)
            raise OpenAICompatError(f"openai_compat HTTP {response.status_code} {code} — the provider "
                                    "account has no balance left")
        if response.status_code == 400 and _is_moderation_refusal(response):
            raise OpenAICompatContentRejected("openai_compat HTTP 400: the prompt was refused by "
                                              "the provider's content moderation")
        if response.status_code >= 400:
            # The body can echo the request; keep it short and never include headers.
            raise OpenAICompatError(
                f"openai_compat HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            payload = response.json()
            choice = payload["choices"][0]
            text = choice["message"].get("content") or ""
            raw_finish = str(choice.get("finish_reason") or "")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            raise OpenAICompatError(f"openai_compat: malformed response ({type(e).__name__})") from e

        quota_breaker.record_success()
        usage = payload.get("usage") if isinstance(payload, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        prompt_tokens = _int(usage.get("prompt_tokens"))
        output_tokens = _int(usage.get("completion_tokens"))
        cached_tokens = _int(usage.get("prompt_cache_hit_tokens"))
        if cached_tokens is None:
            details = usage.get("prompt_tokens_details")
            cached_tokens = _int(details.get("cached_tokens")) if isinstance(details, dict) else None
        total = _int(usage.get("total_tokens"))
        # One greppable line per call, the provider-neutral twin of GEMINI_USAGE.
        logger.info(
            "LLM_USAGE provider=openai_compat model=%s call_site=generate_json tag=%s "
            "prompt=%s cached=%s output=%s total=%s finish=%s",
            model, usage_tag, prompt_tokens, cached_tokens, output_tokens, total, raw_finish,
        )
        return {
            "text": text,
            "model": model,
            "tokens_used": total,
            "finish_reason": _FINISH.get(raw_finish.lower(), raw_finish.upper() or "STOP"),
        }


def parse_extra_body(raw: Optional[str]) -> Dict[str, Any]:
    """`NEWS_LLM_EXTRA_BODY` is a JSON object string; anything else is ignored, loudly."""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        logger.error("NEWS_LLM_EXTRA_BODY is not valid JSON — ignored")
        return {}
    if not isinstance(value, dict):
        logger.error("NEWS_LLM_EXTRA_BODY must be a JSON object — ignored")
        return {}
    reserved = sorted(k for k in value if k in RESERVED_BODY_KEYS)
    if reserved:
        logger.error("NEWS_LLM_EXTRA_BODY may not set %s — those keys are ignored "
                     "(use NEWS_LLM_MODEL / NEWS_LLM_MAX_TOKENS instead)", ", ".join(reserved))
    return {k: v for k, v in value.items() if k not in RESERVED_BODY_KEYS}
