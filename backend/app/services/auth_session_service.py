"""Server-side app sessions: single-use refresh tokens, reuse detection, revocation (migration 214).

WHY THIS EXISTS (audit 2026-11-26: SEC-3, AUTH-BE-10)
-----------------------------------------------------
The app mints its own JWTs. Refresh tokens were stateless, carried no id, and `/auth/refresh`
re-issued a fresh 7-day one on every call without retiring the one it was given. So a copied
refresh token was a permanent session: refresh before day 7, repeat forever, through every Sign
Out the owner tapped. `/auth/logout` only logged, and an Apple/Google account (no password) had no
way to evict anyone.

THE MODEL
---------
* A **session** is one sign-in on one device: a row in `auth_refresh_families`. Its id travels in
  every token as the `sid` claim (access tokens too, so even a build that cannot send its refresh
  token to `/auth/logout` still names the session it is ending).
* Each **refresh token** is a row in `auth_refresh_tokens`, keyed by its `jti` claim, and is
  SINGLE-USE: `/auth/refresh` retires it and mints the successor in one transaction
  (`rotate_refresh_token`).
* A retired token presented AGAIN means two parties hold it. Outside a short grace window the
  whole session is revoked (`reuse`), which signs out the thief and the real device alike — the
  real one signs in again; the thief cannot. Inside the window (a lost response the app retries,
  or the app's restore and pre-flight refreshes racing on one token) a SIBLING is issued instead,
  at most `REFRESH_TOKEN_MAX_CHILDREN` per token.
* Revocation: `/auth/logout` revokes the caller's session; "Sign out of all devices" stamps
  `users.sessions_revoked_at` and revokes every session; reuse revokes one session.
* Every state change is ONE RPC, under a per-user advisory lock (see migration 214), so a
  rotation can never interleave with a revocation of the same account.

LEGACY TOKENS (minted before 214) carry no `jti`. They keep working until their own `exp` — never a
mass sign-out on deploy — but they are made single-use too: their id is DERIVED from a hash of the
token (`legacy_jti`), and the first refresh records it, so a second presentation is reuse like any
other. Their successors are ordinary tracked tokens.

FAIL OPEN OR CLOSED — decided per call, never by default
--------------------------------------------------------
* `start_session` (after a VERIFIED credential: sign-in, provider sign-in, confirm-email, password
  change) **fails OPEN** to an untracked token, after one retry, logged at ERROR. The caller just
  proved who they are, and the untracked token is a legacy-format token: its first refresh records
  it and moves the session onto a tracked one, so the gap is one access-token life of "logout cannot
  revoke this yet". Failing closed would add a way for a Supabase blip to fail a sign-in GoTrue
  already accepted — and on the password-change paths, to report an irreversible change as failed.
* `rotate` (`/auth/refresh`) **fails CLOSED**: a read or write error is `SessionStoreUnavailable`,
  which the route answers 503 AUTH_UNAVAILABLE. A refresh MINTS; "could not check" must never mean
  "allowed" (the same rule as SEC-4). The client keeps its credential on a 503 and retries.
* `revoke_session` / `revoke_all_sessions` raise on failure; the routes answer 503 so the failure is
  visible. The device has already dropped its tokens either way.
* One exception everywhere: the migration NOT APPLIED YET (`SessionSchemaMissing`). That is a
  deploy-order state no caller can induce, and failing closed on it would sign every user out of a
  backend deployed one step early. Sessions then degrade to the old stateless tokens, logged at
  ERROR (throttled) until 214 lands.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.config import settings
from app.utils.supabase_errors import is_unique_violation

logger = logging.getLogger(__name__)

#: The access/refresh token claim carrying the session (family) id.
SESSION_CLAIM = "sid"
#: Unix seconds of the credential verification that started the session (OIDC `auth_time`).
AUTH_TIME_CLAIM = "auth_time"

# Fixed namespace for `legacy_jti`. Random once, constant forever: changing it would make every
# legacy token look unused again.
_LEGACY_NAMESPACE = uuid.UUID("b2f0c6e4-3a59-4d17-8e2b-6c1f0a9d4e73")

#: PostgREST's answer when the RPC itself is not in its schema cache: "migration 214 is not applied".
#: Deliberately ONLY these two (review 2026-11-26). 42883 / 42P01 / 42703 can also come from
#: INSIDE a function body, and a column-cache miss is not a missing function: reading any of them
#: as "not applied" would quietly turn single use, reuse detection and logout off for everyone.
#: PGRST202 also answers an argument-name drift (Python deployed ahead of a changed migration);
#: that is why a TRACKED token never degrades — see `rotate` and `revoke_session`.
_SCHEMA_MISSING_CODES = frozenset({"PGRST202", "PGRST205"})
#: A missing COLUMN (`users.sessions_revoked_at` before 214): PostgREST's cache miss and Postgres's
#: undefined_column, as `app.utils.supabase_errors.UNKNOWN_COLUMN_CODES`.
_COLUMN_MISSING_CODES = frozenset({"PGRST204", "42703"})

#: The ERROR for a missing migration fires at most this often per process; WARNING otherwise.
#: Every refresh would otherwise raise one Sentry event while the migration is unapplied.
_SCHEMA_MISSING_LOG_EVERY_SECONDS = 600.0
_last_schema_missing_error = 0.0

#: The retry before `start_session` falls back to an untracked token.
_START_RETRY_DELAY_SECONDS = 0.25


class SessionStoreUnavailable(Exception):
    """A session read or write failed: the caller could not be checked. Never "allowed"."""


class SessionSchemaMissing(SessionStoreUnavailable):
    """Migration 214 is not applied on this database (see the module docstring)."""


@dataclass(frozen=True)
class IssuedSession:
    """What `start_session` minted. `sid` / `jti` are None for an untracked (fallback) token."""
    access_token: str
    refresh_token: str
    sid: Optional[str]
    jti: Optional[str]
    auth_time: Optional[int]


@dataclass(frozen=True)
class Rotation:
    """`rotate`'s answer. `status` is one of migration 214's statuses, or `degraded` when the
    migration is not applied (the caller then mints untracked tokens, as before 214)."""
    status: str
    new_jti: str
    new_expires_at: datetime
    family_id: Optional[str] = None
    auth_time: Optional[int] = None
    reason: Optional[str] = None
    age_seconds: Optional[float] = None
    child_count: Optional[int] = None
    legacy: bool = False


# ── helpers ─────────────────────────────────────────────────────────────────

def _codes_in_chain(exc: BaseException, depth: int = 5) -> list[str]:
    codes: list[str] = []
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and depth > 0 and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "code", None)
        # A str code is a SQLSTATE / PGRST code; an int code is an HTTP status (transient).
        if isinstance(code, str):
            codes.append(code.strip().upper())
        current = current.__cause__ or current.__context__
        depth -= 1
    return codes


def is_schema_missing(exc: BaseException) -> bool:
    """True when `exc` says a 214 RPC is not in PostgREST's schema cache (the migration is not
    applied, or its signature drifted).

    Keyed on the structured `code` only — never on message text, which would let a transient
    failure whose message happens to say "not found" read as a missing migration.
    """
    return any(code in _SCHEMA_MISSING_CODES for code in _codes_in_chain(exc))


def is_column_missing(exc: BaseException) -> bool:
    """True when a SELECT named a column the database does not have yet (`sessions_revoked_at`)."""
    return any(code in _COLUMN_MISSING_CODES for code in _codes_in_chain(exc))


def _log_schema_missing(operation: str, exc: BaseException, **ids: Any) -> None:
    global _last_schema_missing_error
    now = time.monotonic()
    context = " ".join(f"{k}={v}" for k, v in ids.items())
    if now - _last_schema_missing_error >= _SCHEMA_MISSING_LOG_EVERY_SECONDS:
        _last_schema_missing_error = now
        logger.error(
            "auth sessions: migration 214 is NOT applied (%s: %s: %s) %s — sessions are "
            "stateless and NOT revocable until it is; apply "
            "backend/database/migrations/214_auth_refresh_tokens.sql",
            operation, type(exc).__name__, exc, context,
        )
    else:
        logger.warning(
            "auth sessions: migration 214 still not applied (%s) %s", operation, context,
        )


def legacy_jti(raw_token: str) -> str:
    """The id a pre-214 refresh token is recorded under: a UUID5 of the SHA-256 of its SIGNING
    INPUT (`header.payload`), never of the whole string.

    The SIGNATURE segment is malleable: python-jose accepts it with `=` padding appended, and with
    the unused low bits of its last base64url character flipped (review 2026-11-26: one token gave
    six "first uses" and six live sessions that way). The signing input is not — any change to it
    breaks the HMAC — so it names exactly one minted token. Deterministic, so a second
    presentation finds the row the first one wrote; a hash, so the token is never stored.
    """
    signing_input = raw_token.strip().rsplit(".", 1)[0]
    digest = hashlib.sha256(signing_input.encode("utf-8")).hexdigest()
    return str(uuid.uuid5(_LEGACY_NAMESPACE, digest))


def parse_uuid(value: Any) -> Optional[str]:
    """`value` as a canonical UUID string, or None. Claims are attacker-shaped input until the
    signature is checked, and even after it a malformed id must not reach a uuid parameter."""
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        return None


def auth_time_of(value: Any) -> Optional[int]:
    """An `auth_time` claim as whole Unix seconds, or None when absent or unusable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")) or value <= 0:
        return None
    return int(value)


