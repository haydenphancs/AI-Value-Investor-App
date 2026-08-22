#!/usr/bin/env python3
"""Fail if any function in `public` is callable by a role a client key can reach.

WHY THIS EXISTS
---------------
Migration 049 hardened four SECURITY DEFINER functions and nothing enforced the convention
afterwards. Three more shipped without it — `cleanup_expired_news_articles()`,
`create_user_credits()`, `touch_whale_snapshot_processed_at()` — and the only thing that ever
noticed was the Supabase Security Advisor, months later.

`tests/test_security_definer_grants.py` is the cheap half of the fix: it scans the migrations and
runs on every `pytest`. This is the other half. It reads the LIVE catalog, so it also catches:

  * a function created by hand in the Supabase SQL editor, which no migration scan can see;
  * a `DROP FUNCTION` + `CREATE` (rather than `CREATE OR REPLACE`), which silently discards the
    existing GRANTs and re-owns the function — see the `project_credit_packs_two_pool` memory;
  * a migration whose REVOKE was written but never applied.

WHAT COUNTS AS AN OFFENDER
--------------------------
A `public` function whose ACL is NULL (Postgres default = EXECUTE to PUBLIC) or that names
`anon` / `authenticated` / PUBLIC explicitly. Extension-owned functions are excluded — pgvector
installs ~120 of them and none is ours to revoke.

SECURITY DEFINER functions are the dangerous ones — they run with the owner's privileges, so a
PUBLIC grant is a privilege-escalation path straight through PostgREST RPC. SECURITY INVOKER
functions are reported separately at a lower severity: they cannot escalate, but they are still
RPC surface that only the backend should have (the `search_*_chunks` RAG functions are the live
example — harmless only because the chunk tables grant to `postgres`/`service_role` alone).

Read-only. Never prints the connection string: a traceback carrying the DSN would put the
database password in a log, which is exactly what CLAUDE.local.md forbids.

Usage:
    ./venv/bin/python scripts/check_function_grants.py           # exit 1 if any SECDEF offender
    ./venv/bin/python scripts/check_function_grants.py --strict  # also fail on INVOKER offenders
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:  # pragma: no cover - dev convenience
    sys.exit("psycopg2 is not installed in this venv")

_ENV = Path(__file__).resolve().parents[1] / ".env"

# Roles reachable from a key that ships to a client. `service_role` is deliberately NOT here:
# it is the backend's own full-trust key, and every hardened function grants EXECUTE to it.
_CLIENT_ROLES = ("anon", "authenticated")

_QUERY = """
SELECT p.proname,
       pg_get_function_identity_arguments(p.oid) AS args,
       p.prosecdef                                AS is_secdef,
       -- ⚠️ CAST. psycopg2 has no parser for `aclitem[]`, so a bare `p.proacl` arrives as the
       -- RAW STRING '{postgres=X/postgres,...}'. Iterating that yields CHARACTERS, and every
       -- ACL contains '=', so the "empty grantee means PUBLIC" test fired on all of them —
       -- 31 phantom offenders against a true count of 3. `::text[]` returns a real list.
       p.proacl::text[]                           AS acl
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  -- Exclude functions OWNED BY AN EXTENSION. `vector` alone installs ~120 operator-support
  -- functions into public, all with the default PUBLIC EXECUTE, and none of them is ours to
  -- revoke — doing so would break the extension. Left in, they drown the 4 findings that
  -- matter (the search_*_chunks RPCs) and make --strict unusable.
  AND NOT EXISTS (
      SELECT 1 FROM pg_depend d
      WHERE d.objid = p.oid AND d.classid = 'pg_proc'::regclass AND d.deptype = 'e'
  )
