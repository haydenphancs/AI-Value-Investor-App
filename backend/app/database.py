"""
Database Connection - Supabase Only
No SQLAlchemy. Uses Supabase Python client for all DB operations.
"""

import asyncio
import contextvars
import ipaddress
from typing import Any, Callable, Optional
import httpx
from supabase import create_client, Client
import logging

from app.config import settings

logger = logging.getLogger(__name__)

_supabase_client: Optional[Client] = None


def _force_http1_on_postgrest(client: Client) -> None:
    """Replace the postgrest sub-client's httpx session with an HTTP/1.1 one.

    Why: supabase-py's postgrest/storage/auth sub-clients each build their httpx
    client with ``http2=True`` (hardcoded). Our long-lived singleton keeps ONE
    pooled HTTP/2 connection to the Supabase gateway; it accumulates streams
    (seen: last_stream_id=431) until the gateway idle-closes it or sends a GOAWAY
    (ConnectionTerminated, error_code 9 = COMPRESSION_ERROR). Reusing that dead
    connection then raises ``httpx.RemoteProtocolError`` / ``LocalProtocolError``
    from the h2 state machine, and a torn-down response surfaces as postgrest
    ``APIError('JSON could not be generated')`` — the exact Sentry pairs we saw.

    HTTP/1.1 has no such reuse race: httpx transparently discards a server-closed
    keepalive connection and opens a fresh one. The postgrest client is SYNC, so it
    never multiplexes concurrent streams anyway — HTTP/2 buys it nothing here and
    only adds the fragility. We swap ONLY postgrest (the source of these errors and
    by far the highest-volume path); storage/auth keep their own clients.

    Best-effort: if a supabase-py bump changes these internals, log and keep the
    default client rather than breaking startup.
    """
    try:
        pg = client.postgrest  # property — lazily constructs the postgrest sub-client
        old = pg.session
        new = httpx.Client(
            base_url=old.base_url,
            headers=old.headers,
            timeout=old.timeout,
            follow_redirects=True,
            http2=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        pg.session = new
        try:
            old.close()
        except Exception:
            pass
        logger.info(
            "Supabase postgrest client set to HTTP/1.1 (avoids h2 stale-connection "
            "reuse races: RemoteProtocolError/LocalProtocolError/APIError)"
        )
    except Exception as e:
        logger.warning(
            "Could not force HTTP/1.1 on the Supabase postgrest client "
            "(%s: %s) — keeping the default http2 client",
            type(e).__name__, e,
        )


def get_supabase() -> Client:
    """
    Get or create Supabase client singleton.
    Uses service role key for server-side operations (bypasses RLS).
    """
    global _supabase_client
    if _supabase_client is None:
        logger.info("Initializing Supabase client")
        _supabase_client = create_client(
            supabase_url=settings.SUPABASE_URL,
            supabase_key=settings.SUPABASE_SERVICE_ROLE_KEY
        )
        _force_http1_on_postgrest(_supabase_client)
    # Self-heal on every resolution, like get_auth_client / get_admin_client. Nothing is
    # supposed to sign in on this client (tests/test_supabase_client_isolation.py pins it),
    # but `_auth_of()` in auth.py falls back to THIS client when a handler forgets the
    # isolated dependency, and one such sign-in used to demote every later `.table()` call
    # in the process to that user's JWT. Since migration 163 the tables this client serves
    # grant NOTHING to `authenticated`, so that demotion is no longer a wrong-row read but a
    # process-wide 42501 until restart. A header write per call is the cheap insurance.
    return _reset_to_service_role(_supabase_client)


_auth_client: Optional[Client] = None
_admin_client: Optional[Client] = None


def _new_isolated_client(label: str) -> Client:
    """A fresh service-role client that never persists or refreshes a session.

    `persist_session` / `auto_refresh_token` are off so the SDK never carries one caller's
    session — or a background refresh thread — into another request on a client that serves
    every user.

    The options are an optimisation, not the isolation itself: a separate client INSTANCE is
    what keeps the other clients clean. So a supabase-py bump that moves `ClientOptions`
    degrades to a plain client rather than failing startup.
    """
    logger.info("Initializing Supabase %s client (isolated from the service-role client)", label)
    try:
        from supabase.lib.client_options import ClientOptions  # noqa: PLC0415

        return create_client(
            supabase_url=settings.SUPABASE_URL,
            supabase_key=settings.SUPABASE_SERVICE_ROLE_KEY,
            options=ClientOptions(persist_session=False, auto_refresh_token=False),
        )
    except Exception as e:
        logger.warning(
            "Could not build the %s client with ClientOptions (%s: %s) — "
            "falling back to a plain isolated client",
            label, type(e).__name__, e,
        )
        return create_client(
            supabase_url=settings.SUPABASE_URL,
            supabase_key=settings.SUPABASE_SERVICE_ROLE_KEY,
        )


def _reset_to_service_role(client: Client) -> Client:
    """Re-assert `service_role` on a process-wide client, once per dependency resolution.

    IT IS ALL ONE DICT. `SyncClient.__init__` hands `self.options.headers` by REFERENCE to the
    GoTrue client, which hands the same reference on to `SyncGoTrueAdminAPI`. Measured on
    supabase 2.16.0 / gotrue 2.12.4:

        options.headers is auth._headers   -> True
        auth._headers  is admin._headers   -> True

    So `_listen_to_auth_events` rewriting `Authorization` on SIGNED_IN rewrites it for
    `auth.admin.*` as well, and `SyncGoTrueBaseAPI._request` sends `{**self._headers, ...}` —
    `delete_user` / `update_user_by_id` / `get_user_by_id` pass no per-call `jwt` to override
    it. GoTrue answers a user JWT on `/admin/*` with `User not allowed`.

    Writing the SAME key the SDK's own listener writes is deliberate: it is the one place every
    sub-client reads from. `_in_memory_session` is cleared too — `persist_session=False` routes
    `_save_session` there instead of storage, so the last signer's session would otherwise stay
    readable on the next caller's request.

    THE DICT WRITE ALONE CANNOT UN-DEMOTE A REBUILT SUB-CLIENT. The SIGNED_IN listener also
    sets `_postgrest` / `_storage` / `_functions` to None, and the next `.table()` /
    `.storage` access rebuilds them from the (then user-JWT) dict — into an httpx session
    that SNAPSHOTS the headers. A later dict write repairs `auth.admin.*` and any sub-client
    not yet rebuilt, but a postgrest client built in between keeps sending the user's JWT on
    every query until restart (a process-wide 42501 since migration 163). So this also
    re-stamps the bearer on any already-built postgrest / storage session — the SDK's own
    `postgrest.auth(token)` idiom — rather than nulling them, which would rebuild an
    `http2=True` postgrest client and undo `_force_http1_on_postgrest`.

    Best-effort: a supabase-py bump that renames these internals must degrade, not take auth
    down. The structural guarantee is the separate INSTANCE; this is a self-healing layer on
    top, and `tests/test_supabase_client_isolation.py` is what actually detects misuse.
    """
    try:
        bearer = f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}"
        client.options.headers["Authorization"] = bearer
        auth = getattr(client, "auth", None)
        if getattr(auth, "_in_memory_session", None) is not None:
            auth._in_memory_session = None
        for attr in ("_postgrest", "_storage"):
            sub = getattr(client, attr, None)
            session = getattr(sub, "session", None)
            headers = getattr(session, "headers", None)
            if headers is None:
                continue
            # A sub-client rebuilt while `run_gotrue` had a caller's address in the shared dict
            # SNAPSHOTS it (the same way it snapshots a user JWT). Only GoTrue reads the header,
            # but one user's address must not ride on every later query. The sub-session's own
            # copy only — never the shared dict, which a locked GoTrue call may be using.
            headers.pop(GOTRUE_FORWARDED_FOR_HEADER, None)
            if headers.get("Authorization") == bearer:
                continue
            headers["Authorization"] = bearer
            logger.warning(
                "Supabase %s sub-client was carrying a non-service-role bearer — "
                "restored to service_role (a sign-in ran on a shared client)",
                attr.lstrip("_"),
            )
    except Exception as e:
        logger.warning(
            "Could not reset a Supabase client to service_role (%s: %s) — the isolated "
            "instance still stands, but the self-healing layer is inert",
            type(e).__name__, e,
        )
    return client


