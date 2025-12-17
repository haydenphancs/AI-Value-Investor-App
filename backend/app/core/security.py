"""
Security Utilities
JWT token handling, password hashing, and authentication helpers.
Requirements: Section 5.3 - Security Requirements
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Any
from jose import JWTError, jwt
from passlib.context import CryptContext
import secrets
import logging

from app.config import settings

logger = logging.getLogger(__name__)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# Password Utilities
# ==================

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against a hashed password.

    Args:
        plain_password: Plain text password
        hashed_password: Hashed password from database

    Returns:
        bool: True if password matches
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Hash a password for storage.

    Args:
        password: Plain text password

    Returns:
        str: Hashed password
    """
    return pwd_context.hash(password)


# JWT Token Utilities
# ===================

def create_access_token(
    data: dict[str, Any],
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create a JWT access token.

    Args:
        data: Data to encode in the token (usually user_id, email)
        expires_delta: Token expiration time (default from settings)

    Returns:
        str: Encoded JWT token

    Example:
        token = create_access_token({"sub": user_id, "email": user_email})
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access"
    })

    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM
    )

    return encoded_jwt


def create_refresh_token(
    data: dict[str, Any],
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create a JWT refresh token (longer expiration).

    Args:
        data: Data to encode in the token
        expires_delta: Token expiration time (default from settings)

    Returns:
        str: Encoded JWT refresh token
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES
        )

    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "refresh"
    })

    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM
    )

    return encoded_jwt


def decode_token(token: str) -> Optional[dict[str, Any]]:
    """
    Decode and verify a JWT token.

    Args:
        token: JWT token string

    Returns:
        Optional[dict]: Decoded token payload or None if invalid

    Raises:
        JWTError: If token is invalid or expired
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        return payload
    except JWTError as e:
        logger.warning(f"Token decode failed: {e}")
        raise


def verify_supabase_token(token: str) -> Optional[dict[str, Any]]:
    """
    Verify a Supabase Auth JWT token.
    Used when iOS app authenticates users via Supabase Auth.

    Args:
        token: Supabase JWT token

    Returns:
        Optional[dict]: Decoded token payload or None if invalid
    """
    if not settings.SUPABASE_JWT_SECRET:
        logger.error("SUPABASE_JWT_SECRET not configured")
        return None

    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated"
        )
        return payload
    except JWTError as e:
        logger.warning(f"Supabase token verification failed: {e}")
        return None


# API Key Utilities
# =================

def generate_api_key() -> str:
    """
    Generate a secure random API key.
    Can be used for service-to-service authentication.

    Returns:
        str: Random API key
    """
    return secrets.token_urlsafe(32)


def verify_api_key(api_key: str, valid_keys: set[str]) -> bool:
    """
    Verify an API key against a set of valid keys.

    Args:
        api_key: API key to verify
        valid_keys: Set of valid API keys

    Returns:
        bool: True if API key is valid
    """
    return secrets.compare_digest(api_key, next(iter(valid_keys)))


# Rate Limiting Utilities
# =======================

class RateLimiter:
    """
    Simple in-memory rate limiter.
    For production, use Redis-based rate limiting.
    """

    def __init__(self):
        self._requests: dict[str, list[datetime]] = {}

    def is_allowed(
        self,
        identifier: str,
        max_requests: int = 60,
        window_seconds: int = 60
    ) -> bool:
        """
        Check if request is allowed based on rate limit.

        Args:
            identifier: Unique identifier (user_id, IP, etc.)
            max_requests: Maximum requests allowed
            window_seconds: Time window in seconds

        Returns:
            bool: True if request is allowed
        """
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(seconds=window_seconds)

        # Initialize or clean old requests
        if identifier not in self._requests:
            self._requests[identifier] = []

        # Remove old requests outside the window
        self._requests[identifier] = [
            req_time for req_time in self._requests[identifier]
            if req_time > window_start
        ]

        # Check if limit exceeded
        if len(self._requests[identifier]) >= max_requests:
            return False

        # Add current request
        self._requests[identifier].append(now)
        return True


# Global rate limiter instance
rate_limiter = RateLimiter()
