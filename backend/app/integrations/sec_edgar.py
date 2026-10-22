"""SEC EDGAR XBRL API — company concept facts (thin client).

Used by ``services/sec_concept_service.py`` to fetch the us-gaap cash-flow concepts that
``services/_buyback_verification.py`` checks FMP's quarterly buyback figure against: FMP's
``commonStockRepurchased`` is sometimes employee tax withholding or a preferred redemption
(INTC's $181M and $617M are RSU withholding), and the company's own filing says which.

API: ``GET https://data.sec.gov/api/xbrl/companyconcept/CIK##########/{taxonomy}/{tag}.json``
  - Public-domain data, no key. SEC's fair-access policy REQUIRES a declared User-Agent
    ("Company contact@email") and caps a client at 10 requests per second. A missing
    User-Agent is answered 403, and so is a client over the cap: SEC throttles by refusing,
    so a 403 here is a rate-limit signal, not an auth failure.
  - 404 = the company never reported that concept. That is an ANSWER (``None``), never an
    error and never a zero.

Behaviour:
  - ``settings.SEC_EDGAR_USER_AGENT`` blank, or not printable ASCII →
    ``SECConfigException`` before any I/O (the check is OFF until the owner sets it;
    ``user_agent_configured`` is the one predicate, shared with the service).
  - Requests start at least ``1 / settings.SEC_EDGAR_MAX_RPS`` seconds apart, process-wide.
  - A 429 or 403 opens a process-wide circuit for ``CIRCUIT_SECONDS``: every call in that
    window raises ``SECRateLimitException`` with no network I/O, so a throttled client
    stops knocking instead of extending its own block.
  - The User-Agent carries a contact address: it never appears in an exception message or
    a log line (``_scrub``).

HTTP in, dict out (rules/integrations.md). The two-tier cache, the dedup and the stale
fallback live in the service.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "CIRCUIT_SECONDS",
    "SECException",
    "SECRateLimitException",
    "SECUnavailableException",
    "SECConfigException",
    "SECEdgarClient",
    "normalize_cik",
    "user_agent_configured",
    "get_sec_edgar_client",
    "close_sec_edgar_client",
]


# ── Exception hierarchy ────────────────────────────────────────────────


class SECException(Exception):
    """Base for every SEC EDGAR failure. A plain instance is a definite refusal: a 4xx
    other than 404 (an answer), 403 / 429 (throttling) and 408 (transient)."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SECRateLimitException(SECException):
    """A 429, or a 403 (SEC refuses a throttled or blocked client with 403), or a call made
    while the circuit those open is still open. ``retry_after`` is SEC's Retry-After in
    seconds when it sent one, or the circuit's remaining seconds."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.retry_after = retry_after


class SECUnavailableException(SECException):
    """Transient: a 5xx or 408, a timeout, a network error, or a 200 whose body is not a
    JSON object. Worth asking again later."""


class SECConfigException(SECException):
    """``settings.SEC_EDGAR_USER_AGENT`` is blank or not a sendable header value (not
    printable ASCII — ``user_agent_configured``). Raised before any network I/O."""


# ── Inputs ─────────────────────────────────────────────────────────────

_DIGITS_RE = re.compile(r"^[0-9]+$")
# An XBRL element / taxonomy name. It goes into the URL PATH, so nothing else may.
_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,199}$")
_TAXONOMY_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def normalize_cik(value: Any) -> Optional[str]:
    """A CIK as SEC's 10-digit, zero-padded string, or None when ``value`` is not one.

    Accepts an int (``320193``) or a digit string (``"320193"``, ``"0000320193"``; outer
    whitespace is ignored). None for None, ``""``, a bool, a negative or zero value, a
    non-digit string and anything longer than 10 digits.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        if value <= 0:
            return None
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None
    # `str.isdigit` accepts "²" and other Unicode digits; SEC's CIK is ASCII 0-9 only.
    if not _DIGITS_RE.match(text) or len(text) > 10 or int(text) == 0:
        return None
    return text.zfill(10)


# ── Process-wide state: the limiter and the circuit ─────────────────────

#: How long a 429 / 403 keeps every call off the network.
CIRCUIT_SECONDS = 600.0

