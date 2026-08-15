"""
Security Utilities
JWT token handling, password hashing, and authentication helpers.
Requirements: Section 5.3 - Security Requirements
"""

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Optional, Any
from jose import JWTError, jwt
from passlib.context import CryptContext
import secrets
import logging
import time

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


# Supabase JWT signing keys (JWKS)
# ================================
#
# Supabase is migrating projects off the single shared HS256 secret onto asymmetric signing
# keys (ES256), published at `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`. During the
# migration a project has BOTH: a current key and a standby, and the dashboard's "Rotate keys"
# promotes the standby. Tokens minted before a rotation stay valid until the old key is
# explicitly revoked.
#
# This verifier therefore has to accept both, or sign-in breaks in the window between the
# rotation and a matching deploy — and, worse, revoking the legacy key would be gated on a
# code change nobody remembered was needed.
_JWKS_CACHE: dict[str, Any] = {"keys": {}, "fetched_at": 0.0}
_JWKS_TTL_SECONDS = 600.0
# Refusing to re-fetch more than once a minute bounds the damage from a flood of tokens
# carrying unknown `kid`s, which would otherwise be a free way to make us hammer Supabase.
_JWKS_MIN_REFETCH_INTERVAL = 60.0
_jwks_lock: Optional[Any] = None   # asyncio.Lock, created lazily on the running loop


async def _fetch_jwks() -> dict[str, Any]:
    """Fetch and index the project's JWKS by `kid`. Returns {} on any failure."""
    import httpx  # noqa: PLC0415

    url = f"{str(settings.SUPABASE_URL).rstrip('/')}/auth/v1/.well-known/jwks.json"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            keys = {k["kid"]: k for k in resp.json().get("keys", []) if k.get("kid")}
            logger.info("Supabase JWKS refreshed: %d key(s)", len(keys))
            return keys
    except Exception as e:
        # Non-fatal by design: a JWKS outage must degrade to the legacy HS256 path rather
        # than locking every Supabase-authenticated user out.
        logger.warning("Supabase JWKS fetch failed (%s: %s)", type(e).__name__, e)
        return {}


async def _jwks_key_for(kid: str) -> Optional[dict[str, Any]]:
    """Return the JWK for `kid`, refreshing the cache when it is unknown or stale."""
    import asyncio  # noqa: PLC0415

    global _jwks_lock
    if _jwks_lock is None:
        _jwks_lock = asyncio.Lock()

    now = time.monotonic()
    cached = _JWKS_CACHE["keys"]
    fresh = (now - _JWKS_CACHE["fetched_at"]) < _JWKS_TTL_SECONDS
    if kid in cached and fresh:
        return cached[kid]

    # Single-flight: a rotation makes every in-flight request miss at once, and without this
    # each one would launch its own fetch.
    async with _jwks_lock:
        now = time.monotonic()
        cached = _JWKS_CACHE["keys"]
        if kid in cached and (now - _JWKS_CACHE["fetched_at"]) < _JWKS_TTL_SECONDS:
            return cached[kid]
        if (now - _JWKS_CACHE["fetched_at"]) < _JWKS_MIN_REFETCH_INTERVAL and cached:
            return cached.get(kid)
        keys = await _fetch_jwks()
        if keys:
            _JWKS_CACHE["keys"] = keys
            _JWKS_CACHE["fetched_at"] = now
            return keys.get(kid)
        # Keep serving the stale cache on a fetch failure.
        return cached.get(kid)


def _reset_jwks_cache_for_tests() -> None:
    _JWKS_CACHE["keys"] = {}
    _JWKS_CACHE["fetched_at"] = 0.0


