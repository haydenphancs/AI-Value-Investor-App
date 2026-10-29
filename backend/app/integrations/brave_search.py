"""
Brave Search API — a thin web-search client for Ask Cay AI's `web_search` tool and the Updates
Insights jump search.

TWO callers, each with its own gate and daily budget: `app/services/chat_web_search_service.py`
(Ask Cay AI — the gate, the budget, the per-turn guard, the digest and the source pills all live
there) and `app/services/jump_web_search_service.py` (why a stock jumped, 2026-10-28).
Integration-layer rules (.claude/rules/integrations.md): HTTP in, dict out; typed exceptions; a
lazy module-level `httpx.AsyncClient` closed in the app lifespan (`close_brave_search_client`); NO
cache of any kind and no Supabase — Brave's terms allow transient storage only, so nothing here
keeps a result.

WHY BRAVE AND NOT GEMINI'S BUILT-IN SEARCH GROUNDING: grounded answers must show a Google-branded
"Search Suggestions" chip on every answer and may not be modified, mixed or cached — which a
"Cay AI by Caydex" answer cannot honour (CLAUDE.md invariant 7) — and gemini-2.5 cannot combine
the built-in search tool with function tools in one call. A function tool backed by a plain
search API has neither problem.

THE `not_run` FLAG IS THE POINT OF THE EXCEPTION SHAPE. Each caller claims a daily budget unit
BEFORE the call and refunds it only when the search provably did not run (was never billed):
  * `BraveSearchNotConfiguredException` — no key / unusable key / a non-https base URL; raised
    BEFORE any I/O. not_run=True.
  * `BraveSearchAuthException`          — 401 / 403 (a bad key or a plan without this endpoint).
    not_run=True. Logged at ERROR: it never heals by itself.
  * `BraveSearchRateLimitException`     — 429; `retry_after` (seconds) when known. not_run=True.
  * `BraveSearchRequestException`       — any other 4xx (400 / 404 / 422): the request itself was
    refused. not_run=True.
  * `BraveSearchUnavailableException`   — a connect failure (`ConnectError`, `ConnectTimeout`,
    `PoolTimeout`, …) never left the machine: not_run=True. A read timeout, a dropped connection,
    any other transport error, a 5xx / 3xx or an unreadable 200 MAY have been billed: not_run=False.

SECRETS AND PRIVACY. The key travels in the `X-Subscription-Token` header only — never the URL —
and is held in a constant-`repr` wrapper (`_ApiKey`) so a Sentry frame-variable dump of any frame
in this module prints nothing secret. Exception messages carry the HTTP status and Brave's own
`error.code` (a short upper-case identifier, validated) and NOTHING else: no key, no URL, no query
(the query is derived from a user's question) and no result text. Transport exceptions are never
chained (`from None`): an httpx exception holds the request, and the request holds the key header.
Redirects are never followed, so the key cannot be carried to another host.

API facts this module relies on (Brave Search API, Web search endpoint, `GET /res/v1/web/search`):
`q` (required), `count` (≤ 20), `country`, `search_lang`, `ui_lang`, `safesearch`
(off/moderate/strict), `freshness` (pd/pw/pm/py), `text_decorations`, `result_filter`,
`extra_snippets` (plan-dependent). The answer's `web.results[]` rows carry `title`, `url`,
`description`, `age`, `page_age`, `meta_url.hostname`, `profile.name`, `extra_snippets[]`;
`query.altered` is the spell-corrected query when Brave changed it. Rate-limit state rides in
`X-RateLimit-Limit` / `-Policy` / `-Remaining` / `-Reset`, one comma-separated value per window in
the order the policy names them ("1;w=1, 15000;w=2592000": per second, then per 30 days). A monthly
limit of 0 means UNLIMITED (Brave's rate-limiting guide): a metered plan, capped in dollars in
Brave's dashboard rather than in requests, whose monthly remaining count reads 0 on every answer.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None
#: Two INFO lines, each logged at most once per process: the plan's monthly quota (so the logs say
#: what the key is on) and rate-limit headers no monthly quota can be read from (so a warning that
#: can never fire shows in the logs).
_quota_logged = False
_unreadable_logged = False

#: Brave's freshness windows (past day / week / month / year). Anything else is not sent.
_FRESHNESS = frozenset({"pd", "pw", "pm", "py"})
#: Brave answers at most 20 web results per request.
_MAX_COUNT = 20
#: Warn when a MONTHLY request quota's remaining requests fall to this.
_MONTHLY_REMAINING_WARN = 50
#: A rate-limit window at least this long is the monthly quota (Brave's is w=2592000, 30 days); the
#: other one is the 1-second burst limit.
_MONTHLY_WINDOW_MIN_SECONDS = 28 * 86400
#: Brave's `error.code` is a short upper-case identifier ("RATE_LIMITED", "VALIDATION", …). Anything
#: else is not echoed — the error body may quote the request back.
_ERROR_CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{0,39}")
#: A usable key is one printable token: a key pasted across a line wrap would make h11 refuse the
#: header and echo it, escaped, inside its own error text.
_PRINTABLE_KEY_RE = re.compile(r"[\x21-\x7e]+")
#: Per-field caps on what is passed through (the service trims again for the model).
_TEXT_CAP = 2000
_EXTRA_SNIPPETS_CAP = 5

#: Transport failures that provably never reached Brave (nothing was billed).
_NOT_SENT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.UnsupportedProtocol,
    httpx.LocalProtocolError,
    httpx.InvalidURL,
)


# ── Exception hierarchy ────────────────────────────────────────────────


class BraveSearchException(Exception):
    """Base for every Brave Search failure. `not_run` is a fact about the HTTP exchange: True only
    when the search provably did not run (the service refunds its budget unit on exactly that)."""

    def __init__(self, message: str, *, not_run: bool = False, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.not_run = bool(not_run)
        self.status = status


class BraveSearchNotConfiguredException(BraveSearchException):
    """No key (or an unusable one) / a non-https base URL. Raised before any I/O."""


class BraveSearchAuthException(BraveSearchException):
    """401 / 403 — a bad key, or a plan that does not include this endpoint."""


class BraveSearchRateLimitException(BraveSearchException):
    """429. `retry_after` is the wait in seconds when Brave said so."""

    def __init__(self, message: str, *, retry_after: Optional[float] = None,
                 status: Optional[int] = 429) -> None:
        super().__init__(message, not_run=True, status=status)
        self.retry_after = retry_after


class BraveSearchRequestException(BraveSearchException):
    """Any other 4xx: the request itself was refused (a bad parameter, a plan feature)."""


class BraveSearchUnavailableException(BraveSearchException):
    """Transport failure, 5xx / 3xx, or an unreadable body. `not_run` tells the two apart."""


# ── Key + client ───────────────────────────────────────────────────────


class _ApiKey:
    """The subscription token behind a CONSTANT repr: a frame-variable dump (Sentry's
    `include_local_variables`, a debugger) of any frame holding it prints nothing secret."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    def __repr__(self) -> str:
        return "<brave search key redacted>"

    __str__ = __repr__


