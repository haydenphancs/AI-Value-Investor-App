"""
News Service
Business logic for news aggregation, summarization, and management.
Requirements: Section 4.1 - Automated News Summarization
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import asyncio

from supabase import Client

from app.agents.news_summarizer import NewsSummarizerAgent
from app.integrations.news_api import NewsAggregator
from app.integrations.fmp import FMPClient
from app.schemas.news import NewsArticle, NewsSummarizationResponse
from app.schemas.common import SentimentType

logger = logging.getLogger(__name__)


class NewsService:
    """
    Service for news aggregation and AI-powered summarization.
    Handles the complete news pipeline from fetching to AI processing.
    """

    def __init__(
        self,
        supabase: Client,
        news_aggregator: Optional[NewsAggregator] = None,
        summarizer: Optional[NewsSummarizerAgent] = None,
        fmp_client: Optional[FMPClient] = None
    ):
        """
        Initialize news service.

        Args:
            supabase: Supabase client
            news_aggregator: News aggregation client
            summarizer: News summarizer agent
            fmp_client: FMP client for stock data
        """
        self.supabase = supabase
        self.news_aggregator = news_aggregator or NewsAggregator()
        self.summarizer = summarizer or NewsSummarizerAgent()
        self.fmp_client = fmp_client or FMPClient()
        logger.info("NewsService initialized")

    async def fetch_and_process_stock_news(
        self,
        ticker: str,
        company_name: str,
        days_back: int = 7,
        max_articles: int = 20
    ) -> List[str]:
        """
        Fetch and process news for a specific stock.
        Section 4.1 - Automated News Aggregation

        Args:
            ticker: Stock ticker
            company_name: Company name
            days_back: Days to look back
            max_articles: Maximum articles to process

        Returns:
            list: Created news article IDs

        Example:
            article_ids = await service.fetch_and_process_stock_news("AAPL", "Apple Inc.")
        """
        try:
            logger.info(f"Fetching news for {ticker}")

            # Step 1: Fetch news from aggregator
            raw_articles = await self.news_aggregator.get_stock_news(
                ticker=ticker,
                company_name=company_name,
                days_back=days_back,
                max_results=max_articles
            )

            if not raw_articles:
                logger.warning(f"No news found for {ticker}")
                return []

            logger.info(f"Found {len(raw_articles)} articles for {ticker}")

            # Step 2: Check for existing articles (deduplication)
            new_articles = await self._deduplicate_articles(raw_articles)

            if not new_articles:
                logger.info("All articles already exist in database")
                return []

            # Step 3: Get stock ID
            stock_result = self.supabase.table("stocks").select("id").eq(
                "ticker", ticker.upper()
            ).execute()

            stock_id = stock_result.data[0]["id"] if stock_result.data else None

            if not stock_id:
                logger.warning(f"Stock {ticker} not found in database")
                # Could create stock entry here
                return []

            # Step 4: Process articles with AI in batches
            created_ids = await self._process_and_save_articles(
                articles=new_articles,
                stock_id=stock_id
            )

            logger.info(f"Successfully processed {len(created_ids)} articles for {ticker}")

            return created_ids

        except Exception as e:
            logger.error(f"Failed to fetch/process news for {ticker}: {e}", exc_info=True)
            raise

    async def _deduplicate_articles(
        self,
        raw_articles: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Remove articles that already exist in database.

        Args:
            raw_articles: Raw article data

        Returns:
            list: New articles only
        """
        if not raw_articles:
            return []

        # Check by URL (most reliable)
        urls = [a.get("url") for a in raw_articles if a.get("url")]

        if not urls:
            return raw_articles

        # Query existing articles
        existing = self.supabase.table("news_articles").select("source_url").in_(
            "source_url", urls
        ).execute()

        existing_urls = {a["source_url"] for a in existing.data}

        # Filter out existing
        new_articles = [a for a in raw_articles if a.get("url") not in existing_urls]

        logger.info(f"Deduplication: {len(new_articles)} new / {len(existing_urls)} existing")

        return new_articles

    async def _process_and_save_articles(
        self,
        articles: List[Dict[str, Any]],
        stock_id: str
    ) -> List[str]:
        """
        Process articles with AI and save to database.

        Args:
            articles: Articles to process
            stock_id: Stock ID to associate

        Returns:
            list: Created article IDs
        """
        created_ids = []

        # Process in batches of 5 concurrent
        batch_size = 5

        for i in range(0, len(articles), batch_size):
            batch = articles[i:i + batch_size]

            # Process batch concurrently
            tasks = [self._process_single_article(article, stock_id) for article in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Collect successful results
            for result in results:
                if not isinstance(result, Exception) and result:
                    created_ids.append(result)

            # Small delay between batches to avoid rate limits
            if i + batch_size < len(articles):
                await asyncio.sleep(1)

        return created_ids

    async def _process_single_article(
        self,
        article: Dict[str, Any],
        stock_id: str
    ) -> Optional[str]:
        """
        Process single article with AI and save.

        Args:
            article: Article data
            stock_id: Stock ID

        Returns:
            str: Created article ID or None
        """
        try:
            content = article.get("content") or article.get("summary") or ""
            title = article.get("title", "")

            if not content or len(content) < 50:
                logger.warning(f"Skipping article with insufficient content: {title}")
                return None

            # Summarize with AI (includes sentiment analysis)
            summary_response = await self.summarizer.summarize(
                content=content,
                title=title,
                max_bullets=3,
                include_sentiment=True
            )

            # Prepare article data
            article_data = {
                "source_name": article.get("source_name", "Unknown"),
                "source_url": article.get("url", ""),
                "external_id": article.get("external_id"),
                "title": title,
                "summary": article.get("summary"),
                "content": content,
                "image_url": article.get("image_url"),
                "author": article.get("author"),
                "published_at": article.get("published_at") or datetime.utcnow().isoformat(),
                "scraped_at": datetime.utcnow().isoformat(),
                # AI results
                "ai_summary": summary_response.summary,
                "ai_summary_bullets": summary_response.bullets,
                "sentiment": summary_response.sentiment.value if summary_response.sentiment else None,
                "relevance_score": summary_response.sentiment_confidence / 100 if summary_response.sentiment_confidence else None,
                "ai_processed": True,
                "ai_processed_at": datetime.utcnow().isoformat(),
                "ai_model_version": summary_response.ai_metadata.model_name
            }

            # Save article
            result = self.supabase.table("news_articles").insert(article_data).execute()

            if not result.data:
                logger.error("Failed to insert article")
                return None

            article_id = result.data[0]["id"]

            # Create news-stock relationship
            await self._create_news_stock_relation(article_id, stock_id)

            logger.info(f"Processed article: {title[:50]}... (sentiment: {article_data['sentiment']})")

            return article_id

        except Exception as e:
            logger.error(f"Failed to process article: {e}", exc_info=True)
            return None

    async def _create_news_stock_relation(
        self,
        news_id: str,
        stock_id: str,
        relevance_score: float = 0.9
    ):
        """
        Create relationship between news and stock.

        Args:
            news_id: News article ID
            stock_id: Stock ID
            relevance_score: Relevance score
        """
        try:
            self.supabase.table("news_stocks").insert({
                "news_id": news_id,
                "stock_id": stock_id,
                "relevance_score": relevance_score
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to create news-stock relation: {e}")

    async def identify_breaking_news(
        self,
        stock_id: str,
        time_window_hours: int = 24
    ) -> List[str]:
        """
        Identify breaking news based on recency and impact.
        Section 4.1.3 - REQ-4: Show breaking news

        Args:
            stock_id: Stock ID
            time_window_hours: Time window to consider

        Returns:
            list: Breaking news IDs
        """
        try:
            # Get recent news for stock
            cutoff_time = datetime.utcnow() - timedelta(hours=time_window_hours)

            result = self.supabase.table("news_stocks").select(
                "news_id, news:news_articles(*)"
            ).eq("stock_id", stock_id).gte(
                "created_at", cutoff_time.isoformat()
            ).execute()

            if not result.data:
                return []

            # Criteria for breaking news:
            # 1. Recent (within time window)
            # 2. Strong sentiment (not neutral)
            # 3. High relevance score

            breaking_news_ids = []

            for item in result.data:
                news = item.get("news")
                if not news:
                    continue

                # Check criteria
                sentiment = news.get("sentiment")
                relevance = item.get("relevance_score", 0)

                is_breaking = (
                    sentiment in ["bullish", "bearish"] and
                    relevance >= 0.7
                )

                if is_breaking:
                    # Create breaking news entry
                    await self._create_breaking_news_entry(
                        news_id=item["news_id"],
                        stock_id=stock_id,
                        impact_score=relevance
                    )

                    breaking_news_ids.append(item["news_id"])

            logger.info(f"Identified {len(breaking_news_ids)} breaking news items for stock {stock_id}")

            return breaking_news_ids

        except Exception as e:
            logger.error(f"Failed to identify breaking news: {e}", exc_info=True)
            return []

    async def _create_breaking_news_entry(
        self,
        news_id: str,
        stock_id: str,
        impact_score: float
    ):
        """Create breaking news entry."""
        try:
            # Check for price movement (if available)
            price_moving = False
            price_change = None

            # Could fetch real-time price here via FMP
            # For now, mark as breaking without price data

            self.supabase.table("breaking_news").insert({
                "news_id": news_id,
                "stock_id": stock_id,
                "impact_score": impact_score,
                "is_price_moving": price_moving,
                "price_change_percent": price_change,
                "expires_at": (datetime.utcnow() + timedelta(hours=24)).isoformat()
            }).execute()

        except Exception as e:
            logger.warning(f"Failed to create breaking news entry: {e}")

    async def fetch_market_news(
        self,
        category: str = "business",
        max_articles: int = 20
    ) -> List[str]:
        """
        Fetch and process general market news.

        Args:
            category: News category
            max_articles: Maximum articles

        Returns:
            list: Created article IDs
        """
        try:
            logger.info(f"Fetching market news (category: {category})")

            # Fetch news
            raw_articles = await self.news_aggregator.get_market_news(
                category=category,
                max_results=max_articles
            )

            if not raw_articles:
                return []

            # Deduplicate
            new_articles = await self._deduplicate_articles(raw_articles)

            # Process (without specific stock association)
            created_ids = []

            for article in new_articles[:max_articles]:
                try:
                    content = article.get("content") or article.get("summary") or ""
                    title = article.get("title", "")

                    if not content:
                        continue

                    # Summarize
                    summary_response = await self.summarizer.summarize(
                        content=content,
                        title=title
                    )

                    # Save
                    article_data = {
                        "source_name": article.get("source_name", "Unknown"),
                        "source_url": article.get("url", ""),
                        "title": title,
                        "summary": article.get("summary"),
                        "content": content,
                        "image_url": article.get("image_url"),
                        "author": article.get("author"),
                        "published_at": article.get("published_at") or datetime.utcnow().isoformat(),
                        "ai_summary": summary_response.summary,
                        "ai_summary_bullets": summary_response.bullets,
                        "sentiment": summary_response.sentiment.value if summary_response.sentiment else None,
                        "ai_processed": True,
                        "ai_processed_at": datetime.utcnow().isoformat()
                    }

                    result = self.supabase.table("news_articles").insert(article_data).execute()

                    if result.data:
                        created_ids.append(result.data[0]["id"])

                except Exception as e:
                    logger.warning(f"Failed to process market news article: {e}")
                    continue

            logger.info(f"Processed {len(created_ids)} market news articles")

            return created_ids

        except Exception as e:
            logger.error(f"Failed to fetch market news: {e}", exc_info=True)
            return []