async def verify_supabase_token(token: str) -> Optional[dict[str, Any]]:
    """
    Verify a Supabase Auth JWT, accepting both asymmetric (ES256/RS256) and legacy HS256.

    ⚠️ The algorithm decides WHICH key is used, and the two are never interchangeable. An
    asymmetric token is verified ONLY against the JWKS public key; an HS256 token ONLY against
    the shared secret. Passing a union of algorithms to a single `jwt.decode` is the classic
    algorithm-confusion shape: an attacker re-signs chosen claims as HS256 using the *public*
    key bytes as the HMAC secret, and a verifier that accepts either algorithm against either
    key admits them as any user.

    Measured, so the comment doesn't overstate it: **python-jose blocks that attack on its
    own** — it refuses an asymmetric key as an HMAC secret ("Incorrect key type. Expected:
    'oct', Received: EC") on both encode and decode. So the split here is defence in depth and
    a guard against a future library swap, not the only thing standing in the way. It is still
    the right shape, and `test_supabase_jwks_verification.py` pins it at the source level
    because no behavioural test can tell a correct verifier from a permissive one while the
    library is refusing the forgery underneath.

    Returns the payload, or None if the token is not valid.
    """
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        logger.warning("Supabase token has an unreadable header: %s", e)
        return None

    alg = (header or {}).get("alg", "")
    kid = (header or {}).get("kid")

    if alg in ("ES256", "RS256"):
        if not kid:
            logger.warning("Supabase token uses %s with no kid; cannot select a key", alg)
            return None
        key = await _jwks_key_for(kid)
        if not key:
            logger.warning("No Supabase JWKS key matches kid=%s", kid)
            return None
        try:
            return jwt.decode(token, key, algorithms=[alg], audience="authenticated")
        except JWTError as e:
            logger.warning("Supabase token verification failed (%s): %s", alg, e)
            return None

    if alg == "HS256":
        # Legacy path. Stays until the project revokes its legacy signing key; after that
        # Supabase stops issuing HS256 tokens and this branch is simply never taken.
        if not settings.SUPABASE_JWT_SECRET:
            logger.error("SUPABASE_JWT_SECRET not configured; cannot verify a legacy token")
            return None
        try:
            return jwt.decode(
                token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
            )
        except JWTError as e:
            logger.warning("Supabase token verification failed (HS256): %s", e)
            return None

    # Anything else — including `none` — is refused rather than guessed at.
    logger.warning("Supabase token uses unsupported alg=%r", alg)
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

    # TWO POOLS, and the split is a security boundary — not tidiness.
    #
    # The general pool's identifier is attacker-CONTROLLED: chat/report/standard buckets key off
    # `guest_user_id_for(X-Guest-Id)`, a uuid5 of a header the client picks, so one caller can
    # mint unlimited distinct keys. The credential pool (`protected=True`) is reachable only from
    # `api/v1/endpoints/auth.py`, whose keys are a real email or `trusted_client_ip`.
    #
    # Mixing them was exploitable. Eviction used to be FIFO by INSERTION order, and re-assigning
    # an existing key preserves its position in a dict — so a long-lived `login:email:<victim>`
    # bucket sat permanently at the front and was the FIRST thing evicted. ~20k cheap requests
    # with a fresh X-Guest-Id each (seconds of work, and each one is *allowed* because every key
    # gets its own budget) deleted the victim's bucket and reset the 10-per-15-minutes cap at
    # `auth.py:132`, buying 10 more password guesses. Repeat indefinitely. That directly
    # falsified `auth.py`'s claim that the per-email window "caps guesses against one address no
    # matter where they come from". Same for `reset:email:` and `register:email:`.
    #
    # Two changes close it:
    #   1. LRU, not FIFO-by-insertion. `move_to_end` on EVERY touch — including a DENIED one, so
    #      a bucket actively being brute-forced is the last thing evicted, not the first.
    #   2. Separate pools. To evict a credential bucket an attacker must now fill the credential
    #      pool with 20k distinct emails, and every one of those attempts is itself throttled by
    #      `login:ip:` (10/60s) — ~33 hours from one address, versus seconds before.
    #
    # ⚠️ Point 2 only holds because the endpoints SHORT-CIRCUIT. `is_allowed` inserts and
    # LRU-touches its key BEFORE deciding, so a route that evaluates a second limiter after the
    # first has already denied still creates the second key. `auth.py` originally did exactly
    # that (`ip_ok = ...; email_ok = ...; if not (ip_ok and email_ok)`), which meant ~20k cheap
    # POSTs from ONE address — every one of them 429'd, never reaching Supabase — still inserted
    # 20k keys and evicted the victim's bucket. The "~33 hours" above was false as written; it is
    # true now that `_enforce_credential_limits` raises on the first denial. Any new paired
    # limiter MUST go through that helper.
    #
    # Evicting a key only resets that identifier's window (best-effort limiter, not a hard wall).
    _MAX_TRACKED = 20_000
    _MAX_TRACKED_PROTECTED = 20_000
    _STALE_SECONDS = 3600  # a key idle this long is definitely stale for any window we use (<=60s)
    # Bounds the stale sweep so eviction stays amortized O(1). The old sweep rebuilt the entire
    # 20k-entry dict AND materialised `list(keys())` on EVERY allowed request once the table was
    # full — ~40k operations on the event loop per request, app-wide, to delete typically one key.
    # Memory was bounded; CPU was not, so 20k cheap requests bought a persistent latency tax.
    _STALE_SCAN_BUDGET = 64

    def __init__(self):
        self._requests: "OrderedDict[str, list[datetime]]" = OrderedDict()
        self._protected: "OrderedDict[str, list[datetime]]" = OrderedDict()

    def is_allowed(
        self,
        identifier: str,
        max_requests: int = 60,
        window_seconds: int = 60,
        *,
        protected: bool = False,
    ) -> bool:
        """
        Check if request is allowed based on rate limit.

        Args:
            identifier: Unique identifier (user_id, IP, etc.)
            max_requests: Maximum requests allowed
            window_seconds: Time window in seconds
            protected: Route this key into the credential pool, which attacker-minted
                identifiers cannot reach. Set it for every limiter guarding a credential
                (sign-in, register, password reset/change, token refresh, OAuth) and for
                nothing else — see the class comment for why the split exists.

        Returns:
            bool: True if request is allowed
        """
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(seconds=window_seconds)

        pool = self._protected if protected else self._requests
        cap = self._MAX_TRACKED_PROTECTED if protected else self._MAX_TRACKED

        times = pool.get(identifier)
        if times is None:
            times = []
            pool[identifier] = times
        # LRU touch BEFORE the limit check: a bucket under active attack is denied and would
        # otherwise never be refreshed, so it would drift to the front and be evicted first —
        # which is precisely the bug this ordering closes.
        pool.move_to_end(identifier)

        # Drop timestamps outside the window (re-assignment keeps the LRU position).
        times = [req_time for req_time in times if req_time > window_start]
        pool[identifier] = times

        allowed = len(times) < max_requests
        if allowed:
            times.append(now)

        # Keep memory bounded against attacker-controlled identifiers (see class comment).
        # Checked on the denied path too, so a `max_requests=0` caller cannot grow the pool.
        if len(pool) > cap:
            self._evict(pool, cap, now)

        return allowed

    def clear(self) -> None:
        """Drop ALL tracked windows, in every pool.

        Exists for tests: the limiter is process-wide, so without a reset they poison each
        other. Tests used to reach in and clear `_requests` directly, which silently stopped
        covering everything the moment the credential pool was added — several auth tests
        began failing on leaked state rather than on anything they assert. A method here
        means a future pool is cleared automatically.
        """
        self._requests.clear()
        self._protected.clear()

    def _evict(self, pool: "OrderedDict[str, list[datetime]]", cap: int, now: datetime) -> None:
        """Bound `pool` in amortized O(1): LRU order puts evictable keys at the front."""
        # Hard cap first. `popitem(last=False)` drops the LEAST-recently-touched key, so a
        # burst of distinct fresh ids evicts itself rather than a real user's live bucket.
        while len(pool) > cap:
            pool.popitem(last=False)
        # Then opportunistically reclaim idle keys, which also accumulate at the front under
        # LRU. Budgeted so this can never become a full-table scan on the hot path.
        cutoff = now - timedelta(seconds=self._STALE_SECONDS)
        for _ in range(self._STALE_SCAN_BUDGET):
            if not pool:
                break
            _, times = next(iter(pool.items()))
            if times and times[-1] > cutoff:
                break  # front is live; under LRU everything behind it is at least as live
            pool.popitem(last=False)