def is_configured() -> bool:
    """A non-blank key is set. Read at call time, so unsetting it turns the feature off."""
    return bool((settings.BRAVE_SEARCH_API_KEY or "").strip())


def _api_key() -> _ApiKey:
    # Wrapped at once: no frame in this module ever holds the bare string in a local, so a
    # frame-variable dump of a raise below prints only the wrapper's constant repr.
    key = _ApiKey((settings.BRAVE_SEARCH_API_KEY or "").strip())
    if not key.value:
        raise BraveSearchNotConfiguredException(
            "brave search: not configured (BRAVE_SEARCH_API_KEY unset)", not_run=True)
    if not _PRINTABLE_KEY_RE.fullmatch(key.value):
        # The setting NAME only — never the value.
        raise BraveSearchNotConfiguredException(
            "brave search: BRAVE_SEARCH_API_KEY contains whitespace or control characters — "
            "set it again as one line", not_run=True)
    return key


def _endpoint() -> str:
    base = (settings.BRAVE_SEARCH_BASE_URL or "").strip().rstrip("/")
    if not base.lower().startswith("https://"):
        # The key rides in a header: never send it over plain http.
        raise BraveSearchNotConfiguredException(
            "brave search: BRAVE_SEARCH_BASE_URL must be an https:// URL", not_run=True)
    return f"{base}/web/search"


