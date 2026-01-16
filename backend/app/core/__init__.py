"""
Core Module - Foundation Classes and Patterns
==============================================

This module contains the foundational patterns for the backend:

Exceptions:
- AppException: Base exception class
- Domain-specific exceptions (NotFoundError, ValidationError, etc.)

Result Pattern:
- Result, Success, Failure: Type-safe error handling

Service Pattern:
- BaseService: Base class for all services
- CachedService: Service with caching support

Dependency Injection:
- ServiceContainer: DI container for services
- get_container: Get global container instance

Usage:
    from app.core import (
        BaseService, Result, Success, Failure,
        NotFoundError, get_container
    )

    class MyService(BaseService):
        async def get_item(self, id: str) -> Result[Item, NotFoundError]:
            item = await self._get_by_id("items", id, "Item")
            return item
"""

# Exceptions
from app.core.exceptions import (
    # Error codes and actions
    ErrorCode,
    ErrorAction,
    # Base exception
    AppException,
    # Auth errors
    AuthenticationError,
    TokenExpiredError,
    AuthorizationError,
    # Business errors
    NotFoundError,
    InsufficientCreditsError,
    CreditLimitReachedError,
    DuplicateEntryError,
    OperationNotAllowedError,
    # Validation errors
    ValidationError,
    InvalidTickerError,
    InvalidPersonaError,
    # External service errors
    ExternalServiceError,
    GeminiError,
    FMPError,
    ServiceTimeoutError,
    # Rate limiting
    RateLimitError,
    # System errors
    DatabaseError,
    ConfigurationError,
)

# Result pattern
from app.core.result import (
    Result,
    Success,
    Failure,
    try_result,
    try_result_async,
    collect_results,
    first_success,
    PaginatedData,
    OperationResult,
)

# Service base classes
from app.core.service_base import (
    BaseService,
    CachedService,
)

# Dependency injection
from app.core.container import (
    ServiceContainer,
    get_container,
    # Service getters
    get_stock_service,
    get_research_service,
    get_news_service,
    get_user_service,
    get_chat_service,
    get_widget_service,
    get_agent_registry,
    # Testing utilities
    TestContainer,
    create_test_container,
)

__all__ = [
    # Error codes
    "ErrorCode",
    "ErrorAction",
    # Exceptions
    "AppException",
    "AuthenticationError",
    "TokenExpiredError",
    "AuthorizationError",
    "NotFoundError",
    "InsufficientCreditsError",
    "CreditLimitReachedError",
    "DuplicateEntryError",
    "OperationNotAllowedError",
    "ValidationError",
    "InvalidTickerError",
    "InvalidPersonaError",
    "ExternalServiceError",
    "GeminiError",
    "FMPError",
    "ServiceTimeoutError",
    "RateLimitError",
    "DatabaseError",
    "ConfigurationError",
    # Result pattern
    "Result",
    "Success",
    "Failure",
    "try_result",
    "try_result_async",
    "collect_results",
    "first_success",
    "PaginatedData",
    "OperationResult",
    # Service base
    "BaseService",
    "CachedService",
    # DI Container
    "ServiceContainer",
    "get_container",
    "get_stock_service",
    "get_research_service",
    "get_news_service",
    "get_user_service",
    "get_chat_service",
    "get_widget_service",
    "get_agent_registry",
    "TestContainer",
    "create_test_container",
]