def supabase_sign_in_time(payload: Any) -> Optional[int]:
    """When the user last entered a credential for this Supabase token: the latest `amr` entry's
    `timestamp`, or None.

    NOT the token's `iat`. A GoTrue refresh mints a new Supabase token with a fresh `iat` and the
    SAME `amr` (verified on a local GoTrue, 2026-11-26), so `iat` says when the token was minted,
    not when anyone signed in. Used for re-authentication freshness, for the session's `auth_time`
    on session exchange, and for the eviction comparison there.
    """
    amr = payload.get("amr") if isinstance(payload, dict) else None
    if not isinstance(amr, list):
        return None
    stamps = [auth_time_of(entry.get("timestamp")) for entry in amr if isinstance(entry, dict)]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def _rpc_data(result: Any) -> dict:
    data = getattr(result, "data", None)
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        raise SessionStoreUnavailable(f"RPC answered an unexpected shape: {type(data).__name__}")
    return data


def _claims(user_id: str, email: Optional[str], sid: Optional[str], auth_time: Optional[int]) -> dict:
    data: dict[str, Any] = {"sub": user_id, "email": email}
    if sid:
        data[SESSION_CLAIM] = sid
    if auth_time is not None:
        data[AUTH_TIME_CLAIM] = auth_time
    return data


