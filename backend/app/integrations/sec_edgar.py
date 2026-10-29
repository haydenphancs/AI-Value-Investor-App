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

Two more for the Trillion Club stake finder (plan "automatic stake finder", S2: discovery
only — every fact comes from the documents themselves, never from a search hit):
  - ``full_text_search``: ``GET https://efts.sec.gov/LATEST/search-index`` (``SEC_EFTS_URL``)
    — EDGAR full-text search, one page of ``EFTS_PAGE_SIZE`` hits per call, the raw JSON
    object returned (hits per DOCUMENT, plus the ``query`` EFTS echoes, which the caller
    validates). Every argument is validated before any I/O, including the two traps that
    return 0 hits without an error: parentheses (EFTS reads them as literal text) and a
    ``/A`` form (a ROOT form already includes its amendments). Same User-Agent and limiter,
    streamed and refused over ``MAX_EFTS_BYTES``; ONE retry of a 5xx / 408 / transport error
    after ``_EFTS_RETRY_DELAY``. A 429 / 403 opens EFTS's OWN circuit
    (``EFTS_CIRCUIT_SECONDS``, ``SECEdgarFullTextThrottled``) and never the shared one, so a
    throttled search does not stop the Learn reads or the buyback check — while the shared
    circuit, when open, refuses searches too. The query text is logged at DEBUG only.
  - ``get_filing_document(..., max_bytes=)``: the finder reads prospectuses and S-1s up to
    ``MAX_DISCOVERY_DOCUMENT_BYTES``; without ``max_bytes`` the cap stays
    ``MAX_DOCUMENT_BYTES``.

Behaviour:
  - ``settings.SEC_EDGAR_USER_AGENT`` blank, or not printable ASCII →
    ``SECConfigException`` before any I/O (the check is OFF until the owner sets it;
    ``user_agent_configured`` is the one predicate, shared with the service).
  - Requests start at least ``1 / settings.SEC_EDGAR_MAX_RPS`` seconds apart, process-wide.
  - A 429 or 403 opens a process-wide circuit for ``CIRCUIT_SECONDS``: every call in that
    window raises ``SECRateLimitException`` with no network I/O, so a throttled client
    stops knocking instead of extending its own block. (Full-text search keeps a circuit of
    its own; above.)
  - The User-Agent carries a contact address: it never appears in an exception message or
    a log line (``_scrub``).

