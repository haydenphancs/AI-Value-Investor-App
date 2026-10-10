"""
Bluesky (atproto XRPC) — a thin client for the marketing PUBLISHER (design doc §12.10).

Two callers, both driven by the publisher loop in `app/services/marketing/publisher_service.py`, in
the WEB process: `app/services/marketing/outlet_bluesky.py` (publish — an image post's blob first —,
reconcile, retract — the account session) and `app/services/marketing/metrics_service.py` (the measure step: `get_posts` /
`get_profile` on the PUBLIC AppView, which take no credential at all). The media worker never holds
these credentials — it holds no social secret at all (rules/marketing.md §2).

Integration-layer rules (.claude/rules/integrations.md): HTTP in, dict out; typed exceptions; a
lazy module-level `httpx.AsyncClient` closed in the app lifespan (`close_bluesky_client`); no
caching (the session lives in the adapter), no business decisions, no Supabase.

THE OUTCOME SPLIT IS THE POINT OF THIS CLIENT. A publish is safe to retry only when the request
provably never left; calling a post "failed" that the platform in fact accepted invites a double
post. So every failure is exactly one of:
  * `BlueskyNotConfiguredError` — a setting/argument is missing or unusable; NO request was made.
  * `BlueskyNotSentError`       — the request provably never reached the platform
                                  (`ConnectError`, `ConnectTimeout`, `PoolTimeout`,
                                  `UnsupportedProtocol`, `LocalProtocolError`, `InvalidURL`).
  * `BlueskyAmbiguousError`     — it MAY have reached the platform: a read/write timeout, a dropped
                                  connection, any other httpx error, a 5xx/3xx, a 408 (a proxy may
                                  answer it after the PDS processed the request), an unreadable or
                                  unexpected 2xx body. The adapter reconciles (`get_record`).
  * `BlueskyRefusedError`       — a definite 4xx; the same request will not succeed on a retry.
    `BlueskyAuthError`, `BlueskyExpiredTokenError`, `BlueskyInvalidSwapError` are refusals too.
  * `BlueskyRateLimitError`     — 429; the request was not processed. `retry_at` when known.

Secrets: the app password and both JWTs never appear in an exception message, an attribute or a log
line. Messages are built from the XRPC method (NSID), the HTTP status and the platform's own
`error` / `message` only — each scrubbed of every secret this call carries, then of every secret
shape (`app.log_redaction.redact_secrets`), and capped. Transport exceptions are never chained
(`from None`): an httpx exception holds the request, and the request holds the Authorization
header. The client never follows a redirect (httpx's default), so a bearer token cannot be carried
to another host, and every host must be `https://`.

atproto facts this module relies on — VERIFIED 2026-09-30 against the lexicons and the reference
PDS source (https://github.com/bluesky-social/atproto — `lexicons/com/atproto/`,
`packages/pds/src/api/com/atproto/`), the XRPC spec (https://atproto.com/specs/xrpc), the DID spec
(https://atproto.com/specs/did) and the rate-limit guide
(https://docs.bsky.app/docs/advanced-guides/rate-limits):
  * XRPC over HTTPS: a PROCEDURE is `POST {host}/xrpc/<nsid>` with a JSON body; a QUERY is
    `GET {host}/xrpc/<nsid>?<params>`. An error answer is JSON `{"error": "<Name>", "message": "…"}`.
  * com.atproto.server.createSession {identifier, password} — the password is an APP PASSWORD
    (`xxxx-xxxx-xxxx-xxxx`, never the account password) — on the entryway (https://bsky.social)
    → {accessJwt, refreshJwt, did, handle, didDoc?, …}. The account's own PDS is the didDoc
    `service` entry whose `id` ends with `#atproto_pds` (its `serviceEndpoint`); without one we
    fall back to the service URL (the entryway proxies repo calls). createSession is limited to
    30 per 5 minutes and 300 per day per account — the adapter must reuse its session.
  * com.atproto.server.refreshSession: `Authorization: Bearer <refreshJwt>`, no body → the same
    shape with a ROTATED refreshJwt (the old one is spent).
  * An expired access token answers HTTP 400 (sometimes 401) `{"error": "ExpiredToken"}` — it is
    checked BEFORE the generic 401 → auth mapping, because the cure is a refresh, not a re-login.
    A bad password is 401 `AuthenticationRequired`; `AccountTakedown` and
    `AuthFactorTokenRequired` are 401 from createSession too.
  * 429 `RateLimitExceeded` carries `ratelimit-reset` (epoch SECONDS; header names may be
    lowercase — httpx headers are case-insensitive).
  * com.atproto.repo.putRecord {repo, collection, rkey, record, swapRecord} with `"swapRecord":
    null` EXPLICITLY in the JSON means "only if no record exists at this key": an identical retry
    answers 200 with the same uri/cid (a no-op, possibly WITHOUT a `commit` field), and a
    different record at that key answers 400 `{"error": "InvalidSwap"}`. That is what makes a
    Bluesky create idempotent. We never send `validate: false`.
  * com.atproto.repo.getRecord?repo=&collection=&rkey= needs NO auth; an absent record is HTTP 400
    `{"error": "RecordNotFound"}`. The output's `cid` is optional in the lexicon.
  * com.atproto.repo.deleteRecord {repo, collection, rkey} is idempotent: 200 with `{}` when the
    record is already absent, else `{"commit": {…}}` — `commit` is never required.

The metrics reads — VERIFIED 2026-10-01 against the lexicons (`lexicons/app/bsky/feed/getPosts.json`,
`feed/defs.json#postView`, `actor/getProfile.json`, `actor/defs.json#profileViewDetailed`) and the
"API Hosts and Auth" guide (https://docs.bsky.app/docs/advanced-guides/api-directory):
  * Public endpoints "can be made directly against the Bluesky AppView, preferably via the
    https://public.api.bsky.app hostname, which includes additional caching" — no auth, so none is
    ever sent there (counts may lag the app by a few minutes).
  * app.bsky.feed.getPosts?uris=…&uris=… (a query ARRAY repeats the parameter name; `uris` has
    maxLength 25, each an at-uri) → {"posts": [postView]}. A postView requires uri, cid, author,
    record, indexedAt and MAY carry the integer counts likeCount, repostCount, replyCount,
    quoteCount, bookmarkCount. A post that no longer exists is simply absent from `posts`.
  * app.bsky.actor.getProfile?actor=<handle or DID> → profileViewDetailed: did and handle are
    required; followersCount, followsCount and postsCount are optional integers.

The image post (drop 1, 2026-10-09) — against the lexicons (`lexicons/com/atproto/repo/uploadBlob.json`,
`lexicons/app/bsky/embed/images.json`, `embed/defs.json#aspectRatio`) and the data-model spec
(https://atproto.com/specs/data-model — "blob" type and CIDs); recorded with the plan's research:
  * com.atproto.repo.uploadBlob is a PROCEDURE whose input is the raw bytes ("*/*", sent with the
    blob's own Content-Type) and whose output is `{"blob": <blob>}`; a blob in JSON is
    `{"$type": "blob", "ref": {"$link": <CID>}, "mimeType", "size"}`. An uploaded blob that no record
    references is garbage-collected by the PDS, so it is uploaded right before the record is written
    (and again on every resend).
  * A blob's CID is CIDv1, codec raw (0x55), sha2-256 multihash, base32 multibase ("bafkrei…"): it
    depends on the bytes only, so a record can name its blob BEFORE the upload
    (`raw_cid_for_sha256`) — which is what keeps the Bluesky record built once, in the claim.
  * app.bsky.embed.images: `{"images": [{"image": <blob> (accept image/*, maxSize 1,000,000 in the
    lexicon; the 2026-10-09 research note says 2 MB since April 2026 — the lower one is kept),
    "alt": <string, required>, "aspectRatio": {"width", "height"}}]}`, at most 4 images.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import httpx

from app.config import settings
from app.log_redaction import redact_secrets

logger = logging.getLogger(__name__)

#: The collection a Bluesky post record lives in.
POST_COLLECTION = "app.bsky.feed.post"

_CREATE_SESSION = "com.atproto.server.createSession"
_REFRESH_SESSION = "com.atproto.server.refreshSession"
_PUT_RECORD = "com.atproto.repo.putRecord"
_GET_RECORD = "com.atproto.repo.getRecord"
_DELETE_RECORD = "com.atproto.repo.deleteRecord"
_GET_POSTS = "app.bsky.feed.getPosts"
_GET_PROFILE = "app.bsky.actor.getProfile"
_UPLOAD_BLOB = "com.atproto.repo.uploadBlob"

#: The blob types `upload_blob` sends — a fixed allow-list, so a caller's value can never put a
#: line break (or anything else) into the Content-Type header.
BLOB_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
#: The largest blob sent: app.bsky.embed.images' image `maxSize` in the lexicon.
MAX_BLOB_BYTES = 1_000_000
#: The CIDv1 prefix of a raw-codec, sha2-256 block: version 1, codec raw (0x55), multihash sha2-256
#: (0x12), digest length 32 (0x20).
_RAW_SHA256_CID_PREFIX = bytes((0x01, 0x55, 0x12, 0x20))
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
#: A base32-lower CIDv1 as `upload_blob` reads it back ("b" + 58 characters for a sha2-256 one).
_CID_RE = re.compile(r"b[a-z2-7]{20,120}")

#: The PUBLIC Bluesky AppView: unauthenticated, cached reads of post views and profiles. Nothing here
#: ever sends it a credential — it needs none, and the account session belongs to the PDS alone.
APPVIEW_URL = "https://public.api.bsky.app"
#: app.bsky.feed.getPosts takes at most this many URIs per call (`uris.maxLength` in its lexicon).
MAX_GET_POSTS = 25
#: The post-view keys `get_posts` returns — each only when the answer carries it, its value exactly
#: as sent (a count may be missing, null, a string or negative: normalising is the caller's job).
POST_VIEW_KEYS = ("uri", "cid", "likeCount", "repostCount", "replyCount", "quoteCount",
                  "bookmarkCount", "indexedAt")
#: The profile keys `get_profile` returns (None when absent; values exactly as sent).
PROFILE_KEYS = ("did", "handle", "followersCount", "followsCount", "postsCount")

#: DID syntax (https://atproto.com/specs/did): "did:", a lowercase method, ":", then an identifier of
#: [A-Za-z0-9._:%-] that does not end in ":" or "%"; at most 2 KB. Every length is checked BEFORE a
#: regex runs, and every match is a fullmatch (a `$` anchor would let a trailing newline through).
_DID_RE = re.compile(r"did:[a-z]+:[A-Za-z0-9._:%-]*[A-Za-z0-9._-]")
_MAX_DID_LEN = 2048
#: Record-key syntax (https://atproto.com/specs/record-key): 1-512 of [A-Za-z0-9._:~-], never "."/"..".
_RKEY_RE = re.compile(r"[A-Za-z0-9._:~-]{1,512}")
_MAX_RKEY_LEN = 512
#: Handle syntax (https://atproto.com/specs/handle): dot-separated labels of ≤ 63 characters, the last
#: one starting with a letter; at most 253 characters in all.
_HANDLE_RE = re.compile(
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_MAX_HANDLE_LEN = 253
_AT_PREFIX = "at://"
#: The longest post at-URI those parts allow: "at://" + DID + "/" + collection + "/" + record key.
_MAX_POST_URI_LEN = len(_AT_PREFIX) + _MAX_DID_LEN + len(POST_COLLECTION) + _MAX_RKEY_LEN + 2

#: Cap on the platform's own `message` (and on a transport error's text) in our messages.
_DETAIL_CAP = 300
#: Cap on the platform's `error` name (a short identifier in practice).
_ERROR_NAME_CAP = 100
#: A `ratelimit-reset` / `Retry-After` further ahead than this is treated as unreadable
#: (createSession's daily limit makes ~24 h the longest honest wait).
_MAX_RETRY_AHEAD = timedelta(days=2)
#: Secrets shorter than this are not substring-replaced (they would shred the message); the
#: shape-based `redact_secrets` still runs on everything.
_MIN_SECRET_LEN = 6

#: Errors that mean the credential itself is refused (a 401, or these names on any 4xx).
_AUTH_ERRORS = frozenset({
    "AuthenticationRequired", "InvalidToken", "AccountTakedown", "AuthFactorTokenRequired",
})

_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_client: Optional[httpx.AsyncClient] = None


# ── Exception hierarchy ────────────────────────────────────────────────
# Class NAMES are load-bearing: `classify_exception` (app/api/error_response.py) maps a `bluesky*`
# class to 502 when its name contains refused / auth / notconfigured / invalidswap / expiredtoken
# (a permanent refusal) and to 503 otherwise (transient or unknown outcome).


class BlueskyException(Exception):
    """Base for Bluesky failures. Carries the XRPC method (NSID), the HTTP status, the atproto
    `error` name and the platform's `message` (scrubbed, capped) — never a credential."""

    def __init__(
        self,
        message: str = "",
        *,
        method: str = "",
        status: Optional[int] = None,
        error: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.status = status
        self.error = error
        self.detail = detail


class BlueskyNotConfiguredError(BlueskyException):
    """MARKETING_BLUESKY_HANDLE / MARKETING_BLUESKY_APP_PASSWORD unset, a host that is not an
    https:// URL, or an empty token argument. Raised BEFORE any request — nothing was sent."""


class BlueskyNotSentError(BlueskyException):
    """The request provably never reached the platform (connect failure, pool timeout, a request
    httpx refused to build). Safe to retry. (A 408 is NOT here: a proxy may answer it after the PDS
    processed the request — it is ambiguous.)"""


class BlueskyAmbiguousError(BlueskyException):
    """The request may have reached the platform and its outcome is unknown: a timeout after
    connecting, a dropped connection, a 5xx / 3xx / 408, an unreadable or unexpected 2xx body.
    Reconcile with `get_record` before any resend."""


class BlueskyRefusedError(BlueskyException):
    """A definite 4xx: the platform refused this request (bad rkey, record invalid, …).
    Retrying the same request will not help."""


class BlueskyAuthError(BlueskyRefusedError):
    """The credential was refused: any 401 (except ExpiredToken), or `AuthenticationRequired` /
    `InvalidToken` / `AccountTakedown` / `AuthFactorTokenRequired` on any 4xx. A wrong or
    revoked app password lands here — the owner has to fix a setting."""


class BlueskyExpiredTokenError(BlueskyRefusedError):
    """`ExpiredToken` on a 400 or 401: the access (or refresh) JWT is past its expiry. The cure
    is `refresh_session` (or, for an expired refresh token, `create_session`)."""


class BlueskyInvalidSwapError(BlueskyRefusedError):
    """`InvalidSwap`: putRecord with `swapRecord: null` found a DIFFERENT record already at that
    record key (an identical one answers 200)."""


class BlueskyRateLimitError(BlueskyException):
    """HTTP 429 — the request was not processed. `retry_at` (UTC-aware) from the `ratelimit-reset`
    epoch-seconds header, else `Retry-After`, else None."""

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
            follow_redirects=False,  # never carry a bearer token to another host
        )
    return _client


