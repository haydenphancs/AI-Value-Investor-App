"""The monthly reset ledgers what expired (migration 194) — pinned on the LATEST definition.

`ensure_credit_period` wipes the granted pool on the first touch after `resets_at`. Until 194
that wipe was the one balance change with no ledger row, so the Credit History statement
stopped adding up after every reset. 194 writes a `monthly_expired` row (granted only, ref_id
NULL) BEFORE the reset.

This is the ONLY guard on that behaviour once a later migration redefines the function: the
reason-coverage scan (test_credit_history_reason_coverage) would stay green on a regression,
because 194's file still writes `monthly_expired`. So every assertion here reads the LATEST
definition of `public.ensure_credit_period` across the migrations, comments stripped
(testing.md rule 1: 194's own comments name every token below) — written as `CREATE FUNCTION`
as well as `CREATE OR REPLACE FUNCTION`: a change of return type forces `DROP FUNCTION ...;
CREATE FUNCTION` (migration 142 did exactly that to `refund_credits`), and a detector blind to
that shape keeps reading an older file while the live function stops ledgering expiries.

Non-vacuity: `test_the_detectors_reject_hand_mutated_bodies` feeds broken copies of the real
body through the same detectors and requires each one to fail, and
`test_a_later_plain_create_function_without_the_expiry_is_caught` feeds an in-memory later
migration (never a file in migrations/) through `_latest`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "migrations"

_FUNC = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:public\.)?ensure_credit_period\s*\("
    r"(?P<header>.*?)AS\s+(?P<tag>\$\w*\$)(?P<body>.*?)(?P=tag)",
    re.IGNORECASE | re.DOTALL,
)
_RESET_BRANCH_HEAD = "IF v_row.resets_at IS NULL OR now() >= v_row.resets_at THEN"


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _migration_sources() -> list[tuple[str, str]]:
    return [(path.name, path.read_text(encoding="utf-8"))
            for path in sorted(_MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))]


def _latest(sources: list[tuple[str, str]] | None = None) -> tuple[str, str, str, str]:
    """(file name, header, body, whole stripped file) of the latest definition among
    `sources` ((name, raw SQL) in apply order; default: every migration on disk)."""
    found = []
    for name, raw in (_migration_sources() if sources is None else sources):
        text = _strip_sql_comments(raw)
        for m in _FUNC.finditer(text):
            found.append((name, m.group("header"), m.group("body"), text))
    assert found, "no CREATE [OR REPLACE] FUNCTION ensure_credit_period in any migration"
    return found[-1]


def _latest_problems(sources: list[tuple[str, str]] | None = None) -> list[str]:
    """What `test_the_latest_definition_is_194_or_later_and_writes_the_expiry` asserts, as a
    detector a synthetic later migration can be fed through."""
    name, _header, body, _text = _latest(sources)
    out = []
    if int(name[:3]) < 194:
        out.append(f"the latest ensure_credit_period is {name}, before 194")
    if "'monthly_expired'" not in body:
        out.append(f"the latest ensure_credit_period ({name}) writes no monthly_expired row")
    return out


def _reset_branch(body: str) -> str:
    start = body.index(_RESET_BRANCH_HEAD)
    end = body.index("RETURN v_alloc;", start)
    return body[start:end]


def _not_found_branch(body: str) -> str:
    start = body.index("IF NOT FOUND THEN")
    return body[start:body.index(_RESET_BRANCH_HEAD)]


def _ledger_inserts(text: str) -> list[str]:
    return re.findall(
        r"INSERT\s+INTO\s+(?:public\.)?credit_transactions\s*\(.*?;", text, re.I | re.S,
    )


def _values(insert: str) -> dict[str, str]:
    cols = re.search(r"credit_transactions\s*\((.*?)\)", insert, re.S).group(1)
    vals = re.search(r"VALUES\s*\((.*)\)\s*;", insert, re.S).group(1)
    names = [c.strip().lower() for c in cols.split(",")]
    parts, depth, cur = [], 0, ""
    for ch in vals:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    parts.append(cur.strip())
    assert len(names) == len(parts), (names, parts)
    return dict(zip(names, parts))


def _expired_insert(branch: str) -> str:
    hits = [i for i in _ledger_inserts(branch) if "'monthly_expired'" in i]
    assert len(hits) == 1, f"expected ONE monthly_expired ledger insert, found {len(hits)}"
    return hits[0]


# ── the detectors (each returns a list of reasons, empty = pass) ─────────────────────────


def _order_problems(body: str) -> list[str]:
    branch = _reset_branch(body)
    expired = branch.find("'monthly_expired'")
    update = branch.find("UPDATE public.user_credits")
    reset = branch.find("'monthly_reset'")
    out = []
    if expired < 0:
        out.append("no monthly_expired row in the reset branch")
    if update < 0 or reset < 0:
        out.append("the reset branch lost its UPDATE or its monthly_reset row")
    if expired >= 0 and update >= 0 and expired > update:
        out.append("the expiry is ledgered AFTER the wipe (it must read the old period)")
    if expired >= 0 and reset >= 0 and expired > reset:
        out.append("the expiry row is written after monthly_reset (history order breaks)")
    return out


def _values_problems(body: str) -> list[str]:
    vals = _values(_expired_insert(_reset_branch(body)))
    out = []
    delta = re.sub(r"\s+", "", vals.get("delta", ""))
    if not delta.startswith("-") or delta != re.sub(r"\s+", "", vals.get("granted_delta", "")):
        out.append(f"delta/granted_delta are not the same negated amount: {vals}")
    if vals.get("purchased_delta") != "0":
        out.append(f"purchased_delta must be the literal 0 (3.1.1): {vals.get('purchased_delta')}")
    if vals.get("ref_id", "").upper() != "NULL":
        out.append(f"ref_id must be NULL so no refund can pair it: {vals.get('ref_id')}")
    return out


def _guard_problems(body: str) -> list[str]:
    branch = _reset_branch(body)
    out = []
    if not re.search(r"v_expired\s*:=\s*GREATEST\(\s*v_row\.total\s*-\s*v_row\.used\s*,\s*0\s*\)",
                     branch):
        out.append("v_expired is not GREATEST(v_row.total - v_row.used, 0)")
    guard = re.search(r"IF\s+v_expired\s*>\s*0\s+THEN(.*?)END\s+IF;", branch, re.S)
    if not guard or "'monthly_expired'" not in guard.group(1):
        out.append("the expiry insert is not guarded by IF v_expired > 0 (zero rows would land)")
    return out


# ── the assertions ────────────────────────────────────────────────────────────────────────


def test_the_latest_definition_is_194_or_later_and_writes_the_expiry():
    assert _latest_problems() == []


@pytest.mark.parametrize("create", ["CREATE FUNCTION", "CREATE OR REPLACE FUNCTION",
                                    "create function"])
def test_a_later_plain_create_function_without_the_expiry_is_caught(create):
    """A later migration that redefines the function — in the `DROP FUNCTION ...; CREATE
    FUNCTION` shape a return-type change forces — and drops the expiry must become the
    latest definition and fail. Built in memory from the real body: nothing is written to
    migrations/."""
    sources = _migration_sources()
    assert _latest_problems(sources) == [], "the real migrations must pass first"
    _name, header, body, _text = _latest(sources)
    expiries = [i for i in _ledger_inserts(body) if "'monthly_expired'" in i]
    assert expiries, "no expiry insert found to drop — update this mutation"
    stripped = body
    for insert in expiries:
        stripped = stripped.replace(insert, "", 1)
    assert "'monthly_expired'" not in stripped, "the mutation left an expiry insert behind"
    later = (
        "BEGIN;\n"
        "DROP FUNCTION IF EXISTS public.ensure_credit_period(UUID);\n"
        f"{create} public.ensure_credit_period({header}AS $fn${stripped}$fn$;\n"
        "COMMIT;\n"
    )
    synthetic = sources + [("999_synthetic_redefinition.sql", later)]
    assert _latest(synthetic)[0] == "999_synthetic_redefinition.sql", (
        f"`_FUNC` does not see a `{create}` redefinition — the guard keeps reading an older file"
    )
    problems = _latest_problems(synthetic)
    assert any("monthly_expired" in p for p in problems), problems


def test_the_expiry_is_written_before_the_wipe_and_before_the_reset_row():
    _name, _header, body, _text = _latest()
    assert _order_problems(body) == []


def test_the_expiry_row_is_granted_only_and_unpairable():
    _name, _header, body, _text = _latest()
    assert _values_problems(body) == []


def test_the_expiry_is_guarded_and_computed_from_the_old_period():
    _name, _header, body, _text = _latest()
    assert _guard_problems(body) == []


def test_the_wipe_still_names_no_purchased_column():
    """3.1.1: the reset UPDATE must stay column-explicit on the GRANTED pool."""
    _name, _header, body, _text = _latest()
    branch = _reset_branch(body)
    update = branch[branch.index("UPDATE public.user_credits"):]
    update = update[: update.index(";")]
    assert "purchased" not in update.lower(), update


def test_a_first_touch_writes_no_expiry():
    _name, _header, body, _text = _latest()
    assert "monthly_expired" not in _not_found_branch(body)


def test_security_definer_and_its_settings_survive():
    _name, header, _body, _text = _latest()
    assert re.search(r"SECURITY\s+DEFINER", header, re.I)
    assert re.search(r"SET\s+search_path\s*=\s*public\s*,\s*pg_temp", header, re.I)
    assert re.search(r"SET\s+row_security\s*=\s*off", header, re.I)


def test_the_latest_file_reasserts_the_acl():
    _name, _header, _body, text = _latest()
    assert re.search(
        r"REVOKE\s+ALL\s+ON\s+FUNCTION\s+public\.ensure_credit_period\(UUID\)\s+FROM\s+PUBLIC;",
        text, re.I)
    assert re.search(
        r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+public\.ensure_credit_period\(UUID\)\s+TO\s+service_role;",
        text, re.I)


@pytest.mark.parametrize("mutation,detector", [
    # the expiry moved after the reset row
    (lambda b: _swap_expiry_after_reset(b), _order_problems),
    # a month stamp instead of NULL — the expiry would become a pairable debit
    (lambda b: b.replace("'monthly_expired', NULL, 0", "'monthly_expired', "
                         "to_char(v_now_et, 'YYYY-MM'), 0"), _values_problems),
    # purchased credits expiring (App Store 3.1.1)
    (lambda b: b.replace("-v_expired, -v_expired, 0,", "-v_expired, -v_expired, -v_expired,"),
     _values_problems),
    # the zero-row guard dropped
    (lambda b: b.replace("IF v_expired > 0 THEN", "IF TRUE THEN"), _guard_problems),
])
def test_the_detectors_reject_hand_mutated_bodies(mutation, detector):
    _name, _header, body, _text = _latest()
    assert detector(body) == [], "the real body must pass before a mutation can mean anything"
    mutated = mutation(body)
    assert mutated != body, "the mutation did not apply — this test has rotted"
    assert detector(mutated), "a broken body passed the detector — it is vacuous"


def _swap_expiry_after_reset(body: str) -> str:
    branch = _reset_branch(body)
    guard = re.search(r"IF\s+v_expired\s*>\s*0\s+THEN.*?END\s+IF;", branch, re.S).group(0)
    moved = branch.replace(guard, "", 1) + guard + "\n"
    return body.replace(branch, moved, 1)


def _never_after_196_guard_problem(sql: str) -> str | None:
    """None when `sql` (comments stripped) refuses to run once 196's table exists, BEFORE it
    replaces ensure_credit_period; else what is wrong."""
    body = _strip_sql_comments(sql)
    create = re.search(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:public\.)?ensure_credit_period",
                       body, re.IGNORECASE)
    if not create:
        return "the file no longer defines ensure_credit_period"
    guard = re.search(
        r"IF\s+to_regclass\(\s*'public\.subscription_credit_periods'\s*\)\s+IS\s+NOT\s+NULL\s+THEN"
        r"\s+RAISE\s+EXCEPTION",
        body, re.IGNORECASE,
    )
    if not guard:
        return "no RAISE when public.subscription_credit_periods (migration 196) exists"
    if guard.start() > create.start():
        return "the guard runs AFTER the function is replaced — too late to protect 196"
    begin = re.search(r"^\s*BEGIN\s*;", body, re.IGNORECASE | re.MULTILINE)
    if not begin or begin.start() > guard.start():
        # Outside the transaction, psql without ON_ERROR_STOP would report the RAISE and still
        # run the CREATE below it.
        return "the guard is not inside the migration's transaction (BEGIN; must come first)"
    return None


def test_194_refuses_to_run_after_196():
    """194 and 196 both replace ensure_credit_period and the later one wins, so 194 applied
    after 196 would silently put back the calendar-only body (owner-facing order: 194 → 196,
    never the reverse). 194 therefore raises before touching anything once 196's table exists.
    Comments are stripped, so the header's prose cannot satisfy this."""
    sql = (_MIGRATIONS / "194_monthly_expired_ledger_row.sql").read_text()
    assert _never_after_196_guard_problem(sql) is None, _never_after_196_guard_problem(sql)


@pytest.mark.parametrize("mutate", [
    lambda s: re.sub(r"DO \$\$.*?END \$\$;", "", s, count=1, flags=re.DOTALL),          # guard gone
    lambda s: s.replace("subscription_credit_periods", "subscription_credit_period", 1),  # wrong table
    lambda s: s.replace("        RAISE EXCEPTION", "        RAISE NOTICE", 1),                   # warns only
    lambda s: s.replace("BEGIN;\n", "", 1).replace("END $$;\n", "END $$;\nBEGIN;\n", 1),  # guard before BEGIN
])
def test_the_never_after_196_detector_is_not_vacuous(mutate):
    sql = (_MIGRATIONS / "194_monthly_expired_ledger_row.sql").read_text()
    mutated = mutate(sql)
    assert mutated != sql, "the mutation did not apply — re-derive it"
    assert _never_after_196_guard_problem(mutated) is not None
