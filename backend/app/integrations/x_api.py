"""
X API v2 — a thin client for the marketing PUBLISHER's X outlet (design doc §12.10).

ONE caller: `app/services/marketing/outlet_x.py`, driven by the publisher loop in
`app/services/marketing/publisher_service.py`, in the WEB process. It posts text to the brand
account that owns the X developer app, reads that account's own timeline to reconcile a post
whose outcome is unknown, and deletes a post the owner retracts. The media worker never holds
these credentials — it holds no social secret at all (rules/marketing.md §2).

Integration-layer rules (.claude/rules/integrations.md): HTTP in, dict out; typed exceptions; a
lazy module-level `httpx.AsyncClient` closed in the app lifespan (`close_x_client`); no caching,
no business decisions (budget, link policy, cashtags and retry policy live in `outlet_x.py`), no
Supabase.

THE OUTCOME SPLIT IS THE POINT OF THIS CLIENT. POST /2/tweets has no idempotency key, so the
caller must know whether a failed call could have created a post:
  * `XApiNotSentError` — the request provably never left this process (connect failure, connect
    or pool timeout, a protocol error h11 raises while validating the request before writing a
    byte, an unsupported scheme / invalid URL). Safe to send again.
  * `XApiAmbiguousError` — the request may have reached X (read/write timeout, a dropped or
    malformed answer, any other transport error, any 5xx, a 1xx/3xx, an unreadable or
    unexpected 2xx body). The post may exist: reconcile, never blindly resend.
    `XApiDuplicateContentError` is a subclass: X's duplicate-content 403 proves neither outcome
    (our own earlier, ambiguous attempt may be the "duplicate").
  * `XApiRefusedError` family — a definite 4xx: X answered and created nothing.
  * `XApiRateLimitError` — 429; nothing was created; `retry_at` says when to come back.
  * `XApiNotConfiguredError` — a credential is missing; raised BEFORE any request.

SECRETS. OAuth 1.0a puts the consumer key, the access token and the request signature in the
`Authorization` header; the consumer secret and the token secret only key the HMAC and never
leave this process. Nothing here logs a header or a request body, every transport exception is
re-raised `from None` (an httpx exception carries its request), and every message is built from
the method name, the HTTP status and X's own problem type / title / detail — with each configured
credential replaced and `app.log_redaction.redact_secrets` applied as a backstop, capped at 300
characters. No URL here carries a secret (the query is `start_time` / `max_results` /
`tweet.fields` only), so httpx's own INFO request line needs no filter.

X facts this module relies on — VERIFIED 2026-09-30 (the page text, not memory):
  * Base https://api.x.com; POST /2/tweets accepts "UserToken (HTTP OAuth)" (OAuth 1.0a user
    context) besides OAuth 2.0; body fields include `text` and `made_with_ai` ("Disclose that the
    tweet contains AI-generated media"); success is `201 Created` with
    `{"data": {"id", "text", "edit_history_post_ids"}, "errors"?: [...]}` — a `data.id` means the
    post exists even when `errors` rides along. No idempotency key is documented.
    https://docs.x.com/x-api/posts/create-post
  * DELETE /2/tweets/{id} → 200 `{"data": {"deleted": bool}}` ("Whether the Post was deleted").
    A 204 No Content and a 404 for a post already gone are accepted as observed shapes.
    https://docs.x.com/x-api/posts/delete-post
  * GET /2/users/{id}/tweets: `start_time` ISO-8601 date-time, `max_results` 5-100; answer
    `{"data": [...], "meta": {"result_count", "next_token", …}}` with no `data` when empty.
    https://docs.x.com/x-api/users/get-posts
  * Errors are problem objects `{"title", "detail", "type"}` (e.g. type
    `https://api.x.com/2/problems/invalid-request`); 403 = "Valid auth but no permission for this
    resource or action". https://docs.x.com/x-api/fundamentals/response-codes-and-errors
    Older answers use the legacy `{"errors": [{"message", "code"}]}` form; both are read.
  * Rate limits: `x-rate-limit-reset` is the "Unix timestamp when window resets"; exceeding a
    limit answers 429. https://docs.x.com/x-api/fundamentals/rate-limits
  * OAuth 1.0a HMAC-SHA1 signature: base string = METHOD & enc(base URL) & enc(sorted, encoded
    parameter string); signing key = enc(consumer secret) & enc(token secret); query parameters
    and form-encoded body parameters are signed — a JSON body is not a parameter and is not
    signed. The page's worked example (now on api.x.com) is pinned in the tests.
    https://docs.x.com/resources/fundamentals/authentication/oauth-1-0a/creating-a-signature
  * Recorded with the design doc §12.10 research (developer-community reports, not the reference
    pages): a 402 whose problem type ends `credits-depleted` when pay-per-use credits run out; a
    403 "You are not allowed to create a Tweet with duplicate content." vs the generic anti-spam
    403 "You are not permitted to perform this action."; a user's access token is
    "<numeric user id>-<rest>".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets as _secrets
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit

import httpx

from app.config import settings
from app.log_redaction import redact_secrets

logger = logging.getLogger(__name__)

API_BASE = "https://api.x.com"

#: GET /2/users/{id}/tweets page-size bounds (the reference page: 5-100).
MIN_LIST_RESULTS = 5
MAX_LIST_RESULTS = 100

#: Cap on any upstream-sourced text (X's title / detail / type, a transport message).
_TEXT_CAP = 300
#: A reset/Retry-After further out than this is junk, not a wait.
_MAX_RETRY_WAIT = timedelta(days=31)

_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_client: Optional[httpx.AsyncClient] = None

_ID_RE = re.compile(r"[0-9]{1,25}")
_ACCESS_TOKEN_USER_RE = re.compile(r"([0-9]{1,25})-")
_INT_RE = re.compile(r"[0-9]{1,12}")


# ── Exception hierarchy ────────────────────────────────────────────────
# Class names are load-bearing: `classify_exception` (app/api/error_response.py) maps any class
# starting "xapi" to MARKETING_PUBLISHER_UNAVAILABLE, 502 when the lowercase name contains
# refused / auth / notconfigured / forbidden / credits / duplicate, else 503. So the transient /
# unknown-outcome classes must never contain one of those words.


class XApiException(Exception):
    """Base for X API failures. Carries the method name, the HTTP status and X's problem type and
    detail (scrubbed) — never a header, a request body, a URL or a credential."""

    def __init__(
        self,
        message: str = "",
        *,
        method: str = "",
        status: Optional[int] = None,
        problem_type: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.status = status
        self.problem_type = problem_type
        self.detail = detail


class XApiNotConfiguredError(XApiException):
    """A MARKETING_X_* credential is missing — no request was made."""


class XApiNotSentError(XApiException):
    """The request provably never left this process. Sending again cannot duplicate anything."""


class XApiAmbiguousError(XApiException):
    """The request may have reached X and its outcome is unknown (a timeout after connecting, a
    dropped connection, a 5xx, an unreadable or unexpected 2xx). For a create: the post may
    exist — reconcile, never resend blindly."""


class XApiDuplicateContentError(XApiAmbiguousError):
    """403 "…duplicate content". NOT proof of either outcome: the duplicate may be our own
    earlier attempt whose answer was lost. Reconcile."""


class XApiRefusedError(XApiException):
    """A definite 4xx: X answered and did nothing. Also raised, with `status=None`, for an
    argument this client refuses to send (empty text, a non-numeric id)."""


class XApiAuthError(XApiRefusedError):
    """401 — the credentials were rejected."""


class XApiForbiddenError(XApiRefusedError):
    """A generic 403 ("You are not permitted to perform this action": anti-spam or an app
    restriction). X staff ask apps not to retry it."""


class XApiCreditsDepletedError(XApiRefusedError):
    """402, or a problem type containing "credits-depleted": the pay-per-use balance is empty."""


class XApiRateLimitError(XApiException):
    """429. Nothing was created. `retry_at` (UTC, aware) from `x-rate-limit-reset`, else
    `Retry-After`, else None."""

    def __init__(self, message: str = "", *, retry_at: Optional[datetime] = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.retry_at = retry_at


# ── client lifecycle ───────────────────────────────────────────────────


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )
    return _client


async def close_x_client() -> None:
    """Tear-down hook for the app.main lifespan. Idempotent."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ── configuration ──────────────────────────────────────────────────────


