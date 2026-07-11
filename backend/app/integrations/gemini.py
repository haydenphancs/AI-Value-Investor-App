"""
Google Gemini API Integration
Handles all interactions with Google Gemini for AI features.
Requirements: Section 3.3, 4.3.1 - Google Gemini API for deep research
"""

import google.generativeai as genai
from google.generativeai.types import content_types
from typing import Optional, List, Dict, Any, Callable
import logging
import asyncio
import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import wraps

import httpx

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


# Dedicated pool for streaming producer threads. The blocking SDK stream iterator runs in a
# thread; if the network read stalls or the SSE client disconnects mid-stall, that thread stays
# parked (fut.cancel() cannot interrupt a running thread). Isolating it from the DEFAULT asyncio
# executor — which every other to_thread Gemini call (reports, embeddings, generate_json, the chat
# fallback) shares — means a leaked producer can never starve that shared pool and stall unrelated
# work app-wide. Worst case, streams queue here and the endpoint degrades to non-streaming.
_STREAM_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="gemini-stream")


# ── Per-call timeout guard ─────────────────────────────────────────
async def _call_with_timeout(callable_, *args, **kwargs):
    """Run a sync Gemini SDK call in a thread with a timeout.

    The Gemini SDK's default is unbounded — without this guard a hung
    network read parks the whole report-generation task forever (seen
    as a report card stuck at "Deep research complete, synthesizing..."
    at 55% progress).

    On timeout, raises asyncio.TimeoutError — `@async_retry` skips it
    (not a quota error), and the caller's existing exception handler
    returns its sentinel fallback instead of hanging.

    Timeout sourced from settings.GEMINI_REQUEST_TIMEOUT_SECONDS.
    """
    return await asyncio.wait_for(
        asyncio.to_thread(callable_, *args, **kwargs),
        timeout=settings.GEMINI_REQUEST_TIMEOUT_SECONDS,
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


class GeminiClient:
    """
    Client for Google Gemini API.
    Section 4.3.3 - REQ-6: Uses large context window model (Gemini 1.5 Pro+)
    """

    def __init__(self):
        """Initialize Gemini client with API key from settings."""
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model_name = settings.GEMINI_MODEL
        self.generation_config = {
            "temperature": settings.GEMINI_TEMPERATURE,
            "max_output_tokens": settings.GEMINI_MAX_TOKENS,
        }
        cache_ttl = getattr(settings, "GEMINI_CACHE_TTL", 3600)
        self._response_cache = _TTLCache(max_size=256, ttl_seconds=cache_ttl)
        self._embedding_cache = _TTLCache(max_size=512, ttl_seconds=cache_ttl)

    def _get_model(self, model_name: Optional[str] = None):
        """
        Get Gemini model instance.

        Args:
            model_name: Optional model name override

        Returns:
            GenerativeModel: Gemini model instance
        """
        return genai.GenerativeModel(
            model_name=model_name or self.model_name,
            generation_config=self.generation_config
        )

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
        # ── Cache lookup ──
        key = _cache_key(prompt, system_instruction or "", model_name or "")
        cached = self._response_cache.get(key)
        if cached is not None:
            logger.debug("Gemini generate_text cache HIT")
            return cached

        try:
            model = self._get_model(model_name)

            if system_instruction:
                model = genai.GenerativeModel(
                    model_name=model_name or self.model_name,
                    generation_config=self.generation_config,
                    system_instruction=system_instruction
                )

            response = await _call_with_timeout(
                model.generate_content,
                prompt,
            )

            result = {
                "text": response.text,
                "model": self.model_name,
                "tokens_used": response.usage_metadata.total_token_count if hasattr(response, 'usage_metadata') else None,
                "finish_reason": response.candidates[0].finish_reason.name if response.candidates else None
            }
            self._response_cache.set(key, result)
            return result

        except Exception as e:
            logger.error(f"Gemini text generation failed: {e}", exc_info=True)
            raise

    # ── Streaming text (SSE chat) ─────────────────────────────────────
    # NOTE: intentionally NOT decorated with @async_retry — retrying a partial
    # stream would replay already-emitted tokens. Instead we honor the quota
    # circuit breaker manually (fail-fast if open; record quota errors/success)
    # and let the caller's SSE endpoint emit an `error` event + fall back.
    #
    # The Gemini SDK 0.8.3 `generate_content(stream=True)` is a SYNC iterator;
    # we drain it on a worker thread and hand chunks to the event loop through
    # an unbounded asyncio.Queue via call_soon_threadsafe. Function-calling is
    # deliberately unavailable here (tools ⊗ streaming) — the chat endpoint
    # injects context + fetches any widget deterministically instead.
    async def stream_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        """Yield response text chunks as Gemini generates them.

        Raises immediately if the quota circuit is open. Propagates the first
        error the SDK raises (quota or otherwise) across the thread boundary so
        the caller can surface an `error` event. A per-chunk timeout guards
        against a hung stream parking the request forever.
        """
        if _quota_circuit.is_open():
            raise GeminiQuotaError(
                "Gemini quota circuit open (resource_exhausted) — failing fast"
            )

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()  # unbounded — chat streams are small
        _SENTINEL = object()
        # Cooperative stop: set when the consumer leaves (per-chunk timeout, early break, or the
        # generator being closed on client disconnect) so the producer stops pulling the next chunk
        # instead of leaking a parked thread. fut.cancel() alone is a no-op on a running thread.
        stop = threading.Event()

        def _produce() -> None:
            try:
                model = genai.GenerativeModel(
                    model_name=model_name or self.model_name,
                    generation_config=self.generation_config,
                    system_instruction=system_instruction or None,
                )
                for chunk in model.generate_content(prompt, stream=True):
                    if stop.is_set():
                        break
                    # chunk.text raises if the chunk carries no text part
                    # (safety/finish-only chunks) — treat those as empty.
                    try:
                        text = chunk.text or ""
                    except Exception:
                        text = ""
                    if text:
                        loop.call_soon_threadsafe(queue.put_nowait, text)
            except Exception as e:  # push the error across the boundary
                loop.call_soon_threadsafe(queue.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

        # Dedicated executor (not the shared default pool) so a producer that stays blocked on a
        # hung read can't starve the to_thread pool every other Gemini call depends on.
        fut = loop.run_in_executor(_STREAM_EXECUTOR, _produce)
        try:
            while True:
                item = await asyncio.wait_for(
                    queue.get(), timeout=settings.GEMINI_REQUEST_TIMEOUT_SECONDS
                )
                if item is _SENTINEL:
                    break
                if isinstance(item, Exception):
                    if _is_quota_error(item):
                        _quota_circuit.record_quota_error()
                    raise item
                yield item
            _quota_circuit.record_success()
        finally:
            stop.set()       # ask the producer to stop at the next chunk boundary
            fut.cancel()     # cancels only if still queued (no-op once running)

    # ── Context caching (Stage-B narratives) ──────────────────────────
    # The N parallel narrative calls per report share one large evidence blob +
    # persona system prompt. Uploading that shared prefix to a CachedContent
    # once and pointing every call at it bills the prefix ~1x (write) + N×25%
    # (cache reads) instead of N×100%. All three methods are FAIL-SAFE: a
    # missing-SDK / below-min-size / quota error degrades to the inline path
    # (create_* returns None) so report quality is never sacrificed for cost.

    async def create_narrative_cache(
        self,
        system_instruction: Optional[str],
        evidence: str,
        ttl_minutes: Optional[int] = None,
    ) -> Optional[Any]:
        """Create a Gemini CachedContent for the shared (system prompt +
        evidence) prefix and pre-build the model bound to it. Returns an opaque
        handle ``{"cache", "model"}`` or None on ANY failure (caller falls back
        to inline prompts). Never raises.

        The model is built here (not per-call) so a `from_cached_content`
        signature/SDK incompatibility is caught ONCE up front → None → inline
        path, instead of failing all N calls individually.
        """
        if not evidence:
            return None
        try:
            import datetime as _dt
            from google.generativeai import caching

            ttl = ttl_minutes if ttl_minutes is not None else getattr(
                settings, "GEMINI_CONTEXT_CACHE_TTL_MINUTES", 10
            )
            model_name = (
                self.model_name
                if self.model_name.startswith("models/")
                else f"models/{self.model_name}"
            )

            def _create():
                cache = caching.CachedContent.create(
                    model=model_name,
                    system_instruction=system_instruction or None,
                    contents=[f"FINANCIAL EVIDENCE:\n{evidence}"],
                    ttl=_dt.timedelta(minutes=ttl),
                )
                model = genai.GenerativeModel.from_cached_content(
                    cached_content=cache
                )
                return {"cache": cache, "model": model}

            # Through _call_with_timeout (not bare to_thread) so a hung SDK
            # create can't park the agent run for the full 600s pipeline
            # ceiling while holding a MAX_CONCURRENT_AGENT_RUNS slot — a
            # TimeoutError here is caught below → None → inline path.
            handle = await _call_with_timeout(_create)
            logger.info("Gemini context cache created (ttl=%dm)", ttl)
            return handle
        except Exception as e:
            # Below-min-token (2.5 Flash min ~1024), old SDK, or quota → inline.
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
        only the per-field instruction. Goes through the same timeout + quota
        retry/circuit-breaker path as `generate_text`.
        """
        model = handle["model"]
        response = await _call_with_timeout(
            model.generate_content,
            prompt,
            generation_config=self.generation_config,
        )
        return {
            "text": response.text,
            "model": self.model_name,
            "tokens_used": (
                response.usage_metadata.total_token_count
                if hasattr(response, "usage_metadata") else None
            ),
            "finish_reason": (
                response.candidates[0].finish_reason.name
                if response.candidates else None
            ),
        }

    async def delete_cache(self, handle: Optional[Dict[str, Any]]) -> None:
        """Best-effort delete of a CachedContent so cache storage is freed
        before its TTL. Never raises (a failed delete just expires via TTL)."""
        if not handle:
            return
        try:
            # Timeout-guarded like every other Gemini call — a hung delete just
            # lets the cache expire via its TTL instead of parking the caller.
            await _call_with_timeout(handle["cache"].delete)
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
            json_config = {
                **self.generation_config,
                "response_mime_type": "application/json",
            }
            if response_schema is not None:
                json_config["response_schema"] = response_schema

            model = genai.GenerativeModel(
                model_name=model_name or self.model_name,
                generation_config=json_config,
                system_instruction=system_instruction or None,
            )

            response = await _call_with_timeout(
                model.generate_content,
                prompt,
            )

            result = {
                "text": response.text,
                "model": self.model_name,
                "tokens_used": response.usage_metadata.total_token_count if hasattr(response, 'usage_metadata') else None,
                "finish_reason": response.candidates[0].finish_reason.name if response.candidates else None
            }
            self._response_cache.set(key, result)
            return result

        except Exception as e:
            logger.error(f"Gemini JSON generation failed: {e}", exc_info=True)
            raise

    # No @async_retry here: this delegates to generate_text (already decorated).
    # Stacking it would multiply quota backoff and over-count the circuit breaker
    # on a single logical call.
    async def generate_with_context(
        self,
        prompt: str,
        context_documents: List[str],
        system_instruction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate text with additional context documents (RAG).
        Delegates to generate_text which handles caching.
        """
        context_text = "\n\n---\n\n".join(context_documents)
        enhanced_prompt = f"""Context Information:
{context_text}

---

Based on the context above, please answer the following:

{prompt}

Provide a comprehensive answer with citations to the context where appropriate."""

        return await self.generate_text(
            prompt=enhanced_prompt,
            system_instruction=system_instruction
        )

    @async_retry(max_attempts=2, delay=2.0)
    async def generate_embedding(
        self,
        text: str,
        model_name: str = "models/gemini-embedding-001"
    ) -> List[float]:
        """
        Generate embedding vector for text.
        Embeddings are cached — identical text won't hit the API twice.
        """
        key = _cache_key("emb", text, model_name)
        cached = self._embedding_cache.get(key)
        if cached is not None:
            logger.debug("Embedding cache HIT")
            return cached

        try:
            result = await _call_with_timeout(
                genai.embed_content,
                model=model_name,
                content=text,
                task_type="retrieval_document",
                output_dimensionality=settings.EMBEDDING_DIMENSION,
            )
            embedding = result['embedding']
            self._embedding_cache.set(key, embedding)
            return embedding

        except Exception as e:
            logger.error(f"Embedding generation failed: {e}", exc_info=True)
            raise

    # No @async_retry here: delegates to generate_text (already decorated).
    async def analyze_sentiment(
        self,
        text: str
    ) -> Dict[str, Any]:
        """
        Analyze sentiment of text (bullish, bearish, neutral).
        Section 4.1.3 - REQ-1: Sentiment categorization

        Args:
            text: Text to analyze

        Returns:
            dict: Sentiment analysis result
        """
        prompt = f"""Analyze the following financial text and determine the sentiment.

Text: {text}

Provide your response in this exact format:
Sentiment: [bullish/bearish/neutral]
Confidence: [0-100]
Reasoning: [brief explanation]

Focus on fundamental factors, not short-term price movements."""

        response = await self.generate_text(
            prompt=prompt,
            system_instruction="You are a financial analyst specializing in sentiment analysis for value investors."
        )

        # Parse response
        text_response = response["text"]
        sentiment = "neutral"
        confidence = 0

        try:
            lines = text_response.split("\n")
            for line in lines:
                if line.startswith("Sentiment:"):
                    sentiment = line.split(":")[-1].strip().lower()
                elif line.startswith("Confidence:"):
                    confidence = int(line.split(":")[-1].strip())
        except Exception as e:
            logger.warning(f"Failed to parse sentiment response: {e}")

        return {
            "sentiment": sentiment,
            "confidence": confidence,
            "raw_response": text_response,
            **response
        }

    # No @async_retry here: delegates to generate_text (already decorated).
    async def summarize_text(
        self,
        text: str,
        max_bullets: int = 3,
        style: str = "plain_english"
    ) -> Dict[str, Any]:
        """
        Summarize text in plain English.
        Section 4.1.3 - REQ-2: Summaries limited to 3 bullet points
        Section 4.1.3 - REQ-3: Replace jargon with plain English

        Args:
            text: Text to summarize
            max_bullets: Maximum bullet points (default 3)
            style: Summary style (plain_english, technical)

        Returns:
            dict: Summary with bullet points
        """
        if style == "plain_english":
            instruction = """You are summarizing financial news for non-technical investors.
Use simple, clear language. Avoid jargon. If technical terms are necessary,
explain them in plain English."""
        else:
            instruction = "You are a financial analyst providing technical summaries."

        prompt = f"""Summarize the following text in exactly {max_bullets} bullet points.
Each bullet should be concise and focus on key insights.

Text:
{text}

Provide ONLY the bullet points, no additional commentary."""

        response = await self.generate_text(
            prompt=prompt,
            system_instruction=instruction
        )

        # Extract bullet points
        bullets = []
        for line in response["text"].split("\n"):
            line = line.strip()
            if line and (line.startswith("•") or line.startswith("-") or line.startswith("*")):
                bullets.append(line.lstrip("•-* "))

        return {
            "summary": response["text"],
            "bullets": bullets[:max_bullets],
            **response
        }

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
        Generate text with **Google Search grounding** enabled.

        The Python SDK 0.8.3 doesn't expose the `google_search` tool
        through the high-level `GenerativeModel(tools=...)` API, so we
        post directly to the Gemini REST endpoint. The response includes
        `groundingMetadata.groundingChunks` with the actual web URLs
        Gemini consulted — the audit log uses those (more trustworthy
        than asking the model to self-report sources).

        Args:
            prompt: User prompt.
            system_instruction: Optional system instruction.
            model_name: Optional model override (defaults to settings.GEMINI_MODEL).
            temperature: Lower = more deterministic. 0.3 is a good default
                for research-style synthesis.
            max_output_tokens: Cap on generated tokens.

        Returns:
            dict with keys:
              - text: the raw response text (parse JSON / extract code fences
                in the caller)
              - tokens_used: total token count if available
              - grounding_sources: list of {title, uri, publisher} dicts
                extracted from groundingMetadata (deduped by uri)
              - search_queries: list of search query strings Gemini ran
        """
        model = model_name or self.model_name
        api_key = settings.GEMINI_API_KEY
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )

        body: Dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}], "role": "user"}],
            "tools": [{"google_search": {}}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
            },
        }
        if system_instruction:
            body["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        timeout = httpx.Timeout(60.0, connect=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Gemini grounded research HTTP error: %s — body: %s",
                exc.response.status_code, exc.response.text[:300],
            )
            raise
        except Exception as exc:
            logger.error("Gemini grounded research failed: %s", exc, exc_info=True)
            raise

        # Parse response
        candidates = data.get("candidates") or []
        if not candidates:
            return {"text": "", "tokens_used": None, "grounding_sources": [], "search_queries": []}
        cand = candidates[0]
        parts = ((cand.get("content") or {}).get("parts") or [])
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))

        # Extract grounding sources — deduped by uri.
        sources: List[Dict[str, str]] = []
        seen_uris: set = set()
        grounding = cand.get("groundingMetadata") or {}
        for chunk in grounding.get("groundingChunks") or []:
            web = (chunk or {}).get("web") or {}
            uri = web.get("uri") or ""
            title = web.get("title") or ""
            if uri and uri not in seen_uris:
                seen_uris.add(uri)
                # Gemini grounded search returns Vertex AI Search redirect
                # URIs (vertexaisearch.cloud.google.com/...), so the URL host
                # is always "vertexaisearch" — useless as a publisher. The
                # real publisher domain comes through in `title` as a bare
                # host like "infosys.com". Prefer that; fall back to the URI
                # host only when the title isn't a domain (non-grounded
                # responses, future API changes).
                publisher = ""
                title_clean = (title or "").strip().lower()
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
                sources.append({
                    "title": title[:200],
                    "uri": uri,
                    "publisher": publisher,
                })

        search_queries = list(grounding.get("webSearchQueries") or [])

        tokens_used = (
            (data.get("usageMetadata") or {}).get("totalTokenCount")
        )
        finish_reason = cand.get("finishReason")
        if finish_reason and finish_reason != "STOP":
            logger.warning(
                "Gemini grounded research finished with reason=%s — response may be truncated",
                finish_reason,
            )

        return {
            "text": text,
            "tokens_used": tokens_used,
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
        Generate a response using Gemini Function Calling.

        Gemini may decide to call one of the declared tools.  When it does,
        this method executes the matching handler, feeds the result back to
        Gemini, and returns the final text + any structured data the handler
        produced (stashed under the ``tool_results`` key).

        Args:
            prompt: User prompt.
            tools: List of genai Tool objects (see genai.protos.Tool).
            tool_handlers: ``{function_name: async_callable}`` map.  Each
                callable receives the function-call args dict and must return
                a dict that Gemini will see as the tool response.
            system_instruction: Optional system instruction.
            model_name: Optional model override.

        Returns:
            dict with keys: text, model, tokens_used, finish_reason,
            tool_results (list of dicts returned by handlers, may be empty).
        """
        try:
            model = genai.GenerativeModel(
                model_name=model_name or self.model_name,
                generation_config=self.generation_config,
                system_instruction=system_instruction,
                tools=tools,
            )

            response = await _call_with_timeout(
                model.generate_content, prompt,
            )

            tool_results: List[Dict[str, Any]] = []

            # Check if Gemini wants to call a function
            candidate = response.candidates[0] if response.candidates else None
            if candidate and candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    fn_call = part.function_call
                    if fn_call and fn_call.name:
                        handler = tool_handlers.get(fn_call.name)
                        if handler is None:
                            logger.warning(
                                f"Gemini called unknown tool: {fn_call.name}"
                            )
                            continue

                        # Execute the handler with the args Gemini provided
                        args = dict(fn_call.args) if fn_call.args else {}
                        logger.info(
                            f"Gemini invoked tool '{fn_call.name}' "
                            f"with args: {args}"
                        )
                        handler_result = await handler(args)
                        tool_results.append(handler_result)

                        # Feed the tool result back to Gemini so it can
                        # compose a final natural-language answer.
                        import google.generativeai.protos as protos

                        function_response = protos.Part(
                            function_response=protos.FunctionResponse(
                                name=fn_call.name,
                                response={"result": handler_result},
                            )
                        )
                        follow_up = await _call_with_timeout(
                            model.generate_content,
                            [
                                protos.Content(
                                    role="user",
                                    parts=[protos.Part(text=prompt)],
                                ),
                                protos.Content(
                                    role="model",
                                    parts=[part],
                                ),
                                protos.Content(
                                    role="function",
                                    parts=[function_response],
                                ),
                            ],
                        )
                        return {
                            "text": follow_up.text,
                            "model": self.model_name,
                            "tokens_used": (
                                follow_up.usage_metadata.total_token_count
                                if hasattr(follow_up, "usage_metadata")
                                else None
                            ),
                            "finish_reason": (
                                follow_up.candidates[0].finish_reason.name
                                if follow_up.candidates
                                else None
                            ),
                            "tool_results": tool_results,
                        }

            # No function call — return normal text response
            return {
                "text": response.text,
                "model": self.model_name,
                "tokens_used": (
                    response.usage_metadata.total_token_count
                    if hasattr(response, "usage_metadata")
                    else None
                ),
                "finish_reason": (
                    candidate.finish_reason.name if candidate else None
                ),
                "tool_results": tool_results,
            }

        except Exception as e:
            logger.error(
                f"Gemini tool-calling generation failed: {e}", exc_info=True
            )
            raise

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        system_instruction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Multi-turn chat completion.
        Passes history as Content objects (1 API call) instead of replaying
        each message individually (which burned N API calls).
        """
        import google.generativeai.protos as protos

        model = self._get_model()
        if system_instruction:
            model = genai.GenerativeModel(
                model_name=self.model_name,
                generation_config=self.generation_config,
                system_instruction=system_instruction
            )

        # Build history as Content objects — zero API calls
        history = []
        role_map = {"user": "user", "assistant": "model", "model": "model"}
        for msg in messages[:-1]:
            gemini_role = role_map.get(msg["role"], "user")
            history.append(protos.Content(
                role=gemini_role,
                parts=[protos.Part(text=msg["content"])],
            ))

        chat = model.start_chat(history=history)

        # Only 1 API call for the final message
        final_message = messages[-1]["content"]
        response = await _call_with_timeout(chat.send_message, final_message)

        return {
            "text": response.text,
            "model": self.model_name,
            "tokens_used": response.usage_metadata.total_token_count if hasattr(response, 'usage_metadata') else None
        }


# Global client instance
_gemini_client: Optional[GeminiClient] = None


def get_gemini_client() -> GeminiClient:
    """
    Get or create global Gemini client instance.

    Returns:
        GeminiClient: Gemini client instance
    """
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = GeminiClient()
    return _gemini_client
