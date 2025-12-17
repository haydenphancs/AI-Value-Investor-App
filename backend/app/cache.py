"""
Redis Cache Module
Centralized caching for API responses, widget data, and computed results.
Section 5.1 - Performance optimizations
"""

import logging
import json
import hashlib
from typing import Optional, Any, Callable
from datetime import timedelta
import asyncio

import redis.asyncio as redis
from redis.asyncio import Redis
from functools import wraps

from app.config import settings

logger = logging.getLogger(__name__)


class CacheManager:
    """
    Redis cache manager with async support.
    Handles all caching operations with proper serialization.
    """

    def __init__(self):
        """Initialize cache manager."""
        self._redis: Optional[Redis] = None
        self.default_ttl = settings.CACHE_TTL_SECONDS
        logger.info("CacheManager initialized")

    async def connect(self):
        """
        Connect to Redis.
        Called during application startup.
        """
        try:
            self._redis = await redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5
            )

            # Test connection
            await self._redis.ping()
            logger.info(f"✓ Redis connection established: {settings.REDIS_URL}")

        except Exception as e:
            logger.error(f"✗ Failed to connect to Redis: {e}")
            logger.warning("Running WITHOUT caching - Redis unavailable")
            self._redis = None

    async def disconnect(self):
        """
        Disconnect from Redis.
        Called during application shutdown.
        """
        if self._redis:
            await self._redis.close()
            logger.info("Redis connection closed")

    @property
    def is_connected(self) -> bool:
        """Check if Redis is connected."""
        return self._redis is not None

    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None
        """
        if not self.is_connected:
            return None

        try:
            value = await self._redis.get(key)

            if value is None:
                logger.debug(f"Cache miss: {key}")
                return None

            logger.debug(f"Cache hit: {key}")

            # Try to deserialize JSON
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value

        except Exception as e:
            logger.error(f"Cache get error for {key}: {e}")
            return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None
    ) -> bool:
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds (None = default TTL)

        Returns:
            bool: True if successful
        """
        if not self.is_connected:
            return False

        try:
            # Serialize value
            if isinstance(value, (dict, list)):
                serialized = json.dumps(value)
            else:
                serialized = str(value)

            # Set with TTL
            ttl = ttl or self.default_ttl
            await self._redis.setex(key, ttl, serialized)

            logger.debug(f"Cache set: {key} (TTL: {ttl}s)")
            return True

        except Exception as e:
            logger.error(f"Cache set error for {key}: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """
        Delete value from cache.

        Args:
            key: Cache key

        Returns:
            bool: True if deleted
        """
        if not self.is_connected:
            return False

        try:
            deleted = await self._redis.delete(key)
            logger.debug(f"Cache delete: {key} (deleted: {deleted})")
            return bool(deleted)

        except Exception as e:
            logger.error(f"Cache delete error for {key}: {e}")
            return False

    async def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern.

        Args:
            pattern: Key pattern (e.g., "user:123:*")

        Returns:
            int: Number of keys deleted
        """
        if not self.is_connected:
            return 0

        try:
            keys = await self._redis.keys(pattern)

            if not keys:
                return 0

            deleted = await self._redis.delete(*keys)
            logger.info(f"Cache pattern delete: {pattern} (deleted: {deleted} keys)")
            return deleted

        except Exception as e:
            logger.error(f"Cache pattern delete error for {pattern}: {e}")
            return 0

    async def exists(self, key: str) -> bool:
        """
        Check if key exists in cache.

        Args:
            key: Cache key

        Returns:
            bool: True if exists
        """
        if not self.is_connected:
            return False

        try:
            exists = await self._redis.exists(key)
            return bool(exists)

        except Exception as e:
            logger.error(f"Cache exists error for {key}: {e}")
            return False

    async def increment(self, key: str, amount: int = 1, ttl: Optional[int] = None) -> Optional[int]:
        """
        Increment a counter in cache.

        Args:
            key: Cache key
            amount: Amount to increment
            ttl: Time to live (set on first increment)

        Returns:
            int: New value or None
        """
        if not self.is_connected:
            return None

        try:
            new_value = await self._redis.incrby(key, amount)

            # Set TTL if this is a new key
            if new_value == amount and ttl:
                await self._redis.expire(key, ttl)

            return new_value

        except Exception as e:
            logger.error(f"Cache increment error for {key}: {e}")
            return None

    async def get_ttl(self, key: str) -> Optional[int]:
        """
        Get remaining TTL for a key.

        Args:
            key: Cache key

        Returns:
            int: Remaining TTL in seconds or None
        """
        if not self.is_connected:
            return None

        try:
            ttl = await self._redis.ttl(key)

            if ttl == -2:  # Key doesn't exist
                return None
            if ttl == -1:  # Key exists but has no TTL
                return None

            return ttl

        except Exception as e:
            logger.error(f"Cache TTL error for {key}: {e}")
            return None

    async def flush_all(self) -> bool:
        """
        Flush all cache (use with caution).

        Returns:
            bool: True if successful
        """
        if not self.is_connected:
            return False

        try:
            await self._redis.flushdb()
            logger.warning("Cache flushed (all keys deleted)")
            return True

        except Exception as e:
            logger.error(f"Cache flush error: {e}")
            return False

    def make_key(self, *parts: Any) -> str:
        """
        Create a cache key from parts.

        Args:
            *parts: Key components

        Returns:
            str: Cache key

        Example:
            key = cache.make_key("user", 123, "stats")
            # Returns: "user:123:stats"
        """
        return ":".join(str(part) for part in parts)

    def make_hash_key(self, prefix: str, data: dict) -> str:
        """
        Create a cache key with hash of data.

        Args:
            prefix: Key prefix
            data: Data to hash

        Returns:
            str: Cache key with hash

        Example:
            key = cache.make_hash_key("query", {"ticker": "AAPL", "period": "annual"})
            # Returns: "query:a1b2c3d4..."
        """
        # Create deterministic hash of data
        data_str = json.dumps(data, sort_keys=True)
        data_hash = hashlib.md5(data_str.encode()).hexdigest()[:12]
        return f"{prefix}:{data_hash}"


# Global cache manager instance
cache_manager = CacheManager()


# Cache Key Patterns
# ==================

class CacheKeys:
    """Cache key patterns for consistent naming."""

    # Widget data
    WIDGET_UPDATE = "widget:update:{user_id}"
    WIDGET_TIMELINE = "widget:timeline:{user_id}"

    # User data
    USER_STATS = "user:stats:{user_id}"
    USER_CREDITS = "user:credits:{user_id}"

    # Stock data
    STOCK_QUOTE = "stock:quote:{ticker}"
    STOCK_FUNDAMENTALS = "stock:fundamentals:{ticker}"
    STOCK_NEWS = "stock:news:{stock_id}"

    # News data
    BREAKING_NEWS = "news:breaking:{stock_id}"
    MARKET_NEWS = "news:market"

    # Research reports
    REPORT = "report:{report_id}"
    USER_REPORTS = "user:reports:{user_id}"

    # API responses
    API_RESPONSE = "api:{endpoint}:{params_hash}"

    @staticmethod
    def format(pattern: str, **kwargs) -> str:
        """Format a cache key pattern with values."""
        return pattern.format(**kwargs)


# Convenience functions
# =====================

async def get_cached(key: str) -> Optional[Any]:
    """Get value from cache (convenience function)."""
    return await cache_manager.get(key)


async def set_cached(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    """Set value in cache (convenience function)."""
    return await cache_manager.set(key, value, ttl)


async def delete_cached(key: str) -> bool:
    """Delete value from cache (convenience function)."""
    return await cache_manager.delete(key)


async def invalidate_user_cache(user_id: str):
    """
    Invalidate all cache for a user.

    Args:
        user_id: User ID
    """
    patterns = [
        f"user:{user_id}:*",
        f"widget:*:{user_id}",
        f"report:*:{user_id}"
    ]

    for pattern in patterns:
        await cache_manager.delete_pattern(pattern)

    logger.info(f"Cache invalidated for user {user_id}")


async def invalidate_stock_cache(stock_id: str):
    """
    Invalidate all cache for a stock.

    Args:
        stock_id: Stock ID
    """
    await cache_manager.delete_pattern(f"stock:*:{stock_id}")
    logger.info(f"Cache invalidated for stock {stock_id}")