def _timeout() -> httpx.Timeout:
    try:
        seconds = float(settings.BRAVE_SEARCH_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        seconds = 4.0
    if not math.isfinite(seconds) or seconds <= 0:
        seconds = 4.0
    return httpx.Timeout(min(seconds, 30.0), connect=2.0)


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=_timeout(),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5,
                                keepalive_expiry=30.0),
            follow_redirects=False,  # never carry the key header to another host
        )
    return _client


async def close_brave_search_client() -> None:
    """Tear-down hook for the app.main lifespan. Idempotent."""
    global _client
    if _client is not None:
        client, _client = _client, None
        await client.aclose()


# ── Response helpers ───────────────────────────────────────────────────


def _str_or_none(value: Any, cap: int = _TEXT_CAP) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s[:cap] if s else None


def _window_values(header: Optional[str]) -> List[float]:
    """`X-RateLimit-*` headers are comma-separated per-window numbers ("1, 1999")."""
    out: List[float] = []
    for part in (header or "").split(","):
        try:
            v = float(part.strip())
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            out.append(v)
    return out


def _retry_after(headers: httpx.Headers) -> Optional[float]:
    raw = headers.get("retry-after")
    if raw is not None:
        try:
            v = float(raw.strip())
            if math.isfinite(v) and v >= 0:
                return v
        except (TypeError, ValueError):
            pass
    reset = _window_values(headers.get("x-ratelimit-reset"))
    if reset and reset[0] >= 0:
        return reset[0]
    return None


def _aligned_values(header: Optional[str]) -> Optional[List[float]]:
    """One number per window, or None when any part is unreadable: the headers are read by
    POSITION, so skipping a part would pair one window's limit with another window's count."""
    if not header or not header.strip():
        return None
    out: List[float] = []
    for part in header.split(","):
        try:
            v = float(part.strip())
        except ValueError:
            return None
        if not math.isfinite(v):
            return None
        out.append(v)
    return out


def _policy_windows(header: Optional[str]) -> Optional[List[float]]:
    """Each window's length in seconds from `X-RateLimit-Policy` ("1;w=1, 15000;w=2592000" →
    [1, 2592000]), or None when the header is missing or an entry has no readable `w`."""
    if not header or not header.strip():
        return None
    out: List[float] = []
    for entry in header.split(","):
        window: Optional[float] = None
        for param in entry.split(";")[1:]:
            name, _, value = param.partition("=")
            if name.strip().lower() == "w":
                try:
                    window = float(value.strip())
                except ValueError:
                    return None
        if window is None or not math.isfinite(window):
            return None
        out.append(window)
    return out


