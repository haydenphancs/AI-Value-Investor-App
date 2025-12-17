"""
Cache Decorators
Function decorators for automatic caching of API responses and computations.
"""

import logging
import hashlib
import json
from typing import Optional, Callable, Any
from functools import wraps
import asyncio
import inspect

from app.cache import cache_manager

logger = logging.getLogger(__name__)


def cached(
    ttl: Optional[int] = None,
    key_prefix: Optional[str] = None,
    key_builder: Optional[Callable] = None
):
    """
    Decorator for caching function results.

    Args:
        ttl: Time to live in seconds (None = default)
        key_prefix: Prefix for cache key (default: function name)
        key_builder: Custom function to build cache key from args/kwargs

    Example:
        @cached(ttl=3600, key_prefix="user_stats")
        async def get_user_stats(user_id: str):
            # Expensive computation
            return stats
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Skip caching if not connected
            if not cache_manager.is_connected:
                return await func(*args, **kwargs)

            # Build cache key
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                prefix = key_prefix or func.__name__
                key_parts = _build_key_from_args(args, kwargs)
                cache_key = f"{prefix}:{key_parts}"

            # Try to get from cache
            cached_value = await cache_manager.get(cache_key)

            if cached_value is not None:
                logger.debug(f"Cache hit for {cache_key}")
                return cached_value

            # Execute function
            result = await func(*args, **kwargs)

            # Cache result
            if result is not None:
                await cache_manager.set(cache_key, result, ttl=ttl)
                logger.debug(f"Cached result for {cache_key}")

            return result

        return wrapper

    return decorator


def cached_with_invalidation(
    ttl: Optional[int] = None,
    key_prefix: Optional[str] = None,
    invalidation_key: Optional[str] = None
):
    """
    Decorator for caching with invalidation support.

    Args:
        ttl: Time to live in seconds
        key_prefix: Prefix for cache key
        invalidation_key: Key pattern for bulk invalidation

    Example:
        @cached_with_invalidation(
            ttl=3600,
            key_prefix="user_reports",
            invalidation_key="user:{user_id}:reports:*"
        )
        async def get_user_reports(user_id: str):
            return reports
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if not cache_manager.is_connected:
                return await func(*args, **kwargs)

            # Build cache key
            prefix = key_prefix or func.__name__
            key_parts = _build_key_from_args(args, kwargs)
            cache_key = f"{prefix}:{key_parts}"

            # Try cache
            cached_value = await cache_manager.get(cache_key)

            if cached_value is not None:
                return cached_value

            # Execute and cache
            result = await func(*args, **kwargs)

            if result is not None:
                await cache_manager.set(cache_key, result, ttl=ttl)

                # Store invalidation mapping if provided
                if invalidation_key:
                    inv_key = invalidation_key.format(**kwargs)
                    await cache_manager.set(f"invalidation:{inv_key}", cache_key, ttl=ttl)

            return result

        # Add invalidation method
        async def invalidate(*args, **kwargs):
            """Invalidate cache for this function."""
            if invalidation_key:
                pattern = invalidation_key.format(**kwargs)
                await cache_manager.delete_pattern(pattern)
            else:
                prefix = key_prefix or func.__name__
                key_parts = _build_key_from_args(args, kwargs)
                cache_key = f"{prefix}:{key_parts}"
                await cache_manager.delete(cache_key)

        wrapper.invalidate = invalidate

        return wrapper

    return decorator


