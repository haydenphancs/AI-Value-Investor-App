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
import time 
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
    Skips retries for quota errors (429) to avoid burning more quota.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if _is_quota_error(e):
                        logger.error(f"Quota/rate-limit error — skipping retries: {e}")
                        raise
                    if attempt == max_attempts - 1:
                        raise
                    logger.warning(
                        f"Attempt {attempt + 1} failed: {e}. Retrying..."
                    )
                    await asyncio.sleep(delay * (attempt + 1))
            return None
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

    @async_retry(max_attempts=2, delay=2.0)
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

    @async_retry(max_attempts=2, delay=2.0)
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

    @async_retry(max_attempts=2, delay=2.0)
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