async def close_bluesky_client() -> None:
    """Tear-down hook for the app.main lifespan. Idempotent."""
    global _client
    if _client is not None:
        client, _client = _client, None
        await client.aclose()


# ── settings ───────────────────────────────────────────────────────────


def _handle() -> str:
    return (settings.MARKETING_BLUESKY_HANDLE or "").strip().lstrip("@")


def handle() -> str:
    """The configured handle as the API wants it (no `@`, no whitespace) — the same normalisation
    createSession uses, for callers that name the repo by handle (a reconcile after a restart)."""
    return _handle()


def _app_password() -> str:
    return (settings.MARKETING_BLUESKY_APP_PASSWORD or "").strip()


def configured() -> bool:
    """The handle and the app password are set (non-blank) AND the service URL is a usable https
    URL — a blank or http MARKETING_BLUESKY_SERVICE would make every login fail before any request,
    so Bluesky must count as OFF (previews, no Approve buttons), never half-on."""
    service = service_url()
    return (bool(_handle()) and bool(_app_password())
            and service.lower().startswith("https://") and len(service) > len("https://"))


def service_url() -> str:
    """Where createSession / refreshSession go (MARKETING_BLUESKY_SERVICE, no trailing '/')."""
    return (settings.MARKETING_BLUESKY_SERVICE or "").strip().rstrip("/")


