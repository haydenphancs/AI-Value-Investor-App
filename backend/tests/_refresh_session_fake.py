"""An in-memory model of migration 214's session RPCs — and of migration 216's widget Holdings
grants, which hang off them — for the hermetic suite.

Not a test module (no `test_` prefix, like `_price_fakes.py`). Every refresh/sign-in/logout test
that needs the session store uses THIS model, so they all exercise the same state machine.

It mirrors `backend/database/migrations/214_auth_refresh_tokens.sql` statement by statement:
the statuses, the whole-second eviction comparison, the grace window and the sibling cap, the
legacy first-use branch, revocation scoped to the caller, the prune. Migration 216
(`216_widget_holdings_grants.sql`) is mirrored the same way: the ONE liveness predicate
(`_grant_is_live`), mint / check / revoke / note-head / push register / targets / settle, and the
`auth_refresh_families` trigger (`_on_family_revoked`, called from exactly the three places the
214 SQL updates `revoked_at`; `trigger_enabled = False` removes it, for the defence-in-depth
mutation). With no grants the trigger is a no-op, so 214's own tests are unaffected. It cannot
prove the SQL itself is right — `scripts/` E2E against a local Supabase does that (SYSTEM_DESIGN_GUIDELINES §9)
— but a divergence between the two is a bug in this file, never a reason to relax a test.
"""

