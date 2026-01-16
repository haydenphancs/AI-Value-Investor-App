"""
Result Pattern - Type-Safe Service Returns
==========================================

This module implements the Result pattern (also known as Either/Maybe pattern)
for type-safe error handling in service methods.

Instead of raising exceptions or returning None, services return Result objects
that explicitly indicate success or failure, making error handling more
predictable and composable.

Benefits:
1. Explicit error handling - No hidden exceptions
2. Composable - Can chain operations with map/flatmap
3. Type-safe - IDE understands the possible return types
4. Testable - Easy to mock and verify results

Usage:
    from app.core.result import Result, Success, Failure

    # In a service method
    async def get_stock(self, ticker: str) -> Result[Stock, NotFoundError]:
        stock = await self.db.get_stock(ticker)
        if stock:
            return Success(stock)
        return Failure(NotFoundError("Stock", ticker))

    # In a caller
    result = await stock_service.get_stock("AAPL")

    if result.is_success:
        stock = result.value
        # use stock
    else:
        error = result.error
        # handle error

    # Or use pattern matching (Python 3.10+)
    match result:
        case Success(stock):
            return stock.to_dict()
        case Failure(error):
            raise error
"""

from __future__ import annotations
from typing import TypeVar, Generic, Optional, Callable, Union, Any
from dataclasses import dataclass
from abc import ABC, abstractmethod

from app.core.exceptions import AppException


# Type variables for generic Result
T = TypeVar("T")  # Success value type
E = TypeVar("E", bound=AppException)  # Error type (must be AppException subclass)
U = TypeVar("U")  # Transformed value type


class Result(ABC, Generic[T, E]):
    """
    Abstract base class for Result pattern.

    A Result is either a Success containing a value, or a Failure containing an error.
    This forces explicit handling of both cases and makes error handling more predictable.
    """

    @property
    @abstractmethod
    def is_success(self) -> bool:
        """Check if result is a success."""
        pass

    @property
    @abstractmethod
    def is_failure(self) -> bool:
        """Check if result is a failure."""
        pass

    @property
    @abstractmethod
    def value(self) -> T:
        """Get the success value. Raises if result is failure."""
        pass

    @property
    @abstractmethod
    def error(self) -> E:
        """Get the error. Raises if result is success."""
        pass

    @abstractmethod
    def map(self, fn: Callable[[T], U]) -> Result[U, E]:
        """
        Transform the success value using fn.
        If this is a Failure, returns the failure unchanged.
        """
        pass

    @abstractmethod
    def flat_map(self, fn: Callable[[T], Result[U, E]]) -> Result[U, E]:
        """
        Transform the success value using fn that returns a Result.
        Useful for chaining operations that might fail.
        """
        pass

    @abstractmethod
    def map_error(self, fn: Callable[[E], AppException]) -> Result[T, AppException]:
        """
        Transform the error using fn.
        If this is a Success, returns the success unchanged.
        """
        pass

    @abstractmethod
    def get_or_default(self, default: T) -> T:
        """Get the value or return default if failure."""
        pass

    @abstractmethod
    def get_or_else(self, fn: Callable[[E], T]) -> T:
        """Get the value or compute from error if failure."""
        pass

    @abstractmethod
    def on_success(self, fn: Callable[[T], None]) -> Result[T, E]:
        """Execute side effect on success. Returns self for chaining."""
        pass

    @abstractmethod
    def on_failure(self, fn: Callable[[E], None]) -> Result[T, E]:
        """Execute side effect on failure. Returns self for chaining."""
        pass

    def unwrap(self) -> T:
        """
        Get the value or raise the error.
        Useful when you want to propagate errors as exceptions.
        """
        if self.is_success:
            return self.value
        raise self.error

    def unwrap_or_raise(self) -> T:
        """Alias for unwrap()."""
        return self.unwrap()


@dataclass(frozen=True)
class Success(Result[T, E]):
    """
    Represents a successful result containing a value.

    Example:
        result = Success(user)
        if result.is_success:
            print(result.value.name)
    """

    _value: T

    @property
    def is_success(self) -> bool:
        return True

    @property
    def is_failure(self) -> bool:
        return False

    @property
    def value(self) -> T:
        return self._value

    @property
    def error(self) -> E:
        raise ValueError("Cannot get error from Success result")

    def map(self, fn: Callable[[T], U]) -> Result[U, E]:
        return Success(fn(self._value))

    def flat_map(self, fn: Callable[[T], Result[U, E]]) -> Result[U, E]:
        return fn(self._value)

    def map_error(self, fn: Callable[[E], AppException]) -> Result[T, AppException]:
        return self  # type: ignore

    def get_or_default(self, default: T) -> T:
        return self._value

    def get_or_else(self, fn: Callable[[E], T]) -> T:
        return self._value

    def on_success(self, fn: Callable[[T], None]) -> Result[T, E]:
        fn(self._value)
        return self

    def on_failure(self, fn: Callable[[E], None]) -> Result[T, E]:
        return self

    def __repr__(self) -> str:
        return f"Success({self._value!r})"