ORDER BY p.prosecdef DESC, p.proname;
"""


def _dsn() -> str:
    """DATABASE_URL from backend/.env, normalised for psycopg2."""
    if not _ENV.exists():
        sys.exit(f"missing {_ENV}")
    for line in _ENV.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "DATABASE_URL":
            # SQLAlchemy's driver prefix is not valid libpq.
            return value.strip().strip('"').strip("'").replace(
                "postgresql+asyncpg://", "postgresql://"
            )
    sys.exit("no DATABASE_URL in backend/.env")


def _assert_parser_is_sane() -> None:
    """Prove the offender test still discriminates, against synthetic ACLs.

    Without this the script can fail in either direction silently: a parsing change that makes
    every ACL look hostile produces 31 false alarms nobody can act on, and one that makes every
    ACL look clean produces a green run over a live PUBLIC grant. Both have happened here — the
    first one on the very first run of this script.
    """
    hostile = [
        (None, "default ACL"),
        (["=X/postgres"], "bare PUBLIC entry"),
        (["postgres=X/postgres", "anon=X/postgres"], "anon"),
        (["authenticated=X/postgres"], "authenticated"),
    ]
    for acl, label in hostile:
        assert _is_offender(acl), f"parser stopped catching {label}"
    clean = [
        ["postgres=X/postgres", "service_role=X/postgres"],
        ["postgres=X/postgres"],
    ]
    for acl in clean:
        assert _is_offender(acl) is None, f"parser now flags a correctly-locked ACL: {acl}"


def _is_offender(acl) -> str | None:
    """The reason this function is over-exposed, or None.

    A NULL acl is the Postgres DEFAULT, which is EXECUTE to PUBLIC — the single most common way
    this goes wrong, and the one that looks like "no grants" if you skim it.
    """
    if acl is None:
        return "default ACL (implicit EXECUTE to PUBLIC)"
    entries = list(acl)
    # An ACL entry is 'grantee=privs/grantor'; an EMPTY grantee means PUBLIC.
    for entry in entries:
        grantee = str(entry).split("=", 1)[0]
        if grantee == "":
            return "explicit PUBLIC grant"
        if grantee in _CLIENT_ROLES:
            return f"granted to {grantee}"
    return None


def main() -> int:
    strict = "--strict" in sys.argv
    _assert_parser_is_sane()
    try:
        conn = psycopg2.connect(_dsn())
    except Exception as exc:  # noqa: BLE001
        # Type only. The DSN carries the password and must never reach a log.
        return int(bool(sys.stderr.write(f"connect failed: {type(exc).__name__}\n"))) or 2

    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(_QUERY)
            rows = cur.fetchall()
    finally:
        conn.close()

    secdef, invoker = [], []
    for name, args, is_secdef, acl in rows:
        reason = _is_offender(acl)
        if reason is None:
            continue
        (secdef if is_secdef else invoker).append((name, args, reason))

    total = len(rows)
    print(f"scanned {total} function(s) in schema public")

    if secdef:
        print(f"\n❌ {len(secdef)} SECURITY DEFINER function(s) callable by a client key:")
        for name, args, reason in secdef:
            print(f"   • public.{name}({args}) — {reason}")
        print(
            "\n   Fix in a migration, using this repo's convention:\n"
            "     REVOKE ALL ON FUNCTION public.<name>(<args>) FROM PUBLIC;\n"
            "     GRANT EXECUTE ON FUNCTION public.<name>(<args>) TO service_role;\n"
            "   A TRIGGER function gets the REVOKE and NO grant — it runs as the table owner."
        )
    else:
        print("✅ no SECURITY DEFINER function is callable by anon/authenticated/PUBLIC")

    if invoker:
        mark = "❌" if strict else "⚠️ "
        print(f"\n{mark} {len(invoker)} SECURITY INVOKER function(s) exposed as RPC:")
        for name, args, reason in invoker:
            print(f"   • public.{name}({args}) — {reason}")
        if not strict:
            print("   (cannot escalate — they run as the caller. Re-run with --strict to gate.)")

    if secdef or (strict and invoker):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
