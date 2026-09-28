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
    """Process-local: open for a cooldown after a 429. Never shared with Gemini's breaker."""

    def __init__(self) -> None:
        self._open_until = 0.0

    @property
    def tripped(self) -> bool:
        return time.monotonic() < self._open_until

    def trip(self, seconds: float) -> None:
        self._open_until = max(self._open_until, time.monotonic() + max(1.0, seconds))

    def reset(self) -> None:
        self._open_until = 0.0


quota_breaker = _QuotaBreaker()


def _retry_after(response: httpx.Response) -> Optional[float]:
    raw = response.headers.get("retry-after")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


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
    ) -> None:
        if not base_url or not api_key:
            raise OpenAICompatError("openai_compat: base_url and api_key are required")
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._extra_body = dict(extra_body or {})
        self._json_mode = json_mode if json_mode in ("json_object", "json_schema") else "json_object"

    def __repr__(self) -> str:  # never print the key
        return f"OpenAICompatClient(url={self._url!r}, json_mode={self._json_mode!r})"

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
        body: Dict[str, Any] = {"model": model, "messages": messages}
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
        if temperature is not None:
            body["temperature"] = float(temperature)
        if max_tokens is not None:
            body["max_tokens"] = int(max_tokens)
        # Provider-specific switches (e.g. DeepSeek {"thinking": {"type": "disabled"}}, OpenAI
        # {"reasoning_effort": "minimal"}) come from settings, never from code.
        body.update(self._extra_body)

        last: Optional[BaseException] = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return await self._post(body, model=model, usage_tag=usage_tag)
            except OpenAICompatQuotaError:
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
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise OpenAICompatTransientError(f"{type(e).__name__}: {e}") from e

        if response.status_code == 429:
            retry_after = _retry_after(response)
            quota_breaker.trip(retry_after or _QUOTA_COOLDOWN_SECONDS)
            raise OpenAICompatQuotaError("openai_compat HTTP 429", retry_after=retry_after)
        if response.status_code >= 500:
            raise OpenAICompatTransientError(f"openai_compat HTTP {response.status_code}")
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
    return value
