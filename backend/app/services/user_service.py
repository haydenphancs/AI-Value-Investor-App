"""
User Service
Business logic for user management, credit tracking, and tier management.
Requirements: Section 5.5 - Usage Quotas and Credit System
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import asyncio

from supabase import Client

from app.schemas.common import UserTier
from app.config import settings

logger = logging.getLogger(__name__)


class UserService:
    """
    Service for user management and credit tracking.
    Section 5.5 - Enforces usage quotas per tier
    """

    # Credit limits per tier (Section 5.5.1)
    TIER_LIMITS = {
        UserTier.FREE: settings.FREE_TIER_DEEP_RESEARCH_LIMIT,  # 1
        UserTier.PRO: settings.PRO_TIER_DEEP_RESEARCH_LIMIT,  # 10
        UserTier.PREMIUM: settings.PREMIUM_TIER_DEEP_RESEARCH_LIMIT  # -1 (unlimited)
    }

    # Activity tracking
    ACTIVITY_TYPES = {
        "deep_research_generated": "Deep Research Report Generated",
        "news_viewed": "News Article Viewed",
        "widget_updated": "Widget Updated",
        "chat_message_sent": "Chat Message Sent",
        "content_uploaded": "Educational Content Uploaded",
        "tier_upgraded": "Tier Upgraded",
        "credits_reset": "Monthly Credits Reset"
    }

    def __init__(self, supabase: Client):
        """
        Initialize user service.

        Args:
            supabase: Supabase client
        """
        self.supabase = supabase
        logger.info("UserService initialized")

    async def check_user_credits(
        self,
        user_id: str,
        required_credits: int = 1
    ) -> bool:
        """
        Check if user has sufficient credits for deep research.
        Section 5.5 - Credit checking before expensive operations

        Args:
            user_id: User ID
            required_credits: Number of credits required (default: 1)

        Returns:
            bool: True if user has enough credits, False otherwise

        Example:
            has_credits = await service.check_user_credits(user_id="user-123")
            if not has_credits:
                raise HTTPException(403, "Insufficient credits")
        """
        try:
            logger.info(f"Checking credits for user {user_id}")

            # Get user data
            user = await self._get_user(user_id)

            if not user:
                logger.warning(f"User {user_id} not found")
                return False

            # Get user tier
            tier = UserTier(user.get("tier", "free"))
            tier_limit = self.TIER_LIMITS.get(tier, 1)

            # Premium/Unlimited tier
            if tier_limit == -1:
                logger.info(f"User {user_id} has unlimited credits (tier: {tier.value})")
                return True

            # Check usage
            used = user.get("monthly_deep_research_used", 0)
            remaining = tier_limit - used

            logger.info(
                f"User {user_id} credits: {remaining}/{tier_limit} remaining "
                f"(tier: {tier.value}, used: {used})"
            )

            return remaining >= required_credits

        except Exception as e:
            logger.error(f"Failed to check user credits: {e}", exc_info=True)
            # Fail open for better UX (could be fail closed for strict enforcement)
            return False

    async def decrement_credits(
        self,
        user_id: str,
        credits: int = 1,
        activity_type: str = "deep_research_generated",
        activity_metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Decrement user credits after successful operation.
        Section 5.5 - Credit deduction after report generation

        Args:
            user_id: User ID
            credits: Number of credits to decrement (default: 1)
            activity_type: Activity type for tracking
            activity_metadata: Optional metadata for activity log

        Returns:
            bool: True if successful, False otherwise

        Example:
            success = await service.decrement_credits(
                user_id="user-123",
                activity_metadata={"report_id": "report-456"}
            )
        """
        try:
            logger.info(f"Decrementing {credits} credits for user {user_id}")

            # Get current user
            user = await self._get_user(user_id)

            if not user:
                logger.error(f"User {user_id} not found")
                return False

            # Check if unlimited tier
            tier = UserTier(user.get("tier", "free"))
            if self.TIER_LIMITS.get(tier) == -1:
                logger.info(f"User {user_id} has unlimited credits, no decrement needed")
                # Still log activity
                await self._log_user_activity(
                    user_id=user_id,
                    activity_type=activity_type,
                    metadata=activity_metadata
                )
                return True

            # Update credits
            current_used = user.get("monthly_deep_research_used", 0)
            new_used = current_used + credits

            update_data = {
                "monthly_deep_research_used": new_used,
                "updated_at": datetime.utcnow().isoformat()
            }

            self.supabase.table("users").update(update_data).eq("id", user_id).execute()

            logger.info(f"User {user_id} credits updated: {current_used} → {new_used}")

            # Log activity
            await self._log_user_activity(
                user_id=user_id,
                activity_type=activity_type,
                metadata={
                    **(activity_metadata or {}),
                    "credits_used": credits,
                    "total_used": new_used
                }
            )

            return True

        except Exception as e:
            logger.error(f"Failed to decrement credits: {e}", exc_info=True)
            return False

    async def reset_monthly_credits(
        self,
        user_id: str
    ) -> bool:
        """
        Reset user's monthly credits.
        Section 5.5 - Monthly credit reset

        Args:
            user_id: User ID

        Returns:
            bool: True if successful

        Example:
            success = await service.reset_monthly_credits("user-123")
        """
        try:
            logger.info(f"Resetting monthly credits for user {user_id}")

            update_data = {
                "monthly_deep_research_used": 0,
                "last_credit_reset_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }

            self.supabase.table("users").update(update_data).eq("id", user_id).execute()

            # Log activity
            await self._log_user_activity(
                user_id=user_id,
                activity_type="credits_reset",
                metadata={"reset_at": datetime.utcnow().isoformat()}
            )

            logger.info(f"Credits reset for user {user_id}")

            return True

        except Exception as e:
            logger.error(f"Failed to reset credits: {e}", exc_info=True)
            return False

    async def bulk_reset_monthly_credits(
        self,
        max_concurrent: int = 50
    ) -> Dict[str, int]:
        """
        Reset credits for all users (scheduled monthly job).
        Section 5.5 - Monthly credit reset for all users

        Args:
            max_concurrent: Maximum concurrent resets

        Returns:
            dict: Statistics (successful, failed, total)

        Example:
            stats = await service.bulk_reset_monthly_credits()
            # {"successful": 1234, "failed": 5, "total": 1239}
        """
        try:
            logger.info("Starting bulk credit reset for all users")

            # Get all users who need reset
            # Reset if last reset was > 30 days ago or never reset
            cutoff_date = (datetime.utcnow() - timedelta(days=30)).isoformat()

            users = self.supabase.table("users").select("id").or_(
                f"last_credit_reset_at.is.null,last_credit_reset_at.lt.{cutoff_date}"
            ).execute()

            if not users.data:
                logger.info("No users need credit reset")
                return {"successful": 0, "failed": 0, "total": 0}

            user_ids = [u["id"] for u in users.data]
            logger.info(f"Resetting credits for {len(user_ids)} users")

            # Process in batches
            semaphore = asyncio.Semaphore(max_concurrent)
            successful = 0
            failed = 0

            async def reset_with_semaphore(uid):
                nonlocal successful, failed
                async with semaphore:
                    try:
                        await self.reset_monthly_credits(uid)
                        successful += 1
                    except Exception as e:
                        logger.error(f"Failed to reset credits for {uid}: {e}")
                        failed += 1

            # Execute resets
            await asyncio.gather(*[reset_with_semaphore(uid) for uid in user_ids])

            stats = {
                "successful": successful,
                "failed": failed,
                "total": len(user_ids)
            }

            logger.info(f"Bulk credit reset completed: {stats}")

            return stats

        except Exception as e:
            logger.error(f"Bulk credit reset failed: {e}", exc_info=True)
            return {"successful": 0, "failed": 0, "total": 0}

    async def get_user_stats(
        self,
        user_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get comprehensive user statistics.

        Args:
            user_id: User ID

        Returns:
            dict: User statistics including credits, tier, usage

        Example:
            stats = await service.get_user_stats("user-123")
            # {
            #     "tier": "pro",
            #     "credits_remaining": 7,
            #     "credits_total": 10,
            #     "days_until_reset": 15,
            #     "total_reports_generated": 23,
            #     ...
            # }
        """
        try:
            user = await self._get_user(user_id)

            if not user:
                return None

            # Calculate credits
            tier = UserTier(user.get("tier", "free"))
            tier_limit = self.TIER_LIMITS.get(tier, 1)
            used = user.get("monthly_deep_research_used", 0)

            if tier_limit == -1:
                credits_remaining = -1  # Unlimited
                credits_total = -1
            else:
                credits_remaining = max(0, tier_limit - used)
                credits_total = tier_limit

            # Calculate days until reset
            last_reset = user.get("last_credit_reset_at")
            if last_reset:
                last_reset_date = datetime.fromisoformat(last_reset.replace("Z", "+00:00"))
                next_reset = last_reset_date + timedelta(days=30)
                days_until_reset = max(0, (next_reset - datetime.utcnow()).days)
            else:
                days_until_reset = 0

            # Get activity counts
            activities = await self._get_user_activity_counts(user_id)

            return {
                "user_id": user_id,
                "email": user.get("email"),
                "tier": tier.value,
                "tier_display": tier.value.title(),
                "is_premium": tier in [UserTier.PRO, UserTier.PREMIUM],
                "credits_remaining": credits_remaining,
                "credits_used": used,
                "credits_total": credits_total,
                "credits_percentage": (used / tier_limit * 100) if tier_limit > 0 else 0,
                "days_until_reset": days_until_reset,
                "last_reset_at": last_reset,
                "account_created_at": user.get("created_at"),
                "total_reports_generated": activities.get("deep_research_generated", 0),
                "total_chat_messages": activities.get("chat_message_sent", 0),
                "total_news_viewed": activities.get("news_viewed", 0),
                "last_activity_at": user.get("last_login_at")
            }

        except Exception as e:
            logger.error(f"Failed to get user stats: {e}", exc_info=True)
            return None

    async def upgrade_user_tier(
        self,
        user_id: str,
        new_tier: UserTier,
        reset_credits: bool = True
    ) -> bool:
        """
        Upgrade/downgrade user tier.

        Args:
            user_id: User ID
            new_tier: New tier to assign
            reset_credits: Whether to reset credits on upgrade (default: True)

        Returns:
            bool: True if successful

        Example:
            success = await service.upgrade_user_tier(
                user_id="user-123",
                new_tier=UserTier.PRO
            )
        """
        try:
            logger.info(f"Upgrading user {user_id} to {new_tier.value}")

            update_data = {
                "tier": new_tier.value,
                "updated_at": datetime.utcnow().isoformat()
            }

            if reset_credits:
                update_data["monthly_deep_research_used"] = 0
                update_data["last_credit_reset_at"] = datetime.utcnow().isoformat()

            self.supabase.table("users").update(update_data).eq("id", user_id).execute()

            # Log activity
            await self._log_user_activity(
                user_id=user_id,
                activity_type="tier_upgraded",
                metadata={
                    "new_tier": new_tier.value,
                    "credits_reset": reset_credits
                }
            )

            logger.info(f"User {user_id} upgraded to {new_tier.value}")

            return True

        except Exception as e:
            logger.error(f"Failed to upgrade user tier: {e}", exc_info=True)
            return False

    async def track_activity(
        self,
        user_id: str,
        activity_type: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Track user activity (lightweight wrapper for _log_user_activity).

        Args:
            user_id: User ID
            activity_type: Activity type (e.g., "news_viewed", "chat_message_sent")
            metadata: Optional metadata

        Returns:
            bool: True if logged

        Example:
            await service.track_activity(
                user_id="user-123",
                activity_type="news_viewed",
                metadata={"article_id": "article-456"}
            )
        """
        return await self._log_user_activity(user_id, activity_type, metadata)

    async def check_tier_features(
        self,
        user_id: str,
        feature: str
    ) -> bool:
        """
        Check if user's tier has access to a feature.

        Args:
            user_id: User ID
            feature: Feature name

        Returns:
            bool: True if user has access

        Feature flags by tier:
        - FREE: basic_news, basic_chat
        - PRO: deep_research, advanced_chat, widgets
        - PREMIUM: everything + priority_support, custom_personas
        """
        try:
            user = await self._get_user(user_id)

            if not user:
                return False

            tier = UserTier(user.get("tier", "free"))

            # Define feature access
            feature_access = {
                "basic_news": [UserTier.FREE, UserTier.PRO, UserTier.PREMIUM],
                "basic_chat": [UserTier.FREE, UserTier.PRO, UserTier.PREMIUM],
                "deep_research": [UserTier.PRO, UserTier.PREMIUM],
                "advanced_chat": [UserTier.PRO, UserTier.PREMIUM],
                "widgets": [UserTier.PRO, UserTier.PREMIUM],
                "educational_rag": [UserTier.PRO, UserTier.PREMIUM],
                "priority_support": [UserTier.PREMIUM],
                "custom_personas": [UserTier.PREMIUM],
                "unlimited_credits": [UserTier.PREMIUM]
            }

            allowed_tiers = feature_access.get(feature, [])

            return tier in allowed_tiers

        except Exception as e:
            logger.error(f"Failed to check tier features: {e}", exc_info=True)
            return False

    # Private helper methods

    async def _get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by ID."""
        try:
            result = self.supabase.table("users").select("*").eq("id", user_id).single().execute()
            return result.data
        except Exception as e:
            logger.error(f"Failed to get user: {e}")
            return None

    async def _log_user_activity(
        self,
        user_id: str,
        activity_type: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Log user activity to user_activity table.

        Args:
            user_id: User ID
            activity_type: Activity type
            metadata: Optional metadata

        Returns:
            bool: True if logged
        """
        try:
            activity_data = {
                "user_id": user_id,
                "activity_type": activity_type,
                "activity_description": self.ACTIVITY_TYPES.get(
                    activity_type,
                    activity_type.replace("_", " ").title()
                ),
                "metadata": metadata or {},
                "created_at": datetime.utcnow().isoformat()
            }

            self.supabase.table("user_activity").insert(activity_data).execute()

            return True

        except Exception as e:
            logger.warning(f"Failed to log user activity: {e}")
            # Don't fail the main operation if logging fails
            return False

    async def _get_user_activity_counts(
        self,
        user_id: str
    ) -> Dict[str, int]:
        """
        Get activity counts by type for a user.

        Args:
            user_id: User ID

        Returns:
            dict: Activity counts by type
        """
        try:
            # Get all activities
            result = self.supabase.table("user_activity").select(
                "activity_type"
            ).eq("user_id", user_id).execute()

            # Count by type
            counts = {}
            for activity in result.data:
                activity_type = activity["activity_type"]
                counts[activity_type] = counts.get(activity_type, 0) + 1

            return counts

        except Exception as e:
            logger.warning(f"Failed to get activity counts: {e}")
            return {}

    async def get_low_credit_users(
        self,
        threshold_percentage: float = 0.8
    ) -> List[Dict[str, Any]]:
        """
        Get users who are running low on credits (for notifications).

        Args:
            threshold_percentage: Percentage threshold (0.8 = 80% used)

        Returns:
            list: Users with low credits

        Example:
            low_credit_users = await service.get_low_credit_users(threshold_percentage=0.9)
            # Returns users who've used 90%+ of their credits
        """
        try:
            # Get all non-premium users
            users = self.supabase.table("users").select(
                "id, email, tier, monthly_deep_research_used"
            ).neq("tier", "premium").execute()

            low_credit_users = []

            for user in users.data:
                tier = UserTier(user.get("tier", "free"))
                tier_limit = self.TIER_LIMITS.get(tier, 1)

                if tier_limit == -1:  # Skip unlimited
                    continue

                used = user.get("monthly_deep_research_used", 0)
                usage_percentage = used / tier_limit if tier_limit > 0 else 0

                if usage_percentage >= threshold_percentage:
                    low_credit_users.append({
                        "user_id": user["id"],
                        "email": user["email"],
                        "tier": tier.value,
                        "credits_used": used,
                        "credits_total": tier_limit,
                        "usage_percentage": usage_percentage * 100
                    })

            logger.info(f"Found {len(low_credit_users)} users with low credits")

            return low_credit_users

        except Exception as e:
            logger.error(f"Failed to get low credit users: {e}", exc_info=True)
            return []