from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX = re.compile(r"^[0-9a-f]+$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
#: 214's family reasons the trigger copies; anything else becomes 'session_ended'.
_FAMILY_REASONS_COPIED = ("logout", "reuse", "signout_all")


#: Migration 216's RPC names (for `grants_missing = {"*"}`).
_GRANT_RPCS = frozenset({
    "mint_widget_holdings_grant", "check_widget_holdings_grant", "revoke_widget_holdings_grant",
    "note_widget_holdings_head", "widget_push_register", "widget_push_targets",
    "widget_push_settle",
})


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
        #: migration 216: grant_id -> row (datetimes, like the table).
        self.grants: dict[str, dict] = {}
        #: False models `DROP TRIGGER trg_auth_refresh_families_revoke_widget_grants`.
        self.trigger_enabled = True
        #: Names of 216 RPCs that are "not applied" (PGRST202), or "*" for all of them.
        self.grants_missing: set[str] = set()

    # ── plumbing ──────────────────────────────────────────────────────────────
    def now(self) -> datetime:
        return self._clock()

    def rpc(self, name: str, params: dict):
        def run():
            self.calls.append((name, dict(params)))
            if self.missing:
                raise PgError("PGRST202", f"Could not find the function public.{name}")
            if name in self.grants_missing or (
                "*" in self.grants_missing and name in _GRANT_RPCS
            ):
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
        self._on_family_revoked(fam)
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
        self._on_family_revoked(fam)
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
                self._on_family_revoked(fam)
                n += 1
        self._prune(p_user_id)
        return {"status": "revoked", "families": n, "sessions_revoked_at": stamp.isoformat()}

    def _account_has_identity(self, p_user_id, p_provider, p_subject):
        identities = self.users.get(p_user_id, {}).get("_identities", ())
        return (p_provider, p_subject) in identities

    # ── migration 216: widget Holdings grants ────────────────────────────────
    def delete_user(self, user_id: str) -> None:
        """`DELETE FROM public.users` — the FK cascades (214's tables and 216's grants)."""
        self.users.pop(user_id, None)
        for table in (self.tokens, self.families, self.grants):
            for key in [k for k, row in table.items() if row["user_id"] == user_id]:
                del table[key]

    @staticmethod
    def _dead_push(row: dict) -> None:
        row.update(push_token=None, push_environment=None, last_head_ticker=None)

    def _revoke_grant_row(self, row: dict, reason: str, at: Optional[datetime] = None) -> None:
        row.update(revoked_at=at or self.now(), revoke_reason=reason)
        self._dead_push(row)

    def _on_family_revoked(self, fam: dict) -> None:
        """The AFTER UPDATE OF revoked_at trigger (`_revoke_widget_grants_for_session`)."""
        if not self.trigger_enabled:
            return
        reason = fam["revoke_reason"]
        mapped = reason if reason in _FAMILY_REASONS_COPIED else "session_ended"
        for row in self.grants.values():
            if (row["sid"] == fam["family_id"] and row["user_id"] == fam["user_id"]
                    and row["revoked_at"] is None):
                self._revoke_grant_row(row, mapped)

    def _stamp(self, user: dict) -> Optional[datetime]:
        stamps = [_ts(user.get("password_changed_at")), _ts(user.get("sessions_revoked_at"))]
        stamps = [x for x in stamps if x is not None]
        return max(stamps) if stamps else None

    def _evicted(self, minted_iat: int, user: dict) -> bool:
        stamp = self._stamp(user)
        return stamp is not None and (
            datetime.fromtimestamp(minted_iat, tz=timezone.utc) < stamp.replace(microsecond=0)
        )

    def _grant_is_live(self, row: dict) -> bool:
        """`widget_holdings_grant_is_live(g)` — the ONE predicate."""
        if row["revoked_at"] is not None or row["expires_at"] <= self.now():
            return False
        user = self.users.get(row["user_id"])
        return user is not None and not self._evicted(row["minted_iat"], user)

    @staticmethod
    def _z(dt: Optional[datetime]) -> Optional[str]:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None

    def _mint_widget_holdings_grant(self, p_user_id, p_sid, p_minted_iat, p_key_hash, p_ttl_days,
                                    p_max_live):
        if None in (p_user_id, p_sid, p_minted_iat, p_key_hash, p_ttl_days, p_max_live):
            raise PgError("22004", "mint_widget_holdings_grant: a required argument is NULL")
        if not _HEX64.fullmatch(p_key_hash):
            raise PgError("22023", "p_key_hash is not 64 lower-case hex")
        if not 1 <= p_ttl_days <= 30 or not 1 <= p_max_live <= 10 or p_minted_iat <= 0:
            raise PgError("22023", "argument out of range")
        now = self.now()
        # Bounded cross-user prune (500 rows).
        dead = [g for g, r in self.grants.items()
                if (r["revoked_at"] or r["expires_at"]) < now - timedelta(days=1)][:500]
        for g in dead:
            del self.grants[g]
        user = self.users.get(p_user_id)
        if user is None:
            return {"status": "account_missing"}
        # The account's evicted grants are revoked under the lock (216: "Every grant of the
        # account the stamps evicted is REVOKED here"), before the minting iat is judged.
        for r in self.grants.values():
            if (r["user_id"] == p_user_id and r["revoked_at"] is None
                    and self._evicted(r["minted_iat"], user)):
                self._revoke_grant_row(r, "session_ended", now)
        if self._evicted(p_minted_iat, user):
            return {"status": "evicted"}
        fam = self.families.get(p_sid)
        if fam is None or fam["user_id"] != p_user_id:
            return {"status": "session_unknown"}
        if fam["revoked_at"] is not None:
            return {"status": "session_revoked"}
        if any(r["key_hash"] == p_key_hash for r in self.grants.values()):
            raise PgError("23505", "duplicate key value violates widget_holdings_grants_key_hash_key")

        same = [r for r in self.grants.values()
                if r["sid"] == p_sid and r["user_id"] == p_user_id and r["revoked_at"] is None]
        for r in same:
            if r["superseded_by"] is not None:
                self._revoke_grant_row(r, "renewed", now)
        new_id = str(uuid.uuid4())
        live_pred = sorted((r for r in same if r["superseded_by"] is None
                            and r["expires_at"] > now), key=lambda r: r["created_at"])
        carried = dict(live_pred[-1]) if live_pred else None
        for r in same:
            if r["revoked_at"] is None and r["superseded_by"] is None:
                r.update(superseded_by=new_id,
                         expires_at=min(r["expires_at"], now + timedelta(hours=24)),
                         push_token=None, push_environment=None, push_token_updated_at=None,
                         push_last_sent_at=None, push_sent_day=None, push_sent_count=0,
                         push_bad_since=None, push_last_error=None, last_head_ticker=None)
        expires = now + timedelta(days=p_ttl_days)
        row = {
            "grant_id": new_id, "user_id": p_user_id, "key_hash": p_key_hash, "sid": p_sid,
            "minted_iat": p_minted_iat, "created_at": now, "expires_at": expires,
            "last_used_at": None, "revoked_at": None, "revoke_reason": None,
            "superseded_by": None, "last_head_ticker": None, "push_token": None,
            "push_environment": None, "push_token_updated_at": None, "push_last_sent_at": None,
            "push_sent_day": None, "push_sent_count": 0, "push_bad_since": None,
            "push_last_error": None,
        }
        if carried is not None:
            for col in ("last_head_ticker", "push_token", "push_environment",
                        "push_token_updated_at", "push_last_sent_at", "push_sent_day",
                        "push_sent_count", "push_bad_since", "push_last_error"):
                row[col] = carried[col]
        self.grants[new_id] = row
        # The cap: live NON-superseded rows, the new one never capped.
        others = sorted(
            (r for r in self.grants.values()
             if r["user_id"] == p_user_id and r["revoked_at"] is None
             and r["superseded_by"] is None and r["expires_at"] > now
             and r["grant_id"] != new_id),
            key=lambda r: (r["created_at"], r["grant_id"]), reverse=True,
        )
        capped = {r["grant_id"] for r in others[p_max_live - 1:]}
        for r in self.grants.values():
            if r["user_id"] == p_user_id and r["revoked_at"] is None and (
                    r["grant_id"] in capped or r["superseded_by"] in capped):
                self._revoke_grant_row(r, "cap", now)
        return {"status": "minted", "grant_id": new_id, "expires_at": self._z(expires)}

    def _check_widget_holdings_grant(self, p_key_hash):
        if not isinstance(p_key_hash, str) or not _HEX64.fullmatch(p_key_hash):
            return {"status": "unknown"}
        row = next((r for r in self.grants.values() if r["key_hash"] == p_key_hash), None)
        if row is None:
            return {"status": "unknown"}
        now = self.now()
        if not self._grant_is_live(row):
            if row["revoked_at"] is not None:
                return {"status": "revoked"}
            if row["expires_at"] <= now:
                return {"status": "expired"}
            user = self.users.get(row["user_id"])
            if user is None:
                return {"status": "account_missing"}
            if self._evicted(row["minted_iat"], user):
                # 216: an evicted grant is REVOKED the first time it is seen.
                self._revoke_grant_row(row, "session_ended", now)
                return {"status": "evicted"}
            return {"status": "revoked"}
        if row["last_used_at"] is None:
            for r in self.grants.values():
                if (r["superseded_by"] == row["grant_id"] and r["user_id"] == row["user_id"]
                        and r["revoked_at"] is None):
                    self._revoke_grant_row(r, "renewed", now)
        if row["last_used_at"] is None or row["last_used_at"] < now - timedelta(hours=1):
            row["last_used_at"] = now
        return {"status": "live", "grant_id": row["grant_id"], "user_id": row["user_id"],
                "expires_at": self._z(row["expires_at"]),
                "superseded": row["superseded_by"] is not None}

    def _revoke_widget_holdings_grant(self, p_grant_id, p_user_id, p_reason):
        if None in (p_grant_id, p_user_id, p_reason):
            raise PgError("22004", "revoke_widget_holdings_grant: a required argument is NULL")
        if p_reason not in ("app_revoked", "widget_removed"):
            raise PgError("22023", f"unknown reason {p_reason}")
        row = self.grants.get(p_grant_id)
        if row is None or row["user_id"] != p_user_id or row["revoked_at"] is not None:
            return {"status": "already_revoked_or_unknown"}
        self._revoke_grant_row(row, p_reason)
        return {"status": "revoked"}

    def _note_widget_holdings_head(self, p_grant_id, p_ticker):
        if p_grant_id is None:
            raise PgError("22004", "p_grant_id is NULL")
        ticker = (p_ticker or "").strip().upper() or None
        if ticker is not None and len(ticker) > 32:
            raise PgError("22023", "ticker longer than 32")
        row = self.grants.get(p_grant_id)
        if row is None or row["revoked_at"] is not None or row["last_head_ticker"] == ticker:
            return {"status": "unchanged"}
        row["last_head_ticker"] = ticker
        return {"status": "noted"}

    def _widget_push_register(self, p_user_id, p_grant_id, p_token, p_environment):
        if p_user_id is None or p_grant_id is None:
            raise PgError("22004", "widget_push_register: a required argument is NULL")
        if p_token is not None:
            if (not _HEX.fullmatch(p_token) or not 32 <= len(p_token) <= 400
                    or len(p_token) % 2):
                raise PgError("22023", "malformed token")
            if p_environment not in ("sandbox", "production"):
                raise PgError("22023", "unknown environment")
        row = self.grants.get(p_grant_id)
        if row is None or row["user_id"] != p_user_id:
            return {"stored": False, "grant_usable": False}
        if row["superseded_by"] is not None or not self._grant_is_live(row):
            return {"stored": False, "grant_usable": False}
        now = self.now()
        if p_token is None:
            row.update(push_token=None, push_environment=None, push_token_updated_at=now,
                       push_bad_since=None, push_last_error=None)
            return {"stored": True, "grant_usable": True}
        for r in self.grants.values():
            if r["push_token"] == p_token and r["grant_id"] != p_grant_id:
                r.update(push_token=None, push_environment=None, push_token_updated_at=now,
                         push_bad_since=None, push_last_error="moved to another grant")
        row.update(push_token=p_token, push_environment=p_environment, push_token_updated_at=now,
                   push_bad_since=None, push_last_error=None)
        return {"stored": True, "grant_usable": True}

    def _widget_push_targets(self, p_scopes, p_all, p_grant_ids, p_after, p_limit):
        scopes = {str(x).strip().upper() for x in (p_scopes or [])}
        ids = set(p_grant_ids or [])
        limit = min(max(p_limit or 1000, 1), 1000)
        rows = sorted(
            (r for r in self.grants.values()
             if r["push_token"] is not None and r["superseded_by"] is None
             and self._grant_is_live(r)
             and (p_after is None or r["grant_id"] > p_after)
             and (bool(p_all) or r["grant_id"] in ids or r["last_head_ticker"] in scopes)),
            key=lambda r: r["grant_id"],
        )[:limit]
        return [{
            "grant_id": r["grant_id"], "user_id": r["user_id"], "push_token": r["push_token"],
            "push_environment": r["push_environment"], "last_head_ticker": r["last_head_ticker"],
            "push_last_sent_at": r["push_last_sent_at"].isoformat() if r["push_last_sent_at"] else None,
            "push_sent_day": r["push_sent_day"].isoformat() if r["push_sent_day"] else None,
            "push_sent_count": r["push_sent_count"],
        } for r in rows]

    def _widget_push_settle(self, p_outcomes):
        if not isinstance(p_outcomes, list):
            raise PgError("22023", "p_outcomes must be a JSON array")
        if len(p_outcomes) > 100:
            raise PgError("22023", "at most 100 outcomes per call")
        now = self.now()
        today = now.astimezone(_ET).date()
        n = dict.fromkeys(("accepted", "gone", "bad", "cleared_bad", "retry", "not_sent",
                           "unmatched", "skipped"), 0)
        for e in p_outcomes:
            if (not isinstance(e, dict) or not isinstance(e.get("grant_id"), str)
                    or not _UUID.fullmatch(e["grant_id"]) or not isinstance(e.get("token"), str)
                    or not _HEX.fullmatch(e["token"]) or not 32 <= len(e["token"]) <= 400
                    or not isinstance(e.get("outcome"), str)):
                n["skipped"] += 1
                continue
            row = self.grants.get(e["grant_id"].lower())
            match = row is not None and row["push_token"] == e["token"]
            out = e["outcome"]
            err = (e.get("error") or None)
            if out == "accepted":
                count = e.get("count") if isinstance(e.get("count"), bool) else True
                if match:
                    if count:
                        row["push_sent_count"] = (row["push_sent_count"] + 1
                                                  if row["push_sent_day"] == today else 1)
                        row["push_sent_day"] = today
                    row.update(push_last_sent_at=now, push_bad_since=None, push_last_error=None)
                    n["accepted"] += 1
                else:
                    n["unmatched"] += 1
            elif out == "gone":
                ts = e.get("apns_ts")
                when = now
                if isinstance(ts, (int, float)) and not isinstance(ts, bool) and 0 <= ts <= 1e14:
                    when = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
                if match and (row["push_token_updated_at"] is None
                              or row["push_token_updated_at"] <= when):
                    row.update(push_token=None, push_environment=None, push_bad_since=None,
                               push_last_error=(err or "410 Unregistered")[:200])
                    n["gone"] += 1
                else:
                    n["unmatched"] += 1
            elif out == "bad":
                if match:
                    row["push_sent_count"] = (row["push_sent_count"] + 1
                                              if row["push_sent_day"] == today else 1)
                    row.update(push_sent_day=today, push_last_sent_at=now,
                               push_bad_since=row["push_bad_since"] or now,
                               push_last_error=(err or "bad device token")[:200])
                    n["bad"] += 1
                    if row["push_bad_since"] < now - timedelta(hours=24):
                        row.update(push_token=None, push_environment=None, push_bad_since=None,
                                   push_last_error="cleared: refused by APNs for over 24 h")
                        n["cleared_bad"] += 1
                else:
                    n["unmatched"] += 1
            elif out == "retry":
                if match:
                    row["push_last_error"] = (err or "retry")[:200]
                n["retry"] += 1
            elif out == "not_sent":
                n["not_sent"] += 1
            else:
                n["skipped"] += 1
        return {"status": "settled", **n}

    def grant_by_hash(self, key_hash: str) -> Optional[dict]:
        return next((r for r in self.grants.values() if r["key_hash"] == key_hash), None)

    # ── assertions helpers ───────────────────────────────────────────────────
    def family_of(self, jti: str) -> Optional[dict]:
        tok = self.tokens.get(jti)
        return self.families.get(tok["family_id"]) if tok else None

    def live_tokens(self, family_id: str) -> list[dict]:
        return [t for t in self.tokens.values()
                if t["family_id"] == family_id and t["revoked_at"] is None]


class _UsersTable:
    """`supabase.table("users")` — select / eq / limit / execute over the store's users dict."""

    def __init__(self, db: "SupabaseFake", name: str) -> None:
        self._db, self._name, self._id = db, name, None

    def select(self, *_cols, **_kw):
        return self

    def eq(self, col, value):
        if col in ("id", "user_id"):
            self._id = value
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        if self._name != "users":
            raise AssertionError(f"unexpected table read: {self._name}")
        if self._db.users_down:
            raise RuntimeError("users read: connection reset")
        row = self._db.store.users.get(self._id)
        return SimpleNamespace(data=[{**row, "id": self._id}] if row is not None else [])


class SupabaseFake:
    """What `get_supabase()` returns, over one `SessionStoreFake`: `.rpc` for the 214 / 216
    functions and `.table("users")` for the users-row reads the auth paths make. Hermetic."""

    def __init__(self, store: Optional[SessionStoreFake] = None, users: Optional[dict] = None):
        self.store = store if store is not None else SessionStoreFake(users=users)
        #: True → every users-row read raises (a transient PostgREST failure).
        self.users_down = False

    def rpc(self, name: str, params: dict):
        return self.store.rpc(name, params)

    def table(self, name: str) -> _UsersTable:
        return _UsersTable(self, name)