def mint_pair(
    user_id: str,
    email: Optional[str],
    *,
    sid: Optional[str],
    jti: Optional[str],
    auth_time: Optional[int],
    refresh_expires_at: Optional[datetime] = None,
) -> tuple[str, str]:
    """Access + refresh token for one session. `sid`/`jti` None → the untracked, legacy shape."""
    from app.core.security import create_access_token, create_refresh_token

    claims = _claims(user_id, email, sid, auth_time)
    access = create_access_token(data=dict(claims))
    refresh_claims = dict(claims)
    if jti:
        refresh_claims["jti"] = jti
    refresh = create_refresh_token(data=refresh_claims, expires_at=refresh_expires_at)
    return access, refresh


def _refresh_expiry(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now(timezone.utc)) + timedelta(
        minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES
    )


# ── operations (sync: the Supabase SDK is sync — call through asyncio.to_thread) ─────────────

def start_session(
    supabase: Any,
    user_id: str,
    email: Optional[str],
    *,
    auth_time: Optional[int] = None,
    auth_time_unknown: bool = False,
) -> IssuedSession:
    """Register a new session and mint its first token pair. Fails OPEN (module docstring).

    `auth_time` defaults to now: every caller has just verified a credential. Session exchange
    passes the Supabase token's own sign-in time (`amr`) instead, or `auth_time_unknown=True` when
    the token carries none — then the session has no `auth_time` and never counts as "recent
    authentication" for account deletion.
    """
    now = datetime.now(timezone.utc)
    when: Optional[int] = (
        None if auth_time_unknown
        else auth_time if auth_time is not None
        else int(now.timestamp())
    )
    family_id = str(uuid.uuid4())
    jti = str(uuid.uuid4())
    expires_at = _refresh_expiry(now)

    for attempt in (1, 2):
        try:
            data = _rpc_data(
                supabase.rpc(
                    "start_refresh_family",
                    {
                        "p_user_id": user_id,
                        "p_family_id": family_id,
                        "p_jti": jti,
                        "p_expires_at": expires_at.isoformat(),
                        "p_auth_time": (
                            datetime.fromtimestamp(when, tz=timezone.utc).isoformat()
                            if when is not None else None
                        ),
                    },
                ).execute()
            )
            status = data.get("status")
            if status == "started":
                access, refresh = mint_pair(
                    user_id, email, sid=family_id, jti=jti, auth_time=when,
                    refresh_expires_at=expires_at,
                )
                return IssuedSession(access, refresh, family_id, jti, when)
            # `account_missing`: the sign-in paths already decided what a missing public.users
            # row means (login falls back to the auth id). Not a store failure: no retry.
            logger.warning(
                "auth sessions: start_refresh_family answered %r for user=%s — issuing an "
                "untracked token", status, user_id,
            )
            break
        except Exception as e:  # noqa: BLE001 — classified below; fail OPEN by design
            if attempt == 2 and is_unique_violation(e):
                # Our ids are fresh uuid4s, so only OUR first attempt can have inserted them: it
                # committed and its answer was lost. The session exists — use it.
                logger.warning(
                    "auth sessions: start_refresh_family retry for user=%s found the first "
                    "attempt committed (its answer was lost) — using that session", user_id,
                )
                access, refresh = mint_pair(
                    user_id, email, sid=family_id, jti=jti, auth_time=when,
                    refresh_expires_at=expires_at,
                )
                return IssuedSession(access, refresh, family_id, jti, when)
            if is_schema_missing(e):
                _log_schema_missing("start_refresh_family", e, user=user_id)
                break
            if attempt == 1:
                logger.warning(
                    "auth sessions: start_refresh_family failed for user=%s (%s: %s) — retrying "
                    "once", user_id, type(e).__name__, e,
                )
                time.sleep(_START_RETRY_DELAY_SECONDS)
                continue
            logger.error(
                "auth sessions: start_refresh_family failed twice for user=%s (%s: %s) — "
                "issuing an UNTRACKED token (logout cannot revoke it until its first refresh "
                "records it)", user_id, type(e).__name__, e, exc_info=True,
            )

    access, refresh = mint_pair(user_id, email, sid=None, jti=None, auth_time=when)
    return IssuedSession(access, refresh, None, None, when)