def get_auth_client() -> Client:
    """Client for the SIGN-IN half of `supabase.auth.*`. NEVER call `.table()` or `.admin.*` on it.

    supabase-py registers an auth-state listener on every client it builds. On a successful
    sign-in that listener REWRITES `options.headers["Authorization"]` with the signing-in USER's
    JWT and sets `_postgrest` to None, so the next `.table()` call rebuilds postgrest carrying
    that user's token. Verified directly against the installed SDK:

        before      : Bearer SERVICE_ROLE_FAKE
        after       : Bearer USER_A_JWT
        _postgrest reset to None: True

    On the shared service-role singleton that is a process-wide privilege demotion. Every
    subsequent database read runs as that ONE user under RLS instead of service_role — and
    nothing restored it. Concretely: user A signs in, and user B's next request has
    `select("*") from users where id = B` return ZERO rows (RLS `users_select_own` is
    `auth.uid() = id`, and uid is now A). `get_current_user` reads that as a dead session and
    401s, so B — who did nothing — is silently demoted to the guest identity and the app renders
    "Guest" with no name or email.

    MORE CALLS DEMOTE THAN THE OBVIOUS THREE. `gotrue_client.py` emits SIGNED_IN from
    `sign_in_with_password`, `sign_in_with_id_token`, `sign_up`, `verify_otp`,
    `exchange_code_for_session`, `sign_in_anonymously` and `_recover_and_refresh`, and
    TOKEN_REFRESHED from `set_session` / `_call_refresh_token`. All of them rewrite the header.

    ADMIN CALLS DO NOT BELONG HERE — use `get_admin_client()`. `auth.admin.*` shares this
    client's headers dict (see `_reset_to_service_role`), so a sign-in demotes it too, and
    GoTrue rejects `/admin/*` under a user JWT with `User not allowed`. That is what broke
    account deletion, change-password and reset-password in production.

    `_reset_to_service_role` runs on every resolution so a request always starts from
    service_role and one caller's (possibly expired) JWT never rides along on the next
    caller's sign-in.
    """
    global _auth_client
    if _auth_client is None:
        _auth_client = _new_isolated_client("AUTH")
    return _reset_to_service_role(_auth_client)


