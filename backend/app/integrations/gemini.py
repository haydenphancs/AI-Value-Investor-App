"""
Google Gemini API Integration
Handles all interactions with Google Gemini for AI features.
Requirements: Section 3.3, 4.3.1 - Google Gemini API for deep research

Uses the unified `google-genai` SDK (async-native via `client.aio.*`). The
`GeminiClient` public method signatures are frozen — the 12 services that call
`get_gemini_client()` are unaffected by the SDK swap.
"""

from typing import Optional, List, Dict, Any, Callable
import logging
import asyncio
import hashlib
import json
import re
import time
from functools import wraps

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

# ── Quota-error detection ──────────────────────────────────────────
_QUOTA_ERROR_STRINGS = ("429", "resource_exhausted", "quota", "rate limit")


def _is_quota_error(exc: Exception) -> bool:
    """Return True if the exception looks like a quota/rate-limit error."""
    msg = str(exc).lower()
    return any(s in msg for s in _QUOTA_ERROR_STRINGS)


class GeminiQuotaError(Exception):
    """Raised by the circuit breaker when it's open (fail-fast).

    The message intentionally contains "quota"/"resource_exhausted" so both
    `_is_quota_error` and the API error classifier
    (`app.api.error_response.classify_exception`) recognize it and route to the
    GEMINI_QUOTA_EXCEEDED contract — and so the caller's existing sentinel
    fallback (e.g. narrative jobs) fires instead of propagating a raw error.
    """


class _QuotaCircuitBreaker:
    """Process-wide breaker that stops hammering Gemini during a sustained
    quota outage.

    Without it, under load every one of the ~15 parallel narrative calls (per
    report, across every concurrent report) would each burn its full backoff
    ladder against an API that is already returning 429 — adding load and
    latency for nothing. After `GEMINI_QUOTA_CIRCUIT_THRESHOLD` *consecutive*
    quota errors the breaker opens and `is_open()` returns True for
    `GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS`; calls then fail fast (the caller's
    sentinel fallback applies). Any success resets it. A single half-open trial
    is allowed once the cooldown elapses.

    Single-event-loop process → no lock needed (all access is on one thread).
    """

    def __init__(self) -> None:
        self._consecutive = 0
        self._opened_at = 0.0

    def is_open(self) -> bool:
        if self._opened_at <= 0.0:
            return False
        if time.time() - self._opened_at >= settings.GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS:
            # Cooldown elapsed → half-open: clear state and allow one trial.
            self._opened_at = 0.0
            self._consecutive = 0
            return False
        return True

    def record_quota_error(self) -> None:
        self._consecutive += 1
        if self._consecutive >= settings.GEMINI_QUOTA_CIRCUIT_THRESHOLD:
            # Stamp the open time ONLY on the closed→open transition. Setting it
            # unconditionally would let every straggler 429 (the ~15 parallel
            # calls already past the is_open() check) push the deadline forward,
            # holding the breaker open well beyond the configured cooldown.
            if self._opened_at <= 0.0:
                logger.error(
                    "Gemini quota circuit OPEN after %d consecutive quota "
                    "errors — failing fast for %.0fs",
                    self._consecutive,
                    settings.GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS,
                )
                self._opened_at = time.time()

    def record_success(self) -> None:
        self._consecutive = 0
        self._opened_at = 0.0


# Module-level breaker shared by every decorated Gemini call.
_quota_circuit = _QuotaCircuitBreaker()


# ── Per-call timeout guard ─────────────────────────────────────────
async def _call_with_timeout(coro):
    """Await a Gemini coroutine with a hard timeout.

    The unified SDK is async-native (`client.aio.*` returns coroutines), so this
    just wraps the coroutine in `asyncio.wait_for` — no more thread offload. A
    hung network read would otherwise park the whole report-generation task
    forever (seen as a report card stuck at "synthesizing..." at 55%).

    On timeout, raises asyncio.TimeoutError — `@async_retry` skips it (not a
    quota error), and the caller's existing exception handler returns its
    sentinel fallback instead of hanging.

    Timeout sourced from settings.GEMINI_REQUEST_TIMEOUT_SECONDS.
    """
    return await asyncio.wait_for(
        coro, timeout=settings.GEMINI_REQUEST_TIMEOUT_SECONDS
    )


