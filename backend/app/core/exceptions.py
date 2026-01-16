"""
Custom Exceptions - Unified Error Handling
==========================================

This module defines domain-specific exceptions that provide:
1. Structured error codes for client-side handling
2. User-friendly messages that can be displayed directly
3. HTTP status code mapping
4. Suggested actions for recovery

Usage:
    from app.core.exceptions import NotFoundError, ValidationError

    # Raising errors
    raise NotFoundError(
        message="Stock INVALID not found",
        user_message="We couldn't find that stock. Check the ticker symbol.",
        details={"ticker": "INVALID"}
    )

    # In endpoints, these are caught by the global exception handler
    # and converted to proper JSON responses.
"""

from enum import Enum
from typing import Optional, Dict, Any


class ErrorCode(str, Enum):
    """
    Standardized error codes for client-side handling.

    Format: {CATEGORY}_{NUMBER}
    - AUTH_1xxx: Authentication/Authorization errors
    - BIZ_2xxx: Business logic errors
    - VAL_3xxx: Validation errors
    - EXT_4xxx: External service errors
    - SYS_5xxx: System/Internal errors
    """

    # Authentication (1xxx)
    AUTH_TOKEN_EXPIRED = "AUTH_1001"
    AUTH_TOKEN_INVALID = "AUTH_1002"
    AUTH_UNAUTHORIZED = "AUTH_1003"
    AUTH_FORBIDDEN = "AUTH_1004"
    AUTH_REFRESH_FAILED = "AUTH_1005"

    # Business Logic (2xxx)
    BIZ_CREDITS_INSUFFICIENT = "BIZ_2001"
    BIZ_CREDITS_LIMIT_REACHED = "BIZ_2002"
    BIZ_REPORT_GENERATION_FAILED = "BIZ_2003"
    BIZ_STOCK_NOT_FOUND = "BIZ_2004"
    BIZ_USER_NOT_FOUND = "BIZ_2005"
    BIZ_REPORT_NOT_FOUND = "BIZ_2006"
    BIZ_WATCHLIST_LIMIT = "BIZ_2007"
    BIZ_DUPLICATE_ENTRY = "BIZ_2008"
    BIZ_OPERATION_NOT_ALLOWED = "BIZ_2009"
    BIZ_CHAT_SESSION_NOT_FOUND = "BIZ_2010"
    BIZ_CONTENT_NOT_FOUND = "BIZ_2011"

    # Validation (3xxx)
    VAL_INVALID_REQUEST = "VAL_3001"
    VAL_INVALID_TICKER = "VAL_3002"
    VAL_INVALID_PERSONA = "VAL_3003"
    VAL_INVALID_PERIOD = "VAL_3004"
    VAL_MISSING_FIELD = "VAL_3005"
    VAL_INVALID_FORMAT = "VAL_3006"

    # External Services (4xxx)
    EXT_GEMINI_ERROR = "EXT_4001"
    EXT_FMP_ERROR = "EXT_4002"
    EXT_SUPABASE_ERROR = "EXT_4003"
    EXT_NEWS_API_ERROR = "EXT_4004"
    EXT_SERVICE_TIMEOUT = "EXT_4005"
    EXT_RATE_LIMITED = "EXT_4006"

    # System (5xxx)
    SYS_INTERNAL_ERROR = "SYS_5001"
    SYS_DATABASE_ERROR = "SYS_5002"
    SYS_CACHE_ERROR = "SYS_5003"
    SYS_CONFIGURATION_ERROR = "SYS_5004"


class ErrorAction(str, Enum):
    """Suggested actions for error recovery."""
    RETRY = "retry"
    RETRY_LATER = "retry_later"
    UPGRADE = "upgrade"
    REAUTH = "reauth"
    CONTACT_SUPPORT = "contact_support"
    CHECK_INPUT = "check_input"
    GO_BACK = "go_back"
    NONE = "none"