@dataclass(frozen=True)
class Failure(Result[T, E]):
    """
    Represents a failed result containing an error.

    Example:
        result = Failure(NotFoundError("User", user_id))
        if result.is_failure:
            print(result.error.user_message)
    """

    _error: E

    @property
    def is_success(self) -> bool:
        return False

    @property
    def is_failure(self) -> bool:
        return True

    @property
    def value(self) -> T:
        raise ValueError("Cannot get value from Failure result")

    @property
    def error(self) -> E:
        return self._error

    def map(self, fn: Callable[[T], U]) -> Result[U, E]:
        return self  # type: ignore

    def flat_map(self, fn: Callable[[T], Result[U, E]]) -> Result[U, E]:
        return self  # type: ignore

    def map_error(self, fn: Callable[[E], AppException]) -> Result[T, AppException]:
        return Failure(fn(self._error))

    def get_or_default(self, default: T) -> T:
        return default

    def get_or_else(self, fn: Callable[[E], T]) -> T:
        return fn(self._error)

    def on_success(self, fn: Callable[[T], None]) -> Result[T, E]:
        return self

    def on_failure(self, fn: Callable[[E], None]) -> Result[T, E]:
        fn(self._error)
        return self

    def __repr__(self) -> str:
        return f"Failure({self._error!r})"


# ============================================================================
# Helper Functions
# ============================================================================

def try_result(fn: Callable[[], T]) -> Result[T, AppException]:
    """
    Execute a function and wrap the result in a Result.
    Catches any AppException and returns Failure.

    Example:
        result = try_result(lambda: some_risky_operation())
    """
    try:
        return Success(fn())
    except AppException as e:
        return Failure(e)


async def try_result_async(fn: Callable[[], T]) -> Result[T, AppException]:
    """
    Async version of try_result.

    Example:
        result = await try_result_async(lambda: await some_async_operation())
    """
    try:
        result = await fn()  # type: ignore
        return Success(result)
    except AppException as e:
        return Failure(e)


def collect_results(results: list[Result[T, E]]) -> Result[list[T], E]:
    """
    Collect a list of Results into a Result of list.
    Returns Failure with first error if any result is a Failure.

    Example:
        results = [Success(1), Success(2), Success(3)]
        collected = collect_results(results)  # Success([1, 2, 3])

        results = [Success(1), Failure(error), Success(3)]
        collected = collect_results(results)  # Failure(error)
    """
    values = []
    for result in results:
        if result.is_failure:
            return result  # type: ignore
        values.append(result.value)
    return Success(values)


def first_success(results: list[Result[T, E]]) -> Result[T, E]:
    """
    Return the first successful result, or the last failure.

    Useful for fallback patterns where you try multiple sources.

    Example:
        result = first_success([
            await try_cache(),
            await try_database(),
            await try_api()
        ])
    """
    last_failure: Optional[Result[T, E]] = None
    for result in results:
        if result.is_success:
            return result
        last_failure = result

    if last_failure:
        return last_failure

    # This shouldn't happen with non-empty list
    from app.core.exceptions import ValidationError
    return Failure(ValidationError("No results provided"))  # type: ignore


# ============================================================================
# Paginated Result
# ============================================================================

@dataclass
class PaginatedData(Generic[T]):
    """Container for paginated data."""
    items: list[T]
    page: int
    per_page: int
    total_items: int
    total_pages: int

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    def to_dict(self) -> dict:
        return {
            "items": [item.dict() if hasattr(item, "dict") else item for item in self.items],
            "pagination": {
                "page": self.page,
                "per_page": self.per_page,
                "total_items": self.total_items,
                "total_pages": self.total_pages,
                "has_next": self.has_next,
                "has_prev": self.has_prev
            }
        }


# Type alias for paginated results
PaginatedResult = Result[PaginatedData[T], E]


# ============================================================================
# Operation Result (for mutations with metadata)
# ============================================================================

@dataclass
class OperationResult(Generic[T]):
    """
    Result of a mutation operation with additional metadata.

    Attributes:
        data: The created/updated entity
        created: Whether entity was created (vs updated)
        message: Human-readable description of what happened
    """
    data: T
    created: bool = False
    message: str = ""

    @classmethod
    def created_new(cls, data: T, message: str = "Created successfully") -> OperationResult[T]:
        return cls(data=data, created=True, message=message)

    @classmethod
    def updated(cls, data: T, message: str = "Updated successfully") -> OperationResult[T]:
        return cls(data=data, created=False, message=message)
