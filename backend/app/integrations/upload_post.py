"""
Upload-Post — a thin client for the marketing PUBLISHER's Stage 2 outlets (design doc §12.10).

Two callers, both driven by the publisher loop in `app/services/marketing/publisher_service.py`, in
the WEB process: `app/services/marketing/outlet_upload_post.py` (TikTok, YouTube, Instagram,
Facebook, LinkedIn, Threads through one middleman API) and `app/services/marketing/metrics_service.py`
(the measure step's `get_post_analytics`). The media worker never holds this key — it holds no social
secret at all (rules/marketing.md §2).

Integration-layer rules (.claude/rules/integrations.md): HTTP in, dict out; typed exceptions; a
lazy module-level `httpx.AsyncClient` closed in the app lifespan (`close_upload_post_client`); no
caching, no business decisions (which fields a platform gets, resend windows, what a status means
for a post — all in the outlet), no Supabase.

THE OUTCOME SPLIT IS THE POINT OF THIS CLIENT. Every failure is exactly one of:
  * `UploadPostNotConfiguredError` — the API key or the profile (`user`) is unset; NO request.
  * `UploadPostNotSentError`  — the request provably never left this process (`ConnectError`,
                                `ConnectTimeout`, `PoolTimeout`, `UnsupportedProtocol`,
                                `LocalProtocolError`, `InvalidURL`, a request httpx could not build,
                                a closed client). Safe to send again.
  * `UploadPostAmbiguousError` — it MAY have reached Upload-Post: a read/write timeout, a dropped
                                connection, any other transport error, a 5xx / 3xx, a 408 (a proxy
                                can answer it after the origin processed the request), an
                                unreadable or unexpected 2xx. The job may exist: poll `get_status`
                                with the SAME request id (the Idempotency-Key makes a resend inside
                                24 h return the existing job).
  * `UploadPostRefusedError` family — a definite 4xx: Upload-Post answered and queued nothing.
    `UploadPostAuthError` (401), `UploadPostPlanError` (403 — the plan lacks the platform),
    `UploadPostQuotaError` (429 carrying `usage` — the MONTHLY quota), `UploadPostNotConnectedError`
    (400 `invalid_platforms` — the only requested platform is not connected to the profile),
    `UploadPostReauthError` (`account_reauth_required` / `account_checkpoint_required` /
    `tiktok_reconnect_required` or `reauth_required: true`, any status — the owner must reconnect).
    Also raised with `status=None` for an argument this client refuses to send (empty text, a bad
    request id, a reserved or unencodable field) — nothing was sent.
  * `UploadPostRateLimitError` — any other 429 (per-minute window, the daily per-platform cap with
    `violations`, the 5-minute IP block) and `account_restricted` (a parked account, any status).
    Nothing was queued; `retry_at` (UTC) says when to come back, when known.

SECRETS. The only secret is the API key, sent as `Authorization: Apikey <key>`. It lives behind a
constant-repr holder (`_ApiKey`), so a frame-variable dump never prints it; no header, request body
or URL is ever logged (no URL here carries a secret); every message is built from the method name,
the HTTP status and Upload-Post's own `error_code` / `message` / `error` — with the key replaced and
`app.log_redaction.redact_secrets` applied as a backstop (it also knows the `Apikey <key>` shape and
e-mail addresses), capped at 300 characters. Every transport failure is RAISED OUTSIDE its `except`
block: an httpx exception holds the request, the request holds the Authorization header, and
`from None` would only hide it from tracebacks while keeping it as `__context__`. The client never
follows a redirect, so the key cannot be carried to another host. The account e-mail that
`/uploadposts/me` and every history item carry never leaves this module.

Upload-Post facts this module relies on — VERIFIED 2026-10-01 against docs.upload-post.com (the page
text, `/llms-full.txt` and `/openapi.json`, not memory):
  * Base https://api.upload-post.com/api; `Authorization: Apikey <key>`; requests are
    multipart/form-data. https://docs.upload-post.com/api/reference
  * POST /upload (video: a public URL is accepted; "when `video` is a URL, we fetch the file in the
    background too, so the `request_id` comes back within seconds") and POST /upload_text (`title`
    IS the text): `user`, `platform[]`, `async_upload`, `request_id` ("Client-provided request
    identifier. If omitted, the server generates one. Returned in every response"), `external_id`
    ("max 255 chars … a label only — reusing one never blocks a publish"), per-platform fields.
    `Idempotency-Key` header: "if a matching upload job already exists, the API returns the existing
    job instead of creating a duplicate" (24 h window per the research note in §12.10).
    https://docs.upload-post.com/api/upload-video · https://docs.upload-post.com/api/upload-text
  * 200 async `{"success": true, "message", "request_id", "total_platforms"}`; 200 sync
    `{"success": true, "results": {<platform>: {success, url?, post_id?, error?, skipped?}},
    "usage": {count, limit, last_reset}}`; 202 scheduled/queued `{"success": true, "job_id", …}`.
    400 `{"success": false, "message", "invalid_platforms": {<platform>: "<why>"}}` when NONE of
    the requested platforms is connected; 401 `{"success": false, "message": "Invalid or expired
    token"}`; 403 plan restrictions; 404 user not found; 429 `{"success": false, "message": "This
    upload would exceed your monthly limit.", "usage": {…}}`; 500 `{"success": false, "error"}`.
  * Rate limits: every authenticated response carries `X-RateLimit-Limit` / `-Remaining` /
    `-Reset` ("Unix timestamp when the window resets"); exceeding it is 429. A per-platform daily
    cap answers 429 with a `violations` array (rolling 24 h — the per-minute `X-RateLimit-Reset`
    says nothing about it, so it is NOT used for those). 10 failed auth attempts block the IP for 5
    minutes (429). https://docs.upload-post.com/guides/rate-limits
  * Precheck refusals (troubleshooting guide): `error_code: "account_reauth_required"` /
    `"account_checkpoint_required"` (reconnect / clear Meta's prompt); `"account_restricted"` with
    `restricted_until` and `retry_after_seconds` (a parked account: "Don't re-queue a paused
    account before `restricted_until`").
  * GET /uploadposts/status?request_id= → `{request_id, external_id, status: pending | queued |
    processing | in_progress | completed | failed | not_found, completed, total, results: [{platform,
    success, message, upload_timestamp, status?, …}], last_update}`; `not_found` comes "with HTTP
    404". https://docs.upload-post.com/api/upload-status
  * GET /uploadposts/history?request_id= (exact match, "≤ 200 chars", no control characters, else
    400 "Invalid request_id") → `{history: [...], in_progress: [...], total, page, limit}`; an item
    carries `platform`, `success`, `platform_post_id` (string | array | null), `post_url`,
    `error_message`, `fallback_to_inbox`, `request_id`, `external_id` — and `user_email`.
    https://docs.upload-post.com/api/upload-history
  * POST /uploadposts/posts/unpublish JSON `{platform, user, post_id}` → 200 `{success, message}`;
    400 missing fields or unsupported platform (`error_code: "platform_not_supported"`), 403 not
    authorized, 404 no such post. The supported list differs between pages (see the outlet).
  * GET /uploadposts/me → `{success, message, email, plan, …}`; "the response includes
    `api_usage.count`" (rate-limits guide) — parsed tolerantly.
  * GET /uploadposts/post-analytics/{request_id}[?platform=] (`platform`: "Filter to a single
    platform … significantly faster") → `{"success": true, "post": {request_id, profile_username,
    post_title, post_caption, media_type, upload_timestamp}, "platforms": {<platform>: {success,
    platform_post_id, post_url, post_metrics?: {views, likes, comments, favorites, shares, reach,
    saves, …}, post_metrics_source, post_metrics_error?: "<why>", profile_snapshot_at_post_date,
    profile_snapshot_latest: {followers, impressions, …}, profile_snapshot_latest_date}}}`.
    TikTok's `post_metrics` also carries ratios and lists; "A missing field is not a zero … omitted,
    never filled with 0". Documented errors: 401, 404 "No post found with the given request ID",
    500. The live per-post reads are "limited to 100 requests per 5 minutes". No plan rule is
    documented for this route: a 403 reads as a plan refusal like every 403 here.
    https://docs.upload-post.com/api/get-analytics (VERIFIED 2026-10-01, the page's markdown)
"""

