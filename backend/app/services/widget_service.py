"""
Widget Service
Generates and manages iOS home screen widget data.
Optimized for minimal payload and maximum battery efficiency.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import json

from supabase import Client

from app.agents.news_summarizer import NewsSummarizerAgent
from app.integrations.fmp import FMPClient
from app.schemas.widget import WidgetUpdate, WidgetTimeline, WidgetTimelineEntry
from app.schemas.common import SentimentType

logger = logging.getLogger(__name__)


class WidgetService:
    """
    Service for iOS widget data generation and management.
    Designed for efficiency - widgets should never wait.
    """

    # Emoji mappings for sentiments and trends
    SENTIMENT_EMOJIS = {
        SentimentType.BULLISH: "📈",
        SentimentType.BEARISH: "📉",
        SentimentType.NEUTRAL: "📊"
    }

    MARKET_EMOJIS = {
        "strong_up": "🚀",
        "up": "📈",
        "flat": "➡️",
        "down": "📉",
        "strong_down": "⚠️",
        "mixed": "🔄"
    }

    def __init__(
        self,
        supabase: Client,
        news_summarizer: Optional[NewsSummarizerAgent] = None,
        fmp_client: Optional[FMPClient] = None
    ):
        """
        Initialize widget service.

        Args:
            supabase: Supabase client
            news_summarizer: News summarizer agent
            fmp_client: FMP client for market data
        """
        self.supabase = supabase
        self.news_summarizer = news_summarizer or NewsSummarizerAgent()
        self.fmp_client = fmp_client or FMPClient()
        logger.info("WidgetService initialized")

    async def generate_widget_update(
        self,
        user_id: str,
        force_regenerate: bool = False
    ) -> WidgetUpdate:
        """
        Generate widget update for user.
        Uses cache if recent update exists (< 1 hour).

        Args:
            user_id: User ID
            force_regenerate: Force new generation

        Returns:
            WidgetUpdate: Widget data

        Example:
            update = await service.generate_widget_update("user-123")
        """
        try:
            logger.info(f"Generating widget update for user {user_id}")

            # Check cache (last update < 1 hour)
            if not force_regenerate:
                cached = await self._get_cached_update(user_id)
                if cached:
                    logger.info("Returning cached widget update")
                    return cached

            # Get user's watchlist
            watchlist_stocks = await self._get_user_watchlist_stocks(user_id)

            # Generate market summary
            market_data = await self._generate_market_summary(watchlist_stocks)

            # Create widget update
            update_data = {
                "user_id": user_id,
                "headline": market_data["headline"],
                "sentiment": market_data["sentiment"],
                "emoji": market_data["emoji"],
                "daily_trend": market_data["daily_trend"],
                "market_summary": market_data.get("market_summary"),
                "top_movers": market_data.get("top_movers"),
                "published_at": datetime.utcnow().isoformat(),
                "deep_link_url": market_data.get("deep_link_url")
            }

            # Save to database
            result = self.supabase.table("widget_updates").insert(update_data).execute()

            if not result.data:
                raise ValueError("Failed to save widget update")

            logger.info(f"Widget update generated: {market_data['headline'][:50]}...")

            return WidgetUpdate(**result.data[0])

        except Exception as e:
            logger.error(f"Widget generation failed: {e}", exc_info=True)
            # Return fallback widget
            return await self._create_fallback_widget(user_id)

    async def generate_widget_timeline(
        self,
        user_id: str,
        hours_ahead: int = 24
    ) -> WidgetTimeline:
        """
        Generate WidgetKit timeline with scheduled updates.
        Section 4.2 - Widget updates at least twice daily.

        Args:
            user_id: User ID
            hours_ahead: Hours to generate timeline for

        Returns:
            WidgetTimeline: Timeline for iOS WidgetKit

        Example:
            timeline = await service.generate_widget_timeline("user-123")
        """
        try:
            logger.info(f"Generating widget timeline for user {user_id}")

            entries = []

            # Entry 1: Current update
            current = await self.generate_widget_update(user_id)
            entries.append(WidgetTimelineEntry(
                date=datetime.utcnow(),
                headline=current.headline,
                sentiment=current.sentiment,
                emoji=current.emoji,
                daily_trend=current.daily_trend,
                deep_link_url=current.deep_link_url,
                priority=10
            ))

            # Entry 2: Market open update (7:30 AM MT = 9:30 AM ET)
            market_open_time = self._get_next_market_time("open")
            if market_open_time and market_open_time < datetime.utcnow() + timedelta(hours=hours_ahead):
                entries.append(WidgetTimelineEntry(
                    date=market_open_time,
                    headline="Markets Opening - Stay Informed",
                    sentiment=SentimentType.NEUTRAL,
                    emoji="🔔",
                    daily_trend="Market open",
                    priority=8
                ))

            # Entry 3: Market close update (2:00 PM MT = 4:00 PM ET)
            market_close_time = self._get_next_market_time("close")
            if market_close_time and market_close_time < datetime.utcnow() + timedelta(hours=hours_ahead):
                entries.append(WidgetTimelineEntry(
                    date=market_close_time,
                    headline="Markets Closing - Review Your Watchlist",
                    sentiment=SentimentType.NEUTRAL,
                    emoji="📊",
                    daily_trend="Market close",
                    priority=7
                ))

            # Calculate next reload
            next_reload = min([e.date for e in entries if e.date > datetime.utcnow()], default=None)

            if not next_reload:
                next_reload = datetime.utcnow() + timedelta(hours=1)

            timeline = WidgetTimeline(
                entries=sorted(entries, key=lambda x: x.date),
                reload_policy="atEnd",
                next_reload_date=next_reload,
                generated_at=datetime.utcnow(),
                valid_until=datetime.utcnow() + timedelta(hours=hours_ahead),
                user_id=user_id
            )

            logger.info(f"Generated timeline with {len(entries)} entries")

            return timeline

        except Exception as e:
            logger.error(f"Timeline generation failed: {e}", exc_info=True)
            raise

    async def _get_cached_update(self, user_id: str) -> Optional[WidgetUpdate]:
        """Get cached widget update if recent (< 1 hour)."""
        try:
            one_hour_ago = datetime.utcnow() - timedelta(hours=1)

            result = self.supabase.table("widget_updates").select("*").eq(
                "user_id", user_id
            ).not_.is_("published_at", "null").gte(
                "published_at", one_hour_ago.isoformat()
            ).order("published_at", desc=True).limit(1).execute()

            if result.data:
                return WidgetUpdate(**result.data[0])

            return None

        except Exception as e:
            logger.warning(f"Cache check failed: {e}")
            return None

    async def _get_user_watchlist_stocks(self, user_id: str) -> List[Dict[str, Any]]:
        """Get user's watchlist stocks."""
        try:
            result = self.supabase.table("watchlists").select(
                "stock:stocks(id, ticker, company_name, sector)"
            ).eq("user_id", user_id).execute()

            return [item["stock"] for item in result.data if item.get("stock")]

        except Exception as e:
            logger.error(f"Failed to get watchlist: {e}")
            return []

    async def _generate_market_summary(
        self,
        watchlist_stocks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Generate market summary for widget.

        Args:
            watchlist_stocks: User's watchlist

        Returns:
            dict: Market summary data
        """
        try:
            # Get latest breaking news for watchlist
            breaking_news = await self._get_breaking_news(watchlist_stocks)

            # Get market indices (S&P 500, Nasdaq, Dow)
            market_indices = await self._get_market_indices()

            # Determine overall market sentiment
            market_sentiment = self._calculate_market_sentiment(market_indices)

            # Generate headline
            if breaking_news:
                # Use breaking news as headline
                news_item = breaking_news[0]
                headline = news_item.get("title", "Market Update")[:100]
                sentiment = news_item.get("sentiment", "neutral")
            else:
                # Use market performance
                headline = self._generate_market_headline(market_indices)
                sentiment = market_sentiment

            # Get emoji
            emoji = self.SENTIMENT_EMOJIS.get(
                SentimentType(sentiment),
                self.MARKET_EMOJIS["flat"]
            )

            # Generate daily trend
            daily_trend = self._generate_daily_trend(market_indices)

            # Get top movers from watchlist
            top_movers = await self._get_top_movers(watchlist_stocks)

            # Build market summary
            market_summary = self._build_market_summary(
                market_indices,
                breaking_news[:3] if breaking_news else []
            )

            return {
                "headline": headline,
                "sentiment": sentiment,
                "emoji": emoji,
                "daily_trend": daily_trend,
                "market_summary": market_summary,
                "top_movers": top_movers,
                "deep_link_url": None  # Could link to app's news feed
            }

        except Exception as e:
            logger.error(f"Market summary generation failed: {e}")
            return self._get_fallback_summary()

    async def _get_breaking_news(
        self,
        watchlist_stocks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Get breaking news for watchlist stocks."""
        if not watchlist_stocks:
            return []

        try:
            stock_ids = [s["id"] for s in watchlist_stocks]

            result = self.supabase.table("breaking_news").select(
                "*, news:news_articles(title, sentiment, published_at)"
            ).in_("stock_id", stock_ids).filter(
                "expires_at", "gt", datetime.utcnow().isoformat()
            ).order("impact_score", desc=True).limit(5).execute()

            return [item["news"] for item in result.data if item.get("news")]

        except Exception as e:
            logger.warning(f"Failed to get breaking news: {e}")
            return []

    async def _get_market_indices(self) -> Dict[str, Any]:
        """Get major market indices."""
        try:
            # Fetch S&P 500, Nasdaq, Dow
            indices = {
                "SPY": None,  # S&P 500 ETF
                "QQQ": None,  # Nasdaq 100 ETF
                "DIA": None   # Dow Jones ETF
            }

            for ticker in indices.keys():
                try:
                    quote = await self.fmp_client.get_stock_price_quote(ticker)
                    indices[ticker] = {
                        "price": quote.get("price"),
                        "change": quote.get("change"),
                        "change_percent": quote.get("changesPercentage")
                    }
                except Exception as e:
                    logger.warning(f"Failed to get {ticker}: {e}")

            return indices

        except Exception as e:
            logger.error(f"Failed to get market indices: {e}")
            return {}

    def _calculate_market_sentiment(
        self,
        indices: Dict[str, Any]
    ) -> str:
        """Calculate overall market sentiment from indices."""
        if not indices:
            return "neutral"

        changes = []
        for data in indices.values():
            if data and data.get("change_percent") is not None:
                changes.append(float(data["change_percent"]))

        if not changes:
            return "neutral"

        avg_change = sum(changes) / len(changes)

        if avg_change > 1.0:
            return "bullish"
        elif avg_change < -1.0:
            return "bearish"
        else:
            return "neutral"

    def _generate_market_headline(self, indices: Dict[str, Any]) -> str:
        """Generate headline from market data."""
        if not indices or not any(indices.values()):
            return "Markets Update - Stay Informed"

        spy = indices.get("SPY")
        if spy and spy.get("change_percent") is not None:
            change = float(spy["change_percent"])

            if change > 1.5:
                return f"Markets Rally Strong, S&P 500 Up {abs(change):.1f}%"
            elif change > 0.5:
                return f"Markets Edge Higher, S&P 500 Up {abs(change):.1f}%"
            elif change < -1.5:
                return f"Markets Decline, S&P 500 Down {abs(change):.1f}%"
            elif change < -0.5:
                return f"Markets Dip, S&P 500 Down {abs(change):.1f}%"
            else:
                return "Markets Trade Mixed, Little Changed"

        return "Markets Update Available"

    def _generate_daily_trend(self, indices: Dict[str, Any]) -> str:
        """Generate daily trend summary."""
        if not indices:
            return "Market data updating..."

        spy = indices.get("SPY")
        if spy and spy.get("change_percent") is not None:
            change = float(spy["change_percent"])
            sign = "+" if change >= 0 else ""
            return f"S&P 500: {sign}{change:.2f}%"

        return "Market trend updating..."

    async def _get_top_movers(
        self,
        watchlist_stocks: List[Dict[str, Any]],
        limit: int = 3
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Get top gainers and losers from watchlist."""
        if not watchlist_stocks:
            return {"gainers": [], "losers": []}

        try:
            movers = []

            for stock in watchlist_stocks[:20]:  # Limit API calls
                try:
                    quote = await self.fmp_client.get_stock_price_quote(stock["ticker"])
                    if quote and quote.get("changesPercentage") is not None:
                        movers.append({
                            "ticker": stock["ticker"],
                            "company": stock["company_name"],
                            "change_percent": float(quote["changesPercentage"])
                        })
                except Exception as e:
                    logger.debug(f"Failed to get quote for {stock['ticker']}: {e}")

            # Sort
            movers.sort(key=lambda x: x["change_percent"], reverse=True)

            return {
                "gainers": movers[:limit],
                "losers": movers[-limit:][::-1]  # Reverse to show biggest losers first
            }

        except Exception as e:
            logger.error(f"Failed to get top movers: {e}")
            return {"gainers": [], "losers": []}

    def _build_market_summary(
        self,
        indices: Dict[str, Any],
        breaking_news: List[Dict[str, Any]]
    ) -> str:
        """Build market summary text."""
        parts = []

        # Indices summary
        if indices:
            spy = indices.get("SPY")
            if spy and spy.get("change_percent"):
                change = float(spy["change_percent"])
                direction = "up" if change > 0 else "down"
                parts.append(f"S&P 500 {direction} {abs(change):.1f}%")

        # Breaking news count
        if breaking_news:
            parts.append(f"{len(breaking_news)} breaking stories")

        if not parts:
            return "Market update available in app"

        return " • ".join(parts)

    def _get_next_market_time(self, time_type: str) -> Optional[datetime]:
        """Get next market open/close time (ET)."""
        now = datetime.utcnow()

        # Market hours: 9:30 AM - 4:00 PM ET (Mon-Fri)
        # Convert to MT: 7:30 AM - 2:00 PM MT

        if time_type == "open":
            target_hour = 14  # 9:30 AM ET = 14:30 UTC (approx)
        else:  # close
            target_hour = 21  # 4:00 PM ET = 21:00 UTC (approx)

        # Find next occurrence
        target = now.replace(hour=target_hour, minute=30, second=0, microsecond=0)

        # Skip weekends
        while target.weekday() >= 5:  # Saturday=5, Sunday=6
            target += timedelta(days=1)

        if target <= now:
            target += timedelta(days=1)
            while target.weekday() >= 5:
                target += timedelta(days=1)

        return target

    async def _create_fallback_widget(self, user_id: str) -> WidgetUpdate:
        """Create fallback widget on error."""
        fallback_data = {
            "user_id": user_id,
            "headline": "Welcome to AI Value Investor",
            "sentiment": "neutral",
            "emoji": "📊",
            "daily_trend": "Market data loading...",
            "market_summary": "Your personalized market summary will appear here.",
            "published_at": datetime.utcnow().isoformat()
        }

        result = self.supabase.table("widget_updates").insert(fallback_data).execute()

        return WidgetUpdate(**result.data[0]) if result.data else WidgetUpdate(**fallback_data)

    def _get_fallback_summary(self) -> Dict[str, Any]:
        """Get fallback summary data."""
        return {
            "headline": "Market Update Available",
            "sentiment": "neutral",
            "emoji": "📊",
            "daily_trend": "Loading...",
            "market_summary": "Check app for details",
            "top_movers": {"gainers": [], "losers": []}
        }

    async def bulk_generate_widgets(
        self,
        user_ids: List[str],
        max_concurrent: int = 10
    ) -> Dict[str, int]:
        """
        Generate widgets for multiple users (background job).

        Args:
            user_ids: List of user IDs
            max_concurrent: Maximum concurrent generations

        Returns:
            dict: Stats (successful, failed)
        """
        import asyncio

        logger.info(f"Bulk generating widgets for {len(user_ids)} users")

        semaphore = asyncio.Semaphore(max_concurrent)
        successful = 0
        failed = 0

        async def generate_with_semaphore(uid):
            nonlocal successful, failed
            async with semaphore:
                try:
                    await self.generate_widget_update(uid)
                    successful += 1
                except Exception as e:
                    logger.error(f"Widget generation failed for {uid}: {e}")
                    failed += 1

        await asyncio.gather(
            *[generate_with_semaphore(uid) for uid in user_ids],
            return_exceptions=True
        )

        logger.info(f"Bulk widget generation complete: {successful} successful, {failed} failed")

        return {"successful": successful, "failed": failed}