HTTP in, dict out (rules/integrations.md). The two-tier cache, the dedup and the stale
fallback live in the service.
"""

from __future__ import annotations

import asyncio
import collections.abc
import hashlib
import json
import logging
import re
import time
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

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
    "SECEdgarFullTextThrottled",
    "SECEdgarClient",
    "SEC_ARCHIVES_BASE_URL",
    "MAX_DOCUMENT_BYTES",
    "MAX_DISCOVERY_DOCUMENT_BYTES",
    "COMPANY_TICKERS_URL",
    "MAX_COMPANY_TICKERS_BYTES",
    "SEC_EFTS_URL",
    "MAX_EFTS_BYTES",
    "EFTS_PAGE_SIZE",
    "EFTS_MAX_WINDOW",
    "EFTS_CIRCUIT_SECONDS",
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


class SECEdgarFullTextThrottled(SECRateLimitException):
    """EDGAR full-text search (efts.sec.gov) answered 429 or 403, or a search was asked for
    while the circuit those open is still open (no I/O; ``retry_after`` = its remaining
    seconds, or SEC's Retry-After on the answer itself).

    That circuit is full-text search's OWN (``EFTS_CIRCUIT_SECONDS``): a throttled search
    never closes the submissions, document and XBRL reads the Learn engine and the buyback
    check share. The reverse does hold: while the SHARED circuit is open a search refuses
    too, with a plain ``SECRateLimitException`` — every SEC read is off then, not only search.

    The ``SECEdgar`` prefix is load-bearing (``classify_exception``), as for
    ``SECEdgarDocumentTooLargeException``.
    """


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
#: The highest cap a caller may ask ``get_filing_document(max_bytes=)`` for, in DECODED bytes.
#: The stake finder reads whole prospectuses and S-1s (a few MB of HTML); a 10-K with every
#: table inlined can still pass it, and is refused rather than held in memory.
MAX_DISCOVERY_DOCUMENT_BYTES = 12 * 1024 * 1024

#: SEC's ticker → CIK table (one JSON object, ~10k listings). Served from www.sec.gov like the
#: Archives, under the same fair-access policy.
COMPANY_TICKERS_URL = f"{SEC_ARCHIVES_BASE_URL}/files/company_tickers.json"
#: The largest company_tickers.json body read, in DECODED bytes. The file is ~0.8 MB today; ten
#: times that is room to grow while a broken or hostile answer still cannot fill memory.
MAX_COMPANY_TICKERS_BYTES = 8 * 1024 * 1024

#: EDGAR full-text search — the API behind sec.gov's full-text search page. Another host, the
#: same fair-access policy, User-Agent and limiter, but a circuit of its own (below).
SEC_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
#: The largest search answer read, in DECODED bytes. A page of 100 hits is ~30-60 KB (EFTS
#: leaves the document text out of each hit); anything near this is broken or hostile.
MAX_EFTS_BYTES = 4 * 1024 * 1024
#: Hits per EFTS page (fixed by SEC); ``offset`` moves in steps of it.
EFTS_PAGE_SIZE = 100
#: EFTS's result window (Elasticsearch's): no page can start at or past 10,000 hits, so a date
#: range holding more is split into smaller ranges rather than paged.
EFTS_MAX_WINDOW = 10_000
#: Seconds before the ONE retry of a 5xx / 408 / transport failure (tests patch it to 0).
_EFTS_RETRY_DELAY = 2.0
_EFTS_ATTEMPTS = 2
_EFTS_QUERY_MAX_CHARS = 1000
#: A ROOT form type (8-K, 6-K, 424B5, S-1, SCHEDULE 13D). No "/": a root already includes its
#: amendments, and a "/A" form in the list makes EFTS return 0 hits, with no error. It must end
#: in a letter or digit (and ``_efts_request`` refuses a double space): EFTS matches the form
#: exactly, so "8-K " would silently match nothing too.
_EFTS_FORM_RE = re.compile(r"[A-Z0-9](?:[A-Z0-9 -]{0,18}[A-Z0-9])?")
# Bounds on the filter lists: a caller's bug must not build an unbounded URL.
_EFTS_MAX_FORMS = 50
_EFTS_MAX_CIKS = 100

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


#: How long a 429 / 403 from full-text search keeps every SEARCH off the network.
EFTS_CIRCUIT_SECONDS = 600.0

#: Monotonic time before which every full-text search raises SECEdgarFullTextThrottled without
#: I/O. Apart from `_circuit_open_until`: EFTS throttles on its own, and its 429 must not stop
#: the Learn reads or the buyback check.
_efts_circuit_open_until: float = 0.0


def _efts_circuit_remaining() -> float:
    return _efts_circuit_open_until - time.monotonic()


def _open_efts_circuit() -> None:
    global _efts_circuit_open_until
    _efts_circuit_open_until = max(
        _efts_circuit_open_until, time.monotonic() + EFTS_CIRCUIT_SECONDS
    )


def _raise_if_efts_circuit_open(what: str) -> None:
    remaining = _efts_circuit_remaining()
    if remaining > 0:
        raise SECEdgarFullTextThrottled(
            f"SEC EDGAR: full-text search circuit open after a 429/403 ({remaining:.0f}s left) "
            f"— no request made for {what}",
            retry_after=remaining,
        )


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


def _document_limit(max_bytes: Any) -> int:
    """``get_filing_document``'s byte cap: ``MAX_DOCUMENT_BYTES`` for None, else ``max_bytes``
    when it is an int in [1, ``MAX_DISCOVERY_DOCUMENT_BYTES``] (ValueError otherwise)."""
    if max_bytes is None:
        return MAX_DOCUMENT_BYTES
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or not 1 <= max_bytes <= MAX_DISCOVERY_DOCUMENT_BYTES
    ):
        raise ValueError(
            f"max_bytes must be None or an int in [1, {MAX_DISCOVERY_DOCUMENT_BYTES}], "
            f"got {max_bytes!r}"
        )
    return max_bytes


# ── EDGAR full-text search: arguments and one streamed answer ──────────


def _efts_list(value: Any, name: str, cap: int) -> List[Any]:
    # A str is a Sequence too: forms="424B5" would go out as the forms 4, 2, 4, B, 5 (each one
    # passes the form rule) and ciks="1326811" as seven one-digit CIKs — 0 hits, no error.
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(
        value, collections.abc.Sequence
    ):
        raise ValueError(
            f"full-text search: {name} must be a list or tuple, got {type(value).__name__}"
        )
    if len(value) > cap:
        raise ValueError(f"full-text search: {len(value)} {name}, more than the {cap} allowed")
    return list(value)


def _efts_request(
    query: Any, *, start: Any, end: Any, forms: Any, ciks: Any, offset: Any
) -> Tuple[Dict[str, str], str]:
    """The query parameters of one search and its ``what`` label — or ValueError, before any
    I/O. ``what`` names the search by a digest of its query (``q#…``), never by the text,
    which is logged at DEBUG only."""
    if not isinstance(query, str):
        raise ValueError(f"full-text search: the query must be a str, got {type(query).__name__}")
    if not 1 <= len(query) <= _EFTS_QUERY_MAX_CHARS or not query.strip():
        raise ValueError(
            f"full-text search: the query must be 1-{_EFTS_QUERY_MAX_CHARS} characters and "
            f"not blank (got {len(query)} characters)"
        )
    if not (query.isascii() and query.isprintable()):
        raise ValueError("full-text search: the query must be printable ASCII")
    if "(" in query or ")" in query:
        raise ValueError(
            "full-text search: the query must not contain '(' or ')' — EFTS reads them as "
            "literal text, so the search silently matches nothing"
        )
    pieces = query.split('"')
    if len(pieces) % 2 == 0:
        raise ValueError(
            f"full-text search: the query has {query.count(chr(34))} double quotes — an "
            "unclosed phrase"
        )
    if any(not phrase.strip() for phrase in pieces[1::2]):
        raise ValueError("full-text search: the query has an empty quoted phrase")
    for name, day in (("start", start), ("end", end)):
        if not isinstance(day, date) or isinstance(day, datetime):
            raise ValueError(
                f"full-text search: {name} must be a date (not a datetime), got "
                f"{type(day).__name__}"
            )
    if start > end:
        raise ValueError(f"full-text search: start {start} is after end {end}")
    form_list = _efts_list(forms, "forms", _EFTS_MAX_FORMS)
    for form in form_list:
        if not isinstance(form, str) or not _EFTS_FORM_RE.fullmatch(form) or "  " in form:
            raise ValueError(
                f"full-text search: bad form {form!r} — a ROOT form in upper case (8-K, "
                "424B5, SCHEDULE 13D), never a '/A' amendment"
            )
    cik_list: List[str] = []
    for raw in _efts_list(ciks, "ciks", _EFTS_MAX_CIKS):
        cik10 = normalize_cik(raw)
        if cik10 is None:
            raise ValueError(f"full-text search: invalid CIK {raw!r}")
        cik_list.append(cik10)
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset < EFTS_MAX_WINDOW
        or offset % EFTS_PAGE_SIZE
    ):
        raise ValueError(
            f"full-text search: offset must be a multiple of {EFTS_PAGE_SIZE} in "
            f"[0, {EFTS_MAX_WINDOW}), got {offset!r}"
        )

    params: Dict[str, str] = {
        "q": query,
        "dateRange": "custom",
        "startdt": start.isoformat(),
        "enddt": end.isoformat(),
    }
    if form_list:
        params["forms"] = ",".join(form_list)
    if cik_list:
        params["ciks"] = ",".join(cik_list)
    if offset:
        params["from"] = str(offset)
    digest = hashlib.sha256(query.encode("ascii")).hexdigest()[:12]
    what = (
        f"EFTS search q#{digest} {start.isoformat()}..{end.isoformat()} "
        f"forms={len(form_list)} ciks={len(cik_list)} from={offset}"
    )
    return params, what


async def _efts_get(
    params: Dict[str, str], headers: Dict[str, str], what: str
) -> Tuple[int, bytes, Optional[float]]:
    """One streamed GET: ``(status, body, Retry-After)``. A 200's body is read whole but
    abandoned past ``MAX_EFTS_BYTES`` decoded bytes (or a declared Content-Length over it):
    ``SECEdgarDocumentTooLargeException``. Any other status brings back a bounded excerpt
    for the error message. Transport errors propagate as ``httpx.HTTPError``."""
    async with _get_http_client().stream(
        "GET", SEC_EFTS_URL, params=params, headers=headers
    ) as resp:
        status = resp.status_code
        if status != 200:
            excerpt = bytearray()
            async for chunk in resp.aiter_bytes():
                excerpt.extend(chunk)
                if len(excerpt) >= _ERROR_BODY_EXCERPT_BYTES:
                    break
            return status, bytes(excerpt[:_ERROR_BODY_EXCERPT_BYTES]), _retry_after(resp)
        declared = resp.headers.get("content-length", "")
        # A gzip body's Content-Length is its COMPRESSED size: over the cap it is certainly too
        # large; under it, the streaming count below still decides.
        if declared.isascii() and declared.isdigit() and int(declared) > MAX_EFTS_BYTES:
            raise SECEdgarDocumentTooLargeException(
                f"SEC EDGAR: {what} declares {int(declared)} bytes, over the "
                f"{MAX_EFTS_BYTES}-byte cap — not read",
                limit_bytes=MAX_EFTS_BYTES,
                size_bytes=int(declared),
                status_code=status,
            )
        body = bytearray()
        async for chunk in resp.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_EFTS_BYTES:
                raise SECEdgarDocumentTooLargeException(
                    f"SEC EDGAR: {what} ran past the {MAX_EFTS_BYTES}-byte cap while "
                    "streaming — abandoned",
                    limit_bytes=MAX_EFTS_BYTES,
                    status_code=status,
                )
        return status, bytes(body), None


def _efts_json(body: bytes, what: str) -> Dict[str, Any]:
    try:
        # utf-8-sig: JSON is UTF-8, and a leading BOM must not turn a good answer into an outage.
        data = json.loads(body.decode("utf-8-sig"))
    # UnicodeDecodeError and JSONDecodeError are ValueErrors; a body nested too deep for the
    # parser raises RecursionError (as in get_company_concept).
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


class SECEdgarClient:
    """Thin async wrapper around SEC EDGAR's public reads: XBRL ``companyconcept``,
    submissions, filing documents, the ticker table and full-text search."""

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
        self, cik: Any, accession: Any, document: Any, *, max_bytes: Optional[int] = None
    ) -> Optional[str]:
        """One document of one filing, as raw text (HTML stays HTML — the caller converts).

        The body is STREAMED and abandoned once it passes the cap in decoded bytes (or a
        declared Content-Length says it will), so an oversized 10-K or a gzip bomb is never
        held in memory. The cap is ``MAX_DOCUMENT_BYTES`` unless ``max_bytes`` (keyword-only,
        an int in [1, ``MAX_DISCOVERY_DOCUMENT_BYTES``]) says otherwise — only the stake
        finder raises it, to read whole prospectuses.

        Returns the text on 200, None on 404 (no such filing or document).

        Raises:
            ValueError: ``cik``, ``accession``, ``document`` or ``max_bytes`` is not valid
                (no I/O). ``document`` must FULLY match a plain file name: no folder, no
                leading dot, no trailing newline.
            SECConfigException / SECRateLimitException / SECUnavailableException /
                SECException: as ``get_submissions``.
            SECEdgarDocumentTooLargeException: the document is over the cap
                (``limit_bytes`` is the cap this call used).
        """
        cik10 = normalize_cik(cik)
        if cik10 is None:
            raise ValueError(f"invalid CIK: {cik!r}")
        acc = normalize_accession(accession)
        if acc is None:
            raise ValueError(f"invalid accession number: {accession!r}")
        # fullmatch: `$` also matches before a trailing newline, which would reach the URL.
        if not isinstance(document, str) or not _DOCUMENT_RE.fullmatch(document):
            raise ValueError(f"invalid filing document name: {document!r}")
        limit = _document_limit(max_bytes)
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
                    and int(declared) > limit
                ):
                    raise SECEdgarDocumentTooLargeException(
                        f"SEC EDGAR: {what} declares {int(declared)} bytes, over the "
                        f"{limit}-byte cap — not read",
                        limit_bytes=limit,
                        size_bytes=int(declared),
                        status_code=status,
                    )
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > limit:
                        raise SECEdgarDocumentTooLargeException(
                            f"SEC EDGAR: {what} ran past the {limit}-byte cap "
                            "while streaming — abandoned",
                            limit_bytes=limit,
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

    async def full_text_search(
        self,
        query: str,
        *,
        start: date,
        end: date,
        forms: Sequence[str] = (),
        ciks: Sequence[Any] = (),
        offset: int = 0,
    ) -> Dict[str, Any]:
        """One page (at most ``EFTS_PAGE_SIZE`` hits) of EDGAR full-text search, as EFTS's raw
        JSON object: ``hits.hits[*]._source`` (one hit per DOCUMENT), ``hits.total``, and
        ``query`` — the search EFTS actually ran, which the caller validates. HTTP in, dict
        out: the echo check, paging and window splitting live in the caller.

        ``query`` is EFTS syntax (``"exact phrase"``, ``OR``); ``start`` / ``end`` bound the
        filing date, inclusive; ``forms`` are ROOT forms (a root includes its amendments);
        ``ciks`` restricts the filers (any form ``normalize_cik`` accepts); ``offset`` pages in
        steps of ``EFTS_PAGE_SIZE`` below ``EFTS_MAX_WINDOW``. Empty ``forms`` / ``ciks`` and a
        zero ``offset`` are left out of the request.

        Raises:
            ValueError: an invalid argument, before any I/O. The query must be 1-1000
                printable ASCII characters, not blank, with no ``(`` / ``)`` and balanced,
                non-empty ``"`` phrases; ``start`` / ``end`` dates (not datetimes),
                ``start <= end``; each form a root form (no ``/``); each CIK valid.
            SECConfigException: ``settings.SEC_EDGAR_USER_AGENT`` is unusable (no I/O).
            SECRateLimitException: the SHARED circuit is open — every SEC read is off (no I/O).
            SECEdgarFullTextThrottled: EFTS answered 429 / 403 (its own circuit opens), or
                that circuit is open (no I/O) — only full-text search is off.
            SECUnavailableException: a 5xx, 408, transport error or PARTIAL answer
                (``timed_out``, a failed shard, or fewer successful shards than the total) on
                both tries (one retry after ``_EFTS_RETRY_DELAY``), or a 200 whose body is not
                a JSON object.
            SECEdgarDocumentTooLargeException: the answer is over ``MAX_EFTS_BYTES``.
            SECException: any other 4xx, 404 included (``status_code`` set).
        """
        params, what = _efts_request(
            query, start=start, end=end, forms=forms, ciks=ciks, offset=offset
        )
        user_agent = _refuse_before_io(what)
        _raise_if_efts_circuit_open(what)
        logger.debug("[sec-efts-query] %s: q=%r", what, query)
        headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        }
        for attempt in range(1, _EFTS_ATTEMPTS + 1):
            last = attempt == _EFTS_ATTEMPTS
            if attempt > 1:
                await asyncio.sleep(_EFTS_RETRY_DELAY)
            await _take_request_slot(what)
            # The wait can outlast another search's 429 / 403: re-check before sending.
            _raise_if_efts_circuit_open(what)
            try:
                status, body, retry_after = await _efts_get(params, headers, what)
            except httpx.HTTPError as exc:
                failure = _transport_error(exc, what)
                if not last:
                    _log_efts_retry(failure)
                    continue
                raise SECUnavailableException(
                    f"{failure} (after one retry)", status_code=failure.status_code
                ) from exc

            if status == 200:
                data = _efts_json(body, what)
                partial = _efts_partial(data)
                if partial is None:
                    return data
                # A 200 built from SOME shards drops their hits silently (Elasticsearch
                # answers partial results by default), or carries the backend's own failure
                # envelope: unavailable, retried like a 5xx.
                failure = SECUnavailableException(
                    f"SEC EDGAR: partial full-text answer for {what} ({partial})", status_code=200
                )
                if not last:
                    _log_efts_retry(failure)
                    continue
                raise SECUnavailableException(f"{failure} (after one retry)", status_code=200)
            if status in (429, 403):
                _open_efts_circuit()
                logger.warning(
                    "[sec-efts-throttled] SEC answered %d for %s — full-text search is "
                    "refused for %.0fs (retry_after=%s); the other SEC reads are unaffected",
                    status, what, EFTS_CIRCUIT_SECONDS, retry_after,
                )
                raise SECEdgarFullTextThrottled(
                    f"SEC EDGAR: HTTP {status} for {what} (throttled or blocked; full-text "
                    f"search circuit open for {EFTS_CIRCUIT_SECONDS:.0f}s)",
                    status_code=status,
                    retry_after=retry_after,
                )
            # Scrubbed BEFORE the cut, so an echoed User-Agent cannot survive half-cut.
            excerpt = _scrub(body.decode("utf-8", errors="replace"))
            if status == 408 or status >= 500:
                failure = SECUnavailableException(
                    f"SEC EDGAR: HTTP {status} for {what}: {excerpt}", status_code=status
                )
                if not last:
                    _log_efts_retry(failure)
                    continue
                raise SECUnavailableException(
                    f"{failure} (after one retry)", status_code=status
                )
            if 400 <= status < 500:
                raise SECException(
                    f"SEC EDGAR: HTTP {status} for {what}: {excerpt}", status_code=status
                )
            raise SECUnavailableException(
                f"SEC EDGAR: unexpected HTTP {status} for {what}", status_code=status
            )
        # Unreachable: the last attempt returns or raises.
        raise SECUnavailableException(f"SEC EDGAR: no answer for {what}")


def _efts_partial(data: Mapping[str, Any]) -> Optional[str]:
    """Why an EFTS 200 is only PART of the answer — or no answer at all — or None. Absent keys
    pass (the recorded answers carry none of them); a bool is never a shard count. SEC's search
    backend can also answer 200 with its own failure envelope instead of a search result (seen
    live 2026-10-29: ``{"errorType": "ConnectionError", "errorMessage": "getaddrinfo EBUSY …",
    "trace": […]}``) — unavailable and retried like a 5xx, never read as an answer."""
    if "hits" not in data and ("errorType" in data or "errorMessage" in data):
        kind = data.get("errorType") if isinstance(data.get("errorType"), str) else "error"
        message = data.get("errorMessage") if isinstance(data.get("errorMessage"), str) else ""
        return _scrub(f"SEC's search backend answered an error body: {kind}: {message}"[:200])
    if data.get("timed_out") is True:
        return "timed_out"
    shards = data.get("_shards")
    if not isinstance(shards, Mapping):
        return None
    count = lambda k: shards.get(k) if type(shards.get(k)) is int else None  # noqa: E731
    failed, total, ok = count("failed"), count("total"), count("successful")
    if failed is not None and failed > 0:
        return f"{failed} shard(s) failed"
    if total is not None and ok is not None and ok < total:
        return f"{ok} of {total} shards answered"
    return None


def _log_efts_retry(failure: SECUnavailableException) -> None:
    # A WARNING, not silence: a retry that then succeeds would otherwise hide a flaky upstream.
    logger.warning(
        "[sec-efts-retry] %s — retrying once in %.1fs", failure, _EFTS_RETRY_DELAY
    )


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
