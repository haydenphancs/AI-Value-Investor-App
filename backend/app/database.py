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


def get_auth_client() -> Client:
    """Client used ONLY for `supabase.auth.*` calls. NEVER call `.table()` on it.

    supabase-py registers an auth-state listener on every client it builds. On a successful
    `sign_in_with_password` / `sign_in_with_id_token` / `verify_otp`, that listener REWRITES
    `options.headers["Authorization"]` with the signing-in USER's JWT and sets `_postgrest`
    to None, so the next `.table()` call rebuilds postgrest carrying that user's token.

    On the shared service-role singleton that is a process-wide privilege demotion. Verified
    directly against the installed SDK:

        before      : Bearer SERVICE_ROLE_FAKE
        after       : Bearer USER_A_JWT
        _postgrest reset to None: True

    Every subsequent database read in the process then runs as that ONE user under RLS instead
    of service_role — and nothing anywhere restored it. Concretely: user A signs in, and user
    B's next request has `select("*") from users where id = B` return ZERO rows (RLS
    `users_select_own` is `auth.uid() = id`, and uid is now A). `get_current_user` reads that
    as a dead session and 401s, so B — who did nothing — is silently demoted to the guest
    identity and the app renders "Guest" with no name or email.

    Confining auth calls to this second client keeps `get_supabase()` service_role forever.
    `persist_session` / `auto_refresh_token` are off so the SDK never carries one caller's
    session (or a background refresh thread) into another request.
    """
    global _auth_client
    if _auth_client is None:
        logger.info("Initializing Supabase AUTH client (isolated from the service-role client)")
        try:
            from supabase.lib.client_options import ClientOptions  # noqa: PLC0415

            _auth_client = create_client(
                supabase_url=settings.SUPABASE_URL,
                supabase_key=settings.SUPABASE_SERVICE_ROLE_KEY,
                options=ClientOptions(persist_session=False, auto_refresh_token=False),
            )
        except Exception as e:
            # Options are an optimisation, not the isolation itself — a separate client
            # instance is what keeps the service-role one clean. Degrade rather than fail.
            logger.warning(
                "Could not build the auth client with ClientOptions (%s: %s) — "
                "falling back to a plain isolated client",
                type(e).__name__, e,
            )
            _auth_client = create_client(
                supabase_url=settings.SUPABASE_URL,
                supabase_key=settings.SUPABASE_SERVICE_ROLE_KEY,
            )
    return _auth_client


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
