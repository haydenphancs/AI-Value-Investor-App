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

Two more reads for the Learn weekly pipeline (``services/learn_weekly/sec_facts.py``; plan
"Wiser weekly content engine" §1.5/§1.6 — every figure frozen into a Learn article comes from
SEC, never FMP):
  - ``get_submissions``: ``GET https://data.sec.gov/submissions/CIK##########.json`` — the
    company's name, tickers, SIC and its recent filings (8-K items feed the topic ranking).
  - ``get_filing_document``: ``GET https://www.sec.gov/Archives/edgar/data/<cik>/<accession
    without dashes>/<document>`` — one filing document as raw text (the 8-K body / EX-99.1 a
    peg fact is quoted from). Documents live on ``www.sec.gov`` (``SEC_ARCHIVES_BASE_URL``),
    not on the API host, under the SAME User-Agent, limiter and circuit; a body over
    ``MAX_DOCUMENT_BYTES`` is refused with ``SECEdgarDocumentTooLargeException`` without
    being held in memory.
  Both answer a 404 with None.
  - ``get_company_tickers``: ``GET https://www.sec.gov/files/company_tickers.json`` — SEC's
    own ticker → CIK table (``{"0": {"cik_str": 320193, "ticker": "AAPL", "title": ...}}``).
    The Learn topic step (``services/learn_weekly/signals.py``) maps its ranking universe to
    CIKs from it, so the CIK — the key every SEC read above takes — comes from SEC, never from
    FMP. Same User-Agent, limiter and circuit; streamed and refused over
    ``MAX_COMPANY_TICKERS_BYTES``; a 404 answers None. The map itself (and its 24 h cache)
    lives in that service.

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
import json
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
    "SECEdgarDocumentTooLargeException",
    "SECEdgarClient",
    "SEC_ARCHIVES_BASE_URL",
    "MAX_DOCUMENT_BYTES",
    "COMPANY_TICKERS_URL",
    "MAX_COMPANY_TICKERS_BYTES",
    "normalize_cik",
    "normalize_accession",
    "filing_index_url",
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