class AppException(Exception):
    """
    Base exception for all application errors.

    All custom exceptions should inherit from this class to ensure
    consistent error handling and response formatting.

    Attributes:
        error_code: Structured error code for client handling
        message: Technical message for logging
        user_message: User-friendly message for display
        status_code: HTTP status code
        details: Additional context (dict)
        action: Suggested recovery action
        retry_after: Seconds to wait before retry (for rate limiting)
    """

    def __init__(
        self,
        error_code: ErrorCode,
        message: str,
        user_message: Optional[str] = None,
        status_code: int = 500,
        details: Optional[Dict[str, Any]] = None,
        action: ErrorAction = ErrorAction.NONE,
        retry_after: Optional[int] = None
    ):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.user_message = user_message or self._default_user_message()
        self.status_code = status_code
        self.details = details or {}
        self.action = action
        self.retry_after = retry_after

    def _default_user_message(self) -> str:
        """Provide a default user-friendly message."""
        return "Something went wrong. Please try again."

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for JSON response."""
        result = {
            "error_code": self.error_code.value,
            "message": self.message,
            "user_message": self.user_message,
            "action": self.action.value,
        }

        if self.details:
            result["details"] = self.details

        if self.retry_after:
            result["retry_after"] = self.retry_after

        return result

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.error_code.value}: {self.message})"


# ============================================================================
# Authentication Errors (401, 403)
# ============================================================================

class AuthenticationError(AppException):
    """User is not authenticated or token is invalid."""

    def __init__(
        self,
        message: str = "Authentication failed",
        user_message: str = "Please sign in to continue.",
        error_code: ErrorCode = ErrorCode.AUTH_UNAUTHORIZED,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            error_code=error_code,
            message=message,
            user_message=user_message,
            status_code=401,
            details=details,
            action=ErrorAction.REAUTH
        )


class TokenExpiredError(AuthenticationError):
    """JWT token has expired."""

    def __init__(self, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message="Token has expired",
            user_message="Your session has expired. Please sign in again.",
            error_code=ErrorCode.AUTH_TOKEN_EXPIRED,
            details=details
        )


class AuthorizationError(AppException):
    """User is authenticated but not authorized for this action."""

    def __init__(
        self,
        message: str = "Access denied",
        user_message: str = "You don't have permission to perform this action.",
        required_tier: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        _details = details or {}
        if required_tier:
            _details["required_tier"] = required_tier

        super().__init__(
            error_code=ErrorCode.AUTH_FORBIDDEN,
            message=message,
            user_message=user_message,
            status_code=403,
            details=_details,
            action=ErrorAction.UPGRADE if required_tier else ErrorAction.NONE
        )


# ============================================================================
# Business Logic Errors (400, 403, 404)
# ============================================================================

class NotFoundError(AppException):
    """Requested resource was not found."""

    def __init__(
        self,
        resource_type: str = "Resource",
        resource_id: Optional[str] = None,
        message: Optional[str] = None,
        user_message: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        _message = message or f"{resource_type} not found"
        _user_message = user_message or f"The requested {resource_type.lower()} could not be found."
        _details = details or {}

        if resource_id:
            _details["resource_id"] = resource_id
        _details["resource_type"] = resource_type

        # Map resource types to specific error codes
        error_code_map = {
            "Stock": ErrorCode.BIZ_STOCK_NOT_FOUND,
            "User": ErrorCode.BIZ_USER_NOT_FOUND,
            "Report": ErrorCode.BIZ_REPORT_NOT_FOUND,
            "ChatSession": ErrorCode.BIZ_CHAT_SESSION_NOT_FOUND,
            "Content": ErrorCode.BIZ_CONTENT_NOT_FOUND,
        }
        error_code = error_code_map.get(resource_type, ErrorCode.BIZ_STOCK_NOT_FOUND)

        super().__init__(
            error_code=error_code,
            message=_message,
            user_message=_user_message,
            status_code=404,
            details=_details,
            action=ErrorAction.GO_BACK
        )


class InsufficientCreditsError(AppException):
    """User doesn't have enough credits for the operation."""

    def __init__(
        self,
        required: int = 1,
        available: int = 0,
        user_tier: str = "free",
        details: Optional[Dict[str, Any]] = None
    ):
        _details = details or {}
        _details.update({
            "required_credits": required,
            "available_credits": available,
            "user_tier": user_tier
        })

        super().__init__(
            error_code=ErrorCode.BIZ_CREDITS_INSUFFICIENT,
            message=f"Insufficient credits: need {required}, have {available}",
            user_message="You don't have enough credits for this analysis. Upgrade your plan to get more.",
            status_code=403,
            details=_details,
            action=ErrorAction.UPGRADE
        )


