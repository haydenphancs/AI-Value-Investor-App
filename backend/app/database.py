"""
Database Connection - Supabase Only
No SQLAlchemy. Uses Supabase Python client for all DB operations.
"""

from typing import Optional
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
    return _supabase_client


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

    Best-effort: a supabase-py bump that renames these internals must degrade, not take auth
    down. The structural guarantee is the separate INSTANCE; this is a self-healing layer on
    top, and `tests/test_supabase_client_isolation.py` is what actually detects misuse.
    """
    try:
        client.options.headers["Authorization"] = (
            f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}"
        )
        auth = getattr(client, "auth", None)
        if getattr(auth, "_in_memory_session", None) is not None:
            auth._in_memory_session = None
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


async def check_supabase_health() -> bool:
    """Check Supabase connection health via the PostgREST root endpoint."""
    try:
        import httpx

        # Eagerly initialise the client singleton
        get_supabase()

        # Hit the PostgREST schema endpoint — no table permissions needed
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                f"{settings.SUPABASE_URL}/rest/v1/",
                headers={
                    "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
                    "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
                },
                timeout=5.0,
            )
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"Supabase health check failed: {e}")
        return False
