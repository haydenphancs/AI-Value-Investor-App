"""
Service Base Classes - Foundation for Business Logic Layer
==========================================================

This module provides base classes for all services in the application.
Services encapsulate business logic and coordinate between:
- Database operations (via Supabase client)
- External APIs (FMP, Gemini, etc.)
- AI Agents

Design Principles:
1. **Single Responsibility**: Each service handles one domain (stocks, research, news)
2. **Dependency Injection**: Services receive dependencies via constructor
3. **Testability**: All dependencies are injectable, making mocking easy
4. **Logging**: Consistent logging across all services
5. **Error Handling**: Services return Result objects or raise domain exceptions

Usage:
    class StockService(BaseService):
        def __init__(self, supabase: Client, fmp_client: FMPClient):
            super().__init__(supabase)
            self.fmp = fmp_client

        async def get_stock(self, ticker: str) -> Result[Stock, NotFoundError]:
            # Business logic here
            pass

    # In endpoint
    @router.get("/stocks/{ticker}")
    async def get_stock(
        ticker: str,
        service: StockService = Depends(get_stock_service)
    ):
        result = await service.get_stock(ticker)
        return result.unwrap()
"""

from abc import ABC, abstractmethod
from typing import Optional, TypeVar, Generic, Dict, Any, List
from datetime import datetime
import logging

from supabase import Client

from app.core.exceptions import (
    AppException,
    NotFoundError,
    DatabaseError,
    ValidationError
)
from app.core.result import Result, Success, Failure, PaginatedData


# Type variable for entity types
T = TypeVar("T")