from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import httpx

from app.config import settings
from app.log_redaction import redact_secrets

logger = logging.getLogger(__name__)

API_BASE = "https://api.upload-post.com/api"

#: Longest request id / external id we send: Upload-Post's history filters accept ≤ 200 characters
#: (a longer id would make the post unfindable by the very lookup a reconcile relies on).
MAX_ID_LEN = 200
#: Cap on any upstream-sourced text (message, error, a transport error's text).
_TEXT_CAP = 300
#: Cap on an `error_code` (a short identifier in practice).
_CODE_CAP = 100
#: A reset / restriction further out than this is junk, not a wait (TikTok's longest park is 7 d).
_MAX_RETRY_AHEAD = timedelta(days=31)
#: Secrets shorter than this are not substring-replaced (they would shred the message); the
#: shape-based `redact_secrets` still runs on everything.
_MIN_SECRET_LEN = 6
#: An X-RateLimit-Reset at or above this is a Unix timestamp (2001-09-09); below, a delta in seconds.
_EPOCH_FLOOR = 1_000_000_000
#: …and at or above this, a Unix timestamp in MILLISECONDS.
_EPOCH_MS_FLOOR = 1_000_000_000_000

_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_client: Optional[httpx.AsyncClient] = None

#: error codes (any status) that only the account owner can cure by reconnecting the account.
_REAUTH_CODES = frozenset({
    "account_reauth_required", "account_checkpoint_required", "tiktok_reconnect_required",
})
_RESTRICTED_CODE = "account_restricted"

#: Header-safe (it is also the Idempotency-Key) and filterable in history.
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,199}")
#: A usable API key: visible ASCII only (no space, line break or control character).
_PRINTABLE_KEY_RE = re.compile(r"[\x21-\x7e]+")
_PLATFORM_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_FIELD_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{0,62}(?:\[\])?")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DIGITS_RE = re.compile(r"[0-9]{1,15}")
#: The calendar-date head of `profile_snapshot_latest_date` (ISO-8601 extended form only).
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
#: Longest snapshot-date text read ("2026-02-20T14:30:00.123456+05:30" is 32 characters).
_DATE_TEXT_CAP = 40
#: A snapshot dated more than this after today (UTC) is junk: no time zone is that far ahead.
_SNAPSHOT_DATE_AHEAD = timedelta(days=1)

#: Form fields this client owns: a caller's `fields` may not set them.
_OWN_FIELDS = frozenset({"user", "platform", "platform[]", "async_upload", "request_id", "external_id"})


# ── Exception hierarchy ────────────────────────────────────────────────
# Class NAMES are load-bearing: `classify_exception` (app/api/error_response.py) maps any class
# starting "uploadpost" to MARKETING_PUBLISHER_UNAVAILABLE, 502 when the lowercase name contains
# refused / auth / notconfigured / plan / quota / notconnected / reauth (… the X and Bluesky words),
# else 503. So the transient / unknown-outcome classes must never contain one of those words.


