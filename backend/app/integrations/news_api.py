"""
News API Integration
Handles news aggregation from multiple sources.
Requirements: Section 3.3 - News Provider APIs (NewsAPI, SerpApi)
Requirements: Section 4.1 - Automated News Summarization
"""

import httpx
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import logging

from app.config import settings

logger = logging.getLogger(__name__)


class NewsAPIClient:
    """
    Client for NewsAPI.org
    Provides general news and financial news.
    """

    def __init__(self):
        """Initialize NewsAPI client."""
        self.base_url = "https://newsapi.org/v2"
        self.api_key = settings.NEWS_API_KEY
        self.timeout = settings.HTTP_TIMEOUT_SECONDS

    async def _make_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Make HTTP request to NewsAPI.

        Args:
            endpoint: API endpoint
            params: Query parameters

        Returns:
            dict: API response
        """
        url = f"{self.base_url}/{endpoint}"

        if params is None:
            params = {}
        params["apiKey"] = self.api_key

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()

        except httpx.HTTPError as e:
            logger.error(f"NewsAPI request failed: {e}")
            raise

    async def search_news(
        self,
        query: str,
        language: str = "en",
        sort_by: str = "publishedAt",
        page_size: int = 20,
        from_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Search for news articles.

        Args:
            query: Search query (stock ticker, company name, etc.)
            language: Language code
            sort_by: Sort order (relevancy, popularity, publishedAt)
            page_size: Number of results
            from_date: Filter articles from this date (YYYY-MM-DD)

        Returns:
            list: News articles
        """
        params = {
            "q": query,
            "language": language,
            "sortBy": sort_by,
            "pageSize": page_size
        }

        if from_date:
            params["from"] = from_date

        data = await self._make_request("everything", params=params)
        return data.get("articles", [])

    async def get_top_headlines(
        self,
        category: str = "business",
        country: str = "us",
        page_size: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get top headlines.

        Args:
            category: News category (business, technology, etc.)
            country: Country code
            page_size: Number of results

        Returns:
            list: Top headline articles
        """
        params = {
            "category": category,
            "country": country,
            "pageSize": page_size
        }

        data = await self._make_request("top-headlines", params=params)
        return data.get("articles", [])


class SerpAPIClient:
    """
    Client for SerpApi (Google News scraping).
    Alternative to NewsAPI with more comprehensive coverage.
    """

    def __init__(self):
        """Initialize SerpApi client."""
        self.base_url = "https://serpapi.com/search"
        self.api_key = settings.SERP_API_KEY
        self.timeout = settings.HTTP_TIMEOUT_SECONDS

    async def search_google_news(
        self,
        query: str,
        num_results: int = 20,
        time_period: str = "d"  # d=day, w=week, m=month
    ) -> List[Dict[str, Any]]:
        """
        Search Google News via SerpApi.

        Args:
            query: Search query
            num_results: Number of results
            time_period: Time period filter

        Returns:
            list: News articles
        """
        params = {
            "engine": "google_news",
            "q": query,
            "api_key": self.api_key,
            "num": num_results,
            "tbs": f"qdr:{time_period}"  # Recent news
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self.base_url, params=params)
                response.raise_for_status()
                data = response.json()

                return data.get("news_results", [])

        except httpx.HTTPError as e:
            logger.error(f"SerpApi request failed: {e}")
            raise


class NewsAggregator:
    """
    Unified news aggregator that combines multiple sources.
    Section 4.1 - Automated News Aggregation
    """

    def __init__(self):
        """Initialize news aggregator with all available clients."""
        self.newsapi = NewsAPIClient() if settings.NEWS_API_KEY else None
        self.serpapi = SerpAPIClient() if settings.SERP_API_KEY else None

    async def get_stock_news(
        self,
        ticker: str,
        company_name: str,
        days_back: int = 7,
        max_results: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get news for a specific stock from all sources.
        Section 4.1.1 - Aggregate news for stocks

        Args:
            ticker: Stock ticker
            company_name: Company name
            days_back: Number of days to look back
            max_results: Maximum results to return

        Returns:
            list: Aggregated news articles
        """
        articles = []
        from_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        # Search query
        query = f"{ticker} OR {company_name}"

        # Try NewsAPI
        if self.newsapi:
            try:
                newsapi_articles = await self.newsapi.search_news(
                    query=query,
                    from_date=from_date,
                    page_size=max_results
                )
                # Normalize format
                for article in newsapi_articles:
                    articles.append({
                        "source": "newsapi",
                        "source_name": article.get("source", {}).get("name", "Unknown"),
                        "title": article.get("title"),
                        "summary": article.get("description"),
                        "content": article.get("content"),
                        "url": article.get("url"),
                        "image_url": article.get("urlToImage"),
                        "published_at": article.get("publishedAt"),
                        "author": article.get("author")
                    })
            except Exception as e:
                logger.error(f"NewsAPI fetch failed: {e}")

        # Try SerpAPI
        if self.serpapi and len(articles) < max_results:
            try:
                serpapi_articles = await self.serpapi.search_google_news(
                    query=query,
                    num_results=max_results - len(articles)
                )
                # Normalize format
                for article in serpapi_articles:
                    articles.append({
                        "source": "serpapi",
                        "source_name": article.get("source", {}).get("name", "Google News"),
                        "title": article.get("title"),
                        "summary": article.get("snippet"),
                        "content": None,
                        "url": article.get("link"),
                        "image_url": article.get("thumbnail"),
                        "published_at": article.get("date"),
                        "author": None
                    })
            except Exception as e:
                logger.error(f"SerpAPI fetch failed: {e}")

        # Sort by published date (most recent first)
        articles.sort(
            key=lambda x: x.get("published_at", ""),
            reverse=True
        )

        return articles[:max_results]

    async def get_market_news(
        self,
        category: str = "business",
        max_results: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get general market news.

        Args:
            category: News category
            max_results: Maximum results

        Returns:
            list: Market news articles
        """
        articles = []

        # Try NewsAPI
        if self.newsapi:
            try:
                newsapi_articles = await self.newsapi.get_top_headlines(
                    category=category,
                    page_size=max_results
                )
                # Normalize format
                for article in newsapi_articles:
                    articles.append({
                        "source": "newsapi",
                        "source_name": article.get("source", {}).get("name", "Unknown"),
                        "title": article.get("title"),
                        "summary": article.get("description"),
                        "content": article.get("content"),
                        "url": article.get("url"),
                        "image_url": article.get("urlToImage"),
                        "published_at": article.get("publishedAt"),
                        "author": article.get("author")
                    })
            except Exception as e:
                logger.error(f"Market news fetch failed: {e}")

        return articles[:max_results]


# Global client instances
_news_aggregator: Optional[NewsAggregator] = None


def get_news_aggregator() -> NewsAggregator:
    """
    Get or create global news aggregator instance.

    Returns:
        NewsAggregator: News aggregator instance
    """
    global _news_aggregator
    if _news_aggregator is None:
        _news_aggregator = NewsAggregator()
    return _news_aggregator