#: Monotonic time before which every call raises SECRateLimitException without I/O.
_circuit_open_until: float = 0.0
#: Monotonic time the next request may start at (the limiter's reservation cursor).
_next_request_at: float = 0.0

_rate_lock: Optional[asyncio.Lock] = None
_rate_lock_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_rate_lock() -> asyncio.Lock:
    """The limiter's lock, rebuilt when the running loop changes. An ``asyncio.Lock`` binds
    to the loop of its first contended wait, and a module-level one reused from another
    loop raises (pytest-asyncio runs every test on a fresh loop)."""
    global _rate_lock, _rate_lock_loop
    loop = asyncio.get_running_loop()
    if _rate_lock is None or _rate_lock_loop is not loop:
        _rate_lock = asyncio.Lock()
        _rate_lock_loop = loop
    return _rate_lock


def _min_interval() -> float:
    try:
        rps = float(settings.SEC_EDGAR_MAX_RPS)
    except (TypeError, ValueError):
        rps = 5.0
    if not rps > 0:  # also refuses NaN
        rps = 5.0
    return 1.0 / rps


async def _rate_limit() -> None:
    """Start requests at least ``1 / SEC_EDGAR_MAX_RPS`` seconds apart, process-wide.

    Each caller RESERVES its slot under the lock and sleeps OUTSIDE it (the coingecko
    lesson: a sleep inside the lock stalls every other caller behind the first one's wait).
    """
    global _next_request_at
    async with _get_rate_lock():
        now = time.monotonic()
        slot = max(now, _next_request_at)
        _next_request_at = slot + _min_interval()
    wait = slot - now
    if wait > 0:
        await asyncio.sleep(wait)


def _circuit_remaining() -> float:
    return _circuit_open_until - time.monotonic()


def _open_circuit() -> None:
    global _circuit_open_until
    _circuit_open_until = max(_circuit_open_until, time.monotonic() + CIRCUIT_SECONDS)


# ── HTTP client (module-level, lazy) ────────────────────────────────────

_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=5),
        )
    return _client


def _user_agent() -> str:
    raw = getattr(settings, "SEC_EDGAR_USER_AGENT", "") or ""
    return raw.strip() if isinstance(raw, str) else ""


def user_agent_configured() -> bool:
    """True when ``settings.SEC_EDGAR_USER_AGENT`` can be sent as an HTTP header value.

    The ONE predicate for "the SEC check is on" — the client and ``sec_concept_service``
    both call it. Blank is off; so is a value that is not printable ASCII: a non-ASCII
    character (an em dash) makes httpx raise an untyped ``UnicodeEncodeError`` from
    ``.get()``, and a control character (``\\n``) makes h11 raise an error whose message
    carries the header — contact address included — in a form ``_scrub`` cannot match.
    Either way it is a configuration mistake, so it is refused like a blank one, before I/O.
    """
    ua = _user_agent()
    return bool(ua) and ua.isascii() and ua.isprintable()


def _scrub(text: str) -> str:
    """``text`` with the configured User-Agent (it carries a contact address) removed,
    folded onto one line and capped — safe for an exception message or a log."""
    ua = _user_agent()
    if ua:
        text = text.replace(ua, "<user-agent>")
    return " ".join(text.split())[:200]


def _retry_after(resp: httpx.Response) -> Optional[float]:
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None  # an HTTP-date; the circuit covers it
    return value if value >= 0 else None