# ── helpers ────────────────────────────────────────────────────────────


def _scrub(text: Any, secrets: Iterable[str], cap: int = _DETAIL_CAP) -> str:
    """Upstream-sourced text with this call's secrets and every secret shape removed, capped."""
    s = str(text)
    for secret in secrets:
        if secret and len(secret) >= _MIN_SECRET_LEN:
            s = s.replace(secret, "***")
    return redact_secrets(s)[:cap]


def _https_base(host: str, method: str) -> str:
    base = (host or "").strip().rstrip("/")
    if not base.lower().startswith("https://") or len(base) <= len("https://"):
        scheme = base.split("://", 1)[0][:10] if "://" in base else "none"
        raise BlueskyNotConfiguredError(
            f"bluesky {method}: the host must be an https:// URL (scheme: {scheme or 'none'})",
            method=method,
        )
    return base


def _number(raw: Any) -> Optional[float]:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _retry_at(headers: httpx.Headers, now: Optional[datetime] = None) -> Optional[datetime]:
    """When a 429 may be retried: `ratelimit-reset` (epoch seconds), else `Retry-After` (seconds
    or an HTTP date), else None. A time already past becomes `now`; one implausibly far ahead
    (> 2 days, e.g. milliseconds sent as seconds) is ignored."""
    now = now or datetime.now(timezone.utc)
    reset = _number(headers.get("ratelimit-reset"))
    if reset is not None and reset > 0:
        try:
            at = datetime.fromtimestamp(reset, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            at = None
        if at is not None and at <= now + _MAX_RETRY_AHEAD:
            return max(at, now)
    raw = headers.get("retry-after")
    if raw:
        seconds = _number(raw)
        if seconds is not None:
            if 0 <= seconds <= _MAX_RETRY_AHEAD.total_seconds():
                return now + timedelta(seconds=seconds)
            return None
        try:
            at = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            return None
        if at is None:
            return None
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        at = at.astimezone(timezone.utc)
        if at <= now + _MAX_RETRY_AHEAD:
            return max(at, now)
    return None


#: Raised by httpx before a single byte reached the platform.
_NOT_SENT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.UnsupportedProtocol,
    httpx.LocalProtocolError,
    httpx.InvalidURL,
)