class UploadPostException(Exception):
    """Base for Upload-Post failures. Carries the method name, the HTTP status, Upload-Post's
    `error_code` and its message (scrubbed, capped) — never a header, a body, a URL or the key."""

    def __init__(
        self,
        message: str = "",
        *,
        method: str = "",
        status: Optional[int] = None,
        error_code: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.status = status
        self.error_code = error_code
        self.detail = detail


class UploadPostNotConfiguredError(UploadPostException):
    """MARKETING_UPLOAD_POST_API_KEY / MARKETING_UPLOAD_POST_USER unset — no request was made."""


class UploadPostNotSentError(UploadPostException):
    """The request provably never left this process. Sending again cannot duplicate anything."""


class UploadPostAmbiguousError(UploadPostException):
    """The request may have reached Upload-Post and its outcome is unknown (a timeout after
    connecting, a dropped connection, a 5xx / 3xx / 408, an unreadable or unexpected 2xx).
    Poll `get_status` with the same request id before any resend."""


class UploadPostRefusedError(UploadPostException):
    """A definite 4xx: Upload-Post answered and queued nothing. Also raised, with `status=None`,
    for an argument this client refuses to send."""


class UploadPostAuthError(UploadPostRefusedError):
    """401 — the API key was refused (invalid or expired)."""


class UploadPostPlanError(UploadPostRefusedError):
    """403 — the plan does not allow this (e.g. TikTok on the Free plan) or no permission."""


class UploadPostQuotaError(UploadPostRefusedError):
    """429 whose body carries `usage`: the MONTHLY upload quota is spent. `usage` is
    {"count": int | None, "limit": int | None} when readable."""

    def __init__(self, message: str = "", *, usage: Optional[Dict[str, Any]] = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.usage = usage


class UploadPostNotConnectedError(UploadPostRefusedError):
    """400 with `invalid_platforms`: the requested platform is not connected to the profile."""


class UploadPostReauthError(UploadPostRefusedError):
    """The social account needs the owner (reconnect it, or clear Meta's checkpoint prompt):
    `account_reauth_required`, `account_checkpoint_required`, `tiktok_reconnect_required` or
    `reauth_required: true`, on any status."""


class UploadPostRateLimitError(UploadPostException):
    """Nothing was queued; come back later. Any 429 without `usage`, and `account_restricted` on
    any status. `retry_at` (UTC, aware) from `retry_after_seconds`, else `restricted_until`, else
    `X-RateLimit-Reset` (a plain per-window 429 only), else `Retry-After`, else None."""

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
            follow_redirects=False,  # never carry the Apikey header to another host
        )
    return _client


async def close_upload_post_client() -> None:
    """Tear-down hook for the app.main lifespan. Idempotent."""
    global _client
    if _client is not None:
        client, _client = _client, None
        await client.aclose()


# ── configuration ──────────────────────────────────────────────────────


def _setting(name: str) -> str:
    value = getattr(settings, name, None)
    return value.strip() if isinstance(value, str) else ""


def user() -> str:
    """The Upload-Post PROFILE name the social accounts are connected to (stripped)."""
    return _setting("MARKETING_UPLOAD_POST_USER")


def configured() -> bool:
    """The API key and the profile name are both set (non-blank)."""
    return bool(_setting("MARKETING_UPLOAD_POST_API_KEY")) and bool(user())


class _ApiKey:
    """The API key behind a CONSTANT repr: a frame-variable dump (Sentry's
    `include_local_variables`, a debugger) of any frame holding it prints nothing secret."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    def __repr__(self) -> str:
        return "<upload-post api key redacted>"

    __str__ = __repr__


def _credentials(method: str) -> Tuple[_ApiKey, str]:
    key, profile = _setting("MARKETING_UPLOAD_POST_API_KEY"), user()
    missing = [name for name, v in (("MARKETING_UPLOAD_POST_API_KEY", key),
                                    ("MARKETING_UPLOAD_POST_USER", profile)) if not v]
    if missing:
        # Names only — never a value.
        raise UploadPostNotConfiguredError(
            f"upload-post {method}: not configured ({', '.join(missing)} unset)", method=method)
    if not _PRINTABLE_KEY_RE.fullmatch(key):
        # A key with a line break / control character inside (a paste across a wrap) would make h11
        # refuse the header and ECHO it escaped in the error — where the exact-key scrub cannot find
        # it (review 2026-10-01). Unusable → not configured; the setting NAME only.
        raise UploadPostNotConfiguredError(
            f"upload-post {method}: MARKETING_UPLOAD_POST_API_KEY contains whitespace or control "
            "characters — set it again as one line", method=method)
    return _ApiKey(key), profile


def _headers(key: _ApiKey, idempotency_key: Optional[str]) -> Dict[str, str]:
    headers = {"Authorization": f"Apikey {key.value}", "Accept": "application/json"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


# ── helpers ────────────────────────────────────────────────────────────


def _scrub(text: Any, key: Optional[_ApiKey], cap: int = _TEXT_CAP) -> str:
    """Upstream-sourced text with the API key and every other secret shape removed, capped."""
    s = str(text)
    if key is not None and key.value and len(key.value) >= _MIN_SECRET_LEN:
        s = s.replace(key.value, "<redacted>")
    return redact_secrets(s)[:cap]


def _text_or_none(value: Any, key: Optional[_ApiKey], cap: int = _TEXT_CAP) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return _scrub(value.strip(), key, cap) or None


def _refuse(method: str, why: str) -> UploadPostRefusedError:
    return UploadPostRefusedError(f"upload-post {method}: {why} — not sent", method=method)


def _ambiguous(method: str, status: int, why: str) -> UploadPostAmbiguousError:
    return UploadPostAmbiguousError(f"upload-post {method}: HTTP {status} {why}", method=method, status=status)


def _number(raw: Any) -> Optional[float]:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _count(raw: Any) -> Optional[int]:
    """A non-negative integer from an int or a digit string; anything else is None."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, str) and _DIGITS_RE.fullmatch(raw.strip()):
        return int(raw.strip())
    return None


def _snapshot_date(raw: Any, today: Optional[date] = None) -> Optional[str]:
    """`profile_snapshot_latest_date` as "YYYY-MM-DD", else None.

    Read: a calendar date "YYYY-MM-DD", or an ISO-8601 date-time ("T", "t" or a space between date and
    time; "Z" or an offset allowed) whose own calendar date is kept AS WRITTEN — never shifted to UTC,
    which would move an evening snapshot west of Greenwich to the next day. Not read (None): anything
    not a string, the basic or week forms ("20260220", "2026-W08-5"), an impossible date or time, text
    longer than 40 characters, and a date more than one day after `today` (UTC) — a snapshot cannot be
    from the future, and one that claimed to be would look fresh forever."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or len(s) > _DATE_TEXT_CAP or not _ISO_DATE_RE.match(s):
        return None
    if len(s) > 10 and s[10] not in "Tt ":
        return None
    try:
        day = date.fromisoformat(s[:10])
        if len(s) > 10:
            datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))  # validates the time
    except ValueError:
        return None
    if day > (today or datetime.now(timezone.utc).date()) + _SNAPSHOT_DATE_AHEAD:
        return None
    return day.isoformat()


def _bounded(at: datetime, now: datetime) -> Optional[datetime]:
    if at > now + _MAX_RETRY_AHEAD:
        return None
    return max(at, now)


def _from_epoch(value: float, now: datetime) -> Optional[datetime]:
    seconds = value / 1000.0 if value >= _EPOCH_MS_FLOOR else value
    try:
        at = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return _bounded(at, now)


def _instant(raw: Any, now: datetime) -> Optional[datetime]:
    """An absolute time from an ISO-8601 string or a Unix timestamp (seconds or ms)."""
    if raw is None or isinstance(raw, bool):
        return None
    number = _number(raw)
    if number is not None:
        return _from_epoch(number, now) if number >= _EPOCH_FLOOR else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        at = datetime.fromisoformat(raw.strip().replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    at = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)
    return _bounded(at, now)


def _retry_at(envelope: Mapping[str, Any], headers: httpx.Headers, *, window: bool,
              now: Optional[datetime] = None) -> Optional[datetime]:
    """When to come back: the body's `retry_after_seconds`, else its `restricted_until`, else (for a
    plain per-window 429 only — `window`) `X-RateLimit-Reset` (a Unix timestamp per the docs; a
    small value is read as seconds-from-now, a 13-digit one as milliseconds), else `Retry-After`
    (seconds or an HTTP date), else None. A time already past becomes `now`; one more than 31 days
    out is ignored."""
    now = now or datetime.now(timezone.utc)
    seconds = _number(envelope.get("retry_after_seconds"))
    if seconds is not None and 0 <= seconds <= _MAX_RETRY_AHEAD.total_seconds():
        return now + timedelta(seconds=seconds)
    at = _instant(envelope.get("restricted_until"), now)
    if at is not None:
        return at
    if window:
        reset = _number(headers.get("x-ratelimit-reset"))
        if reset is not None and reset >= 0:
            if reset >= _EPOCH_FLOOR:
                at = _from_epoch(reset, now)
            elif reset <= _MAX_RETRY_AHEAD.total_seconds():
                at = now + timedelta(seconds=reset)
            if at is not None:
                return at
    raw = (headers.get("retry-after") or "").strip()
    if raw:
        seconds = _number(raw)
        if seconds is not None:
            if 0 <= seconds <= _MAX_RETRY_AHEAD.total_seconds():
                return now + timedelta(seconds=seconds)
            return None
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)
        return _bounded(when, now)
    return None


def _usage(raw: Any) -> Optional[Dict[str, Any]]:
    """{"count": int, "limit": int | None} from a usage object, or None without a readable count."""
    if not isinstance(raw, dict):
        return None
    count = _count(raw.get("count"))
    if count is None:
        return None
    return {"count": count, "limit": _count(raw.get("limit"))}


def _json_or_none(resp: httpx.Response) -> Any:
    if not (resp.content or b"").strip():
        return None
    try:
        return resp.json()
    except (ValueError, RecursionError):  # JSONDecodeError / UnicodeDecodeError; absurd nesting
        return None


#: Raised by httpx BEFORE a byte reaches the network: no connection (ConnectError /
#: ConnectTimeout), no free pool slot (PoolTimeout), a scheme httpx cannot speak
#: (UnsupportedProtocol), a URL it cannot build (InvalidURL), or a request h11 refuses to
#: serialise (LocalProtocolError — while validating the request line / headers, before writing).
#: Caught FIRST: ConnectTimeout and PoolTimeout are TimeoutExceptions, and every one of them is an
#: httpx.HTTPError, which below means "may have been sent".
_NOT_SENT_ERRORS: Tuple[type, ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.UnsupportedProtocol,
    httpx.LocalProtocolError,
    httpx.InvalidURL,
)


def _raise_for(method: str, status: int, body: Any, headers: httpx.Headers, key: _ApiKey) -> None:
    """Raise the typed exception for a non-2xx answer."""
    envelope: Mapping[str, Any] = body if isinstance(body, dict) else {}
    code = _text_or_none(envelope.get("error_code"), key, _CODE_CAP)
    code_key = code.lower() if code else None
    detail = (_text_or_none(envelope.get("message"), key)
              or _text_or_none(envelope.get("error"), key))
    label = f"upload-post {method}: HTTP {status}"
    if code:
        label += f" [{code}]"
    if detail:
        label += f": {detail}"
    kw: Dict[str, Any] = {"method": method, "status": status, "error_code": code, "detail": detail}

    if code_key in _REAUTH_CODES or envelope.get("reauth_required") is True:
        raise UploadPostReauthError(label, **kw)
    if code_key == _RESTRICTED_CODE:
        reason = _text_or_none(envelope.get("restriction_reason"), key, _CODE_CAP)
        retry_at = _retry_at(envelope, headers, window=False)
        raise UploadPostRateLimitError(
            f"{label}{f' (reason={reason})' if reason else ''} "
            f"(retry_at={retry_at.isoformat() if retry_at else None})", retry_at=retry_at, **kw)
    if status == 429:
        if isinstance(envelope.get("usage"), dict):
            usage = _usage(envelope.get("usage"))
            shown = f" (usage {usage['count']}/{usage['limit']})" if usage else ""
            raise UploadPostQuotaError(f"{label}{shown}", usage=usage, **kw)
        violations = envelope.get("violations")
        violations = [v for v in violations if isinstance(v, dict)] if isinstance(violations, list) else []
        first = _text_or_none(violations[0].get("message"), key) if violations else None
        # A daily per-platform cap is a rolling 24 h window: the per-minute X-RateLimit-Reset that
        # rides on every answer would send us straight back into the same cap.
        retry_at = _retry_at(envelope, headers, window=not violations)
        raise UploadPostRateLimitError(
            f"{label}{f' — {first}' if first else ''} "
            f"(retry_at={retry_at.isoformat() if retry_at else None})", retry_at=retry_at, **kw)
    if status in (408, 409) or not 400 <= status < 500:
        # 5xx, 1xx, 3xx — 408, which a proxy can answer after the origin processed the request, and
        # 409, which an Idempotency-Key API answers when a job under the same key exists or is still
        # running (undocumented here — exactly when a resend reuses the key): not a refusal.
        raise UploadPostAmbiguousError(label, **kw)
    if status == 401:
        raise UploadPostAuthError(label, **kw)
    if status == 403:
        raise UploadPostPlanError(label, **kw)
    invalid = envelope.get("invalid_platforms")
    if status == 400 and isinstance(invalid, dict) and invalid:
        named = "; ".join(f"{_scrub(k, key, 40)}: {_scrub(v, key, 120)}" for k, v in list(invalid.items())[:6])
        raise UploadPostNotConnectedError(_scrub(f"{label} (invalid_platforms: {named})", key, 2 * _TEXT_CAP),
                                          **kw)
    raise UploadPostRefusedError(label, **kw)


async def _call(
    method: str,
    verb: str,
    path: str,
    *,
    form: Optional[List[Tuple[str, Tuple[None, bytes]]]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, str]] = None,
    idempotency_key: Optional[str] = None,
    accept: Tuple[int, ...] = (),
) -> Tuple[int, Any]:
    """Send one request. Returns (status, parsed body or None) for a 2xx or a status in `accept`;
    raises the typed exception family for everything else."""
    key, _profile = _credentials(method)
    url = f"{API_BASE}{path}"
    # Build, then send — two steps so ANY failure has a definite meaning, and every failure is
    # raised OUTSIDE its `except` block (no `__context__` holding the request and its header).
    failure: Optional[Tuple[type, str]] = None
    request: Optional[httpx.Request] = None
    client = _get_client()
    if client.is_closed:
        failure = (UploadPostNotSentError, f"upload-post {method}: not sent (the HTTP client is closed)")
    else:
        try:
            request = client.build_request(verb, url, files=form, json=json_body, params=params,
                                           headers=_headers(key, idempotency_key))
        except Exception as e:  # nothing has left: a body httpx cannot encode, a bad header …
            failure = (UploadPostNotSentError,
                       f"upload-post {method}: not sent ({type(e).__name__}: {_scrub(e, key)})")
    resp: Optional[httpx.Response] = None
    if request is not None:
        try:
            resp = await client.send(request)
        except _NOT_SENT_ERRORS as e:
            failure = (UploadPostNotSentError,
                       f"upload-post {method}: not sent ({type(e).__name__}: {_scrub(e, key)})")
        except Exception as e:  # httpx.HTTPError and anything else once sending began: may have landed
            failure = (UploadPostAmbiguousError,
                       f"upload-post {method}: outcome unknown ({type(e).__name__}: {_scrub(e, key)})")
    del request
    if failure is not None or resp is None:
        cls, message = failure or (UploadPostAmbiguousError, f"upload-post {method}: outcome unknown (no response)")
        raise cls(message, method=method)

    status = resp.status_code
    body = _json_or_none(resp)
    if 200 <= status < 300 or status in accept:
        return status, body
    _raise_for(method, status, body, resp.headers, key)
    raise _ambiguous(method, status, "unmapped")  # unreachable: _raise_for always raises


# ── request building ───────────────────────────────────────────────────


def _render(method: str, name: str, value: Any) -> List[str]:
    """A form value as the strings to send: bool → "true"/"false", int/float → str, str as-is,
    a list/tuple → one field per item, None → omitted. Anything else is refused."""
    if value is None:
        return []
    if isinstance(value, bool):
        return ["true" if value else "false"]
    if isinstance(value, int):
        return [str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _refuse(method, f"field {name!r} is not a finite number")
        return [repr(value)]
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            if isinstance(item, (list, tuple, dict)):
                raise _refuse(method, f"field {name!r} nests a {type(item).__name__}")
            out.extend(_render(method, name, item))
        return out
    raise _refuse(method, f"field {name!r} has an unsupported type ({type(value).__name__})")


def _form(method: str, own: List[Tuple[str, Any]], fields: Mapping[str, Any],
          reserved: Iterable[str]) -> List[Tuple[str, Tuple[None, bytes]]]:
    """The multipart parts: our own fields first, then the caller's per-platform fields. Forced
    multipart (no filename), every value UTF-8 encoded here so an unencodable value is refused
    before anything is built."""
    if not isinstance(fields, Mapping):
        raise _refuse(method, "fields must be a mapping")
    blocked = set(reserved)
    pairs: List[Tuple[str, Any]] = list(own)
    for name, value in fields.items():
        if not isinstance(name, str) or not _FIELD_NAME_RE.fullmatch(name):
            raise _refuse(method, f"field name {str(name)[:40]!r} is not a plain form field name")
        if name in blocked:
            raise _refuse(method, f"field {name!r} is set by this client")
        pairs.append((name, value))
    parts: List[Tuple[str, Tuple[None, bytes]]] = []
    for name, value in pairs:
        for text in _render(method, name, value):
            encoded: Optional[bytes] = None
            try:
                encoded = text.encode("utf-8")
            except UnicodeEncodeError:  # a lone surrogate — raised below, outside this block
                pass
            if encoded is None:
                raise _refuse(method, f"field {name!r} is not valid UTF-8 text")
            parts.append((name, (None, encoded)))
    return parts


def _check_platform(method: str, platform: Any) -> str:
    p = platform if isinstance(platform, str) else ""
    if not _PLATFORM_RE.fullmatch(p):
        raise _refuse(method, f"platform {str(platform)[:40]!r} is not a platform name")
    return p


def _check_request_id(method: str, request_id: Any) -> str:
    # Not stripped: it is also the Idempotency-Key, so the id we send must be the caller's, byte
    # for byte — a silently trimmed copy would be a different key on the next resend.
    rid = request_id if isinstance(request_id, str) else ""
    if not _REQUEST_ID_RE.fullmatch(rid):
        raise _refuse(method, "request_id must be 1-200 characters of [A-Za-z0-9._:-]")
    return rid


def _check_external_id(method: str, external_id: Any) -> Optional[str]:
    """None (omitted) for a blank label; refused when too long or holding a control character."""
    if external_id is None:
        return None
    if not isinstance(external_id, str):
        raise _refuse(method, "external_id must be a string")
    eid = external_id.strip()
    if not eid:
        return None
    if len(eid) > MAX_ID_LEN or _CONTROL_RE.search(eid):
        raise _refuse(method, f"external_id must be ≤ {MAX_ID_LEN} characters without control characters")
    return eid


def _upload_result(method: str, platform: str, request_id: str, status: int, body: Any) -> Dict[str, Any]:
    """Normalise a 2xx upload answer (sync results, async ack or scheduled job)."""
    if not isinstance(body, dict):
        raise _ambiguous(method, status, "with an unreadable body")
    results = body.get("results")
    answered_id = body.get("request_id") if isinstance(body.get("request_id"), str) and body["request_id"] else None
    if answered_id is not None and not _REQUEST_ID_RE.fullmatch(answered_id):
        # Upstream text we would store and later poll by (sync answers too — review 2026-10-01):
        # anything that is not an id is dropped, and we keep polling by our own.
        logger.warning("upload-post %s: ignored an answered request_id that is not an id (platform=%s)",
                       method, platform)
        answered_id = None
    if results is not None:
        # Sync answer (Upload-Post may finish fast, even when asked for async). Read whatever the
        # top-level `success` says: the verdict that counts is per platform.
        if not isinstance(results, dict) or not isinstance(results.get(platform), dict):
            raise _ambiguous(method, status, f"with results but no entry for {platform}")
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else None
        return {
            "mode": "sync",
            "request_id": answered_id,
            "results": {k: dict(v) for k, v in results.items() if isinstance(k, str) and isinstance(v, dict)},
            "usage": dict(usage) if usage is not None else None,
        }
    if body.get("success") is not True:
        raise _ambiguous(method, status, "without success: true")
    job_id = body.get("job_id")
    job_id = str(job_id) if isinstance(job_id, (str, int)) and not isinstance(job_id, bool) and str(job_id) else None
    if status == 202:
        if job_id is None:
            raise _ambiguous(method, status, "without a job_id")
        return {"mode": "scheduled", "job_id": job_id}
    if answered_id is not None:
        if not _REQUEST_ID_RE.fullmatch(answered_id):
            # Upstream text we would log, store and later poll by: refuse anything that is not an id.
            raise _ambiguous(method, status, "with a request_id that is not an id")
        if answered_id != request_id:
            logger.warning("upload-post %s: answered request_id=%s for ours=%s (platform=%s) — using theirs",
                           method, answered_id, request_id, platform)
        return {"mode": "async", "request_id": answered_id}
    if job_id is not None:
        return {"mode": "scheduled", "job_id": job_id}
    raise _ambiguous(method, status, "without a request_id, results or job_id")


async def _upload(method: str, path: str, *, platform: str, media: List[Tuple[str, str]],
                  fields: Mapping[str, Any], request_id: str, external_id: str) -> Dict[str, Any]:
    """`media` = the route's own content parts, in order (`video`; `title`; `title` + `photos[]`…) —
    a caller's `fields` may set none of their names."""
    _credentials(method)  # not configured → raised before any argument work, nothing sent
    p = _check_platform(method, platform)
    rid = _check_request_id(method, request_id)
    eid = _check_external_id(method, external_id)
    own: List[Tuple[str, Any]] = [
        ("user", user()),
        ("platform[]", p),
        *media,
        ("async_upload", True),
        ("request_id", rid),
        ("external_id", eid),
    ]
    form = _form(method, own, fields, _OWN_FIELDS | {name for name, _ in media})
    status, body = await _call(method, "POST", path, form=form, idempotency_key=rid)
    return _upload_result(method, p, rid, status, body)


