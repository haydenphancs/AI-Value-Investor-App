"""An in-memory model of migration 214's session RPCs, for the hermetic suite.

Not a test module (no `test_` prefix, like `_price_fakes.py`). Every refresh/sign-in/logout test
that needs the session store uses THIS model, so they all exercise the same state machine.

It mirrors `backend/database/migrations/214_auth_refresh_tokens.sql` statement by statement:
the statuses, the whole-second eviction comparison, the grace window and the sibling cap, the
legacy first-use branch, revocation scoped to the caller, the prune. It cannot prove the SQL
itself is right — `scripts/` E2E against a local Supabase does that (SYSTEM_DESIGN_GUIDELINES §9)
— but a divergence between the two is a bug in this file, never a reason to relax a test.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Optional


class PgError(Exception):
    """A PostgREST/Postgres error with a str `code`, as postgrest-py raises it."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def _ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class _Call:
    def __init__(self, fn: Callable[[], Any]) -> None:
        self._fn = fn

    def execute(self):
        return SimpleNamespace(data=self._fn())


class SessionStoreFake:
    """`supabase.rpc(...)` for the 214 functions, over plain dicts."""

    def __init__(self, users: Optional[dict] = None, clock: Optional[Callable[[], datetime]] = None):
        #: user_id -> row dict holding `password_changed_at` / `sessions_revoked_at` (ISO or None).
        self.users: dict = users if users is not None else {}
        self.families: dict[str, dict] = {}
        self.tokens: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        #: RPC names that raise a transient error, or "*" for all of them.
        self.fail: set[str] = set()
        #: True → every RPC raises PGRST202 (migration 214 not applied).
        self.missing = False
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ── plumbing ──────────────────────────────────────────────────────────────
    def now(self) -> datetime:
        return self._clock()

    def rpc(self, name: str, params: dict):
        def run():
            self.calls.append((name, dict(params)))
            if self.missing:
                raise PgError("PGRST202", f"Could not find the function public.{name}")
            if name in self.fail or "*" in self.fail:
                raise RuntimeError(f"{name}: upstream connection reset")
            handler = getattr(self, "_" + name, None)
            if handler is None:
                raise PgError("PGRST202", f"Could not find the function public.{name}")
            return handler(**params)
        return _Call(run)

    # ── helpers mirroring the SQL ────────────────────────────────────────────
    def _prune(self, user_id: str) -> None:
        cutoff = self.now() - timedelta(days=1)
        for jti in [j for j, t in self.tokens.items()
                    if t["user_id"] == user_id and t["expires_at"] < cutoff]:
            del self.tokens[jti]
        for fid in [f for f, fam in self.families.items()
                    if fam["user_id"] == user_id and fam["created_at"] < cutoff
                    and not any(t["family_id"] == f for t in self.tokens.values())]:
            del self.families[fid]

    def _token(self, jti, family_id, user_id, parent, issued_at, expires_at, **extra) -> dict:
        row = {
            "jti": jti, "family_id": family_id, "user_id": user_id, "parent_jti": parent,
            "issued_at": issued_at, "expires_at": expires_at, "revoked_at": None,
            "replaced_by": None, "child_count": 0, "legacy": False,
        }
        row.update(extra)
        self.tokens[jti] = row
        return row

    # ── the functions ────────────────────────────────────────────────────────
    def _start_refresh_family(self, p_user_id, p_family_id, p_jti, p_expires_at, p_auth_time):
        if None in (p_user_id, p_family_id, p_jti, p_expires_at):
            raise PgError("P0001", "start_refresh_family: a required argument is NULL")
        if p_user_id not in self.users:
            return {"status": "account_missing"}
        if p_family_id in self.families or p_jti in self.tokens:
            raise PgError("23505", "duplicate key")
        now = self.now()
        self.families[p_family_id] = {
            "family_id": p_family_id, "user_id": p_user_id, "created_at": now,
            "auth_time": _ts(p_auth_time), "revoked_at": None, "revoke_reason": None,
        }
        self._token(p_jti, p_family_id, p_user_id, None, now, _ts(p_expires_at))
        self._prune(p_user_id)
        return {"status": "started", "family_id": p_family_id}

    def _rotate_refresh_token(self, p_user_id, p_jti, p_token_iat, p_token_exp, p_legacy,
                              p_new_jti, p_new_family_id, p_new_expires_at, p_grace_seconds,
                              p_max_children):
        if None in (p_user_id, p_jti, p_new_jti, p_token_iat, p_new_expires_at):
            raise PgError("P0001", "rotate_refresh_token: a required argument is NULL")
        now = self.now()
        user = self.users.get(p_user_id)
        if user is None:
            return {"status": "account_missing"}
        stamps = [_ts(user.get("password_changed_at")), _ts(user.get("sessions_revoked_at"))]
        stamps = [s for s in stamps if s is not None]
        if stamps:
            boundary = max(stamps).replace(microsecond=0)
            if datetime.fromtimestamp(p_token_iat, tz=timezone.utc) < boundary:
                return {"status": "evicted"}

        tok = self.tokens.get(p_jti)
        if tok is None:
            if not p_legacy:
                return {"status": "unknown"}
            if p_new_family_id is None or p_token_exp is None:
                raise PgError("P0001", "a legacy token needs p_new_family_id and p_token_exp")
            self.families[p_new_family_id] = {
                "family_id": p_new_family_id, "user_id": p_user_id, "created_at": now,
                "auth_time": None, "revoked_at": None, "revoke_reason": None,
            }
            self._token(p_jti, p_new_family_id, p_user_id, None,
                        datetime.fromtimestamp(p_token_iat, tz=timezone.utc),
                        datetime.fromtimestamp(p_token_exp, tz=timezone.utc),
                        revoked_at=now, replaced_by=p_new_jti, child_count=1, legacy=True)
            self._token(p_new_jti, p_new_family_id, p_user_id, p_jti, now, _ts(p_new_expires_at))
            self._prune(p_user_id)
            return {"status": "rotated", "family_id": p_new_family_id, "auth_time": None,
                    "legacy": True}

        if tok["user_id"] != p_user_id:
            return {"status": "mismatch"}
        fam = self.families[tok["family_id"]]
        if fam["revoked_at"] is not None:
            return {"status": "revoked", "reason": fam["revoke_reason"], "family_id": fam["family_id"]}
        if tok["expires_at"] <= now:
            return {"status": "expired", "family_id": fam["family_id"]}
        auth_epoch = math.floor(fam["auth_time"].timestamp()) if fam["auth_time"] else None

        if tok["revoked_at"] is None:
            tok.update(revoked_at=now, replaced_by=p_new_jti, child_count=tok["child_count"] + 1)
            self._token(p_new_jti, fam["family_id"], p_user_id, p_jti, now, _ts(p_new_expires_at))
            self._prune(p_user_id)
            return {"status": "rotated", "family_id": fam["family_id"], "auth_time": auth_epoch}

        age = (now - tok["revoked_at"]).total_seconds()
        if age <= max(p_grace_seconds or 0, 0) and tok["child_count"] < max(p_max_children or 1, 1):
            tok["child_count"] += 1
            self._token(p_new_jti, fam["family_id"], p_user_id, p_jti, now, _ts(p_new_expires_at))
            return {"status": "grace", "family_id": fam["family_id"], "auth_time": auth_epoch,
                    "age_seconds": age, "child_count": tok["child_count"]}

        fam.update(revoked_at=now, revoke_reason="reuse")
        return {"status": "reuse", "family_id": fam["family_id"], "age_seconds": age,
                "child_count": tok["child_count"]}

    def _revoke_refresh_family(self, p_user_id, p_family_id, p_reason):
        if None in (p_user_id, p_family_id, p_reason):
            raise PgError("P0001", "revoke_refresh_family: a required argument is NULL")
        if p_reason not in ("logout", "reuse"):
            raise PgError("P0001", f"unknown reason {p_reason}")
        fam = self.families.get(p_family_id)
        if fam is None or fam["user_id"] != p_user_id:
            return {"status": "unknown"}
        if fam["revoked_at"] is not None:
            return {"status": "already_revoked", "reason": fam["revoke_reason"]}
        fam.update(revoked_at=self.now(), revoke_reason=p_reason)
        return {"status": "revoked"}

    def _revoke_all_refresh_sessions(self, p_user_id):
        if p_user_id is None:
            raise PgError("P0001", "p_user_id is NULL")
        user = self.users.get(p_user_id)
        if user is None:
            return {"status": "account_missing"}
        # The NEXT whole second (as the SQL): evicts every token minted in the current second.
        stamp = self.now().replace(microsecond=0) + timedelta(seconds=1)
        user["sessions_revoked_at"] = stamp.isoformat()
        n = 0
        for fam in self.families.values():
            if fam["user_id"] == p_user_id and fam["revoked_at"] is None:
                fam.update(revoked_at=stamp, revoke_reason="signout_all")
                n += 1
        self._prune(p_user_id)
        return {"status": "revoked", "families": n, "sessions_revoked_at": stamp.isoformat()}

    def _account_has_identity(self, p_user_id, p_provider, p_subject):
        identities = self.users.get(p_user_id, {}).get("_identities", ())
        return (p_provider, p_subject) in identities

    # ── assertions helpers ───────────────────────────────────────────────────
    def family_of(self, jti: str) -> Optional[dict]:
        tok = self.tokens.get(jti)
        return self.families.get(tok["family_id"]) if tok else None

    def live_tokens(self, family_id: str) -> list[dict]:
        return [t for t in self.tokens.values()
                if t["family_id"] == family_id and t["revoked_at"] is None]