class SECEdgarDocumentTooLargeException(SECException):
    """A filing document over ``MAX_DOCUMENT_BYTES`` (decoded). Permanent for that document:
    asking again fetches the same bytes, so the caller skips it rather than retrying.

    The ``SECEdgar`` prefix is load-bearing: ``classify_exception`` maps this module's
    classes by EXACT name or by that prefix (``api/error_response.py``), so the class
    classifies as a data-source outage without a new branch there.
    """

    def __init__(
        self,
        message: str,
        *,
        limit_bytes: int,
        size_bytes: Optional[int] = None,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.limit_bytes = limit_bytes
        # The declared Content-Length when SEC sent one, else None (the body ran past the cap
        # while streaming and was abandoned there — its real size was never read).
        self.size_bytes = size_bytes


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


#: Filing documents (the EDGAR Archives) are served from www.sec.gov, NOT from the API host in
#: ``settings.SEC_EDGAR_BASE_URL`` (data.sec.gov). Same fair-access policy, same User-Agent.
SEC_ARCHIVES_BASE_URL = "https://www.sec.gov"

#: The largest filing document ``get_filing_document`` reads, in DECODED bytes (so a gzip body
#: cannot inflate past it). An 8-K body or an EX-99.1 press release is ~20-300 KB; a 10-K's
#: primary document can pass 10 MB, which the Learn peg reader never needs and must not hold.
MAX_DOCUMENT_BYTES = 3 * 1024 * 1024

#: SEC's ticker → CIK table (one JSON object, ~10k listings). Served from www.sec.gov like the
#: Archives, under the same fair-access policy.
COMPANY_TICKERS_URL = f"{SEC_ARCHIVES_BASE_URL}/files/company_tickers.json"
#: The largest company_tickers.json body read, in DECODED bytes. The file is ~0.8 MB today; ten
#: times that is room to grow while a broken or hostile answer still cannot fill memory.
MAX_COMPANY_TICKERS_BYTES = 8 * 1024 * 1024

# An accession number: 10-digit filer id, 2-digit year, 6-digit sequence — dashed or not.
_ACCESSION_RE = re.compile(r"^([0-9]{10})-?([0-9]{2})-?([0-9]{6})$")
# A document file name inside one filing folder. It goes into the URL PATH: no slash, no
# leading dot (so no "..", no hidden name), no query or fragment characters.
_DOCUMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


def normalize_accession(value: Any) -> Optional[str]:
    """An accession number in SEC's dashed form (``0001045810-25-000023``), or None.

    Accepts the dashed or the 18-digit undashed form, outer whitespace ignored. None for
    anything else (a bool, a non-string, Unicode digits, a wrong length).
    """
    if not isinstance(value, str):
        return None
    m = _ACCESSION_RE.match(value.strip())
    if m is None:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def filing_index_url(cik: Any, accession: Any) -> Optional[str]:
    """The public index page of one filing — the link a Learn fact cites — or None when
    ``cik`` / ``accession`` is not valid. Pure: no I/O."""
    cik10 = normalize_cik(cik)
    acc = normalize_accession(accession)
    if cik10 is None or acc is None:
        return None
    return (
        f"{SEC_ARCHIVES_BASE_URL}/Archives/edgar/data/{int(cik10)}/"
        f"{acc.replace('-', '')}/{acc}-index.htm"
    )


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


# ── Shared request steps for the Learn reads (submissions, filing documents) ───
# `get_company_concept` below keeps its own inline copy of these steps, unchanged; the two
# newer reads share them, so the refusal order (config → circuit → limiter → circuit again)
# and the status split are identical across all three.


def _refuse_before_io(what: str) -> str:
    """The configured User-Agent, after the two refusals that need no network I/O: a blank
    or unsendable User-Agent (``SECConfigException``) and an open circuit
    (``SECRateLimitException``)."""
    if not user_agent_configured():
        # Never the value itself: it carries a contact address.
        raise SECConfigException(
            "SEC EDGAR: SEC_EDGAR_USER_AGENT is not set or is not printable ASCII — no "
            f"request made for {what}"
        )
    _raise_if_circuit_open(what)
    return _user_agent()


def _raise_if_circuit_open(what: str) -> None:
    remaining = _circuit_remaining()
    if remaining > 0:
        raise SECRateLimitException(
            f"SEC EDGAR: circuit open after a 429/403 ({remaining:.0f}s left) — no "
            f"request made for {what}",
            retry_after=remaining,
        )


async def _take_request_slot(what: str) -> None:
    await _rate_limit()
    # The wait can outlast another caller's 429 / 403: re-check before sending.
    _raise_if_circuit_open(what)


def _raise_for_status(status: int, retry_after: Optional[float], body: str, what: str) -> None:
    """Raise the typed exception for a status that is neither 200 nor 404 — the same split
    as ``get_company_concept``: 429/403 open the circuit, 408/5xx are transient, any other
    4xx is a definite refusal. ``body`` is an excerpt; it is scrubbed here."""
    if status in (429, 403):
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
    if status == 408 or status >= 500:
        raise SECUnavailableException(
            f"SEC EDGAR: HTTP {status} for {what}: {_scrub(body[:300])}", status_code=status
        )
    if 400 <= status < 500:
        raise SECException(
            f"SEC EDGAR: HTTP {status} for {what}: {_scrub(body[:300])}", status_code=status
        )
    raise SECUnavailableException(
        f"SEC EDGAR: unexpected HTTP {status} for {what}", status_code=status
    )


def _transport_error(exc: httpx.HTTPError, what: str) -> SECUnavailableException:
    if isinstance(exc, httpx.TimeoutException):
        return SECUnavailableException(
            f"SEC EDGAR: request timed out for {what}: {type(exc).__name__}"
        )
    return SECUnavailableException(
        f"SEC EDGAR: request failed for {what}: {type(exc).__name__}: {_scrub(str(exc))}"
    )


def _decode_document(body: bytes, charset: Optional[str]) -> str:
    """Bytes → text: the declared charset, else UTF-8, else Windows-1252 (older EDGAR HTML is
    often cp1252 with no charset). Never raises: an undecodable byte becomes U+FFFD, which no
    peg span can match, so a bad byte costs a quote rather than the whole document."""
    if charset:
        try:
            return body.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


#: How much of a refused document response is read for the error message.
_ERROR_BODY_EXCERPT_BYTES = 4096


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

    async def get_submissions(self, cik: Any) -> Optional[Dict[str, Any]]:
        """One company's submissions record (name, tickers, exchanges, SIC, recent filings).

        Returns the parsed JSON object on 200, None on 404 (SEC has no such CIK).

        Raises:
            ValueError: ``cik`` is not a valid CIK (no I/O).
            SECConfigException: ``settings.SEC_EDGAR_USER_AGENT`` is unusable (no I/O).
            SECRateLimitException: a 429 or 403, or the circuit they open is still open.
            SECUnavailableException: 5xx, 408, timeout, network error, or a 200 whose body
                is not a JSON object.
            SECException: any other 4xx (``status_code`` set).
        """
        cik10 = normalize_cik(cik)
        if cik10 is None:
            raise ValueError(f"invalid CIK: {cik!r}")
        what = f"CIK{cik10} submissions"
        user_agent = _refuse_before_io(what)
        await _take_request_slot(what)

        url = f"{settings.SEC_EDGAR_BASE_URL.rstrip('/')}/submissions/CIK{cik10}.json"
        headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        }
        try:
            resp = await _get_http_client().get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise _transport_error(exc, what) from exc

        status = resp.status_code
        if status == 404:
            return None
        if status != 200:
            _raise_for_status(status, _retry_after(resp), resp.text, what)
        try:
            data = resp.json()
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

    async def get_filing_document(
        self, cik: Any, accession: Any, document: Any
    ) -> Optional[str]:
        """One document of one filing, as raw text (HTML stays HTML — the caller converts).

        The body is STREAMED and abandoned once it passes ``MAX_DOCUMENT_BYTES`` decoded
        bytes (or a declared Content-Length says it will), so an oversized 10-K or a gzip
        bomb is never held in memory.

        Returns the text on 200, None on 404 (no such filing or document).

        Raises:
            ValueError: ``cik``, ``accession`` or ``document`` is not valid (no I/O).
            SECConfigException / SECRateLimitException / SECUnavailableException /
                SECException: as ``get_submissions``.
            SECEdgarDocumentTooLargeException: the document is over the cap.
        """
        cik10 = normalize_cik(cik)
        if cik10 is None:
            raise ValueError(f"invalid CIK: {cik!r}")
        acc = normalize_accession(accession)
        if acc is None:
            raise ValueError(f"invalid accession number: {accession!r}")
        if not isinstance(document, str) or not _DOCUMENT_RE.match(document):
            raise ValueError(f"invalid filing document name: {document!r}")
        what = f"CIK{cik10} {acc} {document}"
        user_agent = _refuse_before_io(what)
        await _take_request_slot(what)

        url = (
            f"{SEC_ARCHIVES_BASE_URL}/Archives/edgar/data/{int(cik10)}/"
            f"{acc.replace('-', '')}/{document}"
        )
        headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "text/html, application/xhtml+xml, text/plain;q=0.9, */*;q=0.5",
        }
        try:
            async with _get_http_client().stream("GET", url, headers=headers) as resp:
                status = resp.status_code
                if status == 404:
                    return None
                if status != 200:
                    excerpt = bytearray()
                    async for chunk in resp.aiter_bytes():
                        excerpt.extend(chunk)
                        if len(excerpt) >= _ERROR_BODY_EXCERPT_BYTES:
                            break
                    _raise_for_status(
                        status,
                        _retry_after(resp),
                        bytes(excerpt[:_ERROR_BODY_EXCERPT_BYTES]).decode(
                            "utf-8", errors="replace"
                        ),
                        what,
                    )

                declared = resp.headers.get("content-length", "")
                # A gzip body's Content-Length is its COMPRESSED size: over the cap it is
                # certainly too large; under it, the streaming count below still decides.
                if (
                    declared.isascii()
                    and declared.isdigit()
                    and int(declared) > MAX_DOCUMENT_BYTES
                ):
                    raise SECEdgarDocumentTooLargeException(
                        f"SEC EDGAR: {what} declares {int(declared)} bytes, over the "
                        f"{MAX_DOCUMENT_BYTES}-byte cap — not read",
                        limit_bytes=MAX_DOCUMENT_BYTES,
                        size_bytes=int(declared),
                        status_code=status,
                    )
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_DOCUMENT_BYTES:
                        raise SECEdgarDocumentTooLargeException(
                            f"SEC EDGAR: {what} ran past the {MAX_DOCUMENT_BYTES}-byte cap "
                            "while streaming — abandoned",
                            limit_bytes=MAX_DOCUMENT_BYTES,
                            status_code=status,
                        )
                charset = resp.charset_encoding
        except httpx.HTTPError as exc:
            raise _transport_error(exc, what) from exc
        return _decode_document(bytes(body), charset)

    async def get_company_tickers(self) -> Optional[Dict[str, Any]]:
        """SEC's ticker → CIK table (``company_tickers.json``) as the parsed JSON object, or
        None on 404. HTTP in, dict out: the map, its validation and its cache live in the
        caller (``services/learn_weekly/signals.py``).

        The body is STREAMED and abandoned past ``MAX_COMPANY_TICKERS_BYTES`` decoded bytes
        (or a declared Content-Length over it), exactly like a filing document.

        Raises:
            SECConfigException / SECRateLimitException / SECUnavailableException /
                SECException: as ``get_submissions`` (one shared circuit across every read).
            SECEdgarDocumentTooLargeException: the body is over the cap.
            SECUnavailableException: a 200 whose body is not a JSON object.
        """
        what = "company_tickers.json"
        user_agent = _refuse_before_io(what)
        await _take_request_slot(what)
        headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        }
        try:
            async with _get_http_client().stream("GET", COMPANY_TICKERS_URL, headers=headers) as resp:
                status = resp.status_code
                if status == 404:
                    logger.warning("[sec-edgar-tickers-404] SEC answered 404 for %s", what)
                    return None
                if status != 200:
                    excerpt = bytearray()
                    async for chunk in resp.aiter_bytes():
                        excerpt.extend(chunk)
                        if len(excerpt) >= _ERROR_BODY_EXCERPT_BYTES:
                            break
                    _raise_for_status(
                        status,
                        _retry_after(resp),
                        bytes(excerpt[:_ERROR_BODY_EXCERPT_BYTES]).decode(
                            "utf-8", errors="replace"
                        ),
                        what,
                    )
                declared = resp.headers.get("content-length", "")
                if (
                    declared.isascii()
                    and declared.isdigit()
                    and int(declared) > MAX_COMPANY_TICKERS_BYTES
                ):
                    raise SECEdgarDocumentTooLargeException(
                        f"SEC EDGAR: {what} declares {int(declared)} bytes, over the "
                        f"{MAX_COMPANY_TICKERS_BYTES}-byte cap — not read",
                        limit_bytes=MAX_COMPANY_TICKERS_BYTES,
                        size_bytes=int(declared),
                        status_code=status,
                    )
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_COMPANY_TICKERS_BYTES:
                        raise SECEdgarDocumentTooLargeException(
                            f"SEC EDGAR: {what} ran past the {MAX_COMPANY_TICKERS_BYTES}-byte "
                            "cap while streaming — abandoned",
                            limit_bytes=MAX_COMPANY_TICKERS_BYTES,
                            status_code=status,
                        )
        except httpx.HTTPError as exc:
            raise _transport_error(exc, what) from exc
        try:
            data = json.loads(bytes(body).decode("utf-8"))
        # UnicodeDecodeError and JSONDecodeError are ValueErrors; a body nested too deep for
        # the parser raises RecursionError (as in get_company_concept).
        except (ValueError, RecursionError) as exc:
            raise SECUnavailableException(
                f"SEC EDGAR: 200 with a body that is not JSON for {what}: {type(exc).__name__}",
                status_code=200,
            ) from exc
        if not isinstance(data, dict):
            raise SECUnavailableException(
                f"SEC EDGAR: 200 with a JSON {type(data).__name__}, not an object, for {what}",
                status_code=200,
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