# ── API methods ────────────────────────────────────────────────────────


async def upload_video(*, platform: str, video_url: str, fields: Mapping[str, Any], request_id: str,
                       external_id: str) -> Dict[str, Any]:
    """POST /upload — one platform, the video by its PUBLIC https URL (Upload-Post fetches it),
    `async_upload=true`, `request_id` + `Idempotency-Key: <request_id>`, `external_id` (omitted when
    blank), plus the caller's per-platform `fields` (title, description, tiktok_title, is_aigc …).

    Returns {"mode": "async", "request_id"} | {"mode": "sync", "request_id", "results", "usage"} |
    {"mode": "scheduled", "job_id"}."""
    method = "upload_video"
    url = video_url.strip() if isinstance(video_url, str) else ""
    if not _public_https(url):
        _credentials(method)
        raise _refuse(method, "video_url must be a public https:// URL")
    return await _upload(method, "/upload", platform=platform, media=[("video", url)], fields=fields,
                         request_id=request_id, external_id=external_id)


def _public_https(url: str) -> bool:
    return (url.lower().startswith("https://") and len(url) > len("https://")
            and not _CONTROL_RE.search(url) and " " not in url)


async def upload_text(*, platform: str, text: str, fields: Mapping[str, Any], request_id: str,
                      external_id: str) -> Dict[str, Any]:
    """POST /upload_text — `title` IS the text; otherwise as `upload_video` (facebook_page_id,
    target_linkedin_page_id, threads_long_text_as_post … come in `fields`)."""
    method = "upload_text"
    if not isinstance(text, str) or not text.strip():
        _credentials(method)
        raise _refuse(method, "empty text")
    return await _upload(method, "/upload_text", platform=platform, media=[("title", text)], fields=fields,
                         request_id=request_id, external_id=external_id)