class SECEdgarClient:
    """Thin async wrapper around SEC EDGAR's XBRL ``companyconcept`` endpoint."""

    async def get_company_concept(
        self, cik: str, tag: str, taxonomy: str = "us-gaap"
    ) -> Optional[Dict[str, Any]]:
        """One company's facts for one XBRL concept.

        Returns the parsed JSON object on 200, None on 404 (the company never used the
        concept — an answer, not a failure).

        Raises:
            ValueError: ``cik``, ``tag`` or ``taxonomy`` is not a valid identifier.
            SECConfigException: ``settings.SEC_EDGAR_USER_AGENT`` is blank (no I/O).
            SECRateLimitException: a 429 or 403, or the circuit they open is still open
                (no I/O).
            SECUnavailableException: 5xx, 408, timeout, network error, or a 200 whose
                body is not a JSON object.
            SECException: any other 4xx (``status_code`` set).
        """
        cik10 = normalize_cik(cik)
        if cik10 is None:
            raise ValueError(f"invalid CIK: {cik!r}")
        if not isinstance(tag, str) or not _TAG_RE.match(tag):
            raise ValueError(f"invalid XBRL concept name: {tag!r}")
        if not isinstance(taxonomy, str) or not _TAXONOMY_RE.match(taxonomy):
            raise ValueError(f"invalid XBRL taxonomy: {taxonomy!r}")
        what = f"CIK{cik10} {taxonomy}:{tag}"

        if not user_agent_configured():
            # Never the value itself: it carries a contact address.
            raise SECConfigException(
                "SEC EDGAR: SEC_EDGAR_USER_AGENT is not set or is not printable ASCII — no "
                f"request made for {what}"
            )
        user_agent = _user_agent()

        remaining = _circuit_remaining()
        if remaining > 0:
            raise SECRateLimitException(
                f"SEC EDGAR: circuit open after a 429/403 ({remaining:.0f}s left) — no "
                f"request made for {what}",
                retry_after=remaining,
            )

        await _rate_limit()
        # The wait can outlast another caller's 429 / 403: re-check before sending.
        remaining = _circuit_remaining()
        if remaining > 0:
            raise SECRateLimitException(
                f"SEC EDGAR: circuit open after a 429/403 ({remaining:.0f}s left) — no "
                f"request made for {what}",
                retry_after=remaining,
            )

        url = (
            f"{settings.SEC_EDGAR_BASE_URL.rstrip('/')}/api/xbrl/companyconcept/"
            f"CIK{cik10}/{taxonomy}/{tag}.json"
        )
        headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        }
        try:
            resp = await _get_http_client().get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise SECUnavailableException(
                f"SEC EDGAR: request timed out for {what}: {type(exc).__name__}"
            ) from exc
        except httpx.HTTPError as exc:
            raise SECUnavailableException(
                f"SEC EDGAR: request failed for {what}: {type(exc).__name__}: "
                f"{_scrub(str(exc))}"
            ) from exc

        status = resp.status_code
        if status in (429, 403):
            retry_after = _retry_after(resp)
            _open_circuit()
            logger.warning(
                "[sec-edgar-throttled] SEC answered %d for %s — every SEC call is refused "
                "for %.0fs (retry_after=%s)",
                status, what, CIRCUIT_SECONDS, retry_after,
            )
            raise SECRateLimitException(
                f"SEC EDGAR: HTTP {status} for {what} (throttled or blocked; circuit open "
                f"for {CIRCUIT_SECONDS:.0f}s)",
                status_code=status,
                retry_after=retry_after,
            )
        if status == 404:
            return None
        if status == 408 or status >= 500:
            raise SECUnavailableException(
                f"SEC EDGAR: HTTP {status} for {what}: {_scrub(resp.text[:300])}",
                status_code=status,
            )
        if 400 <= status < 500:
            raise SECException(
                f"SEC EDGAR: HTTP {status} for {what}: {_scrub(resp.text[:300])}",
                status_code=status,
            )
        if status != 200:
            raise SECUnavailableException(
                f"SEC EDGAR: unexpected HTTP {status} for {what}", status_code=status
            )

        try:
            data = resp.json()
        # JSONDecodeError, a gzip body that will not decode, and a body nested too deep for
        # the parser (RecursionError is not a ValueError — x_api handles it the same way).
        except (ValueError, RecursionError) as exc:
            raise SECUnavailableException(
                f"SEC EDGAR: 200 with a body that is not JSON for {what}: "
                f"{type(exc).__name__}",
                status_code=status,
            ) from exc
        if not isinstance(data, dict):
            raise SECUnavailableException(
                f"SEC EDGAR: 200 with a JSON {type(data).__name__}, not an object, for {what}",
                status_code=status,
            )
        return data


# ── Singleton ──────────────────────────────────────────────────────────

_client_singleton: Optional[SECEdgarClient] = None


def get_sec_edgar_client() -> SECEdgarClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = SECEdgarClient()
    return _client_singleton


async def close_sec_edgar_client() -> None:
    """Tear-down hook for the app.main lifespan."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        finally:
            _client = None
