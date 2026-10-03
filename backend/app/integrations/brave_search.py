"""
Brave Search API — a thin web-search client for report chat's `web_search` tool.

ONE caller: `app/services/chat_web_search_service.py` (the gate, the budget, the per-turn guard,
the digest and the source pills all live there). Integration-layer rules
(.claude/rules/integrations.md): HTTP in, dict out; typed exceptions; a lazy module-level
`httpx.AsyncClient` closed in the app lifespan (`close_brave_search_client`); NO cache of any kind
and no Supabase — Brave's terms allow transient storage only, so nothing here keeps a result.

WHY BRAVE AND NOT GEMINI'S BUILT-IN SEARCH GROUNDING: grounded answers must show a Google-branded
"Search Suggestions" chip on every answer and may not be modified, mixed or cached — which a
"Cay AI by Caydex" answer cannot honour (CLAUDE.md invariant 7) — and gemini-2.5 cannot combine
the built-in search tool with function tools in one call. A function tool backed by a plain
search API has neither problem.

THE `not_run` FLAG IS THE POINT OF THE EXCEPTION SHAPE. The service claims a daily budget unit
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
`X-RateLimit-Remaining` / `X-RateLimit-Reset` as comma-separated per-window values (per second,
then per month).
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None

#: Brave's freshness windows (past day / week / month / year). Anything else is not sent.
_FRESHNESS = frozenset({"pd", "pw", "pm", "py"})
#: Brave answers at most 20 web results per request.
_MAX_COUNT = 20
#: Warn when the MONTHLY window's remaining requests fall to this.
_MONTHLY_REMAINING_WARN = 50
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


def _warn_on_low_quota(headers: httpx.Headers) -> None:
    remaining = _window_values(headers.get("x-ratelimit-remaining"))
    if remaining and remaining[-1] <= _MONTHLY_REMAINING_WARN:
        logger.warning("brave search: monthly request quota nearly spent (remaining=%d)",
                       int(remaining[-1]))


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