#: Photos one /upload_photos request carries here (an image post is ONE picture; X takes 4 per post,
#: Bluesky 4 — more becomes a thread or a split post, which the publisher never wants).
MAX_PHOTOS = 4


async def upload_photos(*, platform: str, photo_urls: Any, caption: str, fields: Mapping[str, Any],
                        request_id: str, external_id: str) -> Dict[str, Any]:
    """POST /upload_photos — the caption as `title` ("the default caption"; a `<platform>_title` in
    `fields` would override it), then each photo by its PUBLIC https URL as one `photos[]` field
    (Upload-Post fetches it, as it does a video); otherwise as `upload_video` (facebook_page_id,
    target_linkedin_page_id, the `<platform>_alt_text` fields … come in `fields`). Same answers, same
    Idempotency-Key, same outcome split.

    Upload-Post facts (the upload-photo page and `/openapi.json`, VERIFIED 2026-10-09 with the plan's
    research): required `user`, `platform[]`, `photos[]` ("public HTTPS URLs of the images (send each
    URL as a separate `photos[]` field)" — the OpenAPI spec types it as binary only); `title` is the
    default caption; Facebook, LinkedIn and Threads are photo platforms, YouTube is not.

    A blank caption, no photo, more than 4, or a photo URL that is not a public https:// URL raises
    UploadPostRefusedError (status None) — nothing was sent."""
    method = "upload_photos"
    if not isinstance(caption, str) or not caption.strip():
        _credentials(method)
        raise _refuse(method, "empty caption")
    urls = [u.strip() if isinstance(u, str) else "" for u in photo_urls] \
        if isinstance(photo_urls, (list, tuple)) else []
    if not 1 <= len(urls) <= MAX_PHOTOS or not all(_public_https(u) for u in urls):
        _credentials(method)
        raise _refuse(method, f"photo_urls must be 1-{MAX_PHOTOS} public https:// URLs")
    return await _upload(method, "/upload_photos", platform=platform,
                         media=[("title", caption), *(("photos[]", u) for u in urls)], fields=fields,
                         request_id=request_id, external_id=external_id)