async def _xrpc(
    verb: str,
    host: str,
    method: str,
    *,
    bearer: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    params: Optional[Union[Mapping[str, str], Sequence[Tuple[str, str]]]] = None,
    secrets: Iterable[str] = (),
    empty_ok: bool = False,
    raw: Optional[Tuple[bytes, str]] = None,
) -> Dict[str, Any]:
    """One XRPC call. Returns the JSON object of a 2xx answer (`{}` for an empty 2xx when
    `empty_ok`); raises the typed exception family otherwise. `params` is a mapping, or a sequence
    of (name, value) pairs for a query ARRAY (XRPC repeats the parameter name: `uris=a&uris=b`).
    `raw` = (bytes, content type) sends a binary body instead of JSON (uploadBlob); never both."""
    secrets = tuple(s for s in secrets if s)
    url = f"{_https_base(host, method)}/xrpc/{method}"
    headers = {"Accept": "application/json"}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    content: Optional[bytes] = None
    if raw is not None:
        if payload is not None:
            raise BlueskyRefusedError(f"bluesky {method}: a JSON and a binary body at once; nothing was sent",
                                      method=method)
        content = bytes(raw[0])
        headers["Content-Type"] = raw[1]
    if payload is not None:
        try:
            # Serialised here (not httpx's `json=`) so the bytes are pinned — `"swapRecord":null`
            # must reach the PDS literally — and a NaN is refused instead of sent.
            content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                                 allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as e:
            raise BlueskyRefusedError(
                f"bluesky {method}: the request body is not valid JSON ({type(e).__name__}); "
                f"nothing was sent",
                method=method,
            ) from None
        headers["Content-Type"] = "application/json"

    # Every transport failure is RAISED OUTSIDE its `except` block: `from None` hides an httpx
    # exception from tracebacks but keeps it as `__context__`, and that exception holds the request
    # — the Authorization header (review 2026-09-30).
    failure: Optional[Tuple[type, str]] = None
    resp: Optional[httpx.Response] = None
    try:
        resp = await _get_client().request(verb, url, content=content, params=params,
                                           headers=headers, timeout=_TIMEOUT)
    except _NOT_SENT_ERRORS as e:
        failure = (BlueskyNotSentError,
                   f"bluesky {method}: not sent ({type(e).__name__}: {_scrub(e, secrets, 200)})")
    except Exception as e:
        # ReadTimeout, WriteTimeout, RemoteProtocolError, ReadError, WriteError, ProxyError, a
        # decoding error, anything else once sending began — the request may have been processed.
        failure = (BlueskyAmbiguousError,
                   f"bluesky {method}: outcome unknown ({type(e).__name__}: {_scrub(e, secrets, 200)})")
    if failure is not None or resp is None:
        cls, message = failure or (BlueskyAmbiguousError, f"bluesky {method}: outcome unknown (no response)")
        raise cls(message, method=method)

    status = resp.status_code
    raw = resp.content or b""
    body: Any = None
    if raw.strip():
        try:
            body = resp.json()
        except (ValueError, RecursionError):  # JSONDecodeError / UnicodeDecodeError; absurd nesting
            body = None
    envelope: Dict[str, Any] = body if isinstance(body, dict) else {}

    if 200 <= status < 300:
        if not raw.strip():
            if empty_ok:
                return {}
            raise BlueskyAmbiguousError(f"bluesky {method}: HTTP {status} with an empty body",
                                        method=method, status=status)
        if not isinstance(body, dict):
            raise BlueskyAmbiguousError(f"bluesky {method}: HTTP {status} with an unreadable body",
                                        method=method, status=status)
        return body

    err_raw = envelope.get("error")
    msg_raw = envelope.get("message")
    error = _scrub(err_raw, secrets, _ERROR_NAME_CAP) if isinstance(err_raw, str) and err_raw else None
    detail = _scrub(msg_raw, secrets) if isinstance(msg_raw, str) and msg_raw else None
    label = f"bluesky {method}: HTTP {status}"
    if error:
        label += f" {error}"
    if detail:
        label += f": {detail}"
    kw: Dict[str, Any] = {"method": method, "status": status, "error": error, "detail": detail}

    if status == 429:
        retry_at = _retry_at(resp.headers)
        raise BlueskyRateLimitError(
            f"{label} (retry_at={retry_at.isoformat() if retry_at else None})",
            retry_at=retry_at, **kw,
        )
    if status == 408:
        # NOT "never received": a proxy in front of the PDS can answer 408 after the PDS processed
        # the request. Unknown outcome → reconcile (cheap) rather than a resend or a refusal.
        raise BlueskyAmbiguousError(label, **kw)
    if not 400 <= status < 500:
        raise BlueskyAmbiguousError(label, **kw)
    if error == "ExpiredToken" and status in (400, 401):
        raise BlueskyExpiredTokenError(label, **kw)
    if status == 401 or error in _AUTH_ERRORS:
        raise BlueskyAuthError(label, **kw)
    if error == "InvalidSwap":
        raise BlueskyInvalidSwapError(label, **kw)
    raise BlueskyRefusedError(label, **kw)


