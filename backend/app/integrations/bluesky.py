"""
Bluesky (atproto XRPC) — a thin client for the marketing PUBLISHER (design doc §12.10).

ONE caller: `app/services/marketing/outlet_bluesky.py`, the Bluesky adapter that the publisher loop
in `app/services/marketing/publisher_service.py` drives, in the WEB process. The media worker never
holds these credentials — it holds no social secret at all (rules/marketing.md §2).

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
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, Optional, Tuple

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
    params: Optional[Dict[str, str]] = None,
    secrets: Iterable[str] = (),
    empty_ok: bool = False,
) -> Dict[str, Any]:
    """One XRPC call. Returns the JSON object of a 2xx answer (`{}` for an empty 2xx when
    `empty_ok`); raises the typed exception family otherwise."""
    secrets = tuple(s for s in secrets if s)
    url = f"{_https_base(host, method)}/xrpc/{method}"
    headers = {"Accept": "application/json"}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    content: Optional[bytes] = None
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