async def get_status(request_id: str) -> Dict[str, Any]:
    """GET /uploadposts/status?request_id= → {"request_id", "external_id", "status" (lowercase),
    "completed", "total", "results": [dict, …], "last_update", "message"}.

    Upload-Post's documented 404 `{"status": "not_found"}` returns {"status": "not_found",
    "request_id": request_id} — NOT an exception. Any other 404 raises (UploadPostRefusedError):
    "absent" licenses a resend, so anything less certain must not read as absent."""
    method = "get_status"
    key, _profile = _credentials(method)
    rid = _check_request_id(method, request_id)
    status, body = await _call(method, "GET", "/uploadposts/status", params={"request_id": rid}, accept=(404,))
    envelope = body if isinstance(body, dict) else None
    top = envelope.get("status") if envelope is not None else None
    top = top.strip().lower() if isinstance(top, str) else ""
    if status == 404:
        if top == "not_found":
            return {"status": "not_found", "request_id": rid}
        detail = (_text_or_none(envelope.get("message") if envelope else None, key)
                  or _text_or_none(envelope.get("error") if envelope else None, key))
        raise UploadPostRefusedError(
            f"upload-post {method}: HTTP 404 without status not_found{f': {detail}' if detail else ''}",
            method=method, status=404, detail=detail)
    if envelope is None:
        raise _ambiguous(method, status, "with an unreadable body")
    if not top:
        raise _ambiguous(method, status, "without a status")
    results = envelope.get("results")
    if results is None:
        results = []
    if not isinstance(results, list) or not all(isinstance(r, dict) for r in results):
        raise _ambiguous(method, status, "with malformed results")
    answered = envelope.get("request_id")
    external = envelope.get("external_id")
    last = envelope.get("last_update")
    return {
        "request_id": answered if isinstance(answered, str) and answered else rid,
        "external_id": external if isinstance(external, str) and external else None,
        "status": top[:40],
        "completed": _count(envelope.get("completed")),
        "total": _count(envelope.get("total")),
        "results": [{k: v for k, v in r.items() if k != "user_email"} for r in results],
        "last_update": last if isinstance(last, str) and last else None,
        "message": _text_or_none(envelope.get("message"), key),
    }