# Global rate limiter instance
rate_limiter = RateLimiter()


def trusted_client_ip(req: Any) -> str:
    """The client address as observed by OUR edge — never one the caller chose.

    `request.client.host` is NOT trustworthy here. Both `railway.toml` and the Dockerfile
    start uvicorn with `--proxy-headers --forwarded-allow-ips='*'`, which sets
    `ProxyHeadersMiddleware.always_trust`, and in that mode uvicorn rewrites
    `scope["client"]` to the **leftmost** `X-Forwarded-For` entry. The leftmost entry is
    whatever the caller sent before Railway's edge appended the real peer address — i.e. it
    is attacker-controlled.

    Every per-IP auth limiter keyed off that value, so rotating one header per request gave a
    brand-new bucket every time and the limits never fired: unlimited password guesses against
    a known address on `/auth/login`, unlimited Supabase confirmation emails to an arbitrary
    address on `/auth/register`, and a bypass of the anonymous WebSocket connection cap.

    The RIGHTMOST entry is the one our own edge appended, so that is the only part of the
    header a caller cannot forge. `--forwarded-allow-ips='*'` stays as-is — it is there so
    logs and `.client.host` reflect the real client, and this helper is what the security
    controls key on instead.

    Chain depth note: this assumes exactly one trusted proxy in front of the app (Railway's
    edge). If another is ever added, drop that many entries from the right instead of taking
    just the last.
    """
    try:
        forwarded = req.headers.get("x-forwarded-for")
    except Exception:  # pragma: no cover - defensive; a Request always has headers
        forwarded = None

    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-1]

    client = getattr(req, "client", None)
    return getattr(client, "host", None) or "unknown"