def _pds_from(did_doc: Any, did: str) -> str:
    """The `#atproto_pds` serviceEndpoint of a DID document (https only), else the service URL."""
    services = did_doc.get("service") if isinstance(did_doc, dict) else None
    if isinstance(services, list):
        for entry in services:
            if not isinstance(entry, dict):
                continue
            sid = entry.get("id")
            if not (isinstance(sid, str) and sid.endswith("#atproto_pds")):
                continue
            endpoint = entry.get("serviceEndpoint")
            if isinstance(endpoint, str):
                endpoint = endpoint.strip().rstrip("/")
                if endpoint.lower().startswith("https://") and len(endpoint) > len("https://"):
                    return endpoint
            logger.warning(
                "bluesky session: did=%s lists an #atproto_pds that is not an https URL — "
                "using the service URL", did,
            )
            return service_url()
    if did_doc is not None:
        logger.warning("bluesky session: did=%s has no #atproto_pds in its DID document — "
                       "using the service URL", did)
    return service_url()


def _session(method: str, body: Dict[str, Any]) -> Dict[str, Any]:
    access, refresh, did = body.get("accessJwt"), body.get("refreshJwt"), body.get("did")
    if not (isinstance(access, str) and access and isinstance(refresh, str) and refresh
            and isinstance(did, str) and did.startswith("did:")):
        raise BlueskyAmbiguousError(
            f"bluesky {method}: HTTP 200 without a usable accessJwt / refreshJwt / did",
            method=method, status=200,
        )
    handle = body.get("handle")
    return {
        "access_jwt": access,
        "refresh_jwt": refresh,
        "did": did,
        "handle": handle if isinstance(handle, str) else "",
        "pds": _pds_from(body.get("didDoc"), did),
    }