def _items(method: str, status: int, raw: Any, what: str, request_id: str) -> List[Dict[str, Any]]:
    """A history list: missing → []; not a list, or a non-dict item → ambiguous (a skipped item
    could hide the very post a reconcile is looking for). `user_email` is dropped, and so is an item
    that NAMES another request: the OpenAPI spec lists only page/limit for this endpoint, so if the
    `request_id` filter were ever ignored, another post's row must not pass for ours."""
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise _ambiguous(method, status, f"with a malformed {what}")
    kept: List[Dict[str, Any]] = []
    for item in raw:
        other = item.get("request_id")
        if other != request_id:
            # Another request's row — or a row that does not say whose it is. Either could be another
            # post on the same platform whose id a later retract would unpublish: never ours.
            logger.warning("upload-post %s: dropped a %s row that does not name request_id=%s — the "
                           "request_id filter looks ignored", method, what, request_id)
            continue
        kept.append({k: v for k, v in item.items() if k != "user_email"})
    return kept


async def get_history(request_id: str) -> Dict[str, Any]:
    """GET /uploadposts/history?request_id= (exact match) → {"history": [...], "in_progress": [...]}
    — both lists always present (missing → []), each item a dict without `user_email`; an item that
    does not name THIS request (another id, or none) is dropped."""
    method = "get_history"
    _credentials(method)
    rid = _check_request_id(method, request_id)
    status, body = await _call(method, "GET", "/uploadposts/history", params={"request_id": rid})
    if not isinstance(body, dict):
        raise _ambiguous(method, status, "with an unreadable body")
    return {
        "history": _items(method, status, body.get("history"), "history", rid),
        "in_progress": _items(method, status, body.get("in_progress"), "in_progress", rid),
    }


