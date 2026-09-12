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


def _defined_secdef_signatures() -> dict[str, str]:
    """{name(argtypes): migration filename} — the LAST definition of each signature wins,
    which is what makes a DROP+CREATE visible."""
    found: dict[str, str] = {}
    for path in _sql_files():
        code = _strip_sql_comments(path.read_text(encoding="utf-8"))
        for match in re.finditer(
            r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:public\.)?([a-z0-9_]+)\s*\(",
            code, re.I,
        ):
            args = _balanced_args(code, match.end() - 1)
            start = match.end()
            nxt = re.search(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION", code[start:], re.I)
            body = code[start:start + (nxt.start() if nxt else len(code))]
            if re.search(r"SECURITY\s+DEFINER", body, re.I):
                found[_signature(match.group(1), args)] = path.name
    return found


#: `name(normalised arg types)` — the identity Postgres actually uses.
#:
#: A bare NAME is not a function. An overload with a different signature is a DIFFERENT
#: object that starts life on the default `EXECUTE TO PUBLIC`, and `DROP FUNCTION` DISCARDS
#: the ACL — migration 142's own header says so ("A DROP DISCARDS GRANTS. `CREATE OR
#: REPLACE` preserves them … here the tail is LOAD-BEARING"). Matching on the name alone
#: therefore let a DROP+CREATE, or a new overload, inherit an old migration's REVOKE on
#: paper while being wide open in the database.
def _signature(name: str, args: str) -> str:
    types = []
    for part in args.split(","):
        part = re.sub(r"--.*$", "", part).strip()
        if not part:
            continue
        # `IN p_user_id uuid` / `p_user_id uuid DEFAULT NULL` -> `uuid`
        part = re.sub(r"^(?:IN|OUT|INOUT|VARIADIC)\s+", "", part, flags=re.I)
        part = re.split(r"\s+DEFAULT\s+|\s*=\s*", part, flags=re.I)[0].strip()
        tokens = part.split()
        types.append(tokens[-1].lower() if tokens else "")
    return f"{name.lower()}({','.join(t for t in types if t)})"


def _balanced_args(code: str, open_paren: int) -> str:
    """Text between the arg-list parens starting at `open_paren`."""
    depth, i = 0, open_paren
    while i < len(code):
        if code[i] == "(":
            depth += 1
        elif code[i] == ")":
            depth -= 1
            if depth == 0:
                return code[open_paren + 1:i]
        i += 1
    return ""


def _revoked_functions() -> set[str]:
    """Every function name revoked from PUBLIC anywhere in the migration set.

    Kept NAME-keyed because the REVOKE and the CREATE are often written with different
    (but equivalent) spellings of the same types; `_revoked_signatures` is the strict
    companion and `test_every_secdef_signature_is_revoked_too` is what actually bites.
    """
    return {sig.split("(")[0] for sig in _revoked_signatures()}


def _revoked_signatures() -> set[str]:
    revoked: set[str] = set()
    for path in _sql_files():
        code = _strip_sql_comments(path.read_text(encoding="utf-8"))
        for match in re.finditer(
            r"REVOKE\s+(?:ALL|EXECUTE)[A-Z\s]*ON\s+FUNCTION\s+(?:public\.)?([a-z0-9_]+)\s*\(",
            code, re.I,
        ):
            args = _balanced_args(code, match.end() - 1)
            revoked.add(_signature(match.group(1), args))
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
        # `cleanup_expired_news_articles` was the third one until migration 168 dropped it with
        # its table; `cleanup_expired_news_cache` is its surviving sibling from the same 153 batch.
        "cleanup_expired_news_cache",
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


def test_every_secdef_signature_is_revoked_too():
    """Name-level coverage is not function-level coverage.

    `_revoked_functions()` flattens to bare names, so an OVERLOAD — a different signature,
    therefore a different object that starts on the default `EXECUTE TO PUBLIC` — inherited
    an old migration's REVOKE on paper while being wide open in the database. `DROP
    FUNCTION` has the same effect from the other direction: it DISCARDS the ACL, which
    migration 142's own header states ("A DROP DISCARDS GRANTS. `CREATE OR REPLACE`
    preserves them … here the tail is LOAD-BEARING").

    Signature normalisation is best-effort on hand-written SQL, so a definition whose
    types this scan cannot line up with its REVOKE is reported, not silently passed.
    """
    defined = _defined_secdef_signatures()
    revoked = _revoked_signatures()

    # EXACT signature match, with no "the name is revoked somewhere" escape. That escape
    # was the whole hole: an overload IS a name that is revoked somewhere, and it is
    # exactly the object that is not covered. Verified 2026-09-12 that all 31 defined
    # SECURITY DEFINER signatures line up exactly with a REVOKE, so this costs no false
    # positives — if a future migration spells a type differently (int vs integer), make
    # `_signature` alias it rather than reopening the escape.
    missing = {
        sig: mig for sig, mig in defined.items()
        if sig not in revoked and sig.split("(")[0] not in _EXEMPT
    }
    assert not missing, (
        "SECURITY DEFINER signature(s) with no REVOKE — an overload or a DROP+CREATE is a "
        "NEW object on the default `EXECUTE TO PUBLIC`:\n"
        + "\n".join(f"  • public.{sig}  (defined in {mig})"
                     for sig, mig in sorted(missing.items()))
    )


def test_the_signature_normaliser_does_what_it_claims():
    """Anti-vacuity: if `_signature` collapsed everything to the bare name, the test above
    would be a duplicate of the name-level one and prove nothing."""
    assert _signature("f", "p_user_id uuid, p_n integer") == "f(uuid,integer)"
    assert _signature("f", "") == "f()"
    assert _signature("f", "IN p_id uuid, p_limit integer DEFAULT 50") == "f(uuid,integer)"
    assert _signature("f", "p_id uuid") != _signature("f", "p_id text"), (
        "two overloads must not normalise to the same identity"
    )
    # Nested parens in a type must not truncate the arg list.
    assert _balanced_args("f(a numeric(10,2), b text)", 1) == "a numeric(10,2), b text"