def _monthly_quota(headers: httpx.Headers) -> Optional[Tuple[int, int]]:
    """(limit, remaining) of the plan's monthly request quota, or None when the headers name none.

    The monthly window is the shortest one the policy gives at least 28 days; without a usable
    policy, the second of exactly two values (Brave's documented order: per second, then per
    month). A limit of 0 is returned as is: it means unlimited, so nothing can run out.
    """
    limits = _aligned_values(headers.get("x-ratelimit-limit"))
    remaining = _aligned_values(headers.get("x-ratelimit-remaining"))
    if limits is None or remaining is None or len(limits) != len(remaining):
        return None
    windows = _policy_windows(headers.get("x-ratelimit-policy"))
    if windows is not None and len(windows) == len(limits):
        long_windows = [i for i, w in enumerate(windows) if w >= _MONTHLY_WINDOW_MIN_SECONDS]
        if not long_windows:
            return None
        i = min(long_windows, key=lambda j: windows[j])
    elif len(limits) == 2:
        i = 1
    else:
        return None
    if limits[i] < 0 or remaining[i] < 0:
        return None
    return int(limits[i]), int(remaining[i])


def _log_plan_once(headers: httpx.Headers, quota: Optional[Tuple[int, int]]) -> None:
    """The plan's monthly quota, from the first answer it can be read from; and the first
    rate-limit headers no monthly quota can be read from — no window of 28 days or more, or counts
    that are missing, unreadable, misaligned or negative (answers in that shape are never warned
    about). Each is one INFO line per process, so neither hides the other in either order."""
    global _quota_logged, _unreadable_logged
    if quota is not None:
        if not _quota_logged:
            _quota_logged = True
            limit, left = quota
            if limit == 0:
                logger.info("brave search: the plan has no monthly request quota (0 = unlimited); "
                            "its spend limit is set in Brave's dashboard")
            else:
                logger.info("brave search: monthly request quota %d, %d left", limit, left)
        return
    if _unreadable_logged:
        return
    seen = [(headers.get(name) or "")[:80]
            for name in ("x-ratelimit-limit", "x-ratelimit-policy", "x-ratelimit-remaining")]
    if any(v.strip() for v in seen):
        _unreadable_logged = True
        logger.info("brave search: no monthly request quota could be read from these rate-limit "
                    "headers, so answers in this shape get no quota warning "
                    "(limit=%r policy=%r remaining=%r)", *seen)


def _warn_on_low_quota(headers: httpx.Headers) -> None:
    """Warn only when a REAL monthly request quota is nearly spent. A metered plan's monthly limit
    is 0 (unlimited) and its remaining count reads 0 on every answer: reading that count alone
    warned on every search (2026-10-29). Runs on every answer BEFORE `_raise_for`, so it never
    raises: an exception here would stand in for the typed one and lose its `not_run`."""
    try:
        quota = _monthly_quota(headers)
        _log_plan_once(headers, quota)
    except Exception as e:  # noqa: BLE001 — a diagnostic must never break a search
        logger.warning("brave search: rate-limit headers not read (%s)", type(e).__name__,
                       exc_info=True)
        return
    if quota is not None and quota[0] > 0 and quota[1] <= _MONTHLY_REMAINING_WARN:
        logger.warning("brave search: monthly request quota nearly spent (remaining=%d of %d)",
                       quota[1], quota[0])


def _error_code(resp: httpx.Response) -> str:
    """Brave's own `error.code`, validated, or "" — never any other body text."""
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — an unreadable error body is simply not echoed
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    if isinstance(code, str) and _ERROR_CODE_RE.fullmatch(code.strip()):
        return code.strip()
    return ""


def _raise_for(resp: httpx.Response) -> None:
    status = resp.status_code
    if 200 <= status < 300:
        return
    code = _error_code(resp) if 400 <= status < 500 else ""
    label = f"HTTP {status}" + (f" ({code})" if code else "")
    if status in (401, 403):
        logger.error("brave search: refused the subscription token (%s) — check "
                     "BRAVE_SEARCH_API_KEY and the plan", label)
        raise BraveSearchAuthException(f"brave search refused the key: {label}",
                                       not_run=True, status=status)
    if status == 429:
        raise BraveSearchRateLimitException(f"brave search rate limited: {label}",
                                            retry_after=_retry_after(resp.headers), status=status)
    if 400 <= status < 500:
        raise BraveSearchRequestException(f"brave search refused the request: {label}",
                                          not_run=True, status=status)
    # 5xx, and a 1xx/3xx (no redirect is ever followed): Brave answered, so it may have billed.
    raise BraveSearchUnavailableException(f"brave search unavailable: {label}",
                                          not_run=False, status=status)