async def unpublish(*, platform: str, post_id: str) -> Dict[str, Any]:
    """POST /uploadposts/posts/unpublish (JSON {"platform", "user", "post_id"}) → {"unpublished":
    True, "message": str | None}. A 4xx (unsupported platform, no such post, not authorized) raises
    the Refused family; a 2xx that does not say success raises UploadPostAmbiguousError."""
    method = "unpublish"
    key, _profile = _credentials(method)
    p = _check_platform(method, platform)
    pid = post_id.strip() if isinstance(post_id, str) else ""
    if not pid or len(pid) > 500 or _CONTROL_RE.search(pid):
        raise _refuse(method, "post_id must be a non-empty platform post id")
    status, body = await _call(method, "POST", "/uploadposts/posts/unpublish",
                               json_body={"platform": p, "user": user(), "post_id": pid})
    if not isinstance(body, dict):
        raise _ambiguous(method, status, "with an unreadable body")
    if body.get("success") is not True:
        # Only an explicit success counts: `{}` or "Post not found" in a 200 must never read as a
        # takedown that did not happen.
        raise _ambiguous(method, status, "without success: true")
    return {"unpublished": True, "message": _text_or_none(body.get("message"), key)}


async def get_usage() -> Optional[Dict[str, Any]]:
    """GET /uploadposts/me → {"count": int, "limit": int | None} from `api_usage` (else `usage`), or
    None when the answer carries no readable count. Request failures raise as usual; a missing or
    odd field never does. The account e-mail in that answer is never returned."""
    method = "get_usage"
    _credentials(method)
    status, body = await _call(method, "GET", "/uploadposts/me")
    if not isinstance(body, dict):
        raise _ambiguous(method, status, "with an unreadable body")
    for name in ("api_usage", "usage"):
        usage = _usage(body.get(name))
        if usage is not None:
            return usage
    return None


async def get_post_analytics(request_id: str, *, platform: Optional[str] = None) -> Dict[str, Any]:
    """GET /uploadposts/post-analytics/{request_id}[?platform=] — the per-post metrics Upload-Post
    reads LIVE from each platform for one upload (Upload-Post allows 100 such reads per 5 minutes; a
    429 raises UploadPostRateLimitError). `platform` narrows the read to one platform (faster).

    Returns {"platforms": {<platform>: {"post_metrics": dict | None, "post_metrics_error": str | None,
    "followers": int | None, "followers_date": str | None}}}: `post_metrics` is the platform's object
    exactly as sent (a count it did not report is omitted, never 0; TikTok adds ratios and lists —
    normalising is the caller's job), None when absent; `post_metrics_error` is Upload-Post's reason
    when it could not read them (scrubbed, capped); `followers` is the latest profile snapshot's
    follower count when it is a non-negative integer, else None; `followers_date` is that snapshot's
    own date (`profile_snapshot_latest_date` — a STORED snapshot, possibly days older than the read)
    as "YYYY-MM-DD", or None when absent or unreadable (`_snapshot_date`). Platform keys are
    lower-cased; a key that is not a platform name is skipped. The post's caption, title, URL and
    profile are never returned.

    HTTP 403 → UploadPostPlanError; 402 and 404 ("No post found with the given request ID") →
    UploadPostRefusedError with that status (an `error_code` the shared mapping knows — reconnect,
    account_restricted — wins, as on every route). A 2xx that is not an object, says
    `success: false`, carries a malformed `platforms`, or answers for ANOTHER request id raises
    UploadPostAmbiguousError. A bad request id or platform raises UploadPostRefusedError (status
    None) before anything is sent."""
    method = "get_post_analytics"
    key, _profile = _credentials(method)
    rid = _check_request_id(method, request_id)
    if rid.lower() == "cached":
        # `/post-analytics/cached` is a sibling route (the cached replay), not an upload.
        raise _refuse(method, "request_id 'cached' names a route, not an upload")
    params = None if platform is None else {"platform": _check_platform(method, platform)}
    status, body = await _call(method, "GET", f"/uploadposts/post-analytics/{rid}", params=params)
    if not isinstance(body, dict):
        raise _ambiguous(method, status, "with an unreadable body")
    if body.get("success") is False:
        why = _text_or_none(body.get("message"), key) or _text_or_none(body.get("error"), key)
        raise _ambiguous(method, status, f"with success: false{f' ({why})' if why else ''}")
    post = body.get("post")
    answered = post.get("request_id") if isinstance(post, dict) else None
    if isinstance(answered, str) and answered and answered != rid:
        # Another upload's numbers must never pass for this one's.
        raise _ambiguous(method, status, f"for another request_id ({_scrub(answered, key, 80)!r}, asked {rid})")
    raw = body.get("platforms")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise _ambiguous(method, status, "with a malformed platforms")
    platforms: Dict[str, Dict[str, Any]] = {}
    for name, entry in raw.items():
        p = name.strip().lower() if isinstance(name, str) else ""
        if not _PLATFORM_RE.fullmatch(p) or p in platforms:
            logger.warning("upload-post %s: skipped platforms key %s (not a platform name, or a repeat) "
                           "request_id=%s", method, _scrub(repr(name), key, 60), rid)
            continue
        if not isinstance(entry, dict):
            raise _ambiguous(method, status, f"with a malformed platforms entry for {p}")
        metrics = entry.get("post_metrics")
        snapshot = entry.get("profile_snapshot_latest")
        platforms[p] = {
            "post_metrics": dict(metrics) if isinstance(metrics, dict) else None,
            "post_metrics_error": _text_or_none(entry.get("post_metrics_error"), key),
            "followers": _count(snapshot.get("followers")) if isinstance(snapshot, dict) else None,
            "followers_date": _snapshot_date(entry.get("profile_snapshot_latest_date")),
        }
    return {"platforms": platforms}
