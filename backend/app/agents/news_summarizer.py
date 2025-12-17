"""
News Summarizer AI Agent
Handles news summarization and sentiment analysis with optimized prompts.
Requirements: Section 4.1 - Automated News Summarization
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import asyncio
import re

from app.integrations.gemini import GeminiClient
from app.schemas.common import SentimentType
from app.schemas.news import NewsSummarizationResponse, AIMetadata

logger = logging.getLogger(__name__)


class NewsSummarizerAgent:
    """
    AI agent specialized in summarizing financial news for non-technical investors.
    Section 4.1.3 - REQ-1, REQ-2, REQ-3
    """

    # Optimized system prompt for value investing perspective
    SYSTEM_PROMPT = """You are a financial news analyst specializing in value investing.
Your role is to translate complex financial news into clear, plain English that non-technical
investors can understand and act upon.

Key principles:
1. Focus on FUNDAMENTALS, not short-term price movements
2. Explain WHY news matters for long-term investors
3. Replace jargon with simple language
4. Be honest about uncertainty - don't oversell or undersell
5. Think like a teacher, not a trader

Remember: Your audience is learning value investing. Help them understand the business,
not just the stock price."""

    # Sentiment analysis prompt (optimized for fundamentals)
    SENTIMENT_PROMPT_TEMPLATE = """Analyze the following financial news and determine its sentiment
from a VALUE INVESTING perspective.

News: {content}

Instructions:
- BULLISH means positive for the company's long-term fundamentals (moat, earnings power, management)
- BEARISH means negative for long-term fundamentals
- NEUTRAL means no clear long-term impact OR mixed signals

Ignore short-term price movements. Focus on business fundamentals.

Respond in this EXACT format:
SENTIMENT: [bullish/bearish/neutral]
CONFIDENCE: [0-100]
REASONING: [2-3 sentences explaining your assessment focusing on fundamentals]
"""

    # Summarization prompt (plain English, 3 bullets max)
    SUMMARIZATION_PROMPT_TEMPLATE = """Summarize the following financial news in PLAIN ENGLISH
for someone learning value investing.

News: {content}

Requirements:
1. Create EXACTLY {max_bullets} bullet points
2. Each bullet should be ONE clear insight
3. Replace jargon with simple language:
   - "EBITDA" → "operating profit"
   - "YoY" → "compared to last year"
   - "Guidance" → "company's forecast"
   - "Multiple expansion" → "investors willing to pay more"
4. Focus on what matters for long-term investors
5. Be concise but complete (15-25 words per bullet)

Format:
• [First key insight]
• [Second key insight]
• [Third key insight]

