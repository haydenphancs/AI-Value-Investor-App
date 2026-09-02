"""How an account signs in: does it have a password, and via which providers.

Why this exists. An Apple/Google account is provisioned by Supabase through
`sign_in_with_id_token` and never has a password written for it, so
`auth.users.encrypted_password` is NULL. Nothing on either side of the wire knew that: the
provider string is a transient argument on the inbound `POST /auth/oauth` body and is never
persisted, and `public.users` has no provider column. So `/auth/change-password` — which proves
the current password by attempting a real sign-in — told those users **"Your current password is
incorrect"** about a password that has never existed (reported from TestFlight), and iOS had no
way to hide or relabel the affordance.

The truth lives in the `auth` schema, which PostgREST does not expose, so this reads it through
the `account_auth_methods` SECURITY DEFINER function (migration 156) — one round trip,
service-role only.

Tier 1 ONLY, deliberately. The canonical two-tier pattern (CLAUDE.md invariant #4) puts a
Supabase `*_cache` table behind the in-process dict, but here Supabase *is* the upstream: a
cache table would just be a slower copy of the same row. The in-process dict exists solely to
keep the extra round trip off `GET /users/me`, which runs on every session restore (launch,
foreground, network-path-restored) and is therefore on the launch critical path.

⚠️ `invalidate()` is not optional. Every write that can flip `has_password` must call it, or a
user who has just set a password keeps being offered "Set a Password" for up to the TTL.
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Short on purpose. This answers a question that changes at most a handful of times in an
# account's life, but a stale FALSE is user-visible (the wrong settings row), so the window is
# kept small enough to self-heal even if an `invalidate()` call site is ever missed.
_TTL_SECONDS = 60

# The cache is keyed by user id and nothing else ever removes an entry, so on a long-lived
# Railway process it would grow once per distinct signed-in user, forever. Not attacker-driven —
# the key is a verified JWT subject, not a client-chosen header like the one that forced the
# shared `RateLimiter` to become bounded — but it is still an unbounded structure on the
# `GET /users/me` path, which every session restore hits. Sweeping expired entries first means
# the hard eviction below almost never fires in practice.
_MAX_ENTRIES = 5_000


class AuthMethodsService:
    """Cache-aside reader for `public.account_auth_methods`."""

    def __init__(self) -> None:
        self._cache: Dict[str, tuple[float, Optional[Dict[str, Any]]]] = {}
        self._inflight: Dict[str, asyncio.Future] = {}

    def invalidate(self, user_id: str) -> None:
        """Drop the cached answer for one user. Call after ANY password write."""
        self._cache.pop(user_id, None)

    def _evict_if_needed(self) -> None:
        """Keep the cache bounded. Expired entries first, then oldest-first if still over."""
        if len(self._cache) <= _MAX_ENTRIES:
            return
        now = time.time()
        for key in [k for k, (ts, _) in self._cache.items() if now - ts >= _TTL_SECONDS]:
            self._cache.pop(key, None)
        if len(self._cache) <= _MAX_ENTRIES:
            return
        # Everything is still live: drop the oldest quarter rather than one entry per call, so
        # a process at the ceiling does not pay a sort on every single read.
        for key, _ in sorted(self._cache.items(), key=lambda kv: kv[1][0])[:_MAX_ENTRIES // 4]:
            self._cache.pop(key, None)

    async def get(self, supabase, user_id: str) -> Optional[Dict[str, Any]]:
        """Return `{"has_password": bool, "providers": [str]}`, or None if unknown.

        None means "we could not determine it" and covers three distinct cases — no
        `auth.users` row, an RPC/transport failure, and a deployment where migration 156 has
        not been applied yet. It is deliberately NOT `has_password: False`: the two callers
        need opposite handling. `GET /users/me` fails OPEN on None (the client keeps today's
        behaviour); `/auth/set-password` fails CLOSED, because writing a password without
        confirming that none exists would overwrite an existing one with no proof of the
        current — the exact attack `/auth/change-password` guards against.
        """
        cached = self._cache.get(user_id)
        if cached is not None and time.time() - cached[0] < _TTL_SECONDS:
            return cached[1]

        inflight = self._inflight.get(user_id)
        if inflight is not None:
            # SHIELDED. Awaiting the shared future directly would propagate THIS joiner's
            # cancellation into the future itself, so the leader's later `set_result` raises
            # `InvalidStateError` and every other joiner gets a CancelledError it never asked
            # for. `profit_power_service.py` is the reference; pinned by
            # `tests/test_inflight_cancellation_safety.py`.
            return await asyncio.shield(inflight)

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._inflight[user_id] = future
        methods: Optional[Dict[str, Any]] = None
        try:
            methods = await self._fetch(supabase, user_id)
            # A `None` here is a real answer (no `auth.users` row) and is cached like any
            # other; the failure path below caches separately.
            self._cache[user_id] = (time.time(), methods)
            self._evict_if_needed()
            return methods
        except Exception as e:  # noqa: BLE001 — the whole point is that this never raises
            methods = None
            # Cache the FAILURE too, for the same TTL. Measured against production before
            # migration 156 was applied: without this, every `GET /users/me` re-hit PostgREST
            # for a function that does not exist yet — one guaranteed-failing round trip on the
            # launch critical path, on every session restore, plus a warning per call. Safe to
            # cache because unknown fails OPEN at both call sites: for 60 s the app simply
            # behaves exactly as it did before this feature existed.
            self._cache[user_id] = (time.time(), None)
            self._evict_if_needed()
            logger.warning(
                "[AuthMethods] probe failed for user=%s: %s — reporting unknown",
                user_id, f"{type(e).__name__}: {e}",
            )
            return None
        finally:
            self._inflight.pop(user_id, None)
            # Resolve on EVERY exit, cancellation included. `CancelledError` is a
            # `BaseException`, so it skips `except Exception` entirely — a `finally` that only
            # popped the dict would stop NEW joiners while leaving everyone already parked on
            # the shared future hanging for the life of the process.
            if not future.done():
                future.set_result(methods)

    async def _fetch(self, supabase, user_id: str) -> Optional[Dict[str, Any]]:
        result = await asyncio.to_thread(
            lambda: supabase.rpc(
                "account_auth_methods", {"p_user_id": user_id}
            ).execute()
        )
        data = getattr(result, "data", None)
        if not isinstance(data, dict):
            # The function returns SQL NULL for an unknown account, and PostgREST hands that
            # back as None. Anything else is a shape we do not understand — treat both as
            # unknown rather than guessing.
            if data is not None:
                logger.warning(
                    "[AuthMethods] unexpected RPC shape for user=%s: %s",
                    user_id, type(data).__name__,
                )
            return None

        has_password = data.get("has_password")
        if not isinstance(has_password, bool):
            logger.warning(
                "[AuthMethods] RPC returned a non-bool has_password for user=%s", user_id
            )
            return None

        providers = data.get("providers")
        if not isinstance(providers, list):
            providers = []
        return {
            "has_password": has_password,
            "providers": [p for p in providers if isinstance(p, str)],
        }


auth_methods_service = AuthMethodsService()
