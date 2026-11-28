"""
Migration 215 ("🔄 Next company", owner decision 2026-11-27): the replacement columns and claim key, the two SQL
functions, and the in-memory model of both (`tests/_replacement_rpc_fake.py`) that every later replacement test uses.

Its own file (not a section of test_marketing_run_service.py, already ~9,600 lines): it pins a migration text and a
helper fake, not the run service. The owner applies 215 by hand (never Claude); these checks pin its text against the
Python constants and the code that will call it (schemas.REPLACEMENT_*, run_service._REVIEW_OFFER_STAMPS), and pin the
fake to the SQL statement by statement, so a test written against the fake tests the function.

Hermetic: no database (backend/conftest.py blocks sockets); the SQL is read as text.
"""

from __future__ import annotations

import copy
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

import _migration_sql
import _replacement_rpc_fake as rpcfake
from _replacement_rpc_fake import REQUEST, RESTORE, ReplacementRpcFake
from app.config import Settings, settings
from app.schemas import marketing as schemas
from app.services.marketing import run_service as mrs
from app.services.marketing import selection

_MIGRATION_215 = _migration_sql.MIGRATIONS / "215_marketing_replacement_runs.sql"
_SNAPSHOT = _migration_sql.MIGRATIONS.parent / "schema_snapshot.sql"
_FUNCTIONS = (REQUEST, RESTORE)
_SIGNATURES = {
    REQUEST: ("p_run_id UUID", "p_by TEXT", "p_not_after TIMESTAMPTZ"),
    RESTORE: ("p_run_id UUID", "p_by TEXT", "p_now TIMESTAMPTZ"),
}
_LOCK_EXPR = "pg_advisory_xact_lock(hashtextextended('marketing_request_replacement:' || v_day::text, 0))"


# ══ helpers over the SQL text ═════════════════════════════════════════════════════════════════════════════════════


def _strip(text: str) -> str:
    return _migration_sql.strip_sql_comments(text)


def _definition(text: str, name: str) -> _migration_sql.Definition:
    """The definition of `name` in THIS text (a mutated copy included), through the suite's one SQL reader."""
    return _migration_sql.latest_definition(name, sources=[(_MIGRATION_215.name, text)])


def _outside_bodies(sql: str) -> str:
    """`sql` (comments stripped) with every dollar-quoted body (the two functions, the DO block) blanked."""
    return re.sub(r"\$(\w*)\$.*?\$\1\$", "$$ $$", sql, flags=re.S)