Do not include any other text, headers, or explanations. Just the bullets."""

    def __init__(self, gemini_client: Optional[GeminiClient] = None):
        """
        Initialize news summarizer agent.

        Args:
            gemini_client: Optional Gemini client (creates new if not provided)
        """
        self.gemini_client = gemini_client or GeminiClient()
        logger.info("NewsSummarizerAgent initialized")

    async def analyze_sentiment(
        self,
        content: str,
        title: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze sentiment of news article from value investing perspective.
        Section 4.1.3 - REQ-1: Categorize as bullish/bearish/neutral

        Args:
            content: Article content
            title: Optional article title

        Returns:
            dict: Sentiment analysis result

        Example:
            result = await agent.analyze_sentiment(article_content)
            # Returns: {"sentiment": "bullish", "confidence": 85, "reasoning": "..."}
        """
        try:
            # Combine title and content
            full_content = f"Title: {title}\n\n{content}" if title else content

            # Truncate if too long (keep first 8000 chars)
            if len(full_content) > 8000:
                full_content = full_content[:8000] + "..."

            # Generate sentiment analysis
            prompt = self.SENTIMENT_PROMPT_TEMPLATE.format(content=full_content)

            response = await self.gemini_client.generate_text(
                prompt=prompt,
                system_instruction=self.SYSTEM_PROMPT
            )

            # Parse response
            text = response["text"]
            sentiment = self._parse_sentiment(text)

            logger.info(f"Sentiment analysis complete: {sentiment['sentiment']} ({sentiment['confidence']}%)")

            return {
                "sentiment": sentiment["sentiment"],
                "confidence": sentiment["confidence"],
                "reasoning": sentiment["reasoning"],
                "model_version": response.get("model"),
                "tokens_used": response.get("tokens_used")
            }

        except Exception as e:
            logger.error(f"Sentiment analysis failed: {e}", exc_info=True)
            # Return neutral with low confidence on error
            return {
                "sentiment": "neutral",
                "confidence": 0,
                "reasoning": f"Error analyzing sentiment: {str(e)}",
                "error": str(e)
            }

    async def summarize(
        self,
        content: str,
        title: Optional[str] = None,
        max_bullets: int = 3,
        include_sentiment: bool = True
    ) -> NewsSummarizationResponse:
        """
        Summarize news article in plain English.
        Section 4.1.3 - REQ-2: Limit to 3 bullet points
        Section 4.1.3 - REQ-3: Replace jargon with plain English

        Args:
            content: Article content
            title: Optional article title
            max_bullets: Maximum bullet points (default 3)
            include_sentiment: Whether to include sentiment analysis

        Returns:
            NewsSummarizationResponse: Structured summary

        Example:
            summary = await agent.summarize(article_content)
            # Returns structured response with bullets, sentiment, etc.
        """
        try:
            start_time = datetime.utcnow()

            # Combine title and content
            full_content = f"Title: {title}\n\n{content}" if title else content

            # Truncate if too long
            if len(full_content) > 8000:
                full_content = full_content[:8000] + "..."

            # Run summarization and sentiment in parallel
            tasks = [
                self._generate_summary(full_content, max_bullets)
            ]

            if include_sentiment:
                tasks.append(self.analyze_sentiment(full_content))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Extract results
            summary_result = results[0] if not isinstance(results[0], Exception) else None
            sentiment_result = results[1] if len(results) > 1 and not isinstance(results[1], Exception) else None

            if summary_result is None:
                raise ValueError("Summary generation failed")

            # Calculate generation time
            generation_time = (datetime.utcnow() - start_time).total_seconds()

            # Build response
            return NewsSummarizationResponse(
                summary=summary_result["summary"],
                bullets=summary_result["bullets"],
                sentiment=SentimentType(sentiment_result["sentiment"]) if sentiment_result else None,
                sentiment_confidence=sentiment_result.get("confidence") if sentiment_result else None,
                key_points=summary_result["bullets"],  # Same as bullets for now
                mentioned_companies=self._extract_companies(full_content),
                ai_metadata=AIMetadata(
                    model_name=summary_result.get("model", "gemini-1.5-pro"),
                    tokens_used=summary_result.get("tokens_used", 0) + (sentiment_result.get("tokens_used", 0) if sentiment_result else 0),
                    generation_time_seconds=generation_time,
                    temperature=0.7
                )
            )

        except Exception as e:
            logger.error(f"Summarization failed: {e}", exc_info=True)
            raise

    async def _generate_summary(self, content: str, max_bullets: int) -> Dict[str, Any]:
        """
        Generate bullet point summary.

        Args:
            content: Content to summarize
            max_bullets: Maximum bullet points

        Returns:
            dict: Summary result
        """
        prompt = self.SUMMARIZATION_PROMPT_TEMPLATE.format(
            content=content,
            max_bullets=max_bullets
        )

        response = await self.gemini_client.generate_text(
            prompt=prompt,
            system_instruction=self.SYSTEM_PROMPT
        )

        # Parse bullets from response
        text = response["text"]
        bullets = self._parse_bullets(text, max_bullets)

        return {
            "summary": text,
            "bullets": bullets,
            "model": response.get("model"),
            "tokens_used": response.get("tokens_used")
        }

    def _parse_sentiment(self, text: str) -> Dict[str, Any]:
        """
        Parse sentiment from AI response.

        Args:
            text: AI response text

        Returns:
            dict: Parsed sentiment data
        """
        sentiment = "neutral"
        confidence = 50
        reasoning = ""

        try:
            lines = text.strip().split("\n")
            for line in lines:
                line = line.strip()
                if line.startswith("SENTIMENT:"):
                    sentiment_text = line.split(":")[-1].strip().lower()
                    if sentiment_text in ["bullish", "bearish", "neutral"]:
                        sentiment = sentiment_text
                elif line.startswith("CONFIDENCE:"):
                    try:
                        confidence = int(re.search(r'\d+', line).group())
                    except:
                        confidence = 50
                elif line.startswith("REASONING:"):
                    reasoning = line.split(":", 1)[-1].strip()

            # If reasoning spans multiple lines, capture it
            if "REASONING:" in text:
                reasoning_section = text.split("REASONING:")[-1].strip()
                reasoning = reasoning_section.split("\n")[0].strip()

        except Exception as e:
            logger.warning(f"Error parsing sentiment: {e}")

        return {
            "sentiment": sentiment,
            "confidence": max(0, min(100, confidence)),
            "reasoning": reasoning or "No reasoning provided"
        }

    def _parse_bullets(self, text: str, max_bullets: int) -> List[str]:
        """
        Parse bullet points from AI response.

        Args:
            text: AI response text
            max_bullets: Maximum bullets to extract

        Returns:
            list: Bullet points
        """
        bullets = []

        # Remove common bullet markers
        lines = text.strip().split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Remove bullet markers
            for marker in ["•", "-", "*", "–", "—"]:
                if line.startswith(marker):
                    line = line[len(marker):].strip()
                    break

            # Check if line looks like a bullet (not a header/title)
            if line and len(line) > 10 and not line.endswith(":"):
                bullets.append(line)

            if len(bullets) >= max_bullets:
                break

        # If we didn't find bullets, try to split by periods
        if not bullets:
            sentences = [s.strip() for s in text.split(".") if s.strip()]
            bullets = sentences[:max_bullets]

        return bullets[:max_bullets]

    def _extract_companies(self, text: str) -> List[str]:
        """
        Extract mentioned company names from text.
        Simple implementation - can be enhanced with NER.

        Args:
            text: Text to analyze

        Returns:
            list: Company names/tickers
        """
        companies = []

        # Look for common patterns
        # Pattern 1: Ticker symbols (uppercase 1-5 letters)
        ticker_pattern = r'\b[A-Z]{1,5}\b'
        tickers = re.findall(ticker_pattern, text[:1000])  # First 1000 chars

        # Filter out common words that look like tickers
        exclude_words = {"THE", "FOR", "AND", "WITH", "CEO", "CFO", "Q1", "Q2", "Q3", "Q4", "YOY", "EPS"}
        tickers = [t for t in tickers if t not in exclude_words]

        companies.extend(tickers[:5])  # Max 5 tickers

        return list(set(companies))  # Remove duplicates


    async def batch_summarize(
        self,
        articles: List[Dict[str, str]],
        max_concurrent: int = 5
    ) -> List[NewsSummarizationResponse]:
        """
        Summarize multiple articles concurrently.

        Args:
            articles: List of articles with 'content' and optional 'title'
            max_concurrent: Maximum concurrent requests

        Returns:
            list: Summarization results

        Example:
            results = await agent.batch_summarize([
                {"title": "...", "content": "..."},
                {"title": "...", "content": "..."}
            ])
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def summarize_with_semaphore(article):
            async with semaphore:
                return await self.summarize(
                    content=article.get("content", ""),
                    title=article.get("title")
                )

        results = await asyncio.gather(
            *[summarize_with_semaphore(article) for article in articles],
            return_exceptions=True
        )

        # Filter out exceptions
        summaries = [r for r in results if not isinstance(r, Exception)]

        logger.info(f"Batch summarization complete: {len(summaries)}/{len(articles)} successful")

        return summaries