def async_retry(max_attempts: int = 3, delay: float = 1.0):
    """
    Decorator for retrying async functions on failure.

    Two independent retry budgets:
      * Generic errors → up to `max_attempts` tries, linear backoff `delay*n`.
      * Quota/rate-limit (429) errors → up to GEMINI_QUOTA_MAX_RETRIES tries
        with GEMINI_QUOTA_RETRY_DELAY_SECONDS*n backoff. Previously these were
        NOT retried (immediate raise → sentinel narrative); under the
        agent-run semaphore a short backoff recovers transient 429s so the
        report keeps its real prose. The shared `_quota_circuit` short-circuits
        once quota errors are sustained, so retries never pile onto an outage.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            attempt = 0          # generic failures
            quota_attempt = 0    # quota/429 failures
            while True:
                # Fail fast while the breaker is open — don't add load to an
                # already-exhausted quota; the caller's sentinel fallback fires.
                if _quota_circuit.is_open():
                    raise GeminiQuotaError(
                        "Gemini quota circuit open (resource_exhausted) — "
                        "failing fast"
                    )
                try:
                    result = await func(*args, **kwargs)
                    _quota_circuit.record_success()
                    return result
                except Exception as e:
                    if _is_quota_error(e):
                        _quota_circuit.record_quota_error()
                        quota_attempt += 1
                        if (
                            quota_attempt > settings.GEMINI_QUOTA_MAX_RETRIES
                            or _quota_circuit.is_open()
                        ):
                            logger.error(
                                f"Quota/rate-limit error — giving up after "
                                f"{quota_attempt} attempt(s): {e}"
                            )
                            raise
                        backoff = (
                            settings.GEMINI_QUOTA_RETRY_DELAY_SECONDS
                            * quota_attempt
                        )
                        logger.warning(
                            f"Quota/rate-limit (attempt {quota_attempt}/"
                            f"{settings.GEMINI_QUOTA_MAX_RETRIES}) — backing "
                            f"off {backoff:.1f}s: {e}"
                        )
                        await asyncio.sleep(backoff)
                        continue
                    attempt += 1
                    if attempt >= max_attempts:
                        raise
                    logger.warning(
                        f"Attempt {attempt} failed: {e}. Retrying..."
                    )
                    await asyncio.sleep(delay * attempt)
        return wrapper
    return decorator


# ── In-memory LRU cache with TTL ──────────────────────────────────

class _TTLCache:
    """Simple in-memory cache with max-size eviction and TTL expiry."""

    def __init__(self, max_size: int = 128, ttl_seconds: int = 3600):
        self._store: Dict[str, Any] = {}
        self._timestamps: Dict[str, float] = {}
        self._max_size = max_size
        self._ttl = ttl_seconds

    def get(self, key: str) -> Any:
        if key in self._store:
            if time.time() - self._timestamps[key] < self._ttl:
                return self._store[key]
            # Expired
            del self._store[key]
            del self._timestamps[key]
        return None

    def set(self, key: str, value: Any):
        # Evict oldest if full
        if len(self._store) >= self._max_size and key not in self._store:
            oldest = min(self._timestamps, key=self._timestamps.get)
            del self._store[oldest]
            del self._timestamps[oldest]
        self._store[key] = value
        self._timestamps[key] = time.time()

    @property
    def size(self) -> int:
        return len(self._store)


def _cache_key(*parts: str) -> str:
    """Build a deterministic cache key from string parts."""
    raw = "|".join(str(p) for p in parts if p)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ── Response accessors (defensive; the SDK's .text raises on no-text parts) ──

def _iter_parts(response: Any) -> List[Any]:
    """Parts of the first candidate — works for a full response OR a streaming chunk.
    The unified SDK has no top-level `.parts`; they live under candidates[0].content.parts."""
    try:
        cand = (response.candidates or [None])[0]
        if cand and cand.content and cand.content.parts:
            return list(cand.content.parts)
    except (AttributeError, TypeError, IndexError):
        pass
    return []


def _response_text(response: Any) -> str:
    """Safe `.text` — the SDK property raises ValueError when the candidate has
    no text Part (function-call-only / finish-only). Falls back to walking parts.
    Skips thought parts so real reasoning never leaks into the answer text."""
    try:
        return response.text or ""
    except (ValueError, AttributeError):
        pass
    chunks: List[str] = []
    for p in _iter_parts(response):
        if getattr(p, "thought", False):
            continue
        try:
            t = p.text
        except (ValueError, AttributeError):
            continue
        if t:
            chunks.append(t)
    return "\n".join(chunks)


def _response_tokens(response: Any) -> Optional[int]:
    um = getattr(response, "usage_metadata", None)
    return getattr(um, "total_token_count", None) if um else None


def _response_finish(response: Any) -> Optional[str]:
    try:
        cand = (response.candidates or [None])[0]
        fr = getattr(cand, "finish_reason", None) if cand else None
        return getattr(fr, "name", fr) if fr is not None else None
    except Exception:
        return None


class GeminiClient:
    """Client for Google Gemini API (unified google-genai SDK)."""

    def __init__(self):
        """Initialize Gemini client with API key from settings."""
        # An HTTP-level timeout bounds every call (including streams — a stalled
        # read can't park forever); the async _call_with_timeout adds an app-level
        # bound on non-streaming calls. HttpOptions.timeout is in milliseconds.
        self._client = genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options=types.HttpOptions(
                timeout=int(settings.GEMINI_REQUEST_TIMEOUT_SECONDS * 1000)
            ),
        )
        self.model_name = settings.GEMINI_MODEL
        self._temperature = settings.GEMINI_TEMPERATURE
        self._max_tokens = settings.GEMINI_MAX_TOKENS
        cache_ttl = getattr(settings, "GEMINI_CACHE_TTL", 3600)
        self._response_cache = _TTLCache(max_size=256, ttl_seconds=cache_ttl)
        self._embedding_cache = _TTLCache(max_size=512, ttl_seconds=cache_ttl)

    def _config(
        self,
        *,
        system_instruction: Optional[str] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        tools: Optional[List[Any]] = None,
        response_mime_type: Optional[str] = None,
        response_schema: Optional[Any] = None,
        cached_content: Optional[str] = None,
        thinking_config: Optional[Any] = None,
    ) -> types.GenerateContentConfig:
        """Assemble a GenerateContentConfig from the knobs that used to live in
        the legacy generation_config dict + per-call GenerativeModel kwargs."""
        kwargs: Dict[str, Any] = {
            "temperature": self._temperature if temperature is None else temperature,
            "max_output_tokens": self._max_tokens if max_output_tokens is None else max_output_tokens,
        }
        if system_instruction:
            kwargs["system_instruction"] = system_instruction
        if tools:
            kwargs["tools"] = list(tools)
        if response_mime_type:
            kwargs["response_mime_type"] = response_mime_type
        if response_schema is not None:
            kwargs["response_schema"] = response_schema
        if cached_content:
            kwargs["cached_content"] = cached_content
        if thinking_config is not None:
            kwargs["thinking_config"] = thinking_config
        return types.GenerateContentConfig(**kwargs)

    @async_retry(max_attempts=2, delay=2.0)
    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate text using Gemini.  Results are cached by (prompt, system_instruction)
        for GEMINI_CACHE_TTL seconds to avoid duplicate API calls.
        """
        key = _cache_key(prompt, system_instruction or "", model_name or "")
        cached = self._response_cache.get(key)
        if cached is not None:
            logger.debug("Gemini generate_text cache HIT")
            return cached

        try:
            response = await _call_with_timeout(
                self._client.aio.models.generate_content(
                    model=model_name or self.model_name,
                    contents=prompt,
                    config=self._config(system_instruction=system_instruction),
                )
            )
            result = {
                "text": _response_text(response),
                "model": self.model_name,
                "tokens_used": _response_tokens(response),
                "finish_reason": _response_finish(response),
            }
            self._response_cache.set(key, result)
            return result
        except Exception as e:
            logger.error(f"Gemini text generation failed: {e}", exc_info=True)
            raise

    # ── Streaming text (SSE chat) ─────────────────────────────────────
    # NOT decorated with @async_retry — retrying a partial stream would replay
    # already-emitted tokens. We honor the quota circuit breaker manually
    # (fail-fast if open; record quota errors/success) and let the caller's SSE
    # endpoint emit an `error` event + fall back. The unified SDK streams
    # natively (`aio.models.generate_content_stream`) — no thread bridge.
    async def stream_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        """Yield ``(kind, text)`` chunks as Gemini generates.

        `kind` is "thought" (real reasoning summary → the thinking card) or "answer"
        (→ the message bubble). Thinking is requested via ThinkingConfig(include_thoughts=True);
        each streamed part carries a `.thought` flag we branch on — no more prompt-hack
        separator. Raises immediately if the quota circuit is open; propagates the first SDK
        error so the caller can surface an `error` event; the client HTTP timeout guards a hung read.
        """
        if _quota_circuit.is_open():
            raise GeminiQuotaError(
                "Gemini quota circuit open (resource_exhausted) — failing fast"
            )
        config = self._config(
            system_instruction=system_instruction,
            thinking_config=types.ThinkingConfig(include_thoughts=True),
        )
        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=model_name or self.model_name,
                contents=prompt,
                config=config,
            )
            async for chunk in stream:
                for part in _iter_parts(chunk):
                    # part.text raises on non-text parts (finish-only) — treat as empty.
                    try:
                        text = part.text or ""
                    except (ValueError, AttributeError):
                        text = ""
                    if not text:
                        continue
                    yield ("thought" if getattr(part, "thought", False) else "answer"), text
            _quota_circuit.record_success()
        except Exception as e:
            if _is_quota_error(e):
                _quota_circuit.record_quota_error()
            raise

    # ── Context caching (Stage-B narratives) ──────────────────────────
    # The N parallel narrative calls per report share one large evidence blob +
    # persona system prompt. Uploading that shared prefix to a CachedContent
    # once and pointing every call at it (config.cached_content) bills the prefix
    # ~1x (write) + N×25% (cache reads) instead of N×100%. All three methods are
    # FAIL-SAFE: a below-min-size / quota / hung-SDK condition degrades to the
    # inline path (create_* returns None) so report quality is never sacrificed.

    async def create_narrative_cache(
        self,
        system_instruction: Optional[str],
        evidence: str,
        ttl_minutes: Optional[int] = None,
    ) -> Optional[Any]:
        """Create a Gemini CachedContent for the shared (system prompt +
        evidence) prefix. Returns an opaque handle ``{"cache": <CachedContent>}``
        or None on ANY failure (caller falls back to inline prompts). Never raises.

        Unlike the legacy SDK there is no cache-bound model object — callers pass
        ``config.cached_content = cache.name`` per request (see generate_text_cached).
        """
        if not evidence:
            return None
        try:
            ttl = ttl_minutes if ttl_minutes is not None else getattr(
                settings, "GEMINI_CONTEXT_CACHE_TTL_MINUTES", 10
            )
            model_name = (
                self.model_name
                if self.model_name.startswith("models/")
                else f"models/{self.model_name}"
            )
            # Through _call_with_timeout so a hung SDK create can't park the agent
            # run for the full 600s pipeline ceiling — TimeoutError → None → inline.
            cache = await _call_with_timeout(
                self._client.aio.caches.create(
                    model=model_name,
                    config=types.CreateCachedContentConfig(
                        system_instruction=system_instruction or None,
                        contents=[f"FINANCIAL EVIDENCE:\n{evidence}"],
                        ttl=f"{int(ttl) * 60}s",
                    ),
                )
            )
            logger.info("Gemini context cache created (ttl=%dm)", ttl)
            return {"cache": cache}
        except Exception as e:
            # Below-min-token (2.5 Flash min ~1024), quota, or hung → inline.
            logger.info(
                "Gemini context cache unavailable (%s: %s) — using inline prompts",
                type(e).__name__, e,
            )
            return None

    @async_retry(max_attempts=2, delay=2.0)
    async def generate_text_cached(
        self, prompt: str, handle: Dict[str, Any]
    ) -> Dict[str, Any]:
        """generate_text variant that runs against a CachedContent prefix.

        The shared evidence + system instruction live in the cache; `prompt` is
        only the per-field instruction. Same timeout + quota path as generate_text.
        """
        cache = handle["cache"]
        response = await _call_with_timeout(
            self._client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=self._config(cached_content=cache.name),
            )
        )
        return {
            "text": _response_text(response),
            "model": self.model_name,
            "tokens_used": _response_tokens(response),
            "finish_reason": _response_finish(response),
        }

    async def delete_cache(self, handle: Optional[Dict[str, Any]]) -> None:
        """Best-effort delete of a CachedContent so cache storage is freed
        before its TTL. Never raises (a failed delete just expires via TTL)."""
        if not handle:
            return
        try:
            cache = handle.get("cache")
            if cache is None:
                return
            await _call_with_timeout(
                self._client.aio.caches.delete(name=cache.name)
            )
        except Exception as e:
            logger.debug("Context cache delete failed (expires via TTL): %s", e)

    @async_retry(max_attempts=2, delay=2.0)
    async def generate_json(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model_name: Optional[str] = None,
        response_schema: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Generate structured JSON using Gemini with response_mime_type.
        Optionally enforce a response_schema for guaranteed output shape.
        Results are cached.
        """
        key = _cache_key("json", prompt, system_instruction or "", model_name or "")
        cached = self._response_cache.get(key)
        if cached is not None:
            logger.debug("Gemini generate_json cache HIT")
            return cached

        try:
            response = await _call_with_timeout(
                self._client.aio.models.generate_content(
                    model=model_name or self.model_name,
                    contents=prompt,
                    config=self._config(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        response_schema=response_schema,
                    ),
                )
            )
            result = {
                "text": _response_text(response),
                "model": self.model_name,
                "tokens_used": _response_tokens(response),
                "finish_reason": _response_finish(response),
            }
            self._response_cache.set(key, result)
            return result
        except Exception as e:
            logger.error(f"Gemini JSON generation failed: {e}", exc_info=True)
            raise

    @async_retry(max_attempts=2, delay=2.0)
    async def generate_embedding(
        self,
        text: str,
        model_name: str = "models/gemini-embedding-001",
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> List[float]:
        """
        Generate an embedding vector for `text`.

        `task_type` defaults to RETRIEVAL_DOCUMENT (matches the stored corpus).
        Pass "RETRIEVAL_QUERY" for user-query embeddings (Phase 4 query rewrite).
        Embeddings are cached — identical (text, model, task_type) won't hit the API twice.
        """
        key = _cache_key("emb", text, model_name, task_type)
        cached = self._embedding_cache.get(key)
        if cached is not None:
            logger.debug("Embedding cache HIT")
            return cached

        try:
            result = await _call_with_timeout(
                self._client.aio.models.embed_content(
                    model=model_name,
                    contents=text,
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=settings.EMBEDDING_DIMENSION,
                    ),
                )
            )
            embedding = list(result.embeddings[0].values)
            self._embedding_cache.set(key, embedding)
            return embedding
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}", exc_info=True)
            raise

    @async_retry(max_attempts=2, delay=2.0)
    async def generate_grounded_research(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.3,
        max_output_tokens: int = 8192,
    ) -> Dict[str, Any]:
        """
        Generate text with **Google Search grounding** enabled (first-class Tool
        in the unified SDK — no more raw REST). The response's grounding metadata
        carries the actual web URLs Gemini consulted (more trustworthy than
        asking the model to self-report sources).

        Returns dict with: text, tokens_used, grounding_sources (list of
        {title, uri, publisher} deduped by uri), search_queries, finish_reason, model.
        """
        model = model_name or self.model_name
        try:
            response = await _call_with_timeout(
                self._client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction or None,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                    ),
                )
            )
        except Exception as exc:
            logger.error("Gemini grounded research failed: %s", exc, exc_info=True)
            raise

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return {"text": "", "tokens_used": None, "grounding_sources": [], "search_queries": []}
        cand = candidates[0]
        text = _response_text(response)

        # Extract grounding sources — deduped by uri.
        sources: List[Dict[str, str]] = []
        seen_uris: set = set()
        grounding = getattr(cand, "grounding_metadata", None)
        for chunk in (getattr(grounding, "grounding_chunks", None) or []) if grounding else []:
            web = getattr(chunk, "web", None)
            uri = (getattr(web, "uri", "") or "") if web else ""
            title = (getattr(web, "title", "") or "") if web else ""
            if uri and uri not in seen_uris:
                seen_uris.add(uri)
                # Grounded search returns Vertex AI Search redirect URIs, so the
                # URL host is always "vertexaisearch" — useless as a publisher.
                # The real publisher domain comes through in `title` as a bare
                # host like "infosys.com". Prefer that; fall back to the URI host.
                publisher = ""
                title_clean = title.strip().lower()
                if re.match(r"^[\w.-]+\.[a-z]{2,}$", title_clean):
                    publisher = title_clean.replace("www.", "").split(".")[0]
                else:
                    try:
                        from urllib.parse import urlparse
                        host = urlparse(uri).hostname or ""
                        if host and "vertexaisearch" not in host:
                            publisher = host.replace("www.", "").split(".")[0]
                    except Exception:
                        pass
                sources.append({"title": title[:200], "uri": uri, "publisher": publisher})

        search_queries = list(getattr(grounding, "web_search_queries", None) or []) if grounding else []
        finish_reason = _response_finish(response)
        if finish_reason and finish_reason != "STOP":
            logger.warning(
                "Gemini grounded research finished with reason=%s — response may be truncated",
                finish_reason,
            )

        return {
            "text": text,
            "tokens_used": _response_tokens(response),
            "grounding_sources": sources,
            "search_queries": search_queries,
            "finish_reason": finish_reason,
            "model": model,
        }

    @async_retry(max_attempts=2, delay=2.0)
    async def generate_with_tools(
        self,
        prompt: str,
        tools: List[Any],
        tool_handlers: Dict[str, Callable],
        system_instruction: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate a response using Gemini Function Calling (single-round).

        Gemini may call one of the declared tools. When it does, this method
        executes the matching handler, feeds the result back, and returns the
        final text + any structured data the handler produced (``tool_results``).

        Args:
            prompt: User prompt.
            tools: List of ``types.Tool`` objects (function declarations).
            tool_handlers: ``{function_name: async_callable}`` map; each callable
                receives the function-call args dict and returns a dict.
            system_instruction: Optional system instruction.
            model_name: Optional model override.
        """
        model = model_name or self.model_name
        config = self._config(system_instruction=system_instruction, tools=tools)
        try:
            response = await _call_with_timeout(
                self._client.aio.models.generate_content(
                    model=model, contents=prompt, config=config,
                )
            )

            tool_results: List[Dict[str, Any]] = []
            candidate = (response.candidates or [None])[0]
            parts = (candidate.content.parts if candidate and candidate.content else None) or []

            # Collect EVERY function_call in the model's turn — gemini-2.5 can emit several in
            # parallel. Handling only the first (while echoing candidate.content, which carries ALL
            # the calls) sent back a function_response count that mismatched the call count → the API
            # 400s and the whole tool round is lost. Mirror stream_agentic: run each call and append
            # ONE function_response per call (an error response for an unknown handler) so counts match.
            fn_calls = [
                p.function_call for p in parts
                if getattr(p, "function_call", None) and p.function_call.name
            ]
            if fn_calls:
                response_parts: List[Any] = []
                for fc in fn_calls:
                    args = dict(fc.args) if fc.args else {}
                    handler = tool_handlers.get(fc.name)
                    if handler is None:
                        logger.warning(f"Gemini called unknown tool: {fc.name}")
                        handler_result = {"error": f"unknown tool: {fc.name}"}
                    else:
                        logger.info(f"Gemini invoked tool '{fc.name}' with args: {args}")
                        handler_result = await handler(args)
                        tool_results.append(handler_result)
                    response_parts.append(types.Part.from_function_response(
                        name=fc.name,
                        response={"result": handler_result},
                    ))

                # Feed the results back. Append the model's turn VERBATIM (candidate.content) so any
                # thought_signature is preserved, then ONE user turn with a response per call.
                follow_up = await _call_with_timeout(
                    self._client.aio.models.generate_content(
                        model=model,
                        contents=[
                            types.Content(role="user", parts=[types.Part(text=prompt)]),
                            candidate.content,
                            types.Content(role="user", parts=response_parts),
                        ],
                        config=config,
                    )
                )
                return {
                    "text": _response_text(follow_up),
                    "model": self.model_name,
                    "tokens_used": _response_tokens(follow_up),
                    "finish_reason": _response_finish(follow_up),
                    "tool_results": tool_results,
                }

            # No function call — return normal text response.
            return {
                "text": _response_text(response),
                "model": self.model_name,
                "tokens_used": _response_tokens(response),
                "finish_reason": _response_finish(response),
                "tool_results": tool_results,
            }

        except Exception as e:
            logger.error(f"Gemini tool-calling generation failed: {e}", exc_info=True)
            raise

    def create_tool_chat(
        self,
        system_instruction: Optional[str],
        tools: List[Any],
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
    ):
        """Create a stateful async chat session bound to function-calling tools
        (for the agentic research loop). Returns a google-genai AsyncChat; drive
        it with ``await _call_with_timeout(chat.send_message(...))``. The chats
        module auto-preserves the model's turns (incl. thought_signature) across
        rounds, so the caller only feeds tool responses back."""
        return self._client.aio.chats.create(
            model=self.model_name,
            config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                system_instruction=system_instruction or None,
                tools=list(tools),
            ),
        )

    async def stream_agentic(
        self,
        prompt: str,
        tools: List[Any],
        tool_handlers: Dict[str, Callable],
        system_instruction: Optional[str] = None,
        max_rounds: int = 4,
        model_name: Optional[str] = None,
    ):
        """Stream a MULTI-ROUND agentic answer: the model can call function-calling tools
        mid-stream (manual FC), while reasoning + answer stream throughout.

        Yields tagged events:
          * ("thought", str) — a reasoning summary chunk (→ the thinking card)
          * ("answer", str)  — an answer text chunk (→ the message bubble)
          * ("tool", {"name","args","result"}) — AFTER a tool ran (→ tool_step + widget extraction)

        client.aio.chats auto-preserves the model's turns (incl. thought signatures) across rounds;
        we only feed tool responses back. Bounded by max_rounds, with a final answer round if the
        model is still calling tools at the cap (so the user always gets a reply). Honors the quota
        circuit breaker manually (a partial stream can't be safely @async_retry'd)."""
        if _quota_circuit.is_open():
            raise GeminiQuotaError("Gemini quota circuit open (resource_exhausted) — failing fast")
        config = self._config(
            system_instruction=system_instruction,
            tools=tools,
            thinking_config=types.ThinkingConfig(include_thoughts=True),
        )
        # Manual function calling — we run handlers ourselves (AFC-while-streaming is buggy upstream).
        config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
        chat = self._client.aio.chats.create(model=model_name or self.model_name, config=config)

        message: Any = prompt
        try:
            for _round in range(max_rounds):
                fcalls: List[Any] = []
                stream = await chat.send_message_stream(message)
                async for chunk in stream:
                    for part in _iter_parts(chunk):
                        fc = getattr(part, "function_call", None)
                        if fc and fc.name:
                            fcalls.append(fc)
                            continue
                        try:
                            text = part.text or ""
                        except (ValueError, AttributeError):
                            text = ""
                        if text:
                            yield ("thought" if getattr(part, "thought", False) else "answer"), text
                if not fcalls:
                    _quota_circuit.record_success()
                    return
                # Run the requested tools, emit a "tool" event each, feed responses back next round.
                response_parts: List[Any] = []
                for fc in fcalls:
                    args = dict(fc.args) if fc.args else {}
                    handler = tool_handlers.get(fc.name)
                    if handler is None:
                        logger.warning("Agentic chat requested unknown tool: %s", fc.name)
                        result = {"error": f"unknown tool: {fc.name}"}
                    else:
                        try:
                            result = await handler(args)
                        except Exception as e:
                            logger.warning("Agentic tool %s failed: %s: %s", fc.name, type(e).__name__, e)
                            result = {"error": str(e)}
                    yield "tool", {"name": fc.name, "args": args, "result": result}
                    response_parts.append(types.Part.from_function_response(
                        name=fc.name,
                        response={"result": json.dumps(result, default=str)[:8000]},
                    ))
                message = response_parts

            # max_rounds exhausted while still calling tools — one final answer round (tools ignored)
            # so the user always gets a reply.
            final_stream = await chat.send_message_stream(message)
            async for chunk in final_stream:
                for part in _iter_parts(chunk):
                    if getattr(part, "function_call", None):
                        continue
                    try:
                        text = part.text or ""
                    except (ValueError, AttributeError):
                        text = ""
                    if text:
                        yield ("thought" if getattr(part, "thought", False) else "answer"), text
            _quota_circuit.record_success()
        except Exception as e:
            if _is_quota_error(e):
                _quota_circuit.record_quota_error()
            raise


# Global client instance
_gemini_client: Optional[GeminiClient] = None


def get_gemini_client() -> GeminiClient:
    """Get or create the global Gemini client instance."""
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = GeminiClient()
    return _gemini_client