def get_admin_client() -> Client:
    """Client used ONLY for `supabase.auth.admin.*`. NEVER sign in on it. NEVER call `.table()`.

    The third client, and the reason is narrow: `auth.admin.*` authenticates with whatever sits
    in the shared headers dict, and every sign-in verb rewrites that dict (see
    `get_auth_client`). Sharing one client between sign-ins and admin calls meant a sign-in
    anywhere in the process — or, for change-password and reset-password, the sign-in/OTP
    check EARLIER IN THE SAME REQUEST — left `admin.delete_user` / `admin.update_user_by_id`
    running as that user, which GoTrue refuses with `User not allowed`.

    Nothing ever signs in here, so nothing ever rewrites its header. `_reset_to_service_role`
    on each resolution makes that self-healing rather than merely conventional, and
    `tests/test_supabase_client_isolation.py` fails the build if a sign-in verb or a `.table()`
    call appears on this client.
    """
    global _admin_client
    if _admin_client is None:
        _admin_client = _new_isolated_client("ADMIN")
    return _reset_to_service_role(_admin_client)


# One lock per event loop. GoTrue verbs used to run ON the loop precisely because the loop
# serialised them: supabase-py's auth-state listener rewrites the process-wide client's
# shared `Authorization` header on every sign-in, so two sign-ins interleaving in threads
# would be the cross-user demotion `get_auth_client` documents. The lock keeps that
# serialisation while the verb itself runs in a worker thread — so a flood of wrong-password
# logins (a bcrypt round trip each, ~0.4-0.9 s) queues LOGINS behind each other instead of
# stalling every chat stream, report poll and credit read in the process.
_GOTRUE_LOCK: Optional[asyncio.Lock] = None
_GOTRUE_LOCK_LOOP: Any = None


def _gotrue_lock() -> asyncio.Lock:
    global _GOTRUE_LOCK, _GOTRUE_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _GOTRUE_LOCK is None or _GOTRUE_LOCK_LOOP is not loop:
        _GOTRUE_LOCK, _GOTRUE_LOCK_LOOP = asyncio.Lock(), loop
    return _GOTRUE_LOCK


