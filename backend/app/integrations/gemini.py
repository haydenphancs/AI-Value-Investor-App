"""
Google Gemini API Integration
Handles all interactions with Google Gemini for AI features.
Requirements: Section 3.3, 4.3.1 - Google Gemini API for deep research
"""

import google.generativeai as genai
from typing import Optional, List, Dict, Any
import logging
import asyncio
from functools import wraps

from app.config import settings

logger = logging.getLogger(__name__)


def async_retry(max_attempts: int = 3, delay: float = 1.0):
    """
    Decorator for retrying async functions on failure.

    Args:
        max_attempts: Maximum retry attempts
        delay: Delay between retries in seconds
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise
                    logger.warning(
                        f"Attempt {attempt + 1} failed: {e}. Retrying..."
                    )
                    await asyncio.sleep(delay * (attempt + 1))
            return None
        return wrapper
    return decorator


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

    @async_retry(max_attempts=3, delay=2.0)
    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate text using Gemini.

        Args:
            prompt: User prompt
            system_instruction: Optional system instruction
            model_name: Optional model name override

        Returns:
            dict: Response with text and metadata

        Example:
            response = await client.generate_text(
                prompt="Analyze Apple Inc.",
                system_instruction="You are a value investing expert."
            )
        """
        try:
            model = self._get_model(model_name)

            if system_instruction:
                model = genai.GenerativeModel(
                    model_name=model_name or self.model_name,
                    generation_config=self.generation_config,
                    system_instruction=system_instruction
                )

            # Use asyncio to run synchronous API call in executor
            response = await asyncio.to_thread(
                model.generate_content,
                prompt
            )

            return {
                "text": response.text,
                "model": self.model_name,
                "tokens_used": response.usage_metadata.total_token_count if hasattr(response, 'usage_metadata') else None,
                "finish_reason": response.candidates[0].finish_reason.name if response.candidates else None
            }

        except Exception as e:
            logger.error(f"Gemini text generation failed: {e}", exc_info=True)
            raise

    @async_retry(max_attempts=3, delay=2.0)
    async def generate_with_context(
        self,
        prompt: str,
        context_documents: List[str],
        system_instruction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate text with additional context documents.
        Useful for RAG-based generation.

        Args:
            prompt: User prompt
            context_documents: List of context documents/chunks
            system_instruction: Optional system instruction

        Returns:
            dict: Response with text and metadata
        """
        # Build context-enhanced prompt
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

    @async_retry(max_attempts=3, delay=2.0)
    async def generate_embedding(
        self,
        text: str,
        model_name: str = "models/text-embedding-004"
    ) -> List[float]:
        """
        Generate embedding vector for text.
        Section 4.4 - RAG System embeddings

        Args:
            text: Text to embed
            model_name: Embedding model name

        Returns:
            List[float]: Embedding vector
        """
        try:
            result = await asyncio.to_thread(
                genai.embed_content,
                model=model_name,
                content=text,
                task_type="retrieval_document"
            )

            return result['embedding']

        except Exception as e:
            logger.error(f"Embedding generation failed: {e}", exc_info=True)
            raise

    @async_retry(max_attempts=3, delay=2.0)
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

    @async_retry(max_attempts=3, delay=2.0)
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

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        system_instruction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Multi-turn chat completion.
        Used for chat sessions.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system_instruction: Optional system instruction

        Returns:
            dict: Response with text and metadata
        """
        model = self._get_model()

        if system_instruction:
            model = genai.GenerativeModel(
                model_name=self.model_name,
                generation_config=self.generation_config,
                system_instruction=system_instruction
            )

        # Convert messages to Gemini format
        chat = model.start_chat(history=[])

        # Add previous messages to context
        for msg in messages[:-1]:
            if msg["role"] == "user":
                chat.send_message(msg["content"])

        # Send final user message and get response
        final_message = messages[-1]["content"]
        response = await asyncio.to_thread(chat.send_message, final_message)

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