def _without_literals(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def _const(body: str, name: str) -> Optional[str]:
    """The literal right side of `<name> CONSTANT <type> := <value>;` in a function body."""
    m = re.search(rf"\b{name}\s+CONSTANT\s+[A-Z\[\]]+\s*:=\s*(.*?);", body, re.I | re.S)
    return m.group(1).strip() if m else None


def _quoted(text: str) -> List[str]:
    return re.findall(r"'([a-z_]+)'", text)


def _comment_literal(sql: str, target: str) -> Optional[str]:
    """The text of `COMMENT ON <target> IS '…' '…' …;` (adjacent literals joined), or None. Postgres joins adjacent
    string constants ONLY across a newline; a same-line pair is a syntax error, returned as "<same line>"."""
    m = re.search(rf"COMMENT\s+ON\s+{re.escape(target)}\s+IS\s+((?:'(?:[^']|'')*'\s*)+);", sql, re.I)
    if not m:
        return None
    body = m.group(1)
    lits = list(re.finditer(r"'((?:[^']|'')*)'", body))
    if any("\n" not in body[a.end():b.start()] for a, b in zip(lits, lits[1:])):
        return "<same line>"
    return "".join(x.group(1).replace("''", "'") for x in lits)


#: The statements the order pin reads, in text order: the advisory lock, a row lock (`FROM public.<t> … FOR
#: UPDATE`), each `RETURN jsonb_build_object('outcome', '<name>'` (`<name>:replayed` when `'replayed', true`
#: follows the outcome), and each INSERT / UPDATE by table.
_MARKER_RE = re.compile(
    r"(?P<lock>\bpg_advisory_xact_lock\b)"
    r"|(?P<rowlock>\bFROM\s+public\.(?P<lt>\w+)\b[^;]*?\bFOR\s+UPDATE\b)"
    r"|(?P<ret>\bRETURN\s+jsonb_build_object\(\s*'outcome'\s*,\s*'(?P<out>\w+)'(?P<replayed>\s*,\s*'replayed'\s*,"
    r"\s*true\b)?)"
    r"|(?P<ins>\bINSERT\s+INTO\s+public\.(?P<it>\w+))"
    r"|(?P<upd>\bUPDATE\s+public\.(?P<ut>\w+))",
    re.I | re.S)


def _markers(body: str) -> List[str]:
    out = []
    for m in _MARKER_RE.finditer(body):
        if m.group("lock"):
            out.append("lock")
        elif m.group("rowlock"):
            out.append(f"lock:{m.group('lt')}")
        elif m.group("ret"):
            out.append(m.group("out") + (":replayed" if m.group("replayed") else ""))
        elif m.group("ins"):
            out.append(f"insert:{m.group('it')}")
        else:
            out.append(f"update:{m.group('ut')}")
    return out


def _outcomes(body: str) -> set:
    return {m.split(":")[0] for m in _markers(body)
            if not m.startswith(("lock", "insert:", "update:"))}


def _norm(sql: str) -> str:
    return _migration_sql.norm_sql(sql)


# ── what each statement DECIDES (review 2026-11-27): the marker order above sees only which statement comes next ──────

#: `IF <condition> THEN RETURN jsonb_build_object('outcome', '<name>'[, 'replayed', true]` or `… THEN RAISE`, over a
#: normalised body. `(?<!END )` skips the IF of `END IF`; the condition never crosses a `;` or a THEN.
_BRANCH_RE = re.compile(
    r"(?<!END )\bIF (?P<cond>(?:(?!\bTHEN\b)[^;])+?) THEN "
    r"(?:RETURN jsonb_build_object\('outcome', '(?P<out>\w+)'(?P<rep>, 'replayed', true\b)?|(?P<raise>RAISE)\b)")


def _branches(body: str) -> List[Tuple[str, str]]:
    """(marker, normalised condition) of every IF that returns an outcome (or raises), in text order."""
    flat = _norm(_strip(body))
    return [("raise" if m.group("raise") else m.group("out") + (":replayed" if m.group("rep") else ""), m.group("cond"))
            for m in _BRANCH_RE.finditer(flat)]


def _wheres(body: str) -> List[Tuple[str, str]]:
    """(table, `WHERE …`) for every WHERE in the body — to the end of its statement, its sub-select's closing
    parenthesis or a RETURNING, whichever comes first (so ORDER BY / LIMIT / FOR UPDATE ride along). The table is the
    last `FROM` / `UPDATE public.<t>` before it (a label only). Literals are skipped when scanning."""
    flat = _norm(_strip(body))
    blank = _migration_sql.blank_literals(flat)
    out = []
    for w in re.finditer(r"\bWHERE\b", blank):
        depth, end = 0, len(blank)
        for i in range(w.start(), len(blank)):
            ch = blank[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    end = i
                    break
                depth -= 1
            elif depth == 0 and (ch == ";" or blank.startswith(" RETURNING ", i)):
                end = i
                break
        tables = re.findall(r"\b(?:FROM|UPDATE) public\.(\w+)", blank[:w.start()])
        out.append((tables[-1] if tables else "?", flat[w.start():end].strip()))
    return out


def _assigns(body: str) -> List[str]:
    """Every `<variable> := <expression>` statement of the body (after its DECLARE block), normalised."""
    begin = re.search(r"\bBEGIN\b", _strip(body)).start()
    return [s for _at, s in _migration_sql.statements(_strip(body)[begin:]) if re.match(r"\w+ :=", s)]


def _structure_problems(name: str, body: str) -> List[str]:
    """What no static pin of a single statement sees, and Postgres would refuse at CREATE (or, worse, read as
    something else): a jsonb_build_object with an odd number of arguments (a dropped comma turns `'a', x, 'b', y`
    into three), an IF without its END IF, a BEGIN or CASE without its END, unbalanced parentheses."""
    problems = []
    flat = _migration_sql.blank_literals(_norm(_strip(body)))
    for m in re.finditer(r"\bjsonb_build_object\(", flat):
        depth, i = 1, m.end()
        while depth and i < len(flat):
            depth += (flat[i] == "(") - (flat[i] == ")")
            i += 1
        args = _migration_sql.split_top_level(flat[m.end():i - 1])
        if len(args) % 2:
            problems.append(f"{name}: a jsonb_build_object with an odd number of arguments ({args[:4]}…)")
    ifs = len(re.findall(r"(?<!END )\bIF\b", flat))
    end_ifs = len(re.findall(r"\bEND IF\b", flat))
    if ifs != end_ifs:
        problems.append(f"{name}: {ifs} IF but {end_ifs} END IF")
    opens = len(re.findall(r"\bBEGIN\b", flat)) + len(re.findall(r"\bCASE\b", flat))
    ends = len(re.findall(r"\bEND\b(?! IF\b)(?! LOOP\b)", flat))
    if opens != ends:
        problems.append(f"{name}: {opens} BEGIN / CASE but {ends} END")
    if flat.count("(") != flat.count(")"):
        problems.append(f"{name}: unbalanced parentheses")
    return problems


#: The literal tables each body must match (kept beside the model: tests/_replacement_rpc_fake.py).
_PINNED = {
    REQUEST: (rpcfake.REQUEST_SQL_BRANCHES, rpcfake.REQUEST_SQL_WHERES, rpcfake.REQUEST_SQL_ASSIGNS),
    RESTORE: (rpcfake.RESTORE_SQL_BRANCHES, rpcfake.RESTORE_SQL_WHERES, rpcfake.RESTORE_SQL_ASSIGNS),
}


def _decision_problems(name: str, body: str) -> List[str]:
    """Each branch condition, WHERE and assignment of `body` against the pinned tables, one problem per difference
    (named by its position, so a reader finds the row)."""
    problems = []
    branches, wheres, assigns = _PINNED[name]
    for what, got, want in (("branch", _branches(body), list(branches)), ("WHERE", _wheres(body), list(wheres)),
                            ("assignment", _assigns(body), list(assigns))):
        if len(got) != len(want):
            problems.append(f"{name}: {len(got)} {what}(es) where {len(want)} are pinned: {got}")
            continue
        for i, (g, w) in enumerate(zip(got, want)):
            if g != w:
                problems.append(f"{name}: {what} {i} is {g!r}, pinned {w!r}")
    return problems


def _static_215_problems(text: str) -> List[str]:
    """Why a migration-215 text breaks its contract, or [] — every check names itself."""
    problems: List[str] = []
    sql = _strip(text)
    outside = _outside_bodies(sql)
    bare = _without_literals(outside)
    stmts = [s.strip() for s in bare.split(";") if s.strip()]
    if not stmts or stmts[0].upper() != "BEGIN":
        problems.append("does not open with BEGIN")
    if not stmts or stmts[-1].upper() != "COMMIT":
        problems.append("does not end with COMMIT")

    # (a) the columns: idempotent ADD COLUMN, the exact types and defaults
    for table, col, decl in (("marketing_runs", "replacement", r"SMALLINT\s+NOT\s+NULL\s+DEFAULT\s+0"),
                             ("marketing_runs", "replaces_run_id", r"UUID"),
                             ("marketing_scripts", "replacement", r"SMALLINT\s+NOT\s+NULL\s+DEFAULT\s+0")):
        if not re.search(rf"ALTER\s+TABLE\s+public\.{table}\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+{col}\s+{decl}\s*;",
                         outside, re.I):
            problems.append(f"{table}.{col}: no ADD COLUMN IF NOT EXISTS with its declared type")

    # (b) every named constraint: DROP IF EXISTS, then ADD
    named = {
        "marketing_runs_replacement_valid": ("marketing_runs", r"CHECK\s*\(\s*replacement\s+BETWEEN\s+0\s+AND\s+(\d+)\s*\)"),
        "marketing_runs_replaces_run_id_valid": (
            "marketing_runs", r"CHECK\s*\(\s*\(\s*replacement\s*=\s*0\s*\)\s*=\s*\(\s*replaces_run_id\s+IS\s+NULL\s*\)\s*\)"),
        "marketing_runs_replaces_run_id_fkey": (
            "marketing_runs", r"FOREIGN\s+KEY\s*\(\s*replaces_run_id\s*\)\s+REFERENCES\s+public\.marketing_runs\s*"
                              r"\(\s*id\s*\)\s+ON\s+DELETE\s+RESTRICT"),
        "marketing_runs_run_date_lane_replacement_key": (
            "marketing_runs", r"UNIQUE\s*\(\s*run_date\s*,\s*lane\s*,\s*replacement\s*\)"),
        "marketing_scripts_replacement_valid": (
            "marketing_scripts", r"CHECK\s*\(\s*replacement\s+BETWEEN\s+0\s+AND\s+(\d+)\s*\)"),
    }
    adds = {}
    for name, (table, definition) in named.items():
        drop = re.search(rf"ALTER\s+TABLE\s+public\.{table}\s+DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+{name}\b", outside, re.I)
        add = re.search(rf"ALTER\s+TABLE\s+public\.{table}\s+ADD\s+CONSTRAINT\s+{name}\s+{definition}", outside, re.I)
        if not add:
            problems.append(f"{name}: no ADD with its definition")
            continue
        adds[name] = add
        if not drop or drop.start() > add.start():
            problems.append(f"{name}: not DROP IF EXISTS before ADD")
        if "BETWEEN" in definition and int(add.group(1)) != schemas.RUN_REPLACEMENT_MAX:
            problems.append(f"{name}: the CHECK bound is not RUN_REPLACEMENT_MAX")

    # (c) the claim-key swap: the new key first, then the (run_date, lane) key by DEFINITION
    blocks = list(re.finditer(r"\bDO\s+\$\$(.*?)\$\$", sql, re.S | re.I))
    if len(blocks) != 1:
        problems.append(f"expected ONE DO block dropping the (run_date, lane) key, found {len(blocks)}")
    else:
        do = blocks[0]
        for must, why in (
                (r"conrelid\s*=\s*'public\.marketing_runs'::regclass", "the DO block is not bound to marketing_runs"),
                (r"contype\s*=\s*'u'", "the DO block does not select UNIQUE constraints only"),
                (r"pg_get_constraintdef\(\s*oid\s*\)\s*=\s*'UNIQUE \(run_date, lane\)'",
                 "the DO block does not match the (run_date, lane) key by its definition"),
                (r"EXECUTE\s+format\(\s*'ALTER TABLE public\.marketing_runs DROP CONSTRAINT %I'",
                 "the DO block does not drop by a quoted identifier")):
            if not re.search(must, do.group(1), re.I):
                problems.append(why)
        key = adds.get("marketing_runs_run_date_lane_replacement_key")
        if key is not None and sql.find(do.group(0)) < sql.find(key.group(0)):
            problems.append("the (run_date, lane) key is dropped before the (run_date, lane, replacement) key is added")
    if re.search(r"DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?marketing_runs_run_date_lane_key\b", bare, re.I):
        problems.append("the (run_date, lane) key is dropped by a hard-coded name (match its definition)")

    # (d) nothing else outside the two functions: no data, table, column or trigger change
    for bad in (r"\bINSERT\s+INTO\b", r"\bUPDATE\s+\w", r"\bDELETE\s+FROM\b", r"\bDROP\s+TABLE\b", r"\bTRUNCATE\b",
                r"\bCREATE\s+TRIGGER\b", r"\bDROP\s+COLUMN\b", r"\bALTER\s+COLUMN\b", r"\bmarketing_posts\b",
                r"\bmarketing_assets\b", r"\bSECURITY\s+DEFINER\b", r"\bDROP\s+FUNCTION\b"):
        if re.search(bad, bare, re.I):
            problems.append(f"unexpected statement {bad}")
    if re.search(r"\bSECURITY\s+DEFINER\b", _without_literals(sql), re.I):
        problems.append("a SECURITY DEFINER function")

    # (e) the two functions
    found = re.findall(r"\bCREATE\s+(OR\s+REPLACE\s+)?FUNCTION\s+public\.(\w+)\s*\(", sql, re.I)
    if sorted(n for _, n in found) != sorted(_FUNCTIONS) or not all(o for o, _ in found):
        problems.append(f"expected CREATE OR REPLACE FUNCTION of exactly {_FUNCTIONS}, found {found}")
    bodies: Dict[str, str] = {}
    for name in _FUNCTIONS:
        try:
            d = _definition(text, name)
        except AssertionError:
            problems.append(f"{name}: no definition")
            continue
        bodies[name] = d.body
        header = _norm(d.header)
        params = [p.strip() for p in header.split(")", 1)[0].split(",")]
        if tuple(_norm(p) for p in params) != _SIGNATURES[name]:
            problems.append(f"{name}: the arguments are not {_SIGNATURES[name]}")
        for must in ("RETURNS JSONB", "LANGUAGE plpgsql", "SECURITY INVOKER", "SET search_path = public, pg_temp"):
            if must.lower() not in header.lower():
                problems.append(f"{name}: no {must}")
        lock = d.body.find(_LOCK_EXPR)
        if lock < 0:
            problems.append(f"{name}: does not take the per-ET-day lock")
        first_row_lock = re.search(r"\bFOR\s+UPDATE\b", d.body, re.I)
        if lock >= 0 and first_row_lock and first_row_lock.start() < lock:
            problems.append(f"{name}: a row lock is taken before the per-ET-day lock")
        if not re.search(r"\bv_now\s*:=\s*clock_timestamp\(\)", d.body[lock:] if lock >= 0 else "", re.I):
            problems.append(f"{name}: the clock is not read AFTER the lock")
        post_locks = [_norm(s) for s in re.findall(r"PERFORM\s+1\s+FROM\s+public\.marketing_posts\s+p\b[^;]*;",
                                                     d.body, re.I)]
        if len(post_locks) != 1 or not post_locks[0].endswith("ORDER BY p.id FOR UPDATE;"):
            problems.append(f"{name}: the posts are not locked once, ORDER BY p.id FOR UPDATE")
        if not re.search(r"RAISE\s+EXCEPTION[^;]*USING\s+ERRCODE\s*=\s*'22023'", d.body, re.I):
            problems.append(f"{name}: no 22023 on a missing argument")
        problems += _structure_problems(name, d.body)
        problems += _decision_problems(name, d.body)

    req, res = bodies.get(REQUEST, ""), bodies.get(RESTORE, "")
    if req:
        if _const(req, "c_per_day") != str(schemas.REPLACEMENTS_PER_DAY):
            problems.append("c_per_day is not REPLACEMENTS_PER_DAY")
        if (len(schemas.REPLACEABLE_SERIES) != 1
                or _const(req, "c_series") != f"'{schemas.REPLACEABLE_SERIES[0]}'"):
            problems.append("c_series is not the one REPLACEABLE_SERIES")
        if _const(req, "c_max") != str(schemas.RUN_REPLACEMENT_MAX):
            problems.append("c_max is not RUN_REPLACEMENT_MAX")
        if _outcomes(req) != set(schemas.REPLACEMENT_OUTCOMES):
            problems.append(f"the request's outcomes {sorted(_outcomes(req))} are not REPLACEMENT_OUTCOMES")
        blocking = re.search(r"p\.status\s+IN\s*\(([^)]*)\)\s*;\s*IF\s+v_blocking\s+IS\s+NOT\s+NULL", req, re.I)
        if not blocking or tuple(_quoted(blocking.group(1))) != schemas.REPLACEMENT_BLOCKING_POST_STATUSES:
            problems.append("the blocking statuses are not REPLACEMENT_BLOCKING_POST_STATUSES")
        # A6: decision message ids are typed numbers of non-preview posts, never the text form
        if re.search(r"->>\s*'review_message_id'", req):
            problems.append("a review_message_id is read as text (->>)")
        typed = re.findall(r"jsonb_typeof\(\s*(?:p\.metadata\s*->\s*'review_message_id'|mid)\s*\)\s*=\s*'number'",
                           req, re.I)
        previews = re.findall(r"NULLIF\(\s*(?:p\.metadata\s*->>\s*'review_preview_at'|preview)\s*,\s*''\s*\)\s+IS\s+NULL",
                              req, re.I)
        if len(typed) != 2 or len(previews) != 2:
            problems.append("the decision message ids are not numbers of non-preview posts on BOTH paths")
        if not re.search(r"p\.metadata\s*->>\s*'review_preview_at'\s+AS\s+preview", req, re.I):
            problems.append("the fresh path does not return the preview stamp it filters on")
        insert = re.search(r"INSERT\s+INTO\s+public\.marketing_runs\b(.*?);", req, re.I | re.S)
        if not insert:
            problems.append("the request inserts no run")
        else:
            ins = insert.group(1)
            if re.search(r"\bp_by\b", ins):
                problems.append("the new run carries the owner's id (the worker reads it)")
            cols = [c.strip() for c in re.search(r"\((.*?)\)\s*VALUES", ins, re.S).group(1).split(",")]
            vals = _migration_sql.split_top_level(re.search(r"VALUES\s*\((.*)\)\s*RETURNING", ins, re.S).group(1))
            row = dict(zip(cols, vals)) if len(cols) == len(vals) else {}
            if not row:
                problems.append("the INSERT's columns and values do not pair up")
            else:
                for col, want in (("status", "'planned'"), ("stage", "'planned'"), ("attempts", "0"),
                                  ("replacement", "v_next"), ("replaces_run_id", "v_run.id"),
                                  ("lane", "v_run.lane"), ("created_at", "v_now"), ("updated_at", "v_now")):
                    if row.get(col) != want:
                        problems.append(f"the new run's {col} is not {want}")
        if not re.search(r"'replaced_by'\s*,\s*jsonb_build_object\([^;]*'not_after'\s*,\s*p_not_after", req, re.I | re.S):
            problems.append("the request does not record the cutoff in replaced_by")
        if not re.search(r"AND\s+NOT\s*\(\s*v_run\.metadata\s*\?\s*'restored_from'\s*\)", req, re.I):
            problems.append("a restored run can be replayed")
        # A request that committed and lost its response is answered on a retry AFTER the cutoff too.
        replay = re.search(r"'outcome'\s*,\s*'requested'\s*,\s*'replayed'\s*,\s*true\b(.*?)\);", req, re.I | re.S)
        late = re.search(r"RETURN\s+jsonb_build_object\(\s*'outcome'\s*,\s*'too_late'", req, re.I)
        if not replay or not late or replay.start() > late.start():
            problems.append("the request's replay is not answered before its cutoff check")
        if not replay or not re.search(r"'status'\s*,\s*v_newest\.status\b", replay.group(1), re.I):
            problems.append("the request's replay does not say the replacement's status now")
    if res:
        if _const(res, "c_max_attempts") != str(schemas.REPLACEMENT_MAX_ATTEMPTS):
            problems.append("c_max_attempts is not REPLACEMENT_MAX_ATTEMPTS")
        stamps = _const(res, "c_offer_stamps")
        if stamps is None or tuple(_quoted(stamps)) != mrs._REVIEW_OFFER_STAMPS:
            problems.append("c_offer_stamps is not run_service._REVIEW_OFFER_STAMPS")
        if _outcomes(res) != set(schemas.RESTORE_OUTCOMES):
            problems.append(f"the restore's outcomes {sorted(_outcomes(res))} are not RESTORE_OUTCOMES")
        final = re.search(r"v_rep\.status\s+IN\s*\(([^)]*)\)\s*OR\s*\(\s*v_rep\.status\s*=\s*'failed'\s+AND\s+"
                          r"v_rep\.attempts\s*>=\s*c_max_attempts\s*\)", res, re.I)
        if not final or tuple(_quoted(final.group(1))) != schemas.RESTORE_FINAL_RUN_STATUSES:
            problems.append("the replacement's final statuses are not RESTORE_FINAL_RUN_STATUSES + failed at the cap")
        # A13: the replacement must have NO post of ANY status
        has_posts = [_norm(s) for s in re.findall(r"IF\s+EXISTS\s*\([^;]*marketing_posts[^;]*;", res, re.I)]
        if has_posts != ["IF EXISTS (SELECT 1 FROM public.marketing_posts p WHERE p.run_id = v_rep.id) THEN "
                         "RETURN jsonb_build_object('outcome', 'replacement_has_posts');"]:
            problems.append("the restore does not require a replacement with NO post")
        flip = re.search(r"UPDATE\s+public\.marketing_posts\s+p\b(.*?)\bRETURNING\b", res, re.I | re.S)
        if not flip:
            problems.append("the restore updates no post")
        else:
            where = _norm(flip.group(1).split("WHERE", 1)[-1])
            if where != ("p.run_id = v_run.id AND p.status = 'rejected' "
                         "AND p.metadata -> 'review' ->> 'replaced_by_run_id' = v_rep.id::text"):
                problems.append("the restore does not flip exactly the posts its replacement request rejected")
            if not re.search(r"\(\s*p\.metadata\s*-\s*'review'\s*-\s*c_offer_stamps\s*\)", flip.group(1), re.I):
                problems.append("the restore keeps the review record or the offer stamps")
            if not re.search(r"SET\s+status\s*=\s*'pending_review'", flip.group(1), re.I):
                problems.append("the restore does not put the posts back to pending_review")
        # The restore's window is the run's own ET DAY, never the tap cutoff: a replacement asked for in the last hour
        # before the cutoff only starts after it, so a cutoff-bound restore could never bring that original back.
        if re.search(r"'not_after'", res, re.I):
            problems.append("the restore reads the recorded tap cutoff (its window is the run's own ET day)")
        replay = re.search(r"IF\s+v_run\.metadata\s*\?\s*'restored_from'\s+THEN", res, re.I)
        day = re.search(r"IF\s*\(\s*v_judge\s+AT\s+TIME\s+ZONE", res, re.I)
        if not replay or not day or replay.start() > day.start():
            problems.append("the restore's replay is not answered before its day check")
        if not re.search(r"GREATEST\(\s*v_now\s*,\s*p_now\s*\)", res, re.I):
            problems.append("the restore's window is not judged by the later of the two clocks")
    if req and res and (req.count(_LOCK_EXPR) != 1 or res.count(_LOCK_EXPR) != 1):
        problems.append("the two functions do not take the SAME per-ET-day lock once each")

    # (f) the grants: REVOKE from PUBLIC, anon, authenticated, then GRANT EXECUTE to service_role, per signature
    for name in _FUNCTIONS:
        sig = rf"public\.{name}\s*\(\s*UUID\s*,\s*TEXT\s*,\s*TIMESTAMPTZ\s*\)"
        revoke = re.search(rf"REVOKE\s+ALL\s+ON\s+FUNCTION\s+{sig}\s+FROM\s+PUBLIC\s*,\s*anon\s*,\s*authenticated\s*;",
                           outside, re.I)
        grant = re.search(rf"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+{sig}\s+TO\s+service_role\s*;", outside, re.I)
        if not revoke or not grant or revoke.start() > grant.start():
            problems.append(f"{name}: not REVOKEd from PUBLIC, anon, authenticated before its GRANT to service_role")
    if re.search(r"GRANT\b[^;]*\bTO\s+[^;]*\b(?:anon|authenticated|PUBLIC)\b", bare, re.I):
        problems.append("a GRANT to anon / authenticated / PUBLIC")

    # (g) comments
    comments = {
        "TABLE public.marketing_runs": "(run_date, lane, replacement)",
        "COLUMN public.marketing_runs.replacement": "UNIQUE (run_date, lane, replacement)",
        "COLUMN public.marketing_runs.replaces_run_id": "replacement = 0",
        "COLUMN public.marketing_scripts.replacement": "insert_script",
        f"FUNCTION public.{REQUEST}(UUID, TEXT, TIMESTAMPTZ)": "replayed",
        f"FUNCTION public.{RESTORE}(UUID, TEXT, TIMESTAMPTZ)": "replayed",
    }
    for target, must in comments.items():
        got = _comment_literal(sql, target)
        if got is None:
            problems.append(f"no comment on {target}")
        elif got == "<same line>":
            problems.append(f"the {target} comment joins string literals on one line (a syntax error)")
        elif must not in got:
            problems.append(f"the {target} comment does not say {must!r}")
    return problems


# ══ the migration text ═══════════════════════════════════════════════════════════════════════════════════════════


def test_215_is_the_newest_migration_and_names_itself():
    """Numbering: 215 follows 214, no other 215 exists, and the header says what and why (owner decision quoted)."""
    names = sorted(p.name for p in _migration_sql.MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    assert [n for n in names if n.startswith("215_")] == [_MIGRATION_215.name]
    assert any(n.startswith("214_") for n in names)
    text = _MIGRATION_215.read_text(encoding="utf-8")
    assert text.startswith("-- 215_marketing_replacement_runs.sql")
    assert '"Full replacement: the next company' in text and "Max 2 replacements a day" in text


def test_migration_215_holds_its_contract():
    """The whole contract in one sweep (each check names itself): idempotent DDL, the claim-key swap matched by
    definition AFTER the new key exists, the two INVOKER functions with their constants pinned to the Python ones,
    their outcome sets, the shared per-ET-day lock before any row lock, posts locked in id order, the blocking set,
    the typed decision message ids (A6), the restore's guards (A1/A13), service_role-only grants, comments."""
    assert _static_215_problems(_MIGRATION_215.read_text(encoding="utf-8")) == []


def test_the_header_states_the_deploy_rollback_and_210_facts():
    """L5/L6: the header's operational text — apply before the web, never roll the web back while a replacement is
    within 15 ET days, the true 210-re-run behaviour (fails only before the first replacement), the Verify block
    for both functions, and what to re-run after applying."""
    text = _MIGRATION_215.read_text(encoding="utf-8")
    header = text.split("\nBEGIN;\n", 1)[0]
    assert all(line.startswith("--") or not line.strip() for line in header.splitlines())
    # the comment block as prose: each line's `--` dropped, the lines joined, runs of spaces collapsed
    header = re.sub(r"\s+", " ", " ".join(line[2:] for line in header.splitlines() if line.startswith("--")))
    for must in ("Deploy order: apply BEFORE the web deploy that carries replacements",
                 "within the last 15 ET days",
                 "Never re-run 210 after this migration",
                 "UNIQUE (run_date, lane) is back — was 210 re-run after 215?",
                 "210's ADD CONSTRAINT fails on the duplicate (run_date, lane) and 210 rolls back whole",
                 "Verify after applying (read-only)",
                 "marketing_runs_run_date_lane_replacement_key | UNIQUE (run_date, lane, replacement)",
                 "'public.marketing_restore_replaced(uuid, text, timestamptz)'",
                 "expect: f | f | t | f | f | t",
                 "scripts/dump_schema.sh", "scripts/generate_schema_doc.py", "check_function_grants.py",
                 "at most one PUBLISHING run per (day, lane)"):
        assert must in header, must
    assert "within the last 3 ET days" not in header


def test_the_live_definitions_of_both_functions_are_215s():
    """Through the suite's one SQL reader over EVERY migration: no later file redefines or drops them."""
    for name in _FUNCTIONS:
        assert _migration_sql.latest_definition(name).file == _MIGRATION_215.name


_REQUEST_LOCK_THEN_ROW = (
    "    PERFORM pg_advisory_xact_lock(hashtextextended('marketing_request_replacement:' || v_day::text, 0));\n"
    "    v_now := clock_timestamp();   -- after the lock wait: every stamp below and the cutoff use the same instant\n"
    "\n"
    "    SELECT * INTO v_run FROM public.marketing_runs WHERE id = p_run_id FOR UPDATE;\n")
_REQUEST_ROW_THEN_LOCK = (
    "    SELECT * INTO v_run FROM public.marketing_runs WHERE id = p_run_id FOR UPDATE;\n"
    "    PERFORM pg_advisory_xact_lock(hashtextextended('marketing_request_replacement:' || v_day::text, 0));\n"
    "    v_now := clock_timestamp();   -- after the lock wait: every stamp below and the cutoff use the same instant\n"
    "\n")

#: (mutation of the real file, the problem it must raise). Each is a way the migration could silently break.
_BROKEN_215 = [
    (lambda t: t.replace("BEGIN;\n", "", 1), "does not open with BEGIN"),
    (lambda t: t.rstrip().rsplit("COMMIT;", 1)[0], "does not end with COMMIT"),
    (lambda t: t.replace("ADD COLUMN IF NOT EXISTS replaces_run_id", "ADD COLUMN replaces_run_id", 1),
     "marketing_runs.replaces_run_id: no ADD COLUMN IF NOT EXISTS"),
    (lambda t: t.replace("scripts ADD COLUMN IF NOT EXISTS replacement SMALLINT NOT NULL DEFAULT 0",
                         "scripts ADD COLUMN IF NOT EXISTS replacement SMALLINT NOT NULL DEFAULT 1", 1),
     "marketing_scripts.replacement: no ADD COLUMN IF NOT EXISTS"),
    (lambda t: t.replace("marketing_runs_replacement_valid CHECK (replacement BETWEEN 0 AND 2)",
                         "marketing_runs_replacement_valid CHECK (replacement BETWEEN 0 AND 3)", 1),
     "marketing_runs_replacement_valid: the CHECK bound is not RUN_REPLACEMENT_MAX"),
    (lambda t: t.replace("ALTER TABLE public.marketing_scripts DROP CONSTRAINT IF EXISTS "
                         "marketing_scripts_replacement_valid;\n", "", 1),
     "marketing_scripts_replacement_valid: not DROP IF EXISTS before ADD"),
    (lambda t: t.replace("ON DELETE RESTRICT", "ON DELETE CASCADE", 1),
     "marketing_runs_replaces_run_id_fkey: no ADD with its definition"),
    (lambda t: t.replace("CHECK ((replacement = 0) = (replaces_run_id IS NULL))",
                         "CHECK (replacement >= 0)", 1),
     "marketing_runs_replaces_run_id_valid: no ADD with its definition"),
    (lambda t: t.replace("pg_get_constraintdef(oid) = 'UNIQUE (run_date, lane)'",
                         "conname = 'marketing_runs_run_date_lane_key'", 1), "by its definition"),
    (lambda t: t.replace("           AND contype = 'u'\n", "", 1), "UNIQUE constraints only"),
    (lambda t: _key_after_do_block(t), "dropped before the (run_date, lane, replacement) key"),
    (lambda t: t.replace("COMMIT;", "ALTER TABLE public.marketing_runs DROP CONSTRAINT IF EXISTS "
                                    "marketing_runs_run_date_lane_key;\nCOMMIT;"), "hard-coded name"),
    (lambda t: t.replace("COMMIT;", "UPDATE public.marketing_runs SET replacement = 0;\nCOMMIT;"),
     "unexpected statement"),
    (lambda t: t.replace("c_per_day   CONSTANT INTEGER := 2;", "c_per_day   CONSTANT INTEGER := 3;", 1),
     "c_per_day is not REPLACEMENTS_PER_DAY"),
    (lambda t: t.replace("c_series    CONSTANT TEXT    := 'money_map';",
                         "c_series    CONSTANT TEXT    := 'earnings';", 1), "c_series is not"),
    (lambda t: t.replace("c_max       CONSTANT INTEGER := 2;", "c_max       CONSTANT INTEGER := 1;", 1),
     "c_max is not RUN_REPLACEMENT_MAX"),
    (lambda t: t.replace("c_max_attempts CONSTANT INTEGER := 2;", "c_max_attempts CONSTANT INTEGER := 1;", 1),
     "c_max_attempts is not REPLACEMENT_MAX_ATTEMPTS"),
    (lambda t: t.replace("'review_message_id',\n                                             'review_message_ids']",
                         "'review_message_id']", 1), "c_offer_stamps is not"),
    (lambda t: t.replace("('approved', 'queued', 'published', 'failed', 'retracted')",
                         "('approved', 'queued', 'published', 'failed')", 1),
     "the blocking statuses are not REPLACEMENT_BLOCKING_POST_STATUSES"),
    (lambda t: t.replace("WHERE jsonb_typeof(mid) = 'number' AND", "WHERE", 1),
     "the decision message ids are not numbers of non-preview posts"),
    (lambda t: t.replace("p.metadata -> 'review_message_id' AS mid", "p.metadata ->> 'review_message_id' AS mid", 1),
     "read as text"),
    (lambda t: t.replace("AND NULLIF(p.metadata ->> 'review_preview_at', '') IS NULL),", "),", 1),
     "the decision message ids are not numbers of non-preview posts"),
    (lambda t: t.replace("LANGUAGE plpgsql\nSECURITY INVOKER", "LANGUAGE plpgsql\nSECURITY DEFINER", 1),
     "a SECURITY DEFINER function"),
    (lambda t: t.replace("LANGUAGE plpgsql\nSECURITY INVOKER", "LANGUAGE plpgsql", 1), f"{REQUEST}: no SECURITY INVOKER"),
    (lambda t: t.replace("SET search_path = public, pg_temp\nAS $$\nDECLARE\n    -- Pinned equal to "
                         "app/schemas/marketing.py REPLACEMENTS_PER_DAY",
                         "AS $$\nDECLARE\n    -- Pinned equal to app/schemas/marketing.py REPLACEMENTS_PER_DAY", 1),
     f"{REQUEST}: no SET search_path"),
    (lambda t: t.replace("    PERFORM pg_advisory_xact_lock(hashtextextended('marketing_request_replacement:' || "
                         "v_day::text, 0));\n    v_now := clock_timestamp();\n\n    SELECT * INTO v_run FROM "
                         "public.marketing_runs WHERE id = p_run_id FOR UPDATE;\n    IF NOT FOUND THEN\n        RETURN "
                         "jsonb_build_object('outcome', 'not_found');\n    END IF;\n\n    -- (1)",
                         "    v_now := clock_timestamp();\n\n    SELECT * INTO v_run FROM "
                         "public.marketing_runs WHERE id = p_run_id FOR UPDATE;\n    IF NOT FOUND THEN\n        RETURN "
                         "jsonb_build_object('outcome', 'not_found');\n    END IF;\n\n    -- (1)", 1),
     f"{RESTORE}: does not take the per-ET-day lock"),
    (lambda t: t.replace("'marketing_request_replacement:' || v_day::text, 0));\n    v_now := clock_timestamp();\n\n"
                         "    SELECT * INTO v_run FROM public.marketing_runs WHERE id = p_run_id FOR UPDATE;\n"
                         "    IF NOT FOUND THEN\n        RETURN jsonb_build_object('outcome', 'not_found');\n"
                         "    END IF;\n\n    -- (1)",
                         "'marketing_restore_replaced:' || v_day::text, 0));\n    v_now := clock_timestamp();\n\n"
                         "    SELECT * INTO v_run FROM public.marketing_runs WHERE id = p_run_id FOR UPDATE;\n"
                         "    IF NOT FOUND THEN\n        RETURN jsonb_build_object('outcome', 'not_found');\n"
                         "    END IF;\n\n    -- (1)", 1),
     f"{RESTORE}: does not take the per-ET-day lock"),
    (lambda t: t.replace(_REQUEST_LOCK_THEN_ROW, _REQUEST_ROW_THEN_LOCK, 1),
     f"{REQUEST}: a row lock is taken before the per-ET-day lock"),
    (lambda t: t.replace("    PERFORM pg_advisory_xact_lock(hashtextextended('marketing_request_replacement:' || "
                         "v_day::text, 0));\n    v_now := clock_timestamp();\n",
                         "    v_now := clock_timestamp();\n    PERFORM pg_advisory_xact_lock(hashtextextended("
                         "'marketing_request_replacement:' || v_day::text, 0));\n", 1),
     f"{RESTORE}: the clock is not read AFTER the lock"),
    (lambda t: t.replace("PERFORM 1 FROM public.marketing_posts p WHERE p.run_id = p_run_id ORDER BY p.id FOR UPDATE;",
                         "PERFORM 1 FROM public.marketing_posts p WHERE p.run_id = p_run_id FOR UPDATE;", 1),
     f"{REQUEST}: the posts are not locked once, ORDER BY p.id FOR UPDATE"),
    (lambda t: t.replace("'at', v_now, 'from_run_id', v_run.id,", "'at', v_now, 'by', p_by, 'from_run_id', v_run.id,", 1),
     "carries the owner's id"),
    (lambda t: t.replace("         v_now, v_now)\n    RETURNING id INTO v_new_id;", "         now(), v_now)\n"
                         "    RETURNING id INTO v_new_id;", 1), "the new run's created_at is not v_now"),
    (lambda t: t.replace(",\n               'replacements_today', v_today + 1, 'not_after', p_not_after)),",
                         ",\n               'replacements_today', v_today + 1)),", 1),
     "does not record the cutoff"),
    (lambda t: t.replace("\n       AND NOT (v_run.metadata ? 'restored_from') THEN", " THEN", 1),
     "a restored run can be replayed"),
    (lambda t: t.replace("IF EXISTS (SELECT 1 FROM public.marketing_posts p WHERE p.run_id = v_rep.id) THEN",
                         "IF EXISTS (SELECT 1 FROM public.marketing_posts p WHERE p.run_id = v_rep.id "
                         "AND p.status <> 'rejected') THEN", 1),
     "a replacement with NO post"),
    (lambda t: t.replace("v_rep.status IN ('skipped', 'published', 'media_ready')",
                         "v_rep.status IN ('skipped', 'published', 'media_ready', 'in_progress')", 1),
     "RESTORE_FINAL_RUN_STATUSES"),
    (lambda t: t.replace("OR (v_rep.status = 'failed' AND v_rep.attempts >= c_max_attempts)",
                         "OR v_rep.status = 'failed'", 1), "RESTORE_FINAL_RUN_STATUSES"),
    (lambda t: t.replace("           AND p.status = 'rejected'\n", "", 1),
     "flip exactly the posts its replacement request rejected"),
    (lambda t: t.replace("(p.metadata - 'review' - c_offer_stamps)", "(p.metadata - 'review')", 1),
     "keeps the review record or the offer stamps"),
    (lambda t: t.replace("v_judge := GREATEST(v_now, p_now);", "v_judge := p_now;", 1), "the later of the two clocks"),
    (lambda t: t.replace("RETURN jsonb_build_object('outcome', 'replacement_has_posts');",
                         "RETURN jsonb_build_object('outcome', 'has_posts');", 1), "are not RESTORE_OUTCOMES"),
    (lambda t: t.replace("RETURN jsonb_build_object('outcome', 'nothing_pending');",
                         "RETURN jsonb_build_object('outcome', 'nothing_left');", 1), "are not REPLACEMENT_OUTCOMES"),
    (lambda t: t.replace("REVOKE ALL ON FUNCTION public.marketing_restore_replaced(UUID, TEXT, TIMESTAMPTZ)\n"
                         "    FROM PUBLIC, anon, authenticated;",
                         "REVOKE ALL ON FUNCTION public.marketing_restore_replaced(UUID, TEXT, TIMESTAMPTZ)\n"
                         "    FROM PUBLIC;", 1), f"{RESTORE}: not REVOKEd"),
    (lambda t: t.replace("COMMIT;", "GRANT EXECUTE ON FUNCTION public.marketing_request_replacement(UUID, TEXT, "
                                    "TIMESTAMPTZ) TO authenticated;\nCOMMIT;"), "a GRANT to anon"),
    (lambda t: t.replace("(UNIQUE (run_date, lane, replacement), '", "(UNIQUE (run_date, lane), '", 1),
     "TABLE public.marketing_runs comment does not say"),
    (lambda t: t.replace("'§12). The media worker CLAIMS a day''s run by inserting this row (UNIQUE (run_date, lane, "
                         "replacement), '\n",
                         "'§12). The media worker CLAIMS a day''s run by inserting this row (UNIQUE (run_date, lane, "
                         "replacement), ' ", 1), "one line"),
    # ── what each statement decides (review 2026-11-27: every one of these passed the marker-order pin) ──
    (lambda t: t.replace("IF v_today >= c_per_day OR", "IF v_today > c_per_day OR", 1),
     f"{REQUEST}: branch 11"),                                          # three a day
    (lambda t: t.replace("IF v_today >= c_per_day OR v_run.replacement >= c_max THEN",
                         "IF v_today >= c_per_day THEN", 1), f"{REQUEST}: branch 11"),
    (lambda t: t.replace("         WHERE p.run_id = p_run_id AND p.status = 'pending_review'\n        RETURNING",
                         "         WHERE p.run_id = p_run_id\n        RETURNING", 1),
     f"{REQUEST}: WHERE 11"),                                           # rejects owner-rejected / skipped posts too
    (lambda t: t.replace("     ORDER BY r.replacement DESC\n     LIMIT 1;\n    IF v_newest.replaces_run_id",
                         "     ORDER BY r.replacement ASC\n     LIMIT 1;\n    IF v_newest.replaces_run_id", 1),
     f"{REQUEST}: WHERE 2"),                                            # the replay can never match
    (lambda t: t.replace("IF v_newest.replacement > v_run.replacement THEN",
                         "IF v_newest.replacement >= v_run.replacement THEN", 1), f"{REQUEST}: branch 5"),
    (lambda t: t.replace("IF v_run.status <> 'media_ready' THEN\n        RETURN jsonb_build_object("
                         "'outcome', 'not_ready'",
                         "IF false THEN\n        RETURN jsonb_build_object('outcome', 'not_ready'", 1),
     f"{REQUEST}: branch 6"),
    (lambda t: t.replace("IF v_script.status <> 'accepted' OR v_script.template_id IS DISTINCT FROM c_series THEN",
                         "IF v_script.template_id IS DISTINCT FROM c_series THEN", 1), f"{REQUEST}: branch 8"),
    (lambda t: t.replace("    SELECT count(*) INTO v_pending FROM public.marketing_posts p\n"
                         "     WHERE p.run_id = p_run_id AND p.status = 'pending_review';",
                         "    SELECT count(*) INTO v_pending FROM public.marketing_posts p\n"
                         "     WHERE p.run_id = p_run_id;", 1), f"{REQUEST}: WHERE 9"),
    (lambda t: t.replace("IF v_pending = 0 THEN", "IF v_pending < 0 THEN", 1), f"{REQUEST}: branch 10"),
    (lambda t: t.replace("IF v_newest.replaces_run_id = v_run.id AND",
                         "IF true OR v_newest.replaces_run_id = v_run.id AND", 1), f"{REQUEST}: branch 3"),
    (lambda t: t.replace("            'run_id', v_newest.id,\n", "            'run_id', v_newest.id\n", 1),
     "odd number of arguments"),                                       # a dropped comma in the replay payload
    (lambda t: t.replace("    IF v_pending = 0 THEN\n        RETURN jsonb_build_object('outcome', "
                         "'nothing_pending');\n    END IF;\n",
                         "    IF v_pending = 0 THEN\n        RETURN jsonb_build_object('outcome', "
                         "'nothing_pending');\n", 1), f"{REQUEST}: 12 IF but 11 END IF"),
    (lambda t: t.replace("    IF (v_judge AT TIME ZONE c_zone)::date <> v_run.run_date THEN",
                         "    IF false THEN", 1), f"{RESTORE}: branch 11"),
    (lambda t: t.replace("IF NOT (v_rep.status IN ('skipped', 'published', 'media_ready')",
                         "IF NOT (v_rep.status IN ('skipped', 'published', 'media_ready', 'planned')", 1),
     f"{RESTORE}: branch 8"),
    (lambda t: t.replace("     ORDER BY r.replacement DESC\n     LIMIT 1;\n    IF v_newest IS DISTINCT FROM v_rep_id",
                         "     ORDER BY r.replacement ASC\n     LIMIT 1;\n"
                         "    IF v_newest IS DISTINCT FROM v_rep_id", 1),
     f"{RESTORE}: WHERE 2"),
    (lambda t: t.replace("IF v_rep.replaces_run_id IS DISTINCT FROM v_run.id THEN",
                         "IF v_rep.replaces_run_id IS NULL THEN", 1), f"{RESTORE}: branch 7"),
    (lambda t: t.replace("    IF v_run.metadata ? 'restored_from' THEN", "    IF true THEN", 1),
     f"{RESTORE}: branch 10"),
    (lambda t: t.replace("    IF v_run.status <> 'media_ready' THEN\n        RETURN jsonb_build_object('outcome', "
                         "'not_restorable'", "    IF false THEN\n        RETURN jsonb_build_object('outcome', "
                         "'not_restorable'", 1), f"{RESTORE}: branch 3"),
    (lambda t: t.replace("v_rep_id := v_rep_text::uuid;", "v_rep_id := v_run.id;", 1), f"{RESTORE}: assignment 2"),
    # the restore window is the run's own ET DAY (never the tap cutoff), its replay answered before that check
    (lambda t: t.replace("    v_judge := GREATEST(v_now, p_now);\n    IF (v_judge AT TIME ZONE c_zone)::date <> "
                         "v_run.run_date THEN",
                         "    v_judge := GREATEST(v_now, p_now);\n    IF v_judge >= "
                         "(v_run.metadata -> 'replaced_by' ->> 'not_after')::timestamptz "
                         "OR (v_judge AT TIME ZONE c_zone)::date <> v_run.run_date THEN", 1),
     "reads the recorded tap cutoff"),
    (lambda t: _restore_day_check_first(t), "the restore's replay is not answered before its day check"),
    (lambda t: _request_cutoff_first(t), "the request's replay is not answered before its cutoff check"),
    (lambda t: t.replace("            'status', v_newest.status,\n", "", 1),
     "the request's replay does not say the replacement's status now"),
]

_RESTORE_REPLAY = (
    "    -- (5) already restored: answer again, read back, never a second write. Answered BEFORE the day check (4): a\n"
    "    -- restore that committed just before midnight and lost its response is still answered `restored` on a "
    "retry.\n"
    "    IF v_run.metadata ? 'restored_from' THEN\n"
    "        RETURN jsonb_build_object(\n"
    "            'outcome', 'restored', 'replayed', true,\n"
    "            'post_ids', COALESCE(\n"
    "                (SELECT jsonb_agg(p.id ORDER BY p.id) FROM public.marketing_posts p\n"
    "                  WHERE p.run_id = v_run.id\n"
    "                    AND p.metadata -> 'restored' ->> 'by_run_id' = v_rep.id::text),\n"
    "                '[]'::jsonb));\n"
    "    END IF;\n"
    "\n")
_RESTORE_DAY = (
    "    v_judge := GREATEST(v_now, p_now);\n"
    "    IF (v_judge AT TIME ZONE c_zone)::date <> v_run.run_date THEN\n"
    "        RETURN jsonb_build_object('outcome', 'too_late');\n"
    "    END IF;\n")
_REQUEST_CUTOFF = (
    "    IF v_now >= p_not_after OR (v_now AT TIME ZONE c_zone)::date <> v_run.run_date THEN\n"
    "        RETURN jsonb_build_object('outcome', 'too_late');\n"
    "    END IF;\n")


def _restore_day_check_first(text: str) -> str:
    """The real file with the restore's day check moved back ABOVE its replay (the order of before the fix)."""
    assert _RESTORE_REPLAY in text and _RESTORE_DAY in text, "the fixture text moved"
    return text.replace(_RESTORE_REPLAY, "", 1).replace(_RESTORE_DAY, "", 1).replace(
        "    -- Every post of the run, locked in ONE order (id), as marketing_request_replacement locks them.\n",
        _RESTORE_DAY + "\n" + _RESTORE_REPLAY
        + "    -- Every post of the run, locked in ONE order (id), as marketing_request_replacement locks them.\n", 1)


def _request_cutoff_first(text: str) -> str:
    """The real file with the request's cutoff check moved back ABOVE its replay (the order of before the fix)."""
    anchor = ("    -- The (day, lane)'s newest run: a retried request finds its own replacement there and is answered "
              "again,\n")
    assert _REQUEST_CUTOFF in text and anchor in text, "the fixture text moved"
    return text.replace("\n" + _REQUEST_CUTOFF, "", 1).replace(anchor, _REQUEST_CUTOFF + "\n" + anchor, 1)

_215_KEY = ("ALTER TABLE public.marketing_runs DROP CONSTRAINT IF EXISTS marketing_runs_run_date_lane_replacement_key;\n"
            "ALTER TABLE public.marketing_runs\n"
            "    ADD CONSTRAINT marketing_runs_run_date_lane_replacement_key UNIQUE (run_date, lane, replacement);\n")


def _key_after_do_block(text: str) -> str:
    """The real file with the new key added AFTER the DO block drops the old one (a window with no claim key)."""
    assert _215_KEY in text, "the fixture text moved"
    return text.replace(_215_KEY, "", 1).replace("-- (c) marketing_scripts.replacement",
                                                  _215_KEY + "\n-- (c) marketing_scripts.replacement", 1)


@pytest.mark.parametrize("mutate, expect", _BROKEN_215)
def test_the_215_static_check_fails_on_each_broken_variant(mutate, expect):
    """Guard against the guard: each mutation of the real file is caught, by the named check."""
    original = _MIGRATION_215.read_text(encoding="utf-8")
    broken = mutate(original)
    assert broken != original, "the mutation did not apply — the fixture text moved"
    problems = _static_215_problems(broken)
    assert any(expect in p for p in problems), problems


def test_the_blocking_set_covers_every_status_from_which_a_post_can_reach_a_platform():
    """A13: the request refuses a run with a post in ANY status that can still reach a platform (approved → queued →
    published) or already did (published, failed after a send, retracted); only pending_review, rejected and skipped
    never post. This is what keeps at most one PUBLISHING run per (day, lane)."""
    blocking = set(schemas.REPLACEMENT_BLOCKING_POST_STATUSES)
    assert blocking == set(schemas.POST_STATUSES) - {"pending_review", "rejected", "skipped"}
    assert {"approved", "queued", "published", "failed", "retracted"} <= blocking
    assert tuple(rpcfake.BLOCKING) == schemas.REPLACEMENT_BLOCKING_POST_STATUSES


def test_the_offer_stamps_the_restore_removes_are_the_run_services():
    """A1: the restore clears exactly the stamps that mark a post as offered (`_REVIEW_OFFER_STAMPS`, the tuple
    `review_bundle`'s release uses), so the next review sweep offers the restored posts as a fresh bundle. The SQL
    list, the fake's and the Python tuple are one list."""
    body = _definition(_MIGRATION_215.read_text(encoding="utf-8"), RESTORE).body
    assert tuple(_quoted(_const(body, "c_offer_stamps"))) == mrs._REVIEW_OFFER_STAMPS
    assert rpcfake.C_OFFER_STAMPS == mrs._REVIEW_OFFER_STAMPS
    assert "review_preview_at" not in mrs._REVIEW_OFFER_STAMPS   # a preview keeps its preview stamp


def test_the_fake_constants_are_the_sql_and_python_ones():
    body_req = _definition(_MIGRATION_215.read_text(encoding="utf-8"), REQUEST).body
    assert (rpcfake.C_PER_DAY, rpcfake.C_MAX, rpcfake.C_SERIES) == (
        schemas.REPLACEMENTS_PER_DAY, schemas.RUN_REPLACEMENT_MAX, schemas.REPLACEABLE_SERIES[0])
    assert _const(body_req, "c_per_day") == str(rpcfake.C_PER_DAY)
    assert rpcfake.C_MAX_ATTEMPTS == schemas.REPLACEMENT_MAX_ATTEMPTS
    assert rpcfake.FINAL_RUN_STATUSES == schemas.RESTORE_FINAL_RUN_STATUSES
    assert (rpcfake.REQUEST, rpcfake.RESTORE) == (schemas.REPLACEMENT_REQUEST_RPC, schemas.REPLACEMENT_RESTORE_RPC)


# ══ the snapshot forward guard ═══════════════════════════════════════════════════════════════════════════════════


_OLD_KEY_RE = re.compile(r"ALTER TABLE ONLY public\.marketing_runs\s+ADD CONSTRAINT \w+ UNIQUE \(run_date, lane\);")


def _snapshot_215_problems(snapshot: str) -> List[str]:
    """Why a schema snapshot contradicts migration 215, or [] — before 215 (no `replacement` column) the 210 key the
    DO block matches must be there; after it, the new key, columns, CHECKs, FK and revoked functions, and NOT the old
    key."""
    problems: List[str] = []
    runs_body = re.search(r"CREATE TABLE public\.marketing_runs \((.*?)\n\);", snapshot, re.S).group(1)
    if not re.search(r"^\s+replacement smallint", runs_body, re.M):
        if not _OLD_KEY_RE.search(snapshot):
            problems.append("pre-215: the UNIQUE (run_date, lane) key 215's DO block matches is missing")
        if REQUEST in snapshot or RESTORE in snapshot:
            problems.append("pre-215: a 215 function without its column")
        return problems
    for must, why in (
            (r"^\s+replacement smallint DEFAULT 0 NOT NULL,?$", "marketing_runs.replacement is not smallint 0 NOT NULL"),
            (r"^\s+replaces_run_id uuid,?$", "marketing_runs.replaces_run_id is not a nullable uuid")):
        if not re.search(must, runs_body, re.M):
            problems.append(why)
    for name in ("marketing_runs_replacement_valid", "marketing_scripts_replacement_valid",
                 "marketing_runs_replaces_run_id_valid"):
        if f"CONSTRAINT {name} CHECK" not in snapshot:
            problems.append(f"no {name}")
    if not re.search(r"ADD CONSTRAINT marketing_runs_replaces_run_id_fkey FOREIGN KEY \(replaces_run_id\) "
                     r"REFERENCES public\.marketing_runs\(id\) ON DELETE RESTRICT", snapshot):
        problems.append("no replaces_run_id FK ON DELETE RESTRICT")
    if not re.search(r"ADD CONSTRAINT marketing_runs_run_date_lane_replacement_key UNIQUE \(run_date, lane, "
                     r"replacement\);", snapshot):
        problems.append("no UNIQUE (run_date, lane, replacement)")
    if _OLD_KEY_RE.search(snapshot):
        problems.append("the two-column UNIQUE (run_date, lane) key is still there")
    for name in _FUNCTIONS:
        if not re.search(rf"CREATE FUNCTION public\.{name}\(", snapshot):
            problems.append(f"no {name}")
        for role in ("anon", "authenticated", "PUBLIC"):
            if re.search(rf"GRANT (?:ALL|EXECUTE) ON FUNCTION public\.{name}\([^)]*\) TO {role};", snapshot):
                problems.append(f"{name} is granted to {role}")
    return problems


def _as_if_215_applied(snapshot: str) -> str:
    """The real snapshot as pg_dump would print it after 215: the columns appended, the key swapped, the functions."""
    out = snapshot.replace("    lane text DEFAULT 'main'::text NOT NULL,\n    CONSTRAINT marketing_runs_content_class_check",
                           "    lane text DEFAULT 'main'::text NOT NULL,\n    replacement smallint DEFAULT 0 NOT NULL,\n"
                           "    replaces_run_id uuid,\n    CONSTRAINT marketing_runs_content_class_check", 1)
    out = out.replace("    CONSTRAINT marketing_runs_lane_valid CHECK",
                      "    CONSTRAINT marketing_runs_replacement_valid CHECK (((replacement >= 0) AND (replacement <= 2))),\n"
                      "    CONSTRAINT marketing_runs_replaces_run_id_valid CHECK (((replacement = 0) = "
                      "(replaces_run_id IS NULL))),\n    CONSTRAINT marketing_runs_lane_valid CHECK", 1)
    out = out.replace("    CONSTRAINT marketing_scripts_lane_valid CHECK",
                      "    CONSTRAINT marketing_scripts_replacement_valid CHECK (((replacement >= 0) AND "
                      "(replacement <= 2))),\n    CONSTRAINT marketing_scripts_lane_valid CHECK", 1)
    out = _OLD_KEY_RE.sub("ALTER TABLE ONLY public.marketing_runs\n    ADD CONSTRAINT "
                          "marketing_runs_run_date_lane_replacement_key UNIQUE (run_date, lane, replacement);", out, 1)
    out += ("\nALTER TABLE ONLY public.marketing_runs\n    ADD CONSTRAINT marketing_runs_replaces_run_id_fkey FOREIGN KEY "
            "(replaces_run_id) REFERENCES public.marketing_runs(id) ON DELETE RESTRICT;\n")
    for name, arg in ((REQUEST, "p_not_after"), (RESTORE, "p_now")):
        out += (f"\nCREATE FUNCTION public.{name}(p_run_id uuid, p_by text, {arg} timestamp with time zone) "
                f"RETURNS jsonb\n    LANGUAGE plpgsql\n    AS $$ $$;\n"
                f"REVOKE ALL ON FUNCTION public.{name}(p_run_id uuid, p_by text, {arg} timestamp with time zone) "
                f"FROM PUBLIC;\nGRANT ALL ON FUNCTION public.{name}(p_run_id uuid, p_by text, {arg} timestamp with "
                f"time zone) TO service_role;\n")
    return out


def test_once_215_is_in_the_snapshot_its_columns_key_and_functions_hold_there_too():
    """Forward guard for the re-dump after the owner applies 215: once the snapshot carries `replacement smallint`,
    the claim key is UNIQUE (run_date, lane, replacement), NO two-column (run_date, lane) key remains (the DO block's
    drop really happened), both CHECKs and the FK are there, and both functions are revoked from anon and
    authenticated. Until then this checks the pre-215 state is what 215 expects to find: the 210 key its DO block
    matches by definition."""
    assert _snapshot_215_problems(_SNAPSHOT.read_text(encoding="utf-8")) == []


def test_the_snapshot_guard_s_post_215_branch_is_not_vacuous():
    """Both branches of the guard bite. The post-215 branch is run on a 215 snapshot: the real one since the re-dump of
    2026-11-27 (it already holds 215), or the real one rewritten as pg_dump would print it after 215 while it did not.
    It must come out clean, and each way the apply could have gone wrong must be named (the old key kept, a function
    left granted, the FK missing). The pre-215 branch is run on a synthetic pre-215 table, which must name a missing
    210 key."""
    real = _SNAPSHOT.read_text(encoding="utf-8")
    applied = real if re.search(r"^\s+replacement smallint", real, re.M) else _as_if_215_applied(real)
    assert "replacement smallint" in applied
    assert _snapshot_215_problems(applied) == []
    kept_old = applied + ("\nALTER TABLE ONLY public.marketing_runs\n    ADD CONSTRAINT marketing_runs_run_date_lane_key "
                          "UNIQUE (run_date, lane);\n")
    assert "the two-column UNIQUE (run_date, lane) key is still there" in _snapshot_215_problems(kept_old)
    granted = applied + (f"GRANT ALL ON FUNCTION public.{RESTORE}(p_run_id uuid, p_by text, p_now timestamp with "
                         f"time zone) TO anon;\n")
    assert f"{RESTORE} is granted to anon" in _snapshot_215_problems(granted)
    no_fk = applied.replace("ON DELETE RESTRICT;\n", ";\n")
    assert "no replaces_run_id FK ON DELETE RESTRICT" in _snapshot_215_problems(no_fk)
    # The pre-215 branch, on a pre-215 table (no `replacement` column): the 210 key must be found, and its absence named.
    pre_215 = "CREATE TABLE public.marketing_runs (\n    id uuid NOT NULL,\n    lane text DEFAULT 'main'::text NOT NULL\n);\n"
    old_key = ("ALTER TABLE ONLY public.marketing_runs\n    ADD CONSTRAINT marketing_runs_run_date_lane_key "
               "UNIQUE (run_date, lane);\n")
    assert _snapshot_215_problems(pre_215 + old_key) == []
    assert "pre-215: the UNIQUE (run_date, lane) key 215's DO block matches is missing" in _snapshot_215_problems(
        pre_215)
    assert "pre-215: a 215 function without its column" in _snapshot_215_problems(pre_215 + old_key + REQUEST)


# ══ the constants and the switch ═════════════════════════════════════════════════════════════════════════════════


def test_the_replacement_constants():
    assert schemas.RUN_REPLACEMENT_MAX == 2 and schemas.REPLACEMENTS_PER_DAY == 2
    assert schemas.RUNS_PER_DAY_MAX == len(schemas.RUN_LANES) + schemas.REPLACEMENTS_PER_DAY == 4
    assert schemas.REPLACEABLE_SERIES == ("money_map",)
    # a replacement only ever posts Money Map: it must exist and be shipped
    assert set(schemas.REPLACEABLE_SERIES) <= set(selection.SERIES_BY_ID)
    assert set(schemas.REPLACEABLE_SERIES) <= set(selection.SHIPPED_SERIES)
    assert schemas.REPLACEMENT_MAX_ATTEMPTS == 2
    assert schemas.REPLACEMENT_OUTCOMES[0] == "requested" and schemas.RESTORE_OUTCOMES[0] == "restored"
    for group in (schemas.REPLACEMENT_OUTCOMES, schemas.RESTORE_OUTCOMES, schemas.REPLACEMENT_REFUSALS_LOCAL,
                  schemas.REPLACEMENT_EXPIRED_WHY, schemas.REPLACEMENT_BLOCKING_POST_STATUSES,
                  schemas.RESTORE_FINAL_RUN_STATUSES):
        assert len(set(group)) == len(group), group
    # a local refusal is never an SQL outcome, and "too_late" is checked on both sides but is an SQL outcome
    assert not set(schemas.REPLACEMENT_REFUSALS_LOCAL) & set(schemas.REPLACEMENT_OUTCOMES)
    assert set(schemas.REPLACEMENT_REFUSALS_LOCAL) == {"switched_off", "lane_closed", "money_map_off"}
    # the expiry whys: "swept" was unreachable and dropped (A10); the web clock adds lane_closed
    assert schemas.REPLACEMENT_EXPIRED_WHY == ("start_window_closed", "day_over", "switched_off", "lane_closed")
    assert schemas.REPLACEMENT_SKIP_REASON == "replacement_expired"
    assert schemas.REPLACEMENT_SKIP_REASON not in schemas.SCRIPT_REJECT_REASONS   # a run skip, never a script reason
    assert set(schemas.RESTORE_FINAL_RUN_STATUSES) <= set(schemas.RUN_STATUSES)
    assert "failed" not in schemas.RESTORE_FINAL_RUN_STATUSES      # only at its attempts cap, checked apart
    assert "planned" not in schemas.RESTORE_FINAL_RUN_STATUSES and "in_progress" not in schemas.RESTORE_FINAL_RUN_STATUSES


def test_the_replacement_metadata_keys_are_server_owned_and_appended_last():
    """A worker PATCH may write none of them (run_service.update_run strips SERVER_OWNED_RUN_METADATA); appended
    after the five older keys, in this order, so nothing older moved."""
    new = (schemas.RUN_META_REPLACED_BY, schemas.RUN_META_REPLACEMENT_REQUEST, schemas.RUN_META_REPLACEMENT_REPORTED,
           schemas.RUN_META_REPLACEMENT_EXPIRED, schemas.RUN_META_RESTORED_FROM)
    assert new == ("replaced_by", "replacement_request", "replacement_reported", "replacement_expired",
                   "restored_from")
    assert schemas.SERVER_OWNED_RUN_METADATA[:5] == ("claim_nonce", "closed", "worker_capabilities", "series",
                                                     "series_trail")
    assert schemas.SERVER_OWNED_RUN_METADATA[5:] == new


def test_the_replace_switch_is_declared_off():
    """Owner: "off until you switch it on". Declared before any read (test_settings_attribute_parity), a bool, False."""
    field = Settings.model_fields["MARKETING_REPLACE_ENABLED"]
    assert field.annotation is bool and field.default is False
    assert hasattr(settings, "MARKETING_REPLACE_ENABLED")


# ══ the fake against the SQL: statement order ════════════════════════════════════════════════════════════════════

D = "2026-11-27"
NOW = datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc)            # 12:00 ET (EST)
CUTOFF_MAIN = datetime(2026, 11, 28, 3, 10, tzinfo=timezone.utc)    # 22:10 ET
RUN = "00000000-0000-4000-8000-000000000001"
OTHER_LANE_RUN = "00000000-0000-4000-8000-000000000002"
P = [f"00000000-0000-4000-8000-0000000001{i:02d}" for i in range(10)]
SOURCE_REF = "news:money_map:NVDA:2026"


def _ids():
    n = iter(range(900, 10_000))
    return lambda: f"00000000-0000-4000-8000-000000000{next(n):03d}"


def _post(pid: str, run_id: str = RUN, *, status: str = "pending_review", platform: str = "facebook",
          fmt: str = "video", **meta) -> dict:
    return {"id": pid, "run_id": run_id, "platform": platform, "format": fmt, "status": status,
            "idempotency_key": f"{D}:{platform}:{fmt}", "updated_at": "2026-11-27T16:50:00+00:00",
            "metadata": {"generation_id": "g-1", **meta}}


def _world(*, status: str = "media_ready", template: Optional[str] = "money_map", script_status: str = "accepted",
           posts: Optional[List[dict]] = None, clock: Optional[Callable[[], datetime]] = None,
           lane: Optional[str] = None) -> ReplacementRpcFake:
    run = {"id": RUN, "run_date": D, "status": status, "stage": "assets_ready", "content_class": "F",
           "dry_run": False, "attempts": 1, "metadata": {"claim_nonce": "n" * 32, "series": "money_map"},
           "created_at": "2026-11-27T15:15:00+00:00", "updated_at": "2026-11-27T16:40:00+00:00"}
    if lane is not None:
        run["lane"] = lane
    scripts = [] if template is None else [{"run_id": RUN, "status": script_status, "template_id": template,
                                            "source_ref": SOURCE_REF, "lane": lane or "main"}]
    if posts is None:
        posts = [
            _post(P[0], review_message_id=501, review_notified_at="t", review_bundle={"id": "b-video"}),
            _post(P[1], fmt="image", review_message_id=502, review_notified_at="t", review_bundle={"id": "b-post"}),
            _post(P[2], platform="linkedin", fmt="image", review_message_id=502, review_notified_at="t"),
            # a read-only preview: its id is the CAPTION message (no keyboard) — never a decision message (A6)
            _post(P[3], platform="threads", fmt="text", review_message_id=503, review_preview_at="t"),
            # an id stamped as text by an older writer: not a number, so not returned (A6)
            _post(P[4], platform="bluesky", fmt="text", review_message_id="504", review_notified_at="t"),
            # a skipped post never posts and is not pending: untouched
            _post(P[5], platform="x", fmt="text", status="skipped"),
        ]
    return ReplacementRpcFake([run], scripts, posts, clock=clock or (lambda: NOW), new_id=_ids())


def _request(fake: ReplacementRpcFake, run_id: str = RUN, *, by: str = "telegram:42",
             not_after: Any = CUTOFF_MAIN) -> dict:
    stamp = not_after.isoformat() if isinstance(not_after, datetime) else not_after
    return fake.rpc(REQUEST, {"p_run_id": run_id, "p_by": by, "p_not_after": stamp}).execute().data


def _restore(fake: ReplacementRpcFake, run_id: str = RUN, *, by: str = "telegram:42", now: datetime = NOW) -> dict:
    return fake.rpc(RESTORE, {"p_run_id": run_id, "p_by": by, "p_now": now.isoformat()}).execute().data


def _sql_markers(name: str) -> List[str]:
    return _markers(_definition(_MIGRATION_215.read_text(encoding="utf-8"), name).body)


def _a_prefix_ending_at(trace: List[str], markers: List[str]) -> bool:
    """`trace` is `markers` up to and including SOME occurrence of the trace's last statement."""
    return bool(trace) and any(markers[:i + 1] == trace for i, m in enumerate(markers) if m == trace[-1])


def test_the_sql_markers_are_what_the_pin_expects():
    """The marker scan is not vacuous: the request's and the restore's statement lists, read off the SQL."""
    req = _sql_markers(REQUEST)
    assert req == ["not_found", "lock", "lock:marketing_runs", "not_found", "requested:replayed", "too_late",
                   "already_replaced", "not_ready", "not_money_map", "not_money_map", "lock:marketing_posts",
                   "member_decided", "nothing_pending", "cap_reached", "insert:marketing_runs",
                   "update:marketing_posts", "update:marketing_runs", "requested"]
    res = _sql_markers(RESTORE)
    assert res == ["not_found", "lock", "lock:marketing_runs", "not_found", "not_restorable", "not_restorable",
                   "not_restorable", "lock:marketing_runs", "not_restorable", "not_restorable", "replacement_running",
                   "replacement_has_posts", "restored:replayed", "too_late", "lock:marketing_posts",
                   "update:marketing_posts", "update:marketing_runs", "restored"]


def test_the_decision_tables_read_every_branch_where_and_assignment_of_both_functions():
    """The pinned tables are not vacuous: the readers find every outcome branch (each RETURN of an outcome but the
    final one sits behind an IF), every WHERE and every assignment, and the real file matches them exactly."""
    text = _MIGRATION_215.read_text(encoding="utf-8")
    for name in _FUNCTIONS:
        body = _definition(text, name).body
        branches, wheres, assigns = _PINNED[name]
        returns = [m for m in _markers(body) if not m.startswith(("lock", "insert:", "update:"))]
        # every RETURN of an outcome but the last (the fresh request / restore) sits behind an IF
        assert [b for b, _ in _branches(body) if b != "raise"] == returns[:-1], name
        assert len(_wheres(body)) == len(re.findall(r"\bWHERE\b", _without_literals(_strip(body)))) == len(wheres)
        assert len(_assigns(body)) == len(re.findall(r":=", _strip(body).split("BEGIN", 1)[1])) == len(assigns)
        assert _decision_problems(name, body) == [] and _structure_problems(name, body) == []


def test_the_fake_runs_both_functions_statement_by_statement_in_the_sql_order():
    """The full success path of each function runs every check, lock and write: the fake's trace must equal the SQL's
    marker list exactly (a check, a lock or a write moved, dropped or added on one side fails here)."""
    fake = _world()
    assert _request(fake)["outcome"] == "requested"
    assert fake.trace == _sql_markers(REQUEST)
    _end_replacement(fake, "skipped")
    assert _restore(fake)["outcome"] == "restored"
    assert fake.trace == _sql_markers(RESTORE)


def _end_replacement(fake: ReplacementRpcFake, status: str, *, attempts: int = 1) -> dict:
    rep = next(r for r in fake.runs if r.get("replacement") == 1 and r.get("replaces_run_id") == RUN)
    rep.update(status=status, attempts=attempts)
    return rep


# ══ marketing_request_replacement, through the fake ══════════════════════════════════════════════════════════════


def test_a_request_writes_exactly_the_replacement_the_rejections_and_the_stamp():
    fake = _world()
    before = copy.deepcopy(fake.posts)
    out = _request(fake)
    new_id = out["run_id"]
    assert out == {"outcome": "requested", "replayed": False, "run_id": new_id, "replacement": 1,
                   "replacements_today": 1, "rejected_post_ids": sorted(P[:5]),
                   # numbers only, previews skipped, distinct and sorted (A6)
                   "review_message_ids": [501, 502], "from_source_ref": SOURCE_REF}
    # the new run: planned, unstarted, the replaced run's lane / class / dry_run, and never the owner's id
    rep = next(r for r in fake.runs if r["id"] == new_id)
    stamp = NOW.isoformat()
    assert rep == {"id": new_id, "run_date": D, "lane": "main", "replacement": 1, "replaces_run_id": RUN,
                   "status": "planned", "stage": "planned", "content_class": "F", "dry_run": False, "attempts": 0,
                   "timings": {},
                   "metadata": {"replacement_request": {"at": stamp, "from_run_id": RUN,
                                                        "from_source_ref": SOURCE_REF}},
                   "created_at": stamp, "updated_at": stamp}
    assert "telegram:42" not in repr(rep)
    # every pending post rejected, its other metadata kept and the review record merged; the skipped one untouched
    for old, new in zip(before, fake.posts):
        if old["status"] != "pending_review":
            assert new == old
            continue
        assert new["status"] == "rejected" and new["updated_at"] == stamp
        assert new["metadata"] == {**old["metadata"], "review": {
            "decision": "rejected", "by": "telegram:42", "at": stamp, "replaced_by_run_id": new_id, "replacement": 1}}
    # the replaced run: still media_ready, stamped with who asked, what it became and the cutoff
    run = fake.runs[0]
    assert run["status"] == "media_ready" and run["updated_at"] == stamp
    assert run["metadata"] == {"claim_nonce": "n" * 32, "series": "money_map", "replaced_by": {
        "run_id": new_id, "replacement": 1, "at": stamp, "by": "telegram:42", "rejected": 5,
        "replacements_today": 1, "not_after": CUTOFF_MAIN.isoformat()}}
    assert fake.locks == [f"marketing_request_replacement:{D}"]


def test_a_retried_request_answers_the_same_payload_and_writes_nothing():
    """A5/L4: the response was lost after COMMIT; the owner taps again. The same answer comes back, read from the
    ledger (`replayed: true`), and nothing is written a second time."""
    fake = _world()
    fake.lose_response.add(REQUEST)
    with pytest.raises(RuntimeError, match="after COMMIT"):
        _request(fake)
    fake.lose_response.clear()
    after_first = copy.deepcopy((fake.runs, fake.posts))
    out = _request(fake)
    assert out["replayed"] is True
    first_id = next(r["id"] for r in fake.runs if r.get("replacement") == 1)
    assert out == {"outcome": "requested", "replayed": True, "run_id": first_id, "replacement": 1,
                   "status": "planned", "replacements_today": 1, "rejected_post_ids": sorted(P[:5]),
                   "review_message_ids": [501, 502], "from_source_ref": SOURCE_REF}
    assert (fake.runs, fake.posts) == after_first
    assert _a_prefix_ending_at(fake.trace, _sql_markers(REQUEST))
    assert fake.locks == [f"marketing_request_replacement:{D}"] * 2


def test_a_retry_after_the_cutoff_of_a_request_that_committed_before_it_is_still_the_replay():
    """Review 2026-11-27: ✅ Yes at 22:09:58 commits and loses its response; the owner taps again at 22:10:03. The replay
    is read BEFORE the cutoff check, so he hears what happened (`requested`, replayed) — never "too late" for a request
    that rejected his posts and planned the 22:15 run. Nothing is written by the retry."""
    clock = [CUTOFF_MAIN - timedelta(seconds=2)]
    fake = _world(clock=lambda: clock[0])
    fake.lose_response.add(REQUEST)
    with pytest.raises(RuntimeError, match="after COMMIT"):
        _request(fake)
    fake.lose_response.clear()
    clock[0] = CUTOFF_MAIN + timedelta(seconds=3)
    after_first = copy.deepcopy((fake.runs, fake.posts))
    out = _request(fake)
    assert (out["outcome"], out["replayed"], out["status"]) == ("requested", True, "planned")
    assert (fake.runs, fake.posts) == after_first and fake.trace[-1] == "requested:replayed"
    # a run that was never replaced is still too late at that instant
    other = _world(clock=lambda: CUTOFF_MAIN + timedelta(seconds=3))
    assert _request(other) == {"outcome": "too_late"}


@pytest.mark.parametrize("status", ["in_progress", "skipped", "media_ready", "failed"])
def test_a_replay_says_what_became_of_the_replacement(status):
    """The replay carries the replacement's status NOW, so a stale ✅ Yes on a kept keyboard is never told "the next
    company comes in 20-75 min" after that run already ended or made its posts."""
    fake = _world()
    _request(fake)
    _end_replacement(fake, status)
    assert _request(fake)["status"] == status


def test_a_request_on_a_run_whose_replaced_by_names_no_replacement_never_writes_past_its_cutoff():
    """The web may call the function past the cutoff (it passes the day's start) only to read a replay back. A run that
    claims `replaced_by` but has no newer row (a hand edit) is NOT a replay: past the cutoff it is too late, and nothing
    is written — so that call can never plan a replacement the web's own gates refused."""
    fake = _world()
    fake.runs[0]["metadata"]["replaced_by"] = {"run_id": "00000000-0000-4000-8000-00000000abcd"}
    before = copy.deepcopy((fake.runs, fake.posts))
    day_start = datetime(2026, 11, 27, 5, 0, tzinfo=timezone.utc)          # 00:00 ET of the run's day
    assert _request(fake, not_after=day_start) == {"outcome": "too_late"}
    assert (fake.runs, fake.posts) == before


def test_a_replay_reports_the_count_recorded_at_the_request_not_today_s():
    """The other lane asked after this one: the replay still says "1 of 2" (what this request spent), never 2."""
    fake = _world()
    _request(fake)
    fake.runs.append({"id": OTHER_LANE_RUN, "run_date": D, "lane": "spotlight", "replacement": 1,
                      "replaces_run_id": "00000000-0000-4000-8000-000000000003", "status": "planned", "metadata": {}})
    assert _request(fake)["replacements_today"] == 1


def test_a_replay_without_a_recorded_count_counts_the_day():
    """Robustness of the read-back: a replaced_by without a numeric count (never written that way) counts the rows."""
    fake = _world()
    _request(fake)
    fake.runs[0]["metadata"]["replaced_by"]["replacements_today"] = "1"
    assert _request(fake)["replacements_today"] == 1


def test_a_restored_run_is_never_replayed_it_is_already_replaced():
    """After a restore the run's newest row still replaces it, but a new tap must not hear "requested" again."""
    fake = _world()
    _request(fake)
    rep = _end_replacement(fake, "skipped")
    assert _restore(fake)["outcome"] == "restored"
    out = _request(fake)
    assert out == {"outcome": "already_replaced", "run_id": rep["id"], "replacement": 1, "status": "skipped"}


def test_a_request_on_a_run_with_a_newer_replacement_elsewhere_in_the_chain_is_already_replaced():
    """Gen 0 was replaced by r1, r1 by r2: a stale tap on gen 0 names the NEWEST run and its state."""
    fake = _world()
    r1 = _request(fake)["run_id"]
    rep = next(r for r in fake.runs if r["id"] == r1)
    rep.update(status="media_ready")
    fake.scripts.append({"run_id": r1, "status": "accepted", "template_id": "money_map",
                         "source_ref": "news:money_map:AAPL:2026"})
    fake.posts.append(_post(P[6], r1, review_message_id=601))
    r2 = _request(fake, r1)
    assert r2["outcome"] == "requested" and r2["replacement"] == 2 and r2["replacements_today"] == 2
    assert _request(fake, RUN) == {"outcome": "already_replaced", "run_id": r2["run_id"], "replacement": 2,
                                   "status": "planned"}


def test_the_day_cap_counts_both_lanes():
    """D7: two replacement rows on the day (any lanes) → the third request is refused, writes nothing."""
    fake = _world()
    for i, lane in enumerate(("spotlight", "spotlight")):
        fake.runs.append({"id": f"00000000-0000-4000-8000-00000000003{i}", "run_date": D, "lane": lane,
                          "replacement": i + 1, "replaces_run_id": OTHER_LANE_RUN, "status": "skipped",
                          "metadata": {}})
    before = copy.deepcopy((fake.runs, fake.posts))
    assert _request(fake) == {"outcome": "cap_reached", "today": 2}
    assert (fake.runs, fake.posts) == before


def test_another_day_s_replacements_do_not_count():
    fake = _world()
    fake.runs.append({"id": OTHER_LANE_RUN, "run_date": "2026-11-26", "lane": "main", "replacement": 2,
                      "replaces_run_id": "00000000-0000-4000-8000-000000000003", "status": "skipped", "metadata": {}})
    assert _request(fake)["outcome"] == "requested"


def test_the_per_lane_cap_is_a_backstop_of_its_own():
    """`replacement >= c_max` refuses even when the day count would allow it (the count masks it at today's
    constants, so the fake's day cap is raised to reach the arm)."""
    fake = _world()
    fake.per_day = 3
    fake.runs[0].update(replacement=2, replaces_run_id=OTHER_LANE_RUN)
    out = _request(fake)
    assert out == {"outcome": "cap_reached", "today": 1}


@pytest.mark.parametrize("blocking", schemas.REPLACEMENT_BLOCKING_POST_STATUSES)
def test_any_post_that_can_reach_a_platform_makes_the_run_final(blocking):
    """A13 / R3: one post approved, queued, published, failed or retracted → member_decided, nothing written (the
    owner cannot get a half-replaced run)."""
    fake = _world()
    fake.posts[1]["status"] = blocking
    before = copy.deepcopy((fake.runs, fake.posts))
    assert _request(fake) == {"outcome": "member_decided", "statuses": blocking}
    assert (fake.runs, fake.posts) == before
    assert _a_prefix_ending_at(fake.trace, _sql_markers(REQUEST))


def test_member_decided_lists_each_blocking_status_once_sorted():
    fake = _world()
    fake.posts[0]["status"], fake.posts[1]["status"], fake.posts[2]["status"] = "queued", "approved", "queued"
    assert _request(fake) == {"outcome": "member_decided", "statuses": "approved,queued"}


def test_nothing_pending_when_every_post_is_already_decided_without_reaching_a_platform():
    fake = _world(posts=[_post(P[0], status="rejected"), _post(P[1], status="skipped")])
    assert _request(fake) == {"outcome": "nothing_pending"}


def test_nothing_pending_for_a_run_with_no_post():
    assert _request(_world(posts=[])) == {"outcome": "nothing_pending"}


@pytest.mark.parametrize("template, script_status", [
    (None, "accepted"),                 # no script row
    ("earnings", "accepted"),           # another series
    ("money_map", "rejected"),          # a rejected script
    ("money_map", "generating"),
    ("checklist", "accepted"),          # a lesson
])
def test_only_an_accepted_money_map_script_can_be_replaced(template, script_status):
    fake = _world(template=template, script_status=script_status)
    before = copy.deepcopy((fake.runs, fake.posts))
    assert _request(fake) == {"outcome": "not_money_map"}
    assert (fake.runs, fake.posts) == before
    assert _a_prefix_ending_at(fake.trace, _sql_markers(REQUEST))


@pytest.mark.parametrize("status", ["planned", "in_progress", "failed", "skipped", "published"])
def test_only_a_media_ready_run_can_be_replaced(status):
    fake = _world(status=status)
    assert _request(fake) == {"outcome": "not_ready", "status": status}
    assert len(fake.runs) == 1 and all(p["status"] != "rejected" for p in fake.posts)


@pytest.mark.parametrize("clock, not_after", [
    (CUTOFF_MAIN, CUTOFF_MAIN),                                   # at the cutoff exactly: closed
    (CUTOFF_MAIN + timedelta(seconds=1), CUTOFF_MAIN),
    # an honest-looking far cutoff cannot open another day: the clock is on the NEXT ET day
    (datetime(2026, 11, 28, 5, 30, tzinfo=timezone.utc), datetime(2026, 11, 29, tzinfo=timezone.utc)),
    # nor the day before (a clock behind the run's own day)
    (datetime(2026, 11, 27, 4, 30, tzinfo=timezone.utc), CUTOFF_MAIN),
])
def test_too_late_after_the_cutoff_or_off_the_run_s_own_et_day(clock, not_after):
    fake = _world(clock=lambda: clock)
    before = copy.deepcopy((fake.runs, fake.posts))
    assert _request(fake, not_after=not_after) == {"outcome": "too_late"}
    assert (fake.runs, fake.posts) == before


def test_one_second_before_the_cutoff_is_still_open():
    fake = _world(clock=lambda: CUTOFF_MAIN - timedelta(seconds=1))
    assert _request(fake)["outcome"] == "requested"


def test_an_unknown_run_is_not_found_and_takes_no_lock():
    fake = _world()
    assert _request(fake, "00000000-0000-4000-8000-00000000dead") == {"outcome": "not_found"}
    assert fake.locks == [] and fake.trace == ["not_found"]


def test_a_spotlight_request_keeps_its_lane():
    fake = _world(lane="spotlight")
    out = _request(fake)
    rep = next(r for r in fake.runs if r["id"] == out["run_id"])
    assert rep["lane"] == "spotlight" and rep["replacement"] == 1


@pytest.mark.parametrize("params, code", [
    ({"p_run_id": None, "p_by": "telegram:1", "p_not_after": CUTOFF_MAIN.isoformat()}, "22023"),
    ({"p_run_id": RUN, "p_by": "", "p_not_after": CUTOFF_MAIN.isoformat()}, "22023"),
    ({"p_run_id": RUN, "p_by": "x" * 65, "p_not_after": CUTOFF_MAIN.isoformat()}, "22023"),
    ({"p_run_id": RUN, "p_by": "telegram:1", "p_not_after": None}, "22023"),
    ({"p_run_id": "not-a-uuid", "p_by": "telegram:1", "p_not_after": CUTOFF_MAIN.isoformat()}, "22P02"),
    ({"p_run_id": RUN, "p_by": "telegram:1", "p_not_after": "tomorrow"}, "22007"),
])
def test_bad_arguments_raise_before_anything_is_read(params, code):
    from postgrest.exceptions import APIError

    fake = _world()
    before = copy.deepcopy((fake.runs, fake.posts))
    with pytest.raises(APIError) as e:
        fake.rpc(REQUEST, params).execute()
    assert e.value.code == code
    assert (fake.runs, fake.posts) == before and fake.locks == []


def test_the_64_character_by_is_accepted():
    assert _request(_world(), by="t" * 64)["outcome"] == "requested"


def test_a_re_run_210_makes_every_insert_a_23505_and_writes_nothing():
    """L5: UNIQUE (run_date, lane) back → the INSERT raises the unique violation; the transaction rolls back whole."""
    from postgrest.exceptions import APIError

    fake = _world()
    fake.legacy_unique = True
    before = copy.deepcopy((fake.runs, fake.posts))
    with pytest.raises(APIError) as e:
        _request(fake)
    assert e.value.code == "23505"
    assert (fake.runs, fake.posts) == before
    assert fake.trace[-1] == "insert:marketing_runs"


def test_the_missing_function_and_a_transient_failure_raise():
    from postgrest.exceptions import APIError

    fake = _world()
    fake.missing = True
    with pytest.raises(APIError) as e:
        _request(fake)
    assert e.value.code == "PGRST202"
    fake.missing, fake.missing_code = True, "42883"
    with pytest.raises(APIError) as e:
        _request(fake)
    assert e.value.code == "42883"
    fake.missing = False
    fake.fail.add("*")
    with pytest.raises(RuntimeError):
        _request(fake)
    assert len(fake.runs) == 1


@pytest.mark.parametrize("mid, preview, returned", [
    (77, None, [77]),
    (77.0, None, [77.0]),          # a JSON number either way
    (True, None, []),              # a JSON bool is not a number
    ("77", None, []),              # text
    (None, None, []),
    ([77], None, []),
    (77, "", [77]),                # an empty preview stamp is no preview (NULLIF)
    (77, "2026-11-27T16:00:00Z", []),
])
def test_decision_message_ids_are_numbers_of_non_preview_posts(mid, preview, returned):
    """A6 edge cases, on the fresh path and the replay alike."""
    meta = {} if mid is None else {"review_message_id": mid}
    if preview is not None:
        meta["review_preview_at"] = preview
    fake = _world(posts=[_post(P[0], **meta)])
    assert _request(fake)["review_message_ids"] == returned
    assert _request(fake)["review_message_ids"] == returned     # the replay


@pytest.mark.parametrize("scenario", [
    "not_found", "too_late", "already_replaced", "not_ready", "not_money_map_none", "not_money_map_other",
    "member_decided", "nothing_pending", "cap_reached", "replayed",
])
def test_every_request_refusal_stops_where_the_sql_returns(scenario):
    """Each refusal's trace is the SQL's statement list up to the RETURN that answers it: no check runs early or
    late, and nothing after it runs."""
    fake = _world()
    run = fake.runs[0]
    rid = RUN
    if scenario == "not_found":
        rid = "00000000-0000-4000-8000-00000000beef"
    elif scenario == "too_late":
        fake._clock = lambda: CUTOFF_MAIN
    elif scenario == "already_replaced":
        fake.runs.append({"id": OTHER_LANE_RUN, "run_date": D, "replacement": 1,
                          "replaces_run_id": "00000000-0000-4000-8000-000000000003", "status": "planned",
                          "metadata": {}})
    elif scenario == "not_ready":
        run["status"] = "in_progress"
    elif scenario == "not_money_map_none":
        fake.scripts.clear()
    elif scenario == "not_money_map_other":
        fake.scripts[0]["template_id"] = "earnings"
    elif scenario == "member_decided":
        fake.posts[0]["status"] = "approved"
    elif scenario == "nothing_pending":
        for p in fake.posts:
            p["status"] = "rejected"
    elif scenario == "cap_reached":
        fake.per_day = 0
    elif scenario == "replayed":
        _request(fake)
    out = _request(fake, rid)
    expect = {"not_money_map_none": "not_money_map", "not_money_map_other": "not_money_map",
              "replayed": "requested"}.get(scenario, scenario)
    assert out["outcome"] == expect
    markers = _sql_markers(REQUEST)
    assert _a_prefix_ending_at(fake.trace, markers), fake.trace
    if scenario == "not_money_map_none":
        assert fake.trace == markers[:markers.index("not_money_map") + 1]
    if scenario == "not_money_map_other":
        assert fake.trace.count("not_money_map") == 2
    if scenario == "replayed":
        assert fake.trace[-1] == "requested:replayed"


# ══ marketing_restore_replaced, through the fake ═════════════════════════════════════════════════════════════════


def _replaced_world(*, rep_status: str = "skipped", rep_attempts: int = 1,
                    clock: Optional[Callable[[], datetime]] = None) -> Tuple[ReplacementRpcFake, dict]:
    """A media_ready Money Map run whose replacement request landed, the replacement ended `rep_status`."""
    fake = _world(clock=clock)
    out = _request(fake)
    rep = next(r for r in fake.runs if r["id"] == out["run_id"])
    rep.update(status=rep_status, attempts=rep_attempts)
    return fake, rep


def test_a_restore_puts_back_exactly_the_posts_the_request_rejected():
    """A1: the replacement found nothing (skipped, no post). The posts it rejected go back to pending_review, their
    review record and offer stamps removed (the next sweep offers them as a fresh bundle), an audit record kept; a
    post the owner rejected himself and the skipped post stay as they are; the run records the restore."""
    fake = _world()
    fake.posts.append(_post(P[6], status="rejected", platform="tiktok",
                            review={"decision": "rejected", "by": "telegram:42", "at": "t", "reason": "weak"}))
    out = _request(fake)
    rep = next(r for r in fake.runs if r["id"] == out["run_id"])
    rep.update(status="skipped")
    later = NOW + timedelta(hours=1)
    fake._clock = lambda: later
    before = copy.deepcopy(fake.posts)
    res = _restore(fake, now=later)
    assert res == {"outcome": "restored", "replayed": False, "post_ids": sorted(P[:5])}
    stamp = later.isoformat()
    for old, new in zip(before, fake.posts):
        if old["id"] not in P[:5]:
            assert new == old
            continue
        assert new["status"] == "pending_review" and new["updated_at"] == stamp
        kept = {k: v for k, v in old["metadata"].items() if k != "review" and k not in mrs._REVIEW_OFFER_STAMPS}
        assert new["metadata"] == {**kept, "restored": {"by_run_id": rep["id"], "at": stamp, "by": "telegram:42"}}
    # the preview keeps its preview stamp; generation_id survives
    assert fake.posts[3]["metadata"]["review_preview_at"] == "t"
    assert all(p["metadata"]["generation_id"] == "g-1" for p in fake.posts[:5])
    run = fake.runs[0]
    assert run["metadata"]["restored_from"] == {"run_id": rep["id"], "at": stamp}
    assert run["metadata"]["replaced_by"]["run_id"] == rep["id"]        # the history stays
    assert run["status"] == "media_ready" and rep["status"] == "skipped"
    assert fake.locks == [f"marketing_request_replacement:{D}"] * 2   # the SAME per-day lock


def test_a_second_restore_answers_again_and_writes_nothing():
    fake, rep = _replaced_world()
    first = _restore(fake)
    after = copy.deepcopy((fake.runs, fake.posts))
    again = _restore(fake)
    assert again == {"outcome": "restored", "replayed": True, "post_ids": first["post_ids"]}
    assert (fake.runs, fake.posts) == after
    assert fake.trace[-1] == "restored:replayed"


@pytest.mark.parametrize("status, attempts, outcome", [
    ("skipped", 1, "restored"),
    ("media_ready", 1, "restored"),        # finished with NO post (A10's N5): no claim takes it again
    ("published", 1, "restored"),
    ("failed", 2, "restored"),             # at its 2-attempt cap
    ("failed", 3, "restored"),
    ("failed", 1, "replacement_running"),  # below its cap: the next tick may re-claim it and post
    ("failed", 0, "replacement_running"),
    ("planned", 0, "replacement_running"),
    ("in_progress", 1, "replacement_running"),
])
def test_only_a_replacement_that_can_never_post_again_lets_the_original_back(status, attempts, outcome):
    fake, rep = _replaced_world(rep_status=status, rep_attempts=attempts)
    before = copy.deepcopy((fake.runs, fake.posts))
    out = _restore(fake)
    assert out["outcome"] == outcome
    if outcome == "replacement_running":
        assert out == {"outcome": outcome, "status": status, "attempts": attempts}
        assert (fake.runs, fake.posts) == before


@pytest.mark.parametrize("post_status", schemas.POST_STATUSES)
def test_a_replacement_with_any_post_at_all_keeps_the_original_rejected(post_status):
    """A13: one post of the replacement, in ANY status (even rejected or skipped), refuses the restore: at most one
    run of a (day, lane) may ever publish."""
    fake, rep = _replaced_world(rep_status="media_ready")
    fake.posts.append(_post(P[7], rep["id"], status=post_status))
    before = copy.deepcopy((fake.runs, fake.posts))
    assert _restore(fake) == {"outcome": "replacement_has_posts"}
    assert (fake.runs, fake.posts) == before


@pytest.mark.parametrize("status", ["in_progress", "failed", "skipped", "published", "planned"])
def test_the_replaced_run_must_still_be_media_ready(status):
    fake, _ = _replaced_world()
    fake.runs[0]["status"] = status
    assert _restore(fake) == {"outcome": "not_restorable", "status": status}


@pytest.mark.parametrize("replaced_by", [
    None, {}, {"run_id": None}, {"run_id": 7}, {"run_id": "not-a-uuid"},
    {"run_id": "00000000-0000-4000-8000-00000000090A"},      # upper case: not the canonical form the SQL wrote
])
def test_an_unreadable_replaced_by_is_not_restorable(replaced_by):
    fake, _ = _replaced_world()
    if replaced_by is None:
        fake.runs[0]["metadata"].pop("replaced_by")
    else:
        fake.runs[0]["metadata"]["replaced_by"] = replaced_by
    before = copy.deepcopy((fake.runs, fake.posts))
    assert _restore(fake) == {"outcome": "not_restorable"}
    assert (fake.runs, fake.posts) == before


def test_a_run_whose_replacement_was_replaced_again_is_not_restorable():
    """Gen 0 → r1 (made posts, then replaced by r2): gen 0's replaced_by names r1, which is not the newest."""
    fake, r1 = _replaced_world(rep_status="media_ready")
    fake.scripts.append({"run_id": r1["id"], "status": "accepted", "template_id": "money_map",
                         "source_ref": "news:money_map:AAPL:2026"})
    fake.posts.append(_post(P[7], r1["id"], review_message_id=601))
    r2 = _request(fake, r1["id"])["run_id"]
    next(r for r in fake.runs if r["id"] == r2).update(status="skipped")
    assert _restore(fake, RUN) == {"outcome": "not_restorable"}
    # r1 itself is restorable: its replacement (r2) ended with no post
    assert _restore(fake, r1["id"]) == {"outcome": "restored", "replayed": False, "post_ids": [P[7]]}


def test_a_replacement_that_does_not_point_back_is_not_restorable():
    fake, rep = _replaced_world()
    rep["replaces_run_id"] = OTHER_LANE_RUN
    assert _restore(fake) == {"outcome": "not_restorable"}


def test_a_replaced_by_naming_a_vanished_run_is_not_restorable():
    fake, rep = _replaced_world()
    fake.runs[0]["metadata"]["replaced_by"]["run_id"] = "00000000-0000-4000-8000-00000000abcd"
    assert _restore(fake) == {"outcome": "not_restorable"}


MIDNIGHT = datetime(2026, 11, 28, 5, 0, tzinfo=timezone.utc)         # 00:00 ET, the end of the run's own day


@pytest.mark.parametrize("clock, caller, outcome", [
    (NOW, NOW, "restored"),
    (CUTOFF_MAIN, NOW, "restored"),                          # past the 22:10 TAP cutoff: still the run's own day
    (NOW, CUTOFF_MAIN + timedelta(minutes=10), "restored"),  # the 22:20 notice of a tap made at 21:30
    (MIDNIGHT - timedelta(seconds=1), NOW, "restored"),      # 23:59:59 ET
    (MIDNIGHT, NOW, "too_late"),                             # the database clock on the next ET day
    (NOW, MIDNIGHT, "too_late"),                             # the caller's clock there: the later one judges
    (datetime(2026, 11, 27, 4, 59, tzinfo=timezone.utc), NOW, "restored"),   # a db clock behind: the caller's judges
])
def test_the_restore_window_is_the_whole_of_the_run_s_own_et_day(clock, caller, outcome):
    """Review 2026-11-27: the window ends with the run's ET day, never at the lane's tap cutoff. A tap at 21:30 starts
    its replacement at 22:15 — after the 22:10 cutoff — so a cutoff-bound restore could never bring that original
    back, although T2 / T6 promised it; nor after an N3 for a replacement never started (sent at 23:35 / 15:35)."""
    fake, _ = _replaced_world()
    fake._clock = lambda: clock
    assert _restore(fake, now=caller)["outcome"] == outcome


@pytest.mark.parametrize("not_after", [None, 7, "", "not a time", "2026-13-45T00:00:00+00:00", "infinity",
                                       "2026-11-29T03:10:00+00:00", (NOW - timedelta(hours=1)).isoformat()])
def test_the_restore_never_reads_the_recorded_tap_cutoff(not_after):
    """`replaced_by.not_after` is the request's record of the tap cutoff only: missing, unreadable or long past, the
    restore is judged by the run's own ET day alone."""
    fake, _ = _replaced_world()
    if not_after is None:
        fake.runs[0]["metadata"]["replaced_by"].pop("not_after")
    else:
        fake.runs[0]["metadata"]["replaced_by"]["not_after"] = not_after
    assert _restore(fake)["outcome"] == "restored"


def test_a_retried_restore_after_midnight_of_one_that_committed_before_it_is_still_the_replay():
    """The restore's replay is read BEFORE its day check: a restore that committed at 23:59:58 ET and lost its response
    is answered `restored` (replayed) on a retry at 00:00:03 — never "too late" for posts already back in review."""
    clock = [NOW]
    fake, _ = _replaced_world(clock=lambda: clock[0])
    clock[0] = MIDNIGHT - timedelta(seconds=2)
    fake.lose_response.add(RESTORE)
    with pytest.raises(RuntimeError, match="after COMMIT"):
        _restore(fake, now=clock[0])
    fake.lose_response.clear()
    assert {p["status"] for p in fake.posts[:5]} == {"pending_review"}                 # it DID commit
    clock[0] = MIDNIGHT + timedelta(seconds=3)
    after_first = copy.deepcopy((fake.runs, fake.posts))
    out = _restore(fake, now=clock[0])
    assert (out["outcome"], out["replayed"]) == ("restored", True) and sorted(out["post_ids"]) == sorted(P[:5])
    assert (fake.runs, fake.posts) == after_first


def test_restore_of_an_unknown_run_and_bad_arguments():
    from postgrest.exceptions import APIError

    fake, _ = _replaced_world()
    assert _restore(fake, "00000000-0000-4000-8000-00000000dead") == {"outcome": "not_found"}
    for params, code in (({"p_run_id": RUN, "p_by": None, "p_now": NOW.isoformat()}, "22023"),
                         ({"p_run_id": RUN, "p_by": "t", "p_now": None}, "22023"),
                         ({"p_run_id": "x", "p_by": "t", "p_now": NOW.isoformat()}, "22P02")):
        with pytest.raises(APIError) as e:
            fake.rpc(RESTORE, params).execute()
        assert e.value.code == code


def test_a_restored_run_s_posts_can_be_restored_only_once_even_if_rejected_again():
    """After the restore the owner rejects a restored post himself: it no longer carries replaced_by_run_id, so a
    replayed restore never flips it back (the replay writes nothing at all)."""
    fake, _ = _replaced_world()
    _restore(fake)
    fake.posts[0].update(status="rejected")
    fake.posts[0]["metadata"]["review"] = {"decision": "rejected", "by": "telegram:42", "at": "t"}
    assert _restore(fake)["replayed"] is True
    assert fake.posts[0]["status"] == "rejected"


@pytest.mark.parametrize("scenario", [
    "not_found", "not_media_ready", "no_replaced_by", "not_newest", "vanished", "points_elsewhere", "running",
    "has_posts", "other_day", "replayed", "replayed_other_day",
])
def test_every_restore_refusal_stops_where_the_sql_returns(scenario):
    fake, rep = _replaced_world()
    run = fake.runs[0]
    rid, now = RUN, NOW
    if scenario == "not_found":
        rid = "00000000-0000-4000-8000-00000000beef"
    elif scenario == "not_media_ready":
        run["status"] = "failed"
    elif scenario == "no_replaced_by":
        run["metadata"].pop("replaced_by")
    elif scenario == "not_newest":
        fake.runs.append({"id": OTHER_LANE_RUN, "run_date": D, "replacement": 2, "replaces_run_id": rep["id"],
                          "status": "planned", "metadata": {}})
    elif scenario == "vanished":
        fake.runs.remove(rep)
        run["metadata"]["replaced_by"]["run_id"] = rep["id"]
    elif scenario == "points_elsewhere":
        rep["replaces_run_id"] = OTHER_LANE_RUN
    elif scenario == "running":
        rep["status"] = "in_progress"
    elif scenario == "has_posts":
        fake.posts.append(_post(P[8], rep["id"]))
    elif scenario == "other_day":
        now = datetime(2026, 11, 28, 6, 0, tzinfo=timezone.utc)
    elif scenario in ("replayed", "replayed_other_day"):
        _restore(fake)
        if scenario == "replayed_other_day":
            now = datetime(2026, 11, 28, 6, 0, tzinfo=timezone.utc)
    out = _restore(fake, rid, now=now)
    expect = {"not_media_ready": "not_restorable", "no_replaced_by": "not_restorable", "not_newest": "not_restorable",
              "vanished": "not_restorable", "points_elsewhere": "not_restorable", "running": "replacement_running",
              "has_posts": "replacement_has_posts", "other_day": "too_late", "replayed": "restored",
              "replayed_other_day": "restored"}.get(scenario, scenario)
    assert out["outcome"] == expect
    assert _a_prefix_ending_at(fake.trace, _sql_markers(RESTORE)), fake.trace