def _setting(name: str) -> str:
    value = getattr(settings, name, None)
    return value.strip() if isinstance(value, str) else ""


def configured() -> bool:
    """All four MARKETING_X_* OAuth 1.0a credentials are non-empty."""
    return all(_setting(n) for n in (
        "MARKETING_X_CONSUMER_KEY", "MARKETING_X_CONSUMER_SECRET",
        "MARKETING_X_ACCESS_TOKEN", "MARKETING_X_ACCESS_TOKEN_SECRET",
    ))


def user_id_from_access_token() -> Optional[str]:
    """The account's numeric user id: an OAuth 1.0a access token is "<user id>-<rest>"."""
    match = _ACCESS_TOKEN_USER_RE.match(_setting("MARKETING_X_ACCESS_TOKEN"))
    return match.group(1) if match else None


class _Credentials:
    """The four OAuth 1.0a credentials behind a CONSTANT repr: a frame-variable dump (Sentry's
    `include_local_variables`, a debugger) of any frame holding this object prints nothing secret.
    Plain `str` locals named `cs` / `ats` would have been serialised verbatim (review 2026-09-30)."""

    __slots__ = ("consumer_key", "consumer_secret", "token", "token_secret")

    def __init__(self, consumer_key: str, consumer_secret: str, token: str, token_secret: str) -> None:
        self.consumer_key, self.consumer_secret = consumer_key, consumer_secret
        self.token, self.token_secret = token, token_secret

    def __repr__(self) -> str:
        return "<x credentials redacted>"

    __str__ = __repr__