def rotate(supabase: Any, user_id: str, payload: dict, raw_token: str) -> Rotation:
    """Retire the presented refresh token and reserve its successor. Fails CLOSED.

    Raises `SessionStoreUnavailable` on any failure except a missing migration, which answers
    `Rotation(status="degraded")`. A tracked token whose `jti` is not a UUID raises ValueError
    (the route answers AUTH_TOKEN_INVALID): it was signed by us, so it is a bug, not a client.
    """
    claimed = payload.get("jti")
    legacy = claimed is None
    if legacy:
        jti = legacy_jti(raw_token)
    else:
        jti = parse_uuid(claimed)
        if jti is None:
            raise ValueError("refresh token jti is not a UUID")

    iat = payload.get("iat")
    exp = payload.get("exp")
    if isinstance(iat, bool) or not isinstance(iat, (int, float)):
        raise ValueError("refresh token has no numeric iat")
    exp_int = int(exp) if isinstance(exp, (int, float)) and not isinstance(exp, bool) else None

    now = datetime.now(timezone.utc)
    new_jti = str(uuid.uuid4())
    new_expires_at = _refresh_expiry(now)
    try:
        data = _rpc_data(
            supabase.rpc(
                "rotate_refresh_token",
                {
                    "p_user_id": user_id,
                    "p_jti": jti,
                    "p_token_iat": int(iat),
                    "p_token_exp": exp_int,
                    "p_legacy": legacy,
                    "p_new_jti": new_jti,
                    "p_new_family_id": str(uuid.uuid4()),
                    "p_new_expires_at": new_expires_at.isoformat(),
                    "p_grace_seconds": int(settings.REFRESH_TOKEN_REUSE_GRACE_SECONDS),
                    "p_max_children": int(settings.REFRESH_TOKEN_MAX_CHILDREN),
                },
            ).execute()
        )
    except SessionStoreUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 — fail CLOSED, except the deploy-order case
        if is_schema_missing(e) and legacy:
            _log_schema_missing("rotate_refresh_token", e, user=user_id)
            return Rotation(status="degraded", new_jti=new_jti, new_expires_at=new_expires_at,
                            legacy=legacy)
        if is_schema_missing(e):
            # A token WITH a jti was minted by a live 214, so "the function is missing" now is a
            # rollback or a signature drift — never a reason to mint stateless tokens for it.
            logger.error(
                "auth sessions: rotate_refresh_token is missing (%s) but user=%s presented a "
                "TRACKED token — refusing (fail closed); check migration 214 / the deployed "
                "signature", type(e).__name__, user_id,
            )
            raise SessionStoreUnavailable(f"{type(e).__name__}: {e}") from e
        logger.error(
            "auth sessions: rotate_refresh_token failed for user=%s (%s: %s) — refusing to mint "
            "(fail closed)", user_id, type(e).__name__, e,
        )
        raise SessionStoreUnavailable(f"{type(e).__name__}: {e}") from e

    status = data.get("status")
    if not isinstance(status, str):
        raise SessionStoreUnavailable(f"rotate_refresh_token answered no status: {data!r}"[:300])
    family = parse_uuid(data.get("family_id"))
    age = data.get("age_seconds")
    children = data.get("child_count")
    return Rotation(
        status=status,
        new_jti=new_jti,
        new_expires_at=new_expires_at,
        family_id=family,
        auth_time=auth_time_of(data.get("auth_time")),
        reason=data.get("reason") if isinstance(data.get("reason"), str) else None,
        age_seconds=float(age) if isinstance(age, (int, float)) and not isinstance(age, bool) else None,
        child_count=int(children) if isinstance(children, int) and not isinstance(children, bool) else None,
        legacy=legacy or bool(data.get("legacy") is True),
    )


