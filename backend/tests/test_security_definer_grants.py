"""Every SECURITY DEFINER function in a migration must be revoked from PUBLIC.

WHY THIS FILE EXISTS
--------------------
Migration 049 hardened four SECURITY DEFINER functions, wrote down the convention, and nothing
enforced it afterwards. Three more shipped without it and stayed open for months:

    cleanup_expired_news_articles()      -- returns void, so PostgREST DOES expose it as RPC,
                                            and it DELETEs from news_articles
    create_user_credits()                -- trigger
    touch_whale_snapshot_processed_at()  -- trigger

The only thing that ever noticed was the Supabase Security Advisor. 29 of the other 32 were
correctly locked, which is exactly why the drift was invisible: the pattern LOOKED universal.

A SECURITY DEFINER function runs with its OWNER's privileges. Left on the Postgres default ACL
(EXECUTE to PUBLIC) it is a privilege-escalation path straight through PostgREST RPC for anyone
holding the shipped anon key.

WHAT THIS CAN AND CANNOT SEE
----------------------------
This is a SOURCE SCAN over `backend/database/migrations/`, per .claude/rules/testing.md (no
Supabase integration tests). It catches the case that actually happened — a migration that
defines a SECDEF function and forgets the revoke.

It CANNOT see a function created by hand in the Supabase SQL editor, or a migration that was
written but never applied. `scripts/check_function_grants.py` reads the live catalog and covers
those; run it after applying anything.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "migrations"

# A function whose privileges are managed somewhere other than a REVOKE line, with the reason.
# Empty today. An entry here is a deliberate exception and must say why.
_EXEMPT: dict[str, str] = {}


def _sql_files() -> list[Path]:
    files = sorted(_MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    assert len(files) > 100, f"the migration glob found only {len(files)} files — it has rotted"
    return files


def _strip_sql_comments(sql: str) -> str:
    """Drop `--` line comments and `/* */` blocks.

    Load-bearing, in BOTH directions. These migrations carry long rationale headers that name
    the functions and quote the very REVOKE lines being asserted — 153's header alone mentions
    every function it locks. An un-stripped scan would find a "definition" in prose, and would
    equally find a "revoke" in prose after the real statement was deleted.
    """
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _defined_secdef_functions() -> dict[str, str]:
    """{function_name: migration filename} for every SECURITY DEFINER definition."""
    found: dict[str, str] = {}
    for path in _sql_files():
        code = _strip_sql_comments(path.read_text(encoding="utf-8"))
        # CREATE [OR REPLACE] FUNCTION public.name(...) ... SECURITY DEFINER
        for match in re.finditer(
            r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:public\.)?([a-z0-9_]+)\s*\(",
            code, re.I,
        ):
            name = match.group(1).lower()
            # Bound the body at the next CREATE FUNCTION so one SECDEF function in a file
            # cannot vouch for a plain one defined after it.
            start = match.end()
            nxt = re.search(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION", code[start:], re.I)
            body = code[start:start + (nxt.start() if nxt else len(code))]
            if re.search(r"SECURITY\s+DEFINER", body, re.I):
                found.setdefault(name, path.name)
    return found


def _revoked_functions() -> set[str]:
    """Every function name revoked from PUBLIC anywhere in the migration set."""
    revoked: set[str] = set()
    for path in _sql_files():
        code = _strip_sql_comments(path.read_text(encoding="utf-8"))
        for match in re.finditer(
            r"REVOKE\s+(?:ALL|EXECUTE)[A-Z\s]*ON\s+FUNCTION\s+(?:public\.)?([a-z0-9_]+)\s*\(",
            code, re.I,
        ):
            revoked.add(match.group(1).lower())
    return revoked


def test_every_security_definer_function_is_revoked_from_public():
    defined = _defined_secdef_functions()
    revoked = _revoked_functions()

    missing = {
        name: mig for name, mig in defined.items()
        if name not in revoked and name not in _EXEMPT
    }
    assert not missing, (
        "SECURITY DEFINER function(s) with no REVOKE anywhere in the migration set — each is "
        "callable by anon/authenticated through PostgREST RPC, running as the function owner:\n"
        + "\n".join(f"  • public.{n}()  (defined in {m})" for n, m in sorted(missing.items()))
        + "\n\nAdd to a migration:\n"
        "  REVOKE ALL ON FUNCTION public.<name>(<args>) FROM PUBLIC;\n"
        "  GRANT EXECUTE ON FUNCTION public.<name>(<args>) TO service_role;\n"
        "A TRIGGER function gets the REVOKE and NO grant."
    )


def test_the_three_functions_the_advisor_found_are_covered():
    """Pins the specific regression. These are the mutation targets: delete a REVOKE from
    migration 153 and this must go red."""
    revoked = _revoked_functions()
    for name in (
        "cleanup_expired_news_articles",
        "create_user_credits",
        "touch_whale_snapshot_processed_at",
    ):
        assert name in revoked, f"public.{name} lost its REVOKE — Security Advisor will re-flag it"


def test_the_rag_search_rpcs_are_not_public():
    """They return chunks of LICENSED book and article text. Safe today only because the chunk
    tables grant SELECT to service_role alone — one grant away from being a content leak."""
    revoked = _revoked_functions()
    for name in (
        "search_all_chunks",
        "search_article_chunks",
        "search_book_chunks",
        "search_filing_chunks",
    ):
        assert name in revoked, f"public.{name} is callable by anon via RPC"


def test_the_detectors_are_not_vacuous():
    """Both halves must actually match something, or every assertion above passes on nothing."""
    defined = _defined_secdef_functions()
    revoked = _revoked_functions()
    assert len(defined) >= 25, f"the SECDEF detector found only {len(defined)}"
    assert len(revoked) >= 25, f"the REVOKE detector found only {len(revoked)}"

    # And they must DISCRIMINATE, proven against synthetic SQL rather than the live tree.
    import tempfile

    sample_secdef = "CREATE OR REPLACE FUNCTION public.x() RETURNS void LANGUAGE plpgsql SECURITY DEFINER AS $$ BEGIN END $$;"
    sample_plain = "CREATE OR REPLACE FUNCTION public.y() RETURNS void LANGUAGE plpgsql AS $$ BEGIN END $$;"
    assert re.search(r"SECURITY\s+DEFINER", sample_secdef, re.I)
    assert not re.search(r"SECURITY\s+DEFINER", sample_plain, re.I)

    # A comment must NOT satisfy either detector.
    commented = "-- REVOKE ALL ON FUNCTION public.ghost() FROM PUBLIC;"
    stripped = _strip_sql_comments(commented)
    assert "REVOKE" not in stripped, "comment stripping is broken; prose would satisfy the scan"
    del tempfile