class _Redacted:
    """The strings scrubbed out of upstream text (the credentials, the Authorization header, the
    signature), behind a constant repr for the same reason."""

    __slots__ = ("values",)

    def __init__(self, values: Iterable[str]) -> None:
        self.values: Tuple[str, ...] = tuple(sorted({v for v in values if v}, key=len, reverse=True))

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__


def _credentials(method: str) -> "_Credentials":
    ck = _setting("MARKETING_X_CONSUMER_KEY")
    cs = _setting("MARKETING_X_CONSUMER_SECRET")
    at = _setting("MARKETING_X_ACCESS_TOKEN")
    ats = _setting("MARKETING_X_ACCESS_TOKEN_SECRET")
    missing = [name for name, v in (
        ("MARKETING_X_CONSUMER_KEY", ck), ("MARKETING_X_CONSUMER_SECRET", cs),
        ("MARKETING_X_ACCESS_TOKEN", at), ("MARKETING_X_ACCESS_TOKEN_SECRET", ats),
    ) if not v]
    if missing:
        # Names only — never a value.
        raise XApiNotConfiguredError(f"x {method}: not configured ({', '.join(missing)} unset)",
                                     method=method)
    return _Credentials(ck, cs, at, ats)


# ── OAuth 1.0a (HMAC-SHA1), hand-written over the stdlib ───────────────


def percent_encode(value: Any) -> str:
    """RFC 3986 percent-encoding as OAuth 1.0a requires: only A-Z a-z 0-9 - . _ ~ stay bare."""
    return quote(str(value), safe="~")


