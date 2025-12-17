"""
Database Connection and Session Management
Handles connections to Supabase PostgreSQL with respect to existing schema.
References: database/supabase_schema.sql
"""

from typing import AsyncGenerator, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.pool import NullPool
from supabase import create_client, Client
import logging

from app.config import settings

logger = logging.getLogger(__name__)

# SQLAlchemy Base for ORM models (if needed)
Base = declarative_base()


class DatabaseManager:
    """
    Manages database connections for both Supabase client and direct PostgreSQL access.
    """

    def __init__(self):
        self._supabase_client: Optional[Client] = None
        self._engine = None
        self._async_engine = None
        self._session_factory = None
        self._async_session_factory = None

    def get_supabase_client(self) -> Client:
        """
        Get Supabase client for easy access to Supabase features.
        Uses service role key for server-side operations.

        Returns:
            Client: Supabase client instance
        """
        if self._supabase_client is None:
            logger.info("Initializing Supabase client")
            self._supabase_client = create_client(
                supabase_url=settings.SUPABASE_URL,
                supabase_key=settings.SUPABASE_KEY  # Service role key (server-side only)
            )
        return self._supabase_client

    def get_sync_engine(self):
        """
        Create synchronous SQLAlchemy engine for direct PostgreSQL access.
        Useful for data migrations and batch operations.

        Returns:
            Engine: SQLAlchemy engine
        """
        if self._engine is None:
            if settings.DATABASE_URL:
                logger.info("Creating synchronous database engine")
                self._engine = create_engine(
                    settings.DATABASE_URL,
                    pool_size=settings.DB_POOL_SIZE,
                    max_overflow=settings.DB_MAX_OVERFLOW,
                    echo=settings.DEBUG,
                )
            else:
                # Build connection string from Supabase URL
                db_url = self._build_postgres_url()
                self._engine = create_engine(
                    db_url,
                    pool_size=settings.DB_POOL_SIZE,
                    max_overflow=settings.DB_MAX_OVERFLOW,
                    echo=settings.DEBUG,
                )
        return self._engine

    def get_async_engine(self):
        """
        Create async SQLAlchemy engine for async operations.
        Recommended for FastAPI endpoints.

        Returns:
            AsyncEngine: SQLAlchemy async engine
        """
        if self._async_engine is None:
            if settings.DATABASE_URL:
                # Convert postgresql:// to postgresql+asyncpg://
                async_url = settings.DATABASE_URL.replace(
                    "postgresql://", "postgresql+asyncpg://"
                )
                logger.info("Creating async database engine")
                self._async_engine = create_async_engine(
                    async_url,
                    pool_size=settings.DB_POOL_SIZE,
                    max_overflow=settings.DB_MAX_OVERFLOW,
                    echo=settings.DEBUG,
                )
            else:
                db_url = self._build_postgres_url(async_driver=True)
                self._async_engine = create_async_engine(
                    db_url,
                    pool_size=settings.DB_POOL_SIZE,
                    max_overflow=settings.DB_MAX_OVERFLOW,
                    echo=settings.DEBUG,
                )
        return self._async_engine

    def _build_postgres_url(self, async_driver: bool = False) -> str:
        """
        Build PostgreSQL connection URL from Supabase URL.

        Args:
            async_driver: Whether to use asyncpg driver

        Returns:
            str: PostgreSQL connection string
        """
        # Extract database connection info from Supabase URL
        # Format: https://[project-ref].supabase.co
        # PostgreSQL: postgresql://postgres:[password]@db.[project-ref].supabase.co:5432/postgres

        supabase_url = settings.SUPABASE_URL
        project_ref = supabase_url.replace("https://", "").replace(".supabase.co", "")

        driver = "postgresql+asyncpg" if async_driver else "postgresql"

        # Note: You'll need to set DATABASE_URL in .env with proper credentials
        return f"{driver}://postgres:[YOUR_PASSWORD]@db.{project_ref}.supabase.co:5432/postgres"

    def get_session_factory(self):
        """
        Get synchronous session factory.

        Returns:
            sessionmaker: SQLAlchemy session factory
        """
        if self._session_factory is None:
            engine = self.get_sync_engine()
            self._session_factory = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=engine
            )
        return self._session_factory

    def get_async_session_factory(self):
        """
        Get async session factory.

        Returns:
            async_sessionmaker: SQLAlchemy async session factory
        """
        if self._async_session_factory is None:
            engine = self.get_async_engine()
            self._async_session_factory = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autocommit=False,
                autoflush=False,
            )
        return self._async_session_factory

    async def check_connection(self) -> bool:
        """
        Check if database connection is healthy.

        Returns:
            bool: True if connection is healthy
        """
        try:
            async with self.get_async_session_factory()() as session:
                result = await session.execute(text("SELECT 1"))
                return result.scalar() == 1
        except Exception as e:
            logger.error(f"Database connection check failed: {e}")
            return False

    async def close(self):
        """
        Close all database connections and dispose engines.
        Call this on application shutdown.
        """
        logger.info("Closing database connections")

        if self._async_engine:
            await self._async_engine.dispose()
            self._async_engine = None

        if self._engine:
            self._engine.dispose()
            self._engine = None

        self._session_factory = None
        self._async_session_factory = None
        self._supabase_client = None


# Global database manager instance
db_manager = DatabaseManager()


# Dependency for FastAPI endpoints
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency to get database session.
    Automatically handles session lifecycle.

    Yields:
        AsyncSession: Database session

    Example:
        @router.get("/users")
        async def get_users(db: AsyncSession = Depends(get_db)):
            # Use db session here
            pass
    """
    async_session_factory = db_manager.get_async_session_factory()
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def get_supabase() -> Client:
    """
    FastAPI dependency to get Supabase client.

    Returns:
        Client: Supabase client

    Example:
        @router.get("/users")
        def get_users(supabase: Client = Depends(get_supabase)):
            # Use supabase client here
            result = supabase.table("users").select("*").execute()
            pass
    """
    return db_manager.get_supabase_client()


# Vector search utilities for RAG features (Section 4.4)
async def vector_similarity_search(
    session: AsyncSession,
    table_name: str,
    embedding: list[float],
    top_k: int = 5,
    threshold: float = 0.7
) -> list[dict]:
    """
    Perform vector similarity search using pgvector.
    Used for RAG-based educational content and company insights.

    Args:
        session: Database session
        table_name: Name of table with embeddings (content_chunks or article_chunks)
        embedding: Query embedding vector
        top_k: Number of results to return
        threshold: Similarity threshold (0-1)

    Returns:
        list[dict]: Similar chunks with metadata

    Example:
        results = await vector_similarity_search(
            session,
            "content_chunks",
            query_embedding,
            top_k=5
        )
    """
    # pgvector uses <=> operator for cosine distance
    # Lower distance = higher similarity
    query = text(f"""
        SELECT
            id,
            chunk_text,
            1 - (embedding <=> :embedding::vector) as similarity,
            chunk_index,
            content_id
        FROM {table_name}
        WHERE 1 - (embedding <=> :embedding::vector) > :threshold
        ORDER BY embedding <=> :embedding::vector
        LIMIT :top_k
    """)

    result = await session.execute(
        query,
        {
            "embedding": str(embedding),
            "threshold": threshold,
            "top_k": top_k
        }
    )

    return [dict(row._mapping) for row in result]
