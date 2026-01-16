"""
Service Container - Dependency Injection
========================================

This module provides a dependency injection container for managing
service instances throughout the application lifecycle.

Design Goals:
1. **Lazy Loading**: Services are created only when first requested
2. **Singleton Pattern**: Services are created once and reused
3. **Testability**: Easy to swap implementations for testing
4. **Type Safety**: Full type hints for IDE support

Usage:
    # In FastAPI endpoints
    @router.get("/stocks/{ticker}")
    async def get_stock(
        ticker: str,
        stock_service: StockService = Depends(get_stock_service)
    ):
        return await stock_service.get_stock(ticker)

    # In tests
    container = ServiceContainer()
    container.register_instance(StockService, mock_stock_service)
"""

from typing import TypeVar, Type, Dict, Any, Optional, Callable
from functools import lru_cache
import logging

from supabase import Client

from app.database import get_supabase
from app.cache import cache_manager

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ServiceContainer:
    """
    Dependency injection container for services.

    Manages service lifecycle and provides dependency resolution.
    Uses lazy loading to create services only when needed.
    """

    _instance: Optional["ServiceContainer"] = None
    _instances: Dict[Type, Any] = {}
    _factories: Dict[Type, Callable] = {}

    def __new__(cls) -> "ServiceContainer":
        """Singleton pattern for container."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._instances = {}
            cls._instance._factories = {}
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """
        Reset the container (useful for testing).

        Clears all registered instances and factories.
        """
        if cls._instance:
            cls._instance._instances.clear()
            cls._instance._factories.clear()

    def register_factory(
        self,
        service_type: Type[T],
        factory: Callable[..., T]
    ) -> None:
        """
        Register a factory function for a service type.

        Args:
            service_type: The type to register
            factory: Factory function that creates the instance
        """
        self._factories[service_type] = factory
        logger.debug(f"Registered factory for {service_type.__name__}")

    def register_instance(
        self,
        service_type: Type[T],
        instance: T
    ) -> None:
        """
        Register a pre-created instance for a service type.

        Useful for testing with mocks.

        Args:
            service_type: The type to register
            instance: The instance to use
        """
        self._instances[service_type] = instance
        logger.debug(f"Registered instance for {service_type.__name__}")

    def resolve(self, service_type: Type[T]) -> T:
        """
        Resolve a service instance.

        Returns existing instance or creates new one using factory.

        Args:
            service_type: The type to resolve

        Returns:
            Service instance

        Raises:
            ValueError: If no factory registered for type
        """
        # Return existing instance if available
        if service_type in self._instances:
            return self._instances[service_type]

        # Create new instance using factory
        if service_type not in self._factories:
            raise ValueError(
                f"No factory registered for {service_type.__name__}. "
                f"Call register_factory() first."
            )

        instance = self._factories[service_type]()
        self._instances[service_type] = instance
        logger.debug(f"Created new instance of {service_type.__name__}")

        return instance

    def get(self, service_type: Type[T]) -> Optional[T]:
        """
        Get a service instance if registered.

        Like resolve() but returns None instead of raising.

        Args:
            service_type: The type to get

        Returns:
            Service instance or None
        """
        try:
            return self.resolve(service_type)
        except ValueError:
            return None


# Global container instance
_container: Optional[ServiceContainer] = None


def get_container() -> ServiceContainer:
    """
    Get the global service container.

    Returns:
        ServiceContainer instance
    """
    global _container
    if _container is None:
        _container = ServiceContainer()
        _setup_default_factories(_container)
    return _container


def _setup_default_factories(container: ServiceContainer) -> None:
    """
    Setup default service factories.

    Called once when container is first created.
    """
    from app.services.stock_service import StockService
    from app.services.research_service import ResearchService
    from app.services.news_service import NewsService
    from app.services.user_service import UserService
    from app.services.chat_service import ChatService
    from app.services.widget_service import WidgetService

    from app.integrations.fmp import FMPClient
    from app.integrations.gemini import GeminiClient
    from app.integrations.news_api import NewsAPIClient

    from app.agents.registry import AgentRegistry

    # Get common dependencies
    supabase = get_supabase()

    # Register integration clients
    container.register_factory(FMPClient, lambda: FMPClient())
    container.register_factory(GeminiClient, lambda: GeminiClient())
    container.register_factory(NewsAPIClient, lambda: NewsAPIClient())
    container.register_factory(AgentRegistry, lambda: AgentRegistry())

    # Register services
    container.register_factory(
        StockService,
        lambda: StockService(
            supabase=supabase,
            fmp_client=container.resolve(FMPClient)
        )
    )

    container.register_factory(
        ResearchService,
        lambda: ResearchService(
            supabase=supabase,
            agent_registry=container.resolve(AgentRegistry)
        )
    )

    container.register_factory(
        NewsService,
        lambda: NewsService(
            supabase=supabase,
            news_client=container.resolve(NewsAPIClient),
            gemini_client=container.resolve(GeminiClient)
        )
    )

    container.register_factory(
        UserService,
        lambda: UserService(supabase=supabase)
    )

    container.register_factory(
        ChatService,
        lambda: ChatService(
            supabase=supabase,
            gemini_client=container.resolve(GeminiClient)
        )
    )

    container.register_factory(
        WidgetService,
        lambda: WidgetService(
            supabase=supabase,
            gemini_client=container.resolve(GeminiClient)
        )
    )

    logger.info("Service container initialized with default factories")


# ============================================================================
# FastAPI Dependency Functions
# ============================================================================

def get_stock_service() -> "StockService":
    """FastAPI dependency for StockService."""
    from app.services.stock_service import StockService
    return get_container().resolve(StockService)


def get_research_service() -> "ResearchService":
    """FastAPI dependency for ResearchService."""
    from app.services.research_service import ResearchService
    return get_container().resolve(ResearchService)


def get_news_service() -> "NewsService":
    """FastAPI dependency for NewsService."""
    from app.services.news_service import NewsService
    return get_container().resolve(NewsService)


def get_user_service() -> "UserService":
    """FastAPI dependency for UserService."""
    from app.services.user_service import UserService
    return get_container().resolve(UserService)


def get_chat_service() -> "ChatService":
    """FastAPI dependency for ChatService."""
    from app.services.chat_service import ChatService
    return get_container().resolve(ChatService)


def get_widget_service() -> "WidgetService":
    """FastAPI dependency for WidgetService."""
    from app.services.widget_service import WidgetService
    return get_container().resolve(WidgetService)


def get_agent_registry() -> "AgentRegistry":
    """FastAPI dependency for AgentRegistry."""
    from app.agents.registry import AgentRegistry
    return get_container().resolve(AgentRegistry)


# ============================================================================
# Testing Utilities
# ============================================================================

class TestContainer:
    """
    Context manager for test-scoped container.

    Usage:
        async def test_something():
            with TestContainer() as container:
                container.register_instance(StockService, mock_service)
                # Test code here
            # Container is reset after test
    """

    def __init__(self):
        self._container = get_container()

    def __enter__(self) -> ServiceContainer:
        ServiceContainer.reset()
        return self._container

    def __exit__(self, exc_type, exc_val, exc_tb):
        ServiceContainer.reset()
        global _container
        _container = None


def create_test_container() -> ServiceContainer:
    """
    Create a fresh container for testing.

    Returns a container without any registered services,
    allowing tests to register mocks.

    Returns:
        Fresh ServiceContainer
    """
    ServiceContainer.reset()
    return ServiceContainer()