def rate_limited(
    max_calls: int,
    period: int,
    key_prefix: Optional[str] = None
):
    """
    Decorator for rate limiting using Redis.

    Args:
        max_calls: Maximum number of calls
        period: Time period in seconds
        key_prefix: Prefix for rate limit key

    Example:
        @rate_limited(max_calls=10, period=60)  # 10 calls per minute
        async def expensive_api_call(user_id: str):
            pass

    Raises:
        Exception: If rate limit exceeded
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if not cache_manager.is_connected:
                # Skip rate limiting if cache unavailable
                return await func(*args, **kwargs)

            # Build rate limit key
            prefix = key_prefix or f"rate_limit:{func.__name__}"
            key_parts = _build_key_from_args(args, kwargs)
            rate_key = f"{prefix}:{key_parts}"

            # Increment counter
            current = await cache_manager.increment(rate_key, amount=1, ttl=period)

            if current and current > max_calls:
                logger.warning(f"Rate limit exceeded for {rate_key}: {current}/{max_calls}")
                raise Exception(f"Rate limit exceeded. Try again in {period} seconds.")

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def memoize(ttl: Optional[int] = 3600):
    """
    Simple memoization decorator using cache.

    Args:
        ttl: Time to live in seconds (default: 1 hour)

    Example:
        @memoize(ttl=600)
        async def compute_heavy_metric(data: dict):
            # Heavy computation
            return result
    """
    return cached(ttl=ttl)


def cache_aside(
    get_from_db: Callable,
    ttl: Optional[int] = None,
    key_prefix: Optional[str] = None
):
    """
    Cache-aside pattern decorator.
    Tries cache first, falls back to database, then updates cache.

    Args:
        get_from_db: Function to get data from database
        ttl: Cache TTL
        key_prefix: Cache key prefix

    Example:
        async def get_user_from_db(user_id: str):
            return await db.query(...)

        @cache_aside(get_from_db=get_user_from_db, ttl=3600)
        async def get_user(user_id: str):
            pass  # Implementation handled by decorator
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Build cache key
            prefix = key_prefix or func.__name__
            key_parts = _build_key_from_args(args, kwargs)
            cache_key = f"{prefix}:{key_parts}"

            # Try cache first
            if cache_manager.is_connected:
                cached_value = await cache_manager.get(cache_key)

                if cached_value is not None:
                    logger.debug(f"Cache-aside hit: {cache_key}")
                    return cached_value

            # Get from database
            result = await get_from_db(*args, **kwargs)

            # Update cache
            if result is not None and cache_manager.is_connected:
                await cache_manager.set(cache_key, result, ttl=ttl)
                logger.debug(f"Cache-aside updated: {cache_key}")

            return result

        return wrapper

    return decorator


def _build_key_from_args(args: tuple, kwargs: dict) -> str:
    """
    Build a cache key from function arguments.

    Args:
        args: Positional arguments
        kwargs: Keyword arguments

    Returns:
        str: Cache key part
    """
    # Filter out 'self' and 'cls' from args
    filtered_args = []
    for arg in args:
        # Skip self/cls instances
        if not (inspect.isclass(type(arg)) and hasattr(arg, '__dict__')):
            filtered_args.append(arg)

    # Combine args and kwargs
    key_data = {
        "args": [str(a) for a in filtered_args],
        "kwargs": {k: str(v) for k, v in sorted(kwargs.items())}
    }

    # Create hash
    key_str = json.dumps(key_data, sort_keys=True)
    key_hash = hashlib.md5(key_str.encode()).hexdigest()[:12]

    return key_hash


# Utility decorators
# ==================

def invalidate_on_change(cache_pattern: str):
    """
    Decorator to invalidate cache when function completes successfully.

    Args:
        cache_pattern: Pattern of keys to invalidate

    Example:
        @invalidate_on_change("user:{user_id}:*")
        async def update_user(user_id: str, data: dict):
            # Update user
            pass
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Execute function
            result = await func(*args, **kwargs)

            # Invalidate cache on success
            if cache_manager.is_connected:
                pattern = cache_pattern.format(**kwargs)
                await cache_manager.delete_pattern(pattern)
                logger.debug(f"Cache invalidated: {pattern}")

            return result

        return wrapper

    return decorator


def warm_cache(loader: Callable, ttl: Optional[int] = 3600):
    """
    Decorator to warm cache on first call.

    Args:
        loader: Function to load initial data
        ttl: Cache TTL

    Example:
        async def load_initial_stocks():
            return await fetch_all_stocks()

        @warm_cache(loader=load_initial_stocks, ttl=3600)
        async def get_stocks():
            pass
    """

    def decorator(func: Callable) -> Callable:
        cache_warmed = False

        @wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal cache_warmed

            # Warm cache on first call
            if not cache_warmed and cache_manager.is_connected:
                try:
                    data = await loader()
                    cache_key = f"{func.__name__}:warmed"
                    await cache_manager.set(cache_key, data, ttl=ttl)
                    cache_warmed = True
                    logger.info(f"Cache warmed for {func.__name__}")
                except Exception as e:
                    logger.warning(f"Cache warming failed: {e}")

            return await func(*args, **kwargs)

        return wrapper

    return decorator