class CreditLimitReachedError(AppException):
    """User has reached their monthly credit limit."""

    def __init__(
        self,
        limit: int,
        used: int,
        reset_date: Optional[str] = None,
        user_tier: str = "free",
        details: Optional[Dict[str, Any]] = None
    ):
        _details = details or {}
        _details.update({
            "limit": limit,
            "used": used,
            "user_tier": user_tier
        })
        if reset_date:
            _details["reset_date"] = reset_date

        super().__init__(
            error_code=ErrorCode.BIZ_CREDITS_LIMIT_REACHED,
            message=f"Monthly limit reached: {used}/{limit}",
            user_message=f"You've used all {limit} of your monthly research credits. Upgrade for more.",
            status_code=403,
            details=_details,
            action=ErrorAction.UPGRADE
        )


class DuplicateEntryError(AppException):
    """Attempted to create a duplicate entry."""

    def __init__(
        self,
        resource_type: str = "Item",
        message: Optional[str] = None,
        user_message: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            error_code=ErrorCode.BIZ_DUPLICATE_ENTRY,
            message=message or f"Duplicate {resource_type}",
            user_message=user_message or f"This {resource_type.lower()} already exists.",
            status_code=409,
            details=details,
            action=ErrorAction.CHECK_INPUT
        )


class OperationNotAllowedError(AppException):
    """The requested operation is not allowed in current state."""

    def __init__(
        self,
        operation: str,
        reason: str,
        user_message: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        _details = details or {}
        _details.update({"operation": operation, "reason": reason})

        super().__init__(
            error_code=ErrorCode.BIZ_OPERATION_NOT_ALLOWED,
            message=f"Operation '{operation}' not allowed: {reason}",
            user_message=user_message or f"This action cannot be performed right now.",
            status_code=400,
            details=_details,
            action=ErrorAction.GO_BACK
        )


# ============================================================================
# Validation Errors (400, 422)
# ============================================================================

class ValidationError(AppException):
    """Request validation failed."""

    def __init__(
        self,
        message: str = "Validation failed",
        user_message: str = "Please check your input and try again.",
        field: Optional[str] = None,
        error_code: ErrorCode = ErrorCode.VAL_INVALID_REQUEST,
        details: Optional[Dict[str, Any]] = None
    ):
        _details = details or {}
        if field:
            _details["field"] = field

        super().__init__(
            error_code=error_code,
            message=message,
            user_message=user_message,
            status_code=422,
            details=_details,
            action=ErrorAction.CHECK_INPUT
        )


class InvalidTickerError(ValidationError):
    """Invalid stock ticker symbol."""

    def __init__(self, ticker: str, details: Optional[Dict[str, Any]] = None):
        _details = details or {}
        _details["ticker"] = ticker

        super().__init__(
            message=f"Invalid ticker symbol: {ticker}",
            user_message=f"'{ticker}' is not a valid stock ticker. Please check and try again.",
            field="ticker",
            error_code=ErrorCode.VAL_INVALID_TICKER,
            details=_details
        )


class InvalidPersonaError(ValidationError):
    """Invalid investor persona specified."""

    def __init__(self, persona: str, valid_personas: list, details: Optional[Dict[str, Any]] = None):
        _details = details or {}
        _details.update({"persona": persona, "valid_personas": valid_personas})

        super().__init__(
            message=f"Invalid investor persona: {persona}",
            user_message=f"'{persona}' is not a valid analysis style. Choose from: {', '.join(valid_personas)}.",
            field="investor_persona",
            error_code=ErrorCode.VAL_INVALID_PERSONA,
            details=_details
        )


# ============================================================================
# External Service Errors (502, 503, 504)
# ============================================================================

class ExternalServiceError(AppException):
    """Error from an external service (Gemini, FMP, etc.)."""

    def __init__(
        self,
        service_name: str,
        message: str,
        user_message: Optional[str] = None,
        error_code: ErrorCode = ErrorCode.EXT_GEMINI_ERROR,
        is_retryable: bool = True,
        details: Optional[Dict[str, Any]] = None
    ):
        _details = details or {}
        _details["service"] = service_name
        _details["retryable"] = is_retryable

        super().__init__(
            error_code=error_code,
            message=f"{service_name} error: {message}",
            user_message=user_message or "We're having trouble connecting to an external service. Please try again.",
            status_code=502,
            details=_details,
            action=ErrorAction.RETRY if is_retryable else ErrorAction.RETRY_LATER
        )


class GeminiError(ExternalServiceError):
    """Error from Google Gemini API."""

    def __init__(
        self,
        message: str,
        is_retryable: bool = True,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            service_name="Gemini AI",
            message=message,
            user_message="Our AI service is temporarily unavailable. Please try again in a moment.",
            error_code=ErrorCode.EXT_GEMINI_ERROR,
            is_retryable=is_retryable,
            details=details
        )


class FMPError(ExternalServiceError):
    """Error from Financial Modeling Prep API."""

    def __init__(
        self,
        message: str,
        is_retryable: bool = True,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            service_name="Financial Data",
            message=message,
            user_message="Unable to fetch financial data. Please try again.",
            error_code=ErrorCode.EXT_FMP_ERROR,
            is_retryable=is_retryable,
            details=details
        )


class ServiceTimeoutError(ExternalServiceError):
    """External service timed out."""

    def __init__(
        self,
        service_name: str,
        timeout_seconds: int,
        details: Optional[Dict[str, Any]] = None
    ):
        _details = details or {}
        _details["timeout_seconds"] = timeout_seconds

        super().__init__(
            service_name=service_name,
            message=f"Service timed out after {timeout_seconds}s",
            user_message="The request took too long. Please try again.",
            error_code=ErrorCode.EXT_SERVICE_TIMEOUT,
            is_retryable=True,
            details=_details
        )


# ============================================================================
# Rate Limiting Errors (429)
# ============================================================================

class RateLimitError(AppException):
    """User has exceeded rate limits."""

    def __init__(
        self,
        limit: int,
        window_seconds: int,
        retry_after: int,
        details: Optional[Dict[str, Any]] = None
    ):
        _details = details or {}
        _details.update({
            "limit": limit,
            "window_seconds": window_seconds
        })

        super().__init__(
            error_code=ErrorCode.EXT_RATE_LIMITED,
            message=f"Rate limit exceeded: {limit} requests per {window_seconds}s",
            user_message=f"Too many requests. Please wait {retry_after} seconds.",
            status_code=429,
            details=_details,
            action=ErrorAction.RETRY_LATER,
            retry_after=retry_after
        )


# ============================================================================
# System Errors (500)
# ============================================================================

class DatabaseError(AppException):
    """Database operation failed."""

    def __init__(
        self,
        operation: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        _details = details or {}
        _details["operation"] = operation

        super().__init__(
            error_code=ErrorCode.SYS_DATABASE_ERROR,
            message=f"Database error during {operation}: {message}",
            user_message="We're experiencing technical difficulties. Please try again.",
            status_code=500,
            details=_details,
            action=ErrorAction.RETRY
        )


class ConfigurationError(AppException):
    """Application configuration error."""

    def __init__(
        self,
        config_key: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        _details = details or {}
        _details["config_key"] = config_key

        super().__init__(
            error_code=ErrorCode.SYS_CONFIGURATION_ERROR,
            message=f"Configuration error for {config_key}: {message}",
            user_message="The application is misconfigured. Please contact support.",
            status_code=500,
            details=_details,
            action=ErrorAction.CONTACT_SUPPORT
        )