# ── XRPC methods ───────────────────────────────────────────────────────


async def create_session() -> Dict[str, Any]:
    """createSession with the configured handle + APP password, on the service URL.

    Returns {"access_jwt", "refresh_jwt", "did", "handle", "pds"} — `pds` is the account's
    `#atproto_pds` endpoint (no trailing '/'), else `service_url()`. Rate-limited by the platform
    to 30 / 5 min and 300 / day: reuse the session, refresh it on ExpiredToken."""
    handle, password = _handle(), _app_password()
    if not handle or not password:
        raise BlueskyNotConfiguredError(
            f"bluesky {_CREATE_SESSION}: MARKETING_BLUESKY_HANDLE and "
            f"MARKETING_BLUESKY_APP_PASSWORD must both be set",
            method=_CREATE_SESSION,
        )
    body = await _xrpc("POST", service_url(), _CREATE_SESSION,
                       payload={"identifier": handle, "password": password},
                       secrets=(password,))
    return _session(_CREATE_SESSION, body)


async def refresh_session(refresh_jwt: str) -> Dict[str, Any]:
    """refreshSession on the service URL with `Bearer <refresh_jwt>` (no body). Same shape as
    `create_session`; the returned refresh_jwt is ROTATED — the one passed in is spent."""
    if not refresh_jwt:
        raise BlueskyNotConfiguredError(f"bluesky {_REFRESH_SESSION}: no refresh token",
                                        method=_REFRESH_SESSION)
    body = await _xrpc("POST", service_url(), _REFRESH_SESSION, bearer=refresh_jwt,
                       secrets=(refresh_jwt, _app_password()))
    return _session(_REFRESH_SESSION, body)


async def put_record(
    pds: str,
    access_jwt: str,
    *,
    repo: str,
    collection: str,
    rkey: str,
    record: Dict[str, Any],
) -> Dict[str, Any]:
    """putRecord with `"swapRecord": null` — create ONLY if nothing exists at `rkey`. An identical
    retry is a 200 no-op with the same uri/cid; a different record there → BlueskyInvalidSwapError.
    Returns {"uri": str, "cid": str}."""
    if not access_jwt:
        raise BlueskyNotConfiguredError(f"bluesky {_PUT_RECORD}: no access token", method=_PUT_RECORD)
    body = await _xrpc(
        "POST", pds, _PUT_RECORD, bearer=access_jwt,
        payload={"repo": repo, "collection": collection, "rkey": rkey, "record": record,
                 "swapRecord": None},
        secrets=(access_jwt, _app_password()),
    )
    uri, cid = body.get("uri"), body.get("cid")
    if not (isinstance(uri, str) and uri and isinstance(cid, str) and cid):
        raise BlueskyAmbiguousError(f"bluesky {_PUT_RECORD}: HTTP 200 without uri / cid",
                                    method=_PUT_RECORD, status=200)
    return {"uri": uri, "cid": cid}


async def get_record(host: str, *, repo: str, collection: str, rkey: str) -> Optional[Dict[str, Any]]:
    """getRecord (no auth). Returns {"uri": str, "cid": Optional[str], "value": dict}, or None when
    the platform answers `RecordNotFound`. Any other failure raises — a 400 with another error
    name, a 5xx or an unreadable answer is NOT "absent"."""
    try:
        body = await _xrpc("GET", host, _GET_RECORD,
                           params={"repo": repo, "collection": collection, "rkey": rkey},
                           secrets=(_app_password(),))
    except BlueskyRefusedError as e:
        # Only the documented answer (HTTP 400 RecordNotFound) means absent: "absent" licenses a
        # resend, so anything less certain must raise instead.
        if type(e) is BlueskyRefusedError and e.error == "RecordNotFound" and e.status == 400:
            return None
        raise
    uri, cid, value = body.get("uri"), body.get("cid"), body.get("value")
    if not (isinstance(uri, str) and uri and isinstance(value, dict)):
        raise BlueskyAmbiguousError(f"bluesky {_GET_RECORD}: HTTP 200 without uri / value",
                                    method=_GET_RECORD, status=200)
    return {"uri": uri, "cid": cid if isinstance(cid, str) and cid else None, "value": value}