# ── The end user's address, forwarded to Supabase Auth (SEC-7 / AUTH-BE-09) ──────────────────
#
# Every GoTrue call leaves from the Railway container, so GoTrue's per-IP limits (sign-ups and
# sign-ins 30 / 5 min, `verify` 30 / 5 min, `token` 150 / 5 min by default) were ONE bucket for
# every user of the app: a handful of junk logins from one attacker address answered every real
# user's sign-in with a 429 → 503 AUTH_UNAVAILABLE. Supabase documents a fix for exactly this
# shape ("Rate limits" guide, § Sb-Forwarded-For): a server sends the end user's address in
# `Sb-Forwarded-For`, and Auth keys its rate limits on it. Three conditions, all from those docs:
#   1. the request must be made with a SECRET API key (`sb_secret_…`) — publishable and legacy
#      anon / service_role JWT keys are not honoured. `SUPABASE_SERVICE_ROLE_KEY` has held an
#      `sb_secret_` key since the 2026-08-15 key rotation;
#   2. the project must have IP Address Forwarding ENABLED (Auth → Rate limits, or the
#      Management API's `security_sb_forwarded_for_enabled`) — an OWNER action. Until then
#      GoTrue ignores the header, so sending it is harmless;
#   3. Auth uses the FIRST value and falls back to the connection address on an invalid one —
#      so only one normalised address is ever sent.
# The address is `core.security.trusted_client_ip` (the rightmost X-Forwarded-For entry, the one
# Railway's edge appended — never a caller-chosen value), bound per request by the FastAPI
# dependency `dependencies.forward_client_ip_to_gotrue`. Outside a request (a job, a script)
# nothing is bound and no header is sent.
GOTRUE_FORWARDED_FOR_HEADER = "Sb-Forwarded-For"
_gotrue_client_ip: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "gotrue_client_ip", default=None
)


def normalise_client_ip(value: Any) -> Optional[str]:
    """`value` as a canonical IP address string, or None when it is not exactly one address.

    Validating is what keeps the header injection-proof (no CR/LF, no commas, no second value)
    and keeps GoTrue from silently falling back on a malformed one. An IPv4-mapped IPv6 address
    is unwrapped to its IPv4 form so one client never has two buckets.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    if not candidate or len(candidate) > 64:
        return None
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    if address.is_unspecified:
        return None
    return str(address)


def bind_gotrue_client_ip(value: Any) -> Optional[str]:
    """Bind the end user's address for the GoTrue calls of THIS request (this asyncio task).

    Returns what was bound (None when `value` is not a valid address — nothing is forwarded
    then). A contextvar, not a module global: every request runs in its own task with its own
    copy of the context, so one request's address can never ride on another's call.
    """
    address = normalise_client_ip(value)
    if address is None and value not in (None, "", "unknown"):
        logger.warning(
            "run_gotrue: the caller's address is not a valid IP — GoTrue will rate-limit this "
            "request on the backend's egress address instead",
        )
    _gotrue_client_ip.set(address)
    return address


def _is_admin_api(owner: Any) -> bool:
    """`auth.admin.*` verbs get no forwarded address.

    GoTrue does not rate-limit `/admin/*` by IP, so there is nothing to gain — and the admin
    client's headers dict is also read OUTSIDE `run_gotrue` (`users.py`'s account deletion calls
    `admin.delete_user` directly), where a header left in it could ride on another user's call.
    """
    return "admin" in type(owner).__name__.lower()


async def _await_holding_lock(work: "asyncio.Future[Any]") -> Any:
    """Await the worker thread to completion even if the awaiting task is cancelled.

    `asyncio.to_thread` cannot stop a running thread: cancelling the awaiting task used to
    return control — and release `_GOTRUE_LOCK` — while the verb was still running. The next
    caller then rewrote the shared headers dict (its `Authorization` reset, its forwarded
    address) under a request that had not been sent yet. Holding the lock until the thread
    returns is what makes "the lock spans the call" true; the cancellation is re-raised after.
    """
    cancelled: Optional[BaseException] = None
    while not work.done():
        try:
            await asyncio.shield(work)
        except asyncio.CancelledError as e:
            if work.done():
                cancelled = cancelled or e
                break
            cancelled = e
            continue
        except BaseException:  # noqa: BLE001 — the verb's own error; `work.result()` re-raises it
            if not work.done():
                raise  # KeyboardInterrupt / SystemExit on the awaiting side: the process is going down
            break
    if cancelled is not None:
        if not work.cancelled():
            work.exception()  # mark the worker's outcome retrieved; the task is still cancelled
        raise cancelled
    return work.result()


async def run_gotrue(verb: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a synchronous GoTrue verb OFF the event loop, serialised process-wide.

    `verb` is the BOUND method on the resolved client — written at the call site as
    `_auth_of(auth_client, supabase).auth.sign_in_with_password` or
    `resolve_admin_client(...).auth.admin.update_user_by_id` — so the source-scan guards in
    tests/test_supabase_client_isolation.py keep seeing the resolver they grep for.

    The service-role header is re-asserted INSIDE the lock, right before the verb: the reset
    `get_auth_client` performs at dependency resolution is not enough once the verb no longer
    runs in the same loop turn, because a sign-in that completed in between rewrote the
    shared dict. Best-effort on the owner's internals, like `_reset_to_service_role`; a test
    fake without them is simply run.

    The end user's address (`bind_gotrue_client_ip`) is written into that same dict as
    `Sb-Forwarded-For` for a non-admin verb, and ALWAYS removed again in `finally`. That is
    race-free only because the lock spans the whole call — the SDK reads the dict when it
    builds the request, inside the worker thread — which `_await_holding_lock` keeps true even
    when the awaiting request is cancelled mid-call.
    """
    # Sentry keeps the end user's address out of events BY NAME: `client_ip` and the header name
    # are on `log_redaction`'s PII key list and the SDK scrubber's `pii_denylist` (TOKSEC-2) —
    # rename either one there too (tests/test_log_redaction.py pins the pairing).
    client_ip = _gotrue_client_ip.get()
    async with _gotrue_lock():
        owner = getattr(verb, "__self__", None)
        headers = getattr(owner, "_headers", None)
        if not isinstance(headers, dict):
            headers = None
        try:
            if headers is not None:
                headers["Authorization"] = f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}"
                # Never inherit an address a crashed call left behind.
                headers.pop(GOTRUE_FORWARDED_FOR_HEADER, None)
                if client_ip and not _is_admin_api(owner):
                    headers[GOTRUE_FORWARDED_FOR_HEADER] = client_ip
            if getattr(owner, "_in_memory_session", None) is not None:
                owner._in_memory_session = None
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("run_gotrue: could not reset the auth client to service_role "
                           "(%s: %s)", type(e).__name__, e)
        try:
            work = asyncio.ensure_future(asyncio.to_thread(verb, *args, **kwargs))
            return await _await_holding_lock(work)
        finally:
            if headers is not None:
                headers.pop(GOTRUE_FORWARDED_FOR_HEADER, None)