def _base_url(url: str) -> Tuple[str, List[Tuple[str, str]]]:
    """(scheme://host[:non-default port]/path, the URL's own query pairs) — RFC 5849 §3.4.1.2."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host if port is None or (scheme, port) in (("https", 443), ("http", 80)) else f"{host}:{port}"
    return (urlunsplit((scheme, netloc, parts.path or "/", "", "")),
            parse_qsl(parts.query, keep_blank_values=True))


def signature_base_string(method: str, url: str, params: Mapping[str, str]) -> str:
    """METHOD & enc(base URL) & enc(k=v pairs, each side encoded, sorted, joined by "&").

    `params` are the DECODED query (and form) parameters plus the oauth_* parameters (never
    `oauth_signature`). A query string already in `url` is folded in too."""
    base, url_pairs = _base_url(url)
    pairs = sorted(
        (percent_encode(k), percent_encode(v))
        for k, v in [*url_pairs, *((str(k), str(v)) for k, v in params.items())]
    )
    param_string = "&".join(f"{k}={v}" for k, v in pairs)
    return "&".join((method.upper(), percent_encode(base), percent_encode(param_string)))


def hmac_sha1_signature(base_string: str, consumer_secret: str, token_secret: str) -> str:
    """base64(HMAC-SHA1(key = enc(consumer secret) & enc(token secret), base string))."""
    key = f"{percent_encode(consumer_secret)}&{percent_encode(token_secret)}".encode("utf-8")
    digest = hmac.new(key, base_string.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def oauth1_authorization(
    method: str,
    url: str,
    query: Optional[Mapping[str, str]] = None,
    *,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
    nonce: Optional[str] = None,
    timestamp: Optional[int] = None,
) -> str:
    """The full `Authorization` header value for one request (OAuth 1.0a, HMAC-SHA1).

    Sign the query parameters you SEND (pass them decoded); a JSON body is not signed."""
    oauth: Dict[str, str] = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce if nonce is not None else _secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(timestamp if timestamp is not None else int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    signed = {**{str(k): str(v) for k, v in (query or {}).items()}, **oauth}
    oauth["oauth_signature"] = hmac_sha1_signature(
        signature_base_string(method, url, signed), consumer_secret, token_secret)
    return "OAuth " + ", ".join(
        f'{percent_encode(k)}="{percent_encode(v)}"' for k, v in sorted(oauth.items()))


# ── helpers ────────────────────────────────────────────────────────────


def _scrub(text: Any, hidden: Any) -> str:
    """Upstream-sourced text with every credential (and any other secret shape) removed. `hidden`
    is a `_Redacted` (or, in tests, a plain sequence of strings)."""
    s = str(text)
    for value in (hidden.values if isinstance(hidden, _Redacted) else hidden):
        # A real credential is 25+ characters; the floor keeps a degenerate test value from
        # blanking every matching letter of the message.
        if value and len(value) >= 6:
            s = s.replace(value, "<redacted>")
    return redact_secrets(s)[:_TEXT_CAP]


def _text_or_none(value: Any, hidden: Sequence[str]) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return _scrub(value.strip(), hidden) or None


def _error_dicts(body: Any) -> List[Dict[str, Any]]:
    errs = body.get("errors") if isinstance(body, dict) else None
    return [e for e in errs if isinstance(e, dict)] if isinstance(errs, list) else []


def _problem(body: Any, hidden: Sequence[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(type, title, detail) from an RFC 7807 problem or a legacy `{"errors": [...]}` body."""
    top = body if isinstance(body, dict) else {}
    first = next(iter(_error_dicts(body)), {})
    ptype = _text_or_none(top.get("type"), hidden) or _text_or_none(first.get("type"), hidden)
    title = _text_or_none(top.get("title"), hidden) or _text_or_none(first.get("title"), hidden)
    detail = (_text_or_none(top.get("detail"), hidden) or _text_or_none(first.get("detail"), hidden)
              or _text_or_none(first.get("message"), hidden))
    return ptype, title, detail