def _parse_row(row: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    url = row.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    meta = row.get("meta_url") if isinstance(row.get("meta_url"), dict) else {}
    profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
    extras_raw = row.get("extra_snippets")
    extras = [s for s in (_str_or_none(x) for x in extras_raw[:_EXTRA_SNIPPETS_CAP])
              if s] if isinstance(extras_raw, list) else []
    return {
        "title": _str_or_none(row.get("title")),
        "url": url.strip(),
        "description": _str_or_none(row.get("description")),
        "age": _str_or_none(row.get("age"), 80),
        "page_age": _str_or_none(row.get("page_age"), 80),
        # Brave's view of the host. NOT what the service trusts — it re-derives the host from
        # the URL itself, and never takes `publisher` (the site's own self-description) alone.
        "hostname": _str_or_none(meta.get("hostname"), 255),
        "publisher": _str_or_none(profile.get("name"), 200),
        "extra_snippets": extras,
    }


def _parse(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise BraveSearchUnavailableException(
            "brave search answered 200 with a body that is not an object", not_run=False)
    web = body.get("web")
    rows = web.get("results") if isinstance(web, dict) else None
    results = [r for r in (_parse_row(x) for x in rows) if r] if isinstance(rows, list) else []
    query = body.get("query") if isinstance(body.get("query"), dict) else {}
    return {"results": results, "altered_query": _str_or_none(query.get("altered"), 400)}


# ── Public API ─────────────────────────────────────────────────────────


async def web_search(
    query: str,
    *,
    count: int = 10,
    freshness: Optional[str] = None,
    extra_snippets: bool = False,
) -> Dict[str, Any]:
    """One Brave web search. Returns
    ``{"results": [{title, url, description, age, page_age, hostname, publisher,
    extra_snippets}], "altered_query": str | None}`` — documented fields only, rows without a
    string `url` skipped. Raises a `BraveSearchException` subclass on every failure (see the
    module docstring for which ones set `not_run`)."""
    if not isinstance(query, str) or not query.strip():
        raise BraveSearchRequestException("brave search: empty query", not_run=True)
    key = _api_key()          # before any I/O
    url = _endpoint()
    try:
        n = int(count)
    except (TypeError, ValueError):
        n = 10
    params: Dict[str, str] = {
        "q": query.strip()[:400],
        "count": str(max(1, min(_MAX_COUNT, n))),
        "country": "US",
        "search_lang": "en",
        "ui_lang": "en-US",
        "safesearch": "strict",
        "text_decorations": "false",
        "result_filter": "web",
    }
    if isinstance(freshness, str) and freshness in _FRESHNESS:
        params["freshness"] = freshness
    if extra_snippets:
        params["extra_snippets"] = "true"
    try:
        resp = await _get_client().get(
            url, params=params, timeout=_timeout(),
            headers={"Accept": "application/json", "X-Subscription-Token": key.value},
        )
    except _NOT_SENT_ERRORS as e:
        raise BraveSearchUnavailableException(
            f"brave search transport failure before sending ({type(e).__name__})",
            not_run=True) from None
    except httpx.HTTPError as e:
        # Read/write timeouts, a dropped connection, a remote protocol error: the request may
        # have reached Brave and been billed.
        raise BraveSearchUnavailableException(
            f"brave search transport failure ({type(e).__name__})", not_run=False) from None
    _warn_on_low_quota(resp.headers)
    _raise_for(resp)
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — a non-JSON 200 (an HTML error page from a proxy)
        raise BraveSearchUnavailableException(
            "brave search answered 200 with an unreadable body", not_run=False) from None
    return _parse(body)