def revoke_session(supabase: Any, user_id: str, sid: str, *, reason: str = "logout") -> str:
    """End one session. Returns the RPC's status (`revoked`, `already_revoked`, `unknown`).

    Raises `SessionStoreUnavailable` on ANY failure, a missing function included: a `sid` claim
    was minted by a live 214, so its function being gone is a rollback or a drift, and a logout
    must not answer 200 for a session it could not end (review 2026-11-26)."""
    try:
        data = _rpc_data(
            supabase.rpc(
                "revoke_refresh_family",
                {"p_user_id": user_id, "p_family_id": sid, "p_reason": reason},
            ).execute()
        )
    except SessionStoreUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        if is_schema_missing(e):
            _log_schema_missing("revoke_refresh_family", e, user=user_id, sid=sid)
        raise SessionStoreUnavailable(f"{type(e).__name__}: {e}") from e
    status = data.get("status")
    return status if isinstance(status, str) else "unknown"


def revoke_all_sessions(supabase: Any, user_id: str) -> dict:
    """Stamp `users.sessions_revoked_at` and revoke every session of the account.

    Raises `SessionSchemaMissing` when 214 is not applied (there is no stamp to write, so "signed
    out everywhere" would be a lie) and `SessionStoreUnavailable` on any other failure.
    """
    try:
        return _rpc_data(
            supabase.rpc("revoke_all_refresh_sessions", {"p_user_id": user_id}).execute()
        )
    except SessionStoreUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        if is_schema_missing(e):
            _log_schema_missing("revoke_all_refresh_sessions", e, user=user_id)
            raise SessionSchemaMissing(f"{type(e).__name__}: {e}") from e
        raise SessionStoreUnavailable(f"{type(e).__name__}: {e}") from e


def account_has_identity(supabase: Any, user_id: str, provider: str, subject: str) -> bool:
    """Is this provider subject already linked to this account? Fails CLOSED (raises)."""
    try:
        result = supabase.rpc(
            "account_has_identity",
            {"p_user_id": user_id, "p_provider": provider, "p_subject": subject},
        ).execute()
    except Exception as e:  # noqa: BLE001
        if is_schema_missing(e):
            _log_schema_missing("account_has_identity", e, user=user_id)
            raise SessionSchemaMissing(f"{type(e).__name__}: {e}") from e
        raise SessionStoreUnavailable(f"{type(e).__name__}: {e}") from e
    data = getattr(result, "data", None)
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if isinstance(data, dict) and len(data) == 1:
        data = next(iter(data.values()))
    if not isinstance(data, bool):
        raise SessionStoreUnavailable(
            f"account_has_identity answered an unexpected shape: {type(data).__name__}"
        )
    return data