def _all_texts(body: Any, keys: Iterable[str]) -> str:
    """Every string under `keys`, top level and in each errors[] entry, lowercased (matching only)."""
    out: List[str] = []
    for d in [body if isinstance(body, dict) else {}, *_error_dicts(body)]:
        out.extend(d[k] for k in keys if isinstance(d.get(k), str))
    return " ".join(out).lower()


def _describe(method: str, status: Optional[int], ptype: Optional[str], title: Optional[str],
              detail: Optional[str]) -> str:
    parts = [f"x {method}: HTTP {status}"]
    if title:
        parts.append(title)
    if detail and detail != title:
        parts.append(f"- {detail}")
    if ptype:
        parts.append(f"[{ptype}]")
    return " ".join(parts)


def _int_header(headers: httpx.Headers, name: str) -> Optional[int]:
    raw = (headers.get(name) or "").strip()
    return int(raw) if _INT_RE.fullmatch(raw) else None


def _retry_at(headers: httpx.Headers) -> Optional[datetime]:
    """`x-rate-limit-reset` (epoch seconds), else `Retry-After` (seconds or an HTTP date)."""
    now = datetime.now(timezone.utc)
    reset = _int_header(headers, "x-rate-limit-reset")
    if reset is not None and 0 < reset <= (now + _MAX_RETRY_WAIT).timestamp():
        return datetime.fromtimestamp(reset, tz=timezone.utc)
    seconds = _int_header(headers, "retry-after")
    if seconds is not None and seconds <= _MAX_RETRY_WAIT.total_seconds():
        return now + timedelta(seconds=seconds)
    raw = (headers.get("retry-after") or "").strip()
    if raw and seconds is None:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)
        return when if when <= now + _MAX_RETRY_WAIT else None
    return None


