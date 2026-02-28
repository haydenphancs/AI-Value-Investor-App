"""
User Service — credits from user_credits table.
"""

import logging
from typing import Optional

from app.database import get_supabase

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self):
        self.supabase = get_supabase()

    async def check_credits(self, user_id: str, required: int = 1) -> bool:
        """Check if user has sufficient credits."""
        try:
            result = self.supabase.table("user_credits").select(
                "remaining"
            ).eq("user_id", user_id).single().execute()

            if not result.data:
                return False

            return result.data["remaining"] >= required
        except Exception as e:
            logger.error(f"Credit check failed: {e}")
            return False

    async def decrement_credits(self, user_id: str, amount: int = 1) -> bool:
        """Decrement user credits after successful operation."""
        try:
            credits = self.supabase.table("user_credits").select(
                "id, used"
            ).eq("user_id", user_id).single().execute()

            if not credits.data:
                return False

            new_used = credits.data["used"] + amount
            self.supabase.table("user_credits").update({
                "used": new_used,
            }).eq("id", credits.data["id"]).execute()

            logger.info(f"Credits decremented for user {user_id}: +{amount}")
            return True
        except Exception as e:
            logger.error(f"Credit decrement failed: {e}")
            return False