class BaseService(ABC):
    """
    Abstract base class for all services.

    Provides:
    - Supabase client access
    - Logging
    - Common helper methods
    - Standard error handling patterns

    All domain services should inherit from this class.
    """

    def __init__(self, supabase: Client):
        """
        Initialize base service.

        Args:
            supabase: Supabase client for database operations
        """
        self._supabase = supabase
        self._logger = logging.getLogger(self.__class__.__name__)

    @property
    def supabase(self) -> Client:
        """Get the Supabase client."""
        return self._supabase

    @property
    def logger(self) -> logging.Logger:
        """Get the logger for this service."""
        return self._logger

    # ========================================================================
    # Database Helper Methods
    # ========================================================================

    async def _get_by_id(
        self,
        table: str,
        id: str,
        select: str = "*",
        resource_type: str = "Resource"
    ) -> Result[Dict[str, Any], NotFoundError]:
        """
        Get a single record by ID.

        Args:
            table: Table name
            id: Record ID
            select: Columns to select
            resource_type: Type name for error message

        Returns:
            Result with record data or NotFoundError
        """
        try:
            result = self.supabase.table(table).select(select).eq("id", id).single().execute()

            if not result.data:
                return Failure(NotFoundError(resource_type, id))

            return Success(result.data)

        except Exception as e:
            self.logger.error(f"Database error getting {table}/{id}: {e}")
            return Failure(NotFoundError(resource_type, id))

    async def _get_by_id_for_user(
        self,
        table: str,
        id: str,
        user_id: str,
        select: str = "*",
        resource_type: str = "Resource"
    ) -> Result[Dict[str, Any], NotFoundError]:
        """
        Get a single record by ID with user ownership check.

        Args:
            table: Table name
            id: Record ID
            user_id: Owner user ID
            select: Columns to select
            resource_type: Type name for error message

        Returns:
            Result with record data or NotFoundError
        """
        try:
            result = (
                self.supabase.table(table)
                .select(select)
                .eq("id", id)
                .eq("user_id", user_id)
                .is_("deleted_at", "null")
                .single()
                .execute()
            )

            if not result.data:
                return Failure(NotFoundError(resource_type, id))

            return Success(result.data)

        except Exception as e:
            self.logger.error(f"Database error getting {table}/{id}: {e}")
            return Failure(NotFoundError(resource_type, id))

    async def _list_for_user(
        self,
        table: str,
        user_id: str,
        select: str = "*",
        order_by: str = "created_at",
        order_desc: bool = True,
        limit: int = 20,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None
    ) -> Result[List[Dict[str, Any]], DatabaseError]:
        """
        List records for a user with pagination.

        Args:
            table: Table name
            user_id: Owner user ID
            select: Columns to select
            order_by: Column to sort by
            order_desc: Sort descending
            limit: Maximum records
            offset: Records to skip
            filters: Additional filter conditions

        Returns:
            Result with list of records
        """
        try:
            query = (
                self.supabase.table(table)
                .select(select)
                .eq("user_id", user_id)
                .is_("deleted_at", "null")
            )

            # Apply additional filters
            if filters:
                for key, value in filters.items():
                    if value is not None:
                        query = query.eq(key, value)

            # Apply ordering and pagination
            query = query.order(order_by, desc=order_desc).range(offset, offset + limit - 1)

            result = query.execute()
            return Success(result.data or [])

        except Exception as e:
            self.logger.error(f"Database error listing {table}: {e}")
            return Failure(DatabaseError("list", str(e)))

    async def _count_for_user(
        self,
        table: str,
        user_id: str,
        filters: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Count records for a user.

        Args:
            table: Table name
            user_id: Owner user ID
            filters: Additional filter conditions

        Returns:
            Count of records
        """
        try:
            query = (
                self.supabase.table(table)
                .select("id", count="exact")
                .eq("user_id", user_id)
                .is_("deleted_at", "null")
            )

            if filters:
                for key, value in filters.items():
                    if value is not None:
                        query = query.eq(key, value)

            result = query.execute()
            return result.count or 0

        except Exception as e:
            self.logger.error(f"Database error counting {table}: {e}")
            return 0

    async def _create(
        self,
        table: str,
        data: Dict[str, Any]
    ) -> Result[Dict[str, Any], DatabaseError]:
        """
        Create a new record.

        Args:
            table: Table name
            data: Record data

        Returns:
            Result with created record
        """
        try:
            result = self.supabase.table(table).insert(data).execute()

            if not result.data:
                return Failure(DatabaseError("create", "Insert returned no data"))

            return Success(result.data[0])

        except Exception as e:
            self.logger.error(f"Database error creating {table}: {e}")
            return Failure(DatabaseError("create", str(e)))

    async def _update(
        self,
        table: str,
        id: str,
        data: Dict[str, Any],
        user_id: Optional[str] = None
    ) -> Result[Dict[str, Any], DatabaseError]:
        """
        Update an existing record.

        Args:
            table: Table name
            id: Record ID
            data: Updated data
            user_id: Optional user ID for ownership check

        Returns:
            Result with updated record
        """
        try:
            query = self.supabase.table(table).update(data).eq("id", id)

            if user_id:
                query = query.eq("user_id", user_id)

            result = query.execute()

            if not result.data:
                return Failure(DatabaseError("update", "Update returned no data"))

            return Success(result.data[0])

        except Exception as e:
            self.logger.error(f"Database error updating {table}/{id}: {e}")
            return Failure(DatabaseError("update", str(e)))

    async def _soft_delete(
        self,
        table: str,
        id: str,
        user_id: Optional[str] = None
    ) -> Result[bool, DatabaseError]:
        """
        Soft delete a record by setting deleted_at.

        Args:
            table: Table name
            id: Record ID
            user_id: Optional user ID for ownership check

        Returns:
            Result with success boolean
        """
        try:
            query = (
                self.supabase.table(table)
                .update({"deleted_at": datetime.utcnow().isoformat()})
                .eq("id", id)
            )

            if user_id:
                query = query.eq("user_id", user_id)

            result = query.execute()
            return Success(bool(result.data))

        except Exception as e:
            self.logger.error(f"Database error deleting {table}/{id}: {e}")
            return Failure(DatabaseError("delete", str(e)))

    # ========================================================================
    # Pagination Helpers
    # ========================================================================

    async def _paginated_list(
        self,
        table: str,
        user_id: str,
        page: int = 1,
        per_page: int = 20,
        select: str = "*",
        order_by: str = "created_at",
        order_desc: bool = True,
        filters: Optional[Dict[str, Any]] = None
    ) -> Result[PaginatedData[Dict[str, Any]], DatabaseError]:
        """
        Get paginated list of records.

        Args:
            table: Table name
            user_id: Owner user ID
            page: Page number (1-indexed)
            per_page: Records per page
            select: Columns to select
            order_by: Column to sort by
            order_desc: Sort descending
            filters: Additional filter conditions

        Returns:
            Result with PaginatedData
        """
        offset = (page - 1) * per_page

        # Get total count
        total_items = await self._count_for_user(table, user_id, filters)
        total_pages = (total_items + per_page - 1) // per_page if total_items > 0 else 1

        # Get items
        items_result = await self._list_for_user(
            table=table,
            user_id=user_id,
            select=select,
            order_by=order_by,
            order_desc=order_desc,
            limit=per_page,
            offset=offset,
            filters=filters
        )

        if items_result.is_failure:
            return Failure(items_result.error)

        return Success(PaginatedData(
            items=items_result.value,
            page=page,
            per_page=per_page,
            total_items=total_items,
            total_pages=total_pages
        ))

    # ========================================================================
    # Validation Helpers
    # ========================================================================

    def _validate_required(self, value: Any, field_name: str) -> None:
        """
        Validate that a required field is present.

        Args:
            value: Field value
            field_name: Field name for error message

        Raises:
            ValidationError: If field is missing
        """
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValidationError(
                message=f"Missing required field: {field_name}",
                user_message=f"Please provide a value for {field_name}.",
                field=field_name
            )

    def _validate_in_list(
        self,
        value: Any,
        valid_values: List[Any],
        field_name: str
    ) -> None:
        """
        Validate that a value is in a list of valid values.

        Args:
            value: Field value
            valid_values: List of valid values
            field_name: Field name for error message

        Raises:
            ValidationError: If value is not in list
        """
        if value not in valid_values:
            raise ValidationError(
                message=f"Invalid {field_name}: {value}",
                user_message=f"Please choose from: {', '.join(str(v) for v in valid_values)}.",
                field=field_name
            )


class CachedService(BaseService):
    """
    Base service with Redis caching support.

    Extends BaseService with methods for cached database operations.
    """

    def __init__(self, supabase: Client, cache_manager: Any):
        """
        Initialize cached service.

        Args:
            supabase: Supabase client
            cache_manager: Cache manager instance
        """
        super().__init__(supabase)
        self._cache = cache_manager

    @property
    def cache(self) -> Any:
        """Get the cache manager."""
        return self._cache

    async def _get_cached(
        self,
        cache_key: str,
        fetch_fn,
        ttl: int = 300
    ) -> Any:
        """
        Get data from cache or fetch and cache.

        Args:
            cache_key: Cache key
            fetch_fn: Async function to fetch data if not cached
            ttl: Cache TTL in seconds

        Returns:
            Cached or fetched data
        """
        # Try cache first
        cached = await self.cache.get(cache_key)
        if cached is not None:
            self.logger.debug(f"Cache hit: {cache_key}")
            return cached

        # Fetch and cache
        self.logger.debug(f"Cache miss: {cache_key}")
        data = await fetch_fn()

        if data is not None:
            await self.cache.set(cache_key, data, ttl=ttl)

        return data

    async def _invalidate_cache(self, pattern: str) -> None:
        """
        Invalidate cache entries matching pattern.

        Args:
            pattern: Cache key pattern (supports *)
        """
        await self.cache.delete_pattern(pattern)
        self.logger.debug(f"Cache invalidated: {pattern}")