def raw_cid_for_sha256(sha256_hex: str) -> str:
    """The CID atproto gives a blob whose bytes have this sha256 (64 hex characters): CIDv1, codec
    raw, sha2-256 multihash, base32-lower multibase — "bafkrei…". Pure; the empty input's is the
    well-known "bafkreihdwdcefgh4dqkjv67uzcmw7ojee6xedzdetojuzjevtenxquvyku". ValueError for a
    digest that is not 64 hex characters."""
    digest = sha256_hex.strip().lower() if isinstance(sha256_hex, str) else ""
    if not _SHA256_HEX_RE.fullmatch(digest):
        raise ValueError("a blob CID needs a sha256 of 64 hex characters")
    encoded = base64.b32encode(_RAW_SHA256_CID_PREFIX + bytes.fromhex(digest)).decode("ascii")
    return "b" + encoded.lower().rstrip("=")


def blob_ref(cid: str, mime_type: str, size: int) -> Dict[str, Any]:
    """A blob as a record names it: `{"$type": "blob", "ref": {"$link": cid}, "mimeType", "size"}`."""
    return {"$type": "blob", "ref": {"$link": cid}, "mimeType": mime_type, "size": int(size)}


async def upload_blob(pds: str, access_jwt: str, *, data: bytes, mime_type: str) -> Dict[str, Any]:
    """com.atproto.repo.uploadBlob — the bytes as the body, `Content-Type: <mime_type>`, on the
    account's PDS. Returns {"cid": str, "mime_type": str, "size": int} read from the answer's blob.

    An upload is never a post: nothing is visible until a record names the blob, and the same bytes
    always get the same CID (a repeat upload is harmless). Empty bytes, more than 1,000,000 bytes, a
    type outside BLOB_MIME_TYPES or no access token raise BEFORE anything is sent (Refused /
    NotConfigured); a 2xx without a readable `blob` raises BlueskyAmbiguousError."""
    method = _UPLOAD_BLOB
    if not access_jwt:
        raise BlueskyNotConfiguredError(f"bluesky {method}: no access token", method=method)
    if mime_type not in BLOB_MIME_TYPES:
        raise BlueskyRefusedError(f"bluesky {method}: blob type must be one of {sorted(BLOB_MIME_TYPES)}; "
                                  "nothing was sent", method=method)
    if not isinstance(data, (bytes, bytearray)) or not data or len(data) > MAX_BLOB_BYTES:
        size = len(data) if isinstance(data, (bytes, bytearray)) else type(data).__name__
        raise BlueskyRefusedError(f"bluesky {method}: blob must be 1-{MAX_BLOB_BYTES} bytes (got {size}); "
                                  "nothing was sent", method=method)
    body = await _xrpc("POST", pds, method, bearer=access_jwt, raw=(bytes(data), mime_type),
                       secrets=(access_jwt, _app_password()))
    blob = body.get("blob")
    ref = blob.get("ref") if isinstance(blob, dict) else None
    cid = ref.get("$link") if isinstance(ref, dict) else None
    answered_type = blob.get("mimeType") if isinstance(blob, dict) else None
    size = blob.get("size") if isinstance(blob, dict) else None
    if not (isinstance(cid, str) and _CID_RE.fullmatch(cid) and isinstance(answered_type, str)
            and isinstance(size, int) and not isinstance(size, bool) and size >= 0):
        raise BlueskyAmbiguousError(f"bluesky {method}: HTTP 200 without a readable blob",
                                    method=method, status=200)
    return {"cid": cid, "mime_type": answered_type[:100], "size": size}


async def delete_record(pds: str, access_jwt: str, *, repo: str, collection: str, rkey: str) -> Dict[str, Any]:
    """deleteRecord — idempotent (an absent record answers 200 `{}`). Returns {"deleted": True};
    a 200 with `{}`, an empty body or `{"commit": …}` all count."""
    if not access_jwt:
        raise BlueskyNotConfiguredError(f"bluesky {_DELETE_RECORD}: no access token",
                                        method=_DELETE_RECORD)
    await _xrpc(
        "POST", pds, _DELETE_RECORD, bearer=access_jwt,
        payload={"repo": repo, "collection": collection, "rkey": rkey},
        secrets=(access_jwt, _app_password()), empty_ok=True,
    )
    return {"deleted": True}


# ── public AppView reads (the measure step) — never a credential ─────────


def is_post_uri(value: Any) -> bool:
    """True for `at://did:<method>:<id>/app.bsky.feed.post/<rkey>` — the only URI shape `get_posts`
    sends. A handle-based URI is NOT one: a handle can change hands, a DID cannot."""
    if not isinstance(value, str) or len(value) > _MAX_POST_URI_LEN or not value.startswith(_AT_PREFIX):
        return False
    parts = value[len(_AT_PREFIX):].split("/")
    if len(parts) != 3:
        return False
    did, collection, rkey = parts
    return (collection == POST_COLLECTION
            and len(did) <= _MAX_DID_LEN and _DID_RE.fullmatch(did) is not None
            and rkey not in (".", "..") and _RKEY_RE.fullmatch(rkey) is not None)