def _json_or_none(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


#: Raised by httpx BEFORE a byte reaches the network: no connection (ConnectError /
#: ConnectTimeout), no free pool slot (PoolTimeout), a scheme httpx cannot speak
#: (UnsupportedProtocol), a URL it cannot build (InvalidURL), or a request h11 refuses to
#: serialise (LocalProtocolError — our bodies are in-memory with a computed Content-Length, so it
#: can only fire while validating the request line / headers, before writing). Listed explicitly
#: and caught FIRST: ConnectTimeout and PoolTimeout are TimeoutExceptions, and every one of them is
#: an httpx.HTTPError, which below means "may have been sent".
_NOT_SENT_ERRORS: Tuple[type, ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.UnsupportedProtocol,
    httpx.LocalProtocolError,
    httpx.InvalidURL,
)


async def _call(
    method: str,
    http_method: str,
    path: str,
    *,
    query: Optional[Mapping[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    accept: Tuple[int, ...] = (),
) -> Tuple[httpx.Response, Any, Tuple[str, ...]]:
    """Sign and send one request. Returns (response, parsed body or None, the strings to scrub)
    for a 2xx or a status in `accept`; raises the typed exception for everything else."""
    credentials = _credentials(method)
    url = f"{API_BASE}{path}"
    params = {str(k): str(v) for k, v in (query or {}).items()}
    authorization = oauth1_authorization(
        http_method, url, params, consumer_key=credentials.consumer_key,
        consumer_secret=credentials.consumer_secret, token=credentials.token,
        token_secret=credentials.token_secret)
    oauth_sig = authorization.split('oauth_signature="', 1)[1].split('"', 1)[0]
    hidden = _Redacted((credentials.consumer_key, credentials.consumer_secret, credentials.token,
                        credentials.token_secret, authorization, oauth_sig, unquote(oauth_sig)))
    del oauth_sig
    # The query is encoded exactly as it was signed (RFC 3986), so X's re-encoding matches ours.
    send_url = url + ("?" + "&".join(f"{percent_encode(k)}={percent_encode(v)}"
                                     for k, v in sorted(params.items())) if params else "")
    # Build, then send — two steps so ANY failure has a definite meaning, and every failure is
    # raised OUTSIDE its `except` block: `from None` hides an httpx exception from tracebacks but
    # keeps it as `__context__`, and that exception carries the request (the Authorization header).
    failure: Optional[Tuple[type, str]] = None
    request: Optional[httpx.Request] = None
    try:
        request = _get_client().build_request(http_method, send_url, json=json_body,
                                              headers={"Authorization": authorization})
    except Exception as e:  # nothing has left: a body that cannot be encoded, a closed client …
        failure = (XApiNotSentError, f"x {method}: not sent ({type(e).__name__}: {_scrub(e, hidden)})")
    resp: Optional[httpx.Response] = None
    if request is not None:
        try:
            resp = await _get_client().send(request)
        except _NOT_SENT_ERRORS as e:
            failure = (XApiNotSentError, f"x {method}: not sent ({type(e).__name__}: {_scrub(e, hidden)})")
        except Exception as e:  # httpx.HTTPError and anything else once sending began: may have landed
            failure = (XApiAmbiguousError,
                       f"x {method}: outcome unknown ({type(e).__name__}: {_scrub(e, hidden)})")
    del request
    if failure is not None or resp is None:
        cls, message = failure or (XApiAmbiguousError, f"x {method}: outcome unknown (no response)")
        raise cls(message, method=method)

    status = resp.status_code
    body = _json_or_none(resp)
    if 200 <= status < 300 or status in accept:
        return resp, body, hidden

    ptype, title, detail = _problem(body, hidden)
    message = _describe(method, status, ptype, title, detail)
    kw: Dict[str, Any] = {"method": method, "status": status, "problem_type": ptype, "detail": detail}
    if status == 429:
        retry_at = _retry_at(resp.headers)
        raise XApiRateLimitError(
            f"{message} (retry_at={retry_at.isoformat() if retry_at else None})", retry_at=retry_at, **kw)
    if not 400 <= status < 500 or status == 408:
        # 5xx, 1xx, 3xx — and 408, which a proxy can answer after X processed the request: X (or
        # something in front of it) answered without a definite refusal.
        raise XApiAmbiguousError(message, **kw)
    if status == 402 or "credits-depleted" in _all_texts(body, ("type",)):
        raise XApiCreditsDepletedError(message, **kw)
    if status == 401:
        raise XApiAuthError(message, **kw)
    if status == 403:
        texts = _all_texts(body, ("title", "detail", "message"))
        legacy_dup = any(str(e.get("code")) == "187" for e in _error_dicts(body))
        if "duplicate content" in texts or "is a duplicate" in texts or legacy_dup:
            raise XApiDuplicateContentError(message, **kw)
        raise XApiForbiddenError(message, **kw)
    raise XApiRefusedError(message, **kw)


def _require_id(method: str, value: Any, what: str) -> str:
    s = str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""
    if not _ID_RE.fullmatch(s):
        raise XApiRefusedError(f"x {method}: {what} must be a numeric id — not sent", method=method)
    return s


def _ambiguous_body(method: str, status: int, why: str) -> XApiAmbiguousError:
    return XApiAmbiguousError(f"x {method}: HTTP {status} {why}", method=method, status=status)


# ── API methods ────────────────────────────────────────────────────────


async def create_post(text: str, *, made_with_ai: bool = False) -> Dict[str, Any]:
    """POST /2/tweets with `{"text"}` (+ `"made_with_ai": true` only when asked).

    Returns `{"id": str, "text": str, "errors": list}` — `text` is X's stored copy ("" if X sent
    none), `errors` any problem objects that rode along with the created post."""
    method = "create_post"
    if not isinstance(text, str) or not text.strip():
        raise XApiRefusedError(f"x {method}: empty text — not sent", method=method)
    payload: Dict[str, Any] = {"text": text}
    if made_with_ai is True:
        payload["made_with_ai"] = True
    resp, body, hidden = await _call(method, "POST", "/2/tweets", json_body=payload)

    data = body.get("data") if isinstance(body, dict) else None
    raw_id = data.get("id") if isinstance(data, dict) else None
    post_id = str(raw_id) if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool) else ""
    if not _ID_RE.fullmatch(post_id):
        # A success status without a post id: the post may or may not exist.
        raise _ambiguous_body(method, resp.status_code, "without a readable data.id")
    errors = _error_dicts(body)
    if errors:
        ptype, title, detail = _problem({"errors": errors}, hidden)
        logger.warning(
            "x create_post: post %s created WITH %d error object(s) — first: %s",
            post_id, len(errors), _describe(method, resp.status_code, ptype, title, detail),
        )
    out_text = data.get("text") if isinstance(data.get("text"), str) else ""
    return {"id": post_id, "text": out_text, "errors": errors}


async def delete_post(post_id: str) -> Dict[str, Any]:
    """DELETE /2/tweets/{id}. Returns `{"deleted": True, "already_gone": bool}`; a 404 means the
    post is already gone. A 200 that does not say `deleted: true` is ambiguous."""
    method = "delete_post"
    pid = _require_id(method, post_id, "post_id")
    resp, body, _hidden = await _call(method, "DELETE", f"/2/tweets/{pid}", accept=(404,))
    status = resp.status_code
    if status == 404:
        return {"deleted": True, "already_gone": True}
    if status == 204:
        return {"deleted": True, "already_gone": False}
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict) and data.get("deleted") is True:
        return {"deleted": True, "already_gone": False}
    raise _ambiguous_body(method, status, "without data.deleted == true")


async def list_user_posts(user_id: str, *, start_time: datetime, max_results: int = 5) -> Dict[str, Any]:
    """GET /2/users/{id}/tweets since `start_time` (one page, `max_results` clamped to 5-100).

    Returns `{"posts": [{"id": str, "text": str, "created_at": str | None}], "result_count": int}`.
    A malformed item raises XApiAmbiguousError (a skipped item could hide the very post a
    reconcile is looking for)."""
    method = "list_user_posts"
    uid = _require_id(method, user_id, "user_id")
    if not isinstance(start_time, datetime):
        raise XApiRefusedError(f"x {method}: start_time must be a datetime — not sent", method=method)
    # A naive datetime is taken as UTC (the server clock is UTC; never the local zone).
    start = start_time.replace(tzinfo=timezone.utc) if start_time.tzinfo is None \
        else start_time.astimezone(timezone.utc)
    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = MIN_LIST_RESULTS
    query = {
        "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "max_results": str(max(MIN_LIST_RESULTS, min(MAX_LIST_RESULTS, n))),
        "tweet.fields": "created_at",
    }
    resp, body, _hidden = await _call(method, "GET", f"/2/users/{uid}/tweets", query=query)
    status = resp.status_code
    if not isinstance(body, dict):
        raise _ambiguous_body(method, status, "with an unreadable body")
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    count = meta.get("result_count")
    count = count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None
    data = body.get("data")
    if data is None:
        if count:
            raise _ambiguous_body(method, status, f"with result_count={count} and no data")
        return {"posts": [], "result_count": 0}
    if not isinstance(data, list):
        raise _ambiguous_body(method, status, "with a non-list data")
    posts: List[Dict[str, Any]] = []
    for item in data:
        raw_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(raw_id, str) or not _ID_RE.fullmatch(raw_id) or not isinstance(item.get("text"), str):
            raise _ambiguous_body(method, status, "with a malformed post item")
        created = item.get("created_at")
        posts.append({"id": raw_id, "text": item["text"],
                      "created_at": created if isinstance(created, str) else None})
    return {"posts": posts, "result_count": count if count is not None else len(posts)}