def resolve_admin_client(*candidates) -> Client:
    """The client to run `auth.admin.*` on: the first candidate that is really a client.

    In production that is the injected `get_admin_client()`. The fallbacks exist because the
    suite calls handlers DIRECTLY as Python functions (there is no TestClient anywhere in
    `backend/tests`), so an un-injected parameter is still a FastAPI `Depends(...)` sentinel and
    must fall through to the fake the test did pass. Same accommodation, and same reasoning, as
    `api.v1.endpoints.auth._auth_of`.

    Deliberately ONE name in ONE module: it is the token the source-scan guard greps for, so
    every `auth.admin.*` call site is checkable with a single rule.
    """
    for candidate in candidates:
        if hasattr(candidate, "auth"):
            return candidate
    return candidates[-1]


# One persistent client for the readiness probe. `/health` is unauthenticated and
# reachable by anyone; it used to open a fresh `httpx.AsyncClient` (a new TLS handshake to
# Supabase) per hit and GET the PostgREST ROOT — which makes PostgREST build the whole
# ~556 KB OpenAPI schema document server-side on every call. A curl loop against `/health`
# was therefore a cheap amplifier against the database's API layer. The probe is now a
# HEAD on one tiny table over a shared pool, and the pool is closed in the lifespan
# teardown next to the integration clients.
_health_http: Optional[httpx.AsyncClient] = None

#: A small, always-present table for the readiness HEAD. `limit=1` bounds the scan;
#: `Prefer: count=none` keeps PostgREST from counting.
_HEALTH_PROBE_PATH = "/rest/v1/agent_personas?select=id&limit=1"


def _get_health_client() -> httpx.AsyncClient:
    global _health_http
    if _health_http is None:
        _health_http = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=3.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
    return _health_http


async def close_health_client() -> None:
    global _health_http
    if _health_http is not None:
        await _health_http.aclose()
        _health_http = None


async def check_supabase_health() -> bool:
    """Readiness: can PostgREST answer a trivial, bounded read right now?"""
    try:
        # Eagerly initialise the client singleton
        get_supabase()

        resp = await _get_health_client().head(
            f"{settings.SUPABASE_URL}{_HEALTH_PROBE_PATH}",
            headers={
                "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
                "Prefer": "count=none",
            },
        )
        # 200 (rows) or 206 (a Range-limited answer) both prove the API layer is up.
        return resp.status_code in (200, 206)
    except Exception as e:
        logger.error(f"Supabase health check failed: {type(e).__name__}: {e}")
        return False