def _actor(value: Any) -> Optional[str]:
    """A DID, or a handle (surrounding whitespace and one leading "@" dropped); None otherwise."""
    ident = value.strip() if isinstance(value, str) else ""
    if ident.startswith("@"):
        ident = ident[1:]
    if ident.startswith("did:"):
        return ident if len(ident) <= _MAX_DID_LEN and _DID_RE.fullmatch(ident) else None
    return ident if len(ident) <= _MAX_HANDLE_LEN and _HANDLE_RE.fullmatch(ident) else None


async def get_posts(uris: Sequence[str], *, host: str = APPVIEW_URL) -> List[Dict[str, Any]]:
    """app.bsky.feed.getPosts on the public AppView — NO Authorization header, ever — for 1-25 post
    at-URIs (a URI given twice is asked for once). Returns the answer's post views in its order, each
    restricted to the `POST_VIEW_KEYS` it carries with the values exactly as sent; the caller
    normalises the counts. A post that no longer exists (deleted, or its account gone) is simply
    absent.

    An empty list returns [] without a call. `uris` that is not a list/tuple, more than 25 URIs, or
    any URI that is not `at://did:<method>:<id>/app.bsky.feed.post/<rkey>` raises
    BlueskyRefusedError (status None) BEFORE anything is sent. An answer without a `posts` list, or
    with an item that is not an object carrying a string `uri`, raises BlueskyAmbiguousError: a
    skipped item would read as a deleted post."""
    method = _GET_POSTS
    if not isinstance(uris, (list, tuple)):
        raise BlueskyRefusedError(
            f"bluesky {method}: uris must be a list of post at:// URIs (got {type(uris).__name__}); "
            f"nothing was sent", method=method)
    if not uris:
        return []
    if len(uris) > MAX_GET_POSTS:
        raise BlueskyRefusedError(
            f"bluesky {method}: {len(uris)} URIs, at most {MAX_GET_POSTS} per call; nothing was sent",
            method=method)
    for index, uri in enumerate(uris):
        if not is_post_uri(uri):
            shown = repr(uri[:60]) if isinstance(uri, str) else type(uri).__name__
            raise BlueskyRefusedError(
                f"bluesky {method}: uris[{index}] is not an at://did:<method>:<id>/{POST_COLLECTION}/<rkey> "
                f"URI ({_scrub(shown, (_app_password(),), 120)}); nothing was sent", method=method)
    body = await _xrpc("GET", host, method, params=[("uris", uri) for uri in dict.fromkeys(uris)],
                       secrets=(_app_password(),))
    posts = body.get("posts")
    if not isinstance(posts, list):
        raise BlueskyAmbiguousError(f"bluesky {method}: HTTP 200 without a posts list",
                                    method=method, status=200)
    views: List[Dict[str, Any]] = []
    for item in posts:
        uri = item.get("uri") if isinstance(item, dict) else None
        if not isinstance(uri, str) or not uri:
            raise BlueskyAmbiguousError(f"bluesky {method}: HTTP 200 with a post view that has no uri",
                                        method=method, status=200)
        views.append({key: item[key] for key in POST_VIEW_KEYS if key in item})
    return views


async def get_profile(actor: str, *, host: str = APPVIEW_URL) -> Dict[str, Any]:
    """app.bsky.actor.getProfile on the public AppView — NO Authorization header, ever — for a DID or
    a handle. Returns {"did", "handle", "followersCount", "followsCount", "postsCount"}: values
    exactly as sent, None when absent (the caller normalises the counts).

    An `actor` that is neither a DID nor a handle raises BlueskyRefusedError (status None) before
    anything is sent. An answer without a usable `did`, or about ANOTHER account (a different DID
    for a DID, a different handle for a handle), raises BlueskyAmbiguousError — never that
    account's numbers."""
    method = _GET_PROFILE
    ident = _actor(actor)
    if ident is None:
        raise BlueskyRefusedError(f"bluesky {method}: actor must be a DID or a handle; nothing was sent",
                                  method=method)
    body = await _xrpc("GET", host, method, params={"actor": ident}, secrets=(_app_password(),))
    did, handle = body.get("did"), body.get("handle")
    if not (isinstance(did, str) and len(did) <= _MAX_DID_LEN and _DID_RE.fullmatch(did)):
        raise BlueskyAmbiguousError(f"bluesky {method}: HTTP 200 without a usable did",
                                    method=method, status=200)
    if ident.startswith("did:"):
        same = did == ident
    else:
        same = isinstance(handle, str) and handle.lower() == ident.lower()
    if not same:
        answered = _scrub(did, (_app_password(),), 80)   # a DID: the regex above admits no newline
        if isinstance(handle, str):
            # Upstream text, unvalidated: repr() so a newline in it cannot forge a log line.
            answered += f" / {_scrub(repr(handle[:80]), (_app_password(),), 100)}"
        raise BlueskyAmbiguousError(
            f"bluesky {method}: HTTP 200 about another account (asked {ident}, answered {answered})",
            method=method, status=200)
    return {key: body.get(key) for key in PROFILE_KEYS}
