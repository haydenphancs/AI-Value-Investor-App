"""
learn_narrator/api.py — the worker's client for the web service's internal Learn API
(`/api/v1/internal/learn/*`, plan "Wiser weekly content engine" §1.8; the server half is
`app/api/v1/endpoints/learn_internal.py`, which this package never imports).

The contract, as this client speaks it:
  * Every call carries `X-Learn-Narrator-Token`. After a claim, every call also carries
    `X-Learn-Claim: <attempts>.<claim_nonce>` (`hold`), which the server fences each write on — a
    worker whose job was cancelled or re-claimed meanwhile gets 409 instead of writing over it.
  * `POST /jobs/claim` → 204 (nothing to render: `claim` returns None) or 200 `{"job": {...}}`.
  * `POST /jobs/{id}/heartbeat`, `/upload`, `/complete`, `/fail`.
  * The audio itself goes to Storage through the signed upload URL `/upload` mints (`put_signed`):
    the worker holds no Supabase key.

Status handling (one table, so every route behaves the same):
  * 2xx            → the JSON body (204 → None).
  * 3xx (any non-2xx below 400) → `NarratorRedirectError`, never followed and never retried: httpx
                     does not follow redirects, and following one would carry the token header to
                     wherever it points. Before 2026-10-23 an empty-bodied 301 read as "204, no job",
                     so a redirecting LEARN_NARRATOR_API_BASE looked healthy forever. On claim the
                     caller exits 2 (configuration). The same rule holds for the signed upload PUT,
                     where a redirect would otherwise have read as "stored".
  * 401 / 403      → `NarratorAuthError`: the token is missing or wrong. The caller logs an ERROR and
                     exits non-zero — retrying cannot fix a credential.
  * 404 / 409 / 422 → `NarratorTerminalError`, never retried: the job is gone, no longer ours, or the
                     body was refused. Retrying a refusal re-sends the same refusal.
  * 502 / 503 / 504 and transport errors → retried up to RETRIES times with backoff, then
                     `NarratorUnavailable`.
  * anything else  → `NarratorAPIError` (not retried: a 500 is a server bug, deterministic).

⚠️ The signed upload URL is a WRITE CREDENTIAL (its `?token=` lets anyone put an object at that
path until it expires). It is never logged: main.py quiets the `httpx` / `httpcore` loggers, and
nothing here formats the URL into a message or an exception.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

import httpx

logger = logging.getLogger("learn_narrator.api")

API_PREFIX = "/api/v1/internal/learn"
TOKEN_HEADER = "X-Learn-Narrator-Token"
CLAIM_HEADER = "X-Learn-Claim"
AUDIO_CONTENT_TYPE = "audio/mp4"

AUTH_STATUS = frozenset({401, 403})
TERMINAL_STATUS = frozenset({404, 409, 422})
RETRYABLE_STATUS = frozenset({502, 503, 504})
#: Retries AFTER the first attempt (4 attempts in all), with BACKOFF_SECONDS[k] before retry k+1.
RETRIES = 3
BACKOFF_SECONDS = (2.0, 4.0, 8.0)
#: Per-request timeout for the JSON routes.
TIMEOUT_SECONDS = 30.0
#: The signed-upload PUT carries ~9 MB (a 12-minute Deep Dive at 96 kbps).
UPLOAD_TIMEOUT_SECONDS = 180.0


class NarratorAPIError(RuntimeError):
    """A non-2xx answer. `status` is None for a transport failure; `error_code` is the body's
    `error_code` (the APIErrorResponse contract) when it had one."""

    def __init__(self, message: str, *, status: Optional[int] = None, error_code: Optional[str] = None,
                 server_message: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.server_message = server_message


class NarratorAuthError(NarratorAPIError):
    """401 / 403: the worker token is missing, wrong, or not configured on the web service."""


class NarratorTerminalError(NarratorAPIError):
    """404 / 409 / 422: not retried. 409 on a held job = cancelled or re-claimed: stop."""


class NarratorUnavailable(NarratorAPIError):
    """502 / 503 / 504 or a transport error on every attempt."""


class NarratorRedirectError(NarratorAPIError):
    """A 3xx (any non-2xx answer below 400): never followed, never retried. On the JSON routes it
    means LEARN_NARRATOR_API_BASE is not the final URL (http:// instead of https://, a bare domain
    that redirects to www, …)."""


def _redirect_error(resp: httpx.Response, what: str, *, name_target: bool) -> NarratorRedirectError:
    """Names where the redirect points — scheme, host and port only, never userinfo, path or query
    — and only when `name_target` (the JSON routes). The signed upload passes False: a Storage
    redirect's Location may carry the same write token as the signed URL itself. Naming is a hint,
    so ANY failure to read the Location (a bad URL, a bad IDNA host) just leaves it out."""
    target = ""
    if name_target:
        try:
            location = resp.headers.get("location", "")
            url = resp.request.url.join(location) if location else None
            if url is not None and url.scheme and url.host:
                target = f" to {url.scheme}://{url.netloc.decode('ascii')}"
        except Exception:  # noqa: BLE001 — the hint is optional; the typed error below is not
            target = ""
    kind = "redirect" if 300 <= resp.status_code < 400 else "non-2xx answer"
    hint = " — set LEARN_NARRATOR_API_BASE to the final URL" if name_target else ""
    return NarratorRedirectError(f"{what} -> {resp.status_code} {kind}{target} (not followed){hint}",
                                 status=resp.status_code)


def _refuse_redirects(*, name_target: bool, what: Optional[str] = None) -> Callable[[httpx.Response], None]:
    """An httpx RESPONSE HOOK that turns any non-2xx answer below 400 into NarratorRedirectError.
    It must be a hook, not only a check after `request()` returns: for 301/302/303/307/308 httpx
    parses the Location inside `request()` even with follow_redirects off (to build `next_request`),
    so a malformed one used to surface as a retried RemoteProtocolError or an untyped InvalidURL.
    Hooks run before that parse, and an exception raised here is not a TransportError, so the
    retry loops never retry it."""
    def hook(resp: httpx.Response) -> None:
        if 200 <= resp.status_code < 300 or resp.status_code >= 400:
            return
        label = what
        if label is None:
            path = resp.request.url.path
            label = f"{resp.request.method} {path[len(API_PREFIX):] if path.startswith(API_PREFIX) else path}"
        raise _redirect_error(resp, label, name_target=name_target)
    return hook


def _error_from(resp: httpx.Response, what: str) -> NarratorAPIError:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else body
    code = detail.get("error_code") if isinstance(detail.get("error_code"), str) else None
    message = detail.get("message") if isinstance(detail.get("message"), str) else ""
    if not message and isinstance(body.get("detail"), str):
        message = body["detail"]
    message = " ".join(message.split())[:300]
    text = f"{what} -> {resp.status_code} {code or '?'}: {message}"
    if resp.status_code in AUTH_STATUS:
        cls = NarratorAuthError
    elif resp.status_code in TERMINAL_STATUS:
        cls = NarratorTerminalError
    else:
        cls = NarratorAPIError
    return cls(text, status=resp.status_code, error_code=code, server_message=message)


class NarratorClient:
    """Thin, synchronous client (the worker is one process doing one job per cron tick)."""

    def __init__(self, base_url: str, token: str, *, timeout: float = TIMEOUT_SECONDS,
                 transport: Optional[httpx.BaseTransport] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 user_agent: str = "caydex-learn-narrator") -> None:
        if not token:
            raise NarratorAuthError("LEARN_NARRATOR_API_TOKEN is empty", status=None)
        self._sleep = sleep
        self._transport = transport
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + API_PREFIX,
            headers={TOKEN_HEADER: token, "User-Agent": user_agent},
            timeout=timeout, transport=transport,
            event_hooks={"response": [_refuse_redirects(name_target=True)]},
        )

    # ── claim ────────────────────────────────────────────────────────────────

    def hold(self, attempts: int, claim_nonce: str) -> None:
        """Present THIS claim on every later call."""
        self._client.headers[CLAIM_HEADER] = f"{int(attempts)}.{claim_nonce}"

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "NarratorClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── transport with the status table ──────────────────────────────────────

    def _call(self, method: str, path: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        what = f"{method} {path}"
        last: Optional[NarratorAPIError] = None
        for attempt in range(RETRIES + 1):
            if attempt:
                self._sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
            try:
                resp = self._client.request(method, path, **kwargs)
            except httpx.TransportError as e:
                last = NarratorUnavailable(f"{what}: {type(e).__name__}: {e}")
                logger.warning("learn api %s transport error (attempt %d/%d): %s: %s",
                               what, attempt + 1, RETRIES + 1, type(e).__name__, e)
                continue
            if resp.status_code in RETRYABLE_STATUS:
                last = NarratorUnavailable(f"{what} -> {resp.status_code}", status=resp.status_code)
                logger.warning("learn api %s -> %d (attempt %d/%d)", what, resp.status_code,
                               attempt + 1, RETRIES + 1)
                continue
            if resp.status_code >= 400:
                raise _error_from(resp, what)
            if not 200 <= resp.status_code < 300:        # backstop: the response hook raises first
                raise _redirect_error(resp, what, name_target=True)
            if resp.status_code == 204 or not resp.content:
                return None
            try:
                body = resp.json()
            except ValueError:
                raise NarratorAPIError(f"{what} -> {resp.status_code} with a body that is not JSON",
                                       status=resp.status_code) from None
            if not isinstance(body, dict):
                raise NarratorAPIError(f"{what} -> {resp.status_code} with a {type(body).__name__} body",
                                       status=resp.status_code)
            return body
        assert last is not None
        raise NarratorUnavailable(f"{what} failed after {RETRIES + 1} attempts: {last}",
                                  status=last.status)

    # ── routes ───────────────────────────────────────────────────────────────

    def claim(self, engines: List[str], worker_version: str) -> Optional[Dict[str, Any]]:
        """The oldest job whose frozen engine is in `engines`, or None (204)."""
        body = self._call("POST", "/jobs/claim", json={"engines": list(engines),
                                                       "worker_version": worker_version})
        if body is None:
            return None
        job = body.get("job")
        if not isinstance(job, dict):
            raise NarratorAPIError("POST /jobs/claim -> 200 without a job object", status=200)
        return job

    def heartbeat(self, job_id: str) -> Dict[str, Any]:
        return self._call("POST", f"/jobs/{job_id}/heartbeat", json={}) or {}

    def upload_target(self, job_id: str, *, size_bytes: int, sha256: str) -> Dict[str, Any]:
        body = self._call("POST", f"/jobs/{job_id}/upload",
                          json={"content_type": AUDIO_CONTENT_TYPE, "bytes": int(size_bytes),
                                "sha256": sha256})
        if not body or not isinstance(body.get("path"), str):
            raise NarratorAPIError(f"POST /jobs/{job_id}/upload -> 200 without a path", status=200)
        return body

    def complete(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("POST", f"/jobs/{job_id}/complete", json=payload) or {}

    def fail(self, job_id: str, code: str, detail: str) -> Dict[str, Any]:
        return self._call("POST", f"/jobs/{job_id}/fail", json={"code": code, "detail": detail}) or {}

    def put_signed(self, upload_url: str, data: bytes, *, path: str, apikey: Optional[str] = None,
                   timeout: float = UPLOAD_TIMEOUT_SECONDS) -> None:
        """PUT the audio to the signed upload URL: raw body, `Content-Type: audio/mp4`,
        `x-upsert: false` (paths are content-addressed and immutable — a second PUT to an existing
        key must fail, not overwrite). 409 = the object is already there (an earlier attempt's PUT
        landed but its `complete` never did): not an error, `complete` verifies size and type.
        Retried like the JSON routes on 502/503/504 and transport errors. Only `path` is ever
        logged — never the URL."""
        headers = {"Content-Type": AUDIO_CONTENT_TYPE, "x-upsert": "false"}
        if apikey:
            headers["apikey"] = apikey
        what = f"signed upload PUT {path}"
        last: Optional[str] = None
        with httpx.Client(timeout=timeout, transport=self._transport,
                          event_hooks={"response": [_refuse_redirects(name_target=False, what=what)]}) as c:
            for attempt in range(RETRIES + 1):
                if attempt:
                    self._sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
                try:
                    resp = c.put(upload_url, content=data, headers=headers)
                except httpx.TransportError as e:
                    # str(e) of an httpx error can carry the request URL — log the type only.
                    last = type(e).__name__
                    logger.warning("%s transport error (attempt %d/%d): %s", what, attempt + 1,
                                   RETRIES + 1, type(e).__name__)
                    continue
                if resp.status_code in RETRYABLE_STATUS:
                    last = str(resp.status_code)
                    logger.warning("%s -> %d (attempt %d/%d)", what, resp.status_code, attempt + 1, RETRIES + 1)
                    continue
                if resp.status_code == 409:
                    logger.info("%s -> 409 (already in the bucket); continuing to complete", path)
                    return
                if resp.status_code >= 400:
                    snippet = " ".join(resp.text.split())[:200]
                    raise NarratorAPIError(f"{what} -> {resp.status_code}: {snippet}",
                                           status=resp.status_code)
                if not 200 <= resp.status_code < 300:    # backstop: the response hook raises first
                    raise _redirect_error(resp, what, name_target=False)
                return
        raise NarratorUnavailable(f"{what} failed after {RETRIES + 1} attempts ({last})")
